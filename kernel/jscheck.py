"""JavaScript syntax gate for generated documents [R12, docs/FIXES-2026-09-28.md D7].

`validate_html()` never parsed JavaScript: the feature test's qwen35-9b notes app shipped with
`async generateImage: async function()` (V8: "SyntaxError: Unexpected token ':'"), passed the
structural checks, reached the user, and the shell's self-heal then failed to fix it in six
rounds. This module parses every classic inline <script> of a document with a REAL ES2023
parser before the app is returned, so the generation pipeline's own repair round
(`repair_html`) corrects a syntax error with the parser's message, line and code excerpt in
hand — the cheapest point to fix it.

The parser is QuickJS's compiler `qjsc` (Debian package `quickjs`, ~2 MB, in trixie as
2025-04-26): `qjsc -c -o /dev/null file.js` compiles a script to bytecode without running
it and reports `SyntaxError: <message>\n    at <file>:<line>:<col>` on stderr. It is the
dependency-free choice for the appliance (Debian trixie has no node in the image):

  * pure-Python parsers packaged in trixie parse ES5 only (`python3-calmjs.parse`) — they
    would flag valid `?.`, `??=`, class fields and async code and send working apps into
    repair loops; a JS parser written here would be neither complete nor trustworthy;
  * the shell already reports parse errors from the mounted iframe (window.onerror carries
    line/column) — but only after the app was delivered, through the expensive whole-document
    self-heal that D1 now caps; the gate belongs before `done`;
  * QuickJS accepts everything V8 accepts that generated apps use (verified 2026-09-28:
    class fields/private/static blocks, `?.`/`??`/logical assignment, numeric separators,
    BigInt, regex named groups/lookbehind/`d` flag, for-await, dynamic import(), new.target,
    tagged/nested templates, destructuring defaults, generators, Annex B HTML comments and
    `/]/` `/{/` regexes, legacy octal, `with`, ASI across lines) and rejects what V8 rejects
    (the `: async function` pattern, top-level `await`/`return`, unterminated strings,
    truncated files). The one divergence found — V8's regex `v` flag (ES2024) — is on the
    ignore list below; a QuickJS regex-syntax complaint is never reported (the runtime error
    path still catches a truly bad regex).

`node --check` is used when node is on PATH and qjsc is not (developer machines). Neither
installed → the gate is off (one log line; `/api/status.js_gate` says so); the old behaviour
(browser SyntaxError → bug report → self-heal) is unchanged. VIBEOS_JS_CHECK=off disables it,
VIBEOS_JS_CHECK=/path/to/qjsc or node pins a binary. A QuickJS older than 2024-01 (Ubuntu
noble ships 2021-03-27, which lacks class static blocks) is refused as too old.
"""
import os
import re
import shutil
import subprocess
import tempfile

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.I | re.S)
_CLASSIC_TYPES = ("", "text/javascript", "application/javascript", "text/ecmascript",
                  "application/ecmascript", "text/jscript", "module-less")
# QuickJS-vs-V8 divergences: a message matching one of these is not a defect of the document
_IGNORE = (re.compile(r"invalid regular expression", re.I),)
_MIN_QJS_VERSION = "2024-01-01"
TIMEOUT_S = 15


def _is_classic(attrs: str) -> bool:
    if re.search(r"\bsrc\s*=", attrs, re.I):
        return False
    m = re.search(r"""\btype\s*=\s*["']?\s*([^"'\s>]*)""", attrs, re.I)
    return not m or m.group(1).strip().lower() in _CLASSIC_TYPES


def _qjsc_version(path: str):
    try:
        out = subprocess.run([path, "-h"], capture_output=True, text=True, timeout=10)
        m = re.search(r"version (\d{4}-\d{2}-\d{2})", out.stdout + out.stderr)
        return m.group(1) if m else None
    except Exception:
        return None


def detect() -> dict:
    """The checker this kernel runs: {"kind": "qjsc"|"node"|None, "path", "version", "reason"}."""
    want = os.environ.get("VIBEOS_JS_CHECK", "").strip()
    if want.lower() in ("off", "0", "no", "none"):
        return {"kind": None, "path": None, "version": None, "reason": "disabled (VIBEOS_JS_CHECK=off)"}
    candidates = [want] if want else ["qjsc", "node"]
    for c in candidates:
        path = c if os.path.isabs(c) else shutil.which(c)
        if not path:
            continue
        base = os.path.basename(path)
        if base.startswith("node"):
            return {"kind": "node", "path": path, "version": None, "reason": None}
        ver = _qjsc_version(path)
        if ver and ver >= _MIN_QJS_VERSION:
            return {"kind": "qjsc", "path": path, "version": ver, "reason": None}
        if ver:
            return {"kind": None, "path": path, "version": ver,
                    "reason": f"quickjs {ver} is older than {_MIN_QJS_VERSION} (no ES2022 syntax) — install trixie's quickjs"}
    return {"kind": None, "path": None, "version": None,
            "reason": "no JS parser installed (apt install quickjs) — syntax gate off"}


CHECKER = detect()


def enabled() -> bool:
    return bool(CHECKER.get("kind"))


def _run(src: str) -> tuple:
    """(ok, message, line, col) for ONE script body as a classic (non-module) script."""
    kind, path = CHECKER["kind"], CHECKER["path"]
    with tempfile.NamedTemporaryFile("w", suffix=".js", prefix="kairos-gate-", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(src)
        name = tf.name
    try:
        if kind == "qjsc":
            cmd = [path, "-c", "-o", os.devnull, name]
        else:
            cmd = [path, "--check", name]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return True, "", 0, 0                  # never block delivery on a stuck parser
        except OSError:
            return True, "", 0, 0
        if r.returncode == 0:
            return True, "", 0, 0
        err = (r.stderr or "") + (r.stdout or "")
        return False, *_parse_error(err, name)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def _parse_error(err: str, name: str) -> tuple:
    """QuickJS: 'SyntaxError: msg\\n    at file:line:col'; node: 'file:line\\n<src>\\n   ^\\n\\nSyntaxError: msg'."""
    msg = ""
    for line in err.splitlines():
        if "SyntaxError" in line or "ReferenceError" in line:
            msg = line.strip()
            break
    if not msg:
        msg = (err.strip().splitlines() or ["syntax error"])[0].strip()
    line = col = 0
    m = re.search(re.escape(name) + r":(\d+)(?::(\d+))?", err)
    if m:
        line, col = int(m.group(1)), int(m.group(2) or 0)
    return msg[:300], line, col


def _excerpt(src: str, line: int) -> str:
    if not line:
        return ""
    lines = src.split("\n")
    if line > len(lines):
        line = len(lines)
    text = lines[line - 1].strip()
    return text[:160]


def js_syntax_problems(html: str) -> list:
    """validate_html() issues for every classic inline <script> that does not parse. Each
    issue names the script, the document line (the mounted document has the OS bridge in
    <head>, but this document's own numbering is what the repair prompt shows), the parser
    message and the offending line. Scripts are checked one by one, then — when all pass —
    once concatenated, which is how the browser sees their shared global lexical scope
    (`let x` declared in two <script> tags is a SyntaxError in the second)."""
    if not enabled():
        return []
    issues = []
    scripts = []
    for n, m in enumerate(_SCRIPT_RE.finditer(html), 1):
        attrs, body = m.group(1), m.group(2)
        if not _is_classic(attrs) or not body.strip():
            continue
        body_start = m.start(2)
        doc_line0 = html.count("\n", 0, body_start)      # document line of the body's first line = this + 1
        scripts.append((n, body, doc_line0))
        ok, msg, line, col = _run(body)
        if ok or any(p.search(msg) for p in _IGNORE):
            continue
        where = f"document line {doc_line0 + line}" if line else "position unknown"
        ex = _excerpt(body, line)
        issues.append(f"JavaScript does not parse — inline <script> #{n}, {where}"
                      + (f", column {col}" if col else "") + f": {msg}"
                      + (f" — the line reads: {ex}" if ex else "")
                      + " (fix this statement itself; keep the feature)")
    if not issues and len(scripts) > 1:
        joined = "\n;\n".join(b for _, b, _ in scripts)
        ok, msg, line, col = _run(joined)
        if not ok and not any(p.search(msg) for p in _IGNORE):
            # map the concatenated line back to (script, document line)
            pos, target = 0, None
            for n, body, doc_line0 in scripts:
                count = body.count("\n") + 1
                if line and pos < line <= pos + count:
                    target = (n, doc_line0 + (line - pos), _excerpt(body, line - pos))
                    break
                pos += count + 1                            # the "\n;\n" separator adds one line (";")
            # QuickJS reports a lexical redefinition without a position: name the culprits
            dups = _duplicate_lexicals(scripts)
            where = (f"inline <script> #{target[0]}, document line {target[1]}" if target
                     else "in <script> #" + ", #".join(str(n) for n, _, _ in scripts))
            issues.append(f"JavaScript does not parse when the inline scripts share the page's global scope — {where}: {msg}"
                          + (f" — the line reads: {target[2]}" if target and target[2] else "")
                          + (" — declared more than once: " + "; ".join(dups) if dups else "")
                          + " (a name declared with let/const/class in one <script> cannot be declared again in another"
                          " — declare it once, or use var / a property of window)")
    return issues


_LEXICAL_RE = re.compile(r"^[ \t]*(?:let|const|class)\s+([A-Za-z_$][\w$]*)", re.M)


def _duplicate_lexicals(scripts: list) -> list:
    """'x (#1, #3)' for every let/const/class name declared at the start of a line in more
    than one script — the redefinition QuickJS reports without a position."""
    seen = {}
    for n, body, _ in scripts:
        for name in set(_LEXICAL_RE.findall(body)):
            seen.setdefault(name, []).append(n)
    return [f"{name} (#{', #'.join(map(str, ns))})" for name, ns in sorted(seen.items()) if len(ns) > 1]
