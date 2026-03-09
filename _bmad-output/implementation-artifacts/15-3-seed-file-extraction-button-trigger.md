# Story 15.3: Seed File Extraction Button Trigger

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a platform operator,
I want a pre-configured ButtonAction on the ApplicationFormFile form that triggers the file_extraction agent,
so that users can extract structured data from uploaded files with a single click.

## Acceptance Criteria

1. **AC1 — Workflow node seeded**
   **Given** the seed script runs against Neo4j
   **When** it completes
   **Then** a Workflow node exists with `name: "File Extraction"`, `agentName: "file_extraction"`
   **And** `WORKFLOW_TARGETS_CLASS` links the Workflow to OntologyClass `"ApplicationFormFile"`

2. **AC2 — Input mapping seeded (context type)**
   **Given** the seed script creates input mappings
   **When** it completes
   **Then** a WorkflowInputMapping exists with `name: "context_node_id"`, `mappingType: "context"`, `defaultValue: "entityId"`, `isRequired: true`
   **And** `WORKFLOW_HAS_INPUT_MAPPING` links the Workflow to this mapping

3. **AC3 — ButtonAction seeded with TRIGGERS_WORKFLOW**
   **Given** the seed script creates the ButtonAction
   **When** it completes
   **Then** a ButtonAction node exists with `actionType: "triggerWorkflow"`
   **And** `TRIGGERS_WORKFLOW` links the ButtonAction to the "File Extraction" Workflow
   **And** the ButtonAction is linked to the appropriate Heading on the ApplicationFormFile form via `HAS_ADD_BUTTON_ACTION`

4. **AC4 — End-to-end manual dispatch flow**
   **Given** a user clicks the "Extract" button on an ApplicationFormFile with ID `"appfile-789"`
   **When** the MANUAL dispatch flow executes
   **Then** the middleware resolves `context_node_id` to `"appfile-789"` via the context mapping
   **And** dispatches `POST /run-agent { agent: "file_extraction", context_node_id: "appfile-789", async_mode: true }`
   **And** a WorkflowDispatch node tracks the extraction lifecycle

5. **AC5 — Dispatch lifecycle tracking**
   **Given** the file_extraction agent completes (success or failure)
   **When** the dispatch callback fires
   **Then** the WorkflowDispatch status is updated to `"completed"` or `"failed"`
   **And** the result or error is stored on the dispatch node

## Tasks / Subtasks

- [x] Task 1: Create seed function for File Extraction Workflow (AC: #1)
  - [x] 1.1: Create `seedFileExtractionButtonTrigger()` function in `src/ai-workflow/seed-trigger.ts` (extend existing file) following the `seedMatterNodeCreatedTrigger()` pattern
  - [x] 1.2: Create Workflow node via GraphQL mutation: `{ name: "File Extraction", agentName: "file_extraction", description: "Extract structured data from uploaded files", status: "active" }` with well-known ID `file-extraction-workflow`
  - [x] 1.3: Link `WORKFLOW_TARGETS_CLASS` to OntologyClass `"ApplicationFormFile"` via connect mutation
  - [x] 1.4: Handle idempotent re-runs: wrap in try-catch, use `isDuplicateError()` to detect existing nodes and query for their IDs

- [x] Task 2: Seed WorkflowInputMapping (AC: #2)
  - [x] 2.1: Create WorkflowInputMapping node: `{ name: "context_node_id", parameterType: "string", mappingType: "context", defaultValue: "entityId", isRequired: true, description: "ApplicationFormFile node ID from button click context" }`
  - [x] 2.2: Link `WORKFLOW_HAS_INPUT_MAPPING` from Workflow to this mapping

- [x] Task 3: Seed ButtonAction with TRIGGERS_WORKFLOW (AC: #3)
  - [x] 3.1: Query the ApplicationFormFile form's appropriate Heading node ID (the heading that should contain the Extract button). Use a GraphQL query to find the Heading linked to the ApplicationFormFile FormSection.
  - [x] 3.2: Create ButtonAction node: `{ actionType: "triggerWorkflow", label: "Extract", icon: "file-search" }` (or appropriate label/icon matching DynamicUI patterns)
  - [x] 3.3: Link `TRIGGERS_WORKFLOW` from ButtonAction to the "File Extraction" Workflow
  - [x] 3.4: Link `HAS_ADD_BUTTON_ACTION` from the Heading to the ButtonAction
  - [x] 3.5: Handle case where Heading does not exist: log warning and skip ButtonAction creation (Workflow + InputMapping are still valid without the UI button)

- [x] Task 4: Add CLI entry point (AC: #1-#3)
  - [x] 4.1: Add `seedFileExtractionButtonTrigger()` call to the existing CLI runner in `seed-trigger.ts` (the `if (import.meta.url...)` block)
  - [x] 4.2: Ensure the seed function returns `SeedTriggerResult` with success, message, workflowId fields

- [x] Task 5: Write tests (AC: #1-#5)
  - [x] 5.1: Create `tests/story-15-3-seed-file-extraction-button.test.ts`
  - [x] 5.2: Unit tests verifying seed function creates correct GraphQL mutations:
    - [15.3-UNIT-001] Workflow created with correct name, agentName, status
    - [15.3-UNIT-002] WORKFLOW_TARGETS_CLASS links to ApplicationFormFile
    - [15.3-UNIT-003] WorkflowInputMapping created with context_node_id mapping
    - [15.3-UNIT-004] WORKFLOW_HAS_INPUT_MAPPING links Workflow to mapping
    - [15.3-UNIT-005] ButtonAction created with actionType "triggerWorkflow"
    - [15.3-UNIT-006] TRIGGERS_WORKFLOW links ButtonAction to Workflow
  - [x] 5.3: Integration tests verifying end-to-end dispatch:
    - [15.3-INTG-001] dispatchManualTrigger() with mocked ButtonAction returns resolved context_node_id
    - [15.3-INTG-002] buildDispatchPayload() produces correct payload for file_extraction
  - [x] 5.4: Idempotency tests:
    - [15.3-UNIT-007] Running seed twice does not create duplicate Workflow
    - [15.3-UNIT-008] Running seed twice does not create duplicate ButtonAction
  - [x] 5.5: Edge case tests:
    - [15.3-UNIT-009] Seed handles missing Heading gracefully (logs warning, skips ButtonAction)
  - [x] 5.6: Ensure zero regressions on existing trigger tests (118+ across 15.1, 15.2, 18.x stories)

## Dev Notes

### Architecture Reference

This story implements the seed data for Phase 6 (Manual Trigger / ButtonAction Integration) from `docs/architecture/ai-workflow-trigger-system.md` (ai-workflow repo). All infrastructure was built in stories 15.1 and 15.2:
- Story 15.1: TRIGGERS_WORKFLOW relation, MANUAL trigger type, WorkflowDispatch lifecycle, `dispatchManualTrigger()` function
- Story 15.2: PathNode resolution (path mappingType) — not needed for this story since we use `context` mappingType
- Architecture §6: TRIGGERS_WORKFLOW relationship (ButtonAction → Workflow, 0:1)
- Architecture §8: `context` mappingType — entityId injected from mutation context

### Implementation Boundary

**All changes are in the graphology repository** (`~/src/graphology`).

| File | Change Type | Description |
|------|-------------|-------------|
| `src/ai-workflow/seed-trigger.ts` | Modified | Add `seedFileExtractionButtonTrigger()` function and CLI call |
| `tests/story-15-3-seed-file-extraction-button.test.ts` | New | Unit + integration + idempotency tests |

**ai-workflow repo requires NO code changes.**

### Key Design Decisions

1. **Extend existing `seed-trigger.ts` rather than create new file.** All workflow trigger seeds belong in one file, following the established pattern with `seedLegalFirmWebsiteTrigger()` and `seedMatterNodeCreatedTrigger()`.

2. **Use `context` mappingType (not `path`) for context_node_id.** The file extraction agent needs the ApplicationFormFile node ID, which is the entity being acted upon. The `context` mappingType with `defaultValue: "entityId"` resolves this directly from the dispatch context — no PathNode traversal needed.

3. **ButtonAction `actionType: "triggerWorkflow"`.** This is a new actionType value distinct from the existing DynamicUI actions (e.g., OPEN_MODAL). The frontend should detect this value and invoke the manual dispatch endpoint rather than performing a standard DynamicUI action.

4. **Well-known Workflow ID `file-extraction-workflow`.** Following the pattern from `seedMatterNodeCreatedTrigger()` where `IMPORT_MATTER_QA_WORKFLOW_ID = 'import-matter-qa-workflow'` is used. A well-known ID allows other seeds and tests to reference it deterministically.

5. **No WorkflowTrigger node needed.** MANUAL triggers don't use WorkflowTrigger nodes — the ButtonAction → TRIGGERS_WORKFLOW → Workflow link IS the trigger mechanism. The middleware's `dispatchManualTrigger()` queries ButtonAction directly via `MANUAL_TRIGGER_QUERY`.

6. **Heading discovery for ButtonAction placement.** The ButtonAction must be linked to a Heading via `HAS_ADD_BUTTON_ACTION`. The seed queries for the Heading on the ApplicationFormFile form. If no Heading exists (form not seeded yet), the seed logs a warning and skips the ButtonAction — the Workflow and InputMapping are still valid.

### Existing Seed Data Pattern (from `seed-trigger.ts`)

**Function structure:**
```typescript
export async function seedFileExtractionButtonTrigger(
  apolloEndpoint: string,
  apiKey?: string,
  fetchFn: typeof fetch = fetch,
): Promise<SeedTriggerResult> {
  // 1. Create Workflow node (idempotent)
  // 2. Link WORKFLOW_TARGETS_CLASS → OntologyClass("ApplicationFormFile")
  // 3. Create WorkflowInputMapping (context_node_id)
  // 4. Link WORKFLOW_HAS_INPUT_MAPPING
  // 5. Query Heading on ApplicationFormFile form
  // 6. Create ButtonAction (triggerWorkflow)
  // 7. Link TRIGGERS_WORKFLOW → Workflow
  // 8. Link HAS_ADD_BUTTON_ACTION from Heading
  // Return { success, message, workflowId }
}
```

**GraphQL mutation pattern (from existing seeds):**
```typescript
const CREATE_WORKFLOW = `
  mutation CreateFileExtractionWorkflow {
    createWorkflows(input: [{
      id: "${FILE_EXTRACTION_WORKFLOW_ID}"
      name: "File Extraction"
      agentName: "file_extraction"
      description: "Extract structured data from uploaded files"
      status: "active"
    }]) {
      workflows { id name }
    }
  }
`;
```

**Idempotency pattern:**
```typescript
const result = await executeMutation(CREATE_WORKFLOW, apolloEndpoint, apiKey, fetchFn);
if (result.errors) {
  if (isDuplicateError(result.errors)) {
    // Fetch existing ID via query
    const existing = await executeQuery(QUERY_WORKFLOW, apolloEndpoint, apiKey, fetchFn);
    workflowId = existing.data.workflows[0].id;
  } else {
    return { success: false, message: `Failed: ${JSON.stringify(result.errors)}` };
  }
} else {
  workflowId = result.data.createWorkflows.workflows[0].id;
}
```

### Middleware Functions Already Available (from 15.1)

All dispatch infrastructure is implemented and tested. This story only seeds data — no middleware code changes needed.

| Function | Purpose | Story |
|----------|---------|-------|
| `dispatchManualTrigger()` | End-to-end: query → resolve → dispatch → track | 15.1 |
| `queryManualTriggerWorkflow()` | MANUAL_TRIGGER_QUERY execution | 15.1 |
| `createWorkflowDispatch()` | Create dispatch tracking node | 15.1 |
| `updateWorkflowDispatch()` | Update dispatch status on completion | 15.1 |
| `buildDispatchPayload()` | Build /run-agent POST body | 18.3 |
| `resolveInputMappings()` | Resolve literal/context/path mappings | 18.3 + 15.2 |

### GraphQL Client Functions (from `src/ai-workflow/graphql-client.ts`)

```typescript
import { executeQuery, executeMutation, isDuplicateError } from './graphql-client.js';
```

These are the established helpers used by `seed-trigger.ts` — reuse them exactly.

### Testing Standards

- **Framework**: Vitest
- **Test location**: `tests/story-15-3-seed-file-extraction-button.test.ts`
- **Test ID format**: `[15.3-UNIT-NNN]` for unit, `[15.3-INTG-NNN]` for integration
- **Priority tags**: `[P0]` critical path, `[P1]` important, `[P2]` edge case
- **Import pattern**: Import from `'../src/ai-workflow/seed-trigger.js'` (ESM `.js` extension)
- **Mock pattern**: Mock `fetchFn` to capture GraphQL mutations without live Neo4j
- **Existing test counts**: 118+ tests across 15.1/15.2/18.x — ensure zero regressions

### Previous Story Learnings (15.1 + 15.2)

1. **Seed pattern is idempotent**: `isDuplicateError()` handles re-runs. No cleanup needed.
2. **Cross-ontology relations work**: TRIGGERS_WORKFLOW (DynamicUI → AIWorkflow) established in 15.1 bootstrap
3. **`dispatchManualTrigger()` has injectable `fetchFn`**: All external calls mockable in tests
4. **Manual dispatch creates WorkflowDispatch with `triggerSnapshot: { type: 'MANUAL', buttonActionId, entityId, entityType }`** — the dispatch node preserves full audit trail
5. **`context` mappingType resolves `entityId` from dispatch context**: Used by `resolveInputMappings()` with contextLookup `{ entityId, entityType, mutationName, mutationOp }`
6. **`buildDispatchPayload()` spreads resolved inputs then sets `agent` and `async_mode: true`**: Core fields set AFTER spread to prevent override
7. **Fire-and-forget dispatch**: All dispatch errors caught and logged with `[aiWorkflowTrigger]` prefix, never affect UI response
8. **MANUAL_TRIGGER_QUERY does NOT fetch PathNode data**: Only fetches `workflowHasInputMapping { name, parameterType, isRequired, defaultValue, mappingType }` — sufficient for `context` mappingType
9. **`resolveInputMappings()` is async** (changed in 15.2): The seed tests that call it must `await`
10. **3 pre-existing test failures in `story-18-1-trigger-bootstrap.test.ts`**: Stale exact-count assertions — NOT regressions

### Project Structure Notes

- Seed file: `src/ai-workflow/seed-trigger.ts` — extend with new function
- Tests: `tests/story-15-3-seed-file-extraction-button.test.ts` (new file)
- ESM imports with `.js` extension required
- Separate `import type` for TypeScript types
- GraphQL client: reuse `executeQuery`/`executeMutation`/`isDuplicateError` from `src/ai-workflow/graphql-client.ts`
- All changes in graphology repo — ai-workflow repo requires no code changes

### References

- [Source: ~/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md#§6] — TRIGGERS_WORKFLOW: ButtonAction → Workflow (0:1)
- [Source: ~/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md#§8] — context mappingType: entityId, entityType from mutation context
- [Source: ~/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md#§5] — MANUAL trigger type: linked via ButtonAction → TRIGGERS_WORKFLOW → Workflow
- [Source: ~/src/ai-workflow/_bmad-output/planning-artifacts/epics.md#Story 15.3] — Story acceptance criteria
- [Source: ~/src/graphology/src/ai-workflow/seed-trigger.ts] — Existing seed pattern (seedLegalFirmWebsiteTrigger, seedMatterNodeCreatedTrigger)
- [Source: ~/src/graphology/src/ai-workflow/graphql-client.ts] — executeQuery, executeMutation, isDuplicateError
- [Source: ~/src/graphology/src/plugins/ai-workflow-trigger-middleware.ts#L741-759] — MANUAL_TRIGGER_QUERY
- [Source: ~/src/graphology/src/plugins/ai-workflow-trigger-middleware.ts#L1010-1130] — dispatchManualTrigger() function
- [Source: ~/src/graphology/src/plugins/ai-workflow-trigger-middleware.ts#L854-927] — createWorkflowDispatch() function
- [Source: ~/src/graphology/src/plugins/ai-workflow-trigger-middleware.ts#L293-350] — resolveInputMappings() async signature
- [Source: ~/src/ai-workflow/_bmad-output/implementation-artifacts/15-1-bootstrap-triggers-workflow-and-manual-trigger-type.md] — Story 15.1 learnings and design decisions
- [Source: ~/src/ai-workflow/_bmad-output/implementation-artifacts/15-2-pathnode-input-mapping-resolution.md] — Story 15.2 learnings and code patterns

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

None — clean implementation, no debugging needed.

### Completion Notes List

- **Task 1-4**: Implemented `seedFileExtractionButtonTrigger()` in `src/ai-workflow/seed-trigger.ts` following the established `seedMatterNodeCreatedTrigger()` pattern. Creates Workflow (with well-known ID `file-extraction-workflow`), links WORKFLOW_TARGETS_CLASS to ApplicationFormFile, creates context_node_id WorkflowInputMapping (context mappingType, entityId defaultValue), links WORKFLOW_HAS_INPUT_MAPPING, queries Heading on ApplicationFormFile form, creates ButtonAction (actionType: triggerWorkflow), links TRIGGERS_WORKFLOW and HAS_ADD_BUTTON_ACTION. All operations are idempotent via `isDuplicateError()`. Missing Heading gracefully skips ButtonAction creation with warning. CLI entry point added. Exported `FILE_EXTRACTION_WORKFLOW_ID` constant.
- **Task 5**: 14 tests total — 12 unit tests (UNIT-001 through UNIT-012) covering all mutations, idempotency, missing heading edge case, error handling, and constant export. 2 integration tests (INTG-001, INTG-002) verifying context_node_id resolution and dispatch payload. All 14 pass. 171/174 existing tests pass (3 pre-existing stale count failures in story-18-1-trigger-bootstrap.test.ts — documented in Dev Notes).

### Change Log

- 2026-03-09: Implemented all tasks (1-5) for story 15.3 — seed function, CLI entry, and 14 tests
- 2026-03-09: Code review fixes — well-known IDs for idempotency (context mapping + ButtonAction), status casing, header comment, icon assertion, constant export tests

### File List

- `src/ai-workflow/seed-trigger.ts` (modified) — Added `seedFileExtractionButtonTrigger()`, `FILE_EXTRACTION_WORKFLOW_ID`, `FILE_EXTRACTION_CONTEXT_MAPPING_ID`, `FILE_EXTRACTION_BUTTON_ACTION_ID`, CLI call
- `tests/story-15-3-seed-file-extraction-button.test.ts` (new) — 14 unit + integration tests
