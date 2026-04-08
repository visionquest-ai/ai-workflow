"""
Graphology Custom Actions for TEA YAMLEngine.

Story: 15.1 - Graphology Custom Action for TEA
Provides graphology.get_questions and graphology.save_responses actions
for YAML agents to communicate with graphology's Apollo Server via GraphQL.

Actions:
- graphology.get_questions: Query Workflow->Steps->Prompts->PromptVersions (active)
- graphology.save_responses: Create PromptExecution + ContextNode + PromptResponse
"""

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List

import requests

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

DEFAULT_GRAPHOLOGY_URL = "http://localhost:4000"

GRAPHQL_TIMEOUT_SECONDS = 30

def _get_graphql_api_key(kwargs: dict, state: dict = None) -> str | None:
    """Resolve the GraphQL API key from kwargs, state variables, or env."""
    return (
        kwargs.get("graphql_api_key")
        or (state.get("variables", {}).get("GRAPHOLOGY_API_KEY") if state else None)
        or os.environ.get("GRAPHOLOGY_API_KEY")
    )

# =============================================================================
# GraphQL OPERATIONS
# =============================================================================

GET_WORKFLOW_QUESTIONS_QUERY = """
query GetWorkflowQuestions($workflowId: ID!) {
  workflow(where: { id_EQ: $workflowId }) {
    id
    name
    hasStep {
      id
      name
      order
      stepType
      hasPrompt {
        id
        name
        description
        hasVersion(where: { status_EQ: "active" }) {
          id
          versionNumber
          status
          content
          systemPrompt
        }
      }
    }
  }
}
"""

# =============================================================================
# HELPERS
# =============================================================================

def _get_graphql_url(kwargs: dict, state: dict = None) -> str:
    """Resolve the GraphQL endpoint URL from kwargs, state variables, or env.
    Ensures the URL ends with /graphql (Apollo Server serves GraphQL at this path)."""
    url = (
        kwargs.get("graphql_url")
        or (state.get("variables", {}).get("GRAPHOLOGY_URL") if state else None)
        or os.environ.get("GRAPHOLOGY_URL")
        or DEFAULT_GRAPHOLOGY_URL
    )
    if not url.endswith("/graphql"):
        url = url.rstrip("/") + "/graphql"
    return url


def _execute_graphql(url: str, query: str, variables: dict = None, api_key: str = None) -> dict:
    """
    Execute a GraphQL operation against the graphology Apollo Server.

    Args:
        url: GraphQL endpoint URL
        query: GraphQL query/mutation string
        variables: Optional variables dict
        api_key: Optional API key for x-api-key header

    Returns:
        Parsed JSON response

    Raises:
        ConnectionError: If the server is unreachable
        RuntimeError: If the response contains GraphQL errors
    """
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=GRAPHQL_TIMEOUT_SECONDS,
            headers=headers,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(
            f"Cannot connect to graphology Apollo Server at {url}. "
            f"Ensure the server is running (npm run server). Details: {e}"
        ) from e
    except requests.exceptions.Timeout as e:
        raise ConnectionError(
            f"Timeout connecting to graphology Apollo Server at {url} "
            f"after {GRAPHQL_TIMEOUT_SECONDS}s. Details: {e}"
        ) from e
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(
            f"HTTP error from graphology Apollo Server at {url}: "
            f"{response.status_code} {response.text}"
        ) from e

    try:
        result = response.json()
    except (ValueError, requests.exceptions.JSONDecodeError) as e:
        raise RuntimeError(
            f"Invalid JSON response from graphology Apollo Server at {url}: "
            f"{response.text[:200]}"
        ) from e

    if "errors" in result:
        error_messages = "; ".join(e.get("message", str(e)) for e in result["errors"])
        raise RuntimeError(f"GraphQL errors: {error_messages}")

    return result.get("data", {})


def _flatten_questions(workflow_data: dict) -> List[dict]:
    """
    Flatten the nested Workflow->Steps->Prompts->PromptVersions into a flat
    list suitable for dynamic_parallel item iteration.

    Each item includes: promptId, name, versionId, versionNumber, content,
    stepName, stepOrder.
    """
    questions = []
    workflows = workflow_data.get("workflow", [])

    if not workflows:
        return questions

    workflow = workflows[0]
    steps = workflow.get("hasStep", [])

    # Sort steps by order
    sorted_steps = sorted(steps, key=lambda s: s.get("order") or 0)

    for step in sorted_steps:
        step_name = step.get("name", "")
        step_order = step.get("order", 0)

        for prompt in step.get("hasPrompt", []):
            for version in prompt.get("hasVersion", []):
                questions.append({
                    "promptId": prompt.get("id"),
                    "name": prompt.get("name", ""),
                    "description": prompt.get("description", ""),
                    "versionId": version.get("id"),
                    "versionNumber": version.get("versionNumber"),
                    "content": version.get("content", ""),
                    "systemPrompt": version.get("systemPrompt", ""),
                    "stepName": step_name,
                    "stepOrder": step_order,
                })

    return questions


# =============================================================================
# TEA CUSTOM ACTIONS
# =============================================================================

def get_workflow_questions(
    state: Dict[str, Any],
    workflow_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """
    Query graphology for workflow questions (active PromptVersions).

    TEA Custom Action: graphology.get_questions

    Args:
        state: Current agent state
        workflow_id: ID of the Workflow to retrieve questions from
        graphql_url: (optional kwarg) GraphQL endpoint URL

    Returns:
        Dict with success, questions list (flat for dynamic_parallel), count
    """
    logger.info(f"graphology.get_questions: workflow_id={workflow_id}")

    if not workflow_id:
        return {"success": False, "error": "workflow_id is required"}

    url = _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    try:
        data = _execute_graphql(
            url, GET_WORKFLOW_QUESTIONS_QUERY, {"workflowId": workflow_id},
            api_key=api_key,
        )
    except (ConnectionError, RuntimeError) as e:
        logger.error(f"graphology.get_questions failed: {e}")
        return {"success": False, "error": str(e)}

    questions = _flatten_questions(data)

    logger.info(
        f"graphology.get_questions: Retrieved {len(questions)} questions "
        f"from workflow {workflow_id}"
    )

    return {
        "success": True,
        "questions": questions,
        "count": len(questions),
        "workflow_id": workflow_id,
    }


def collect_parallel_answers(
    state: Dict[str, Any],
    **kwargs,
) -> Dict[str, Any]:
    """
    Collect parallel LLM results from dynamic_parallel fan-in and map them
    to the response format expected by graphology.save_responses.

    TEA Custom Action: graphology.collect_answers

    Reads state.parallel_results (set by dynamic_parallel node) and builds
    a list of response dicts with versionId, llmRequest, llmResponse, status.

    Args:
        state: Current agent state (must contain parallel_results from fan-in)

    Returns:
        Dict with answers list ready for save_responses
    """
    parallel_results = state.get("parallel_results", [])
    answers = []

    for result in parallel_results:
        # ParallelFlowResult is a dict with: branch, success, state, error, timing_ms
        if isinstance(result, dict):
            success = result.get("success", False)
            branch_state = result.get("state", {})
            error_msg = result.get("error", "")
        else:
            # Handle ParallelFlowResult dataclass
            success = getattr(result, "success", False)
            branch_state = getattr(result, "state", {})
            error_msg = getattr(result, "error", "")

        # Extract the prompt item from branch state (injected by dynamic_parallel)
        prompt = branch_state.get("prompt", {})
        version_id = prompt.get("versionId", "")
        content = prompt.get("content", "")

        # Build structured llmRequest with question + matter context
        # matter_context lives in parent state (not branch state) to avoid
        # duplicating the full document 39× in ParallelFlowResult objects.
        matter_context = state.get("matter_context", "")
        llm_request = {
            "text": content,
            "promptName": prompt.get("name", ""),
            "stepName": prompt.get("stepName", ""),
        }
        if matter_context:
            llm_request["context"] = matter_context

        # Extract LLM result from branch state.
        # When wrapped with ratelimit.wrap, the result is nested:
        #   llm_result = {"success": True, "result": {"content": "..."}, ...}
        # When using llm.call directly:
        #   llm_result = {"content": "..."}
        #
        # Error handling: there are TWO layers of success/failure:
        #   1. ParallelFlowResult.success — did the branch execute without throwing?
        #   2. llm_result.success — did the LLM call actually return a valid response?
        # Both must be True for status="completed". If the LLM call fails (401,
        # rate-limit, etc.), ratelimit.wrap returns {"success": False, "error": "..."}
        # but ParallelFlowResult.success is still True (the action didn't throw).
        llm_result = branch_state.get("llm_result", {})
        llm_response = ""
        llm_error = ""

        if isinstance(llm_result, dict):
            # Check ratelimit.wrap success flag (layer 2)
            llm_success = llm_result.get("success", True)
            if not llm_success:
                llm_error = llm_result.get("error", "LLM call failed (unknown reason)")
                success = False
            else:
                # Unwrap ratelimit.wrap envelope if present
                inner = llm_result.get("result", llm_result)
                if isinstance(inner, dict):
                    llm_response = inner.get("content", inner.get("text", ""))
                elif isinstance(inner, str):
                    llm_response = inner
        elif isinstance(llm_result, str):
            llm_response = llm_result

        # Final guard: if extraction produced empty response, mark as failed
        if success and not llm_response:
            success = False
            llm_error = llm_error or "LLM returned empty response"

        answer = {
            "versionId": version_id,
            "llmRequest": llm_request,
            "llmResponse": llm_response,
            "status": "completed" if success else "failed",
        }
        if not success:
            answer["error"] = error_msg or llm_error

        answers.append(answer)

    logger.info(
        f"graphology.collect_answers: Collected {len(answers)} answers "
        f"({sum(1 for a in answers if a['status'] == 'completed')} completed, "
        f"{sum(1 for a in answers if a['status'] == 'failed')} failed)"
    )

    return {"answers": answers, "answer_count": len(answers)}


# =============================================================================
# SCHEMA INTROSPECTION CACHE (Story 16.1, subtask 1.6)
# =============================================================================

_SCHEMA_CACHE_TTL_SECONDS = 300  # 5 minutes

_schema_cache: Dict[str, Any] = {}
_schema_cache_ts: Dict[str, float] = {}


def _cache_get(key: str) -> Any:
    """Get a cached value if it exists and hasn't expired."""
    if key in _schema_cache:
        if time.monotonic() - _schema_cache_ts.get(key, 0) < _SCHEMA_CACHE_TTL_SECONDS:
            return _schema_cache[key]
        del _schema_cache[key]
        _schema_cache_ts.pop(key, None)
    return None


def _cache_set(key: str, value: Any) -> None:
    """Set a cached value with current timestamp."""
    _schema_cache[key] = value
    _schema_cache_ts[key] = time.monotonic()


# =============================================================================
# SCHEMA INTROSPECTION QUERIES (Story 16.1, subtask 1.1)
# =============================================================================

INTROSPECT_ROOT_QUERY_FIELDS = """
query IntrospectRootQueryFields {
  __schema {
    queryType {
      fields {
        name
        type {
          name
          kind
          ofType {
            name
            kind
            ofType {
              name
              kind
              ofType {
                name
                kind
              }
            }
          }
        }
      }
    }
  }
}
"""

INTROSPECT_TYPE_FIELDS = """
query IntrospectTypeFields($typeName: String!) {
  __type(name: $typeName) {
    fields {
      name
      type {
        kind
        name
        ofType {
          kind
          name
          ofType {
            kind
            name
          }
        }
      }
    }
  }
}
"""


# =============================================================================
# NODE INTROSPECTION HELPERS (Story 16.1, subtasks 1.1-1.3)
# =============================================================================

def _get_root_query_fields(url: str, api_key: str = None) -> Dict[str, str]:
    """
    Introspect the GraphQL schema to get all root query field names
    and their corresponding type names.

    Returns:
        Dict mapping query field name to type name, e.g.:
        {"submissions": "Submission", "matters": "Matter"}
    """
    data = _execute_graphql(url, INTROSPECT_ROOT_QUERY_FIELDS, api_key=api_key)

    fields = data.get("__schema", {}).get("queryType", {}).get("fields", [])
    result = {}
    for field in fields:
        field_name = field.get("name", "")
        # Unwrap NON_NULL/LIST wrappers to find the actual type name
        # e.g., NON_NULL(LIST(NON_NULL(Workflow))) → "Workflow"
        type_info = field.get("type", {})
        type_name = type_info.get("name")
        while not type_name and type_info.get("ofType"):
            type_info = type_info["ofType"]
            type_name = type_info.get("name")
        if type_name and not field_name.startswith("__"):
            result[field_name] = type_name
    return result


def _get_type_scalar_fields(url: str, type_name: str, api_key: str = None) -> List[str]:
    """
    Introspect a GraphQL type to get its scalar field names only.
    Excludes relation fields (OBJECT) and list fields (LIST).

    Args:
        url: GraphQL endpoint URL
        type_name: The type to introspect (e.g., "Submission")

    Returns:
        List of scalar field names
    """
    data = _execute_graphql(
        url, INTROSPECT_TYPE_FIELDS, {"typeName": type_name}, api_key=api_key,
    )

    type_info = data.get("__type")
    if not type_info:
        return []

    scalar_fields = []
    for field in type_info.get("fields", []):
        field_type = field.get("type", {})
        kind = field_type.get("kind", "")

        # Direct scalar
        if kind == "SCALAR":
            scalar_fields.append(field["name"])
        # NON_NULL wrapped scalar
        elif kind == "NON_NULL":
            inner = field_type.get("ofType", {})
            if inner.get("kind") == "SCALAR":
                scalar_fields.append(field["name"])
        # Skip OBJECT, LIST, and other complex types

    return scalar_fields


def _find_node_type(
    url: str, node_id: str, query_fields: Dict[str, str], api_key: str = None
) -> tuple:
    """
    Discover the type of a node by trying each root query field.

    Args:
        url: GraphQL endpoint URL
        node_id: The node ID to find
        query_fields: Dict mapping query field name to type name

    Returns:
        Tuple of (type_name, query_field_name) or (None, None) if not found
    """
    # Check priority types first to avoid brute-forcing all ~250 types
    PRIORITY_TYPES = [
        "ApplicationFormFile",
        "Submission", "Matter", "Company", "Workflow", "Prompt",
        "PromptExecution", "PromptVersion", "Step", "PromptOutput",
    ]
    priority_items = []
    remaining_items = []
    for query_field, type_name in query_fields.items():
        if type_name in PRIORITY_TYPES:
            priority_items.append((query_field, type_name))
        else:
            remaining_items.append((query_field, type_name))

    for query_field, type_name in priority_items + remaining_items:
        query = f'query FindNode($id: ID!) {{ {query_field}(where: {{id_EQ: $id}}) {{ id }} }}'
        try:
            data = _execute_graphql(url, query, {"id": node_id}, api_key=api_key)
        except (ConnectionError, RuntimeError):
            # Skip types that don't support id filtering
            continue

        results = data.get(query_field, [])
        if results:
            return (type_name, query_field)

    return (None, None)


# =============================================================================
# get_node ACTION (Story 16.1, subtask 1.4)
# =============================================================================

def get_node(
    state: Dict[str, Any],
    node_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """
    Fetch any graph node by ID via schema introspection.

    TEA Custom Action: graphology.get_node

    Discovers the node's type via introspection, queries all scalar fields,
    and returns the node data. No Cypher needed — uses GraphQL only.

    Args:
        state: Current agent state
        node_id: ID of the node to fetch
        graphql_url: (optional kwarg) GraphQL endpoint URL

    Returns:
        Dict with success, node_type, data (scalar fields)
    """
    logger.info(f"graphology.get_node: node_id={node_id}")

    if not node_id:
        return {"success": False, "error": "node_id is required"}

    url = _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    try:
        # Step 1: Get root query fields (cached with TTL)
        cache_key = f"root_query_fields:{url}"
        query_fields = _cache_get(cache_key)
        if query_fields is None:
            query_fields = _get_root_query_fields(url, api_key=api_key)
            _cache_set(cache_key, query_fields)

        # Step 2: Find the node's type
        type_name, query_field = _find_node_type(url, node_id, query_fields, api_key=api_key)

        if not type_name:
            return {"success": False, "error": f"Node not found: {node_id}"}

        # Step 3: Get scalar fields for the type (cached with TTL)
        fields_cache_key = f"type_fields:{url}:{type_name}"
        scalar_fields = _cache_get(fields_cache_key)
        if scalar_fields is None:
            scalar_fields = _get_type_scalar_fields(url, type_name, api_key=api_key)
            _cache_set(fields_cache_key, scalar_fields)

        # Step 4: Query the full node
        fields_str = " ".join(scalar_fields)
        full_query = f'query GetNode($id: ID!) {{ {query_field}(where: {{id_EQ: $id}}) {{ {fields_str} }} }}'
        data = _execute_graphql(url, full_query, {"id": node_id}, api_key=api_key)

        results = data.get(query_field, [])
        if not results:
            return {"success": False, "error": f"Node not found: {node_id}"}

        node_data = results[0]

        logger.info(
            f"graphology.get_node: Found {type_name} node with "
            f"{len(scalar_fields)} scalar fields"
        )

        return {
            "success": True,
            "node_type": type_name,
            "data": node_data,
            "data_json": json.dumps(node_data),
        }

    except (ConnectionError, RuntimeError) as e:
        logger.error(f"graphology.get_node failed: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# update_node ACTION
# =============================================================================

def _pluralize(name: str) -> str:
    """Simple pluralization for GraphQL type names."""
    if name.endswith("s"):
        return name + "es"
    if name.endswith("y") and not name.endswith(("ay", "ey", "oy", "uy")):
        return name[:-1] + "ies"
    return name + "s"


def update_node(
    state: Dict[str, Any],
    node_id: str,
    node_type: str,
    updates: Dict[str, Any],
    **kwargs,
) -> Dict[str, Any]:
    """
    Update scalar fields on a graph node by ID via GraphQL mutation.

    TEA Custom Action: graphology.update_node

    Uses the Neo4j GraphQL convention: update<PluralType>(where: {id: $id},
    update: {field_SET: value}) to update scalar fields.

    Args:
        state: Current agent state
        node_id: ID of the node to update
        node_type: GraphQL type name (e.g. "ApplicationFormFile")
        updates: Dict of field names to new values (e.g. {"payload": "..."})
        graphql_url: (optional kwarg) GraphQL endpoint URL

    Returns:
        Dict with success, updated field count
    """
    logger.info(f"graphology.update_node: node_id={node_id}, type={node_type}, fields={list(updates.keys())}")

    if not node_id:
        return {"success": False, "error": "node_id is required"}
    if not node_type:
        return {"success": False, "error": "node_type is required"}
    if not updates:
        return {"success": False, "error": "updates dict is required and must not be empty"}

    url = _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    # Build _SET update fields
    update_fields = {}
    for field_name, value in updates.items():
        update_fields[f"{field_name}_SET"] = value

    # Build mutation — Neo4j GraphQL Library v6 uses singular type names
    mutation_name = f"update{node_type}"
    # Return fields: the updated scalar fields we set
    return_fields = " ".join(updates.keys()) + " id"

    # Result field accessor: lowercase first char of type name (e.g. ApplicationFormFile → applicationFormFile)
    result_field = node_type[0].lower() + node_type[1:]

    mutation = f"""
    mutation UpdateNode($where: {node_type}Where!, $update: {node_type}UpdateInput!) {{
      {mutation_name}(where: $where, update: $update) {{
        {result_field} {{ {return_fields} }}
      }}
    }}
    """

    variables = {
        "where": {"id_EQ": node_id},
        "update": update_fields,
    }

    try:
        data = _execute_graphql(url, mutation, variables, api_key=api_key)
    except (ConnectionError, RuntimeError) as e:
        logger.error(f"graphology.update_node failed: {e}")
        return {"success": False, "error": str(e)}

    results = data.get(mutation_name, {}).get(result_field, [])
    if not results:
        return {"success": False, "error": f"No node returned after update: {node_id}"}

    logger.info(
        f"graphology.update_node: Updated {len(updates)} field(s) on "
        f"{node_type} {node_id}"
    )

    return {
        "success": True,
        "node_id": node_id,
        "node_type": node_type,
        "updated_fields": list(updates.keys()),
        "data": results[0],
    }


# =============================================================================
# create_node ACTION (Story 16.4)
# =============================================================================

def create_node(
    state: Dict[str, Any],
    node_type: str,
    properties: Dict[str, Any],
    **kwargs,
) -> Dict[str, Any]:
    """
    Create a new graph node via GraphQL mutation.

    TEA Custom Action: graphology.create_node

    Uses Neo4j GraphQL Library v6 convention: create<SingularType>(input: {...}).

    Args:
        state: Current agent state
        node_type: GraphQL type name (e.g. "ProtoMatter")
        properties: Dict of property names to values
        graphql_url: (optional kwarg) GraphQL endpoint URL

    Returns:
        Dict with success, node_id
    """
    logger.info(f"graphology.create_node: type={node_type}, properties={list(properties.keys())}")

    if not node_type:
        return {"success": False, "error": "node_type is required"}
    if not properties:
        return {"success": False, "error": "properties dict is required and must not be empty"}

    url = _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    # Build mutation — Neo4j GraphQL Library v6 uses singular mutation name
    # but still takes list input: createProtoMatter(input: [ProtoMatterCreateInput!]!)
    # and returns singular field name with list: { protoMatter: [...] }
    mutation_name = f"create{node_type}"
    # Result field accessor: lowercase first char, singular (e.g. ProtoMatter → protoMatter)
    result_field = node_type[0].lower() + node_type[1:]

    mutation = f"""
    mutation CreateNode($input: [{node_type}CreateInput!]!) {{
      {mutation_name}(input: $input) {{
        {result_field} {{ id }}
      }}
    }}
    """

    variables = {"input": [properties]}

    try:
        data = _execute_graphql(url, mutation, variables, api_key=api_key)
    except (ConnectionError, RuntimeError) as e:
        logger.error(f"graphology.create_node failed: {e}")
        return {"success": False, "error": str(e)}

    results_list = data.get(mutation_name, {}).get(result_field, [])
    if not results_list:
        return {"success": False, "error": f"No node returned after create {node_type}"}

    node_id = results_list[0].get("id", "") if results_list else ""

    logger.info(f"graphology.create_node: Created {node_type} node {node_id}")

    return {
        "success": True,
        "node_id": node_id,
    }


# =============================================================================
# connect_nodes ACTION (Story 16.4)
# =============================================================================

def _relationship_to_field_name(relationship_name: str) -> str:
    """Convert UPPER_SNAKE_CASE relationship name to camelCase field name.

    Neo4j GraphQL Library v6 generates field names by converting
    UPPER_SNAKE_CASE to camelCase. E.g.:
        FILE_HAS_PROTO_MATTER → fileHasProtoMatter
        DEPARTMENT_HAS_PROTO_MATTER → departmentHasProtoMatter
    """
    parts = relationship_name.lower().split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def connect_nodes(
    state: Dict[str, Any],
    source_id: str,
    source_type: str,
    relationship_name: str,
    target_id: str,
    target_type: str,
    **kwargs,
) -> Dict[str, Any]:
    """
    Connect two nodes via a relationship using GraphQL update + connect.

    TEA Custom Action: graphology.connect_nodes

    Uses Neo4j GraphQL Library v6 convention:
        update<SourceType>(where: {id: ...}, update: {
            <relationshipField>: { connect: { where: { node: { id: ... }}}}
        })

    Args:
        state: Current agent state
        source_id: ID of the source node
        source_type: GraphQL type name of source (e.g. "ApplicationFormFile")
        relationship_name: UPPER_SNAKE_CASE relationship name (e.g. "FILE_HAS_PROTO_MATTER")
        target_id: ID of the target node
        target_type: GraphQL type name of target (e.g. "ProtoMatter")
        graphql_url: (optional kwarg) GraphQL endpoint URL

    Returns:
        Dict with success
    """
    logger.info(
        f"graphology.connect_nodes: {source_type}({source_id}) "
        f"-[{relationship_name}]-> {target_type}({target_id})"
    )

    if not source_id:
        return {"success": False, "error": "source_id is required"}
    if not source_type:
        return {"success": False, "error": "source_type is required"}
    if not relationship_name:
        return {"success": False, "error": "relationship_name is required"}
    if not target_id:
        return {"success": False, "error": "target_id is required"}
    if not target_type:
        return {"success": False, "error": "target_type is required"}

    url = _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    # Derive camelCase field name from UPPER_SNAKE_CASE relationship
    rel_field = _relationship_to_field_name(relationship_name)

    # Build mutation — singular type name (v6)
    mutation_name = f"update{source_type}"
    result_field = source_type[0].lower() + source_type[1:]

    mutation = f"""
    mutation ConnectNodes($where: {source_type}Where!, $update: {source_type}UpdateInput!) {{
      {mutation_name}(where: $where, update: $update) {{
        {result_field} {{ id }}
      }}
    }}
    """

    variables = {
        "where": {"id_EQ": source_id},
        "update": {
            rel_field: [
                {
                    "connect": [
                        {
                            "where": {
                                "node": {"id_EQ": target_id}
                            }
                        }
                    ]
                }
            ]
        },
    }

    try:
        data = _execute_graphql(url, mutation, variables, api_key=api_key)
    except (ConnectionError, RuntimeError) as e:
        logger.error(f"graphology.connect_nodes failed: {e}")
        return {"success": False, "error": str(e)}

    results = data.get(mutation_name, {}).get(result_field, [])
    if not results:
        return {"success": False, "error": f"No node returned after connect: {source_id}"}

    logger.info(
        f"graphology.connect_nodes: Connected {source_type}({source_id}) "
        f"-[{relationship_name}]-> {target_type}({target_id})"
    )

    return {"success": True}


# =============================================================================
# MATTER CONTEXT ACTION (Story MA-3a)
# =============================================================================

# Map directory codes to their MatterDetail subtype names in the GraphQL schema.
DIRECTORY_CODE_TO_MATTER_DETAIL_TYPE: Dict[str, str] = {
    "CH": "ChambersMatterDetail",
    "L500": "Legal500MatterDetail",
    "LL": "LeadersLeagueMatterDetail",
    "ITR": "ItrMatterDetail",
    "IFLR": "Iflr1000MatterDetail",
}


def _build_matter_context_query(detail_type: str, detail_scalar_fields: List[str]) -> str:
    """
    Build a GraphQL query that fetches Matter + MatterDetail + Client + Department.

    Uses an inline fragment for the specific MatterDetail subtype so only
    the fields relevant to that directory are returned.

    Args:
        detail_type: MatterDetail subtype name (e.g. "ChambersMatterDetail")
        detail_scalar_fields: Scalar field names for the detail subtype

    Returns:
        GraphQL query string
    """
    detail_fields_str = " ".join(detail_scalar_fields) if detail_scalar_fields else "id"

    return f"""
    query GetMatterContext($matterId: ID!, $detailId: ID!) {{
      matter(where: {{ id_EQ: $matterId }}) {{
        id
        mTitle
        mStatus
        mValue
        isCrossBorder
        isConfidential
        dateClosed
        dateOpened
        firmRole
        jurisdiction
        industrySector
        matterSubCategory
        matterHasMatterDetail(where: {{ id_EQ: $detailId }}) {{
          ... on {detail_type} {{ {detail_fields_str} }}
        }}
        matterHasClient {{
          id
          name
          industry
        }}
        departmentHasMatterFrom {{
          id
          deptName
          legalFirmHasDepartmentFrom {{
            id
            firmName
          }}
        }}
      }}
    }}
    """


def get_matter_context(
    state: Dict[str, Any],
    matter_id: str,
    matter_detail_id: str,
    directory_code: str,
    **kwargs,
) -> Dict[str, Any]:
    """
    Fetch structured context from Matter + MatterDetail graph nodes.

    TEA Custom Action: graphology.get_matter_context

    Queries Matter scalar fields, the specific MatterDetail via inline fragment,
    Client context, and Department context. Returns a structured response
    suitable for analysis agents.

    Args:
        state: Current agent state
        matter_id: ID of the Matter node
        matter_detail_id: ID of the MatterDetail node
        directory_code: Directory code (CH, L500, LL, ITR, IFLR)
        graphql_url: (optional kwarg) GraphQL endpoint URL

    Returns:
        Dict with success, matter, detail, directory, client, department
    """
    logger.info(
        f"graphology.get_matter_context: matter_id={matter_id}, "
        f"detail_id={matter_detail_id}, directory={directory_code}"
    )

    if not matter_id:
        return {"success": False, "error": "matter_id is required"}
    if not matter_detail_id:
        return {"success": False, "error": "matter_detail_id is required"}
    if not directory_code:
        return {"success": False, "error": "directory_code is required"}

    detail_type = DIRECTORY_CODE_TO_MATTER_DETAIL_TYPE.get(directory_code)
    if not detail_type:
        valid_codes = ", ".join(sorted(DIRECTORY_CODE_TO_MATTER_DETAIL_TYPE.keys()))
        return {
            "success": False,
            "error": f"Invalid directory_code '{directory_code}'. "
                     f"Valid codes: {valid_codes}",
        }

    url = _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    try:
        # Introspect the detail type's scalar fields (cached with TTL)
        fields_cache_key = f"type_fields:{url}:{detail_type}"
        detail_scalar_fields = _cache_get(fields_cache_key)
        if detail_scalar_fields is None:
            detail_scalar_fields = _get_type_scalar_fields(url, detail_type, api_key=api_key)
            _cache_set(fields_cache_key, detail_scalar_fields)

        query = _build_matter_context_query(detail_type, detail_scalar_fields)
        data = _execute_graphql(
            url, query, {"matterId": matter_id, "detailId": matter_detail_id},
            api_key=api_key,
        )
    except (ConnectionError, RuntimeError) as e:
        logger.error(f"graphology.get_matter_context failed: {e}")
        return {"success": False, "error": str(e)}

    matters = data.get("matter", [])
    if not matters:
        return {"success": False, "error": f"Matter not found: {matter_id}"}

    matter_node = matters[0]

    # Extract related nodes from the Matter response
    details = matter_node.pop("matterHasMatterDetail", [])
    detail_node = details[0] if details else None

    clients = matter_node.pop("matterHasClient", [])
    client_node = clients[0] if clients else None

    departments = matter_node.pop("departmentHasMatterFrom", [])
    dept_node = departments[0] if departments else None

    logger.info(
        f"graphology.get_matter_context: Retrieved Matter context "
        f"(detail={'yes' if detail_node else 'no'}, "
        f"client={'yes' if client_node else 'no'}, "
        f"department={'yes' if dept_node else 'no'})"
    )

    result = {
        "success": True,
        "matter": matter_node,
        "detail": detail_node,
        "directory": directory_code,
        "client": client_node,
        "department": dept_node,
    }
    result["matter_context_json"] = json.dumps(result, default=str)

    return result


# =============================================================================
# ACTION REGISTRATION
# =============================================================================

def register_actions(registry: Dict[str, Callable], engine: Any) -> None:
    """
    Register graphology actions with the TEA YAMLEngine.

    Args:
        registry: Action registry dictionary
        engine: YAMLEngine instance
    """
    registry["graphology.get_questions"] = get_workflow_questions
    registry["graphology.collect_answers"] = collect_parallel_answers
    registry["graphology.get_node"] = get_node
    registry["graphology.update_node"] = update_node
    registry["graphology.create_node"] = create_node
    registry["graphology.connect_nodes"] = connect_nodes
    registry["graphology.get_matter_context"] = get_matter_context

    logger.info(
        "Graphology actions registered: "
        "graphology.get_questions, "
        "graphology.collect_answers, graphology.get_node, "
        "graphology.update_node, graphology.create_node, "
        "graphology.connect_nodes, graphology.get_matter_context"
    )
