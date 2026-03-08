"""
Tests for ai-workflow FastAPI service.

Test Categories:
- Health endpoint
- API key authentication (AC4)
- Run agent endpoint validation (AC2, AC5)
- Agent loading (AC6)
- Error handling
- Async agent execution (Story 14.1)

Usage:
    pytest tests/test_app.py -v
"""

import os
import threading
import time
import pytest
from unittest.mock import patch, MagicMock
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
        # Import app inside fixture so env vars are set
        from app import app
        yield app


@pytest.fixture
def client(set_env):
    return TestClient(set_env)


@pytest.fixture
def auth_headers():
    return {"x-api-key": "test-api-key-123"}


# =============================================================================
# T01-T02: Health endpoint
# =============================================================================

class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        """T01: GET /health returns 200 with status ok."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_health_no_auth_required(self, client):
        """T02: Health endpoint doesn't require API key."""
        response = client.get("/health")
        assert response.status_code == 200


# =============================================================================
# T03-T05: API key authentication (AC4)
# =============================================================================

class TestApiKeyAuth:
    def test_missing_api_key_returns_401(self, client):
        """T03: Missing x-api-key header returns 401 (AC4)."""
        response = client.post("/run-agent", json={
            "agent": "import_matter_qa",
            "workflow_id": "wf-1",
            "context_node_id": "node-1",
        })
        assert response.status_code == 401
        assert "Unauthorized" in response.json()["detail"]

    def test_invalid_api_key_returns_401(self, client):
        """T04: Invalid x-api-key returns 401 (AC4)."""
        response = client.post(
            "/run-agent",
            json={
                "agent": "import_matter_qa",
                "workflow_id": "wf-1",
                "context_node_id": "node-1",
            },
            headers={"x-api-key": "wrong-key"},
        )
        assert response.status_code == 401

    def test_valid_api_key_passes_auth(self, client, auth_headers):
        """T05: Valid x-api-key passes authentication (AC4)."""
        with patch("app._load_and_run_agent") as mock_run:
            mock_run.return_value = {
                "success": True,
                "execution_ids": ["exec-1"],
                "context_node_type": "Submission",
            }
            response = client.post(
                "/run-agent",
                json={
                    "agent": "import_matter_qa",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                },
                headers=auth_headers,
            )
            assert response.status_code == 200


# =============================================================================
# T06-T08: Run agent endpoint (AC2)
# =============================================================================

class TestRunAgentEndpoint:
    def test_successful_run_returns_execution_ids(self, client, auth_headers):
        """T06: Successful run returns execution_ids and context_node_type (AC2)."""
        with patch("app._load_and_run_agent") as mock_run:
            mock_run.return_value = {
                "success": True,
                "execution_ids": ["exec-1", "exec-2"],
                "context_node_type": "Submission",
            }
            response = client.post(
                "/run-agent",
                json={
                    "agent": "import_matter_qa",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                },
                headers=auth_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["execution_ids"]) == 2
            assert data["context_node_type"] == "Submission"

    def test_missing_required_fields_returns_422(self, client, auth_headers):
        """T07: Missing required fields returns 422."""
        response = client.post(
            "/run-agent",
            json={"agent": "test"},  # missing workflow_id, context_node_id
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_agent_not_found_returns_error(self, client, auth_headers):
        """T08: Non-existent agent returns error (AC5)."""
        with patch("app._load_and_run_agent") as mock_run:
            mock_run.return_value = {
                "success": False,
                "error": "Agent not found: nonexistent_agent",
            }
            response = client.post(
                "/run-agent",
                json={
                    "agent": "nonexistent_agent",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                },
                headers=auth_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False
            assert "Agent not found" in data["error"]


# =============================================================================
# T09-T11: _load_and_run_agent unit tests
# =============================================================================

class TestLoadAndRunAgent:
    def test_agent_file_not_found(self, set_env):
        """T09: Returns error when agent YAML doesn't exist (AC5)."""
        from app import _load_and_run_agent
        result = _load_and_run_agent(
            agent="nonexistent",
            workflow_id="wf-1",
            context_node_id="node-1",
            agents_dir="/tmp/no-such-dir",
            actions_dir="/tmp/no-such-dir",
        )
        assert result["success"] is False
        assert "Agent not found" in result["error"]

    @patch("app._fetch_context_node")
    def test_node_not_found(self, mock_fetch, set_env):
        """T10: Returns error when context node doesn't exist (AC3)."""
        mock_fetch.return_value = {"success": False, "error": "Node not found: bad-id"}

        from app import _load_and_run_agent
        # Create a temp agent file
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "test_agent.yaml")
            with open(agent_path, "w") as f:
                f.write("name: test\nnodes: []\nedges: []")

            result = _load_and_run_agent(
                agent="test_agent",
                workflow_id="wf-1",
                context_node_id="bad-id",
                agents_dir=tmpdir,
                actions_dir="/tmp",
            )
            assert result["success"] is False
            assert "Node not found" in result["error"]

    @patch("app._validate_workflow")
    @patch("app._fetch_context_node")
    def test_workflow_not_found(self, mock_fetch, mock_validate, set_env):
        """T10b: Returns error when workflow_id is not valid (AC3b)."""
        mock_fetch.return_value = {
            "success": True,
            "node_type": "Submission",
            "data": {"id": "sub-1", "name": "Test"},
        }
        mock_validate.return_value = {"success": False, "error": "Workflow not found: bad-wf"}

        from app import _load_and_run_agent
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "test_agent.yaml")
            with open(agent_path, "w") as f:
                f.write("name: test\nnodes: []\nedges: []")

            result = _load_and_run_agent(
                agent="test_agent",
                workflow_id="bad-wf",
                context_node_id="sub-1",
                agents_dir=tmpdir,
                actions_dir="/tmp",
            )
            assert result["success"] is False
            assert "Workflow not found" in result["error"]


# =============================================================================
# T12-T13: Context node fetch and workflow validation
# =============================================================================

class TestContextNodeFetch:
    @patch("app.get_node")
    def test_fetch_context_node_delegates_to_get_node(self, mock_get, set_env):
        """T12: _fetch_context_node calls graphology.get_node."""
        mock_get.return_value = {
            "success": True,
            "node_type": "Submission",
            "data": {"id": "sub-1"},
        }

        from app import _fetch_context_node
        result = _fetch_context_node("sub-1")

        assert result["success"] is True
        assert result["node_type"] == "Submission"
        mock_get.assert_called_once()

    @patch("app.get_node")
    def test_validate_workflow_checks_type(self, mock_get, set_env):
        """T13: _validate_workflow checks the node resolves as Workflow type."""
        mock_get.return_value = {
            "success": True,
            "node_type": "Workflow",
            "data": {"id": "wf-1", "name": "Import Matter"},
        }

        from app import _validate_workflow
        result = _validate_workflow("wf-1")

        assert result["success"] is True

    @patch("app.get_node")
    def test_validate_workflow_wrong_type(self, mock_get, set_env):
        """T13b: _validate_workflow returns error if node is not Workflow type."""
        mock_get.return_value = {
            "success": True,
            "node_type": "Submission",
            "data": {"id": "sub-1"},
        }

        from app import _validate_workflow
        result = _validate_workflow("sub-1")

        assert result["success"] is False
        assert "Workflow not found" in result["error"]


# =============================================================================
# Story 14.1: Async Agent Endpoint Tests
# =============================================================================

class TestAsyncRequestModel:
    """Task 1: async_mode field and job store infrastructure."""

    def test_async_mode_defaults_to_false(self, set_env):
        """T14: RunAgentRequest.async_mode defaults to False (AC7)."""
        from app import RunAgentRequest
        req = RunAgentRequest(agent="test", workflow_id="wf-1", context_node_id="n-1")
        assert req.async_mode is False

    def test_async_mode_accepts_true(self, set_env):
        """T15: RunAgentRequest.async_mode can be set to True (AC2)."""
        from app import RunAgentRequest
        req = RunAgentRequest(
            agent="test", workflow_id="wf-1", context_node_id="n-1", async_mode=True
        )
        assert req.async_mode is True

    def test_job_status_enum_values(self, set_env):
        """T16: JobStatus enum has ACCEPTED, RUNNING, SUCCESS, ERROR (AC3)."""
        from app import JobStatus
        assert JobStatus.ACCEPTED.value == "accepted"
        assert JobStatus.RUNNING.value == "running"
        assert JobStatus.SUCCESS.value == "success"
        assert JobStatus.ERROR.value == "error"

    def test_job_store_exists(self, set_env):
        """T17: _job_store module-level dict exists."""
        from app import _job_store
        assert isinstance(_job_store, dict)

    def test_generate_job_id_returns_uuid_string(self, set_env):
        """T18: _generate_job_id() returns a UUID string."""
        from app import _generate_job_id
        job_id = _generate_job_id()
        assert isinstance(job_id, str)
        assert len(job_id) == 36  # UUID4 format: 8-4-4-4-12

    def test_generate_job_id_unique(self, set_env):
        """T19: _generate_job_id() returns unique IDs."""
        from app import _generate_job_id
        ids = {_generate_job_id() for _ in range(100)}
        assert len(ids) == 100


class TestAsyncExecution:
    """Task 2: Async execution path (AC2, AC7)."""

    def test_sync_mode_unchanged(self, client, auth_headers):
        """T20: POST without async_mode works identically (AC7)."""
        with patch("app._load_and_run_agent") as mock_run:
            mock_run.return_value = {
                "success": True,
                "status": "success",
                "context_node_type": "ApplicationFormFile",
                "payload": {"key": "value"},
            }
            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                },
                headers=auth_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["status"] == "success"

    def test_sync_mode_explicit_false(self, client, auth_headers):
        """T21: POST with async_mode=false works identically (AC7)."""
        with patch("app._load_and_run_agent") as mock_run:
            mock_run.return_value = {
                "success": True,
                "status": "success",
                "context_node_type": "ApplicationFormFile",
            }
            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                    "async_mode": False,
                },
                headers=auth_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

    def test_async_mode_returns_202_with_job_id(self, client, auth_headers):
        """T22: POST with async_mode=true returns 202 with job_id (AC2)."""
        with patch("app._load_and_run_agent") as mock_run:
            mock_run.return_value = {"success": True, "status": "success"}
            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                    "async_mode": True,
                },
                headers=auth_headers,
            )
            assert response.status_code == 202
            data = response.json()
            assert "job_id" in data
            assert data["status"] == "accepted"
            assert len(data["job_id"]) == 36  # UUID format

    def test_async_mode_launches_background_thread(self, client, auth_headers):
        """T23: Async mode launches a thread that calls _load_and_run_agent."""
        with patch("app._load_and_run_agent") as mock_run:
            call_event = threading.Event()
            def tracked_agent(*args, **kwargs):
                call_event.set()
                return {"success": True, "status": "success"}
            mock_run.side_effect = tracked_agent

            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                    "async_mode": True,
                },
                headers=auth_headers,
            )
            assert response.status_code == 202
            assert call_event.wait(timeout=5), "Background thread did not call _load_and_run_agent"
            mock_run.assert_called_once_with(
                agent="file_extraction",
                workflow_id="wf-1",
                context_node_id="node-1",
            )


class TestJobStatusEndpoint:
    """Task 3: Job status polling (AC3, AC4, AC6)."""

    def test_job_poll_running(self, client, auth_headers):
        """T24: GET /run-agent/jobs/{job_id} returns running status (AC3)."""
        with patch("app._load_and_run_agent") as mock_run:
            # Use events for deterministic thread sync
            agent_entered = threading.Event()
            barrier = threading.Event()
            def slow_agent(*args, **kwargs):
                agent_entered.set()
                barrier.wait(timeout=5)
                return {"success": True, "status": "success"}
            mock_run.side_effect = slow_agent

            # Submit async job
            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                    "async_mode": True,
                },
                headers=auth_headers,
            )
            job_id = response.json()["job_id"]

            # Wait for thread to enter agent (deterministic, no sleep)
            assert agent_entered.wait(timeout=5), "Thread did not start agent"
            poll = client.get(f"/run-agent/jobs/{job_id}", headers=auth_headers)
            assert poll.status_code == 200
            data = poll.json()
            assert data["status"] == "running"
            assert data["job_id"] == job_id
            assert "result" not in data

            barrier.set()  # Unblock

    def test_job_poll_success(self, client, auth_headers):
        """T25: GET /run-agent/jobs/{job_id} returns success with result (AC3)."""
        with patch("app._load_and_run_agent") as mock_run:
            done_event = threading.Event()
            def completing_agent(*args, **kwargs):
                result = {
                    "success": True,
                    "status": "success",
                    "context_node_type": "ApplicationFormFile",
                    "payload": {"extracted": True},
                }
                done_event.set()
                return result
            mock_run.side_effect = completing_agent

            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                    "async_mode": True,
                },
                headers=auth_headers,
            )
            job_id = response.json()["job_id"]

            assert done_event.wait(timeout=5), "Agent thread did not complete"
            # Small yield to let _run_agent_job finish storing the result
            time.sleep(0.05)

            poll = client.get(f"/run-agent/jobs/{job_id}", headers=auth_headers)
            assert poll.status_code == 200
            data = poll.json()
            assert data["status"] == "success"
            assert data["job_id"] == job_id
            assert data["result"]["success"] is True
            assert data["result"]["payload"]["extracted"] is True

    def test_job_poll_error(self, client, auth_headers):
        """T26: GET /run-agent/jobs/{job_id} returns error with result (AC3)."""
        with patch("app._load_and_run_agent") as mock_run:
            done_event = threading.Event()
            def failing_agent(*args, **kwargs):
                result = {"success": False, "error": "Agent execution failed"}
                done_event.set()
                return result
            mock_run.side_effect = failing_agent

            response = client.post(
                "/run-agent",
                json={
                    "agent": "file_extraction",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                    "async_mode": True,
                },
                headers=auth_headers,
            )
            job_id = response.json()["job_id"]

            assert done_event.wait(timeout=5), "Agent thread did not complete"
            time.sleep(0.05)

            poll = client.get(f"/run-agent/jobs/{job_id}", headers=auth_headers)
            assert poll.status_code == 200
            data = poll.json()
            assert data["status"] == "error"
            assert data["result"]["error"] == "Agent execution failed"

    def test_job_poll_requires_auth(self, client):
        """T27: GET /run-agent/jobs/{job_id} requires x-api-key (AC4)."""
        poll = client.get("/run-agent/jobs/some-job-id")
        assert poll.status_code == 401

    def test_job_poll_invalid_auth(self, client):
        """T28: GET /run-agent/jobs/{job_id} rejects invalid key (AC4)."""
        poll = client.get(
            "/run-agent/jobs/some-job-id",
            headers={"x-api-key": "wrong-key"},
        )
        assert poll.status_code == 401

    def test_unknown_job_returns_404(self, client, auth_headers):
        """T29: GET /run-agent/jobs/{unknown_id} returns 404 (AC6)."""
        poll = client.get(
            "/run-agent/jobs/00000000-0000-0000-0000-000000000000",
            headers=auth_headers,
        )
        assert poll.status_code == 404
