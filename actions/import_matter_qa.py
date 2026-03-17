"""
Import Matter Q&A Custom Actions for TEA YAMLEngine.

Story: RX.40 - ProtoMatter traceability

Actions:
- import_matter_qa.finalize_proto_matter: Post-processing for ProtoMatter sources.
  Updates ProtoMatter.status and creates PROTO_MATTER_IMPORTED_AS relation.
"""

import logging
from typing import Any, Callable, Dict

from graphology import (
    _execute_graphql,
    _get_graphql_api_key,
    _get_graphql_url,
)

logger = logging.getLogger(__name__)


# =============================================================================
# GraphQL MUTATIONS
# =============================================================================

UPDATE_PROTO_MATTER_STATUS = """
mutation UpdateProtoMatter($id: ID!, $status: String!) {
  updateProtoMatter(
    where: { id_EQ: $id }
    update: { status_SET: $status }
  ) { protoMatter { id status } }
}
"""

LINK_PROTO_MATTER_TO_MATTER = """
mutation LinkProtoMatterToMatter($protoMatterId: ID!, $matterId: ID!) {
  updateProtoMatter(
    where: { id_EQ: $protoMatterId }
    update: {
      protoMatterImportedAs: {
        connect: { where: { node: { id_EQ: $matterId } } }
      }
    }
  ) { protoMatter { id } }
}
"""


# =============================================================================
# TEA CUSTOM ACTIONS
# =============================================================================

def finalize_proto_matter(
    state: Dict[str, Any],
    **kwargs,
) -> Dict[str, Any]:
    """
    Post-processing for ProtoMatter traceability (Story RX.40).

    TEA Custom Action: import_matter_qa.finalize_proto_matter

    When the source context_node is a ProtoMatter:
      - Updates ProtoMatter.status to "imported" (or "error" on failure)
      - Creates PROTO_MATTER_IMPORTED_AS relation from ProtoMatter to Matter

    Args:
        state: Current agent state (needs context_result, save_result,
               context_node_id, matter_id)

    Returns:
        Dict with source_node_type, proto_matter_id, error (if any)
    """
    context_result = state.get("context_result", {})
    node_type = context_result.get("node_type", "")

    # Only run for ProtoMatter sources
    if node_type != "ProtoMatter":
        return {"source_node_type": node_type}

    save_result = state.get("save_result", {})
    context_node_id = state.get("context_node_id", "")
    matter_id = state.get("matter_id") or context_node_id
    proto_matter_id = context_node_id

    graphql_url = _get_graphql_url(kwargs, state)
    api_key = _get_graphql_api_key(kwargs, state)

    save_success = save_result.get("success", False) if save_result else False
    new_status = "imported" if save_success else "error"

    errors = []

    # Update ProtoMatter.status
    try:
        _execute_graphql(
            graphql_url,
            UPDATE_PROTO_MATTER_STATUS,
            {"id": proto_matter_id, "status": new_status},
            api_key=api_key,
        )
    except Exception as e:
        errors.append(f"Status update: {e}")

    # Create PROTO_MATTER_IMPORTED_AS relation (only on success)
    if save_success and matter_id and matter_id != proto_matter_id:
        try:
            _execute_graphql(
                graphql_url,
                LINK_PROTO_MATTER_TO_MATTER,
                {"protoMatterId": proto_matter_id, "matterId": matter_id},
                api_key=api_key,
            )
        except Exception as e:
            errors.append(f"Link creation: {e}")

    return {
        "source_node_type": "ProtoMatter",
        "proto_matter_id": proto_matter_id,
        "error": "; ".join(errors) if errors else "",
    }


# =============================================================================
# ACTION REGISTRATION
# =============================================================================

def register_actions(registry: Dict[str, Callable], engine: Any) -> None:
    """Register import_matter_qa actions with the TEA YAMLEngine."""
    registry["import_matter_qa.finalize_proto_matter"] = finalize_proto_matter

    logger.info(
        "Import Matter QA actions registered: "
        "import_matter_qa.finalize_proto_matter"
    )


__tea_actions__ = {
    "version": "1.0.0",
    "description": "ProtoMatter traceability post-processing for Import Matter QA agent",
    "actions": ["import_matter_qa.finalize_proto_matter"],
}
