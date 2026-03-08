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
import hmac
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Add the actions directory to path so graphology module is importable
ACTIONS_DIR = os.environ.get("ACTIONS_DIR", str(Path(__file__).parent / "actions"))
if ACTIONS_DIR not in sys.path:
    sys.path.insert(0, ACTIONS_DIR)

# Import get_node from graphology actions
from graphology import get_node, register_actions

logging.basicConfig(level=logging.INFO)
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
    async_mode: bool = False


class JobStatus(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


_job_store: dict[str, dict] = {}
_job_lock = threading.Lock()
_MAX_COMPLETED_JOBS = 1000


def _generate_job_id() -> str:
    return str(uuid.uuid4())


def _evict_oldest_completed_jobs():
    """Remove oldest completed jobs when store exceeds limit. Caller must hold _job_lock."""
    completed = [
        (jid, j["created_at"])
        for jid, j in _job_store.items()
        if j["status"] in (JobStatus.SUCCESS, JobStatus.ERROR)
    ]
    if len(completed) <= _MAX_COMPLETED_JOBS:
        return
    completed.sort(key=lambda x: x[1])
    for jid, _ in completed[: len(completed) - _MAX_COMPLETED_JOBS]:
        del _job_store[jid]


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
            logger.info(f"TEA event type: {type(event).__name__}")
            final_state = event

        agent_state = {}
        if final_state and isinstance(final_state, dict):
            # TEA returns {"type": ..., "state": {...}, "output": ...}
            # The actual agent state is nested under "state" key
            agent_state = final_state.get("state", final_state)
            logger.info(f"Agent state keys: {list(agent_state.keys()) if isinstance(agent_state, dict) else 'N/A'}")
        else:
            logger.warning(f"Unexpected final_state type: {type(final_state)}, value: {str(final_state)[:500]}")

        agent_status = agent_state.get("status", "unknown")
        agent_error = agent_state.get("error")

        result = {
            "success": agent_status == "success",
            "status": agent_status,
            "context_node_type": context_node_type,
        }
        if agent_error:
            result["error"] = agent_error

        payload_json = agent_state.get("payload_json")
        if payload_json:
            result["payload"] = json.loads(payload_json)

        return result

    except Exception as e:
        logger.error(f"Agent execution failed: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# ASYNC JOB HELPERS
# =============================================================================

def _run_agent_job(job_id: str, agent: str, workflow_id: str, context_node_id: str):
    """Run agent in background thread, updating job store with result."""
    with _job_lock:
        _job_store[job_id]["status"] = JobStatus.RUNNING
    try:
        result = _load_and_run_agent(
            agent=agent,
            workflow_id=workflow_id,
            context_node_id=context_node_id,
        )
        new_status = JobStatus.SUCCESS if result.get("success") else JobStatus.ERROR
    except Exception as e:
        # Defensive: _load_and_run_agent already catches exceptions internally,
        # but guard against unexpected failures in dict/json processing.
        result = {"success": False, "error": str(e)}
        new_status = JobStatus.ERROR
    with _job_lock:
        _job_store[job_id]["result"] = result
        _job_store[job_id]["status"] = new_status  # status LAST to avoid race
        _evict_oldest_completed_jobs()


def _check_api_key(x_api_key: Optional[str]):
    """Validate API key, raise 401 if invalid."""
    expected_key = os.environ.get("RUN_AGENT_API_KEY", "")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected_key):
        raise HTTPException(status_code=401, detail="Unauthorized")


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
    Run a TEA agent with a context node from graphology.

    Requires x-api-key header (AC4).
    Supports sync (default) and async modes (AC2, AC7).
    """
    _check_api_key(x_api_key)

    if request.async_mode:
        job_id = _generate_job_id()
        with _job_lock:
            _job_store[job_id] = {
                "status": JobStatus.ACCEPTED,
                "result": None,
                "created_at": datetime.now(timezone.utc),
            }
        thread = threading.Thread(
            target=_run_agent_job,
            args=(job_id, request.agent, request.workflow_id, request.context_node_id),
            daemon=True,
        )
        thread.start()
        return JSONResponse(
            status_code=202,
            content={"job_id": job_id, "status": "accepted"},
        )

    result = _load_and_run_agent(
        agent=request.agent,
        workflow_id=request.workflow_id,
        context_node_id=request.context_node_id,
    )
    return result


@app.get("/run-agent/jobs/{job_id}")
def get_job_status(
    job_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """
    Poll async job status (AC3, AC4, AC6).

    Returns current job state with result when complete.
    """
    _check_api_key(x_api_key)

    with _job_lock:
        if job_id not in _job_store:
            raise HTTPException(status_code=404, detail="Job not found")

        job = _job_store[job_id]
        status = job["status"]
        response = {"job_id": job_id, "status": status.value}

        if status in (JobStatus.SUCCESS, JobStatus.ERROR):
            response["result"] = job["result"]

    return response
