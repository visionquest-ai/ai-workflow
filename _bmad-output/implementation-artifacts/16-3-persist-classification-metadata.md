# Story 16.3: Persist Classification Metadata

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want the classification metadata (directory_source, year_source, category, region, confidence) persisted alongside the extraction payload,
So that downstream consumers can audit how files were classified and leverage the enriched metadata.

## Acceptance Criteria

1. **AC1 - classificationPayload persisted on success:** Given a successful file extraction with classification metadata available, when the `save_payload` step runs, then the GraphQL mutation includes `classificationPayload` alongside `payload` on the ApplicationFormFile node, and `classificationPayload` contains JSON with: `directory`, `directory_source`, `detected_year`, `year_source`, `category`, `region_country`, `region_country_display`, `region_state`, `region_state_display`, `confidence`, `is_empty_form`.

2. **AC2 - TIER 1 metadata (no LLM fields):** Given a file classified via TIER 1 (filename) with no LLM call, when the classification metadata is persisted, then `classificationPayload` contains `directory_source: "filename"` and `category`, `region_country`, `region_state` are `null` (not available without LLM), and `confidence` is `1.0` (exact pattern match).

3. **AC3 - TIER 2 metadata (no LLM fields):** Given a file classified via TIER 2 (content) with no LLM call, when the classification metadata is persisted, then `classificationPayload` contains `directory_source: "content"` and `category`, `region_country`, `region_state` are `null`, and `confidence` is `1.0`.

4. **AC4 - TIER 3 metadata (full LLM classification):** Given a file classified via TIER 3 (LLM) with full classification, when the classification metadata is persisted, then `classificationPayload` contains all fields populated: `directory_source: "llm"`, `category`, `region_country`, `region_country_display`, `region_state`, `region_state_display`, `confidence`, `is_empty_form`.

5. **AC5 - classificationPayload persisted even on extraction failure:** Given a file where directory was detected but LlamaExtract extraction failed, when the `save_payload` step runs with error status, then `classificationPayload` is still persisted with whatever classification metadata was available, and `payload` contains the error JSON as before.

6. **AC6 - detectedYear persisted as separate field:** Given a file with a detected year (from any tier or pre-set), when the `save_payload` step runs, then `detectedYear` is persisted as a separate scalar field on the ApplicationFormFile node (not just inside classificationPayload).

7. **AC7 - Ontology property creation for classificationPayload:** Given the `classificationPayload` property does not exist on ApplicationFormFile, when the story is implemented, then the property is created via GraphQL mutations: create OntologyProperty node (`name: "classificationPayload"`, `type: "String"`), connect via `HAS_PROPERTY` to ApplicationFormFile OntologyClass, and graphology is restarted to regenerate the schema.

8. **AC8 - Ontology property creation for detectedYear:** Given the `detectedYear` property does not exist on ApplicationFormFile, when the story is implemented, then the property is created via GraphQL mutations: create OntologyProperty node (`name: "detectedYear"`, `type: "String"`), connect via `HAS_PROPERTY` to ApplicationFormFile OntologyClass, and graphology is restarted to regenerate the schema.

9. **AC9 - Input fast-path metadata:** Given a file with `directoryName` pre-set on the node (fast path), when the classification metadata is persisted, then `classificationPayload` contains `directory_source: "input"`, `confidence: 1.0`, and LLM-only fields are `null`.

## Tasks / Subtasks

- [x] Task 1: Add `classification_json` field to state_schema in `file_extraction.yaml` (AC: all)
  - [x] 1.1 Add `classification_json` (str) — serialized JSON string of classification metadata for GraphQL persistence

- [x] Task 2: Modify `prepare_payload` node to build `classification_json` (AC: 1-6, 9)
  - [x] 2.1 Build classification metadata dict from state fields: `directory_name` → `directory`, `directory_source`, `detected_year`, `year_source`
  - [x] 2.2 If `classification_result` exists in state (TIER 3 was invoked), merge LLM fields: `category`, `region_country`, `region_country_display`, `region_state`, `region_state_display`, `confidence`, `is_empty_form`
  - [x] 2.3 If NO `classification_result` (TIER 1/2 or fast-path), set LLM-only fields to `null` and `confidence` to `1.0`
  - [x] 2.4 Serialize the classification dict as JSON string → `classification_json`
  - [x] 2.5 Return `classification_json` alongside existing `payload_json` and `status`
  - [x] 2.6 Build classification_json even when extraction fails (AC5) — classification metadata is independent of LlamaExtract success

- [x] Task 3: Modify `save_payload` node to persist `classificationPayload` and `detectedYear` (AC: 1, 5, 6)
  - [x] 3.1 Add `classificationPayload: "{{ state.classification_json }}"` to the `updates` dict in `graphology.update_node`
  - [x] 3.2 Add `detectedYear: "{{ state.detected_year }}"` to the `updates` dict
  - [x] 3.3 Verify `graphology.update_node` handles 3 fields in `updates` (payload + classificationPayload + detectedYear) — current implementation iterates `updates.items()` so this works out of the box

- [x] Task 4: Create ontology properties via GraphQL (AC: 7, 8)
  - [x] 4.1 Document the GraphQL mutations to create `classificationPayload` OntologyProperty and connect to ApplicationFormFile via `HAS_PROPERTY`
  - [x] 4.2 Document the GraphQL mutations to create `detectedYear` OntologyProperty and connect to ApplicationFormFile via `HAS_PROPERTY`
  - [x] 4.3 Note: These are manual one-time operations executed against graphology, NOT code changes. Include the exact mutations in the story for the dev agent to document/execute.

- [x] Task 5: Add tests for classification metadata persistence (AC: 1-6, 9)
  - [x] 5.1 Test `prepare_payload` with TIER 1 classification: `directory_source="filename"`, no `classification_result` → `classification_json` has `confidence: 1.0`, LLM fields null
  - [x] 5.2 Test `prepare_payload` with TIER 2 classification: `directory_source="content"` → same as 5.1 pattern
  - [x] 5.3 Test `prepare_payload` with TIER 3 classification: `classification_result` populated → `classification_json` includes all LLM fields
  - [x] 5.4 Test `prepare_payload` with fast-path (input): `directory_source="input"` → `classification_json` has `confidence: 1.0`
  - [x] 5.5 Test `prepare_payload` on extraction failure: `extract_result.success=false` → `classification_json` still built
  - [x] 5.6 Test `prepare_payload` with detected_year from various sources (filename, content, llm, input)
  - [x] 5.7 Test `save_payload` YAML node `updates` contains all three fields: `payload`, `classificationPayload`, `detectedYear`
  - [x] 5.8 Test existing `prepare_payload` and `finalize` tests still pass (no regression)

## Dev Notes

### Critical: Minimal Changes Required

This story is the simplest of Epic 16 — it only modifies two existing nodes (`prepare_payload` and `save_payload`) and adds one state field. **No new nodes, no new files, no new actions.** The heavy lifting was done in Stories 16.1 (detection) and 16.2 (LLM classification). Story 16.3 just persists what they already computed.

### Critical: prepare_payload Node Changes

The current `prepare_payload` node (lines 232-249 in `file_extraction.yaml`) only builds `payload_json` from `extract_result`. It needs to ALSO build `classification_json` from the classification state fields.

**Current prepare_payload output:**
```python
return {"payload_json": payload, "status": "success"}
```

**New prepare_payload output (after changes):**
```python
# Build classification metadata
classification = {
    "directory": state.get("directory_name", ""),
    "directory_source": state.get("directory_source", ""),
    "detected_year": state.get("detected_year"),
    "year_source": state.get("year_source", ""),
}

# Merge LLM classification fields if available (TIER 3)
classification_result = state.get("classification_result")
if classification_result:
    if isinstance(classification_result, str):
        classification_result = json.loads(classification_result)
    classification["category"] = classification_result.get("category")
    classification["region_country"] = classification_result.get("region_country")
    classification["region_country_display"] = classification_result.get("region_country_display")
    classification["region_state"] = classification_result.get("region_state")
    classification["region_state_display"] = classification_result.get("region_state_display")
    classification["confidence"] = classification_result.get("confidence")
    classification["is_empty_form"] = classification_result.get("is_empty_form")
else:
    # TIER 1/2 or fast-path — no LLM fields, confidence=1.0 (exact match)
    classification["category"] = None
    classification["region_country"] = None
    classification["region_country_display"] = None
    classification["region_state"] = None
    classification["region_state_display"] = None
    classification["confidence"] = 1.0
    classification["is_empty_form"] = None

classification_json = json.dumps(classification)

return {
    "payload_json": payload,
    "classification_json": classification_json,
    "status": "success"  # or "error" as before
}
```

**IMPORTANT:** The classification_json must be built REGARDLESS of whether extraction succeeded or failed. Even on error, the classification metadata (which tier detected the directory, year info) is valuable and should be persisted.

### Critical: save_payload Node Changes

The current `save_payload` node (lines 252-260 in `file_extraction.yaml`) uses `graphology.update_node` with a single field in `updates`. Expand to three fields:

**Current:**
```yaml
- name: save_payload
  uses: graphology.update_node
  with:
    node_id: "{{ state.context_node_id }}"
    node_type: "ApplicationFormFile"
    updates:
      payload: "{{ state.payload_json }}"
    graphql_url: "{{ variables.GRAPHOLOGY_URL }}"
  output: update_result
```

**New:**
```yaml
- name: save_payload
  uses: graphology.update_node
  with:
    node_id: "{{ state.context_node_id }}"
    node_type: "ApplicationFormFile"
    updates:
      payload: "{{ state.payload_json }}"
      classificationPayload: "{{ state.classification_json }}"
      detectedYear: "{{ state.detected_year }}"
    graphql_url: "{{ variables.GRAPHOLOGY_URL }}"
  output: update_result
```

**Verification:** `graphology.update_node` (in `actions/graphology.py:806-892`) iterates `updates.items()` and builds `field_SET` entries for each — three fields work identically to one. No changes needed in the action.

### Critical: Ontology Property Creation (Manual Step)

Before running the agent with these changes, two new OntologyProperty nodes must exist on ApplicationFormFile. Execute these GraphQL mutations against graphology:

**Create classificationPayload property:**
```graphql
mutation {
  createOntologyProperties(input: [{
    name: "classificationPayload"
    type: "String"
    hasPropertyFrom: {
      connect: [{
        where: { node: { name: "ApplicationFormFile" } }
      }]
    }
  }]) {
    ontologyProperties { id name }
  }
}
```

**Create detectedYear property:**
```graphql
mutation {
  createOntologyProperties(input: [{
    name: "detectedYear"
    type: "String"
    hasPropertyFrom: {
      connect: [{
        where: { node: { name: "ApplicationFormFile" } }
      }]
    }
  }]) {
    ontologyProperties { id name }
  }
}
```

Then restart graphology to regenerate the GraphQL schema with the new fields.

**Note:** This follows the same pattern as the `websitePayload` property creation documented in Story 1.3 for LegalFirm nodes.

### State Schema Addition

Story 16.3 adds only ONE new field to state_schema:
- `classification_json` (str) — Serialized JSON string of classification metadata for GraphQL persistence

All other classification fields (`directory_source`, `detected_year`, `year_source`, `classification_result`) were already added by Stories 16.1 and 16.2.

### Classification Metadata JSON Structure

The `classificationPayload` field stored on ApplicationFormFile will contain a JSON string with this structure:

```json
{
  "directory": "chambers",
  "directory_source": "filename",
  "detected_year": "2025",
  "year_source": "filename",
  "category": null,
  "region_country": null,
  "region_country_display": null,
  "region_state": null,
  "region_state_display": null,
  "confidence": 1.0,
  "is_empty_form": null
}
```

For TIER 3 (LLM), all fields are populated:
```json
{
  "directory": "chambers",
  "directory_source": "llm",
  "detected_year": "2025",
  "year_source": "llm",
  "category": "Corporate/M&A",
  "region_country": "BRA",
  "region_country_display": "Brazil",
  "region_state": "BR-SP",
  "region_state_display": "São Paulo",
  "confidence": 0.95,
  "is_empty_form": false
}
```

### Flow After Story 16.3 Changes

No flow changes — the node execution order is unchanged from Story 16.2. Only the internal logic of `prepare_payload` and the config of `save_payload` are modified:

```
__start__ → fetch_file_node → extract_and_download
  ├── (has storage_url) → detect_directory
  │     ├── (has directory_name) → resolve_agent → run_extraction → prepare_payload* → save_payload* → finalize → __end__
  │     ├── (no directory after TIER 1+2) → invoke_classification → process_classification → resolve_agent → ...
  │     └── (directory found, year missing) → invoke_classification (known-dir mode) → process_classification → resolve_agent → ...
  └── (no storage_url) → __end__

* = modified by Story 16.3
```

### Existing Test Impact

Existing `prepare_payload` tests in `tests/test_file_extraction.py` (lines 391-447) test `payload_json` and `status` output. They will continue to pass because `classification_json` is an ADDITIONAL field in the return dict — existing assertions still hold. However, tests should be EXTENDED to verify `classification_json` is present.

The `save_payload` is a `uses: graphology.update_node` node (not a `run:` block), so it's tested via integration/YAML structure validation rather than direct Python execution. Verify the YAML `updates` dict contains all three keys.

### Dependencies

**Story 16.1 (TIER 1+2) MUST be implemented first** — provides `directory_source`, `detected_year`, `year_source` state fields.

**Story 16.2 (TIER 3 LLM) MUST be implemented first** — provides `classification_result` state field.

Story 16.3 reads from state fields set by 16.1 and 16.2. Without them, `classification_json` would contain empty/null values for everything.

### Previous Story Intelligence

**From Story 16.1:**
- `prepare_payload` runs AFTER `detect_directory` — all detection state fields are populated by then
- `detect_directory` sets: `directory_name`, `directory_source`, `detected_year`, `year_source`
- Test helper `_exec_node()` can test `prepare_payload` directly with mocked state

**From Story 16.2:**
- `classification_result` is a JSON string stored in state from `process_classification` node
- Contains: `directory`, `category`, `region_country`, `region_state`, `confidence`, `year`, `is_empty_form`, plus display names
- May need `json.loads()` if stored as string (check Story 16.2 implementation)

**From Story 1.3 (websitePayload):**
- Same ontology property creation pattern — create OntologyProperty, connect via HAS_PROPERTY, restart graphology
- Validates that `graphology.update_node` correctly handles custom String properties on nodes

### Git Intelligence

Recent commits confirm:
- `graphology.update_node` works correctly for persisting JSON strings to node properties (used by `scrape_law_firm` agent for `websitePayload`)
- Test patterns: `_exec_node()` helper with mocked state is the standard approach
- All YAML agent modifications follow the same edit-in-place pattern

### Project Structure Notes

- **Modified file:** `agents/file_extraction.yaml` — Add `classification_json` to state_schema, modify `prepare_payload` run block, modify `save_payload` updates
- **Modified file:** `tests/test_file_extraction.py` — Add new test class `TestClassificationMetadataPersistence` with 7-8 tests
- **No new files** — this story only modifies existing files
- **No changes to:** `app.py`, `actions/graphology.py`, or any other file

### References

- [Source: agents/file_extraction.yaml:232-249] — `prepare_payload` node to modify (add classification_json building)
- [Source: agents/file_extraction.yaml:252-260] — `save_payload` node to modify (add classificationPayload + detectedYear to updates)
- [Source: agents/file_extraction.yaml:44-61] — state_schema to extend with `classification_json`
- [Source: actions/graphology.py:806-892] — `update_node` action (handles multiple fields in updates dict — no changes needed)
- [Source: tests/test_file_extraction.py:391-447] — Existing `prepare_payload` tests to extend
- [Source: _bmad-output/planning-artifacts/epics.md:520-563] — Story 16.3 requirements and implementation notes
- [Source: _bmad-output/implementation-artifacts/16-1-tier-1-2-filename-content-pattern-detection.md] — Story 16.1 state field additions
- [Source: _bmad-output/implementation-artifacts/16-2-tier-3-llm-classification-agent.md] — Story 16.2 classification_result field
- [Source: _bmad-output/implementation-artifacts/1-3-persist-extraction-result-to-websitepayload.md] — Ontology property creation pattern reference

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

None — clean implementation with no debugging needed.

### Completion Notes List

- Task 1: Added `classification_json` (str) field to `state_schema` in `file_extraction.yaml`
- Task 2: Modified `prepare_payload` node to build `classification_json` from state fields (`directory_name`, `directory_source`, `detected_year`, `year_source`) and merge LLM fields from `classification_result` when present (TIER 3). For TIER 1/2/input, LLM fields are `null` and `confidence=1.0`. Classification JSON is built regardless of extraction success/failure (AC5).
- Task 3: Modified `save_payload` node to persist `classificationPayload` and `detectedYear` alongside `payload` in `graphology.update_node` updates dict.
- Task 4: GraphQL mutations for `classificationPayload` and `detectedYear` OntologyProperty creation documented in story Dev Notes. These are manual one-time operations.
- Task 5: Added 10 new tests in `TestClassificationMetadataPersistence` class covering all ACs: TIER 1/2/3 (including dict classification_result), fast-path, extraction failure, year sources (including empty year edge case), save_payload YAML structure, and regression.
- All 66 file_extraction tests pass. 253 total project tests pass (1 pre-existing failure in `test_docker_deployment.py::test_has_docx2pdf` — unrelated).

### Change Log

- 2026-03-10: Story 16.3 implemented — classification metadata persistence (all 5 tasks complete)
- 2026-03-10: Code review — added 2 tests (dict classification_result path, empty detected_year edge case), fixed test count in completion notes

### File List

- agents/file_extraction.yaml (modified: state_schema + prepare_payload + save_payload)
- tests/test_file_extraction.py (modified: added TestClassificationMetadataPersistence class with 9 tests)
- _bmad-output/implementation-artifacts/sprint-status.yaml (modified: status tracking)
- _bmad-output/implementation-artifacts/16-3-persist-classification-metadata.md (modified: task checkboxes, dev record)
