#!/usr/bin/env python3
"""prompt-sync.py — kAIrOS's system prompts as plain text files [R92] (docs/PROMPTS-2026-10-24.md).

Every prompt the models are told lives in kernel/prompts.py (the registry at its end names them all) plus the dock
apps' forge requests in shell/index.html. A running kernel reads overrides — <NAME>.txt in its override directory
(KAIROS_PROMPTS_DIR, default $VIBEOS_DIR/prompts; /opt/vibeos/prompts on the stick) — and changes them live through
/api/prompts (the desktop's ✎ Prompts panel). This script moves prompts between a folder of text files, a running
kernel, an override directory and the repository:

  prompt-sync.py list   [--kernel URL]                      every prompt: name, kind, where it lives, edited?, chars
  prompt-sync.py export DIR [--kernel URL]                  <NAME>.txt for every prompt (+ INDEX.md): the repo's shipped
                                                            defaults, or a kernel's LIVE texts with --kernel
  prompt-sync.py diff   DIR [--kernel URL] [--full]         which files in DIR differ from the defaults (and from the kernel)
  prompt-sync.py push   DIR (--kernel URL | --dir OVERRIDES) the files that differ from the defaults become overrides:
                                                            live over the API, or copied into an override directory
  prompt-sync.py pull   (--kernel URL | --dir OVERRIDES) DIR the kernel's / the directory's overrides -> DIR
  prompt-sync.py reset  (NAME… | --all) (--kernel URL | --dir OVERRIDES)   back to the shipped prompt(s)
  prompt-sync.py bake   DIR [--only NAME,…] [--check]       the edited texts become the NEW DEFAULTS in kernel/prompts.py
                                                            (and shell/index.html for DOCK_*): backup outside the repo,
                                                            verified by re-import, --check = report only
  prompt-sync.py html   [-o FILE] [--kernel URL] [--fragment]  the architecture + prompts document (every prompt in full)

  --token T   the kernel's X-Kairos-Token for push/reset over the API (default: /run/kairos/token when readable,
              else $KAIROS_TOKEN; a loopback caller needs none)
  --repo P    the repository root (default: this script's parent directory's parent)

Typical loops: edit in the OS (✎ Prompts) → `pull --kernel http://127.0.0.1:1337 ~/kairos/prompts` → `bake` → the suite →
commit; or edit the files → `push --kernel …` (live) / `push --dir /opt/vibeos/prompts` (a mounted root) → test.
Files equal to the shipped default are never overrides (push removes a stale one). Parts (STYLE, the ADULT_* rules, the
_KREA_* rules, the _*_TAIL texts) are baked into the prompts built from them: export lists them, push/bake skip them.
"""
import argparse
import ast
import datetime as dt
import difflib
import html
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parent
DOCK_RE = re.compile(r'data-app="([a-z0-9-]+)"\s+data-prompt="([^"]*)"')
KIND_ORDER = ["system", "instruction", "template", "hint", "dock", "part", "legacy"]
KIND_LABEL = {"system": "system prompts", "instruction": "instructions and headers", "template": "templates (formatted at use)",
              "hint": "media prompt hints", "dock": "dock apps: forge requests", "part": "parts (building blocks, read-only)",
              "legacy": "legacy (not sent to any model)"}


# ---------------------------------------------------------------------------------------------- the registry (repo)
def load_prompts(repo: Path):
    """kernel/prompts.py as a fresh module, the dock prompts of shell/index.html registered the way the kernel does"""
    path = repo / "kernel" / "prompts.py"
    if not path.is_file():
        sys.exit(f"no kernel/prompts.py under {repo} (use --repo)")
    loader = importlib.machinery.SourceFileLoader("kairos_prompts_sync", str(path))
    spec = importlib.util.spec_from_loader("kairos_prompts_sync", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    for app, name, line, text in dock_prompts(repo):
        mod.register(name, text, f"shell/index.html:{line}",
                     f"the dock's {app} app: the forge request the shell sends (POST /api/app, app_id {app})", kind="dock")
    return mod


def dock_prompts(repo: Path):
    """(app, DOCK_NAME, line, text) for every system-app button of shell/index.html"""
    out = []
    index = repo / "shell" / "index.html"
    if not index.is_file():
        return out
    for i, line in enumerate(index.read_text(encoding="utf-8").splitlines(), 1):
        for app, raw in DOCK_RE.findall(line):
            out.append((app, f"DOCK_{app.upper().replace('-', '_')}", i, html.unescape(raw)))
    return out


def repo_catalogue(repo: Path) -> list:
    mod = load_prompts(repo)
    items = []
    for name in mod.names():
        it = mod.info(name)
        it["text"], it["default"] = mod.get(name), mod.DEFAULTS[name]
        items.append(it)
    return items


# ---------------------------------------------------------------------------------------------- the kernel (HTTP)
class Kernel:
    def __init__(self, url: str, token: str = None):
        self.url = url.rstrip("/")
        self.token = token or self._token()

    @staticmethod
    def _token():
        try:
            return Path("/run/kairos/token").read_text().strip()
        except OSError:
            return os.environ.get("KAIROS_TOKEN", "")

    def _call(self, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Kairos-Token"] = self.token
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
            except ValueError:
                return e.code, {"error": str(e)}
        except (urllib.error.URLError, OSError) as e:
            sys.exit(f"kernel {self.url} not reachable: {e}")

    def catalogue(self) -> list:
        st, r = self._call("/api/prompts?full=1")
        if st != 200:
            sys.exit(f"GET /api/prompts: {st} {r.get('error', r)}")
        self.meta = r
        return r["prompts"]

    def set(self, name: str, text: str):
        return self._call(f"/api/prompts/{name}", {"text": text})

    def reset(self, name: str):
        return self._call(f"/api/prompts/{name}", {"reset": True})

    def reload(self):
        return self._call("/api/prompts", {"reload": True})


def catalogue(args) -> list:
    """the prompts, from the kernel when --kernel is given (live texts), else from the repository (defaults)"""
    if getattr(args, "kernel", None):
        return Kernel(args.kernel, args.token).catalogue()
    return repo_catalogue(args.repo)


def by_name(items) -> dict:
    return {it["name"]: it for it in items}


def read_dir(d: Path, items) -> dict:
    """{name: text} for every <NAME>.txt in DIR that names a registered prompt (an editor's trailing newline dropped when
    the default has none); unknown files are reported"""
    known = by_name(items)
    out, unknown = {}, []
    for f in sorted(d.glob("*.txt")):
        name = f.stem
        if name not in known:
            unknown.append(f.name)
            continue
        text = f.read_text(encoding="utf-8")
        if text.endswith("\n") and not known[name]["default"].endswith("\n"):
            text = text[:-1]
        out[name] = text
    if unknown:
        print(f"ignored (no such prompt): {', '.join(unknown)}", file=sys.stderr)
    return out


def changed(items, texts: dict) -> dict:
    """the files whose text differs from the shipped default, editable ones only"""
    known = by_name(items)
    out = {}
    for name, text in texts.items():
        it = known[name]
        if not it.get("editable", True):
            if text != it["default"]:
                print(f"skipped {name}: a {it['kind']} — edit the prompt built from it", file=sys.stderr)
            continue
        if text != it["default"]:
            out[name] = text
    return out


# ---------------------------------------------------------------------------------------------- the commands
def cmd_list(args):
    items = catalogue(args)
    src = args.kernel or f"{args.repo} (shipped defaults)"
    print(f"{len(items)} prompts from {src}\n")
    for kind in KIND_ORDER:
        rows = [it for it in items if it["kind"] == kind]
        if not rows:
            continue
        print(f"== {KIND_LABEL.get(kind, kind)} ==")
        for it in rows:
            flag = "EDITED" if it.get("overridden") else ("rebuilt" if it.get("rebuilt") else "")
            print(f"  {it['name']:<30} {it['chars']:>6} chars  {it['where']:<26} {flag:<7} {it.get('note', '')}")
        print()
    return 0


def write_index(d: Path, items, source: str):
    lines = [f"# kAIrOS prompts — exported {dt.datetime.now():%Y-%m-%d %H:%M} from {source}", "",
             "One `<NAME>.txt` per prompt (the text verbatim). Edit a file, then `scripts/prompt-sync.py push DIR --kernel URL`",
             "(live) or `push DIR --dir <override dir>`; `bake DIR` makes the texts the shipped defaults. Parts are read-only.", "",
             "| name | kind | where it lives | chars | state | who sends it |", "|---|---|---|---|---|---|"]
    for it in items:
        state = "edited" if it.get("overridden") else ("rebuilt" if it.get("rebuilt") else ("read-only" if not it.get("editable", True) else ""))
        lines.append(f"| `{it['name']}` | {it['kind']} | `{it['where']}` | {it['chars']} | {state} | {it.get('note', '')} |")
    (d / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_export(args):
    items = catalogue(args)
    d = Path(args.dir)
    d.mkdir(parents=True, exist_ok=True)
    for it in items:
        (d / f"{it['name']}.txt").write_text(it["text"], encoding="utf-8")
    write_index(d, items, args.kernel or "the repository's defaults")
    n_over = sum(1 for it in items if it.get("overridden"))
    print(f"exported {len(items)} prompts to {d} ({n_over} with an override, INDEX.md written)")
    return 0


def cmd_pull(args):
    """the overrides only: from a kernel (its edited prompts) or from an override directory"""
    d = Path(args.dir)
    d.mkdir(parents=True, exist_ok=True)
    if args.kernel:
        items = Kernel(args.kernel, args.token).catalogue()
        edited = [it for it in items if it.get("overridden")]
        for it in edited:
            (d / f"{it['name']}.txt").write_text(it["text"], encoding="utf-8")
        print(f"pulled {len(edited)} edited prompt(s) from {args.kernel} into {d}: {', '.join(it['name'] for it in edited) or '—'}")
    else:
        src = Path(args.overrides)
        files = sorted(src.glob("*.txt"))
        for f in files:
            shutil.copy2(f, d / f.name)
        print(f"pulled {len(files)} file(s) from {src} into {d}")
    return 0


def cmd_diff(args):
    items = catalogue(args)
    known = by_name(items)
    texts = read_dir(Path(args.dir), items)
    diffs = changed(items, texts)
    if not diffs:
        print(f"{len(texts)} file(s) in {args.dir}: none differs from the shipped defaults")
    for name, text in diffs.items():
        it = known[name]
        live = it["text"] if args.kernel else None
        same_live = " (= the kernel's live text)" if live is not None and live == text else ""
        print(f"edited {name}: {len(it['default'])} -> {len(text)} chars{same_live}")
        if args.full:
            for ln in difflib.unified_diff(it["default"].splitlines(), text.splitlines(), f"{name} (default)", f"{name} ({args.dir})", lineterm="", n=2):
                print("   " + ln)
    if args.kernel:
        stale = [n for n, it in known.items() if it.get("overridden") and n not in texts]
        if stale:
            print(f"the kernel has overrides this folder does not: {', '.join(stale)}")
    return 1 if diffs else 0


def cmd_push(args):
    items = catalogue(args)
    texts = read_dir(Path(args.dir), items)
    diffs = changed(items, texts)
    known = by_name(items)
    if args.kernel:
        k = Kernel(args.kernel, args.token)
        for name, text in diffs.items():
            st, r = k.set(name, text)
            detail = f"{r.get('chars')} chars live" if st == 200 else r.get("error", "")
            print(f"{'set  ' if st == 200 else 'FAIL '} {name}: {st} {detail}")
        # a file equal to the default removes a stale override on the kernel
        for name, text in texts.items():
            if name not in diffs and known[name].get("overridden") and text == known[name]["default"]:
                st, r = k.reset(name)
                print(f"{'reset' if st == 200 else 'FAIL '} {name} (the file equals the default): {st}")
        print(f"pushed {len(diffs)} override(s) to {args.kernel}")
    else:
        od = Path(args.overrides)
        od.mkdir(parents=True, exist_ok=True)
        for name, text in diffs.items():
            (od / f"{name}.txt").write_text(text, encoding="utf-8")
            print(f"wrote {od / (name + '.txt')}")
        for name, text in texts.items():
            if name not in diffs and (od / f"{name}.txt").exists():
                (od / f"{name}.txt").unlink()
                print(f"removed {od / (name + '.txt')} (the file equals the default)")
        print(f"{len(diffs)} override(s) in {od} — a running kernel picks them up with "
              f"`POST /api/prompts {{\"reload\": true}}` or at its next start")
    return 0


def cmd_reset(args):
    items = catalogue(args)
    names = [it["name"] for it in items if it.get("overridden")] if args.all else args.names
    if args.kernel:
        k = Kernel(args.kernel, args.token)
        for name in names:
            st, r = k.reset(name)
            print(f"{'reset' if st == 200 else 'FAIL '} {name}: {st} {r.get('error', '')}")
    else:
        od = Path(args.overrides)
        names = [f.stem for f in od.glob("*.txt")] if args.all else names
        for name in names:
            try:
                (od / f"{name}.txt").unlink()
                print(f"removed {od / (name + '.txt')}")
            except FileNotFoundError:
                print(f"no override file for {name}")
    return 0


# ---------------------------------------------------------------------------------------------- bake
def _escape_triple(body: str) -> str:
    """the text inside a triple-double-quoted literal: backslashes doubled, triple quotes escaped, and a final quote that
    is not escaped yet escaped (it would close the literal)"""
    body = body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if body.endswith('"'):
        n = len(body) - len(body[:-1].rstrip("\\")) - 1       # backslashes right before the last quote
        if n % 2 == 0:
            body = body[:-1] + '\\"'
    return body


def plain_literal(text: str) -> str:
    return '"""' + _escape_triple(text) + '"""'


def fstring_literal(text: str, node: ast.JoinedStr, mod) -> str:
    """an f-string literal whose interpolations ({STYLE}, {ADULT_RULE_APP_SHORT}, …) are put back wherever their
    current value occurs in the new text — the baked prompt keeps following the parts"""
    body = text.replace("{", "{{").replace("}", "}}")
    seen = set()
    for part in node.values:
        if isinstance(part, ast.FormattedValue):
            expr = ast.unparse(part.value)
            if expr in seen:
                continue
            seen.add(expr)
            try:
                val = eval(expr, vars(mod))      # the module's own names (STYLE, the ADULT_* rules)
            except Exception:
                continue
            if isinstance(val, str) and val:
                esc = val.replace("{", "{{").replace("}", "}}")
                if esc in body:
                    body = body.replace(esc, "{" + expr + "}")
    return 'f"""' + _escape_triple(body) + '"""'


def _attr_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def backup(repo: Path, rel: str, tag: str) -> Path:
    root = Path(os.environ.get("KAIROS_HOME", str(Path.home() / "kairos"))) / "repo-backups" / f"{dt.date.today():%Y-%m-%d}"
    dst = root / f"{rel}.bak-{dt.date.today():%Y-%m-%d}{tag}-{dt.datetime.now():%H%M%S}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / rel, dst)
    return dst


def cmd_bake(args):
    repo = args.repo
    items = repo_catalogue(repo)
    known = by_name(items)
    texts = read_dir(Path(args.dir), items)
    if args.only:
        wanted = set(args.only.split(","))
        texts = {n: t for n, t in texts.items() if n in wanted}
    diffs = changed(items, texts)
    if not diffs:
        print("nothing to bake: no file differs from the shipped defaults")
        return 0
    mod = load_prompts(repo)
    src_path = repo / "kernel" / "prompts.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    spans = {n.targets[0].id: n for n in tree.body
             if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
    edits, docks, refused = [], {}, {}
    for name, text in diffs.items():
        if name.startswith("DOCK_"):
            docks[name] = text
            continue
        node = spans.get(name)
        if node is None:
            refused[name] = "not a plain module-level assignment in kernel/prompts.py"
            continue
        v = node.value
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            literal = plain_literal(text)
        elif isinstance(v, ast.JoinedStr):
            literal = fstring_literal(text, v, mod)
        else:
            base = mod._DERIVED.get(name)
            refused[name] = (f"a composition of {base[0]} + {base[1]} — bake those (or edit the override only)" if base
                             else f"built from other names ({ast.unparse(v)[:60]}) — edit the parts")
            continue
        edits.append((node.lineno, node.end_lineno, f"{name} = {literal}\n"))
    for name, why in refused.items():
        print(f"refused {name}: {why}")
    if args.check:
        for name in list(diffs):
            if name not in refused:
                print(f"would bake {name} ({len(known[name]['default'])} -> {len(diffs[name])} chars) into "
                      f"{'shell/index.html' if name.startswith('DOCK_') else 'kernel/prompts.py'}")
        return 1 if refused else 0
    baked = []
    if edits:
        bak = backup(repo, "kernel/prompts.py", "bake")
        lines = src.splitlines(keepends=True)
        for start, end, new in sorted(edits, key=lambda e: -e[0]):
            lines[start - 1:end] = [new]
        src_path.write_text("".join(lines), encoding="utf-8")
        # verify: every baked name equals its file, every other name keeps its value (compositions follow their base)
        try:
            new_mod = load_prompts(repo)
            problems = []
            for name in mod.names():
                if name.startswith("DOCK_"):
                    continue
                expected = diffs[name] if name in diffs and name not in refused else None
                if expected is None:
                    base = mod._DERIVED.get(name)
                    expected = (new_mod.get(base[0]) + new_mod.get(base[1])) if base else mod.get(name)
                if new_mod.get(name) != expected:
                    problems.append(name)
            if problems:
                raise RuntimeError("values differ after the bake: " + ", ".join(problems))
        except Exception as e:
            shutil.copy2(bak, src_path)
            print(f"BAKE FAILED, kernel/prompts.py restored from {bak}: {e}")
            return 2
        baked += [n for n, _, _ in [(e, 0, 0) for e in diffs] if n in diffs and not n.startswith("DOCK_") and n not in refused]
        print(f"baked {len(edits)} prompt(s) into kernel/prompts.py (backup {bak})")
    if docks:
        index = repo / "shell" / "index.html"
        bak = backup(repo, "shell/index.html", "bake")
        text = index.read_text(encoding="utf-8")
        for name, new in docks.items():
            app = next(a for a, n, _, _ in dock_prompts(repo) if n == name)
            pat = re.compile(r'(data-app="' + re.escape(app) + r'"\s+data-prompt=")([^"]*)(")')
            text, n = pat.subn(lambda m: m.group(1) + _attr_escape(new) + m.group(3), text, count=1)
            if n != 1:
                print(f"refused {name}: the attribute was not found in shell/index.html")
                continue
            baked.append(name)
        index.write_text(text, encoding="utf-8")
        check = {n: t for _, n, _, t in dock_prompts(repo)}
        bad = [n for n in docks if n in baked and check.get(n) != docks[n]]
        if bad:
            shutil.copy2(bak, index)
            print(f"BAKE FAILED for {', '.join(bad)}, shell/index.html restored from {bak}")
            return 2
        print(f"baked {len([n for n in docks if n in baked])} dock prompt(s) into shell/index.html (backup {bak})")
    print("baked: " + ", ".join(sorted(set(baked))) + ". Run the suite; an override file equal to the new default is no longer an override.")
    return 1 if refused else 0


# ---------------------------------------------------------------------------------------------- the HTML document
STACK = [
    ("Image", "Debian 13 (trixie) + systemd, a raw GPT image: systemd-boot ESP + an ext4 root that grows on first boot; kairos-install clones it onto an internal disk", "iso/ (live-build config, build-img.sh, the overlay config/includes.chroot)"),
    ("Boot", "the initramfs painter draws the boot screen with a loading bar fed by /api/boot; tty1 autologin → startx → openbox → ONE Chromium window over every monitor (the one desktop, R90/R91)", "scripts/splash/, iso/…/usr/local/sbin/kairos-*, scripts/desktop/kairos-desktop.py"),
    ("Kernel", "kernel/server.py (Python, aiohttp, root, loopback by default): the ModelManager + GPU scheduler, the app pipeline, the virtual web, media jobs, the game forge, hardware, VFS, knowledge, translation, the refusal ladder, the prompt registry, the installer, the API", "kernel/server.py + hw.py, vfs.py, knowledge.py, wm.py, jscheck.py, jsfix.py, refusal.py, prompts.py, monitors.py, public.py, dictionary.py"),
    ("Shell", "vanilla HTML/JS: window manager, dock, launcher, taskbar, the everything-search, the studios (Imagine, Motion, Melody, Arcade), the built-in browser, the panels (Models, Prompts); BRIDGE_SRC = the vibe.* bridge injected into every generated app", "shell/index.html, shell.js, browser.js, games.js, pictures.js, models.js, prompts.js, monitors.js, shell.css"),
    ("Engines", "llama-server (llama.cpp) for every language model · sd-cli / sd-server (stable-diffusion.cpp) for pictures and video · ace-lm + ace-synth (acestep.cpp) for music · kiwix-serve for the offline encyclopedia", "built by scripts/install-engines.sh; paired with the manifests through bin/ENGINES.json"),
    ("Models", "per VRAM tier: the coder/chat LLM (8/16 GB: Gemma 4 26B-A4B heretic, uncensored), the image model (Muse v3.5, a Krea 2 fine-tune) with its Qwen3-VL-4B encoder as prompt expander, Wan 2.2 video, ACE-Step 1.5 music, Qwen3-VL vision, Index-Translate 2B, the Wikipedia ZIM", "manifests/tier-{8,16,24,32}gb.json (file, url, sha256, serve/gen args, sampling, chat_template_kwargs)"),
    ("Knowledge", "DuckDuckGo online, kiwix full-text search over the Wikipedia ZIM offline — one interface; facts are prepended to app generation, pages and vibe.ask", "kernel/knowledge.py, dictionaries (kernel/dictionary.py)"),
    ("Tests", "the mock-engine suite (tests/run-tests.sh in dev/docker/run-suite.sh, no network), the VM rig (dev/rig), the stick run; the boot log's verdict is the pass criterion", "tests/, dev/, docs/PROGRESS.md"),
]
ORGANS = [
    ("ModelManager + GpuScheduler", "who owns the GPU now (LLM / expander / image / video / music), VRAM swap (llama-server stopped for a media job, reloaded lazily), parallel LLM slots, the engine stamp check", "MM.chat / chat_stream / sd_job / music_job"),
    ("App pipeline", "research facts → spec → code → rewrite → validate (a real JS parser) → repair loop → mount; runtime bug reports from the app → repair → remount; ✨ improve = the next version from the previous HTML", "POST /api/app, /api/app/{id}/modify, /repair"),
    ("The virtual web", "every /web/<address> page is LLM-written on the first visit and cached; links are prompts for the next page; ✨ improve writes the page's next version", "GET /web/…, POST /api/browse, /api/web/improve"),
    ("Media jobs", "pictures (/genimg/ on demand, vibe.image, sessions via sd-server, the Krea prompt expander, ✨ edit), video, music (ACE-Step, request-JSON driven, the caption/lyrics checks), the songwriter", "POST /api/image(s), /api/video, /api/music, /api/lyrics, /api/wallpaper, GET /genimg/…"),
    ("Game forge", "pygame programs: a static gate (pyflakes) → a launch test on the kiosk display → tracebacks fed back; games open as real X windows in desktop frames; ✨ improve evolves them", "POST /api/game, /api/windows, /api/frames"),
    ("Hardware API", "WiFi, Bluetooth, airplane mode, battery, brightness, volume, displays (the one desktop, HDMI reverse PRIME), date/time, power", "GET/POST /api/hw (kernel/hw.py)"),
    ("VFS", "Android-style: read-only /system (OS identity, the manifest, the docs, the live prompts), formattable /data (settings live there)", "POST /api/fs, /api/settings, /api/store"),
    ("Knowledge", "online web search or the offline ZIMs behind one interface; FACTS blocks for generation", "POST /api/search, /api/ask"),
    ("Translation layer", "settings translation / language / translation_quality: the shell's DOM translator and the apps translate through the fast CPU model or the loaded LLM; English hops for the English-only models", "POST /api/translate, GET /api/dict"),
    ("Refusal ladder", "a refusal by the model is detected and routed around (the preamble rung, the uncensored model), never cached; the narrow minors block checks every request at the entry", "kernel/refusal.py, server.llm_generate / llm_stream"),
    ("Prompt registry", "every prompt by name with its default, its line and its note; overrides from KAIROS_PROMPTS_DIR, live edits through the API and the ✎ Prompts panel", "GET/POST /api/prompts, kernel/prompts.py"),
    ("Installer, boot log, downloads", "kairos-install onto an internal disk; the boot log's PASS/WARN/FAIL verdict on the ESP; a public image's first-boot downloads and Muse with the user's civitai key", "POST /api/install, GET /api/boot, /api/downloads, /api/muse"),
]
PATHS = [
    ("New app (App Launcher, the search, a dock app)", "POST /api/app", ["SPEC_SYSTEM (+ CONTEXT_HEADER facts, WIKI_HEADER recipes)", "APP_SYSTEM + API_HEADER sheets (docs/api/*.md) + WIKI_HEADER recipes (docs/wiki/*.md) + CONTEXT_HEADER facts", "APP_ASSETS_INSTRUCTION when forged with media (the App Launcher)", "HTML_FIX_SYSTEM for every repair round", "DOCK_* = the forge request of a dock app"], "spec 0.7 · code 0.75 · repair 0.4 (0.7 on a repeat)"),
    ("✨ improve an app / an instruction", "POST /api/app evolve · /api/app/{id}/modify", ["APP_EVOLVE_SYSTEM (= APP_SYSTEM + the evolution tail) · APP_REGEN_SYSTEM (= APP_SYSTEM + the modify tail)", "HTML_FIX_SYSTEM"], "evolve 0.75 · modify 0.6"),
    ("The imaginary web", "GET /web/… · POST /api/browse · /api/web/improve", ["BROWSER_SYSTEM (+ CONTEXT_HEADER facts)", "WEB_EVOLVE_SYSTEM (= BROWSER_SYSTEM + the website evolution tail) + WEB_EVOLVE_SITE_INTRO on ✨ improve", "HTML_FIX_SYSTEM"], "page 0.8 · improve 0.75"),
    ("vibe.ask", "POST /api/ask", ["ASK_SYSTEM (+ CONTEXT_HEADER facts)"], "0.8"),
    ("Games", "POST /api/game", ["GAME_SYSTEM; on ✨ improve the user message carries GAME_EVOLVE_REQUEST or GAME_EVOLVE_NO_REQUEST + GAME_EVOLVE_INSTRUCTION", "GAME_FIX_SYSTEM with a traceback / syntax error"], "0.7 · fix 0.4 (0.7 on a repeat)"),
    ("Pictures", "POST /api/image(s) · GET /genimg/… · forge pictures · ✨ edit", ["KREA_EXPAND_SYSTEM_NEUTRAL (the uncensored default model) / KREA_EXPAND_SYSTEM (SFW sections) / KREA_EXPAND_SYSTEM_ADULT (adult requests); + KREA_EXPAND_ENGLISH while translation is on", "IMAGE_PROMPT_HINT appended to the final picture prompt"], "expander: EXPAND_SAMPLING (0.7, top_k 64, top_p 0.95, min_p 0.05, repeat 1.05)"),
    ("Wallpaper", "POST /api/wallpaper", ["WALLPAPER_SYSTEM over the session's fragments, else WALLPAPER_PROMPT"], "1.0"),
    ("Video", "POST /api/video · data-gen-video", ["VIDEO_PROMPT_HINT appended"], "—"),
    ("Music", "POST /api/music · data-gen-music", ["MUSIC_PROMPT_HINT appended to the caption; music_caption_check lifts BPM/key/time signature into the request fields; music_lyrics_check drops repeat markers; use_cot_caption off, seed = lm_seed"], "ACE-Step: 8 steps, guidance 1.0, shift 3.0"),
    ("The songwriter (Melody's lyrics box)", "POST /api/lyrics", ["LYRICS_SYSTEM → one JSON object: title, caption, lyrics, language, duration, bpm, keyscale, timesignature (a json_schema grammar); lyrics_check repairs tags and ranges, retries a broken song"], "LYRICS_SAMPLING: temperature 1.2, top_k 80 (the owner's, 2026-10-24)"),
    ("Vision", "POST /api/vision", ["VISION_SYSTEM"], "0.2"),
    ("Translation", "POST /api/translate · the English hops", ["TRANSLATE_PROMPT as the ONE user message, no system message", "LANGUAGE_INSTRUCTION appended to APP_*/GAME/ASK/BROWSER prompts while translation is on and the OS language is not English"], "0 (the vendor sampling left out)"),
    ("The refusal ladder / the limits", "every LLM request", ["ADULT_PREAMBLE prepended to the system prompt on the preamble rung (and always on the llm_uncensored rung)", "UNCENSORED_LIMITS appended to every system prompt an uncensored primary serves"], "—"),
]
HOW = [
    ("kernel/prompts.py", "the shipped defaults: every prompt as a Python string, the registry at the end (names, lines, notes, _DERIVED compositions)", "edit here for a permanent change — or `prompt-sync.py bake DIR`"),
    ("shell/index.html", "the dock apps' forge requests (data-prompt attributes: Settings, Files, Notes, News), registered as DOCK_*", "the same: edit the attribute or bake DOCK_*.txt"),
    ("$VIBEOS_DIR/prompts/<NAME>.txt", "the override directory of a running kernel (KAIROS_PROMPTS_DIR; /opt/vibeos/prompts on the stick, /tmp/kairos-test/prompts in the suite): read at start, live through the API", "the ✎ Prompts panel (⊞ Apps → Prompts, About → Prompts) · `prompt-sync.py push DIR --kernel URL` · `--dir`"),
    ("/system/prompts/<NAME>.txt (VFS)", "read-only copies of the live values for apps (vibe.fs.read); refreshed on every change", "—"),
    ("GET/POST /api/prompts[/<name>]", "the registry over HTTP: list (+?full=1), one prompt with its default, set {text} / {reset:true}, {reload:true}", "the panel and the script use it; guarded (loopback or the token)"),
    ("docs/API.md · docs/api/*.md · docs/wiki/*.md", "prompt material the coder LLM reads: the bridge contract, the per-topic API sheets (appended under API_HEADER when a task touches them), the recipes (WIKI_HEADER)", "edit the markdown; it is injected verbatim and budgeted against the context"),
    ("manifests/tier-*.json → llm.sampling, chat_template_kwargs, serve_args", "what else the model is told: the vendor sampler fields merged into EVERY request (Gemma: temperature 1.0, top_k 64, top_p 0.95 — note: this overrides the per-route temperature), thinking off (enable_thinking false, --reasoning off)", "a manifest edit (keep the 8 GB test variants identical outside llm)"),
    ("kernel/server.py constants", "LYRICS_SAMPLING, EXPAND_SAMPLING, the per-route temperatures (llm_generate(..., temperature=)), SPARKS (the search's random sparks), the music checks", "a code edit"),
]


def route_temperatures(repo: Path) -> dict:
    """{prompt name: temperature} scanned from kernel/server.py: a `prompts.X_SYSTEM` call followed by `temperature=` nearby"""
    out = {}
    try:
        src = (repo / "kernel" / "server.py").read_text(encoding="utf-8")
    except OSError:
        return out
    for m in re.finditer(r"prompts\.([A-Z_]+_SYSTEM)\b", src):
        window = src[m.end():m.end() + 500]
        t = re.search(r"temperature=([0-9.]+)", window)
        if t:
            out.setdefault(m.group(1), set()).add(t.group(1))
    return {k: ", ".join(sorted(v)) for k, v in out.items()}


def sheets(repo: Path) -> list:
    out = []
    for rel in ("docs/API.md",):
        p = repo / rel
        if p.is_file():
            out.append((rel, len(p.read_text(encoding="utf-8")), "the bridge contract — /system/docs/API.md, vibe.docs(), the base of every API sheet"))
    for sub, what in (("docs/api", "an API sheet, appended under API_HEADER when the app's task touches its topic (keywords in the first line)"),
                      ("docs/wiki", "a recipe, appended under WIKI_HEADER when it matches the request")):
        d = repo / sub
        if d.is_dir():
            for p in sorted(d.glob("*.md")):
                out.append((f"{sub}/{p.name}", len(p.read_text(encoding="utf-8")), what))
    return out


def manifest_llms(repo: Path) -> list:
    out = []
    for p in sorted((repo / "manifests").glob("tier-*.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in ("llm", "llm_fast", "python_llm", "llm_uncensored", "vision", "translation"):
            sec = m.get(key)
            if not isinstance(sec, dict) or not sec.get("name"):
                continue
            serve = " ".join(a for a in (sec.get("serve_args") or []) if isinstance(a, str) and a.startswith("--reasoning")) or ""
            out.append((p.name, key, sec.get("name", ""), json.dumps(sec.get("sampling")) if sec.get("sampling") else "—",
                        json.dumps(sec.get("chat_template_kwargs")) if sec.get("chat_template_kwargs") else "—",
                        serve or "—", "yes" if sec.get("uncensored") else ""))
    return out


def git_head(repo: Path) -> str:
    try:
        return subprocess.run(["git", "--no-optional-locks", "-C", str(repo), "log", "-1", "--format=%h %cs"],
                              capture_output=True, text=True, timeout=10).stdout.strip() or "?"
    except Exception:
        return "?"


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def render_html(items, repo: Path, source: str, fragment: bool, kernel_meta: dict = None) -> str:
    temps = route_temperatures(repo)
    n_over = sum(1 for it in items if it.get("overridden"))
    now = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
    head = f"""<title>kAIrOS Architecture and Prompts</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* layout: one reading column (72ch) with full-width tables; the prompt catalogue as open <details> blocks */
:root {{ --bg: #f6f5fb; --bg2: #ffffff; --fg: #17162a; --fg2: #5a5878; --line: #d9d7ea; --accent: #5b3fd6; --accent2: #0b8fa6;
         --code-bg: #eeedf7; --mark: #ffe9b3;
         --sans: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif; --mono: "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg: #0a0a14; --bg2: #12121f; --fg: #e8e8f2; --fg2: #9a9ab8; --line: #26263a;
         --accent: #9d86ff; --accent2: #41e2ff; --code-bg: #15152a; --mark: #4a3a10; color-scheme: dark; }} }}
:root[data-theme="dark"] {{ --bg: #0a0a14; --bg2: #12121f; --fg: #e8e8f2; --fg2: #9a9ab8; --line: #26263a;
         --accent: #9d86ff; --accent2: #41e2ff; --code-bg: #15152a; --mark: #4a3a10; color-scheme: dark; }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.55 var(--sans); margin: 0;
       padding-block: 28px 60px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }}
main {{ max-width: 1040px; margin: 0 auto; }}
h1, h2, h3 {{ text-wrap: balance; line-height: 1.2; font-weight: 600; }}
h1 {{ font-size: 2rem; margin: 0 0 6px; }} h2 {{ font-size: 1.35rem; margin: 44px 0 10px; color: var(--accent); }} h3 {{ font-size: 1.05rem; margin: 26px 0 6px; }}
p, li {{ max-width: 72ch; }}
.meta {{ color: var(--fg2); font-size: 13px; display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 22px; }}
.meta b {{ color: var(--fg); font-weight: 500; }}
code, pre, .mono {{ font-family: var(--mono); font-size: 12.5px; }}
code {{ background: var(--code-bg); padding: 1px 5px; border-radius: 4px; }}
pre {{ background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; overflow-x: auto; white-space: pre-wrap; line-height: 1.5; margin: 6px 0 0; }}
.tbl {{ overflow-x: auto; margin: 8px 0 4px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
th, td {{ text-align: left; vertical-align: top; padding: 7px 10px; border-bottom: 1px solid var(--line); }}
th {{ color: var(--fg2); font-weight: 500; font-size: 12px; letter-spacing: .04em; text-transform: uppercase; }}
td.n {{ font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }}
.pipe {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin: 14px 0 6px; }}
.pipe > div {{ background: var(--bg2); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; min-width: 0; }}
.pipe b {{ display: block; color: var(--accent2); font-size: 12px; letter-spacing: .05em; text-transform: uppercase; margin-bottom: 4px; }}
.pipe small {{ color: var(--fg2); font-size: 12.5px; line-height: 1.45; display: block; }}
.chain {{ color: var(--fg2); font-size: 12px; text-align: center; margin: -2px 0 10px; }}
.badge {{ display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 999px; background: var(--accent2); color: #002; font-weight: 600; vertical-align: 1px; }}
.badge.ro {{ background: var(--line); color: var(--fg2); }}
.badge.edit {{ background: var(--mark); color: var(--fg); }}
details {{ border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; margin: 10px 0; background: var(--bg2); }}
summary {{ cursor: pointer; display: flex; gap: 10px; flex-wrap: wrap; align-items: baseline; }}
summary .name {{ font-family: var(--mono); font-weight: 500; color: var(--accent); }}
summary .where {{ font-family: var(--mono); font-size: 12px; color: var(--fg2); }}
summary .chars {{ font-size: 12px; color: var(--fg2); margin-left: auto; font-variant-numeric: tabular-nums; }}
.note {{ color: var(--fg2); font-size: 13px; margin: 6px 0 2px; }}
.kind {{ margin-top: 30px; }}
.toc {{ display: flex; gap: 8px 16px; flex-wrap: wrap; font-size: 13.5px; padding: 0; list-style: none; max-width: none; }}
.toc li {{ max-width: none; }}
a {{ color: var(--accent2); }}
.finding {{ border-left: 3px solid var(--accent2); padding: 6px 12px; background: var(--bg2); margin: 10px 0; }}
.finding p {{ margin: 4px 0; }}
:focus-visible {{ outline: 2px solid var(--accent2); outline-offset: 2px; }}
@media (max-width: 600px) {{ h1 {{ font-size: 1.5rem; }} summary .chars {{ margin-left: 0; }} }}
</style>
"""
    parts = [head, "<main>",
             "<h1>kAIrOS Architecture and Prompts</h1>",
             f'<div class="meta"><span>generated <b>{now}</b></span><span>source <b>{_e(source)}</b></span>'
             f'<span>repo <b>{_e(git_head(repo))}</b></span><span>prompts <b>{len(items)}</b></span><span>edited <b>{n_over}</b></span>'
             + (f'<span>override dir <b>{_e(kernel_meta.get("dir"))}</b></span>' if kernel_meta and kernel_meta.get("dir") else "") + "</div>",
             '<ul class="toc"><li><a href="#what">What kAIrOS is</a></li><li><a href="#stack">The stack</a></li><li><a href="#picture">One picture</a></li>'
             '<li><a href="#organs">The kernel\'s organs</a></li><li><a href="#paths">Where each prompt enters</a></li><li><a href="#sampling">Sampling</a></li>'
             '<li><a href="#catalogue">Every prompt, in full</a></li><li><a href="#material">Other prompt material</a></li><li><a href="#edit">Where they stay and how to edit them</a></li></ul>',
             '<h2 id="what">What kAIrOS is</h2>',
             "<p>kAIrOS is a bootable Debian appliance whose desktop apps, web pages, pictures, games and music are generated on the user's GPU by "
             "local models. The shell is real and instant; the apps are hallucinated: an app is a prompt, the coder LLM writes a complete HTML "
             "document, the shell mounts it in a sandboxed iframe with the <code>vibe.*</code> bridge, and every version is kept so ✨ improve can "
             "build the next one from the last. One GPU serves every organ: the kernel's ModelManager swaps models in and out of 8 GB of VRAM, "
             "and every generation path is a job with an id. It is an 18+ system with uncensored models and no content filter; the one hard "
             "line is the narrow minors block in <code>kernel/refusal.py</code>.</p>",
             '<h2 id="stack">The stack, layer by layer</h2><div class="tbl"><table><tr><th>layer</th><th>what</th><th>where in the repository</th></tr>']
    for layer, what, where in STACK:
        parts.append(f"<tr><td><b>{_e(layer)}</b></td><td>{_e(what)}</td><td class='mono'>{_e(where)}</td></tr>")
    parts.append("</table></div>")
    parts.append('<h2 id="picture">One picture</h2><p>The request path from the user to the GPU and back. Prompts enter in the kernel, between a route and an engine.</p>')
    parts.append('<div class="pipe">'
                 '<div><b>1 · boot</b><small>initramfs painter → tty1 autologin → startx → openbox → one Chromium window over every monitor → http://localhost:1337</small></div>'
                 '<div><b>2 · shell</b><small>window manager, dock, launcher, search, the studios and panels; every app runs in a sandboxed iframe and reaches the OS only through the vibe.* bridge (postMessage → the shell → the kernel)</small></div>'
                 '<div><b>3 · kernel route</b><small>POST /api/app, /api/browse, /api/ask, /api/image, /api/music, /api/lyrics, /api/game, /api/hw …; the minors block at the entry; facts gathered; the system prompt chosen</small></div>'
                 '<div><b>4 · prompt</b><small>the system prompt from kernel/prompts.py (an override when one is set) + the API sheets, recipes and facts + the limits line of an uncensored model + the language line while translation is on</small></div>'
                 '<div><b>5 · engine</b><small>llama-server (the LLM, the expander, vision, translation) · sd-cli / sd-server (pictures, video) · ace-lm + ace-synth (music); one GPU, the scheduler decides who holds it</small></div>'
                 '<div><b>6 · after</b><small>the answer is judged (a refusal is routed around), rewritten and validated (links, pictures, truncation, a JS parser), repaired by the model until clean, cached, mounted — and the next version can start from it</small></div>'
                 '</div>')
    parts.append('<h2 id="organs">The kernel\'s organs</h2><div class="tbl"><table><tr><th>organ</th><th>what it does</th><th>routes / code</th></tr>')
    for organ, what, routes in ORGANS:
        parts.append(f"<tr><td><b>{_e(organ)}</b></td><td>{_e(what)}</td><td class='mono'>{_e(routes)}</td></tr>")
    parts.append("</table></div>")
    parts.append('<h2 id="paths">Where each prompt enters</h2><p>Every generation path, the prompts it sends in order, and the temperature the route asks for.</p>'
                 '<div class="tbl"><table><tr><th>path</th><th>route</th><th>prompts, in order</th><th>temperature / sampling</th></tr>')
    for path, route, plist, temp in PATHS:
        parts.append(f"<tr><td><b>{_e(path)}</b></td><td class='mono'>{_e(route)}</td><td>{'<br>'.join(_e(x) for x in plist)}</td><td>{_e(temp)}</td></tr>")
    parts.append("</table></div>")
    llm_rows = manifest_llms(repo)
    parts.append('<h2 id="sampling">Sampling: what the manifests add to every request</h2>'
                 '<div class="finding"><p><b>Finding.</b> <code>chat_payload</code> merges the LLM section\'s <code>sampling</code> into every request after '
                 'the route\'s own <code>temperature</code>. The 8/16 GB manifests ship Gemma with <code>temperature 1.0</code> in that block, so every Gemma '
                 'generation (apps, pages, ask, games, repairs) effectively runs at 1.0, whatever the route asked for. Only the translation hop '
                 '(<code>vendor_sampling=False</code>, temperature 0) and the songwriter (a per-call override: 1.2 / top_k 80) escape it. '
                 'To make the per-route temperatures count again, remove <code>temperature</code> from the manifests\' <code>llm.sampling</code>.</p></div>'
                 '<div class="tbl"><table><tr><th>manifest</th><th>section</th><th>model</th><th>sampling (merged into every request)</th><th>chat_template_kwargs</th><th>reasoning</th><th>uncensored</th></tr>')
    for row in llm_rows:
        parts.append("<tr>" + "".join(f"<td class='mono'>{_e(c)}</td>" if i in (3, 4, 5) else f"<td>{_e(c)}</td>" for i, c in enumerate(row)) + "</tr>")
    parts.append("</table></div>")
    if temps:
        parts.append("<p class='note'>Per-route temperatures scanned from kernel/server.py: " + "; ".join(f"<code>{_e(k)}</code> {_e(v)}" for k, v in sorted(temps.items())) + ".</p>")
    # the catalogue
    parts.append('<h2 id="catalogue">Every prompt, in full</h2><p>The live text of each prompt (the override where one is set, else the shipped default), '
                 'its kind, the line it lives on and who sends it. Parts are the building blocks the prompts above are composed from; they are read-only here '
                 'and in the panel. Everything is in <code>kernel/prompts.py</code> unless the location says otherwise.</p>')
    for kind in KIND_ORDER:
        rows = [it for it in items if it["kind"] == kind]
        if not rows:
            continue
        parts.append(f'<h3 class="kind">{_e(KIND_LABEL.get(kind, kind))} <span class="badge ro">{len(rows)}</span></h3>')
        for it in rows:
            badge = ' <span class="badge edit">edited</span>' if it.get("overridden") else (' <span class="badge">rebuilt</span>' if it.get("rebuilt") else "")
            ro = "" if it.get("editable", True) else ' <span class="badge ro">read-only</span>'
            derived = f" — built from {' + '.join(it['derived_from'])}" if it.get("derived_from") else ""
            open_attr = " open" if kind in ("system", "instruction", "template", "hint", "dock") else ""
            parts.append(f'<details id="p-{_e(it["name"])}"{open_attr}><summary><span class="name">{_e(it["name"])}</span>{badge}{ro}'
                         f'<span class="where">{_e(it["where"])}</span><span class="chars">{it["chars"]} chars</span></summary>'
                         f'<div class="note">{_e(it.get("note", ""))}{_e(derived)}</div><pre>{_e(it["text"])}</pre>'
                         + (f'<div class="note">shipped default ({len(it["default"])} chars):</div><pre>{_e(it["default"])}</pre>' if it.get("overridden") else "")
                         + "</details>")
    # other material
    parts.append('<h2 id="material">Other prompt material</h2><p>Text that reaches the models without being a prompt constant: the documents the coder LLM '
                 'is given, and the manifest fields merged into every request (see Sampling above).</p><div class="tbl"><table><tr><th>file</th><th class="n">chars</th><th>how it reaches the model</th></tr>')
    for rel, n, what in sheets(repo):
        parts.append(f"<tr><td class='mono'>{_e(rel)}</td><td class='n'>{n}</td><td>{_e(what)}</td></tr>")
    parts.append("</table></div>")
    parts.append('<h2 id="edit">Where they stay and how to edit them</h2><div class="tbl"><table><tr><th>place</th><th>what is there</th><th>how to change it</th></tr>')
    for place, what, how in HOW:
        parts.append(f"<tr><td class='mono'>{_e(place)}</td><td>{_e(what)}</td><td>{_e(how)}</td></tr>")
    parts.append("</table></div>")
    parts.append("<h3>The loop</h3><pre>"
                 + _e("# on the running system: ⊞ Apps → Prompts (or About kAIrOS → Prompts): pick a prompt, edit, Save (Ctrl+S); Reset = the shipped text\n"
                      "# from a terminal, the same prompts as files:\n"
                      "scripts/prompt-sync.py export ~/kairos/prompts                      # the shipped defaults as <NAME>.txt + INDEX.md\n"
                      "scripts/prompt-sync.py export ~/kairos/prompts --kernel http://127.0.0.1:1337   # a kernel's LIVE texts\n"
                      "scripts/prompt-sync.py diff   ~/kairos/prompts [--full]             # what you changed\n"
                      "scripts/prompt-sync.py push   ~/kairos/prompts --kernel http://127.0.0.1:1337   # live, at once (loopback or --token)\n"
                      "scripts/prompt-sync.py push   ~/kairos/prompts --dir /opt/vibeos/prompts          # into an override directory (a mounted root)\n"
                      "scripts/prompt-sync.py pull   --kernel http://127.0.0.1:1337 ~/kairos/prompts     # the kernel's edits back into the folder\n"
                      "scripts/prompt-sync.py bake   ~/kairos/prompts [--check]            # the edits become the shipped defaults (kernel/prompts.py, shell/index.html)\n"
                      "scripts/prompt-sync.py html   -o ~/kairos/prompts/kairos-architecture-and-prompts.html   # this document, regenerated\n")
                 + "</pre>")
    parts.append("</main>")
    body = "\n".join(parts)
    if fragment:
        return body
    return ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            + body.replace("<main>", "</head>\n<body>\n<main>", 1) + "\n</body>\n</html>\n")


def cmd_html(args):
    meta = None
    if args.kernel:
        k = Kernel(args.kernel, args.token)
        items = k.catalogue()
        meta = k.meta
    else:
        items = repo_catalogue(args.repo)
    doc = render_html(items, args.repo, args.kernel or "the repository's defaults", args.fragment, meta)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(doc, encoding="utf-8")
        print(f"wrote {args.out} ({len(doc)} chars, {len(items)} prompts)")
    else:
        sys.stdout.write(doc)
    return 0


# ---------------------------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.split("\n")[2:]))
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="the repository root")
    ap.add_argument("--token", default=None, help="X-Kairos-Token for guarded calls")
    common = argparse.ArgumentParser(add_help=False)          # the same two options after the subcommand, too
    common.add_argument("--repo", type=Path, default=argparse.SUPPRESS)
    common.add_argument("--token", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list", parents=[common]); s.add_argument("--kernel"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("export", parents=[common]); s.add_argument("dir"); s.add_argument("--kernel"); s.set_defaults(fn=cmd_export)
    s = sub.add_parser("pull", parents=[common]); s.add_argument("dir"); g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--kernel"); g.add_argument("--dir", dest="overrides"); s.set_defaults(fn=cmd_pull)
    s = sub.add_parser("diff", parents=[common]); s.add_argument("dir"); s.add_argument("--kernel"); s.add_argument("--full", action="store_true"); s.set_defaults(fn=cmd_diff)
    s = sub.add_parser("push", parents=[common]); s.add_argument("dir"); g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--kernel"); g.add_argument("--dir", dest="overrides"); s.set_defaults(fn=cmd_push)
    s = sub.add_parser("reset", parents=[common]); s.add_argument("names", nargs="*"); s.add_argument("--all", action="store_true")
    g = s.add_mutually_exclusive_group(required=True); g.add_argument("--kernel"); g.add_argument("--dir", dest="overrides"); s.set_defaults(fn=cmd_reset)
    s = sub.add_parser("bake", parents=[common]); s.add_argument("dir"); s.add_argument("--only"); s.add_argument("--check", action="store_true"); s.set_defaults(fn=cmd_bake)
    s = sub.add_parser("html", parents=[common]); s.add_argument("-o", "--out"); s.add_argument("--kernel"); s.add_argument("--fragment", action="store_true"); s.set_defaults(fn=cmd_html)
    args = ap.parse_args(argv)
    if args.cmd == "reset" and not args.all and not args.names:
        ap.error("reset needs NAME… or --all")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
