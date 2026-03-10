# AI Workflow — Agent Instructions

## Mission

Build and evolve the AIWorkflow ontology in the graphology graph. The ontology defines Workflows, Steps, Prompts, PromptVersions, PromptExecutions, and supporting classes that the TEA YAMLEngine agents consume via GraphQL.

**Endpoint:** `POST http://localhost:4000/graphql` (`Content-Type: application/json`)

## Workflow

1. **Pick a task** — open [TODO.md](./TODO.md), find the next unchecked `- [ ]` item
2. **Read instructions** — each section links to a `tasks/*.md` file with goal, data points, scripts, and mutation patterns
3. **Execute** — run mutations via GraphQL or run a payload script
4. **Verify** — query the graph to confirm expected state
5. **Update instructions** — see "Maintaining Task Instructions" below
6. **Mark done** — update the item in TODO.md: `- [ ]` → `- [x]`
7. **Suggest next** — when no pending user request, suggest the next unchecked task from TODO.md. If multiple tasks are independent, offer to batch them in parallel (see "Parallel Batching" below).

---

> **IMPORTANT: Maintaining Task Instructions**
>
> As you execute tasks, the instruction files in `tasks/` are **living documents**.
> After completing a task or discovering new information:
>
> 1. Propose updates to the relevant `tasks/*.md` file (new data points, script outputs, status changes, lessons learned)
> 2. **HALT and show the proposed changes to the user for confirmation** before writing
> 3. Only update the file after user approval
>
> This ensures instructions stay accurate for future runs and the user retains control over documentation.

---

## Parallel Batching

When suggesting tasks, **always offer to group independent tasks into parallel batches**.

### How it works

1. **Agent proposes batches** — analyze unchecked items, identify which have no dependencies on each other, and present groups to the user
2. **User confirms or adjusts** — user picks which batches to run, may regroup
3. **Agent executes** — launch as many parallel processes as possible within the same batch. Items in Batch #N+1 wait until Batch #N completes.

### Dependency rules

| If task A... | Then task B... | Parallel? |
|---|---|---|
| Creates Workflow/Step nodes | Links Prompts to those Steps | No — B waits for A |
| Creates PromptVersion for Prompt X | Also modifies Prompt X | No — serialize |
| Creates PromptVersion for Prompt X | Creates PromptVersion for Prompt Y | Yes |
| Runs `npm run generate` | Needs new schema | No — B waits |

## AIWorkflow Ontology Reference

The generated ontology documentation is available at [`docs/reference/aiworkflow-ontology/`](./docs/reference/aiworkflow-ontology/).

### Key Files

| Type | File |
|------|------|
| Ontology overview | `docs/reference/aiworkflow-ontology/AIWorkflow.md` |
| Class docs (17) | `docs/reference/aiworkflow-ontology/<ClassName>.md` |
| Class schemas (17) | `docs/reference/aiworkflow-ontology/<ClassName>.graphql` |
| Relation docs (8) | `docs/reference/aiworkflow-ontology/<RELATION_NAME>.md` |
| Relation schemas (8) | `docs/reference/aiworkflow-ontology/<RELATION_NAME>.graphql` |

### Core Classes

| Class | Purpose |
|-------|---------|
| Workflow | Top-level workflow container |
| Step | Ordered step within a workflow |
| Prompt | Question/instruction template |
| PromptVersion | Versioned content of a prompt (active/draft/archived) |
| PromptBody | Structured prompt body content |
| PromptExecution | Record of a prompt being executed |
| PromptResponse | LLM response linked to an execution |
| PromptOutput | Structured output from a prompt |
| ContextNode | Document/data context provided to a prompt |
| Company | Company entity |
| CompanyAnalysis | Analysis result for a company |
| Sector | Industry sector classification |
| Party | Legal party |
| LegalEntity | Legal entity |
| Jurisdiction | Legal jurisdiction |
| DocumentSection | Section within a document |
| DocumentTopic | Topic classification for documents |

### Key Relations

| Relation | From → To | Purpose |
|----------|-----------|---------|
| HAS_STEP | Workflow → Step | Steps in a workflow |
| HAS_PROMPT | Step → Prompt | Prompts within a step |
| HAS_VERSION | Prompt → PromptVersion | Version history |
| HAS_EXECUTION | PromptVersion → PromptExecution | Execution records |
| HAS_CONTEXT | PromptExecution → ContextNode | Input context |
| HAS_RESPONSE | PromptExecution → PromptResponse | LLM responses |
| FOLLOWED_BY | Step → Step | Step ordering |
| DEFINES_CLASS / DEFINES_PROPERTY | Ontology structure relations |

## Existing Components

### Custom Actions (`actions/graphology.py`)

TEA actions that interact with the graphology Apollo Server:

| Action | Purpose |
|--------|---------|
| `graphology.get_questions` | Fetch active PromptVersions for a workflow |
| `graphology.save_responses` | Persist PromptExecution + ContextNode + PromptResponse |
| `graphology.collect_answers` | Fan-in parallel LLM results |
| `graphology.get_node` | Fetch any node by ID via schema introspection |
| `graphology.update_node` | Update scalar fields on any node |

### YAML Agents (`agents/`)

| Agent | Purpose |
|-------|---------|
| `import_matter_qa` | Full Q&A pipeline: fetch questions → parallel LLM → save responses |
| `file_extraction` | Extract structured data from ApplicationFormFile via LlamaExtract |

## Schema Generation

The AIWorkflow ontology lives in the **rankellix** Neo4j database. To regenerate schema/docs:

```bash
# From graphology directory (AppTree/_visionquest/graphology)
# Temporarily set .env to rankellix database:
#   NEO4J_URI=bolt+s://neo4j.visionquest.space:7687
#   NEO4J_DATABASE=rankellix
npm run build && NODE_OPTIONS="--max-old-space-size=16384" npm run generate
# Then restore .env to native database and regenerate
```

## File Conventions

| Type | Location |
|------|----------|
| YAML agents | `agents/<agent-name>.yaml` |
| Custom actions | `actions/<module>.py` |
| Task instructions | `tasks/<N>-<name>.md` |
| Ontology reference | `docs/reference/aiworkflow-ontology/` |
| Complex task refs | `references/<name>.md` |

## Key References

| File | Purpose |
|------|---------|
| `docs/reference/aiworkflow-ontology/AIWorkflow.md` | Ontology overview with all classes and relations |
| `actions/graphology.py` | GraphQL actions for TEA agents |
| `agents/import_matter_qa.yaml` | Main Q&A workflow agent |
| `agents/file_extraction.yaml` | Document extraction agent |
