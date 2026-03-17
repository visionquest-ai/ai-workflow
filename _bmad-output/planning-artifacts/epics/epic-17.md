---
status: completed
inputDocuments:
  - "User specification (command arguments) — detailed requirements for file_extraction.yaml enhancement"
  - "visionQuest/ai-workflow/agents/file_extraction.yaml — existing agent implementation"
  - "visionQuest/spa-base/docs/formsPayloadsTemplate/ — LlamaExtract schema templates"
  - "visionQuest/ai-workflow/actions/graphology.py — GraphQL actions"
  - "Graph ontology (ApplicationFormFile, Directory, PracticeArea, LegalField, Region nodes)"
---

# Epic 17: Directory Enrichment & Fuzzy Matching

> **Status: COMPLETED** — Moved from repo root epics.md during hetzner↔spa-replace-apptree merge.
> Originally tracked as Epics 1-3 in the root repo before ai-workflow was established as the owner module.

## Overview

ApplicationFormFile nodes are enriched with Directory, PracticeArea, LegalField, and Region data by combining LlamaExtract extraction results with fuzzy matching against graph master data.

## Requirements Inventory

### Functional Requirements

FR1: Save directory name to ApplicationFormFile node immediately after directory detection (early in pipeline, before LlamaExtract call)
FR2: Add `directoryName` property to ApplicationFormFile in the ontology schema
FR3: Query the Directory node by name via GraphQL and retrieve its ID
FR4: Save the Directory node ID to ApplicationFormFile (relationship or property — to be determined from ontology)
FR5: After LlamaExtract returns extraction result, extract the `practiceArea` string from the payload (format varies by directory schema)
FR6: Query all PracticeArea nodes associated with the detected Directory from the graph via GraphQL
FR7: Perform fuzzy matching of the LlamaExtract `practiceArea` value against the PracticeArea master list from the graph, using a 3-tier algorithm:
  - CA-1 Substring Matching (priority): exact case-insensitive match, whole word intersection scoring, substring containment (min 3 chars)
  - CA-2 Jaro-Winkler Similarity (fallback): threshold >= 0.85 for valid match, using jellyfish library
  - CA-3 Fallback: return empty string if no match found
FR8: Once PracticeArea is matched, save PracticeArea name and ID to ApplicationFormFile immediately
FR9: Traverse PracticeArea → PRACTICE_AREA_HAS_LEGAL_FIELD → LegalField to retrieve the LegalField node
FR10: Save LegalField name and ID to ApplicationFormFile
FR11: Extract region from LlamaExtract result — top-level/global field (not nested inside matters), field name varies by schema: `location` (Chambers), `jurisdiction` (IFLR1000, ITR), `country` (Legal 500)
FR12: Query all Region nodes from the graph via GraphQL (including hints property)
FR13: Perform fuzzy matching of the LlamaExtract region value against Region master list using the same 3-tier algorithm (CA-1, CA-2, CA-3), with expanded hints
FR14: Once Region is matched, save Region name and ID to ApplicationFormFile
FR15: All data persistence operations must use the typed GraphQL API (no direct Cypher)
FR16: Region matching must support a hints/aliases system. Hints stored as a property on the Region node in Neo4j (e.g., `hints: "MG|Minas|Southeast Brazil"`), enabling abbreviations like "MG" to match "Minas Gerais"
FR17: When querying Region nodes via GraphQL, also fetch the `hints` property. Expand each Region into multiple searchable terms (canonical name + each hint), with `via_hint` flag for traceability
FR18: Hints bypass the minimum 3-character requirement in substring matching (CA-1), allowing short abbreviations like "MG", "SP", "RJ" to match
FR19: In case of multiple exact matches, hint-based matches take priority over canonical name matches (more specific)
FR20: PracticeArea nodes also support a `hints` property for the same fuzzy matching flexibility
FR21: Add `hints` property (String, pipe-delimited) to the Region node type in the ontology schema
FR22: Add `hints` property (String, pipe-delimited) to the PracticeArea node type in the ontology schema

### NonFunctional Requirements

NFR1: Fuzzy matching must be deterministic and reproducible — same input always produces same match
NFR2: The `jellyfish` library must be added as a dependency to `requirements.txt`
NFR3: Fuzzy matching scores >= 0.7 should be logged for debug; scores >= 0.5 should be tracked
NFR4: All GraphQL operations must include error handling with best-effort status updates on failure (consistent with existing AC6 pattern in file_extraction.yaml)
NFR5: The pipeline must remain idempotent — re-running on the same file should produce the same results

### Additional Requirements

- The `practiceArea` field format varies across directory schemas: Chambers uses "Practice: Region" format, Legal 500 uses full dropdown path, IFLR1000/ITR use simple names
- Each directory schema has a different field name for region: `location` (Chambers), `jurisdiction` (IFLR1000, ITR), `country` (Legal 500)
- ITR's `practiceArea` is an object with boolean flags (`tax`, `transferPricing`, `both`) — requires special handling
- New properties on ApplicationFormFile: `practiceAreaName`, `practiceAreaId`, `legalFieldName`, `legalFieldId`, `regionName`, `regionId`, `directoryId`, `directoryName`
- The fuzzy matching module should be reusable (used for both PracticeArea and Region matching)
- Ontology changes required: add `hints` to Region and PracticeArea node types, add `directoryName` to ApplicationFormFile
- These ontology changes must be applied before the fuzzy matching stories can work (dependency)
- After ontology changes: regenerate schema → reload server → update docs
- Region nodes in Neo4j need a `hints` property (pipe-delimited aliases string)
- The fuzzy matching module must accept a searchable list with expanded hints, propagating `via_hint` in results for traceability

### FR Coverage Map

| FR | Sub-Epic | Description |
|---|---|---|
| FR1 | 17A | Save directoryName to ApplicationFormFile early |
| FR2 | 17A | Add directoryName to ApplicationFormFile ontology |
| FR3 | 17A | Query Directory node by name via GraphQL |
| FR4 | 17A | Save Directory ID to ApplicationFormFile |
| FR5 | 17B | Extract practiceArea from LlamaExtract payload |
| FR6 | 17B | Query PracticeArea nodes for detected Directory |
| FR7 | 17B | Fuzzy match practiceArea (CA-1, CA-2, CA-3) |
| FR8 | 17B | Save PracticeArea name+ID to ApplicationFormFile |
| FR9 | 17B | Traverse PracticeArea → LegalField |
| FR10 | 17B | Save LegalField name+ID to ApplicationFormFile |
| FR11 | 17C | Extract region (global field) from LlamaExtract |
| FR12 | 17C | Query Region nodes with hints via GraphQL |
| FR13 | 17C | Fuzzy match region (CA-1, CA-2, CA-3) |
| FR14 | 17C | Save Region name+ID to ApplicationFormFile |
| FR15 | 17A | All persistence via typed GraphQL API |
| FR16 | 17B | Hints/aliases system for Region matching |
| FR17 | 17B | Expand hints into searchable terms with via_hint |
| FR18 | 17B | Hints bypass 3-char minimum |
| FR19 | 17B | Hint matches take priority |
| FR20 | 17B | PracticeArea hints support |
| FR21 | 17A | Add hints property to Region ontology |
| FR22 | 17A | Add hints property to PracticeArea ontology |

## Sub-Epics

### 17A: Directory Identification & Early Persistence (COMPLETED)
ApplicationFormFile nodes are enriched with directory name and ID immediately after detection, providing instant visibility into which legal directory a file belongs to. Ontology is extended to support all new fields needed across the feature.
**FRs covered:** FR1, FR2, FR3, FR4, FR15, FR21, FR22
**NFRs covered:** NFR4, NFR5

### 17B: PracticeArea Resolution & LegalField Derivation (COMPLETED)
After LlamaExtract extraction, ApplicationFormFile is enriched with matched PracticeArea (name+ID) and derived LegalField (name+ID), enabling downstream workflows to route files by legal domain. Includes the reusable fuzzy matching engine with hint expansion.
**FRs covered:** FR5, FR6, FR7, FR8, FR9, FR10, FR16, FR17, FR18, FR19, FR20
**NFRs covered:** NFR1, NFR2, NFR3

### 17C: Region Resolution (COMPLETED)
ApplicationFormFile is enriched with matched Region (name+ID), completing the geographic dimension of file metadata and enabling region-based filtering and routing.
**FRs covered:** FR11, FR12, FR13, FR14
**NFRs covered:** NFR4, NFR5

---

## Story 17A.1: Ontology Schema Extension for Directory Enrichment (COMPLETED)

As a **system administrator**,
I want the ontology schema extended with new properties on ApplicationFormFile (`directoryName`, `directoryId`, `practiceAreaName`, `practiceAreaId`, `legalFieldName`, `legalFieldId`, `regionName`, `regionId`), Region (`hints`), and PracticeArea (`hints`),
So that the graph database can persist all enrichment data produced by the extraction pipeline.

**Acceptance Criteria:**

**Given** the current ontology schema
**When** the schema extension is applied
**Then** ApplicationFormFile has new String properties: `directoryName`, `directoryId`, `practiceAreaName`, `practiceAreaId`, `legalFieldName`, `legalFieldId`, `regionName`, `regionId`
**And** Region node type has a new String property `hints` (pipe-delimited aliases)
**And** PracticeArea node type has a new String property `hints` (pipe-delimited aliases)
**And** schema is regenerated and server reloaded
**And** all new properties are queryable and mutable via GraphQL API

## Story 17A.2: Save Directory Name to ApplicationFormFile Early in Pipeline (COMPLETED)

As a **file extraction pipeline**,
I want the detected directory name saved to the ApplicationFormFile node immediately after directory detection (before LlamaExtract call),
So that downstream steps and external systems have visibility into which directory a file belongs to as early as possible.

**Acceptance Criteria:**

**Given** an ApplicationFormFile node with a detected `directory_name` (from TIER 1, 2, or 3 detection)
**When** the `detect_directory` step completes successfully
**Then** the `directoryName` property is persisted to the ApplicationFormFile node via GraphQL
**And** the update occurs before the LlamaExtract extraction step
**And** if the GraphQL update fails, the error is logged but the pipeline continues (best-effort, consistent with AC6 pattern)
**And** re-running the pipeline on the same file overwrites `directoryName` with the same value (idempotent)

## Story 17A.3: Directory Node Lookup and ID Persistence (COMPLETED)

As a **file extraction pipeline**,
I want to query the Directory node by name via GraphQL, retrieve its ID, and save it to the ApplicationFormFile,
So that the file has a direct reference to its canonical Directory entity in the graph.

**Acceptance Criteria:**

**Given** a detected `directoryName` value on the ApplicationFormFile (saved in Story 17A.2)
**When** the pipeline queries for a Directory node matching the directory name via GraphQL
**Then** the matching Directory node's ID is retrieved
**And** the `directoryId` property is saved to the ApplicationFormFile via GraphQL
**And** if no Directory node matches the name, `directoryId` is left empty and a warning is logged
**And** if the GraphQL query fails, the error is logged but the pipeline continues (best-effort)
**And** no direct Cypher queries are used — all operations go through typed GraphQL API (FR15)

## Story 17B.1: Reusable Fuzzy Matching Module (COMPLETED)

As a **file extraction pipeline**,
I want a reusable fuzzy matching module that can match an input string against a master list using a 3-tier algorithm (substring matching, Jaro-Winkler similarity, fallback),
So that both PracticeArea and Region matching use the same proven, deterministic logic.

**Acceptance Criteria:**

**Given** an input string and a master list of entries (each with `name`, optional `hints` pipe-delimited, and `id`)
**When** the fuzzy matcher is invoked
**Then** it expands each master entry into searchable terms: canonical name + each hint, with `via_hint` flag for traceability

**Given** an input string
**When** CA-1 Substring Matching runs (priority)
**Then** exact case-insensitive match is attempted first
**And** whole word intersection scoring is performed (input_words ∩ search_words), ranked by word count and total length
**And** substring containment is checked (input in name or name in input), with minimum 3-character requirement
**And** hints bypass the 3-character minimum, allowing short matches like "MG", "SP"

**Given** CA-1 produces no match
**When** CA-2 Jaro-Winkler Similarity runs (fallback)
**Then** `jellyfish.jaro_winkler_similarity()` is used against the expanded searchable list
**And** threshold >= 0.85 is required for a valid match
**And** scores >= 0.7 are logged for debug
**And** scores >= 0.5 are tracked

**Given** CA-2 produces no match
**When** CA-3 Fallback runs
**Then** empty string is returned for both name and id

**Given** multiple exact matches exist
**When** resolving priority
**Then** hint-based matches take priority over canonical name matches

**And** the `jellyfish` library is added to `requirements.txt` (NFR2)
**And** the module is deterministic — same input always produces same output (NFR1)
**And** the module returns a result dict: `{matched_name, matched_id, score, via_hint, tier}`

## Story 17B.2: Extract PracticeArea from LlamaExtract Payload (COMPLETED)

As a **file extraction pipeline**,
I want to extract the `practiceArea` value from the LlamaExtract extraction result, handling per-directory format variations,
So that the raw practice area string is available for fuzzy matching.

**Acceptance Criteria:**

**Given** a successful LlamaExtract extraction result for a **Chambers** file
**When** the practiceArea is extracted
**Then** the `practiceArea` string field is read (format: "Practice: Region", e.g., "Tax: Southeast")

**Given** a successful extraction result for a **Legal 500** file
**When** the practiceArea is extracted
**Then** the `practiceArea` string field is read (format: full dropdown path, e.g., "Brazil - City focus - Belo Horizonte - Commercial, corporate and M&A")

**Given** a successful extraction result for an **IFLR1000** file
**When** the practiceArea is extracted
**Then** the `practiceArea` string field is read (simple name, e.g., "M&A", "Banking & Finance")

**Given** a successful extraction result for an **ITR** file
**When** the practiceArea is extracted
**Then** the `practiceArea` object with boolean flags (`tax`, `transferPricing`, `both`) is converted to a canonical string (e.g., "Tax", "Transfer Pricing", "Tax and Transfer Pricing")

**Given** a successful extraction result for a **Leaders League** file
**When** the practiceArea is extracted
**Then** the `practiceArea` string field is read

**Given** the extraction result has no `practiceArea` field or it is empty
**When** extraction is attempted
**Then** an empty string is returned and a warning is logged

## Story 17B.3: PracticeArea Fuzzy Matching & Persistence (COMPLETED)

As a **file extraction pipeline**,
I want to query PracticeArea nodes for the detected directory from the graph, fuzzy match the extracted practiceArea string, and save the matched PracticeArea name and ID to the ApplicationFormFile,
So that files are linked to their canonical practice area in the graph.

**Acceptance Criteria:**

**Given** a detected directory and an extracted practiceArea string
**When** the pipeline queries PracticeArea nodes via GraphQL
**Then** all PracticeArea nodes associated with the directory are fetched, including `name`, `id`, and `hints` properties

**Given** the PracticeArea master list and the extracted practiceArea string
**When** the fuzzy matching module (Story 17B.1) is invoked
**Then** the best matching PracticeArea is identified using the 3-tier algorithm with hint expansion

**Given** a successful PracticeArea match
**When** the result is persisted
**Then** `practiceAreaName` and `practiceAreaId` are saved to the ApplicationFormFile via GraphQL
**And** the save occurs immediately after match resolution

**Given** no PracticeArea match is found (CA-3 fallback)
**When** the result is handled
**Then** `practiceAreaName` and `practiceAreaId` are left empty
**And** a warning is logged with the unmatched input string and top candidates

**And** if the GraphQL operations fail, errors are logged but the pipeline continues (best-effort, NFR4)
**And** re-running produces the same match result (NFR5)

## Story 17B.4: LegalField Derivation from PracticeArea (COMPLETED)

As a **file extraction pipeline**,
I want to traverse the graph from the matched PracticeArea via `PRACTICE_AREA_HAS_LEGAL_FIELD` to retrieve the LegalField node, and save its name and ID to the ApplicationFormFile,
So that the legal field classification is automatically derived and persisted.

**Acceptance Criteria:**

**Given** a matched PracticeArea with a valid `practiceAreaId`
**When** the pipeline queries the graph via GraphQL for the `PRACTICE_AREA_HAS_LEGAL_FIELD` relationship
**Then** the connected LegalField node's `name` and `id` are retrieved

**Given** a valid LegalField is found
**When** the result is persisted
**Then** `legalFieldName` and `legalFieldId` are saved to the ApplicationFormFile via GraphQL

**Given** no PracticeArea was matched (empty `practiceAreaId`)
**When** LegalField derivation is attempted
**Then** the step is skipped and `legalFieldName`/`legalFieldId` remain empty

**Given** the PracticeArea has no connected LegalField
**When** the traversal returns no results
**Then** `legalFieldName` and `legalFieldId` are left empty and a warning is logged

**And** all operations use typed GraphQL API (FR15)
**And** errors are handled with best-effort pattern (NFR4)

## Story 17C.1: Extract Region from LlamaExtract Payload (COMPLETED)

As a **file extraction pipeline**,
I want to extract the region value from the LlamaExtract extraction result, handling per-directory field name variations,
So that the raw region string is available for fuzzy matching.

**Acceptance Criteria:**

**Given** a successful LlamaExtract extraction result for a **Chambers** file
**When** the region is extracted
**Then** the `location` top-level field is read (e.g., "Brazil")

**Given** a successful extraction result for a **Legal 500** file
**When** the region is extracted
**Then** the `country` top-level field is read (e.g., "United Kingdom")

**Given** a successful extraction result for an **IFLR1000** file
**When** the region is extracted
**Then** the `jurisdiction` top-level field is read (e.g., "Brazil")

**Given** a successful extraction result for an **ITR** file
**When** the region is extracted
**Then** the `jurisdiction` top-level field is read

**Given** a successful extraction result for a **Leaders League** file
**When** the region is extracted
**Then** the appropriate region/location field is read

**Given** the extraction result has no region field or it is empty
**When** extraction is attempted
**Then** an empty string is returned and a warning is logged

## Story 17C.2: Region Fuzzy Matching & Persistence (COMPLETED)

As a **file extraction pipeline**,
I want to query Region nodes (with hints) from the graph, fuzzy match the extracted region string, and save the matched Region name and ID to the ApplicationFormFile,
So that files are linked to their canonical geographic region in the graph.

**Acceptance Criteria:**

**Given** an extracted region string from Story 17C.1
**When** the pipeline queries Region nodes via GraphQL
**Then** all Region nodes are fetched, including `name`, `id`, and `hints` properties

**Given** the Region master list and the extracted region string
**When** the fuzzy matching module (Story 17B.1) is invoked
**Then** the best matching Region is identified using the 3-tier algorithm with hint expansion
**And** abbreviations like "MG" match "Minas Gerais" via hints (FR16)

**Given** a successful Region match
**When** the result is persisted
**Then** `regionName` and `regionId` are saved to the ApplicationFormFile via GraphQL

**Given** no Region match is found (CA-3 fallback)
**When** the result is handled
**Then** `regionName` and `regionId` are left empty
**And** a warning is logged with the unmatched input string and top candidates

**And** if the GraphQL operations fail, errors are logged but the pipeline continues (best-effort, NFR4)
**And** re-running produces the same match result (NFR5)
