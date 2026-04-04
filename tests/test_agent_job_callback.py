"""
Tests for AW.1.3 — Apollo callback (_notify_apollo) on job completion.

Test Categories:
- _notify_apollo called with correct payload on success
- _notify_apollo called with status="error" on exception
- _notify_apollo NOT called for pending → running transition
- _notify_apollo never re-raises exceptions
"""

import json
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
# _notify_apollo unit tests
# =============================================================================

class TestNotifyApollo:
    def test_notify_apollo_posts_correct_mutation_on_success(self, set_env):
        """AC4/AC5: _notify_apollo sends updateAgentJob mutation with correct payload."""
        from app import _notify_apollo

        with patch("app.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_post.return_value = mock_response

            _notify_apollo("job-123", "success", result='{"foo": "bar"}')

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            url = call_args[0][0]
            payload = call_args[1]["json"]
            headers = call_args[1]["headers"]
            timeout = call_args[1]["timeout"]

            assert url == "http://localhost:4000"
            assert "updateAgentJob" in payload["query"]
            assert payload["variables"]["jobId"] == "job-123"
            assert payload["variables"]["status"] == "success"
            assert payload["variables"]["result"] == '{"foo": "bar"}'
            assert payload["variables"]["error"] is None
            assert headers["X-API-Key"] == "test-graphology-key"
            assert headers["Content-Type"] == "application/json"
            assert timeout == 10.0

    def test_notify_apollo_posts_error_payload(self, set_env):
        """AC4: _notify_apollo sends error status and error message."""
        from app import _notify_apollo

        with patch("app.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.raise_for_status.return_value = None
            mock_post.return_value = mock_response

            _notify_apollo("job-456", "error", error="Something went wrong")

            payload = mock_post.call_args[1]["json"]
            assert payload["variables"]["status"] == "error"
            assert payload["variables"]["error"] == "Something went wrong"
            assert payload["variables"]["result"] is None

    def test_notify_apollo_does_not_reraise_on_exception(self, set_env):
        """AC6/story constraint: _notify_apollo swallows exceptions, does not re-raise."""
        from app import _notify_apollo

        with patch("app.requests.post", side_effect=Exception("network error")):
            # This must NOT raise
            _notify_apollo("job-789", "success", result='{}')

    def test_notify_apollo_logs_error_on_exception(self, set_env, caplog):
        """_notify_apollo logs an error when notification fails."""
        import logging
        from app import _notify_apollo

        with patch("app.requests.post", side_effect=Exception("timeout")):
            with caplog.at_level(logging.ERROR, logger="app"):
                _notify_apollo("job-abc", "error", error="agent blew up")

        assert any("job-abc" in record.message for record in caplog.records)


# =============================================================================
# _run_agent_job callback integration tests
# =============================================================================

class TestRunAgentJobCallback:
    def test_notify_apollo_called_on_success(self, set_env):
        """AC4: _notify_apollo called once with status=success when job succeeds."""
        from app import _run_agent_job, _job_store, _job_lock, JobStatus
        from datetime import datetime, timezone

        job_id = "test-job-success-001"
        with _job_lock:
            _job_store[job_id] = {
                "status": JobStatus.ACCEPTED,
                "result": None,
                "created_at": datetime.now(timezone.utc),
            }

        success_result = {"success": True, "status": "success", "context_node_type": "X"}

        with patch("app._load_and_run_agent", return_value=success_result), \
             patch("app._notify_apollo") as mock_notify:
            _run_agent_job(
                job_id=job_id,
                agent="test_agent",
                workflow_id=None,
                context_node_id="node-1",
            )

        mock_notify.assert_called_once()
        notify_call = mock_notify.call_args
        assert notify_call[0][0] == job_id
        assert notify_call[0][1] == "success"
        # result should be JSON string
        assert notify_call[1].get("result") is not None
        assert "success" in notify_call[1]["result"]

    def test_notify_apollo_called_on_error_result(self, set_env):
        """AC4: _notify_apollo called with status=error when agent returns success=False."""
        from app import _run_agent_job, _job_store, _job_lock, JobStatus
        from datetime import datetime, timezone

        job_id = "test-job-error-001"
        with _job_lock:
            _job_store[job_id] = {
                "status": JobStatus.ACCEPTED,
                "result": None,
                "created_at": datetime.now(timezone.utc),
            }

        error_result = {"success": False, "error": "Agent failed badly"}

        with patch("app._load_and_run_agent", return_value=error_result), \
             patch("app._notify_apollo") as mock_notify:
            _run_agent_job(
                job_id=job_id,
                agent="test_agent",
                workflow_id=None,
                context_node_id="node-1",
            )

        mock_notify.assert_called_once()
        notify_call = mock_notify.call_args
        assert notify_call[0][1] == "error"

    def test_notify_apollo_called_on_exception(self, set_env):
        """AC4: _notify_apollo called with status=error when exception is raised."""
        from app import _run_agent_job, _job_store, _job_lock, JobStatus
        from datetime import datetime, timezone

        job_id = "test-job-exception-001"
        with _job_lock:
            _job_store[job_id] = {
                "status": JobStatus.ACCEPTED,
                "result": None,
                "created_at": datetime.now(timezone.utc),
            }

        with patch("app._load_and_run_agent", side_effect=RuntimeError("unexpected crash")), \
             patch("app._notify_apollo") as mock_notify:
            _run_agent_job(
                job_id=job_id,
                agent="test_agent",
                workflow_id=None,
                context_node_id="node-1",
            )

        mock_notify.assert_called_once()
        notify_call = mock_notify.call_args
        assert notify_call[0][1] == "error"

    def test_notify_apollo_not_called_for_running_transition(self, set_env):
        """AC6: _notify_apollo is NOT called for the pending → running transition.

        Validates that the first status update to RUNNING does not trigger
        an Apollo callback — only final states (success/error) do.
        """
        from app import _run_agent_job, _job_store, _job_lock, JobStatus
        from datetime import datetime, timezone

        job_id = "test-job-running-001"
        with _job_lock:
            _job_store[job_id] = {
                "status": JobStatus.ACCEPTED,
                "result": None,
                "created_at": datetime.now(timezone.utc),
            }

        running_statuses_seen = []

        def track_status_changes(jid, status, **kwargs):
            # This should only be called for final states
            running_statuses_seen.append(status)

        with patch("app._load_and_run_agent", return_value={"success": True, "status": "success"}), \
             patch("app._notify_apollo", side_effect=track_status_changes):
            _run_agent_job(
                job_id=job_id,
                agent="test_agent",
                workflow_id=None,
                context_node_id="node-1",
            )

        # _notify_apollo should only be called once (final state), never with "running"
        assert "running" not in running_statuses_seen
        assert len(running_statuses_seen) == 1

    def test_notify_apollo_called_once_not_multiple_times(self, set_env):
        """_notify_apollo is called exactly once per job completion."""
        from app import _run_agent_job, _job_store, _job_lock, JobStatus
        from datetime import datetime, timezone

        job_id = "test-job-once-001"
        with _job_lock:
            _job_store[job_id] = {
                "status": JobStatus.ACCEPTED,
                "result": None,
                "created_at": datetime.now(timezone.utc),
            }

        with patch("app._load_and_run_agent", return_value={"success": True}), \
             patch("app._notify_apollo") as mock_notify:
            _run_agent_job(
                job_id=job_id,
                agent="test_agent",
                workflow_id=None,
                context_node_id="node-1",
            )

        assert mock_notify.call_count == 1

    def test_batch_id_and_accumulator_params_accepted(self, set_env):
        """_run_agent_job accepts batch_id and accumulator params (AW.1.4 forward compat)."""
        from app import _run_agent_job, _job_store, _job_lock, JobStatus
        from datetime import datetime, timezone

        job_id = "test-job-batch-001"
        with _job_lock:
            _job_store[job_id] = {
                "status": JobStatus.ACCEPTED,
                "result": None,
                "created_at": datetime.now(timezone.utc),
            }

        with patch("app._load_and_run_agent", return_value={"success": True}), \
             patch("app._notify_apollo"):
            # Must not raise even with batch_id and accumulator set
            _run_agent_job(
                job_id=job_id,
                agent="test_agent",
                workflow_id=None,
                context_node_id="node-1",
                batch_id="batch-xyz",
                accumulator=MagicMock(),
            )


# =============================================================================
# Full async flow: callback fires after background thread completes
# =============================================================================

class TestAsyncCallbackIntegration:
    def test_async_job_triggers_apollo_callback(self, client, auth_headers):
        """AC4: After async job finishes, Apollo callback is fired."""
        done_event = threading.Event()
        callback_calls = []

        def mock_notify(job_id, status, **kwargs):
            callback_calls.append({"job_id": job_id, "status": status})
            done_event.set()

        with patch("app._load_and_run_agent", return_value={"success": True, "status": "success"}), \
             patch("app._notify_apollo", side_effect=mock_notify):
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
        job_id = response.json()["job_id"]

        assert done_event.wait(timeout=5), "Apollo callback was not called within 5s"
        assert len(callback_calls) == 1
        assert callback_calls[0]["job_id"] == job_id
        assert callback_calls[0]["status"] == "success"
