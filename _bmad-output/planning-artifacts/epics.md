---
stepsCompleted: ["step-01-validate-prerequisites", "step-02-design-epics", "step-03-create-stories", "step-04-final-validation"]
inputDocuments:
  - "/home/fabricio/src/ai-workflow/docs/architecture/ai-workflow-trigger-system.md"
  - "/home/fabricio/src/ai-workflow/agents/file_extraction.yaml"
  - "/home/fabricio/src/ai-workflow/_bmad-output/planning-artifacts/epics/epic-14.md"
  - "/home/fabricio/src/ai-workflow/_bmad-output/implementation-artifacts/sprint-status.yaml"
scope: "ButtonAction trigger for file_extraction agent (Phase 6 of trigger system architecture)"
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
