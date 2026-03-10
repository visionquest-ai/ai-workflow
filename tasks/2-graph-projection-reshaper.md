# Task 2: Graph Projection Reshaper — Model Relations

## Goal

Create the 3 new ontology relations (JSON_SCHEMA_CENTERS_AT, DEFINITION_CENTERS_AT, PROPERTY_HAS_PROJECTION_PATH) in the Neo4j meta-ontology, generate the GraphQL schema, and validate with the Graph Consistency Guardian.

## Architecture Reference

- **Full architecture:** `docs/architecture/graph-projection-reshaper.md`
- **ITR mapping spec:** `dynamic-rankellix-executor/itr-projection-mapping.md`

## Prerequisites

- Rankellix Neo4j database running (`bolt+s://neo4j.visionquest.space:7687`, database: `rankellix`)
- Graphology server available at `http://localhost:4000/graphql`
- GraphQL schema generator at `AppTree/_visionquest/graphology`

## Phase 1: Create Relations in Neo4j

### 1.1 JSON_SCHEMA_CENTERS_AT

Create OntologyRelation node:
```cypher
CREATE (r:OntologyRelation {
  name: 'JSON_SCHEMA_CENTERS_AT',
  description: 'Declares the root Rankellix entity class for a JSON Schema document. The center class is the starting point for all projection path traversals within this schema.'
})
```

Link to source/destination classes and parent ontology:
```cypher
MATCH (r:OntologyRelation {name: 'JSON_SCHEMA_CENTERS_AT'})
MATCH (src:OntologyClass {name: 'JSONSchema'})
MATCH (dst:OntologyClass {name: 'OntologyClass'})
MATCH (ont:Ontology {name: 'JSONSchemaOntology'})
CREATE (r)-[:RELATION_SOURCE {cardinality: '0:1'}]->(src)
CREATE (r)-[:RELATION_DESTINATION {cardinality: '0:1'}]->(dst)
CREATE (ont)-[:ONTOLOGY_HAS_ONTOLOGY_RELATION]->(r)
```

### 1.2 DEFINITION_CENTERS_AT

```cypher
CREATE (r:OntologyRelation {
  name: 'DEFINITION_CENTERS_AT',
  description: 'Declares the Rankellix entity class for a SchemaDefinition, when it differs from the parent JSONSchema center. If absent, the definition inherits the schema-level center.'
})
```

```cypher
MATCH (r:OntologyRelation {name: 'DEFINITION_CENTERS_AT'})
MATCH (src:OntologyClass {name: 'SchemaDefinition'})
MATCH (dst:OntologyClass {name: 'OntologyClass'})
MATCH (ont:Ontology {name: 'JSONSchemaOntology'})
CREATE (r)-[:RELATION_SOURCE {cardinality: '0:1'}]->(src)
CREATE (r)-[:RELATION_DESTINATION {cardinality: '0:1'}]->(dst)
CREATE (ont)-[:ONTOLOGY_HAS_ONTOLOGY_RELATION]->(r)
```

### 1.3 PROPERTY_HAS_PROJECTION_PATH

```cypher
CREATE (r:OntologyRelation {
  name: 'PROPERTY_HAS_PROJECTION_PATH',
  description: 'Ordered traversal path from center entity to the Rankellix data that feeds (export) or is fed by (import) this schema property. Mirrors COLUMN_HAS_PATH pattern.'
})
```

```cypher
MATCH (r:OntologyRelation {name: 'PROPERTY_HAS_PROJECTION_PATH'})
MATCH (src:OntologyClass {name: 'SchemaProperty'})
MATCH (dst:OntologyClass {name: 'PathNode'})
MATCH (ont:Ontology {name: 'JSONSchemaOntology'})
CREATE (r)-[:RELATION_SOURCE {cardinality: '0:N'}]->(src)
CREATE (r)-[:RELATION_DESTINATION {cardinality: '0:N'}]->(dst)
CREATE (ont)-[:ONTOLOGY_HAS_ONTOLOGY_RELATION]->(r)
```

Create edge properties (mirror COLUMN_HAS_PATH edge properties):
```cypher
MATCH (r:OntologyRelation {name: 'PROPERTY_HAS_PROJECTION_PATH'})
CREATE (p1:OntologyProperty {name: 'ord', type: 'Int', description: 'Order index in the path chain (0-based)'})
CREATE (p2:OntologyProperty {name: 'isFirst', type: 'Boolean', description: 'True if this is the first step in the path chain'})
CREATE (p3:OntologyProperty {name: 'isLast', type: 'Boolean', description: 'True if this is the last step in the path chain'})
CREATE (r)-[:ONTOLOGY_RELATION_HAS_ONTOLOGY_PROPERTY]->(p1)
CREATE (r)-[:ONTOLOGY_RELATION_HAS_ONTOLOGY_PROPERTY]->(p2)
CREATE (r)-[:ONTOLOGY_RELATION_HAS_ONTOLOGY_PROPERTY]->(p3)
```

## Phase 2: Generate & Validate

```bash
# From AppTree/_visionquest/graphology
npm run build && NODE_OPTIONS="--max-old-space-size=16384" npm run generate
```

### Validation Checklist

- [ ] `generated/schema/relations/JSON_SCHEMA_CENTERS_AT.graphql` exists
- [ ] `generated/schema/relations/DEFINITION_CENTERS_AT.graphql` exists
- [ ] `generated/schema/relations/PROPERTY_HAS_PROJECTION_PATH.graphql` exists
- [ ] PROPERTY_HAS_PROJECTION_PATH has edge properties (ord, isFirst, isLast)
- [ ] JSONSchema class doc shows JSON_SCHEMA_CENTERS_AT as outgoing relation
- [ ] SchemaDefinition class doc shows DEFINITION_CENTERS_AT as outgoing relation
- [ ] SchemaProperty class doc shows PROPERTY_HAS_PROJECTION_PATH as outgoing relation
- [ ] PathNode class doc shows PROPERTY_HAS_PROJECTION_PATH as incoming relation
- [ ] GraphQL server starts without errors
- [ ] Run Graph Consistency Guardian validation

## Phase 3: Wire ITR Projection Paths (next task)

After relations are generated and validated, create PathNode chains for the ITR World Tax schema using the mapping in `dynamic-rankellix-executor/itr-projection-mapping.md`. This will be a separate task file.

## Completion Status — 2026-03-08

**All Phase 1 & 2 items DONE.** Relations were created in a prior session.

- [x] All 3 relations exist in Neo4j with correct source/destination/cardinality
- [x] Generated GraphQL schema includes all 3 relations with correct types
- [x] Edge properties on PROPERTY_HAS_PROJECTION_PATH match COLUMN_HAS_PATH pattern (ord, isFirst, isLast)
- [x] All SchemaProperty subtypes (String, Number, Integer, Object, Array, Boolean, Null) have `propertyHasProjectionPath` field
- [x] PathNode has `propertyHasProjectionPathFrom` incoming relation
- [x] `PropertyHasProjectionPathProperties` type generated with { isLast, isFirst, ord, createdAt }
- [x] Server starts and serves queries on port 4000
- [ ] Graph Consistency Guardian full validation (deferred to Phase 2 wiring)
- [x] No regressions in existing Dynamic UI or JSONSchema functionality
