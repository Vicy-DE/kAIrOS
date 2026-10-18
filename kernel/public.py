"""kAIrOS public images [R87] (docs/PUBLIC-IMAGES-2026-10-16.md).

What the owner's private images never use — and what a shareable image needs:

  * the build-time PROFILE. `/etc/kairos/profile` holding the word `public` (scripts/public-image-prep.sh writes it;
    KAIROS_PROFILE / KAIROS_PROFILE_FILE override it for tests). No file, or anything else = "private" = the owner's
    images: apply() then returns the tier manifest object untouched and nothing below runs.
    In the public profile the overlay `manifests/profile-public.json` is applied on top of the tier manifest:
      - Muse (the tier's `image`) has no redistribution grant outside civitai and civitai serves it only to a
        logged-in account, so a public image never carries it. While it is not installed, `image` and `image_safe`
        are the tier's `image_classic` files (Z-Image-Turbo, Apache-2.0) with Muse's prompt-rule families, and
        `image_muse` is Muse as a download that needs the USER's own civitai API key. Once Muse is installed the
        tier's own `image` / `image_safe` apply unchanged — as on the owner's images;
      - `vision_encoder` (the vision add-on's weights, Muse's text encoder on the owner's images) and
        `knowledge_zim` (the offline Wikipedia) become concrete file entries;
  * the ESSENTIALS: the baked files a public image may leave out (the minimal image) download at first boot with
    aria2c (R47's mechanism): one file at a time, resumable (aria2 control files), sha256-checked, waiting while
    offline and resuming by itself, pausable, never blocking the desktop. Each feature whose model is missing says
    "… is downloading (n %)" / "… needs a download" instead of failing;
  * MUSE WITH THE USER'S CIVITAI KEY: the key is stored root-only (0600 in a 0700 dir), sent only to the configured
    civitai download URL's own host — never to the CDN it redirects to, never logged, never in a response, an error
    or a page — and Muse's licence summary must be accepted first;
  * the LICENCES index every profile serves: /usr/share/doc/kairos/model-licences/index.json (the texts live next to
    it; the repo keeps them in iso/config/includes.chroot/…).
"""
import asyncio
import contextlib
import copy
import hashlib
import json
import os
import re
import shutil
import struct
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlencode, parse_qsl, urlunsplit

try:
    import aiohttp
except ImportError:            # tools that only read manifests (the image build, a smoke layout) need no aiohttp
    aiohttp = None

PROFILES = ("private", "public")
PROFILE_FILE = Path("/etc/kairos/profile")                 # KAIROS_PROFILE_FILE overrides
OVERLAY_NAME = "profile-public.json"
SECRETS_DIR = Path("/var/lib/kairos/secrets")      # KAIROS_SECRETS_DIR overrides (read when Muse is set up)
LICENCE_DIR_DEFAULT = Path("/usr/share/doc/kairos/model-licences")
REPO_LICENCE_DIR = Path(__file__).resolve().parent.parent / "iso" / "config" / "includes.chroot" / LICENCE_DIR_DEFAULT.relative_to("/")
# aria2c exit codes that are the network's fault (retried after a pause); 3/4 = not found, 9 = disk full,
# 24 = authorization — those stop the item until the user retries
ARIA2_HARD = {3: "the file is not on the server (HTTP 404)", 4: "the file is not on the server",
              9: "not enough disk space", 24: "the server refused the download (authorization)"}
DISK_MARGIN = 512 * 1024 * 1024
# aria2c's write cache (KAIROS_ARIA2_DISK_CACHE, default 16M): what it holds in RAM before the file grows on disk — the
# progress a user sees moves in steps of it; the tests set 0 for their few-MB files


def backoff(n: int) -> float:
    """seconds before the n-th retry while the network is away (KAIROS_DL_BACKOFF="0.2,0.5" in the tests)"""
    steps = [float(x) for x in os.environ.get("KAIROS_DL_BACKOFF", "5,15,30,60,120,300").split(",") if x.strip()] or [30.0]
    return steps[min(n, len(steps) - 1)]


async def reachable(url: str, timeout: float = 5.0) -> bool:
    """can a TCP connection to the URL's host be opened (DNS included)? — the cheap probe while waiting offline"""
    p = urlsplit(url or "")
    if not p.hostname:
        return False
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(p.hostname, p.port or (443 if p.scheme == "https" else 80)), timeout)
    except Exception:
        return False
    w.close()
    with contextlib.suppress(Exception):
        await w.wait_closed()
    return True


def profile_name() -> str:
    """"public" only when KAIROS_PROFILE or the profile file says exactly that; everything else is "private"
    (the owner's images: no file) — an unreadable or garbled file never switches an image into the public mode."""
    v = os.environ.get("KAIROS_PROFILE")
    if v is None:
        try:
            v = Path(os.environ.get("KAIROS_PROFILE_FILE") or PROFILE_FILE).read_text(encoding="utf-8", errors="replace")
        except OSError:
            v = ""
    v = v.strip().lower()
    if v and v not in PROFILES:
        print(f"[kernel] WARN profile {v[:40]!r} is unknown — using the private profile", flush=True)
    return v if v in PROFILES else "private"


def walk_files(node) -> list:
    """every {url, file} entry under a manifest node, in order"""
    out = []

    def collect(n):
        if isinstance(n, dict):
            if "url" in n and "file" in n:
                out.append(n)
            for v in n.values():
                collect(v)
    collect(node)
    return out


def node_at(manifest: dict, dotted: str):
    n = manifest
    for part in dotted.split("."):
        if not isinstance(n, dict) or part not in n:
            return None
        n = n[part]
    return n


def short_name(name) -> str:
    name = str(name or "").strip()
    return re.split(r"\s+[(—–]", name, maxsplit=1)[0].strip() or name


def file_digest(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def aria2_done(control: Path):
    """completed bytes recorded in an aria2 control file (format v0/v1: version, ext, infohash, piece length,
    total length, upload length, bitfield …) — None when it cannot be read"""
    try:
        data = control.read_bytes()
        ver = struct.unpack(">H", data[:2])[0]
        e = ">" if ver == 1 else "<"
        pos = 6
        (ihl,) = struct.unpack(e + "I", data[pos:pos + 4]); pos += 4 + ihl
        (plen,) = struct.unpack(e + "I", data[pos:pos + 4]); pos += 4
        (total,) = struct.unpack(e + "Q", data[pos:pos + 8]); pos += 16          # total + upload length
        (bfl,) = struct.unpack(e + "I", data[pos:pos + 4]); pos += 4
        bits = sum(bin(b).count("1") for b in data[pos:pos + bfl])
        return min(total, bits * plen) if total else None
    except Exception:
        return None


# one download per destination file at a time: the essentials, the add-on downloads (server.download_entries)
# and the Muse flow can name the same file (Muse needs the vision encoder too)
_LOCKS = {}


def file_lock(dest: Path) -> asyncio.Lock:
    """the lock of one destination file in the running event loop (a lock never crosses loops)"""
    return _LOCKS.setdefault((id(asyncio.get_running_loop()), str(dest)), asyncio.Lock())


# ============================================================================ the profile
class Profile:
    def __init__(self, manifests_dir: Path, models_dir: Path, knowledge_dir: Path, name: str = None):
        self.name = name or profile_name()
        self.public = self.name == "public"
        self.models_dir, self.knowledge_dir = Path(models_dir), Path(knowledge_dir)
        self.overlay, self.base = {}, None
        if self.public:
            p = Path(manifests_dir) / OVERLAY_NAME
            try:
                self.overlay = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[kernel] WARN public profile: {p} unreadable ({type(e).__name__}) — the tier manifest is used "
                      "as it is", flush=True)

    # ---- files
    def dest(self, e: dict) -> Path:
        return (self.knowledge_dir if e.get("store") == "knowledge" else self.models_dir) / e["file"]

    def present(self, e: dict) -> bool:
        """on disk, complete (no aria2 control file next to it) and — where the manifest knows it — the right size"""
        f = self.dest(e)
        try:
            st = f.stat()
        except OSError:
            return False
        if f.with_name(f.name + ".aria2").exists():
            return False
        return not isinstance(e.get("size_bytes"), int) or st.st_size == e["size_bytes"]

    def done_bytes(self, e: dict) -> int:
        """bytes of `e` on disk so far (a partial aria2 download: its control file / the sparse file's blocks; the
        Muse flow's .part file)"""
        size = e.get("size_bytes") or 0
        if self.present(e):
            return size or self.dest(e).stat().st_size
        f = self.dest(e)
        best = 0
        for p in (f, f.with_name(f.name + ".part")):
            try:
                st = p.stat()
                best = max(best, min(st.st_size, getattr(st, "st_blocks", 0) * 512) if p == f else st.st_size)
            except OSError:
                pass
        ctl = aria2_done(f.with_name(f.name + ".aria2"))
        if ctl is not None:
            best = max(best, ctl)
        return min(best, size) if size else best

    # ---- the overlay
    def muse_section(self, manifest: dict):
        m = self.overlay.get("muse") or {}
        sec = manifest.get(m.get("section", "image"))
        if not isinstance(sec, dict) or not isinstance(sec.get("diffusion"), dict):
            return None
        if m.get("match_url") and sec["diffusion"].get("url") != m["match_url"]:
            return None                       # this tier's default is no civitai file: nothing to replace
        return sec

    def muse_installed(self, manifest: dict = None) -> bool:
        sec = self.muse_section(manifest if manifest is not None else (self.base or {}))
        files = walk_files(sec) if sec else []
        return bool(files) and all(self.present(e) for e in files)

    def apply(self, manifest: dict, remember: bool = True) -> dict:
        """the effective manifest of this profile. Private (or no overlay): the SAME object, untouched."""
        if not self.public or not self.overlay:
            return manifest
        if remember:
            self.base = copy.deepcopy(manifest)
        m = copy.deepcopy(manifest)
        ov = self.overlay
        for k, sec in (ov.get("sections") or {}).items():
            sec = copy.deepcopy(sec)
            src = sec.pop("from", None)
            if src:                               # a tier entry under its own name (its file / url / size from the tier)
                ent = node_at(manifest, src)
                if not isinstance(ent, dict) or "file" not in ent:
                    continue
                sec = {**copy.deepcopy(ent), **sec}
            m[k] = sec
        for k, patch in (ov.get("patch") or {}).items():
            if isinstance(m.get(k), dict):
                _merge(m[k], patch)
        shas = ov.get("sha256") or {}
        for e in walk_files(m):
            if not e.get("sha256") and e["file"] in shas:
                e["sha256"] = shas[e["file"]]
        muse = self.muse_section(m)
        classic = m.get((ov.get("image_default") or {}).get("from", "image_classic"))
        if muse is not None and isinstance(classic, dict):
            mo = ov.get("muse") or {}
            if self.muse_installed(m):
                pass                              # the tier's own `image` / `image_safe`: the owner's behaviour
            else:
                sec = copy.deepcopy(muse)
                sec.update(addon=True, auth=copy.deepcopy(mo.get("auth") or {}),
                           licence_dir=mo.get("licence_dir"), licence_summary=list(mo.get("licence_summary") or []))
                m["image_muse"] = sec
                for key, spec in (("image", ov.get("image_default") or {}), ("image_safe", ov.get("image_safe") or {})):
                    src = m.get(spec.get("from", "image_classic"))
                    if not isinstance(src, dict):
                        continue
                    sec = copy.deepcopy(src)
                    sec.pop("addon", None)
                    for f in ("expander", "expander_parallel", "comment"):
                        if f in spec:
                            sec[f] = spec[f]
                    if spec.get("name"):
                        sec["name"] = spec["name"].format(short=short_name(src.get("name")), name=src.get("name"))
                    sec["public_default"] = True
                    m[key] = sec
        return m

    def refresh(self, manifest: dict) -> bool:
        """re-apply the overlay to the remembered tier manifest IN PLACE (MM.manifest is the same object) — after
        Muse arrived. True when the effective manifest changed."""
        if not self.public or self.base is None:
            return False
        new = self.apply(self.base, remember=False)
        if new == manifest:
            return False
        manifest.clear()
        manifest.update(new)
        return True

    # ---- the essentials
    def essential_items(self, manifest: dict) -> list:
        out = []
        if not self.public:
            return out
        muse = self.muse_installed(manifest)
        for it in self.overlay.get("essentials") or []:
            entries = []
            for p in it.get("paths") or []:
                entries.extend(walk_files(node_at(manifest, p)))
            seen, uniq = set(), []
            for e in entries:
                if e["file"] not in seen:
                    seen.add(e["file"])
                    uniq.append(e)
            out.append({"key": it["key"], "label": it.get("label") or it["key"], "feature": it.get("feature") or "",
                        "entries": uniq, "skipped": bool(it.get("skip_with_muse") and muse)})
        return out


def _merge(dst: dict, patch: dict):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


def plural(phrase) -> bool:
    """[R88] English number of a download label or feature, for the verb that follows: "the dictionaries", "pictures",
    "dictionary look-ups", "apps, web pages, games and answers" are plural; "the language model", "the offline Wikipedia",
    "translation", "offline search", "looking at pictures" (a gerund phrase) and "this" are singular"""
    p = str(phrase or "").strip().lower()
    if not p:
        return False
    if " and " in p or "," in p:
        return True
    words = p.split()
    if len(words) > 1 and words[0].endswith("ing"):
        return False                                   # "looking at pictures" = one activity
    last = words[-1].strip(".")
    return len(last) > 2 and last.endswith("s") and not last.endswith(("ss", "us", "is", "ics"))


def missing_sentence(label: str, feature: str, status: str, pct=0, left_gb="0", error=None) -> str:
    """[R87; R88 the grammar] the sentence a feature answers while its first-boot download is not done: "the picture model
    is downloading (37 %) — pictures work when it is done", "the dictionaries are queued for download (0.3 GB to go) —
    dictionary look-ups work when they arrive", "the offline Wikipedia … — offline search works when it arrives"."""
    many = plural(label)
    feat = feature or "this"
    is_, needs = ("are", "need") if many else ("is", "needs")
    work = "work" if plural(feat) else "works"
    done, arrives = ("they are done", "they arrive") if many else ("it is done", "it arrives")
    if status in ("downloading", "verifying"):
        return f"{label} {is_} downloading ({pct} %) — {feat} {work} when {done}"
    if status == "waiting":
        return (f"{label} {is_} waiting for the network ({pct} % downloaded) — the download resumes by itself; "
                f"{feat} {work} when {done}")
    if status == "queued":
        return f"{label} {is_} queued for download ({left_gb} GB to go) — {feat} {work} when {arrives}"
    if status == "paused":
        return f"{label} {needs} a download ({left_gb} GB, paused) — resume it in ⚙ Models & licences"
    if status == "error":
        return f"{label} could not be downloaded ({error}) — retry it in ⚙ Models & licences"
    return f"{label} {needs} a download ({left_gb} GB) — start it in ⚙ Models & licences"


def _gb(n) -> str:
    return f"{n / 1e9:.1f}"


# ============================================================================ the first-boot downloads
class Essentials:
    """the public profile's first-boot downloads (see the module docstring). One background task; GET state via
    snapshot(); start() / pause() / resume(). The user's pause survives a restart (state_file)."""

    def __init__(self, prof: Profile, manifest_fn, log_dir: Path, state_file: Path, disk_free=None, on_item=None):
        self.prof, self.manifest_fn, self.log_dir, self.state_file = prof, manifest_fn, Path(log_dir), Path(state_file)
        self.disk_free, self.on_item = disk_free, on_item
        self.task, self.proc = None, None
        self.state, self.current, self.reason, self.retry_at = "idle", None, None, None
        self.errors, self.attempts, self.fails = {}, {}, 0
        self.verifying = None
        self._wake = None
        self.paused = bool(self._load().get("paused"))

    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except Exception:
            return {}

    def _save(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"paused": self.paused, "at": time.time()}))
        except OSError as e:
            print(f"[kernel] WARN downloads state not saved: {e}", flush=True)

    def items(self) -> list:
        return self.prof.essential_items(self.manifest_fn())

    def item_of(self, file: str):
        for it in self.items():
            if any(e["file"] == file for e in it["entries"]):
                return it
        return None

    def missing(self) -> list:
        return [(it, e) for it in self.items() if not it["skipped"] for e in it["entries"] if not self.prof.present(e)]

    # ---- state for the API, the menubar and the feature notes
    def item_status(self, it) -> dict:
        done = sum(self.prof.done_bytes(e) for e in it["entries"])
        total = sum(e.get("size_bytes") or 0 for e in it["entries"])
        miss = [e["file"] for e in it["entries"] if not self.prof.present(e)]
        if it["skipped"]:
            status = "skipped"
        elif not miss:
            status = "present"
        elif self.current and self.current[0] == it["key"]:
            status = "verifying" if self.verifying else ("waiting" if self.state == "waiting" else "downloading")
        elif it["key"] in self.errors:
            status = "error"
        elif self.paused:
            status = "paused"
        elif self.running():
            status = "queued"
        else:
            status = "missing"
        return {"key": it["key"], "label": it["label"], "feature": it["feature"], "status": status,
                "done_bytes": done, "total_bytes": total, "pct": int(done * 100 / total) if total else 100,
                "files": len(it["entries"]), "missing": miss, "error": self.errors.get(it["key"])}

    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    def snapshot(self) -> dict:
        items = [self.item_status(it) for it in self.items()]
        live = [i for i in items if i["status"] != "skipped"]
        done, total = sum(i["done_bytes"] for i in live), sum(i["total_bytes"] for i in live)
        complete = all(i["status"] == "present" for i in live)
        state = "done" if complete else ("paused" if self.paused else self.state if self.running() else
                                         "error" if self.errors else "idle")
        return {"profile": self.prof.name, "state": state, "paused": self.paused, "complete": complete,
                "current": {"key": self.current[0], "file": self.current[1]} if self.current and self.running() else None,
                "reason": self.reason if state == "waiting" else None,
                "retry_in": max(0, round(self.retry_at - time.monotonic())) if state == "waiting" and self.retry_at else None,
                "done_bytes": done, "total_bytes": total, "missing_bytes": max(0, total - done),
                "pct": int(done * 100 / total) if total else 100, "items": items}

    def note(self, entries, what: str = None):
        """None when every file of `entries` is on disk or none of the missing ones is an essential; else the sentence
        a feature answers instead of failing ("… is downloading (37 %) — pictures work when it is done")."""
        if not self.prof.public:
            return None
        missing = [e for e in entries if not self.prof.present(e)]
        if not missing:
            return None
        it = None
        for e in missing:
            it = self.item_of(e["file"])
            if it:
                break
        if not it:
            return None
        st = self.item_status(it)
        return missing_sentence(what or st["label"], st["feature"], st["status"], st["pct"],
                                _gb(max(0, st["total_bytes"] - st["done_bytes"])), st.get("error"))

    # ---- control
    def start(self) -> bool:
        if self.paused or self.running() or not self.prof.public:
            return False
        if not self.missing():
            self.state = "done"
            return False
        self.task = asyncio.create_task(self._run())
        return True

    async def pause(self):
        self.paused = True
        self._save()
        await self._stop_proc()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        self.state, self.current = "paused", None

    def resume(self) -> bool:
        self.paused = False
        self._save()
        self.errors.clear()
        self.attempts.clear()
        if self._wake:
            self._wake.set()
        return self.start()

    async def _stop_proc(self):
        p = self.proc
        if p and p.returncode is None:
            try:
                p.terminate()                    # aria2c writes its control file on SIGTERM: the next start resumes
                await asyncio.wait_for(p.wait(), 10)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    async def _sleep(self, s: float):
        self._wake = asyncio.Event()
        self.retry_at = time.monotonic() + s
        try:
            await asyncio.wait_for(self._wake.wait(), s)
        except asyncio.TimeoutError:
            pass
        finally:
            self.retry_at = None

    async def _run(self):
        try:
            while True:
                todo = [(it, e) for it, e in self.missing() if it["key"] not in self.errors]
                if not todo:
                    self.state = "error" if self.errors else "done"
                    if not self.errors:
                        print("[kernel] downloads: every essential file is on disk", flush=True)
                    return
                it, e = todo[0]
                self.current, self.state, self.reason = (it["key"], e["file"]), "downloading", None
                ok, kind, msg = await fetch_aria2(self.prof, e, self.log_dir, self, self.disk_free)
                if kind == "paused":
                    return
                if ok:
                    self.fails = 0
                    if self.on_item:
                        try:
                            await self.on_item(it["key"], e)
                        except Exception as ex:
                            print(f"[kernel] WARN after download of {e['file']}: {ex}", flush=True)
                    continue
                if kind == "checksum":
                    self.attempts[e["file"]] = self.attempts.get(e["file"], 0) + 1
                    if self.attempts[e["file"]] < 2:
                        continue
                if kind in ("hard", "checksum", "disk"):
                    self.errors[it["key"]] = msg
                    print(f"[kernel] download of {e['file']} stopped: {msg}", flush=True)
                    continue
                # the network's fault: wait until the server answers again, then aria2 resumes from its control file
                print(f"[kernel] download of {e['file']} interrupted ({msg}) — waiting for the network", flush=True)
                self.state = "waiting"
                while True:
                    wait = backoff(self.fails)
                    self.fails += 1
                    self.reason = f"offline or the server is unreachable — retrying in {wait:g} s"
                    await self._sleep(wait)
                    if await reachable(e["url"]):
                        break
        finally:
            self.current = None
            self.proc = None


async def fetch_aria2(prof: Profile, e: dict, log_dir: Path, owner=None, disk_free=None) -> tuple:
    """one {url, file} entry with aria2c (no credentials ever): (True, None, None) | (False, kind, message) with kind
    network | hard | disk | checksum | paused. Resumes a partial download from its control file."""
    dest = prof.dest(e)
    async with file_lock(dest):
        if prof.present(e):
            return True, None, None
        need = (e.get("size_bytes") or 0) - prof.done_bytes(e)
        free = disk_free(dest.parent if dest.parent.exists() else prof.models_dir) if disk_free else None
        if free is not None and need > 0 and free < need + DISK_MARGIN:
            return False, "disk", f"not enough disk space ({_gb(need)} GB needed, {_gb(free)} GB free)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        ctl = dest.with_name(dest.name + ".aria2")
        if dest.exists() and not ctl.exists() and isinstance(e.get("size_bytes"), int) \
                and dest.stat().st_size >= e["size_bytes"]:
            dest.unlink()                          # a complete-looking file that failed its check: start over
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / "downloads.log", "a", encoding="utf-8")
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} aria2c {e['file']} <- {e['url']}\n")
        log.flush()
        try:
            proc = await asyncio.create_subprocess_exec(
                "aria2c", "-x8", "-s8", "--continue=true", "--console-log-level=warn", "--summary-interval=0",
                "--file-allocation=none", "--auto-save-interval=5", f"--disk-cache={os.environ.get('KAIROS_ARIA2_DISK_CACHE', '16M')}",
                "--max-tries=3", "--retry-wait=2",
                "--connect-timeout=20", "--timeout=60", "--allow-overwrite=false", "--auto-file-renaming=false",
                "-d", str(dest.parent), "-o", dest.name, e["url"],
                stdout=log, stderr=asyncio.subprocess.STDOUT)
        except FileNotFoundError:
            log.close()
            return False, "hard", "aria2c is not installed"
        if owner is not None:
            owner.proc = proc
        try:
            rc = await proc.wait()
        finally:
            log.close()
            if owner is not None:
                owner.proc = None
        if owner is not None and getattr(owner, "paused", False):
            return False, "paused", "paused"
        if rc != 0:
            if rc in ARIA2_HARD:
                return False, "hard" if rc != 9 else "disk", f"{ARIA2_HARD[rc]} (aria2c exit {rc})"
            return False, "network", f"aria2c exit {rc}"
        if isinstance(e.get("size_bytes"), int) and dest.stat().st_size != e["size_bytes"]:
            dest.unlink(missing_ok=True)
            return False, "checksum", f"the download has the wrong size ({dest.name})"
        for algo in ("sha256", "sha512"):
            if e.get(algo):
                if owner is not None:
                    owner.verifying = e["file"]
                try:
                    have = await asyncio.to_thread(file_digest, dest, algo)
                finally:
                    if owner is not None:
                        owner.verifying = None
                if have != e[algo].lower():
                    dest.unlink(missing_ok=True)
                    return False, "checksum", f"the download did not match its {algo} ({dest.name})"
        print(f"[kernel] downloaded {e['file']} ({_gb(dest.stat().st_size)} GB, checksum ok)", flush=True)
        return True, None, None


# ============================================================================ Muse with the user's civitai key
NET_ERRORS = (ConnectionError, asyncio.TimeoutError, OSError) + ((aiohttp.ClientError,) if aiohttp else ())
KEY_RE = re.compile(r"^[A-Za-z0-9_\-.]{16,256}$")


class MuseError(RuntimeError):
    pass


class Muse:
    """the public profile's optional Muse download with the user's own civitai API key (see the module docstring)"""

    def __init__(self, prof: Profile, manifest_fn, log_dir: Path, state_file: Path, secrets_dir: Path = None,
                 disk_free=None, on_installed=None):
        self.prof, self.manifest_fn, self.log_dir = prof, manifest_fn, Path(log_dir)
        self.state_file = Path(state_file)
        self.secrets_dir = Path(secrets_dir or os.environ.get("KAIROS_SECRETS_DIR") or SECRETS_DIR)
        self.key_path = self.secrets_dir / "civitai.key"
        self.disk_free, self.on_installed = disk_free, on_installed
        self.task, self.proc = None, None
        self.state, self.error, self.current, self.reason, self.retry_at = "idle", None, None, None, None
        self.paused, self.fails = False, 0
        self._wake = None

    # ---- the key: root-only, never echoed
    def key_set(self) -> bool:
        try:
            return self.key_path.stat().st_size > 0
        except OSError:
            return False

    def _key(self):
        try:
            k = self.key_path.read_text(encoding="ascii").strip()
            return k or None
        except (OSError, UnicodeDecodeError):
            return None

    def store_key(self, key) -> None:
        if not isinstance(key, str) or not KEY_RE.match(key.strip()):
            raise ValueError("that does not look like a civitai API key (letters and digits, from civitai.com → "
                             "Account settings → API Keys)")
        key = key.strip()
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secrets_dir, 0o700)
        tmp = self.key_path.with_name(f".civitai.key.{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, key.encode("ascii") + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.key_path)

    def remove_key(self) -> bool:
        try:
            self.key_path.unlink()
            return True
        except OSError:
            return False

    def scrub(self, text) -> str:
        """an error text with the key (and every URL's query string) removed — before it reaches a log or a page"""
        s = str(text or "")
        k = self._key()
        if k:
            s = s.replace(k, "[key]")
        return re.sub(r"(https?://[^\s'\"?]+)\?[^\s'\"]*", r"\1?…", s)

    # ---- state
    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except Exception:
            return {}

    def _save(self, **kw):
        d = {**self._load(), **kw}
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(d))
        except OSError:
            pass

    def section(self):
        m = self.manifest_fn()
        sec = m.get("image_muse")
        return sec if isinstance(sec, dict) else None

    def auth(self) -> dict:
        sec = self.section() or {}
        a = dict(sec.get("auth") or (self.prof.overlay.get("muse") or {}).get("auth") or {})
        a["url"] = os.environ.get("KAIROS_MUSE_URL") or a.get("url")
        mode = os.environ.get("KAIROS_CIVITAI_AUTH") or self._load().get("auth_mode") or a.get("mode") or "header"
        a["mode"] = mode if mode in ("header", "query") else "header"
        return a

    def installed(self) -> bool:
        return self.prof.muse_installed(self.prof.base) if self.prof.public else self.prof.muse_installed(self.manifest_fn())

    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    def status(self) -> dict:
        ov = self.prof.overlay.get("muse") or {}
        sec = self.section()
        files = walk_files(sec) if sec else []
        done = sum(self.prof.done_bytes(e) for e in files)
        total = sum(e.get("size_bytes") or 0 for e in files)
        a = self.auth() if self.prof.public else {}
        installed = self.installed()
        state = "installed" if installed else ("paused" if self.paused else self.state if self.running() else
                                               "error" if self.error else "idle")
        part = (sec or {}).get((a.get("part") if a else None) or "diffusion") if sec else None
        started = bool(part) and self.prof.done_bytes(part) > 0      # the civitai file itself is under way
        return {"available": self.prof.public, "profile": self.prof.name, "installed": installed, "started": started,
                "missing_bytes": max(0, total - done),
                "key_set": self.key_set() if self.prof.public else False, "state": state,
                "error": self.error if state == "error" else None,
                "reason": self.reason if state == "waiting" else None,
                "current": self.current if self.running() else None,
                "done_bytes": done, "total_bytes": total, "pct": int(done * 100 / total) if total else (100 if installed else 0),
                "name": (sec or {}).get("name") or (self.manifest_fn().get(ov.get("section", "image")) or {}).get("name"),
                "auth_mode": a.get("mode"), "auth_modes": list(a.get("modes") or ["header", "query"]),
                "host": urlsplit(a.get("url") or "").hostname if a.get("url") else None,
                "signup": a.get("signup"), "key_help": a.get("key_help"),
                "licence_summary": list(ov.get("licence_summary") or []), "licence_dir": ov.get("licence_dir"),
                "accepted": bool(self._load().get("accepted_at")),
                "reason_unavailable": None if self.prof.public else "this image ships its own picture models"}

    # ---- control
    def start(self, accept: bool, auth_mode: str = None):
        """-> (http status, body)"""
        if not self.prof.public:
            return 409, {"error": "Muse is managed by this image's own build (private profile)", "available": False}
        if self.installed():
            return 200, {"ok": True, "already": True}
        if not self.section():
            return 409, {"error": "this tier has no Muse download"}
        if not self.key_set():
            return 409, {"error": "Muse needs your own civitai API key first (free civitai account): paste it in "
                                  "⚙ Models & licences", "needs_key": True}
        if not accept:
            return 409, {"error": "read Muse's licence summary and accept it first (accept_licence: true)",
                         "needs_licence": True, "licence_summary": self.status()["licence_summary"]}
        if auth_mode in ("header", "query"):
            self._save(auth_mode=auth_mode)
        self._save(accepted_at=time.time())
        self.paused, self.error = False, None
        if self._wake:
            self._wake.set()
        if self.running():
            return 200, {"ok": True, "downloading": True}
        self.task = asyncio.create_task(self._run())
        return 200, {"ok": True, "started": True, "download_gb": round(self.status()["total_bytes"] / 1e9, 2)}

    async def pause(self):
        self.paused = True
        p = self.proc
        if p and p.returncode is None:
            try:
                p.terminate()
                await asyncio.wait_for(p.wait(), 10)
            except Exception:
                pass
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        self.state, self.current = "paused", None

    def resume_on_boot(self):
        """a Muse download the user started (key + licence accepted) and a restart interrupted continues"""
        if self.prof.public and not self.installed() and self.key_set() and self._load().get("accepted_at"):
            sec = self.section()
            if sec and any(self.prof.done_bytes(e) > 0 for e in walk_files(sec)):
                self.task = asyncio.create_task(self._run())

    async def _sleep(self, s: float):
        self._wake = asyncio.Event()
        self.retry_at = time.monotonic() + s
        try:
            await asyncio.wait_for(self._wake.wait(), s)
        except asyncio.TimeoutError:
            pass
        finally:
            self.retry_at = None

    async def _run(self):
        self.state, self.error, self.reason = "downloading", None, None
        try:
            while True:
                sec = self.section()
                if not sec:
                    break
                auth = self.auth()
                part = (sec.get("auth") or {}).get("part", "diffusion")
                todo = [e for e in walk_files(sec) if not self.prof.present(e)]
                if not todo:
                    break
                # the civitai file first: a bad key fails before gigabytes of encoder download
                todo.sort(key=lambda e: 0 if e is sec.get(part) else 1)
                e = todo[0]
                self.current, self.state, self.reason = e["file"], "downloading", None
                try:
                    if e is sec.get(part):
                        await self._fetch_auth(e, auth)
                    else:
                        ok, kind, msg = await fetch_aria2(self.prof, e, self.log_dir, self, self.disk_free)
                        if not ok:
                            raise (ConnectionError(msg) if kind == "network" else MuseError(msg))
                    self.fails = 0
                except NET_ERRORS as ex:
                    if isinstance(ex, (PermissionError, FileNotFoundError, IsADirectoryError)):
                        raise                    # the disk's fault, not the network's: an error the user sees
                    print(f"[kernel] Muse download interrupted ({self.scrub(type(ex).__name__)}) — waiting for the network",
                          flush=True)
                    self.state = "waiting"
                    while True:
                        wait = backoff(self.fails)
                        self.fails += 1
                        self.reason = f"offline or civitai is unreachable — retrying in {wait:g} s"
                        await self._sleep(wait)
                        if await reachable(auth.get("url") or e.get("url")):
                            break
            self.state, self.current = "done", None
            print("[kernel] Muse is installed (the user's own civitai download)", flush=True)
            if self.on_installed:
                await self.on_installed()
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            self.state, self.error = "error", self.scrub(ex)
            print(f"[kernel] Muse download failed: {self.error}", flush=True)
        finally:
            self.current = None

    async def _fetch_auth(self, e: dict, auth: dict):
        """the civitai file: the key goes ONLY to the configured URL's own origin (header or ?token=); a redirect to
        another host (civitai's CDN) is followed WITHOUT it. Resumes a .part file with Range; size + sha256 checked."""
        key = self._key()
        if not key:
            raise MuseError("no civitai API key is stored")
        base = auth.get("url") or e.get("url")
        origin = _origin(base)
        dest = self.prof.dest(e)
        tmp = dest.with_name(dest.name + ".part")
        async with file_lock(dest):
            if self.prof.present(e):
                return
            need = (e.get("size_bytes") or 0) - (tmp.stat().st_size if tmp.exists() else 0)
            free = self.disk_free(dest.parent if dest.parent.exists() else self.prof.models_dir) if self.disk_free else None
            if free is not None and need > 0 and free < need + DISK_MARGIN:
                raise MuseError(f"not enough disk space ({_gb(need)} GB needed, {_gb(free)} GB free)")
            dest.parent.mkdir(parents=True, exist_ok=True)
            target, hops = base, 0
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=120)
            async with aiohttp.ClientSession(timeout=timeout, auto_decompress=False) as s:
                while True:
                    start = tmp.stat().st_size if tmp.exists() else 0
                    headers = {"User-Agent": "kAIrOS (public image; the user's own civitai key)"}
                    if start:
                        headers["Range"] = f"bytes={start}-"
                    url = target
                    if _origin(target) == origin:
                        if auth.get("mode") == "query":
                            url = _with_query(target, token=key)
                        else:
                            headers["Authorization"] = f"Bearer {key}"
                    async with s.get(url, headers=headers, allow_redirects=False) as r:
                        if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                            hops += 1
                            if hops > 6:
                                raise MuseError("too many redirects from civitai")
                            target = urljoin(target, r.headers["Location"])
                            continue
                        if r.status in (401, 403):
                            raise MuseError(f"civitai refused the download (HTTP {r.status}) — check the API key "
                                            "in ⚙ Models & licences (civitai.com → Account settings → API Keys)")
                        if r.status == 416 and start and isinstance(e.get("size_bytes"), int) and start >= e["size_bytes"]:
                            break
                        if r.status not in (200, 206):
                            if r.status >= 500 or r.status == 429:
                                raise ConnectionError(f"HTTP {r.status} from {urlsplit(target).hostname}")
                            raise MuseError(f"HTTP {r.status} from {urlsplit(target).hostname}")
                        mode = "ab" if (r.status == 206 and start) else "wb"
                        with open(tmp, mode) as f:
                            async for chunk in r.content.iter_chunked(1 << 20):
                                f.write(chunk)
                        break
            size = tmp.stat().st_size
            if isinstance(e.get("size_bytes"), int) and size != e["size_bytes"]:
                if size > e["size_bytes"]:
                    tmp.unlink(missing_ok=True)
                    raise MuseError("the download is larger than expected — it was discarded")
                raise ConnectionError(f"the download stopped at {_gb(size)} GB")
            if e.get("sha256"):
                self.state = "verifying"
                have = await asyncio.to_thread(file_digest, tmp, "sha256")
                if have != e["sha256"].lower():
                    tmp.unlink(missing_ok=True)
                    raise MuseError("the download did not match Muse's sha256 — it was discarded")
            os.replace(tmp, dest)
            print(f"[kernel] downloaded {e['file']} from {urlsplit(base).hostname} ({_gb(size)} GB, checksum ok)", flush=True)


def _origin(url: str) -> tuple:
    p = urlsplit(url or "")
    return (p.scheme, (p.hostname or "").lower(), p.port or {"http": 80, "https": 443}.get(p.scheme))


def _with_query(url: str, **params) -> str:
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k not in params] + list(params.items())
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


# ============================================================================ the licences
def licence_dir() -> Path:
    env = os.environ.get("KAIROS_LICENCE_DIR")
    if env:
        return Path(env)
    return LICENCE_DIR_DEFAULT if (LICENCE_DIR_DEFAULT / "index.json").is_file() else REPO_LICENCE_DIR


def licence_index() -> dict:
    try:
        return json.loads((licence_dir() / "index.json").read_text(encoding="utf-8"))
    except Exception as e:
        return {"models": [], "error": f"no licence index ({type(e).__name__})"}


def licences(manifest: dict, prof: Profile) -> dict:
    """GET /api/licences: every model in the index with its licence, attribution and texts — `installed` when one of
    its files is on disk — plus the installed manifest files the index does not name (with their manifest line)."""
    idx = licence_index()
    d = licence_dir()
    covered, out = set(), []
    prefixes = []
    for m in idx.get("models", []):
        files = list(m.get("files") or [])
        covered.update(files)
        prefixes.extend((p, m["id"]) for p in m.get("prefixes") or [])
        installed = any(prof.present({"file": f, "store": "knowledge" if f.endswith(".zim") else None}) for f in files)
        texts = [t for t in m.get("texts") or [] if (d / m["id"] / t).is_file()]
        out.append({"id": m["id"], "name": m.get("name"), "licence": m.get("licence"), "attribution": m.get("attribution"),
                    "note": m.get("note"), "files": files, "installed": installed, "texts": texts,
                    "links": m.get("links") or {}})
    other, seen = [], set()
    for key, sec in manifest.items():
        if not isinstance(sec, dict):
            continue
        for e in walk_files(sec):
            if e["file"] in covered or e["file"] in seen or not prof.present(e):
                continue
            hit = next((mid for p, mid in prefixes if e["file"].startswith(p)), None)
            if hit:                                   # a file family (dict/…, wiktionary_…): its entry is installed
                for m in out:
                    if m["id"] == hit:
                        m["installed"] = True
                continue
            seen.add(e["file"])
            other.append({"file": e["file"], "section": key, "name": sec.get("name"), "licence": sec.get("licence"),
                          "url": e.get("url")})
    return {"dir": str(LICENCE_DIR_DEFAULT), "readme": (d / "README.txt").is_file(), "models": out, "other": other,
            "error": idx.get("error")}


_TEXT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def licence_text(model_id: str, name: str):
    """(text, content type) of one licence file the index names, or None — never a path outside the licence dir"""
    if not (isinstance(model_id, str) and isinstance(name, str) and _TEXT_NAME.match(model_id) and _TEXT_NAME.match(name)):
        return None
    d = licence_dir()
    if model_id == "_" and name == "README.txt":
        p = d / name
    else:
        entry = next((m for m in licence_index().get("models", []) if m.get("id") == model_id), None)
        if not entry or name not in (entry.get("texts") or []):
            return None
        p = d / model_id / name
    try:
        rp = p.resolve()
        if d.resolve() not in rp.parents or not rp.is_file():
            return None
        return rp.read_text(encoding="utf-8", errors="replace"), "text/plain"
    except OSError:
        return None
