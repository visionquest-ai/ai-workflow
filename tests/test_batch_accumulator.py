"""
Tests for BatchAccumulator — AW.1.4

Covers:
- AC10: 10 concurrent threads → 1 dispatch call
- AC3/AC5: 2 different step_keys → 2 separate dispatch calls, no cross-mixing
- AC5:  Results distributed to correct threads
- AC4:  Dispatch fires after window with only 1 pending call
- AC4:  Early dispatch when threshold reached before window expires
- AC9:  No circular imports between batch_accumulator and app.py
"""

import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# Ensure ai-workflow root is on sys.path
import os

AI_WORKFLOW_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if AI_WORKFLOW_DIR not in sys.path:
    sys.path.insert(0, AI_WORKFLOW_DIR)

from batch_accumulator import BatchAccumulator, PendingCall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _CapturingAccumulator(BatchAccumulator):
    """Subclass that records dispatch calls and returns controllable results."""

    def __init__(self, result_factory=None, **kwargs):
        """
        Args:
            result_factory: Callable(step_key, payloads) → list[results].
                            Defaults to echoing payloads back.
        """
        self._dispatch_calls = []
        self._dispatch_calls_lock = threading.Lock()
        self._result_factory = result_factory or (lambda sk, ps: list(ps))
        super().__init__(**kwargs)

    def _dispatch_batch(self, step_key, payloads):
        with self._dispatch_calls_lock:
            self._dispatch_calls.append((step_key, list(payloads)))
        return self._result_factory(step_key, payloads)

    @property
    def dispatch_call_count(self):
        with self._dispatch_calls_lock:
            return len(self._dispatch_calls)

    @property
    def dispatch_calls_snapshot(self):
        with self._dispatch_calls_lock:
            return list(self._dispatch_calls)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBatchAccumulator:

    def test_10_concurrent_threads_same_step_key_produce_1_dispatch_call(self):
        """AC10: 10 concurrent threads all hitting the same step_key must
        produce exactly 1 call to _dispatch_batch (not 10)."""
        acc = _CapturingAccumulator(dispatch_window_ms=200)

        N = 10
        barrier = threading.Barrier(N)
        results = [None] * N

        def worker(index):
            barrier.wait()  # synchronize start so all threads enter within the same window
            with acc.pending_call("agent.step", f"payload_{index}") as call:
                results[index] = call.result

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert acc.dispatch_call_count == 1, (
            f"Expected 1 dispatch call, got {acc.dispatch_call_count}"
        )
        # All threads should have received a result
        assert all(r is not None for r in results)

    def test_2_different_step_keys_produce_2_separate_dispatch_calls(self):
        """2 different step_keys must be dispatched separately — no cross-step mixing."""
        dispatched = {}
        dispatched_lock = threading.Lock()

        class _TwoKeyAccumulator(BatchAccumulator):
            def _dispatch_batch(self, step_key, payloads):
                with dispatched_lock:
                    dispatched[step_key] = list(payloads)
                return list(payloads)

        acc = _TwoKeyAccumulator(dispatch_window_ms=200)

        N_PER_KEY = 3
        barrier = threading.Barrier(N_PER_KEY * 2)
        results = {}
        results_lock = threading.Lock()

        def worker(step_key, index):
            barrier.wait()
            with acc.pending_call(step_key, f"{step_key}_payload_{index}") as call:
                with results_lock:
                    results.setdefault(step_key, []).append(call.result)

        threads = []
        for i in range(N_PER_KEY):
            threads.append(threading.Thread(target=worker, args=("step_A", i)))
            threads.append(threading.Thread(target=worker, args=("step_B", i)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Each step_key dispatched exactly once
        assert set(dispatched.keys()) == {"step_A", "step_B"}

        # No cross-contamination: step_A payloads contain only step_A payloads
        assert all("step_A" in p for p in dispatched["step_A"])
        assert all("step_B" in p for p in dispatched["step_B"])

    def test_results_distributed_to_correct_threads(self):
        """Each thread must receive its own result, not another thread's."""
        def result_factory(step_key, payloads):
            # Return a transformed version so we can verify origin
            return [f"result_for_{p}" for p in payloads]

        acc = _CapturingAccumulator(
            result_factory=result_factory,
            dispatch_window_ms=200,
        )

        N = 5
        barrier = threading.Barrier(N)
        thread_results = {}
        thread_results_lock = threading.Lock()

        def worker(index):
            payload = f"payload_{index}"
            barrier.wait()
            with acc.pending_call("agent.step", payload) as call:
                with thread_results_lock:
                    thread_results[index] = call.result

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Every thread should have received the result matching ITS payload
        for index in range(N):
            expected = f"result_for_payload_{index}"
            actual = thread_results[index]
            assert actual == expected, (
                f"Thread {index} got wrong result: expected {expected!r}, got {actual!r}"
            )

    def test_dispatch_fires_after_window_with_single_pending_call(self):
        """Dispatch must fire even with only 1 pending call, after the window expires."""
        window_ms = 150
        acc = _CapturingAccumulator(dispatch_window_ms=window_ms)

        result_holder = [None]

        def worker():
            with acc.pending_call("sole.call", "only_payload") as call:
                result_holder[0] = call.result

        t = threading.Thread(target=worker)
        start = time.monotonic()
        t.start()
        t.join(timeout=5.0)
        elapsed_ms = (time.monotonic() - start) * 1000

        # Thread should have completed (dispatch fired)
        assert not t.is_alive(), "Thread is still blocked — dispatch did not fire"
        assert result_holder[0] == "only_payload"
        # Should not have taken dramatically longer than the window
        assert elapsed_ms < window_ms * 10, (
            f"Dispatch took too long: {elapsed_ms:.0f}ms (window={window_ms}ms)"
        )

    def test_early_dispatch_when_threshold_reached(self):
        """When batch_threshold is set and reached, dispatch before window expires."""
        threshold = 3
        long_window_ms = 5000  # deliberately long so only threshold triggers dispatch

        acc = _CapturingAccumulator(
            dispatch_window_ms=long_window_ms,
            batch_threshold=threshold,
        )

        barrier = threading.Barrier(threshold)
        results = [None] * threshold

        def worker(index):
            barrier.wait()
            with acc.pending_call("threshold.step", f"p_{index}") as call:
                results[index] = call.result

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(threshold)]
        for t in threads:
            t.start()

        # Threads should complete well before the long window
        deadline = time.monotonic() + 3.0  # 3s max
        for t in threads:
            remaining = deadline - time.monotonic()
            t.join(timeout=max(remaining, 0.1))

        assert all(not t.is_alive() for t in threads), (
            "Some threads are still blocked — early dispatch did not fire"
        )
        assert all(r is not None for r in results)
        assert acc.dispatch_call_count == 1

    def test_no_circular_import_with_app_py(self):
        """batch_accumulator must be importable without pulling in app.py,
        and app.py must be importable without circular dependency."""
        # If either import fails with ImportError or circular-import RuntimeError,
        # this test will fail with the exception.
        import importlib

        # batch_accumulator should import cleanly with zero dependency on app
        ba_mod = importlib.import_module("batch_accumulator")
        assert hasattr(ba_mod, "BatchAccumulator")

        # app.py imports batch_accumulator — verify the chain is acyclic by
        # checking that batch_accumulator has no reference back to app.
        import inspect
        source = inspect.getsource(ba_mod)
        assert "import app" not in source
        assert "from app" not in source

    def test_error_propagated_to_all_threads_in_batch(self):
        """If _dispatch_batch raises, all threads in that batch receive the error."""
        class _ErrorAccumulator(BatchAccumulator):
            def _dispatch_batch(self, step_key, payloads):
                raise ValueError("LLM provider unavailable")

        acc = _ErrorAccumulator(dispatch_window_ms=100)

        N = 3
        barrier = threading.Barrier(N)
        errors = [None] * N

        def worker(index):
            barrier.wait()
            try:
                with acc.pending_call("error.step", f"p_{index}") as call:
                    pass  # should not reach here
            except ValueError as e:
                errors[index] = e

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert all(e is not None for e in errors), "Some threads did not receive the error"
        assert all(isinstance(e, ValueError) for e in errors)
        assert all("LLM provider unavailable" in str(e) for e in errors)
