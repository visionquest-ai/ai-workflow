#!/usr/bin/env python3
"""
CLI tool to import a file through the AI Workflow pipeline.

Flow:
  1. Upload file to GCS → storageUrl
  2. Create ApplicationFormFile node in the graph
  3. Run file_extraction agent → extracts payload, creates ProtoMatters

The import_matter_qa step is handled automatically by database triggers
when ProtoMatters are created with status "pending".

Usage:
  python cli_import.py /path/to/file.docx
  python cli_import.py /path/to/file.pdf --directory chambers --year 2025
  python cli_import.py /path/to/file.docx --dry-run

Environment variables (from .env):
  GRAPHOLOGY_URL, GRAPHOLOGY_API_KEY, RUN_AGENT_API_KEY (or AI_WORKFLOW_API_KEY)
  AI_WORKFLOW_URL (defaults to https://neo4j.visionquest.space/ai-workflow)
  GCS_BUCKET (defaults to rankellix-law.firebasestorage.app)
"""

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


# =============================================================================
# CONFIGURATION
# =============================================================================

def load_config():
    """Load config from .env files."""
    # Try multiple .env locations
    for env_path in [
        Path(__file__).parent / ".env",
        Path.home() / "src/graphology/.env",
    ]:
        if env_path.exists():
            load_dotenv(env_path, override=False)

    config = {
        "graphql_url": os.environ.get("GRAPHOLOGY_URL")
            or os.environ.get("GRAPHQL_ENDPOINT")
            or "https://neo4j.visionquest.space/graphology/graphql",
        "graphql_api_key": os.environ.get("GRAPHOLOGY_API_KEY")
            or os.environ.get("GRAPHQL_API_KEY")
            or "",
        "ai_workflow_url": os.environ.get("AI_WORKFLOW_URL")
            or "https://neo4j.visionquest.space/ai-workflow",
        "ai_workflow_api_key": os.environ.get("RUN_AGENT_API_KEY")
            or os.environ.get("AI_WORKFLOW_API_KEY")
            or "",
        "gcs_bucket": os.environ.get("GCS_BUCKET")
            or "rankellix-law.firebasestorage.app",
        "gcs_prefix": os.environ.get("GCS_PREFIX")
            or "imports/cli",
    }

    missing = []
    if not config["graphql_api_key"]:
        missing.append("GRAPHOLOGY_API_KEY")
    if not config["ai_workflow_api_key"]:
        missing.append("RUN_AGENT_API_KEY or AI_WORKFLOW_API_KEY")
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    return config


# =============================================================================
# WORKFLOW IDS (from production graph)
# =============================================================================

FILE_EXTRACTION_WORKFLOW_ID = "07646202-ddd7-4086-8acf-4c51561328fc"


# =============================================================================
# STEP 1: Upload file to GCS
# =============================================================================

def upload_to_gcs(file_path: Path, config: dict) -> str:
    """Upload a local file to GCS and return the gs:// URL."""
    import fsspec

    bucket = config["gcs_bucket"]
    prefix = config["gcs_prefix"]
    timestamp = int(time.time() * 1000)
    safe_name = file_path.name.replace(" ", "_").replace("(", "").replace(")", "")
    gcs_path = f"{prefix}/{timestamp}-{safe_name}"
    gcs_url = f"gs://{bucket}/{gcs_path}"

    print(f"  Uploading to {gcs_url} ...")
    fs = fsspec.filesystem("gs")
    with open(file_path, "rb") as local_f:
        with fs.open(gcs_url, "wb") as gcs_f:
            gcs_f.write(local_f.read())

    print(f"  Upload complete: {gcs_url}")
    return gcs_url


# =============================================================================
# STEP 2: Create ApplicationFormFile node
# =============================================================================

def create_application_form_file(
    storage_url: str,
    file_name: str,
    mime_type: str,
    directory_name: str | None,
    year: str | None,
    config: dict,
) -> str:
    """Create an ApplicationFormFile node in the graph. Returns the node ID."""
    properties = {
        "storageUrl": storage_url,
        "fileName": file_name,
        "mimeType": mime_type,
    }
    if directory_name:
        properties["directoryName"] = directory_name
    if year:
        properties["year"] = year

    # Build dynamic args for the custom resolver (flat args, not input)
    gql_args_def = []
    gql_args_pass = []
    variables = {}
    for key, val in properties.items():
        gql_args_def.append(f"${key}: String")
        gql_args_pass.append(f"{key}: ${key}")
        variables[key] = val

    mutation = f"""
    mutation CreateFile({', '.join(gql_args_def)}) {{
      createApplicationFormFile({', '.join(gql_args_pass)}) {{
        id
      }}
    }}
    """

    headers = {
        "Content-Type": "application/json",
        "x-api-key": config["graphql_api_key"],
    }

    resp = requests.post(
        config["graphql_url"],
        json={"query": mutation, "variables": variables},
        headers=headers,
        timeout=30,
    )
    if resp.status_code != 200:
        try:
            err = resp.json()
            msg = err.get("errors", [{}])[0].get("message", resp.text[:200])
        except Exception:
            msg = resp.text[:200]
        raise RuntimeError(f"GraphQL error ({resp.status_code}): {msg}")

    result = resp.json()

    if "errors" in result:
        raise RuntimeError(f"GraphQL error: {result['errors'][0]['message']}")

    node_data = result["data"]["createApplicationFormFile"]
    if not node_data or not node_data.get("id"):
        raise RuntimeError("No ApplicationFormFile node created")

    node_id = node_data["id"]
    print(f"  Created ApplicationFormFile: {node_id}")
    return node_id


# =============================================================================
# STEP 3: Run file_extraction agent
# =============================================================================

def run_agent(agent: str, workflow_id: str, context_node_id: str, config: dict) -> dict:
    """Call the /run-agent endpoint and return the result."""
    url = f"{config['ai_workflow_url']}/run-agent"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": config["ai_workflow_api_key"],
    }
    payload = {
        "agent": agent,
        "workflow_id": workflow_id,
        "context_node_id": context_node_id,
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=600)
    resp.raise_for_status()
    return resp.json()


# =============================================================================
# STEP 4: Query ProtoMatters linked to file
# =============================================================================

def get_proto_matters(file_id: str, config: dict) -> list[dict]:
    """Query ProtoMatter nodes linked to an ApplicationFormFile."""
    query = """
    query GetProtoMatters($fileId: ID!) {
      protoMatter(where: {
        fileHasProtoMatterFrom_SOME: { id_EQ: $fileId }
      }) {
        id
        status
        directory
        payload
      }
    }
    """
    headers = {
        "Content-Type": "application/json",
        "x-api-key": config["graphql_api_key"],
    }
    resp = requests.post(
        config["graphql_url"],
        json={"query": query, "variables": {"fileId": file_id}},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()

    if "errors" in result:
        raise RuntimeError(f"GraphQL error: {result['errors'][0]['message']}")

    return result["data"]["protoMatter"]


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Import a file through the full AI Workflow pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli_import.py submission_form.docx
  python cli_import.py form.pdf --directory chambers --year 2025
  python cli_import.py form.docx --dry-run
        """,
    )
    parser.add_argument("file", type=Path, help="Path to the file to import (.doc, .docx, .pdf)")
    parser.add_argument("--directory", type=str, help="Directory name (chambers, iflr1000, legal500, itr, leadersleague)")
    parser.add_argument("--year", type=str, help="Year for the submission")
    parser.add_argument("--storage-url", type=str, help="Skip upload, use this existing GCS URL")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without executing")
    parser.add_argument("--env", type=Path, help="Path to .env file")

    args = parser.parse_args()

    # Validate file
    if not args.storage_url and not args.file.exists():
        print(f"ERROR: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    # Load env
    if args.env:
        load_dotenv(args.env, override=True)
    config = load_config()

    file_name = args.file.name
    mime_type, _ = mimetypes.guess_type(file_name)
    mime_type = mime_type or "application/octet-stream"

    print(f"\n{'='*60}")
    print(f"AI Workflow Pipeline — File Import")
    print(f"{'='*60}")
    print(f"  File:      {file_name}")
    print(f"  MIME:      {mime_type}")
    print(f"  Directory: {args.directory or '(auto-detect)'}")
    print(f"  Year:      {args.year or '(auto-detect)'}")
    print()

    if args.dry_run:
        print("[DRY RUN] Would execute:")
        print(f"  1. Upload {file_name} to GCS")
        print(f"  2. Create ApplicationFormFile node")
        print(f"  3. Run file_extraction agent")
        print(f"  Q&A is handled automatically by database triggers.")
        print("\nNo changes made.")
        return

    # --- Step 1: Upload to GCS ---
    print("[1/3] Uploading file to GCS...")
    if args.storage_url:
        storage_url = args.storage_url
        print(f"  Using existing URL: {storage_url}")
    else:
        storage_url = upload_to_gcs(args.file, config)

    # --- Step 2: Create ApplicationFormFile ---
    print("\n[2/3] Creating ApplicationFormFile node...")
    file_id = create_application_form_file(
        storage_url=storage_url,
        file_name=file_name,
        mime_type=mime_type,
        directory_name=args.directory,
        year=args.year,
        config=config,
    )

    # --- Step 3: Run file_extraction ---
    print("\n[3/3] Running file_extraction agent...")
    t0 = time.time()
    result = run_agent("file_extraction", FILE_EXTRACTION_WORKFLOW_ID, file_id, config)
    elapsed = time.time() - t0
    print(f"  Result: {result.get('status', 'unknown')} ({elapsed:.1f}s)")

    if not result.get("success"):
        print(f"  ERROR: {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)

    # --- Query ProtoMatters ---
    print("\n  Querying ProtoMatter nodes...")
    proto_matters = get_proto_matters(file_id, config)
    print(f"  Found {len(proto_matters)} ProtoMatter(s)")

    for i, pm in enumerate(proto_matters, 1):
        payload = json.loads(pm.get("payload", "{}")) if pm.get("payload") else {}
        matter_name = payload.get("matter_name", "(unnamed)")
        print(f"    {i}. {pm['id'][:12]}... — {matter_name} [{pm['status']}]")

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"Done. ApplicationFormFile: {file_id}")
    print(f"ProtoMatters created: {len(proto_matters)}")
    print(f"Q&A will be triggered automatically by the database.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
