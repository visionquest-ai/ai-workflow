"""
Unit tests for llm_prompt agent (Story 18.1).

Tests:
- Task 4: agents.llm_prompt registration and invoke_llm_prompt wrapper
- Task 6: build_request and parse_result node logic

Usage:
    pytest tests/test_llm_prompt.py -v
"""

import json
import os
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest
import yaml


# =============================================================================
# HELPERS — Load and execute YAML agent node run blocks
# =============================================================================

def _load_agent_yaml():
    """Load the llm_prompt YAML agent definition."""
    agent_path = os.path.join(os.path.dirname(__file__), "..", "agents", "llm_prompt.yaml")
    with open(agent_path) as f:
        return yaml.safe_load(f)


def _get_node_code(agent_def, node_name):
    """Extract the `run` code block from a named node."""
    for node in agent_def["nodes"]:
        if node["name"] == node_name:
            return node.get("run", "")
    raise ValueError(f"Node '{node_name}' not found in agent definition")


def _exec_node(agent_def, node_name, state):
    """Execute a node's run block with given state and return the result."""
    code = _get_node_code(agent_def, node_name)
    if not code:
        raise ValueError(f"Node '{node_name}' has no run block")

    settings = agent_def.get("settings", {})
    namespace = {"state": state, "settings": settings}

    indented = textwrap.indent(code, "    ")
    wrapped = f"def _node_fn():\n{indented}\n_result = _node_fn()"

    exec(wrapped, namespace)
    return namespace.get("_result")


# =============================================================================
# TASK 4 TESTS — Registration and invoke_llm_prompt wrapper
# =============================================================================

class TestAgentRegistration:
    """Test that agents.llm_prompt is registered in the action registry."""

    def test_llm_prompt_registered(self):
        """AC6: agents.llm_prompt is registered in the action registry."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
        from agents import register_actions, invoke_llm_prompt

        registry = {}
        engine = MagicMock()
        register_actions(registry, engine)

        assert "agents.llm_prompt" in registry
        assert registry["agents.llm_prompt"] is invoke_llm_prompt

    def test_invoke_agent_still_registered(self):
        """Verify existing registrations are preserved."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
        from agents import register_actions

        registry = {}
        engine = MagicMock()
        register_actions(registry, engine)

        assert "agents.invoke_agent" in registry
        assert "agents.cache_get" in registry
        assert "agents.cache_set" in registry


class TestInvokeLlmPrompt:
    """Test the invoke_llm_prompt wrapper function (Task 4.2 — input/output mapping)."""

    @patch("agents.invoke_agent")
    def test_structured_mode_maps_inputs(self, mock_invoke):
        """AC6: system_prompt, user_message, output_schema mapped to sub-agent input_state."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
        from agents import invoke_llm_prompt

        mock_invoke.return_value = {
            "success": True,
            "result": {"result": {"name": "Acme"}, "status": "success", "error": ""},
        }

        schema = {"title": "company", "type": "object", "properties": {"name": {"type": "string"}}}
        result = invoke_llm_prompt(
            state={},
            system_prompt="You are a helper.",
            user_message="Extract company name.",
            output_schema=schema,
        )

        # Verify invoke_agent was called with correct input_state
        call_kwargs = mock_invoke.call_args
        assert call_kwargs[1]["agent_name"] == "llm_prompt" or call_kwargs[0][1] == "llm_prompt"
        input_state = call_kwargs[1].get("input_state") or call_kwargs[0][2]
        assert input_state["system_prompt"] == "You are a helper."
        assert input_state["user_message"] == "Extract company name."
        assert input_state["output_schema"] == schema

        # Verify output mapping
        assert result["result"] == {"name": "Acme"}
        assert result["status"] == "success"

    @patch("agents.invoke_agent")
    def test_plain_mode_omits_output_schema(self, mock_invoke):
        """AC6: When output_schema is None, it is not passed to input_state."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
        from agents import invoke_llm_prompt

        mock_invoke.return_value = {
            "success": True,
            "result": {"result": "Hello world", "status": "success", "error": ""},
        }

        invoke_llm_prompt(
            state={},
            system_prompt="You are a helper.",
            user_message="Say hello.",
        )

        call_kwargs = mock_invoke.call_args
        input_state = call_kwargs[1].get("input_state") or call_kwargs[0][2]
        assert "output_schema" not in input_state

    @patch("agents.invoke_agent")
    def test_invocation_failure_returns_error(self, mock_invoke):
        """AC4: When sub-agent invocation fails, error is propagated."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
        from agents import invoke_llm_prompt

        mock_invoke.return_value = {
            "success": False,
            "error": "Sub-agent not found: llm_prompt",
        }

        result = invoke_llm_prompt(
            state={},
            system_prompt="test",
            user_message="test",
        )

        assert result["status"] == "error"
        assert "not found" in result["error"]

    @patch("agents.invoke_agent")
    def test_output_fields_mapped_from_agent_state(self, mock_invoke):
        """AC6: result, status, error extracted from sub-agent state."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
        from agents import invoke_llm_prompt

        mock_invoke.return_value = {
            "success": True,
            "result": {
                "result": "plain text output",
                "status": "success",
                "error": "",
            },
        }

        result = invoke_llm_prompt(
            state={},
            system_prompt="sys",
            user_message="usr",
        )

        assert result["result"] == "plain text output"
        assert result["status"] == "success"
        assert result["error"] == ""


# =============================================================================
# TASK 6 TESTS — build_request and parse_result node logic
# =============================================================================

@pytest.fixture
def agent_def():
    return _load_agent_yaml()


class TestBuildRequestNode:
    """Test build_request node inline code."""

    def test_with_output_schema_sets_structured(self, agent_def):
        """AC1/6.1: With output_schema → use_structured=True, response_format built."""
        schema = {
            "title": "company_info",
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        state = {
            "system_prompt": "You extract data.",
            "user_message": "Extract company name from: Acme Corp",
            "output_schema": schema,
        }

        result = _exec_node(agent_def, "build_request", state)

        assert result["use_structured"] is True
        assert len(result["llm_messages"]) == 2
        assert result["llm_messages"][0]["role"] == "system"
        assert result["llm_messages"][1]["role"] == "user"
        assert result["response_format"]["type"] == "json_schema"
        assert result["response_format"]["json_schema"]["name"] == "company_info"
        assert result["response_format"]["json_schema"]["strict"] is True
        assert result["response_format"]["json_schema"]["schema"] == schema

    def test_without_output_schema_sets_plain(self, agent_def):
        """AC2/6.2: Without output_schema → use_structured=False, no response_format."""
        state = {
            "system_prompt": "You are a helper.",
            "user_message": "Say hello",
        }

        result = _exec_node(agent_def, "build_request", state)

        assert result["use_structured"] is False
        assert len(result["llm_messages"]) == 2
        assert "response_format" not in result

    def test_missing_inputs_returns_unstructured(self, agent_def):
        """Edge case: missing system_prompt/user_message → use_structured=False."""
        state = {}
        result = _exec_node(agent_def, "build_request", state)
        assert result["use_structured"] is False

    def test_schema_without_title_uses_default_name(self, agent_def):
        """Edge case: schema missing 'title' key → defaults to 'response'."""
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        state = {
            "system_prompt": "sys",
            "user_message": "msg",
            "output_schema": schema,
        }

        result = _exec_node(agent_def, "build_request", state)
        assert result["response_format"]["json_schema"]["name"] == "response"


class TestParseResultNode:
    """Test parse_result node inline code."""

    def test_valid_json_string_parsed(self, agent_def):
        """AC1/6.3: Valid JSON string → parsed dict."""
        state = {
            "llm_result": {
                "success": True,
                "result": {"content": '{"name": "Acme", "industry": "tech"}'},
            },
            "use_structured": True,
        }

        result = _exec_node(agent_def, "parse_result", state)

        assert result["status"] == "success"
        assert result["result"] == {"name": "Acme", "industry": "tech"}

    def test_json_wrapped_in_markdown_fallback(self, agent_def):
        """AC3/6.4: JSON wrapped in markdown code block → fallback parser extracts it."""
        state = {
            "llm_result": {
                "success": True,
                "result": {"content": '```json\n{"name": "Acme"}\n```'},
            },
            "use_structured": True,
        }

        result = _exec_node(agent_def, "parse_result", state)

        assert result["status"] == "success"
        assert result["result"] == {"name": "Acme"}

    def test_ratelimit_error_envelope(self, agent_def):
        """AC4/6.5: Ratelimit error envelope → status: error."""
        state = {
            "llm_result": {
                "success": False,
                "error": "Rate limit exceeded",
            },
            "use_structured": True,
        }

        result = _exec_node(agent_def, "parse_result", state)

        assert result["status"] == "error"
        assert "Rate limit" in result["error"]

    def test_plain_text_mode_raw_string(self, agent_def):
        """AC2/6.6: Plain text mode → raw string result."""
        state = {
            "llm_result": {
                "success": True,
                "result": {"content": "Hello, I am an AI assistant."},
            },
            "use_structured": False,
        }

        result = _exec_node(agent_def, "parse_result", state)

        assert result["status"] == "success"
        assert result["result"] == "Hello, I am an AI assistant."

    def test_string_llm_result_plain_mode(self, agent_def):
        """Edge case: llm_result is a raw string (not dict envelope)."""
        state = {
            "llm_result": "Some plain text",
            "use_structured": False,
        }

        result = _exec_node(agent_def, "parse_result", state)

        assert result["status"] == "success"
        assert result["result"] == "Some plain text"

    def test_unparseable_json_returns_error(self, agent_def):
        """Edge case: structured mode but response is not valid JSON at all."""
        state = {
            "llm_result": {
                "success": True,
                "result": {"content": "This is not JSON at all"},
            },
            "use_structured": True,
        }

        result = _exec_node(agent_def, "parse_result", state)

        assert result["status"] == "error"
        assert "Failed to parse JSON" in result["error"]
