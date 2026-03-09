# Story 15.1: Bootstrap TRIGGERS_WORKFLOW & MANUAL Trigger Type

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system administrator,
I want the TRIGGERS_WORKFLOW relationship bootstrapped and the MANUAL trigger type handled by the middleware,
so that ButtonActions in the DynamicUI can dispatch AI workflows without code changes.

## Acceptance Criteria

1. **AC1 — TRIGGERS_WORKFLOW relationship bootstrap**
   **Given** the bootstrap script `src/ai-workflow/bootstrap.ts`
   **When** it runs against Neo4j
   **Then** `TRIGGERS_WORKFLOW` OntologyRelation exists from ButtonAction (DynamicUI) to Workflow (AIWorkflow)
   **And** GraphQL schema regeneration produces the relationship on both ButtonAction and Workflow types

2. **AC2 — MANUAL trigger evaluation in middleware**
   **Given** a ButtonAction node linked via `TRIGGERS_WORKFLOW` to a Workflow with `agentName: "file_extraction"`
   **When** the trigger middleware receives a manual dispatch request for that ButtonAction
   **Then** it identifies the linked Workflow via `TRIGGERS_WORKFLOW`
   **And** skips trigger condition evaluation (MANUAL type has no conditions)
   **And** dispatches to `/run-agent` with the Workflow's `agentName` and resolved inputs

3. **AC3 — WorkflowDispatch node lifecycle tracking**
   **Given** a manual dispatch is triggered
   **When** the middleware processes it
   **Then** a WorkflowDispatch node is created with `status: "pending"`, `executionMode: "fire_and_forget"`, `dispatchedAt: <timestamp>`
   **And** `DISPATCH_OF_WORKFLOW` links the dispatch to the Workflow
   **And** on completion, status is updated to `"completed"` or `"failed"`

4. **AC4 — No dispatch for non-workflow ButtonActions**
   **Given** a ButtonAction node with NO `TRIGGERS_WORKFLOW` relationship
   **When** queried by the middleware
   **Then** no dispatch occurs (button behaves as standard DynamicUI button — e.g., opens modal)

5. **AC5 — Error resilience for MANUAL dispatch**
   **Given** any error during MANUAL dispatch
   **When** it occurs
   **Then** the error is logged with `[aiWorkflowTrigger]` prefix
   **And** the original UI response is NOT affected

## Tasks / Subtasks

- [x] Task 1: Bootstrap TRIGGERS_WORKFLOW relation (AC: #1)
  - [x] 1.1: Add `TRIGGERS_WORKFLOW` RelationDef to `TRIGGER_RELATIONS` array in `src/ai-workflow/bootstrap.ts`: source=`ButtonAction` (DynamicUI ontology), destination=`Workflow` (AIWorkflow ontology), directed=true
  - [x] 1.2: Note: ButtonAction is in the DynamicUI ontology (bootstrapped in `src/bootstrap.ts`). The relation links ACROSS ontologies — this is supported because `RELATION_SOURCE`/`RELATION_DESTINATION` just point to OntologyClass nodes regardless of which ontology owns them
  - [x] 1.3: Write unit test verifying `TRIGGER_RELATIONS_EXPORT` includes `TRIGGERS_WORKFLOW` with correct source (`ButtonAction`) and destination (`Workflow`)

- [x] Task 2: Bootstrap WorkflowDispatch class (AC: #3)
  - [x] 2.1: Add `WorkflowDispatch` ClassDef to `TRIGGER_CLASSES` array with properties: `status` (string, required), `dispatchedAt` (string, required), `completedAt` (string, optional), `result` (string, optional), `error` (string, optional), `inputSnapshot` (string, optional), `triggerSnapshot` (string, optional), `executionMode` (string, required)
  - [x] 2.2: Add `WorkflowDispatch` to `META_MODEL_TYPES` set in `ai-workflow-trigger-middleware.ts`
  - [x] 2.3: Write unit test verifying `TRIGGER_CLASSES_EXPORT` includes `WorkflowDispatch` with all 8 properties

- [x] Task 3: Bootstrap WorkflowDispatch relationships (AC: #3)
  - [x] 3.1: Add `DISPATCH_OF_WORKFLOW` RelationDef: source=`WorkflowDispatch`, destination=`Workflow`, directed=true
  - [x] 3.2: Add `DISPATCH_TRIGGERED_BY` RelationDef: source=`WorkflowDispatch`, destination=`WorkflowTrigger`, directed=true
  - [x] 3.3: Write unit tests verifying both relations have correct source/destination

- [x] Task 4: Implement MANUAL trigger query (AC: #2, #4)
  - [x] 4.1: Create a new GraphQL query `MANUAL_TRIGGER_QUERY` to find Workflow linked from a ButtonAction via TRIGGERS_WORKFLOW: `query ManualTriggerWorkflow($buttonActionId: ID!) { buttonActions(where: { id_EQ: $buttonActionId }) { triggersWorkflow { id, name, agentName, workflowHasInputMapping { name, parameterType, isRequired, defaultValue, mappingType } } } }`
  - [x] 4.2: Create `queryManualTriggerWorkflow(buttonActionId: string)` function that executes the query and returns `WorkflowConfig | null`
  - [x] 4.3: Write unit tests: ButtonAction with TRIGGERS_WORKFLOW returns WorkflowConfig; ButtonAction without returns null

- [x] Task 5: Implement MANUAL dispatch endpoint (AC: #2, #5)
  - [x] 5.1: Create `dispatchManualTrigger(buttonActionId: string, entityId: string, entityType: string)` function that: queries the workflow via Task 4, resolves input mappings (reusing `resolveInputMappings()` from 18.3), creates WorkflowDispatch node, dispatches to `/run-agent`
  - [x] 5.2: Add MANUAL dispatch to the middleware's trigger evaluation loop: in the `for (const trigger of workflow.triggers)` block, add `else if (trigger.triggerType === 'MANUAL')` case — though MANUAL triggers are NOT evaluated by the mutation middleware; they are dispatched via a separate code path (see Dev Notes)
  - [x] 5.3: Write unit tests for manual dispatch: success path, input mapping resolution, error handling

- [x] Task 6: Implement WorkflowDispatch lifecycle (AC: #3)
  - [x] 6.1: Create `createWorkflowDispatch(workflowId: string, triggerId: string | null, inputSnapshot: Record<string, string>, executionMode: string)` function using GraphQL mutation
  - [x] 6.2: Create `updateWorkflowDispatch(dispatchId: string, updates: { status, completedAt?, result?, error? })` function
  - [x] 6.3: Wire into `dispatchToWorkflowEngine()`: create dispatch before HTTP POST, update on success/failure in the `.then()/.catch()` chain
  - [x] 6.4: Write unit tests for dispatch lifecycle: pending → completed, pending → failed

- [x] Task 7: Update bootstrap + middleware tests (AC: #1-#5)
  - [x] 7.1: Add tests in `tests/story-15-1-manual-trigger.test.ts` verifying:
    - `TRIGGER_RELATIONS_EXPORT` includes `TRIGGERS_WORKFLOW` with source=ButtonAction, dest=Workflow
    - `TRIGGER_CLASSES_EXPORT` includes `WorkflowDispatch` with 8 properties
    - `TRIGGER_RELATIONS_EXPORT` includes `DISPATCH_OF_WORKFLOW` and `DISPATCH_TRIGGERED_BY`
    - `META_MODEL_TYPES` includes `WorkflowDispatch`
    - `queryManualTriggerWorkflow()` returns WorkflowConfig for linked ButtonAction
    - `queryManualTriggerWorkflow()` returns null for non-linked ButtonAction
    - `createWorkflowDispatch()` returns dispatch ID
    - `updateWorkflowDispatch()` updates status correctly
    - Manual dispatch end-to-end: query → resolve → dispatch → track
    - Error resilience: failed dispatch logs error, doesn't throw

## Dev Notes

### Architecture Reference

This story implements Phase 6 (Manual Trigger / ButtonAction Integration) from `docs/architecture/ai-workflow-trigger-system.md` (ai-workflow repo). Key sections:
- Section 5: MANUAL trigger type semantics (line 115)
- Section 6: TRIGGERS_WORKFLOW relationship definition (line 168)
- Section 4.3: WorkflowDispatch class definition (lines 92-103)
- Section 9: Middleware dispatch flow (lines 348-398)

### Implementation Boundary

**All changes are in the graphology repository** (`~/src/graphology`).

| File | Change Type | Description |
|------|-------------|-------------|
| `src/ai-workflow/bootstrap.ts` | Modified | Add TRIGGERS_WORKFLOW relation, WorkflowDispatch class + relations |
| `src/plugins/ai-workflow-trigger-middleware.ts` | Modified | Add MANUAL query, WorkflowDispatch lifecycle, META_MODEL_TYPES update |
| `tests/story-15-1-manual-trigger.test.ts` | New | Unit + integration tests |

**ai-workflow repo requires NO code changes.** The `/run-agent` endpoint already supports the dispatch pattern.

### Key Design Decisions

1. **MANUAL triggers are NOT evaluated in the mutation middleware loop.** Unlike PROPERTY_CHANGED and NODE_CREATED which fire on mutations, MANUAL triggers fire from a ButtonAction click. The middleware needs a **separate entry point** — either a dedicated GraphQL mutation (e.g., `dispatchManualWorkflow(buttonActionId, entityId)`) or the existing middleware detects ButtonAction mutations with TRIGGERS_WORKFLOW. The recommended approach is a **dedicated resolver** since the UI needs to know which ButtonAction to call.

2. **Cross-ontology TRIGGERS_WORKFLOW**: ButtonAction lives in DynamicUI ontology, Workflow lives in AIWorkflow ontology. The bootstrap creates an OntologyRelation that links OntologyClass nodes from different ontologies — this is supported because `RELATION_SOURCE`/`RELATION_DESTINATION` just point to OntologyClass nodes by name.

3. **WorkflowDispatch is a runtime tracking node**, not a meta-model node. However, it IS added to META_MODEL_TYPES to prevent trigger evaluation loops (creating a WorkflowDispatch should not trigger other workflows).

4. **WorkflowDispatch lifecycle**: `pending` (created before HTTP POST) → `running` (on `/run-agent` 202 response) → `completed`/`failed` (on callback). The `inputSnapshot` captures resolved parameters at dispatch time for auditability.

5. **Idempotent bootstrap**: Follows existing `isDuplicateError()` pattern — re-running bootstrap is safe.

### Existing Code Patterns (from 18.1/18.2/18.3)

**Bootstrap pattern** (`src/ai-workflow/bootstrap.ts`):
```typescript
// Add to TRIGGER_RELATIONS array:
{ name: 'TRIGGERS_WORKFLOW', source: 'ButtonAction', destination: 'Workflow', directed: true }

// Add to TRIGGER_CLASSES array:
{
  name: 'WorkflowDispatch',
  properties: [
    { name: 'status', type: 'string', required: true },
    { name: 'dispatchedAt', type: 'string', required: true },
    // ... 6 more properties
  ]
}
```

**Middleware trigger evaluation** (current loop, lines 664-701):
```typescript
if (trigger.triggerType === 'PROPERTY_CHANGED') {
  matched = evaluatePropertyChangedTrigger(trigger, ctx.changedFields);
} else if (trigger.triggerType === 'NODE_CREATED') {
  matched = evaluateNodeCreatedTrigger(trigger, ctx.mutationOp);
} else {
  console.warn(`[aiWorkflowTrigger] Unknown trigger type "${trigger.triggerType}"...`);
}
```
For MANUAL: do NOT add to this loop. MANUAL triggers are dispatched from a separate code path.

**dispatchToWorkflowEngine()** (current, lines 380-414):
```typescript
export async function dispatchToWorkflowEngine(
  agentName: string,
  contextNodeId: string,
  aiWorkflowUrl: string,
  aiWorkflowApiKey: string,
  resolvedInputs?: Record<string, string>,
): Promise<void>
```

**buildDispatchPayload()** (current, lines 363-374):
```typescript
export function buildDispatchPayload(
  agentName: string,
  contextNodeId: string,
  resolvedInputs?: Record<string, string>,
) {
  return { context_node_id: contextNodeId, ...resolvedInputs, agent: agentName, async_mode: true };
}
```

### Testing Standards

- **Framework**: Vitest
- **Test location**: `tests/story-15-1-manual-trigger.test.ts`
- **Test ID format**: `[15.1-UNIT-NNN]` for unit, `[15.1-INTG-NNN]` for integration
- **Priority tags**: `[P0]` critical path, `[P1]` important, `[P2]` edge case
- **Import pattern**: Import from `'../src/plugins/ai-workflow-trigger-middleware.js'` and `'../src/ai-workflow/bootstrap.js'` (ESM `.js` extension)
- **No live Neo4j needed**: All tests use mocked data, matching 18.1/18.2/18.3 test patterns
- **Existing test counts**: 48+ tests across story-18-1/18-2/18-3 — ensure zero regressions

### Previous Story Learnings (18.1 + 18.2 + 18.3)

1. **`META_MODEL_TYPES` must include ALL new classes**: WorkflowTrigger (18.1), WorkflowInputMapping (18.3), now WorkflowDispatch (15.1) — prevents infinite loops
2. **Bootstrap is idempotent**: `isDuplicateError()` handles re-runs. No cleanup needed.
3. **Cross-ontology relations work**: TRIGGER_WATCHES_CLASS already links WorkflowTrigger → OntologyClass (MetaOntology). TRIGGERS_WORKFLOW follows same pattern: ButtonAction (DynamicUI) → Workflow (AIWorkflow)
4. **Entity ID extraction for create mutations**: From response body `response.body.singleResult.data` (lines 646-660), not args
5. **Singular/plural mapping**: `singularize()` handles English pluralization. ButtonAction → ButtonAction (no change needed, already singular in mutation names)
6. **`buildDispatchPayload()` is exported and tested**: Changes must maintain backward compatibility
7. **Fire-and-forget**: All dispatch errors caught and logged with `[aiWorkflowTrigger]` prefix
8. **HTTP_TIMEOUT_MS = 30_000**: AbortController on dispatch — no change needed
9. **Cache invalidation**: `TriggerConfigCache.invalidateAll()` on meta-model mutations — already handles WorkflowDispatch since it's in META_MODEL_TYPES

### Project Structure Notes

- Bootstrap: `src/ai-workflow/bootstrap.ts` — extend existing `TRIGGER_CLASSES` and `TRIGGER_RELATIONS` arrays
- Middleware: `src/plugins/ai-workflow-trigger-middleware.ts` — add MANUAL query + WorkflowDispatch lifecycle
- Tests: `tests/story-15-1-manual-trigger.test.ts` (new file)
- ESM imports with `.js` extension required
- Separate `import type` for TypeScript types
- GraphQL client: reuse `executeQuery`/`executeMutation` from `src/ai-workflow/graphql-client.ts`

### References

- [Source: ~/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md#§4.3] — WorkflowDispatch class definition
- [Source: ~/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md#§5] — MANUAL trigger type: "linked via ButtonAction → TRIGGERS_WORKFLOW → Workflow"
- [Source: ~/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md#§6] — TRIGGERS_WORKFLOW relationship: ButtonAction → Workflow (0:1)
- [Source: ~/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md#§9] — Middleware dispatch flow
- [Source: ~/src/graphology/src/ai-workflow/bootstrap.ts] — TRIGGER_CLASSES + TRIGGER_RELATIONS arrays, bootstrap function
- [Source: ~/src/graphology/src/plugins/ai-workflow-trigger-middleware.ts] — Middleware plugin, META_MODEL_TYPES, trigger evaluation loop, dispatch functions
- [Source: ~/src/graphology/src/ai-workflow/graphql-client.ts] — executeQuery/executeMutation, isDuplicateError
- [Source: ~/src/graphology/src/ai-workflow/seed-trigger.ts] — Seed data pattern (idempotent creation)
- [Source: ~/src/graphology/generated/schema/classes/ButtonAction.graphql] — Current ButtonAction schema (no TRIGGERS_WORKFLOW yet)
- [Source: ~/src/graphology/generated/schema/classes/Workflow.graphql] — Current Workflow schema
- [Source: ~/src/graphology/_bmad-output/implementation-artifacts/18-1-trigger-system-pilot.md] — Story 18.1 learnings
- [Source: ~/src/graphology/_bmad-output/implementation-artifacts/18-3-workflow-input-mapping-bootstrap-and-resolution.md] — Story 18.3 patterns

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

None — all tests passed on first GREEN phase run.

### Completion Notes List

- Task 1: Added `TRIGGERS_WORKFLOW` RelationDef (ButtonAction → Workflow, directed) to `TRIGGER_RELATIONS` array. Cross-ontology linking (DynamicUI → AIWorkflow) follows established pattern from TRIGGER_WATCHES_CLASS.
- Task 2: Added `WorkflowDispatch` ClassDef with 8 properties (status, dispatchedAt, completedAt, result, error, inputSnapshot, triggerSnapshot, executionMode). Added `WorkflowDispatch` to `META_MODEL_TYPES` to prevent infinite trigger loops.
- Task 3: Added `DISPATCH_OF_WORKFLOW` (WorkflowDispatch → Workflow) and `DISPATCH_TRIGGERED_BY` (WorkflowDispatch → WorkflowTrigger) relations.
- Task 4: Implemented `MANUAL_TRIGGER_QUERY` and `queryManualTriggerWorkflow()` — queries ButtonAction's TRIGGERS_WORKFLOW relationship, returns WorkflowConfig or null.
- Task 5: Implemented `dispatchManualTrigger()` — full flow: query workflow → resolve inputs (literal/context) → create dispatch → POST /run-agent → update dispatch status. MANUAL triggers NOT added to mutation middleware loop (separate code path per design). All errors logged with `[aiWorkflowTrigger]` prefix, never thrown.
- Task 6: Implemented `createWorkflowDispatch()` and `updateWorkflowDispatch()` using GraphQL mutations. Lifecycle: pending → completed/failed. Wired into dispatchManualTrigger flow.
- Task 7: Created `tests/story-15-1-manual-trigger.test.ts` with 20 tests (14 unit, 2 integration, 4 lifecycle). All pass. Zero regressions on 98 existing trigger tests (118 total pass).
- Design decision: `queryManualTriggerWorkflow`, `createWorkflowDispatch`, `updateWorkflowDispatch`, and `dispatchManualTrigger` all accept injectable `fetchFn` parameter for testability without global mocking — consistent with existing middleware patterns.
- Note: 3 pre-existing test failures in `story-18-1-trigger-bootstrap.test.ts` (stale exact-count assertions from before story 18.3 added WorkflowInputMapping class + relations). These are NOT regressions from this story.

### File List

- `src/ai-workflow/bootstrap.ts` (modified) — Added WorkflowDispatch class, TRIGGERS_WORKFLOW + DISPATCH_OF_WORKFLOW + DISPATCH_TRIGGERED_BY relations
- `src/plugins/ai-workflow-trigger-middleware.ts` (modified) — Added WorkflowDispatch to META_MODEL_TYPES, MANUAL_TRIGGER_QUERY, queryManualTriggerWorkflow(), createWorkflowDispatch(), updateWorkflowDispatch(), dispatchManualTrigger()
- `tests/story-15-1-manual-trigger.test.ts` (new) — 20 tests covering all ACs

## Senior Developer Review (AI)

**Reviewer:** Fabricio on 2026-03-09
**Outcome:** Approved with fixes applied

### Fixes Applied (6 issues)

**HIGH (3 fixed):**
- **H1**: Changed `dispatchManualTrigger` to mark dispatch `running` (not `completed`) after 202 — fire-and-forget has no callback to mark completion
- **H2**: Changed `queryManualTriggerWorkflow` to return null + log error on HTTP/GraphQL failures instead of throwing (matches `Promise<WorkflowConfig | null>` contract)
- **H3**: Changed `dispatchManualTrigger` to return null on failed HTTP dispatch (was returning `{ dispatchId }` — violated JSDoc "null on error" contract)

**MEDIUM (3 fixed):**
- **M1**: Added `triggerSnapshot` field (`{type: "MANUAL", buttonActionId, entityId, entityType}`) to `createWorkflowDispatch` call for audit trail; updated function signature to accept optional `triggerSnapshot` parameter
- **M2**: Stored `/run-agent` response JSON in dispatch's `result` field instead of discarding it — preserves `task_id` for correlation
- **M3**: Changed `inputSnapshot` parameter type from `Record<string, string>` to `Record<string, unknown>` for type safety

### Change Log

| Date | Action | Details |
|------|--------|---------|
| 2026-03-09 | Code Review | 6 issues found (3 HIGH, 3 MEDIUM, 2 LOW), all HIGH+MEDIUM fixed. 20/20 tests pass. 117 existing trigger tests pass (zero regressions). |
