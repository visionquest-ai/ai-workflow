---
epic_id: 14
title: 'File Extraction Agent & Async Endpoint'
target_module: ai-workflow
phase: 'Phase 3'
source: 'in-session planning'
shardedAt: '2026-03-08'
frs_covered: []
nfrs_addressed: []
depends_on: []
cross_module_dependencies:
  - module: graphology
    repo: https://github.com/visionquest-ai/graphology.git
    reason: 'Reads/writes ApplicationFormFile nodes via GraphQL (storageUrl, payload, directoryName)'
  - module: spa-base
    repo: https://github.com/visionquest-ai/spa-base.git
    reason: 'LlamaExtract agents uploaded via spa-base/scripts/rankellix/upload_to_llamaextract.py; GCS credentials from spa-base service account'
---

# Epic 14: File Extraction Agent & Async Endpoint

Provides a production-ready file extraction pipeline that downloads documents from GCS, converts Word docs to PDF (workaround for LlamaExtract docx bug), extracts structured data via pre-created LlamaExtract agents, and persists results to GraphQL. Supports both synchronous and asynchronous HTTP invocation to handle long-running extractions (up to 300s).

**Dependencies:**
- Graphology Apollo Server (Neo4j) for ApplicationFormFile nodes
- LlamaExtract API with pre-created Rankellix agents (`rankellix-{base_name}-{mode}`)
- GCS for file storage (`gs://` URLs)
- LibreOffice headless for docx-to-PDF conversion

## Stories

### Story 14.1: Async Agent Endpoint

As a calling application,
I want the `/run-agent` endpoint to support both sync and async execution modes,
So that long-running agents (like file extraction with 300s LlamaExtract timeout) don't cause HTTP timeouts.

**Acceptance Criteria:**

**Given** a POST to `/run-agent` with default parameters
**When** the agent completes within the HTTP timeout
**Then** the response contains the full agent result (status, error, payload) synchronously

**Given** a POST to `/run-agent` with `"async": true`
**When** the request is received
**Then** a job ID is returned immediately with status `"accepted"`
**And** the agent runs in the background

**Given** a job ID from an async invocation
**When** GET `/run-agent/{job_id}` is called
**Then** the current status is returned (`running`, `success`, `error`)
**And** when complete, the full result (status, error, payload) is included

**Given** the sync endpoint
**When** uvicorn timeout is configured
**Then** the keep-alive timeout supports at least 600s for sync mode

**Technical Notes:**
- Current `app.py` `/run-agent` endpoint is synchronous
- TEA engine `graph.invoke()` is blocking — async mode needs background thread/task
- Agent result must include `status`, `error`, and `payload` from agent state
- Job storage can be in-memory dict (single instance) initially
- The TEA edge agent has built-in async polling support for LlamaExtract (`async_mode=True, use_rest=True`)

### Story 14.2: File Extraction Agent Hardening

As a platform operator,
I want the file extraction YAML agent to be production-ready,
So that it handles edge cases, reports errors clearly, and cleans up resources.

**Acceptance Criteria:**

**Given** a file extraction request for a supported directory (chambers, iflr1000, legal500, itr, leadersleague)
**When** the agent runs end-to-end
**Then** the file is downloaded from GCS, converted to PDF if needed, extracted via LlamaExtract, and the payload is saved to the node

**Given** a docx file
**When** the agent processes it
**Then** it is converted to PDF via docx2pdf (LibreOffice) before sending to LlamaExtract
**And** temporary files are cleaned up after extraction

**Given** a LlamaExtract extraction failure
**When** the error is returned
**Then** the error details are saved to the node payload as `{"error": "...", "status": "failed"}`
**And** the HTTP response includes the error

**Given** an unsupported directoryName
**When** the agent tries to resolve the LlamaExtract agent
**Then** a clear error is returned listing supported directories

**Technical Notes:**
- YAML agent: `agents/file_extraction.yaml`
- LlamaExtract agents: `rankellix-{base_name}-balanced` (balanced mode, pre-created)
- docx2pdf wraps LibreOffice — requires `libreoffice-writer` in Docker image
- Dockerfile already includes LibreOffice and requirements.txt includes docx2pdf, fsspec, gcsfs

### Story 14.3: Docker & Deployment Readiness

As a DevOps engineer,
I want the ai-workflow Docker image to include all dependencies for file extraction,
So that it can be deployed to cloud headless compute.

**Acceptance Criteria:**

**Given** the Dockerfile
**When** the image is built
**Then** it includes `libreoffice-writer` for PDF conversion
**And** Python dependencies include `docx2pdf`, `fsspec`, `gcsfs`
**And** the image size is reasonable (< 500MB)

**Given** the deployment configuration
**When** deployed to Kubernetes/Cloud Run
**Then** environment variables are configured: `LLAMAEXTRACT_API_KEY`, `GRAPHOLOGY_URL`, `GRAPHOLOGY_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`
**And** the health check endpoint responds

**Given** a long-running extraction (up to 300s)
**When** running behind a load balancer
**Then** timeout configurations allow the request to complete (sync mode) or return immediately (async mode)

**Technical Notes:**
- Current Dockerfile: `python:3.11-slim` + `libreoffice-writer` (~200MB overhead)
- Alternative: Gotenberg sidecar for PDF conversion (lighter main image)
- Helm chart needs `LLAMAEXTRACT_API_KEY` secret
- GCS credentials via service account JSON or workload identity
