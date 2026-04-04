# AI-Workflow Execution Guide

**Service:** `ai-workflow` (Cloud Run, `rankellix-law` project)  
**Entry point:** `app.py` (FastAPI)  
**Agents directory:** `agents/`  
**Actions directory:** `actions/`

---

## Overview

The ai-workflow service executes YAML-defined agents via TEA (The Edge Agent). Each agent is a state machine: nodes receive state, perform work, and pass state to the next node via conditional edges.

Two primary pipelines exist:

| Pipeline | Entry point | Trigger |
|---|---|---|
| **File Extraction** | `file_extraction.yaml` | `ApplicationFormFile` node created |
| **Matter Analysis** | `import_matter_qa.yaml` → `matter_strategy.yaml` → `matter_rewrite.yaml` | `ProtoMatter` node created / manual |

Supporting agents: `file_classification.yaml` (sub-agent of file_extraction), `llm_prompt.yaml` (generic), `scrape_law_firm.yaml` (firm enrichment).

---

## HTTP API

All endpoints require `x-api-key` header (`RUN_AGENT_API_KEY` env var).

```
POST /run-agent          — run agent for one or many context nodes
GET  /run-agent/jobs/:id — poll async job status
POST /run-prompt         — run agent with arbitrary input state (no graphology context)
POST /extract-callback   — receive LlamaExtract webhook (called by LlamaExtract, not users)
GET  /extract-callback/:job_id — check if a webhook result arrived
GET  /health             — liveness probe (503 if RSS > MEMORY_LIMIT_MB with no active requests)
```

### Run a single agent (async)
```json
POST /run-agent
{
  "agent": "file_extraction",
  "contextNodeId": "<ApplicationFormFile-id>",
  "asyncMode": true
}
→ 202 { "job_id": "...", "status": "accepted" }
```

### Fan-out batch (multiple nodes simultaneously)
```json
POST /run-agent
{
  "agent": "import_matter_qa",
  "workflow_id": "<workflow-id>",
  "contextNodeIds": ["<ProtoMatter-1>", "<ProtoMatter-2>"],
  "job_ids": { "<ProtoMatter-1>": "<AgentJob-1>", "<ProtoMatter-2>": "<AgentJob-2>" },
  "asyncMode": true
}
→ 202 { "job_ids": ["...", "..."], "status": "accepted" }
```

`job_ids` maps each `context_node_id` to a pre-created `AgentJob` ID in Neo4j. The service calls `agentJobCallback` on Apollo when each job completes.

---

## Pipeline 1 — File Extraction

**Agent:** `file_extraction.yaml`  
**Input node type:** `ApplicationFormFile`  
**Trigger:** `POST /run-agent { "agent": "file_extraction", "contextNodeId": "<id>" }`

### What it does

Extracts structured data from a legal directory submission file (PDF or Word), classifies its directory and year, fuzzy-matches practice areas and regions against the graph, and creates `ProtoMatter` nodes for each matter found in the extraction payload.

### Execution stages

```
[1] Fetch node          graphology.get_node → ApplicationFormFile
[2] Download + extract  GCS/HTTP download, text extraction, Word→PDF conversion (LibreOffice)
[3] Status update       → "reading" (best-effort)
[4] Detect directory    TIER 1: filename patterns
                        TIER 2: weighted content keyword scoring (min score=2, margin=2)
                        TIER 3: LLM classification (file_classification sub-agent) if TIER 1+2 inconclusive
[5] Save directory name graphology.update_node (directoryName)
[6] Lookup directory ID fuzzy match against Directory nodes in graph
[7] Resolve agent       LlamaExtract agent name from variables (e.g. "rankellix-chambers-partners-balanced")
[8] Submit job          llamaextract.submit_job → async job_id
                        Registers webhook: POST {AI_WORKFLOW_URL}/extract-callback
                        (nodeId routing not yet implemented — next iteration)
[9] Poll status         15s interval, 700s max (until LlamaExtract webhook fires or timeout)
[10] Get result         llamaextract.get_result → extraction payload
[11] Prepare payload    serialize JSON, derive year (precedence: LlamaExtract > content > filename > node)
[12] Practice area      extract from payload (directory-specific JSON path) → fuzzy match → persist
[13] Category           PracticeArea parent traversal → derive category
[14] Region             extract region string → fuzzy match against Region hierarchy → persist
[15] Firm/department    extract fileFirm, fileDepartment from payload
[16] Save payload       graphology.update_node (payload, classificationPayload, detectedYear)
[17] Expand ProtoMatters create ProtoMatter nodes for each matter in payload
                        connect: FILE_HAS_PROTO_MATTER, DEPARTMENT_HAS_PROTO_MATTER
[18] Finalize           update ApplicationFormFile.status → "succeeded" | "failed"
```

### Directory-to-agent mapping

| Directory | LlamaExtract agent base name | Extraction mode |
|---|---|---|
| Chambers | `chambers-partners` | BALANCED |
| IFLR1000 | `iflr-1000` | BALANCED |
| Legal 500 | `the-legal-500` | PREMIUM |
| ITR | `itr-world-tax` | BALANCED |
| Leaders League | `leaders-league` | BALANCED |

Agent full name: `rankellix-{base}-{mode}` (hardcoded in `file_extraction.yaml` variables section).

### File classification sub-agent (TIER 3)

Three variants depending on caching needs:

| Variant | Cache | Use case |
|---|---|---|
| `file_classification.yaml` | In-memory dict | Single process, fast |
| `file_classification_neo4j.yaml` | Neo4j (DuckDB LTM) | Persistent, cross-process |
| `file_classification_nocache.yaml` | None | Testing/debugging |

Classification uses GPT with PDF vision (first 3 pages at 150 DPI) when available. Confidence ≥ 0.5 required to use result.

### LlamaExtract webhook

When `AI_WORKFLOW_URL` env var is set, each job is submitted with a webhook:
- URL: `{AI_WORKFLOW_URL}/extract-callback?nodeId={context_node_id}`
- Auth: `x-api-key: {RUN_AGENT_API_KEY}` header
- Events: `extract.success`, `extract.error`

On callback receipt, `app.py` stores the result in `_extract_results[job_id]`.  
**Note:** The polling loop in `poll_extraction_status` still runs as fallback — webhook is registered but not yet used to skip polling (next iteration).

### Known quirks

- Word docs (`.docx`) must be converted to PDF before submission — LlamaExtract has a bug with `.docx` (0 pages extracted).
- Status updates throughout the pipeline are best-effort (failures don't abort the workflow).
- Year detection: LlamaExtract result takes precedence over filename/content detection.

---

## Pipeline 2 — Matter Analysis

Three agents run in sequence: `import_matter_qa` → `matter_strategy` → `matter_rewrite`.

```
ProtoMatter created
    ↓
import_matter_qa     (Stage A: Q&A — one parallel LLM call per active completion in the workflow)
    ↓
matter_strategy      (Stage B: gate evaluation + strategy package)   ← manual trigger today
    ↓
matter_rewrite       (Stage C: rewrite + integrity audit)            ← chained from Stage B
```

---

### Stage A — Import Matter Q&A (`import_matter_qa.yaml`)

**Input node type:** `Matter` or `ProtoMatter`  
**Trigger:** `POST /run-agent { "agent": "import_matter_qa", "workflow_id": "<id>", "contextNodeId": "<id>" }`

#### What it does

Runs all active prompts for an `ImportMatter` workflow against a matter's content. Each question gets an independent parallel LLM call. Results are persisted as `PromptExecution` nodes linked to the matter. When the source node is a `ProtoMatter`, updates its status to `imported` and creates a `PROTO_MATTER_IMPORTED_AS` relation to the `Matter`.

#### Execution stages

```
[1] fetch_step_config   analysis.get_step_config (step="answer_question", workflow="MatterQA")
[2] fetch_context       graphology.get_node (if context_node_id provided and no matter_context)
[3] fetch_questions     graphology.get_questions (all active questions for workflow_id)
[4] answer_questions    dynamic_parallel fan-out — one LLM call per active completion in the workflow
                        max_concurrency: 100 (semaphore)
                        rate limit: 500 RPM
                        LLM: gpt-5.3-chat, temperature=1
                        User message: "MATTER CONTEXT:\n{context}\n---\nQUESTION: {prompt.content}"
[5] collect_answers     fan-in — maps ParallelFlowResult → {versionId, llmRequest, llmResponse, status}
[6] post_process        analysis.post_process_batch → creates PromptExecution nodes in graph
                        links: PromptExecution → HAS_CONTEXT → Matter/ProtoMatter node
[7] finalize_proto      (ProtoMatter only) update status → "imported"|"error"
                        create PROTO_MATTER_IMPORTED_AS relation
```

#### Concurrency model

| Setting | Value | Purpose |
|---|---|---|
| `max_concurrency` | 100 | Max simultaneous LLM calls (semaphore) |
| `rpm` | 500 | Max Azure OpenAI requests per minute |

All LLM calls are I/O-bound — CPU usage stays low even at 100 concurrent. The real constraint is Azure's RPM limit.

#### Context provision

Two modes accepted:
- `context_node_id` only → agent fetches node from graphology (`fetch_context` step)
- `matter_context` pre-populated → agent skips fetch (used when called via `/run-agent`, which pre-fetches)

---

### Stage B — Matter Strategy (`matter_strategy.yaml`)

**Trigger:** Manual — auto-chaining from Stage A not yet implemented  
**LLM:** `claude-sonnet-4-6`, temperature=0.3

> **Note:** This agent uses a different request shape than other agents — it does not use `workflow_id` and takes `directory_code`, `target_research`, and `metadata` instead:
> ```json
> POST /run-agent
> {
>   "agent": "matter_strategy",
>   "contextNodeId": "<Matter-id>",
>   "directory_code": "CH",
>   "target_research": "...",
>   "metadata": { "matter_type": "...", "jurisdiction": "..." }
> }
> ```

#### What it does

Evaluates a deterministic gate (no LLM) against Stage A criteria results. If the matter passes, generates a strategy package for the target directory. On success, chains into Stage C (`matter_rewrite`).

#### Execution stages

```
[1] fetch_stage_config   analysis.get_stage_config (stage="B_strategy", workflow="MatterAnalysis")
[2] fetch_stage_a        analysis.get_stage_a_results
[3] evaluate_gate        deterministic rule engine (matter_config.evaluate_minimum_gate)
                         inputs: directory_profile.gate_rules + stage_a criteria
                         output: gate_result.passed (bool) — no LLM involved
[4] render_prompt        analysis.render_prompt (only if gate passes)
[5] build_strategy       llm.call (claude-sonnet-4-6, 20 RPM) → strategy package JSON
[6] post_process         analysis.post_process → persist to graph (gate-pass or gate-fail)
[7] invoke_rewrite       agents.invoke_agent → matter_rewrite (only if gate passes)
```

Gate failure is a normal outcome — it's persisted to the graph and the pipeline stops cleanly.

---

### Stage C — Matter Rewrite (`matter_rewrite.yaml`)

**Trigger:** Called by `matter_strategy` via `agents.invoke_agent`  
**LLM:** `claude-sonnet-4-6`, temperature=0.7

#### What it does

Generates the rewritten matter using the strategy package from Stage B. Runs an integrity audit combining LLM self-report with deterministic checks. Never reasons independently — fully executes the strategy as defined.

#### Execution stages

```
[1] fetch_stage_config   analysis.get_stage_config (stage="C_rewrite", workflow="MatterAnalysis")
[2] render_prompt        analysis.render_prompt
[3] generate_rewrite     llm.call (claude-sonnet-4-6, 20 RPM) → rewritten matter JSON
[4] integrity_audit      parse LLM JSON response
                         deterministic: empty rewrite → object_preserved=false
                         integrity_passed = object_preserved AND NOT invented_facts
                                           AND NOT forced_criteria AND directory_fit
[5] post_process         analysis.post_process → persist to graph
```

---

## Other Agents

### `llm_prompt.yaml` — Generic LLM wrapper

Simple wrapper for one-off LLM calls with optional JSON schema output.

```
POST /run-prompt { "agent": "llm_prompt", "input_state": { "system_prompt": "...", "user_message": "...", "output_schema": {...} } }
```

Supports structured output (`response_format: json_schema`) when `output_schema` is provided.

---

### `scrape_law_firm.yaml` — Firm website enrichment

Scrapes a `LegalFirm` node's website using ScrapeGraphAI. Extracts firm metadata (name, founded year, partners, offices, practice areas, awards) and persists to `websitePayload`.

```
POST /run-agent { "agent": "scrape_law_firm", "contextNodeId": "<LegalFirm-id>" }
```

Status lifecycle: `running` → `completed` | `failed`.

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `RUN_AGENT_API_KEY` | API key for all endpoints (required — service won't start without it) |
| `GRAPHOLOGY_URL` | Apollo GraphQL endpoint (e.g. `https://graphoogy-xxx.run.app/graphql`) |
| `GRAPHOLOGY_API_KEY` | Apollo API key |
| `LLAMAEXTRACT_API_KEY` | LlamaExtract / LlamaCloud API key |
| `AI_WORKFLOW_URL` | This service's own public URL — used to register LlamaExtract webhooks |
| `MEMORY_LIMIT_MB` | RSS threshold for health check restart (default: 768) |
| `ACTIONS_DIR` | Path to actions directory (default: `./actions`) |
| `AGENTS_DIR` | Path to agents directory (default: `./agents`) |

---

## Data Flow Diagram

```
ApplicationFormFile
    │
    └─► file_extraction
              │
              ├─ file_classification (sub-agent, TIER 3 only)
              ├─ LlamaExtract (async job → webhook callback)
              ├─ fuzzy_match (practice area, region, directory)
              └─ creates ProtoMatter nodes
                        │
                        └─► import_matter_qa (Stage A)
                                  │ N × parallel LLM calls (one per active completion)
                                  └─► PromptExecution nodes
                                            │
                                            └─► matter_strategy (Stage B)
                                                      │ deterministic gate
                                                      └─► matter_rewrite (Stage C)
                                                                │ LLM rewrite + audit
                                                                └─► persisted to graph
```
