#!/usr/bin/env python3
"""
Simulate the resolve_region pipeline against real sample files.

Runs:
1. Extract region string from JSON payload (same logic as extract_region step)
2. Query Apollo for hierarchy regions (new REGION_HAS_CHILD query)
3. Fuzzy match and show result

Usage:
    cd visionQuest/ai-workflow
    source .venv/bin/activate
    python tests/test_region_matching.py
"""

import json
import logging
import sys
from pathlib import Path

# Add parent to path so we can import fuzzy_match
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from actions.fuzzy_match import fuzzy_match

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# --- Config ---
GQL_URL = "https://rankellix.ai/graphology/graphql"
API_KEY = "c1befac2e952f32a54a90ec225f656327cf2413b03953f3a4cefc549273ca57c"
HEADERS = {"Content-Type": "application/json", "x-api-key": API_KEY}

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # ai-workflow -> visionQuest -> rankellix-ai

SAMPLES = [
    ("itr", "docs/bases/ITR World Tax/VF_ITR World Tax_2026_AMSA.json"),
    ("itr", "docs/bases/ITR World Tax/ITR World Tax_2024_GVM Advogados.json"),
    ("chambers", "docs/bases/Chambers/VF_Chambers and Partners_2025_Labour.json"),
    ("chambers", "docs/bases/Chambers/05.08_Chambers and Partners_2025_Consumer Law_Pessoa & Pessoa_vmf.json"),
    ("legal500", "docs/bases/The Legal 500/VF_The Legal 500_2026_City Focus BH_Dispute Resolution_LD.json"),
    ("legal500", "docs/bases/The Legal 500/VF_The Legal 500_2026_City Focus BH_Corporate_VWA.json"),
    ("legal500", "docs/bases/The Legal 500/VF_The Legal 500_2026_City Focus BH_Tax_VWA.json"),
    ("iflr1000", "docs/bases/IFLR1000/VF_iflr1000_2025_research_submission_form_LD-RM.json"),
    ("iflr1000", "docs/bases/IFLR1000/IFLR 1000_2025_NankranMourao.json"),
    ("leaders league", "docs/bases/Leaders League/VF_Arbitros_2026_Leaders League_ Henrique Barbosa.json"),
    ("leaders league", "docs/bases/Leaders League/VF_Dispute_Resolution_Southeast (Law_Firms) 2026_Erico Andrade.json"),
]


# --- Step 1: Extract region from payload (mirrors extract_region step) ---

def _extract_itr_region(p):
    jurisdictions = p.get("firmInfo", {}).get("jurisdictions", "")
    if isinstance(jurisdictions, dict):
        l1 = jurisdictions.get("jurisdiction_l1", "")
        l2 = jurisdictions.get("jurisdiction_l2", "")
        return l2 or l1, l1, l2
    if isinstance(jurisdictions, list):
        val = jurisdictions[0] if jurisdictions else ""
        return val, "", ""
    return str(jurisdictions), "", ""


def _extract_leaders_region(p):
    return p.get("jurisdiction", "") or ""


EXTRACTORS = {
    "chambers": lambda p: p.get("preliminaryInformation", {}).get("jurisdiction", ""),
    "iflr1000": lambda p: p.get("firmDetails", {}).get("jurisdiction", ""),
    "legal500": lambda p: p.get("country", ""),
    "itr": lambda p: _extract_itr_region(p),
    "leaders league": lambda p: _extract_leaders_region(p),
}


def extract_region(directory: str, payload: dict) -> tuple[str, str]:
    """Returns (extracted_region, context)."""
    extractor = EXTRACTORS.get(directory)
    if not extractor:
        return "", ""
    raw = extractor(payload)
    if isinstance(raw, tuple):
        region, l1, l2 = raw
        context = l1 if (l1 and l2) else ""
    else:
        region = raw or ""
        context = ""
    return str(region).strip(), context.strip()


# --- Step 2: Query hierarchy regions from Apollo ---

HIERARCHY_QUERY = """
query GetHierarchyRegions {
  parents: region(where: { regionHasChild_SOME: {} }) {
    id
    regionName
    regionType
    hints
  }
  children: region(where: { regionHasChildFrom_SOME: {} }) {
    id
    regionName
    regionType
    hints
  }
}
"""

LEVEL_PRIORITY = {
    "country": 0,
    "country region": 1,
    "state": 2,
    "country city": 3,
}


def fetch_hierarchy_regions() -> list[dict]:
    """Fetch and deduplicate hierarchy regions from Apollo."""
    resp = requests.post(
        GQL_URL,
        json={"query": HIERARCHY_QUERY},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})

    # Merge parents + children by id
    seen = {}
    for r in (data.get("parents") or []) + (data.get("children") or []):
        if r["id"] not in seen:
            seen[r["id"]] = r

    regions = list(seen.values())

    # Deduplicate by name: keep higher level
    best_by_name = {}
    for r in regions:
        rname = (r.get("regionName") or "").strip().lower()
        rprio = LEVEL_PRIORITY.get(r.get("regionType", ""), 99)
        if rname not in best_by_name or rprio < best_by_name[rname][1]:
            best_by_name[rname] = (r, rprio)

    deduped = [entry[0] for entry in best_by_name.values()]
    return regions, deduped


# --- Main ---

def main():
    print("=" * 70)
    print("REGION MATCHING SIMULATION")
    print("=" * 70)

    # Fetch regions once
    print("\n>>> Fetching hierarchy regions from Apollo...")
    all_regions, deduped_regions = fetch_hierarchy_regions()
    print(f"    Total hierarchy nodes: {len(all_regions)}")
    print(f"    After name dedup:      {len(deduped_regions)}")

    print("\n--- Hierarchy regions (deduped) ---")
    for r in sorted(deduped_regions, key=lambda x: LEVEL_PRIORITY.get(x.get("regionType", ""), 99)):
        print(f"    [{r['regionType']}] {r['regionName']}  hints={r.get('hints', '')[:60]}...")

    master_list = [
        {"name": r["regionName"], "id": r["id"], "hints": r.get("hints") or ""}
        for r in deduped_regions
    ]

    # Process each sample
    print("\n" + "=" * 70)
    print("SAMPLE RESULTS")
    print("=" * 70)

    base = Path(__file__).resolve().parent
    for directory, rel_path in SAMPLES:
        fpath = (REPO_ROOT / rel_path).resolve()
        short_name = fpath.name
        if not fpath.exists():
            print(f"\n[SKIP] {short_name} — file not found")
            continue

        with open(fpath) as f:
            payload = json.load(f)

        extracted, context = extract_region(directory, payload)

        result = fuzzy_match(extracted, master_list, prefer_canonical=True) if extracted else {
            "matched_name": "", "tier": "CA-3", "score": 0.0, "via_hint": False
        }

        status = "MATCH" if result["tier"] != "CA-3" else "NO MATCH"
        print(f"\n[{status}] {short_name}")
        print(f"    Directory:  {directory}")
        print(f"    Extracted:  '{extracted}'" + (f"  (context: '{context}')" if context else ""))
        print(f"    Result:     '{result['matched_name']}' | tier={result['tier']} | score={result['score']:.4f} | via_hint={result['via_hint']}")


if __name__ == "__main__":
    main()
