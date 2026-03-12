"""
Tests for graphology create_node and connect_nodes actions (Story 16.4).

Tests the new actions in actions/graphology.py:
- graphology.create_node: Creates a node via GraphQL mutation (AC5)
- graphology.connect_nodes: Connects two nodes via relationship (AC6)
- _relationship_to_field_name: Converts UPPER_SNAKE_CASE to camelCase

Usage:
    pytest tests/test_graphology_actions.py -v
"""

from unittest.mock import MagicMock, patch

import pytest

from actions.graphology import (
    _relationship_to_field_name,
    connect_nodes,
    create_node,
    register_actions,
)


# =============================================================================
# _relationship_to_field_name tests
# =============================================================================

class TestRelationshipToFieldName:
    """Tests for UPPER_SNAKE_CASE → camelCase conversion."""

    def test_file_has_proto_matter(self):
        assert _relationship_to_field_name("FILE_HAS_PROTO_MATTER") == "fileHasProtoMatter"

    def test_department_has_proto_matter(self):
        assert _relationship_to_field_name("DEPARTMENT_HAS_PROTO_MATTER") == "departmentHasProtoMatter"

    def test_single_word(self):
        assert _relationship_to_field_name("RELATES") == "relates"

    def test_two_words(self):
        assert _relationship_to_field_name("HAS_STEP") == "hasStep"


# =============================================================================
# create_node tests (AC5)
# =============================================================================

class TestCreateNode:
    """Tests for graphology.create_node action."""

    def test_missing_node_type(self):
        result = create_node({}, node_type="", properties={"payload": "test"})
        assert result["success"] is False
        assert "node_type is required" in result["error"]

    def test_missing_properties(self):
        result = create_node({}, node_type="ProtoMatter", properties={})
        assert result["success"] is False
        assert "properties" in result["error"]

    @patch("actions.graphology._execute_graphql")
    def test_success(self, mock_gql):
        mock_gql.return_value = {
            "createProtoMatters": {
                "protoMatters": [{"id": "proto-123"}]
            }
        }
        result = create_node(
            {},
            node_type="ProtoMatter",
            properties={"payload": "{}", "directory": "chambers", "status": "pending"},
            graphql_url="http://localhost:4000",
        )
        assert result["success"] is True
        assert result["node_id"] == "proto-123"

        # Verify mutation structure — plural name, list input
        call_args = mock_gql.call_args
        assert call_args[0][0] == "http://localhost:4000"
        mutation = call_args[0][1]
        assert "createProtoMatters" in mutation
        assert "[ProtoMatterCreateInput!]!" in mutation
        variables = call_args[0][2]
        assert isinstance(variables["input"], list)
        assert variables["input"][0]["payload"] == "{}"
        assert variables["input"][0]["directory"] == "chambers"

    @patch("actions.graphology._execute_graphql")
    def test_graphql_error(self, mock_gql):
        mock_gql.side_effect = RuntimeError("GraphQL errors: Invalid input")
        result = create_node(
            {},
            node_type="ProtoMatter",
            properties={"payload": "{}"},
            graphql_url="http://localhost:4000",
        )
        assert result["success"] is False
        assert "Invalid input" in result["error"]

    @patch("actions.graphology._execute_graphql")
    def test_connection_error(self, mock_gql):
        mock_gql.side_effect = ConnectionError("Cannot connect")
        result = create_node(
            {},
            node_type="ProtoMatter",
            properties={"payload": "{}"},
            graphql_url="http://localhost:4000",
        )
        assert result["success"] is False
        assert "Cannot connect" in result["error"]

    @patch("actions.graphology._execute_graphql")
    def test_empty_response(self, mock_gql):
        mock_gql.return_value = {"createProtoMatters": {"protoMatters": []}}
        result = create_node(
            {},
            node_type="ProtoMatter",
            properties={"payload": "{}"},
            graphql_url="http://localhost:4000",
        )
        assert result["success"] is False
        assert "No node returned" in result["error"]


# =============================================================================
# connect_nodes tests (AC6)
# =============================================================================

class TestConnectNodes:
    """Tests for graphology.connect_nodes action."""

    def test_missing_source_id(self):
        result = connect_nodes(
            {}, source_id="", source_type="ApplicationFormFile",
            relationship_name="FILE_HAS_PROTO_MATTER",
            target_id="proto-1", target_type="ProtoMatter",
        )
        assert result["success"] is False
        assert "source_id" in result["error"]

    def test_missing_source_type(self):
        result = connect_nodes(
            {}, source_id="file-1", source_type="",
            relationship_name="FILE_HAS_PROTO_MATTER",
            target_id="proto-1", target_type="ProtoMatter",
        )
        assert result["success"] is False
        assert "source_type" in result["error"]

    def test_missing_relationship_name(self):
        result = connect_nodes(
            {}, source_id="file-1", source_type="ApplicationFormFile",
            relationship_name="",
            target_id="proto-1", target_type="ProtoMatter",
        )
        assert result["success"] is False
        assert "relationship_name" in result["error"]

    def test_missing_target_id(self):
        result = connect_nodes(
            {}, source_id="file-1", source_type="ApplicationFormFile",
            relationship_name="FILE_HAS_PROTO_MATTER",
            target_id="", target_type="ProtoMatter",
        )
        assert result["success"] is False
        assert "target_id" in result["error"]

    def test_missing_target_type(self):
        result = connect_nodes(
            {}, source_id="file-1", source_type="ApplicationFormFile",
            relationship_name="FILE_HAS_PROTO_MATTER",
            target_id="proto-1", target_type="",
        )
        assert result["success"] is False
        assert "target_type" in result["error"]

    @patch("actions.graphology._execute_graphql")
    def test_success(self, mock_gql):
        mock_gql.return_value = {
            "updateApplicationFormFile": {
                "applicationFormFile": [{"id": "file-1"}]
            }
        }
        result = connect_nodes(
            {},
            source_id="file-1",
            source_type="ApplicationFormFile",
            relationship_name="FILE_HAS_PROTO_MATTER",
            target_id="proto-1",
            target_type="ProtoMatter",
            graphql_url="http://localhost:4000",
        )
        assert result["success"] is True

        # Verify mutation uses correct camelCase field name with list wrapping
        call_args = mock_gql.call_args
        variables = call_args[0][2]
        assert variables["where"]["id_EQ"] == "file-1"
        assert "fileHasProtoMatter" in variables["update"]
        rel_list = variables["update"]["fileHasProtoMatter"]
        assert isinstance(rel_list, list)
        connect_list = rel_list[0]["connect"]
        assert isinstance(connect_list, list)
        assert connect_list[0]["where"]["node"]["id_EQ"] == "proto-1"

    @patch("actions.graphology._execute_graphql")
    def test_graphql_error(self, mock_gql):
        mock_gql.side_effect = RuntimeError("GraphQL errors: Relationship not found")
        result = connect_nodes(
            {},
            source_id="file-1",
            source_type="ApplicationFormFile",
            relationship_name="FILE_HAS_PROTO_MATTER",
            target_id="proto-1",
            target_type="ProtoMatter",
            graphql_url="http://localhost:4000",
        )
        assert result["success"] is False
        assert "Relationship not found" in result["error"]

    @patch("actions.graphology._execute_graphql")
    def test_connection_error(self, mock_gql):
        mock_gql.side_effect = ConnectionError("Cannot connect")
        result = connect_nodes(
            {},
            source_id="file-1",
            source_type="ApplicationFormFile",
            relationship_name="FILE_HAS_PROTO_MATTER",
            target_id="proto-1",
            target_type="ProtoMatter",
            graphql_url="http://localhost:4000",
        )
        assert result["success"] is False
        assert "Cannot connect" in result["error"]


# =============================================================================
# Registration test
# =============================================================================

class TestRegistration:
    """Test that new actions are registered."""

    def test_create_node_registered(self):
        registry = {}
        register_actions(registry, MagicMock())
        assert "graphology.create_node" in registry
        assert registry["graphology.create_node"] is create_node

    def test_connect_nodes_registered(self):
        registry = {}
        register_actions(registry, MagicMock())
        assert "graphology.connect_nodes" in registry
        assert registry["graphology.connect_nodes"] is connect_nodes
