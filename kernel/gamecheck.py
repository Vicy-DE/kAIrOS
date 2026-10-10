"""[R77] Quality gates of the game forge (docs/FORGE-POLISH-2026-10-07.md).

The language benchmark (docs/LANG-BENCH-2026-09.md, gemma4-26b × 6 languages) measured:
  - running the program and feeding the failure back did the repairing — 33–44 test rounds per
    language against 0–4 compiler/static rounds; a static gate alone adds little, but it is cheap
    and catches what a short run never reaches (a NameError in the game-over branch);
  - a valid program made invalid in a later round (Java abc399_d: first valid, final invalid) —
    the owner's rule: only the FINAL product counts, never hand over a regression;
  - small models resubmit the same broken program (Bonsai, Rust abc399_b: the same
    `error[E0308]: mismatched types` 10×, 9 unchanged resubmissions) — feedback must be specific
    and an identical resubmission must be pushed differently.

So a generated game passes, in this order, before main.py is ever written:
  1. static_problems(): compile() + pyflakes (the vendored kernel/vendor/pyflakes 4.0.2, MIT;
     error classes only — the set the benchmark gate used; unused imports etc. never block) + an
     import check (a module that is not installed: only the stdlib and pygame exist);
  2. play_test(): kernel/gamecheck_harness.py plays it headless for PLAYTEST_SECONDS with
     synthetic input (SDL dummy drivers, no window on the user's display, CPU only);
then the kernel launches it on the display (server.game_gate). Every failure goes back to the
game forge debugger with the error, the failing source line and — for a play-test crash — the
input that triggered it (fix_message()).
"""
import ast
import asyncio
import hashlib
import importlib.machinery
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

KERNEL_DIR = Path(__file__).resolve().parent
VENDOR_DIR = KERNEL_DIR / "vendor"
HARNESS = KERNEL_DIR / "gamecheck_harness.py"

PLAYTEST_SECONDS = 7.0       # wall time of synthetic play (the brief: ~6–8 s; the QUIT comes at the end)
PLAYTEST_GRACE = 3.0         # + interpreter/pygame start-up and the exit after QUIT, then the group is killed
STDERR_TAIL = 3000

# pyflakes message classes that are errors (the program breaks when the line runs) — the exact set
# of the language benchmark's Python gate (lang-bench-2026-09 sandbox/runner.py PYFLAKES_ERRORS);
# everything else (unused import/variable, redefinition, star-import "may be undefined", f-string
# without placeholders …) is a warning and never blocks a game.
PYFLAKES_ERRORS = {
    "UndefinedName", "UndefinedLocal", "UndefinedExport", "DuplicateArgument", "ReturnOutsideFunction",
    "YieldOutsideFunction", "ContinueOutsideLoop", "BreakOutsideLoop", "DefaultExceptNotLast",
    "TwoStarredExpressions", "TooManyExpressionsInStarredAssignment", "ForwardAnnotationSyntaxError",
    "InvalidPrintSyntax", "RaiseNotImplemented", "PercentFormatInvalidFormat", "PercentFormatMixedPositionalAndNamed",
    "PercentFormatUnsupportedFormatCharacter", "PercentFormatPositionalCountMismatch", "PercentFormatExpectedMapping",
    "PercentFormatExpectedSequence", "PercentFormatStarRequiresSequence", "PercentFormatMissingArgument",
    "StringDotFormatInvalidFormat", "StringDotFormatMissingArgument", "StringDotFormatMixingAutomatic",
}


def _pyflakes():
    """the vendored pyflakes (kernel/vendor/pyflakes, 4.0.2) — the image has no python3-pyflakes"""
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    from pyflakes import checker
    return checker


def source_line(code: str, line) -> str:
    try:
        return code.splitlines()[int(line) - 1].strip()[:200]
    except (TypeError, ValueError, IndexError):
        return ""


def _module_available(name: str) -> bool:
    top = name.split(".")[0]
    if not top or top in sys.builtin_module_names or top == "__future__":
        return True
    # the game's interpreter path, without the kernel's own directories (a game never sees wm.py)
    path = [p for p in sys.path if p and Path(p).resolve() not in (KERNEL_DIR, VENDOR_DIR)]
    try:
        return importlib.machinery.PathFinder.find_spec(top, path) is not None
    except (ImportError, ValueError):
        return False


def static_problems(code: str) -> list:
    """-> ["line N: <message>\\n    <source line>", …]; empty = the game may be played.
    Blocks only real errors: a SyntaxError, a pyflakes error class, an import of a module that is
    not installed (outside a try: — `try: import numpy except ImportError` is the game's business)."""
    try:
        tree = compile(code, "main.py", "exec", ast.PyCF_ONLY_AST)
        compile(tree, "main.py", "exec")             # the second pass reports what the AST pass accepts
    except SyntaxError as se:
        return [f"line {se.lineno}: SyntaxError: {se.msg}" + (f"\n    {(se.text or '').strip()[:200]}" if se.text else "")]
    except (ValueError, TypeError) as e:
        return [f"the source cannot be compiled: {e}"]
    out = []
    try:
        checker = _pyflakes()
        w = checker.Checker(tree, filename="main.py")
        for m in sorted(w.messages, key=lambda m: getattr(m, "lineno", 0)):
            if type(m).__name__ in PYFLAKES_ERRORS:
                txt = m.message % m.message_args
                out.append(f"line {m.lineno}: {txt}" + (f"\n    {source_line(code, m.lineno)}" if source_line(code, m.lineno) else ""))
    except Exception as e:                  # never expected — the gate must not block on its own bug
        print(f"[kernel] WARN game static gate: pyflakes failed: {type(e).__name__}: {e}", flush=True)
    tried = set()

    def visit(node, in_try):
        for child in ast.iter_child_nodes(node):
            guarded = in_try or isinstance(child, ast.Try) or type(child).__name__ == "TryStar"
            if isinstance(child, (ast.Import, ast.ImportFrom)) and not in_try:
                names = ([a.name for a in child.names] if isinstance(child, ast.Import)
                         else ([child.module] if child.module and not child.level else []))
                for n in names:
                    if n.split(".")[0] not in tried and not _module_available(n):
                        tried.add(n.split(".")[0])
                        out.append(f"line {child.lineno}: import of a module that is not installed: '{n.split('.')[0]}' "
                                   f"(only the Python standard library and pygame exist on this OS)"
                                   f"\n    {source_line(code, child.lineno)}")
            visit(child, guarded)
    visit(tree, False)
    return out[:8]


def code_key(code: str) -> str:
    """identity of a submission: trailing whitespace and blank lines do not make code different"""
    norm = "\n".join(ln.rstrip() for ln in code.strip().splitlines() if ln.strip())
    return hashlib.sha256(norm.encode("utf-8", "ignore")).hexdigest()


def error_signature(kind: str, text: str) -> str:
    """the 'same error again' key: the kind + the last exception line (or first static message),
    with numbers that only move with the source (line numbers, addresses) taken out"""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if kind == "static":
        head = lines[0] if lines else ""
    else:
        exc = [ln for ln in lines if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b|^pygame\.error", ln)]
        head = exc[-1] if exc else (lines[-1] if lines else "")
    return kind + ":" + re.sub(r"\b(line \d+|0x[0-9a-f]+)\b", "", head)[:240]


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def play_test(code: str, *, seconds: float = None, seed: int = 0, popen: dict = None,
                    python: str = "python3") -> dict:
    """Play the game headless. -> {"ok", "verdict", "summary", "detail", "traceback", "input",
    "after_quit", "line", "seconds", "stderr"}; verdict: passed | exception | exit | timeout (ignored
    QUIT — passes) | no-pygame (skipped — passes) | error (the harness itself failed — passes, logged).
    `popen` = {"user", "group"} to run it as the kiosk user (game_env's), never as root."""
    seconds = PLAYTEST_SECONDS if seconds is None else seconds
    t0 = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="kairos-playtest-"))
    try:
        (tmp / "main.py").write_text(code)
        report = tmp / "report.json"
        if popen and popen.get("user") is not None:
            for p in (tmp, tmp / "main.py"):
                try:
                    os.chown(p, popen["user"], popen.get("group", -1))
                except OSError:
                    pass
        env = {k: v for k, v in os.environ.items()
               if k in ("PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "PYTHONPATH", "TZ")}
        env.update(HOME=str(tmp), SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
                   PYGAME_HIDE_SUPPORT_PROMPT="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        proc = await asyncio.create_subprocess_exec(
            python, str(HARNESS), str(tmp / "main.py"), str(report), str(seconds), str(seed),
            cwd=str(tmp), env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, **(popen or {}))
        tail = [""]

        async def drain():
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    return
                tail[0] = (tail[0] + chunk.decode("utf-8", "ignore"))[-STDERR_TAIL:]
        reader = asyncio.ensure_future(drain())
        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), seconds + PLAYTEST_GRACE)
        except asyncio.TimeoutError:
            timed_out = True
            _kill_group(proc)
            await proc.wait()
        _kill_group(proc)                    # anything the game spawned
        try:
            await asyncio.wait_for(reader, 1.0)
        except asyncio.TimeoutError:
            reader.cancel()
        try:
            rep = json.loads(report.read_text())
        except (OSError, ValueError):
            rep = {}
        return verdict_of(rep, proc.returncode, tail[0], timed_out, round(time.monotonic() - t0, 1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verdict_of(rep: dict, returncode, stderr: str, timed_out: bool, elapsed: float) -> dict:
    """the harness report (+ exit status, stderr tail) -> the play-test verdict (pure: unit-tested)"""
    st = rep.get("status")
    base = {"seconds": elapsed, "stderr": (stderr or "")[-STDERR_TAIL:], "input": rep.get("recent_input") or [],
            "after_quit": bool(rep.get("after_quit")), "line": rep.get("line"), "traceback": rep.get("traceback") or "",
            "input_events": rep.get("input_events", 0)}
    if st == "exception":
        when = ("after the window was closed (the QUIT event) — the game kept running into pygame calls"
                if rep.get("after_quit") else f"{rep.get('since_window')} s after its window opened")
        return dict(base, ok=False, verdict="exception",
                    summary=f"{rep.get('type')}: {rep.get('message')}"[:300],
                    detail=f"the game crashed {when}")
    if st == "exit" and rep.get("code", 0) != 0:
        lines = [ln for ln in (stderr or "").strip().splitlines() if ln.strip()]
        return dict(base, ok=False, verdict="exit",
                    summary=(rep.get("message") or (lines[-1] if lines else f"exit status {rep.get('code')}"))[:300],
                    detail=f"the game ended itself with exit status {rep.get('code')} during play")
    if st in ("exit", "returned") and not rep.get("window"):
        return dict(base, ok=False, verdict="no-window",
                    summary=f"the program ended after {rep.get('t')} s without opening a game window",
                    detail="pygame.display.set_mode() was never reached — the game loop must open its window and run until "
                           "the window is closed")
    if st in ("exit", "returned"):
        return dict(base, ok=True, verdict="passed", summary="played without an error", detail="")
    if st == "no-pygame":
        return dict(base, ok=True, verdict="no-pygame", summary="pygame is not installed — the play-test was skipped",
                    detail=rep.get("error", ""))
    if timed_out and st in ("running", "starting"):
        return dict(base, ok=True, verdict="timeout",
                    summary="played without an error (it did not end on the QUIT event; ended by the kernel)", detail="")
    # the harness itself died before it could report (a fatal signal in SDL, an OOM kill): a game
    # killed by a signal is a crash; anything else is the harness's problem and never blocks
    if returncode is not None and returncode < 0:
        try:
            sig = signal.Signals(-returncode).name
        except ValueError:
            sig = f"signal {-returncode}"
        return dict(base, ok=False, verdict="signal", summary=f"the game process was killed by {sig}",
                    detail="it died without a Python traceback (a crash inside SDL/pygame or out of memory)")
    return dict(base, ok=True, verdict="error",
                summary=f"the play-test harness could not judge the game (status {st!r}, exit {returncode})", detail="")


def failure_text(kind: str, info) -> str:
    """the ERROR / TRACEBACK block the debugger gets for one failed check"""
    if kind == "static":
        return "\n".join(f"- {p}" for p in info)
    if kind == "playtest":
        parts = [f"{info['detail']}." if info.get("detail") else "",
                 (info.get("traceback") or info.get("stderr") or info.get("summary") or "").strip()[-2500:]]
        if info.get("input"):
            parts.append("THE INPUT THAT TRIGGERED IT (the synthetic play-test, last events before the crash):\n"
                         + "\n".join(f"  {e}" for e in info["input"]))
        return "\n\n".join(p for p in parts if p)
    return str(info or "").strip()[-3000:]


def fix_message(code: str, kind: str, text: str, line=None, repeat: str = None) -> str:
    """the debugger's user message: the source, what failed (specific: the failing line's code), and
    — when the model sent the same program again or the same error came back — a stronger order"""
    intro = {"static": "The game was checked before it was run (compile + pyflakes + imports) and has errors — "
                       "fix every listed line.",
             "playtest": "The game was played headless with synthetic keyboard and mouse input (a play-test) and failed.",
             "display": "The game was launched on the desktop and crashed."}.get(kind, "The game failed.")
    msg = f"SOURCE:\n{code}\n\n{intro}\n{'ERROR' if kind == 'static' else 'TRACEBACK'}:\n{text}"
    if line and source_line(code, line):
        msg += f"\n\nTHE FAILING LINE {line}:\n    {source_line(code, line)}"
    if repeat == "identical":
        msg += ("\n\nIMPORTANT: your previous answer was IDENTICAL to the broken source above — it did not change "
                "anything, so it fails exactly the same way. Do NOT send this source again. Find the cause of the "
                "error above and CHANGE the code that causes it (rewrite that function or block if needed), then "
                "output the full corrected program.")
    elif repeat == "same-error":
        msg += ("\n\nIMPORTANT: this is the SAME error as after your previous fix — that fix did not address the "
                "cause. Do not patch around it the same way again: re-read the failing line and everything it "
                "depends on (names, types, the order things are created in) and fix the root cause.")
    return msg
