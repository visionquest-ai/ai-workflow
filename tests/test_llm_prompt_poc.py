"""
POC test for llm_prompt agent — validates response_format structured output.

Requires Azure OpenAI credentials:
  AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT
  OPENAI_API_VERSION must be 2024-08-01-preview or later for json_schema mode.

Usage:
    cd visionQuest/ai-workflow
    AZURE_OPENAI_API_KEY="..." \
    AZURE_OPENAI_ENDPOINT="https://rankellix-ai-resource.openai.azure.com/" \
    AZURE_OPENAI_DEPLOYMENT="gpt-5.3-chat" \
    OPENAI_API_VERSION="2024-08-01-preview" \
    python tests/test_llm_prompt_poc.py
"""

import json
import os
import sys

TEA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "the_edge_agent", "python", "src")
sys.path.insert(0, TEA_PATH)

ACTIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "actions")
sys.path.insert(0, ACTIONS_DIR)


def run_poc():
    from the_edge_agent import YAMLEngine

    agent_path = os.path.join(os.path.dirname(__file__), "..", "agents", "llm_prompt.yaml")

    # ── Test 1: Structured output with json_schema ────────────────────
    print("\n" + "=" * 60)
    print("TEST 1: Structured output (response_format: json_schema)")
    print("=" * 60)

    output_schema = {
        "title": "company_info",
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Company name"},
            "founded_year": {"type": "integer", "description": "Year founded"},
            "industry": {"type": "string", "description": "Primary industry"},
            "headquarters": {"type": "string", "description": "HQ city and country"},
        },
        "required": ["name", "founded_year", "industry", "headquarters"],
        "additionalProperties": False,
    }

    initial_state = {
        "system_prompt": "You are a helpful assistant that extracts company information from text.",
        "user_message": "Apple Inc. was founded in 1976 by Steve Jobs, Steve Wozniak, and Ronald Wayne. "
                        "It is a technology company headquartered in Cupertino, California, USA.",
        "output_schema": output_schema,
    }

    engine = YAMLEngine()
    graph = engine.load_from_file(agent_path)
    events = list(graph.invoke(initial_state))
    final_event = events[-1] if events else {}
    final_state = final_event.get("state", final_event) if isinstance(final_event, dict) else {}

    print(f"Status: {final_state.get('status')}")
    result_val = final_state.get('result')
    if isinstance(result_val, dict):
        print(f"Result: {json.dumps(result_val, indent=2)}")
    else:
        print(f"Result: {result_val}")

    if final_state.get("status") == "success":
        result = final_state["result"]
        assert isinstance(result, dict), "Result should be a dict"
        assert "name" in result, "Result should have 'name'"
        assert "founded_year" in result, "Result should have 'founded_year'"
        print("\n>> TEST 1 PASSED — structured output works!")
    else:
        print(f"\n>> TEST 1 FAILED — {final_state.get('error')}")
        return False

    # ── Test 2: Plain text mode (no output_schema) ────────────────────
    print("\n" + "=" * 60)
    print("TEST 2: Plain text mode (no output_schema)")
    print("=" * 60)

    initial_state_plain = {
        "system_prompt": "You are a helpful assistant. Reply in one sentence.",
        "user_message": "What is the capital of France?",
    }

    engine2 = YAMLEngine()
    graph2 = engine2.load_from_file(agent_path)
    events2 = list(graph2.invoke(initial_state_plain))
    final_event2 = events2[-1] if events2 else {}
    final_state2 = final_event2.get("state", final_event2) if isinstance(final_event2, dict) else {}

    print(f"Status: {final_state2.get('status')}")
    print(f"Result: {final_state2.get('result')}")

    if final_state2.get("status") == "success":
        assert isinstance(final_state2["result"], str), "Result should be a string"
        print("\n>> TEST 2 PASSED — plain text mode works!")
    else:
        print(f"\n>> TEST 2 FAILED — {final_state2.get('error')}")
        return False

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)
    return True


if __name__ == "__main__":
    required = ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)

    success = run_poc()
    sys.exit(0 if success else 1)
