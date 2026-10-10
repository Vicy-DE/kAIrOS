"""kAIrOS dictionary organ [R71, P3]: word lookup over the `dictionaries` add-on.

Sources (docs/TRANSLATION-2026-10-06.md §3, downloads pod/translation-2026-10-06.queue):
- BEOLINGUS / Ding (TU Chemnitz, GPL-2+): `dict/beolingus-de-en.txt.gz`, ~205k lines
  `de :: en` — `|` aligns groups (lemma | plural | phrases), `;` separates synonyms,
  `{m}` `[ugs.]` `(…)` annotate. Indexed in BOTH directions (de→en, en→de).
- FreeDict dictd archives (`dict/freedict-<src>-<tgt>-<ver>.dictd.tar.xz`, GPL / CC-BY-SA per
  dictionary): `<pair>.index` (headword TAB offset TAB length, dictd's base64) + `<pair>.dict.dz`
  (dictzip = gzip with an FEXTRA block; Python's gzip reads it sequentially). An entry = a
  headline (`forms /pron/ <pos>`), translation lines, optional definition / Synonyms / see /
  Note lines. A direction that has no forward file (it→fr, ja→it, ru→ja) is served by the
  reverse of the opposite pair; es↔ja has none.

The kernel builds ONE compact index per source and direction on first use (or right after
the download): `CACHE_DIR/dict/<id>.idx`, sorted lines
    fold TAB headword TAB display TAB pos TAB translations (" | "-joined)
plus `<id>.meta.json` (archive size/mtime, entry count, licence). A lookup mmaps the index
and binary-searches it — no index is ever loaded into RAM, at most MAX_OPEN maps stay open
(the page cache does the rest). Matching, in order: exact (the headword as typed), folded
(case + diacritics + punctuation folded; ja keeps its kana intact), prefix — capped.
"""
import gzip
import io
import json
import mmap
import os
import re
import tarfile
import threading
import time
import unicodedata
from pathlib import Path

INDEX_VERSION = 2                 # bump when the parsers change: stale indexes are rebuilt
MAX_OPEN = 8                      # mmapped indexes kept open (LRU)
MAX_ITEM = 120                    # chars: a headword / phrase longer than this is not indexed
MAX_TRANSLATIONS = 8              # translation lines kept per entry
LANG3 = {"en": "eng", "de": "deu", "es": "spa", "fr": "fra", "it": "ita", "ja": "jpn", "ru": "rus"}
LANG2 = {v: k for k, v in LANG3.items()}
_DICTD_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_ANNOT_RE = re.compile(r"\{[^}]*\}|\[[^\]]*\]|\([^)]*\)|<[^>]*>")
_PRON_RE = re.compile(r"\s*/+[^/]*/+")                 # /ˈap/, WikDict's //ˈapəl// and /[ɛːpɐl]/
_TAG_RE = re.compile(r"\s*<[^>]*>")
_PRIO_RE = re.compile(r"\[[a-z]+\d*\]|\(\)")           # JMdict priority tags [ichi1] [news1] [nf07], empty ()
_NUM_RE = re.compile(r"^\d+\.\s*")
_SKIP_LINE_RE = re.compile(r"^\s*(Synonyms?|Antonyms?|see|Note|Usage|Example)s?\s*:", re.I)
_REF_LINE_RE = re.compile(r"^(\{[^}]*\}[,;]?\s*)+$")   # a line of {cross-references} only
_SENSE_RE = re.compile(r"\s+\d{1,2}\.?(?=\s*[,;|]|\s*$)")   # WikDict's sense index after a word: "casa 2." → "casa"
_WS_RE = re.compile(r"\s+")
_LETTER_RE = re.compile(r"[^\W\d_]")


class DictError(Exception):
    """A clean sentence for the user (never a traceback); `status` is the HTTP code."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def fold(s: str) -> str:
    """Case-, diacritic- and punctuation-insensitive key. Latin/Cyrillic/Greek letters lose
    their marks (ü → u, é → e, ё → е); kana and kanji stay as they are (NFKC only, so a
    voiced kana never loses its dakuten); punctuation → space; whitespace collapsed."""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    out = []
    for ch in s:
        if ord(ch) < 0x2000:
            out.append("".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)))
        else:
            out.append(ch)
    s = re.sub(r"[^\w\s]", " ", "".join(out))
    return _WS_RE.sub(" ", s).strip()


def _clean(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").replace("\t", " ")).strip()


def _strip_annotations(s: str) -> str:
    return _clean(_ANNOT_RE.sub(" ", s)).strip(" ,;:.-")


def dictd_number(s: str) -> int:
    n = 0
    for c in s:
        n = n * 64 + _DICTD_B64.index(c)
    return n


# ---------------------------------------------------------------- the sources

class Source:
    """One dictionary file in ONE direction (a Ding file gives two, a dictd archive one, or
    one reversed one when no forward file exists for that direction)."""

    def __init__(self, sid, kind, file, frm, to, label, licence="", reverse=False, size=0):
        self.id, self.kind, self.file, self.frm, self.to = sid, kind, file, frm, to
        self.label, self.licence, self.reverse, self.size = label, licence, reverse, size

    def describe(self) -> dict:
        return {"id": self.id, "label": self.label, "from": self.frm, "to": self.to, "kind": self.kind,
                "file": self.file, "reversed": self.reverse, "licence": self.licence}


def sources_from_section(section) -> list:
    """The manifest's `dictionaries` section → Source list, ordered per direction: Ding
    first, then the forward dictd files (largest first), then the reversed ones."""
    entries = []

    def collect(node):
        if isinstance(node, dict):
            if "file" in node and "url" in node and node.get("pair"):
                entries.append(node)
            for v in node.values():
                collect(v)
    collect(section if isinstance(section, dict) else {})
    out, forward = [], set()
    for e in entries:
        a, b = e["pair"].split("-", 1)
        frm, to = LANG2.get(a, a), LANG2.get(b, b)
        ver = e.get("version") or ""
        lic = e.get("licence") or ""
        if e.get("format") == "ding":
            label = e.get("name") or f"BEOLINGUS {frm}-{to}"
            out.append(Source(f"beolingus-{frm}-{to}", "ding", e["file"], frm, to, label, lic, size=e.get("size_bytes", 0)))
            out.append(Source(f"beolingus-{to}-{frm}", "ding", e["file"], to, frm, label, lic, reverse=True,
                              size=e.get("size_bytes", 0)))
            forward.update({(frm, to), (to, frm)})
        else:
            label = f"FreeDict {e['pair']}{' ' + ver if ver else ''}"
            out.append(Source(f"freedict-{e['pair']}", "dictd", e["file"], frm, to, label, lic, size=e.get("size_bytes", 0)))
            forward.add((frm, to))
    for e in entries:                                    # reversed dictd only where nothing forward exists
        if e.get("format") == "ding":
            continue
        a, b = e["pair"].split("-", 1)
        frm, to = LANG2.get(a, a), LANG2.get(b, b)
        if (to, frm) not in forward:
            ver = e.get("version") or ""
            out.append(Source(f"freedict-{e['pair']}-rev", "dictd", e["file"], to, frm,
                              f"FreeDict {e['pair']}{' ' + ver if ver else ''} (reversed)", e.get("licence") or "",
                              reverse=True, size=e.get("size_bytes", 0)))
    rank = {"ding": 0, "dictd": 1}
    out.sort(key=lambda s: (s.frm, s.to, rank.get(s.kind, 9), s.reverse, -s.size))   # BEOLINGUS first for de<->en, both ways
    return out


# ---------------------------------------------------------------- parsers

def parse_ding(text: str):
    """BEOLINGUS lines → (left_items, right_items) per aligned group: each item is the raw
    string (annotations kept for display)."""
    for line in text.splitlines():
        if not line or line.startswith("#") or " :: " not in line:
            continue
        left, right = line.split(" :: ", 1)
        lg, rg = left.split(" | "), right.split(" | ")
        if len(lg) != len(rg):
            lg, rg = [left], [right]
        for l, r in zip(lg, rg):
            li = [x.strip() for x in l.split(";") if x.strip()]
            ri = [x.strip() for x in r.split(";") if x.strip()]
            if li and ri:
                yield li, ri


def parse_dictd_entry(entry: str):
    """One dictd entry → (forms, pos, translations). forms = the headline's comma-separated
    headwords without pronunciation / tags; translations = the lines that carry the target
    language (numbering stripped; POS-only, definition, Synonyms/see/Note lines skipped)."""
    lines = entry.split("\n")
    head = lines[0] if lines else ""
    pos = " ".join(t.strip("<>") for t in re.findall(r"<[^>]*>", head))
    head = _TAG_RE.sub("", _PRON_RE.sub("", head))
    forms = [_clean(_PRIO_RE.sub(" ", f)).strip(" ,") for f in head.split(", ")]
    forms = [f for f in forms if f and len(f) <= MAX_ITEM]
    out = []
    for ln in lines[1:]:
        s = ln.strip()
        if not s or _SKIP_LINE_RE.match(s) or _REF_LINE_RE.match(s):   # Synonyms / see / Note lines, {see-also} lines
            continue
        s = _NUM_RE.sub("", s)                           # "1. cat" → "cat"
        if s.startswith("(") and s.endswith(")"):        # a POS line such as "(noun (common) (futsuumeishi))"
            continue
        s = _clean(_SENSE_RE.sub("", _TAG_RE.sub("", s)))
        if not s or _REF_LINE_RE.match(s):
            continue
        s = ", ".join(dict.fromkeys(x.strip() for x in s.split(",") if x.strip()))   # "猫 2., 猫" → "猫"
        words = s.split(" ")
        if len(words) > 7 or (s.endswith(".") and len(words) > 2):    # a definition sentence, not a translation
            continue
        out.append(s[:200])
        if len(out) >= MAX_TRANSLATIONS:
            break
    return forms, pos, out


def _split_items(s: str) -> list:
    """'house, home; dwelling' → ['house', 'home', 'dwelling'] (annotations dropped)."""
    items = []
    for part in re.split(r"[,;]", s):
        p = _strip_annotations(_SENSE_RE.sub("", part))
        if p and "…" not in p and len(p) <= 60 and p not in items:
            items.append(p)
    return items


# ---------------------------------------------------------------- the index

class Index:
    """A built index file: sorted lines `fold \t head \t display \t pos \t translations`."""

    def __init__(self, path: Path):
        self.path = path
        self.f = open(path, "rb")
        self.mm = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ) if path.stat().st_size else None
        self.used = time.monotonic()

    def close(self):
        try:
            if self.mm is not None:
                self.mm.close()
            self.f.close()
        except Exception:
            pass

    def _line_start(self, pos: int) -> int:
        return self.mm.rfind(b"\n", 0, pos) + 1

    def _key_at(self, start: int):
        end = self.mm.find(b"\n", start)
        if end < 0:
            end = len(self.mm)
        tab = self.mm.find(b"\t", start, end)
        return self.mm[start:tab if tab >= 0 else end], end

    def lower_bound(self, key: bytes) -> int:
        lo, hi = 0, len(self.mm)
        while lo < hi:
            mid = (lo + hi) // 2
            start = self._line_start(mid)
            k, end = self._key_at(start)
            if k < key:
                lo = end + 1
            else:
                hi = start
        return lo

    def find(self, key: str, word: str, limit: int):
        """(exact, folded, prefix) record lists for `key` = fold(word); each record is a dict."""
        self.used = time.monotonic()
        exact, folded, prefix = [], [], []
        if self.mm is None or not key:
            return exact, folded, prefix
        kb = key.encode("utf-8")
        pos = self.lower_bound(kb)
        wnorm = _clean(word)
        n = len(self.mm)
        while pos < n and len(prefix) < limit:
            end = self.mm.find(b"\n", pos)
            if end < 0:
                end = n
            line = self.mm[pos:end]
            pos = end + 1
            if not line.startswith(kb):
                break
            parts = line.decode("utf-8", "replace").split("\t")
            if len(parts) < 5:
                continue
            k, head, display, pos_tag, tr = parts[:5]
            rec = {"headword": head, "display": display, "pos": pos_tag, "translations": tr.split(" | ")}
            if k == key:
                (exact if head == wnorm else folded).append(rec)
            else:
                prefix.append(rec)
        # within a match class the plain sense first: a translation whose first item carries no
        # [register/region] tag, the fewest [tags] in the display, then the shortest display and
        # translation — "Katze {f} [zool.] → cat" before "Katze {f} → moggy [Br.]", "Haus {n} →
        # house" before "Haus {n} [adm.] → establishment"
        def rank(r):
            first = (r["translations"][0] if r["translations"] else "").split(";")[0]
            return ("[" in first, r["display"].count("[") + r["display"].count("("), len(r["display"]), len(" | ".join(r["translations"])))
        exact.sort(key=rank)
        folded.sort(key=rank)
        return exact, folded, prefix


class Dictionary:
    """The organ: sources from the manifest section, archives under models_dir, indexes under
    cache_dir/dict. lookup() is synchronous (call it in a thread: the first use of a source
    builds its index — seconds for the big ones)."""

    def __init__(self, section_getter, models_dir: Path, cache_dir: Path):
        self._section = section_getter          # callable → the manifest's `dictionaries` section (or None)
        self.models_dir, self.index_dir = Path(models_dir), Path(cache_dir) / "dict"
        self.open = {}                           # id → Index (LRU, MAX_OPEN)
        self.broken = {}                         # id → (archive signature, error text)
        self.build_lock = threading.Lock()       # one build at a time
        self.stats = {"lookups": 0, "builds": 0, "build_s": 0.0}

    # ---- sources and presence

    def sources(self) -> list:
        return sources_from_section(self._section())

    def archive(self, src: Source) -> Path:
        return self.models_dir / src.file

    def present(self, src: Source) -> bool:
        p = self.archive(src)
        return p.exists() and not p.with_name(p.name + ".aria2").exists()

    def pairs(self) -> list:
        seen = []
        for s in self.sources():
            if (s.frm, s.to) not in seen:
                seen.append((s.frm, s.to))
        return [f"{a}-{b}" for a, b in seen]

    def _signature(self, src: Source):
        try:
            st = self.archive(src).stat()
            return [st.st_size, int(st.st_mtime)]
        except OSError:
            return None

    def index_paths(self, src: Source):
        return self.index_dir / f"{src.id}.idx", self.index_dir / f"{src.id}.meta.json"

    def indexed(self, src: Source) -> dict:
        """The meta of a built, up-to-date index, else None."""
        idx, meta = self.index_paths(src)
        try:
            m = json.loads(meta.read_text())
            if m.get("version") == INDEX_VERSION and m.get("archive") == self._signature(src) and idx.exists():
                return m
        except Exception:
            pass
        return None

    def status(self) -> dict:
        srcs = self.sources()
        present = [s for s in srcs if self.present(s)]
        files = sorted({s.file for s in srcs})
        return {"pairs": self.pairs(), "sources": len(srcs), "files": len(files),
                "present": bool(srcs) and all(self.present(s) for s in srcs),
                "present_files": len({s.file for s in present}),
                "indexed": sum(1 for s in srcs if self.indexed(s)),
                "broken": {k: v[1] for k, v in self.broken.items()},
                "index_dir": str(self.index_dir), **self.stats}

    def describe(self) -> list:
        out = []
        for s in self.sources():
            m = self.indexed(s)
            out.append({**s.describe(), "present": self.present(s), "indexed": bool(m),
                        "entries": (m or {}).get("entries"), "error": (self.broken.get(s.id) or (None, None))[1]})
        return out

    # ---- building

    def build_all(self, log=print):
        """Index every present source (after the add-on download)."""
        for s in self.sources():
            if self.present(s) and not self.indexed(s):
                try:
                    self.ensure_index(s)
                    log(f"[kernel] dictionary index built: {s.id} ({self.indexed(s)['entries']} entries)")
                except DictError as e:
                    log(f"[kernel] dictionary index NOT built: {s.id}: {e}")

    def ensure_index(self, src: Source) -> Path:
        idx, meta = self.index_paths(src)
        if self.indexed(src):
            return idx
        sig = self._signature(src)
        if sig is None:
            raise DictError(f"the dictionary file {src.file} is missing", 400)
        b = self.broken.get(src.id)
        if b and b[0] == sig:
            raise DictError(f"{src.label}: {b[1]}", 502)
        with self.build_lock:
            if self.indexed(src):
                return idx
            t0 = time.monotonic()
            try:
                records = self._records(src)
                self.index_dir.mkdir(parents=True, exist_ok=True)
                tmp = idx.with_suffix(".idx.tmp")
                n = 0
                with open(tmp, "wb") as f:
                    last = None
                    for rec in sorted(records, key=lambda r: (r[0].encode("utf-8"), r[1])):
                        if rec == last:
                            continue
                        last = rec
                        f.write(("\t".join(rec) + "\n").encode("utf-8"))
                        n += 1
                os.replace(tmp, idx)
                meta.write_text(json.dumps({"version": INDEX_VERSION, "id": src.id, "archive": sig, "file": src.file,
                                            "entries": n, "built": time.strftime("%Y-%m-%d %H:%M:%S"),
                                            "build_s": round(time.monotonic() - t0, 1), "licence": src.licence}))
                self.stats["builds"] += 1
                self.stats["build_s"] += round(time.monotonic() - t0, 1)
                self.broken.pop(src.id, None)
                self.open.pop(src.id, None)
                return idx
            except DictError:
                raise
            except Exception as e:
                msg = f"{type(e).__name__}: {str(e)[:160]}"
                self.broken[src.id] = (sig, f"unreadable dictionary file ({msg})")
                raise DictError(f"{src.label}: unreadable dictionary file ({msg})", 502)

    def _records(self, src: Source):
        """Yield (fold, head, display, pos, translations) tuples for one source."""
        path = self.archive(src)
        if src.kind == "ding":
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
                text = f.read()
            for li, ri in parse_ding(text):
                heads, trans = (ri, li) if src.reverse else (li, ri)
                tr = " | ".join(_clean(t) for t in trans[:MAX_TRANSLATIONS])
                for item in heads:
                    head = _strip_annotations(item)
                    if head and len(item) <= MAX_ITEM:
                        yield fold(head), head, _clean(item), "", tr
            return
        if src.kind != "dictd":
            raise DictError(f"unknown dictionary format {src.kind!r}", 502)
        index_text, data = self._read_dictd(path)
        seen = set()
        for ln in index_text.splitlines():
            parts = ln.split("\t")
            if len(parts) < 3 or parts[0].startswith("00-database") or parts[0].startswith("00database"):
                continue
            off, length = dictd_number(parts[1]), dictd_number(parts[2])
            if off in seen:
                continue
            seen.add(off)
            entry = data[off:off + length].decode("utf-8", "replace").strip("\n")
            forms, pos, trans = parse_dictd_entry(entry)
            if not forms or not trans:
                continue
            if src.reverse:
                head = forms[0]
                for t in trans:
                    for item in _split_items(t):
                        yield fold(item), item, item, "", head
            else:
                tr = " | ".join(trans)
                for form in forms:
                    head = _strip_annotations(form) or form
                    yield fold(head), head, form, pos, tr

    @staticmethod
    def _read_dictd(path: Path):
        """(index text, dict bytes) from a FreeDict .dictd.tar.xz — the `.index` member and
        the sequentially decompressed `.dict.dz`."""
        index_text = data = None
        with tarfile.open(path, "r:xz") as t:
            for m in t.getmembers():
                if m.name.endswith(".index"):
                    index_text = t.extractfile(m).read().decode("utf-8", "replace")
                elif m.name.endswith(".dict.dz"):
                    raw = t.extractfile(m).read()
                    data = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                elif m.name.endswith(".dict"):
                    data = t.extractfile(m).read()
        if index_text is None or data is None:
            raise DictError("no .index / .dict.dz member in the archive", 502)
        return index_text, data

    # ---- lookup

    def _index(self, src: Source) -> Index:
        ix = self.open.get(src.id)
        if ix is not None and self.indexed(src):
            return ix
        if ix is not None:
            ix.close()
            self.open.pop(src.id, None)
        path = self.ensure_index(src)
        ix = Index(path)
        self.open[src.id] = ix
        while len(self.open) > MAX_OPEN:
            oldest = min(self.open, key=lambda k: self.open[k].used)
            self.open.pop(oldest).close()
        return ix

    def lookup(self, word: str, frm, to: str, limit: int = 20) -> dict:
        """{"word", "from", "to", "entries": [...], "count", "limit", "sources": [...], "ms"}.
        Raises DictError (400: no dictionaries for the pair / add-on missing; 502: every
        dictionary of the pair unreadable)."""
        t0 = time.monotonic()
        word = _clean(word)
        key = fold(word)
        srcs = [s for s in self.sources() if s.to == to and (frm is None or s.frm == frm)]
        if not self.sources():
            raise DictError("this tier has no dictionaries section", 400)
        if not srcs:
            pairs = ", ".join(self.pairs())
            raise DictError(f"no dictionary for {frm or '?'}→{to} — the pairs are: {pairs}", 400)
        present = [s for s in srcs if self.present(s)]
        if not present:
            raise DictError("the dictionaries add-on isn't installed — download it in Settings "
                            "(vibe.models.download('dictionaries'))", 400)
        exact, folded, prefix, used, errors = [], [], [], [], []
        for s in present:
            try:
                ix = self._index(s)
            except DictError as e:
                errors.append({"id": s.id, "label": s.label, "error": str(e)})
                continue
            e, f, p = ix.find(key, word, limit)
            for lst, recs in ((exact, e), (folded, f), (prefix, p)):
                for r in recs:
                    lst.append({**r, "source": s.label, "from": s.frm, "to": s.to})
            used.append({"id": s.id, "label": s.label, "from": s.frm, "to": s.to,
                         "entries": (self.indexed(s) or {}).get("entries")})
        if not used and errors:
            raise DictError("; ".join(x["error"] for x in errors), 502)
        for lst, kind in ((exact, "exact"), (folded, "folded"), (prefix, "prefix")):
            for r in lst:
                r["match"] = kind
        entries = (exact + folded + prefix)[:limit]
        self.stats["lookups"] += 1
        return {"word": word, "from": frm, "to": to, "entries": entries, "count": len(entries), "limit": limit,
                "matches": {"exact": len(exact), "folded": len(folded), "prefix": len(prefix)},
                "sources": used, "errors": errors, "ms": round((time.monotonic() - t0) * 1000)}
