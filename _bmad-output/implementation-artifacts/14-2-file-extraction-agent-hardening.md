# Story 14.2: File Extraction Agent Hardening

Status: review

## Story

As a platform operator,
I want the file extraction YAML agent to be production-ready,
So that it handles edge cases, reports errors clearly, and cleans up resources.

## Acceptance Criteria

1. **AC1 - Happy path:** Given a supported directoryName (chambers, iflr1000, legal500, itr, leadersleague), the agent downloads from GCS, converts docx to PDF, extracts via LlamaExtract, and saves the payload to the node via GraphQL.

2. **AC2 - Temp file cleanup on success:** After successful extraction, the temp file in `/tmp/` is deleted. No orphaned files remain.

3. **AC3 - Temp file cleanup on failure:** If extraction fails, the temp file is still cleaned up before the agent exits.

4. **AC4 - Extraction failure persisted:** When LlamaExtract returns an error, the error is saved to the node payload as `{"error": "...", "status": "failed"}` so the caller and GraphQL both have the failure reason.

5. **AC5 - Unsupported directory error:** When `directoryName` is not in the supported map, the agent returns a clear error listing supported directories and does NOT attempt extraction.

6. **AC6 - Missing storageUrl error:** When the ApplicationFormFile has no `storageUrl`, the agent returns `"ApplicationFormFile has no storageUrl"` and exits cleanly.

7. **AC7 - Wrong node type error:** When `context_node_id` resolves to a node that is NOT `ApplicationFormFile`, the agent returns an error with the actual node type.

8. **AC8 - GCS download failure:** When GCS download fails (permissions, file not found, network), the error is captured with details and the agent exits without proceeding to extraction.

9. **AC9 - PDF conversion failure:** When docx2pdf/LibreOffice fails, the error is captured and the agent exits. The original docx temp file is cleaned up.

10. **AC10 - GraphQL save failure:** When `graphology.update_node` fails after successful extraction, the agent reports `status: error` with `"Failed to save payload"`.

11. **AC11 - Extraction mode from settings:** The LlamaExtract agent name uses the mode from `settings.llamaextract.mode` (currently `BALANCED`) rather than being hardcoded, so changing the setting propagates to the agent name.

12. **AC12 - Graphology server unavailable:** When the graphology server is down (503, timeout), the error from `graphology.get_node` is surfaced clearly, not masked as "no storageUrl".

## Tasks / Subtasks

- [x] Task 1: Fix temp file cleanup on failure paths (AC: 3, 9)
  - [x] 1.1 In `extract_and_download`, if PDF conversion fails, clean up the original docx temp file before returning error
  - [x] 1.2 Add cleanup in `prepare_payload` for cases where extraction fails (already partially done — verify coverage)

- [x] Task 2: Derive extraction mode from settings (AC: 11)
  - [x] 2.1 In `resolve_agent`, read extraction mode from `settings.llamaextract.mode` instead of hardcoding `"balanced"`
  - [x] 2.2 Convert mode to lowercase for agent name (settings uses uppercase `BALANCED`, agent name needs lowercase `balanced`)

- [x] Task 3: Improve graphology error surfacing (AC: 12)
  - [x] 3.1 In `extract_and_download`, check if `context_result` itself indicates failure (e.g., `success: false`) before accessing `data`
  - [x] 3.2 Surface the graphology error message instead of generic "no storageUrl"

- [x] Task 4: Verify all error paths save to node (AC: 4, 10)
  - [x] 4.1 Verify that `prepare_payload` saves error payloads to the node (currently it does for extraction failures)
  - [x] 4.2 Verify early exits (missing storageUrl, wrong node type, unsupported directory) still set `completed: True` and skip save_payload

- [x] Task 5: Add integration test for the YAML agent (AC: 1-12)
  - [x] 5.1 Create `tests/test_file_extraction.py` with mocked graphology and LlamaExtract
  - [x] 5.2 Test happy path: mock GCS download, mock LlamaExtract success, verify payload saved
  - [x] 5.3 Test error paths: missing storageUrl, wrong node type, unsupported directory, extraction failure
  - [x] 5.4 Test temp file cleanup after success and failure

## Dev Notes

### Current State of the YAML Agent

The agent at `agents/file_extraction.yaml` is functional end-to-end (tested with real Chambers docx → PDF → LlamaExtract → GraphQL). Key findings from testing:

**Working:**
- GCS download via fsspec
- docx2pdf conversion (wraps LibreOffice)
- LlamaExtract SDK agent path with `rankellix-{base_name}-balanced`
- GraphQL payload save via `graphology.update_node`
- Error edges: `extract_and_download → __end__` and `resolve_agent → __end__` for early exits

**Known Issues to Fix:**

1. **Hardcoded extraction mode**: `resolve_agent` step hardcodes `extraction_mode = "balanced"` at line 156. Should derive from `settings.llamaextract.mode.lower()`. Currently the settings say `BALANCED` but the agent name needs lowercase.

2. **Temp file leak on PDF conversion failure**: If `docx2pdf.convert()` fails, the original docx temp file at `local_path` is not cleaned up. The error return skips cleanup.

3. **Graphology failure masked**: If `graphology.get_node` fails (server down, 503), `context_result` may have `success: false` with an error, but `extract_and_download` just reads `data.storageUrl` which will be empty, returning "no storageUrl" instead of the real error.

4. **Early exit paths skip save_payload**: When `extract_and_download` or `resolve_agent` exit early (via `__end__` edges), the error is NOT saved to the node payload. The node stays unchanged, and only the HTTP response carries the error. This may be acceptable — but should be documented as intentional.

### LlamaExtract Findings from Testing

- **docx direct**: LlamaExtract has a bug parsing docx files — `parse_job_id=None`, 0 pages extracted, status ERROR with "An unexpected error occurred". Confirmed via `llama_cloud.client.LlamaCloud.llama_extract.get_job()`.
- **PDF via conversion**: Works reliably. Chambers extraction in ~80s with BALANCED mode.
- **Job queue**: Was temporarily stuck (all jobs PENDING for 33+ min). Resolved on LlamaExtract side.
- **Available agents**: All 20 Rankellix agents confirmed (5 directories x 4 modes each).

### Edge Flow Analysis

```
__start__ → fetch_file_node → extract_and_download
  ├── (has storage_url) → resolve_agent
  │     ├── (has agent_name) → run_extraction → prepare_payload → save_payload → finalize → __end__
  │     └── (no agent_name) → __end__  [error: unsupported directory]
  └── (no storage_url) → __end__  [error: no storageUrl / wrong type / no directory]
```

Early exits go directly to `__end__` without saving to the node. Only the `prepare_payload → save_payload` path persists results.

### Previous Story Intelligence

Story 14.1 (Async Agent Endpoint) is `ready-for-dev` but not yet implemented. This story (14.2) is independent — no code dependency on 14.1.

### Project Structure Notes

- YAML agent: `agents/file_extraction.yaml` — all changes here
- Actions: `actions/graphology.py` — no changes needed (update_node works)
- LlamaExtract actions: built-in to TEA engine, no changes needed
- Tests: create new `tests/test_file_extraction.py`

### References

- [Source: agents/file_extraction.yaml] — Complete YAML agent
- [Source: agents/file_extraction.yaml:131-141] — PDF conversion via docx2pdf
- [Source: agents/file_extraction.yaml:149-194] — resolve_agent with hardcoded mode
- [Source: agents/file_extraction.yaml:208-225] — prepare_payload with cleanup
- [Source: agents/file_extraction.yaml:247-271] — Edge definitions with early exits
- [Source: actions/graphology.py:899-918] — Action registration
- [Source: app.py:142-164] — Agent state extraction (status, error, payload)
- [Source: epic-14.md:65-95] — Story 14.2 acceptance criteria

## Dev Agent Record

### Agent Model Used
Claude Opus 4.6

### Debug Log References
N/A — no debugging issues encountered.

### Completion Notes List
- **Task 1.1:** Added `os.unlink(local_path)` cleanup in `extract_and_download` before returning PDF conversion error. Prevents orphaned docx temp files.
- **Task 1.2:** Verified `prepare_payload` already cleans up temp files before the success/failure branch. No changes needed.
- **Task 2.1/2.2:** Changed `resolve_agent` from hardcoded `extraction_mode = "balanced"` to `settings.get("llamaextract", {}).get("mode", "BALANCED")).lower()`. Mode now derived from settings and lowercased.
- **Task 3.1/3.2:** Added graphology failure check at top of `extract_and_download`. When `context_result.success` is false, the actual graphology error (e.g., "Service unavailable (503)") is surfaced instead of generic "no storageUrl".
- **Task 4.1:** Verified `prepare_payload` serializes error payloads with `{"error": "...", "status": "failed"}` and flows through `save_payload` to persist.
- **Task 4.2:** Verified early exits (missing storageUrl, wrong node type, unsupported directory) set `completed: True` and route to `__end__` via edges, skipping `save_payload`. Documented as intentional — errors returned in HTTP response only.
- **Task 5:** Created `tests/test_file_extraction.py` with 26 tests covering all 12 ACs. Tests execute YAML agent node run blocks directly with mocked dependencies (fsspec, docx2pdf). All pass.

### Change Log
- 2026-03-08: Implemented story 14.2 — all 5 tasks complete, 26 new tests added, 0 regressions.

### File List
- agents/file_extraction.yaml (modified — Tasks 1, 2, 3)
- tests/test_file_extraction.py (new — Task 5)
