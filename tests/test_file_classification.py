"""
Tests for file_classification YAML agent logic (TIER 3 LLM classification).

Tests the inline Python code from agents/file_classification.yaml by executing
the node run blocks directly with mocked dependencies. This validates:
- Parse result from LLM response (AC2, AC3)
- ISO country/state resolution (AC5)
- Confidence threshold validation (AC4)
- Known directory mode (AC6)
- Content hash caching (AC7)
- LLM configuration (AC9)

Also tests the actions/agents.py cache actions directly.

Usage:
    pytest tests/test_file_classification.py -v
"""

import json
import os
import sys
import textwrap

import pytest
import yaml


# =============================================================================
# HELPERS — Load and execute YAML agent node run blocks
# =============================================================================

def _load_agent_yaml():
    """Load the file_classification YAML agent definition."""
    agent_path = os.path.join(os.path.dirname(__file__), "..", "agents", "file_classification.yaml")
    with open(agent_path) as f:
        return yaml.safe_load(f)


def _get_node_code(agent_def, node_name):
    """Extract the `run` code block from a named node."""
    for node in agent_def["nodes"]:
        if node["name"] == node_name:
            return node.get("run", "")
    raise ValueError(f"Node '{node_name}' not found in agent definition")


def _exec_node(agent_def, node_name, state, settings=None, mock_modules=None):
    """Execute a node's run block with given state and return the result."""
    code = _get_node_code(agent_def, node_name)
    if not code:
        raise ValueError(f"Node '{node_name}' has no run block")

    settings = settings or agent_def.get("settings", {})
    mock_modules = mock_modules or {}

    namespace = {"state": state, "settings": settings}

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

        namespace["__builtins__"] = {**__builtins__.__dict__, "__import__": patched_import} if hasattr(__builtins__, '__dict__') else {**__builtins__, "__import__": patched_import}

    exec(compile(wrapped, f"<agent:{node_name}>", "exec"), namespace)
    return namespace.get("_result")


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def agent_def():
    """Load the file_classification YAML agent."""
    return _load_agent_yaml()


# =============================================================================
# parse_result node tests
# =============================================================================

class TestParseResult:
    """Tests for the parse_result node — LLM response parsing."""

    def test_chambers_classification(self, agent_def):
        """AC2: Chambers submission text → correct classification."""
        state = {
            "llm_result": {
                "success": True,
                "result": {
                    "content": '{"directory": "chambers", "category": "Corporate/M&A", "region_country": "BRA", "region_state": "BR-SP", "confidence": 0.95, "year": 2025, "is_empty_form": false}'
                },
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        assert result["directory"] == "chambers"
        assert result["category"] == "Corporate/M&A"
        assert result["region_country"] == "BRA"
        assert result["region_state"] == "BR-SP"
        assert result["confidence"] == 0.95
        assert result["year"] == "2025"
        assert result["is_empty_form"] is False

    def test_iflr1000_classification(self, agent_def):
        """AC2: IFLR1000 text → correct classification."""
        state = {
            "llm_result": {
                "success": True,
                "result": {
                    "content": '{"directory": "iflr1000", "category": "Banking", "region_country": "GBR", "region_state": "", "confidence": 0.88, "year": 2024, "is_empty_form": false}'
                },
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        assert result["directory"] == "iflr1000"
        assert result["category"] == "Banking"
        assert result["confidence"] == 0.88

    def test_known_directory_mode(self, agent_def):
        """AC6: Known directory mode uses pre-set directory, extracts category/region/year."""
        state = {
            "llm_result": {
                "success": True,
                "result": {
                    "content": '{"category": "Tax", "region_country": "USA", "region_state": "US-NY", "confidence": 0.92, "year": 2025, "is_empty_form": false}'
                },
            },
            "detected_directory": "itr",
        }
        result = _exec_node(agent_def, "parse_result", state)
        # When detected_directory is set, directory comes from it, not LLM
        assert result["directory"] == "itr"
        assert result["category"] == "Tax"
        assert result["year"] == "2025"

    def test_iso_country_resolution(self, agent_def):
        """AC5: ISO 3166-1 alpha-3 'BRA' → 'Brazil'."""
        state = {
            "llm_result": {
                "success": True,
                "result": {
                    "content": '{"directory": "chambers", "category": "Corporate", "region_country": "BRA", "region_state": "", "confidence": 0.9, "year": 2025, "is_empty_form": false}'
                },
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        assert result["region_country_display"] == "Brazil"

    def test_iso_state_resolution(self, agent_def):
        """AC5: ISO 3166-2 'BR-SP' → 'São Paulo'."""
        state = {
            "llm_result": {
                "success": True,
                "result": {
                    "content": '{"directory": "chambers", "category": "Corporate", "region_country": "BRA", "region_state": "BR-SP", "confidence": 0.9, "year": 2025, "is_empty_form": false}'
                },
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        assert result["region_country_display"] == "Brazil"
        assert result["region_state_display"] == "São Paulo"

    def test_unknown_iso_code_fallback(self, agent_def):
        """AC5: Unknown ISO code falls back to raw code as display name."""
        state = {
            "llm_result": {
                "success": True,
                "result": {
                    "content": '{"directory": "chambers", "category": "Corporate", "region_country": "XYZ", "region_state": "", "confidence": 0.9, "year": 2025, "is_empty_form": false}'
                },
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        # Unknown code → raw code as display name
        assert result["region_country_display"] == "XYZ"

    def test_llm_failure_surfaces_error(self, agent_def):
        """LLM call failure → error status."""
        state = {
            "llm_result": {
                "success": False,
                "error": "Rate limit exceeded",
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        assert result["status"] == "error"
        assert "Rate limit" in result["error"]

    def test_no_json_in_response(self, agent_def):
        """No JSON in LLM response → error."""
        state = {
            "llm_result": {
                "success": True,
                "result": {"content": "I cannot classify this document."},
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        assert result["status"] == "error"
        assert "No JSON found" in result["error"]

    def test_invalid_json_in_response(self, agent_def):
        """Invalid JSON in LLM response → error."""
        state = {
            "llm_result": {
                "success": True,
                "result": {"content": '{"directory": "chambers", broken}'},
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        assert result["status"] == "error"
        assert "Invalid JSON" in result["error"]

    def test_json_in_markdown_code_block(self, agent_def):
        """JSON wrapped in markdown code block is still parsed."""
        state = {
            "llm_result": {
                "success": True,
                "result": {
                    "content": '```json\n{"directory": "legal500", "category": "IP", "region_country": "GBR", "region_state": "", "confidence": 0.85, "year": 2024, "is_empty_form": false}\n```'
                },
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        assert result["directory"] == "legal500"
        assert result["category"] == "IP"

    def test_null_year_returns_empty_string(self, agent_def):
        """Year=null from LLM → empty string in result."""
        state = {
            "llm_result": {
                "success": True,
                "result": {
                    "content": '{"directory": "chambers", "category": "Lit", "region_country": "USA", "region_state": "", "confidence": 0.8, "year": null, "is_empty_form": false}'
                },
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        assert result["year"] == ""

    def test_empty_form_detection(self, agent_def):
        """is_empty_form=true from LLM → True in result."""
        state = {
            "llm_result": {
                "success": True,
                "result": {
                    "content": '{"directory": "chambers", "category": "", "region_country": "", "region_state": "", "confidence": 0.75, "year": null, "is_empty_form": true}'
                },
            },
            "detected_directory": "",
        }
        result = _exec_node(agent_def, "parse_result", state)
        assert result["is_empty_form"] is True


# =============================================================================
# validate_result node tests
# =============================================================================

class TestValidateResult:
    """Tests for the validate_result node — confidence/directory validation."""

    def test_valid_classification_succeeds(self, agent_def):
        """Valid directory + confidence >= 0.5 → success."""
        state = {"directory": "chambers", "confidence": 0.8, "status": ""}
        result = _exec_node(agent_def, "validate_result", state)
        assert result["status"] == "success"

    def test_low_confidence_errors(self, agent_def):
        """AC4: Confidence < 0.5 → error with supported list."""
        state = {"directory": "chambers", "confidence": 0.3, "status": ""}
        result = _exec_node(agent_def, "validate_result", state)
        assert result["status"] == "error"
        assert "inconclusive" in result["error"]
        assert "chambers" in result["error"]
        assert "iflr1000" in result["error"]

    def test_null_directory_errors(self, agent_def):
        """AC4: No directory → error."""
        state = {"directory": "", "confidence": 0.9, "status": ""}
        result = _exec_node(agent_def, "validate_result", state)
        assert result["status"] == "error"
        assert "inconclusive" in result["error"]

    def test_unsupported_directory_errors(self, agent_def):
        """Unsupported directory slug → error."""
        state = {"directory": "unknown_dir", "confidence": 0.9, "status": ""}
        result = _exec_node(agent_def, "validate_result", state)
        assert result["status"] == "error"
        assert "unsupported" in result["error"]
        assert "unknown_dir" in result["error"]

    def test_all_five_directories_accepted(self, agent_def):
        """AC3: All 5 directories are valid."""
        for directory in ["chambers", "iflr1000", "legal500", "itr", "leadersleague"]:
            state = {"directory": directory, "confidence": 0.8, "status": ""}
            result = _exec_node(agent_def, "validate_result", state)
            assert result["status"] == "success", f"Failed for {directory}"

    def test_error_status_propagated(self, agent_def):
        """Pre-existing error status from parse_result is propagated."""
        state = {"directory": "chambers", "confidence": 0.8, "status": "error", "error": "LLM failed"}
        result = _exec_node(agent_def, "validate_result", state)
        # Returns empty dict, error status already set
        assert result == {}

    def test_boundary_confidence_exactly_0_5(self, agent_def):
        """Confidence exactly 0.5 should succeed (>= 0.5)."""
        state = {"directory": "chambers", "confidence": 0.5, "status": ""}
        result = _exec_node(agent_def, "validate_result", state)
        assert result["status"] == "success"

    def test_boundary_confidence_0_49(self, agent_def):
        """Confidence 0.49 should fail (< 0.5)."""
        state = {"directory": "chambers", "confidence": 0.49, "status": ""}
        result = _exec_node(agent_def, "validate_result", state)
        assert result["status"] == "error"


# =============================================================================
# apply_cache node tests
# =============================================================================

class TestApplyCache:
    """Tests for the apply_cache node — extracting cached results."""

    def test_applies_cached_fields(self, agent_def):
        """Cached result fields are extracted to state."""
        state = {
            "cache_result": {
                "hit": True,
                "directory": "chambers",
                "category": "Corporate/M&A",
                "region_country": "BRA",
                "region_state": "BR-SP",
                "region_country_display": "Brazil",
                "region_state_display": "São Paulo",
                "confidence": 0.95,
                "year": "2025",
                "is_empty_form": False,
                "status": "success",
            },
        }
        result = _exec_node(agent_def, "apply_cache", state)
        assert result["directory"] == "chambers"
        assert result["category"] == "Corporate/M&A"
        assert result["region_country_display"] == "Brazil"
        assert result["status"] == "success"


# =============================================================================
# Cache action tests (actions/agents.py)
# =============================================================================

# Add actions dir to path so we can import it
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))


class TestCacheActions:
    """Tests for agents.cache_get and agents.cache_set actions."""

    def setup_method(self):
        """Clear cache before each test."""
        from agents import _classification_cache, _classification_cache_ts
        _classification_cache.clear()
        _classification_cache_ts.clear()

    def test_cache_miss_returns_no_hit(self):
        """Cache miss returns hit=False."""
        from agents import cache_get_classification
        result = cache_get_classification({}, document_text="some text")
        assert result["hit"] is False

    def test_cache_set_and_get(self):
        """AC7: Same text → cached result returned."""
        from agents import cache_get_classification, cache_set_classification

        text = "Chambers and Partners Band 1 ranking for Corporate/M&A in Brazil"
        state = {
            "status": "success",
            "directory": "chambers",
            "category": "Corporate/M&A",
            "region_country": "BRA",
            "region_state": "BR-SP",
            "region_country_display": "Brazil",
            "region_state_display": "São Paulo",
            "confidence": 0.95,
            "year": "2025",
            "is_empty_form": False,
        }

        # Store
        set_result = cache_set_classification(state, document_text=text)
        assert set_result["cached"] is True

        # Retrieve
        get_result = cache_get_classification({}, document_text=text)
        assert get_result["hit"] is True
        assert get_result["directory"] == "chambers"
        assert get_result["confidence"] == 0.95

    def test_cache_not_stored_on_error(self):
        """Cache does not store error results."""
        from agents import cache_get_classification, cache_set_classification

        text = "unknown document"
        state = {"status": "error", "error": "inconclusive"}

        set_result = cache_set_classification(state, document_text=text)
        assert set_result["cached"] is False

        get_result = cache_get_classification({}, document_text=text)
        assert get_result["hit"] is False

    def test_cache_empty_text_returns_miss(self):
        """Empty text → cache miss."""
        from agents import cache_get_classification
        result = cache_get_classification({}, document_text="")
        assert result["hit"] is False

    def test_cache_different_text_different_key(self):
        """Different text → different cache key, miss."""
        from agents import cache_get_classification, cache_set_classification

        state = {
            "status": "success",
            "directory": "chambers",
            "category": "Corporate",
            "region_country": "BRA",
            "region_state": "",
            "region_country_display": "Brazil",
            "region_state_display": "",
            "confidence": 0.9,
            "year": "2025",
            "is_empty_form": False,
        }

        cache_set_classification(state, document_text="text A")
        get_result = cache_get_classification({}, document_text="text B")
        assert get_result["hit"] is False


# =============================================================================
# Agent YAML structure tests
# =============================================================================

class TestAgentYAMLStructure:
    """Tests for YAML agent structure and configuration."""

    def test_llm_temperature(self, agent_def):
        """AC9: Temperature is 0.1."""
        assert agent_def["settings"]["llm"]["temperature"] == 0.1

    def test_llm_max_tokens(self, agent_def):
        """AC9: Max tokens is 200."""
        assert agent_def["settings"]["llm"]["max_tokens"] == 200

    def test_state_schema_has_required_fields(self, agent_def):
        """State schema includes all required input/output fields."""
        schema = agent_def["state_schema"]
        required_fields = [
            "document_text", "detected_directory",
            "directory", "category", "region_country", "region_state",
            "region_country_display", "region_state_display",
            "confidence", "year", "is_empty_form", "status", "error",
        ]
        for field in required_fields:
            assert field in schema, f"Missing field: {field}"

    def test_prompt_covers_all_5_directories(self, agent_def):
        """AC3: System prompt includes indicators for all 5 directories."""
        classify_node = None
        for node in agent_def["nodes"]:
            if node["name"] == "classify_document":
                classify_node = node
                break
        assert classify_node is not None

        # Get the system prompt content
        messages = classify_node["with"]["args"]["messages"]
        system_msg = messages[0]["content"]

        for directory in ["chambers", "iflr1000", "legal500", "itr", "leadersleague"]:
            assert directory in system_msg, f"Prompt missing directory: {directory}"

        # Check specific indicators per AC3
        for indicator in ["Band", "PAB006", "PAM006", "Market Leader", "accreditation.euromoney.com",
                          "Tier", "Hall of Fame", "World Tax", "itrworldtax.com",
                          "Décideurs", "Peer Feedback"]:
            assert indicator in system_msg, f"Prompt missing indicator: {indicator}"

    def test_flow_has_cache_nodes(self, agent_def):
        """AC7: Agent has check_cache and store_cache nodes."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        assert "check_cache" in node_names
        assert "store_cache" in node_names
        assert "apply_cache" in node_names
