#!/usr/bin/env python3
"""
Populate Region and Country node hints via GraphQL mutations.

Reads hint mappings from JSON data files and updates (or creates) graph nodes
with pipe-delimited hint strings for fuzzy matching (Story 3.3).

Usage:
    python scripts/populate_region_hints.py                    # Update all hints
    python scripts/populate_region_hints.py --dry-run          # Preview without mutating
    python scripts/populate_region_hints.py --name "São Paulo" # Update single node
    python scripts/populate_region_hints.py --create --name "New Region" --hints "alias1|alias2"

Environment variables (from .env):
    GRAPHOLOGY_URL       GraphQL endpoint (default: http://localhost:4000)
    GRAPHOLOGY_API_KEY   API key for x-api-key header (optional)
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv


# =============================================================================
# CONFIGURATION
# =============================================================================

GRAPHQL_TIMEOUT_SECONDS = 30

REGION_HINTS_PATH = Path(__file__).parent.parent / "data" / "region_hints.json"
COUNTRY_HINTS_PATH = Path(__file__).parent.parent / "data" / "country_hints.json"


def load_config() -> dict:
    """Load config from .env files."""
    for env_path in [
        Path(__file__).parent / ".env",
        Path(__file__).parent.parent / ".env",
        Path.home() / "src/graphology/.env",
    ]:
        if env_path.exists():
            load_dotenv(env_path, override=False)

    return {
        "graphql_url": (
            os.environ.get("GRAPHOLOGY_URL")
            or os.environ.get("GRAPHQL_ENDPOINT")
            or "http://localhost:4000"
        ),
        "graphql_api_key": (
            os.environ.get("GRAPHOLOGY_API_KEY")
            or os.environ.get("GRAPHQL_API_KEY")
            or ""
        ),
    }


# =============================================================================
# GRAPHQL HELPERS
# =============================================================================

def execute_graphql(
    url: str, query: str, variables: dict | None = None, api_key: str = ""
) -> dict:
    """Execute a GraphQL operation and return the data payload."""
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key

    response = requests.post(url, json=payload, timeout=GRAPHQL_TIMEOUT_SECONDS, headers=headers)
    response.raise_for_status()

    result = response.json()
    if "errors" in result:
        error_msgs = "; ".join(e.get("message", str(e)) for e in result["errors"])
        raise RuntimeError(f"GraphQL errors: {error_msgs}")

    return result.get("data", {})


def query_all_regions(url: str, api_key: str = "") -> list[dict]:
    """Fetch all Region nodes with id, name, hints."""
    query = """
    query GetAllRegions {
      region {
        id
        name
        hints
      }
    }
    """
    data = execute_graphql(url, query, api_key=api_key)
    return data.get("region", [])


def query_all_countries(url: str, api_key: str = "") -> list[dict]:
    """Fetch all Country nodes with id, name, hints."""
    query = """
    query GetAllCountries {
      country {
        id
        name
        hints
      }
    }
    """
    data = execute_graphql(url, query, api_key=api_key)
    return data.get("country", [])


def update_region_hints(url: str, region_id: str, hints: str, api_key: str = "") -> dict:
    """Update hints on a Region node."""
    mutation = """
    mutation UpdateRegionHints($where: RegionWhere!, $update: RegionUpdateInput!) {
      updateRegion(where: $where, update: $update) {
        region {
          id
          name
          hints
        }
      }
    }
    """
    variables = {
        "where": {"id_EQ": region_id},
        "update": {"hints_SET": hints},
    }
    return execute_graphql(url, mutation, variables, api_key=api_key)


def update_country_hints(url: str, country_id: str, hints: str, api_key: str = "") -> dict:
    """Update hints on a Country node."""
    mutation = """
    mutation UpdateCountryHints($where: CountryWhere!, $update: CountryUpdateInput!) {
      updateCountry(where: $where, update: $update) {
        country {
          id
          name
          hints
        }
      }
    }
    """
    variables = {
        "where": {"id_EQ": country_id},
        "update": {"hints_SET": hints},
    }
    return execute_graphql(url, mutation, variables, api_key=api_key)


def create_region(url: str, name: str, hints: str, api_key: str = "") -> dict:
    """Create a new Region node with name and hints."""
    mutation = """
    mutation CreateRegion($input: [RegionCreateInput!]!) {
      createRegion(input: $input) {
        region {
          id
          name
          hints
        }
      }
    }
    """
    properties: dict = {"name": name}
    if hints:
        properties["hints"] = hints
    variables = {"input": [properties]}
    return execute_graphql(url, mutation, variables, api_key=api_key)


# =============================================================================
# DATA LOADING
# =============================================================================

def load_hints_file(path: Path) -> dict[str, str]:
    """Load a hints JSON file and return name→hints mapping (skip _comment)."""
    if not path.exists():
        print(f"ERROR: Hints file not found: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path) as f:
        data = json.load(f)

    return {k: v for k, v in data.items() if not k.startswith("_")}


# =============================================================================
# POPULATION LOGIC
# =============================================================================

def populate_hints(
    node_type: str,
    hints_map: dict[str, str],
    graph_nodes: list[dict],
    update_fn,
    config: dict,
    dry_run: bool = False,
    filter_name: str | None = None,
) -> tuple[int, int, list[str]]:
    """
    Populate hints on graph nodes.

    Returns:
        (success_count, failure_count, warnings)
    """
    # Build name→node lookup (case-insensitive)
    name_to_node = {}
    for node in graph_nodes:
        name_to_node[node["name"].lower()] = node

    success = 0
    failure = 0
    warnings = []

    for name, hints in hints_map.items():
        if filter_name and name.lower() != filter_name.lower():
            continue

        node = name_to_node.get(name.lower())
        if not node:
            warnings.append(f"  {node_type} not found in graph: '{name}'")
            continue

        node_id = node["id"]
        current_hints = node.get("hints") or ""

        if dry_run:
            status = "UNCHANGED" if current_hints == hints else "WOULD UPDATE"
            print(f"  [{status}] {name} (id={node_id[:12]}...)")
            if current_hints != hints:
                print(f"    Current: {current_hints or '(null)'}")
                print(f"    New:     {hints}")
            success += 1
            continue

        try:
            update_fn(config["graphql_url"], node_id, hints, api_key=config["graphql_api_key"])
            print(f"  [OK] {name}")
            success += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}", file=sys.stderr)
            failure += 1

    # Report graph nodes without hints configured
    configured_names = {n.lower() for n in hints_map}
    for node in graph_nodes:
        if node["name"].lower() not in configured_names:
            warnings.append(f"  {node_type} in graph but no hints configured: '{node['name']}'")

    return success, failure, warnings


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Populate Region and Country hints for fuzzy matching",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without mutating the graph",
    )
    parser.add_argument(
        "--name", type=str,
        help="Update a single Region or Country by name",
    )
    parser.add_argument(
        "--create", action="store_true",
        help="Create a new Region node (requires --name and --hints)",
    )
    parser.add_argument(
        "--hints", type=str,
        help="Pipe-delimited hints string (used with --create)",
    )
    parser.add_argument(
        "--env", type=Path,
        help="Path to .env file",
    )

    args = parser.parse_args()

    # Load env
    if args.env:
        load_dotenv(args.env, override=True)
    config = load_config()

    # Handle --create mode
    if args.create:
        if not args.name:
            print("ERROR: --create requires --name", file=sys.stderr)
            sys.exit(1)
        hints = args.hints or ""

        if args.dry_run:
            print(f"[DRY RUN] Would create Region: '{args.name}' with hints: '{hints}'")
            return

        print(f"Creating Region: '{args.name}'...")
        try:
            data = create_region(
                config["graphql_url"], args.name, hints,
                api_key=config["graphql_api_key"],
            )
            regions = data.get("createRegion", {}).get("region", [])
            if regions:
                new_id = regions[0].get("id", "")
                print(f"  [OK] Created Region '{args.name}' with id={new_id}")
                print(f"  Update region_hints.json with this ID for future idempotency.")
            else:
                print("  [FAIL] No region returned after creation", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"  [FAIL] {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Standard population mode
    print(f"{'='*60}")
    print(f"Region & Country Hints Population")
    print(f"{'='*60}")
    print(f"  GraphQL: {config['graphql_url']}")
    print(f"  Mode:    {'DRY RUN' if args.dry_run else 'EXECUTE'}")
    if args.name:
        print(f"  Filter:  {args.name}")
    print()

    # Load hints data
    region_hints = load_hints_file(REGION_HINTS_PATH)
    country_hints = load_hints_file(COUNTRY_HINTS_PATH)

    # Query current graph state
    print("Querying graph for current nodes...")
    try:
        regions = query_all_regions(config["graphql_url"], api_key=config["graphql_api_key"])
        countries = query_all_countries(config["graphql_url"], api_key=config["graphql_api_key"])
    except Exception as e:
        print(f"ERROR: Cannot query graph: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  Found {len(regions)} Region nodes, {len(countries)} Country nodes")
    print()

    # Populate Region hints
    print(f"--- Regions ({len(region_hints)} configured) ---")
    r_ok, r_fail, r_warn = populate_hints(
        "Region", region_hints, regions, update_region_hints,
        config, dry_run=args.dry_run, filter_name=args.name,
    )

    print()

    # Populate Country hints (only if Country type has hints field)
    print(f"--- Countries ({len(country_hints)} configured) ---")
    country_has_hints = any("hints" in c for c in countries)
    if country_has_hints:
        c_ok, c_fail, c_warn = populate_hints(
            "Country", country_hints, countries, update_country_hints,
            config, dry_run=args.dry_run, filter_name=args.name,
        )
    else:
        print("  SKIP: Country type does not have a 'hints' field in the schema.")
        print("  Country hints are prepared in country_hints.json for when the field is added.")
        c_ok, c_fail, c_warn = 0, 0, []

    # Warnings
    all_warnings = r_warn + c_warn
    if all_warnings:
        print(f"\n--- Warnings ({len(all_warnings)}) ---")
        for w in all_warnings:
            print(w)

    # Summary
    total_ok = r_ok + c_ok
    total_fail = r_fail + c_fail
    print(f"\n{'='*60}")
    print(f"Summary: {total_ok} updated, {total_fail} failed, {len(all_warnings)} warnings")
    print(f"{'='*60}")

    if total_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
