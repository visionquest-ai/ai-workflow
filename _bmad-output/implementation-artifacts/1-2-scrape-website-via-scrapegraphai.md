# Story 1.2: Scrape Website via ScrapeGraphAI

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want the agent to scrape the law firm website using ScrapeGraphAI and extract structured firm data,
so that comprehensive firm information is available in a standardized JSON format.

## Acceptance Criteria

1. **AC1 — Happy path: successful scrape with full data**
   **Given** a validated LegalFirm node with a non-empty `website` URL
   **When** the agent calls `web.ai_scrape` with the URL and extraction prompt
   **Then** the result contains structured fields: `firmName`, `foundedYear`, `website`, `linkedin`, `businessModel`, `description`, `country`, `city`, `state`, `totalLawyers`, `totalPartners`, `mainEmail`, `mainPhone`, `managingPartners[]`, `offices[]`, `practiceAreas[]`, `awards[]`

2. **AC2 — AI scraper navigates multiple pages**
   **Given** the extraction prompt sent to ScrapeGraphAI
   **When** the AI scraper processes the website
   **Then** it navigates About, Team, Contact, and Practice Areas pages as needed
   **And** returns data matching the JSON schema from the spa-base reference agent

3. **AC3 — ScrapeGraphAI API error handling**
   **Given** the ScrapeGraphAI API returns an error (timeout, rate limit, auth failure)
   **When** the agent processes the response
   **Then** `status` is set to `"error"` with a descriptive error message
   **And** the agent terminates gracefully without crashing

4. **AC4 — Partial/empty result handling**
   **Given** the ScrapeGraphAI API returns a partial or empty result
   **When** the agent processes the response
   **Then** the partial result is still serialized and available for downstream persistence (story 1.3)
   **And** `status` reflects `"success"` (partial data is valid)

5. **AC5 — SCRAPEGRAPH_API_KEY required**
   **Given** the `SCRAPEGRAPH_API_KEY` environment variable is not set
   **When** the `web.ai_scrape` action executes
   **Then** it returns `success: false` with an appropriate error message
   **And** the agent sets `status: "error"` and terminates gracefully

6. **AC6 — TEA YAML pattern compliance (NFR5)**
   **Given** the modified agent YAML file
   **When** reviewed against `file_extraction.yaml` patterns
   **Then** the new scraping node uses `uses: web.ai_scrape` or `uses: cache.wrap` with `action: web.ai_scrape`
   **And** error handling follows the same `return {"error": ..., "status": "error", "completed": True}` pattern
   **And** edges use conditional branching based on state fields

## Tasks / Subtasks

- [x] Task 1: Add scraping-related state fields to `scrape_law_firm.yaml` (AC: #1, #6)
  - [x] 1.1: Add `scrape_result: dict` to `state_schema` (output from `web.ai_scrape`)
  - [x] 1.2: Add `scrape_data_json: str` to `state_schema` (serialized extraction for story 1.3)

- [x] Task 2: Implement `scrape_website` node using `web.ai_scrape` (AC: #1, #2, #5)
  - [x] 2.1: Create node `scrape_website` using `uses: web.ai_scrape`
  - [x] 2.2: Set `url: "{{ state.website_url }}"` (populated by `validate_input` from story 1.1)
  - [x] 2.3: Add extraction `prompt` matching spa-base reference (comprehensive firm data extraction instruction)
  - [x] 2.4: Add `output_schema` with the complete JSON schema for all 17 fields (see Dev Notes below)
  - [x] 2.5: Set `output: scrape_result`

- [x] Task 3: Implement `process_scrape_result` node to handle success/error/partial (AC: #1, #3, #4)
  - [x] 3.1: Create `process_scrape_result` node as a `run:` Python block
  - [x] 3.2: Check `scrape_result.get("success")` — if `False`, set `status: "error"` with error message and `completed: True` (AC #3)
  - [x] 3.3: On success (full or partial), serialize `scrape_result.get("data", {})` to JSON string in `scrape_data_json` (AC #1, #4)
  - [x] 3.4: Set `status: "success"` — partial data is still valid (AC #4)
  - [x] 3.5: Do NOT set `completed: True` on success — story 1.3 will add the persistence step after this

- [x] Task 4: Update edges to wire scraping into the flow (AC: #3, #6)
  - [x] 4.1: Change `validate_input` success path: instead of → `__end__`, route to → `scrape_website`
  - [x] 4.2: Add edge `scrape_website` → `process_scrape_result`
  - [x] 4.3: Add edge `process_scrape_result` → `__end__` when `completed` is `true` (error path)
  - [x] 4.4: Add edge `process_scrape_result` → `__end__` when `status` is `"success"` (temporary — story 1.3 will retarget this to `save_payload`)

- [x] Task 5: Write/extend tests (AC: #1, #3, #4, #5, #6)
  - [x] 5.1: In `tests/test_scrape_law_firm.py`, add test for happy path — mock `web.ai_scrape` returning full structured data
  - [x] 5.2: Test scrape error — mock `web.ai_scrape` returning `{"success": False, "error": "Rate limited"}`
  - [x] 5.3: Test partial result — mock `web.ai_scrape` returning `{"success": True, "data": {"firmName": "Test"}}` (only partial fields)
  - [x] 5.4: Test that `scrape_data_json` contains valid JSON string after successful scrape
  - [x] 5.5: Test YAML structure — verify `scrape_website` node exists with correct `uses: web.ai_scrape` and `output_schema`
  - [x] 5.6: Test edge wiring — verify `validate_input` success path leads to `scrape_website` (not `__end__`)

## Dev Notes

### Architecture Requirements

- **Agent Framework**: TEA YAML engine (`the_edge_agent` submodule) — agents are `.yaml` files in `agents/` directory
- **ScrapeGraphAI Integration**: `web.ai_scrape` is a TEA built-in action registered in `the_edge_agent/python/src/the_edge_agent/actions/web_actions.py`
- **API Key**: `SCRAPEGRAPH_API_KEY` env var — read by TEA engine, NOT by ai-workflow code
- **No new Python dependencies**: `web.ai_scrape` is fully handled by the TEA engine; no additions to `requirements.txt` needed

### Key Patterns from file_extraction.yaml (NFR5 Compliance)

1. **`uses:` nodes**: For TEA built-in actions like `web.ai_scrape`, use `uses:` with `with:` parameters and `output:` field
2. **`run:` nodes**: For inline Python (processing results), use `run:` with `state.get()` to read state
3. **Error Handling**: Every `run:` block checks errors and returns `{"error": "...", "status": "error", "completed": True}`
4. **Edge Conditions**: Use `condition: "{{ state.field }}"` / `"{{ not state.field }}"` for branching
5. **Config**: `config: raise_exceptions: false` already set in story 1.1

### web.ai_scrape Action Interface

From TEA docs (`the_edge_agent/docs/shared/yaml-reference/actions/integrations.md`):

```yaml
- name: scrape_website
  uses: web.ai_scrape
  with:
    url: "{{ state.website_url }}"         # Required
    prompt: "Extract comprehensive..."     # Required
    output_schema:                          # Schema (inline dict)
      type: object
      properties:
        firmName: { type: string }
        # ... all fields
    max_retries: 3                          # Optional (default: 3)
  output: scrape_result
```

**Returns:**
```python
# Success:
{"success": True, "data": {...extracted_fields...}, "url": str, "schema_used": {...}}

# Failure:
{"success": False, "error": "Rate limited / timeout / auth failure"}
```

### Complete Output Schema (from spa-base reference agent)

The `output_schema` MUST include ALL 18 fields exactly as defined in the spa-base reference agent at `/home/fabricio/src/spa-base/firebase/functions-agents/agents/scrape-law-firm.yaml`. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `firmName` | string | Official registered name |
| `foundedYear` | string | YYYY format or null |
| `website` | string | Official website URL |
| `linkedin` | string | LinkedIn URL or null |
| `businessModel` | string | One of: corporate-firm, boutique-firm, full-service, specialist, virtual, alternative-legal-provider |
| `description` | string | Brief 2-3 sentence description |
| `country` | string | ISO 3166-1 alpha-3 (e.g., BRA, USA) |
| `city` | string | HQ city |
| `state` | string | ISO 3166-2 code (e.g., BR-SP) |
| `totalLawyers` | integer | Total lawyers or null |
| `totalPartners` | integer | Total partners or null |
| `mainEmail` | string | Main contact email |
| `mainPhone` | string | Main contact phone |
| `managingPartners` | array | Objects with: title, firstName, surname, email, phone, role, isManagingPartner, biography, employmentStartDate |
| `offices` | array | Objects with: city, state, country, address, phone, isHeadOffice |
| `practiceAreas` | array | Strings of practice area names |
| `awards` | array | Strings of awards/rankings |

### Extraction Prompt (from spa-base reference)

Use this exact prompt (proven to work with ScrapeGraphAI):
```
Extract comprehensive law firm information from this website.
Look for: firm name, founding year, contact details, partners/lawyers,
office locations, practice areas, awards, and any available biographical
information about key personnel. Check the About, Team, Contact, and
Practice Areas pages if available.
```

### cache.wrap Decision

The spa-base reference uses `cache.wrap` wrapping `web.ai_scrape` with 60-day TTL. However, `web.ai_scrape` also has built-in `cache:` support. **Decision for developer**:

- **Option A (Recommended)**: Use `web.ai_scrape` directly with built-in `cache:` config — simpler, fewer nodes
- **Option B**: Use `cache.wrap` like spa-base — more explicit, matches reference exactly

Either approach is acceptable. The developer should choose based on whether caching is needed for this use case (scraping the same firm repeatedly). If unsure, start without caching (can be added later).

### Story 1.1 Continuation Points

This story extends the agent created in story 1.1. Key connection points:

- **`validate_input` success path**: Currently routes to `__end__` — must be retargeted to `scrape_website`
- **`website_url` state field**: Already populated by `validate_input` in story 1.1
- **Error pattern**: Same `{"error": ..., "status": "error", "completed": True}` pattern

### Story 1.3 Continuation Points

This story prepares for story 1.3:
- `scrape_data_json` will contain the serialized JSON string ready for `graphology.update_node`
- The success path from `process_scrape_result` → `__end__` will be retargeted to `save_payload` in story 1.3

### Environment Variables

- `GRAPHOLOGY_URL` — GraphQL endpoint (already configured in story 1.1)
- `GRAPHOLOGY_API_KEY` — Optional API key for graphology (already configured)
- `SCRAPEGRAPH_API_KEY` — **Required for this story** — API key for ScrapeGraphAI service

### Project Structure Notes

- Agent YAML file: `agents/scrape_law_firm.yaml` (created in story 1.1)
- Tests: `tests/test_scrape_law_firm.py` (created in story 1.1, extend with scraping tests)
- No new files need to be created — this story modifies existing files only

### Git Intelligence (Recent Commits)

Recent commits show:
- `file_extraction.yaml` is the primary pattern reference — recently hardened with error handling, PDF conversion, and settings-driven extraction mode
- `graphology.get_node` pattern is well-established
- Submodule `the_edge_agent` is frequently updated — ensure using current version
- Convention: commit messages use `feat:`, `chore:`, `test:`, `docs:` prefixes

### References

- [Source: agents/file_extraction.yaml] — Primary pattern reference for TEA YAML structure and `uses:` node pattern
- [Source: spa-base/firebase/functions-agents/agents/scrape-law-firm.yaml] — Original reference agent with complete output_schema and prompt
- [Source: the_edge_agent/docs/shared/yaml-reference/actions/integrations.md#web.ai_scrape] — `web.ai_scrape` action API reference
- [Source: the_edge_agent/docs/python/actions-reference.md] — TEA actions overview
- [Source: _bmad-output/implementation-artifacts/1-1-fetch-legalfirm-node-and-validate-input.md] — Previous story with continuation points
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.2] — Story definition and acceptance criteria
- [Source: tests/test_file_extraction.py] — Test patterns for TEA YAML agents

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

No issues encountered during implementation.

### Completion Notes List

- Task 1: Added `scrape_result: dict` and `scrape_data_json: str` to state_schema
- Task 2: Implemented `scrape_website` node using `uses: web.ai_scrape` with full extraction prompt and complete 17-field output_schema matching spa-base reference (firmName, foundedYear, website, linkedin, businessModel, description, country, city, state, totalLawyers, totalPartners, mainEmail, mainPhone, managingPartners[], offices[], practiceAreas[], awards[])
- Task 3: Implemented `process_scrape_result` as `run:` Python block — checks `scrape_result.success`, serializes data to `scrape_data_json` JSON string on success, sets `status: "error"` + `completed: True` on failure, does NOT set `completed: True` on success (reserved for story 1.3)
- Task 4: Rewired edges — `validate_input` success → `scrape_website` → `process_scrape_result` → `__end__` (both error and temporary success paths)
- Task 5: Extended tests from 10 to 32 — added 9 process_scrape_result tests (happy path, rate limit, timeout, auth failure, default error, partial, empty, JSON validity, missing data key) + 8 YAML structure tests (scrape_website node, output_schema, edges wiring)
- Decision: Used `web.ai_scrape` directly without `cache.wrap` (Option A from Dev Notes) — simpler, caching can be added later if needed

### Change Log

- 2026-03-08: Story 1.2 implementation complete — all 5 tasks done, 32 tests passing
- 2026-03-08: Code review — fixed 4 issues: updated outdated header comment (H1), added ensure_ascii=False to json.dumps for Unicode support (M1), corrected test file AC mapping (M2), fixed 18→17 field count in docs (M3). 32 tests passing. Status → done

### File List

- agents/scrape_law_firm.yaml (modified — added scrape_website node, process_scrape_result node, state fields, edges)
- tests/test_scrape_law_firm.py (modified — extended from 10 to 32 tests covering all ACs)
