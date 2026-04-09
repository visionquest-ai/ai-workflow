FROM python:3.11.14-slim-trixie

# =============================================================================
# Required environment variables:
#   RUN_AGENT_API_KEY            - API key for authenticating /run-agent requests (required, fail-fast on startup)
#   GRAPHOLOGY_URL               - GraphQL endpoint URL for Graphology (required)
#   GRAPHOLOGY_API_KEY           - API key for Graphology authentication (required)
#   LLAMAEXTRACT_API_KEY         - API key for LlamaExtract SDK (required for file extraction)
#   GOOGLE_APPLICATION_CREDENTIALS - Path to GCS service account JSON (required for GCS access)
#   NEO4J_URI                    - Neo4j connection URI (required for import pipeline)
#   NEO4J_USER                   - Neo4j username (required for import pipeline)
#   NEO4J_PASSWORD               - Neo4j password (required for import pipeline)
#
# Optional environment variables:
#   AGENT_NAME_PREFIX            - Namespace prefix for LlamaExtract agent names (optional)
# =============================================================================

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends libreoffice-writer default-jre-headless && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Non-root user for security (AC10)
RUN useradd -m -s /bin/bash appuser

WORKDIR /app

# Install Python deps as root first for better layer caching (AC8)
COPY the_edge_agent/python/ ./the_edge_agent/python/
COPY requirements.txt .
RUN pip install --no-cache-dir ./the_edge_agent/python && \
    pip install --no-cache-dir -r requirements.txt

# Install import pipeline deps (neo4j driver + httpx for GraphQL client)
RUN pip install --no-cache-dir neo4j>=5.0.0 httpx>=0.24.0

# Copy app code (after deps for layer caching) with appuser ownership
COPY --chown=appuser:appuser . .

# Copy rankellix_import module (copied into build context by cloudbuild.yaml)
COPY --chown=appuser:appuser rankellix_import/ /app/rankellix_import/

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:' + __import__('os').environ.get('PORT','8080') + '/health')" || exit 1

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-keep-alive 600"]
