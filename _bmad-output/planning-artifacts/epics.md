---
stepsCompleted: ["step-01-validate-prerequisites", "step-02-design-epics", "step-03-create-stories"]
inputDocuments:
  - "/home/fabricio/src/spa-base/firebase/functions-agents/agents/scrape-law-firm.yaml"
  - "/home/fabricio/src/ai-workflow/agents/file_extraction.yaml"
  - "user-provided context: LegalFirm ontology, websitePayload, GraphQL schema, credentials"
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
| 1 | Law Firm Website Scraper Agent | 3 | P0 |

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
