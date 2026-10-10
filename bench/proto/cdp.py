#!/usr/bin/env python3
"""Minimal Chrome DevTools Protocol client over --remote-debugging-pipe (fds 3/4,
NUL-separated JSON) — stdlib only, no websocket dependency. Used by the baseline
driver for the shell's own functions (Runtime.evaluate) and by every candidate for
DOM evidence. One browser process, flat sessions (Target.attachToTarget flatten)."""
import json
import os
import subprocess
import threading
import time


class CDPError(Exception):
    pass


class Browser:
    def __init__(self, cmd, env=None, log=None):
        r_in, w_in = os.pipe()      # we write w_in  -> chromium reads fd 3
        r_out, w_out = os.pipe()    # chromium writes fd 4 -> we read r_out

        def preexec():
            a, b = os.dup(r_in), os.dup(w_out)
            os.dup2(a, 3)
            os.dup2(b, 4)
        self.proc = subprocess.Popen(cmd + ["--remote-debugging-pipe"], preexec_fn=preexec,
                                     pass_fds=(r_in, w_out, 3, 4), stdout=log, stderr=log, env=env)
        os.close(r_in)
        os.close(w_out)
        self._w = os.fdopen(w_in, "wb", buffering=0)
        self._rfd = r_out
        self._pending = {}
        self._seq = 0
        self._lock = threading.Lock()
        self.events = []            # (session, method, params) — kept short
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buf = b""
        while True:
            try:
                chunk = os.read(self._rfd, 1 << 16)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\0" in buf:
                raw, buf = buf.split(b"\0", 1)
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if "id" in msg:
                    p = self._pending.get(msg["id"])
                    if p:
                        p[1] = msg
                        p[0].set()
                else:
                    if len(self.events) < 2000:
                        self.events.append((msg.get("sessionId"), msg.get("method"), msg.get("params")))

    def call(self, method, params=None, session=None, timeout=30):
        with self._lock:
            self._seq += 1
            mid = self._seq
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        ev = threading.Event()
        self._pending[mid] = [ev, None]
        self._w.write(json.dumps(msg).encode() + b"\0")
        if not ev.wait(timeout):
            self._pending.pop(mid, None)
            raise CDPError(f"timeout: {method}")
        reply = self._pending.pop(mid)[1]
        if "error" in reply:
            raise CDPError(f"{method}: {reply['error']}")
        return reply.get("result", {})

    def targets(self):
        return self.call("Target.getTargets")["targetInfos"]

    def attach(self, url_part, timeout=30):
        """attach to the first page target whose url contains url_part -> sessionId"""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            for t in self.targets():
                if t.get("type") == "page" and url_part in t.get("url", ""):
                    sid = self.call("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})["sessionId"]
                    self.call("Runtime.enable", session=sid)
                    return sid
            time.sleep(0.05)
        raise CDPError(f"no page target matching {url_part!r}")

    def evaluate(self, session, expression, await_promise=True, timeout=30):
        r = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": await_promise,
                                           "returnByValue": True}, session=session, timeout=timeout)
        if "exceptionDetails" in r:
            ex = r["exceptionDetails"]
            raise CDPError(ex.get("exception", {}).get("description") or ex.get("text"))
        return r["result"].get("value")

    def metrics(self, session):
        self.call("Performance.enable", session=session)
        return {m["name"]: m["value"] for m in self.call("Performance.getMetrics", session=session)["metrics"]}

    def close(self):
        try:
            self.call("Browser.close", timeout=5)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
