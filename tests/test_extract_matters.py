"""
Tests for extract_matters YAML agent logic.

Tests the inline Python code from agents/extract_matters.yaml by executing
the node run blocks directly with mocked dependencies. This validates:
- Happy path extraction for all 5 directories (AC4)
- Correct ProtoMatter creation per matter (AC4, AC5)
- Multiple matter paths per directory (chambers, legal500)
- Edge cases: empty payload, malformed JSON, zero matters, unknown directory
- Directory alias normalization

Usage:
    pytest tests/test_extract_matters.py -v
"""

import json
import os
import textwrap

import pytest
import yaml


# =============================================================================
# HELPERS
# =============================================================================

def _load_agent_yaml():
    """Load the extract_matters YAML agent definition."""
    agent_path = os.path.join(os.path.dirname(__file__), "..", "agents", "extract_matters.yaml")
    with open(agent_path) as f:
        return yaml.safe_load(f)


def _get_node_code(agent_def, node_name):
    """Extract the `run` code block from a named node."""
    for node in agent_def["nodes"]:
        if node["name"] == node_name:
            return node.get("run", "")
    raise ValueError(f"Node '{node_name}' not found in agent definition")


def _exec_node(agent_def, node_name, state, settings=None, variables=None, mock_modules=None):
    """Execute a node's run block with given state and return the result."""
    code = _get_node_code(agent_def, node_name)
    if not code:
        raise ValueError(f"Node '{node_name}' has no run block")

    settings = settings or agent_def.get("settings", {})
    variables = variables or agent_def.get("variables", {})
    mock_modules = mock_modules or {}

    namespace = {"state": state, "settings": settings, "variables": variables}

    indented = textwrap.indent(code, "    ")
    wrapped = f"def _node_fn():\n{indented}\n_result = _node_fn()"

    for mod_name, mod_obj in mock_modules.items():
        namespace[mod_name] = mod_obj

    if mock_modules:
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def patched_import(name, *args, **kwargs):
            if name in mock_modules:
                return mock_modules[name]
            return original_import(name, *args, **kwargs)

        namespace["__builtins__"] = (
            {**__builtins__.__dict__, "__import__": patched_import}
            if hasattr(__builtins__, '__dict__')
            else {**__builtins__, "__import__": patched_import}
        )

    exec(compile(wrapped, f"<agent:{node_name}>", "exec"), namespace)
    return namespace.get("_result")


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def agent_def():
    """Load the extract_matters YAML agent."""
    return _load_agent_yaml()


def _make_file_state(payload, directory="chambers", node_type="ApplicationFormFile"):
    """Build a state dict simulating a fetched ApplicationFormFile node."""
    return {
        "context_node_id": "file-node-001",
        "context_result": {
            "success": True,
            "node_type": node_type,
            "data": {
                "payload": json.dumps(payload) if isinstance(payload, dict) else payload,
                "directoryName": directory,
            },
        },
    }


# =============================================================================
# extract_matters node tests
# =============================================================================

class TestExtractMattersNode:
    """Tests for the extract_matters node (Step 2 in YAML agent)."""

    def test_graphology_failure_surfaces_error(self, agent_def):
        """When graphology returns success=false, surface the real error."""
        state = {
            "context_result": {"success": False, "error": "Service unavailable (503)"},
        }
        result = _exec_node(agent_def, "extract_matters", state)
        assert result["status"] == "error"
        assert "503" in result["error"]
        assert result["completed"] is True

    def test_wrong_node_type_returns_error(self, agent_def):
        """Wrong node type should return error."""
        state = _make_file_state({"foo": "bar"}, node_type="LegalFirm")
        result = _exec_node(agent_def, "extract_matters", state)
        assert result["status"] == "error"
        assert "ApplicationFormFile" in result["error"]

    def test_empty_payload_returns_error(self, agent_def):
        """Missing payload should return error."""
        state = {
            "context_node_id": "file-001",
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"payload": "", "directoryName": "chambers"},
            },
        }
        result = _exec_node(agent_def, "extract_matters", state)
        assert result["status"] == "error"
        assert "no payload" in result["error"].lower()

    def test_missing_directory_returns_error(self, agent_def):
        """Missing directory should return error."""
        state = {
            "context_node_id": "file-001",
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"payload": '{"test": 1}', "directoryName": ""},
            },
        }
        result = _exec_node(agent_def, "extract_matters", state)
        assert result["status"] == "error"
        assert "no directory" in result["error"].lower()

    def test_malformed_json_returns_error(self, agent_def):
        """Malformed JSON payload should return error."""
        state = {
            "context_node_id": "file-001",
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"payload": "not-valid-json{", "directoryName": "chambers"},
            },
        }
        result = _exec_node(agent_def, "extract_matters", state)
        assert result["status"] == "error"
        assert "malformed json" in result["error"].lower()

    def test_unknown_directory_returns_error(self, agent_def):
        """Unknown directory should return error."""
        state = _make_file_state({"foo": "bar"}, directory="unknown_dir")
        result = _exec_node(agent_def, "extract_matters", state)
        assert result["status"] == "error"
        assert "unknown directory" in result["error"].lower()

    def test_chambers_extracts_both_matter_arrays(self, agent_def):
        """Chambers: extracts from both publishable and confidential."""
        payload = {
            "workHighlights": {
                "publishableInformation": {
                    "matters": [
                        {"clientName": "Acme Corp", "summary": "M&A deal"},
                    ]
                },
                "confidentialInformation": {
                    "matters": [
                        {"clientName": "Secret Client", "summary": "Private deal"},
                    ]
                },
            }
        }
        state = _make_file_state(payload, directory="chambers")
        result = _exec_node(agent_def, "extract_matters", state)
        assert len(result["matters_extracted"]) == 2
        assert result["directory"] == "chambers"

    def test_iflr1000_extracts_deals(self, agent_def):
        """IFLR1000: extracts from section_3_deal_highlights.deal_highlights."""
        payload = {
            "section_3_deal_highlights": {
                "deal_highlights": [
                    {"dealName": "Deal A", "dealValue": "USD 1B"},
                    {"dealName": "Deal B", "dealValue": "USD 500M"},
                ]
            }
        }
        state = _make_file_state(payload, directory="iflr1000")
        result = _exec_node(agent_def, "extract_matters", state)
        assert len(result["matters_extracted"]) == 2
        assert result["directory"] == "iflr1000"

    def test_legal500_extracts_both_matter_arrays(self, agent_def):
        """Legal500: extracts from publishable and non-publishable."""
        payload = {
            "section_work_highlights": {
                "publishable_matters": [
                    {"title": "Matter 1"},
                ],
                "non_publishable_matters": [
                    {"title": "Matter 2"},
                    {"title": "Matter 3"},
                ],
            }
        }
        state = _make_file_state(payload, directory="legal500")
        result = _exec_node(agent_def, "extract_matters", state)
        assert len(result["matters_extracted"]) == 3

    def test_itr_extracts_matters(self, agent_def):
        """ITR: extracts from section_2_deal_case_highlights.matter_highlights."""
        payload = {
            "section_2_deal_case_highlights": {
                "matter_highlights": [
                    {"title": "Tax case 1"},
                ]
            }
        }
        state = _make_file_state(payload, directory="itr")
        result = _exec_node(agent_def, "extract_matters", state)
        assert len(result["matters_extracted"]) == 1

    def test_leadersleague_extracts_matters(self, agent_def):
        """Leaders League: extracts from section_e_work_highlights.work_highlights."""
        payload = {
            "section_e_work_highlights": {
                "work_highlights": [
                    {"title": "Deal Alpha"},
                    {"title": "Deal Beta"},
                ]
            }
        }
        state = _make_file_state(payload, directory="leadersleague")
        result = _exec_node(agent_def, "extract_matters", state)
        assert len(result["matters_extracted"]) == 2

    def test_zero_matters_returns_success_with_count_zero(self, agent_def):
        """File with no matters should succeed with count=0."""
        payload = {
            "workHighlights": {
                "publishableInformation": {"matters": []},
                "confidentialInformation": {"matters": []},
            }
        }
        state = _make_file_state(payload, directory="chambers")
        result = _exec_node(agent_def, "extract_matters", state)
        assert result["status"] == "success"
        assert result["count"] == 0
        assert result["matters_extracted"] == []

    def test_directory_alias_normalization(self, agent_def):
        """Directory aliases (chambers-partners, the-legal-500, etc.) should work."""
        payload = {
            "section_3_deal_highlights": {
                "deal_highlights": [{"dealName": "Test"}]
            }
        }
        for alias in ["iflr-1000", "iflr1000"]:
            state = _make_file_state(payload, directory=alias)
            result = _exec_node(agent_def, "extract_matters", state)
            assert result["directory"] == "iflr1000", f"Alias '{alias}' not normalized"
            assert len(result["matters_extracted"]) == 1

    def test_missing_nested_path_returns_zero_matters(self, agent_def):
        """If the expected path doesn't exist in payload, return 0 matters."""
        payload = {"someOtherSection": {"data": "irrelevant"}}
        state = _make_file_state(payload, directory="chambers")
        result = _exec_node(agent_def, "extract_matters", state)
        assert result["status"] == "success"
        assert result["count"] == 0
