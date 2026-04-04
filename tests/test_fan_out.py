"""
Tests for AW.1.3 — Fan-out: simultaneous thread start for context_node_ids.

Test Categories:
- Simultaneous start ordering (all .start() before any .join())
- context_node_ids triggers N parallel threads
- Backward compat: single context_node_id still works
- Validation: both context_node_id and context_node_ids → 400
"""

import os
import threading
import time
import pytest
from unittest.mock import patch, MagicMock, call
from fastapi.testclient import TestClient


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def set_env():
    """Set required env vars for testing."""
    with patch.dict(os.environ, {
        "RUN_AGENT_API_KEY": "test-api-key-123",
        "GRAPHOLOGY_URL": "http://localhost:4000",
        "GRAPHOLOGY_API_KEY": "test-graphology-key",
    }):
        from app import app
        yield app


@pytest.fixture
def client(set_env):
    return TestClient(set_env)


@pytest.fixture
def auth_headers():
    return {"x-api-key": "test-api-key-123"}


# =============================================================================
# Validation tests
# =============================================================================

class TestFanOutValidation:
    def test_both_singular_and_plural_returns_422(self, client, auth_headers):
        """AC1: providing both context_node_id and context_node_ids → 422 (validation error)."""
        response = client.post(
            "/run-agent",
            json={
                "agent": "file_extraction",
                "context_node_id": "node-1",
                "contextNodeIds": ["node-1", "node-2"],
            },
            headers=auth_headers,
        )
        assert response.status_code == 422, response.text

    def test_neither_singular_nor_plural_returns_422(self, client, auth_headers):
        """Providing neither context_node_id nor context_node_ids → 422."""
        response = client.post(
            "/run-agent",
            json={"agent": "file_extraction"},
            headers=auth_headers,
        )
        assert response.status_code == 422, response.text

    def test_snake_case_context_node_id_works(self, client, auth_headers):
        """Backward compat: snake_case context_node_id is still accepted."""
        with patch("app._load_and_run_agent") as mock_run:
            mock_run.return_value = {"success": True, "status": "success", "context_node_type": "X"}
            response = client.post(
                "/run-agent",
                json={"agent": "file_extraction", "context_node_id": "node-1"},
                headers=auth_headers,
            )
            assert response.status_code == 200

    def test_camel_case_context_node_id_works(self, client, auth_headers):
        """camelCase contextNodeId alias is accepted."""
        with patch("app._load_and_run_agent") as mock_run:
            mock_run.return_value = {"success": True, "status": "success", "context_node_type": "X"}
            response = client.post(
                "/run-agent",
                json={"agent": "file_extraction", "contextNodeId": "node-1"},
                headers=auth_headers,
            )
            assert response.status_code == 200

    def test_both_camel_and_snake_returns_422(self, client, auth_headers):
        """Both contextNodeId and contextNodeIds → 422."""
        response = client.post(
            "/run-agent",
            json={
                "agent": "file_extraction",
                "contextNodeId": "node-1",
                "contextNodeIds": ["node-2", "node-3"],
            },
            headers=auth_headers,
        )
        assert response.status_code == 422, response.text


# =============================================================================
# Fan-out async: simultaneous thread start ordering
# =============================================================================

class TestFanOutSimultaneousStart:
    """AC2: All threads must start before any join."""

    def test_all_threads_started_before_any_joined(self, client, auth_headers):
        """Verify start() is called for all threads before join() is called on any.

        We capture the sequence of calls on mock Thread objects and assert that
        all start() calls precede all join() calls.
        """
        call_sequence = []

        class TrackedThread:
            """Minimal threading.Thread stand-in that records start/join order."""
            def __init__(self, target=None, args=(), kwargs=None, daemon=None):
                self._target = target
                self._args = args
                self._kwargs = kwargs or {}
                self._id = id(self)

            def start(self):
                call_sequence.append(("start", self._id))
                # Execute synchronously so join() can complete instantly
                self._target(*self._args, **self._kwargs)

            def join(self):
                call_sequence.append(("join", self._id))

        with patch("app._run_agent_job"), \
             patch("app.threading.Thread", side_effect=TrackedThread):
            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "contextNodeIds": ["n-1", "n-2", "n-3"],
                    "asyncMode": True,
                },
                headers=auth_headers,
            )

        assert response.status_code == 202

        starts = [i for op, i in call_sequence if op == "start"]
        joins = [i for op, i in call_sequence if op == "join"]

        # In async fan-out we start all and return immediately (no joins)
        # so there should be 3 starts and 0 joins (we return 202 right away)
        assert len(starts) == 3, f"Expected 3 starts, got {len(starts)}: {call_sequence}"
        # No joins expected in async mode (we return immediately)
        assert len(joins) == 0, f"Async mode should not join threads: {call_sequence}"

    def test_sync_fanout_starts_all_before_joining(self, client, auth_headers):
        """Sync fan-out must also start all threads before joining any."""
        call_sequence = []

        class TrackedThread:
            def __init__(self, target=None, args=(), kwargs=None, daemon=None):
                self._target = target
                self._args = args
                self._kwargs = kwargs or {}
                self._id = id(self)

            def start(self):
                call_sequence.append(("start", self._id))

            def join(self):
                call_sequence.append(("join", self._id))

        with patch("app._load_and_run_agent", return_value={"success": True}), \
             patch("app.threading.Thread", side_effect=TrackedThread):
            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "context_node_ids": ["n-1", "n-2", "n-3"],
                    "async_mode": False,
                },
                headers=auth_headers,
            )

        assert response.status_code == 200

        starts = [i for op, i in call_sequence if op == "start"]
        joins = [i for op, i in call_sequence if op == "join"]

        assert len(starts) == 3, f"Expected 3 starts: {call_sequence}"
        assert len(joins) == 3, f"Expected 3 joins: {call_sequence}"

        # Verify all starts come before all joins
        last_start_pos = max(
            next(pos for pos, (op, i) in enumerate(call_sequence) if op == "start" and i == tid)
            for tid in starts
        )
        first_join_pos = min(
            next(pos for pos, (op, i) in enumerate(call_sequence) if op == "join" and i == tid)
            for tid in joins
        )
        assert last_start_pos < first_join_pos, (
            f"A join happened before all starts: {call_sequence}"
        )


# =============================================================================
# Fan-out async: N threads, N job_ids
# =============================================================================

class TestFanOutAsync:
    def test_context_node_ids_triggers_n_threads_async(self, client, auth_headers):
        """AC9: fan-out with 3 node IDs returns 3 job_ids and status=accepted."""
        with patch("app._run_agent_job"):
            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "contextNodeIds": ["n-1", "n-2", "n-3"],
                    "asyncMode": True,
                },
                headers=auth_headers,
            )

        assert response.status_code == 202
        data = response.json()
        assert "job_ids" in data
        assert len(data["job_ids"]) == 3
        assert data["status"] == "accepted"
        # Each job_id should be a UUID (36 chars)
        for jid in data["job_ids"]:
            assert len(jid) == 36

    def test_each_thread_gets_own_node_id(self, client, auth_headers):
        """AC3: Each thread receives its own context_node_id."""
        received_node_ids = []

        def capture_node_id(job_id, agent, workflow_id, node_id, **kwargs):
            received_node_ids.append(node_id)

        with patch("app._run_agent_job", side_effect=capture_node_id):
            client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "contextNodeIds": ["n-alpha", "n-beta", "n-gamma"],
                    "asyncMode": True,
                },
                headers=auth_headers,
            )

        # Wait briefly for daemon threads (they run synchronously via mock's side_effect)
        time.sleep(0.1)
        assert sorted(received_node_ids) == ["n-alpha", "n-beta", "n-gamma"]

    def test_batch_id_passed_to_each_thread(self, client, auth_headers):
        """AC3: batch_id is passed to each thread."""
        received_batch_ids = []

        def capture_batch_id(job_id, agent, workflow_id, node_id, **kwargs):
            received_batch_ids.append(kwargs.get("batch_id"))

        with patch("app._run_agent_job", side_effect=capture_batch_id):
            client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "contextNodeIds": ["n-1", "n-2"],
                    "asyncMode": True,
                    "batchId": "batch-xyz",
                },
                headers=auth_headers,
            )

        time.sleep(0.1)
        assert all(bid == "batch-xyz" for bid in received_batch_ids)


# =============================================================================
# Backward compatibility: single context_node_id
# =============================================================================

class TestSingleNodeBackwardCompat:
    def test_single_context_node_id_async_unchanged(self, client, auth_headers):
        """AC7: single context_node_id with async_mode=true still returns job_id."""
        with patch("app._run_agent_job"):
            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "context_node_id": "node-1",
                    "async_mode": True,
                },
                headers=auth_headers,
            )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "accepted"

    def test_single_context_node_id_sync_unchanged(self, client, auth_headers):
        """AC8: single context_node_id without async_mode still returns result directly."""
        with patch("app._load_and_run_agent") as mock_run:
            mock_run.return_value = {"success": True, "status": "success", "context_node_type": "X"}
            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "context_node_id": "node-1",
                },
                headers=auth_headers,
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_application_form_id_alias_still_works(self, client, auth_headers):
        """application_form_id alias backward compat."""
        with patch("app._load_and_run_agent") as mock_run:
            mock_run.return_value = {"success": True, "status": "success", "context_node_type": "X"}
            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "application_form_id": "form-1",
                },
                headers=auth_headers,
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
