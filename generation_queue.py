"""Global, in-memory generation queue.

A single background worker drains a FIFO (with manual bump-to-top) queue of
generation jobs — art / prompt / flavor — that live ABOVE decks: each job
carries its own deck id and is executed against that deck regardless of which
deck the UI currently shows. This decouples the critique loop from GPU
throughput (enqueue is instant) and lets rendering continue across deck
switches.

This module owns only the mechanics (job model, ordered store, selection,
cancel/bump/clear, pause/resume, snapshot, the worker thread). Execution is
injected via ``start(execute_fn)`` so the module stays free of any heavy app
imports and is unit-testable on its own.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

# Job type constants
ART = 'art'
PROMPT = 'prompt'
FLAVOR = 'flavor'
ANALYZE = 'analyze'                # inspiration/style analysis + distillation
_VALID_TYPES = {ART, PROMPT, FLAVOR, ANALYZE}

# Status constants
QUEUED = 'queued'
RUNNING = 'running'
DONE = 'done'
FAILED = 'failed'
CANCELLED = 'cancelled'
_TERMINAL = {DONE, FAILED, CANCELLED}


@dataclass
class Job:
    type: str
    deck_id: str
    card_name: str
    deck_name: str = ''
    face: str = 'all'                 # art only: 'front'|'back'|'all'
    custom_prompt: Optional[str] = None   # art: explicit prompt override
    feedback: Optional[str] = None        # art/prompt: steer text
    use_ai: bool = True                   # prompt: AI-enhanced subject
    model_key: Optional[str] = None       # art: snapshotted model at enqueue
    label: str = ''                       # display label (card display name)
    params: dict = field(default_factory=dict)  # type-specific args (analyze: mode/filename/...)
    # runtime
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = QUEUED
    priority: int = 0                     # higher runs sooner
    seq: int = 0                          # monotonic FIFO tiebreaker (set on add)
    progress: dict = field(default_factory=dict)   # {step,total,message,pct}
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


class GenerationQueue:
    def __init__(self, persist_path=None, deck_exists=None):
        self._persist_path = persist_path
        self._deck_exists = deck_exists   # optional validator for restores
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._jobs: list[Job] = []        # order-agnostic; selection sorts
        self._seq = 0
        self._paused = False
        self._running_id: Optional[str] = None
        self._execute_fn: Optional[Callable[[Job], None]] = None
        self._worker: Optional[threading.Thread] = None
        self._stop = False
        # bounded history of finished jobs so the drawer's "Recent" doesn't grow
        self._history_cap = 40

    # -- persistence ------------------------------------------------------------
    # The queue survives server restarts: every mutation snapshots the live
    # (queued + running) jobs to persist_path, and start() restores them before
    # the worker launches — a running job at shutdown is re-queued at the front.
    # Learned the hard way: an in-memory-only queue turned a routine server
    # restart into silent loss of every queued job.
    def _persist_locked(self):
        if not self._persist_path:
            return
        import json, os, tempfile
        live = [j.to_dict() for j in self._jobs if j.status in (QUEUED, RUNNING)]
        try:
            fd, tmp = tempfile.mkstemp(dir=str(self._persist_path.parent),
                                       prefix='.queue_state_')
            with os.fdopen(fd, 'w') as f:
                json.dump({'paused': self._paused, 'jobs': live}, f, indent=2)
            os.replace(tmp, self._persist_path)
        except OSError as e:
            print(f"[queue] persist failed: {e}")

    def _restore_locked(self):
        if not self._persist_path:
            return
        import json
        try:
            with open(self._persist_path) as f:
                state = json.load(f)
        except (OSError, ValueError):
            return
        restored = 0
        for jd in state.get('jobs', []):
            try:
                if self._deck_exists and not self._deck_exists(jd.get('deck_id', '')):
                    continue          # deck deleted while the server was down
                was_running = jd.get('status') == RUNNING
                jd = {k: v for k, v in jd.items()
                      if k in Job.__dataclass_fields__}
                job = Job(**{**jd, 'status': QUEUED, 'progress': {},
                             'started_at': None, 'finished_at': None})
                if was_running:
                    # it was mid-flight at shutdown — run it first
                    job.priority = max(job.priority, 1_000_000)
                self._jobs.append(job)
                self._seq = max(self._seq, job.seq)
                restored += 1
            except (TypeError, ValueError) as e:
                print(f"[queue] skipped unrestorable job: {e}")
        self._paused = bool(state.get('paused', False))
        if restored:
            print(f"[queue] restored {restored} pending job(s) from disk")

    # -- lifecycle ------------------------------------------------------------
    def start(self, execute_fn: Callable[[Job], None]):
        """Register the executor and launch the single worker thread (idempotent)."""
        with self._lock:
            self._execute_fn = execute_fn
            if self._worker and self._worker.is_alive():
                return
            self._restore_locked()
            self._stop = False
            self._worker = threading.Thread(target=self._run, name='gen-queue',
                                            daemon=True)
            self._worker.start()

    def shutdown(self):
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    # -- enqueue --------------------------------------------------------------
    def enqueue(self, job: Job) -> Job:
        with self._cond:
            self._seq += 1
            job.seq = self._seq
            job.status = QUEUED
            self._jobs.append(job)
            self._persist_locked()
            self._cond.notify_all()
        return job

    # -- selection ------------------------------------------------------------
    def _next_locked(self) -> Optional[Job]:
        """Highest priority, then lowest seq (FIFO). Caller holds the lock."""
        pending = [j for j in self._jobs if j.status == QUEUED]
        if not pending:
            return None
        return min(pending, key=lambda j: (-j.priority, j.seq))

    # -- worker loop ----------------------------------------------------------
    def _run(self):
        while True:
            with self._cond:
                while not self._stop and (self._paused or self._next_locked() is None):
                    self._cond.wait(timeout=1.0)
                    if self._stop:
                        break
                if self._stop:
                    return
                job = self._next_locked()
                if job is None:
                    continue
                job.status = RUNNING
                job.started_at = time.time()
                self._running_id = job.id
                self._persist_locked()
                execute_fn = self._execute_fn
            # Execute OUTSIDE the lock so enqueue/cancel/snapshot stay responsive.
            try:
                if execute_fn is not None:
                    execute_fn(job)
                # A cooperative cancel may have flipped status to CANCELLED during
                # execution; don't override that.
                with self._lock:
                    if job.status == RUNNING:
                        job.status = DONE
            except Exception as e:  # noqa: BLE001 — worker must never die
                with self._lock:
                    if job.status == RUNNING:
                        job.status = FAILED
                        job.error = str(e)[:500]
            finally:
                with self._cond:
                    job.finished_at = time.time()
                    if self._running_id == job.id:
                        self._running_id = None
                    self._trim_history_locked()
                    self._persist_locked()
                    self._cond.notify_all()

    def _trim_history_locked(self):
        terminal = [j for j in self._jobs if j.status in _TERMINAL]
        if len(terminal) > self._history_cap:
            terminal.sort(key=lambda j: j.finished_at or 0)
            drop = set(id(j) for j in terminal[:len(terminal) - self._history_cap])
            self._jobs = [j for j in self._jobs if id(j) not in drop]

    # -- management -----------------------------------------------------------
    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return next((j for j in self._jobs if j.id == job_id), None)

    def cancel(self, job_id: str, running_cancel_hook: Optional[Callable[[Job], None]] = None) -> bool:
        """Cancel a job. Queued → instant. Running → cooperative: mark CANCELLED
        and invoke the hook (e.g. flag the card so the executor stops writing);
        the in-flight image still finishes but its result is discarded."""
        with self._cond:
            job = next((j for j in self._jobs if j.id == job_id), None)
            if not job or job.status in _TERMINAL:
                return False
            if job.status == QUEUED:
                job.status = CANCELLED
                job.finished_at = time.time()
                self._persist_locked()
                self._cond.notify_all()
                return True
            # running
            job.status = CANCELLED
            self._persist_locked()
            hook = running_cancel_hook
        if hook:
            hook(job)      # outside lock
        return True

    def cancel_deck(self, deck_id: str, running_cancel_hook=None) -> int:
        """Cancel all queued+running jobs for a deck (used on deck delete)."""
        n = 0
        for job in list(self._snapshot_jobs()):
            if job.deck_id == deck_id and job.status in (QUEUED, RUNNING):
                if self.cancel(job.id, running_cancel_hook):
                    n += 1
        return n

    def wait_for_deck_idle(self, deck_id: str, timeout: float = 120.0) -> bool:
        """Block until no RUNNING job belongs to ``deck_id`` (or timeout).

        Used on deck delete: a cooperative cancel only *flags* the in-flight
        job, so the worker may still be mid-render (and would recreate the deck
        directory it's writing into). Waiting for the worker to drain that job
        before ``rmtree`` avoids resurrecting a zombie deck folder. Returns True
        if the deck went idle, False on timeout."""
        deadline = time.time() + timeout
        with self._cond:
            while True:
                running = next((j for j in self._jobs
                                if j.id == self._running_id), None)
                if running is None or running.deck_id != deck_id:
                    return True
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=min(remaining, 1.0))

    def bump(self, job_id: str) -> bool:
        """Move a queued job to the front (above all other queued jobs)."""
        with self._cond:
            job = next((j for j in self._jobs if j.id == job_id), None)
            if not job or job.status != QUEUED:
                return False
            top = max((j.priority for j in self._jobs if j.status == QUEUED),
                      default=0)
            job.priority = top + 1
            self._persist_locked()
            self._cond.notify_all()
            return True

    def clear_completed(self) -> int:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if j.status not in _TERMINAL]
            return before - len(self._jobs)

    def set_paused(self, paused: bool):
        with self._cond:
            self._paused = bool(paused)
            self._persist_locked()
            self._cond.notify_all()

    # -- introspection --------------------------------------------------------
    def _snapshot_jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs)

    @property
    def is_generating(self) -> bool:
        with self._lock:
            return self._running_id is not None

    @property
    def paused(self) -> bool:
        return self._paused

    def running_job(self) -> Optional[Job]:
        with self._lock:
            if self._running_id is None:
                return None
            return next((j for j in self._jobs if j.id == self._running_id), None)

    def jobs_for_deck(self, deck_id: str, active_only=True) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs
                    if j.deck_id == deck_id
                    and (not active_only or j.status in (QUEUED, RUNNING))]

    def snapshot(self) -> dict:
        """Full queue state for the API/drawer, newest-relevant ordering."""
        with self._lock:
            running = [j for j in self._jobs if j.status == RUNNING]
            queued = sorted((j for j in self._jobs if j.status == QUEUED),
                            key=lambda j: (-j.priority, j.seq))
            recent = sorted((j for j in self._jobs if j.status in _TERMINAL),
                            key=lambda j: j.finished_at or 0, reverse=True)
            return {
                'paused': self._paused,
                'is_generating': self._running_id is not None,
                'counts': {
                    'running': len(running),
                    'queued': len(queued),
                    'done': sum(1 for j in recent if j.status == DONE),
                    'failed': sum(1 for j in recent if j.status == FAILED),
                    'cancelled': sum(1 for j in recent if j.status == CANCELLED),
                },
                'running': [j.to_dict() for j in running],
                'queued': [j.to_dict() for j in queued],
                'recent': [j.to_dict() for j in recent[:20]],
            }
