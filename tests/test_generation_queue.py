"""Unit tests for generation_queue.py — pure mechanics, no app import."""

import threading
import time

import pytest

from generation_queue import (
    GenerationQueue, Job, ART, PROMPT, FLAVOR,
    QUEUED, RUNNING, DONE, FAILED, CANCELLED,
)


def _job(deck='d1', card='Sol Ring', jtype=ART, **kw):
    return Job(type=jtype, deck_id=deck, card_name=card, **kw)


class TestOrdering:
    def test_fifo_selection(self):
        q = GenerationQueue()
        a, b, c = _job(card='A'), _job(card='B'), _job(card='C')
        q.enqueue(a); q.enqueue(b); q.enqueue(c)
        # _next_locked picks lowest seq (FIFO) at equal priority
        with q._lock:
            assert q._next_locked().card_name == 'A'

    def test_bump_moves_to_front(self):
        q = GenerationQueue()
        a, b, c = _job(card='A'), _job(card='B'), _job(card='C')
        for j in (a, b, c):
            q.enqueue(j)
        assert q.bump(c.id) is True
        with q._lock:
            assert q._next_locked().card_name == 'C'

    def test_bump_only_queued(self):
        q = GenerationQueue()
        a = _job()
        q.enqueue(a)
        a.status = RUNNING
        assert q.bump(a.id) is False


class TestCancel:
    def test_cancel_queued_instant(self):
        q = GenerationQueue()
        a = _job()
        q.enqueue(a)
        assert q.cancel(a.id) is True
        assert a.status == CANCELLED
        with q._lock:
            assert q._next_locked() is None  # no longer selectable

    def test_cancel_running_calls_hook_and_marks(self):
        q = GenerationQueue()
        a = _job()
        q.enqueue(a)
        a.status = RUNNING
        called = []
        assert q.cancel(a.id, running_cancel_hook=lambda j: called.append(j.id)) is True
        assert a.status == CANCELLED
        assert called == [a.id]

    def test_cancel_terminal_noop(self):
        q = GenerationQueue()
        a = _job()
        q.enqueue(a)
        a.status = DONE
        assert q.cancel(a.id) is False

    def test_cancel_deck_cancels_only_that_deck(self):
        q = GenerationQueue()
        d1a, d1b, d2 = _job(deck='d1', card='A'), _job(deck='d1', card='B'), _job(deck='d2', card='C')
        for j in (d1a, d1b, d2):
            q.enqueue(j)
        n = q.cancel_deck('d1')
        assert n == 2
        assert d1a.status == CANCELLED and d1b.status == CANCELLED
        assert d2.status == QUEUED


class TestClearPause:
    def test_clear_completed(self):
        q = GenerationQueue()
        a, b, c = _job(card='A'), _job(card='B'), _job(card='C')
        for j in (a, b, c):
            q.enqueue(j)
        a.status = DONE
        b.status = FAILED
        removed = q.clear_completed()
        assert removed == 2
        assert [j.card_name for j in q._snapshot_jobs()] == ['C']

    def test_pause_blocks_selection_in_worker(self):
        q = GenerationQueue()
        ran = []
        q.set_paused(True)
        q.start(lambda job: ran.append(job.id))
        q.enqueue(_job())
        time.sleep(0.3)
        assert ran == []          # paused → not executed
        q.set_paused(False)
        time.sleep(0.3)
        assert len(ran) == 1


class TestWorkerExecution:
    def test_jobs_run_in_order_and_complete(self):
        q = GenerationQueue()
        order = []
        gate = threading.Event()

        def execute(job):
            order.append(job.card_name)

        q.start(execute)
        for name in ('A', 'B', 'C'):
            q.enqueue(_job(card=name))
        # wait for drain
        for _ in range(50):
            if all(j.status == DONE for j in q._snapshot_jobs()):
                break
            time.sleep(0.05)
        assert order == ['A', 'B', 'C']
        assert all(j.status == DONE for j in q._snapshot_jobs())

    def test_failure_marks_failed_not_kills_worker(self):
        q = GenerationQueue()

        def execute(job):
            if job.card_name == 'boom':
                raise RuntimeError('kaboom')

        q.start(execute)
        bad = _job(card='boom')
        q.enqueue(bad)
        good = _job(card='ok')
        q.enqueue(good)
        for _ in range(50):
            if good.status == DONE:
                break
            time.sleep(0.05)
        assert bad.status == FAILED
        assert 'kaboom' in (bad.error or '')
        assert good.status == DONE   # worker survived the failure

    def test_running_cancel_not_overridden_to_done(self):
        q = GenerationQueue()
        started = threading.Event()
        release = threading.Event()

        def execute(job):
            started.set()
            release.wait(timeout=2)

        q.start(execute)
        j = _job()
        q.enqueue(j)
        assert started.wait(timeout=2)
        q.cancel(j.id)               # cooperative cancel while running
        release.set()
        for _ in range(50):
            if j.finished_at:
                break
            time.sleep(0.05)
        assert j.status == CANCELLED  # not flipped to DONE


class TestSnapshot:
    def test_snapshot_shape(self):
        q = GenerationQueue()
        a, b, c = _job(card='A'), _job(card='B', jtype=PROMPT), _job(card='C')
        for j in (a, b, c):
            q.enqueue(j)
        a.status = RUNNING
        c.status = DONE
        c.finished_at = time.time()
        snap = q.snapshot()
        assert snap['counts'] == {'running': 1, 'queued': 1, 'done': 1,
                                  'failed': 0, 'cancelled': 0}
        assert [j['card_name'] for j in snap['running']] == ['A']
        assert [j['card_name'] for j in snap['queued']] == ['B']
        assert [j['card_name'] for j in snap['recent']] == ['C']

    def test_jobs_for_deck_active_only(self):
        q = GenerationQueue()
        a, b = _job(deck='d1', card='A'), _job(deck='d1', card='B')
        q.enqueue(a); q.enqueue(b)
        b.status = DONE
        assert [j.card_name for j in q.jobs_for_deck('d1')] == ['A']
        assert len(q.jobs_for_deck('d1', active_only=False)) == 2
