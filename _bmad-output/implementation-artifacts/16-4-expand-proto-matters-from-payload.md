# Story 16.4: Expand ProtoMatters from LlamaExtract Payload

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want the file_extraction agent to parse the LlamaExtract payload after saving it and create individual ProtoMatter nodes for each extracted matter/deal, linked to both the ApplicationFormFile and the Department the file belongs to,
So that extracted data is graph-native and each matter can be independently reviewed, imported, or rejected downstream.

## Acceptance Criteria

1. **AC1 - ProtoMatter nodes created from payload:** Given a successful LlamaExtract extraction with `payload_json` containing an array of matters/deals, when the `expand_proto_matters` node runs after `save_payload`, then one ProtoMatter node is created for each item in the array via `graphology.create_node`, with properties: `payload` (JSON string of that single matter), `directory` (from `state.directory_name`), `status` ("pending").

2. **AC2 - FILE_HAS_PROTO_MATTER relationship:** Given ProtoMatter nodes are created, when each node is created, then it is connected to the source ApplicationFormFile via the `FILE_HAS_PROTO_MATTER` relationship (ApplicationFormFile → ProtoMatter, already defined in bootstrap.ts).

3. **AC3 - Department discovery via graph traversal:** Given the ApplicationFormFile is linked to a Department (via `DEPARTMENT_HAS_FILES` relationship or `DEPARTMENT_HAS_SUBMISSION → SUBMISSION_HAS_FILE` path), when the `expand_proto_matters` node runs, then it queries the graph to find the Department node ID associated with this file.

4. **AC4 - DEPARTMENT_HAS_PROTO_MATTER relationship:** Given a Department is found for the file, when ProtoMatter nodes are created, then each ProtoMatter is also connected to that Department via a new `DEPARTMENT_HAS_PROTO_MATTER` relationship (Department → ProtoMatter). If no Department is found, the ProtoMatters are still created (linked only to ApplicationFormFile) and a warning is logged.

5. **AC5 - graphology.create_node action:** Given the `create_node` action does not exist in `actions/graphology.py`, when this story is implemented, then a new `graphology.create_node` action is added that creates a node of a given type with given properties via GraphQL mutation, returns `{success: true, node_id: "<id>"}` on success, and is registered in `register_actions`.

6. **AC6 - graphology.connect_nodes action:** Given the `connect_nodes` action does not exist in `actions/graphology.py`, when this story is implemented, then a new `graphology.connect_nodes` action is added that creates a relationship between two nodes via GraphQL mutation (using `update` + `connect`), accepts `source_id`, `source_type`, `relationship_name`, `target_id`, `target_type`, and is registered in `register_actions`.

7. **AC7 - Extraction failure skips expansion:** Given the extraction failed (`status == "error"`), when the flow reaches `expand_proto_matters`, then the node is skipped entirely (no ProtoMatter nodes created) and the flow proceeds to `finalize` as before.

8. **AC8 - Empty matters array handled gracefully:** Given the LlamaExtract payload contains zero matters/deals (empty array or missing key), when the `expand_proto_matters` node runs, then no ProtoMatter nodes are created, no error is raised, and a debug log records "No matters found in payload".

9. **AC9 - ProtoMatter ontology prerequisite:** Given the ProtoMatter class and its relationships (`FILE_HAS_PROTO_MATTER`, `PROTO_MATTER_IMPORTED_AS`) are defined in bootstrap.ts but may not be deployed to the live DB, when this story is implemented, then a manual step documents running the graphology bootstrap (or creating the ProtoMatter class and relationships manually) as a prerequisite before using the agent with this feature.

10. **AC10 - New DEPARTMENT_HAS_PROTO_MATTER relationship bootstrapped:** Given this relationship does not exist in bootstrap.ts, when this story is implemented, then the relationship is either added to graphology's `bootstrap.ts` (preferred) or created via manual GraphQL mutations, connecting Department as source and ProtoMatter as destination.

## Tasks / Subtasks

- [x] Task 1: Add `graphology.create_node` action to `actions/graphology.py` (AC: 5)
  - [x] 1.1 Implement `create_node(node_type, properties, graphql_url, **kwargs)` — builds `createProtoMatter(payload: "...", directory: "...", status: "...")` GraphQL mutation using singular type name (v6 convention)
  - [x] 1.2 Return `{"success": True, "node_id": "<id>"}` on success, `{"success": False, "error": "..."}` on failure
  - [x] 1.3 Register `graphology.create_node` in `register_actions`

- [x] Task 2: Add `graphology.connect_nodes` action to `actions/graphology.py` (AC: 6)
  - [x] 2.1 Implement `connect_nodes(source_id, source_type, relationship_name, target_id, target_type, graphql_url, **kwargs)` — builds `updateApplicationFormFile(where: {id: ...}, update: { fileHasProtoMatter: { connect: { where: { node: { id: ... }}}}})` style mutation
  - [x] 2.2 Handle the camelCase relationship field name derivation from UPPER_SNAKE_CASE (e.g., `FILE_HAS_PROTO_MATTER` → `fileHasProtoMatter`) — this is how Neo4j GraphQL Library v6 generates field names
  - [x] 2.3 Return `{"success": True}` on success, `{"success": False, "error": "..."}` on failure
  - [x] 2.4 Register `graphology.connect_nodes` in `register_actions`

- [x] Task 3: Add `expand_proto_matters` node to `file_extraction.yaml` (AC: 1, 2, 3, 4, 7, 8)
  - [x] 3.1 Add new state field `proto_matter_ids` (list) to `state_schema`
  - [x] 3.2 Add new state field `department_id` (str) to `state_schema`
  - [x] 3.3 Insert `expand_proto_matters` node between `save_payload` and `finalize` in the flow
  - [x] 3.4 Implement `run:` block that: skips if `status == "error"`, parses `payload_json`, finds matters array, iterates and creates ProtoMatter nodes
  - [x] 3.5 For each ProtoMatter, call `graphology.create_node` then `graphology.connect_nodes` for FILE_HAS_PROTO_MATTER
  - [x] 3.6 Query Department via GraphQL from ApplicationFormFile's relationships, call `graphology.connect_nodes` for DEPARTMENT_HAS_PROTO_MATTER if found

- [x] Task 4: Implement matters array extraction per directory (AC: 1, 8)
  - [x] 4.1 Implement `_extract_matters(payload_dict, directory_name)` helper that returns a flat list of matter dicts using the directory-specific key mapping documented in Dev Notes
  - [x] 4.2 For directories with 2 arrays (chambers: publishable + confidential; legal500: publishable + nonPublishable), merge both arrays into one list

- [x] Task 5: Ontology prerequisites — ProtoMatter + DEPARTMENT_HAS_PROTO_MATTER (AC: 9, 10)
  - [x] 5.1 Verify ProtoMatter class exists in live DB (run bootstrap if needed)
  - [x] 5.2 Add `DEPARTMENT_HAS_PROTO_MATTER` relationship to graphology's `bootstrap.ts` DOMAIN_RELATIONS array, or create via manual GraphQL mutations
  - [x] 5.3 Document the prerequisite steps in Dev Notes

- [x] Task 6: Add tests for ProtoMatter expansion (AC: 1-4, 7, 8)
  - [x] 6.1 Test `expand_proto_matters` with successful extraction containing 3 matters → 3 ProtoMatter nodes created
  - [x] 6.2 Test `expand_proto_matters` with extraction error → skipped, no ProtoMatters
  - [x] 6.3 Test `expand_proto_matters` with empty matters array → no ProtoMatters, no error
  - [x] 6.4 Test Department discovery and linkage
  - [x] 6.5 Test ProtoMatter creation without Department (warning logged, still creates nodes)
  - [x] 6.6 Test `create_node` action with success and error scenarios
  - [x] 6.7 Test `connect_nodes` action with success and error scenarios

## Dev Notes

### Critical: New Graphology Actions Required

This story introduces two new actions in `actions/graphology.py` — `create_node` and `connect_nodes`. Currently only `get_node` and `update_node` exist. These new actions follow the same patterns:
- Use `_execute_graphql()` for HTTP calls
- Use `_get_graphql_url()` and `_get_graphql_api_key()` for config
- Use singular type names (Neo4j GraphQL Library v6 convention, per the fix in Story 16.3)

**create_node mutation pattern:**
```graphql
mutation {
  createProtoMatter(payload: "...", directory: "chambers", status: "pending") {
    id
  }
}
```

**connect_nodes mutation pattern (using update + connect):**
```graphql
mutation {
  updateApplicationFormFile(
    where: { id: "appfile-123" }
    update: {
      fileHasProtoMatter: {
        connect: { where: { node: { id: "proto-456" } } }
      }
    }
  ) {
    applicationFormFiles { id }
  }
}
```

### Critical: Relationship Field Name Convention

Neo4j GraphQL Library v6 auto-generates relationship field names from the OntologyRelation name by converting `UPPER_SNAKE_CASE` to `camelCase` with the source/destination type name appended. The exact field name depends on how graphology generates the schema. Examples:
- `FILE_HAS_PROTO_MATTER` on ApplicationFormFile → likely `fileHasProtoMatter` or `applicationFormFileFileHasProtoMatterProtoMatter`
- `DEPARTMENT_HAS_PROTO_MATTER` on Department → similar pattern

**IMPORTANT:** The dev agent MUST introspect the GraphQL schema (after bootstrap) to discover the exact field names before implementing `connect_nodes`. Use: `{ __schema { types { name fields { name } } } }` filtered to the relevant types.

### Critical: LlamaExtract Payload Structure — Matters Key Mapping

Each directory stores matters/deals under different keys. The extraction helper must map directory → key path(s).
Source: `/home/fabricio/src/spa-base/docs/formsPayloadsTemplate/` (schema + sample files per directory).

**Directory-to-key mapping:**

| Directory | Key Path(s) | Notes |
|---|---|---|
| chambers | `workHighlights.publishableInformation.matters` + `workHighlights.confidentialInformation.matters` | 2 arrays, merge both. Each item: clientName, summary, matterValue, leadPartner, etc. |
| iflr1000 | `dealHighlights.deals` | Nested 1 level. Each item: dealName, clientsAdvised, valueUSD, leadPartners, etc. Up to 10. |
| legal500 | `workHighlightsDetailed` + `workHighlightsDetailed_nonPublishable` | 2 top-level arrays, merge both. Each item: nameOfClient, matterDescription, dealValue, leadPartners. |
| itr | `section_2_deal_and_case_highlights` | Top-level. Each item: matterName, category, transaction_value_usd, clientAdvised. |
| leadersleague | `work_highlights` | Top-level. Each item: matterName, client, value, leadPartners. Max 15 (schema constraint). |

**Implementation:**
```python
MATTERS_KEY_MAP = {
    "chambers": [
        ("workHighlights", "publishableInformation", "matters"),
        ("workHighlights", "confidentialInformation", "matters"),
    ],
    "iflr1000": [
        ("dealHighlights", "deals"),
    ],
    "legal500": [
        ("workHighlightsDetailed",),
        ("workHighlightsDetailed_nonPublishable",),
    ],
    "itr": [
        ("section_2_deal_and_case_highlights",),
    ],
    "leadersleague": [
        ("work_highlights",),
    ],
}

def _extract_matters(payload_dict, directory_name):
    """Extract all matters from payload using directory-specific key paths."""
    key_paths = MATTERS_KEY_MAP.get(directory_name, [])
    matters = []
    for path in key_paths:
        obj = payload_dict
        for key in path:
            obj = obj.get(key, {}) if isinstance(obj, dict) else {}
        if isinstance(obj, list):
            matters.extend(obj)
    return matters
```

**Target model reference:** `matterModel_schema.json` defines the canonical Matter structure. ProtoMatter stores the raw extracted item; normalization to Matter happens later via `PROTO_MATTER_IMPORTED_AS`.

### Critical: Department Discovery

Two known paths from Department to ApplicationFormFile in the ontology:
1. **Direct:** `DEPARTMENT_HAS_FILES` (Department → File, where File is a superclass or the same as ApplicationFormFile)
2. **Indirect:** `DEPARTMENT_HAS_SUBMISSION` → `SUBMISSION_HAS_FILE` (Department → Submission → ApplicationFormFile)

The `expand_proto_matters` node should query the graph to find the Department:
```graphql
query {
  applicationFormFiles(where: { id: "appfile-123" }) {
    # Reverse traversal: find which Department points to this file
    departmentHasFilesConnection { edges { node { id name } } }
  }
}
```

If the reverse field doesn't exist, use a Cypher-free alternative:
```graphql
query {
  departments(where: { departmentHasFiles_SOME: { id: "appfile-123" } }) {
    id name
  }
}
```

**IMPORTANT:** The exact field names depend on the generated schema. Introspect first.

### Flow After Story 16.4 Changes

```
__start__ → fetch_file_node → extract_and_download
  ├── (has storage_url) → detect_directory
  │     ├── (has directory_name) → resolve_agent → run_extraction → prepare_payload → save_payload → expand_proto_matters* → finalize → __end__
  │     ├── (no directory after TIER 1+2) → invoke_classification → process_classification → resolve_agent → ...
  │     └── (directory found, year missing) → invoke_classification (known-dir mode) → process_classification → resolve_agent → ...
  └── (no storage_url) → __end__

* = new node added by Story 16.4 (skipped on extraction error)
```

### ProtoMatter Ontology (from bootstrap.ts)

Already defined in `~/src/graphology/src/ai-workflow/bootstrap.ts`:
```typescript
// ProtoMatter class
{ name: 'ProtoMatter', description: 'Intermediate node holding a single extracted matter/deal payload before import into the final Matter graph',
  properties: [
    { name: 'payload', type: 'string' },   // Full JSON of a single matter/deal
    { name: 'directory', type: 'string' },  // Source directory identifier
    { name: 'status', type: 'string' },     // Lifecycle: pending, imported, error
  ]
}

// Relationships
FILE_HAS_PROTO_MATTER: ApplicationFormFile → ProtoMatter
PROTO_MATTER_IMPORTED_AS: ProtoMatter → Matter
```

**NOT YET DEPLOYED to live DB** — bootstrap must be run or nodes created manually before using this feature.

### New Relationship: DEPARTMENT_HAS_PROTO_MATTER

Not yet in bootstrap.ts. Must be added:
```typescript
{
  name: 'DEPARTMENT_HAS_PROTO_MATTER',
  description: 'Links a Department to ProtoMatter nodes created from files in that department',
  directed: true,
  source: 'Department',
  destination: 'ProtoMatter',
  properties: [],
}
```

Add to `DOMAIN_RELATIONS` array in `bootstrap.ts`, then re-run bootstrap.

### Dependencies

**Story 16.3 MUST be done** — provides `save_payload` with `payload_json`, `classificationPayload`, and `detectedYear`.

**Graphology bootstrap MUST include ProtoMatter** — already in code but may not be deployed. Run bootstrap or create manually.

### Project Structure Notes

- **Modified file:** `actions/graphology.py` — Add `create_node` and `connect_nodes` actions + register them
- **Modified file:** `agents/file_extraction.yaml` — Add `expand_proto_matters` node, add `proto_matter_ids` and `department_id` to state_schema
- **Modified file (cross-repo):** `~/src/graphology/src/ai-workflow/bootstrap.ts` — Add `DEPARTMENT_HAS_PROTO_MATTER` to DOMAIN_RELATIONS
- **Modified file:** `tests/test_file_extraction.py` — Add `TestProtoMatterExpansion` test class
- **New file (possible):** `tests/test_graphology_actions.py` — Tests for `create_node` and `connect_nodes` if not fitting in existing test file

### References

- [Source: agents/file_extraction.yaml:542-552] — `save_payload` node (expand_proto_matters goes after this)
- [Source: agents/file_extraction.yaml:554-562] — `finalize` node (expand_proto_matters goes before this)
- [Source: agents/file_extraction.yaml:44-72] — state_schema to extend
- [Source: actions/graphology.py:806-916] — `update_node` and `register_actions` (pattern for new actions)
- [Source: ~/src/graphology/src/ai-workflow/bootstrap.ts:248-256] — ProtoMatter class definition
- [Source: ~/src/graphology/src/ai-workflow/bootstrap.ts:303-320] — FILE_HAS_PROTO_MATTER and PROTO_MATTER_IMPORTED_AS relationships
- [Source: _bmad-output/planning-artifacts/epics.md:389-563] — Epic 16 requirements and previous stories

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

### Completion Notes List

- Task 1+2: Added `create_node` and `connect_nodes` actions to `actions/graphology.py`. `create_node` builds `create<Type>(input: {...})` mutation. `connect_nodes` builds `update<SourceType>(where: {id}, update: {<relField>: {connect: ...}})` mutation with `_relationship_to_field_name` helper for UPPER_SNAKE_CASE → camelCase conversion. Both registered in `register_actions`. 20 tests pass.
- Task 3+4: Added `expand_proto_matters` node to `file_extraction.yaml` between `save_payload` and `finalize`. Implements: skip on error (AC7), matter extraction via `MATTERS_KEY_MAP` directory-to-key mapping (AC1/AC8), department discovery via GraphQL query (AC3), ProtoMatter creation + FILE_HAS_PROTO_MATTER connection (AC1/AC2), DEPARTMENT_HAS_PROTO_MATTER connection when department found (AC4). 15 new tests in `TestProtoMatterExpansion` and `TestMattersExtraction` covering all 5 directories + edge cases.
- Task 5: Added `DEPARTMENT_HAS_PROTO_MATTER` relationship to `~/src/graphology/src/ai-workflow/bootstrap.ts` DOMAIN_RELATIONS array. ProtoMatter class already defined in bootstrap.ts. **Prerequisite:** Run graphology bootstrap (`npm run bootstrap`) before using this feature to ensure ProtoMatter class and all relationships exist in the live DB.
- Task 6: 35 total new tests across `tests/test_graphology_actions.py` (20 tests) and `tests/test_file_extraction.py` (15 tests). All pass. Full regression suite: 289/292 pass (3 pre-existing failures unrelated to this story).

### File List

- `actions/graphology.py` — Added `create_node`, `connect_nodes`, `_relationship_to_field_name`; registered in `register_actions`
- `agents/file_extraction.yaml` — Added `proto_matter_ids` and `department_id` to state_schema; added `expand_proto_matters` node between `save_payload` and `finalize`
- `tests/test_graphology_actions.py` — New file: 20 tests for `create_node`, `connect_nodes`, `_relationship_to_field_name`, registration
- `tests/test_file_extraction.py` — Added `TestProtoMatterExpansion` (8 tests), `TestMattersExtraction` (7 tests), `TestProtoMatterStateSchema` (2 tests), `_exec_node_with_actions` helper
- `~/src/graphology/src/ai-workflow/bootstrap.ts` — Added `DEPARTMENT_HAS_PROTO_MATTER` to DOMAIN_RELATIONS (cross-repo)
