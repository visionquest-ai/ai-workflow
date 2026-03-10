---
stepsCompleted: ["step-01-validate-prerequisites", "step-02-design-epics", "step-03-create-stories", "step-04-final-validation"]
inputDocuments:
  - "/home/fabricio/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md"
  - "/home/fabricio/src/ai-workflow/agents/file_extraction.yaml"
  - "/home/fabricio/src/ai-workflow/_bmad-output/planning-artifacts/epics/epic-14.md"
  - "/home/fabricio/src/ai-workflow/_bmad-output/implementation-artifacts/sprint-status.yaml"
  - "/home/fabricio/src/ai-workflow/_bmad-output/project-context.md"
  - "ref:spa-base/firebase/functions-agents/triggers/file_extraction.py"
  - "ref:spa-base/firebase/functions-agents/agents/file_classification_agent.yaml"
  - "ref:spa-base/firebase/projects/rankellix/functions-python/main.py"
scope: "Three-tier directory & year detection for file_extraction agent (ported from spa-base)"
---

# ai-workflow - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for ai-workflow, implementing a law firm website scraper agent (`scrape_law_firm`) that receives a LegalFirm node, reads its `website` property, scrapes the site via ScrapeGraphAI, and persists structured results to the `websitePayload` property via GraphQL.

## Requirements Inventory

### Functional Requirements

- FR1: Agent receives a LegalFirm node ID as `context_node_id` input
- FR2: Agent fetches the LegalFirm node via GraphQL (`graphology.get_node`) to read the `website` property
- FR3: Agent validates the node is of type LegalFirm and has a non-empty `website` URL
- FR4: Agent calls ScrapeGraphAI (`web.ai_scrape`) with the website URL and a structured extraction prompt
- FR5: Agent extracts comprehensive law firm data: firmName, foundedYear, website, linkedin, businessModel, description, country, city, state, totalLawyers, totalPartners, mainEmail, mainPhone, managingPartners[], offices[], practiceAreas[], awards[]
- FR6: Agent serializes the extraction result as JSON and saves it to the `websitePayload` property on the same LegalFirm node via GraphQL mutation (`graphology.update_node`)
- FR7: Agent reports success/error status upon completion

### NonFunctional Requirements

- NFR1: Scraping errors (timeouts, rate limits, auth failures) must be handled gracefully without crashing
- NFR2: Temporary state must be cleaned up; agent must not leak resources
- NFR3: The `websitePayload` property must exist in the Neo4j ontology and GraphQL schema for LegalFirm
- NFR4: ScrapeGraphAI API key (`SCRAPEGRAPH_API_KEY`) must be available in the environment
- NFR5: Agent must follow the same TEA YAML patterns as `file_extraction.yaml` (nodes, edges, graphology actions)

### Additional Requirements

- The `websitePayload` property may need to be added to the LegalFirm ontology class (OntologyClass -> OntologyProperty)
- GraphQL schema (graphology) must expose `websitePayload` as a mutable String field on LegalFirm
- The `web.ai_scrape` action must be available in the TEA engine (from `the_edge_agent` submodule)
- The extraction prompt and JSON schema match the spa-base reference agent's structure
- Agent invocation: `POST /run-agent {"agent": "scrape_law_firm", "context_node_id": "<LegalFirm-node-id>"}`

### FR Coverage Map

| Requirement | Epic | Story | Description |
|-------------|------|-------|-------------|
| FR1 | Epic 1 | 1.1 | Agent receives LegalFirm node ID as input |
| FR2 | Epic 1 | 1.1 | Fetch node via graphology.get_node |
| FR3 | Epic 1 | 1.1 | Validate node type and website URL |
| FR4 | Epic 1 | 1.2 | Call ScrapeGraphAI with extraction prompt |
| FR5 | Epic 1 | 1.2 | Extract structured law firm data |
| FR6 | Epic 1 | 1.3 | Save result to websitePayload via GraphQL |
| FR7 | Epic 1 | 1.3 | Report success/error status |
| NFR1 | Epic 1 | 1.2 | Graceful error handling |
| NFR2 | Epic 1 | 1.3 | Resource cleanup |
| NFR3 | Epic 1 | 1.3 | websitePayload in ontology/schema (precondition) |
| NFR4 | Epic 1 | 1.2 | SCRAPEGRAPH_API_KEY in environment |
| NFR5 | Epic 1 | All | Follow file_extraction.yaml patterns |

## Epic List

| Epic | Title | Stories | Priority |
|------|-------|---------|----------|
| 1 | Law Firm Website Scraper Agent | 3 | P0 (done) |
| 15 | File Extraction Button Trigger with PathNode Input Mapping | 3 | P0 |
| 16 | Automatic File Classification for Directory & Year Detection | 3 | P0 |

---

## Epic 1: Law Firm Website Scraper Agent

Operators can scrape a law firm's website by providing a LegalFirm node ID, and the agent automatically fetches the website URL, extracts structured firm data, and persists the result — all through a single API call.
**FRs covered:** FR1-FR7, NFR1-NFR5

### Story 1.1: Fetch LegalFirm Node and Validate Input

As a system operator,
I want the agent to fetch a LegalFirm node by ID and validate it has a website URL,
So that the scraping pipeline only proceeds with valid, scrapeable nodes.

**Acceptance Criteria:**

**Given** a valid `context_node_id` pointing to a LegalFirm node with a populated `website` field
**When** the agent executes the `fetch_node` and `validate_input` steps
**Then** `context_result.data` contains `website`, `firmName`, and other LegalFirm fields
**And** `context_result.node_type` equals `"LegalFirm"`

**Given** a `context_node_id` pointing to a node that is NOT a LegalFirm
**When** the agent executes validation
**Then** the agent returns `status: "error"` with message indicating wrong node type
**And** `completed` is set to `true`

**Given** a LegalFirm node with an empty or missing `website` field
**When** the agent executes validation
**Then** the agent returns `status: "error"` with message "LegalFirm has no website URL"
**And** the agent terminates gracefully via `__end__`

### Story 1.2: Scrape Website via ScrapeGraphAI

As a system operator,
I want the agent to scrape the law firm website using ScrapeGraphAI and extract structured firm data,
So that comprehensive firm information is available in a standardized JSON format.

**Acceptance Criteria:**

**Given** a validated LegalFirm node with a non-empty `website` URL
**When** the agent calls `web.ai_scrape` with the URL and extraction prompt
**Then** the result contains structured fields: `firmName`, `foundedYear`, `website`, `linkedin`, `businessModel`, `description`, `country`, `city`, `state`, `totalLawyers`, `totalPartners`, `mainEmail`, `mainPhone`, `managingPartners[]`, `offices[]`, `practiceAreas[]`, `awards[]`

**Given** the extraction prompt sent to ScrapeGraphAI
**When** the AI scraper processes the website
**Then** it navigates About, Team, Contact, and Practice Areas pages as needed
**And** returns data matching the JSON schema from the spa-base reference agent

**Given** the ScrapeGraphAI API returns an error (timeout, rate limit, auth failure)
**When** the agent processes the response
**Then** `status` is set to `"error"` with a descriptive error message
**And** the agent terminates gracefully without crashing

**Given** the ScrapeGraphAI API returns a partial or empty result
**When** the agent processes the response
**Then** the partial result is still serialized and persisted (no data loss)
**And** `status` reflects `"success"` (partial data is valid)

### Story 1.3: Persist Extraction Result to websitePayload

As a system operator,
I want the scrape result to be saved to the `websitePayload` field on the LegalFirm node via GraphQL,
So that the extracted data is accessible from the knowledge graph for downstream consumers.

**Precondition:** The `websitePayload` property must exist on the LegalFirm ontology class in Neo4j. If it does not exist, create it via Cypher before running the agent:
```cypher
MATCH (c:OntologyClass {name: "LegalFirm"})
CREATE (p:OntologyProperty {name: "websitePayload", type: "String"})
CREATE (c)-[:HAS_PROPERTY]->(p)
```
Then restart graphology to regenerate the GraphQL schema.

**Acceptance Criteria:**

**Given** a successful scrape with structured result data
**When** the agent calls `graphology.update_node` with `node_type: "LegalFirm"` and `updates: {websitePayload: "<json>"}`
**Then** the LegalFirm node's `websitePayload` field contains the serialized JSON string
**And** `update_result.success` is `true`

**Given** the scrape returned an error
**When** the agent prepares the payload
**Then** `websitePayload` contains a JSON object with `{"error": "...", "status": "failed"}`
**And** the error is persisted to the node for visibility

**Given** the GraphQL mutation fails (e.g., field doesn't exist, network error)
**When** the `save_payload` step runs
**Then** the agent returns `status: "error"` with the GraphQL error message
**And** `completed` is `true`

**Given** the agent completes successfully (scrape + save)
**When** the `finalize` step runs
**Then** `completed` is `true` and `status` is `"success"`
**And** temporary state is cleaned up

---

# Epic 15 Scope: File Extraction Button Trigger with PathNode Input Mapping

## Requirements Inventory (Phase 6 - Manual Trigger / ButtonAction Integration)

### Functional Requirements

- FR-BT1: TRIGGERS_WORKFLOW relationship bootstrapped from ButtonAction (DynamicUI) to Workflow (AIWorkflow)
- FR-BT2: MANUAL trigger type handled by trigger middleware — dispatches when ButtonAction with TRIGGERS_WORKFLOW is activated
- FR-BT3: The dispatch passes the ApplicationFormFile node ID as `context_node_id` to `/run-agent`, resolved via PathNode chain or context mapping
- FR-BT4: WorkflowDispatch node created for manual triggers to track lifecycle (pending → running → completed/failed)
- FR-BT5: Manual dispatch skips trigger condition evaluation (no PROPERTY_CHANGED/VALUE_MATCH to check)
- FR-BT6: INPUT_MAPPING_HAS_PATH relationship from WorkflowInputMapping to PathNode enables graph-traversal parameter resolution (mappingType: "path")
- FR-BT7: Middleware dispatches to existing `/run-agent` endpoint with resolved parameters

### Non-Functional Requirements

- NFR-BT1: Dispatch is fire-and-forget — the UI button click returns immediately (architecture: non-blocking principle)
- NFR-BT2: The async endpoint (Story 14.1, already done) supports long-running extraction (up to 300s)
- NFR-BT3: Failed dispatches logged with `[aiWorkflowTrigger]` prefix, never affect UI response

### Additional Requirements

- Cross-repo: all ontology/middleware changes are in graphology repo; ai-workflow requires NO code changes
- Story 18.1 (trigger system pilot) is done — middleware, bootstrap, PROPERTY_CHANGED working
- Story 18.3 (WorkflowInputMapping) is in-progress — supports `literal` and `context` mappingTypes
- PathNode class already exists in DynamicUI ontology — reused by Field, Column, Tab visibility, Modal upload paths
- PathNode chains: ordered via `ord`, `isFirst`, `isLast` edge properties; traverse via PATH_STEP_VIA_RELATION, PATH_STEP_AT_CLASS, PATH_STEP_TO_PROPERTY
- Architecture doc Section 8 defines `path` mappingType using INPUT_MAPPING_HAS_PATH → PathNode chains
- The `file_extraction` agent already works via `POST /run-agent {"agent": "file_extraction", "context_node_id": "<id>"}`

### FR Coverage Map (Epic 15)

| Requirement | Story | Description |
|-------------|-------|-------------|
| FR-BT1 | 15.1 | TRIGGERS_WORKFLOW relation bootstrapped (ButtonAction → Workflow) |
| FR-BT2 | 15.1 | MANUAL trigger type evaluation in middleware |
| FR-BT3 | 15.2 + 15.3 | PathNode/context mapping resolves entityId → context_node_id |
| FR-BT4 | 15.1 | WorkflowDispatch created for manual triggers |
| FR-BT5 | 15.1 | MANUAL dispatch skips condition evaluation |
| FR-BT6 | 15.2 | INPUT_MAPPING_HAS_PATH + PathNode resolution at dispatch time |
| FR-BT7 | 15.1 | Middleware dispatches to existing /run-agent |
| NFR-BT1 | 15.1 | Fire-and-forget from middleware |
| NFR-BT2 | 15.1 | Async endpoint already done (14.1) |
| NFR-BT3 | 15.1 | Failed dispatches logged, never block UI |

## Epic 15: File Extraction Button Trigger with PathNode Input Mapping

A user viewing an ApplicationFormFile can click an "Extract" button, which dispatches the file_extraction agent with parameters resolved from the graph via PathNode chains — fully graph-configured, zero code changes needed to add new button triggers in the future.

**FRs covered:** FR-BT1 through FR-BT7, NFR-BT1 through NFR-BT3
**Depends on:** Story 18.3 (WorkflowInputMapping, graphology, in-progress)
**Implementation repo:** graphology (ai-workflow requires NO code changes)

### Story 15.1: Bootstrap TRIGGERS_WORKFLOW & MANUAL Trigger Type

As a system administrator,
I want the TRIGGERS_WORKFLOW relationship bootstrapped and the MANUAL trigger type handled by the middleware,
So that ButtonActions in the DynamicUI can dispatch AI workflows without code changes.

**Acceptance Criteria:**

**Given** the bootstrap script `src/ai-workflow/bootstrap.ts`
**When** it runs against Neo4j
**Then** `TRIGGERS_WORKFLOW` OntologyRelation exists from ButtonAction (DynamicUI) to Workflow (AIWorkflow)
**And** GraphQL schema regeneration produces the relationship on both ButtonAction and Workflow types

**Given** a ButtonAction node linked via `TRIGGERS_WORKFLOW` to a Workflow with `agentName: "file_extraction"`
**When** the trigger middleware receives a manual dispatch request for that ButtonAction
**Then** it identifies the linked Workflow via `TRIGGERS_WORKFLOW`
**And** skips trigger condition evaluation (MANUAL type has no conditions)
**And** dispatches to `/run-agent` with the Workflow's `agentName` and resolved inputs

**Given** a manual dispatch is triggered
**When** the middleware processes it
**Then** a WorkflowDispatch node is created with `status: "pending"`, `executionMode: "fire_and_forget"`, `dispatchedAt: <timestamp>`
**And** `DISPATCH_OF_WORKFLOW` links the dispatch to the Workflow
**And** on completion, status is updated to `"completed"` or `"failed"`

**Given** a ButtonAction node with NO `TRIGGERS_WORKFLOW` relationship
**When** queried by the middleware
**Then** no dispatch occurs (button behaves as standard DynamicUI button — e.g., opens modal)

**Given** any error during MANUAL dispatch
**When** it occurs
**Then** the error is logged with `[aiWorkflowTrigger]` prefix
**And** the original UI response is NOT affected

### Story 15.2: PathNode Input Mapping Resolution (mappingType: "path")

As a system administrator,
I want workflow input parameters resolved by traversing PathNode chains from the triggered entity,
So that complex parameters (nested relations, related entity properties) can be mapped to agent inputs without code.

**Acceptance Criteria:**

**Given** the bootstrap script `src/ai-workflow/bootstrap.ts`
**When** it runs against Neo4j
**Then** `INPUT_MAPPING_HAS_PATH` OntologyRelation exists from WorkflowInputMapping to PathNode (0:N)
**And** the relationship has edge properties: `ord` (Int), `isFirst` (Boolean), `isLast` (Boolean)
**And** GraphQL schema regeneration produces the relationship with edge properties

**Given** a WorkflowInputMapping with `mappingType: "path"` and a PathNode chain:
  - PathNode(ord=0, isFirst=true) → PATH_STEP_TO_PROPERTY → OntologyProperty("storageUrl")
**When** `resolveInputMappings()` processes this mapping with entityId `"appfile-123"` of type `"ApplicationFormFile"`
**Then** it builds a GraphQL query to read `storageUrl` from ApplicationFormFile node `"appfile-123"`
**And** the resolved output includes the property value

**Given** a WorkflowInputMapping with `mappingType: "path"` and a multi-step PathNode chain:
  - PathNode(ord=0) → PATH_STEP_VIA_RELATION → OntologyRelation("HAS_DIRECTORY") + PATH_STEP_AT_CLASS → OntologyClass("Directory")
  - PathNode(ord=1) → PATH_STEP_TO_PROPERTY → OntologyProperty("name")
**When** `resolveInputMappings()` processes this mapping
**Then** it traverses the relation from the entity, arrives at the related class, reads the property
**And** the resolved output includes the traversed property value

**Given** a PathNode chain that resolves to `null` (e.g., no related entity exists)
**When** the mapping has `isRequired: true`
**Then** the dispatch is skipped and an error is logged
**When** the mapping has `isRequired: false`
**Then** `defaultValue` is used as fallback (or null if no default)

**Given** the `MATCHING_WORKFLOWS_QUERY` in the trigger middleware
**When** it loads workflows for an entity class
**Then** it also fetches `inputMappingHasPath` with `pathStepViaRelation { name }`, `pathStepAtClass { name }`, `pathStepToProperty { name }` and edge properties `ord`, `isFirst`, `isLast`

### Story 15.3: Seed File Extraction Button Trigger

As a platform operator,
I want a pre-configured ButtonAction on the ApplicationFormFile form that triggers the file_extraction agent,
So that users can extract structured data from uploaded files with a single click.

**Acceptance Criteria:**

**Given** the seed script runs against Neo4j
**When** it completes
**Then** a Workflow node exists with `name: "File Extraction"`, `agentName: "file_extraction"`
**And** `WORKFLOW_TARGETS_CLASS` links the Workflow to OntologyClass `"ApplicationFormFile"`

**Given** the seed script creates input mappings
**When** it completes
**Then** a WorkflowInputMapping exists with `name: "context_node_id"`, `mappingType: "context"`, `defaultValue: "entityId"`, `isRequired: true`
**And** `WORKFLOW_HAS_INPUT_MAPPING` links the Workflow to this mapping

**Given** the seed script creates the ButtonAction
**When** it completes
**Then** a ButtonAction node exists with `actionType: "triggerWorkflow"`
**And** `TRIGGERS_WORKFLOW` links the ButtonAction to the "File Extraction" Workflow
**And** the ButtonAction is linked to the appropriate Heading on the ApplicationFormFile form via `HAS_ADD_BUTTON_ACTION`

**Given** a user clicks the "Extract" button on an ApplicationFormFile with ID `"appfile-789"`
**When** the MANUAL dispatch flow executes
**Then** the middleware resolves `context_node_id` to `"appfile-789"` via the context mapping
**And** dispatches `POST /run-agent { agent: "file_extraction", context_node_id: "appfile-789", async_mode: true }`
**And** a WorkflowDispatch node tracks the extraction lifecycle

**Given** the file_extraction agent completes (success or failure)
**When** the dispatch callback fires
**Then** the WorkflowDispatch status is updated to `"completed"` or `"failed"`
**And** the result or error is stored on the dispatch node

---

# Epic 16 Scope: Three-Tier Directory & Year Detection for File Extraction Agent

## Requirements Inventory (Ported from spa-base RX.20 + RX.23.4)

### Functional Requirements

- FR-DD1: When directoryName is missing/empty on the ApplicationFormFile node, the agent SHALL attempt automatic directory detection via a three-tier fallback cascade (filename → content → LLM)
- FR-DD2: TIER 1 — Detect directory from the file's fileName by matching against keyword patterns (chambers: ['chambers','c&p','candp'], iflr1000: ['iflr','iflr1000'], legal500: ['legal500','legal 500','l500'], itr: ['itr','international tax review'], leadersleague: ['leaders','league','leadersleague','leaders league'])
- FR-DD3: TIER 2 — Extract text from the downloaded file (first 3500 chars) and match against 52+ content keyword patterns mapped to each directory (URLs, domain terms, form structure indicators)
- FR-DD4: TIER 3 — When TIER 1 and TIER 2 both fail, invoke a file_classification LLM agent to classify the document and detect the directory
- FR-DD5: Track which tier detected the directory via a directory_source field: "input" (pre-set), "filename" (TIER 1), "content" (TIER 2), "llm" (TIER 3)
- FR-DD6: When directoryName IS already populated on the node, use it directly with directory_source="input" — preserving current behavior as a fast path
- FR-DD7: The detected directory slug must resolve to the correct LlamaExtract agent name via the existing base_name_map (chambers→chambers-partners, iflr1000→iflr-1000, etc.)
- FR-DD8: Text extraction for TIER 2 must support DOCX (python-docx) and PDF (pdfplumber) file formats
- FR-DD9: The LLM classification agent (TIER 3) must return: directory, category, region_country, region_state, confidence, is_empty_form — with ISO code resolution for country/state
- FR-DD10: Detect the form year using the same three-tier cascade — TIER 1: extract year from fileName (e.g., "chambers_2025.docx" → 2025), TIER 2: extract year from content keywords/patterns in the first 3500 chars, TIER 3: LLM classification returns the year
- FR-DD11: Track year detection source via year_source field ("filename", "content", "llm", "input") independently from directory_source
- FR-DD12: When a year is already provided on the node, use it directly with year_source="input"

### Non-Functional Requirements

- NFR-DD1: TIER 2 content scanning must be limited to the first 3500 characters for performance
- NFR-DD2: TIER 3 LLM classification should be cached by content hash to avoid redundant API calls
- NFR-DD3: TIER 1 and TIER 2 detection must complete sub-second (no network calls, pure pattern matching)
- NFR-DD4: If all 3 tiers fail to detect a directory, the agent must error gracefully with a descriptive message listing supported directories
- NFR-DD5: All new nodes must follow TEA YAML patterns consistent with existing file_extraction.yaml structure

### Additional Requirements

- Text extraction for TIER 2 requires python-docx and pdfplumber dependencies (file already downloaded in extract_and_download node)
- A new file_classification agent YAML (agents/file_classification.yaml) must be created for TIER 3
- The LLM classification prompt must cover all 5 directory types with specific indicators from spa-base
- State schema needs new fields: directory_source, document_text, classification_result, detected_year, year_source
- The classification metadata (category, region, confidence) should be persisted alongside the extraction payload
- The existing extract_and_download node already downloads and converts files — text extraction for TIER 2 should reuse the downloaded file
- Reference implementation: spa-base/firebase/functions-agents/triggers/file_extraction.py (TIER 1+2) and spa-base/firebase/functions-agents/agents/file_classification_agent.yaml (TIER 3)

### FR Coverage Map (Epic 16)

| Requirement | Story | Description |
|-------------|-------|-------------|
| FR-DD1 | 16.1 | Three-tier cascade when directoryName missing |
| FR-DD2 | 16.1 | TIER 1 filename keyword patterns |
| FR-DD3 | 16.1 | TIER 2 content keyword patterns (52+) |
| FR-DD4 | 16.2 | TIER 3 LLM classification fallback |
| FR-DD5 | 16.1 + 16.3 | directory_source tracking |
| FR-DD6 | 16.1 | Fast path when directoryName pre-set |
| FR-DD7 | 16.1 | Directory slug → LlamaExtract agent name |
| FR-DD8 | 16.1 | DOCX/PDF text extraction for TIER 2 |
| FR-DD9 | 16.2 | LLM returns directory + category + region + confidence |
| FR-DD10 | 16.1 + 16.2 | Year detection via same three tiers |
| FR-DD11 | 16.1 + 16.3 | year_source tracking |
| FR-DD12 | 16.1 | Fast path when year pre-set |
| NFR-DD1 | 16.1 | First 3500 chars limit |
| NFR-DD2 | 16.2 | LLM cache by content hash |
| NFR-DD3 | 16.1 | Sub-second TIER 1+2 |
| NFR-DD4 | 16.1 | Graceful error on all-tiers-fail |
| NFR-DD5 | All | TEA YAML patterns |

## Epic 16: Automatic File Classification for Directory & Year Detection

The file_extraction agent automatically identifies which legal directory a document belongs to and its submission year, using a three-tier fallback cascade (filename patterns → content keyword scanning → LLM classification), eliminating the need for pre-set metadata on ApplicationFormFile nodes.

**FRs covered:** FR-DD1 through FR-DD12, NFR-DD1 through NFR-DD5
**Depends on:** Epic 14 (file_extraction agent already working — done)

### Story 16.1: TIER 1+2 — Filename & Content Pattern Detection for Directory and Year

As a system operator,
I want the file_extraction agent to automatically detect the directory and year from the filename and file content when they are not pre-set on the node,
So that most files (~80%) are classified and routed to the correct LlamaExtract agent without LLM calls.

**Acceptance Criteria:**

**Given** an ApplicationFormFile node with `directoryName` already populated (e.g., `"chambers"`)
**When** the agent executes the `detect_directory` step
**Then** `directory_name` is set to the pre-existing value
**And** `directory_source` is set to `"input"`
**And** the agent skips TIER 1, 2, and 3 detection

**Given** an ApplicationFormFile with `directoryName` empty/missing and `fileName` = `"chambers_2025.docx"`
**When** the agent executes TIER 1 filename detection
**Then** `directory_name` is set to `"chambers"` (matched keyword `"chambers"`)
**And** `directory_source` is set to `"filename"`
**And** `detected_year` is set to `2025` (extracted from filename)
**And** `year_source` is set to `"filename"`

**Given** an ApplicationFormFile with `directoryName` empty and `fileName` = `"submission_form.docx"` (no directory keyword in name)
**When** the agent executes TIER 1
**Then** TIER 1 returns no match
**And** the agent proceeds to TIER 2 content scanning

**Given** the downloaded file contains `"myaccount.chambers.com"` or `"Band 1"` within the first 3500 characters
**When** the agent executes TIER 2 content keyword scanning
**Then** `directory_name` is set to `"chambers"`
**And** `directory_source` is set to `"content"`

**Given** the downloaded file contains `"iflr1000.com"` or `"Market Leader"` within the first 3500 characters
**When** the agent executes TIER 2
**Then** `directory_name` is set to `"iflr1000"`
**And** `directory_source` is set to `"content"`

**Given** the file content contains a 4-digit year pattern (e.g., `"2025 submission"`) within the first 3500 chars and no year was detected from filename
**When** the agent executes TIER 2 year detection
**Then** `detected_year` is extracted from content
**And** `year_source` is set to `"content"`

**Given** TIER 1 and TIER 2 both fail to detect a directory
**When** the agent reaches the end of TIER 2
**Then** `directory_name` remains empty
**And** the agent proceeds to TIER 3 (LLM classification in Story 16.2) if available, or errors gracefully with: `"Could not detect directory. Supported: chambers, iflr1000, legal500, itr, leadersleague"`

**Given** a `.docx` file is downloaded
**When** the agent extracts text for TIER 2 content scanning
**Then** it uses `python-docx` to extract paragraph and table text
**And** only the first 3500 characters are scanned

**Given** a `.pdf` file is downloaded (including Word docs already converted to PDF)
**When** the agent extracts text for TIER 2 content scanning
**Then** it uses `pdfplumber` to extract page text
**And** only the first 3500 characters are scanned

**Given** an ApplicationFormFile with `year` already populated on the node
**When** the agent executes year detection
**Then** `detected_year` is set to the pre-existing value
**And** `year_source` is set to `"input"`
**And** year detection tiers are skipped

**Implementation Notes:**
- Modify `extract_and_download` node to also extract text (first 3500 chars) from the downloaded file after conversion
- Add new `detect_directory` node between `extract_and_download` and `resolve_agent`
- Add state fields: `directory_source`, `document_text`, `detected_year`, `year_source`
- TIER 1 patterns: chambers=['chambers','c&p','candp'], iflr1000=['iflr','iflr1000'], legal500=['legal500','legal 500','l500'], itr=['itr','international tax review'], leadersleague=['leaders','league','leadersleague','leaders league']
- TIER 2 content patterns: 52+ keywords from spa-base `DIRECTORY_CONTENT_PATTERNS` mapping
- Remove the hard error on missing `directoryName` in `extract_and_download` — detection replaces it
- Reference: `spa-base/firebase/functions-agents/triggers/file_extraction.py` lines 27-88, 256-282, 460-495

### Story 16.2: TIER 3 — LLM Classification Agent

As a system operator,
I want a fallback LLM classification agent that identifies the directory and year when filename and content patterns fail,
So that 100% of files can be classified regardless of naming or content patterns.

**Acceptance Criteria:**

**Given** TIER 1 and TIER 2 both failed to detect `directory_name`
**When** the file_extraction agent reaches the TIER 3 step
**Then** it invokes `agents/file_classification.yaml` with `document_text` (first 3500 chars)
**And** passes `detected_directory: null` to indicate no pre-detection

**Given** the LLM classification agent receives document text from a Chambers submission
**When** the LLM analyzes the text
**Then** it returns JSON with `directory: "chambers"`, `category` (e.g., "Corporate/M&A"), `region_country` (ISO 3166-1 alpha-3), `region_state` (ISO 3166-2), `confidence` (0.0-1.0), `year`, `is_empty_form` (boolean)
**And** `directory_source` is set to `"llm"`

**Given** the LLM classification agent receives document text from any of the 5 supported directories
**When** the LLM analyzes the text
**Then** the prompt includes specific indicators for all 5 directories: Chambers (Band rankings, PAB006/PAM006 refs), IFLR1000 (Market Leader, accreditation.euromoney.com), Legal500 (Tier rankings, Hall of Fame), ITR (World Tax, itrworldtax.com), Leaders League (Décideurs, peer feedback)

**Given** the LLM cannot identify the directory with confidence
**When** it returns a low confidence score (< 0.5) or `directory: null`
**Then** the file_extraction agent errors gracefully with `"LLM classification inconclusive. Supported: chambers, iflr1000, legal500, itr, leadersleague"`
**And** `status` is set to `"error"` and `completed` is `true`

**Given** the LLM returns ISO country/state codes (e.g., `"BRA"`, `"BR-SP"`)
**When** the classification result is parsed
**Then** `region_country_display` resolves to the country name (e.g., `"Brazil"`)
**And** `region_state_display` resolves to the state name (e.g., `"São Paulo"`)

**Given** TIER 1 or TIER 2 successfully detected the directory but year was not detected
**When** the agent reaches TIER 3
**Then** it invokes the LLM classification agent with `detected_directory` pre-set
**And** the LLM uses the "known directory" prompt branch (focused on category/region/year extraction)

**Given** the LLM classification result is cached by content hash
**When** the same document text is processed again
**Then** the cached result is returned without a new LLM call
**And** cache TTL is 30 days

**Implementation Notes:**
- Create `agents/file_classification.yaml` — TEA YAML agent with LLM node
- Two prompt branches: unknown directory (full classification) vs known directory (category/region/year only)
- Cache key: `classify:file:{{ document_text | sha256 }}`, TTL: 30 days
- LLM model: configurable (default: gpt-4o or similar)
- Temperature: 0.1 (deterministic classification)
- Max tokens: 200
- Add conditional node in file_extraction.yaml: if TIER 1+2 failed OR year missing → invoke file_classification agent
- Output stored in `classification_result` state field
- Reference: `spa-base/firebase/functions-agents/agents/file_classification_agent.yaml`

### Story 16.3: Persist Classification Metadata

As a system operator,
I want the classification metadata (directory_source, year_source, category, region, confidence) persisted alongside the extraction payload,
So that downstream consumers can audit how files were classified and leverage the enriched metadata.

**Acceptance Criteria:**

**Given** a successful file extraction with classification metadata available
**When** the `save_payload` step runs
**Then** the GraphQL mutation includes `classificationPayload` alongside `payload` on the ApplicationFormFile node
**And** `classificationPayload` contains JSON with: `directory`, `directory_source`, `detected_year`, `year_source`, `category`, `region_country`, `region_country_display`, `region_state`, `region_state_display`, `confidence`, `is_empty_form`

**Given** a file classified via TIER 1 (filename) with no LLM call
**When** the classification metadata is persisted
**Then** `classificationPayload` contains `directory_source: "filename"` and `category`, `region_country`, `region_state` are `null` (not available without LLM)
**And** `confidence` is `1.0` (exact pattern match)

**Given** a file classified via TIER 3 (LLM) with full classification
**When** the classification metadata is persisted
**Then** `classificationPayload` contains all fields populated: `directory_source: "llm"`, `category`, `region_country`, `region_country_display`, `region_state`, `region_state_display`, `confidence`, `is_empty_form`

**Given** a file where directory was detected but extraction failed
**When** the `save_payload` step runs with error status
**Then** `classificationPayload` is still persisted with whatever metadata was available
**And** `payload` contains the error JSON as before

**Given** the `classificationPayload` property does not exist on ApplicationFormFile
**When** the story is implemented
**Then** the property is created via GraphQL mutations: create OntologyProperty node (`name: "classificationPayload"`, `type: "String"`), then connect it to the ApplicationFormFile OntologyClass via `HAS_PROPERTY` relationship
**And** graphology is restarted to regenerate the GraphQL schema

**Given** the `detectedYear` property does not exist on ApplicationFormFile
**When** the story is implemented
**Then** the property is created via GraphQL mutations: create OntologyProperty node (`name: "detectedYear"`, `type: "String"`), then connect it to the ApplicationFormFile OntologyClass via `HAS_PROPERTY` relationship
**And** graphology is restarted to regenerate the GraphQL schema

**Implementation Notes:**
- Add `classificationPayload` and `detectedYear` to ApplicationFormFile ontology via GraphQL mutations (create OntologyProperty + connect via HAS_PROPERTY)
- Modify `prepare_payload` node to also build `classification_json` from classification state fields
- Modify `save_payload` node to include `classificationPayload` and `detectedYear` in the `graphology.update_node` updates
- State schema adds: `classification_json` (serialized classification metadata)
- Reference: spa-base persists classification alongside extraction in the same RTDB write
