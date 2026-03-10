# Story 16.1: TIER 1+2 — Filename & Content Pattern Detection for Directory and Year

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want the file_extraction agent to automatically detect the directory and year from the filename and file content when they are not pre-set on the node,
So that most files (~80%) are classified and routed to the correct LlamaExtract agent without LLM calls.

## Acceptance Criteria

1. **AC1 - Fast path for pre-set directoryName:** Given an ApplicationFormFile node with `directoryName` already populated, when the agent executes `detect_directory`, then `directory_name` is set to the pre-existing value, `directory_source` is set to `"input"`, and TIER 1/2/3 detection is skipped entirely.

2. **AC2 - TIER 1 filename directory detection:** Given an ApplicationFormFile with empty `directoryName` and `fileName` = `"chambers_2025.docx"`, when TIER 1 executes, then `directory_name` = `"chambers"`, `directory_source` = `"filename"`.

3. **AC3 - TIER 1 filename year detection:** Given `fileName` = `"chambers_2025.docx"`, when TIER 1 executes, then `detected_year` = `2025`, `year_source` = `"filename"`.

4. **AC4 - TIER 1 miss falls through to TIER 2:** Given `fileName` = `"submission_form.docx"` (no directory keyword), when TIER 1 returns no match, then the agent proceeds to TIER 2 content scanning.

5. **AC5 - TIER 2 content directory detection (Chambers):** Given the downloaded file contains `"myaccount.chambers.com"` or `"Band 1"` within first 3500 chars, when TIER 2 executes, then `directory_name` = `"chambers"`, `directory_source` = `"content"`.

6. **AC6 - TIER 2 content directory detection (IFLR1000):** Given the file contains `"iflr1000.com"` or `"Market Leader"` within first 3500 chars, when TIER 2 executes, then `directory_name` = `"iflr1000"`, `directory_source` = `"content"`.

7. **AC7 - TIER 2 content year detection:** Given year not detected from filename and file content contains a 4-digit year pattern (e.g., `"2025 submission"`) within first 3500 chars, when TIER 2 year detection executes, then `detected_year` is extracted, `year_source` = `"content"`.

8. **AC8 - All tiers fail gracefully:** Given TIER 1 and TIER 2 both fail to detect a directory, when detection completes, then the agent errors with: `"Could not detect directory. Supported: chambers, iflr1000, legal500, itr, leadersleague"`.

9. **AC9 - DOCX text extraction:** Given a `.docx` file, when TIER 2 extracts text, then it uses `python-docx` to extract paragraph and table text, limited to first 3500 chars.

10. **AC10 - PDF text extraction:** Given a `.pdf` file, when TIER 2 extracts text, then it uses `pdfplumber` to extract page text, limited to first 3500 chars.

11. **AC11 - Fast path for pre-set year:** Given an ApplicationFormFile with `year` already populated on the node, when year detection runs, then `detected_year` = pre-existing value, `year_source` = `"input"`, and year detection tiers are skipped.

12. **AC12 - Directory slug resolves to LlamaExtract agent:** Given a detected `directory_name` (from any tier), when `resolve_agent` runs, then it resolves via the existing `base_name_map` to the correct LlamaExtract agent name (e.g., `chambers` → `rankellix-chambers-partners-balanced`).

## Tasks / Subtasks

- [x] Task 1: Add new state schema fields to `file_extraction.yaml` (AC: all)
  - [x] 1.1 Add `directory_source` (str) — tracks how directory was detected: "input", "filename", "content", "llm"
  - [x] 1.2 Add `document_text` (str) — first 3500 chars extracted from file for content scanning
  - [x] 1.3 Add `detected_year` (str) — year detected from any tier
  - [x] 1.4 Add `year_source` (str) — tracks how year was detected: "input", "filename", "content", "llm"

- [x] Task 2: Modify `extract_and_download` node to extract text and remove hard error on missing directoryName (AC: 1, 4, 9, 10)
  - [x] 2.1 Remove the hard error `"ApplicationFormFile has no directoryName"` — detection replaces it
  - [x] 2.2 After file download + PDF conversion, extract text from the local file (first 3500 chars)
  - [x] 2.3 For PDF files: use `pdfplumber` to extract page text
  - [x] 2.4 For DOCX files: use `python-docx` to extract paragraph and table text (note: this runs BEFORE PDF conversion for text extraction, then PDF conversion still happens for LlamaExtract)
  - [x] 2.5 Store extracted text in `document_text` state field
  - [x] 2.6 Still pass `directory_name` from node data if present (fast path preserved)
  - [x] 2.7 If `directoryName` is empty, set `directory_name` to empty string (not error) so flow continues to `detect_directory`

- [x] Task 3: Add new `detect_directory` node between `extract_and_download` and `resolve_agent` (AC: 1-8, 11)
  - [x] 3.1 Create `detect_directory` node with `run:` block containing the full three-tier logic
  - [x] 3.2 **Fast path check:** If `directory_name` already set (from node data), set `directory_source = "input"` and skip detection
  - [x] 3.3 **Fast path check for year:** If node data has `year` field populated, set `detected_year` from it and `year_source = "input"`
  - [x] 3.4 **TIER 1 — Filename detection:** Match `fileName` (case-insensitive) against keyword patterns:
    - `chambers`: ['chambers', 'c&p', 'candp']
    - `iflr1000`: ['iflr', 'iflr1000']
    - `legal500`: ['legal500', 'legal 500', 'l500']
    - `itr`: ['itr', 'international tax review']
    - `leadersleague`: ['leaders', 'league', 'leadersleague', 'leaders league']
  - [x] 3.5 **TIER 1 — Year from filename:** Extract 4-digit year (2000-2099) from `fileName` using regex
  - [x] 3.6 **TIER 2 — Content keyword scanning:** If TIER 1 didn't detect directory, scan `document_text` against 52+ content patterns (see Dev Notes for full pattern map)
  - [x] 3.7 **TIER 2 — Year from content:** If year not yet detected, extract 4-digit year from `document_text`
  - [x] 3.8 **Error on all-fail:** If directory still empty after TIER 1+2, return error with supported directories list
  - [x] 3.9 Update `goto` routing: `detect_directory` → `resolve_agent` (on success) or `__end__` (on error)

- [x] Task 4: Update flow wiring in `file_extraction.yaml` (AC: all)
  - [x] 4.1 Change `extract_and_download` goto: route to `detect_directory` instead of `resolve_agent`
  - [x] 4.2 Add `detect_directory` node with goto: `resolve_agent` (success) or `__end__` (error)
  - [x] 4.3 Existing `resolve_agent` → `run_extraction` → `prepare_payload` → `save_payload` → `finalize` flow unchanged

- [x] Task 5: Add `python-docx` and `pdfplumber` to `requirements.txt` (AC: 9, 10)
  - [x] 5.1 Add `python-docx>=1.0.0`
  - [x] 5.2 Add `pdfplumber>=0.10.0`

- [x] Task 6: Add tests for directory and year detection (AC: 1-12)
  - [x] 6.1 Test fast path: pre-set directoryName → directory_source="input", no detection
  - [x] 6.2 Test fast path: pre-set year → year_source="input", no year detection
  - [x] 6.3 Test TIER 1: filename "chambers_2025.docx" → directory="chambers", year=2025
  - [x] 6.4 Test TIER 1: filename "iflr_submission.pdf" → directory="iflr1000"
  - [x] 6.5 Test TIER 1: filename with no keywords → falls through to TIER 2
  - [x] 6.6 Test TIER 2: content with "myaccount.chambers.com" → directory="chambers"
  - [x] 6.7 Test TIER 2: content with "iflr1000.com" → directory="iflr1000"
  - [x] 6.8 Test TIER 2: content with "legal500.com" → directory="legal500"
  - [x] 6.9 Test TIER 2: content with year pattern → year detected
  - [x] 6.10 Test all-tiers-fail: no match in filename or content → graceful error
  - [x] 6.11 Test text extraction from PDF via pdfplumber (mocked)
  - [x] 6.12 Test text extraction from DOCX via python-docx (mocked)
  - [x] 6.13 Test existing `extract_and_download` tests still pass (no directoryName error regression)
  - [x] 6.14 Test `resolve_agent` still works with detected directory_name (no changes to that node)

## Dev Notes

### Critical Design Decision: Text Extraction Timing

The existing `extract_and_download` node already downloads and converts files. Text extraction for TIER 2 MUST happen here (after download, using the local file) because:
1. The file is already downloaded and available as `local_path`
2. For DOCX files: extract text BEFORE PDF conversion (python-docx reads .docx, not .pdf)
3. For PDF files: extract text from the PDF directly
4. The extracted `document_text` is needed by the next node (`detect_directory`)

**Implementation approach:** Add text extraction at the end of `extract_and_download`, after successful download/conversion but BEFORE returning. For DOCX: extract text first, THEN convert to PDF. For PDF: extract text from the PDF file.

### TIER 1 Filename Keyword Patterns

```python
DIRECTORY_FILENAME_PATTERNS = {
    "chambers": ["chambers", "c&p", "candp"],
    "iflr1000": ["iflr", "iflr1000"],
    "legal500": ["legal500", "legal 500", "l500"],
    "itr": ["itr", "international tax review"],
    "leadersleague": ["leaders", "league", "leadersleague", "leaders league"],
}
```

Match is case-insensitive against `fileName.lower()`. First match wins.

**IMPORTANT for TIER 1 "itr" and "leaders"/"league" keywords:** These are short/common words. The filename detection should match as substrings but be aware of false positives in TIER 2 content scanning. TIER 1 filename patterns are acceptable because filenames are user-provided naming conventions. The `itr` keyword in a filename is a strong signal.

### TIER 2 Content Keyword Patterns (52+ patterns from spa-base)

```python
DIRECTORY_CONTENT_PATTERNS = {
    "chambers": [
        "chambers.com", "myaccount.chambers.com", "chambers and partners",
        "chambers & partners", "band 1", "band 2", "band 3", "band 4",
        "ranked individual", "leading individual", "recognised practitioner",
        "associate to watch", "up and coming", "star individual",
        "PAB006", "PAM006", "pab006", "pam006",
        "chambers global", "chambers latin america", "chambers asia",
        "chambers europe", "chambers usa",
    ],
    "iflr1000": [
        "iflr1000.com", "iflr.com", "accreditation.euromoney.com",
        "iflr1000", "market leader", "highly regarded",
        "notable practitioner", "rising star",
    ],
    "legal500": [
        "legal500.com", "thelegal500.com", "the legal 500",
        "legal 500", "hall of fame", "leading individual",
        "next generation partner", "rising star",
        "recommended lawyer", "tier 1", "tier 2", "tier 3",
    ],
    "itr": [
        "itrworldtax.com", "itr world tax", "international tax review",
        "world tax", "world transfer pricing",
        "tax controversy leaders", "indirect tax leaders",
        "women in tax leaders",
    ],
    "leadersleague": [
        "leadersleague.com", "leaders league", "décideurs",
        "decideurs", "classement", "peer feedback",
        "highly recommended", "leading", "excellent",
        "strong reputation",
    ],
}
```

Match is case-insensitive. Scan `document_text` (first 3500 chars) for any keyword match. First directory with a match wins.

**IMPORTANT:** Some keywords overlap (e.g., "leading individual" appears in both chambers and legal500, "rising star" in iflr1000 and legal500). The pattern matching should prioritize more specific patterns first (URLs, unique terms) before generic ones. If a URL pattern matches, that's definitive. For ambiguous generic terms, the first directory in iteration order wins — this is acceptable because TIER 2 hits ~80% of cases and the remaining edge cases fall to TIER 3 LLM (Story 16.2).

### Year Detection Logic

```python
import re

def detect_year_from_text(text):
    """Extract 4-digit year (2000-2099) from text. Returns latest year found or None."""
    years = re.findall(r'\b(20[0-9]{2})\b', text)
    return max(years) if years else None
```

For filename: extract from `fileName` string.
For content: extract from `document_text` (first 3500 chars).
Use `max()` to get the latest year (most likely to be the submission year).

### Flow After Changes

```
__start__ → fetch_file_node → extract_and_download
  ├── (has storage_url) → detect_directory
  │     ├── (has directory_name) → resolve_agent → run_extraction → prepare_payload → save_payload → finalize → __end__
  │     └── (no directory_name after detection) → __end__  [error: could not detect directory]
  └── (no storage_url) → __end__  [error: no storageUrl / wrong type]
```

### Existing Node Changes Summary

**`extract_and_download` node — MODIFY:**
- REMOVE: Hard error on missing `directoryName` (lines 102-103 in current YAML)
- ADD: Text extraction block after download/conversion
- ADD: Return `document_text` and `file_name` in output
- CHANGE: `goto` routes to `detect_directory` instead of `resolve_agent`
- KEEP: All other logic (GCS download, PDF conversion, error handling) unchanged

**`resolve_agent` node — NO CHANGES:**
- Still reads `state.directory_name` and resolves to LlamaExtract agent
- The `directory_name` will now come from `detect_directory` instead of `extract_and_download`

### Existing Test Impact

The test `test_missing_directory_name_returns_error` in `tests/test_file_extraction.py` will need to be **removed or updated** — it tests the exact behavior being removed (hard error on missing directoryName). Replace it with a test verifying that missing directoryName allows the flow to continue to `detect_directory`.

### Dependencies

- `python-docx>=1.0.0` — DOCX text extraction (paragraphs + tables)
- `pdfplumber>=0.10.0` — PDF text extraction (page text)
- Both are well-maintained, stable libraries with no known security issues

### Project Structure Notes

- Agent YAML: `agents/file_extraction.yaml` — primary file to modify
- Tests: `tests/test_file_extraction.py` — extend with new test classes
- Dependencies: `requirements.txt` — add python-docx and pdfplumber
- No new files created (detect_directory is a new node inside existing YAML)
- No changes to `app.py`, `actions/graphology.py`, or any other file

### References

- [Source: agents/file_extraction.yaml] — Complete current YAML agent (all modifications here)
- [Source: agents/file_extraction.yaml:78-167] — `extract_and_download` node to modify
- [Source: agents/file_extraction.yaml:170-218] — `resolve_agent` node (no changes, uses `state.directory_name`)
- [Source: agents/file_extraction.yaml:44-60] — Current `state_schema` to extend
- [Source: tests/test_file_extraction.py:159-171] — Test to update (missing directoryName)
- [Source: _bmad-output/planning-artifacts/epics.md:396-466] — Story 16.1 requirements and FR coverage
- [Source: _bmad-output/implementation-artifacts/14-2-file-extraction-agent-hardening.md] — Previous story intelligence
- [Source: _bmad-output/project-context.md] — Project conventions

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- Year regex `\b(20[0-9]{2})\b` failed on filenames like `chambers_2025.docx` because `_` is a word character. Fixed with `(?<!\d)(20[0-9]{2})(?!\d)` lookaround pattern.
- 3 pre-existing test failures confirmed (test_mode_from_settings_lowercase, test_mode_from_settings_accurate, test_pdf_conversion_failure_cleans_up_docx) — all fail on main before this story's changes.

### Completion Notes List

- Task 1: Added 4 new state schema fields (directory_source, document_text, detected_year, year_source) to file_extraction.yaml
- Task 2: Removed hard error on missing directoryName. Added text extraction (pdfplumber for PDF, python-docx for DOCX) before PDF conversion. Now returns document_text, file_name, year in output.
- Task 3: Created `detect_directory` node with full TIER 1 (filename keywords) + TIER 2 (content patterns) detection for both directory and year. Fast path for pre-set values. Graceful error on all-fail.
- Task 4: Wired extract_and_download → detect_directory → resolve_agent. Error path goes to __end__.
- Task 5: Added python-docx>=1.0.0 and pdfplumber>=0.10.0 to requirements.txt.
- Task 6: Added 20 new tests across 4 test classes (TestDetectDirectoryFastPath, TestDetectDirectoryTier1, TestDetectDirectoryTier2, TestDetectDirectoryTextExtraction). Updated existing test_missing_directory_name to verify flow-through instead of error. All 44 tests pass (3 pre-existing failures unchanged).

### Change Log

- 2026-03-10: Implemented Story 16.1 — TIER 1+2 filename & content pattern detection for directory and year

### File List

- agents/file_extraction.yaml (modified — state_schema, extract_and_download node, new detect_directory node, flow wiring)
- tests/test_file_extraction.py (modified — updated 1 existing test, added 20 new tests)
- requirements.txt (modified — added python-docx, pdfplumber)
- _bmad-output/implementation-artifacts/sprint-status.yaml (modified — status updates)
- _bmad-output/implementation-artifacts/16-1-tier-1-2-filename-content-pattern-detection.md (modified — task checkboxes, dev record)
