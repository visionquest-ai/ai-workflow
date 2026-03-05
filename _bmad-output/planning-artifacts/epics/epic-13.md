---
epic_id: 13
title: 'AI Workflow Engine'
target_module: ai-workflow
phase: 'Phase 3'
source: '_bmad-output/planning-artifacts/epics.md'
shardedAt: '2026-03-05'
frs_covered: [FR36, FR37, FR38]
nfrs_addressed: [NFR-13]
depends_on: ['Epic 1 (graphology)']
cross_module_dependencies:
  - module: graphology
    repo: https://github.com/visionquest-ai/graphology.git
    reason: 'Extends Graphology meta-ontology with workflow domain classes; uses bootstrap patterns and generator pipeline'
    current_paths:
      - modules/graphology/src/bootstrap.ts             # Meta-ontology creation patterns to extend
      - modules/graphology/src/generator.ts             # Generator orchestrator pattern to follow
      - modules/graphology/src/templates/               # Handlebars templates as reference
      - modules/graphology/generated/schema/ontology/   # Meta-ontology schema definitions
    target_paths_after_epic_1:
      - modules/graphology/build/schema/                # Meta-ontology schemas (foundation for workflow extension)
      - modules/graphology/build/manifest.json          # Version for dependency declaration
      - modules/graphology/runtime/types.ts             # ModuleRegistration interface to implement
      - modules/graphology/author/                      # Bootstrap and generator patterns to replicate
---

# Epic 13: AI Workflow Engine

System extends the meta-ontology for workflow domain definitions, generates workflow configurations, and executes workflows headlessly without UI dependency.

**FRs covered:** FR36, FR37, FR38
**NFRs addressed:** NFR-13
**Depends on:** Epic 1 (graphology)

### Cross-Module Dependencies

**graphology** (visionquest-ai/graphology) -- via Epic 1 outputs:
- `build/schema/` -- meta-ontology schema definitions that workflow ontology extends
- `runtime/types.ts` -- `ModuleRegistration` interface implemented by `registerAiWorkflow()`
- `author/` -- bootstrap and generator patterns replicated for workflow-ontology-bootstrap
- Currently at: `src/bootstrap.ts`, `src/generator.ts`, `generated/schema/ontology/`

## Stories

### Story 13.1: AI Workflow Module Convention & Registration

As a Module Developer,
I want the ai-workflow module structured with the standard convention and registration API,
So that it can be composed into vqApp and operate independently.

**Acceptance Criteria:**

**Given** the ai-workflow module repository
**When** structured according to module convention
**Then** `author/` contains: workflow-schema-generator, workflow-ui-export-generator, executor-config-generator, workflow-ontology-bootstrap
**And** `runtime/` contains: registerAiWorkflow(), workflow-executor, step-runner, resolvers
**And** `runtime/agents/the_edge_agent/` is a git submodule pointing to visionquest-ai/the_edge_agent
**And** `build/` contains generated artifacts: schema, workflows, executor-configs
**And** BMAD v6 installed with module-specific project-context.md
**And** module operates in authoring mode (needs Neo4j) and consumption mode (built artifacts only)

### Story 13.2: Workflow Ontology Extension

As a Domain Builder,
I want the meta-ontology extended for workflow domain definitions,
So that I can define AI workflows as formal ontology constructs.

**Acceptance Criteria:**

**Given** the Graphology meta-ontology as foundation
**When** the workflow ontology is bootstrapped via `author/workflow-ontology-bootstrap.ts`
**Then** workflow-specific classes are defined: Workflow, Step, Trigger, Condition, Action
**And** workflow relationships are defined: step ordering, conditional branching, data flow
**And** workflow constraints are defined: required inputs/outputs per step, valid step transitions
**And** the workflow ontology is a valid extension of the Graphology meta-ontology
**And** generators can produce workflow schemas from the workflow ontology

### Story 13.3: Workflow Generation & Headless Executor

As a Domain Builder,
I want workflow definitions generated from the ontology and executable without UI dependency,
So that backend AI logic runs reliably as defined workflows.

**Acceptance Criteria:**

**Given** a workflow defined in the workflow ontology
**When** the generator pipeline runs
**Then** `build/workflows/` contains generated workflow definitions
**And** `build/executor-configs/` contains configuration for the headless executor
**And** the workflow executor (`runtime/executors/workflow-executor.ts`) runs workflows step-by-step
**And** execution is headless -- no UI dependency (FR38)
**And** step-runner handles diverse patterns: data pipelines, approval flows, agent chains
**And** execution results are logged and queryable via GraphQL
**And** the_edge_agent integration enables AI-powered workflow steps
