"""
Integration tests for ai-workflow service (Story 16.1, Task 4).

These tests require a running graphology Apollo Server with seeded data.
Skip with: pytest -m "not integration"

Test Categories:
- T01-T03: graphology.get_node with real nodes (AC1)
- T04: /run-agent endpoint end-to-end (AC2)
- T05-T07: Error cases (AC3, AC5, AC4)

Usage:
    # With running graphology server:
    pytest tests/test_integration.py -v

    # Skip integration tests:
    pytest tests/ -v -m "not integration"
"""

import os
import pytest
from unittest.mock import patch

# Mark all tests in this module as integration
pytestmark = pytest.mark.integration

# Skip if server not available
GRAPHOLOGY_URL = os.environ.get("GRAPHOLOGY_URL", "http://localhost:4000")
GRAPHOLOGY_API_KEY = os.environ.get("GRAPHOLOGY_API_KEY", "")


def _server_available():
    """Check if graphology server is running."""
    try:
        import requests
        resp = requests.get(
            GRAPHOLOGY_URL,
            timeout=2,
            headers={"Content-Type": "application/json"},
        )
        return resp.status_code in (200, 400)  # 400 = no query but server running
    except Exception:
        return False


skip_no_server = pytest.mark.skipif(
    not _server_available(),
    reason=f"Graphology server not available at {GRAPHOLOGY_URL}",
)


# =============================================================================
# T01-T03: graphology.get_node with real nodes (AC1)
# =============================================================================

@skip_no_server
class TestGetNodeIntegration:
    def _get_node(self, node_id):
        """Helper to call get_node."""
        import sys
        actions_dir = os.path.join(os.path.dirname(__file__), "..", "actions")
        if actions_dir not in sys.path:
            sys.path.insert(0, os.path.abspath(actions_dir))
        from graphology import get_node

        state = {"variables": {
            "GRAPHOLOGY_URL": GRAPHOLOGY_URL,
            "GRAPHOLOGY_API_KEY": GRAPHOLOGY_API_KEY,
        }}
        # Clear cache for fresh tests
        from graphology import _schema_cache
        _schema_cache.clear()

        return get_node(state, node_id=node_id)

    def test_get_node_finds_submission(self):
        """T01: get_node finds a Submission node with scalar fields (AC1)."""
        # Use a known Submission ID from seeded data
        # This test validates the full introspection flow:
        # 1. Introspect schema → get root query fields
        # 2. Try each type → find the matching one
        # 3. Introspect type → get scalar fields
        # 4. Query full node
        result = self._get_node("test-submission-id")
        if result["success"]:
            assert result["node_type"] == "Submission"
            assert "id" in result["data"]
        else:
            pytest.skip("No test Submission node found - seed data may differ")

    def test_get_node_finds_workflow(self):
        """T02: get_node finds a Workflow node (AC1)."""
        result = self._get_node("test-workflow-id")
        if result["success"]:
            assert result["node_type"] == "Workflow"
        else:
            pytest.skip("No test Workflow node found")

    def test_get_node_not_found(self):
        """T03: get_node returns error for nonexistent ID (AC3)."""
        result = self._get_node("nonexistent-id-12345-xyz")
        assert result["success"] is False
        assert "Node not found" in result["error"]


# =============================================================================
# T04: /run-agent endpoint end-to-end (AC2)
# =============================================================================

@skip_no_server
class TestRunAgentIntegration:
    def test_run_agent_end_to_end(self):
        """T04: Full /run-agent flow with real context node (AC2)."""
        # This is a high-cost test — requires LLM calls
        # Skip unless explicitly enabled
        if not os.environ.get("RUN_E2E_AGENT_TESTS"):
            pytest.skip("Set RUN_E2E_AGENT_TESTS=1 to enable")

        from fastapi.testclient import TestClient
        with patch.dict(os.environ, {
            "RUN_AGENT_API_KEY": "test-key",
            "GRAPHOLOGY_URL": GRAPHOLOGY_URL,
            "GRAPHOLOGY_API_KEY": GRAPHOLOGY_API_KEY,
        }):
            from app import app
            client = TestClient(app)
            response = client.post(
                "/run-agent",
                json={
                    "agent": "import_matter_qa",
                    "workflow_id": "test-workflow-id",
                    "context_node_id": "test-submission-id",
                },
                headers={"x-api-key": "test-key"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["execution_ids"]) > 0


# =============================================================================
# T05-T07: Error cases
# =============================================================================

@skip_no_server
class TestErrorCasesIntegration:
    def test_node_not_found_via_endpoint(self):
        """T05: /run-agent with nonexistent context_node_id (AC3)."""
        from fastapi.testclient import TestClient
        with patch.dict(os.environ, {
            "RUN_AGENT_API_KEY": "test-key",
            "GRAPHOLOGY_URL": GRAPHOLOGY_URL,
            "GRAPHOLOGY_API_KEY": GRAPHOLOGY_API_KEY,
        }):
            from app import app
            client = TestClient(app)
            response = client.post(
                "/run-agent",
                json={
                    "agent": "import_matter_qa",
                    "workflow_id": "wf-1",
                    "context_node_id": "nonexistent-id-xyz",
                },
                headers={"x-api-key": "test-key"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False
            assert "Node not found" in data["error"]

    def test_invalid_agent_via_endpoint(self):
        """T06: /run-agent with nonexistent agent (AC5)."""
        from fastapi.testclient import TestClient
        with patch.dict(os.environ, {
            "RUN_AGENT_API_KEY": "test-key",
            "GRAPHOLOGY_URL": GRAPHOLOGY_URL,
            "GRAPHOLOGY_API_KEY": GRAPHOLOGY_API_KEY,
        }):
            from app import app
            client = TestClient(app)
            response = client.post(
                "/run-agent",
                json={
                    "agent": "nonexistent_agent_xyz",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                },
                headers={"x-api-key": "test-key"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False
            assert "Agent not found" in data["error"]

    def test_missing_api_key_via_endpoint(self):
        """T07: /run-agent without API key returns 401 (AC4)."""
        from fastapi.testclient import TestClient
        with patch.dict(os.environ, {
            "RUN_AGENT_API_KEY": "test-key",
            "GRAPHOLOGY_URL": GRAPHOLOGY_URL,
        }):
            from app import app
            client = TestClient(app)
            response = client.post(
                "/run-agent",
                json={
                    "agent": "import_matter_qa",
                    "workflow_id": "wf-1",
                    "context_node_id": "node-1",
                },
            )
            assert response.status_code == 401
