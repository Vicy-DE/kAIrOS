"""[R77] The app forge's runtime check: load a generated app headless in Chromium, as the shell
mounts it, and report the JavaScript errors it throws — at load and after synthetic clicks
(docs/FORGE-POLISH-2026-10-07.md).

The language benchmark (docs/LANG-BENCH-2026-09.md) measured that RUNNING a program and feeding
the failure back is what repairs it; the JS syntax gate (kernel/jscheck.py) is the compiler step,
this is the test step. Pipeline position: _app_generate → validate_html (structure + syntax) clean
→ check() → problems go to the same repair round as gate problems.

How (one Chromium process per check, CPU only, ~1–3 s):
- `chromium --headless=new --disable-gpu --dump-dom --virtual-time-budget=…` on a temp profile,
  network routed to a dead proxy (the app must never reach the kernel or the internet);
- the page is a WRAPPER that mounts the app the way shell.js mountApp() does: a sandboxed
  srcdoc iframe ("allow-scripts allow-pointer-lock") with the shell's REAL app bridge
  (BRIDGE_SRC, read from shell/shell.js) injected after <head>; the wrapper plays the shell's
  part of the bridge protocol and answers every vibe.* call with CANNED data — never a real
  generation (stub_answer in WRAPPER_JS: media → a tiny PNG, storage → first-run values, model
  text → a canned sentence or, where the app wants structured data, no answer within the run —
  as a real model call takes seconds — so a canned guess can never cause a false error);
- a harness injected before the bridge records uncaught errors, unhandled rejections and
  console.error calls that carry a JS runtime error, fills visible inputs, clicks up to 10
  visible buttons/controls, presses a few keys, and posts its report to the wrapper, which
  writes it into its DOM (dumped on stdout). Chromium's stderr console lines ("Uncaught …")
  are parsed as a second source (parse_console).
`--disable-features=IsolateSandboxedIframes`: the virtual clock only covers same-process frames.
Missing Chromium (the test container) → {"ran": False, "skipped": "…"}: the forge goes on.
"""
import asyncio
import html as htmlmod
import json
import os
import re
import shutil
import signal
import tempfile
import time
from pathlib import Path

CHECK_TIMEOUT = 15.0        # s of wall time per check (Chromium start + the virtual run); then killed
VIRTUAL_BUDGET_MS = 10000   # virtual time the page gets (load + up to 6 fills, 12 clicks, 6 keys + settle)
MAX_PROBLEMS = 4
_BRIDGE = {"path": None, "mtime": None, "src": None}
# a Chromium that never produces a verdict (a build whose --dump-dom hangs: Chrome for Testing 151 in
# a container did) would cost CHECK_TIMEOUT per app: after MAX_SILENT such checks in a row the runtime
# check is off until the kernel restarts, with one journal line
MAX_SILENT = 2
_HEALTH = {"silent": 0, "off": None}

# Chromium console lines on stderr: Chromium ≥ ~120 `…:INFO:CONSOLE:12] "msg", source: url (12)`,
# older `…:INFO:CONSOLE(12)] "msg", source: url (12)`
CONSOLE_RE = re.compile(r':CONSOLE[:(](\d+)\)?\]\s+"(.*)",\s+source:\s+(\S*)\s+\((\d+)\)\s*$')
RUNTIME_ERROR_RE = re.compile(r"\b(TypeError|ReferenceError|SyntaxError|RangeError|URIError|EvalError|InternalError|"
                              r"AggregateError)\b|is not a function|is not defined|Cannot read propert|"
                              r"Cannot set propert|is not iterable|Maximum call stack")
JSON_PARSE_RE = re.compile(r"JSON|Unexpected token|Unexpected end of|is not valid JSON|Unexpected non-whitespace")


def find_chromium():
    env = os.environ.get("KAIROS_CHROMIUM")
    if env:
        return env if (os.path.isfile(env) and os.access(env, os.X_OK)) else None
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def bridge_source(shell_dir) -> str:
    """BRIDGE_SRC from shell/shell.js (the template literal, unescaped); cached per mtime. None when
    it cannot be extracted (then FALLBACK_BRIDGE is used — a check must never fail on the shell)."""
    p = Path(shell_dir) / "shell.js"
    try:
        mt = p.stat().st_mtime
    except OSError:
        return None
    if _BRIDGE["path"] == str(p) and _BRIDGE["mtime"] == mt:
        return _BRIDGE["src"]
    src = extract_template(p.read_text(encoding="utf-8", errors="replace"), "BRIDGE_SRC")
    _BRIDGE.update(path=str(p), mtime=mt, src=src)
    return src


def extract_template(js: str, name: str):
    """the value of `const <name> = `…`;` — a template literal without ${} (None otherwise)"""
    m = re.search(r"\bconst\s+" + re.escape(name) + r"\s*=\s*`", js)
    if not m:
        return None
    out, i = [], m.end()
    esc = {"n": "\n", "t": "\t", "r": "\r", "`": "`", "\\": "\\", "$": "$", "/": "/", "'": "'", '"': '"', "0": "\0"}
    while i < len(js):
        c = js[i]
        if c == "\\" and i + 1 < len(js):
            nxt = js[i + 1]
            if nxt in esc:
                out.append(esc[nxt])
                i += 2
                continue
            if nxt == "u" and re.match(r"[0-9a-fA-F]{4}", js[i + 2:i + 6]):
                out.append(chr(int(js[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(nxt)
            i += 2
            continue
        if c == "$" and js[i + 1:i + 2] == "{":
            return None
        if c == "`":
            return "".join(out)
        out.append(c)
        i += 1
    return None


# the shell's bridge protocol without the shell (used only when shell.js cannot be read)
FALLBACK_BRIDGE = """<script>(() => {
  let seq = 0; const pending = new Map();
  addEventListener("message", (e) => { const d = e.data; if (!d || !d.vibeReply) return; const p = pending.get(d.id);
    if (!p) return; pending.delete(d.id); d.error ? p.reject(new Error(d.error)) : p.resolve(d.result); });
  const call = (method, ...args) => new Promise((resolve, reject) => { const id = ++seq; pending.set(id, { resolve, reject });
    parent.postMessage({ vibe: true, id, method, args }, "*"); });
  const store = Object.assign((k, v) => call("store", k, v), { get: (k) => call("load", k), set: (k, v) => call("store", k, v) });
  const fs = {}; for (const op of ["list", "read", "write", "mkdir", "delete"]) fs[op] = (p, c) => call("fs", op, p, c);
  window.vibe = { ask: (p) => call("ask", p), search: (q, l) => call("search", q, l), browse: (a, f) => call("browse", a, f),
    image: Object.assign((p, w, h) => call("image", p, w, h), { improve: (p, i) => call("imageImprove", String(p), i || ""),
      versions: (p) => call("imageVersions", String(p)) }), images: (p, o) => call("images", p, o || {}), video: (p) => call("video", p),
    music: (...a) => call("music", ...a), vision: (i, p) => call("vision", i, p), translate: (t, to) => call("translate", t, to),
    dict: (...a) => call("dict", ...a), game: (p) => call("game", p), forge: (p) => call("forge", p), store,
    load: (k) => call("load", k), notify: (t) => call("notify", t), docs: (c) => call("docs", c), fs,
    settings: { get: (k) => call("settingsGet", k), set: (k, v) => call("settingsSet", k, v) },
    hw: new Proxy({}, { get: () => new Proxy(() => call("hw", {}), { get: () => () => call("hw", {}) }) }),
    wiki: (n) => call("wiki", n), models: () => call("models") };
  addEventListener("error", (e) => parent.postMessage({ vibe: true, id: 0, method: "jsError", args: [{ message: e.message }] }, "*"));
})();</script>"""

# injected FIRST into the app document (before the bridge): records errors, then interacts
HARNESS_JS = r"""<script>(() => {
  const post = (m) => { try { parent.postMessage({ kairosRT: m }, "*"); } catch (e) {} };
  let action = "load", phase = "load";
  const fmt = (x) => { try { return x && x.stack ? String(x.stack) : (typeof x === "object" ? JSON.stringify(x) : String(x)); } catch (e) { return String(x); } };
  addEventListener("error", (e) => {
    if (!(e instanceof ErrorEvent)) return;                    // a missing picture/resource is not a JS error
    post({ error: { kind: "uncaught", phase, after: action, msg: String(e.message || (e.error && e.error.message) || "error").slice(0, 500),
                    line: e.lineno || 0, col: e.colno || 0, stack: e.error && e.error.stack ? String(e.error.stack).slice(0, 700) : "" } });
  }, true);
  addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    post({ error: { kind: "unhandledrejection", phase, after: action, msg: String((r && r.message) || r).slice(0, 500),
                    line: 0, col: 0, stack: r && r.stack ? String(r.stack).slice(0, 700) : "" } });
  });
  const ce = console.error;
  console.error = function (...a) {
    post({ error: { kind: "console.error", phase, after: action, msg: a.map(fmt).join(" ").slice(0, 500), line: 0, col: 0, stack: "" } });
    return ce.apply(console, a);
  };
  addEventListener("submit", (e) => e.preventDefault(), true);  // a form post would unload the app mid-check
  // elements that get a click handler from script (el.onclick = …, addEventListener("click", …)) are
  // clickable although no selector finds them — recorded here, before any app script runs
  const tracked = [];
  const CLICKY = new Set(["click", "dblclick", "mousedown", "mouseup", "pointerdown", "pointerup", "touchstart"]);
  try {
    const add = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function (type, fn, opts) {
      try { if (CLICKY.has(type) && this instanceof Element && tracked.length < 400) tracked.push(this); } catch (e) {}
      return add.call(this, type, fn, opts);
    };
    const d = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "onclick");
    if (d && d.set) Object.defineProperty(HTMLElement.prototype, "onclick", { configurable: true, enumerable: d.enumerable, get: d.get,
      set: function (v) { try { if (v && tracked.length < 400) tracked.push(this); } catch (e) {} return d.set.call(this, v); } });
  } catch (e) {}
  const visible = (el) => {
    try {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none" && !el.disabled;
    } catch (e) { return false; }
  };
  const label = (el) => {
    const t = (el.innerText || el.value || el.getAttribute("aria-label") || el.title || "").trim().replace(/\s+/g, " ").slice(0, 40);
    return el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (t ? ' "' + t + '"' : "");
  };
  const done = [];
  const sel = "button, input[type=button], input[type=submit], input[type=checkbox], [role=button], [onclick], " +
              "a[href^='#'], a[href^='javascript:'], canvas, select";
  const clicked = new Set();
  let budget = 12;                                            // clicks over both passes
  const fill = (el) => {
    const t = (el.type || "text").toLowerCase();
    if (["button", "submit", "reset", "checkbox", "radio", "file", "color", "range", "hidden", "image"].includes(t)) return null;
    return () => {
      action = "typing into " + label(el);
      try {
        el.focus();
        el.value = t === "number" ? "42" : t === "email" ? "test@example.org" : t === "date" ? "2026-10-07"
                 : t === "time" ? "12:30" : t === "url" ? "https://example.org" : "test";
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        did(action);
      } catch (e) {}
    };
  };
  const click = (el) => () => {
    if (!el.isConnected || !visible(el)) return;
    action = "clicking " + label(el);
    try {
      const r = el.getBoundingClientRect(), o = { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
      if (el.tagName === "SELECT") {
        if (el.options.length > 1) { el.selectedIndex = el.options.length - 1; el.dispatchEvent(new Event("change", { bubbles: true })); }
      } else {
        el.dispatchEvent(new PointerEvent("pointerdown", o)); el.dispatchEvent(new MouseEvent("mousedown", o));
        el.dispatchEvent(new PointerEvent("pointerup", o)); el.dispatchEvent(new MouseEvent("mouseup", o));
        el.dispatchEvent(new MouseEvent("click", o));
      }
      did(action);
    } catch (e) {}
  };
  const key = (k) => () => {
    action = "pressing " + (k === " " ? "Space" : k);
    const t = document.activeElement && document.activeElement !== document.body ? document.activeElement : document;
    const o = { key: k, code: k === " " ? "Space" : k, bubbles: true, cancelable: true };
    try { t.dispatchEvent(new KeyboardEvent("keydown", o)); t.dispatchEvent(new KeyboardEvent("keyup", o)); did(action); } catch (e) {}
  };
  const run = (steps, then) => {                              // one step every 180 ms (virtual time)
    const s = steps.shift();
    if (!s) { then(); return; }
    s();
    setTimeout(() => run(steps, then), 150);
  };
  const did = (a) => { done.push(a); post({ action: a }); };
  // a pass clicks the visible controls not clicked yet — the second pass reaches what the first one
  // created (the "delete" button of the item that "Add" made)
  const clickPass = (then) => {
    const els = [];
    const rank = (el) => (el.matches("button, input[type=button], input[type=submit], [role=button]") ? 0
                          : el.matches("select, input[type=checkbox]") ? 1 : 2);   // real controls before clickable wrappers
    const all = [...new Set([...document.querySelectorAll(sel), ...tracked])].filter((el) => el.isConnected && visible(el));
    const pos = (x, y) => (x === y ? 0 : x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
    all.sort((x, y) => rank(x) - rank(y) || pos(x, y));      // real controls first, document order inside a rank
    for (const el of all) {
      if (els.length >= budget) break;
      if (!clicked.has(el) && el !== document.body && el !== document.documentElement) els.push(el);
    }
    budget -= els.length;
    els.forEach((el) => clicked.add(el));
    run(els.map(click), then);
  };
  const interact = () => {
    phase = "interaction";
    const fills = Array.from(document.querySelectorAll("input, textarea")).filter(visible).slice(0, 6).map(fill).filter(Boolean);
    run(fills, () => clickPass(() => clickPass(() => run(["ArrowRight", "ArrowUp", "ArrowLeft", "ArrowDown", " ", "Enter"].map(key), () => {
      action = "after the interactions";
      setTimeout(() => post({ done: true, actions: done }), 1500);
    }))));
  };
  const start = () => { post({ loaded: true }); setTimeout(interact, 700); };
  if (document.readyState === "complete") setTimeout(start, 0); else addEventListener("load", start);
})();</script>"""

# the wrapper page (top level): mounts the app like shell.js mountApp, answers the bridge, records
WRAPPER_JS = r"""
const CFG = JSON.parse(document.getElementById("__kairos_cfg").textContent);
const R = { errors: [], calls: [], jsError: null, loaded: false, done: false, actions: [], askAnswered: false };
const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAH0lEQVQI12NkYPjPQAxgYqAWGDVw1MBRA0cNHDWQBgAAIScBEf2DXHIAAAAASUVORK5CYII=";
const PENDING = {};
const CANCEL = (what) => ({ reject: "cancelled: the user did not confirm " + what });
function stubAnswer(m, a) {
  const s = (x) => String(x == null ? "" : x);
  switch (m) {
    case "ask":                       // free text: a canned sentence; structured data (JSON …): no answer within the run
      if (/json|array|\{|\[/i.test(s(a[0]))) return PENDING;
      R.askAnswered = true; return "A short sample answer from the kAIrOS language model.";
    case "translate": return a[0];
    case "translateLang": return null;
    case "dict": return { entries: [] };
    case "image": return PNG;
    case "imageImprove": return { url: PNG, version: 1, root: "p0000000000000000", from: 0, versions: [], model: null, ms: 0 };   // [R88]
    case "imageVersions": return { root: null, current: 0, url: s(a[0]), source: s(a[0]), versions: [] };
    case "images": return Array.isArray(a[0]) ? a[0].map(() => PNG) : Array.from({ length: Math.min(8, Math.max(1, +((a[1] || {}).count) || 1)) }, () => PNG);
    case "music": case "forge": case "store": case "notify": return true;
    case "load": return null;
    case "fs": {
      const op = s(a[0]);
      if (op === "list") return [];
      if (op === "read") return { reject: "not found: " + s(a[1]) };
      if (op === "format") return CANCEL("the factory reset");
      return true;
    }
    case "settingsGet": return a[0] ? CFG.settings[a[0]] : CFG.settings;
    case "settingsSet": CFG.settings[s(a[0])] = a[1]; return CFG.settings;
    case "docs": return "kAIrOS API reference (canned in the runtime check)";
    case "upgrade": return CANCEL("the tier download");
    case "hw": { const q = a[0] || {}; return (q.device === "power" && q.op) ? CANCEL("the power action") : PENDING; }
    default: return PENDING;          // search, browse, vision, video, game, hw, wiki, models, setup …: no answer within the run
  }
}
function finish() {
  document.getElementById("__kairos_rt").textContent = JSON.stringify(R);
}
addEventListener("message", (e) => {
  const d = e.data, frame = document.getElementById("app").contentWindow;
  if (!d || typeof d !== "object") return;
  if (d.kairosRT) {
    const m = d.kairosRT;
    if (m.error && R.errors.length < 30) { m.error.askAnswered = R.askAnswered; R.errors.push(m.error); }
    if (m.loaded) R.loaded = true;
    if (m.action && R.actions.length < 40) R.actions.push(m.action);
    if (m.done) { R.done = true; finish(); }
    return;
  }
  if (!d.vibe) return;
  if (d.method === "jsError") { R.jsError = (d.args || [])[0] || null; return; }
  if (R.calls.length < 40) R.calls.push(d.method);
  const ans = stubAnswer(d.method, d.args || []);
  if (ans === PENDING) return;
  setTimeout(() => {
    if (ans && typeof ans === "object" && ans.reject) frame.postMessage({ vibeReply: true, id: d.id, error: ans.reject }, "*");
    else frame.postMessage({ vibeReply: true, id: d.id, result: ans }, "*");
  }, 120);
});
document.getElementById("app").srcdoc = CFG.doc;
setTimeout(finish, CFG.budget - 250);
"""


def inject(html: str, bridge: str):
    """-> (document, first_line, n_lines): the harness + bridge after <head …> as shell.js
    injectBridge() does; the injected block starts on `first_line` and adds `n_lines` newlines"""
    block = HARNESS_JS + bridge
    m = re.search(r"<head[^>]*>", html, re.I)
    end = m.end() if m else 0
    return html[:end] + block + html[end:], html[:end].count("\n") + 1, block.count("\n")


def wrapper_page(doc: str, settings: dict, budget_ms: int) -> str:
    cfg = json.dumps({"doc": doc, "settings": settings or {}, "budget": budget_ms})
    cfg = cfg.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"><title>kairos runtime check</title></head>'
            '<body style="margin:0;background:#0a0a14">'
            '<iframe id="app" sandbox="allow-scripts allow-pointer-lock" style="width:1100px;height:760px;border:0"></iframe>'
            '<pre id="__kairos_rt"></pre>'
            f'<script type="application/json" id="__kairos_cfg">{cfg}</script>'
            f"<script>{WRAPPER_JS}</script></body></html>")


def parse_report(dom: str):
    """the wrapper's JSON report out of Chromium's --dump-dom output (None when absent)"""
    m = re.search(r'<pre id="__kairos_rt">(.*?)</pre>', dom or "", re.S)
    if not m or not m.group(1).strip():
        return None
    try:
        return json.loads(htmlmod.unescape(m.group(1)))
    except ValueError:
        return None


def parse_console(stderr: str) -> list:
    """Chromium's stderr console lines -> [{"msg", "line", "source"}] for the "Uncaught …" ones
    (the page's own console.log, resource and sandbox notices are not errors)"""
    out = []
    for ln in (stderr or "").splitlines():
        m = CONSOLE_RE.search(ln)
        if not m:
            continue
        msg = m.group(2).replace('\\"', '"')
        if msg.startswith("Uncaught"):
            out.append({"msg": msg[:500], "line": int(m.group(4)), "source": m.group(3)})
    return out


def map_line(line, first, n):
    """srcdoc line -> ("app", line) | ("bridge", None): undo the injected block's newlines"""
    if not line:
        return ("app", None)
    if line < first:
        return ("app", line)
    if line < first + n:
        return ("bridge", None)
    return ("app", line - n)


def app_line_text(html: str, line):
    try:
        return html.splitlines()[line - 1].strip()[:180]
    except (TypeError, IndexError):
        return ""


def _norm(msg: str) -> str:
    """one error, however it was reported: 'Uncaught (in promise) Error: x' == 'x' == 'Error: x'"""
    m = re.sub(r"^Uncaught\s+(\(in promise\)\s+)?", "", (msg or "").strip())
    return re.sub(r"^[A-Za-z]*Error:\s*", "", m)


def problems_from(report, console, html: str, first: int, n: int) -> tuple:
    """-> (problems, errors): repair-round problem lines + the raw records kept. Counted: uncaught
    errors, unhandled rejections, console.error carrying a JS runtime error; NOT counted: a JSON
    parse error after the canned free-text vibe.ask answer (a real answer may well be JSON)."""
    errs, seen = [], set()
    for e in (report or {}).get("errors", []):
        if e.get("kind") == "console.error" and not RUNTIME_ERROR_RE.search(e.get("msg", "")):
            continue
        if e.get("askAnswered") and JSON_PARSE_RE.search(e.get("msg", "")):
            continue
        errs.append(e)
    rep_msgs = {_norm(e.get("msg", "")) for e in (report or {}).get("errors", [])}
    for c in console or []:          # what the harness did not see (it was not installed in time, a report was lost)
        msg = _norm(c["msg"])
        if msg not in rep_msgs and not (JSON_PARSE_RE.search(msg) and (report or {}).get("askAnswered")):
            errs.append({"kind": "uncaught", "phase": "?", "after": "?", "msg": c["msg"], "line": c["line"], "col": 0, "stack": ""})
            rep_msgs.add(msg)
    problems = []
    for e in errs:
        key = (e.get("msg"), e.get("line"))
        if key in seen:
            continue
        seen.add(key)
        where, line = map_line(e.get("line"), first, n)
        what = {"uncaught": "uncaught exception", "unhandledrejection": "unhandled promise rejection",
                "console.error": "console.error"}.get(e.get("kind"), "error")
        when = ("while the app loaded" if e.get("phase") == "load" or e.get("after") == "load"
                else f"after {e.get('after')}" if e.get("after") not in (None, "?") else "while the app ran")
        msg = re.sub(r"^Uncaught\s+", "", e.get("msg", ""))
        p = f"runtime JavaScript error ({what} {when}, headless test run): {msg}"
        if where == "bridge":
            p += (" — thrown inside the OS's vibe bridge: a vibe.* call failed (or got wrong arguments) and the "
                  "app did not handle it — catch the error of that call")
        elif line:
            p += f" — app line {line}" + (f": {app_line_text(html, line)}" if app_line_text(html, line) else "")
        problems.append(p[:600])
    return problems[:MAX_PROBLEMS], errs


REPEAT_NOTES = {
    "identical": ("\n\nIMPORTANT: your previous answer was IDENTICAL to a document that already failed these checks — "
                  "it changed nothing. Do NOT send it again: find the code that causes the problems listed above and "
                  "CHANGE it (rewrite that function or handler if needed), then output the full corrected document."),
    "same-error": ("\n\nIMPORTANT: the SAME problem came back after your previous repair — that repair did not address "
                   "its cause. Re-read the line named above and everything it depends on (element ids, variable names, "
                   "the order things are created in, what a vibe.* call returns) and fix the root cause."),
}


def repeat_note(kind) -> str:
    """[R77] the stronger order for an identical resubmission / a repeated error (lang-bench finding)"""
    return REPEAT_NOTES.get(kind, "")


def _kill(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def check(html: str, *, shell_dir, settings: dict = None, popen: dict = None,
                budget_ms: int = None, timeout: float = None) -> dict:
    """Load the app headless and interact with it. -> {"ran", "skipped", "problems", "errors",
    "actions", "calls", "seconds", "sandbox"}; `ran` False = no verdict (no Chromium, it did not
    start, timed out) — the caller treats that as "no runtime problems known"."""
    t0 = time.monotonic()
    chromium = find_chromium()
    if not chromium:
        return {"ran": False, "skipped": "no chromium on this system", "problems": [], "errors": [],
                "seconds": 0.0}
    if _HEALTH["off"]:
        return {"ran": False, "skipped": _HEALTH["off"], "problems": [], "errors": [], "seconds": 0.0}
    budget_ms = budget_ms or VIRTUAL_BUDGET_MS
    timeout = timeout or CHECK_TIMEOUT
    bridge = bridge_source(shell_dir) or FALLBACK_BRIDGE
    doc, first, n = inject(html, bridge)
    tmp = Path(tempfile.mkdtemp(prefix="kairos-appcheck-"))
    try:
        page = tmp / "check.html"
        page.write_text(wrapper_page(doc, settings, budget_ms), encoding="utf-8")
        if popen and popen.get("user") is not None:
            for p in (tmp, page):
                try:
                    os.chown(p, popen["user"], popen.get("group", -1))
                except OSError:
                    pass
        as_root = os.geteuid() == 0 and not (popen and popen.get("user") not in (None, 0))
        result = None
        for no_sandbox in ((True,) if as_root else (False, True)):
            result = await _run(chromium, page, tmp, budget_ms, timeout, popen, no_sandbox)
            if not (result.get("sandbox_failed") and not no_sandbox):
                break
        rep = parse_report(result.get("stdout", ""))
        console = parse_console(result.get("stderr", ""))
        if rep is None and not console:
            why = "timed out" if result.get("timed_out") else f"no report (exit {result.get('rc')})"
            tail = [ln for ln in result.get("stderr", "").splitlines() if "ERROR" in ln or "FATAL" in ln][-2:]
            _HEALTH["silent"] += 1
            if _HEALTH["silent"] >= MAX_SILENT:
                _HEALTH["off"] = f"off until the kernel restarts: {chromium} gave no verdict {MAX_SILENT}× in a row ({why})"
                print(f"[kernel] WARN app runtime check {_HEALTH['off']}", flush=True)
            return {"ran": False, "skipped": f"chromium: {why}" + (f" — {' | '.join(tail)[:300]}" if tail else ""),
                    "problems": [], "errors": [], "seconds": round(time.monotonic() - t0, 1)}
        _HEALTH["silent"] = 0
        problems, errs = problems_from(rep, console, html, first, n)
        return {"ran": True, "skipped": None, "problems": problems, "errors": errs[:10],
                "actions": (rep or {}).get("actions", [])[:20], "calls": (rep or {}).get("calls", [])[:20],
                "loaded": bool((rep or {}).get("loaded")), "finished": bool((rep or {}).get("done")),
                "seconds": round(time.monotonic() - t0, 1), "sandbox": not result.get("no_sandbox")}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _run(chromium, page, tmp, budget_ms, timeout, popen, no_sandbox):
    args = [chromium, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-background-networking", "--disable-sync", "--disable-default-apps",
            "--disable-component-update", "--disable-dev-shm-usage", "--mute-audio", "--hide-scrollbars",
            "--window-size=1280,900", "--disable-features=IsolateSandboxedIframes,Translate,MediaRouter,OptimizationHints",
            "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
            f"--user-data-dir={tmp / 'profile'}", "--enable-logging=stderr", "--v=0",
            f"--virtual-time-budget={budget_ms}", "--dump-dom"]
    if no_sandbox:
        args.append("--no-sandbox")
    args.append(page.as_uri())
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TZ")}
    env.update(HOME=str(tmp))
    proc = await asyncio.create_subprocess_exec(*args, cwd=str(tmp), env=env, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                start_new_session=True, **(popen or {}))
    timed_out = False
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        timed_out = True
        _kill(proc)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 3)
        except asyncio.TimeoutError:
            out, err = b"", b""
    _kill(proc)                      # renderer/zygote leftovers of the group
    err_s = err.decode("utf-8", "replace")
    return {"stdout": out.decode("utf-8", "replace"), "stderr": err_s[-200000:], "rc": proc.returncode,
            "timed_out": timed_out, "no_sandbox": no_sandbox,
            "sandbox_failed": "No usable sandbox" in err_s or ("sandbox" in err_s.lower() and "FATAL" in err_s)}
