#!/usr/bin/env python3
"""
CLI tool to import a file through the AI Workflow pipeline.

Flow:
  1. Upload file to GCS → storageUrl
  2. Create ApplicationFormFile node in the graph
     → NODE_CREATED trigger auto-fires file_extraction agent
  3. Poll until extraction completes (ApplicationFormFile.status = succeeded/failed)
  4. Report ProtoMatters created

The file_extraction agent is triggered automatically by the Apollo middleware
(On ApplicationFormFile Created trigger). The import_matter_qa agent fires
automatically when ProtoMatters are created (On ProtoMatter Created trigger).

Usage:
  python cli_import.py /path/to/file.docx
  python cli_import.py /path/to/file.pdf --directory chambers --year 2025
  python cli_import.py /path/to/file.docx --dry-run

Environment variables (from .env):
  GRAPHOLOGY_URL, GRAPHOLOGY_API_KEY
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
        "gcs_bucket": os.environ.get("GCS_BUCKET")
            or "rankellix-law.firebasestorage.app",
        "gcs_prefix": os.environ.get("GCS_PREFIX")
            or "imports/cli",
    }

    missing = []
    if not config["graphql_api_key"]:
        missing.append("GRAPHOLOGY_API_KEY")
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    return config



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
# STEP 3: Poll for extraction completion
# =============================================================================

POLL_INTERVAL = 5       # seconds between polls
POLL_TIMEOUT = 600      # max seconds to wait (10 min)
TERMINAL_STATUSES = {"succeeded", "failed"}


def get_file_status(file_id: str, config: dict) -> dict:
    """Query ApplicationFormFile status and ProtoMatter count."""
    query = """
    query GetFileStatus($fileId: ID!) {
      applicationFormFile(where: { id_EQ: $fileId }) {
        status
        practiceAreaName
        regionName
        legalFieldName
        fileHasProtoMatter {
          id
          status
          payload
        }
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

    files = result["data"]["applicationFormFile"]
    if not files:
        raise RuntimeError(f"ApplicationFormFile {file_id} not found")

    return files[0]


def poll_until_complete(file_id: str, config: dict) -> dict:
    """Poll ApplicationFormFile until status is terminal or timeout."""
    t0 = time.time()
    last_status = None
    last_pm_count = 0

    while True:
        elapsed = time.time() - t0
        if elapsed > POLL_TIMEOUT:
            raise RuntimeError(
                f"Timeout after {POLL_TIMEOUT}s. Last status: {last_status}, "
                f"ProtoMatters: {last_pm_count}"
            )

        try:
            file_data = get_file_status(file_id, config)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            print(f"  [{elapsed:5.0f}s] poll error: {type(e).__name__}, retrying...")
            time.sleep(POLL_INTERVAL)
            continue

        status = file_data.get("status") or "pending"
        proto_matters = file_data.get("fileHasProtoMatter", [])
        pm_count = len(proto_matters)
        imported_count = sum(
            1 for pm in proto_matters if pm.get("status") == "imported"
        )

        if status != last_status or pm_count != last_pm_count:
            print(
                f"  [{elapsed:5.0f}s] status={status}, "
                f"ProtoMatters={pm_count} (imported={imported_count})"
            )
            last_status = status
            last_pm_count = pm_count

        if status in TERMINAL_STATUSES and pm_count > 0 and imported_count == pm_count:
            return file_data

        if status == "failed":
            return file_data

        time.sleep(POLL_INTERVAL)


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
        print(f"  2. Create ApplicationFormFile node (triggers file_extraction automatically)")
        print(f"  3. Poll until extraction + import completes")
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
    # The NODE_CREATED trigger auto-fires file_extraction agent
    print("\n[2/3] Creating ApplicationFormFile node...")
    file_id = create_application_form_file(
        storage_url=storage_url,
        file_name=file_name,
        mime_type=mime_type,
        directory_name=args.directory,
        year=args.year,
        config=config,
    )
    print("  file_extraction triggered automatically via NODE_CREATED trigger")

    # --- Step 3: Poll until complete ---
    print("\n[3/3] Waiting for extraction + import to complete...")
    t0 = time.time()
    try:
        file_data = poll_until_complete(file_id, config)
    except RuntimeError as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    elapsed = time.time() - t0
    status = file_data.get("status", "unknown")
    proto_matters = file_data.get("fileHasProtoMatter", [])

    if status == "failed":
        print(f"\n  FAILED after {elapsed:.1f}s", file=sys.stderr)
        sys.exit(1)

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"Done in {elapsed:.1f}s. ApplicationFormFile: {file_id}")
    print(f"  status:           {status}")
    print(f"  practiceAreaName: {file_data.get('practiceAreaName') or '(empty)'}")
    print(f"  regionName:       {file_data.get('regionName') or '(empty)'}")
    print(f"  legalFieldName:   {file_data.get('legalFieldName') or '(empty)'}")
    print(f"  ProtoMatters:     {len(proto_matters)}")

    for i, pm in enumerate(proto_matters, 1):
        payload = json.loads(pm.get("payload", "{}")) if pm.get("payload") else {}
        # Try client name fields across all directory schemas
        client = (
            payload.get("clientName")           # chambers
            or payload.get("clientsAdvised")     # iflr1000
            or payload.get("client_advised")     # itr
            or payload.get("client")             # leadersleague
            or payload.get("nameOfClient")       # legal500
            or ""
        )
        deal = (
            payload.get("dealName")              # iflr1000
            or payload.get("matter_name")        # itr, leadersleague
            or ""
        )
        label = client or deal or "(unnamed)"
        if len(label) > 60:
            label = label[:57] + "..."
        print(f"    {i}. {pm['id'][:12]}... — {label} [{pm['status']}]")

    print(f"{'='*60}")


if __name__ == "__main__":
    main()
