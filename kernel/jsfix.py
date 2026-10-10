"""Deterministic fixes for generated app JavaScript [R12, docs/LLM-BENCH-2026-09.md
"Shared generation failures"].

The coder LLMs habitually write `await vibe.load(...)` at the top level of a classic
inline <script>, which is a SyntaxError in every browser (10 of 48 benchmark apps died on it
before running a single line). Wrapping such a script in an async IIFE is a correct
transformation *only if* the script's top-level declarations are re-exported to `window`:
25 of the 48 apps drive their UI through inline `onclick="fn()"` handlers, which look the
function up in the global scope. So this module

  * tokenizes a script (comments, strings, template literals, regex literals skipped),
  * tracks function boundaries (function/method bodies, arrow functions) to tell a
    top-level `await` from one inside an async function,
  * collects the names declared at the script's top level (function/class/const/let/var),
  * and rewrites only the scripts that need it: `(async function(){ /*kairos:tla*/ …
    }).call(window)` with the declared names copied onto `window` — once right away
    (hoisted function declarations, so inline handlers work before the first await
    resolves) and once after the last statement (const/let arrow functions).

Anything not understood is left alone: a false negative keeps today's behaviour (the
browser's SyntaxError becomes a bug report → self-repair), a false positive wraps a script
that did not need it, which is semantically neutral (an async function runs synchronously
until its first await; sloppy mode is kept; `this` is window).
"""
import re

_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
_NUM_RE = re.compile(r"(?:0[xXoObB][\w]+|\d[\w]*(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)")
_WS_RE = re.compile(r"[ \t\r\n\f\v   ]+")
_PUNCT3 = ("===", "!==", "**=", "<<=", ">>=", ">>>", "...", "&&=", "||=", "??=")
_PUNCT2 = ("=>", "==", "!=", "<=", ">=", "&&", "||", "??", "?.", "++", "--", "+=", "-=", "*=",
           "/=", "%=", "&=", "|=", "^=", "<<", ">>", "**")
# a `/` after one of these starts a regex literal, not a division
_REGEX_AFTER_KW = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
                   "throw", "case", "do", "else", "yield", "await"}
_CONTROL_KW = {"if", "for", "while", "switch", "catch", "with"}
_RESERVED = {"break", "case", "catch", "class", "const", "continue", "debugger", "default",
             "delete", "do", "else", "export", "extends", "finally", "for", "function", "if",
             "import", "in", "instanceof", "new", "return", "super", "switch", "this", "throw",
             "try", "typeof", "var", "void", "while", "with", "yield", "let", "static", "enum",
             "await", "async", "implements", "interface", "package", "private", "protected",
             "public", "null", "true", "false", "undefined", "arguments", "eval", "of", "get", "set"}


class Tok:
    __slots__ = ("kind", "val", "pos", "nl")

    def __init__(self, kind, val, pos, nl):
        self.kind, self.val, self.pos, self.nl = kind, val, pos, nl   # nl: a newline precedes it

    def __repr__(self):
        return f"{self.kind}:{self.val!r}"


def tokenize(src: str) -> list:
    """Significant tokens of a JS source. Strings/comments/regex bodies are dropped, a
    template literal yields one 'tpl' token per text run and its ${…} expressions are
    tokenized in place (between 'tpl${' and 'tpl}' tokens)."""
    out = []
    i, n = 0, len(src)
    nl = False
    tpl_stack = []          # per open ${: net {…} depth of the code inside it

    def prev():
        return out[-1] if out else None

    def regex_allowed():
        p = prev()
        if p is None:
            return True
        if p.kind in ("num", "str", "tpl"):
            return False
        if p.kind == "id":
            return p.val in _REGEX_AFTER_KW
        return p.val not in (")", "]", "}")

    while i < n:
        c = src[i]
        m = _WS_RE.match(src, i)
        if m:
            if "\n" in m.group(0) or " " in m.group(0) or " " in m.group(0):
                nl = True
            i = m.end()
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j == -1:
                break
            if "\n" in src[i:j]:
                nl = True
            i = j + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                elif src[j] == "\n":
                    break
                j += 1
            out.append(Tok("str", src[i + 1:j], i, nl))   # the body: dialog_calls reads window["prompt"]
            nl = False
            i = j + 1
            continue
        if c == "`" or (c == "}" and tpl_stack and tpl_stack[-1] == 0):
            # template text: from the opening ` (or the } that closes a ${…}) to the next ${ or `
            if c == "}":
                out.append(Tok("tpl}", "}", i, nl))
                tpl_stack.pop()
            else:
                out.append(Tok("tpl", "", i, nl))
            nl = False
            j = i + 1
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "`":
                    j += 1
                    break
                if ch == "$" and j + 1 < n and src[j + 1] == "{":
                    out.append(Tok("tpl${", "${", j, False))
                    tpl_stack.append(0)
                    j += 2
                    break
                j += 1
            i = j
            continue
        if c == "{" and tpl_stack:
            tpl_stack[-1] += 1
        elif c == "}" and tpl_stack:
            tpl_stack[-1] -= 1
        if c == "/" and regex_allowed():
            j, in_class = i + 1, False
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "\n":
                    break
                if in_class:
                    if ch == "]":
                        in_class = False
                elif ch == "[":
                    in_class = True
                elif ch == "/":
                    break
                j += 1
            j += 1
            while j < n and (src[j].isalpha()):
                j += 1
            out.append(Tok("re", "", i, nl))
            nl = False
            i = j
            continue
        m = _IDENT_RE.match(src, i) or (c == "#" and _IDENT_RE.match(src, i + 1))
        if m:
            out.append(Tok("id", src[i:m.end()], i, nl))
            nl = False
            i = m.end()
            continue
        m = _NUM_RE.match(src, i)
        if m and (c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit())):
            out.append(Tok("num", m.group(0), i, nl))
            nl = False
            i = m.end()
            continue
        for group in (_PUNCT3, _PUNCT2):
            p = next((p for p in group if src.startswith(p, i)), None)
            if p:
                out.append(Tok("op", p, i, nl))
                nl = False
                i += len(p)
                break
        else:
            out.append(Tok("op", c, i, nl))
            nl = False
            i += 1
    return out


class ScriptInfo:
    def __init__(self):
        self.top_level_await = False      # an await outside every function → SyntaxError in a classic script
        self.await_in_sync_fn = False     # an await inside a function that is not async → SyntaxError
        self.async_inserts = []           # char offsets where `async ` makes such a function async
        self.names = []                   # top-level declarations, in order
        self.uses_this_top = False


def scan_js(src: str) -> ScriptInfo:
    toks = tokenize(src)
    info = ScriptInfo()
    ctx = []             # stack of dicts: kind fn|block|arrow|tpl, async
    parens = []          # index of the token before each open (
    paren_depth = bracket_depth = 0
    seen = set()

    def fn_ctx():
        for c in reversed(ctx):
            if c["kind"] in ("fn", "arrow"):
                return c
        return None

    def is_async_before(k):
        """async keyword right before token k (skipping a `*`)?"""
        while k >= 0 and toks[k].kind == "op" and toks[k].val == "*":
            k -= 1
        return k >= 0 and toks[k].kind == "id" and toks[k].val == "async"

    def arrow_async(k):
        """k = index of `=>`: async if `async x =>` or `async (…) =>`"""
        if k - 1 < 0:
            return False
        p = toks[k - 1]
        if p.kind == "id":
            return is_async_before(k - 2)
        if p.kind == "op" and p.val == ")":
            o = _match_open(toks, k - 1)
            return o is not None and is_async_before(o - 1)
        return False

    def arrow_header(k):
        """k = index of `=>`: char offset of its parameter list (where `async ` goes)"""
        p = toks[k - 1] if k >= 1 else None
        if p is None:
            return None
        if p.kind == "id":
            return p.pos
        if p.kind == "op" and p.val == ")":
            o = _match_open(toks, k - 1)
            return toks[o].pos if o is not None else None
        return None

    def pop_arrows_at(depths):
        while ctx and ctx[-1]["kind"] == "arrow" and ctx[-1]["depth"] >= depths:
            ctx.pop()

    def add_name(name):
        if name and name not in seen and name not in _RESERVED and _IDENT_RE.fullmatch(name):
            seen.add(name)
            info.names.append(name)

    i = 0
    while i < len(toks):
        t = toks[i]
        top = not ctx and paren_depth == 0 and bracket_depth == 0
        if t.kind == "op":
            v = t.val
            if v == "(":
                parens.append(i - 1)
                paren_depth += 1
            elif v == ")":
                pop_arrows_at((paren_depth, bracket_depth))
                paren_depth = max(0, paren_depth - 1)
                if parens:
                    parens.pop()
            elif v == "[":
                bracket_depth += 1
            elif v == "]":
                pop_arrows_at((paren_depth, bracket_depth))
                bracket_depth = max(0, bracket_depth - 1)
            elif v in (",", ";"):
                pop_arrows_at((paren_depth, bracket_depth))
            elif v == "{":
                kind, is_async, hdr = "block", False, None
                p = toks[i - 1] if i else None
                if p is not None and p.kind == "op" and p.val == "=>":
                    kind, is_async, hdr = "fn", arrow_async(i - 1), arrow_header(i - 1)
                elif p is not None and p.kind == "op" and p.val == ")":
                    o = _match_open(toks, i - 1)
                    b = toks[o - 1] if o is not None and o >= 1 else None
                    if b is not None and b.kind == "id":
                        if b.val == "function":
                            kind, is_async, hdr = "fn", is_async_before(o - 2), b.pos
                        elif o >= 2 and toks[o - 2].kind == "id" and toks[o - 2].val == "function":
                            kind, is_async, hdr = "fn", is_async_before(o - 3), toks[o - 2].pos
                        elif b.val not in _CONTROL_KW and b.val not in ("return", "await", "typeof",
                                                                          "yield", "in", "of", "new"):
                            kind, is_async = "fn", is_async_before(o - 2)   # method shorthand
                            before = toks[o - 2] if o >= 2 else None
                            # getters/setters/constructors cannot be async; `static m(){` can
                            if b.val != "constructor" and not (before is not None and before.kind == "id"
                                                               and before.val in ("get", "set")):
                                hdr = b.pos
                    elif b is not None and b.kind == "op" and b.val == "*":   # function*(){ / *gen(){
                        kind, is_async = "fn", False
                ctx.append({"kind": kind, "async": is_async, "hdr": hdr})
            elif v == "}":
                pop_arrows_at((paren_depth, bracket_depth))
                if ctx:
                    ctx.pop()
            elif v == "=>":
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if not (nxt is not None and nxt.kind == "op" and nxt.val == "{"):
                    ctx.append({"kind": "arrow", "async": arrow_async(i), "hdr": arrow_header(i),
                                "depth": (paren_depth, bracket_depth)})
        elif t.kind == "tpl${":
            ctx.append({"kind": "tpl", "async": False})
        elif t.kind == "tpl}":
            if ctx:
                ctx.pop()
        elif t.kind == "id":
            v = t.val
            if v == "await":
                f = fn_ctx()
                if f is None:
                    info.top_level_await = True
                elif not f["async"]:
                    info.await_in_sync_fn = True
                    if f.get("hdr") is not None:
                        info.async_inserts.append(f["hdr"])
                        f["async"] = True                 # one insertion per function
            elif top and v == "this":
                info.uses_this_top = True
            elif top and v in ("function", "class"):
                k = i + 1
                while k < len(toks) and toks[k].kind == "op" and toks[k].val == "*":
                    k += 1
                if k < len(toks) and toks[k].kind == "id":
                    add_name(toks[k].val)
            elif top and v in ("const", "let", "var"):
                _declarators(toks, i + 1, add_name)      # names only — the loop keeps scanning
        i += 1
    return info


def _match_open(toks, close_idx):
    """index of the ( matching the ) at close_idx"""
    d = 0
    k = close_idx
    while k >= 0:
        t = toks[k]
        if t.kind == "op":
            if t.val == ")":
                d += 1
            elif t.val == "(":
                d -= 1
                if d == 0:
                    return k
        k -= 1
    return None


def _declarators(toks, i, add_name):
    """names of `const a = …, {b, c} = …, [d] = …;` starting at token i (after the keyword);
    returns the index after the statement (the caller only uses the names)"""
    n = len(toks)
    while i < n:
        t = toks[i]
        if t.kind == "id":
            add_name(t.val)
            i += 1
        elif t.kind == "op" and t.val in ("{", "["):
            close = {"{": "}", "[": "]"}[t.val]
            d = 0
            while i < n:
                u = toks[i]
                if u.kind == "op" and u.val in ("{", "["):
                    d += 1
                elif u.kind == "op" and u.val in ("}", "]"):
                    d -= 1
                    if d == 0:
                        i += 1
                        break
                elif u.kind == "id" and not (i + 1 < n and toks[i + 1].kind == "op" and toks[i + 1].val == ":"):
                    add_name(u.val)
                i += 1
        else:
            return i
        # initializer?
        if i < n and toks[i].kind == "op" and toks[i].val == "=":
            i += 1
            pd = bd = cd = 0
            while i < n:
                u = toks[i]
                if u.kind == "op":
                    if u.val in ("(",):
                        pd += 1
                    elif u.val == ")":
                        pd -= 1
                    elif u.val == "[":
                        bd += 1
                    elif u.val == "]":
                        bd -= 1
                    elif u.val == "{":
                        cd += 1
                    elif u.val == "}":
                        cd -= 1
                    elif u.val == ";" and pd == bd == cd == 0:
                        return i + 1
                    elif u.val == "," and pd == bd == cd == 0:
                        i += 1
                        break
                elif u.kind == "tpl${":
                    cd += 1
                elif u.kind == "tpl}":
                    cd -= 1
                if pd == bd == cd == 0 and i + 1 < n and toks[i + 1].nl and _statement_start(toks, i, i + 1):
                    return i + 1             # ASI: the next line starts a new statement
                i += 1
            if i >= n or pd < 0 or bd < 0 or cd < 0:
                return i
            continue
        if i < n and toks[i].kind == "op" and toks[i].val == ",":
            i += 1
            continue
        if i < n and toks[i].kind == "op" and toks[i].val == ";":
            return i + 1
        return i
    return i


_CONTINUES = {".", "?.", "(", "[", "+", "-", "*", "/", "%", "&&", "||", "??", "?", ":", "=", "==", "===",
              "!=", "!==", "<", ">", "<=", ">=", ",", "=>", "**", "&", "|", "^", "<<", ">>", ">>>",
              "+=", "-=", "*=", "/=", "&&=", "||=", "??=", "in", "instanceof", "of"}


def _statement_start(toks, last, nxt):
    """ASI heuristic: after a line break, does token nxt begin a new statement?"""
    a, b = toks[last], toks[nxt]
    if a.kind == "op" and a.val in _CONTINUES:
        return False
    if b.kind == "op" and b.val in _CONTINUES and b.val not in ("+", "-"):
        return False
    if b.kind == "op" and b.val in ("(", "["):
        return False
    return True


TLA_MARK = "kairos:tla"
_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
_SKIP_WINDOW = {"window", "self", "document", "location", "top", "parent", "frames", "globalThis"}


def wrap_top_level_await(src: str, names: list) -> str:
    """The async-IIFE rewrite (see module docstring). Everything is placed on the script's
    first/last line so the line numbers the browser reports for later errors — and the
    excerpt the shell attaches to a bug report — still match the original code."""
    body = src
    directive = ""
    m = re.match(r'\s*(["\'])use strict\1\s*;?', body)
    if m:
        directive = '"use strict";'
        body = body[m.end():]
    stmts = "".join(
        f'try{{if(typeof w.{n}==="function"||w.{n}===undefined)w.{n}={n}}}catch(e){{}}'
        for n in names if n not in _SKIP_WINDOW)
    prefix = (f"(async function(){{{directive}/*{TLA_MARK}: top-level await wrapped by the OS*/"
              f"function __kairosExport(){{var w=window;{stmts}}}__kairosExport();")
    suffix = ("\n;__kairosExport()}).call(window).catch(function(e){setTimeout(function(){throw e})});")
    return prefix + body + suffix


def _is_classic_script(attrs: str) -> bool:
    if re.search(r"\bsrc\s*=", attrs, re.I):
        return False
    m = re.search(r"""\btype\s*=\s*["']?\s*([^"'\s>]*)""", attrs, re.I)
    return not m or m.group(1).lower() in ("", "text/javascript", "application/javascript", "module-less")


def fix_script(body: str) -> str:
    """One classic script: (1) every function that awaits without being async gets the
    `async` keyword (the function keeps its name, body and callers — only its return value
    becomes a promise, which is what the LLM meant); (2) a script that awaits at its top
    level is wrapped (see wrap_top_level_await). Idempotent."""
    if TLA_MARK in body or not re.search(r"\bawait\b", body):
        return body
    try:
        info = scan_js(body)
        if info.async_inserts:
            for pos in sorted(set(info.async_inserts), reverse=True):
                body = body[:pos] + "async " + body[pos:]
            info = scan_js(body)                 # positions moved; names are the same
        if info.top_level_await:
            body = wrap_top_level_await(body, info.names)
    except Exception:
        return body
    return body


def harden_app_html(html: str) -> str:
    """Rewrite every classic inline <script> of a generated app (fix_script). Idempotent."""
    def fix(m):
        attrs, body = m.group(1), m.group(2)
        if not _is_classic_script(attrs):
            return m.group(0)
        fixed = fix_script(body)
        return m.group(0) if fixed == body else f"<script{attrs}>{fixed}</script>"
    return _SCRIPT_RE.sub(fix, html)


def js_problems(html: str) -> list:
    """Static checks no rewrite can fix — they go to validate_html so the existing
    self-repair round corrects them before delivery (the sandbox has no origin: a direct
    fetch('/api/…') from an app dies on CORS, and R66 keeps it that way)."""
    issues = []
    if re.search(r"""fetch\s*\(\s*[`'"](?:https?://[^/'"`]+)?/api/""", html):
        issues.append("the app calls the OS REST API directly with fetch('/api/…') — the sandbox blocks "
                      "that (CORS); use the injected window.vibe methods instead (vibe.ask, vibe.hw.get, "
                      "vibe.store/vibe.load, vibe.search, …)")
    # native dialogs [D5, docs/FIXES-2026-09-28.md]: the sandboxed iframe ignores prompt()/confirm() —
    # they answer null/false, so a "New file" wired to prompt() is dead and every confirm() is a
    # "no". The prompt rule alone did not stop Gemma 4 from writing prompt() in the 2026-09-28 smoke,
    # so the pipeline's repair round replaces them before delivery (alert() only shows a notice and
    # is left alone). Matches the global call (bare or window.), not an app's own foo.prompt().
    dialogs = dialog_calls(html)
    if dialogs:
        issues.append("the app calls " + " and ".join(f"{d}()" for d in dialogs) + " — native dialogs are blocked in "
                      "the sandbox (prompt() returns null, confirm() returns false, nothing is shown), so that "
                      "feature is dead: replace every prompt() with an in-page <input> + button and every confirm() "
                      "with an in-page Yes/No dialog element; keep the feature")
    return issues


# ---- the dialog check [D5], on executable code only [N1, docs/FIXES-2026-09-29.md] ------------
# The first version was a regex over the WHOLE document text, so `placeholder="Describe your
# image prompt (e.g. a red fox)"`, `<p>please confirm (type YES)</p>`, the comment
# `// never use prompt() here` or the string "confirm() is blocked" each cost an app a
# whole-document repair round (~4 min of Gemma 4) it did not need. Now only executable
# JavaScript is inspected — inline <script> bodies (classic and module; src=/JSON/template
# types skipped) and inline on*="…" handlers — through the tokenizer above, which drops
# strings, comments, regex bodies and template-literal text (a `${…}` expression inside a
# template IS code and is kept). And only the GLOBAL call counts: `prompt(`, `window.prompt(`,
# `self.prompt(`, `globalThis.confirm(`, `top.prompt(`, `window?.prompt(`, `window["prompt"](`;
# an app's own `ui.prompt()`, `this.confirm()`, `promptUser()`, a method `prompt(q) {…}`, a
# declared `function prompt()`/`const prompt` pass (they are the app's, not the blocked dialog).
_DIALOG_NAMES = ("confirm", "prompt")
_GLOBAL_OBJS = {"window", "self", "globalThis", "top", "parent", "frames"}
_JS_TYPES = ("", "text/javascript", "application/javascript", "text/ecmascript",
             "application/ecmascript", "text/jscript", "module", "module-less")
_TAG_RE = re.compile(r"""<[a-zA-Z][^\s/>]*(?:\s+[^\s=/>]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>"']+))?)*\s*/?>""")
_HANDLER_ATTR_RE = re.compile(r"""\s(on[a-z]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)


def _script_type(attrs: str) -> str:
    m = re.search(r"""\btype\s*=\s*["']?\s*([^"'\s>]*)""", attrs, re.I)
    return m.group(1).strip().lower() if m else ""


def executable_js(html: str) -> list:
    """Every piece of JavaScript a document RUNS, as (where, code): the inline <script>
    bodies (classic + module; src=, JSON, template and unknown types skipped) and the
    inline on*="…" event handler attributes (HTML entities decoded)."""
    import html as html_mod
    out = []

    def take(m):
        attrs, body = m.group(1), m.group(2)
        if not re.search(r"\bsrc\s*=", attrs, re.I) and _script_type(attrs) in _JS_TYPES:
            out.append(("script", body))
        return " "
    rest = _SCRIPT_RE.sub(take, html)               # handlers are looked for OUTSIDE script bodies
    for tag in _TAG_RE.finditer(rest):
        for m in _HANDLER_ATTR_RE.finditer(tag.group(0)):
            code = m.group(2) if m.group(2) is not None else (m.group(3) or "")
            if code.strip():
                out.append((m.group(1).lower(), html_mod.unescape(code)))
    return out


def _match_close(toks, open_idx):
    """index of the ) matching the ( at open_idx (None when unbalanced)"""
    d = 0
    for k in range(open_idx, len(toks)):
        t = toks[k]
        if t.kind == "op":
            if t.val == "(":
                d += 1
            elif t.val == ")":
                d -= 1
                if d == 0:
                    return k
    return None


def dialog_calls_js(code: str) -> set:
    """The blocked native dialogs ONE piece of JavaScript calls globally (see above)."""
    try:
        toks = tokenize(code)
    except Exception:
        return set()
    n = len(toks)
    own = set()                          # names the code defines itself: function prompt(){} / const prompt = …
    for k in range(1, n):
        t, p = toks[k], toks[k - 1]
        if t.kind == "id" and t.val in _DIALOG_NAMES and p.kind == "id" and p.val in ("function", "const", "let", "var", "class"):
            own.add(t.val)
    found = set()
    for k, t in enumerate(toks):
        if t.kind == "id" and t.val in _DIALOG_NAMES and t.val not in own:
            nxt = toks[k + 1] if k + 1 < n else None
            if nxt is None or nxt.kind != "op" or nxt.val != "(":
                continue                                            # not a call (a variable, a parameter, .then(prompt))
            close = _match_close(toks, k + 1)
            after = toks[close + 1] if close is not None and close + 1 < n else None
            if after is not None and after.kind == "op" and after.val == "{":
                continue                                            # `prompt(q) {…}`: a method/function DEFINITION
            prev = toks[k - 1] if k >= 1 else None
            if prev is not None and prev.kind == "op" and prev.val in (".", "?."):
                obj = toks[k - 2] if k >= 2 else None
                if obj is None or obj.kind != "id" or obj.val not in _GLOBAL_OBJS:
                    continue                                        # ui.prompt(), this.confirm(): the app's own member
                before = toks[k - 3] if k >= 3 else None
                if before is not None and before.kind == "op" and before.val in (".", "?."):
                    continue                                        # x.window.prompt(): a member of something else
            found.add(t.val)
        elif t.kind == "str" and t.val in _DIALOG_NAMES and 2 <= k < n - 2:
            a, b, c, d = toks[k - 2], toks[k - 1], toks[k + 1], toks[k + 2]   # window["prompt"](
            if a.kind == "id" and a.val in _GLOBAL_OBJS and b.kind == "op" and b.val == "[" \
                    and c.kind == "op" and c.val == "]" and d.kind == "op" and d.val == "(":
                found.add(t.val)
    return found


def dialog_calls(html: str) -> list:
    """The blocked native dialogs a DOCUMENT calls in its executable code: a sorted subset
    of ["confirm", "prompt"]. Text, attributes (placeholders), comments and strings never
    count — see the note above executable_js()."""
    found = set()
    for _, code in executable_js(html):
        found |= dialog_calls_js(code)
    return sorted(found)
