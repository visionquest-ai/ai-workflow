# Task 1: AIWorkflow Schema & UI Generation

## Goal

Generate the AIWorkflow ontology schema from the rankellix Neo4j database and build DynamicUI graph nodes so the App Tree React component can render workflow management pages (navigation, forms, tables).

## Prerequisites

- Rankellix Neo4j database running (`bolt+s://neo4j.visionquest.space:7687`, database: `rankellix`)
- Graphology server available at `http://localhost:4000/graphql`
- AIWorkflow ontology reference docs at `docs/reference/aiworkflow-ontology/`

## Data Points

### AIWorkflow Classes (17)

| Class | Description |
|-------|-------------|
| Workflow | Top-level workflow container |
| Step | Ordered step within a workflow |
| Prompt | Question/instruction template |
| PromptVersion | Versioned prompt content (active/draft/archived) |
| PromptBody | Structured prompt body |
| PromptExecution | Record of prompt execution |
| PromptResponse | LLM response from execution |
| PromptOutput | Structured output |
| ContextNode | Document/data context |
| Company | Company entity |
| CompanyAnalysis | Company analysis result |
| Sector | Industry sector |
| Party | Legal party |
| LegalEntity | Legal entity |
| Jurisdiction | Legal jurisdiction |
| DocumentSection | Document section |
| DocumentTopic | Document topic classification |

### Key Relations (8)

| Relation | From → To |
|----------|-----------|
| HAS_STEP | Workflow → Step |
| HAS_PROMPT | Step → Prompt |
| HAS_VERSION | Prompt → PromptVersion |
| HAS_EXECUTION | PromptVersion → PromptExecution |
| HAS_CONTEXT | PromptExecution → ContextNode |
| HAS_RESPONSE | PromptExecution → PromptResponse |
| FOLLOWED_BY | Step → Step |
| DEFINES_CLASS / DEFINES_PROPERTY | Ontology structure |

## Schema Generation

```bash
# From graphology directory (AppTree/_visionquest/graphology)
# 1. Set .env to rankellix database
# 2. Generate
npm run build && NODE_OPTIONS="--max-old-space-size=16384" npm run generate
# 3. Back up AIWorkflow files to ai-workflow/docs/reference/aiworkflow-ontology/
# 4. Restore .env to native database
# 5. Regenerate for native
```

## DynamicUI Graph Shape

For each AIWorkflow entity that needs a UI:

```
App → HAS_MENU_ELEMENT → MenuElement("AI Workflows")
  → HAS_PAGE → Page("Workflows")
    → PAGE_HAS_TAB → Tab("Workflow List")
      → TAB_HAS_TABLE_VIEW → TableView
        → TABLE_VIEW_HAS_COLUMN → Column(name, status, ...)
    → PAGE_HAS_TAB → Tab("Workflow Detail")
      → TAB_HAS_FORM_SECTION → FormSection
        → FORM_SECTION_HAS_FIELD → Field(name, description, ...)
      → TAB_HAS_TABLE_VIEW → TableView("Steps")
        → TABLE_VIEW_HAS_COLUMN → Column(name, order, stepType, ...)
```

## Acceptance Criteria

- [ ] AIWorkflow ontology docs are up to date in `docs/reference/aiworkflow-ontology/`
- [ ] DynamicUI nodes exist for Workflow list/detail pages
- [ ] DynamicUI nodes exist for Prompt management (versions, execution history)
- [ ] Table views correctly display Workflow → Steps → Prompts hierarchy
- [ ] Form sections allow editing PromptVersion content and status
- [ ] App Tree renders all new pages/tabs without errors
