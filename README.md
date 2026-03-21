# @vq/ai-workflow

FastAPI service for running TEA (The Edge Agent) YAML agents.

## Endpoints

### `GET /health`

Health check. No auth required. Returns `503` when RSS exceeds memory threshold (configurable via `MEMORY_LIMIT_MB`) and no agents are running — used by k8s livenessProbe to trigger restarts.

### `POST /run-agent`

Run a TEA agent with a **graphology context node**. The node is fetched from graphology by ID and injected as `matter_context` into the agent's input state.

**Auth:** `x-api-key` header (must match `RUN_AGENT_API_KEY` env var).

```json
{
  "agent": "file_classification",
  "context_node_id": "abc-123",
  "workflow_id": "wf-456",
  "async_mode": false
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `agent` | yes | YAML agent name (without `.yaml` extension) |
| `context_node_id` | yes* | Graphology node ID to fetch as context |
| `application_form_id` | yes* | Alias for `context_node_id` |
| `workflow_id` | no | Workflow node ID (validated if provided) |
| `async_mode` | no | `true` returns 202 + `job_id` for polling |

\* One of `context_node_id` or `application_form_id` is required.

**Async mode:** When `async_mode: true`, returns `202 Accepted` with a `job_id`. Poll `GET /run-agent/jobs/{job_id}` for status.

### `GET /run-agent/jobs/{job_id}`

Poll async job status. Returns `running`, `success`, or `error` with result payload.

**Auth:** `x-api-key` header.

### `POST /run-prompt`

Run a TEA agent with **arbitrary input state** — no graphology context fetch. Designed for generic agents like `llm_prompt` that don't need a graph node.

**Auth:** `x-api-key` header.

```json
{
  "agent": "llm_prompt",
  "input_state": {
    "system_prompt": "You are a helpful assistant.",
    "user_message": "What is 2+2? Reply with just the number."
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `agent` | yes | YAML agent name (without `.yaml` extension) |
| `input_state` | yes | Dict passed directly as agent input state |

**Structured output example:**

```json
{
  "agent": "llm_prompt",
  "input_state": {
    "system_prompt": "Extract company info.",
    "user_message": "Acme Corp is a tech company.",
    "output_schema": {
      "title": "company_info",
      "type": "object",
      "properties": {
        "name": { "type": "string" },
        "industry": { "type": "string" }
      },
      "required": ["name", "industry"],
      "additionalProperties": false
    }
  }
}
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RUN_AGENT_API_KEY` | yes | — | API key for endpoint auth |
| `GRAPHOLOGY_URL` | no | `http://localhost:4000` | Graphology Apollo endpoint |
| `GRAPHOLOGY_API_KEY` | no | `""` | Graphology API key |
| `AGENTS_DIR` | no | `./agents` | Directory containing YAML agents |
| `ACTIONS_DIR` | no | `./actions` | Directory containing action modules |
| `MEMORY_LIMIT_MB` | no | `768` | RSS threshold for health check |

## Testing

```bash
cd visionQuest/ai-workflow
pytest tests/test_app.py -v
```
