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
    DIRECTORY_CODE_TO_MATTER_DETAIL_TYPE,
    _relationship_to_field_name,
    connect_nodes,
    create_node,
    get_matter_context,
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
# get_matter_context tests (Story MA-3a)
# =============================================================================

class TestDirectoryCodeMapping:
    """Tests for directory code to MatterDetail subtype mapping."""

    def test_ch_maps_to_chambers(self):
        assert DIRECTORY_CODE_TO_MATTER_DETAIL_TYPE["CH"] == "ChambersMatterDetail"

    def test_l500_maps_to_legal500(self):
        assert DIRECTORY_CODE_TO_MATTER_DETAIL_TYPE["L500"] == "Legal500MatterDetail"

    def test_ll_maps_to_leaders_league(self):
        assert DIRECTORY_CODE_TO_MATTER_DETAIL_TYPE["LL"] == "LeadersLeagueMatterDetail"

    def test_itr_maps_to_itr(self):
        assert DIRECTORY_CODE_TO_MATTER_DETAIL_TYPE["ITR"] == "ItrMatterDetail"

    def test_iflr_maps_to_iflr1000(self):
        assert DIRECTORY_CODE_TO_MATTER_DETAIL_TYPE["IFLR"] == "Iflr1000MatterDetail"


class TestGetMatterContext:
    """Tests for graphology.get_matter_context action."""

    def test_missing_matter_id(self):
        result = get_matter_context(
            {}, matter_id="", matter_detail_id="d-1", directory_code="CH",
        )
        assert result["success"] is False
        assert "matter_id is required" in result["error"]

    def test_missing_matter_detail_id(self):
        result = get_matter_context(
            {}, matter_id="m-1", matter_detail_id="", directory_code="CH",
        )
        assert result["success"] is False
        assert "matter_detail_id is required" in result["error"]

    def test_missing_directory_code(self):
        result = get_matter_context(
            {}, matter_id="m-1", matter_detail_id="d-1", directory_code="",
        )
        assert result["success"] is False
        assert "directory_code is required" in result["error"]

    def test_invalid_directory_code(self):
        result = get_matter_context(
            {}, matter_id="m-1", matter_detail_id="d-1", directory_code="INVALID",
        )
        assert result["success"] is False
        assert "Invalid directory_code" in result["error"]
        assert "INVALID" in result["error"]
        # Should list valid codes
        assert "CH" in result["error"]
        assert "IFLR" in result["error"]

    @patch("actions.graphology._get_type_scalar_fields")
    @patch("actions.graphology._execute_graphql")
    def test_success_ch(self, mock_gql, mock_fields):
        mock_fields.return_value = ["id", "description", "otherLawyers", "otherInformation"]
        mock_gql.return_value = {
            "matter": [{
                "id": "m-1",
                "mTitle": "Test Matter",
                "mStatus": "open",
                "mValue": "high",
                "isCrossBorder": False,
                "isConfidential": False,
                "dateClosed": None,
                "dateOpened": "2025-01-01",
                "firmRole": "lead",
                "jurisdiction": "UK",
                "industrySector": "finance",
                "matterSubCategory": None,
                "matterHasMatterDetail": [{
                    "id": "d-1",
                    "__typename": "ChambersMatterDetail",
                    "description": "A complex deal",
                    "otherLawyers": "Smith & Co",
                    "otherInformation": "Notable",
                }],
                "matterHasClient": [{
                    "id": "c-1",
                    "name": "Acme Corp",
                    "industry": "tech",
                }],
                "departmentHasMatterFrom": [{
                    "id": "dept-1",
                    "deptName": "Corporate",
                    "legalFirmHasDepartmentFrom": [{
                        "id": "firm-1",
                        "firmName": "BigLaw LLP",
                    }],
                }],
            }],
        }

        result = get_matter_context(
            {},
            matter_id="m-1",
            matter_detail_id="d-1",
            directory_code="CH",
            graphql_url="http://localhost:4000",
        )

        assert result["success"] is True
        assert result["directory"] == "CH"
        assert result["matter"]["id"] == "m-1"
        assert result["matter"]["mTitle"] == "Test Matter"
        # Relations should be extracted out of matter
        assert "matterHasMatterDetail" not in result["matter"]
        assert "matterHasClient" not in result["matter"]
        assert "departmentHasMatterFrom" not in result["matter"]
        # Detail
        assert result["detail"]["id"] == "d-1"
        assert result["detail"]["description"] == "A complex deal"
        # Client
        assert result["client"]["id"] == "c-1"
        assert result["client"]["name"] == "Acme Corp"
        # Department
        assert result["department"]["id"] == "dept-1"
        assert result["department"]["deptName"] == "Corporate"
        # JSON serialization
        assert "matter_context_json" in result

    @patch("actions.graphology._get_type_scalar_fields")
    @patch("actions.graphology._execute_graphql")
    def test_query_uses_inline_fragment(self, mock_gql, mock_fields):
        """Verify the GraphQL query includes an inline fragment for the correct subtype."""
        mock_fields.return_value = ["id", "summary"]
        mock_gql.return_value = {"matter": []}

        get_matter_context(
            {},
            matter_id="m-1",
            matter_detail_id="d-1",
            directory_code="L500",
            graphql_url="http://localhost:4000",
        )

        # Inspect the query string passed to _execute_graphql
        call_args = mock_gql.call_args
        query = call_args[0][1]
        assert "... on Legal500MatterDetail" in query
        assert "id summary" in query

    @patch("actions.graphology._get_type_scalar_fields")
    @patch("actions.graphology._execute_graphql")
    def test_matter_not_found(self, mock_gql, mock_fields):
        mock_fields.return_value = ["id"]
        mock_gql.return_value = {"matter": []}

        result = get_matter_context(
            {},
            matter_id="nonexistent",
            matter_detail_id="d-1",
            directory_code="ITR",
            graphql_url="http://localhost:4000",
        )

        assert result["success"] is False
        assert "Matter not found" in result["error"]

    @patch("actions.graphology._get_type_scalar_fields")
    @patch("actions.graphology._execute_graphql")
    def test_missing_relations_returns_none(self, mock_gql, mock_fields):
        """When Matter has no Client or Department, those fields should be None."""
        mock_fields.return_value = ["id"]
        mock_gql.return_value = {
            "matter": [{
                "id": "m-1",
                "mTitle": "Solo Matter",
                "mStatus": None,
                "mValue": None,
                "isCrossBorder": None,
                "isConfidential": None,
                "dateClosed": None,
                "dateOpened": None,
                "firmRole": None,
                "jurisdiction": None,
                "industrySector": None,
                "matterSubCategory": None,
                "matterHasMatterDetail": [],
                "matterHasClient": [],
                "departmentHasMatterFrom": [],
            }],
        }

        result = get_matter_context(
            {},
            matter_id="m-1",
            matter_detail_id="d-1",
            directory_code="LL",
            graphql_url="http://localhost:4000",
        )

        assert result["success"] is True
        assert result["detail"] is None
        assert result["client"] is None
        assert result["department"] is None

    @patch("actions.graphology._get_type_scalar_fields")
    @patch("actions.graphology._execute_graphql")
    def test_graphql_error(self, mock_gql, mock_fields):
        mock_fields.return_value = ["id"]
        mock_gql.side_effect = RuntimeError("GraphQL errors: Type not found")

        result = get_matter_context(
            {},
            matter_id="m-1",
            matter_detail_id="d-1",
            directory_code="IFLR",
            graphql_url="http://localhost:4000",
        )

        assert result["success"] is False
        assert "Type not found" in result["error"]

    @patch("actions.graphology._get_type_scalar_fields")
    @patch("actions.graphology._execute_graphql")
    def test_connection_error(self, mock_gql, mock_fields):
        mock_fields.return_value = ["id"]
        mock_gql.side_effect = ConnectionError("Cannot connect")

        result = get_matter_context(
            {},
            matter_id="m-1",
            matter_detail_id="d-1",
            directory_code="CH",
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

    def test_get_matter_context_registered(self):
        registry = {}
        register_actions(registry, MagicMock())
        assert "graphology.get_matter_context" in registry
        assert registry["graphology.get_matter_context"] is get_matter_context
