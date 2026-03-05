"""
AI Workflow Service - FastAPI endpoint for running TEA agents.

Story 16.1 - Generic Node Context Fetcher & Run Agent Endpoint.

Accepts {agent, workflow_id, context_node_id}, fetches the context node
from graphology (any type, introspected), and runs the specified TEA
YAML agent with the node JSON as matter_context.
"""

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# Add the actions directory to path so graphology module is importable
ACTIONS_DIR = os.environ.get("ACTIONS_DIR", str(Path(__file__).parent / "actions"))
if ACTIONS_DIR not in sys.path:
    sys.path.insert(0, ACTIONS_DIR)

# Import get_node from graphology actions
from graphology import get_node, register_actions

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app):
    """Fail fast if required configuration is missing."""
    if not os.environ.get("RUN_AGENT_API_KEY"):
        raise RuntimeError(
            "RUN_AGENT_API_KEY environment variable must be set and non-empty"
        )
    yield


app = FastAPI(title="AI Workflow Service", version="0.1.0", lifespan=lifespan)

AGENTS_DIR = os.environ.get("AGENTS_DIR", str(Path(__file__).parent / "agents"))


# =============================================================================
# REQUEST / RESPONSE MODELS
# =============================================================================

class RunAgentRequest(BaseModel):
    agent: str
    workflow_id: str
    context_node_id: str


# =============================================================================
# HELPERS
# =============================================================================

def _fetch_context_node(node_id: str) -> dict:
    """Fetch any graph node by ID using graphology.get_node."""
    state = {"variables": {
        "GRAPHOLOGY_URL": os.environ.get("GRAPHOLOGY_URL", "http://localhost:4000"),
        "GRAPHOLOGY_API_KEY": os.environ.get("GRAPHOLOGY_API_KEY", ""),
    }}
    return get_node(state, node_id=node_id)


def _validate_workflow(workflow_id: str) -> dict:
    """Validate that workflow_id resolves to a Workflow node."""
    result = _fetch_context_node(workflow_id)
    if not result.get("success"):
        return {"success": False, "error": f"Workflow not found: {workflow_id}"}
    if result.get("node_type") != "Workflow":
        return {"success": False, "error": f"Workflow not found: {workflow_id}"}
    return {"success": True, "data": result.get("data", {})}


def _load_and_run_agent(
    agent: str,
    workflow_id: str,
    context_node_id: str,
    agents_dir: str = None,
    actions_dir: str = None,
) -> dict:
    """
    Load a YAML agent and run it with context node data.

    Steps:
    1. Check agent YAML exists (AC5)
    2. Fetch context node via introspection (AC1)
    3. Validate workflow_id (AC3b)
    4. Load TEA engine, register actions, run agent
    """
    agents_dir = agents_dir or AGENTS_DIR
    actions_dir = actions_dir or ACTIONS_DIR

    # AC5: Check agent exists
    agent_path = Path(agents_dir) / f"{agent}.yaml"
    if not agent_path.exists():
        return {"success": False, "error": f"Agent not found: {agent}"}

    # AC1/AC3: Fetch context node
    node_result = _fetch_context_node(context_node_id)
    if not node_result.get("success"):
        return {"success": False, "error": node_result.get("error", "Unknown error")}

    # AC3b: Validate workflow
    wf_result = _validate_workflow(workflow_id)
    if not wf_result.get("success"):
        return {"success": False, "error": wf_result.get("error", "Unknown error")}

    context_node_type = node_result["node_type"]
    node_data = node_result["data"]
    matter_context = json.dumps(node_data) if isinstance(node_data, dict) else str(node_data)

    # Run TEA engine
    try:
        from the_edge_agent import YAMLEngine

        engine = YAMLEngine()
        register_actions(engine.actions_registry, engine)

        engine.variables["GRAPHOLOGY_URL"] = os.environ.get("GRAPHOLOGY_URL", "http://localhost:4000")
        engine.variables["GRAPHOLOGY_API_KEY"] = os.environ.get("GRAPHOLOGY_API_KEY", "")

        graph = engine.load_from_file(str(agent_path))

        input_state = {
            "workflow_id": workflow_id,
            "context_node_id": context_node_id,
            "matter_context": matter_context,
        }

        final_state = None
        for event in graph.invoke(input_state):
            final_state = event

        execution_ids = []
        if final_state and isinstance(final_state, dict):
            save_result = final_state.get("save_result", {})
            if isinstance(save_result, dict):
                execution_ids = save_result.get("executionIds", [])

        return {
            "success": True,
            "execution_ids": execution_ids,
            "context_node_type": context_node_type,
        }

    except Exception as e:
        logger.error(f"Agent execution failed: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# ENDPOINTS
# =============================================================================

@app.get("/health")
async def health():
    """Health check endpoint (AC: no auth required)."""
    return {"status": "ok"}


@app.post("/run-agent")
def run_agent(
    request: RunAgentRequest,
    x_api_key: Optional[str] = Header(None),
):
    """
    Run a TEA agent with a context node from graphology (AC2).

    Requires x-api-key header (AC4).
    Synchronous endpoint — TEA engine and GraphQL calls are blocking.
    """
    # AC4: API key auth
    expected_key = os.environ.get("RUN_AGENT_API_KEY", "")
    if not x_api_key or x_api_key != expected_key:
        raise HTTPException(status_code=401, detail="Unauthorized")

    result = _load_and_run_agent(
        agent=request.agent,
        workflow_id=request.workflow_id,
        context_node_id=request.context_node_id,
    )

    return result
