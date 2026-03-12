"""
Agent Invocation and Classification Cache Actions for TEA YAMLEngine.

Story: 16.2 - TIER 3 LLM Classification Agent

Actions:
- agents.invoke_agent: Load and run a YAML agent as a sub-agent
- agents.cache_get: Check classification cache by content hash
- agents.cache_set: Store classification result in cache by content hash

Cache:
- Module-level dict cache (single-process). Content hash as key.
- TTL: 30 days (2,592,000 seconds).
"""

import hashlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

# =============================================================================
# CLASSIFICATION CACHE (Story 16.2, Task 2)
# =============================================================================

CLASSIFICATION_CACHE_TTL = 2_592_000  # 30 days in seconds

_classification_cache: Dict[str, Any] = {}
_classification_cache_ts: Dict[str, float] = {}
_cache_lock = threading.Lock()


def _cache_key(document_text: str) -> str:
    """Generate cache key from document text content hash."""
    return f"classify:file:{hashlib.sha256(document_text.encode()).hexdigest()}"


def cache_get_classification(
    state: Dict[str, Any],
    document_text: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """
    Check classification cache for a previously classified document.

    TEA Custom Action: agents.cache_get

    Args:
        state: Current agent state
        document_text: Text to check cache for

    Returns:
        Dict with hit=True and cached result, or hit=False
    """
    if not document_text:
        return {"hit": False}

    key = _cache_key(document_text)

    with _cache_lock:
        if key in _classification_cache:
            ts = _classification_cache_ts.get(key, 0)
            if time.time() - ts < CLASSIFICATION_CACHE_TTL:
                logger.info(f"agents.cache_get: Cache HIT for {key[:40]}...")
                return {"hit": True, **_classification_cache[key]}
            else:
                # Expired
                del _classification_cache[key]
                _classification_cache_ts.pop(key, None)

    return {"hit": False}


def cache_set_classification(
    state: Dict[str, Any],
    document_text: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """
    Store classification result in cache.

    TEA Custom Action: agents.cache_set

    Caches the current classification state fields by content hash.

    Args:
        state: Current agent state (must contain classification results)
        document_text: Text to use as cache key

    Returns:
        Dict with cached=True
    """
    if not document_text:
        return {"cached": False}

    # Only cache successful classifications
    if state.get("status") != "success":
        return {"cached": False}

    key = _cache_key(document_text)
    result = {
        "directory": state.get("directory", ""),
        "category": state.get("category", ""),
        "region_country": state.get("region_country", ""),
        "region_state": state.get("region_state", ""),
        "region_country_display": state.get("region_country_display", ""),
        "region_state_display": state.get("region_state_display", ""),
        "confidence": state.get("confidence", 0.0),
        "year": state.get("year", ""),
        "is_empty_form": state.get("is_empty_form", False),
        "status": "success",
    }

    with _cache_lock:
        _classification_cache[key] = result
        _classification_cache_ts[key] = time.time()

    logger.info(f"agents.cache_set: Cached result for {key[:40]}...")
    return {"cached": True}


# =============================================================================
# SUB-AGENT INVOCATION (Story 16.2, Task 3)
# =============================================================================

def invoke_agent(
    state: Dict[str, Any],
    agent_name: str = "",
    input_state: Dict[str, Any] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Load and run a YAML agent as a sub-agent.

    TEA Custom Action: agents.invoke_agent

    Args:
        state: Current agent state
        agent_name: Name of agent file (without .yaml extension)
        input_state: Input state to pass to the sub-agent

    Returns:
        Dict with success, result (final sub-agent state)
    """
    agents_dir = kwargs.get("agents_dir") or os.environ.get(
        "AGENTS_DIR", str(Path(__file__).parent.parent / "agents")
    )

    agent_path = Path(agents_dir) / f"{agent_name}.yaml"
    if not agent_path.exists():
        return {"success": False, "error": f"Sub-agent not found: {agent_name}"}

    input_state = input_state or {}

    try:
        # Ensure actions directory is on sys.path for graphology import
        actions_dir_path = str(Path(agents_dir).parent / "actions")
        if actions_dir_path not in sys.path:
            sys.path.insert(0, actions_dir_path)

        from the_edge_agent import YAMLEngine

        engine = YAMLEngine()

        # Register all actions (graphology + agents)
        from graphology import register_actions as register_graphology
        register_graphology(engine.actions_registry, engine)
        register_actions(engine.actions_registry, engine)

        # Copy variables from parent
        parent_variables = state.get("variables", {})
        for k, v in parent_variables.items():
            engine.variables[k] = v

        # Also set from env
        engine.variables.setdefault(
            "GRAPHOLOGY_URL",
            os.environ.get("GRAPHOLOGY_URL", "http://localhost:4000"),
        )

        graph = engine.load_from_file(str(agent_path))

        final_state = None
        for event in graph.invoke(input_state):
            final_state = event

        agent_state = {}
        if final_state and isinstance(final_state, dict):
            agent_state = final_state.get("state", final_state)

        return {"success": True, "result": agent_state}

    except Exception as e:
        logger.error(f"agents.invoke_agent failed: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# ACTION REGISTRATION
# =============================================================================

def register_actions(registry: Dict[str, Callable], engine: Any) -> None:
    """
    Register agent actions with the TEA YAMLEngine.

    Args:
        registry: Action registry dictionary
        engine: YAMLEngine instance
    """
    registry["agents.invoke_agent"] = invoke_agent
    registry["agents.cache_get"] = cache_get_classification
    registry["agents.cache_set"] = cache_set_classification

    logger.info(
        "Agent actions registered: "
        "agents.invoke_agent, agents.cache_get, agents.cache_set"
    )
