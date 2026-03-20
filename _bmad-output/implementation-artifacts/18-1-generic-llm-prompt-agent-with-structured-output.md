# Story 18.1: Generic LLM Prompt Agent with Structured Output

Status: done

## Story

As a TEA YAML agent developer,
I want a reusable `llm_prompt` agent that accepts a system prompt, user message, and optional JSON Schema,
So that any workflow can call the LLM with guaranteed structured output without building custom prompt/parse logic each time.

## Context

POC completed 2026-03-20. Both structured (json_schema) and plain text modes verified against Azure OpenAI gpt-5.3-chat. Key findings:

- `response_format` works via `**kwargs` passthrough in `llm.call` (undocumented but confirmed)
- Azure OpenAI requires `OPENAI_API_VERSION=2024-08-01-preview`+ for `json_schema` mode
- `ratelimit.wrap` only forwards `args` dict — two separate LLM nodes needed until BUG.004 is fixed
- TEA conditional edges to `__end__` are broken (BUG.003) — workaround: never branch conditionally to `__end__`

## Acceptance Criteria

1. **AC1 - Structured output mode:** Given `output_schema` is provided as a valid JSON Schema dict, when the agent runs, then the LLM is called with `response_format: {type: json_schema, json_schema: {name, strict: true, schema}}` and the response is parsed into a dict in `state.result`.

2. **AC2 - Plain text mode:** Given `output_schema` is not provided (None/missing), when the agent runs, then the LLM is called without `response_format` and `state.result` contains the raw text string.

3. **AC3 - JSON parse resilience:** Given the LLM returns JSON with surrounding text (e.g., markdown code block), when parsing fails on the raw response, then a fallback parser extracts the first JSON object from the response.

4. **AC4 - Error propagation:** Given the LLM call fails (rate limit, API error), when `ratelimit.wrap` returns `success: false`, then `state.status` is `"error"` and `state.error` contains the error message.

5. **AC5 - Same LLM config as file_classification:** The agent uses `gpt-5.3-chat`, temperature 1, max_completion_tokens 2000, via `ratelimit.wrap` with `limiter: llm_prompt`, `rpm: 60`.

6. **AC6 - Reusable as sub-agent:** The agent can be invoked from other YAML agents via `uses: agents.llm_prompt` with `system_prompt`, `user_message`, and `output_schema` as inputs.

7. **AC7 - API version updated:** Helm values `OPENAI_API_VERSION` is updated to `2024-08-01-preview` to enable `json_schema` response format.

8. **AC8 - Documentation updated:** `the_edge_agent` docs for `llm.call` include `response_format` parameter documentation with examples for `json_object` and `json_schema` modes.

## Tasks / Subtasks

- [x] Task 1: Create `agents/llm_prompt.yaml` agent (AC: 1, 2, 3, 4, 5)
  - [x] 1.1 Define state_schema: inputs (`system_prompt`, `user_message`, `output_schema`), intermediates (`llm_messages`, `response_format`, `use_structured`), outputs (`result`, `status`, `error`)
  - [x] 1.2 Create `build_request` node: constructs messages array, determines structured vs plain mode, builds `response_format` dict when schema provided
  - [x] 1.3 Create `invoke_llm_structured` node: `ratelimit.wrap` → `llm.call` with `response_format` in args
  - [x] 1.4 Create `invoke_llm_plain` node: `ratelimit.wrap` → `llm.call` without `response_format`
  - [x] 1.5 Create `parse_result` node: unwraps ratelimit envelope, parses JSON (with fallback), returns result + status
  - [x] 1.6 Wire conditional edges: `build_request` → structured/plain based on `use_structured` flag

- [x] Task 2: Document `response_format` in TEA (AC: 8)
  - [x] 2.1 Update `the_edge_agent/docs/shared/yaml-reference/actions/llm.md` with `response_format` section
  - [x] 2.2 Include `json_object` and `json_schema` examples
  - [x] 2.3 Note Azure API version requirement and `ratelimit.wrap` args placement

- [x] Task 3: POC test (AC: 1, 2)
  - [x] 3.1 Create `tests/test_llm_prompt_poc.py` — live integration test against Azure OpenAI
  - [x] 3.2 Test 1: structured output with company_info schema → validates parsed dict with correct fields
  - [x] 3.3 Test 2: plain text mode → validates string result

- [x] Task 4: Register agent for sub-agent invocation (AC: 6)
  - [x] 4.1 Register `llm_prompt` in `actions/agents.py` so it can be called via `uses: agents.llm_prompt`
  - [x] 4.2 Map input/output state fields for sub-agent interface

- [x] Task 5: Update Helm API version (AC: 7)
  - [x] 5.1 Update `visionQuest/vq-charts/values.yaml` `openaiApiVersion` from `2024-05-01-preview` to `2024-08-01-preview`
  - [x] 5.2 Verify existing agents (file_classification) still work with the new API version

- [x] Task 6: Unit tests (AC: 1, 2, 3, 4)
  - [x] 6.1 Test `build_request` node: with output_schema → sets `use_structured: true`, builds `response_format`
  - [x] 6.2 Test `build_request` node: without output_schema → sets `use_structured: false`, no `response_format`
  - [x] 6.3 Test `parse_result` node: valid JSON string → parsed dict
  - [x] 6.4 Test `parse_result` node: JSON wrapped in markdown → fallback parser extracts it
  - [x] 6.5 Test `parse_result` node: ratelimit error envelope → status: error
  - [x] 6.6 Test `parse_result` node: plain text mode → raw string result

## Dev Notes

- Agent file already exists at `agents/llm_prompt.yaml` (created during POC)
- POC test at `tests/test_llm_prompt_poc.py` (live test, requires Azure creds)
- Two LLM node pattern is a workaround for BUG.004 (`ratelimit.wrap` drops kwargs). Once BUG.004 is fixed, simplify to single node with conditional `response_format`
- TEA `edges` deprecation warning appears but edges section still works. Conditional edges to `__end__` are broken (BUG.003) — do NOT use

### Project Structure Notes

- `agents/llm_prompt.yaml` — new generic agent, follows same pattern as `file_classification.yaml`
- `tests/test_llm_prompt_poc.py` — integration test, same style as `test_file_classification.py`
- `actions/agents.py` — needs registration of `llm_prompt` sub-agent (Task 4)

### References

- [Source: agents/file_classification.yaml] — pattern reference for ratelimit.wrap + llm.call
- [Source: the_edge_agent/examples/yaml/extraction_with_retry.yaml] — response_format example
- [Source: the_edge_agent/python/src/the_edge_agent/actions/llm_actions.py#llm_call] — kwargs passthrough at line 1217
- [Source: the_edge_agent/python/src/the_edge_agent/actions/ratelimit_actions.py#ratelimit_wrap] — args forwarding at line 339
- [Source: the_edge_agent/docs/stories/BUG.003] — conditional edge to __end__ bug
- [Source: the_edge_agent/docs/stories/BUG.004] — ratelimit.wrap kwargs drop bug

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Completion Notes List

- POC completed 2026-03-20: both structured and plain text modes verified
- Tasks 1-3 completed during POC phase
- Task 4 completed 2026-03-20: `invoke_llm_prompt` wrapper in `actions/agents.py` registered as `agents.llm_prompt`. Maps `system_prompt`, `user_message`, `output_schema` directly to sub-agent input_state. Unwraps result/status/error from agent state.
- Task 5 completed 2026-03-20: Updated `openaiApiVersion` from `2024-05-01-preview` to `2024-08-01-preview` in both umbrella `values.yaml` and sub-chart `charts/ai-workflow/values.yaml`. Backward-compatible — existing agents unaffected.
- Task 6 completed 2026-03-20: 16 unit tests in `tests/test_llm_prompt.py` — covers registration (2), wrapper mapping (4), build_request node (4), parse_result node (6). All pass. 10 pre-existing failures in other test files unrelated to this story.
- Task 2 docs completed 2026-03-20: Added `response_format` section to `the_edge_agent/docs/shared/yaml-reference/actions/llm.md` with `json_object` and `json_schema` examples, Azure API version requirement, and `ratelimit.wrap` args placement note.

## File List

- `visionQuest/ai-workflow/actions/agents.py` — Added `invoke_llm_prompt` function + registered `agents.llm_prompt`
- `visionQuest/ai-workflow/tests/test_llm_prompt.py` — New: 16 unit tests for Task 4 + Task 6
- `visionQuest/vq-charts/values.yaml` — Updated `openaiApiVersion` to `2024-08-01-preview`
- `visionQuest/ai-workflow/the_edge_agent/docs/shared/yaml-reference/actions/llm.md` — Added `response_format` section (AC8)
- `visionQuest/vq-charts/charts/ai-workflow/values.yaml` — Updated `openaiApiVersion` to `2024-08-01-preview`

## Change Log

- 2026-03-20: Completed Tasks 4-6. Agent registered for sub-agent invocation, Helm API version updated, comprehensive unit tests added. All 16 new tests pass.
