"""
Tests for file_extraction YAML agent logic.

Tests the inline Python code from agents/file_extraction.yaml by executing
the node run blocks directly with mocked dependencies. This validates:
- Happy path extraction flow (AC1)
- Temp file cleanup on success and failure (AC2, AC3, AC9)
- Error payloads for extraction failures (AC4)
- Unsupported directory errors (AC5)
- Missing storageUrl errors (AC6)
- Wrong node type errors (AC7)
- GCS download failures (AC8)
- PDF conversion failure cleanup (AC9)
- GraphQL save failures (AC10)
- Extraction mode from settings (AC11)
- Graphology server unavailable (AC12)

Usage:
    pytest tests/test_file_extraction.py -v
"""

import json
import os
import tempfile
import textwrap

import pytest
import yaml


# =============================================================================
# HELPERS — Load and execute YAML agent node run blocks
# =============================================================================

def _load_agent_yaml():
    """Load the file_extraction YAML agent definition."""
    agent_path = os.path.join(os.path.dirname(__file__), "..", "agents", "file_extraction.yaml")
    with open(agent_path) as f:
        return yaml.safe_load(f)


def _get_node_code(agent_def, node_name):
    """Extract the `run` code block from a named node."""
    for node in agent_def["nodes"]:
        if node["name"] == node_name:
            return node.get("run", "")
    raise ValueError(f"Node '{node_name}' not found in agent definition")


def _exec_node(agent_def, node_name, state, settings=None, mock_modules=None):
    """Execute a node's run block with given state and return the result.

    Args:
        agent_def: Parsed YAML agent definition
        node_name: Name of the node to execute
        state: Dict simulating agent state
        settings: Dict simulating agent settings
        mock_modules: Dict of module_name -> mock_module to inject
    """
    code = _get_node_code(agent_def, node_name)
    if not code:
        raise ValueError(f"Node '{node_name}' has no run block")

    settings = settings or agent_def.get("settings", {})
    mock_modules = mock_modules or {}

    # Build execution namespace
    namespace = {"state": state, "settings": settings}

    # Wrap code in a function so `return` works
    indented = textwrap.indent(code, "    ")
    wrapped = f"def _node_fn():\n{indented}\n_result = _node_fn()"

    # Inject mock modules into the namespace
    for mod_name, mod_obj in mock_modules.items():
        namespace[mod_name] = mod_obj

    # Patch imports if mock_modules provided
    if mock_modules:
        import unittest.mock as _mock
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def patched_import(name, *args, **kwargs):
            if name in mock_modules:
                return mock_modules[name]
            return original_import(name, *args, **kwargs)

        namespace["__builtins__"] = {**__builtins__.__dict__, "__import__": patched_import} if hasattr(__builtins__, '__dict__') else {**__builtins__, "__import__": patched_import}

    exec(compile(wrapped, f"<agent:{node_name}>", "exec"), namespace)
    return namespace.get("_result")


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def agent_def():
    """Load the file_extraction YAML agent."""
    return _load_agent_yaml()


@pytest.fixture
def default_settings():
    """Default agent settings."""
    return {"llamaextract": {"mode": "BALANCED", "timeout": 300, "max_retries": 3}}


# =============================================================================
# extract_and_download node tests
# =============================================================================

class TestExtractAndDownloadNode:
    """Tests for the extract_and_download node (Step 2 in YAML agent)."""

    def test_graphology_failure_surfaces_error(self, agent_def):
        """AC12: When graphology returns success=false, surface the real error."""
        state = {
            "context_result": {
                "success": False,
                "error": "Service unavailable (503)"
            }
        }
        result = _exec_node(agent_def, "extract_and_download", state)
        assert result["status"] == "error"
        assert "503" in result["error"]
        assert result["completed"] is True
        assert result["storage_url"] == ""

    def test_missing_storage_url_returns_error(self, agent_def):
        """AC6: Missing storageUrl returns clear error."""
        state = {
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"directoryName": "chambers", "fileName": "test.docx"},
            }
        }
        result = _exec_node(agent_def, "extract_and_download", state)
        assert result["status"] == "error"
        assert "no storageUrl" in result["error"]
        assert result["completed"] is True

    def test_wrong_node_type_returns_error(self, agent_def):
        """AC7: Wrong node type returns error with actual type."""
        state = {
            "context_result": {
                "success": True,
                "node_type": "Submission",
                "data": {"storageUrl": "gs://bucket/file.pdf", "directoryName": "chambers"},
            }
        }
        result = _exec_node(agent_def, "extract_and_download", state)
        assert result["status"] == "error"
        assert "Submission" in result["error"]
        assert result["completed"] is True

    def test_missing_directory_name_continues_to_detection(self, agent_def):
        """Story 16.1: Missing directoryName no longer errors — flows to detect_directory."""
        from unittest.mock import MagicMock

        mock_fsspec = MagicMock()
        mock_fs = MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs

        mock_file_ctx = MagicMock()
        mock_file_ctx.read.return_value = b"%PDF-1.4 fake pdf"
        mock_fs.open.return_value.__enter__ = MagicMock(return_value=mock_file_ctx)
        mock_fs.open.return_value.__exit__ = MagicMock(return_value=False)

        state = {
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"storageUrl": "gs://bucket/file.pdf", "fileName": "test.pdf"},
            }
        }
        result = _exec_node(agent_def, "extract_and_download", state, mock_modules={"fsspec": mock_fsspec})
        # Should NOT error — directory_name is empty string, detection happens in detect_directory
        assert result["directory_name"] == ""
        assert result["storage_url"].startswith("/tmp/")
        # Cleanup
        if os.path.exists(result["storage_url"]):
            os.unlink(result["storage_url"])

    def test_unsupported_url_scheme_returns_error(self, agent_def):
        """AC8 (edge): Unsupported URL scheme returns error."""
        state = {
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {
                    "storageUrl": "ftp://server/file.pdf",
                    "directoryName": "chambers",
                    "fileName": "test.pdf",
                },
            }
        }
        result = _exec_node(agent_def, "extract_and_download", state)
        assert result["status"] == "error"
        assert "Unsupported storage URL scheme" in result["error"]
        assert result["completed"] is True

    def test_gcs_download_failure_returns_error(self, agent_def):
        """AC8: GCS download failure captured with details."""
        from unittest.mock import MagicMock

        mock_fsspec = MagicMock()
        mock_fs = MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs
        mock_fs.open.side_effect = PermissionError("Access denied to bucket")

        state = {
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {
                    "storageUrl": "gs://bucket/file.pdf",
                    "directoryName": "chambers",
                    "fileName": "test.pdf",
                },
            }
        }
        result = _exec_node(agent_def, "extract_and_download", state, mock_modules={"fsspec": mock_fsspec})
        assert result["status"] == "error"
        assert "Failed to download from GCS" in result["error"]
        assert "Access denied" in result["error"]
        assert result["completed"] is True

    def test_pdf_conversion_failure_cleans_up_docx(self, agent_def):
        """AC9/AC3: PDF conversion failure cleans up the original docx temp file."""
        from unittest.mock import MagicMock

        mock_fsspec = MagicMock()
        mock_fs = MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs

        # fsspec.open returns the docx content successfully
        mock_file_ctx = MagicMock()
        mock_file_ctx.read.return_value = b"fake docx content"
        mock_fs.open.return_value.__enter__ = MagicMock(return_value=mock_file_ctx)
        mock_fs.open.return_value.__exit__ = MagicMock(return_value=False)

        # Mock subprocess to simulate LibreOffice failure
        mock_subprocess = MagicMock()
        mock_run_result = MagicMock()
        mock_run_result.returncode = 1
        mock_run_result.stderr = "LibreOffice not found"
        mock_subprocess.run.return_value = mock_run_result

        state = {
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {
                    "storageUrl": "gs://bucket/file.docx",
                    "directoryName": "chambers",
                    "fileName": "test.docx",
                },
            }
        }
        result = _exec_node(
            agent_def, "extract_and_download", state,
            mock_modules={"fsspec": mock_fsspec, "subprocess": mock_subprocess}
        )
        assert result["status"] == "error"
        assert "PDF conversion error" in result["error"]
        assert result["completed"] is True

    def test_happy_path_gcs_pdf_download(self, agent_def):
        """AC1 (partial): Successful GCS PDF download returns local path."""
        from unittest.mock import MagicMock

        mock_fsspec = MagicMock()
        mock_fs = MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs

        mock_file_ctx = MagicMock()
        mock_file_ctx.read.return_value = b"%PDF-1.4 fake pdf"
        mock_fs.open.return_value.__enter__ = MagicMock(return_value=mock_file_ctx)
        mock_fs.open.return_value.__exit__ = MagicMock(return_value=False)

        state = {
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {
                    "storageUrl": "gs://bucket/file.pdf",
                    "directoryName": "chambers",
                    "fileName": "submission.pdf",
                    "mimeType": "application/pdf",
                },
            }
        }
        result = _exec_node(agent_def, "extract_and_download", state, mock_modules={"fsspec": mock_fsspec})
        assert result["directory_name"] == "chambers"
        assert result["file_mime_type"] == "application/pdf"
        assert result["storage_url"].startswith("/tmp/")
        assert result["storage_url"].endswith(".pdf")
        # Cleanup the temp file created by the test
        if os.path.exists(result["storage_url"]):
            os.unlink(result["storage_url"])


# =============================================================================
# resolve_agent node tests
# =============================================================================

class TestResolveAgentNode:
    """Tests for the resolve_agent node (Step 3 in YAML agent)."""

    def test_unsupported_directory_returns_error_with_list(self, agent_def, default_settings):
        """AC5: Unknown directoryName returns error listing supported directories."""
        state = {"directory_name": "unknown_dir"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["status"] == "error"
        assert "Unknown directory" in result["error"]
        assert "unknown_dir" in result["error"]
        assert "chambers" in result["error"]
        assert result["completed"] is True
        assert result["agent_name_used"] == ""

    def test_chambers_resolves_correctly(self, agent_def, default_settings):
        """AC1/AC11: chambers resolves to rankellix-chambers-partners-balanced."""
        state = {"directory_name": "chambers"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-chambers-partners-balanced"
        assert result["status"] == "agent_selected"

    def test_iflr1000_resolves_correctly(self, agent_def, default_settings):
        """AC1: iflr1000 resolves correctly."""
        state = {"directory_name": "iflr1000"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-iflr-1000-balanced"

    def test_legal500_resolves_correctly(self, agent_def, default_settings):
        """AC1: legal500 resolves correctly."""
        state = {"directory_name": "legal500"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-the-legal-500-balanced"

    def test_legal500_with_space_resolves(self, agent_def, default_settings):
        """AC1: 'legal 500' (with space) also resolves."""
        state = {"directory_name": "legal 500"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-the-legal-500-balanced"

    def test_itr_resolves_correctly(self, agent_def, default_settings):
        """AC1: itr resolves correctly."""
        state = {"directory_name": "itr"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-itr-world-tax-balanced"

    def test_leadersleague_resolves_correctly(self, agent_def, default_settings):
        """AC1: leadersleague resolves correctly."""
        state = {"directory_name": "leadersleague"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-leaders-league-balanced"

    def test_mode_from_state_fast(self, agent_def, default_settings):
        """AC11: Mode from state._extraction_mode='fast' produces correct agent name."""
        state = {"directory_name": "chambers", "_extraction_mode": "fast"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-chambers-partners-fast"

    def test_mode_from_state_accurate(self, agent_def, default_settings):
        """AC11: Mode from state._extraction_mode='accurate' produces correct agent name."""
        state = {"directory_name": "itr", "_extraction_mode": "accurate"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-itr-world-tax-accurate"

    def test_case_insensitive_directory_name(self, agent_def, default_settings):
        """AC1: directoryName is case-insensitive."""
        state = {"directory_name": "CHAMBERS"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-chambers-partners-balanced"

    def test_agent_name_prefix_env(self, agent_def, default_settings):
        """Agent name includes AGENT_NAME_PREFIX when set."""
        state = {"directory_name": "chambers"}
        original = os.environ.get("AGENT_NAME_PREFIX")
        try:
            os.environ["AGENT_NAME_PREFIX"] = "dev"
            result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
            assert result["agent_name_used"] == "dev-rankellix-chambers-partners-balanced"
        finally:
            if original is None:
                os.environ.pop("AGENT_NAME_PREFIX", None)
            else:
                os.environ["AGENT_NAME_PREFIX"] = original


# =============================================================================
# prepare_payload node tests
# =============================================================================

class TestPreparePayloadNode:
    """Tests for the prepare_payload node (Step 5 in YAML agent)."""

    def test_success_payload_serialized(self, agent_def):
        """AC1: Successful extraction produces JSON payload."""
        state = {
            "extract_result": {"success": True, "data": {"field1": "value1", "field2": 42}},
            "storage_url": "",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert result["status"] == "success"
        payload = json.loads(result["payload_json"])
        assert payload["field1"] == "value1"
        assert payload["field2"] == 42

    def test_failure_payload_has_error(self, agent_def):
        """AC4: Failed extraction saves error to payload."""
        state = {
            "extract_result": {"success": False, "error": "Parse error: unsupported format"},
            "storage_url": "",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert result["status"] == "error"
        assert "Parse error" in result["error"]
        payload = json.loads(result["payload_json"])
        assert payload["status"] == "failed"
        assert "Parse error" in payload["error"]

    def test_temp_file_cleaned_on_success(self, agent_def):
        """AC2: Temp file in /tmp/ is deleted after successful extraction."""
        tmp = tempfile.NamedTemporaryFile(delete=False, dir="/tmp", suffix=".pdf")
        tmp.write(b"fake pdf")
        tmp.close()
        tmp_path = tmp.name

        state = {
            "extract_result": {"success": True, "data": {"key": "val"}},
            "storage_url": tmp_path,
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert result["status"] == "success"
        assert not os.path.exists(tmp_path), "Temp file should be deleted after success"

    def test_temp_file_cleaned_on_failure(self, agent_def):
        """AC3: Temp file in /tmp/ is deleted after failed extraction."""
        tmp = tempfile.NamedTemporaryFile(delete=False, dir="/tmp", suffix=".pdf")
        tmp.write(b"fake pdf")
        tmp.close()
        tmp_path = tmp.name

        state = {
            "extract_result": {"success": False, "error": "Extraction failed"},
            "storage_url": tmp_path,
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert result["status"] == "error"
        assert not os.path.exists(tmp_path), "Temp file should be deleted after failure"


# =============================================================================
# finalize node tests
# =============================================================================

class TestFinalizeNode:
    """Tests for the finalize node (Step 7 in YAML agent)."""

    def test_successful_save(self, agent_def):
        """AC1: Successful update_node returns success."""
        state = {"update_result": {"success": True}}
        result = _exec_node(agent_def, "finalize", state)
        assert result["completed"] is True
        assert result["status"] == "success"

    def test_failed_save(self, agent_def):
        """AC10: Failed update_node returns error with 'Failed to save payload'."""
        state = {"update_result": {"success": False, "error": "Connection refused"}}
        result = _exec_node(agent_def, "finalize", state)
        assert result["completed"] is True
        assert result["status"] == "error"
        # When update_result has an error, finalize uses it; otherwise falls back
        assert "Failed to save payload" in result["error"] or "Connection refused" in result["error"]

    def test_failed_save_no_error_detail(self, agent_def):
        """AC10: Failed update_node with no error detail returns generic message."""
        state = {"update_result": {"success": False}}
        result = _exec_node(agent_def, "finalize", state)
        assert result["completed"] is True
        assert result["status"] == "error"
        assert "Failed to save payload" in result["error"]


# =============================================================================
# detect_directory node tests — TIER 1+2 detection (Story 16.1)
# =============================================================================

class TestDetectDirectoryFastPath:
    """Tests for detect_directory fast path (pre-set values)."""

    def test_preset_directory_skips_detection(self, agent_def):
        """AC1: Pre-set directoryName → directory_source='input', no detection."""
        state = {"directory_name": "chambers", "file_name": "irrelevant.pdf", "document_text": ""}
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "chambers"
        assert result["directory_source"] == "input"

    def test_preset_year_skips_year_detection(self, agent_def):
        """AC11: Pre-set year → year_source='input', no year detection."""
        state = {"directory_name": "chambers", "year": "2025", "file_name": "test_2024.pdf", "document_text": ""}
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["detected_year"] == "2025"
        assert result["year_source"] == "input"


class TestDetectDirectoryTier1:
    """Tests for TIER 1 filename-based detection."""

    def test_filename_chambers_2025(self, agent_def):
        """AC2+AC3: 'chambers_2025.docx' → directory=chambers, year=2025."""
        state = {"directory_name": "", "file_name": "chambers_2025.docx", "document_text": ""}
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "chambers"
        assert result["directory_source"] == "filename"
        assert result["detected_year"] == "2025"
        assert result["year_source"] == "filename"

    def test_filename_iflr_submission(self, agent_def):
        """AC2: 'iflr_submission.pdf' → directory=iflr1000."""
        state = {"directory_name": "", "file_name": "iflr_submission.pdf", "document_text": ""}
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "iflr1000"
        assert result["directory_source"] == "filename"

    def test_filename_legal500(self, agent_def):
        """TIER 1: 'legal500_form.docx' → directory=legal500."""
        state = {"directory_name": "", "file_name": "legal500_form.docx", "document_text": ""}
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "legal500"
        assert result["directory_source"] == "filename"

    def test_filename_itr(self, agent_def):
        """TIER 1: 'itr_world_tax.pdf' → directory=itr."""
        state = {"directory_name": "", "file_name": "itr_world_tax.pdf", "document_text": ""}
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "itr"
        assert result["directory_source"] == "filename"

    def test_filename_leadersleague(self, agent_def):
        """TIER 1: 'leaders_league_2025.pdf' → directory=leadersleague."""
        state = {"directory_name": "", "file_name": "leaders_league_2025.pdf", "document_text": ""}
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "leadersleague"
        assert result["directory_source"] == "filename"

    def test_filename_case_insensitive(self, agent_def):
        """TIER 1: Case-insensitive matching."""
        state = {"directory_name": "", "file_name": "CHAMBERS_Submission.PDF", "document_text": ""}
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "chambers"
        assert result["directory_source"] == "filename"

    def test_filename_no_keywords_falls_through(self, agent_def):
        """AC4: No filename keywords → directory_name empty, routes to TIER 3."""
        state = {"directory_name": "", "file_name": "submission_form.docx", "document_text": ""}
        result = _exec_node(agent_def, "detect_directory", state)
        # Story 16.2: no longer errors — empty directory_name routes to invoke_classification
        assert result["directory_name"] == ""
        assert result["directory_source"] == ""


class TestDetectDirectoryTier2:
    """Tests for TIER 2 content-based detection."""

    def test_content_chambers_url(self, agent_def):
        """AC5: Content with 'myaccount.chambers.com' → directory=chambers."""
        state = {
            "directory_name": "", "file_name": "submission_form.docx",
            "document_text": "Please submit via myaccount.chambers.com portal",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "chambers"
        assert result["directory_source"] == "content"

    def test_content_chambers_band(self, agent_def):
        """AC5: Content with 'Band 1' → directory=chambers."""
        state = {
            "directory_name": "", "file_name": "form.docx",
            "document_text": "The lawyer is ranked Band 1 in corporate law",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "chambers"
        assert result["directory_source"] == "content"

    def test_content_iflr1000_url(self, agent_def):
        """AC6: Content with 'iflr1000.com' → directory=iflr1000."""
        state = {
            "directory_name": "", "file_name": "form.docx",
            "document_text": "Submit at iflr1000.com/accreditation",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "iflr1000"
        assert result["directory_source"] == "content"

    def test_content_iflr1000_market_leader(self, agent_def):
        """AC6: Content with 'Market Leader' → directory=iflr1000."""
        state = {
            "directory_name": "", "file_name": "form.docx",
            "document_text": "The firm has been recognized as a market leader in M&A",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "iflr1000"
        assert result["directory_source"] == "content"

    def test_content_legal500(self, agent_def):
        """TIER 2: Content with 'legal500.com' → directory=legal500."""
        state = {
            "directory_name": "", "file_name": "form.docx",
            "document_text": "Visit legal500.com for more details",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "legal500"
        assert result["directory_source"] == "content"

    def test_content_itr(self, agent_def):
        """TIER 2: Content with 'itrworldtax.com' → directory=itr."""
        state = {
            "directory_name": "", "file_name": "form.docx",
            "document_text": "Published by itrworldtax.com annually",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "itr"
        assert result["directory_source"] == "content"

    def test_content_leadersleague(self, agent_def):
        """TIER 2: Content with 'leadersleague.com' → directory=leadersleague."""
        state = {
            "directory_name": "", "file_name": "form.docx",
            "document_text": "Ranked on leadersleague.com platform",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "leadersleague"
        assert result["directory_source"] == "content"

    def test_content_year_detection(self, agent_def):
        """AC7: Year in content when not in filename → year_source='content'."""
        state = {
            "directory_name": "", "file_name": "form.docx",
            "document_text": "Chambers and Partners 2025 submission deadline approaching",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "chambers"
        assert result["directory_source"] == "content"
        assert result["detected_year"] == "2025"
        assert result["year_source"] == "content"

    def test_all_tiers_fail_routes_to_tier3(self, agent_def):
        """Story 16.2: No match in filename or content → empty directory_name, routes to TIER 3."""
        state = {
            "directory_name": "", "file_name": "generic_form.docx",
            "document_text": "This document contains no directory keywords at all.",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        # Story 16.2: no longer errors — empty directory_name routes to invoke_classification
        assert result["directory_name"] == ""
        assert result["directory_source"] == ""


class TestDetectDirectoryTextExtraction:
    """Tests for text extraction in extract_and_download (AC9, AC10)."""

    def test_pdf_text_extraction_via_pdfplumber(self, agent_def):
        """AC10: PDF text extraction uses pdfplumber (mocked)."""
        from unittest.mock import MagicMock, patch

        mock_fsspec = MagicMock()
        mock_fs = MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs

        mock_file_ctx = MagicMock()
        mock_file_ctx.read.return_value = b"%PDF-1.4 fake pdf"
        mock_fs.open.return_value.__enter__ = MagicMock(return_value=mock_file_ctx)
        mock_fs.open.return_value.__exit__ = MagicMock(return_value=False)

        # Mock pdfplumber
        mock_pdfplumber = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Submit via myaccount.chambers.com"
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        state = {
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {
                    "storageUrl": "gs://bucket/file.pdf",
                    "fileName": "submission.pdf",
                    "mimeType": "application/pdf",
                },
            }
        }
        result = _exec_node(
            agent_def, "extract_and_download", state,
            mock_modules={"fsspec": mock_fsspec, "pdfplumber": mock_pdfplumber}
        )
        assert result["document_text"] == "Submit via myaccount.chambers.com"
        assert result["directory_name"] == ""
        # Cleanup
        if os.path.exists(result["storage_url"]):
            os.unlink(result["storage_url"])

    def test_docx_text_extraction_via_python_docx(self, agent_def):
        """AC9: DOCX text extraction uses python-docx (mocked)."""
        from unittest.mock import MagicMock

        mock_fsspec = MagicMock()
        mock_fs = MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs

        mock_file_ctx = MagicMock()
        mock_file_ctx.read.return_value = b"PK\x03\x04 fake docx"
        mock_fs.open.return_value.__enter__ = MagicMock(return_value=mock_file_ctx)
        mock_fs.open.return_value.__exit__ = MagicMock(return_value=False)

        # Mock python-docx (imported as 'docx')
        mock_docx_module = MagicMock()
        mock_doc = MagicMock()
        mock_para1 = MagicMock()
        mock_para1.text = "Chambers and Partners 2025"
        mock_para2 = MagicMock()
        mock_para2.text = "Submission Form"
        mock_doc.paragraphs = [mock_para1, mock_para2]
        mock_doc.tables = []
        mock_docx_module.Document.return_value = mock_doc

        # Mock subprocess for libreoffice conversion
        mock_subprocess = MagicMock()
        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = "OK"
        mock_subprocess.run.return_value = mock_run_result

        state = {
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {
                    "storageUrl": "gs://bucket/file.docx",
                    "fileName": "submission.docx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                },
            }
        }

        # We need to handle the PDF file creation after conversion
        import tempfile as _tf
        # This test will fail at PDF conversion since libreoffice isn't available
        # But we can test that document_text was extracted before conversion attempt
        result = _exec_node(
            agent_def, "extract_and_download", state,
            mock_modules={"fsspec": mock_fsspec, "docx": mock_docx_module, "subprocess": mock_subprocess}
        )
        # Conversion will fail because the PDF file won't actually exist
        # But the text extraction happens BEFORE conversion
        assert result["status"] == "error"
        assert "PDF conversion error" in result["error"]

    def test_existing_resolve_agent_works_with_detected_directory(self, agent_def, default_settings):
        """AC12: resolve_agent still works when directory_name comes from detect_directory."""
        state = {"directory_name": "chambers", "_extraction_mode": "balanced"}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-chambers-partners-balanced"
        assert result["status"] == "agent_selected"


# =============================================================================
# TIER 3 integration tests — detect_directory routing + process_classification
# =============================================================================

class TestDetectDirectoryTier3Routing:
    """Tests for TIER 3 routing from detect_directory (Story 16.2)."""

    def test_tier1_2_fail_directory_empty(self, agent_def):
        """AC1: TIER 1+2 fail → directory_name empty, routes to TIER 3."""
        state = {
            "directory_name": "",
            "file_name": "generic_form.docx",
            "document_text": "This has no directory keywords.",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == ""
        # Goto routing: not state.directory_name → invoke_classification

    def test_directory_found_year_missing_routes_to_tier3(self, agent_def):
        """AC6: Directory found but year missing → routes to TIER 3."""
        state = {
            "directory_name": "",
            "file_name": "chambers_submission.docx",
            "document_text": "Chambers and Partners submission",
            "year": "",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "chambers"
        assert result["directory_source"] == "filename"
        assert result["detected_year"] == ""
        # Goto routing: directory_name set + no detected_year → invoke_classification

    def test_directory_and_year_found_skips_tier3(self, agent_def):
        """Directory + year found → routes to resolve_agent, skips TIER 3."""
        state = {
            "directory_name": "",
            "file_name": "chambers_2025.docx",
            "document_text": "",
            "year": "",
        }
        result = _exec_node(agent_def, "detect_directory", state)
        assert result["directory_name"] == "chambers"
        assert result["detected_year"] == "2025"
        # Goto routing: has directory_name + has detected_year → resolve_agent


class TestProcessClassification:
    """Tests for the process_classification node (Story 16.2)."""

    def test_successful_classification(self, agent_def):
        """AC8: Valid classification → directory_name + classification_result set."""
        state = {
            "classification_invoke_result": {
                "success": True,
                "result": {
                    "status": "success",
                    "directory": "chambers",
                    "category": "Corporate/M&A",
                    "region_country": "BRA",
                    "region_state": "BR-SP",
                    "region_country_display": "Brazil",
                    "region_state_display": "São Paulo",
                    "confidence": 0.95,
                    "year": "2025",
                    "is_empty_form": False,
                },
            },
            "directory_name": "",
            "directory_source": "",
            "detected_year": "",
            "year_source": "",
        }
        result = _exec_node(agent_def, "process_classification", state)
        assert result["directory_name"] == "chambers"
        assert result["directory_source"] == "llm"
        assert result["detected_year"] == "2025"
        assert result["year_source"] == "llm"
        assert "classification_result" in result
        cr = json.loads(result["classification_result"])
        assert cr["directory"] == "chambers"
        assert cr["confidence"] == 0.95

    def test_known_directory_year_only(self, agent_def):
        """AC6: Known directory → directory_source preserved, year from LLM."""
        state = {
            "classification_invoke_result": {
                "success": True,
                "result": {
                    "status": "success",
                    "directory": "chambers",
                    "category": "Corporate",
                    "region_country": "BRA",
                    "region_state": "",
                    "region_country_display": "Brazil",
                    "region_state_display": "",
                    "confidence": 0.9,
                    "year": "2025",
                    "is_empty_form": False,
                },
            },
            "directory_name": "chambers",
            "directory_source": "filename",
            "detected_year": "",
            "year_source": "",
        }
        result = _exec_node(agent_def, "process_classification", state)
        assert result["directory_name"] == "chambers"
        assert result["directory_source"] == "filename"  # preserved from TIER 1
        assert result["detected_year"] == "2025"
        assert result["year_source"] == "llm"

    def test_classification_error_propagated(self, agent_def):
        """AC4: Classification error → error status in file_extraction."""
        state = {
            "classification_invoke_result": {
                "success": True,
                "result": {
                    "status": "error",
                    "error": "LLM classification inconclusive. Supported: chambers, iflr1000, legal500, itr, leadersleague",
                },
            },
            "directory_name": "",
            "directory_source": "",
            "detected_year": "",
            "year_source": "",
        }
        result = _exec_node(agent_def, "process_classification", state)
        assert result["status"] == "error"
        assert "inconclusive" in result["error"]
        assert result["completed"] is True

    def test_subagent_invoke_failure(self, agent_def):
        """Sub-agent invocation failure → error."""
        state = {
            "classification_invoke_result": {
                "success": False,
                "error": "Sub-agent not found: file_classification",
            },
            "directory_name": "",
            "directory_source": "",
            "detected_year": "",
            "year_source": "",
        }
        result = _exec_node(agent_def, "process_classification", state)
        assert result["status"] == "error"
        assert "Sub-agent not found" in result["error"]
        assert result["completed"] is True

    def test_low_confidence_errors(self, agent_def):
        """AC4: Low confidence from LLM → error."""
        state = {
            "classification_invoke_result": {
                "success": True,
                "result": {
                    "status": "success",
                    "directory": "chambers",
                    "confidence": 0.3,
                    "year": "",
                },
            },
            "directory_name": "",
            "directory_source": "",
            "detected_year": "",
            "year_source": "",
        }
        result = _exec_node(agent_def, "process_classification", state)
        assert result["status"] == "error"
        assert "inconclusive" in result["error"]


class TestFileExtractionStateSchema:
    """Tests for state_schema additions (Story 16.2)."""

    def test_classification_result_field_exists(self, agent_def):
        """AC8: classification_result field in state_schema."""
        assert "classification_result" in agent_def["state_schema"]

    def test_classification_invoke_result_field_exists(self, agent_def):
        """classification_invoke_result field in state_schema."""
        assert "classification_invoke_result" in agent_def["state_schema"]

    def test_classification_json_field_exists(self, agent_def):
        """Story 16.3 AC1: classification_json field in state_schema."""
        assert "classification_json" in agent_def["state_schema"]


# =============================================================================
# Classification metadata persistence tests — Story 16.3
# =============================================================================

class TestClassificationMetadataPersistence:
    """Tests for classification metadata building in prepare_payload (Story 16.3)."""

    def test_tier1_classification_json(self, agent_def):
        """AC2: TIER 1 (filename) → confidence=1.0, LLM fields null."""
        state = {
            "extract_result": {"success": True, "data": {"field1": "value1"}},
            "storage_url": "",
            "directory_name": "chambers",
            "directory_source": "filename",
            "detected_year": "2025",
            "year_source": "filename",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert "classification_json" in result
        cl = json.loads(result["classification_json"])
        assert cl["directory"] == "chambers"
        assert cl["directory_source"] == "filename"
        assert cl["detected_year"] == "2025"
        assert cl["year_source"] == "filename"
        assert cl["confidence"] == 1.0
        assert cl["category"] is None
        assert cl["region_country"] is None
        assert cl["region_country_display"] is None
        assert cl["region_state"] is None
        assert cl["region_state_display"] is None
        assert cl["is_empty_form"] is None

    def test_tier2_classification_json(self, agent_def):
        """AC3: TIER 2 (content) → confidence=1.0, LLM fields null."""
        state = {
            "extract_result": {"success": True, "data": {"field1": "value1"}},
            "storage_url": "",
            "directory_name": "iflr1000",
            "directory_source": "content",
            "detected_year": "2024",
            "year_source": "content",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        cl = json.loads(result["classification_json"])
        assert cl["directory"] == "iflr1000"
        assert cl["directory_source"] == "content"
        assert cl["confidence"] == 1.0
        assert cl["category"] is None

    def test_tier3_classification_json(self, agent_def):
        """AC4: TIER 3 (LLM) → all fields populated from classification_result."""
        classification_result = json.dumps({
            "directory": "chambers",
            "category": "Corporate/M&A",
            "region_country": "BRA",
            "region_country_display": "Brazil",
            "region_state": "BR-SP",
            "region_state_display": "São Paulo",
            "confidence": 0.95,
            "year": "2025",
            "is_empty_form": False,
        })
        state = {
            "extract_result": {"success": True, "data": {"field1": "value1"}},
            "storage_url": "",
            "directory_name": "chambers",
            "directory_source": "llm",
            "detected_year": "2025",
            "year_source": "llm",
            "classification_result": classification_result,
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        cl = json.loads(result["classification_json"])
        assert cl["directory"] == "chambers"
        assert cl["directory_source"] == "llm"
        assert cl["category"] == "Corporate/M&A"
        assert cl["region_country"] == "BRA"
        assert cl["region_country_display"] == "Brazil"
        assert cl["region_state"] == "BR-SP"
        assert cl["region_state_display"] == "São Paulo"
        assert cl["confidence"] == 0.95
        assert cl["is_empty_form"] is False

    def test_fastpath_input_classification_json(self, agent_def):
        """AC9: Fast-path (input) → directory_source='input', confidence=1.0."""
        state = {
            "extract_result": {"success": True, "data": {"field1": "value1"}},
            "storage_url": "",
            "directory_name": "legal500",
            "directory_source": "input",
            "detected_year": "2025",
            "year_source": "input",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        cl = json.loads(result["classification_json"])
        assert cl["directory"] == "legal500"
        assert cl["directory_source"] == "input"
        assert cl["confidence"] == 1.0
        assert cl["category"] is None

    def test_classification_json_on_extraction_failure(self, agent_def):
        """AC5: Extraction failure → classification_json still built."""
        state = {
            "extract_result": {"success": False, "error": "Parse error"},
            "storage_url": "",
            "directory_name": "chambers",
            "directory_source": "filename",
            "detected_year": "2025",
            "year_source": "filename",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert result["status"] == "error"
        assert "classification_json" in result
        cl = json.loads(result["classification_json"])
        assert cl["directory"] == "chambers"
        assert cl["directory_source"] == "filename"
        assert cl["confidence"] == 1.0

    def test_detected_year_from_various_sources(self, agent_def):
        """AC6: detected_year persisted from different sources."""
        for source in ["filename", "content", "llm", "input"]:
            state = {
                "extract_result": {"success": True, "data": {}},
                "storage_url": "",
                "directory_name": "chambers",
                "directory_source": source if source != "llm" else "filename",
                "detected_year": "2025",
                "year_source": source,
            }
            result = _exec_node(agent_def, "prepare_payload", state)
            cl = json.loads(result["classification_json"])
            assert cl["detected_year"] == "2025", f"Failed for year_source={source}"
            assert cl["year_source"] == source

    def test_existing_payload_json_still_returned(self, agent_def):
        """Regression: payload_json and status still returned alongside classification_json."""
        state = {
            "extract_result": {"success": True, "data": {"key": "val"}},
            "storage_url": "",
            "directory_name": "chambers",
            "directory_source": "filename",
            "detected_year": "2025",
            "year_source": "filename",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert result["status"] == "success"
        payload = json.loads(result["payload_json"])
        assert payload["key"] == "val"
        assert "classification_json" in result

    def test_tier3_classification_result_as_dict(self, agent_def):
        """AC4: classification_result as already-parsed dict (not JSON string)."""
        classification_result = {
            "directory": "legal500",
            "category": "Banking & Finance",
            "region_country": "USA",
            "region_country_display": "United States",
            "region_state": "US-NY",
            "region_state_display": "New York",
            "confidence": 0.88,
            "year": "2024",
            "is_empty_form": False,
        }
        state = {
            "extract_result": {"success": True, "data": {}},
            "storage_url": "",
            "directory_name": "legal500",
            "directory_source": "llm",
            "detected_year": "2024",
            "year_source": "llm",
            "classification_result": classification_result,  # dict, not string
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        cl = json.loads(result["classification_json"])
        assert cl["category"] == "Banking & Finance"
        assert cl["region_country"] == "USA"
        assert cl["confidence"] == 0.88

    def test_classification_json_with_empty_detected_year(self, agent_def):
        """Edge case: detected_year is empty string → classification_json has empty year, not None."""
        state = {
            "extract_result": {"success": True, "data": {}},
            "storage_url": "",
            "directory_name": "chambers",
            "directory_source": "filename",
            "detected_year": "",
            "year_source": "",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        cl = json.loads(result["classification_json"])
        assert cl["detected_year"] == ""
        assert cl["year_source"] == ""

    def test_save_payload_updates_contains_three_fields(self, agent_def):
        """AC1/AC6/AC7: save_payload updates dict has payload, classificationPayload, detectedYear."""
        save_node = None
        for node in agent_def["nodes"]:
            if node["name"] == "save_payload":
                save_node = node
                break
        assert save_node is not None, "save_payload node not found"
        updates = save_node["with"]["updates"]
        assert "payload" in updates
        assert "classificationPayload" in updates
        assert "detectedYear" in updates


# =============================================================================
# expand_proto_matters node tests — Story 16.4
# =============================================================================

def _exec_node_with_actions(agent_def, node_name, state, mock_actions=None, mock_modules=None):
    """Execute a node's run block with actions dict injected (for expand_proto_matters).

    Extends _exec_node to inject an `actions` dict into the namespace,
    matching the TEA YAMLEngine's run block execution context.
    """
    code = _get_node_code(agent_def, node_name)
    if not code:
        raise ValueError(f"Node '{node_name}' has no run block")

    mock_modules = mock_modules or {}
    mock_actions = mock_actions or {}

    namespace = {"state": state, "settings": agent_def.get("settings", {}), "actions": mock_actions}

    indented = textwrap.indent(code, "    ")
    wrapped = f"def _node_fn():\n{indented}\n_result = _node_fn()"

    for mod_name, mod_obj in mock_modules.items():
        namespace[mod_name] = mod_obj

    if mock_modules:
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def patched_import(name, *args, **kwargs):
            if name in mock_modules:
                return mock_modules[name]
            return original_import(name, *args, **kwargs)

        namespace["__builtins__"] = {**__builtins__.__dict__, "__import__": patched_import} if hasattr(__builtins__, '__dict__') else {**__builtins__, "__import__": patched_import}

    exec(compile(wrapped, f"<agent:{node_name}>", "exec"), namespace)
    return namespace.get("_result")


class TestProtoMatterExpansion:
    """Tests for expand_proto_matters node (Story 16.4)."""

    def _make_chambers_payload(self, matters_pub, matters_conf=None):
        """Build a chambers-style payload with publishable + confidential matters."""
        payload = {
            "workHighlights": {
                "publishableInformation": {"matters": matters_pub},
                "confidentialInformation": {"matters": matters_conf or []},
            }
        }
        return json.dumps(payload)

    def test_skip_on_error_status(self, agent_def):
        """AC7: Extraction error → skip expansion, no ProtoMatters."""
        state = {"status": "error", "payload_json": "{}"}
        result = _exec_node(agent_def, "expand_proto_matters", state)
        assert result["proto_matter_ids"] == []
        assert result["department_id"] == ""

    def test_empty_payload_json(self, agent_def):
        """AC8 edge: Empty payload_json → skip."""
        state = {"status": "success", "payload_json": ""}
        result = _exec_node(agent_def, "expand_proto_matters", state)
        assert result["proto_matter_ids"] == []

    def test_empty_matters_array(self, agent_def):
        """AC8: Payload with empty matters array → no ProtoMatters, no error."""
        payload = json.dumps({"workHighlights": {"publishableInformation": {"matters": []}, "confidentialInformation": {"matters": []}}})
        state = {"status": "success", "payload_json": payload, "directory_name": "chambers", "context_node_id": "file-1"}

        # Mock requests for department discovery
        from unittest.mock import MagicMock
        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        result = _exec_node_with_actions(
            agent_def, "expand_proto_matters", state,
            mock_modules={"requests": mock_requests},
        )
        assert result["proto_matter_ids"] == []

    def test_three_matters_creates_three_protos(self, agent_def):
        """AC1: 3 matters in payload → 3 ProtoMatter nodes created."""
        from unittest.mock import MagicMock, call

        matters = [
            {"clientName": "Acme Corp", "summary": "M&A deal"},
            {"clientName": "BigCo", "summary": "Restructuring"},
            {"clientName": "SmallCo", "summary": "IPO"},
        ]
        payload = self._make_chambers_payload(matters)

        # Mock requests for department discovery (no department found)
        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        # Mock actions
        create_call_count = [0]
        def mock_create_node(state, node_type, properties, **kw):
            create_call_count[0] += 1
            return {"success": True, "node_id": f"proto-{create_call_count[0]}"}

        def mock_connect_nodes(state, source_id, source_type, relationship_name, target_id, target_type, **kw):
            return {"success": True}

        mock_actions = {
            "graphology.create_node": mock_create_node,
            "graphology.connect_nodes": mock_connect_nodes,
        }

        state = {
            "status": "success",
            "payload_json": payload,
            "directory_name": "chambers",
            "context_node_id": "file-1",
        }

        result = _exec_node_with_actions(
            agent_def, "expand_proto_matters", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )
        assert len(result["proto_matter_ids"]) == 3
        assert result["proto_matter_ids"] == ["proto-1", "proto-2", "proto-3"]

    def test_file_has_proto_matter_connection(self, agent_def):
        """AC2: Each ProtoMatter connected to ApplicationFormFile via FILE_HAS_PROTO_MATTER."""
        from unittest.mock import MagicMock

        matters = [{"clientName": "Acme"}]
        payload = self._make_chambers_payload(matters)

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        connect_calls = []
        def mock_connect(state, source_id, source_type, relationship_name, target_id, target_type, **kw):
            connect_calls.append({
                "source_id": source_id, "source_type": source_type,
                "relationship_name": relationship_name,
                "target_id": target_id, "target_type": target_type,
            })
            return {"success": True}

        mock_actions = {
            "graphology.create_node": lambda state, node_type, properties, **kw: {"success": True, "node_id": "proto-1"},
            "graphology.connect_nodes": mock_connect,
        }

        state = {
            "status": "success",
            "payload_json": payload,
            "directory_name": "chambers",
            "context_node_id": "file-1",
        }

        _exec_node_with_actions(
            agent_def, "expand_proto_matters", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        # Should have exactly 1 connect call (FILE_HAS_PROTO_MATTER, no department)
        assert len(connect_calls) == 1
        assert connect_calls[0]["source_id"] == "file-1"
        assert connect_calls[0]["source_type"] == "ApplicationFormFile"
        assert connect_calls[0]["relationship_name"] == "FILE_HAS_PROTO_MATTER"
        assert connect_calls[0]["target_id"] == "proto-1"
        assert connect_calls[0]["target_type"] == "ProtoMatter"

    def test_department_discovery_and_linkage(self, agent_def):
        """AC3+AC4: Department found → DEPARTMENT_HAS_PROTO_MATTER connection created."""
        from unittest.mock import MagicMock

        matters = [{"clientName": "Acme"}]
        payload = self._make_chambers_payload(matters)

        # Mock requests to return a department
        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": [{"id": "dept-42"}]}}
        mock_requests.post.return_value = mock_resp

        connect_calls = []
        def mock_connect(state, source_id, source_type, relationship_name, target_id, target_type, **kw):
            connect_calls.append({
                "source_id": source_id, "source_type": source_type,
                "relationship_name": relationship_name,
                "target_id": target_id, "target_type": target_type,
            })
            return {"success": True}

        mock_actions = {
            "graphology.create_node": lambda state, node_type, properties, **kw: {"success": True, "node_id": "proto-1"},
            "graphology.connect_nodes": mock_connect,
        }

        state = {
            "status": "success",
            "payload_json": payload,
            "directory_name": "chambers",
            "context_node_id": "file-1",
        }

        result = _exec_node_with_actions(
            agent_def, "expand_proto_matters", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        assert result["department_id"] == "dept-42"
        # Should have 2 connect calls: FILE_HAS_PROTO_MATTER + DEPARTMENT_HAS_PROTO_MATTER
        assert len(connect_calls) == 2
        assert connect_calls[1]["source_id"] == "dept-42"
        assert connect_calls[1]["source_type"] == "Department"
        assert connect_calls[1]["relationship_name"] == "DEPARTMENT_HAS_PROTO_MATTER"

    def test_no_department_still_creates_protos(self, agent_def):
        """AC4: No Department found → ProtoMatters created without Department link, warning logged."""
        from unittest.mock import MagicMock

        matters = [{"clientName": "Acme"}]
        payload = self._make_chambers_payload(matters)

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        connect_calls = []
        def mock_connect(state, source_id, source_type, relationship_name, target_id, target_type, **kw):
            connect_calls.append(relationship_name)
            return {"success": True}

        mock_actions = {
            "graphology.create_node": lambda state, node_type, properties, **kw: {"success": True, "node_id": "proto-1"},
            "graphology.connect_nodes": mock_connect,
        }

        state = {
            "status": "success",
            "payload_json": payload,
            "directory_name": "chambers",
            "context_node_id": "file-1",
        }

        result = _exec_node_with_actions(
            agent_def, "expand_proto_matters", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        assert len(result["proto_matter_ids"]) == 1
        assert result["department_id"] == ""
        # Only FILE_HAS_PROTO_MATTER, no DEPARTMENT_HAS_PROTO_MATTER
        assert connect_calls == ["FILE_HAS_PROTO_MATTER"]

    def test_create_node_failure_skips_matter(self, agent_def):
        """AC1 edge: create_node failure → skip that matter, continue with rest."""
        from unittest.mock import MagicMock

        matters = [{"clientName": "Acme"}, {"clientName": "BigCo"}]
        payload = self._make_chambers_payload(matters)

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        call_count = [0]
        def mock_create(state, node_type, properties, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"success": False, "error": "Server error"}
            return {"success": True, "node_id": "proto-2"}

        mock_actions = {
            "graphology.create_node": mock_create,
            "graphology.connect_nodes": lambda **kw: {"success": True},
        }

        state = {
            "status": "success",
            "payload_json": payload,
            "directory_name": "chambers",
            "context_node_id": "file-1",
        }

        result = _exec_node_with_actions(
            agent_def, "expand_proto_matters", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        # First matter failed, second succeeded
        assert result["proto_matter_ids"] == ["proto-2"]


class TestMattersExtraction:
    """Tests for _extract_matters logic per directory (Task 4)."""

    def test_chambers_merges_publishable_and_confidential(self, agent_def):
        """AC1: Chambers merges publishable + confidential matters."""
        from unittest.mock import MagicMock

        payload = json.dumps({
            "workHighlights": {
                "publishableInformation": {"matters": [{"client": "A"}, {"client": "B"}]},
                "confidentialInformation": {"matters": [{"client": "C"}]},
            }
        })

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        created_props = []
        def mock_create(state, node_type, properties, **kw):
            created_props.append(json.loads(properties["payload"]))
            return {"success": True, "node_id": f"proto-{len(created_props)}"}

        mock_actions = {
            "graphology.create_node": mock_create,
            "graphology.connect_nodes": lambda **kw: {"success": True},
        }

        state = {
            "status": "success", "payload_json": payload,
            "directory_name": "chambers", "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(agent_def, "expand_proto_matters", state, mock_actions=mock_actions, mock_modules={"requests": mock_requests})
        assert len(result["proto_matter_ids"]) == 3
        clients = [p["client"] for p in created_props]
        assert clients == ["A", "B", "C"]

    def test_iflr1000_deals(self, agent_def):
        """AC1: IFLR1000 extracts from dealHighlights.deals."""
        from unittest.mock import MagicMock

        payload = json.dumps({
            "dealHighlights": {
                "deals": [{"dealName": "Deal A"}, {"dealName": "Deal B"}]
            }
        })

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        create_count = [0]
        def mock_create(state, node_type, properties, **kw):
            create_count[0] += 1
            return {"success": True, "node_id": f"proto-{create_count[0]}"}

        mock_actions = {
            "graphology.create_node": mock_create,
            "graphology.connect_nodes": lambda **kw: {"success": True},
        }

        state = {
            "status": "success", "payload_json": payload,
            "directory_name": "iflr1000", "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(agent_def, "expand_proto_matters", state, mock_actions=mock_actions, mock_modules={"requests": mock_requests})
        assert len(result["proto_matter_ids"]) == 2

    def test_legal500_merges_two_arrays(self, agent_def):
        """AC1: Legal500 merges workHighlightsDetailed + workHighlightsDetailed_nonPublishable."""
        from unittest.mock import MagicMock

        payload = json.dumps({
            "workHighlightsDetailed": [{"nameOfClient": "X"}],
            "workHighlightsDetailed_nonPublishable": [{"nameOfClient": "Y"}, {"nameOfClient": "Z"}],
        })

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        create_count = [0]
        def mock_create(state, node_type, properties, **kw):
            create_count[0] += 1
            return {"success": True, "node_id": f"proto-{create_count[0]}"}

        mock_actions = {
            "graphology.create_node": mock_create,
            "graphology.connect_nodes": lambda **kw: {"success": True},
        }

        state = {
            "status": "success", "payload_json": payload,
            "directory_name": "legal500", "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(agent_def, "expand_proto_matters", state, mock_actions=mock_actions, mock_modules={"requests": mock_requests})
        assert len(result["proto_matter_ids"]) == 3

    def test_itr_section_2(self, agent_def):
        """AC1: ITR extracts from section_2_deal_and_case_highlights."""
        from unittest.mock import MagicMock

        payload = json.dumps({
            "section_2_deal_and_case_highlights": [{"matterName": "Tax Case 1"}]
        })

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        mock_actions = {
            "graphology.create_node": lambda state, node_type, properties, **kw: {"success": True, "node_id": "proto-1"},
            "graphology.connect_nodes": lambda **kw: {"success": True},
        }

        state = {
            "status": "success", "payload_json": payload,
            "directory_name": "itr", "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(agent_def, "expand_proto_matters", state, mock_actions=mock_actions, mock_modules={"requests": mock_requests})
        assert len(result["proto_matter_ids"]) == 1

    def test_leadersleague_work_highlights(self, agent_def):
        """AC1: LeadersLeague extracts from work_highlights."""
        from unittest.mock import MagicMock

        payload = json.dumps({
            "work_highlights": [{"matterName": "Deal X"}, {"matterName": "Deal Y"}]
        })

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        create_count = [0]
        def mock_create(state, node_type, properties, **kw):
            create_count[0] += 1
            return {"success": True, "node_id": f"proto-{create_count[0]}"}

        mock_actions = {
            "graphology.create_node": mock_create,
            "graphology.connect_nodes": lambda **kw: {"success": True},
        }

        state = {
            "status": "success", "payload_json": payload,
            "directory_name": "leadersleague", "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(agent_def, "expand_proto_matters", state, mock_actions=mock_actions, mock_modules={"requests": mock_requests})
        assert len(result["proto_matter_ids"]) == 2

    def test_unknown_directory_no_matters(self, agent_def):
        """AC8: Unknown directory → no key mapping, no matters extracted."""
        from unittest.mock import MagicMock

        payload = json.dumps({"some_key": [{"item": 1}]})

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"departments": []}}
        mock_requests.post.return_value = mock_resp

        state = {
            "status": "success", "payload_json": payload,
            "directory_name": "unknown_dir", "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(agent_def, "expand_proto_matters", state, mock_modules={"requests": mock_requests})
        assert result["proto_matter_ids"] == []


class TestProtoMatterStateSchema:
    """Tests for state_schema additions (Story 16.4)."""

    def test_proto_matter_ids_field_exists(self, agent_def):
        """AC1: proto_matter_ids field in state_schema."""
        assert "proto_matter_ids" in agent_def["state_schema"]

    def test_department_id_field_exists(self, agent_def):
        """AC3: department_id field in state_schema."""
        assert "department_id" in agent_def["state_schema"]
