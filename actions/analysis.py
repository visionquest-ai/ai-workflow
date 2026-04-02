"""
analysis.py — TEA Custom Actions for the Matter Analysis pipeline.

Stories RX.60.4 (query actions) and RX.60.5 (render_prompt, post_process, post_process_batch).

Actions registered:
  analysis.get_stage_config      — load full stage + directory profile from graph
  analysis.get_step_config       — load lightweight step config (Stage A)
  analysis.get_stage_a_results   — normalize Stage A PromptExecution results by criterion
  analysis.render_prompt         — fill template slots, render Jinja2 prompt
  analysis.post_process          — parse LLM JSON, import into graph via PathNode mutations
  analysis.post_process_batch    — Stage A variant: N PromptExecution nodes per answer list
"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

import requests

# Re-use the same GraphQL helper pattern from graphology.py
from graphology import _execute_graphql, _get_graphql_url, _get_graphql_api_key

logger = logging.getLogger(__name__)


# =============================================================================
# GraphQL QUERIES
# =============================================================================

GET_ANALYSIS_STAGE_QUERY = """
query GetAnalysisStage($workflowName: String!, $directoryCode: String!, $stageKey: String!) {
  analysisWorkflows(where: { name_EQ: $workflowName }) {
    hasStage(where: { stageKey_EQ: $stageKey }) {
      id stageKey stageOrder executionMode
      hasStep(orderBy: stepOrder_ASC) {
        id name stepOrder executionMode batchSize
        hasPromptTemplate {
          systemTemplate userTemplate
          hasSlot {
            slotName slotType staticValue
            hasPathNode { sourcePath transform targetSlot }
            hasProjection { id }
          }
        }
        hasResponseFormatConnection {
          edges {
            properties { connectStateKey connectRelation prevStepStateKey prevStepRelation }
            node { id jsonSchemaCentersAt { name } }
          }
        }
      }
    }
  }
  directories(where: { shortCode_EQ: $directoryCode }) {
    id name narrativeTone avoidPatterns
    hasCriterionWeightConnection {
      edges { properties { weightClass } node { code } }
    }
    hasGateRule {
      id ruleType minCount roleName
      gateIncludes { code }
    }
    hasUserSegment {
      segmentKey name description recommendation
      segmentHasPresent { code }
      segmentHasMissing { code }
    }
  }
}
"""

GET_STEP_CONFIG_QUERY = """
query GetStepConfig($stepName: String!, $workflowName: String!) {
  analysisWorkflows(where: { name_EQ: $workflowName }) {
    hasStage {
      hasStep(where: { name_EQ: $stepName }) {
        id name batchSize
        hasResponseFormatConnection {
          edges {
            properties { connectStateKey connectRelation prevStepStateKey prevStepRelation }
            node { id jsonSchemaCentersAt { name } }
          }
        }
      }
    }
  }
}
"""

GET_STAGE_A_RESULTS_QUERY = """
query GetStageAResults($matterId: ID!) {
  matters(where: { id_EQ: $matterId }) {
    matterHasStrategyConnection {
      edges { node { id } }
    }
    matterHasContextConnection(where: { node: {} }) {
      edges {
        node {
          ... on PromptExecution {
            id status llmResponse
            hasExecutionFrom {
              id
              mapsToCriterionConnection {
                edges {
                  properties { confidence }
                  node { code }
                }
              }
            }
            hasContext {
              ... on ContextNode { content }
            }
          }
        }
      }
    }
  }
}
"""

GET_RESPONSE_SCHEMA_PATH_NODES_QUERY = """
query GetResponseSchemaPathNodes($schemaId: ID!) {
  jsonSchemas(where: { id_EQ: $schemaId }) {
    jsonSchemaCentersAt { name }
    hasProperty {
      id name __typename
      propertyHasProjectionPathConnection {
        edges {
          properties { ord isFirst isLast jsonPath schemaId }
          node {
            id isEdgeProperty edgeFilter
            pathStepViaRelation { id name }
            pathStepAtClass { id name }
            pathStepToProperty { id name type }
          }
        }
      }
    }
  }
}
"""


# =============================================================================
# NORMALIZATION HELPERS
# =============================================================================

def _normalize_stage(stage_raw: dict) -> dict:
    """Flatten nested GraphQL stage + step structure into clean Python dicts."""
    steps = []
    for step in stage_raw.get("hasStep", []):
        template_raw = step.get("hasPromptTemplate") or {}
        slots = []
        for slot in template_raw.get("hasSlot", []):
            path_node_raw = slot.get("hasPathNode") or {}
            projection_raw = slot.get("hasProjection") or {}
            slots.append({
                "slotName": slot.get("slotName", ""),
                "slotType": slot.get("slotType", ""),
                "staticValue": slot.get("staticValue", ""),
                "pathNode": {
                    "sourcePath": path_node_raw.get("sourcePath", ""),
                    "transform": path_node_raw.get("transform", ""),
                    "targetSlot": path_node_raw.get("targetSlot", ""),
                } if path_node_raw else None,
                "projectionSchemaId": projection_raw.get("id") if projection_raw else None,
            })

        response_format = None
        rf_conn = step.get("hasResponseFormatConnection", {})
        rf_edges = rf_conn.get("edges", [])
        if rf_edges:
            edge = rf_edges[0]
            props = edge.get("properties", {})
            node = edge.get("node", {})
            center_types = node.get("jsonSchemaCentersAt", [])
            center_entity_type = center_types[0].get("name", "") if center_types else ""
            response_format = {
                "schemaId": node.get("id", ""),
                "centerEntityType": center_entity_type,
                "connectStateKey": props.get("connectStateKey", ""),
                "connectRelation": props.get("connectRelation", ""),
                "prevStepStateKey": props.get("prevStepStateKey") or None,
                "prevStepRelation": props.get("prevStepRelation") or None,
            }

        steps.append({
            "id": step.get("id", ""),
            "name": step.get("name", ""),
            "stepOrder": step.get("stepOrder", 0),
            "executionMode": step.get("executionMode", ""),
            "batchSize": step.get("batchSize", 10),
            "promptTemplate": {
                "systemTemplate": template_raw.get("systemTemplate", ""),
                "userTemplate": template_raw.get("userTemplate", ""),
                "slots": slots,
            },
            "responseFormat": response_format,
        })

    return {
        "stageKey": stage_raw.get("stageKey", ""),
        "executionMode": stage_raw.get("executionMode", ""),
        "steps": steps,
    }


def _normalize_directory(dir_raw: dict) -> dict:
    """Flatten directory profile from GraphQL response."""
    # criteria_by_weight: group criterion codes by weightClass
    criteria_by_weight: Dict[str, List[str]] = {
        "core": [],
        "amplifier": [],
        "support": [],
        "external": [],
    }
    for edge in dir_raw.get("hasCriterionWeightConnection", {}).get("edges", []):
        weight_class = edge.get("properties", {}).get("weightClass", "")
        code = edge.get("node", {}).get("code", "")
        if weight_class in criteria_by_weight:
            criteria_by_weight[weight_class].append(code)

    gate_rules = []
    for gr in dir_raw.get("hasGateRule", []):
        gate_rules.append({
            "id": gr.get("id", ""),
            "ruleType": gr.get("ruleType", ""),
            "minCount": gr.get("minCount", 1),
            "roleName": gr.get("roleName", ""),
            "gateIncludes": [{"code": c.get("code", "")} for c in gr.get("gateIncludes", [])],
        })

    user_segments = []
    for seg in dir_raw.get("hasUserSegment", []):
        user_segments.append({
            "segmentKey": seg.get("segmentKey", ""),
            "name": seg.get("name", ""),
            "description": seg.get("description", ""),
            "recommendation": seg.get("recommendation", ""),
            "present": [{"code": c.get("code", "")} for c in seg.get("segmentHasPresent", [])],
            "missing": [{"code": c.get("code", "")} for c in seg.get("segmentHasMissing", [])],
        })

    return {
        "name": dir_raw.get("name", ""),
        "narrativeTone": dir_raw.get("narrativeTone", ""),
        "avoidPatterns": dir_raw.get("avoidPatterns", ""),
        "criteria_by_weight": criteria_by_weight,
        "gate_rules": gate_rules,
        "user_segments": user_segments,
    }


# =============================================================================
# ACTION: get_stage_config
# =============================================================================

def get_stage_config(
    state: dict,
    workflow_name: str,
    directory_code: str,
    stage_key: str,
    graphql_url: str = "",
    **kwargs,
) -> dict:
    """
    TEA Custom Action: analysis.get_stage_config

    Runs the unified graph projection query for a given workflow + directory + stage.
    Returns everything the agent needs: template, slots, PathNodes, response schema
    references, and directory profile.

    Returns:
        {
          "success": bool,
          "stage": { stageKey, executionMode, steps: [...] },
          "directory_profile": { name, narrativeTone, avoidPatterns,
                                  criteria_by_weight, gate_rules, user_segments }
        }
    """
    logger.info(
        "analysis.get_stage_config: workflow=%s directory=%s stage=%s",
        workflow_name, directory_code, stage_key,
    )

    url = graphql_url or _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    try:
        data = _execute_graphql(
            url,
            GET_ANALYSIS_STAGE_QUERY,
            {
                "workflowName": workflow_name,
                "directoryCode": directory_code,
                "stageKey": stage_key,
            },
            api_key=api_key,
        )
    except (ConnectionError, RuntimeError) as e:
        logger.error("analysis.get_stage_config GraphQL failed: %s", e)
        return {"success": False, "error": str(e)}

    # Validate directory found
    directories = data.get("directories", [])
    if not directories:
        return {"success": False, "error": f"Directory not found: {directory_code}"}

    # Validate workflow + stage found
    workflows = data.get("analysisWorkflows", [])
    if not workflows:
        return {"success": False, "error": f"Workflow not found: {workflow_name}"}

    stages = workflows[0].get("hasStage", [])
    if not stages:
        return {
            "success": False,
            "error": f"Stage not found: {stage_key} in workflow {workflow_name}",
        }

    stage = _normalize_stage(stages[0])
    directory_profile = _normalize_directory(directories[0])

    logger.info(
        "analysis.get_stage_config: stage=%s steps=%d gate_rules=%d",
        stage["stageKey"],
        len(stage["steps"]),
        len(directory_profile["gate_rules"]),
    )

    return {
        "success": True,
        "stage": stage,
        "directory_profile": directory_profile,
    }


# =============================================================================
# ACTION: get_step_config
# =============================================================================

def get_step_config(
    state: dict,
    step_name: str,
    workflow_name: str,
    graphql_url: str = "",
    **kwargs,
) -> dict:
    """
    TEA Custom Action: analysis.get_step_config

    Lighter variant for Stage A — fetches only the step config for
    PromptExecutionSchema without the directory profile.

    Returns:
        {
          "success": bool,
          "step_config": {
            "batchSize": int,
            "responseFormat": {
              "schemaId": str,
              "centerEntityType": str,
              "connectStateKey": str,
              "connectRelation": str
            }
          }
        }
    """
    logger.info(
        "analysis.get_step_config: step=%s workflow=%s", step_name, workflow_name,
    )

    url = graphql_url or _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    try:
        data = _execute_graphql(
            url,
            GET_STEP_CONFIG_QUERY,
            {"stepName": step_name, "workflowName": workflow_name},
            api_key=api_key,
        )
    except (ConnectionError, RuntimeError) as e:
        logger.error("analysis.get_step_config GraphQL failed: %s", e)
        return {"success": False, "error": str(e)}

    workflows = data.get("analysisWorkflows", [])
    if not workflows:
        return {"success": False, "error": f"Workflow not found: {workflow_name}"}

    # Flatten stages → steps
    step_raw = None
    for stage in workflows[0].get("hasStage", []):
        for step in stage.get("hasStep", []):
            if step.get("name") == step_name:
                step_raw = step
                break
        if step_raw:
            break

    if not step_raw:
        return {"success": False, "error": f"Step not found: {step_name}"}

    # Build response format
    response_format = None
    rf_edges = step_raw.get("hasResponseFormatConnection", {}).get("edges", [])
    if rf_edges:
        edge = rf_edges[0]
        props = edge.get("properties", {})
        node = edge.get("node", {})
        center_types = node.get("jsonSchemaCentersAt", [])
        center_entity_type = center_types[0].get("name", "") if center_types else ""
        response_format = {
            "schemaId": node.get("id", ""),
            "centerEntityType": center_entity_type,
            "connectStateKey": props.get("connectStateKey", ""),
            "connectRelation": props.get("connectRelation", ""),
        }

    step_config = {
        "batchSize": step_raw.get("batchSize", 10),
        "responseFormat": response_format,
    }

    logger.info("analysis.get_step_config: found step %s batchSize=%d", step_name, step_config["batchSize"])

    return {
        "success": True,
        "step_config": step_config,
    }


# =============================================================================
# ACTION: get_stage_a_results
# =============================================================================

def get_stage_a_results(
    state: dict,
    matter_id: str,
    graphql_url: str = "",
    **kwargs,
) -> dict:
    """
    TEA Custom Action: analysis.get_stage_a_results

    Fetches PromptExecution nodes linked to the matter via HAS_CONTEXT.
    Normalizes answers by criterion code using MAPS_TO_CRITERION on PromptVersion.

    Returns:
        {
          "success": bool,
          "original_matter": str,
          "criteria": {
            "C1": {
              "answers": [str],
              "question_ids": [str],
              "confidence": "primary"|"secondary"
            },
            ...
          },
          "raw_executions": [...]
        }
    """
    logger.info("analysis.get_stage_a_results: matter_id=%s", matter_id)

    if not matter_id:
        return {"success": False, "error": "matter_id is required"}

    url = graphql_url or _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    try:
        data = _execute_graphql(
            url,
            GET_STAGE_A_RESULTS_QUERY,
            {"matterId": matter_id},
            api_key=api_key,
        )
    except (ConnectionError, RuntimeError) as e:
        logger.error("analysis.get_stage_a_results GraphQL failed: %s", e)
        return {"success": False, "error": str(e)}

    matters = data.get("matters", [])
    if not matters:
        return {
            "success": True,
            "original_matter": "",
            "criteria": {},
            "raw_executions": [],
        }

    matter = matters[0]
    context_edges = matter.get("matterHasContextConnection", {}).get("edges", [])

    raw_executions = []
    original_matter = ""
    criteria: Dict[str, Dict] = {}

    for edge in context_edges:
        node = edge.get("node", {})
        # Only process PromptExecution nodes (union — may have other types)
        if "__typename" in node and node["__typename"] != "PromptExecution":
            continue
        # If no __typename (inline fragment), check for PromptExecution-specific fields
        if "llmResponse" not in node:
            continue

        exec_id = node.get("id", "")
        llm_response = node.get("llmResponse", "")
        status = node.get("status", "")

        # Extract original_matter from ContextNode content (first one found)
        context_nodes = node.get("hasContext", [])
        if isinstance(context_nodes, list):
            for ctx in context_nodes:
                if isinstance(ctx, dict) and ctx.get("content") and not original_matter:
                    original_matter = ctx.get("content", "")
        elif isinstance(context_nodes, dict) and not original_matter:
            original_matter = context_nodes.get("content", "")

        # Map to criteria via PromptVersion.MAPS_TO_CRITERION
        version_raw = node.get("hasExecutionFrom") or {}
        version_id = version_raw.get("id", "")
        criterion_edges = (
            version_raw.get("mapsToCriterionConnection", {}).get("edges", [])
        )

        raw_executions.append({
            "id": exec_id,
            "status": status,
            "llmResponse": llm_response,
            "versionId": version_id,
        })

        for crit_edge in criterion_edges:
            code = crit_edge.get("node", {}).get("code", "")
            confidence = crit_edge.get("properties", {}).get("confidence", "primary")
            if not code:
                continue

            if code not in criteria:
                criteria[code] = {
                    "answers": [],
                    "question_ids": [],
                    "confidence": confidence,
                }
            # When a PromptVersion maps to multiple criteria, the answer appears in all
            if llm_response:
                criteria[code]["answers"].append(llm_response)
            if version_id and version_id not in criteria[code]["question_ids"]:
                criteria[code]["question_ids"].append(version_id)

    logger.info(
        "analysis.get_stage_a_results: %d executions, %d criteria codes",
        len(raw_executions),
        len(criteria),
    )

    return {
        "success": True,
        "original_matter": original_matter,
        "criteria": criteria,
        "raw_executions": raw_executions,
    }


# =============================================================================
# RENDER PROMPT HELPERS
# =============================================================================

def _resolve_state_path(agent_state: dict, source_path: str) -> Any:
    """
    Walk a dot-separated path into agent_state.

    source_path example: "state.stage_a_results.criteria"
    Strip leading "state." prefix, then walk remaining key path.
    Returns empty string if any key is missing.
    """
    path = source_path
    if path.startswith("state."):
        path = path[len("state."):]

    keys = path.split(".")
    value: Any = agent_state
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key, "")
        if value is None:
            return ""
    return value


def _apply_transform(value: Any, transform: Optional[str]) -> str:
    """Apply a named transform to a slot value."""
    if transform == "json_stringify":
        return json.dumps(value, ensure_ascii=False, indent=2)
    # No transform — cast to string
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def _format_criterion_matrix(criteria_by_weight: dict) -> str:
    """
    Format criteria_by_weight as human-readable text for the criterion_matrix slot.

    Output format:
      Core: C1, C8, C12, C14
      Amplifier: C3, C5
      Support: C4, C9
      External: C7
    """
    lines = []
    label_order = [("core", "Core"), ("amplifier", "Amplifier"), ("support", "Support"), ("external", "External")]
    for key, label in label_order:
        codes = criteria_by_weight.get(key, [])
        if codes:
            lines.append(f"{label}: {', '.join(codes)}")
    return "\n".join(lines)


def _format_gate_rules(gate_rules: list) -> str:
    """
    Format gate_rules as human-readable text for the gate_rules slot.

    Output format:
      Rule 1 (required): C1 must be present
      Rule 2 (min 2 of): C8, C14
    """
    lines = []
    for i, rule in enumerate(gate_rules, 1):
        rule_type = rule.get("ruleType", "")
        codes = [item.get("code", "") for item in rule.get("gateIncludes", [])]
        role_name = rule.get("roleName", f"Rule {i}")
        min_count = rule.get("minCount", 1)

        if rule_type == "required":
            lines.append(f"Rule {i} ({role_name}, required): {', '.join(codes)} must be present")
        elif rule_type == "min_from_set":
            lines.append(
                f"Rule {i} ({role_name}, min {min_count} of): {', '.join(codes)}"
            )
        else:
            lines.append(f"Rule {i} ({role_name}): {', '.join(codes)}")
    return "\n".join(lines)


def _format_graph_export_slot(slot_name: str, directory_profile: dict) -> str:
    """
    Dispatcher for known graph_export slot names.

    All underlying data lives in directory_profile (returned by get_stage_config).
    Falls back to JSON-stringifying the whole profile if slot_name is unknown.
    """
    if slot_name == "directory_name":
        return directory_profile.get("name", "")
    elif slot_name == "directory_tone":
        return directory_profile.get("narrativeTone", "")
    elif slot_name == "directory_avoid":
        return directory_profile.get("avoidPatterns", "")
    elif slot_name == "criterion_matrix":
        return _format_criterion_matrix(directory_profile.get("criteria_by_weight", {}))
    elif slot_name == "gate_rules":
        return _format_gate_rules(directory_profile.get("gate_rules", []))
    else:
        # Unknown graph_export slot — warn and return JSON of full profile
        logger.warning(
            "analysis.render_prompt: unknown graph_export slot '%s' — "
            "falling back to full directory_profile JSON",
            slot_name,
        )
        return json.dumps(directory_profile, ensure_ascii=False, indent=2)


# =============================================================================
# ACTION: render_prompt
# =============================================================================

def render_prompt(
    state: dict,
    stage_config: dict,
    agent_state: dict,
    **kwargs,
) -> dict:
    """
    TEA Custom Action: analysis.render_prompt

    For each slot in stage_config.stage.steps[0].promptTemplate.slots:
      - slotType "state":        read agent_state via PathNode.sourcePath, apply transform
      - slotType "graph_export": call _format_graph_export_slot dispatcher
      - slotType "static":       use slot.staticValue
      - slotType "state_connect": skip (post-process instructions, not prompt vars)

    Then render systemTemplate + userTemplate via Jinja2 with resolved slot values.

    Returns:
        { "success": bool, "system": str, "user": str }
    """
    try:
        import jinja2
    except ImportError:
        return {"success": False, "error": "jinja2 is not installed — add to requirements.txt"}

    stage = stage_config.get("stage", {})
    steps = stage.get("steps", [])
    if not steps:
        return {"success": False, "error": "stage_config.stage.steps is empty"}

    step = steps[0]
    template_config = step.get("promptTemplate", {})
    slots = template_config.get("slots", [])
    system_template_str = template_config.get("systemTemplate", "")
    user_template_str = template_config.get("userTemplate", "")

    directory_profile = stage_config.get("directory_profile", {})

    # Resolve slot values
    slot_values: Dict[str, str] = {}
    for slot in slots:
        slot_name = slot.get("slotName", "")
        slot_type = slot.get("slotType", "")

        if not slot_name:
            continue

        if slot_type == "static":
            slot_values[slot_name] = slot.get("staticValue", "")

        elif slot_type == "state":
            path_node = slot.get("pathNode") or {}
            source_path = path_node.get("sourcePath", "")
            transform = path_node.get("transform") or ""
            raw_value = _resolve_state_path(agent_state, source_path) if source_path else ""
            slot_values[slot_name] = _apply_transform(raw_value, transform if transform else None)

        elif slot_type == "graph_export":
            slot_values[slot_name] = _format_graph_export_slot(slot_name, directory_profile)

        elif slot_type == "state_connect":
            # These are post-process connect instructions, not prompt variables — skip
            continue

        else:
            logger.warning("analysis.render_prompt: unknown slotType '%s' for slot '%s'", slot_type, slot_name)
            slot_values[slot_name] = ""

    # Jinja2 rendering — undefined slots render as empty string (not exception)
    env = jinja2.Environment(undefined=jinja2.Undefined)

    try:
        system_rendered = env.from_string(system_template_str).render(**slot_values)
    except Exception as e:
        logger.error("analysis.render_prompt: Jinja2 error in systemTemplate: %s", e)
        return {"success": False, "error": f"Template render error (system): {e}"}

    try:
        user_rendered = env.from_string(user_template_str).render(**slot_values)
    except Exception as e:
        logger.error("analysis.render_prompt: Jinja2 error in userTemplate: %s", e)
        return {"success": False, "error": f"Template render error (user): {e}"}

    logger.info(
        "analysis.render_prompt: rendered system(%d chars) user(%d chars)",
        len(system_rendered),
        len(user_rendered),
    )

    return {
        "success": True,
        "system": system_rendered,
        "user": user_rendered,
    }


# =============================================================================
# PATH NODE MUTATION HELPERS (for post_process)
# =============================================================================

def _strip_markdown_fences(text: str) -> str:
    """Strip ```json ... ``` or ``` ... ``` markdown code fences."""
    # Match optional language tag after opening fence
    pattern = r"^```(?:json)?\s*\n?(.*?)\n?```$"
    match = re.match(pattern, text.strip(), re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _unwrap_llm_result(llm_result: Any) -> str:
    """
    Unwrap ratelimit.wrap + llm.call envelopes to extract the raw content string.

    ratelimit.wrap envelope: { "result": { "content": "..." }, ... }
    llm.call output:          { "content": "..." } or str
    """
    # Layer 1: ratelimit.wrap envelope
    if isinstance(llm_result, dict) and "result" in llm_result:
        inner = llm_result["result"]
    else:
        inner = llm_result

    # Layer 2: llm.call output
    if isinstance(inner, dict):
        content = inner.get("content", inner.get("text", ""))
    elif isinstance(inner, str):
        content = inner
    else:
        content = str(inner)

    return content


def _parse_llm_json(content: str) -> Optional[dict]:
    """
    Parse JSON from LLM content string.

    1. Direct parse
    2. Strip markdown fences, try again
    Returns None on failure.
    """
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        pass

    stripped = _strip_markdown_fences(content)
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None


def _load_response_schema_path_nodes(schema_id: str, url: str, api_key: Optional[str] = None) -> Optional[list]:
    """
    Query PathNodes from the response JsonSchema node.

    Returns list of property dicts with their PathNode steps, or None on failure.
    """
    try:
        data = _execute_graphql(
            url,
            GET_RESPONSE_SCHEMA_PATH_NODES_QUERY,
            {"schemaId": schema_id},
            api_key=api_key,
        )
    except (ConnectionError, RuntimeError) as e:
        logger.error("_load_response_schema_path_nodes failed: %s", e)
        return None

    schemas = data.get("jsonSchemas", [])
    if not schemas:
        return None

    return schemas[0].get("hasProperty", [])


def _relationship_to_field(rel_name: str) -> str:
    """Convert UPPER_SNAKE_CASE relation to camelCase field name."""
    parts = rel_name.lower().split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _create_center_entity(entity_type: str, properties: dict, url: str, api_key: Optional[str] = None) -> Optional[str]:
    """Create a new center entity node, return its ID or None."""
    mutation_name = f"create{entity_type}"
    result_field = entity_type[0].lower() + entity_type[1:]

    mutation = f"""
    mutation CreateCenterEntity($input: [{entity_type}CreateInput!]!) {{
      {mutation_name}(input: $input) {{
        {result_field} {{ id }}
      }}
    }}
    """

    try:
        data = _execute_graphql(url, mutation, {"input": [properties]}, api_key=api_key)
    except (ConnectionError, RuntimeError) as e:
        logger.error("_create_center_entity(%s) failed: %s", entity_type, e)
        return None

    results = data.get(mutation_name, {}).get(result_field, [])
    return results[0].get("id") if results else None


def _connect_via_relation(
    source_type: str, source_id: str, rel_field: str,
    target_id: str, edge_properties: Optional[dict],
    url: str, api_key: Optional[str] = None,
) -> bool:
    """Connect source to target via a relation field using update+connect mutation."""
    mutation_name = f"update{source_type}"
    result_field = source_type[0].lower() + source_type[1:]

    connect_clause: dict = {"where": {"node": {"id_EQ": target_id}}}
    if edge_properties:
        connect_clause["edge"] = edge_properties

    mutation = f"""
    mutation ConnectRelation($where: {source_type}Where!, $update: {source_type}UpdateInput!) {{
      {mutation_name}(where: $where, update: $update) {{
        {result_field} {{ id }}
      }}
    }}
    """

    variables = {
        "where": {"id_EQ": source_id},
        "update": {
            rel_field: [{"connect": [connect_clause]}]
        },
    }

    try:
        _execute_graphql(url, mutation, variables, api_key=api_key)
        return True
    except (ConnectionError, RuntimeError) as e:
        logger.error("_connect_via_relation(%s.%s) failed: %s", source_type, rel_field, e)
        return False


def _set_scalar_field(
    entity_type: str, entity_id: str, field_name: str, value: Any,
    url: str, api_key: Optional[str] = None,
) -> bool:
    """Set a scalar field on an existing entity via update mutation."""
    mutation_name = f"update{entity_type}"
    result_field = entity_type[0].lower() + entity_type[1:]

    mutation = f"""
    mutation SetScalar($where: {entity_type}Where!, $update: {entity_type}UpdateInput!) {{
      {mutation_name}(where: $where, update: $update) {{
        {result_field} {{ id }}
      }}
    }}
    """

    variables = {
        "where": {"id_EQ": entity_id},
        "update": {f"{field_name}_SET": value},
    }

    try:
        _execute_graphql(url, mutation, variables, api_key=api_key)
        return True
    except (ConnectionError, RuntimeError) as e:
        logger.error("_set_scalar_field(%s.%s) failed: %s", entity_type, field_name, e)
        return False


def _apply_path_nodes(
    parsed_json: dict,
    path_node_properties: list,
    center_entity_type: str,
    center_entity_id: str,
    url: str,
    api_key: Optional[str] = None,
) -> None:
    """
    Walk the PathNode mappings and execute GraphQL mutations for each mapped field.

    For each property in path_node_properties:
      - If it has PathNode steps: traverse from center entity → leaf node, set value
      - Scalar properties on the center entity are set directly
      - Array properties iterate over the JSON array, create/connect each item
      - Edge properties (isEdgeProperty) are applied to the connecting edge

    This mirrors the TypeScript importer's ensurePathExists logic in Python.
    """
    for prop in path_node_properties:
        prop_name = prop.get("name", "")
        json_value = parsed_json.get(prop_name)
        if json_value is None:
            continue

        path_conn = prop.get("propertyHasProjectionPathConnection", {})
        path_edges = path_conn.get("edges", [])

        if not path_edges:
            # Direct scalar on center entity
            _set_scalar_field(center_entity_type, center_entity_id, prop_name, json_value, url, api_key)
            continue

        # Sort path edges by ord to walk in order
        sorted_edges = sorted(path_edges, key=lambda e: e.get("properties", {}).get("ord", 0))

        # Handle array properties: iterate and process each item
        typename = prop.get("__typename", "")
        if typename == "ArrayProperty" or isinstance(json_value, list):
            items = json_value if isinstance(json_value, list) else [json_value]
            for item in items:
                _walk_path_and_apply(
                    item, sorted_edges, center_entity_type, center_entity_id, url, api_key
                )
        else:
            _walk_path_and_apply(
                json_value, sorted_edges, center_entity_type, center_entity_id, url, api_key
            )


def _walk_path_and_apply(
    value: Any,
    path_edges: list,
    center_entity_type: str,
    center_entity_id: str,
    url: str,
    api_key: Optional[str] = None,
) -> None:
    """
    Walk a single PathNode chain from center entity to leaf and apply the value.

    For simple scalar paths (1 step): update the field on center entity.
    For traversal paths (N steps): find/create intermediate nodes and connect.
    For connect-to-existing: look up target by some ID field in value.
    """
    if not path_edges:
        return

    first_edge = path_edges[0]
    first_node = first_edge.get("node", {})
    json_path = first_edge.get("properties", {}).get("jsonPath", "")

    # Scalar write directly to center entity
    leaf_prop = first_node.get("pathStepToProperty")
    if leaf_prop and len(path_edges) == 1 and not first_node.get("pathStepAtClass"):
        field_name = leaf_prop.get("name", json_path)
        _set_scalar_field(center_entity_type, center_entity_id, field_name, value, url, api_key)
        return

    # Relation connect: pathStepAtClass gives the target type
    target_class = first_node.get("pathStepAtClass")
    rel_node = first_node.get("pathStepViaRelation")

    if not target_class or not rel_node:
        logger.warning("_walk_path_and_apply: cannot resolve PathNode steps — skipping %s", json_path)
        return

    target_type = target_class.get("name", "")
    rel_name = rel_node.get("name", "")
    rel_field = _relationship_to_field(rel_name)
    is_edge_property = first_node.get("isEdgeProperty", False)

    # If the value is a string ID (connect-to-existing)
    if isinstance(value, str) and value:
        target_id = value
        edge_props = None
        _connect_via_relation(
            center_entity_type, center_entity_id, rel_field, target_id, edge_props, url, api_key
        )
        return

    # If value is a dict, try to extract an ID field or create a new node
    if isinstance(value, dict):
        target_id = value.get("id", "")
        if target_id:
            # Connect to existing node, possibly with edge properties
            edge_props = None
            if is_edge_property:
                # Edge property values come from remaining path steps or value itself
                edge_props = {k: v for k, v in value.items() if k != "id"}
            _connect_via_relation(
                center_entity_type, center_entity_id, rel_field, target_id, edge_props, url, api_key
            )
        else:
            # Create new target node with the dict fields, then connect
            new_id = _create_center_entity(target_type, value, url, api_key)
            if new_id:
                _connect_via_relation(
                    center_entity_type, center_entity_id, rel_field, new_id, None, url, api_key
                )
            # Continue walking remaining path edges on the new entity
            if new_id and len(path_edges) > 1:
                _walk_path_and_apply(value, path_edges[1:], target_type, new_id, url, api_key)


def _execute_connect_instruction(
    center_type: str,
    center_id: str,
    connect_relation: str,
    target_id: str,
    context_type: str,
    url: str,
    api_key: Optional[str] = None,
) -> bool:
    """
    Connect center node to an existing node (matter, context, etc.) via relation.

    Handles union-typed relations (e.g. hasContext is ContextNode | Matter | ProtoMatter)
    by using context_type to build the correct nested connect clause.
    """
    rel_field = _relationship_to_field(connect_relation)
    mutation_name = f"update{center_type}"
    result_field = center_type[0].lower() + center_type[1:]

    if context_type:
        # Union-typed relation: wrap in type branch
        connect_body = {
            context_type: {
                "connect": [{"where": {"node": {"id_EQ": target_id}}}]
            }
        }
    else:
        connect_body = [{"where": {"node": {"id_EQ": target_id}}}]

    mutation = f"""
    mutation ConnectInstruction($where: {center_type}Where!, $update: {center_type}UpdateInput!) {{
      {mutation_name}(where: $where, update: $update) {{
        {result_field} {{ id }}
      }}
    }}
    """

    variables = {
        "where": {"id_EQ": center_id},
        "update": {rel_field: connect_body},
    }

    try:
        _execute_graphql(url, mutation, variables, api_key=api_key)
        return True
    except (ConnectionError, RuntimeError) as e:
        logger.error("_execute_connect_instruction failed: %s", e)
        return False


# =============================================================================
# ACTION: post_process
# =============================================================================

def post_process(
    state: dict,
    stage_config: dict,
    llm_result: Any,
    agent_state: dict,
    graphql_url: str = "",
    **kwargs,
) -> dict:
    """
    TEA Custom Action: analysis.post_process

    Steps:
    1. Extract JSON string from llm_result (unwrap ratelimit.wrap envelope if present)
    2. Parse JSON string → dict
    3. Look up responseFormat config from stage_config.stage.steps[0].responseFormat
    4. Create center entity (StrategyPackage, WritingResult, etc.) via PathNode mutations
    5. Connect output node to matter via connectRelation(matter_id, output_node_id)
    6. If prevStepStateKey is set: connect to prev step node via prevStepRelation
    7. Store full JSON as resultJson on the output node
    8. Return parsed_json as parsed_result so next stage can use it without re-fetching

    Returns:
        { "success": bool, "output_node_id": str, "gate_passed": bool,
          "parsed_result": dict, "error": str }
    """
    url = graphql_url or _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    # Step 1: unwrap LLM result envelope
    content = _unwrap_llm_result(llm_result)

    # Step 2: parse JSON
    parsed = _parse_llm_json(content)
    if parsed is None:
        logger.error("analysis.post_process: JSON parse failed. Raw content (first 500): %s", content[:500])
        return {
            "success": False,
            "error": "JSON parse failed",
            "output_node_id": None,
            "parsed_result": None,
        }

    # Step 3: response format config
    stage = stage_config.get("stage", {})
    steps = stage.get("steps", [])
    if not steps or not steps[0].get("responseFormat"):
        return {"success": False, "error": "stage_config has no responseFormat in steps[0]"}

    rf = steps[0]["responseFormat"]
    schema_id = rf.get("schemaId", "")
    center_entity_type = rf.get("centerEntityType", "")
    connect_state_key = rf.get("connectStateKey", "")
    connect_relation = rf.get("connectRelation", "")
    prev_step_state_key = rf.get("prevStepStateKey")
    prev_step_relation = rf.get("prevStepRelation")

    if not center_entity_type:
        return {"success": False, "error": "responseFormat.centerEntityType is empty"}

    # Step 4a: Load PathNodes for the response schema
    path_node_props = None
    if schema_id:
        path_node_props = _load_response_schema_path_nodes(schema_id, url, api_key)

    # Step 4b: Create center entity
    # Extract top-level scalars from parsed JSON as initial properties
    # PathNode mutations will handle relations afterward
    # For now create with minimal properties; _apply_path_nodes will fill the rest
    initial_props: dict = {}
    if path_node_props:
        for prop in path_node_props:
            pname = prop.get("name", "")
            # Include direct scalars (no path node steps) in initial create
            path_edges = prop.get("propertyHasProjectionPathConnection", {}).get("edges", [])
            if not path_edges and pname in parsed and isinstance(parsed[pname], (str, int, float, bool)):
                initial_props[pname] = parsed[pname]

    # Always store the full JSON as resultJson for traceability
    initial_props["resultJson"] = json.dumps(parsed, ensure_ascii=False)

    output_node_id = _create_center_entity(center_entity_type, initial_props, url, api_key)
    if not output_node_id:
        return {
            "success": False,
            "error": f"Failed to create {center_entity_type} node",
            "output_node_id": None,
            "parsed_result": parsed,
        }

    logger.info("analysis.post_process: created %s node %s", center_entity_type, output_node_id)

    # Step 4c: Apply PathNode-driven mutations for relation fields
    if path_node_props:
        _apply_path_nodes(parsed, path_node_props, center_entity_type, output_node_id, url, api_key)

    # Step 5: Connect output node to matter/context
    connect_target_id = agent_state.get(connect_state_key, "") if connect_state_key else ""
    if connect_target_id and connect_relation:
        _execute_connect_instruction(
            center_entity_type, output_node_id, connect_relation,
            connect_target_id, "", url, api_key,
        )

    # Step 6: Connect to previous step node if configured
    if prev_step_state_key and prev_step_relation:
        prev_node_id = agent_state.get(prev_step_state_key, "")
        if prev_node_id:
            _execute_connect_instruction(
                center_entity_type, output_node_id, prev_step_relation,
                prev_node_id, "", url, api_key,
            )

    # Step 7: gate_passed from agent_state (Stage B carries gate result)
    gate_result = agent_state.get("gate_result", {})
    gate_passed = gate_result.get("passed", False) if isinstance(gate_result, dict) else False

    return {
        "success": True,
        "output_node_id": output_node_id,
        "gate_passed": gate_passed,
        "parsed_result": parsed,
    }


# =============================================================================
# ACTION: post_process_batch
# =============================================================================

def post_process_batch(
    state: dict,
    step_config: dict,
    answers: list,
    matter_id: str,
    context_node_id: str = "",
    context_node_type: str = "",
    graphql_url: str = "",
    **kwargs,
) -> dict:
    """
    TEA Custom Action: analysis.post_process_batch

    Replaces graphology.save_responses for Stage A.

    For each answer in answers:
      - Build JSON: { llmResponse, llmRequest, status, versionId }
      - Run post_process logic using PromptExecutionSchema PathNodes
      - Connect PromptExecution to PromptVersion (via versionId)
      - Connect PromptExecution to Matter/ProtoMatter/ContextNode (via context_node_id or matter_id)

    Batch size: step_config.batchSize (default 10, or 1 if any answer context > 5KB)

    Returns:
        { "success": bool, "executionIds": [str], "count": int, "matter_id": str }
    """
    logger.info(
        "analysis.post_process_batch: matter_id=%s answers=%d", matter_id, len(answers) if answers else 0
    )

    if not matter_id:
        return {"success": False, "error": "matter_id is required"}
    if not answers:
        return {"success": False, "error": "answers list is required and must not be empty"}

    url = graphql_url or _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    rf = (step_config.get("responseFormat") or {}) if step_config else {}
    schema_id = rf.get("schemaId", "")
    connect_state_key = rf.get("connectStateKey", "")
    connect_relation = rf.get("connectRelation", "")

    # Load PathNodes once for the schema
    path_node_props = None
    if schema_id:
        path_node_props = _load_response_schema_path_nodes(schema_id, url, api_key)

    # Batch size determination (mirrors save_responses logic)
    max_ctx = max(
        (len(a.get("llmResponse", "")) for a in answers),
        default=0,
    )
    batch_size = step_config.get("batchSize", 10) if max_ctx <= 5000 else 1

    execution_ids = []
    errors = []

    for i in range(0, len(answers), batch_size):
        batch = answers[i: i + batch_size]
        for answer in batch:
            version_id = answer.get("versionId", "")
            llm_response = answer.get("llmResponse", "")
            llm_request = answer.get("llmRequest", "")
            status = answer.get("status", "completed")

            # Build the JSON payload for this PromptExecution
            exec_json: dict = {
                "llmResponse": llm_response if isinstance(llm_response, str)
                               else json.dumps(llm_response),
                "llmRequest": llm_request if isinstance(llm_request, str)
                              else json.dumps(llm_request),
                "status": status,
                "clientId": matter_id,
            }
            if version_id:
                exec_json["versionId"] = version_id

            # Build initial scalar properties (exclude relation fields)
            initial_props: dict = {
                k: v for k, v in exec_json.items()
                if k not in ("versionId",) and isinstance(v, (str, int, float, bool))
            }

            # Connect to PromptVersion inline in create input
            if version_id:
                initial_props["hasExecutionFrom"] = {
                    "connect": [{"where": {"node": {"id_EQ": version_id}}}]
                }

            # HAS_CONTEXT: connect to existing context node or create ContextNode
            # Uses union type syntax — identical to save_responses
            effective_ctx_id = context_node_id or matter_id
            effective_ctx_type = context_node_type or ("Matter" if not context_node_id else context_node_type)

            if effective_ctx_id and effective_ctx_type:
                initial_props["hasContext"] = {
                    effective_ctx_type: {
                        "connect": [{"where": {"node": {"id_EQ": effective_ctx_id}}}]
                    }
                }
            else:
                # Fallback: create a new ContextNode with the llmRequest as content
                context_content = (
                    llm_request if isinstance(llm_request, str)
                    else json.dumps(llm_request)
                )
                initial_props["hasContext"] = {
                    "ContextNode": {
                        "create": [{"node": {"content": context_content, "contextType": "DOCUMENT"}}]
                    }
                }

            # Create PromptExecution node
            exec_node_id = _create_center_entity("PromptExecution", initial_props, url, api_key)
            if not exec_node_id:
                errors.append(f"Failed to create PromptExecution for versionId={version_id}")
                logger.error(
                    "analysis.post_process_batch: failed to create PromptExecution "
                    "for answer index %d", i
                )
                continue

            execution_ids.append(exec_node_id)

            # Apply PathNode-driven mutations if schema is configured
            if path_node_props:
                _apply_path_nodes(exec_json, path_node_props, "PromptExecution", exec_node_id, url, api_key)

    if errors:
        logger.warning(
            "analysis.post_process_batch: %d errors out of %d answers",
            len(errors), len(answers),
        )

    logger.info(
        "analysis.post_process_batch: created %d PromptExecution nodes for matter %s",
        len(execution_ids), matter_id,
    )

    return {
        "success": len(errors) == 0,
        "executionIds": execution_ids,
        "count": len(execution_ids),
        "matter_id": matter_id,
    }


# =============================================================================
# ACTION REGISTRATION
# =============================================================================

def register_actions(registry: Dict[str, Callable], engine: Any) -> None:
    """
    Register analysis actions with the TEA YAMLEngine.

    Args:
        registry: Action registry dictionary
        engine: YAMLEngine instance
    """
    registry["analysis.get_stage_config"] = get_stage_config
    registry["analysis.get_step_config"] = get_step_config
    registry["analysis.get_stage_a_results"] = get_stage_a_results
    registry["analysis.render_prompt"] = render_prompt
    registry["analysis.post_process"] = post_process
    registry["analysis.post_process_batch"] = post_process_batch

    logger.info(
        "Analysis actions registered: "
        "analysis.get_stage_config, analysis.get_step_config, analysis.get_stage_a_results, "
        "analysis.render_prompt, analysis.post_process, analysis.post_process_batch"
    )
