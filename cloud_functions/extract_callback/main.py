"""
Cloud Function: extract_callback

Receives LlamaExtract webhook callbacks when a file extraction job completes.
Performs all post-extraction processing and saves results to the graph:
  - fuzzy match practice area → practiceAreaId
  - graph walk: practiceAreaId → legalFieldName[], legalFieldId[]
  - graph walk: practiceAreaId → category, categoryId
  - fuzzy match region → regionId, regionName
  - extract fileFirm, fileDepartment
  - single updateApplicationFormFile with all fields + payload + year + status

Trigger URL: {CLOUD_FUNCTION_URL}?nodeId={ApplicationFormFile-id}
Auth: x-api-key header (WEBHOOK_SECRET env var)
"""

import json
import logging
import os

import functions_framework
import requests

from fuzzy_match import fuzzy_match

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GRAPHOLOGY_URL = os.environ.get("GRAPHOLOGY_URL", "").rstrip("/")
GRAPHOLOGY_API_KEY = os.environ.get("GRAPHOLOGY_API_KEY", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


# =============================================================================
# AUTH
# =============================================================================

def _check_auth(request) -> bool:
    key = request.headers.get("x-api-key") or request.args.get("api_key", "")
    if not WEBHOOK_SECRET:
        logger.warning("WEBHOOK_SECRET not set — rejecting all requests")
        return False
    return key == WEBHOOK_SECRET


# =============================================================================
# GRAPHOLOGY HELPERS
# =============================================================================

def _gql(query: str, variables: dict = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if GRAPHOLOGY_API_KEY:
        headers["x-api-key"] = GRAPHOLOGY_API_KEY
    resp = requests.post(
        GRAPHOLOGY_URL,
        json={"query": query, "variables": variables or {}},
        headers=headers,
        timeout=30,
    )
    if not resp.ok:
        logger.error("_gql error %s: %s", resp.status_code, resp.text[:500])
    resp.raise_for_status()
    return resp.json().get("data", {})


def fetch_file_node(node_id: str) -> dict:
    data = _gql("""
        query GetFile($id: ID!) {
          applicationFormFile(where: { id_EQ: $id }) {
            id
            directoryName
            directoryId
          }
        }
    """, {"id": node_id})
    files = data.get("applicationFormFile", [])
    return files[0] if files else {}


def fetch_practice_areas(directory_id: str) -> list:
    if directory_id:
        data = _gql("""
            query GetPAByDir($dirId: ID!) {
              practiceArea(where: { practiceAreaInDirectory_SOME: { id_EQ: $dirId } }) {
                id name hints
              }
            }
        """, {"dirId": directory_id})
    else:
        data = _gql("""
            query GetAllPA {
              practiceArea { id name hints }
            }
        """)
    return data.get("practiceArea", [])


def fetch_legal_fields(pa_id: str) -> tuple[list, list]:
    data = _gql("""
        query GetLF($paId: ID!) {
          practiceArea(where: { id_EQ: $paId }) {
            practiceAreaHasLegalField {
              ... on LegalField { id lfName }
            }
          }
        }
    """, {"paId": pa_id})
    pas = data.get("practiceArea", [])
    if not pas:
        return [], []
    lf_list = pas[0].get("practiceAreaHasLegalField") or []
    if not isinstance(lf_list, list):
        lf_list = [lf_list]
    names = [lf.get("lfName", "") for lf in lf_list if lf.get("lfName")]
    ids = [lf.get("id", "") for lf in lf_list if lf.get("id")]
    return names, ids


def fetch_category(pa_id: str) -> tuple[str | None, str | None]:
    data = _gql("""
        query GetCat($paId: ID!) {
          practiceArea(where: { id_EQ: $paId }) {
            practiceAreaHasCategory { id categoryName }
          }
        }
    """, {"paId": pa_id})
    pas = data.get("practiceArea", [])
    if not pas:
        return None, None
    cat_list = pas[0].get("practiceAreaHasCategory") or []
    if not isinstance(cat_list, list):
        cat_list = [cat_list]
    if cat_list:
        return cat_list[0].get("categoryName"), cat_list[0].get("id")
    return None, None


def fetch_regions() -> list:
    data = _gql("""
        query GetRegions {
          parents: region(where: { regionHasChild_SOME: {} }) { id regionName regionType hints }
          children: region(where: { regionHasChildFrom_SOME: {} }) { id regionName regionType hints }
        }
    """)
    seen = {}
    for r in (data.get("parents") or []) + (data.get("children") or []):
        if r["id"] not in seen:
            seen[r["id"]] = r
    # Deduplicate by regionName: prefer higher hierarchy level
    LEVEL_PRIORITY = {"country": 0, "country region": 1, "state": 2, "country city": 3}
    best_by_name = {}
    for r in seen.values():
        rname = (r.get("regionName") or "").strip().lower()
        rprio = LEVEL_PRIORITY.get(r.get("regionType", ""), 99)
        if rname not in best_by_name or rprio < best_by_name[rname][1]:
            best_by_name[rname] = (r, rprio)
    return [entry[0] for entry in best_by_name.values()]


def update_file_node(node_id: str, updates: dict):
    """Update ApplicationFormFile using parameterized variables to avoid inline injection."""
    # Build _SET keys and a variables dict with unique param names
    set_entries = []
    variables: dict = {"id": node_id}
    for k, v in updates.items():
        param = f"p_{k}"
        set_entries.append(f"{k}_SET: ${param}")
        variables[param] = v

    set_block = ", ".join(set_entries)

    # Build variable declarations — infer type from value
    def _gql_type(v):
        if isinstance(v, list):
            return "[String]"
        if isinstance(v, bool):
            return "Boolean"
        return "String"

    var_decls = ", ".join(f"${p}: {_gql_type(variables[p])}" for p in variables if p != "id")
    if var_decls:
        var_decls = f", {var_decls}"

    mutation = f"""
        mutation UpdateFile($id: ID!{var_decls}) {{
          updateApplicationFormFile(
            where: {{ id_EQ: $id }}
            update: {{ {set_block} }}
          ) {{ applicationFormFile {{ id }} }}
        }}
    """
    try:
        _gql(mutation, variables)
        logger.info("update_file_node: updated %s with keys %s", node_id, list(updates.keys()))
    except Exception as e:
        logger.error("update_file_node: failed for %s: %s", node_id, e)
        raise


# =============================================================================
# PAYLOAD EXTRACTORS (mirrors file_extraction.yaml logic)
# =============================================================================

def extract_year(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    year = str(data.get("year") or "").strip()
    if not year:
        for val in data.values():
            if isinstance(val, dict):
                year = str(val.get("year") or "").strip()
                if year:
                    break
    return year


def extract_practice_area(data: dict, directory: str) -> str:
    def _itr_pa(p):
        pa = p.get("firmInfo", {}).get("primary_practice_area", {})
        if isinstance(pa, str):
            return pa
        if isinstance(pa, dict):
            if pa.get("both"):
                return "Tax and Transfer Pricing"
            parts = []
            if pa.get("tax"):
                parts.append("Tax")
            if pa.get("transferPricing"):
                parts.append("Transfer Pricing")
            return " and ".join(parts) if parts else ""
        return ""

    extractors = {
        "chambers":     lambda p: p.get("preliminaryInformation", {}).get("practiceArea", ""),
        "iflr1000":     lambda p: p.get("firmDetails", {}).get("practiceArea", ""),
        "legal500":     lambda p: p.get("practiceArea", ""),
        "leadersleague":lambda p: p.get("practice_area", ""),
        "leaders league":lambda p: p.get("practice_area", ""),
        "itr":          _itr_pa,
    }
    fn = extractors.get(directory)
    return str(fn(data) or "").strip() if fn else ""


def extract_region(data: dict, directory: str) -> tuple[str, str]:
    """Returns (region, context) where context is the parent level for ITR."""
    def _itr_region(p):
        j = p.get("firmInfo", {}).get("jurisdictions", "")
        if isinstance(j, dict):
            l1 = j.get("jurisdiction_l1", "")
            l2 = j.get("jurisdiction_l2", "")
            return (l2 or l1, l1, l2)
        if isinstance(j, list):
            return (j[0] if j else "", "", "")
        return (str(j), "", "")

    extractors = {
        "chambers":      lambda p: p.get("preliminaryInformation", {}).get("jurisdiction", ""),
        "iflr1000":      lambda p: p.get("firmDetails", {}).get("jurisdiction", ""),
        "legal500":      lambda p: p.get("country", ""),
        "leadersleague": lambda p: p.get("jurisdiction", "") or "",
        "leaders league":lambda p: p.get("jurisdiction", "") or "",
        "itr":           _itr_region,
    }
    fn = extractors.get(directory)
    if not fn:
        return "", ""
    raw = fn(data)
    if isinstance(raw, tuple):
        region, l1, l2 = raw
        context = l1 if (l1 and l2) else ""
    else:
        region, context = str(raw or "").strip(), ""
    return region, context


def extract_firm_department(data: dict, directory: str) -> tuple[str, str]:
    firm_extractors = {
        "chambers":      lambda p: p.get("preliminaryInformation", {}).get("firmName", ""),
        "iflr1000":      lambda p: p.get("firmDetails", {}).get("firmName", ""),
        "legal500":      lambda p: p.get("firmName", ""),
        "itr":           lambda p: p.get("firmInfo", {}).get("firm_name", ""),
        "leadersleague": lambda p: p.get("firm_information", {}).get("firm_name", ""),
        "leaders league":lambda p: p.get("firm_information", {}).get("firm_name", ""),
    }
    dept_extractors = {
        "chambers": lambda p: p.get("departmentInfo", {}).get("name", ""),
        "legal500": lambda p: p.get("teamOrDepartmentName", ""),
    }
    firm = str((firm_extractors.get(directory) or (lambda p: ""))(data) or "").strip()
    dept = str((dept_extractors.get(directory) or (lambda p: ""))(data) or "").strip()
    return firm, dept


# =============================================================================
# MAIN HANDLER
# =============================================================================

@functions_framework.http
def handle_webhook(request):
    if not _check_auth(request):
        return json.dumps({"error": "Unauthorized"}), 401, {"Content-Type": "application/json"}

    node_id = request.args.get("nodeId", "").strip()
    if not node_id:
        return json.dumps({"error": "Missing nodeId query param"}), 400, {"Content-Type": "application/json"}

    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return json.dumps({"error": "Invalid JSON body"}), 400, {"Content-Type": "application/json"}

    # LlamaExtract webhook sends: {"event_id", "event_type", "timestamp", "data": {...}}
    # Manual/agent calls send: {"id", "status", "result": {...}}
    # Normalize both formats:
    if "event_type" in body:
        # LlamaExtract native webhook format
        event_type = body.get("event_type", "")
        job_id = body.get("event_id", "")
        # event_type is "extract.success" or "extract.error"
        if "success" in event_type:
            status = "SUCCESS"
        elif "error" in event_type:
            status = "ERROR"
        else:
            status = event_type
    else:
        # Manual/agent callback format
        job_id = body.get("id", "")
        status = body.get("status", "")

    logger.info("extract_callback: nodeId=%s jobId=%s status=%s keys=%s", node_id, job_id, status, list(body.keys()))

    # On extraction failure — mark node and return
    if status not in ("SUCCESS", "PARTIAL_SUCCESS"):
        try:
            update_file_node(node_id, {"status": "failed"})
        except Exception as e:
            logger.error("extract_callback: failed to mark node as failed: %s", e)
        return json.dumps({"received": True, "nodeId": node_id, "result": "failed"}), 200, {"Content-Type": "application/json"}

    # Get extraction data — LlamaExtract webhook puts it in "data", manual calls use "result"
    raw_data = body.get("data") or body.get("result") or {}
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except Exception:
            raw_data = {}

    # LlamaExtract webhook may nest the extraction result in data.data
    if isinstance(raw_data, dict) and "data" in raw_data:
        data = raw_data["data"]
    else:
        data = raw_data

    # --- Fetch file node to get directory context ---
    try:
        file_node = fetch_file_node(node_id)
    except Exception as e:
        logger.error("extract_callback: failed to fetch file node %s: %s", node_id, e)
        return json.dumps({"error": "Failed to fetch file node"}), 500, {"Content-Type": "application/json"}

    directory = (file_node.get("directoryName") or "").lower().strip()
    directory_id = file_node.get("directoryId", "")

    # --- Extract fields from payload ---
    detected_year = extract_year(data)
    extracted_pa = extract_practice_area(data, directory)
    extracted_region, region_context = extract_region(data, directory)
    firm_name, dept_name = extract_firm_department(data, directory)

    logger.info(
        "extract_callback: dir=%s year=%s pa=%s region=%s",
        directory, detected_year, extracted_pa, extracted_region,
    )

    # --- Fuzzy match practice area ---
    pa_match = {}
    if extracted_pa:
        try:
            pa_nodes = fetch_practice_areas(directory_id)
            master = [{"name": p["name"], "id": p["id"], "hints": p.get("hints") or ""} for p in pa_nodes]
            pa_match = fuzzy_match(extracted_pa, master)
            logger.info("extract_callback: pa_match=%s", pa_match.get("matched_name"))
        except Exception as e:
            logger.warning("extract_callback: pa fuzzy match failed: %s", e)

    # --- Graph walk: LegalField + Category ---
    lf_names, lf_ids = [], []
    category, category_id = None, None
    if pa_match.get("matched_id"):
        try:
            lf_names, lf_ids = fetch_legal_fields(pa_match["matched_id"])
        except Exception as e:
            logger.warning("extract_callback: legal field fetch failed: %s", e)
        try:
            category, category_id = fetch_category(pa_match["matched_id"])
        except Exception as e:
            logger.warning("extract_callback: category fetch failed: %s", e)

    # --- Fuzzy match region ---
    region_match = {}
    if extracted_region:
        try:
            region_nodes = fetch_regions()
            master = [{"name": r["regionName"], "id": r["id"], "hints": r.get("hints") or ""} for r in region_nodes if r.get("regionName")]
            region_match = fuzzy_match(extracted_region, master, prefer_canonical=True)
            # Build display name with context (e.g. "Southeast, Minas Gerais")
            if region_context and region_match.get("matched_name"):
                ctx_match = fuzzy_match(region_context, master, prefer_canonical=True)
                ctx_name = ctx_match.get("matched_name", "")
                if ctx_name and ctx_name != region_match["matched_name"]:
                    region_match["display_name"] = f"{ctx_name}, {region_match['matched_name']}"
                else:
                    region_match["display_name"] = region_match["matched_name"]
            elif region_match.get("matched_name"):
                region_match["display_name"] = region_match["matched_name"]
            logger.info("extract_callback: region_match=%s", region_match.get("matched_name"))
        except Exception as e:
            logger.warning("extract_callback: region fuzzy match failed: %s", e)

    # --- Build update payload ---
    updates = {
        "payload": json.dumps(data),
        "status": "extracted",
    }
    if detected_year:
        updates["detectedYear"] = detected_year
        updates["year"] = detected_year

    if pa_match.get("matched_name"):
        updates["practiceAreaName"] = pa_match["matched_name"]
        updates["practiceAreaId"] = pa_match["matched_id"]
    if lf_names:
        updates["legalFieldName"] = lf_names
        updates["legalFieldId"] = lf_ids
    if category:
        updates["category"] = category
        updates["categoryId"] = category_id
    if region_match.get("matched_name"):
        updates["regionName"] = region_match.get("display_name", region_match["matched_name"])
        updates["regionId"] = region_match["matched_id"]
    if firm_name:
        updates["fileFirm"] = firm_name
    if dept_name:
        updates["fileDepartment"] = dept_name

    # --- Persist to graph (triggers importStatus = "extracted" via middleware) ---
    try:
        update_file_node(node_id, updates)
    except Exception as e:
        logger.error("extract_callback: graph update failed for %s: %s", node_id, e)
        return json.dumps({"error": "Graph update failed", "detail": str(e)}), 500, {"Content-Type": "application/json"}

    return json.dumps({"received": True, "nodeId": node_id, "result": "success"}), 200, {"Content-Type": "application/json"}
