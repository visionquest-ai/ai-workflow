# Story 1.1: Fetch LegalFirm Node and Validate Input

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want the agent to fetch a LegalFirm node by ID and validate it has a website URL,
so that the scraping pipeline only proceeds with valid, scrapeable nodes.

## Acceptance Criteria

1. **AC1 — Happy path: valid LegalFirm with website**
   **Given** a valid `context_node_id` pointing to a LegalFirm node with a populated `website` field
   **When** the agent executes the `fetch_node` and `validate_input` steps
   **Then** `context_result.data` contains `website`, `firmName`, and other LegalFirm fields
   **And** `context_result.node_type` equals `"LegalFirm"`

2. **AC2 — Wrong node type**
   **Given** a `context_node_id` pointing to a node that is NOT a LegalFirm
   **When** the agent executes validation
   **Then** the agent sets `status: "error"` with message indicating wrong node type
   **And** `completed` is set to `true`

3. **AC3 — Missing website URL**
   **Given** a LegalFirm node with an empty or missing `website` field
   **When** the agent executes validation
   **Then** the agent sets `status: "error"` with message "LegalFirm has no website URL"
   **And** the agent terminates gracefully via `__end__`

4. **AC4 — Graphology fetch failure**
   **Given** the graphology server is unreachable or returns an error
   **When** the `fetch_node` step runs
   **Then** the agent sets `status: "error"` with a descriptive error message from graphology
   **And** `completed` is set to `true`

5. **AC5 — TEA YAML pattern compliance (NFR5)**
   **Given** the new agent YAML file
   **When** reviewed against `file_extraction.yaml` patterns
   **Then** it uses the same structural conventions: `name`, `description`, `state_schema`, `nodes`, `edges`, `config`
   **And** it uses `graphology.get_node` action with identical parameter patterns
   **And** error handling follows the same `return {"error": ..., "status": "error", "completed": True}` pattern

## Tasks / Subtasks

- [x] Task 1: Create `agents/scrape_law_firm.yaml` agent scaffold (AC: #5)
  - [x] 1.1: Create YAML file with `name`, `description`, `state_schema` sections following `file_extraction.yaml` patterns
  - [x] 1.2: Define `state_schema` with all required fields: `context_node_id` (input), `context_result`, `website_url`, `status`, `error`, `completed` (intermediate/output)
  - [x] 1.3: Add `config: raise_exceptions: false` (same as file_extraction.yaml)

- [x] Task 2: Implement `fetch_node` step using `graphology.get_node` (AC: #1, #4)
  - [x] 2.1: Create node `fetch_node` using `uses: graphology.get_node` with `node_id: "{{ state.context_node_id }}"` and `graphql_url: "{{ variables.GRAPHOLOGY_URL }}"`
  - [x] 2.2: Set `output: context_result` to store the graphology response

- [x] Task 3: Implement `validate_input` step with all error paths (AC: #1, #2, #3, #4)
  - [x] 3.1: Create `validate_input` node as a `run:` Python block
  - [x] 3.2: Check `context_result.success` — if `False`, return error from graphology (AC #4)
  - [x] 3.3: Check `context_result.node_type` — if not `"LegalFirm"`, return wrong-type error (AC #2)
  - [x] 3.4: Extract `website` from `context_result.data` — if empty/missing, return "LegalFirm has no website URL" (AC #3)
  - [x] 3.5: On success, return `website_url` for downstream use; other fields remain accessible via `state.context_result.data` (AC #1)

- [x] Task 4: Define edges for conditional flow (AC: #2, #3, #4)
  - [x] 4.1: `__start__` → `fetch_node`
  - [x] 4.2: `fetch_node` → `validate_input`
  - [x] 4.3: `validate_input` → `__end__` when `completed` is `true` (error paths)
  - [x] 4.4: `validate_input` → placeholder next step (for story 1.2 to connect scraping) when validation passes — for now, route to `__end__` with success status

- [x] Task 5: Write comprehensive tests (AC: #1, #2, #3, #4, #5)
  - [x] 5.1: Create `tests/test_scrape_law_firm.py` following `tests/test_file_extraction.py` patterns
  - [x] 5.2: Test happy path — mock `graphology.get_node` returning a valid LegalFirm with `website` field
  - [x] 5.3: Test wrong node type — mock returning a non-LegalFirm node
  - [x] 5.4: Test missing website — mock returning LegalFirm with empty `website`
  - [x] 5.5: Test graphology failure — mock `context_result.success = False`
  - [x] 5.6: Test YAML structure compliance — verify agent file has required sections matching file_extraction.yaml conventions

## Dev Notes

### Architecture Requirements

- **Agent Framework**: TEA YAML engine (`the_edge_agent` submodule) — agents are `.yaml` files in `agents/` directory
- **GraphQL Backend**: Graphology Apollo Server at `GRAPHOLOGY_URL` env var (default: `http://localhost:4000`)
- **Action Registration**: `graphology.get_node` is already registered in `actions/graphology.py:register_actions()` — no new actions needed
- **API Invocation**: Agent is invoked via `POST /run-agent {"agent": "scrape_law_firm", "context_node_id": "<id>"}` — the `app.py` endpoint already handles loading any agent from `agents/` directory

### Key Patterns from file_extraction.yaml (NFR5 Compliance)

1. **State Schema**: Declare all state variables with types (`str`, `dict`, `bool`)
2. **Node Types**: Use `uses:` for TEA built-in actions, `run:` for inline Python
3. **Error Handling Pattern**: Every `run:` block checks errors and returns `{"error": "...", "status": "error", "completed": True}`
4. **graphology.get_node Usage**: Pass `node_id` and `graphql_url` via `with:`, store result in `output: context_result`
5. **Success Check**: Always check `context_result.get("success", True)` — note the default `True` (backwards compat)
6. **Node Type Check**: Compare `context_result.get("node_type", "")` against expected type
7. **Data Extraction**: Access fields via `context_result.get("data", {}).get("fieldName", "")`
8. **Edge Conditions**: Use `condition: "{{ state.field }}"` / `"{{ not state.field }}"` for branching
9. **Config Section**: `config: raise_exceptions: false` prevents TEA from crashing on errors

### Graphology get_node Return Shape

```python
# Success:
{"success": True, "node_type": "LegalFirm", "data": {"id": "...", "website": "...", "firmName": "...", ...}, "data_json": "..."}

# Failure (server down, node not found):
{"success": False, "error": "Cannot connect to graphology..." }
```

### Testing Patterns from test_file_extraction.py

- Tests use `pytest` (not `unittest`)
- YAML agent files are loaded and parsed with `yaml.safe_load`
- State schema assertions verify required keys exist
- Edge conditions are tested by asserting edge list structure
- Mock `graphology.get_node` to return canned responses for each scenario
- No need for a running graphology server — all tests use mocks

### LegalFirm Node Expected Fields

Based on the epics and spa-base reference, LegalFirm nodes have these properties:
- `id`, `name`/`firmName`, `website`, `websitePayload` (target for story 1.3)
- The `websitePayload` property may need to be added to the ontology (precondition for story 1.3, NOT this story)

### Important: Story 1.2/1.3 Continuation Points

This story creates the agent scaffold that stories 1.2 and 1.3 will extend:
- Story 1.2 adds `web.ai_scrape` node after `validate_input` (the spa-base reference agent uses `cache.wrap` with `web.ai_scrape`)
- Story 1.3 adds `graphology.update_node` to save `websitePayload`
- The edge from `validate_input` to `__end__` (success path) will be retargeted to the scraping node in story 1.2

### Environment Variables

- `GRAPHOLOGY_URL` — GraphQL endpoint (default: `http://localhost:4000`)
- `GRAPHOLOGY_API_KEY` — Optional API key for graphology
- `SCRAPEGRAPH_API_KEY` — Not needed for this story (story 1.2)

### Project Structure Notes

- Agent YAML files: `agents/<agent_name>.yaml`
- Custom actions: `actions/<module>.py`
- Tests: `tests/test_<module>.py`
- App entry point: `app.py` (FastAPI)
- No new dependencies needed for this story — `graphology.get_node` is already available

### References

- [Source: agents/file_extraction.yaml] — Primary pattern reference for TEA YAML structure
- [Source: actions/graphology.py#get_node] — GraphQL introspection-based node fetcher (lines 715-790)
- [Source: actions/graphology.py#register_actions] — Action registration pattern (lines 899-918)
- [Source: app.py#_load_and_run_agent] — How agents are loaded and executed (lines 128-218)
- [Source: spa-base/firebase/functions-agents/agents/scrape-law-firm.yaml] — Original spa-base reference agent
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.1] — Story definition and acceptance criteria
- [Source: tests/test_file_extraction.py] — Test patterns for TEA YAML agents

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

No issues encountered during implementation.

### Completion Notes List

- Created `agents/scrape_law_firm.yaml` TEA YAML agent following `file_extraction.yaml` patterns (AC5)
- `fetch_node` uses `graphology.get_node` with identical parameter patterns to file_extraction (AC1, AC4)
- `validate_input` implements all 3 error paths: graphology failure (AC4), wrong node type (AC2), missing website (AC3)
- Happy path extracts `website_url` from `context_result.data` for downstream use (AC1)
- Both `validate_input` → `__end__` edges handle error (via `completed`) and success (via `not completed`) paths
- Success path routes to `__end__` as placeholder — Story 1.2 will retarget to scraping node
- 14 tests written: 9 structural compliance + 5 functional (happy path, graphology failure, wrong type, missing website, empty website)
- All 14 new tests pass; 4 pre-existing failures in test_file_extraction.py and test_docker_deployment.py (unrelated to this story)

### Change Log

- 2026-03-08: Story 1.1 implemented — created scrape_law_firm agent scaffold with fetch_node + validate_input + comprehensive tests
- 2026-03-08: Code review — fixed 4 issues: strengthened happy path assertion (H1), tightened edge count test to == 2 (M1), added edge condition validation test (M2), removed dead mock_modules code from test helper (M4), clarified task 3.5 description (M3)

### File List

- agents/scrape_law_firm.yaml (new)
- tests/test_scrape_law_firm.py (new)
