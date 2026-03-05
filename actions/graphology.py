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
  workflows(where: { id: $workflowId }) {
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
        hasVersion(where: { status: "active" }) {
          id
          versionNumber
          status
          content
        }
      }
    }
  }
}
"""

CREATE_PROMPT_EXECUTION_MUTATION = """
mutation CreatePromptExecution($input: [PromptExecutionCreateInput!]!) {
  createPromptExecutions(input: $input) {
    promptExecutions {
      id
    }
  }
}
"""


# =============================================================================
# HELPERS
# =============================================================================

def _get_graphql_url(kwargs: dict, state: dict = None) -> str:
    """Resolve the GraphQL endpoint URL from kwargs, state variables, or env."""
    return (
        kwargs.get("graphql_url")
        or (state.get("variables", {}).get("GRAPHOLOGY_URL") if state else None)
        or os.environ.get("GRAPHOLOGY_URL")
        or DEFAULT_GRAPHOLOGY_URL
    )


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
    workflows = workflow_data.get("workflows", [])

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


def save_workflow_responses(
    state: Dict[str, Any],
    workflow_id: str,
    matter_id: str,
    responses: List[Dict[str, Any]],
    **kwargs,
) -> Dict[str, Any]:
    """
    Save LLM responses as PromptExecution nodes in graphology.

    TEA Custom Action: graphology.save_responses

    Each response dict must contain:
        - versionId: PromptVersion ID to link execution to
        - llmRequest: The request sent to the LLM
        - llmResponse: The LLM's response

    Args:
        state: Current agent state
        workflow_id: Workflow ID (for tracking)
        matter_id: Client/matter ID (stored as clientId)
        responses: List of response dicts
        graphql_url: (optional kwarg) GraphQL endpoint URL

    Returns:
        Dict with success, executionIds list, count
    """
    logger.info(
        f"graphology.save_responses: workflow_id={workflow_id}, "
        f"matter_id={matter_id}, response_count={len(responses) if responses else 0}"
    )

    if not workflow_id:
        return {"success": False, "error": "workflow_id is required"}
    if not matter_id:
        return {"success": False, "error": "matter_id is required"}
    if not responses:
        return {"success": False, "error": "responses list is required and must not be empty"}

    url = _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    # Build all execution inputs upfront, validating before any network call
    execution_inputs = []
    for i, resp in enumerate(responses):
        version_id = resp.get("versionId")
        llm_request = resp.get("llmRequest", "")
        llm_response = resp.get("llmResponse", "")

        if not version_id:
            return {
                "success": False,
                "error": f"Response at index {i} must include versionId",
                "executionIds": [],
                "saved_count": 0,
            }

        execution_status = resp.get("status", "completed")
        resp_error = resp.get("error", "")
        llm_resp_str = llm_response if isinstance(llm_response, str) else json.dumps(llm_response)

        # For failed executions, store the error in llmResponse and metadata
        # so it's visible in the graph (PromptExecution has no error field).
        if execution_status == "failed" and not llm_resp_str:
            llm_resp_str = json.dumps({"error": resp_error or "Unknown error"})
        metadata = json.dumps({"error": resp_error}) if resp_error else None

        # Separate context from the question to keep llmRequest lightweight.
        # The full document context goes into ContextNode only.
        if isinstance(llm_request, dict):
            context_content = llm_request.pop("context", "")
            llm_req_str = json.dumps(llm_request)
        else:
            context_content = ""
            llm_req_str = llm_request if isinstance(llm_request, str) else json.dumps(llm_request)

        # ContextNode stores the document context; llmRequest stores the question
        context_node_content = context_content if context_content else llm_req_str

        exec_input = {
            "clientId": matter_id,
            "status": execution_status,
            "llmRequest": llm_req_str,
            "llmResponse": llm_resp_str,
            "hasContext": {
                "create": [{
                    "node": {
                        "content": context_node_content,
                        "contextType": "DOCUMENT",
                    }
                }]
            },
            "hasResponse": {
                "create": [{
                    "node": {
                        "responseData": llm_resp_str,
                    }
                }]
            },
            "hasExecutionFrom": {
                "connect": [{
                    "where": {
                        "node": {"id": version_id}
                    }
                }]
            },
        }
        if metadata:
            exec_input["metadata"] = metadata
        execution_inputs.append(exec_input)

    # Batched GraphQL mutations — chunk to avoid 413 Payload Too Large
    # Use smaller batches when ContextNode contains large documents
    max_ctx = max(
        (len(e.get("hasContext", {}).get("create", [{}])[0].get("node", {}).get("content", ""))
         for e in execution_inputs),
        default=0,
    )
    BATCH_SIZE = 1 if max_ctx > 5000 else 10
    execution_ids = []
    for batch_start in range(0, len(execution_inputs), BATCH_SIZE):
        batch = execution_inputs[batch_start:batch_start + BATCH_SIZE]
        try:
            data = _execute_graphql(
                url,
                CREATE_PROMPT_EXECUTION_MUTATION,
                {"input": batch},
                api_key=api_key,
            )
        except (ConnectionError, RuntimeError) as e:
            logger.error("graphology.save_responses batch failed: %s", e)
            return {
                "success": False,
                "error": str(e),
                "executionIds": execution_ids,
                "saved_count": len(execution_ids),
            }

        batch_ids = [
            ex.get("id")
            for ex in data.get("createPromptExecutions", {}).get("promptExecutions", [])
        ]
        execution_ids.extend(batch_ids)

    logger.info(
        "graphology.save_responses: Created %d executions for matter %s",
        len(execution_ids), matter_id,
    )

    return {
        "success": True,
        "executionIds": execution_ids,
        "count": len(execution_ids),
        "workflow_id": workflow_id,
        "matter_id": matter_id,
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
          ofType {
            name
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
        type_info = field.get("type", {})
        # Type name can be directly on type or wrapped in ofType (for [Type!]!)
        type_name = type_info.get("name")
        if not type_name:
            of_type = type_info.get("ofType") or {}
            type_name = of_type.get("name")
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
        query = f'query FindNode($id: ID!) {{ {query_field}(where: {{id: $id}}) {{ id }} }}'
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
        full_query = f'query GetNode($id: ID!) {{ {query_field}(where: {{id: $id}}) {{ {fields_str} }} }}'
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
    registry["graphology.save_responses"] = save_workflow_responses
    registry["graphology.collect_answers"] = collect_parallel_answers
    registry["graphology.get_node"] = get_node

    logger.info(
        "Graphology actions registered: "
        "graphology.get_questions, graphology.save_responses, "
        "graphology.collect_answers, graphology.get_node"
    )
