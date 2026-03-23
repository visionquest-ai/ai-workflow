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
    """Default agent settings — mirrors settings section in file_extraction.yaml."""
    return {"llamaextract": {"timeout": 300, "max_retries": 3}}


DIR_VARIABLES = {
    "directories": {
        "chambers":      {"extraction_mode": "balanced", "agent_base_name": "chambers-partners"},
        "iflr1000":      {"extraction_mode": "balanced", "agent_base_name": "iflr-1000"},
        "legal500":      {"extraction_mode": "premium",  "agent_base_name": "the-legal-500"},
        "itr":           {"extraction_mode": "balanced", "agent_base_name": "itr-world-tax"},
        "leadersleague": {"extraction_mode": "balanced", "agent_base_name": "leaders-league"},
    },
    "directory_aliases": {
        "legal 500": "legal500",
        "leaders league": "leadersleague",
    },
}


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
        state = {"directory_name": "unknown_dir", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["status"] == "error"
        assert "Unknown directory" in result["error"]
        assert "unknown_dir" in result["error"]
        assert "chambers" in result["error"]
        assert result["completed"] is True
        assert result["agent_name_used"] == ""

    def test_chambers_resolves_correctly(self, agent_def, default_settings):
        """AC1/AC11: chambers resolves to rankellix-chambers-partners-balanced."""
        state = {"directory_name": "chambers", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-chambers-partners-balanced"
        assert result["status"] == "agent_selected"

    def test_iflr1000_resolves_correctly(self, agent_def, default_settings):
        """AC1: iflr1000 resolves correctly."""
        state = {"directory_name": "iflr1000", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-iflr-1000-balanced"

    def test_legal500_resolves_correctly(self, agent_def, default_settings):
        """AC1: legal500 resolves correctly."""
        state = {"directory_name": "legal500", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-the-legal-500-balanced"

    def test_legal500_with_space_resolves(self, agent_def, default_settings):
        """AC1: 'legal 500' (with space) also resolves."""
        state = {"directory_name": "legal 500", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-the-legal-500-balanced"

    def test_itr_resolves_correctly(self, agent_def, default_settings):
        """AC1: itr resolves correctly."""
        state = {"directory_name": "itr", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-itr-world-tax-balanced"

    def test_leadersleague_resolves_correctly(self, agent_def, default_settings):
        """AC1: leadersleague resolves correctly."""
        state = {"directory_name": "leadersleague", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-leaders-league-balanced"

    def test_mode_from_state_fast(self, agent_def, default_settings):
        """AC11: Mode from state._extraction_mode='fast' produces correct agent name."""
        state = {"directory_name": "chambers", "_extraction_mode": "fast", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-chambers-partners-fast"

    def test_mode_from_state_accurate(self, agent_def, default_settings):
        """AC11: Mode from state._extraction_mode='accurate' produces correct agent name."""
        state = {"directory_name": "itr", "_extraction_mode": "accurate", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-itr-world-tax-accurate"

    def test_case_insensitive_directory_name(self, agent_def, default_settings):
        """AC1: directoryName is case-insensitive."""
        state = {"directory_name": "CHAMBERS", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-chambers-partners-balanced"

    def test_agent_name_prefix_env(self, agent_def, default_settings):
        """Agent name includes AGENT_NAME_PREFIX when set."""
        state = {"directory_name": "chambers", "variables": DIR_VARIABLES}
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
# Settings-driven directory config tests (Story 5.2 simplification)
# =============================================================================

class TestSettingsDrivenDirectoryConfig:
    """Tests that extraction_mode and agent_base_name come from variables.directories."""

    def test_legal500_configured_as_premium(self, agent_def):
        """variables.directories has legal500 extraction_mode=premium."""
        dirs = agent_def["variables"]["directories"]
        assert dirs["legal500"]["extraction_mode"] == "premium"

    def test_all_directories_have_required_keys(self, agent_def):
        """Every entry in variables.directories has extraction_mode and agent_base_name."""
        dirs = agent_def["variables"]["directories"]
        for key, entry in dirs.items():
            assert "extraction_mode" in entry, f"{key} missing extraction_mode"
            assert "agent_base_name" in entry, f"{key} missing agent_base_name"

    def test_legal500_resolves_with_premium_mode(self, agent_def, default_settings):
        """legal500 uses premium mode end-to-end in resolve_agent."""
        state = {"directory_name": "legal500", "_extraction_mode": "premium", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-the-legal-500-premium"

    def test_alias_legal_500_with_space_resolves_agent(self, agent_def, default_settings):
        """'legal 500' alias resolves to legal500 config in resolve_agent."""
        state = {"directory_name": "legal 500", "_extraction_mode": "premium", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-the-legal-500-premium"

    def test_alias_leaders_league_with_space_resolves_agent(self, agent_def, default_settings):
        """'leaders league' alias resolves to leadersleague config in resolve_agent."""
        state = {"directory_name": "leaders league", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["agent_name_used"] == "rankellix-leaders-league-balanced"

    def test_unknown_directory_lists_configured_keys(self, agent_def, default_settings):
        """Unknown directory error lists keys from variables.directories."""
        state = {"directory_name": "unknown", "variables": DIR_VARIABLES}
        result = _exec_node(agent_def, "resolve_agent", state, settings=default_settings)
        assert result["status"] == "error"
        assert "chambers" in result["error"]
        assert "legal500" in result["error"]

    def test_missing_extraction_mode_defaults_to_balanced(self, agent_def):
        """If a directory entry has no extraction_mode, default is 'balanced'."""
        custom_vars = {
            "directories": {"testdir": {"agent_base_name": "test-dir"}},
            "directory_aliases": {},
        }
        state = {"directory_name": "testdir", "variables": custom_vars}
        result = _exec_node(agent_def, "resolve_agent", state)
        assert result["agent_name_used"] == "rankellix-test-dir-balanced"


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
        state = {"directory_name": "chambers", "_extraction_mode": "balanced", "variables": DIR_VARIABLES}
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


# =============================================================================
# ApplicationFormFile Status Tracking tests (Story 14.4)
# =============================================================================

class TestStatusTrackingReadingNode:
    """Tests for update_status_reading node (Story 14.4, AC1)."""

    def test_update_status_reading_node_exists(self, agent_def):
        """AC1: update_status_reading node exists in agent definition."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        assert "update_status_reading" in node_names

    def test_update_status_reading_uses_graphology_update(self, agent_def):
        """AC1: update_status_reading uses graphology.update_node."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "update_status_reading")
        assert node["uses"] == "graphology.update_node"

    def test_update_status_reading_sets_status_reading(self, agent_def):
        """AC1: update_status_reading sets status to 'reading'."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "update_status_reading")
        assert node["with"]["updates"]["status"] == "reading"

    def test_update_status_reading_targets_application_form_file(self, agent_def):
        """AC1: update_status_reading targets ApplicationFormFile node type."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "update_status_reading")
        assert node["with"]["node_type"] == "ApplicationFormFile"

    def test_extract_and_download_routes_to_update_status_reading(self, agent_def):
        """AC1: extract_and_download success routes to update_status_reading."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "extract_and_download")
        goto_targets = [g.get("to") for g in node["goto"]]
        assert "update_status_reading" in goto_targets

    def test_update_status_reading_before_detect_directory(self, agent_def):
        """AC1: update_status_reading appears before detect_directory in node order."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        reading_idx = node_names.index("update_status_reading")
        detect_idx = node_names.index("detect_directory")
        assert reading_idx < detect_idx


class TestStatusTrackingFailedOnDownloadError:
    """Tests for status 'failed' on download errors (Story 14.4, AC3)."""

    def test_gcs_download_failure_sets_failed(self, agent_def):
        """AC3: GCS download failure calls graphology.update_node with status 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        import unittest.mock as mock
        mock_fsspec = mock.MagicMock()
        mock_fsspec.filesystem.return_value.open.side_effect = Exception("GCS error")

        state = {
            "context_node_id": "file-123",
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"storageUrl": "gs://bucket/file.pdf", "fileName": "file.pdf"},
            },
        }

        result = _exec_node_with_actions(
            agent_def, "extract_and_download", state,
            mock_actions=mock_actions, mock_modules={"fsspec": mock_fsspec},
        )

        assert result["status"] == "error"
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}
        assert update_calls[0]["node_id"] == "file-123"

    def test_http_download_failure_sets_failed(self, agent_def):
        """AC3: HTTP download failure calls graphology.update_node with status 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        import unittest.mock as mock
        mock_requests = mock.MagicMock()
        mock_requests.get.side_effect = Exception("HTTP error")

        state = {
            "context_node_id": "file-456",
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"storageUrl": "https://example.com/file.pdf", "fileName": "file.pdf"},
            },
        }

        result = _exec_node_with_actions(
            agent_def, "extract_and_download", state,
            mock_actions=mock_actions, mock_modules={"requests": mock_requests},
        )

        assert result["status"] == "error"
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}

    def test_unsupported_scheme_sets_failed(self, agent_def):
        """AC3: Unsupported URL scheme sets status 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-789",
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"storageUrl": "ftp://invalid/file.pdf", "fileName": "file.pdf"},
            },
        }

        result = _exec_node_with_actions(
            agent_def, "extract_and_download", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "error"
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}

    def test_no_storage_url_sets_failed(self, agent_def):
        """AC3: Missing storageUrl sets status 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-no-url",
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"storageUrl": ""},
            },
        }

        result = _exec_node_with_actions(
            agent_def, "extract_and_download", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "error"
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}

    def test_wrong_node_type_sets_failed(self, agent_def):
        """AC3: Wrong node type sets status 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-wrong-type",
            "context_result": {
                "success": True,
                "node_type": "LegalFirm",
                "data": {"storageUrl": "gs://bucket/file.pdf"},
            },
        }

        result = _exec_node_with_actions(
            agent_def, "extract_and_download", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "error"
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}

    def test_graphology_fetch_failure_sets_failed(self, agent_def):
        """AC3: Graphology fetch failure (context_result.success=False) sets status 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-gql-fail",
            "context_result": {
                "success": False,
                "error": "Service unavailable (503)",
            },
        }

        result = _exec_node_with_actions(
            agent_def, "extract_and_download", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "error"
        assert "503" in result["error"]
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}
        assert update_calls[0]["node_id"] == "file-gql-fail"

    def test_pdf_conversion_failure_sets_failed(self, agent_def):
        """AC3: PDF conversion failure sets status 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        import unittest.mock as mock

        # Create a real temp file that looks like a .docx download
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
        tmp.write(b"fake docx content")
        tmp.close()

        mock_fsspec = mock.MagicMock()
        mock_fs = mock.MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs

        # Make fs.open return the fake content successfully
        mock_file_ctx = mock.MagicMock()
        mock_file_ctx.__enter__ = mock.MagicMock(return_value=mock.MagicMock(read=mock.MagicMock(return_value=b"fake")))
        mock_file_ctx.__exit__ = mock.MagicMock(return_value=False)
        mock_fs.open.return_value = mock_file_ctx

        # Mock subprocess to simulate LibreOffice failure
        mock_subprocess = mock.MagicMock()
        mock_subprocess.run.return_value = mock.MagicMock(returncode=1, stderr="LibreOffice crashed", stdout="")

        state = {
            "context_node_id": "file-pdf-fail",
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"storageUrl": "gs://bucket/file.docx", "fileName": "file.docx"},
            },
        }

        result = _exec_node_with_actions(
            agent_def, "extract_and_download", state,
            mock_actions=mock_actions,
            mock_modules={"fsspec": mock_fsspec, "subprocess": mock_subprocess},
        )

        assert result["status"] == "error"
        assert "PDF conversion" in result["error"] or "LibreOffice" in result["error"]
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}

        # Clean up temp file if still exists
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


class TestStatusTrackingFailedOnClassification:
    """Tests for status 'failed' on classification errors (Story 14.4, AC4, AC5)."""

    def test_classification_subagent_failure_sets_failed(self, agent_def):
        """AC5: Classification sub-agent failure sets status 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-class-fail",
            "classification_invoke_result": {"success": False, "error": "Sub-agent crashed"},
        }

        result = _exec_node_with_actions(
            agent_def, "process_classification", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "error"
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}

    def test_low_confidence_classification_sets_failed(self, agent_def):
        """AC5: Low confidence classification sets status 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-low-conf",
            "classification_invoke_result": {
                "success": True,
                "result": {"status": "success", "directory": "chambers", "confidence": 0.3},
            },
        }

        result = _exec_node_with_actions(
            agent_def, "process_classification", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "error"
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}

    def test_unknown_directory_in_resolve_agent_sets_failed(self, agent_def):
        """AC5: Unknown directory in resolve_agent sets status 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-unknown-dir",
            "directory_name": "unknown_directory",
            "_extraction_mode": "balanced",
        }

        result = _exec_node_with_actions(
            agent_def, "resolve_agent", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "error"
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}


class TestStatusTrackingSucceeded:
    """Tests for status 'succeeded' on successful completion (Story 14.4, AC2)."""

    def test_successful_finalize_sets_succeeded(self, agent_def):
        """AC2: Successful finalize sets status to 'succeeded'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-success",
            "update_result": {"success": True},
        }

        result = _exec_node_with_actions(
            agent_def, "finalize", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "success"
        assert result["completed"] is True
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "succeeded"}

    def test_failed_save_in_finalize_sets_failed(self, agent_def):
        """AC4: Failed save_payload in finalize sets status to 'failed'."""
        update_calls = []

        def mock_update_node(**kwargs):
            update_calls.append(kwargs)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-save-fail",
            "update_result": {"success": False, "error": "GraphQL timeout"},
        }

        result = _exec_node_with_actions(
            agent_def, "finalize", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "error"
        assert len(update_calls) == 1
        assert update_calls[0]["updates"] == {"status": "failed"}


class TestStatusTrackingResilience:
    """Tests for status update resilience (Story 14.4, AC6)."""

    def test_status_update_failure_does_not_crash_download_error(self, agent_def):
        """AC6: Status update failure does not crash the pipeline on download error."""
        def mock_update_node(**kwargs):
            raise ConnectionError("Graphology unreachable")

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-resilient",
            "context_result": {
                "success": True,
                "node_type": "ApplicationFormFile",
                "data": {"storageUrl": "ftp://invalid/file.pdf", "fileName": "file.pdf"},
            },
        }

        result = _exec_node_with_actions(
            agent_def, "extract_and_download", state,
            mock_actions=mock_actions,
        )

        # Pipeline should still return the primary error, not crash
        assert result["status"] == "error"
        assert "Unsupported" in result["error"]

    def test_status_update_failure_does_not_crash_classification(self, agent_def):
        """AC6: Status update failure does not crash classification error path."""
        def mock_update_node(**kwargs):
            raise RuntimeError("GraphQL server error")

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-resilient-class",
            "classification_invoke_result": {"success": False, "error": "Timeout"},
        }

        result = _exec_node_with_actions(
            agent_def, "process_classification", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_status_update_failure_does_not_crash_finalize(self, agent_def):
        """AC6: Status update failure does not crash finalize."""
        def mock_update_node(**kwargs):
            raise Exception("Network down")

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-resilient-fin",
            "update_result": {"success": True},
        }

        result = _exec_node_with_actions(
            agent_def, "finalize", state,
            mock_actions=mock_actions,
        )

        # Pipeline still succeeds even though status update failed
        assert result["status"] == "success"
        assert result["completed"] is True

    def test_status_update_failure_does_not_crash_resolve_agent(self, agent_def):
        """AC6: Status update failure does not crash resolve_agent error path."""
        def mock_update_node(**kwargs):
            raise Exception("Connection refused")

        mock_actions = {"graphology.update_node": mock_update_node}

        state = {
            "context_node_id": "file-resilient-resolve",
            "directory_name": "nonexistent_dir",
            "_extraction_mode": "balanced",
        }

        result = _exec_node_with_actions(
            agent_def, "resolve_agent", state,
            mock_actions=mock_actions,
        )

        assert result["status"] == "error"
        assert "Unknown directory" in result["error"]


# =============================================================================
# Save directoryName early tests (Story 1.2)
# =============================================================================

class TestSaveDirectoryNameNode:
    """Tests for save_directory_name node (Story 1.2)."""

    def test_save_directory_name_node_exists(self, agent_def):
        """AC1: save_directory_name node exists in agent definition."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        assert "save_directory_name" in node_names

    def test_save_directory_name_uses_graphology_update(self, agent_def):
        """AC1: save_directory_name uses graphology.update_node."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "save_directory_name")
        assert node["uses"] == "graphology.update_node"

    def test_save_directory_name_sets_directory_name(self, agent_def):
        """AC1: save_directory_name sets directoryName from state."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "save_directory_name")
        assert node["with"]["updates"]["directoryName"] == "{{ state.directory_name }}"

    def test_save_directory_name_targets_application_form_file(self, agent_def):
        """AC1: save_directory_name targets ApplicationFormFile node type."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "save_directory_name")
        assert node["with"]["node_type"] == "ApplicationFormFile"

    def test_save_directory_name_output_is_underscore_prefixed(self, agent_def):
        """AC3: Output stored in _directory_save_result (best-effort, not checked)."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "save_directory_name")
        assert node["output"] == "_directory_save_result"

    def test_save_directory_name_before_resolve_agent(self, agent_def):
        """AC2: save_directory_name appears before resolve_agent in node order."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        save_idx = node_names.index("save_directory_name")
        resolve_idx = node_names.index("resolve_agent")
        assert save_idx < resolve_idx

    def test_save_directory_name_after_process_classification(self, agent_def):
        """AC2: save_directory_name appears after process_classification."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        classify_idx = node_names.index("process_classification")
        save_idx = node_names.index("save_directory_name")
        assert classify_idx < save_idx

    def test_detect_directory_routes_to_save_directory_name(self, agent_def):
        """AC1,AC2: detect_directory fallthrough routes to save_directory_name."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "detect_directory")
        goto_targets = [g.get("to") for g in node["goto"]]
        assert "save_directory_name" in goto_targets

    def test_process_classification_routes_to_save_directory_name(self, agent_def):
        """AC1,AC2: process_classification success routes to save_directory_name."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "process_classification")
        goto_targets = [g.get("to") for g in node["goto"]]
        assert "save_directory_name" in goto_targets

    def test_detect_directory_does_not_route_to_resolve_agent(self, agent_def):
        """AC2: detect_directory no longer routes directly to resolve_agent."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "detect_directory")
        goto_targets = [g.get("to") for g in node["goto"]]
        assert "resolve_agent" not in goto_targets

    def test_process_classification_does_not_route_to_resolve_agent(self, agent_def):
        """AC2: process_classification no longer routes directly to resolve_agent."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "process_classification")
        goto_targets = [g.get("to") for g in node["goto"]]
        assert "resolve_agent" not in goto_targets

    def test_detect_directory_error_and_classification_routes_unchanged(self, agent_def):
        """AC2: detect_directory error→__end__ and classification routes unchanged."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "detect_directory")
        goto_list = node["goto"]
        # Error route still goes to __end__
        assert goto_list[0]["to"] == "__end__"
        assert goto_list[0]["if"] == "state.error"
        # No directory → invoke_classification
        assert goto_list[1]["to"] == "invoke_classification"
        # Directory but no year → invoke_classification
        assert goto_list[2]["to"] == "invoke_classification"

    def test_save_directory_name_uses_context_node_id(self, agent_def):
        """AC4: save_directory_name uses context_node_id for idempotent updates."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "save_directory_name")
        assert node["with"]["node_id"] == "{{ state.context_node_id }}"

    def test_save_directory_name_has_explicit_goto_to_lookup(self, agent_def):
        """save_directory_name has explicit goto to lookup_directory_id (not implicit sequential)."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "save_directory_name")
        goto = node.get("goto")
        assert goto is not None, "save_directory_name must have an explicit goto block"
        targets = [g.get("to") for g in goto]
        assert "lookup_directory_id" in targets


# =============================================================================
# Story 1.3: lookup_directory_id node tests
# =============================================================================

class TestLookupDirectoryIdStructure:
    """Structural tests for the lookup_directory_id node (Story 1.3)."""

    def test_lookup_directory_id_node_exists(self, agent_def):
        """Story 1.3 Task 1: lookup_directory_id node must exist."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        assert "lookup_directory_id" in node_names

    def test_lookup_directory_id_after_save_directory_name(self, agent_def):
        """Story 1.3 Task 1.1: lookup_directory_id appears after save_directory_name."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        save_idx = node_names.index("save_directory_name")
        lookup_idx = node_names.index("lookup_directory_id")
        assert lookup_idx == save_idx + 1

    def test_lookup_directory_id_before_resolve_agent(self, agent_def):
        """Story 1.3 Task 1.1: lookup_directory_id appears before resolve_agent."""
        node_names = [n["name"] for n in agent_def["nodes"]]
        lookup_idx = node_names.index("lookup_directory_id")
        resolve_idx = node_names.index("resolve_agent")
        assert lookup_idx < resolve_idx

    def test_lookup_directory_id_has_run_block(self, agent_def):
        """Story 1.3 Task 1: lookup_directory_id has a run: block."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "lookup_directory_id")
        assert "run" in node
        assert len(node["run"]) > 0

    def test_lookup_directory_id_routes_to_resolve_agent(self, agent_def):
        """Story 1.3 Task 2.2: lookup_directory_id unconditionally routes to resolve_agent."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "lookup_directory_id")
        goto = node.get("goto")
        # Check unconditional route to resolve_agent
        if isinstance(goto, list):
            targets = [g.get("to") for g in goto]
            assert "resolve_agent" in targets
        elif isinstance(goto, str):
            assert goto == "resolve_agent"
        else:
            raise AssertionError("lookup_directory_id must have goto to resolve_agent")

    def test_directory_id_in_state_schema(self, agent_def):
        """Story 1.3: directory_id field exists in state_schema."""
        assert "directory_id" in agent_def["state_schema"]


class TestLookupDirectoryIdBehavior:
    """Behavioral tests for the lookup_directory_id run block (Story 1.3)."""

    def test_no_directory_name_skips_lookup(self, agent_def):
        """AC#3 variant: If no directory_name in state, skip lookup."""
        state = {
            "context_node_id": "file-1",
            "directory_name": "",
        }
        result = _exec_node_with_actions(agent_def, "lookup_directory_id", state)
        assert result == {}

    def test_successful_directory_lookup_exact_match(self, agent_def):
        """AC#1,#2: Exact name match returns directory_id and calls update_node."""
        from unittest.mock import MagicMock

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"directory": [{"id": "dir-123", "name": "Chambers and Partners"}]}
        }
        mock_requests.post.return_value = mock_resp

        update_calls = []
        def mock_update(state, node_id, node_type, updates, **kw):
            update_calls.append({"node_id": node_id, "node_type": node_type, "updates": updates})
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "context_node_id": "file-1",
            "directory_name": "chambers",
        }

        result = _exec_node_with_actions(
            agent_def, "lookup_directory_id", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        assert result["directory_id"] == "dir-123"
        assert len(update_calls) == 1
        assert update_calls[0]["node_id"] == "file-1"
        assert update_calls[0]["node_type"] == "ApplicationFormFile"
        assert update_calls[0]["updates"] == {"directoryId": "dir-123"}

    def test_case_insensitive_fallback(self, agent_def):
        """AC#1 subtask 1.6: Case-insensitive matching when exact match fails.

        The node fetches all directories in a single query and uses fuzzy_match
        for case-insensitive canonical + hint matching.
        """
        from unittest.mock import MagicMock

        mock_requests = MagicMock()
        # Single query returns all directories (different casing than state.directory_name)
        resp = MagicMock()
        resp.json.return_value = {
            "data": {"directory": [
                {"id": "dir-456", "name": "Chambers and Partners"},
                {"id": "dir-789", "name": "IFLR1000"},
            ]}
        }
        mock_requests.post.return_value = resp

        update_calls = []
        def mock_update(state, node_id, node_type, updates, **kw):
            update_calls.append(updates)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "context_node_id": "file-1",
            "directory_name": "chambers",
        }

        result = _exec_node_with_actions(
            agent_def, "lookup_directory_id", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        assert result["directory_id"] == "dir-456"
        assert update_calls[0] == {"directoryId": "dir-456"}

    def test_no_match_returns_empty_and_no_update(self, agent_def):
        """AC#3: No matching Directory node → directory_id empty, no update_node call."""
        from unittest.mock import MagicMock

        mock_requests = MagicMock()
        # First call: no match
        resp1 = MagicMock()
        resp1.json.return_value = {"data": {"directories": []}}
        # Second call (fallback): still no match
        resp2 = MagicMock()
        resp2.json.return_value = {"data": {"directories": [
            {"id": "dir-1", "name": "Legal500"},
        ]}}
        mock_requests.post.side_effect = [resp1, resp2]

        update_calls = []
        def mock_update(state, node_id, node_type, updates, **kw):
            update_calls.append(updates)
            return {"success": True}

        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "context_node_id": "file-1",
            "directory_name": "chambers",
        }

        result = _exec_node_with_actions(
            agent_def, "lookup_directory_id", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        assert result["directory_id"] == ""
        assert len(update_calls) == 0

    def test_graphql_failure_best_effort(self, agent_def):
        """AC#4: GraphQL failure → error logged, pipeline continues, directory_id empty."""
        from unittest.mock import MagicMock

        mock_requests = MagicMock()
        mock_requests.post.side_effect = Exception("Connection refused")

        mock_actions = {"graphology.update_node": MagicMock()}

        state = {
            "context_node_id": "file-1",
            "directory_name": "chambers",
        }

        result = _exec_node_with_actions(
            agent_def, "lookup_directory_id", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        # Best-effort: no crash, returns empty directory_id
        assert result["directory_id"] == ""

    def test_uses_graphology_url_from_state_variables(self, agent_def):
        """AC#5 subtask 1.4: GQL URL resolved from state.variables.GRAPHOLOGY_URL."""
        from unittest.mock import MagicMock

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"directories": [{"id": "dir-1", "name": "chambers"}]}
        }
        mock_requests.post.return_value = mock_resp

        mock_actions = {"graphology.update_node": lambda **kw: {"success": True}}

        state = {
            "context_node_id": "file-1",
            "directory_name": "chambers",
            "variables": {"GRAPHOLOGY_URL": "http://custom:4000"},
        }

        _exec_node_with_actions(
            agent_def, "lookup_directory_id", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        # Verify the URL used in the request
        call_args = mock_requests.post.call_args
        assert call_args[0][0] == "http://custom:4000"

    def test_api_key_header_set_when_present(self, agent_def):
        """AC#5 subtask 1.5: x-api-key header set when GRAPHOLOGY_API_KEY available."""
        from unittest.mock import MagicMock

        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"directories": [{"id": "dir-1", "name": "chambers"}]}
        }
        mock_requests.post.return_value = mock_resp

        mock_actions = {"graphology.update_node": lambda **kw: {"success": True}}

        state = {
            "context_node_id": "file-1",
            "directory_name": "chambers",
            "variables": {
                "GRAPHOLOGY_URL": "http://localhost:4000",
                "GRAPHOLOGY_API_KEY": "test-key-123",
            },
        }

        _exec_node_with_actions(
            agent_def, "lookup_directory_id", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        call_kwargs = mock_requests.post.call_args
        headers = call_kwargs[1].get("headers") or call_kwargs.kwargs.get("headers", {})
        assert headers.get("x-api-key") == "test-key-123"

    def test_graphql_query_uses_find_directory(self, agent_def):
        """AC#1 subtask 1.2: GraphQL query fetches all directory nodes via singular field."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "lookup_directory_id")
        code = node["run"]
        # Code fetches all directories with a single query and fuzzy-matches client-side
        assert "directory" in code
        assert '.get("directory"' in code

    def test_update_node_uses_graphology_action(self, agent_def):
        """AC#5: Uses actions['graphology.update_node'] not direct mutation."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "lookup_directory_id")
        code = node["run"]
        assert 'actions["graphology.update_node"]' in code


# =============================================================================
# Story 1.4: LlamaExtract year override tests
# =============================================================================

class TestLlamaExtractYearOverride:
    """Tests for LlamaExtract year extraction in prepare_payload (Story 1.4)."""

    def test_top_level_year_overrides_detected_year(self, agent_def):
        """AC1: Top-level year in LlamaExtract data overrides detected_year in classification_json."""
        import json
        state = {
            "extract_result": {"success": True, "data": {"year": "2025", "firmName": "Acme"}},
            "directory_name": "chambers",
            "directory_source": "filename",
            "detected_year": "2024",
            "year_source": "filename",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert result["status"] == "success"
        classification = json.loads(result["classification_json"])
        assert classification["detected_year"] == "2025"
        assert classification["year_source"] == "llamaextract"

    def test_nested_year_found_via_fallback(self, agent_def):
        """AC2: Year nested in sub-section found via fallback search."""
        import json
        state = {
            "extract_result": {"success": True, "data": {
                "firmName": "Acme",
                "lawyersRanking": {"year": "2026", "lawyers": []},
            }},
            "directory_name": "chambers",
            "directory_source": "filename",
            "detected_year": "2024",
            "year_source": "filename",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        classification = json.loads(result["classification_json"])
        assert classification["detected_year"] == "2026"
        assert classification["year_source"] == "llamaextract"

    def test_no_year_anywhere_keeps_previous_tier(self, agent_def):
        """AC3: No year in LlamaExtract data → detected_year unchanged."""
        state = {
            "extract_result": {"success": True, "data": {"firmName": "Acme", "lawyers": []}},
            "directory_name": "chambers",
            "directory_source": "filename",
            "detected_year": "2024",
            "year_source": "filename",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert result["status"] == "success"
        # No detected_year/year_source keys in return = previous state preserved
        assert "detected_year" not in result
        assert "year_source" not in result

    def test_data_not_dict_skips_year_extraction(self, agent_def):
        """AC5: Non-dict data does not cause errors."""
        state = {
            "extract_result": {"success": True, "data": ["item1", "item2"]},
            "directory_name": "chambers",
            "directory_source": "filename",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert result["status"] == "success"
        assert "detected_year" not in result

    def test_integer_year_converted_to_string(self, agent_def):
        """AC1 edge case: Integer year (e.g., 2026) is converted to string."""
        import json
        state = {
            "extract_result": {"success": True, "data": {"year": 2026}},
            "directory_name": "chambers",
            "directory_source": "filename",
            "detected_year": "2024",
            "year_source": "filename",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        classification = json.loads(result["classification_json"])
        assert classification["detected_year"] == "2026"
        assert classification["year_source"] == "llamaextract"

    def test_none_year_treated_as_empty(self, agent_def):
        """AC3 edge case: year=None is treated as empty, no override."""
        state = {
            "extract_result": {"success": True, "data": {"year": None, "firmName": "Acme"}},
            "directory_name": "chambers",
            "directory_source": "filename",
            "detected_year": "2024",
            "year_source": "filename",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        assert "detected_year" not in result

    def test_classification_json_reserialized_with_year(self, agent_def):
        """AC4: classification_json includes updated year when override fires."""
        import json
        state = {
            "extract_result": {"success": True, "data": {"year": "2025"}},
            "directory_name": "iflr1000",
            "directory_source": "filename",
            "detected_year": "2024",
            "year_source": "content",
        }
        result = _exec_node(agent_def, "prepare_payload", state)
        classification = json.loads(result["classification_json"])
        assert classification["detected_year"] == "2025"
        assert classification["year_source"] == "llamaextract"
        # Original directory info preserved
        assert classification["directory"] == "iflr1000"


# =============================================================================
# STORY 2.2 — extract_practice_area node tests
# =============================================================================

class TestExtractPracticeArea:
    """Tests for the extract_practice_area run block (Story 2.2)."""

    # --- AC1: Chambers ---
    def test_chambers_practice_area(self, agent_def):
        """AC1: Chambers payloads yield practiceArea from preliminaryInformation."""
        state = {
            "payload_json": json.dumps({
                "preliminaryInformation": {"practiceArea": "Tax: Southeast"}
            }),
            "directory_name": "chambers",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == "Tax: Southeast"

    # --- AC2: Legal 500 ---
    def test_legal500_practice_area(self, agent_def):
        """AC2: Legal 500 payloads yield practiceArea from top-level."""
        state = {
            "payload_json": json.dumps({
                "practiceArea": "Brazil - City focus - Belo Horizonte - Commercial, corporate and M&A"
            }),
            "directory_name": "legal500",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == "Brazil - City focus - Belo Horizonte - Commercial, corporate and M&A"

    # --- AC3: IFLR1000 ---
    def test_iflr1000_practice_area(self, agent_def):
        """AC3: IFLR1000 payloads yield practiceArea from firmDetails."""
        state = {
            "payload_json": json.dumps({
                "firmDetails": {"practiceArea": "M&A"}
            }),
            "directory_name": "iflr1000",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == "M&A"

    # --- AC4: ITR boolean flags ---
    def test_itr_both_true(self, agent_def):
        """AC4: ITR both=true -> 'Tax and Transfer Pricing'."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"primary_practice_area": {"tax": True, "transferPricing": True, "both": True}}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == "Tax and Transfer Pricing"

    def test_itr_tax_only(self, agent_def):
        """AC4: ITR tax=true only -> 'Tax'."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"primary_practice_area": {"tax": True, "transferPricing": False, "both": False}}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == "Tax"

    def test_itr_transfer_pricing_only(self, agent_def):
        """AC4: ITR transferPricing=true only -> 'Transfer Pricing'."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"primary_practice_area": {"tax": False, "transferPricing": True, "both": False}}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == "Transfer Pricing"

    def test_itr_all_false(self, agent_def):
        """AC4: ITR all flags false -> empty string."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"primary_practice_area": {"tax": False, "transferPricing": False, "both": False}}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == ""

    def test_itr_string_passthrough(self, agent_def):
        """AC4: ITR primary_practice_area already a string -> passthrough."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"primary_practice_area": "Tax"}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == "Tax"

    # --- AC5: Leaders League ---
    def test_leadersleague_practice_area(self, agent_def):
        """AC5: Leaders League payloads yield practiceArea from practice_area."""
        state = {
            "payload_json": json.dumps({
                "practice_area": "Corporate / M&A"
            }),
            "directory_name": "leadersleague",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == "Corporate / M&A"

    # --- AC6: Missing practiceArea field ---
    def test_empty_payload_json(self, agent_def):
        """AC6: Empty payload_json -> empty string."""
        state = {
            "payload_json": "",
            "directory_name": "chambers",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == ""

    def test_invalid_json(self, agent_def):
        """AC6: Invalid JSON -> empty string."""
        state = {
            "payload_json": "not-json{",
            "directory_name": "chambers",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == ""

    def test_missing_practice_area_field(self, agent_def):
        """AC6: Valid payload but no practiceArea field -> empty string."""
        state = {
            "payload_json": json.dumps({"someOtherField": "value"}),
            "directory_name": "chambers",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == ""

    # --- AC7: Unrecognized directory ---
    def test_unknown_directory(self, agent_def):
        """AC7: Unknown directory -> empty string."""
        state = {
            "payload_json": json.dumps({"practiceArea": "Something"}),
            "directory_name": "unknown_dir",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == ""

    # --- AC8: Stored as stripped string ---
    def test_whitespace_stripped(self, agent_def):
        """AC8: Result is stripped."""
        state = {
            "payload_json": json.dumps({
                "practiceArea": "  Brazil - Tax  "
            }),
            "directory_name": "legal500",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == "Brazil - Tax"

    # --- M2 fix: Skip extraction on error status ---
    def test_error_status_skips_extraction(self, agent_def):
        """M2: When upstream failed (status=error), skip extraction."""
        state = {
            "payload_json": json.dumps({"error": "Extraction failed", "status": "failed"}),
            "directory_name": "chambers",
            "status": "error",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == ""

    # --- M3 fix: "leaders league" with space ---
    def test_leaders_league_with_space(self, agent_def):
        """M3: 'leaders league' (with space) variant works."""
        state = {
            "payload_json": json.dumps({"practice_area": "Banking & Finance"}),
            "directory_name": "leaders league",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == "Banking & Finance"

    # --- L1 fix: Verify warning logs ---
    def test_unknown_directory_logs_warning(self, agent_def, caplog):
        """L1/AC7: Unknown directory logs a warning."""
        import logging
        state = {
            "payload_json": json.dumps({"practiceArea": "Something"}),
            "directory_name": "unknown_dir",
        }
        with caplog.at_level(logging.WARNING):
            _exec_node(agent_def, "extract_practice_area", state)
        assert any("No practiceArea extractor for directory" in r.message for r in caplog.records)

    def test_missing_field_logs_warning(self, agent_def, caplog):
        """L1/AC6: Missing practiceArea field logs a warning."""
        import logging
        state = {
            "payload_json": json.dumps({"someField": "value"}),
            "directory_name": "legal500",
        }
        with caplog.at_level(logging.WARNING):
            _exec_node(agent_def, "extract_practice_area", state)
        assert any("No practiceArea found in payload" in r.message for r in caplog.records)

    # --- L3 fix: ITR primary_practice_area as None ---
    def test_itr_null_practice_area(self, agent_def):
        """L3: ITR primary_practice_area is null -> empty string."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"primary_practice_area": None}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_practice_area", state)
        assert result["extracted_practice_area"] == ""


# =============================================================================
# Story 2.3: PracticeArea Fuzzy Matching & Persistence
# =============================================================================

class TestResolvePracticeArea:
    """Tests for resolve_practice_area node (Story 2.3)."""

    def _make_mock_requests(self, practice_areas=None, status_code=200, raise_error=False):
        """Create a mock requests module with configurable GraphQL response."""
        from unittest.mock import MagicMock

        mock_requests = MagicMock()
        mock_response = MagicMock()

        if raise_error:
            mock_requests.post.side_effect = Exception("Connection refused")
        else:
            mock_response.status_code = status_code
            if status_code >= 400:
                mock_response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
            else:
                mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {
                "data": {"practiceArea": practice_areas or []}
            }
            mock_requests.post.return_value = mock_response

        return mock_requests

    def _make_mock_fuzzy_match_module(self, result=None):
        """Create a mock fuzzy_match module."""
        from unittest.mock import MagicMock
        mock_module = MagicMock()
        if result is None:
            result = {
                "matched_name": "Banking & Finance",
                "matched_id": "pa-123",
                "score": 1.0,
                "via_hint": False,
                "tier": "CA-1",
            }
        mock_module.fuzzy_match.return_value = result
        return mock_module

    # --- AC5: Empty extracted_practice_area -> early return ---
    def test_empty_practice_area_returns_empty(self, agent_def):
        """AC4/6.5: Empty extracted_practice_area -> early return, no GraphQL calls."""
        state = {"extracted_practice_area": ""}
        result = _exec_node_with_actions(agent_def, "resolve_practice_area", state)
        assert result == {"practice_area_match": {}}

    def test_missing_practice_area_returns_empty(self, agent_def):
        """AC4: Missing extracted_practice_area key -> early return."""
        state = {}
        result = _exec_node_with_actions(agent_def, "resolve_practice_area", state)
        assert result == {"practice_area_match": {}}

    # --- AC1: Query PracticeArea nodes filtered by directory ---
    def test_queries_by_directory_id(self, agent_def):
        """AC1: Queries PracticeArea nodes filtered by directory_id."""
        practice_areas = [
            {"id": "pa-1", "name": "Banking & Finance", "hints": None},
            {"id": "pa-2", "name": "Corporate Law", "hints": "M&A|Business"},
        ]
        mock_requests = self._make_mock_requests(practice_areas)
        mock_fm = self._make_mock_fuzzy_match_module()
        mock_actions = {"graphology.update_node": lambda **kwargs: {"success": True}}

        state = {
            "extracted_practice_area": "Banking & Finance",
            "directory_id": "dir-uuid-1",
            "context_node_id": "file-uuid-1",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_practice_area", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        assert result["practice_area_match"]["matched_name"] == "Banking & Finance"

        # Verify directory-filtered query was used
        call_args = mock_requests.post.call_args
        query_body = call_args[1].get("json") or call_args.kwargs.get("json", {})
        assert "practiceAreaInDirectory_SOME" in query_body["query"]
        assert query_body["variables"]["directoryId"] == "dir-uuid-1"

    # --- AC1 fallback: No directory_id -> fetch all PracticeAreas ---
    def test_fallback_to_all_practice_areas(self, agent_def):
        """6.8: Missing directory_id -> queries all PracticeAreas."""
        practice_areas = [
            {"id": "pa-1", "name": "Banking & Finance", "hints": None},
        ]
        mock_requests = self._make_mock_requests(practice_areas)
        mock_fm = self._make_mock_fuzzy_match_module()
        mock_actions = {"graphology.update_node": lambda **kwargs: {"success": True}}

        state = {
            "extracted_practice_area": "Banking & Finance",
            "context_node_id": "file-uuid-1",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_practice_area", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        assert result["practice_area_match"]["matched_name"] == "Banking & Finance"

        call_args = mock_requests.post.call_args
        query_body = call_args[1].get("json") or call_args.kwargs.get("json", {})
        assert "practiceAreaInDirectory_SOME" not in query_body["query"]

    # --- AC2: Fuzzy match invoked ---
    def test_invokes_fuzzy_match_with_master_list(self, agent_def):
        """AC2: fuzzy_match() is invoked with the extracted PA and master list."""
        practice_areas = [
            {"id": "pa-1", "name": "Banking & Finance", "hints": None},
            {"id": "pa-2", "name": "Corporate Law", "hints": "M&A|Business"},
        ]
        mock_requests = self._make_mock_requests(practice_areas)
        mock_fm = self._make_mock_fuzzy_match_module()
        mock_actions = {"graphology.update_node": lambda **kwargs: {"success": True}}

        state = {
            "extracted_practice_area": "Banking",
            "directory_id": "dir-1",
            "context_node_id": "file-1",
        }
        _exec_node_with_actions(
            agent_def, "resolve_practice_area", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        mock_fm.fuzzy_match.assert_called_once()
        args = mock_fm.fuzzy_match.call_args[0]
        assert args[0] == "Banking"
        assert len(args[1]) == 2
        assert args[1][0]["name"] == "Banking & Finance"
        assert args[1][0]["hints"] == ""  # None converted to ""
        assert args[1][1]["hints"] == "M&A|Business"

    # --- AC3: Match found -> persist to ApplicationFormFile ---
    def test_persists_match_to_graph(self, agent_def):
        """AC3: Matched PA name and ID saved to ApplicationFormFile via graphology.update_node."""
        from unittest.mock import MagicMock

        practice_areas = [{"id": "pa-1", "name": "Banking & Finance", "hints": None}]
        mock_requests = self._make_mock_requests(practice_areas)
        mock_fm = self._make_mock_fuzzy_match_module({
            "matched_name": "Banking & Finance",
            "matched_id": "pa-1",
            "score": 1.0,
            "via_hint": False,
            "tier": "CA-1",
        })
        mock_update = MagicMock(return_value={"success": True})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_practice_area": "Banking & Finance",
            "directory_id": "dir-1",
            "context_node_id": "file-uuid-42",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_practice_area", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        assert result["practice_area_match"]["matched_name"] == "Banking & Finance"
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs["node_id"] == "file-uuid-42"
        assert call_kwargs["node_type"] == "ApplicationFormFile"
        assert call_kwargs["updates"]["practiceAreaName"] == "Banking & Finance"
        assert call_kwargs["updates"]["practiceAreaId"] == "pa-1"

    # --- AC4: No match -> warning logged, empty result ---
    def test_no_match_logs_warning(self, agent_def, caplog):
        """AC4: No match found -> warning logged, no GraphQL update."""
        import logging
        from unittest.mock import MagicMock

        practice_areas = [{"id": "pa-1", "name": "Banking & Finance", "hints": None}]
        mock_requests = self._make_mock_requests(practice_areas)
        no_match = {
            "matched_name": "",
            "matched_id": "",
            "score": 0.0,
            "via_hint": False,
            "tier": "CA-3",
        }
        mock_fm = self._make_mock_fuzzy_match_module(no_match)
        mock_update = MagicMock(return_value={"success": True})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_practice_area": "Completely Unknown Area",
            "directory_id": "dir-1",
            "context_node_id": "file-1",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "resolve_practice_area", state,
                mock_actions=mock_actions,
                mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
            )

        assert result["practice_area_match"]["matched_name"] == ""
        assert result["practice_area_match"]["tier"] == "CA-3"
        mock_update.assert_not_called()
        assert any("No match for" in r.message for r in caplog.records)

    # --- AC5: GraphQL failure -> pipeline continues ---
    def test_graphql_failure_continues(self, agent_def):
        """AC5/6.6: GraphQL down -> pipeline continues with empty match."""
        mock_requests = self._make_mock_requests(raise_error=True)

        state = {
            "extracted_practice_area": "Banking & Finance",
            "directory_id": "dir-1",
            "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_practice_area", state,
            mock_modules={"requests": mock_requests},
        )

        assert result == {"practice_area_match": {}}

    def test_graphql_http_error_continues(self, agent_def):
        """AC5: GraphQL returns HTTP error -> pipeline continues."""
        mock_requests = self._make_mock_requests(status_code=500)

        state = {
            "extracted_practice_area": "Banking & Finance",
            "directory_id": "dir-1",
            "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_practice_area", state,
            mock_modules={"requests": mock_requests},
        )

        assert result == {"practice_area_match": {}}

    # --- Empty GraphQL result ---
    def test_zero_practice_areas_returns_empty(self, agent_def):
        """AC1: Zero PracticeArea nodes returned -> empty match, no fuzzy_match call."""
        mock_requests = self._make_mock_requests(practice_areas=[])

        state = {
            "extracted_practice_area": "Banking & Finance",
            "directory_id": "dir-1",
            "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_practice_area", state,
            mock_modules={"requests": mock_requests},
        )

        assert result == {"practice_area_match": {}}

    # --- AC6: Idempotency ---
    def test_idempotent_same_result(self, agent_def):
        """AC6/6.7: Re-running produces identical match result."""
        practice_areas = [{"id": "pa-1", "name": "Banking & Finance", "hints": None}]
        match_result = {
            "matched_name": "Banking & Finance",
            "matched_id": "pa-1",
            "score": 1.0,
            "via_hint": False,
            "tier": "CA-1",
        }

        state = {
            "extracted_practice_area": "Banking & Finance",
            "directory_id": "dir-1",
            "context_node_id": "file-1",
        }
        mock_actions = {"graphology.update_node": lambda **kwargs: {"success": True}}

        results = []
        for _ in range(2):
            mock_requests = self._make_mock_requests(practice_areas)
            mock_fm = self._make_mock_fuzzy_match_module(match_result)
            r = _exec_node_with_actions(
                agent_def, "resolve_practice_area", state,
                mock_actions=mock_actions,
                mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
            )
            results.append(r)

        assert results[0] == results[1]

    # --- Routing ---
    def test_extract_practice_area_routes_to_extract_region(self, agent_def):
        """5.1: extract_practice_area routes to extract_region (Story 3.1)."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "extract_practice_area")
        goto = node.get("goto", [])
        targets = [g["to"] if isinstance(g, dict) else g for g in goto]
        assert "extract_region" in targets

    def test_resolve_routes_to_derive_legal_field(self, agent_def):
        """5.2: resolve_practice_area routes to derive_legal_field (Story 2.4)."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "resolve_practice_area")
        goto = node.get("goto", [])
        targets = [g["to"] if isinstance(g, dict) else g for g in goto]
        assert "derive_legal_field" in targets

    # --- State schema ---
    def test_practice_area_match_in_state_schema(self, agent_def):
        """1.2: practice_area_match is in state_schema."""
        assert "practice_area_match" in agent_def["state_schema"]

    # --- Code structure checks ---
    def test_uses_graphology_update_node_action(self, agent_def):
        """AC3: Uses actions['graphology.update_node'] for persistence."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "resolve_practice_area")
        code = node["run"]
        assert 'actions["graphology.update_node"]' in code

    def test_wrapped_in_try_except(self, agent_def):
        """AC5: Entire block wrapped in try/except for best-effort."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "resolve_practice_area")
        code = node["run"]
        assert "try:" in code
        assert "except Exception" in code

    def test_graphql_query_uses_correct_filter(self, agent_def):
        """4.1: GraphQL query uses practiceAreaInDirectory_SOME filter."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "resolve_practice_area")
        code = node["run"]
        assert "practiceAreaInDirectory_SOME" in code

    def test_null_hints_converted_to_empty_string(self, agent_def):
        """AC1: PracticeArea hints=null -> empty string in master_list."""
        practice_areas = [{"id": "pa-1", "name": "Banking & Finance", "hints": None}]
        mock_requests = self._make_mock_requests(practice_areas)
        mock_fm = self._make_mock_fuzzy_match_module()
        mock_actions = {"graphology.update_node": lambda **kwargs: {"success": True}}

        state = {
            "extracted_practice_area": "Banking",
            "directory_id": "dir-1",
            "context_node_id": "file-1",
        }
        _exec_node_with_actions(
            agent_def, "resolve_practice_area", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        args = mock_fm.fuzzy_match.call_args[0]
        assert args[1][0]["hints"] == ""  # None -> ""

    # --- H1 fix: update_node failure -> pipeline continues ---
    def test_update_node_failure_continues(self, agent_def):
        """AC5: update_node raises exception -> pipeline continues, match still returned."""
        from unittest.mock import MagicMock

        practice_areas = [{"id": "pa-1", "name": "Banking & Finance", "hints": None}]
        mock_requests = self._make_mock_requests(practice_areas)
        mock_fm = self._make_mock_fuzzy_match_module({
            "matched_name": "Banking & Finance",
            "matched_id": "pa-1",
            "score": 1.0,
            "via_hint": False,
            "tier": "CA-1",
        })
        mock_update = MagicMock(side_effect=Exception("GraphQL mutation failed"))
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_practice_area": "Banking & Finance",
            "directory_id": "dir-1",
            "context_node_id": "file-uuid-42",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_practice_area", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        # update_node exception is caught by inner try/except; match result is still returned
        assert result["practice_area_match"]["matched_name"] == "Banking & Finance"
        assert result["practice_area_match"]["matched_id"] == "pa-1"

    def test_update_node_returns_failure(self, agent_def, caplog):
        """AC5: update_node returns success=False -> warning logged, match still returned."""
        import logging
        from unittest.mock import MagicMock

        practice_areas = [{"id": "pa-1", "name": "Banking & Finance", "hints": None}]
        mock_requests = self._make_mock_requests(practice_areas)
        mock_fm = self._make_mock_fuzzy_match_module({
            "matched_name": "Banking & Finance",
            "matched_id": "pa-1",
            "score": 1.0,
            "via_hint": False,
            "tier": "CA-1",
        })
        mock_update = MagicMock(return_value={"success": False, "error": "node_id is required"})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_practice_area": "Banking & Finance",
            "directory_id": "dir-1",
            "context_node_id": "file-uuid-42",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "resolve_practice_area", state,
                mock_actions=mock_actions,
                mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
            )

        # Match result is still returned even when persistence fails
        assert result["practice_area_match"]["matched_name"] == "Banking & Finance"
        assert any("update_node failed" in r.message for r in caplog.records)

    # --- M2 fix: missing context_node_id -> warning, no update_node call ---
    def test_missing_context_node_id_skips_update(self, agent_def, caplog):
        """M2: Missing context_node_id -> warning logged, update_node not called."""
        import logging
        from unittest.mock import MagicMock

        practice_areas = [{"id": "pa-1", "name": "Banking & Finance", "hints": None}]
        mock_requests = self._make_mock_requests(practice_areas)
        mock_fm = self._make_mock_fuzzy_match_module({
            "matched_name": "Banking & Finance",
            "matched_id": "pa-1",
            "score": 1.0,
            "via_hint": False,
            "tier": "CA-1",
        })
        mock_update = MagicMock(return_value={"success": True})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_practice_area": "Banking & Finance",
            "directory_id": "dir-1",
            # context_node_id intentionally missing
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "resolve_practice_area", state,
                mock_actions=mock_actions,
                mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
            )

        # Match still returned
        assert result["practice_area_match"]["matched_name"] == "Banking & Finance"
        # update_node NOT called
        mock_update.assert_not_called()
        # Warning logged
        assert any("context_node_id missing" in r.message for r in caplog.records)


# =============================================================================
# derive_legal_field node tests (Story 2.4)
# =============================================================================

class TestDeriveLegalField:
    """Tests for derive_legal_field node (Story 2.4)."""

    def _make_mock_requests(self, practice_areas_response=None, status_code=200, raise_error=False):
        """Create a mock requests module with configurable GraphQL response."""
        from unittest.mock import MagicMock

        mock_requests = MagicMock()
        mock_response = MagicMock()

        if raise_error:
            mock_requests.post.side_effect = Exception("Connection refused")
        else:
            mock_response.status_code = status_code
            if status_code >= 400:
                mock_response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
            else:
                mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {
                "data": practice_areas_response or {}
            }
            mock_requests.post.return_value = mock_response

        return mock_requests

    # --- AC1: Successful LegalField retrieval via graph traversal ---
    def test_successful_legal_field_retrieval(self, agent_def):
        """AC1: PracticeArea with connected LegalField returns name and id."""
        from unittest.mock import MagicMock

        mock_requests = self._make_mock_requests({
            "practiceArea": [{
                "practiceAreaHasLegalField": [{"id": "lf-123", "lfName": "Banking Law"}]
            }]
        })
        mock_update = MagicMock(return_value={"success": True})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "practice_area_match": {"matched_id": "pa-456", "matched_name": "Banking"},
            "context_node_id": "file-789",
        }
        result = _exec_node_with_actions(
            agent_def, "derive_legal_field", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        assert result["legal_field_name"] == ["Banking Law"]
        assert result["legal_field_id"] == ["lf-123"]

    # --- AC2: Persist legalFieldName and legalFieldId to ApplicationFormFile ---
    def test_persists_legal_field_to_graph(self, agent_def):
        """AC2: legalFieldName and legalFieldId saved via graphology.update_node."""
        from unittest.mock import MagicMock

        mock_requests = self._make_mock_requests({
            "practiceArea": [{
                "practiceAreaHasLegalField": [{"id": "lf-100", "lfName": "Tax Law"}]
            }]
        })
        mock_update = MagicMock(return_value={"success": True})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "practice_area_match": {"matched_id": "pa-200"},
            "context_node_id": "file-300",
        }
        result = _exec_node_with_actions(
            agent_def, "derive_legal_field", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs["node_type"] == "ApplicationFormFile"
        assert call_kwargs["updates"]["legalFieldName"] == ["Tax Law"]
        assert call_kwargs["updates"]["legalFieldId"] == ["lf-100"]
        assert call_kwargs["node_id"] == "file-300"

    # --- AC3: No PracticeArea matched -> skip ---
    def test_no_practice_area_id_skips(self, agent_def):
        """AC3: Empty practiceAreaId -> step skipped, empty lists returned."""
        state = {"practice_area_match": {"matched_id": ""}}
        result = _exec_node_with_actions(agent_def, "derive_legal_field", state)
        assert result == {"legal_field_name": [], "legal_field_id": []}

    def test_missing_practice_area_match_skips(self, agent_def):
        """AC3: Missing practice_area_match key -> skip."""
        state = {}
        result = _exec_node_with_actions(agent_def, "derive_legal_field", state)
        assert result == {"legal_field_name": [], "legal_field_id": []}

    def test_empty_practice_area_match_dict_skips(self, agent_def):
        """AC3: Empty practice_area_match dict -> skip."""
        state = {"practice_area_match": {}}
        result = _exec_node_with_actions(agent_def, "derive_legal_field", state)
        assert result == {"legal_field_name": [], "legal_field_id": []}

    # --- AC4: PracticeArea has no connected LegalField ---
    def test_no_legal_field_connected(self, agent_def, caplog):
        """AC4: PracticeArea has no LegalField edge -> empty, warning logged."""
        import logging

        mock_requests = self._make_mock_requests({
            "practiceArea": [{"practiceAreaHasLegalField": []}]
        })
        state = {
            "practice_area_match": {"matched_id": "pa-no-lf"},
            "context_node_id": "file-1",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "derive_legal_field", state,
                mock_modules={"requests": mock_requests},
            )

        assert result == {"legal_field_name": [], "legal_field_id": []}
        assert any("No LegalField found" in r.message for r in caplog.records)

    def test_no_legal_field_null_field(self, agent_def, caplog):
        """AC4: practiceAreaHasLegalField is null -> empty, warning logged."""
        import logging

        mock_requests = self._make_mock_requests({
            "practiceArea": [{"practiceAreaHasLegalField": None}]
        })
        state = {
            "practice_area_match": {"matched_id": "pa-null-lf"},
            "context_node_id": "file-1",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "derive_legal_field", state,
                mock_modules={"requests": mock_requests},
            )

        assert result == {"legal_field_name": [], "legal_field_id": []}
        assert any("No LegalField found" in r.message for r in caplog.records)

    def test_empty_practice_areas_list(self, agent_def, caplog):
        """AC4: practiceArea returns empty list -> warning, empty result."""
        import logging

        mock_requests = self._make_mock_requests({
            "practiceArea": []
        })
        state = {
            "practice_area_match": {"matched_id": "pa-missing"},
            "context_node_id": "file-1",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "derive_legal_field", state,
                mock_modules={"requests": mock_requests},
            )

        assert result == {"legal_field_name": [], "legal_field_id": []}
        assert any("No LegalField found" in r.message for r in caplog.records)

    # --- AC5: GraphQL typed API ---
    def test_graphql_query_uses_practice_area_id(self, agent_def):
        """AC5: GraphQL query passes practiceAreaId as variable."""
        from unittest.mock import MagicMock

        mock_requests = self._make_mock_requests({
            "practiceArea": [{
                "practiceAreaHasLegalField": [{"id": "lf-1", "lfName": "IP"}]
            }]
        })
        mock_actions = {"graphology.update_node": MagicMock(return_value={"success": True})}

        state = {
            "practice_area_match": {"matched_id": "pa-target-id"},
            "context_node_id": "file-1",
        }
        _exec_node_with_actions(
            agent_def, "derive_legal_field", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        call_args = mock_requests.post.call_args
        request_body = call_args[1].get("json") or call_args.kwargs.get("json", {})
        assert request_body["variables"]["paId"] == "pa-target-id"
        assert "practiceAreaHasLegalField" in request_body["query"]

    # --- AC6: Best-effort error handling ---
    def test_graphql_connection_failure(self, agent_def, caplog):
        """AC6: GraphQL endpoint unreachable -> warning, empty, no crash."""
        import logging

        mock_requests = self._make_mock_requests(raise_error=True)
        state = {
            "practice_area_match": {"matched_id": "pa-1"},
            "context_node_id": "file-1",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "derive_legal_field", state,
                mock_modules={"requests": mock_requests},
            )

        assert result == {"legal_field_name": [], "legal_field_id": []}
        assert any("attempts failed" in r.message or "failed" in r.message for r in caplog.records)

    def test_graphql_http_error(self, agent_def, caplog):
        """AC6: GraphQL returns HTTP 500 -> warning, empty, no crash."""
        import logging

        mock_requests = self._make_mock_requests(status_code=500)
        state = {
            "practice_area_match": {"matched_id": "pa-1"},
            "context_node_id": "file-1",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "derive_legal_field", state,
                mock_modules={"requests": mock_requests},
            )

        assert result == {"legal_field_name": [], "legal_field_id": []}

    def test_malformed_graphql_response(self, agent_def):
        """AC6: GraphQL response missing data key -> empty, no crash."""
        from unittest.mock import MagicMock

        mock_requests = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"errors": [{"message": "oops"}]}
        mock_requests.post.return_value = mock_response

        state = {
            "practice_area_match": {"matched_id": "pa-1"},
            "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(
            agent_def, "derive_legal_field", state,
            mock_modules={"requests": mock_requests},
        )

        assert result == {"legal_field_name": [], "legal_field_id": []}

    # --- Edge cases ---
    def test_single_object_instead_of_list(self, agent_def):
        """Edge: practiceAreaHasLegalField returns single object, not list."""
        from unittest.mock import MagicMock

        mock_requests = self._make_mock_requests({
            "practiceArea": [{
                "practiceAreaHasLegalField": {"id": "lf-single", "lfName": "Litigation"}
            }]
        })
        mock_actions = {"graphology.update_node": MagicMock(return_value={"success": True})}

        state = {
            "practice_area_match": {"matched_id": "pa-1"},
            "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(
            agent_def, "derive_legal_field", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        assert result["legal_field_name"] == ["Litigation"]
        assert result["legal_field_id"] == ["lf-single"]

    def test_missing_context_node_id_skips_persistence(self, agent_def):
        """Edge: No context_node_id -> LegalField returned but not persisted."""
        from unittest.mock import MagicMock

        mock_requests = self._make_mock_requests({
            "practiceArea": [{
                "practiceAreaHasLegalField": [{"id": "lf-1", "lfName": "Tax"}]
            }]
        })
        mock_update = MagicMock(return_value={"success": True})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "practice_area_match": {"matched_id": "pa-1"},
            # context_node_id intentionally missing
        }
        result = _exec_node_with_actions(
            agent_def, "derive_legal_field", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        assert result["legal_field_name"] == ["Tax"]
        assert result["legal_field_id"] == ["lf-1"]
        mock_update.assert_not_called()

    def test_api_key_header_sent_when_present(self, agent_def):
        """Edge: API key from state.variables is sent as x-api-key header."""
        from unittest.mock import MagicMock

        mock_requests = self._make_mock_requests({
            "practiceArea": [{
                "practiceAreaHasLegalField": [{"id": "lf-1", "lfName": "IP"}]
            }]
        })
        mock_actions = {"graphology.update_node": MagicMock(return_value={"success": True})}

        state = {
            "practice_area_match": {"matched_id": "pa-1"},
            "context_node_id": "file-1",
            "variables": {
                "GRAPHOLOGY_URL": "http://graph:4000",
                "GRAPHOLOGY_API_KEY": "secret-key-123",
            },
        }
        _exec_node_with_actions(
            agent_def, "derive_legal_field", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        call_args = mock_requests.post.call_args
        headers = call_args[1].get("headers") or call_args.kwargs.get("headers", {})
        assert headers.get("x-api-key") == "secret-key-123"

    # --- Persistence failure handling ---
    def test_update_node_failure_logs_warning(self, agent_def, caplog):
        """M1: update_node failure is logged as warning (best-effort)."""
        import logging
        from unittest.mock import MagicMock

        mock_requests = self._make_mock_requests({
            "practiceArea": [{
                "practiceAreaHasLegalField": [{"id": "lf-1", "lfName": "Tax"}]
            }]
        })
        mock_update = MagicMock(return_value={"success": False, "error": "node not found"})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "practice_area_match": {"matched_id": "pa-1"},
            "context_node_id": "file-1",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "derive_legal_field", state,
                mock_actions=mock_actions,
                mock_modules={"requests": mock_requests},
            )

        assert result["legal_field_name"] == ["Tax"]
        assert result["legal_field_id"] == ["lf-1"]
        mock_update.assert_called_once()
        assert any("update_node failed" in r.message for r in caplog.records)

    def test_update_node_failure_still_returns_result(self, agent_def):
        """M1: Even when persistence fails, legal field values are returned."""
        from unittest.mock import MagicMock

        mock_requests = self._make_mock_requests({
            "practiceArea": [{
                "practiceAreaHasLegalField": [{"id": "lf-2", "lfName": "IP Law"}]
            }]
        })
        mock_update = MagicMock(return_value={"success": False})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "practice_area_match": {"matched_id": "pa-2"},
            "context_node_id": "file-2",
        }
        result = _exec_node_with_actions(
            agent_def, "derive_legal_field", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests},
        )

        assert result["legal_field_name"] == ["IP Law"]
        assert result["legal_field_id"] == ["lf-2"]

    # --- Routing tests ---
    def test_resolve_practice_area_routes_to_derive_legal_field(self, agent_def):
        """Task 3.1: resolve_practice_area transitions to derive_legal_field."""
        for node in agent_def["nodes"]:
            if node["name"] == "resolve_practice_area":
                goto_targets = [g.get("to") or g for g in node["goto"]]
                assert "derive_legal_field" in goto_targets
                return
        pytest.fail("resolve_practice_area node not found")

    def test_derive_legal_field_routes_to_resolve_region(self, agent_def):
        """Story 3.2: derive_legal_field now transitions to resolve_region."""
        for node in agent_def["nodes"]:
            if node["name"] == "derive_legal_field":
                goto_targets = [g.get("to") or g for g in node["goto"]]
                assert "resolve_region" in goto_targets
                return
        pytest.fail("derive_legal_field node not found")

    # --- State schema tests ---
    def test_legal_field_name_in_state_schema(self, agent_def):
        """Task 1.1: legal_field_name exists in state_schema."""
        assert "legal_field_name" in agent_def["state_schema"]

    def test_legal_field_id_in_state_schema(self, agent_def):
        """Task 1.2: legal_field_id exists in state_schema."""
        assert "legal_field_id" in agent_def["state_schema"]


# =============================================================================
# STORY 3.1 — extract_region node tests
# =============================================================================

class TestExtractRegion:
    """Tests for the extract_region run block (Story 3.1)."""

    # --- AC1: Chambers ---
    def test_chambers_region(self, agent_def):
        """AC1: Chambers payloads yield region from preliminaryInformation.jurisdiction."""
        state = {
            "payload_json": json.dumps({
                "preliminaryInformation": {"jurisdiction": "Brazil"}
            }),
            "directory_name": "chambers",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "Brazil"

    # --- AC2: Legal 500 ---
    def test_legal500_region(self, agent_def):
        """AC2: Legal 500 payloads yield region from top-level country."""
        state = {
            "payload_json": json.dumps({"country": "United Kingdom"}),
            "directory_name": "legal500",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "United Kingdom"

    # --- AC3: IFLR1000 ---
    def test_iflr1000_region(self, agent_def):
        """AC3: IFLR1000 payloads yield region from firmDetails.jurisdiction."""
        state = {
            "payload_json": json.dumps({
                "firmDetails": {"jurisdiction": "Brazil"}
            }),
            "directory_name": "iflr1000",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "Brazil"

    # --- AC4: ITR ---
    def test_itr_region_dict_prefers_l2(self, agent_def):
        """AC4: ITR jurisdictions as dict -> prefer jurisdiction_l2 for matching."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"jurisdictions": {"jurisdiction_l1": "United Kingdom", "jurisdiction_l2": "London"}}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "London"
        assert result["extracted_region_context"] == "United Kingdom"

    def test_itr_region_dict_falls_back_to_l1(self, agent_def):
        """AC4: ITR jurisdictions dict with empty jurisdiction_l2 -> fall back to l1."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"jurisdictions": {"jurisdiction_l1": "Brazil - Regional", "jurisdiction_l2": ""}}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "Brazil - Regional"
        assert result["extracted_region_context"] == ""  # no context when only one level

    def test_itr_region_dict_l2_only(self, agent_def):
        """AC4: ITR jurisdictions dict with only jurisdiction_l2 -> returns l2."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"jurisdictions": {"jurisdiction_l2": "Minas Gerais"}}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "Minas Gerais"
        assert result["extracted_region_context"] == ""  # no l1, no context

    def test_itr_region_dict_both_levels(self, agent_def):
        """ITR with both l1 and l2 -> context stores l1 for display composition."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"jurisdictions": {"jurisdiction_l1": "Brazil - Regional", "jurisdiction_l2": "Minas Gerais"}}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "Minas Gerais"
        assert result["extracted_region_context"] == "Brazil - Regional"

    def test_itr_region_string(self, agent_def):
        """AC4: ITR jurisdictions as string (legacy) -> passthrough."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"jurisdictions": "Germany"}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "Germany"

    def test_itr_region_list(self, agent_def):
        """AC4: ITR jurisdictions as list (legacy) -> take first element."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"jurisdictions": ["Japan", "Korea"]}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "Japan"

    def test_itr_region_empty_list(self, agent_def):
        """AC4: ITR jurisdictions as empty list -> empty string."""
        state = {
            "payload_json": json.dumps({
                "firmInfo": {"jurisdictions": []}
            }),
            "directory_name": "itr",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == ""

    # --- AC5: Leaders League top-level jurisdiction field ---
    def test_leadersleague_jurisdiction(self, agent_def):
        """AC5: Leaders League -> top-level jurisdiction field (extracted from header)."""
        state = {
            "payload_json": json.dumps({
                "jurisdiction": "Brasil",
                "practice_area": "DATA PROTECTION",
            }),
            "directory_name": "leadersleague",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "Brasil"

    def test_leadersleague_jurisdiction_missing(self, agent_def):
        """AC5: Leaders League -> missing jurisdiction returns empty."""
        state = {
            "payload_json": json.dumps({
                "practice_area": "DATA PROTECTION",
            }),
            "directory_name": "leadersleague",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == ""

    def test_leaders_league_space_variant(self, agent_def):
        """AC5: 'leaders league' (with space) also works."""
        state = {
            "payload_json": json.dumps({
                "jurisdiction": "France",
            }),
            "directory_name": "leaders league",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "France"

    # --- AC6: Missing/empty region field ---
    def test_missing_region_field(self, agent_def):
        """AC6: Region field not in payload -> empty string, warning logged."""
        state = {
            "payload_json": json.dumps({"preliminaryInformation": {}}),
            "directory_name": "chambers",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == ""

    def test_empty_payload_json(self, agent_def):
        """AC6: Empty payload_json -> empty string."""
        state = {"payload_json": "", "directory_name": "chambers"}
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == ""

    def test_invalid_json(self, agent_def):
        """AC6: Invalid JSON -> empty string."""
        state = {"payload_json": "not-json", "directory_name": "chambers"}
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == ""

    # --- AC7: Unrecognized directory ---
    def test_unknown_directory(self, agent_def):
        """AC7: Unrecognized directory -> empty string, warning logged."""
        state = {
            "payload_json": json.dumps({"country": "Brazil"}),
            "directory_name": "unknowndir",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == ""

    # --- AC8: Result stored stripped ---
    def test_whitespace_stripped(self, agent_def):
        """AC8: Region string is stripped of whitespace."""
        state = {
            "payload_json": json.dumps({
                "preliminaryInformation": {"jurisdiction": "  Brazil  "}
            }),
            "directory_name": "chambers",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "Brazil"

    # --- Error status skip ---
    def test_error_status_skips(self, agent_def):
        """Upstream error status -> empty region, no crash."""
        state = {
            "payload_json": json.dumps({"country": "Brazil"}),
            "directory_name": "legal500",
            "status": "error",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == ""

    # --- Case-insensitive directory ---
    def test_directory_case_insensitive(self, agent_def):
        """Directory name is lowercased before lookup."""
        state = {
            "payload_json": json.dumps({"country": "United Kingdom"}),
            "directory_name": "Legal500",
        }
        result = _exec_node(agent_def, "extract_region", state)
        assert result["extracted_region"] == "United Kingdom"

    # --- Routing tests ---
    def test_extract_region_routes_to_resolve_practice_area(self, agent_def):
        """Task 3.2: extract_region transitions to resolve_practice_area."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "extract_region")
        goto = node.get("goto", [])
        targets = [g["to"] if isinstance(g, dict) else g for g in goto]
        assert "resolve_practice_area" in targets

    def test_extract_practice_area_routes_to_extract_region(self, agent_def):
        """Task 3.1: extract_practice_area transitions to extract_region."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "extract_practice_area")
        goto = node.get("goto", [])
        targets = [g["to"] if isinstance(g, dict) else g for g in goto]
        assert "extract_region" in targets

    # --- State schema ---
    def test_extracted_region_in_state_schema(self, agent_def):
        """Task 1.1: extracted_region exists in state_schema."""
        assert "extracted_region" in agent_def["state_schema"]


# =============================================================================
# resolve_region node tests (Story 3.2)
# =============================================================================

class TestResolveRegion:
    """Tests for resolve_region node (Story 3.2)."""

    def _make_mock_requests(self, regions=None, status_code=200, raise_error=False):
        """Create a mock requests module with configurable GraphQL response.

        The resolve_region node queries using aliased fields (parents/children)
        from a hierarchy relationship query. All regions are placed under
        ``parents`` (parent nodes in the hierarchy); ``children`` is empty.
        """
        from unittest.mock import MagicMock

        mock_requests = MagicMock()
        mock_response = MagicMock()

        if raise_error:
            mock_requests.post.side_effect = Exception("Connection refused")
        else:
            mock_response.status_code = status_code
            if status_code >= 400:
                mock_response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
            else:
                mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {
                "data": {"parents": regions or [], "children": []}
            }
            mock_requests.post.return_value = mock_response

        return mock_requests

    def _make_mock_fuzzy_match_module(self, result=None):
        """Create a mock fuzzy_match module."""
        from unittest.mock import MagicMock
        mock_module = MagicMock()
        if result is None:
            result = {
                "matched_name": "Minas Gerais",
                "matched_id": "region-123",
                "score": 1.0,
                "via_hint": False,
                "tier": "CA-1",
            }
        mock_module.fuzzy_match.return_value = result
        return mock_module

    # --- 4.2: Empty extracted_region -> early return ---
    def test_empty_region_returns_empty(self, agent_def):
        """AC5: Empty extracted_region -> early return, no GraphQL calls."""
        state = {"extracted_region": ""}
        result = _exec_node_with_actions(agent_def, "resolve_region", state)
        assert result == {"region_match": {}}

    def test_missing_region_returns_empty(self, agent_def):
        """AC5: Missing extracted_region key -> early return."""
        state = {}
        result = _exec_node_with_actions(agent_def, "resolve_region", state)
        assert result == {"region_match": {}}

    # --- 4.3: Successful GraphQL query -> correct master list ---
    def test_builds_master_list_with_hints(self, agent_def):
        """AC1/AC2: Queries Region nodes, builds master list with hint expansion."""
        regions = [
            {"id": "r-1", "regionName": "Minas Gerais", "hints": "MG|State of Minas Gerais"},
            {"id": "r-2", "regionName": "São Paulo", "hints": None},
        ]
        mock_requests = self._make_mock_requests(regions)
        mock_fm = self._make_mock_fuzzy_match_module()
        mock_actions = {"graphology.update_node": lambda **kwargs: {"success": True}}

        state = {
            "extracted_region": "Minas Gerais",
            "context_node_id": "file-uuid-1",
        }
        _exec_node_with_actions(
            agent_def, "resolve_region", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        mock_fm.fuzzy_match.assert_called_once()
        args = mock_fm.fuzzy_match.call_args[0]
        assert args[0] == "Minas Gerais"
        assert len(args[1]) == 2
        assert args[1][0]["name"] == "Minas Gerais"
        assert args[1][0]["hints"] == "MG|State of Minas Gerais"
        assert args[1][1]["hints"] == ""  # None converted to ""

    # --- 4.4: Exact match -> persisted via update_node ---
    def test_persists_match_to_graph(self, agent_def):
        """AC4: Matched Region name and ID saved to ApplicationFormFile."""
        from unittest.mock import MagicMock

        regions = [{"id": "r-1", "regionName": "Minas Gerais", "hints": "MG"}]
        mock_requests = self._make_mock_requests(regions)
        mock_fm = self._make_mock_fuzzy_match_module({
            "matched_name": "Minas Gerais",
            "matched_id": "r-1",
            "score": 1.0,
            "via_hint": False,
            "tier": "CA-1",
        })
        mock_update = MagicMock(return_value={"success": True})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_region": "Minas Gerais",
            "context_node_id": "file-uuid-42",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_region", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        assert result["region_match"]["matched_name"] == "Minas Gerais"
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs["node_id"] == "file-uuid-42"
        assert call_kwargs["node_type"] == "ApplicationFormFile"
        assert call_kwargs["updates"]["regionName"] == "Minas Gerais"
        assert call_kwargs["updates"]["regionId"] == "r-1"

    def test_persists_canonical_context_plus_matched_name(self, agent_def):
        """regionName resolves both levels to canonical names: 'Brazil, Southeast'."""
        from unittest.mock import MagicMock

        regions = [
            {"id": "r-1", "regionName": "Southeast", "hints": "Minas Gerais"},
            {"id": "r-2", "regionName": "Brazil", "hints": "Brasil|BR|Brazil - Regional"},
        ]
        mock_requests = self._make_mock_requests(regions)
        # fuzzy_match is called twice: once for l2 (main), once for l1 (context)
        mock_fm = self._make_mock_fuzzy_match_module()  # default won't work for two calls

        # Override: first call returns Southeast (l2 match), second returns Brazil (l1 context)
        mock_fm.fuzzy_match.side_effect = [
            {"matched_name": "Southeast", "matched_id": "r-1", "score": 1.0, "via_hint": True, "tier": "CA-1"},
            {"matched_name": "Brazil", "matched_id": "r-2", "score": 1.0, "via_hint": True, "tier": "CA-1"},
        ]

        mock_update = MagicMock(return_value={"success": True})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_region": "Minas Gerais",
            "extracted_region_context": "Brazil - Regional",
            "context_node_id": "file-uuid-42",
        }
        _exec_node_with_actions(
            agent_def, "resolve_region", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        call_kwargs = mock_update.call_args[1]
        assert call_kwargs["updates"]["regionName"] == "Brazil, Southeast"
        assert call_kwargs["updates"]["regionId"] == "r-1"

    # --- 4.5: Hint-based match (e.g., "MG" -> "Minas Gerais") ---
    def test_hint_based_match(self, agent_def):
        """AC3: Short hint like 'MG' matches via hint expansion."""
        regions = [{"id": "r-1", "regionName": "Minas Gerais", "hints": "MG|State of Minas Gerais"}]
        mock_requests = self._make_mock_requests(regions)
        mock_fm = self._make_mock_fuzzy_match_module({
            "matched_name": "Minas Gerais",
            "matched_id": "r-1",
            "score": 0.95,
            "via_hint": True,
            "tier": "CA-1",
        })
        mock_actions = {"graphology.update_node": lambda **kwargs: {"success": True}}

        state = {
            "extracted_region": "MG",
            "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_region", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        assert result["region_match"]["matched_name"] == "Minas Gerais"
        assert result["region_match"]["via_hint"] is True

    # --- 4.6: No match (CA-3) -> warning logged, empty fields ---
    def test_no_match_logs_warning(self, agent_def, caplog):
        """AC5: No match found -> warning logged, no GraphQL update."""
        import logging
        from unittest.mock import MagicMock

        regions = [{"id": "r-1", "regionName": "Minas Gerais", "hints": "MG"}]
        mock_requests = self._make_mock_requests(regions)
        no_match = {
            "matched_name": "",
            "matched_id": "",
            "score": 0.0,
            "via_hint": False,
            "tier": "CA-3",
        }
        mock_fm = self._make_mock_fuzzy_match_module(no_match)
        mock_update = MagicMock(return_value={"success": True})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_region": "Completely Unknown Region",
            "context_node_id": "file-1",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "resolve_region", state,
                mock_actions=mock_actions,
                mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
            )

        assert result["region_match"]["matched_name"] == ""
        assert result["region_match"]["tier"] == "CA-3"
        mock_update.assert_not_called()
        assert any("No match for" in r.message for r in caplog.records)

    # --- 4.7: GraphQL query failure -> best-effort, returns empty ---
    def test_graphql_failure_continues(self, agent_def):
        """AC6: GraphQL down -> pipeline continues with empty match."""
        mock_requests = self._make_mock_requests(raise_error=True)

        state = {
            "extracted_region": "Minas Gerais",
            "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_region", state,
            mock_modules={"requests": mock_requests},
        )

        assert result == {"region_match": {}}

    def test_graphql_http_error_continues(self, agent_def):
        """AC6: GraphQL returns HTTP error -> pipeline continues."""
        mock_requests = self._make_mock_requests(status_code=500)

        state = {
            "extracted_region": "Minas Gerais",
            "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_region", state,
            mock_modules={"requests": mock_requests},
        )

        assert result == {"region_match": {}}

    # --- 4.8: update_node failure -> warning logged, match result still returned ---
    def test_update_node_failure_continues(self, agent_def, caplog):
        """AC6: update_node raises exception -> pipeline continues, match result preserved."""
        import logging
        from unittest.mock import MagicMock

        regions = [{"id": "r-1", "regionName": "Minas Gerais", "hints": "MG"}]
        mock_requests = self._make_mock_requests(regions)
        mock_fm = self._make_mock_fuzzy_match_module({
            "matched_name": "Minas Gerais",
            "matched_id": "r-1",
            "score": 1.0,
            "via_hint": False,
            "tier": "CA-1",
        })
        mock_update = MagicMock(side_effect=Exception("GraphQL mutation failed"))
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_region": "Minas Gerais",
            "context_node_id": "file-uuid-42",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "resolve_region", state,
                mock_actions=mock_actions,
                mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
            )

        # Match result preserved even when persistence raises exception
        assert result["region_match"]["matched_name"] == "Minas Gerais"
        assert result["region_match"]["matched_id"] == "r-1"
        assert any("update_node exception" in r.message for r in caplog.records)

    def test_update_node_returns_failure(self, agent_def, caplog):
        """AC6: update_node returns success=False -> warning logged, match still returned."""
        import logging
        from unittest.mock import MagicMock

        regions = [{"id": "r-1", "regionName": "Minas Gerais", "hints": "MG"}]
        mock_requests = self._make_mock_requests(regions)
        mock_fm = self._make_mock_fuzzy_match_module({
            "matched_name": "Minas Gerais",
            "matched_id": "r-1",
            "score": 1.0,
            "via_hint": False,
            "tier": "CA-1",
        })
        mock_update = MagicMock(return_value={"success": False, "error": "node_id is required"})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_region": "Minas Gerais",
            "context_node_id": "file-uuid-42",
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "resolve_region", state,
                mock_actions=mock_actions,
                mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
            )

        # Match result is still returned even when persistence fails
        assert result["region_match"]["matched_name"] == "Minas Gerais"
        assert any("update_node failed" in r.message for r in caplog.records)

    # --- 4.9: Missing context_node_id -> match found but persistence skipped ---
    def test_missing_context_node_id_skips_update(self, agent_def, caplog):
        """AC4: Missing context_node_id -> warning logged, update_node not called."""
        import logging
        from unittest.mock import MagicMock

        regions = [{"id": "r-1", "regionName": "Minas Gerais", "hints": "MG"}]
        mock_requests = self._make_mock_requests(regions)
        mock_fm = self._make_mock_fuzzy_match_module({
            "matched_name": "Minas Gerais",
            "matched_id": "r-1",
            "score": 1.0,
            "via_hint": False,
            "tier": "CA-1",
        })
        mock_update = MagicMock(return_value={"success": True})
        mock_actions = {"graphology.update_node": mock_update}

        state = {
            "extracted_region": "Minas Gerais",
            # context_node_id intentionally missing
        }
        with caplog.at_level(logging.WARNING):
            result = _exec_node_with_actions(
                agent_def, "resolve_region", state,
                mock_actions=mock_actions,
                mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
            )

        # Match still returned
        assert result["region_match"]["matched_name"] == "Minas Gerais"
        # update_node NOT called
        mock_update.assert_not_called()
        # Warning logged
        assert any("context_node_id missing" in r.message for r in caplog.records)

    # --- 4.10: Idempotency -> same inputs produce same output ---
    def test_idempotent_same_result(self, agent_def):
        """AC7: Re-running produces identical match result."""
        regions = [{"id": "r-1", "regionName": "Minas Gerais", "hints": "MG"}]
        match_result = {
            "matched_name": "Minas Gerais",
            "matched_id": "r-1",
            "score": 1.0,
            "via_hint": False,
            "tier": "CA-1",
        }

        state = {
            "extracted_region": "Minas Gerais",
            "context_node_id": "file-1",
        }
        mock_actions = {"graphology.update_node": lambda **kwargs: {"success": True}}

        results = []
        for _ in range(2):
            mock_requests = self._make_mock_requests(regions)
            mock_fm = self._make_mock_fuzzy_match_module(match_result)
            r = _exec_node_with_actions(
                agent_def, "resolve_region", state,
                mock_actions=mock_actions,
                mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
            )
            results.append(r)

        assert results[0] == results[1]

    # --- Empty GraphQL result ---
    def test_zero_regions_returns_empty(self, agent_def):
        """AC1: Zero Region nodes returned -> empty match, no fuzzy_match call."""
        mock_requests = self._make_mock_requests(regions=[])

        state = {
            "extracted_region": "Minas Gerais",
            "context_node_id": "file-1",
        }
        result = _exec_node_with_actions(
            agent_def, "resolve_region", state,
            mock_modules={"requests": mock_requests},
        )

        assert result == {"region_match": {}}

    # --- Routing tests ---
    def test_derive_legal_field_routes_to_resolve_region(self, agent_def):
        """Task 3.1: derive_legal_field routes to resolve_region."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "derive_legal_field")
        goto = node.get("goto", [])
        targets = [g["to"] if isinstance(g, dict) else g for g in goto]
        assert "resolve_region" in targets

    def test_resolve_region_routes_to_save_payload(self, agent_def):
        """Task 3.2: resolve_region routes to extract_firm_department."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "resolve_region")
        goto = node.get("goto", [])
        targets = [g["to"] if isinstance(g, dict) else g for g in goto]
        assert "extract_firm_department" in targets

    # --- State schema ---
    def test_region_match_in_state_schema(self, agent_def):
        """Task 1.1: region_match is in state_schema."""
        assert "region_match" in agent_def["state_schema"]

    # --- Code structure checks ---
    def test_uses_graphology_update_node_action(self, agent_def):
        """AC4: Uses actions['graphology.update_node'] for persistence."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "resolve_region")
        code = node["run"]
        assert 'actions["graphology.update_node"]' in code

    def test_wrapped_in_try_except(self, agent_def):
        """AC6: Entire block wrapped in try/except for best-effort."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "resolve_region")
        code = node["run"]
        assert "try:" in code
        assert "except Exception" in code

    def test_graphql_query_uses_singular_region(self, agent_def):
        """AC1: GraphQL query uses singular 'region' field name with aliased hierarchy query."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "resolve_region")
        code = node["run"]
        # Query uses aliased parents/children from a REGION_HAS_CHILD hierarchy query
        assert "region(" in code or "region {" in code or "region{" in code
        # Data is merged from parents + children aliases
        assert '.get("parents")' in code
        assert '.get("children")' in code

    def test_no_directory_filter(self, agent_def):
        """Regions are global — no directory filter in query."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "resolve_region")
        code = node["run"]
        assert "where" not in code.lower() or "practiceAreaInDirectory" not in code

    def test_null_hints_converted_to_empty_string(self, agent_def):
        """AC1: Region hints=null -> empty string in master_list."""
        regions = [{"id": "r-1", "regionName": "Minas Gerais", "hints": None}]
        mock_requests = self._make_mock_requests(regions)
        mock_fm = self._make_mock_fuzzy_match_module()
        mock_actions = {"graphology.update_node": lambda **kwargs: {"success": True}}

        state = {
            "extracted_region": "Minas Gerais",
            "context_node_id": "file-1",
        }
        _exec_node_with_actions(
            agent_def, "resolve_region", state,
            mock_actions=mock_actions,
            mock_modules={"requests": mock_requests, "actions.fuzzy_match": mock_fm},
        )

        args = mock_fm.fuzzy_match.call_args[0]
        assert args[1][0]["hints"] == ""  # None -> ""

    def test_no_cypher_queries(self, agent_def):
        """Task 5.5: No direct Cypher queries — all operations use GraphQL API."""
        node = next(n for n in agent_def["nodes"] if n["name"] == "resolve_region")
        code = node["run"]
        assert "MATCH" not in code
        assert "MERGE" not in code
        assert "cypher" not in code.lower()
