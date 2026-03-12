# Story 14.4: ApplicationFormFile Extraction Status Tracking

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want the file_extraction agent to update the ApplicationFormFile `status` property in the graph at key pipeline milestones (reading, succeeded, failed),
So that the UI and downstream consumers can show real-time extraction progress and operators can identify failed extractions without inspecting logs.

## Acceptance Criteria

1. **AC1 - Status "reading" after file download:** Given a successful file download from GCS/HTTP in `extract_and_download`, when the file is downloaded and ready for processing (before directory detection), then the agent updates the ApplicationFormFile node's `status` property to `"reading"` via `graphology.update_node`.

2. **AC2 - Status "succeeded" after extraction completes:** Given a successful LlamaExtract extraction and payload save, when the `save_payload` step completes successfully, then the agent updates the ApplicationFormFile node's `status` property to `"succeeded"` via `graphology.update_node`.

3. **AC3 - Status "failed" on download error:** Given a failure during file download (GCS error, HTTP error, unsupported URL scheme), when the `extract_and_download` step returns an error, then the agent updates the ApplicationFormFile node's `status` property to `"failed"` via `graphology.update_node` before terminating.

4. **AC4 - Status "failed" on extraction error:** Given a failure during LlamaExtract extraction, agent resolution, or any step after download, when the pipeline errors out at any point, then the agent updates the ApplicationFormFile node's `status` property to `"failed"` via `graphology.update_node`.

5. **AC5 - Status "failed" on classification/detection error:** Given a failure during directory detection or LLM classification (TIER 1-3 all fail, unknown directory), when the agent cannot resolve the extraction agent, then `status` is updated to `"failed"` before the agent terminates.

6. **AC6 - Status update resilience:** Given the status update itself fails (e.g., network error to graphology), when `graphology.update_node` returns an error for the status update, then the agent logs a warning but does NOT fail the entire pipeline — the primary extraction flow takes precedence.

7. **AC7 - ApplicationFormFile `status` ontology property:** Given the `status` property may not exist on the ApplicationFormFile OntologyClass, when this story is implemented, then the property is created via GraphQL mutations (create OntologyProperty `name: "status"`, `type: "String"`, connect to ApplicationFormFile via `HAS_PROPERTY`) and graphology is restarted to regenerate the schema.

## Tasks / Subtasks

- [x] Task 1: Add `status` ontology property to ApplicationFormFile (AC: 7)
  - [x] 1.1 Create OntologyProperty node (`name: "status"`, `type: "String"`) and connect to ApplicationFormFile OntologyClass via `HAS_PROPERTY` — either via graphology bootstrap or manual GraphQL mutation
  - [x] 1.2 Restart graphology to regenerate GraphQL schema
  - [x] 1.3 Verify `status` is available on `updateApplicationFormFile` mutation via schema introspection

- [x] Task 2: Add status update helper node to `file_extraction.yaml` (AC: 1, 6)
  - [x] 2.1 Add a new `update_status_reading` node after `extract_and_download` (before `detect_directory`) that calls `graphology.update_node` with `updates: {status: "reading"}`
  - [x] 2.2 The `goto` routing in `extract_and_download` must be adjusted: on success go to `update_status_reading` instead of directly to `detect_directory`
  - [x] 2.3 `update_status_reading` then routes to `detect_directory` (on success) or continues the flow as before

- [x] Task 3: Set status "failed" on all error paths (AC: 3, 4, 5, 6)
  - [x] 3.1 In `extract_and_download`: on error returns (GCS failure, HTTP failure, unsupported scheme, node type mismatch, no storageUrl), add a `graphology.update_node` call setting `status: "failed"` before returning. Since this is a `run:` block, the update must be done inline via the `actions` dict (same pattern as `expand_proto_matters`)
  - [x] 3.2 In `resolve_agent`: on unknown directory error, update status to "failed" before returning
  - [x] 3.3 In `process_classification`: on classification error/inconclusive, update status to "failed" before returning
  - [x] 3.4 In `finalize`: on `update_result` failure (save_payload failed), update status to "failed"
  - [x] 3.5 Wrap all status update calls in try/except — log warning on failure but do NOT override the primary error (AC6)

- [x] Task 4: Set status "succeeded" on successful completion (AC: 2)
  - [x] 4.1 In `finalize` node: when `update_result.success` is true, call `graphology.update_node` to set `status: "succeeded"` on the ApplicationFormFile
  - [x] 4.2 Wrap in try/except — log warning on failure (AC6)

- [x] Task 5: Add tests for status tracking (AC: 1-6)
  - [x] 5.1 Test status "reading" is set after successful download
  - [x] 5.2 Test status "succeeded" is set after successful extraction pipeline
  - [x] 5.3 Test status "failed" is set on download error (GCS/HTTP)
  - [x] 5.4 Test status "failed" is set on classification/resolution error
  - [x] 5.5 Test resilience: status update failure does not crash the pipeline

## Dev Notes

### Implementation Strategy: `run:` blocks vs `uses:` nodes

The file_extraction agent uses two patterns:
- **`uses:` nodes** (e.g., `fetch_file_node`, `save_payload`) — declarative action calls, easy to add a new `uses: graphology.update_node` node
- **`run:` blocks** (e.g., `extract_and_download`, `resolve_agent`, `expand_proto_matters`) — Python code with inline logic

For the **"reading" status**, the cleanest approach is a new `uses:` node after `extract_and_download`:

```yaml
- name: update_status_reading
  uses: graphology.update_node
  with:
    node_id: "{{ state.context_node_id }}"
    node_type: "ApplicationFormFile"
    updates:
      status: "reading"
    graphql_url: "{{ variables.GRAPHOLOGY_URL }}"
  output: _status_reading_result
```

For **"failed" status in `run:` blocks**, use the `actions` dict pattern already established in `expand_proto_matters` (line 671):

```python
try:
    actions["graphology.update_node"](
        state=state,
        node_id=state.get("context_node_id"),
        node_type="ApplicationFormFile",
        updates={"status": "failed"},
        graphql_url=gql_url,
    )
except Exception:
    import logging
    logging.getLogger(__name__).warning("Failed to update status to 'failed'")
```

For **"succeeded" status in `finalize`**, same pattern but with `"succeeded"`.

### Flow After This Story

```
__start__ → fetch_file_node → extract_and_download
  ├── (success) → update_status_reading* → detect_directory → ...
  │     → resolve_agent → run_extraction → prepare_payload → save_payload
  │     → expand_proto_matters → finalize (set "succeeded"*) → __end__
  ├── (error in download) → set "failed"* → __end__
  ├── (error in classification) → set "failed"* → __end__
  ├── (error in resolve_agent) → set "failed"* → __end__
  └── (error in finalize) → set "failed"* → __end__

* = status update added by this story
```

### ApplicationFormFile Status Lifecycle (Complete)

After this story, the `status` field on ApplicationFormFile tracks:

| Value | Set When | Set Where |
|-------|----------|-----------|
| `"reading"` | File downloaded from GCS, extraction about to start | `update_status_reading` node |
| `"succeeded"` | Full pipeline completed (extraction + save + ProtoMatter expansion) | `finalize` node |
| `"failed"` | Any error in the pipeline (download, classification, extraction, save) | Error paths in `run:` blocks |

### Existing Status Updates on Other Node Types (Context)

For reference, these are the existing status updates that go to the graph:
- **ProtoMatter.status**: `"pending"` (created in `expand_proto_matters`, line 677), `"imported"`/`"error"` (updated in `import_matter_qa.yaml` `finalize_proto_matter`, line 182)

### Critical: `extract_and_download` Has Multiple Error Return Points

The `extract_and_download` `run:` block has **6 error return points** (lines 103, 114, 117, 138, 150, 152, 204). Each one currently returns `{error: ..., status: "error", completed: True}`. All 6 must be updated to also call `graphology.update_node` to set `status: "failed"` on the ApplicationFormFile.

**Pattern for each error return:**
```python
# Before the return statement:
try:
    actions["graphology.update_node"](
        state=state,
        node_id=state.get("context_node_id", ""),
        node_type="ApplicationFormFile",
        updates={"status": "failed"},
        graphql_url=os.environ.get("GRAPHOLOGY_URL", "http://localhost:4000"),
    )
except Exception:
    pass  # Best effort — don't mask the primary error
```

**IMPORTANT:** The `actions` dict is available in `run:` blocks (confirmed by `expand_proto_matters` usage at line 671). The `graphql_url` must be resolved from env since it's a `run:` block (not a `uses:` node where `variables.GRAPHOLOGY_URL` is available via template syntax).

### Routing Change in `extract_and_download`

Current `goto` (line 90-93):
```yaml
goto:
  - if: "not state.storage_url"
    to: __end__
  - to: detect_directory
```

Must become:
```yaml
goto:
  - if: "not state.storage_url"
    to: __end__
  - to: update_status_reading
```

And `update_status_reading` routes to `detect_directory`.

### Ontology Prerequisite

The `status` property likely does NOT exist on ApplicationFormFile yet (existing properties: storageUrl, directoryName, fileName, mimeType, year, payload, classificationPayload, detectedYear). Must be created:

```graphql
mutation {
  createOntologyProperty(input: { name: "status", type: "String" }) {
    ontologyProperties { id name }
  }
}
```

Then connect to ApplicationFormFile class:
```graphql
mutation {
  updateOntologyClass(
    where: { name_EQ: "ApplicationFormFile" }
    update: {
      hasProperty: { connect: { where: { node: { name_EQ: "status" } } } }
    }
  ) { ontologyClasses { id name } }
}
```

Then restart graphology.

### `graphology.update_node` Already Exists

No new actions needed. The existing `graphology.update_node` action (in `actions/graphology.py`) already supports updating arbitrary properties on any node type. Just pass `updates: {status: "reading"}`.

### Project Structure Notes

- **Modified file:** `agents/file_extraction.yaml` — New `update_status_reading` node, modified `extract_and_download` goto routing, status update calls in `run:` blocks (`extract_and_download`, `resolve_agent`, `process_classification`, `finalize`)
- **Modified file (cross-repo, conditional):** Graphology bootstrap or manual ontology mutation — add `status` property to ApplicationFormFile OntologyClass
- **Modified file:** `tests/test_file_extraction.py` — New test class `TestApplicationFormFileStatusTracking`

### References

- [Source: agents/file_extraction.yaml:80-86] — `fetch_file_node` node (where context_node_id is resolved)
- [Source: agents/file_extraction.yaml:89-229] — `extract_and_download` node with 6 error return points
- [Source: agents/file_extraction.yaml:432-481] — `resolve_agent` node with error returns
- [Source: agents/file_extraction.yaml:364-430] — `process_classification` node with error returns
- [Source: agents/file_extraction.yaml:546-557] — `save_payload` node (uses graphology.update_node pattern)
- [Source: agents/file_extraction.yaml:720-729] — `finalize` node (where "succeeded" status is set)
- [Source: agents/file_extraction.yaml:671-678] — `expand_proto_matters` `actions` dict usage pattern (reference for `run:` block action calls)
- [Source: _bmad-output/implementation-artifacts/16-4-expand-proto-matters-from-payload.md] — Previous story with `actions` pattern

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

### Completion Notes List

- Task 1: `status` OntologyProperty confirmed on ApplicationFormFile by PO. Verified via GraphQL mutation at `neo4j.visionquest.space/graphology/graphql` — `updateApplicationFormFile(update: { status_SET: "..." })` works. Graphology pods restarted and schema regenerated.
- Task 2: Added `update_status_reading` node (uses: graphology.update_node) between `extract_and_download` and `detect_directory`. Changed `extract_and_download` goto routing from `detect_directory` to `update_status_reading`. Output stored in `_status_reading_result` (best-effort, not checked).
- Task 3: Added `_set_failed()` helper with try/except in `extract_and_download` (7 error return points), `resolve_agent` (2 error paths), `process_classification` (3 error paths), and `finalize` (1 error path). All calls wrapped in try/except per AC6.
- Task 4: Added `_set_status("succeeded")` call in `finalize` when `update_result.success` is true. Wrapped in try/except per AC6.
- Task 5: Added 20 new tests in `TestStatusTrackingReadingNode` (6), `TestStatusTrackingFailedOnDownloadError` (5), `TestStatusTrackingFailedOnClassification` (3), `TestStatusTrackingSucceeded` (2), `TestStatusTrackingResilience` (4). All 104 tests pass (84 existing + 20 new).

### Code Review Notes (AI)

**Reviewer:** Code Review Agent (Claude Opus 4.6)
**Date:** 2026-03-12
**Result:** APPROVED (after fixes)

**Issues Found & Fixed:**
- **M1 (Fixed):** GRAPHOLOGY_URL resolution in `_set_failed()`/`_set_status()` helpers used `os.environ.get()` only, inconsistent with `expand_proto_matters` pattern. Fixed to use `state.get("variables", {}).get("GRAPHOLOGY_URL") or os.environ.get("GRAPHOLOGY_URL") or "http://localhost:4000"` in all 4 helpers.
- **M2 (Fixed):** Added test `test_graphology_fetch_failure_sets_failed` — graphology fetch failure error path now verified with `_exec_node_with_actions`.
- **M3 (Fixed):** Added test `test_pdf_conversion_failure_sets_failed` — PDF conversion failure error path now verified with `_exec_node_with_actions`.

**Remaining (Low, not fixed):**
- L1: `_set_failed()` duplicated in 3 run blocks (YAML agent limitation)
- L2: `the_edge_agent` submodule in git diff but not in File List (prior commit)
- L3: `update_status_reading` node relies on implicit `raise_exceptions: false` for AC6

**Test Results:** 106 passed (84 existing + 20 story + 2 review)

### File List

- agents/file_extraction.yaml — Added `update_status_reading` node, `_set_failed()` helpers in `extract_and_download`/`resolve_agent`/`process_classification`, `_set_status()` helper in `finalize`. Review fix: GRAPHOLOGY_URL resolution aligned with variables→env→fallback pattern.
- tests/test_file_extraction.py — Added 22 tests across 5 test classes for status tracking (AC1-6). Review added: graphology fetch failure + PDF conversion failure status tracking tests.
- _bmad-output/implementation-artifacts/sprint-status.yaml — Status updated: ready-for-dev → in-progress → review → done
- _bmad-output/implementation-artifacts/14-4-applicationformfile-status-tracking.md — Tasks marked complete, Dev Agent Record updated, Code Review notes added
