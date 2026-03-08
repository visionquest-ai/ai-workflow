# Story 1.3: Persist Extraction Result to websitePayload

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want the scrape result to be saved to the `websitePayload` field on the LegalFirm node via GraphQL,
so that the extracted data is accessible from the knowledge graph for downstream consumers.

## Acceptance Criteria

1. **AC1 -- Happy path: successful scrape persisted to websitePayload**
   **Given** a successful scrape with structured result data in `state.scrape_data_json`
   **When** the agent calls `graphology.update_node` with `node_type: "LegalFirm"` and `updates: {websitePayload: "<json>"}`
   **Then** the LegalFirm node's `websitePayload` field contains the serialized JSON string
   **And** `update_result.success` is `true`

2. **AC2 -- Error scrape result persisted for visibility**
   **Given** the scrape returned an error (from story 1.2)
   **When** the agent prepares the payload
   **Then** `websitePayload` contains a JSON object with `{"error": "...", "status": "failed"}`
   **And** the error is persisted to the node for visibility

3. **AC3 -- GraphQL mutation failure handling**
   **Given** the GraphQL mutation fails (e.g., field doesn't exist in schema, network error)
   **When** the `save_payload` step runs
   **Then** the agent returns `status: "error"` with the GraphQL error message
   **And** `completed` is `true`

4. **AC4 -- Successful completion and cleanup**
   **Given** the agent completes successfully (scrape + save)
   **When** the `finalize` step runs
   **Then** `completed` is `true` and `status` is `"success"`
   **And** temporary state is cleaned up

5. **AC5 -- TEA YAML pattern compliance (NFR5)**
   **Given** the modified agent YAML file
   **When** reviewed against `file_extraction.yaml` patterns
   **Then** `save_payload` uses `uses: graphology.update_node` with identical parameter patterns
   **And** `finalize` node checks `update_result.get("success")` before setting final status
   **And** error handling follows the same `return {"error": ..., "status": "error", "completed": True}` pattern

6. **AC6 -- Precondition: websitePayload property exists in ontology**
   **Given** the LegalFirm ontology class in Neo4j
   **When** the agent attempts to update `websitePayload`
   **Then** the property must already exist (created via Cypher precondition below)
   **And** graphology must have been restarted to regenerate the GraphQL schema

## Precondition

The `websitePayload` property MUST exist on the LegalFirm ontology class in Neo4j before running this agent. If it does not exist, create it via Cypher:

```graphql
mutation AddWebsitePayloadProperty {
  updateOntologyClasses(
    where: { name: "LegalFirm" }
    update: {
      hasProperty: [{
        create: [{
          node: { name: "websitePayload", type: "String" }
        }]
      }]
    }
  ) {
    ontologyClasses { name }
  }
}
```

Then restart graphology to regenerate the GraphQL schema.

## Tasks / Subtasks

- [x] Task 1: Add persistence-related state fields to `scrape_law_firm.yaml` (AC: #1, #5)
  - [x] 1.1: Add `update_result: dict` to `state_schema` (output from `graphology.update_node`)
  - [x] 1.2: Verify `scrape_data_json: str` exists (added in story 1.2 -- holds serialized JSON for persistence)

- [x] Task 2: Implement `prepare_error_payload` conditional logic in `process_scrape_result` (AC: #2)
  - [x] 2.1: In the existing `process_scrape_result` node (from story 1.2), when scrape failed, serialize `{"error": "<message>", "status": "failed"}` to `scrape_data_json` instead of leaving it empty
  - [x] 2.2: Do NOT set `completed: True` on scrape error -- let the error flow through to `save_payload` so it persists the error to the node
  - [x] 2.3: Add `scrape_failed: bool` state field if needed for conditional edge routing after save

- [x] Task 3: Implement `save_payload` node using `graphology.update_node` (AC: #1, #2, #3, #5)
  - [x] 3.1: Create node `save_payload` using `uses: graphology.update_node`
  - [x] 3.2: Set `node_id: "{{ state.context_node_id }}"` (same node fetched in story 1.1)
  - [x] 3.3: Set `node_type: "LegalFirm"` (hardcoded -- this agent only operates on LegalFirm nodes)
  - [x] 3.4: Set `updates: { websitePayload: "{{ state.scrape_data_json }}" }` (JSON string from story 1.2 or error payload)
  - [x] 3.5: Set `graphql_url: "{{ variables.GRAPHOLOGY_URL }}"` (from env)
  - [x] 3.6: Set `output: update_result`

- [x] Task 4: Implement `finalize` node for completion status (AC: #3, #4)
  - [x] 4.1: Create `finalize` node as a `run:` Python block
  - [x] 4.2: Check `update_result.get("success")` -- if `True` AND scrape was successful, return `{"completed": True, "status": "success"}`
  - [x] 4.3: If `update_result.get("success")` is `True` BUT scrape had failed, return `{"completed": True, "status": "error", "error": "Scrape failed but error persisted to node"}`
  - [x] 4.4: If `update_result.get("success")` is `False`, return `{"completed": True, "status": "error", "error": update_result.get("error", "Failed to save payload")}`

- [x] Task 5: Update edges to wire persistence into the flow (AC: #1, #2, #3, #5)
  - [x] 5.1: Change `process_scrape_result` success path: instead of -> `__end__`, route to -> `save_payload`
  - [x] 5.2: Change `process_scrape_result` error path: instead of -> `__end__`, route to -> `save_payload` (persist error too, per AC #2)
  - [x] 5.3: Add edge `save_payload` -> `finalize`
  - [x] 5.4: Add edge `finalize` -> `__end__`
  - [x] 5.5: Keep `validate_input` error path -> `__end__` unchanged (validation errors don't persist -- no scrape was attempted)

- [x] Task 6: Write/extend tests (AC: #1, #2, #3, #4, #5, #6)
  - [x] 6.1: Test happy path -- mock `update_result = {"success": True}` with successful scrape, verify `status: "success"` and `completed: True`
  - [x] 6.2: Test save failure -- mock `update_result = {"success": False, "error": "Connection refused"}`, verify `status: "error"` with error message
  - [x] 6.3: Test error payload persistence -- when scrape failed, verify `scrape_data_json` contains `{"error": "...", "status": "failed"}` JSON
  - [x] 6.4: Test scrape error + successful save -- verify `status: "error"` (scrape failed) even though save succeeded
  - [x] 6.5: Test YAML structure -- verify `save_payload` node exists with `uses: graphology.update_node` and correct parameters
  - [x] 6.6: Test edge wiring -- verify `process_scrape_result` both paths lead to `save_payload` (not `__end__`)
  - [x] 6.7: Test `finalize` node exists and checks `update_result.success`

## Dev Notes

### Architecture Requirements

- **Agent Framework**: TEA YAML engine (`the_edge_agent` submodule) -- agents are `.yaml` files in `agents/` directory
- **GraphQL Backend**: Graphology Apollo Server at `GRAPHOLOGY_URL` env var (default: `http://localhost:4000`)
- **Action Used**: `graphology.update_node` is already registered in `actions/graphology.py:register_actions()` (line ~918) -- no new actions needed
- **No new Python dependencies**: `graphology.update_node` is already available; no additions to `requirements.txt`

### Key Pattern: file_extraction.yaml save_payload (REFERENCE IMPLEMENTATION)

This is the EXACT pattern to follow (from `agents/file_extraction.yaml` lines 244-261):

```yaml
  # Save extraction result back to the node via GraphQL
  - name: save_payload
    uses: graphology.update_node
    with:
      node_id: "{{ state.context_node_id }}"
      node_type: "ApplicationFormFile"     # <-- Change to "LegalFirm"
      updates:
        payload: "{{ state.payload_json }}" # <-- Change to websitePayload: "{{ state.scrape_data_json }}"
      graphql_url: "{{ variables.GRAPHOLOGY_URL }}"
    output: update_result

  # Finalize step
  - name: finalize
    run: |
      update_result = state.get("update_result", {})
      if update_result.get("success"):
          return {"completed": True, "status": "success"}
      else:
          return {"completed": True, "status": "error", "error": update_result.get("error", "Failed to save payload")}
```

**Adaptation for story 1.3:**
- `node_type` changes from `"ApplicationFormFile"` to `"LegalFirm"`
- `updates` changes from `{payload: ...}` to `{websitePayload: ...}`
- `finalize` adds scrape-error awareness (check if scrape failed even though save succeeded)

### graphology.update_node Action Interface

**File:** `actions/graphology.py` lines 806-892

**Parameters:**
| Parameter | Type | Source | Description |
|-----------|------|--------|-------------|
| `node_id` | str | `state.context_node_id` | ID of the LegalFirm node |
| `node_type` | str | `"LegalFirm"` (hardcoded) | GraphQL type name |
| `updates` | dict | `{websitePayload: state.scrape_data_json}` | Fields to update |
| `graphql_url` | str | `variables.GRAPHOLOGY_URL` | GraphQL endpoint |

**Generated GraphQL Mutation:**
```graphql
mutation UpdateNode($where: LegalFirmWhere!, $update: LegalFirmUpdateInput!) {
  updateLegalFirms(where: $where, update: $update) {
    legalFirms { websitePayload id }
  }
}
```

**Pluralization:** `LegalFirm` -> `LegalFirms` (simple "s" suffix via `_pluralize()` at line 797)

**Return Shape:**
```python
# Success:
{"success": True, "node_id": "...", "node_type": "LegalFirm", "updated_fields": ["websitePayload"], "data": {...}}

# Failure (validation):
{"success": False, "error": "node_id is required"}
{"success": False, "error": "updates dict is required and must not be empty"}

# Failure (network/GraphQL):
{"success": False, "error": "Cannot connect to graphology..."}

# Failure (no results):
{"success": False, "error": "No node returned after update: <id>"}
```

### Error Payload Design (AC #2)

When the scrape fails (from story 1.2's `process_scrape_result`), instead of terminating, the agent should:

1. Serialize the error as JSON: `json.dumps({"error": scrape_error_msg, "status": "failed"})`
2. Store it in `scrape_data_json` (same field used for success)
3. Flow to `save_payload` to persist the error to the node
4. `finalize` checks both `update_result.success` AND original `status` to determine final status

This ensures downstream consumers can see WHY a scrape failed by reading `websitePayload`.

### Agent Flow After Story 1.3 (Complete)

```
__start__ -> fetch_node -> validate_input -> scrape_website -> process_scrape_result -> save_payload -> finalize -> __end__
                 |                                  |
                 v (error)                          v (both success and error paths)
              __end__                          save_payload -> finalize -> __end__
```

**Validation errors** (wrong node type, no website) still terminate immediately -- no point persisting errors for nodes that can't be scraped.

### Story 1.2 Modification Required

Story 1.2's `process_scrape_result` currently routes error path to `__end__`. Story 1.3 MUST:
1. Modify `process_scrape_result` to serialize error payloads to `scrape_data_json`
2. Retarget error edge from `__end__` to `save_payload`
3. Retarget success edge from `__end__` to `save_payload`

### Testing Patterns from test_file_extraction.py

- Tests use `pytest` (not `unittest`)
- YAML agent files are loaded and parsed with `yaml.safe_load`
- Node logic is tested by extracting `run:` blocks and executing them with mock state
- Helper: `_exec_node(agent_def, "node_name", state)` runs a node's Python code against given state
- Mock `graphology.update_node` to return canned responses
- Test edge structure by asserting edge list contents

### Environment Variables

- `GRAPHOLOGY_URL` -- GraphQL endpoint (default: `http://localhost:4000`) -- already configured in stories 1.1/1.2
- `GRAPHOLOGY_API_KEY` -- Optional API key for graphology -- already configured
- `SCRAPEGRAPH_API_KEY` -- Not used by this story (configured in story 1.2)

### Project Structure Notes

- Agent YAML file: `agents/scrape_law_firm.yaml` (created in story 1.1, extended in 1.2)
- Tests: `tests/test_scrape_law_firm.py` (created in story 1.1, extended in 1.2)
- No new files need to be created -- this story modifies existing files only
- Alignment with unified project structure: all changes stay within `agents/` and `tests/` directories

### Git Intelligence (Recent Commits)

Recent commits show:
- `file_extraction.yaml` is the primary pattern reference -- uses identical `graphology.update_node` call
- `graphology.update_node` was introduced in commit `16f6c61` alongside `file_extraction.yaml`
- Commit message convention: `feat:`, `chore:`, `test:`, `docs:` prefixes
- Submodule `the_edge_agent` is frequently updated -- ensure using current version

### Previous Story Learnings

- **Story 1.1** established: agent scaffold, `fetch_node` + `validate_input` pattern, state_schema with `context_node_id`, `context_result`, `website_url`, `status`, `error`, `completed`
- **Story 1.2** established: `scrape_website` node using `web.ai_scrape`, `process_scrape_result` with error handling, `scrape_result` and `scrape_data_json` state fields
- Both stories follow file_extraction.yaml patterns exactly

### References

- [Source: agents/file_extraction.yaml#save_payload] -- Primary pattern reference for `graphology.update_node` usage (lines 244-252)
- [Source: agents/file_extraction.yaml#finalize] -- Finalize pattern checking update_result.success (lines 255-261)
- [Source: actions/graphology.py#update_node] -- update_node function implementation (lines 806-892)
- [Source: actions/graphology.py#_pluralize] -- Type name pluralization for GraphQL mutations (lines 797-803)
- [Source: actions/graphology.py#register_actions] -- Action registration confirming update_node availability (lines 899-918)
- [Source: app.py#_load_and_run_agent] -- How agent results (status, error, payload_json) are extracted (lines 190-213)
- [Source: tests/test_file_extraction.py#TestFinalizeNode] -- Test patterns for finalize/update_result (lines 450-480)
- [Source: _bmad-output/implementation-artifacts/1-1-fetch-legalfirm-node-and-validate-input.md] -- Story 1.1 context and continuation points
- [Source: _bmad-output/implementation-artifacts/1-2-scrape-website-via-scrapegraphai.md] -- Story 1.2 context and continuation points
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.3] -- Story definition, acceptance criteria, and preconditions

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

None — all tasks completed without issues.

### Completion Notes List

- Task 1: Added `update_result: dict` and `scrape_failed: bool` to state_schema. Confirmed `scrape_data_json` already present.
- Task 2: Modified `process_scrape_result` error path to serialize error payload as JSON (`{"error": "...", "status": "failed"}`) into `scrape_data_json` instead of terminating. Removed `completed: True` on scrape error so flow continues to `save_payload`.
- Task 3: Added `save_payload` node using `graphology.update_node` with `node_type: "LegalFirm"`, `updates: {websitePayload: state.scrape_data_json}`, matching `file_extraction.yaml` patterns exactly.
- Task 4: Added `finalize` node that checks `update_result.get("success")` and `scrape_failed` to determine final status. Three outcomes: success, scrape-failed-but-persisted, save-failed.
- Task 5: Rewired edges — `process_scrape_result` now routes to `save_payload` (both success and error paths). Added `save_payload -> finalize -> __end__`. Kept `validate_input` error → `__end__` unchanged.
- Task 6: Extended test suite from 33 to 53 tests. Added `TestFinalizeNode` (7 tests), `TestErrorPayloadPersistence` (2 tests), `TestSavePayloadStructure` (6 tests), `TestEdgeWiringStory13` (3 tests). Updated existing error tests to reflect new serialization behavior.

### Change Log

- 2026-03-08: Story 1.3 implementation complete — persistence layer added to scrape_law_firm agent

### File List

- `agents/scrape_law_firm.yaml` — New (untracked): added state fields, save_payload node, finalize node, updated process_scrape_result error handling, rewired edges
- `tests/test_scrape_law_firm.py` — New (untracked): updated existing tests for new behavior, added 18 new tests for story 1.3
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Modified: story status updated
- `_bmad-output/implementation-artifacts/1-3-persist-extraction-result-to-websitepayload.md` — New (untracked): task checkboxes, dev agent record
- `the_edge_agent` — Modified: submodule ref updated
