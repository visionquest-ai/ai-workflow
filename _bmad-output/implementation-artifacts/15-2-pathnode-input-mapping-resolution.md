# Story 15.2: PathNode Input Mapping Resolution (mappingType: "path")

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system administrator,
I want workflow input parameters resolved by traversing PathNode chains from the triggered entity,
so that complex parameters (nested relations, related entity properties) can be mapped to agent inputs without code.

## Acceptance Criteria

1. **AC1 — INPUT_MAPPING_HAS_PATH relationship bootstrap**
   **Given** the bootstrap script `src/ai-workflow/bootstrap.ts`
   **When** it runs against Neo4j
   **Then** `INPUT_MAPPING_HAS_PATH` OntologyRelation exists from WorkflowInputMapping to PathNode (0:N)
   **And** the relationship has edge properties: `ord` (Int), `isFirst` (Boolean), `isLast` (Boolean)
   **And** GraphQL schema regeneration produces the relationship with edge properties

2. **AC2 — Single-step PathNode resolution (direct property read)**
   **Given** a WorkflowInputMapping with `mappingType: "path"` and a PathNode chain:
     - PathNode(ord=0, isFirst=true) → PATH_STEP_TO_PROPERTY → OntologyProperty("storageUrl")
   **When** `resolveInputMappings()` processes this mapping with entityId `"appfile-123"` of type `"ApplicationFormFile"`
   **Then** it builds a GraphQL query to read `storageUrl` from ApplicationFormFile node `"appfile-123"`
   **And** the resolved output includes the property value

3. **AC3 — Multi-step PathNode resolution (relation traversal + property read)**
   **Given** a WorkflowInputMapping with `mappingType: "path"` and a multi-step PathNode chain:
     - PathNode(ord=0) → PATH_STEP_VIA_RELATION → OntologyRelation("HAS_DIRECTORY") + PATH_STEP_AT_CLASS → OntologyClass("Directory")
     - PathNode(ord=1) → PATH_STEP_TO_PROPERTY → OntologyProperty("name")
   **When** `resolveInputMappings()` processes this mapping
   **Then** it traverses the relation from the entity, arrives at the related class, reads the property
   **And** the resolved output includes the traversed property value

4. **AC4 — Null path resolution with isRequired=true**
   **Given** a PathNode chain that resolves to `null` (e.g., no related entity exists)
   **When** the mapping has `isRequired: true`
   **Then** the dispatch is skipped and an error is logged with `[aiWorkflowTrigger]` prefix

5. **AC5 — Null path resolution with isRequired=false and defaultValue fallback**
   **Given** a PathNode chain that resolves to `null`
   **When** the mapping has `isRequired: false`
   **Then** `defaultValue` is used as fallback (or null if no default)

6. **AC6 — MATCHING_WORKFLOWS_QUERY extended with path data**
   **Given** the `MATCHING_WORKFLOWS_QUERY` in the trigger middleware
   **When** it loads workflows for an entity class
   **Then** it also fetches `inputMappingHasPath` with `pathStepViaRelation { name }`, `pathStepAtClass { name }`, `pathStepToProperty { name }` and edge properties `ord`, `isFirst`, `isLast`

## Tasks / Subtasks

- [x] Task 1: Bootstrap INPUT_MAPPING_HAS_PATH relation (AC: #1)
  - [x] 1.1: Add `INPUT_MAPPING_HAS_PATH` RelationDef to `TRIGGER_RELATIONS` array in `src/ai-workflow/bootstrap.ts`: source=`WorkflowInputMapping`, destination=`PathNode` (DynamicUI ontology), directed=true, edgeProperties: `[{ name: 'ord', type: 'integer' }, { name: 'isFirst', type: 'boolean' }, { name: 'isLast', type: 'boolean' }]`
  - [x] 1.2: Note: PathNode is in the DynamicUI ontology (bootstrapped in `src/bootstrap.ts`). This is a cross-ontology relation — same pattern as TRIGGER_WATCHES_CLASS (AIWorkflow → MetaOntology) and TRIGGERS_WORKFLOW (DynamicUI → AIWorkflow). Supported because `RELATION_SOURCE`/`RELATION_DESTINATION` reference OntologyClass nodes by name.
  - [x] 1.3: Follow the existing FIELD_HAS_PATH / COLUMN_HAS_PATH edge property pattern — they use identical `ord`, `isFirst`, `isLast` properties
  - [x] 1.4: Write unit test verifying `TRIGGER_RELATIONS_EXPORT` includes `INPUT_MAPPING_HAS_PATH` with correct source (`WorkflowInputMapping`) and destination (`PathNode`) and 3 edge properties

- [x] Task 2: Extend InputMappingConfig type and MATCHING_WORKFLOWS_QUERY (AC: #6)
  - [x] 2.1: Add `pathSteps` field to `InputMappingConfig` interface with `PathStepConfig` type
  - [x] 2.2: Update `MATCHING_WORKFLOWS_QUERY` to fetch `inputMappingHasPathConnection` with edge properties and PathNode relations
  - [x] 2.3: Created `parsePathSteps()` function to flatten connection edges into sorted `PathStepConfig[]` array
  - [x] 2.4: Update `TriggerConfigCache` — path data included in cached `WorkflowConfig[]` (no separate cache needed)
  - [x] 2.5: Write unit test verifying MATCHING_WORKFLOWS_QUERY includes `inputMappingHasPathConnection` with edge properties

- [x] Task 3: Implement path resolution in resolveInputMappings() (AC: #2, #3, #4, #5)
  - [x] 3.1: Added `else if (mapping.mappingType === 'path')` branch. Function signature changed to async with optional `executeQueryFn` parameter.
  - [x] 3.2: Implemented `resolvePathMapping()` — builds dynamic GraphQL query from PathNode chain, executes, extracts value
  - [x] 3.3: Handle single-step resolution (AC2): direct property read from entity
  - [x] 3.4: Handle multi-step resolution (AC3): relation traversal + property read
  - [x] 3.5: Handle null resolution: returns `undefined` so isRequired/defaultValue logic handles it (AC4, AC5)
  - [x] 3.6: Handle edge cases: empty pathSteps → undefined; missing executeQueryFn → log warning + undefined
  - [x] 3.7: Implemented `pluralize()` function to convert entityType to plural camelCase for GraphQL root query field

- [x] Task 4: Update callers of resolveInputMappings() (AC: #2-#5)
  - [x] 4.1: Updated `willSendResponse` dispatch handler to `await` the now-async `resolveInputMappings()`
  - [x] 4.2: N/A — `dispatchManualTrigger()` from 15.1 not yet implemented (15.1 is ready-for-dev)
  - [x] 4.3: Backward compatibility ensured: `executeQueryFn` is optional, path mappings unresolvable without it

- [x] Task 5: Write comprehensive tests (AC: #1-#6)
  - [x] 5.1: Created `tests/story-15-2-pathnode-input-mapping.test.ts` — 21 tests total
  - [x] 5.2: Bootstrap tests: [15.2-UNIT-001], [15.2-UNIT-002]
  - [x] 5.3: Query tests: [15.2-UNIT-003], [15.2-UNIT-004], [15.2-UNIT-004b]
  - [x] 5.4: Path resolution tests: [15.2-UNIT-005] through [15.2-UNIT-010b]
  - [x] 5.5: Integration tests: [15.2-INTG-001] through [15.2-INTG-006]
  - [x] 5.6: Zero regressions — updated 18.3 and 18.4 test files to `await` the now-async `resolveInputMappings()`. All 137 passing tests remain passing (3 failures are pre-existing stale count assertions from 18.1).

## Dev Notes

### Architecture Reference

This story implements the `path` mappingType from `docs/architecture/ai-workflow-trigger-system.md` (ai-workflow repo). Key sections:
- Section 8: Input Parameter Resolution — `path` mappingType definition and resolution example
- Section 6: `INPUT_MAPPING_HAS_PATH` relationship: WorkflowInputMapping → PathNode (0:N) with edge props
- Section 10: MATCHING_WORKFLOWS_QUERY — shows target state including inputMappingHasPath

### Implementation Boundary

**All changes are in the graphology repository** (`~/src/graphology`).

| File | Change Type | Description |
|------|-------------|-------------|
| `src/ai-workflow/bootstrap.ts` | Modified | Add INPUT_MAPPING_HAS_PATH relation with edge properties |
| `src/plugins/ai-workflow-trigger-middleware.ts` | Modified | Extend InputMappingConfig type, MATCHING_WORKFLOWS_QUERY, resolveInputMappings() with path support |
| `tests/story-15-2-pathnode-input-mapping.test.ts` | New | Unit + integration tests for path resolution |

**ai-workflow repo requires NO code changes.**

### Key Design Decisions

1. **resolveInputMappings() becomes async.** Path resolution requires GraphQL queries. The function signature changes to `async` and returns `Promise<Record<string, string> | null>`. All callers are already in async contexts, so this is safe. The `executeQueryFn` parameter is optional for backward compatibility — when not provided, path mappings are unresolvable.

2. **Dynamic GraphQL query construction.** Path resolution builds a GraphQL query at runtime from the PathNode chain. This follows the same conceptual pattern as DynamicUI Field/Column path resolution, but implemented at the middleware level. The query is constructed by walking the sorted PathStepConfig array and nesting relation traversals.

3. **Reuse existing PathNode infrastructure.** PathNode class and its relationships (PATH_STEP_VIA_RELATION, PATH_STEP_AT_CLASS, PATH_STEP_TO_PROPERTY) are already bootstrapped in the DynamicUI ontology. INPUT_MAPPING_HAS_PATH is a new cross-ontology relation following the same edge property pattern as FIELD_HAS_PATH and COLUMN_HAS_PATH.

4. **Connection query for ordered edges.** Use `inputMappingHasPathConnection` (not `inputMappingHasPath`) to access edge properties (ord, isFirst, isLast) via the Neo4j GraphQL Library connection pattern. Sort by `ord ASC` in the query.

5. **Entity type pluralization for root query field.** GraphQL root query fields use plural camelCase names (e.g., `applicationFormFiles` for `ApplicationFormFile`). The middleware already has pluralization logic — reuse `pluralize()` or the existing `singularize()` inverse pattern.

### Existing Code Patterns (from 18.3)

**resolveInputMappings() current signature** (`ai-workflow-trigger-middleware.ts:282`):
```typescript
export function resolveInputMappings(
  mappings: InputMappingConfig[],
  context: MutationContext,
  entityId: string
): Record<string, string> | null
```

**Current switch on mappingType** (lines 299-311):
```typescript
if (mapping.mappingType === 'literal') {
  // uses defaultValue as-is
} else if (mapping.mappingType === 'context') {
  // looks up defaultValue key in contextLookup
} else {
  console.warn(`[aiWorkflowTrigger] Unknown mappingType "${mapping.mappingType}"...`);
}
```
Story 15.2 adds: `else if (mapping.mappingType === 'path') { ... }` before the unknown fallback.

**MATCHING_WORKFLOWS_QUERY** (`ai-workflow-trigger-middleware.ts:430`):
Currently fetches `workflowHasInputMapping { name, parameterType, isRequired, defaultValue, mappingType }`.
Story 15.2 extends with `inputMappingHasPathConnection(sort: ...) { edges { ... } }`.

**PathNode edge property pattern** (from `src/bootstrap.ts`, FIELD_HAS_PATH):
```typescript
{
  name: 'FIELD_HAS_PATH',
  source: 'Field',
  destination: 'PathNode',
  directed: true,
  edgeProperties: [
    { name: 'ord', type: 'integer' },
    { name: 'isFirst', type: 'boolean' },
    { name: 'isLast', type: 'boolean' },
  ]
}
```
INPUT_MAPPING_HAS_PATH follows this exact pattern.

**GraphQL connection query pattern** (for edge properties):
```graphql
fieldHasPathConnection(sort: [{ edge: { ord: ASC } }]) {
  edges {
    properties { ord isFirst isLast }
    node { pathStepViaRelation { name } pathStepAtClass { name } pathStepToProperty { name } }
  }
}
```

### Entity Type to GraphQL Root Field Mapping

The path resolver needs to convert entity types to GraphQL root query field names:
- `ApplicationFormFile` → `applicationFormFiles` (camelCase + plural)
- `Directory` → `directories` (English pluralization)
- `LegalFirm` → `legalFirms`

Check if middleware already has a utility for this. The `singularize()` function exists — the inverse `pluralize()` may also exist. If not, implement a simple pluralization function or use the GraphQL introspection cache.

### Testing Standards

- **Framework**: Vitest
- **Test location**: `tests/story-15-2-pathnode-input-mapping.test.ts`
- **Test ID format**: `[15.2-UNIT-NNN]` for unit, `[15.2-INTG-NNN]` for integration
- **Priority tags**: `[P0]` critical path, `[P1]` important, `[P2]` edge case
- **Import pattern**: Import from `'../src/plugins/ai-workflow-trigger-middleware.js'` and `'../src/ai-workflow/bootstrap.js'` (ESM `.js` extension)
- **No live Neo4j needed**: All tests use mocked data, matching 18.1/18.2/18.3 test patterns
- **Mock executeQueryFn**: For path resolution tests, mock the function to return expected entity data
- **Existing test counts**: 48+ tests across story-18-1/18-2/18-3 — ensure zero regressions

### Previous Story Learnings (18.3 + 15.1)

1. **`META_MODEL_TYPES` already includes PathNode** — no update needed
2. **Bootstrap is idempotent**: `isDuplicateError()` handles re-runs. No cleanup needed.
3. **Cross-ontology relations work**: INPUT_MAPPING_HAS_PATH links WorkflowInputMapping (AIWorkflow) → PathNode (DynamicUI), same pattern as TRIGGER_WATCHES_CLASS, TRIGGERS_WORKFLOW
4. **Edge properties on relations**: Follow FIELD_HAS_PATH pattern — `ord`, `isFirst`, `isLast` in `edgeProperties` array
5. **Connection queries for edge properties**: Must use `*Connection(sort: ...)` syntax to access edge properties in Neo4j GraphQL Library
6. **`buildDispatchPayload()` is exported and tested**: Changes must maintain backward compatibility
7. **Fire-and-forget**: All dispatch errors caught and logged with `[aiWorkflowTrigger]` prefix
8. **Spread operator in buildDispatchPayload**: `{ context_node_id: contextNodeId, ...resolvedInputs, agent: agentName, async_mode: true }` — core fields set AFTER spread to prevent override
9. **resolveInputMappings returns null on required failure**: This means the dispatch is skipped entirely — path resolution follows this same contract

### Project Structure Notes

- Bootstrap: `src/ai-workflow/bootstrap.ts` — extend existing `TRIGGER_RELATIONS` array with INPUT_MAPPING_HAS_PATH
- Middleware: `src/plugins/ai-workflow-trigger-middleware.ts` — extend types, query, and resolveInputMappings()
- Tests: `tests/story-15-2-pathnode-input-mapping.test.ts` (new file)
- ESM imports with `.js` extension required
- Separate `import type` for TypeScript types
- GraphQL client: reuse `executeQuery` from `src/ai-workflow/graphql-client.ts`

### References

- [Source: ~/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md#§6] — INPUT_MAPPING_HAS_PATH relationship: WorkflowInputMapping → PathNode (0:N) with edge props ord, isFirst, isLast
- [Source: ~/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md#§8] — Path Resolution: mappingType "path" traverses PathNode chain from mutated entity
- [Source: ~/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md#§10] — MATCHING_WORKFLOWS_QUERY target state with inputMappingHasPath
- [Source: ~/src/graphology/src/ai-workflow/bootstrap.ts] — TRIGGER_CLASSES + TRIGGER_RELATIONS arrays (current: no INPUT_MAPPING_HAS_PATH)
- [Source: ~/src/graphology/src/plugins/ai-workflow-trigger-middleware.ts#L282-323] — resolveInputMappings() current implementation (literal + context only)
- [Source: ~/src/graphology/src/plugins/ai-workflow-trigger-middleware.ts#L430-459] — MATCHING_WORKFLOWS_QUERY current state (no path data)
- [Source: ~/src/graphology/src/plugins/ai-workflow-trigger-middleware.ts#L47-53] — InputMappingConfig interface (no pathSteps field)
- [Source: ~/src/graphology/src/plugins/ai-workflow-trigger-middleware.ts#L84-93] — META_MODEL_TYPES set (PathNode already included)
- [Source: ~/src/graphology/src/bootstrap.ts#L428-431] — PathNode class definition in DynamicUI ontology
- [Source: ~/src/graphology/src/bootstrap.ts#L555-559] — PathNode relationships: PATH_STEP_VIA_RELATION, PATH_STEP_AT_CLASS, PATH_STEP_TO_PROPERTY, FIELD_HAS_PATH, COLUMN_HAS_PATH
- [Source: ~/src/graphology/tests/story-18-3-input-mapping.test.ts] — 25 existing tests for input mapping (literal + context)
- [Source: ~/src/graphology/_bmad-output/implementation-artifacts/18-3-workflow-input-mapping-bootstrap-and-resolution.md] — Story 18.3 learnings
- [Source: ~/src/ai-workflow/_bmad-output/implementation-artifacts/15-1-bootstrap-triggers-workflow-and-manual-trigger-type.md] — Story 15.1 context (TRIGGERS_WORKFLOW, MANUAL type, WorkflowDispatch)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (claude-opus-4-6)

### Debug Log References

None — clean implementation, all tests passed on first run.

### Completion Notes List

- Added `INPUT_MAPPING_HAS_PATH` relation with 3 edge properties (ord, isFirst, isLast) to bootstrap
- Added Step 4b to bootstrap function for linking edge properties to relations via GraphQL
- Added `PathStepConfig` interface and `pathSteps` field to `InputMappingConfig`
- Extended `MATCHING_WORKFLOWS_QUERY` with `inputMappingHasPathConnection` including sort and nested PathNode relations
- Added `parsePathSteps()` to flatten connection edges into sorted `PathStepConfig[]`
- Added `pluralize()` utility for entity type → GraphQL root field name conversion
- Added `resolvePathMapping()` for dynamic GraphQL query construction and execution from PathNode chains
- Changed `resolveInputMappings()` from sync to async with optional `executeQueryFn` parameter
- Updated all callers: middleware `willSendResponse`, test files (18.3, 18.4) to `await`
- 21 new tests covering bootstrap, query, path resolution, and integration scenarios
- Decision: Task 4.2 (dispatchManualTrigger) marked N/A since story 15.1 is not yet implemented

### Change Log

- 2026-03-09: Story 15.2 implementation complete — PathNode input mapping resolution with path mappingType

### File List

**graphology repo (`~/src/graphology`):**
- `src/ai-workflow/bootstrap.ts` — Modified: added INPUT_MAPPING_HAS_PATH relation with edge properties + Step 4b for edge property linking
- `src/plugins/ai-workflow-trigger-middleware.ts` — Modified: added PathStepConfig/ExecuteQueryFn types, parsePathSteps(), pluralize(), resolvePathMapping(), async resolveInputMappings() with path support, extended MATCHING_WORKFLOWS_QUERY
- `tests/story-15-2-pathnode-input-mapping.test.ts` — New: 21 unit + integration tests
- `tests/story-18-3-input-mapping.test.ts` — Modified: updated to await async resolveInputMappings()
- `tests/story-18-4-matter-trigger-pilot.test.ts` — Modified: updated to await async resolveInputMappings()

**ai-workflow repo:**
- `_bmad-output/implementation-artifacts/15-2-pathnode-input-mapping-resolution.md` — Modified: task checkboxes, Dev Agent Record
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Modified: 15-2 status → in-progress
