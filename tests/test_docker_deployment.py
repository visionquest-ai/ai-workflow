"""
Tests for Story 14.3: Docker & Deployment Readiness.

Validates Dockerfile configuration, .dockerignore, and deployment settings.

Usage:
    pytest tests/test_docker_deployment.py -v
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


# =============================================================================
# TASK 1: .dockerignore (AC4, AC9)
# =============================================================================

class TestDockerignore:
    """Verify .dockerignore excludes sensitive and unnecessary files."""

    def setup_method(self):
        self.dockerignore_path = PROJECT_ROOT / ".dockerignore"
        assert self.dockerignore_path.exists(), ".dockerignore must exist"
        self.content = self.dockerignore_path.read_text()
        self.lines = [
            line.strip()
            for line in self.content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_excludes_env(self):
        """AC9: .env must not be baked into image."""
        assert ".env" in self.lines

    def test_excludes_pycache(self):
        assert "__pycache__" in self.lines

    def test_excludes_git(self):
        assert ".git" in self.lines

    def test_excludes_bmad(self):
        assert any("_bmad" in line for line in self.lines)

    def test_excludes_tests(self):
        assert any("tests" in line for line in self.lines)

    def test_excludes_edge_agent_docs(self):
        assert any("the_edge_agent/docs" in line for line in self.lines)

    def test_excludes_edge_agent_rust(self):
        assert any("the_edge_agent/rust" in line for line in self.lines)

    def test_excludes_edge_agent_git(self):
        assert any("the_edge_agent/.git" in line for line in self.lines)

    def test_excludes_venv(self):
        assert ".venv" in self.lines

    def test_excludes_root_markdown(self):
        assert "*.md" in self.lines


# =============================================================================
# TASK 2: Dockerfile optimization (AC1, AC2, AC3, AC5, AC8, AC10)
# =============================================================================

class TestDockerfile:
    """Verify Dockerfile structure and configuration."""

    def setup_method(self):
        self.dockerfile_path = PROJECT_ROOT / "Dockerfile"
        assert self.dockerfile_path.exists(), "Dockerfile must exist"
        self.content = self.dockerfile_path.read_text()
        self.lines = self.content.splitlines()

    def test_uses_slim_base(self):
        """AC8: Use slim base image for size."""
        from_lines = [l for l in self.lines if l.strip().startswith("FROM")]
        assert any("slim" in l for l in from_lines)

    def test_base_image_pinned(self):
        """AC1: Pin base image tag for reproducibility (version + distro)."""
        from_lines = [l for l in self.lines if l.strip().startswith("FROM")]
        assert len(from_lines) >= 1
        tag = from_lines[0].split(":")[-1] if ":" in from_lines[0] else ""
        assert "latest" not in from_lines[0]
        # Must have version + distro (e.g. 3.11.14-slim-trixie), not just 3.11-slim
        parts = tag.strip().split("-")
        assert len(parts) >= 3, (
            f"Base image tag '{tag}' not fully pinned — use python:<version>-slim-<distro>"
        )

    def test_installs_libreoffice(self):
        """AC2: Image must include libreoffice-writer for docx2pdf."""
        assert "libreoffice-writer" in self.content

    def test_non_root_user_created(self):
        """AC10: Container must run as non-root."""
        assert "useradd" in self.content or "adduser" in self.content

    def test_user_directive_exists(self):
        """AC10: USER directive sets non-root user."""
        user_lines = [l.strip() for l in self.lines if l.strip().startswith("USER")]
        assert len(user_lines) >= 1
        assert any("appuser" in l for l in user_lines)

    def test_timeout_keep_alive(self):
        """AC5: CMD must include --timeout-keep-alive 600."""
        assert "--timeout-keep-alive" in self.content
        assert "600" in self.content

    def test_layer_caching_order(self):
        """AC8: requirements.txt copied before app code for layer caching."""
        req_copy_idx = None
        app_copy_idx = None
        for i, line in enumerate(self.lines):
            if "COPY" in line and "requirements.txt" in line:
                req_copy_idx = i
            # The final COPY that brings in all app code
            if "COPY" in line and ". ." in line:
                app_copy_idx = i
        if req_copy_idx is not None and app_copy_idx is not None:
            assert req_copy_idx < app_copy_idx, (
                "requirements.txt COPY must come before full app COPY for layer caching"
            )

    def test_pip_install_before_app_copy(self):
        """AC8: pip install should happen before copying all app code."""
        pip_idx = None
        final_copy_idx = None
        for i, line in enumerate(self.lines):
            if "pip install" in line:
                pip_idx = i
            if "COPY . ." in line.strip():
                final_copy_idx = i
        if pip_idx is not None and final_copy_idx is not None:
            assert pip_idx < final_copy_idx, (
                "pip install must come before COPY . . for layer caching"
            )

    def test_expose_8000(self):
        assert any("EXPOSE 8000" in line for line in self.lines)

    def test_healthcheck_instruction(self):
        """AC6: Dockerfile should include HEALTHCHECK for container orchestrators."""
        assert "HEALTHCHECK" in self.content
        assert "/health" in self.content


# =============================================================================
# TASK 3: Python dependencies (AC3)
# =============================================================================

class TestRequirements:
    """Verify all required Python packages are listed."""

    def setup_method(self):
        self.req_path = PROJECT_ROOT / "requirements.txt"
        assert self.req_path.exists(), "requirements.txt must exist"
        self.content = self.req_path.read_text()
        self.packages = [
            line.split(">=")[0].split("==")[0].strip().lower()
            for line in self.content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_has_fastapi(self):
        assert "fastapi" in self.packages

    def test_has_uvicorn(self):
        assert "uvicorn" in self.packages

    def test_has_requests(self):
        assert "requests" in self.packages

    def test_has_fsspec(self):
        assert "fsspec" in self.packages

    def test_has_gcsfs(self):
        assert "gcsfs" in self.packages

    def test_has_docx2pdf(self):
        assert "docx2pdf" in self.packages

    def test_has_openai(self):
        assert "openai" in self.packages

    def test_has_llama_cloud_services(self):
        """AC3: llama-cloud-services required for LlamaExtract SDK."""
        assert "llama-cloud-services" in self.packages

    def test_no_the_edge_agent_in_requirements(self):
        """the-edge-agent is installed from local submodule in Dockerfile, not from PyPI."""
        assert "the-edge-agent" not in self.packages


# =============================================================================
# TASK 4: Environment variable documentation (AC7)
# =============================================================================

class TestEnvVarDocumentation:
    """Verify required env vars are documented in Dockerfile."""

    def setup_method(self):
        self.dockerfile_path = PROJECT_ROOT / "Dockerfile"
        self.content = self.dockerfile_path.read_text()

    def test_documents_run_agent_api_key(self):
        assert "RUN_AGENT_API_KEY" in self.content

    def test_documents_graphology_url(self):
        assert "GRAPHOLOGY_URL" in self.content

    def test_documents_graphology_api_key(self):
        assert "GRAPHOLOGY_API_KEY" in self.content

    def test_documents_llamaextract_api_key(self):
        assert "LLAMAEXTRACT_API_KEY" in self.content

    def test_documents_google_credentials(self):
        assert "GOOGLE_APPLICATION_CREDENTIALS" in self.content

    def test_documents_agent_name_prefix(self):
        assert "AGENT_NAME_PREFIX" in self.content
