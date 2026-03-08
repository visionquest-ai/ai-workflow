# Story 14.3: Docker & Deployment Readiness

Status: done

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

- [x] Task 1: Create .dockerignore (AC: 4, 9)
  - [x] 1.1 Create `.dockerignore` with: `.env`, `__pycache__`, `.git`, `.gitignore`, `_bmad*`, `tests/`, `the_edge_agent/docs/`, `the_edge_agent/rust/`, `the_edge_agent/.git`, `*.md` (root only)

- [x] Task 2: Optimize Dockerfile (AC: 1, 2, 3, 5, 8, 10)
  - [x] 2.1 Add non-root user (`appuser`)
  - [x] 2.2 Use multi-stage or layer ordering: apt deps → copy requirements.txt → pip install → copy app code (better layer caching)
  - [x] 2.3 Add `--timeout-keep-alive 600` to uvicorn CMD
  - [x] 2.4 Verify `libreoffice-writer` is installed (already present)
  - [x] 2.5 Pin Python base image tag for reproducibility

- [x] Task 3: Verify missing Python dependencies (AC: 3)
  - [x] 3.1 Check if `llama-cloud-services` or `llama-cloud` needs to be in requirements.txt (currently only in the_edge_agent's deps — may be transitive)
  - [x] 3.2 Verify `gcsfs` pulls in `google-auth` for GCS credentials

- [x] Task 4: Add env var documentation (AC: 7)
  - [x] 4.1 Add ENV declarations with defaults in Dockerfile (non-secret ones only)
  - [x] 4.2 Add comments listing all required env vars

- [x] Task 5: Build and smoke test (AC: 1, 6, 8)
  - [x] 5.1 `docker build -t ai-workflow .`
  - [ ] 5.2 `docker images ai-workflow` — verify size < 500MB (**NOT MET: 891MB — see Completion Notes**)
  - [x] 5.3 `docker run -e RUN_AGENT_API_KEY=test -p 8000:8000 ai-workflow`
  - [x] 5.4 `curl localhost:8000/health` — verify `{"status": "ok"}`
  - [x] 5.5 Verify `docker run ... whoami` shows non-root user

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

Claude Opus 4.6

### Debug Log References

None

### Completion Notes List

- Task 1: Created `.dockerignore` excluding `.env`, `__pycache__`, `.git`, `.gitignore`, `_bmad*`, `tests/`, `the_edge_agent/docs/`, `the_edge_agent/rust/`, `the_edge_agent/.git`, `*.md`
- Task 2: Optimized Dockerfile with non-root `appuser`, layer caching (deps before app code via `COPY --chown`), `--timeout-keep-alive 600`, libreoffice-writer confirmed present, `python:3.11-slim` tag pinned
- Task 3: Found `llama-cloud-services` is NOT a transitive dep of the_edge_agent — it's imported at runtime by `llamaextract_actions.py`. Added `llama-cloud-services>=0.6.0` to `requirements.txt`. Confirmed `gcsfs` transitively provides `google-auth`.
- Task 4: Added env var documentation block in Dockerfile header listing all 5 required + 1 optional env vars with descriptions
- Task 5: Docker build succeeds. Health check returns `{"status":"ok"}`. Container runs as `appuser` (non-root). `soffice` binary present.
- **AC8 NOT MET:** Image size is 891MB, exceeding 500MB target. Root cause: story estimates were incorrect. Actual layer sizes: libreoffice-writer=404MB (estimated 200MB), pip deps=336MB (estimated 80MB — llama-cloud-services pulls in llama-index-core, numpy, tiktoken, etc.). No further optimization possible without removing required dependencies. Recommend updating AC8 target to 1GB or investigating multi-stage build with alpine in a follow-up story.

### Code Review Fixes (AI)

- **[H1-FIXED]** Pinned base image from `python:3.11-slim` (floating) → `python:3.11.14-slim-trixie` (fully pinned). Strengthened `test_base_image_pinned` to validate version+distro format.
- **[H2-FIXED]** Unmarked Task 5.2 `[x]` → `[ ]` since AC8 (image < 500MB) is not met (891MB).
- **[M1-FIXED]** Added `.venv` to `.dockerignore` to prevent accidental inclusion of local virtual environments.
- **[M2-FIXED]** Removed `the-edge-agent` from `requirements.txt` — it's installed from local submodule in Dockerfile (`pip install ./the_edge_agent/python`). Having it in requirements.txt risked PyPI overwriting the local version. Added test `test_no_the_edge_agent_in_requirements`.
- **[M3-FIXED]** Added `HEALTHCHECK` instruction to Dockerfile using Python urllib (no curl needed in slim image). Added `test_healthcheck_instruction` test.
- **[M4-NOTED]** `the_edge_agent` submodule dirty ref — pre-existing from prior commits.

### Change Log

- 2026-03-08: Code review fixes — pinned base image, added HEALTHCHECK, removed duplicate the-edge-agent dep, added .venv to .dockerignore, fixed Task 5.2 checkmark.
- 2026-03-08: Implemented all tasks (1-5). AC1-7, AC9-10 satisfied. AC8 not achievable with required deps (891MB vs 500MB target).

### File List

- .dockerignore (new)
- Dockerfile (modified)
- requirements.txt (modified)
- tests/test_docker_deployment.py (new)
