#!/usr/bin/env python3
"""[R77] The game forge's headless play-test — runs ONE generated pygame game for a few seconds
with synthetic input, without a window on the user's display (docs/FORGE-POLISH-2026-10-07.md).

    python3 gamecheck_harness.py <dir>/main.py <report.json> <seconds> <seed>

The language benchmark (docs/LANG-BENCH-2026-09.md) measured that running a program and feeding
the failure back is what turns first attempts into valid programs; a game's equivalent of "run
the tests" is playing it. kernel/gamecheck.py starts this file in a subprocess with
SDL_VIDEODRIVER=dummy / SDL_AUDIODRIVER=dummy and no DISPLAY, in a throwaway directory (the
game's saves stay there). Here:

- pygame.event.get/poll/wait/peek/pump are wrapped: before the game reads its queue, the input
  that is due by now (wall time since the game opened its window) is POSTED into pygame's real
  queue — the game's own filters (event.get(QUIT), event.wait()) keep pygame's semantics;
  event.wait() polls instead of blocking forever on the dummy driver;
- pygame.key.get_pressed / pygame.mouse.get_pos / get_pressed report the synthetic held keys and
  pointer (the dummy driver has no keyboard state; most games steer with get_pressed);
- pygame.display.set_mode drops flags the dummy driver cannot honour (OPENGL) and retries plain
  on a pygame.error; toggle_fullscreen is a no-op;
- the input: SPACE/ENTER/clicks (menus), held arrows + WASD, mouse sweeps + clicks, R/P/digits
  and a seeded random sequence, then a QUIT event (the window's close button) at the deadline.
  ESC is never pressed (it quits by contract).
- the verdict goes to <report.json>: "exception" (+ the traceback with the harness frames
  removed, the input of the last 1.5 s, whether it came after the QUIT), "exit" (sys.exit
  code), "returned", "no-pygame". The parent kills the process group when it hangs (a game
  that ignores QUIT is not a crash — the desktop's close escalation ends it)."""
import json
import os
import random
import runpy
import sys
import time
import traceback

HARNESS = os.path.abspath(__file__)


def main():
    game, report_path, seconds, seed = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
    game = os.path.abspath(game)
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    os.environ.pop("DISPLAY", None)
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    t_start = time.monotonic()
    rep = {"status": "starting", "seconds": seconds, "seed": seed}

    drv = None

    def write(**kw):
        rep.update(kw)
        rep["t"] = round(time.monotonic() - t_start, 2)
        rep["window"] = bool(drv and drv.t_window is not None)     # did it ever open its window?
        tmp = report_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rep, f)
        os.replace(tmp, report_path)

    try:
        import pygame
    except Exception as e:                  # no pygame here: nothing can be tested (not the game's fault)
        write(status="no-pygame", error=f"{type(e).__name__}: {e}")
        os._exit(0)
    drv = Driver(pygame, t_start, seconds, seed)
    drv.install()
    write(status="running")
    # the game runs as `python3 main.py` would: its own directory first on sys.path, argv[0]
    sys.path[0] = os.path.dirname(game)
    sys.argv = [game]
    try:
        runpy.run_path(game, run_name="__main__")
        write(status="returned", after_quit=drv.quit_sent, input_events=drv.n_sent)
        code = 0
    except SystemExit as e:
        c = e.code
        code = c if isinstance(c, int) else (0 if c is None else 1)
        if c is not None and not isinstance(c, int):
            sys.stderr.write(f"{c}\n")
        write(status="exit", code=code, message=None if isinstance(c, int) or c is None else str(c)[:300],
              after_quit=drv.quit_sent, input_events=drv.n_sent)
    except BaseException as e:              # noqa: BLE001 — every crash is the verdict
        tb = game_traceback(e, game)
        sys.stderr.write(tb)
        write(status="exception", type=type(e).__name__, message=str(e)[:500], traceback=tb[-4000:],
              line=last_game_line(e, game), after_quit=drv.quit_sent, input_events=drv.n_sent,
              recent_input=drv.recent(1.5), held=drv.held_names(), since_window=drv.since_window())
        code = 3
    sys.stderr.flush()
    os._exit(code)


def game_traceback(exc, game):
    """The traceback a plain `python3 main.py` would print: the harness and runpy frames are
    removed, the temp path is shown as main.py."""
    te = traceback.TracebackException.from_exception(exc)
    te.stack = traceback.StackSummary.from_list(
        [f for f in te.stack if f.filename != HARNESS and "runpy" not in os.path.basename(f.filename)])
    return "".join(te.format()).replace(game, "main.py")


def last_game_line(exc, game):
    tb, line = exc.__traceback__, None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == game:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


class Pressed:
    """pygame.key.get_pressed()'s answer: indexable by K_* constants (also the large SDL2 ones)"""

    def __init__(self, held, real=None):
        self.held, self.real = frozenset(held), real

    def __getitem__(self, k):
        if isinstance(k, int) and k in self.held:
            return True
        try:
            return bool(self.real[k]) if self.real is not None else False
        except Exception:
            return False

    def __len__(self):
        return 512

    def __iter__(self):
        return (self[i] for i in range(512))


class Driver:
    def __init__(self, pygame, t_start, seconds, seed):
        self.pg, self.t_start, self.seconds = pygame, t_start, seconds
        self.rng = random.Random(seed)
        self.t_window = None        # monotonic time of the first set_mode: the input clock starts there
        self.size = (960, 540)
        self.held, self.buttons, self.pos = set(), set(), (480, 270)
        self.schedule, self.next_i = [], 0
        self.log = []               # (t since window, text) of every injected event
        self.n_sent, self.quit_sent = 0, False

    # ---- the input plan ----------------------------------------------------------------------
    def build(self):
        pg, rng = self.pg, self.rng
        w, h = self.size
        ev = []                      # (t, kind, a, b)

        def tap(t, key, dur=0.08):
            ev.append((t, "down", key, None))
            ev.append((t + dur, "up", key, None))

        def click(t, x, y, button=1):
            ev.append((t, "move", (x, y), None))
            ev.append((t + 0.02, "mdown", (x, y), button))
            ev.append((t + 0.10, "mup", (x, y), button))
        # menus / "press SPACE to start"
        tap(0.30, pg.K_SPACE)
        tap(0.55, pg.K_RETURN)
        click(0.75, w // 2, h // 2)
        # held steering keys — arrows, then WASD — with taps of the fire button in between
        t = 1.0
        for key in (pg.K_RIGHT, pg.K_UP, pg.K_LEFT, pg.K_DOWN):
            tap(t, key, 0.35)
            tap(t + 0.15, pg.K_SPACE)
            t += 0.42
        for key in (pg.K_d, pg.K_w, pg.K_a, pg.K_s):
            tap(t, key, 0.25)
            t += 0.3
        # the pointer sweeps the window and clicks (menu buttons, aiming games)
        for i in range(6):
            x, y = int(w * (0.1 + 0.16 * i)), int(h * (0.2 + 0.12 * (i % 5)))
            ev.append((t, "move", (x, y), None))
            if i % 2 == 0:
                click(t + 0.05, x, y, 1 if i % 4 == 0 else 3)
            t += 0.12
        # restart / pause / level keys, then a seeded random sequence
        for key in (pg.K_r, pg.K_p, pg.K_p, pg.K_1, pg.K_TAB):
            tap(t, key)
            t += 0.12
        pool = [pg.K_LEFT, pg.K_RIGHT, pg.K_UP, pg.K_DOWN, pg.K_SPACE, pg.K_RETURN, pg.K_w, pg.K_a,
                pg.K_s, pg.K_d, pg.K_r, pg.K_LSHIFT, pg.K_x, pg.K_z, pg.K_2, pg.K_3, pg.K_e, pg.K_q, pg.K_f]
        while t < self.seconds - 0.6:
            if rng.random() < 0.2:
                click(t, rng.randrange(w), rng.randrange(h))
            else:
                tap(t, rng.choice(pool), rng.choice((0.05, 0.1, 0.3)))
            t += rng.choice((0.06, 0.1, 0.15, 0.25))
        self.schedule = sorted(ev, key=lambda e: e[0])

    def now(self):
        return time.monotonic() - self.t_window if self.t_window is not None else 0.0

    def since_window(self):
        return round(self.now(), 2) if self.t_window is not None else None

    def deadline_passed(self):
        return time.monotonic() - self.t_start >= self.seconds

    def key_name(self, key):
        try:
            return self.pg.key.name(key) or str(key)
        except Exception:
            return str(key)

    def held_names(self):
        return [self.key_name(k) for k in sorted(self.held)]

    def recent(self, window):
        now = self.now()
        return [f"t={t:.2f}s {txt}" for t, txt in self.log if now - t <= window][-12:]

    def post(self, etype, **attrs):
        self._orig_post(self.pg.event.Event(etype, **attrs))
        self.n_sent += 1

    def feed(self):
        """post every scheduled event that is due; the QUIT once the deadline passed"""
        pg = self.pg
        if self.t_window is None:
            try:
                if not pg.display.get_init() or pg.display.get_surface() is None:
                    return
            except Exception:
                return
            self.t_window = time.monotonic()
        try:
            if self.deadline_passed():
                if not self.quit_sent:
                    self.quit_sent = True
                    self.log.append((self.now(), "QUIT (window close)"))
                    self.post(pg.QUIT)
                return
            now = self.now()
            while self.next_i < len(self.schedule) and self.schedule[self.next_i][0] <= now:
                t, kind, a, b = self.schedule[self.next_i]
                self.next_i += 1
                if kind in ("down", "up"):
                    name = self.key_name(a)
                    uni = name if len(name) == 1 else (" " if a == pg.K_SPACE else ("\r" if a == pg.K_RETURN else ""))
                    if kind == "down":
                        self.held.add(a)
                    else:
                        self.held.discard(a)
                    self.post(pg.KEYDOWN if kind == "down" else pg.KEYUP, key=a, mod=0, unicode=uni,
                              scancode=0, window=None)
                    self.log.append((t, f"{'KEYDOWN' if kind == 'down' else 'KEYUP'} {name}"))
                elif kind == "move":
                    rel = (a[0] - self.pos[0], a[1] - self.pos[1])
                    self.pos = a
                    self.post(pg.MOUSEMOTION, pos=a, rel=rel, buttons=tuple(int(i in self.buttons) for i in (1, 2, 3)),
                              touch=False, window=None)
                    self.log.append((t, f"MOUSEMOTION {a}"))
                elif kind in ("mdown", "mup"):
                    (self.buttons.add if kind == "mdown" else self.buttons.discard)(b)
                    self.pos = a
                    self.post(pg.MOUSEBUTTONDOWN if kind == "mdown" else pg.MOUSEBUTTONUP, pos=a, button=b,
                              touch=False, window=None)
                    self.log.append((t, f"{'MOUSEBUTTONDOWN' if kind == 'mdown' else 'MOUSEBUTTONUP'} button {b} at {a}"))
        except pg.error:
            pass                     # the queue is not there (display quit): the game's own calls say so

    # ---- the patches ---------------------------------------------------------------------------
    def install(self):
        pg, drv = self.pg, self
        ev, disp, key, mouse = pg.event, pg.display, pg.key, pg.mouse
        self._orig_post = ev.post
        o_get, o_poll, o_peek, o_pump = ev.get, ev.poll, ev.peek, ev.pump
        o_set_mode, o_pressed = disp.set_mode, key.get_pressed

        def get(*a, **k):
            drv.feed()
            return o_get(*a, **k)

        def poll():
            drv.feed()
            return o_poll()

        def peek(*a, **k):
            drv.feed()
            return o_peek(*a, **k)

        def pump(*a, **k):
            drv.feed()
            return o_pump(*a, **k)

        def wait(timeout=0):
            t0 = time.monotonic()
            while True:
                drv.feed()
                e = o_poll()
                if e.type != pg.NOEVENT:
                    return e
                if timeout and (time.monotonic() - t0) * 1000 >= timeout:
                    return e
                time.sleep(0.005)

        def set_mode(size=(0, 0), flags=0, *a, **k):
            clean = flags & ~getattr(pg, "OPENGL", 0)
            try:
                surf = o_set_mode(size, clean, *a, **k)
            except pg.error:
                try:
                    surf = o_set_mode(size)
                except pg.error:
                    surf = o_set_mode((960, 540))
            try:
                drv.size = surf.get_size()
            except Exception:
                pass
            if not drv.schedule:
                drv.build()
            return surf

        def get_pressed(*a, **k):
            drv.feed()
            try:
                real = o_pressed(*a, **k)
            except Exception:
                real = None
            return Pressed(drv.held, real)

        def mouse_pressed(num_buttons=3):
            return tuple(i in drv.buttons for i in range(1, num_buttons + 1))

        ev.get, ev.poll, ev.peek, ev.pump, ev.wait = get, poll, peek, pump, wait
        disp.set_mode, key.get_pressed = set_mode, get_pressed
        disp.toggle_fullscreen = lambda *a, **k: 0
        mouse.get_pos = lambda: drv.pos
        mouse.get_pressed = mouse_pressed


if __name__ == "__main__":
    main()
