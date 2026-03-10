# Story 16.2: TIER 3 — LLM Classification Agent

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want a fallback LLM classification agent that identifies the directory and year when filename and content patterns fail,
So that 100% of files can be classified regardless of naming or content patterns.

## Acceptance Criteria

1. **AC1 - TIER 3 invocation when TIER 1+2 fail:** Given TIER 1 and TIER 2 both failed to detect `directory_name`, when the file_extraction agent reaches the TIER 3 step, then it invokes `agents/file_classification.yaml` with `document_text` (first 3500 chars) and `detected_directory: null`.

2. **AC2 - LLM classifies Chambers submission:** Given the LLM classification agent receives document text from a Chambers submission, when it analyzes the text, then it returns JSON with `directory: "chambers"`, `category` (e.g., "Corporate/M&A"), `region_country` (ISO 3166-1 alpha-3), `region_state` (ISO 3166-2), `confidence` (0.0-1.0), `year`, `is_empty_form` (boolean), and `directory_source` is set to `"llm"`.

3. **AC3 - LLM prompt covers all 5 directories:** Given the LLM classification agent receives document text, when it processes the prompt, then the system prompt includes specific indicators for all 5 directories: Chambers (Band rankings, PAB006/PAM006 refs), IFLR1000 (Market Leader, accreditation.euromoney.com), Legal500 (Tier rankings, Hall of Fame), ITR (World Tax, itrworldtax.com), Leaders League (Décideurs, peer feedback).

4. **AC4 - Low confidence handling:** Given the LLM cannot identify the directory with confidence, when it returns confidence < 0.5 or `directory: null`, then the file_extraction agent errors gracefully with `"LLM classification inconclusive. Supported: chambers, iflr1000, legal500, itr, leadersleague"` and sets `status: "error"`, `completed: true`.

5. **AC5 - ISO country/state resolution:** Given the LLM returns ISO country/state codes (e.g., `"BRA"`, `"BR-SP"`), when the classification result is parsed, then `region_country_display` resolves to the country name (e.g., `"Brazil"`) and `region_state_display` resolves to the state name (e.g., `"São Paulo"`).

6. **AC6 - Known directory mode (year-only fallback):** Given TIER 1 or TIER 2 successfully detected the directory but year was not detected, when the agent reaches TIER 3, then it invokes the LLM classification agent with `detected_directory` pre-set, and the LLM uses the "known directory" prompt branch (focused on category/region/year extraction only).

7. **AC7 - Content hash caching:** Given the LLM classification result is cached by content hash, when the same `document_text` is processed again, then the cached result is returned without a new LLM call, with cache TTL of 30 days.

8. **AC8 - Classification result stored in state:** Given the LLM returns a valid classification, when the result is parsed, then `classification_result` state field contains the full JSON response (directory, category, region_country, region_state, confidence, year, is_empty_form) and `directory_name` is set from the result.

9. **AC9 - LLM configuration:** The file_classification agent uses temperature 0.1 (deterministic classification), max_tokens 200, and a configurable model (default matches project LLM provider).

## Tasks / Subtasks

- [x] Task 1: Create `agents/file_classification.yaml` — TIER 3 LLM classification agent (AC: 2, 3, 5, 9)
  - [x] 1.1 Define state_schema with inputs: `document_text` (str), `detected_directory` (str, nullable), and outputs: `directory` (str), `category` (str), `region_country` (str), `region_state` (str), `region_country_display` (str), `region_state_display` (str), `confidence` (float), `year` (str), `is_empty_form` (bool), `status` (str), `error` (str)
  - [x] 1.2 Create `classify_document` LLM node with two prompt branches:
    - **Unknown directory prompt:** Full classification covering all 5 directories with specific indicators
    - **Known directory prompt:** Category/region/year extraction only (when `detected_directory` is pre-set)
  - [x] 1.3 Set LLM parameters: temperature=0.1, max_tokens=200
  - [x] 1.4 Create `parse_result` node to extract JSON from LLM response and resolve ISO country/state codes to display names
  - [x] 1.5 Create `validate_result` node: check confidence >= 0.5, check directory is in supported list, set error if invalid
  - [x] 1.6 Wire flow: __start__ → check_cache → classify_document → parse_result → validate_result → store_cache → __end__

- [x] Task 2: Add content hash caching for LLM classification (AC: 7)
  - [x] 2.1 Create cache action or inline caching in the classification flow
  - [x] 2.2 Cache key: `classify:file:{{ sha256(document_text) }}`
  - [x] 2.3 Cache TTL: 30 days (2,592,000 seconds)
  - [x] 2.4 Cache check node before LLM call: if cached, skip LLM and use cached result
  - [x] 2.5 Cache store node after LLM call: persist result with hash key

- [x] Task 3: Modify `agents/file_extraction.yaml` to invoke TIER 3 conditionally (AC: 1, 6, 8)
  - [x] 3.1 Add `classification_result` field to state_schema
  - [x] 3.2 Modify `detect_directory` node (from Story 16.1): instead of erroring when TIER 1+2 fail, set a flag and route to TIER 3
  - [x] 3.3 Add `invoke_classification` node that loads and runs `file_classification.yaml` as a sub-agent
  - [x] 3.4 Add `process_classification` node that extracts `directory_name`, `detected_year`, and `classification_result` from sub-agent output
  - [x] 3.5 Update goto routing: `detect_directory` → `invoke_classification` (when TIER 1+2 fail OR year missing) → `process_classification` → `resolve_agent`
  - [x] 3.6 Handle known-directory mode: pass `detected_directory` to sub-agent when directory was found but year wasn't

- [x] Task 4: Add tests for TIER 3 LLM classification (AC: 1-9)
  - [x] 4.1 Test `file_classification.yaml` standalone: Chambers text → correct classification
  - [x] 4.2 Test `file_classification.yaml` standalone: IFLR1000 text → correct classification
  - [x] 4.3 Test `file_classification.yaml` standalone: unknown text → low confidence error
  - [x] 4.4 Test known-directory mode: pre-set directory → category/region/year extraction
  - [x] 4.5 Test ISO resolution: "BRA" → "Brazil", "BR-SP" → "São Paulo"
  - [x] 4.6 Test caching: same text → cached result returned, no second LLM call
  - [x] 4.7 Test integration: file_extraction with TIER 1+2 failing → TIER 3 invoked → directory resolved
  - [x] 4.8 Test integration: TIER 1 detects directory, year missing → TIER 3 invoked for year only
  - [x] 4.9 Test confidence threshold: confidence < 0.5 → graceful error

## Dev Notes

### Critical Architecture: Sub-Agent Invocation Pattern

**There is NO existing sub-agent invocation pattern in this codebase.** Story 16.2 is the first agent-calls-agent scenario. The recommended approach:

1. **Create a custom action `agents.invoke_agent`** registered in a new `actions/agents.py` file (following the `actions/graphology.py` pattern)
2. The action loads a YAML agent via `YAMLEngine`, invokes it with input state, and returns the final state
3. This action is then used in `file_extraction.yaml` via `uses: agents.invoke_agent` with input/output mapping
4. **Reference:** `app.py:18-45` shows how `YAMLEngine` loads and invokes agents — replicate this pattern inside the custom action

**Alternative (simpler but less reusable):** Implement TIER 3 as inline `run:` Python code within `file_extraction.yaml` that directly calls the LLM via `llm.call` action. This avoids the sub-agent complexity but duplicates LLM invocation logic. **Recommended: custom action approach** for reusability.

### Critical: LLM Classification Prompt Design

The classification prompt MUST include specific, unambiguous indicators for each directory. From the spa-base reference implementation:

**Unknown directory prompt (full classification):**
```
You are a legal document classifier. Analyze the following document text and classify it.

SUPPORTED DIRECTORIES:
1. chambers (Chambers & Partners) — Indicators: Band rankings (Band 1-4), PAB006/PAM006 reference codes, "Chambers Global/Latin America/Asia/Europe/USA", myaccount.chambers.com
2. iflr1000 (IFLR1000) — Indicators: "Market Leader", "Highly Regarded", "Notable Practitioner", "Rising Star", accreditation.euromoney.com, iflr1000.com
3. legal500 (The Legal 500) — Indicators: Tier rankings (Tier 1-3), "Hall of Fame", "Next Generation Partner", thelegal500.com, legal500.com
4. itr (International Tax Review) — Indicators: "World Tax", "World Transfer Pricing", "Tax Controversy Leaders", itrworldtax.com
5. leadersleague (Leaders League) — Indicators: "Décideurs", "Classement", "Peer Feedback", leadersleague.com

Return JSON: {"directory": "<slug>", "category": "<practice area>", "region_country": "<ISO 3166-1 alpha-3>", "region_state": "<ISO 3166-2>", "confidence": <0.0-1.0>, "year": <int or null>, "is_empty_form": <bool>}
```

**Known directory prompt (category/region/year only):**
```
The document is from the {{detected_directory}} legal directory. Extract: category (practice area), region (country ISO 3166-1 alpha-3, state ISO 3166-2), year, and whether it's an empty form.

Return JSON: {"category": "<practice area>", "region_country": "<ISO 3166-1 alpha-3>", "region_state": "<ISO 3166-2>", "confidence": <0.0-1.0>, "year": <int or null>, "is_empty_form": <bool>}
```

### Critical: ISO Code Resolution

The LLM returns ISO codes. The `parse_result` node must resolve them to display names:
- `region_country`: ISO 3166-1 alpha-3 (e.g., "BRA") → display name (e.g., "Brazil")
- `region_state`: ISO 3166-2 (e.g., "BR-SP") → display name (e.g., "São Paulo")

**Implementation:** Use `pycountry` library (already common in Python) OR a simple hardcoded lookup dict for the ~30 countries relevant to legal directories. The `pycountry` approach is more maintainable.
- Check if `pycountry` is in `requirements.txt` — if not, add it
- Fallback: if ISO code not found, set display name to the raw code (never error on unknown country)

### Critical: Caching Implementation

Follow the existing caching pattern from `actions/graphology.py`:

```python
import hashlib
import time

_classification_cache: Dict[str, Any] = {}
_classification_cache_ts: Dict[str, float] = {}
CLASSIFICATION_CACHE_TTL = 2_592_000  # 30 days in seconds

def _cache_key(document_text: str) -> str:
    return f"classify:file:{hashlib.sha256(document_text.encode()).hexdigest()}"
```

**Note:** Module-level dict cache is acceptable for single-process deployment. If multi-process (gunicorn workers), cache won't be shared — this is acceptable per NFR-DD2 (avoid redundant calls, not eliminate them across processes).

### Critical: Confidence Threshold

- If `confidence < 0.5` OR `directory` is null/empty → error gracefully
- Error message: `"LLM classification inconclusive. Supported: chambers, iflr1000, legal500, itr, leadersleague"`
- Set `status: "error"` and `completed: true`
- The `directory_name` must be in the supported set: `["chambers", "iflr1000", "legal500", "itr", "leadersleague"]`

### Flow After Story 16.2 Changes

```
__start__ → fetch_file_node → extract_and_download
  ├── (has storage_url) → detect_directory
  │     ├── (has directory_name from TIER 1/2) → resolve_agent → ...
  │     ├── (no directory_name after TIER 1+2) → invoke_classification
  │     │     ├── (confidence >= 0.5) → process_classification → resolve_agent → ...
  │     │     └── (confidence < 0.5 or error) → __end__  [error: inconclusive]
  │     └── (directory found but year missing) → invoke_classification (known-directory mode) → process_classification → resolve_agent → ...
  └── (no storage_url) → __end__  [error: no storageUrl / wrong type]
```

### State Schema Additions (file_extraction.yaml)

Story 16.1 already adds: `directory_source`, `document_text`, `detected_year`, `year_source`

Story 16.2 adds:
- `classification_result` (str) — Full JSON from LLM classification agent
- No other new fields needed — directory_name, directory_source, detected_year, year_source are reused

### State Schema for file_classification.yaml (new agent)

```yaml
state_schema:
  document_text:
    type: str
    description: "First 3500 chars of document for classification"
  detected_directory:
    type: str
    description: "Pre-detected directory from TIER 1/2, or null"
  directory:
    type: str
    description: "Classified directory slug"
  category:
    type: str
    description: "Practice area category"
  region_country:
    type: str
    description: "ISO 3166-1 alpha-3 country code"
  region_state:
    type: str
    description: "ISO 3166-2 state code"
  region_country_display:
    type: str
    description: "Country display name"
  region_state_display:
    type: str
    description: "State display name"
  confidence:
    type: float
    description: "Classification confidence 0.0-1.0"
  year:
    type: str
    description: "Detected year"
  is_empty_form:
    type: bool
    description: "Whether document is an empty/blank form"
  status:
    type: str
    description: "success or error"
  error:
    type: str
    description: "Error message if classification failed"
```

### TEA YAML Patterns to Follow

- **LLM node pattern:** See `agents/import_matter_qa.yaml` for `llm.call` usage with `ratelimit.wrap`
- **Action registration pattern:** See `actions/graphology.py` for custom action registration via `register_actions()`
- **State management:** All agents use `state_schema` with typed fields
- **Error handling:** Set `status: "error"` and `error: "<message>"`, route to `__end__`
- **Temperature:** Use 0.1 for deterministic classification (contrast with import_matter_qa which uses 1.0 for creative QA)

### Previous Story Intelligence (Story 16.1)

From Story 16.1 analysis:
- `detect_directory` node is a new node added between `extract_and_download` and `resolve_agent`
- `document_text` is already extracted and available in state from Story 16.1
- The current Story 16.1 design errors when TIER 1+2 fail — Story 16.2 MUST modify this to route to TIER 3 instead
- Story 16.1 tasks reference `detect_directory` Task 3.8: "Error on all-fail" — this needs to become conditional: error only if TIER 3 also fails
- **Critical dependency:** Story 16.1 MUST be implemented first. Story 16.2 modifies `detect_directory` routing from 16.1.

### Git Intelligence

Recent commits show:
- **Status tracking pattern** (1af541b): `set_status_running` → process → `set_status_final` with `graphology.update_node` — useful for tracking classification status
- **LLM temperature** (4920198): Changed from 0 to 1 for import_matter_qa — confirms temperature is a per-agent decision; 0.1 is correct for classification
- **Success inference** (4920198): `app.py` infers success from `save_result`/`answers` when no explicit status — ensure file_classification agent sets explicit `status` field

### Project Structure Notes

- **New file:** `agents/file_classification.yaml` — New TEA YAML agent for TIER 3 LLM classification
- **New file:** `actions/agents.py` — Custom action for sub-agent invocation (if using custom action approach)
- **Modified file:** `agents/file_extraction.yaml` — Add invoke_classification node, modify detect_directory routing
- **Modified file:** `app.py` — Register new actions from `actions/agents.py` (if created)
- **Modified file:** `requirements.txt` — Add `pycountry` if used for ISO resolution
- **Test file:** `tests/test_file_classification.py` — New test file for standalone classification agent
- **Test file:** `tests/test_file_extraction.py` — Extend with TIER 3 integration tests

### References

- [Source: _bmad-output/planning-artifacts/epics.md:467-518] — Story 16.2 requirements, acceptance criteria, implementation notes
- [Source: agents/file_extraction.yaml] — Current YAML agent to modify (add TIER 3 invocation)
- [Source: agents/file_extraction.yaml:44-60] — State schema to extend with `classification_result`
- [Source: agents/import_matter_qa.yaml] — LLM call pattern reference (llm.call, ratelimit.wrap)
- [Source: actions/graphology.py] — Custom action registration and caching pattern reference
- [Source: app.py:18-45] — YAMLEngine loading and invocation pattern for sub-agent design
- [Source: _bmad-output/implementation-artifacts/16-1-tier-1-2-filename-content-pattern-detection.md] — Previous story (TIER 1+2) with detect_directory node design
- [Source: _bmad-output/project-context.md] — Project conventions (TypeScript ESM rules, but Python agents follow TEA YAML patterns)
- [Source: _bmad-output/planning-artifacts/epics.md:336-366] — Additional requirements (reference impl paths, dependency list)
- [Source: tests/test_file_extraction.py] — Existing test patterns to extend

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

### Completion Notes List

- Created `agents/file_classification.yaml` with full LLM classification flow: check_cache → classify_document → parse_result → validate_result → store_cache
- Two prompt branches: unknown directory (full 5-directory classification) and known directory (category/region/year only)
- LLM config: temperature=0.1, max_tokens=200, model=gpt-4.1-mini
- ISO code resolution via pycountry: alpha-3 country codes → display names, ISO 3166-2 state codes → display names
- Created `actions/agents.py` with cache_get, cache_set (SHA-256 content hash, 30-day TTL), and invoke_agent (sub-agent invocation via YAMLEngine)
- Modified `agents/file_extraction.yaml`: detect_directory now routes to invoke_classification when TIER 1+2 fail OR directory found but year missing
- Added invoke_classification node (uses agents.invoke_agent) and process_classification node (extracts results, sets directory_name/detected_year)
- Updated `app.py` to register agent actions alongside graphology actions
- Added pycountry to requirements.txt
- 31 new tests in test_file_classification.py, 11 new tests in test_file_extraction.py (TIER 3 routing + process_classification)
- Updated 2 existing tests that expected TIER 1+2 failure to error (now routes to TIER 3 instead)
- Full suite: 248 passed, 4 pre-existing failures (0 new regressions)

### File List

- agents/file_classification.yaml (new)
- actions/agents.py (new)
- agents/file_extraction.yaml (modified)
- app.py (modified)
- requirements.txt (modified)
- tests/test_file_classification.py (new)
- tests/test_file_extraction.py (modified)
- _bmad-output/planning-artifacts/epics.md (modified)
- _bmad-output/implementation-artifacts/sprint-status.yaml (modified)
- _bmad-output/implementation-artifacts/16-2-tier-3-llm-classification-agent.md (modified)
