# Story 14.3: Docker & Deployment Readiness

Status: ready-for-dev

## Story

As a DevOps engineer,
I want the ai-workflow Docker image to include all dependencies and be properly configured for production,
So that it can be deployed to cloud headless compute with reliable file extraction.

## Acceptance Criteria

1. **AC1 - Docker build succeeds:** `docker build .` completes without errors and produces a working image.

2. **AC2 - Image includes LibreOffice:** The image contains `soffice` binary for docx-to-PDF conversion via docx2pdf.

3. **AC3 - Python deps complete:** All runtime dependencies are installed: `fastapi`, `uvicorn`, `requests`, `fsspec`, `gcsfs`, `docx2pdf`, `openai`, `the-edge-agent` (local), `llama-cloud-services` (for LlamaExtract SDK).

4. **AC4 - .dockerignore exists:** A `.dockerignore` file excludes `.env`, `__pycache__`, `.git`, `_bmad*`, `tests/`, and `the_edge_agent/docs/` to keep image small.

5. **AC5 - Uvicorn timeout:** CMD includes `--timeout-keep-alive 600` for long-running sync requests (up to 300s LlamaExtract + overhead).

6. **AC6 - Health check works:** `GET /health` returns `{"status": "ok"}` when the container starts.

7. **AC7 - Environment variables documented:** A clear list of required env vars with descriptions exists in the Dockerfile or a deployment doc:
   - `RUN_AGENT_API_KEY` (required, fail-fast on startup)
   - `GRAPHOLOGY_URL` (required for GraphQL)
   - `GRAPHOLOGY_API_KEY` (required for GraphQL auth)
   - `LLAMAEXTRACT_API_KEY` (required for LlamaExtract)
   - `GOOGLE_APPLICATION_CREDENTIALS` (required for GCS access)
   - `AGENT_NAME_PREFIX` (optional, for namespaced LlamaExtract agents)

8. **AC8 - Image size reasonable:** Final image is under 500MB (python:3.11-slim base ~150MB + LibreOffice ~200MB + Python deps ~100MB).

9. **AC9 - No secrets in image:** `.env`, credentials, and service account files are NOT baked into the image.

10. **AC10 - Container runs as non-root:** The container process runs as a non-root user for security.

## Tasks / Subtasks

- [ ] Task 1: Create .dockerignore (AC: 4, 9)
  - [ ] 1.1 Create `.dockerignore` with: `.env`, `__pycache__`, `.git`, `.gitignore`, `_bmad*`, `tests/`, `the_edge_agent/docs/`, `the_edge_agent/rust/`, `the_edge_agent/.git`, `*.md` (root only)

- [ ] Task 2: Optimize Dockerfile (AC: 1, 2, 3, 5, 8, 10)
  - [ ] 2.1 Add non-root user (`appuser`)
  - [ ] 2.2 Use multi-stage or layer ordering: apt deps → copy requirements.txt → pip install → copy app code (better layer caching)
  - [ ] 2.3 Add `--timeout-keep-alive 600` to uvicorn CMD
  - [ ] 2.4 Verify `libreoffice-writer` is installed (already present)
  - [ ] 2.5 Pin Python base image tag for reproducibility

- [ ] Task 3: Verify missing Python dependencies (AC: 3)
  - [ ] 3.1 Check if `llama-cloud-services` or `llama-cloud` needs to be in requirements.txt (currently only in the_edge_agent's deps — may be transitive)
  - [ ] 3.2 Verify `gcsfs` pulls in `google-auth` for GCS credentials

- [ ] Task 4: Add env var documentation (AC: 7)
  - [ ] 4.1 Add ENV declarations with defaults in Dockerfile (non-secret ones only)
  - [ ] 4.2 Add comments listing all required env vars

- [ ] Task 5: Build and smoke test (AC: 1, 6, 8)
  - [ ] 5.1 `docker build -t ai-workflow .`
  - [ ] 5.2 `docker images ai-workflow` — verify size < 500MB
  - [ ] 5.3 `docker run -e RUN_AGENT_API_KEY=test -p 8000:8000 ai-workflow`
  - [ ] 5.4 `curl localhost:8000/health` — verify `{"status": "ok"}`
  - [ ] 5.5 Verify `docker run ... whoami` shows non-root user

## Dev Notes

### Current Dockerfile State

```dockerfile
FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends libreoffice-writer && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir ./the_edge_agent/python && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Issues:**
- No `.dockerignore` — copies `.git`, `_bmad*`, `tests/`, `docs/`, `rust/` into image
- No `--timeout-keep-alive` — uvicorn default is 5s, way too short for 300s extractions
- Runs as root
- `COPY . .` before pip install — breaks layer caching (any code change re-installs deps)
- No env var documentation

### Optimized Dockerfile Pattern

```dockerfile
FROM python:3.11-slim

# System deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends libreoffice-writer && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -s /bin/bash appuser

WORKDIR /app

# Install Python deps first (better layer caching)
COPY the_edge_agent/python/ ./the_edge_agent/python/
COPY requirements.txt .
RUN pip install --no-cache-dir ./the_edge_agent/python && \
    pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "600"]
```

### Image Size Estimate

| Layer | Size |
|-------|------|
| python:3.11-slim | ~150MB |
| libreoffice-writer | ~200MB |
| Python deps (fastapi, uvicorn, fsspec, gcsfs, etc.) | ~80MB |
| App code (without exclusions) | ~50MB |
| **Total without .dockerignore** | **~480MB** |
| **Total with .dockerignore** | **~350MB** |

The `the_edge_agent/rust/` dir and `.git` are the biggest offenders for bloat.

### Missing Infrastructure

Currently **no** Helm chart, CI/CD pipeline, or `docker-compose.yml` exists at the project root. This story focuses on making the Docker image production-ready. Helm/CI can be a follow-up.

### GCS Credentials

In production, prefer **Workload Identity** (GKE) or **service account key mounted as volume** rather than baking credentials into the image. The `GOOGLE_APPLICATION_CREDENTIALS` env var should point to a mounted path.

### Previous Story Intelligence

- Story 14.1 adds `--timeout-keep-alive 600` to the Dockerfile CMD — coordinate to avoid conflicts
- Story 14.2 confirms docx2pdf needs LibreOffice — already in Dockerfile
- Both stories are `ready-for-dev` but not yet implemented

### Project Structure Notes

- Dockerfile at project root — modify in place
- Create `.dockerignore` at project root (new file)
- No new Python files needed
- the_edge_agent is a git submodule — `.dockerignore` must exclude its `.git`, `rust/`, `docs/`

### References

- [Source: Dockerfile] — Current Dockerfile (17 lines)
- [Source: requirements.txt] — Current Python deps (8 packages)
- [Source: .gitignore] — Current gitignore (3 entries)
- [Source: app.py:33-40] — Lifespan check for RUN_AGENT_API_KEY
- [Source: app.py:177-180] — Health endpoint
- [Source: agents/file_extraction.yaml:27-30] — LlamaExtract settings (300s timeout)
- [Source: epic-14.md:97-124] — Story 14.3 acceptance criteria

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
