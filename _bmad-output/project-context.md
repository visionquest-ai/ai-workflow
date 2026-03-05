---
project_name: 'ai-workflow'
package_name: '@vq/ai-workflow'
parent_project: 'vqApp'
user_name: 'Peconick'
date: '2026-03-04'
role_in_vqapp: 'workflow engine module'
---

# Project Context for AI Agents — @vq/ai-workflow

_This file contains critical rules and patterns that AI agents must follow when implementing code in this module._

---

## Submodule Boundary Rules

This repository is a **git submodule** of [vqApp](https://github.com/visionquest-ai/vqApp) at `modules/ai-workflow/`.

### Scope

- **This module owns:** FR-8, FR-9 (AI workflow ontology built on Graphology, workflow UI export + headless executor)
- **Dependencies:** `@vq/graphology` (build artifacts — extends meta-ontology for workflow domain)
- **Stories and epics here affect ONLY this module.** Never create a story that modifies files in another submodule.

### What Belongs Here

- Workflow ontology bootstrap extending Graphology meta-ontology (`author/workflow-ontology-bootstrap.ts`)
- Generators: workflow schema, workflow UI export, executor config (`author/generators/`)
- Runtime: workflow executor (headless), step runner, resolvers (`runtime/executors/`)
- the_edge_agent integration (`runtime/agents/the_edge_agent/` — git submodule of visionquest-ai/the_edge_agent, fork of fabceolin/the_edge_agent)

### What Does NOT Belong Here

- Meta-ontology code (belongs in graphology)
- UI components or dynamic rendering (belongs in app-tree/shared-ui)
- vqApp composition logic

### Cross-Module Contract

**Consumes:**
- `@vq/graphology` build artifacts (meta-ontology schema, validation predicates)

**Exports:**
- `build/schema/workflow-ontology/` — Workflow GraphQL fragments
- `build/workflows/` — Generated workflow definitions
- `build/executor-configs/` — Generated executor configurations
- `build/manifest.json` — Version, checksums, artifact inventory
- `runtime/index.ts` — `registerAiWorkflow()` function + public API

---

## Technology Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Language | TypeScript (ESM) | Node.js 20.x LTS |
| Database | Neo4j 5.x | Local instance for workflow ontology authoring |
| Testing | Vitest | |
| Agent Framework | the_edge_agent | Fork maintained at visionquest-ai/the_edge_agent |

## Critical Implementation Rules

1. **Workflow ontology extends Graphology meta-ontology** — Not a separate ontology system
2. **ESM imports require `.js` extension**
3. **Separate type imports from value imports**
4. **Result objects for public async APIs**
5. **`author/` never imported by `runtime/`**
6. **the_edge_agent fork must sync periodically with upstream** (fabceolin/the_edge_agent)
7. **All generated files include header** — `// AUTO-GENERATED — DO NOT EDIT MANUALLY`

## Project Status

New module. Repository just created. No existing code or BMAD artifacts yet.
