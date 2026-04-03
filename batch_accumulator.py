"""
BatchAccumulator — Thread-safe per-process LLM batch accumulator.

Collects concurrent LLM calls from fan-out workflow threads and dispatches
them as a single batch to the LLM provider within a configurable time window.

Story: AW.1.4
"""

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PendingCall:
    payload: Any
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Exception = None


class BatchAccumulator:
    """
    Accumulates concurrent LLM calls within a dispatch window and dispatches
    them as a batch to reduce LLM provider API calls.

    Usage:
        with accumulator.pending_call("agent.step", payload) as call:
            use call.result

    The context manager blocks until the dispatcher fires and the result is
    available (or raises if an error occurred).
    """

    def __init__(
        self,
        dispatch_window_ms: int = 200,
        batch_threshold: Optional[int] = None,
    ):
        """
        Args:
            dispatch_window_ms: How long (in ms) to accumulate calls before dispatching.
            batch_threshold: If set, dispatch immediately when this many calls are pending
                             for a single step_key (before the window expires).
        """
        self._dispatch_window = dispatch_window_ms / 1000.0  # convert to seconds
        self._batch_threshold = batch_threshold
        self._pending: dict[str, list[PendingCall]] = {}
        self._lock = threading.Lock()
        self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher.start()

    @contextmanager
    def pending_call(self, step_key: str, payload: Any):
        """
        Context manager: register a pending LLM call and block until dispatched.

        Args:
            step_key: "{agentName}.{stepName}" — e.g. "file_extraction.run_extraction"
            payload: The LLM request payload for this call.

        Yields:
            PendingCall with .result populated after dispatch.

        Raises:
            Any exception raised by _dispatch_batch propagated to the caller.
        """
        call = PendingCall(payload=payload)
        early_dispatch_calls = None

        with self._lock:
            if step_key not in self._pending:
                self._pending[step_key] = []
            self._pending[step_key].append(call)
            # Early dispatch: threshold reached before window expires
            if (
                self._batch_threshold is not None
                and len(self._pending[step_key]) >= self._batch_threshold
            ):
                early_dispatch_calls = self._pending.pop(step_key)

        if early_dispatch_calls is not None:
            # Dispatch outside the lock to avoid holding it during LLM calls
            self._dispatch_step(step_key, early_dispatch_calls)

        call.event.wait()  # blocks until dispatcher (or early dispatch) fires
        if call.error:
            raise call.error
        yield call

    def _dispatch_loop(self):
        """Background thread: wake every dispatch_window and flush all pending calls."""
        while True:
            time.sleep(self._dispatch_window)
            self._dispatch_all()

    def _dispatch_all(self):
        """Snapshot and clear all pending calls, then dispatch each step_key."""
        with self._lock:
            pending_snapshot = {k: v[:] for k, v in self._pending.items()}
            self._pending.clear()

        for step_key, calls in pending_snapshot.items():
            if calls:
                self._dispatch_step(step_key, calls)

    def _dispatch_step(self, step_key: str, calls: list):
        """
        Dispatch a batch of calls for a single step_key, distribute results back.

        All calls are dispatched together; results are distributed in order.
        On error, all waiting threads receive the same exception.
        """
        try:
            results = self._dispatch_batch(step_key, [c.payload for c in calls])
            for call, result in zip(calls, results):
                call.result = result
                call.event.set()
        except Exception as e:
            for call in calls:
                call.error = e
                call.event.set()

    def _dispatch_batch(self, step_key: str, payloads: list) -> list:
        """
        Dispatch a batch of payloads for the given step_key.

        Default implementation: concurrent individual requests via ThreadPoolExecutor.
        All futures are submitted BEFORE any .result() is called (never sequential).

        Override or subclass to use provider-native Batch APIs:
          - OpenAI: POST /v1/batches
          - Anthropic: POST /v1/messages/batches

        Args:
            step_key: The step identifier (for provider detection in subclasses).
            payloads: List of request payloads in order.

        Returns:
            List of results in the same order as payloads.
        """
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
            # CRITICAL: submit ALL futures before calling .result() on any of them
            futures = [
                executor.submit(self._single_llm_call, step_key, p) for p in payloads
            ]
            return [f.result() for f in futures]

    def _single_llm_call(self, step_key: str, payload: Any) -> Any:
        """
        Execute a single LLM call. Override in subclasses or configure via DI.

        Raises:
            NotImplementedError: Base class does not implement provider calls.
        """
        raise NotImplementedError(
            "Subclass BatchAccumulator and implement _single_llm_call, "
            "or override _dispatch_batch for provider-native batch APIs."
        )
