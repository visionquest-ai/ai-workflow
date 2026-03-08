"""
Tests for scrape_law_firm YAML agent logic.

Tests the inline Python code from agents/scrape_law_firm.yaml by executing
the node run blocks directly with mocked dependencies. This validates:
- Story 1.1: validate_input node (fetch, node type, website presence)
- Story 1.2: scrape_website + process_scrape_result nodes
- Story 1.3: save_payload + finalize nodes, error payload persistence, edge wiring
- AC1: Happy path — successful scrape persisted to websitePayload
- AC2: Error scrape result persisted for visibility
- AC3: GraphQL mutation failure handling
- AC4: Successful completion and cleanup
- AC5: TEA YAML pattern compliance with file_extraction.yaml
- AC6: Precondition: websitePayload property exists in ontology

Usage:
    pytest tests/test_scrape_law_firm.py -v
"""

import json
import os
import textwrap

import pytest
import yaml


# =============================================================================
# HELPERS — Load and execute YAML agent node run blocks
# =============================================================================

def _load_agent_yaml():
    """Load the scrape_law_firm YAML agent definition."""
    agent_path = os.path.join(os.path.dirname(__file__), "..", "agents", "scrape_law_firm.yaml")
    with open(agent_path) as f:
        return yaml.safe_load(f)


def _get_node_code(agent_def, node_name):
    """Extract the `run` code block from a named node."""
    for node in agent_def["nodes"]:
        if node["name"] == node_name:
            return node.get("run", "")
    raise ValueError(f"Node '{node_name}' not found in agent definition")


def _exec_node(agent_def, node_name, state, settings=None):
    """Execute a node's run block with given state and return the result."""
    code = _get_node_code(agent_def, node_name)
    if not code:
        raise ValueError(f"Node '{node_name}' has no run block")

    settings = settings or agent_def.get("settings", {})
    namespace = {"state": state, "settings": settings}

    indented = textwrap.indent(code, "    ")
    wrapped = f"def _node_fn():\n{indented}\n_result = _node_fn()"

    exec(compile(wrapped, f"<agent:{node_name}>", "exec"), namespace)
    return namespace.get("_result")


def _get_node(agent_def, node_name):
    """Get a node definition by name."""
    for node in agent_def["nodes"]:
        if node["name"] == node_name:
            return node
    return None


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def agent_def():
    """Load the scrape_law_firm YAML agent."""
    return _load_agent_yaml()


# =============================================================================
# YAML Structure Compliance Tests (AC6)
# =============================================================================

class TestYamlStructureCompliance:
    """AC6: Verify agent file follows file_extraction.yaml conventions."""

    def test_has_required_top_level_sections(self, agent_def):
        """Agent YAML has name, description, state_schema, nodes, config."""
        required = ["name", "description", "state_schema", "nodes", "config"]
        for key in required:
            assert key in agent_def, f"Missing required top-level key: {key}"

    def test_name_matches(self, agent_def):
        assert agent_def["name"] == "scrape_law_firm"

    def test_state_schema_has_required_fields(self, agent_def):
        schema = agent_def["state_schema"]
        required_fields = [
            "context_node_id", "context_result", "website_url",
            "scrape_result", "scrape_data_json", "scrape_failed",
            "update_result",
            "status", "error", "completed",
        ]
        for field in required_fields:
            assert field in schema, f"Missing state_schema field: {field}"

    def test_config_raise_exceptions_false(self, agent_def):
        assert agent_def["config"]["raise_exceptions"] is False

    def test_has_fetch_node_using_graphology(self, agent_def):
        """fetch_node uses graphology.get_node action."""
        fetch_node = _get_node(agent_def, "fetch_node")
        assert fetch_node is not None, "Missing fetch_node"
        assert fetch_node["uses"] == "graphology.get_node"
        assert "node_id" in fetch_node["with"]
        assert "graphql_url" in fetch_node["with"]

    def test_has_validate_input_node(self, agent_def):
        """validate_input node exists as a run: block."""
        node = _get_node(agent_def, "validate_input")
        assert node is not None, "Missing validate_input node"
        assert "run" in node, "validate_input must be a run: block"

    def test_has_scrape_website_node(self, agent_def):
        """AC6: scrape_website node exists with uses: web.ai_scrape."""
        node = _get_node(agent_def, "scrape_website")
        assert node is not None, "Missing scrape_website node"
        assert node["uses"] == "web.ai_scrape"

    def test_scrape_website_has_output_schema(self, agent_def):
        """AC6: scrape_website has output_schema with all 18 fields."""
        node = _get_node(agent_def, "scrape_website")
        assert "output_schema" in node["with"], "Missing output_schema in scrape_website"
        schema = node["with"]["output_schema"]
        assert schema["type"] == "object"
        props = schema["properties"]
        expected_fields = [
            "firmName", "foundedYear", "website", "linkedin", "businessModel",
            "description", "country", "city", "state", "totalLawyers",
            "totalPartners", "mainEmail", "mainPhone", "managingPartners",
            "offices", "practiceAreas", "awards",
        ]
        for field in expected_fields:
            assert field in props, f"Missing output_schema field: {field}"

    def test_scrape_website_output_field(self, agent_def):
        """scrape_website outputs to scrape_result."""
        node = _get_node(agent_def, "scrape_website")
        assert node["output"] == "scrape_result"

    def test_scrape_website_url_from_state(self, agent_def):
        """scrape_website reads url from state.website_url."""
        node = _get_node(agent_def, "scrape_website")
        assert "{{ state.website_url }}" in node["with"]["url"]

    def test_has_process_scrape_result_node(self, agent_def):
        """process_scrape_result node exists as a run: block."""
        node = _get_node(agent_def, "process_scrape_result")
        assert node is not None, "Missing process_scrape_result node"
        assert "run" in node, "process_scrape_result must be a run: block"

    def test_node_order_implicit_chaining(self, agent_def):
        """Nodes are in correct order for implicit chaining (goto replaces edges)."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        expected_order = [
            "fetch_node", "validate_input", "scrape_website",
            "process_scrape_result", "save_payload", "finalize",
        ]
        assert node_names == expected_order

    def test_validate_input_goto_end_on_completed(self, agent_def):
        """validate_input has conditional goto __end__ when completed."""
        node = _get_node(agent_def, "validate_input")
        goto = node.get("goto")
        assert isinstance(goto, list), "validate_input must have conditional goto list"
        end_rule = next((r for r in goto if r.get("to") == "__end__"), None)
        assert end_rule is not None, "Missing goto __end__ rule"
        assert "completed" in end_rule.get("if", ""), "goto __end__ must check completed"

    def test_validate_input_goto_scrape_fallback(self, agent_def):
        """validate_input has fallback goto scrape_website."""
        node = _get_node(agent_def, "validate_input")
        goto = node["goto"]
        fallback = goto[-1]
        assert fallback.get("to") == "scrape_website"
        assert "if" not in fallback, "Fallback goto must be unconditional"

    def test_finalize_goto_end(self, agent_def):
        """finalize has goto __end__."""
        node = _get_node(agent_def, "finalize")
        assert node.get("goto") == "__end__"

    def test_no_legacy_edges_section(self, agent_def):
        """Agent uses goto (not deprecated edges section)."""
        assert "edges" not in agent_def, "Agent should use goto instead of deprecated edges"

    def test_has_save_payload_node(self, agent_def):
        """AC5: save_payload uses graphology.update_node with correct params."""
        node = _get_node(agent_def, "save_payload")
        assert node is not None, "Missing save_payload node"
        assert node["uses"] == "graphology.update_node"
        assert node["with"]["node_type"] == "LegalFirm"
        assert "websitePayload" in node["with"]["updates"]
        assert node["output"] == "update_result"

    def test_has_finalize_node(self, agent_def):
        """AC5: finalize node exists as a run: block."""
        node = _get_node(agent_def, "finalize")
        assert node is not None, "Missing finalize node"
        assert "run" in node, "finalize must be a run: block"


# =============================================================================
# validate_input node tests
# =============================================================================

class TestValidateInputNode:
    """Tests for the validate_input node."""

    def test_happy_path_valid_legalfirm(self, agent_def):
        """AC1: Valid LegalFirm with website returns website_url and firmName."""
        state = {
            "context_result": {
                "success": True,
                "node_type": "LegalFirm",
                "data": {
                    "id": "node-123",
                    "website": "https://example.com",
                    "firmName": "Test Law Firm",
                    "name": "Test Law Firm",
                },
            }
        }
        result = _exec_node(agent_def, "validate_input", state)
        assert result["website_url"] == "https://example.com"
        assert result["status"] == "success"
        assert result.get("completed") is not True

    def test_graphology_failure(self, agent_def):
        """AC4: Graphology fetch failure returns error."""
        state = {
            "context_result": {
                "success": False,
                "error": "Cannot connect to graphology server"
            }
        }
        result = _exec_node(agent_def, "validate_input", state)
        assert result["status"] == "error"
        assert "Cannot connect" in result["error"]
        assert result["completed"] is True

    def test_wrong_node_type(self, agent_def):
        """AC2: Wrong node type returns error."""
        state = {
            "context_result": {
                "success": True,
                "node_type": "Submission",
                "data": {"website": "https://example.com"},
            }
        }
        result = _exec_node(agent_def, "validate_input", state)
        assert result["status"] == "error"
        assert "Submission" in result["error"] or "LegalFirm" in result["error"]
        assert result["completed"] is True

    def test_missing_website(self, agent_def):
        """AC3: Missing website URL returns specific error message."""
        state = {
            "context_result": {
                "success": True,
                "node_type": "LegalFirm",
                "data": {"id": "node-123", "firmName": "No Website Firm"},
            }
        }
        result = _exec_node(agent_def, "validate_input", state)
        assert result["status"] == "error"
        assert "no website URL" in result["error"]
        assert result["completed"] is True

    def test_empty_website(self, agent_def):
        """AC3: Empty string website URL returns error."""
        state = {
            "context_result": {
                "success": True,
                "node_type": "LegalFirm",
                "data": {"id": "node-123", "website": "", "firmName": "Empty Website Firm"},
            }
        }
        result = _exec_node(agent_def, "validate_input", state)
        assert result["status"] == "error"
        assert "no website URL" in result["error"]
        assert result["completed"] is True

    def test_missing_context_result(self, agent_def):
        """Edge case: context_result entirely absent from state."""
        state = {}
        result = _exec_node(agent_def, "validate_input", state)
        assert result["status"] == "error"
        assert result["completed"] is True


# =============================================================================
# process_scrape_result node tests
# =============================================================================

class TestProcessScrapeResultNode:
    """Tests for the process_scrape_result node (AC #1, #3, #4)."""

    def test_happy_path_full_data(self, agent_def):
        """AC1: Successful scrape with full data returns scrape_data_json and status=success."""
        full_data = {
            "firmName": "Test Law Firm LLP",
            "foundedYear": "2005",
            "website": "https://testfirm.com",
            "linkedin": "https://linkedin.com/company/testfirm",
            "businessModel": "full-service",
            "description": "A leading full-service law firm.",
            "country": "USA",
            "city": "New York",
            "state": "US-NY",
            "totalLawyers": 150,
            "totalPartners": 30,
            "mainEmail": "info@testfirm.com",
            "mainPhone": "+1-555-0100",
            "managingPartners": [
                {"firstName": "Jane", "surname": "Doe", "role": "Managing Partner", "isManagingPartner": True}
            ],
            "offices": [
                {"city": "New York", "state": "US-NY", "country": "USA", "isHeadOffice": True}
            ],
            "practiceAreas": ["Corporate", "Litigation", "IP"],
            "awards": ["Chambers Top 100"],
        }
        state = {
            "scrape_result": {"success": True, "data": full_data, "url": "https://testfirm.com"}
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        assert result["status"] == "success"
        assert result["scrape_failed"] is False
        assert result.get("completed") is not True
        parsed = json.loads(result["scrape_data_json"])
        assert parsed["firmName"] == "Test Law Firm LLP"
        assert parsed["totalLawyers"] == 150
        assert len(parsed["practiceAreas"]) == 3

    def test_scrape_error_rate_limited(self, agent_def):
        """AC2: ScrapeGraphAI rate limit error serializes to scrape_data_json for persistence."""
        state = {
            "scrape_result": {"success": False, "error": "Rate limited"}
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        assert result["status"] == "error"
        assert result["scrape_failed"] is True
        assert result.get("completed") is not True  # Flows to save_payload
        payload = json.loads(result["scrape_data_json"])
        assert payload["status"] == "failed"
        assert "Rate limited" in payload["error"]

    def test_scrape_error_timeout(self, agent_def):
        """AC2: ScrapeGraphAI timeout error serialized for persistence."""
        state = {
            "scrape_result": {"success": False, "error": "Request timed out after 30s"}
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        assert result["status"] == "error"
        assert result["scrape_failed"] is True
        payload = json.loads(result["scrape_data_json"])
        assert "timed out" in payload["error"]

    def test_scrape_error_auth_failure(self, agent_def):
        """AC2: Auth failure serialized for persistence."""
        state = {
            "scrape_result": {"success": False, "error": "Authentication failed: invalid API key"}
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        assert result["status"] == "error"
        assert result["scrape_failed"] is True
        payload = json.loads(result["scrape_data_json"])
        assert "Authentication" in payload["error"]

    def test_scrape_error_no_error_message(self, agent_def):
        """AC2: Failure with no error message uses default in serialized payload."""
        state = {
            "scrape_result": {"success": False}
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        assert result["status"] == "error"
        assert result["scrape_failed"] is True
        payload = json.loads(result["scrape_data_json"])
        assert "ScrapeGraphAI scrape failed" in payload["error"]

    def test_partial_result_still_success(self, agent_def):
        """AC4: Partial result (only firmName) is still valid — status=success."""
        state = {
            "scrape_result": {"success": True, "data": {"firmName": "Partial Firm"}}
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        assert result["status"] == "success"
        assert result.get("completed") is not True
        parsed = json.loads(result["scrape_data_json"])
        assert parsed["firmName"] == "Partial Firm"
        assert "totalLawyers" not in parsed  # Partial — missing fields are absent

    def test_empty_data_still_success(self, agent_def):
        """AC4: Empty data dict is still valid — status=success."""
        state = {
            "scrape_result": {"success": True, "data": {}}
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        assert result["status"] == "success"
        assert result.get("completed") is not True
        parsed = json.loads(result["scrape_data_json"])
        assert parsed == {}

    def test_scrape_data_json_is_valid_json(self, agent_def):
        """Task 5.4: scrape_data_json contains valid JSON string after successful scrape."""
        state = {
            "scrape_result": {
                "success": True,
                "data": {"firmName": "JSON Test", "offices": [{"city": "SP"}]}
            }
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        raw_json = result["scrape_data_json"]
        assert isinstance(raw_json, str)
        parsed = json.loads(raw_json)  # Must not raise
        assert parsed["firmName"] == "JSON Test"
        assert parsed["offices"][0]["city"] == "SP"

    def test_missing_data_key_uses_empty_dict(self, agent_def):
        """AC4: If success=True but no 'data' key, defaults to empty dict."""
        state = {
            "scrape_result": {"success": True}
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        assert result["status"] == "success"
        parsed = json.loads(result["scrape_data_json"])
        assert parsed == {}


# =============================================================================
# finalize node tests (Story 1.3 — AC #1, #3, #4)
# =============================================================================

class TestFinalizeNode:
    """Tests for the finalize node."""

    def test_happy_path_success(self, agent_def):
        """AC1/AC4: Successful scrape + successful save → status=success, completed=True."""
        state = {
            "update_result": {"success": True},
            "scrape_failed": False,
        }
        result = _exec_node(agent_def, "finalize", state)
        assert result["completed"] is True
        assert result["status"] == "success"

    def test_save_failure(self, agent_def):
        """AC3: GraphQL mutation failure → status=error with error message."""
        state = {
            "update_result": {"success": False, "error": "Connection refused"},
            "scrape_failed": False,
        }
        result = _exec_node(agent_def, "finalize", state)
        assert result["completed"] is True
        assert result["status"] == "error"
        assert "Connection refused" in result["error"]

    def test_save_failure_default_message(self, agent_def):
        """AC3: GraphQL failure with no error message uses default."""
        state = {
            "update_result": {"success": False},
            "scrape_failed": False,
        }
        result = _exec_node(agent_def, "finalize", state)
        assert result["completed"] is True
        assert result["status"] == "error"
        assert "Failed to save payload" in result["error"]

    def test_scrape_failed_but_save_succeeded(self, agent_def):
        """AC2/Task 6.4: Scrape error persisted successfully → status=error."""
        state = {
            "update_result": {"success": True},
            "scrape_failed": True,
        }
        result = _exec_node(agent_def, "finalize", state)
        assert result["completed"] is True
        assert result["status"] == "error"
        assert "Scrape failed" in result["error"]

    def test_scrape_failed_and_save_failed(self, agent_def):
        """AC3: Both scrape and save failed → status=error with save error."""
        state = {
            "update_result": {"success": False, "error": "Network timeout"},
            "scrape_failed": True,
        }
        result = _exec_node(agent_def, "finalize", state)
        assert result["completed"] is True
        assert result["status"] == "error"
        assert "Network timeout" in result["error"]

    def test_missing_update_result(self, agent_def):
        """AC3: Missing update_result defaults to failure."""
        state = {"scrape_failed": False}
        result = _exec_node(agent_def, "finalize", state)
        assert result["completed"] is True
        assert result["status"] == "error"
        assert "Failed to save payload" in result["error"]

    def test_finalize_checks_update_result_success(self, agent_def):
        """AC5/Task 6.7: finalize node checks update_result.success."""
        node = _get_node(agent_def, "finalize")
        code = node["run"]
        assert "update_result" in code
        assert 'get("success")' in code


# =============================================================================
# Error payload persistence tests (Story 1.3 — AC #2)
# =============================================================================

class TestErrorPayloadPersistence:
    """AC2: Error scrape results are serialized for persistence."""

    def test_error_payload_json_structure(self, agent_def):
        """AC2/Task 6.3: scrape_data_json contains error + status=failed."""
        state = {
            "scrape_result": {"success": False, "error": "Server returned 503"}
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        payload = json.loads(result["scrape_data_json"])
        assert payload["status"] == "failed"
        assert payload["error"] == "Server returned 503"

    def test_error_payload_is_valid_json(self, agent_def):
        """AC2: Error payload is valid JSON string."""
        state = {
            "scrape_result": {"success": False, "error": "Special chars: é à ü"}
        }
        result = _exec_node(agent_def, "process_scrape_result", state)
        raw = result["scrape_data_json"]
        assert isinstance(raw, str)
        parsed = json.loads(raw)  # Must not raise
        assert "error" in parsed


# =============================================================================
# save_payload YAML structure tests (Story 1.3 — AC #5)
# =============================================================================

class TestSavePayloadStructure:
    """AC5: save_payload node matches file_extraction.yaml patterns."""

    def test_uses_graphology_update_node(self, agent_def):
        """AC5/Task 6.5: save_payload uses graphology.update_node."""
        node = _get_node(agent_def, "save_payload")
        assert node["uses"] == "graphology.update_node"

    def test_node_type_is_legalfirm(self, agent_def):
        """AC5: node_type is hardcoded to LegalFirm."""
        node = _get_node(agent_def, "save_payload")
        assert node["with"]["node_type"] == "LegalFirm"

    def test_updates_website_payload(self, agent_def):
        """AC5: updates websitePayload from state.scrape_data_json."""
        node = _get_node(agent_def, "save_payload")
        updates = node["with"]["updates"]
        assert "websitePayload" in updates
        assert "scrape_data_json" in updates["websitePayload"]

    def test_node_id_from_state(self, agent_def):
        """AC5: node_id comes from state.context_node_id."""
        node = _get_node(agent_def, "save_payload")
        assert "context_node_id" in node["with"]["node_id"]

    def test_graphql_url_from_variables(self, agent_def):
        """AC5: graphql_url comes from variables.GRAPHOLOGY_URL."""
        node = _get_node(agent_def, "save_payload")
        assert "GRAPHOLOGY_URL" in node["with"]["graphql_url"]

    def test_output_is_update_result(self, agent_def):
        """AC5: output stored in update_result."""
        node = _get_node(agent_def, "save_payload")
        assert node["output"] == "update_result"


# =============================================================================
# Edge wiring tests (Story 1.3 — AC #5, Task 6.6)
# =============================================================================

class TestFlowWiringStory13:
    """Task 6.6: Verify flow wiring after story 1.3 (uses goto + implicit chaining)."""

    def test_process_scrape_result_no_goto_to_end(self, agent_def):
        """Task 6.6: process_scrape_result should NOT route to __end__."""
        node = _get_node(agent_def, "process_scrape_result")
        assert node.get("goto") is None, "process_scrape_result should use implicit chaining to save_payload"

    def test_validate_input_error_routes_to_end(self, agent_def):
        """Task 5.5: validate_input error → __end__ via goto."""
        node = _get_node(agent_def, "validate_input")
        goto = node.get("goto")
        assert isinstance(goto, list)
        assert any(r.get("to") == "__end__" for r in goto)

    def test_full_flow_order(self, agent_def):
        """Verify complete flow order via implicit chaining + goto."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        expected = ["fetch_node", "validate_input", "scrape_website",
                     "process_scrape_result", "save_payload", "finalize"]
        assert node_names == expected
        # finalize explicitly goes to __end__
        finalize = _get_node(agent_def, "finalize")
        assert finalize.get("goto") == "__end__"
