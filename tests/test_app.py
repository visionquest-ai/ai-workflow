"""
Tests for ai-workflow FastAPI service (Story 16.1, Task 2).

Test Categories:
- Health endpoint
- API key authentication (AC4)
- Run agent endpoint validation (AC2, AC5)
- Agent loading (AC6)
- Error handling

Usage:
    pytest tests/test_app.py -v
"""

import os
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
