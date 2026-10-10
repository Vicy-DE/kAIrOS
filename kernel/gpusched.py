"""GPU scheduler: shared LLM holds next to exclusive holds, in FIFO order [parallel-2026-10-06].

ModelManager.gpu_lock used to be one asyncio.Lock: every LLM request, every image/video/music
job, every model swap took the whole GPU in turn, so two chat requests never reached
llama-server together even though it batches slots continuously (-np N). This object keeps the
exclusive hold (`async with MM.gpu_lock:` works exactly as before for sd/video/music/swaps/tier
and settings switches) and adds a SHARED hold for LLM requests on the loaded model:

    async with MM.gpu_lock.shared(key, slots):    # up to `slots` holders while the group is `key`
    async with MM.gpu_lock.exclusive():            # == async with MM.gpu_lock:

Rules (tests/test_llm_parallel.py proves each):
  (a) FIFO fairness — a request joins the running shared group only when no waiter is queued
      ahead of it, so a waiting image job or a request for another model never starves; the
      consecutive same-key requests at the head of the queue start together.
  (b) single-flight starts are ModelManager.ensure_llama's job (its own lock): concurrent
      holders of a group never start two servers and never send before /health is ready.
  (c) a group with another key — a swap — starts only after the running group has drained
      (shared holders == 0); an exclusive hold likewise.
  (d) cancellation-safe: a cancelled waiter leaves the queue and the next one is woken; a holder
      that errors or is cancelled releases its slot (async-with); a grant that lands in the same
      loop step as the cancel is given back.
  (e) the owner's `on_shared_drained` hook fires after EVERY shared release or handback that
      leaves 0 shared holders (after the dispatch it triggered) — ModelManager.state derives from
      the real holder count, never from a holder's own view of it [verify 2026-10-07: a holder
      that left while a granted waiter still counted, then the waiter cancelled before it ran,
      used to leave the state "llm" with nothing held].
No API shape, no threads: everything runs on the event loop, the release path never awaits.
"""
import asyncio
import collections
import contextlib


class _Waiter:
    __slots__ = ("kind", "key", "slots", "fut")

    def __init__(self, kind, key, slots, fut):
        self.kind, self.key, self.slots, self.fut = kind, key, slots, fut


class GpuScheduler:
    def __init__(self, on_shared_drained=None):
        self._queue = collections.deque()    # _Waiter in arrival order
        self._shared = 0                      # active shared holders (the LLM group)
        self._key = None                      # the group's key while _shared > 0
        self._group_slots = 1                 # the group's slot count, fixed by its first holder
        self._excl = False                    # an exclusive holder is active
        # rule (e): called with no arguments, synchronously, whenever a shared release or the
        # handback of an unused grant leaves 0 shared holders — exclusive holds never call it
        self.on_shared_drained = on_shared_drained

    # ---- introspection -------------------------------------------------------------
    @property
    def shared_holders(self) -> int:
        return self._shared

    @property
    def shared_key(self):
        return self._key if self._shared else None

    @property
    def waiting(self) -> int:
        return sum(1 for w in self._queue if not w.fut.done())

    def locked(self) -> bool:
        """asyncio.Lock's name: anything held at all."""
        return self._excl or self._shared > 0

    # ---- the holds -----------------------------------------------------------------
    @contextlib.asynccontextmanager
    async def shared(self, key, slots=1):
        """A shared hold on model `key`: granted while the running group is `key` with fewer than
        `slots` holders (or nothing runs), in FIFO order with everything else."""
        await self._acquire("shared", key, slots)
        try:
            yield self
        finally:
            self._release("shared")

    @contextlib.asynccontextmanager
    async def exclusive(self):
        await self._acquire("exclusive", None, 1)
        try:
            yield self
        finally:
            self._release("exclusive")

    async def __aenter__(self):               # `async with MM.gpu_lock:` = the exclusive hold
        await self._acquire("exclusive", None, 1)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._release("exclusive")
        return False

    # ---- mechanics -----------------------------------------------------------------
    def _can_start(self, w) -> bool:
        if self._excl:
            return False
        if w.kind == "exclusive":
            return self._shared == 0
        if self._shared == 0:
            return True
        return self._key == w.key and self._shared < self._group_slots

    def _grant(self, w):
        if w.kind == "exclusive":
            self._excl = True
        else:
            if self._shared == 0:
                self._key, self._group_slots = w.key, w.slots
            self._shared += 1

    def _dispatch(self):
        """Start the head of the queue while it can start — consecutive same-key shared
        requests join together; a cancelled waiter at the head is dropped."""
        while self._queue:
            w = self._queue[0]
            if w.fut.done():
                self._queue.popleft()
                continue
            if not self._can_start(w):
                break
            self._queue.popleft()
            self._grant(w)
            w.fut.set_result(None)

    def _release(self, kind):
        if kind == "exclusive":
            self._excl = False
        else:
            self._shared -= 1
            if self._shared <= 0:
                self._shared, self._key = 0, None
        self._dispatch()
        # (e) after the dispatch: a waiter it granted still counts (it is about to run), a grant it
        # could not place leaves the count at 0 — the same path serves the handback in _acquire
        if kind == "shared" and self._shared == 0 and self.on_shared_drained is not None:
            self.on_shared_drained()

    async def _acquire(self, kind, key, slots):
        w = _Waiter(kind, key, max(1, int(slots or 1)), asyncio.get_running_loop().create_future())
        if not self._queue and self._can_start(w):        # (a): nobody ahead
            self._grant(w)
            return
        self._queue.append(w)
        try:
            await w.fut
        except BaseException:
            if w.fut.done() and not w.fut.cancelled():
                self._release(kind)                        # granted in the same step as the cancel: hand it back
            else:
                try:
                    self._queue.remove(w)
                except ValueError:
                    pass
                self._dispatch()                           # the waiter behind it may start now
            raise
