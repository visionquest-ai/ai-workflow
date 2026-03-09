# Story 1.4: Scrape Status Tracking on LegalFirm Node

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want the scrape_law_firm agent to set a `scrapeStatus` property on the LegalFirm node reflecting the execution lifecycle (running, completed, failed),
so that downstream consumers and the UI can display real-time scraping progress without polling the agent endpoint.

## Acceptance Criteria

1. **AC1 -- scrapeStatus set to "running" before scraping begins**
   **Given** a valid LegalFirm node with a non-empty `website` URL (validation passed)
   **When** the agent proceeds to the scraping step
   **Then** `scrapeStatus` is updated to `"running"` on the LegalFirm node via `graphology.update_node` BEFORE the `scrape_website` node executes
   **And** the update uses `node_id: state.context_node_id`, `node_type: "LegalFirm"`, `updates: {scrapeStatus: "running"}`

2. **AC2 -- scrapeStatus set to "completed" on success**
   **Given** the scrape and persistence steps both succeed
   **When** the `finalize` step runs
   **Then** `scrapeStatus` is updated to `"completed"` on the LegalFirm node via `graphology.update_node`
   **And** the final agent `status` remains `"success"`

3. **AC3 -- scrapeStatus set to "failed" on scrape error**
   **Given** the ScrapeGraphAI call fails (timeout, rate limit, error)
   **When** the `finalize` step runs
   **Then** `scrapeStatus` is updated to `"failed"` on the LegalFirm node via `graphology.update_node`
   **And** the final agent `status` is `"error"`

4. **AC4 -- scrapeStatus set to "failed" on save_payload error**
   **Given** the scrape succeeds but `graphology.update_node` for `websitePayload` fails
   **When** the `finalize` step runs
   **Then** `scrapeStatus` is updated to `"failed"` on the LegalFirm node via `graphology.update_node`
   **And** the final agent `status` is `"error"`

5. **AC5 -- scrapeStatus NOT set on validation errors**
   **Given** a LegalFirm node with wrong type or missing website URL
   **When** the `validate_input` step fails and routes to `__end__`
   **Then** `scrapeStatus` is NOT modified (the agent never started scraping)
   **And** the node retains whatever `scrapeStatus` value it previously had

6. **AC6 -- Precondition: scrapeStatus property exists in ontology**
   **Given** the LegalFirm ontology class in Neo4j
   **When** the agent attempts to update `scrapeStatus`
   **Then** the property must already exist (created via GraphQL mutation precondition below)
   **And** graphology must have been restarted to regenerate the schema

7. **AC7 -- "running" status update failure is non-fatal**
   **Given** the `set_status_running` node fails (e.g., graphology temporarily unreachable)
   **When** the agent processes the failure
   **Then** the agent logs a warning but continues to `scrape_website` (best-effort status tracking)
   **And** the scraping pipeline is NOT blocked by a status update failure

## Precondition

The `scrapeStatus` property MUST exist on the LegalFirm ontology class in Neo4j before running this agent. If it does not exist, create it via GraphQL mutation:

```graphql
mutation AddScrapeStatusProperty {
  updateOntologyClasses(
    where: { name: "LegalFirm" }
    update: {
      hasProperty: [{
        create: [{
          node: { name: "scrapeStatus", type: "String" }
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

- [x] Task 1: Add status tracking state field to `scrape_law_firm.yaml` (AC: #1, #2, #3, #4)
  - [x] 1.1: Add `set_running_result: dict` to `state_schema` (output from the "running" status update call)
  - [x] 1.2: Add `set_final_status_result: dict` to `state_schema` (output from the finalize status update call)

- [x] Task 2: Implement `set_status_running` node after `validate_input` (AC: #1, #5, #7)
  - [x] 2.1: Create node `set_status_running` using `uses: graphology.update_node`
  - [x] 2.2: Set `node_id: "{{ state.context_node_id }}"`, `node_type: "LegalFirm"`, `updates: {scrapeStatus: "running"}`, `graphql_url: "{{ variables.GRAPHOLOGY_URL }}"`
  - [x] 2.3: Set `output: set_running_result`
  - [x] 2.4: Wire: `validate_input` success path -> `set_status_running` -> `scrape_website` (keep `validate_input` error path -> `__end__` unchanged)
  - [x] 2.5: The `set_status_running` node should NOT have conditional error routing — it always proceeds to `scrape_website` regardless of success/failure (best-effort, AC #7)

- [x] Task 3: Add `set_status_final` node after current `finalize` logic (AC: #2, #3, #4)
  - [x] 3.1: Create node `set_status_final` using `uses: graphology.update_node`
  - [x] 3.2: The `scrapeStatus` value must be determined BEFORE calling this node. In the `finalize` run block, compute `scrape_status_value`:
    - If `update_result.get("success")` and NOT `scrape_failed` → `"completed"`
    - Otherwise → `"failed"`
  - [x] 3.3: Store `scrape_status_value` in state from `finalize` run block
  - [x] 3.4: `set_status_final` uses `updates: {scrapeStatus: "{{ state.scrape_status_value }}"}`, `node_id: "{{ state.context_node_id }}"`, `node_type: "LegalFirm"`, `graphql_url: "{{ variables.GRAPHOLOGY_URL }}"`
  - [x] 3.5: Set `output: set_final_status_result`
  - [x] 3.6: Wire: `finalize` -> `set_status_final` -> `__end__` (instead of `finalize` -> `__end__`)

- [x] Task 4: Write/extend tests (AC: #1, #2, #3, #4, #5, #7)
  - [x] 4.1: Test `set_status_running` node exists with correct `graphology.update_node` params and `scrapeStatus: "running"`
  - [x] 4.2: Test `set_status_final` node exists with correct `graphology.update_node` params referencing `state.scrape_status_value`
  - [x] 4.3: Test `finalize` run block computes `scrape_status_value = "completed"` on success
  - [x] 4.4: Test `finalize` run block computes `scrape_status_value = "failed"` on scrape failure
  - [x] 4.5: Test `finalize` run block computes `scrape_status_value = "failed"` on save_payload failure
  - [x] 4.6: Test edge wiring: `validate_input` success -> `set_status_running` -> `scrape_website`
  - [x] 4.7: Test edge wiring: `finalize` -> `set_status_final` -> `__end__`
  - [x] 4.8: Test `validate_input` error path still goes to `__end__` (no status update on validation error)
  - [x] 4.9: Test YAML structure: both new nodes use `uses: graphology.update_node` with `node_type: "LegalFirm"`

## Dev Notes

### Architecture Requirements

- **Agent Framework**: TEA YAML engine (`the_edge_agent` submodule) — agents are `.yaml` files in `agents/` directory
- **GraphQL Backend**: Graphology Apollo Server at `GRAPHOLOGY_URL` env var (default: `http://localhost:4000`)
- **Action Used**: `graphology.update_node` is already registered in `actions/graphology.py` — no new actions needed
- **No new Python dependencies**: everything needed is already available

### Key Design Decision: Two Separate update_node Calls

The `scrapeStatus` is updated via TWO separate `graphology.update_node` calls, NOT merged into existing `save_payload`:
1. **`set_status_running`** — fires BEFORE scraping begins (AC #1). This is a standalone call because `save_payload` only runs after scraping.
2. **`set_status_final`** — fires AFTER `finalize` computes the outcome (AC #2, #3, #4). This is separate from `save_payload` because `save_payload` writes `websitePayload` and its success/failure determines the FINAL status value.

Merging `scrapeStatus` into `save_payload.updates` was considered but rejected because:
- `save_payload` can fail, and we still want to record `"failed"` status even when the payload save itself fails
- The "running" status must be set BEFORE scraping, not at save time
- Separation of concerns: payload persistence vs. execution lifecycle tracking

### Agent Flow After Story 1.4

```
__start__ -> fetch_node -> validate_input -> set_status_running -> scrape_website -> process_scrape_result -> save_payload -> finalize -> set_status_final -> __end__
                 |                                                                                                                           |
                 v (validation error)                                                                                                        v
              __end__                                                                                                                     __end__
```

**Key:** `set_status_running` is best-effort (always proceeds to `scrape_website`). `set_status_final` always goes to `__end__`.

### graphology.update_node Interface (Quick Reference)

**Parameters:** `node_id` (str), `node_type` (str), `updates` (dict), `graphql_url` (str)

**Generated GraphQL Mutation:**
```graphql
mutation UpdateNode($where: LegalFirmWhere!, $update: LegalFirmUpdateInput!) {
  updateLegalFirms(where: $where, update: $update) {
    legalFirms { scrapeStatus id }
  }
}
```

### finalize Node Modification

The existing `finalize` run block must be extended to compute and store `scrape_status_value`:

```python
# Current finalize logic (keep as-is)
update_result = state.get("update_result", {})
scrape_failed = state.get("scrape_failed", False)

if update_result.get("success"):
    if scrape_failed:
        return {"completed": True, "status": "error", "error": "Scrape failed but error persisted to node", "scrape_status_value": "failed"}
    return {"completed": True, "status": "success", "scrape_status_value": "completed"}
else:
    return {"completed": True, "status": "error", "error": update_result.get("error", "Failed to save payload"), "scrape_status_value": "failed"}
```

### TEA YAML Pattern for set_status_running

```yaml
  - name: set_status_running
    uses: graphology.update_node
    with:
      node_id: "{{ state.context_node_id }}"
      node_type: "LegalFirm"
      updates:
        scrapeStatus: "running"
      graphql_url: "{{ variables.GRAPHOLOGY_URL }}"
    output: set_running_result
```

### TEA YAML Pattern for set_status_final

```yaml
  - name: set_status_final
    goto: __end__
    uses: graphology.update_node
    with:
      node_id: "{{ state.context_node_id }}"
      node_type: "LegalFirm"
      updates:
        scrapeStatus: "{{ state.scrape_status_value }}"
      graphql_url: "{{ variables.GRAPHOLOGY_URL }}"
    output: set_final_status_result
```

### Edge Wiring Changes

1. `validate_input` success `goto` target changes from `scrape_website` to `set_status_running`
2. `set_status_running` implicitly flows to next node (`scrape_website`) — no conditional routing
3. `finalize` `goto` changes from `__end__` to `set_status_final`
4. `set_status_final` `goto: __end__` (explicit)

### Testing Patterns (from existing test_scrape_law_firm.py)

- Tests use `pytest` with `yaml.safe_load`
- `_exec_node(agent_def, "node_name", state)` helper for running Python blocks against mock state
- YAML structure assertions for node existence and parameters
- Edge wiring assertions checking `goto` targets

### Environment Variables

- `GRAPHOLOGY_URL` — GraphQL endpoint (already configured)
- No new environment variables required

### Project Structure Notes

- Agent YAML file: `agents/scrape_law_firm.yaml` (modified)
- Tests: `tests/test_scrape_law_firm.py` (extended)
- No new files created — this story modifies existing files only
- Alignment with unified project structure: all changes stay within `agents/` and `tests/`

### Previous Story Learnings (from 1.3)

- `graphology.update_node` works reliably with `node_type: "LegalFirm"`
- Error handling follows `return {"error": ..., "status": "error", "completed": True}` pattern
- `finalize` node checks `update_result.get("success")` and `scrape_failed` for tri-state outcome
- The `process_scrape_result` routes BOTH success and error to `save_payload` (not `__end__`)

### Git Intelligence

- Recent commits follow `feat:`, `chore:`, `test:`, `docs:` prefix convention
- Submodule `the_edge_agent` is frequently updated — ensure using current version
- Last story 1.3 was completed on 2026-03-08

### References

- [Source: agents/scrape_law_firm.yaml] — Current agent implementation with full flow
- [Source: agents/file_extraction.yaml#save_payload] — Pattern reference for `graphology.update_node` usage
- [Source: actions/graphology.py#update_node] — update_node function implementation (lines 806-892)
- [Source: _bmad-output/implementation-artifacts/1-3-persist-extraction-result-to-websitepayload.md] — Previous story context, learnings, and patterns
- [Source: _bmad-output/planning-artifacts/epics.md#FR7] — FR7: Agent reports success/error status upon completion
- [Source: app.py#_load_and_run_agent] — How agent results are extracted (lines 128-229)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

None required — clean implementation with no debugging needed.

### Completion Notes List

- Task 1: Added `set_running_result`, `set_final_status_result`, and `scrape_status_value` to `state_schema`
- Task 2: Added `set_status_running` node after `validate_input` using `graphology.update_node` with `scrapeStatus: "running"`. No conditional routing (best-effort per AC #7). Rewired `validate_input` fallback from `scrape_website` to `set_status_running`.
- Task 3: Extended `finalize` run block to compute `scrape_status_value` ("completed" on success, "failed" otherwise). Added `set_status_final` node after `finalize` with `goto: __end__`. Removed `goto: __end__` from `finalize` (now uses implicit chaining to `set_status_final`).
- Task 4: Added 26 new tests across 4 test classes: `TestSetStatusRunningNode` (8 tests), `TestSetStatusFinalNode` (8 tests), `TestEdgeWiringStory14` (5 tests), `TestFinalizeComputesScrapeStatusValue` (4 tests). Updated 3 pre-existing tests to reflect new flow. Updated `test_state_schema_has_required_fields` for new fields. All 76 tests pass. No regressions.

### Change Log

- 2026-03-09: Story 1.4 implementation complete — scrape status tracking with "running", "completed", "failed" lifecycle

### File List

- agents/scrape_law_firm.yaml (modified)
- tests/test_scrape_law_firm.py (modified)
- _bmad-output/implementation-artifacts/1-4-scrape-status-tracking.md (created)
- _bmad-output/implementation-artifacts/sprint-status.yaml (modified)
- the_edge_agent (submodule ref updated)
