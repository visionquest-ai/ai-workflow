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

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict, model_validator

from batch_accumulator import BatchAccumulator

# Add the actions directory to path so graphology module is importable
ACTIONS_DIR = os.environ.get("ACTIONS_DIR", str(Path(__file__).parent / "actions"))
if ACTIONS_DIR not in sys.path:
    sys.path.insert(0, ACTIONS_DIR)

# Import get_node from graphology actions
from graphology import get_node, register_actions
from agents import register_actions as register_agent_actions
from analysis import register_actions as register_analysis_actions

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

# Per-process BatchAccumulator singleton — created at startup, shared across all fan-out threads.
# Collects concurrent LLM calls within a 200ms window and dispatches them as a single batch.
accumulator = BatchAccumulator()

AGENTS_DIR = os.environ.get("AGENTS_DIR", str(Path(__file__).parent / "agents"))


# =============================================================================
# REQUEST / RESPONSE MODELS
# =============================================================================

class RunAgentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent: str
    workflow_id: Optional[str] = None
    context_node_id: Optional[str] = Field(None, alias="contextNodeId")
    context_node_ids: Optional[list[str]] = Field(None, alias="contextNodeIds")
    batch_id: Optional[str] = Field(None, alias="batchId")
    job_id: Optional[str] = Field(None, alias="job_id")
    job_ids: Optional[dict] = Field(None, alias="job_ids")  # context_node_id -> agentJob_id
    application_form_id: Optional[str] = None
    directory_code: Optional[str] = None    # for matter_strategy + matter_rewrite
    matter_detail_id: Optional[str] = None  # for AI Review (Stage A→B→C chaining)
    target_research: Optional[str] = None   # for matter_strategy + matter_rewrite
    metadata: Optional[dict] = None         # for matter_strategy + matter_rewrite
    async_mode: bool = Field(False, alias="asyncMode")

    @model_validator(mode="after")
    def resolve_node_id(self):
        """Accept application_form_id as alias for context_node_id.

        Validates:
        - context_node_id and context_node_ids cannot both be present
        - At least one of context_node_id, application_form_id, or context_node_ids is required
        """
        # Reject both singular and plural at the same time
        if self.context_node_id and self.context_node_ids:
            raise ValueError(
                "context_node_id and context_node_ids cannot both be provided — use one or the other"
            )
        # Accept application_form_id as alias for context_node_id
        if self.application_form_id and not self.context_node_id:
            self.context_node_id = self.application_form_id
        # Require at least one context source
        if not self.context_node_id and not self.context_node_ids:
            raise ValueError(
                "Either context_node_id, contextNodeId, application_form_id, or context_node_ids/contextNodeIds is required"
            )
        return self


class RunPromptRequest(BaseModel):
    """Request model for /run-prompt — runs a TEA agent with arbitrary input state."""
    agent: str
    input_state: dict


class JobStatus(str, Enum):
    ACCEPTED = "accepted"
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


_job_store: dict[str, dict] = {}
_job_lock = threading.Lock()
_MAX_COMPLETED_JOBS = 1000

# Track active agent executions so the liveness probe waits for them to finish
_active_requests = 0
_active_requests_lock = threading.Lock()


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
    workflow_id: Optional[str],
    context_node_id: str,
    agents_dir: Optional[str] = None,
    actions_dir: Optional[str] = None,
    directory_code: Optional[str] = None,
    matter_detail_id: Optional[str] = None,
    target_research: Optional[str] = None,
    metadata: Optional[dict] = None,
    batch_id: Optional[str] = None,
    agent_accumulator=None,
) -> dict:
    """
    Load a YAML agent and run it with context node data.

    Steps:
    1. Check agent YAML exists (AC5)
    2. Fetch context node via introspection (AC1)
    3. Validate workflow_id (AC3b)
    4. Load TEA engine, register actions, run agent
    """
    global _active_requests
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

    # AC3b: Validate workflow (skip if not provided — some agents don't need it)
    if workflow_id:
        wf_result = _validate_workflow(workflow_id)
        if not wf_result.get("success"):
            return {"success": False, "error": wf_result.get("error", "Unknown error")}

    context_node_type = node_result["node_type"]
    node_data = node_result["data"]
    matter_context = json.dumps(node_data) if isinstance(node_data, dict) else str(node_data)

    # Track active execution so liveness probe waits for completion
    with _active_requests_lock:
        _active_requests += 1

    # Run TEA engine
    try:
        from the_edge_agent import YAMLEngine

        engine = YAMLEngine()
        register_actions(engine.actions_registry, engine)
        register_agent_actions(engine.actions_registry, engine)
        register_analysis_actions(engine.actions_registry, engine)

        engine.variables["GRAPHOLOGY_URL"] = os.environ.get("GRAPHOLOGY_URL", "http://localhost:4000")
        engine.variables["GRAPHOLOGY_API_KEY"] = os.environ.get("GRAPHOLOGY_API_KEY", "")

        graph = engine.load_from_file(str(agent_path))

        if agent == "matter_strategy":
            input_state = {
                "matter_id": context_node_id,
                "directory_code": directory_code,
                "target_research": target_research or "",
                "metadata": metadata or {},
            }
        else:
            input_state = {
                "workflow_id": workflow_id,
                "context_node_id": context_node_id,
                "context_result": node_result,
            }
            # When matter_detail_id is provided (AI Review), DON'T pre-populate matter_context.
            # This forces fetch_context to run, which calls get_matter_context and fetches
            # Matter + MatterDetail (with description) + Client + Department — full context.
            if matter_detail_id:
                input_state["matter_detail_id"] = matter_detail_id
            else:
                input_state["matter_context"] = matter_context
            if directory_code:
                input_state["directory_code"] = directory_code

        invoke_config = {"accumulator": agent_accumulator, "batch_id": batch_id}

        final_state = None
        for event in graph.invoke(input_state, config=invoke_config):
            tea_type = event.get("type") if isinstance(event, dict) else type(event).__name__
            logger.info(f"TEA event type: {tea_type}")
            final_state = event

        agent_state = {}
        tea_event_type = None
        if final_state and isinstance(final_state, dict):
            # TEA returns {"type": ..., "state": {...}, "output": ...}
            # "type" is "final" (success) or "error" (exception during execution)
            tea_event_type = final_state.get("type")
            agent_state_raw = final_state.get("state")
            if isinstance(agent_state_raw, dict):
                agent_state = agent_state_raw
            else:
                # Fallback: state not nested (old TEA format)
                agent_state = final_state
            logger.info(f"TEA event type: {tea_event_type}, agent state keys: {list(agent_state.keys()) if isinstance(agent_state, dict) else 'N/A'}")
        else:
            logger.warning(f"Unexpected final_state type: {type(final_state)}, value: {str(final_state)[:500]}")

        # Determine success from TEA event type — most reliable signal.
        # "final" = graph reached __end__ without exception (both finalize_success and
        # finalize_error paths yield "final"). "error" = unhandled exception in a node.
        agent_error = None
        if tea_event_type == "final":
            agent_status = "success"
        elif tea_event_type == "error":
            agent_status = "error"
            # Error string is in the event dict, not the state dict
            agent_error = final_state.get("error") or agent_state.get("error")
        else:
            # Unknown/missing TEA event type — fall back to state-based inference
            agent_status = agent_state.get("status", "unknown")
            agent_error = agent_state.get("error")
            if agent_status == "unknown" and not agent_error:
                save_result = agent_state.get("save_result")
                if save_result and isinstance(save_result, dict) and save_result.get("success"):
                    agent_status = "success"
                elif agent_state.get("answers") or agent_state.get("save_result"):
                    agent_status = "success"

        logger.info(f"Agent status: {agent_status}, error: {agent_error}")

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
    finally:
        with _active_requests_lock:
            _active_requests -= 1


# =============================================================================
# ASYNC JOB HELPERS
# =============================================================================

def _notify_apollo(job_id: str, status: str, result: str = None, error: str = None):
    """Call updateAgentJob mutation on Apollo. Called once when job reaches final state.

    On exception: logs the error but does NOT re-raise — notification failure
    must not discard the job result.
    """
    mutation = """
    mutation AgentJobCallback($jobId: ID!, $status: String!, $result: String, $error: String) {
      agentJobCallback(jobId: $jobId, status: $status, result: $result, error: $error) {
        jobId
        status
      }
    }
    """
    try:
        response = requests.post(
            os.environ["GRAPHOLOGY_URL"],
            json={
                "query": mutation,
                "variables": {
                    "jobId": job_id,
                    "status": status,
                    "result": result,
                    "error": error,
                },
            },
            headers={
                "X-API-Key": os.environ["GRAPHOLOGY_API_KEY"],
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to notify Apollo for job {job_id}: {e}")
        # Do NOT re-raise — notification failure must not discard job result


# Directory code → tab name mapping for clearing loading status after agent completes
_DIRECTORY_CODE_TO_TAB_NAME: dict[str, str] = {
    "CH": "C&P Details",
    "ITR": "ITR Details",
    "IFLR": "IFLR Details",
    "L500": "L500 Details",
    "LL": "LL Details",
}


def _set_tab_loading_status(
    matter_id: str,
    directory_code: str,
):
    """Set resolvedStatus='loading' on the directory details tab when AI agent starts."""
    tab_name = _DIRECTORY_CODE_TO_TAB_NAME.get(directory_code)
    if not tab_name:
        return

    query = """
    mutation SetTabLoading($matterId: String!, $tabName: String!) {
      updateMenuElementInstance(
        where: {
          type_EQ: "TAB"
          name_EQ: $tabName
          hasChildMenuElementFromConnection_SOME: {
            node: { entityId_EQ: $matterId, type_EQ: "PAGE" }
          }
        }
        update: { resolvedStatus_SET: "loading" }
      ) {
        menuElementInstance { id type firmId resolvedStatus }
      }
    }
    """
    try:
        response = requests.post(
            os.environ["GRAPHOLOGY_URL"],
            json={
                "query": query,
                "variables": {"matterId": matter_id, "tabName": tab_name},
            },
            headers={
                "X-API-Key": os.environ["GRAPHOLOGY_API_KEY"],
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        updated = data.get("data", {}).get("updateMenuElementInstance", {}).get("menuElementInstance", [])
        if updated:
            logger.info("Set loading on tab '%s' for matter %s", tab_name, matter_id[:8])
    except Exception as e:
        logger.warning("Failed to set tab loading for matter %s: %s", matter_id[:8], e)


def _clear_tab_loading_status(
    matter_id: str,
    directory_code: str,
):
    """Clear resolvedStatus='loading' on the directory details tab after AI agent completes.

    Finds the matter's PAGE MenuElementInstance, then its child TAB matching
    the directory details name, and sets resolvedStatus='valid'.
    """
    tab_name = _DIRECTORY_CODE_TO_TAB_NAME.get(directory_code)
    if not tab_name:
        return

    # Single query+mutation: find the tab by matter entityId + tab name, then update
    query = """
    mutation ClearTabLoading($matterId: String!, $tabName: String!) {
      updateMenuElementInstance(
        where: {
          type_EQ: "TAB"
          name_EQ: $tabName
          hasChildMenuElementFromConnection_SOME: {
            node: { entityId_EQ: $matterId, type_EQ: "PAGE" }
          }
        }
        update: { resolvedStatus_SET: "valid", loadingStatus_SET: false }
      ) {
        menuElementInstance { id type firmId resolvedStatus loadingStatus }
      }
    }
    """
    try:
        response = requests.post(
            os.environ["GRAPHOLOGY_URL"],
            json={
                "query": query,
                "variables": {"matterId": matter_id, "tabName": tab_name},
            },
            headers={
                "X-API-Key": os.environ["GRAPHOLOGY_API_KEY"],
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        updated = data.get("data", {}).get("updateMenuElementInstance", {}).get("menuElementInstance", [])
        if updated:
            logger.info("Cleared loading status on tab '%s' for matter %s", tab_name, matter_id[:8])
        else:
            logger.debug("No tab '%s' found for matter %s (may not exist)", tab_name, matter_id[:8])
    except Exception as e:
        logger.warning("Failed to clear tab loading status for matter %s: %s", matter_id[:8], e)


def _run_agent_job(
    job_id: str,
    agent: str,
    workflow_id: Optional[str],
    context_node_id: str,
    directory_code: Optional[str] = None,
    matter_detail_id: Optional[str] = None,
    target_research: Optional[str] = None,
    metadata: Optional[dict] = None,
    batch_id: Optional[str] = None,
    accumulator=None,
    neo4j_job_id: Optional[str] = None,
):
    """Run agent in background thread, updating job store with result.

    On completion (success or error), calls _notify_apollo once.
    Does NOT call _notify_apollo for the pending → running transition.

    neo4j_job_id: the AgentJob ID already created in Neo4j by the Apollo resolver.
    If provided, it is used as the job identifier when notifying Apollo so that
    the callback updates the correct pre-existing node. Falls back to the
    internal job_id (a locally-generated UUID) when not provided.
    """
    # Use the Neo4j AgentJob ID for Apollo callbacks when available
    apollo_job_id = neo4j_job_id if neo4j_job_id else job_id

    with _job_lock:
        _job_store[job_id]["status"] = JobStatus.RUNNING

    # Set directory detail tab to "loading" when AI agent starts
    if directory_code and context_node_id:
        _set_tab_loading_status(context_node_id, directory_code)

    try:
        result = _load_and_run_agent(
            agent=agent,
            workflow_id=workflow_id,
            context_node_id=context_node_id,
            directory_code=directory_code,
            matter_detail_id=matter_detail_id,
            target_research=target_research,
            metadata=metadata,
            batch_id=batch_id,
            agent_accumulator=accumulator,
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

    # Notify Apollo once the job reaches a final state (success or error).
    # On success: send a compact receipt only — full extraction data lives on the
    # domain node's payload field and must not be re-sent here (avoids 413).
    if new_status == JobStatus.SUCCESS:
        _notify_apollo(
            apollo_job_id,
            "success",
            result=json.dumps({"completed": True, "contextNodeId": context_node_id}),
        )
        # Clear the loading status on the directory details tab
        if directory_code and context_node_id:
            _clear_tab_loading_status(context_node_id, directory_code)
    else:
        _notify_apollo(apollo_job_id, "error", error=result.get("error", "Unknown error"))


def _check_api_key(x_api_key: Optional[str]):
    """Validate API key, raise 401 if invalid."""
    expected_key = os.environ.get("RUN_AGENT_API_KEY", "")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected_key):
        raise HTTPException(status_code=401, detail="Unauthorized")


# =============================================================================
# ENDPOINTS
# =============================================================================

def _get_rss_mb() -> float:
    """Return current process RSS in megabytes (Linux /proc)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # kB -> MB
    except (OSError, ValueError):
        pass
    return 0.0


@app.get("/health")
async def health():
    """Health check with memory threshold — returns 503 when RSS exceeds limit
    AND no agent executions are in progress.

    The livenessProbe uses this to trigger a pod restart when memory grows
    too large (Python/glibc don't always return freed memory to the OS).
    Restart is deferred while any agent is still processing to avoid
    interrupting work mid-execution.
    """
    rss_mb = _get_rss_mb()
    limit_mb = float(os.environ.get("MEMORY_LIMIT_MB", "768"))

    with _active_requests_lock:
        active = _active_requests

    if rss_mb > limit_mb:
        if active > 0:
            logger.warning(
                f"Memory threshold exceeded: RSS={rss_mb:.0f}MB > limit={limit_mb:.0f}MB "
                f"— but {active} request(s) still active, staying healthy"
            )
            return {
                "status": "ok",
                "rss_mb": round(rss_mb),
                "active_requests": active,
                "memory_pressure": True,
            }

        logger.warning(
            f"Memory threshold exceeded: RSS={rss_mb:.0f}MB > limit={limit_mb:.0f}MB "
            f"— no active requests, reporting unhealthy for restart"
        )
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "reason": "memory_threshold_exceeded",
                "rss_mb": round(rss_mb),
                "limit_mb": round(limit_mb),
                "active_requests": 0,
            },
        )

    return {"status": "ok", "rss_mb": round(rss_mb), "active_requests": active}


@app.post("/run-agent")
def run_agent(
    request: RunAgentRequest,
    x_api_key: Optional[str] = Header(None),
):
    """
    Run a TEA agent with a context node from graphology.

    Requires x-api-key header (AC4).
    Supports sync (default) and async modes (AC2, AC7).
    Supports fan-out: provide context_node_ids (plural) to run N threads simultaneously.
    """
    _check_api_key(x_api_key)

    # --- Fan-out path: context_node_ids (plural) ---
    if request.context_node_ids:
        if request.async_mode:
            # Create a job per node, start ALL threads simultaneously, return immediately
            job_ids = []
            threads = []
            for node_id in request.context_node_ids:
                job_id = _generate_job_id()
                with _job_lock:
                    _job_store[job_id] = {
                        "status": JobStatus.ACCEPTED,
                        "result": None,
                        "created_at": datetime.now(timezone.utc),
                    }
                job_ids.append(job_id)
                t = threading.Thread(
                    target=_run_agent_job,
                    args=(job_id, request.agent, request.workflow_id, node_id),
                    kwargs={
                        "directory_code": request.directory_code,
                        "target_research": request.target_research,
                        "metadata": request.metadata,
                        "batch_id": request.batch_id,
                        "accumulator": accumulator,
                        "neo4j_job_id": (request.job_ids or {}).get(node_id),
                    },
                    daemon=True,
                )
                threads.append(t)
            # CRITICAL: start ALL before joining ANY (batch accumulator window alignment)
            for t in threads:
                t.start()
            return JSONResponse(
                status_code=202,
                content={"job_ids": job_ids, "status": "accepted"},
            )
        else:
            # Synchronous fan-out: start all, then join all
            results = [None] * len(request.context_node_ids)

            def _run_sync(index, node_id):
                results[index] = _load_and_run_agent(
                    agent=request.agent,
                    workflow_id=request.workflow_id,
                    context_node_id=node_id,
                    directory_code=request.directory_code,
                    target_research=request.target_research,
                    metadata=request.metadata,
                )

            threads = [
                threading.Thread(target=_run_sync, args=(i, node_id))
                for i, node_id in enumerate(request.context_node_ids)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            return {"results": results}

    # --- Single node path (backward compatible) ---
    assert request.context_node_id  # guaranteed by model_validator

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
            kwargs={
                "directory_code": request.directory_code,
                "matter_detail_id": request.matter_detail_id,
                "target_research": request.target_research,
                "metadata": request.metadata,
                "batch_id": request.batch_id,
                "accumulator": None,  # AW.1.4 will inject the real accumulator
                "neo4j_job_id": request.job_id,
            },
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
        directory_code=request.directory_code,
        matter_detail_id=request.matter_detail_id,
        target_research=request.target_research,
        metadata=request.metadata,
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


# =============================================================================
# /run-prompt — Generic agent endpoint (no graphology context)
# =============================================================================

def _load_and_run_prompt(
    agent: str,
    input_state: dict,
    agents_dir: Optional[str] = None,
    actions_dir: Optional[str] = None,
) -> dict:
    """
    Load a YAML agent and run it with arbitrary input state.

    Unlike _load_and_run_agent, this skips graphology entirely —
    input_state is passed directly to the TEA agent.
    """
    global _active_requests
    agents_dir = agents_dir or AGENTS_DIR
    actions_dir = actions_dir or ACTIONS_DIR

    agent_path = Path(agents_dir) / f"{agent}.yaml"
    if not agent_path.exists():
        return {"success": False, "error": f"Agent not found: {agent}"}

    with _active_requests_lock:
        _active_requests += 1

    try:
        from the_edge_agent import YAMLEngine

        engine = YAMLEngine()
        register_actions(engine.actions_registry, engine)
        register_agent_actions(engine.actions_registry, engine)
        register_analysis_actions(engine.actions_registry, engine)

        engine.variables["GRAPHOLOGY_URL"] = os.environ.get("GRAPHOLOGY_URL", "http://localhost:4000")
        engine.variables["GRAPHOLOGY_API_KEY"] = os.environ.get("GRAPHOLOGY_API_KEY", "")

        graph = engine.load_from_file(str(agent_path))

        final_state = None
        for event in graph.invoke(input_state):
            tea_type = event.get("type") if isinstance(event, dict) else type(event).__name__
            logger.info(f"TEA event type: {tea_type}")
            final_state = event

        agent_state = {}
        tea_event_type = None
        if final_state and isinstance(final_state, dict):
            tea_event_type = final_state.get("type")
            agent_state_raw = final_state.get("state")
            if isinstance(agent_state_raw, dict):
                agent_state = agent_state_raw
            else:
                agent_state = final_state
            logger.info(f"TEA event type: {tea_event_type}, agent state keys: {list(agent_state.keys()) if isinstance(agent_state, dict) else 'N/A'}")
        else:
            logger.warning(f"Unexpected final_state type: {type(final_state)}, value: {str(final_state)[:500]}")

        agent_error = None
        if tea_event_type == "final":
            agent_status = "success"
        elif tea_event_type == "error":
            agent_status = "error"
            agent_error = final_state.get("error") or agent_state.get("error")
        else:
            agent_status = agent_state.get("status", "unknown")
            agent_error = agent_state.get("error")
            if agent_status == "unknown" and not agent_error:
                save_result = agent_state.get("save_result")
                if save_result and isinstance(save_result, dict) and save_result.get("success"):
                    agent_status = "success"
                elif agent_state.get("answers") or agent_state.get("save_result"):
                    agent_status = "success"

        logger.info(f"Agent status: {agent_status}, error: {agent_error}")

        result = {
            "success": agent_status == "success",
            "status": agent_status,
        }
        if agent_error:
            result["error"] = agent_error

        # Surface agent result — check payload_json first, then state.result
        payload_json = agent_state.get("payload_json")
        if payload_json:
            result["payload"] = json.loads(payload_json)
        elif "result" in agent_state:
            result["result"] = agent_state["result"]

        return result

    except Exception as e:
        logger.error(f"Agent execution failed: {e}")
        return {"success": False, "error": str(e)}
    finally:
        with _active_requests_lock:
            _active_requests -= 1


# In-memory store for completed LlamaExtract webhook results.
# Key: extraction_job_id (str), Value: webhook payload dict.
# Consumed by file_extraction agents that poll this endpoint via /extract-callback/{job_id}.
_extract_results: dict[str, dict] = {}
_extract_results_lock = threading.Lock()


@app.post("/extract-callback")
async def extract_callback(request: Request, x_api_key: Optional[str] = Header(None)):
    """
    Receive LlamaExtract webhook callbacks.

    LlamaExtract posts here when an extraction job completes (extract.success / extract.error).
    Stores the result keyed by job_id so that file_extraction agents can retrieve it
    without polling LlamaExtract directly.

    Expects JSON body with at minimum: {"id": "<job_id>", "status": "SUCCESS"|"ERROR", ...}
    """
    _check_api_key(x_api_key)

    body = await request.json()
    job_id = body.get("id") or body.get("job_id")
    if not job_id:
        raise HTTPException(status_code=400, detail="Missing job id in webhook payload")

    status = body.get("status", "unknown")
    logger.info(f"extract-callback: job_id={job_id} status={status}")

    with _extract_results_lock:
        _extract_results[job_id] = body
        # Keep store bounded — evict oldest when over 500 entries
        if len(_extract_results) > 500:
            oldest_key = next(iter(_extract_results))
            del _extract_results[oldest_key]

    return {"received": True, "job_id": job_id}


@app.get("/extract-callback/{job_id}")
async def get_extract_result(job_id: str, x_api_key: Optional[str] = Header(None)):
    """
    Poll for a LlamaExtract webhook result by job_id.

    Returns 404 if the webhook has not yet been received.
    Returns 200 with the full webhook payload once available.
    """
    _check_api_key(x_api_key)

    with _extract_results_lock:
        result = _extract_results.get(job_id)

    if result is None:
        raise HTTPException(status_code=404, detail="Webhook not yet received for this job")

    return {"job_id": job_id, "received": True, "payload": result}


# ============================================================================
# Import Endpoint — 5-phase directory submission import
# ============================================================================


class ImportRequest(BaseModel):
    """Request body for /api/import/{file_id}."""
    model_config = ConfigDict(extra="forbid")


# Content fields per directory (excluding metadata) — used for change detection.
_DETAIL_CONTENT_FIELDS: dict[str, str] = {
    "CH":   "matterDescription summary firmRoleAndOutput clientRole otherInformation whyHighlight ranking feedback otherLawyers innovationOrImpactReason impactAwardNomination description",
    "ITR":  "matterDescription summary firmRoleAndOutput clientRole otherInformation whyHighlight ranking feedback otherLawyers innovationOrImpactReason impactAwardNomination specialisms description",
    "IFLR": "matterDescription summary firmRoleAndOutput clientRole otherInformation whyHighlight ranking feedback otherLawyers innovationOrImpactReason nominateForIflrAwards leadOrLocalCounsel dealDescription description",
    "L500": "matterDescription summary firmRoleAndOutput clientRole otherInformation whyHighlight ranking feedback otherLawyers innovationOrImpactReason crossBorderStatus description",
    "LL":   "matterDescription summary firmRoleAndOutput clientRole otherInformation whyHighlight ranking feedback otherLawyers innovationOrImpactReason pressCoverage description",
}
_DETAIL_METADATA = {"id", "version", "createdAt", "updatedAt", "lastEditedAt", "createdByUid", "lastEditedByUid"}


def _content_fingerprint(detail: dict) -> str:
    """Stable hash of a MatterDetail's content fields (metadata excluded)."""
    import json
    entries = sorted(
        (k, v) for k, v in detail.items()
        if k not in _DETAIL_METADATA and v not in (None, "", [])
    )
    return json.dumps(entries)


def _matter_data_changed(analysisStatus: str | None, details: list[dict]) -> bool:
    """Return True if AI analysis should be triggered for this matter.

    Logic mirrors TypeScript matter-change-detector.ts:
    - No details → skip (nothing to analyze)
    - First import (no analysisStatus) OR single import detail → trigger
    - Multiple import details → compare newest vs previous fingerprint
    """
    import_details = sorted(
        [d for d in details if not (d.get("version") or "").startswith("AI_")],
        key=lambda d: d.get("createdAt") or "",
        reverse=True,  # newest first
    )
    if not import_details:
        return False
    if not analysisStatus or len(import_details) <= 1:
        return True
    return _content_fingerprint(import_details[0]) != _content_fingerprint(import_details[1])


def _trigger_matter_analysis(
    matter_ids: list[str],
    directory_code: str,
    graphql_endpoint: str,
    graphql_api_key: str,
):
    """Trigger AI analysis for imported matters.

    1. Set matter PAGEs to resolvedStatus='loading' via GraphQL (user sees page-level spinner)
    2. Set analysisStatus='pending' on each matter (triggers Apollo AI Workflow Middleware)

    Tab-level loading is the AI workflow's responsibility (set on agent start, clear on complete).
    """
    headers = {
        "X-API-Key": graphql_api_key,
        "Content-Type": "application/json",
    }

    # Step 1: Set matter PAGE instances to visible + loading via GraphQL (single batch)
    page_parts = []
    for i, mid in enumerate(matter_ids):
        page_parts.append(
            f'p{i}: updateMenuElementInstance('
            f'where: {{ entityId_EQ: "{mid}", type_EQ: "PAGE" }}, '
            f'update: {{ visibilityStatus_SET: true, resolvedStatus_SET: "invalid" }}'
            f') {{ menuElementInstance {{ id type firmId visibilityStatus resolvedStatus }} }}'
        )
    mei_ids: list[str] = []
    firm_id: str | None = None
    for batch_start in range(0, len(page_parts), 10):
        batch = page_parts[batch_start:batch_start + 10]
        try:
            resp = requests.post(
                graphql_endpoint,
                json={"query": "mutation { " + " ".join(batch) + " }"},
                headers=headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            for val in data.values():
                for inst in (val or {}).get("menuElementInstance") or []:
                    if inst.get("id"):
                        mei_ids.append(inst["id"])
                    if not firm_id:
                        firm_id = inst.get("firmId")
        except Exception as e:
            logger.warning("Failed to set page visible for batch %d: %s", batch_start, e)

    # Broadcast so subscribed UIs update without refresh
    if mei_ids and firm_id:
        ids_str = ", ".join(f'"{mid}"' for mid in mei_ids)
        try:
            requests.post(
                graphql_endpoint,
                json={"query": f'mutation {{ broadcastMenuInstanceChanges(instanceIds: [{ids_str}], firmId: "{firm_id}") {{ published errors }} }}'},
                headers=headers, timeout=15.0,
            )
        except Exception as e:
            logger.warning("Broadcast matter page visibility failed: %s", e)

    logger.info("Set PAGE visible+invalid on %d matters (via GraphQL)", len(matter_ids))

    # Step 2: Look up MatterDetail IDs, then set analysisStatus='pending' +
    # pendingAnalysisMatterDetailId on each matter (one-by-one, NOT aliased).
    # The Apollo Trigger Middleware needs pendingAnalysisMatterDetailId to resolve
    # the dispatch path, and cannot parse aliased mutation names.
    _DIR_CODE_TO_DETAIL_RELATION = {
        "CH": "matterHasCpDetail",
        "ITR": "matterHasItrDetail",
        "IFLR": "matterHasIflrDetail",
        "L500": "matterHasL500Detail",
        "LL": "matterHasLlDetail",
    }
    detail_relation = _DIR_CODE_TO_DETAIL_RELATION.get(directory_code)

    # Batch-query matter details (ID + content fields) and analysisStatus for change detection
    content_fields = _DETAIL_CONTENT_FIELDS.get(directory_code, "")
    detail_fields = f"id version createdAt {content_fields}".strip()

    detail_ids: dict[str, str] = {}       # matter_id → newest detail id
    skip_ids: set[str] = set()            # matter_ids where content is unchanged

    if detail_relation:
        parts = []
        for i, mid in enumerate(matter_ids):
            parts.append(
                f'm{i}: matter(where: {{ id_EQ: "{mid}" }}) '
                f'{{ analysisStatus {detail_relation} {{ {detail_fields} }} }}'
            )
        for batch_start in range(0, len(parts), 10):
            batch = parts[batch_start:batch_start + 10]
            try:
                resp = requests.post(
                    graphql_endpoint,
                    json={"query": "{ " + " ".join(batch) + " }"},
                    headers=headers,
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json().get("data", {})
                for i, mid in enumerate(matter_ids[batch_start:batch_start + len(batch)]):
                    matters = data.get(f"m{i}", [])
                    if not matters:
                        continue
                    matter = matters[0]
                    analysis_status = matter.get("analysisStatus")
                    details = matter.get(detail_relation) or []
                    if details:
                        # Newest non-AI detail by createdAt
                        import_details = sorted(
                            [d for d in details if not (d.get("version") or "").startswith("AI_")],
                            key=lambda d: d.get("createdAt") or "",
                            reverse=True,
                        )
                        if import_details:
                            detail_ids[mid] = import_details[0]["id"]
                    if not _matter_data_changed(analysis_status, details):
                        skip_ids.add(mid)
                        logger.info("Matter %s: content unchanged — skipping AI trigger", mid[:8])
            except Exception as e:
                logger.warning("Failed to query matter details for batch %d: %s", batch_start, e)

    # Trigger analysis one-by-one (skip unchanged matters)
    triggered = 0
    for mid in matter_ids:
        if mid in skip_ids:
            continue
        detail_id = detail_ids.get(mid)
        update_fields = 'analysisStatus_SET: "pending"'
        if detail_id:
            update_fields += f', pendingAnalysisMatterDetailId_SET: "{detail_id}", pendingAnalysisDirectoryCode_SET: "{directory_code}"'
        query = f"""mutation {{
            updateMatter(
                where: {{ id_EQ: "{mid}" }}
                update: {{ {update_fields} }}
            ) {{ matter {{ id analysisStatus }} }}
        }}"""
        try:
            resp = requests.post(
                graphql_endpoint,
                json={"query": query},
                headers=headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            triggered += 1
        except Exception as e:
            logger.warning("Failed to trigger analysis for matter %s: %s", mid[:8], e)

    if triggered:
        logger.info("Triggered AI analysis for %d/%d matters (%d with detail IDs)", triggered, len(matter_ids), len(detail_ids))


@app.post("/api/import/{file_id}")
def import_file(
    file_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """
    Import a directory submission file into the graph.

    5-phase pipeline:
    1. Read payload + normalize
    2. Create DptLegalField + Submission via GraphQL (triggers skeleton)
    3. Bulk Cypher for People, Matters, Clients, Awards
    4. Intra-file dedup
    5. Visibility Cypher
    """
    _check_api_key(x_api_key)

    # Import here to avoid startup dependency on neo4j driver.
    # In Docker: module is at /app/rankellix_import/ (copied by cloudbuild).
    # In dev: module is at ../../scripts/ relative to this file.
    _scripts_dir = str(Path(__file__).parent.parent.parent / "scripts")
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    from rankellix_import.executor import execute_import

    graphology_url = os.environ.get("GRAPHOLOGY_URL", "http://localhost:4000")
    graphql_endpoint = graphology_url + ("/graphql" if not graphology_url.endswith("/graphql") else "")
    graphql_api_key = os.environ.get("GRAPHOLOGY_API_KEY", "")
    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
    neo4j_database = os.environ.get("NEO4J_DATABASE", "neo4j")

    try:
        result = execute_import(
            file_id=file_id,
            neo4j_uri=neo4j_uri,
            neo4j_user=neo4j_user,
            neo4j_password=neo4j_password,
            graphql_endpoint=graphql_endpoint,
            database=neo4j_database,
            graphql_api_key=graphql_api_key,
        )

        # Trigger AI matter analysis by setting analysisStatus="pending" on each matter.
        # This fires a GraphQL updateMatter mutation which the Apollo AI Workflow
        # Trigger Middleware intercepts (PROPERTY_CHANGED on analysisStatus) and
        # dispatches /run-agent with the matter_rewrite agent.
        matter_ids = result.get("matterIds") or []
        directory = result.get("directory", "")
        dir_to_code = {"chambers": "CH", "itr": "ITR", "iflr1000": "IFLR", "legal500": "L500", "leadersleague": "LL"}
        dir_code = dir_to_code.get(directory.lower().strip(), "")
        if matter_ids and result.get("success") and dir_code:
            _trigger_matter_analysis(matter_ids, dir_code, graphql_endpoint, graphql_api_key)

        return result
    except Exception as e:
        logger.exception("Import failed for file %s", file_id)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Run Prompt Endpoint
# ============================================================================


@app.post("/run-prompt")
def run_prompt(
    request: RunPromptRequest,
    x_api_key: Optional[str] = Header(None),
):
    """
    Run a TEA agent with arbitrary input state (no graphology context).

    Designed for agents like llm_prompt that don't need a context node.
    Requires x-api-key header.
    """
    _check_api_key(x_api_key)

    result = _load_and_run_prompt(
        agent=request.agent,
        input_state=request.input_state,
    )
    return result
