# AI Workflow Trigger System — Architecture Document

## 1. Problem Statement

The AIWorkflow ontology defines **what** to execute (Workflow → Step → Prompt → PromptExecution) and the TEA engine + FastAPI service handle **how** to execute it. What's missing is **when** — a graph-configurable trigger system that automatically dispatches workflows in response to data mutations.

Currently, workflows are triggered manually via the `/run-agent` API endpoint. There is no mechanism to:
- Auto-trigger workflows when entities are created or updated
- React to specific property value changes (e.g., validation status becoming "valid")
- Chain workflows (workflow A completing triggers workflow B)
- Let users initiate workflows from the UI via buttons

## 2. Design Principles

| Principle | Rationale |
|-----------|-----------|
| **Non-blocking** | Triggers fire after mutation response is sent. Data always persists first (matches validation pattern VNFR1). |
| **Graph-configured** | All trigger conditions, input mappings, and workflow bindings are stored as ontology nodes — not code. |
| **Zero Cypher** | All runtime operations use GraphQL mutations. Only bootstrap uses Cypher. |
| **Separate middleware** | Dedicated Apollo plugin, distinct from validation middleware, runs after validation completes. |
| **Reuse PathNode** | Input parameter mapping reuses the existing PathNode chain pattern from DynamicUI. |
| **Bootstrap into AIWorkflow ontology** | New classes belong to the existing AIWorkflow ontology, not the meta-ontology. |

## 3. Existing Architecture Context

### Current AIWorkflow Ontology (17 classes)

```
Workflow → HAS_STEP → Step → HAS_PROMPT → Prompt → HAS_VERSION → PromptVersion
                                  │                        │
                                  ├─ FOLLOWED_BY → Prompt   ├─ HAS_EXECUTION → PromptExecution
                                  ├─ PROMPT_HAS_BODY        │       ├─ HAS_CONTEXT → ContextNode
                                  └─ PROMPT_HAS_OUTPUT      │       ├─ HAS_RESPONSE → PromptResponse
                                                            │       └─ LOOKBACK_FROM → PromptExecution
                                                            └─ (content, status, versionNumber)
```

### Current Execution Path

```
User/API → POST /run-agent { agent, workflow_id, context_node_id }
    → FastAPI loads TEA YAMLEngine
    → TEA fetches PromptVersions via graphology.get_workflow_questions
    → TEA executes prompts (parallel or sequential)
    → TEA persists results via graphology.save_workflow_responses
    → Creates: PromptExecution + ContextNode + PromptResponse nodes
```

### Validation Middleware Pattern (reference)

```
Mutation → Neo4j persists → Validation plugin (willSendResponse)
    → Load validation chain → Run pipeline → Fire-and-forget:
        → Upsert ValidationState → Replace ValidationMessages
        → Aggregate TabValidations → Aggregate PageValidations
```

## 4. Ontology Model — New Classes

All new classes bootstrap into the **AIWorkflow** ontology via `ONTOLOGY_HAS_ONTOLOGY_CLASS`.

### 4.1 WorkflowTrigger

Defines **when** a Workflow should execute.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | String | yes (unique) | Human-readable trigger name |
| `triggerType` | String | yes | One of: `NODE_CREATED`, `NODE_UPDATED`, `PROPERTY_CHANGED`, `VALUE_MATCH`, `RELATIONSHIP_CHANGED`, `WORKFLOW_COMPLETED`, `MANUAL` |
| `isActive` | Boolean | yes | Enable/disable without deleting (default: `true`) |
| `description` | String | no | What this trigger does |
| `condition` | String | no | Qualifier for the trigger type (see §5) |
| `targetValue` | String | no | Value to match for `VALUE_MATCH` triggers |
| `operator` | String | no | Comparison operator: `EQ`, `NEQ`, `GT`, `LT`, `GTE`, `LTE`, `IN`, `CONTAINS`, `REGEX` |
| `priority` | Int | no | Execution order when multiple triggers match (lower = first, default: `0`) |

### 4.2 WorkflowInputMapping

Defines **how** to resolve an input parameter for the workflow from the mutated entity's data.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | String | yes | Parameter name passed to the workflow |
| `parameterType` | String | yes | `string`, `number`, `boolean`, `json`, `entity` |
| `isRequired` | Boolean | yes | Fail trigger if cannot resolve (default: `true`) |
| `defaultValue` | String | no | Fallback value when path resolution returns null |
| `mappingType` | String | yes | Resolution strategy: `path`, `literal`, `context`, `trigger_value` |
| `description` | String | no | What this parameter provides |

### 4.3 WorkflowDispatch

Runtime state node — one per trigger invocation. Tracks the lifecycle of a dispatched workflow.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `status` | String | yes | `pending`, `dispatched`, `running`, `completed`, `failed`, `cancelled` |
| `dispatchedAt` | DateTime | yes | When the trigger fired |
| `completedAt` | DateTime | no | When execution finished |
| `result` | String | no | JSON payload from workflow completion |
| `error` | String | no | Error message if failed |
| `inputSnapshot` | String | no | JSON of resolved input parameters at dispatch time |
| `triggerSnapshot` | String | no | JSON of trigger context (mutationName, entityId, changedFields) |
| `executionMode` | String | yes | `fire_and_forget`, `async`, `sync` |

## 5. Trigger Type Semantics

| triggerType | Required Relations | condition / targetValue Usage |
|-------------|-------------------|-------------------------------|
| `NODE_CREATED` | `TRIGGER_WATCHES_CLASS` → OntologyClass | — |
| `NODE_UPDATED` | `TRIGGER_WATCHES_CLASS` → OntologyClass | — |
| `PROPERTY_CHANGED` | `TRIGGER_WATCHES_PROPERTY` → OntologyProperty | Optional: add `operator` + `targetValue` to only fire when changed *to* a specific value |
| `VALUE_MATCH` | `TRIGGER_WATCHES_PROPERTY` → OntologyProperty | Required: `operator` + `targetValue`. Fires when property matches condition after mutation. |
| `RELATIONSHIP_CHANGED` | `TRIGGER_WATCHES_RELATION` → OntologyRelation | `condition`: `CREATED` \| `DELETED` \| `ANY` (default: `ANY`) |
| `WORKFLOW_COMPLETED` | `TRIGGER_AFTER_WORKFLOW` → Workflow | `condition`: `SUCCESS` \| `FAILURE` \| `ANY` (default: `SUCCESS`) |
| `MANUAL` | none (linked via ButtonAction → `TRIGGERS_WORKFLOW` → Workflow) | — |

### Evaluation Logic

```
For each mutation:
  1. Extract: entityType, entityId, mutationOp (create|update), inputData, changedFields
  2. Query active WorkflowTriggers linked to Workflows targeting this entityType
  3. For each trigger, evaluate:

     NODE_CREATED:
       match = (mutationOp == "create")

     NODE_UPDATED:
       match = (mutationOp == "update")

     PROPERTY_CHANGED:
       watchedProp = TRIGGER_WATCHES_PROPERTY.name
       match = (watchedProp IN changedFields)
       if operator + targetValue set:
         match = match AND evaluate(inputData[watchedProp], operator, targetValue)

     VALUE_MATCH:
       watchedProp = TRIGGER_WATCHES_PROPERTY.name
       match = evaluate(inputData[watchedProp], operator, targetValue)

     RELATIONSHIP_CHANGED:
       watchedRel = TRIGGER_WATCHES_RELATION.name (camelCase)
       match = (watchedRel appears in inputData connect/disconnect/create/delete keys)
       if condition == "CREATED": match = match AND (connect OR create)
       if condition == "DELETED": match = match AND (disconnect OR delete)

     WORKFLOW_COMPLETED:
       (evaluated separately when WorkflowDispatch.status transitions to completed/failed)

     MANUAL:
       (evaluated via ButtonAction click, not by middleware)
```

## 6. Relationship Model — Complete

### New Relationships

| Relationship | From | To | Cardinality | Edge Props | Description |
|-------------|------|----|-------------|------------|-------------|
| `WORKFLOW_HAS_TRIGGER` | Workflow | WorkflowTrigger | 0:N | — | Triggers that can fire this workflow |
| `WORKFLOW_HAS_INPUT_MAPPING` | Workflow | WorkflowInputMapping | 0:N | ord | Ordered input parameters |
| `WORKFLOW_TARGETS_CLASS` | Workflow | OntologyClass | 0:1 | — | Entity class this workflow operates on |
| `TRIGGER_WATCHES_CLASS` | WorkflowTrigger | OntologyClass | 0:1 | — | Class to watch for NODE_CREATED/NODE_UPDATED |
| `TRIGGER_WATCHES_PROPERTY` | WorkflowTrigger | OntologyProperty | 0:1 | — | Property to watch for PROPERTY_CHANGED/VALUE_MATCH |
| `TRIGGER_WATCHES_RELATION` | WorkflowTrigger | OntologyRelation | 0:1 | — | Relation to watch for RELATIONSHIP_CHANGED |
| `TRIGGER_AFTER_WORKFLOW` | WorkflowTrigger | Workflow | 0:1 | — | Upstream workflow for WORKFLOW_COMPLETED chaining |
| `INPUT_MAPPING_HAS_PATH` | WorkflowInputMapping | PathNode | 0:N | ord, isFirst, isLast | PathNode chain to resolve value from entity |
| `TRIGGERS_WORKFLOW` | ButtonAction | Workflow | 0:1 | — | Manual trigger from UI button |
| `HAS_WORKFLOW_DISPATCH` | *any entity* | WorkflowDispatch | 0:N | — | Polymorphic: entity has dispatch history |
| `DISPATCH_OF_WORKFLOW` | WorkflowDispatch | Workflow | 1:1 | — | Which workflow was dispatched |
| `DISPATCH_TRIGGERED_BY` | WorkflowDispatch | WorkflowTrigger | 0:1 | — | Which trigger fired (null for manual) |
| `DISPATCH_HAS_EXECUTION` | WorkflowDispatch | PromptExecution | 0:N | — | Links to existing execution tracking |

### Cross-Ontology References

These relationships reference classes from **other ontologies** — this is supported by the meta-ontology pattern:

| Referenced Class | Source Ontology | Used By |
|-----------------|-----------------|---------|
| OntologyClass | MetaOntology | WORKFLOW_TARGETS_CLASS, TRIGGER_WATCHES_CLASS |
| OntologyProperty | MetaOntology | TRIGGER_WATCHES_PROPERTY |
| OntologyRelation | MetaOntology | TRIGGER_WATCHES_RELATION |
| PathNode | DynamicUI | INPUT_MAPPING_HAS_PATH |
| ButtonAction | DynamicUI | TRIGGERS_WORKFLOW |
| PromptExecution | AIWorkflow (existing) | DISPATCH_HAS_EXECUTION |

## 7. Full Graph Diagram

```
                         ┌──────────────┐
                         │ ButtonAction │ (DynamicUI)
                         └──────┬───────┘
                                │ TRIGGERS_WORKFLOW
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                      Workflow (existing)                     │
│  name, description, status                                  │
│                                                             │
│  ── WORKFLOW_TARGETS_CLASS ──→ OntologyClass (MetaOntology) │
│  ── HAS_STEP ──→ Step → Prompt → PromptVersion (existing)  │
│                                                             │
│  ── WORKFLOW_HAS_TRIGGER ──→ WorkflowTrigger (NEW)         │
│  ── WORKFLOW_HAS_INPUT_MAPPING ──→ WorkflowInputMapping    │
└─────────────────────────────────────────────────────────────┘
          │                                    │
          │ WORKFLOW_HAS_TRIGGER               │ WORKFLOW_HAS_INPUT_MAPPING
          ▼                                    ▼
┌───────────────────────┐         ┌──────────────────────────┐
│   WorkflowTrigger     │         │  WorkflowInputMapping    │
│                       │         │                          │
│  name, triggerType,   │         │  name, parameterType,    │
│  isActive, condition, │         │  mappingType, isRequired │
│  targetValue, operator│         │  defaultValue            │
│  priority             │         │                          │
│                       │         │  ── INPUT_MAPPING_HAS_   │
│  ── TRIGGER_WATCHES_  │         │     PATH ──→ PathNode    │
│     CLASS             │         │     (ord, isFirst,       │
│  ── TRIGGER_WATCHES_  │         │      isLast)             │
│     PROPERTY          │         └──────────────────────────┘
│  ── TRIGGER_WATCHES_  │
│     RELATION          │
│  ── TRIGGER_AFTER_    │
│     WORKFLOW          │
└───────────────────────┘

┌─────────────────────────────────────────────┐
│          WorkflowDispatch (runtime)          │
│                                             │
│  status, dispatchedAt, completedAt,         │
│  result, error, inputSnapshot,              │
│  triggerSnapshot, executionMode              │
│                                             │
│  ◄── HAS_WORKFLOW_DISPATCH ── Entity        │
│  ── DISPATCH_OF_WORKFLOW ──→ Workflow        │
│  ── DISPATCH_TRIGGERED_BY ──→ WorkflowTrigger│
│  ── DISPATCH_HAS_EXECUTION ──→ PromptExec.  │
└─────────────────────────────────────────────┘
```

## 8. Input Parameter Resolution

### mappingType Strategies

| mappingType | Resolution | Example |
|-------------|-----------|---------|
| `path` | Traverse PathNode chain from the mutated entity to read a value | Firm → hasManagingPartner → email |
| `literal` | Use `defaultValue` as-is | `"gpt-4o"`, `"detailed"` |
| `context` | Injected by middleware from mutation context | Reserved names: `entityId`, `entityType`, `mutationName`, `mutationOp`, `userId`, `timestamp` |
| `trigger_value` | The value that caused the trigger to fire | For VALUE_MATCH: the matched property value. For PROPERTY_CHANGED: the new value. |

### Path Resolution Example

A workflow needs the firm's managing partner email as input:

```
WorkflowInputMapping(name="partnerEmail", mappingType="path", parameterType="string")
  │
  ├─ INPUT_MAPPING_HAS_PATH(ord=0, isFirst=true, isLast=false)
  │   └─ PathNode
  │       ├─ PATH_STEP_VIA_RELATION → OntologyRelation("HAS_MANAGING_PARTNER")
  │       └─ PATH_STEP_AT_CLASS → OntologyClass("ManagingPartner")
  │
  └─ INPUT_MAPPING_HAS_PATH(ord=1, isFirst=false, isLast=true)
      └─ PathNode
          └─ PATH_STEP_TO_PROPERTY → OntologyProperty("email")
```

At dispatch time, the middleware:
1. Starts at the mutated entity (e.g., Firm with id `firm-123`)
2. Traverses `HAS_MANAGING_PARTNER` relation
3. Reads `email` property from the connected ManagingPartner node
4. Passes `{ partnerEmail: "partner@example.com" }` to the workflow

## 9. Middleware Architecture

### Plugin Position in Apollo Pipeline

```
Client request
    ↓
Apollo Server receives mutation
    ↓
Neo4j GraphQL Library executes → data persists
    ↓
┌─────────────────────────────────────────┐
│  Validation Middleware (existing)        │
│  willSendResponse → fire-and-forget     │
│  Persists: ValidationState,             │
│  ValidationMessage, TabValidation,      │
│  PageValidation                         │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│  AI Workflow Trigger Middleware (NEW)    │
│  willSendResponse → fire-and-forget     │
│  After validation completes (priority)  │
│                                         │
│  1. Extract mutation context            │
│  2. Query matching WorkflowTriggers     │
│  3. Evaluate trigger conditions         │
│  4. Resolve WorkflowInputMappings       │
│  5. Create WorkflowDispatch node        │
│  6. Dispatch to /run-agent endpoint     │
│  7. On completion: update dispatch,     │
│     check WORKFLOW_COMPLETED triggers   │
└─────────────────────────────────────────┘
    ↓
Response sent to client
```

### Plugin Structure

```typescript
// src/plugins/ai-workflow-trigger-middleware.ts

export function createAiWorkflowTriggerPlugin(): ApolloServerPlugin<BaseContext> {
  return {
    async requestDidStart() {
      let mutationContext: MutationContext | null = null;

      return {
        async executionDidStart() {
          return {
            willResolveField({ args, info }) {
              if (info.parentType.name !== 'Mutation') return;
              // Capture: mutationName, entityType, entityId, inputData
              mutationContext = extractMutationContext(args, info);
            },
          };
        },

        async willSendResponse(requestContext) {
          if (!mutationContext) return;
          if (hasErrors(requestContext)) return;
          if (isMetaModelType(mutationContext.entityType)) return;

          // Fire-and-forget: evaluate triggers and dispatch
          evaluateAndDispatch(mutationContext, gqlContext)
            .catch(err => console.error('[aiWorkflowTrigger]', err));
        },
      };
    },
  };
}
```

### Dispatch Flow

```typescript
async function evaluateAndDispatch(ctx: MutationContext, gql: GraphQLContext) {
  // 1. Query workflows targeting this entity class with active triggers
  const workflows = await queryMatchingWorkflows(ctx.entityType, gql);

  // 2. Filter triggers that match current mutation
  for (const workflow of workflows) {
    for (const trigger of workflow.triggers) {
      if (!evaluateTrigger(trigger, ctx)) continue;

      // 3. Resolve input mappings
      const inputs = await resolveInputMappings(
        workflow.inputMappings, ctx.entityId, ctx.entityType, trigger, gql
      );

      // 4. Create WorkflowDispatch node
      const dispatchId = await createWorkflowDispatch({
        status: workflow.executionMode === 'sync' ? 'running' : 'pending',
        entityId: ctx.entityId,
        workflowId: workflow.id,
        triggerId: trigger.id,
        inputSnapshot: JSON.stringify(inputs),
        triggerSnapshot: JSON.stringify({
          mutationName: ctx.mutationName,
          entityId: ctx.entityId,
          changedFields: Object.keys(ctx.inputData),
        }),
        executionMode: workflow.executionMode,
      }, gql);

      // 5. Dispatch to TEA engine
      dispatchToWorkflowEngine(workflow, inputs, dispatchId, gql)
        .then(result => onWorkflowComplete(dispatchId, result, gql))
        .catch(err => onWorkflowFailed(dispatchId, err, gql));
    }
  }
}

async function onWorkflowComplete(dispatchId: string, result: any, gql: GraphQLContext) {
  // Update dispatch status
  await updateWorkflowDispatch(dispatchId, {
    status: 'completed',
    completedAt: new Date().toISOString(),
    result: JSON.stringify(result),
  }, gql);

  // Check for WORKFLOW_COMPLETED triggers (chaining)
  await evaluateWorkflowCompletedTriggers(dispatchId, 'SUCCESS', gql);
}
```

### Communication with TEA Engine

The middleware dispatches to the existing FastAPI `/run-agent` endpoint:

```typescript
async function dispatchToWorkflowEngine(
  workflow: WorkflowConfig,
  resolvedInputs: Record<string, unknown>,
  dispatchId: string,
  gql: GraphQLContext,
): Promise<any> {
  const response = await fetch(`${AI_WORKFLOW_URL}/run-agent`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': AI_WORKFLOW_API_KEY,
    },
    body: JSON.stringify({
      agent: workflow.agentName,
      workflow_id: workflow.id,
      context_node_id: dispatchId,  // WorkflowDispatch node carries input context
    }),
  });
  return response.json();
}
```

## 10. Trigger Query Pattern

The middleware needs to efficiently query matching workflows. This query runs once per mutation (cached by entity type):

```graphql
query MatchingWorkflows($entityClassName: String!) {
  workflows(where: {
    workflowTargetsClass_SOME: { name_EQ: $entityClassName }
    workflowHasTrigger_SOME: { isActive_EQ: true }
  }) {
    id
    name
    executionMode

    workflowHasTrigger(where: { isActive_EQ: true }) {
      id
      name
      triggerType
      condition
      targetValue
      operator
      priority

      triggerWatchesClass { name }
      triggerWatchesProperty { name }
      triggerWatchesRelation { name }
      triggerAfterWorkflow { id, name }
    }

    workflowHasInputMapping {
      name
      parameterType
      isRequired
      defaultValue
      mappingType

      inputMappingHasPath {
        pathStepViaRelation { name }
        pathStepAtClass { name }
        pathStepToProperty { name }
      }
    }
  }
}
```

### Caching Strategy

```typescript
// Cache workflow configs per entity type (same pattern as tab-field-mapper)
const workflowCache = new Map<string, WorkflowConfig[]>();

export function clearWorkflowTriggerCache(): void {
  workflowCache.clear();
}
```

Cache invalidated on:
- Server restart
- Mutation to Workflow, WorkflowTrigger, or WorkflowInputMapping nodes (detect via META_MODEL_TYPES set)

## 11. Execution Plan

### Phase 1 — Ontology Bootstrap (Story T-1)

**Goal:** Add WorkflowTrigger, WorkflowInputMapping, WorkflowDispatch classes and all relationships to the AIWorkflow ontology in Neo4j.

**Tasks:**
1. Create GraphQL bootstrap script (following `json-schema/bootstrap.ts` pattern)
2. Create 3 OntologyClass nodes (WorkflowTrigger, WorkflowInputMapping, WorkflowDispatch)
3. Create OntologyProperty nodes for each class
4. Create 12 OntologyRelation nodes with RELATION_SOURCE/RELATION_DESTINATION
5. Link all classes to AIWorkflow ontology via ONTOLOGY_HAS_ONTOLOGY_CLASS
6. Run `npm run generate` to produce GraphQL schema
7. Run `npm run build` to compile
8. Validate: query the new types via GraphQL introspection

**Depends on:** Nothing
**Produces:** GraphQL types available for CRUD

### Phase 2 — Middleware Scaffold (Story T-2)

**Goal:** Create the Apollo Server plugin that intercepts mutations and extracts context.

**Tasks:**
1. Create `src/plugins/ai-workflow-trigger-middleware.ts`
2. Implement `willResolveField` hook to capture mutation context
3. Implement `willSendResponse` hook with fire-and-forget dispatch
4. Add META_MODEL_TYPES exclusion set (including new trigger classes)
5. Register plugin in `server.ts` after validation middleware
6. Add types: `MutationContext`, `WorkflowConfig`, `TriggerMatch`

**Depends on:** Phase 1 (needs GraphQL types to query)
**Produces:** Plugin skeleton that logs matched triggers (no dispatch yet)

### Phase 3 — Trigger Evaluation Engine (Story T-3)

**Goal:** Implement the trigger matching logic for all 7 trigger types.

**Tasks:**
1. Implement `queryMatchingWorkflows()` — GraphQL query with caching
2. Implement `evaluateTrigger()` — switch on triggerType with condition evaluation
3. Implement operator evaluation: EQ, NEQ, GT, LT, GTE, LTE, IN, CONTAINS, REGEX
4. Implement relationship change detection from Neo4j GraphQL Library input shape
5. Add cache invalidation when trigger/workflow nodes are mutated
6. Unit tests for each trigger type

**Depends on:** Phase 2
**Produces:** Triggers evaluate correctly, logged but not dispatched

### Phase 4 — Input Resolution & Dispatch (Story T-4)

**Goal:** Resolve WorkflowInputMapping paths and dispatch to TEA engine.

**Tasks:**
1. Implement `resolveInputMappings()` — iterate mappings, resolve by mappingType
2. Implement `path` resolution — build GraphQL query from PathNode chain, execute, extract value
3. Implement `context` resolution — inject from mutation context
4. Implement `trigger_value` resolution — extract from trigger match
5. Implement `literal` resolution — use defaultValue
6. Implement `createWorkflowDispatch()` — GraphQL mutation to persist dispatch node
7. Implement `dispatchToWorkflowEngine()` — HTTP POST to `/run-agent`
8. Wire up completion callback: update WorkflowDispatch status

**Depends on:** Phase 3
**Produces:** End-to-end: mutation → trigger → dispatch → TEA execution → status update

### Phase 5 — Workflow Chaining (Story T-5)

**Goal:** Support WORKFLOW_COMPLETED triggers for chaining workflows.

**Tasks:**
1. Implement `onWorkflowComplete()` — update dispatch, query WORKFLOW_COMPLETED triggers
2. Implement `evaluateWorkflowCompletedTriggers()` — match condition (SUCCESS/FAILURE/ANY)
3. Implement cascade dispatch with cycle detection (prevent A→B→A loops)
4. Add `maxChainDepth` guard (default: 5) to prevent infinite chains
5. Integration test: WorkflowA → completes → triggers WorkflowB

**Depends on:** Phase 4
**Produces:** Chained workflow execution

### Phase 6 — Manual Trigger / ButtonAction Integration (Story T-6)

**Goal:** Enable users to trigger workflows from UI buttons.

**Tasks:**
1. Add `TRIGGERS_WORKFLOW` relation from ButtonAction (DynamicUI) to Workflow (AIWorkflow) in ontology bootstrap
2. Create UI handler: when ButtonAction with `TRIGGERS_WORKFLOW` is clicked, call dispatch endpoint
3. Extend `/run-agent` or create `/dispatch-workflow` endpoint that accepts entityId + workflowId
4. Skip trigger evaluation for manual dispatch (no condition to check)
5. Still resolve input mappings and create WorkflowDispatch node
6. Wire into existing ButtonAction → Heading → FormSection UI flow

**Depends on:** Phase 4
**Produces:** Users can click buttons to trigger AI workflows

### Phase 7 — Observability & DynamicUI Pages (Story T-7)

**Goal:** Create DynamicUI pages for managing triggers and viewing dispatch history.

**Tasks:**
1. Create VqMenu / Page / Tab / FormSection structure for WorkflowTrigger management
2. Create dispatch history TableView (WorkflowDispatch list with status, timestamps)
3. Add status badges for active/inactive triggers
4. Add dispatch status indicators (pending/running/completed/failed)
5. Wire PathNode chains for input mapping configuration UI

**Depends on:** Phase 6
**Produces:** Full UI for configuring and monitoring AI workflow triggers

## 12. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Trigger storm — one mutation fires many workflows | Medium | High | Priority ordering, max concurrent dispatches per entity, debounce window |
| Infinite chain loop (A triggers B triggers A) | Low | High | Cycle detection + maxChainDepth guard |
| Path resolution fails (deleted related nodes) | Medium | Medium | isRequired flag on WorkflowInputMapping, skip dispatch if required input missing |
| TEA engine unavailable | Medium | Medium | WorkflowDispatch persists with status=`failed`, error logged. Retry via manual re-dispatch. |
| Cache stale after trigger config change | Low | Medium | Cache invalidation on Workflow/WorkflowTrigger mutations (detect in middleware) |
| Cross-ontology relation breaks on schema regen | Low | High | Integration test: bootstrap → generate → query cross-ontology relations |

## 13. Open Questions

1. **Debounce** — Should multiple rapid mutations to the same entity coalesce into one trigger evaluation? (e.g., user edits 5 fields in quick succession)
2. **Retry policy** — Should failed dispatches auto-retry? If so, fixed or exponential backoff? (Currently deferred — manual re-dispatch via UI)
3. **Auth forwarding** — Should the middleware forward the original user's auth context to `/run-agent`? (Currently uses server-to-server API key)
4. **Validation dependency** — Should VALUE_MATCH triggers be able to watch validation status fields (e.g., `overallValid`)? This requires validation middleware to complete before trigger evaluation. Current design already orders them correctly.
5. **Batch triggers** — Deferred from v1. When needed: aggregate threshold trigger (e.g., "when 10 submissions reach status=ready"). Requires a separate polling/aggregation mechanism.
