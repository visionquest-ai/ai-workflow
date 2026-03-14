"""
Reusable Fuzzy Matching Module (Story 2.1).

3-tier matching algorithm:
- CA-1: Substring matching (exact, word intersection, containment)
- CA-2: Jaro-Winkler similarity (threshold >= 0.85)
- CA-3: Fallback (no match)

Usage:
    from actions.fuzzy_match import fuzzy_match

    result = fuzzy_match("Minas Gerais", master_list)
    # {"matched_name": "Minas Gerais", "matched_id": "region-mg", "score": 1.0, "via_hint": False, "tier": "CA-1"}
"""

import logging

import jellyfish

logger = logging.getLogger(__name__)


def _expand_searchable_list(master_list: list[dict]) -> list[dict]:
    """Expand master entries into searchable terms with via_hint flag.

    Each master entry produces one canonical term plus one term per pipe-delimited hint.
    """
    searchable = []
    for entry in master_list:
        name = entry["name"]
        entry_id = entry["id"]

        searchable.append({
            "term": name,
            "id": entry_id,
            "original_name": name,
            "via_hint": False,
        })

        hints_str = entry.get("hints", "")
        if hints_str:
            for hint in hints_str.split("|"):
                hint = hint.strip()
                if hint:
                    searchable.append({
                        "term": hint,
                        "id": entry_id,
                        "original_name": name,
                        "via_hint": True,
                    })

    return searchable


def _ca1_substring_match(input_str: str, searchable: list[dict]) -> dict | None:
    """CA-1: 3-step substring matching (highest confidence).

    Step 1: Exact case-insensitive match → score 1.0
    Step 2: Word intersection scoring → score by intersection size then char length
    Step 3: Substring containment → min 3 chars for canonical names, no min for hints (FR18)

    Returns best match dict or None if no match found.
    """
    input_lower = input_str.lower()
    input_words = set(input_lower.split())

    # Step 1: Exact case-insensitive match
    exact_matches = []
    for item in searchable:
        if input_lower == item["term"].lower():
            exact_matches.append(item)

    if exact_matches:
        # FR19: hint-based matches take priority
        hint_matches = [m for m in exact_matches if m["via_hint"]]
        best = hint_matches[0] if hint_matches else exact_matches[0]
        return {
            "matched_name": best["original_name"],
            "matched_id": best["id"],
            "score": 1.0,
            "via_hint": best["via_hint"],
            "tier": "CA-1",
        }

    # Step 2: Word intersection scoring
    intersection_candidates = []
    for item in searchable:
        term_words = set(item["term"].lower().split())
        common = input_words & term_words
        if common:
            word_count = len(common)
            char_length = sum(len(w) for w in common)
            intersection_candidates.append((word_count, char_length, item))

    if intersection_candidates:
        # Sort by word count (desc), then char length (desc)
        intersection_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)

        best_word_count = intersection_candidates[0][0]
        best_char_length = intersection_candidates[0][1]

        # Get all tied candidates
        tied = [c for c in intersection_candidates
                if c[0] == best_word_count and c[1] == best_char_length]

        # FR19: hint priority among tied candidates
        hint_tied = [c for c in tied if c[2]["via_hint"]]
        best_item = hint_tied[0][2] if hint_tied else tied[0][2]

        total_words = max(len(input_words), len(set(best_item["term"].lower().split())))
        score = best_word_count / total_words if total_words > 0 else 0.0

        return {
            "matched_name": best_item["original_name"],
            "matched_id": best_item["id"],
            "score": round(score, 4),
            "via_hint": best_item["via_hint"],
            "tier": "CA-1",
        }

    # Step 3: Substring containment
    containment_candidates = []
    for item in searchable:
        term_lower = item["term"].lower()

        # Min 3 chars for canonical names, no minimum for hints (FR18)
        if not item["via_hint"] and min(len(term_lower), len(input_lower)) < 3:
            continue

        if input_lower in term_lower or term_lower in input_lower:
            # Score by length of the shorter string relative to the longer
            shorter = min(len(input_lower), len(term_lower))
            longer = max(len(input_lower), len(term_lower))
            score = shorter / longer if longer > 0 else 0.0
            containment_candidates.append((score, item))

    if containment_candidates:
        containment_candidates.sort(key=lambda x: x[0], reverse=True)
        best_score = containment_candidates[0][0]

        tied = [c for c in containment_candidates if c[0] == best_score]
        hint_tied = [c for c in tied if c[1]["via_hint"]]
        best_item = hint_tied[0][1] if hint_tied else tied[0][1]

        return {
            "matched_name": best_item["original_name"],
            "matched_id": best_item["id"],
            "score": round(best_score, 4),
            "via_hint": best_item["via_hint"],
            "tier": "CA-1",
        }

    return None


def _ca2_jaro_winkler(input_str: str, searchable: list[dict]) -> dict | None:
    """CA-2: Jaro-Winkler similarity matching (fallback).

    Threshold >= 0.85 required for valid match.
    Scores >= 0.7 logged at DEBUG level.
    Scores >= 0.5 tracked.
    """
    input_lower = input_str.lower()
    best_score = 0.0
    best_item = None
    tracked_scores = []

    for item in searchable:
        term_lower = item["term"].lower()
        score = jellyfish.jaro_winkler_similarity(input_lower, term_lower)

        if score >= 0.5:
            tracked_scores.append((score, item["term"]))

        if 0.7 <= score < 0.85:
            logger.debug(
                "JW near-miss: '%s' vs '%s' = %.4f", input_str, item["term"], score
            )

        if score > best_score:
            best_score = score
            best_item = item
        elif score == best_score and best_item is not None:
            # FR19: hint priority at equal score
            if item["via_hint"] and not best_item["via_hint"]:
                best_item = item

    if tracked_scores:
        logger.debug("JW tracked scores (>=0.5): %s", tracked_scores)

    if best_score >= 0.85 and best_item is not None:
        return {
            "matched_name": best_item["original_name"],
            "matched_id": best_item["id"],
            "score": round(best_score, 4),
            "via_hint": best_item["via_hint"],
            "tier": "CA-2",
        }

    return None


def _ca3_fallback() -> dict:
    """CA-3: No-match fallback. Returns empty result."""
    return {
        "matched_name": "",
        "matched_id": "",
        "score": 0.0,
        "via_hint": False,
        "tier": "CA-3",
    }


def fuzzy_match(input_str: str, master_list: list[dict]) -> dict:
    """Match input_str against master_list using 3-tier algorithm.

    Args:
        input_str: The string to match (e.g., extracted practiceArea or region text)
        master_list: List of dicts with keys:
            - name (str): Canonical name
            - id (str): Unique identifier
            - hints (str, optional): Pipe-delimited alternative names/abbreviations

    Returns:
        dict with keys:
            - matched_name (str): Canonical name from master list, or ""
            - matched_id (str): ID from master list, or ""
            - score (float): Match confidence (1.0 for exact, 0.85-1.0 for JW, 0.0 for no match)
            - via_hint (bool): Whether the match came from a hint rather than canonical name
            - tier (str): "CA-1", "CA-2", or "CA-3"
    """
    if not input_str or not input_str.strip() or not master_list:
        return _ca3_fallback()

    searchable = _expand_searchable_list(master_list)

    # CA-1: Substring matching (highest confidence)
    result = _ca1_substring_match(input_str, searchable)
    if result is not None:
        logger.info("CA-1 match: '%s' → '%s' (score=%.4f, via_hint=%s)",
                     input_str, result["matched_name"], result["score"], result["via_hint"])
        return result

    # CA-2: Jaro-Winkler (fallback)
    result = _ca2_jaro_winkler(input_str, searchable)
    if result is not None:
        logger.info("CA-2 match: '%s' → '%s' (score=%.4f, via_hint=%s)",
                     input_str, result["matched_name"], result["score"], result["via_hint"])
        return result

    # CA-3: No match
    logger.info("CA-3 no match: '%s'", input_str)
    return _ca3_fallback()
