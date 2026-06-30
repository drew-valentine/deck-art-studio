#!/usr/bin/env python3
"""Cross-module GPU work coordination for the MLX subprocess pipeline.

Both heavy paths — FLUX image generation (local_image_generator) and mlx-lm/
mlx-vlm inference (mlx_llm) — drive their own worker subprocess and EVICT the
other before spawning theirs (only one heavy model fits in 18 GB). Each module
also has its own internal lock. That created two defects:

  1. AB-BA deadlock: FLUX generate() held the image lock and called
     mlx_llm.unload() (wants the mlx lock); concurrently mlx _request() held the
     mlx lock and called local_image_generator.unload() (wants the image lock).
     Opposite acquisition orders wedged both threads permanently.
  2. A stuck worker's blocking stdout read held a lock forever, wedging the path.

`GPU_LOCK` is the single, REENTRANT, OUTERMOST lock both modules acquire before
their internal locks. One consistent order eliminates the AB-BA inversion, and
because it is reentrant the same thread's eviction chain (generate -> spawn ->
kill the other worker) re-enters instead of self-deadlocking, while a DIFFERENT
thread simply blocks at the gate (so analysis can't tear down a live generation
mid-request). Heavy GPU work is inherently mutually exclusive on this hardware,
so serializing it here is correct, not a bottleneck.

`InactivityWatchdog` converts a hung-but-alive worker (silent past a deadline)
into a kill -> EOF on the reader -> raised error -> lock released, so a single
stuck inference can never wedge the whole pipeline.

Only the stdlib `threading` is imported, so this stays importable on CI.
"""

import threading

# Reentrant so a single thread's eviction chain re-enters rather than deadlocks.
GPU_LOCK = threading.RLock()


class InactivityWatchdog:
    """Kill `proc` if it produces no output for `timeout` seconds.

    Call `kick()` on every line received to reset the timer (so long but
    actively-streaming work — FLUX emits a progress line per step — never trips),
    and `stop()` when the request completes. On expiry it kills the worker, which
    makes the parent's blocking readline() return '' (EOF) so the reader raises
    and releases GPU_LOCK instead of hanging forever.
    """

    def __init__(self, proc, timeout):
        self._proc = proc
        self._timeout = timeout
        self._lock = threading.Lock()
        self._timer = None
        self._done = False
        self.fired = False
        self.kick()

    def _fire(self):
        self.fired = True
        try:
            self._proc.kill()
        except Exception:
            pass

    def kick(self):
        with self._lock:
            if self._done:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._timeout, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def stop(self):
        with self._lock:
            self._done = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
