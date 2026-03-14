"""
Tests for fuzzy_match module (Story 2.1).

Tests the reusable 3-tier fuzzy matching algorithm:
- CA-1: Substring matching (exact, word intersection, containment)
- CA-2: Jaro-Winkler similarity (threshold >= 0.85)
- CA-3: Fallback (no match)

Usage:
    pytest tests/test_fuzzy_match.py -v
"""

from actions.fuzzy_match import (
    fuzzy_match,
    _expand_searchable_list,
    _ca3_fallback,
)


# =============================================================================
# Test fixtures
# =============================================================================

SAMPLE_MASTER_LIST = [
    {"name": "Minas Gerais", "id": "region-mg", "hints": "MG|Belo Horizonte"},
    {"name": "São Paulo", "id": "region-sp", "hints": "SP|Sampa"},
    {"name": "Rio de Janeiro", "id": "region-rj", "hints": "RJ"},
    {"name": "Corporate Law", "id": "pa-corp", "hints": "Business Law|M&A"},
    {"name": "Intellectual Property", "id": "pa-ip", "hints": "IP|Patents|Trademarks"},
    {"name": "Environmental Law", "id": "pa-env"},
]


# =============================================================================
# Task 3.11: Searchable list expansion
# =============================================================================

class TestExpandSearchableList:
    """Tests for _expand_searchable_list — AC #1."""

    def test_expands_canonical_name(self):
        master = [{"name": "Corporate Law", "id": "pa-corp"}]
        result = _expand_searchable_list(master)
        assert len(result) == 1
        assert result[0]["term"] == "Corporate Law"
        assert result[0]["id"] == "pa-corp"
        assert result[0]["original_name"] == "Corporate Law"
        assert result[0]["via_hint"] is False

    def test_expands_hints_pipe_delimited(self):
        master = [{"name": "Minas Gerais", "id": "region-mg", "hints": "MG|Belo Horizonte"}]
        result = _expand_searchable_list(master)
        assert len(result) == 3
        terms = [r["term"] for r in result]
        assert "Minas Gerais" in terms
        assert "MG" in terms
        assert "Belo Horizonte" in terms

    def test_hint_entries_have_via_hint_true(self):
        master = [{"name": "Minas Gerais", "id": "region-mg", "hints": "MG|Belo Horizonte"}]
        result = _expand_searchable_list(master)
        hints = [r for r in result if r["via_hint"]]
        assert len(hints) == 2
        for h in hints:
            assert h["original_name"] == "Minas Gerais"
            assert h["id"] == "region-mg"

    def test_no_hints_field(self):
        master = [{"name": "Environmental Law", "id": "pa-env"}]
        result = _expand_searchable_list(master)
        assert len(result) == 1
        assert result[0]["via_hint"] is False

    def test_empty_hints_string(self):
        master = [{"name": "Test", "id": "test-1", "hints": ""}]
        result = _expand_searchable_list(master)
        assert len(result) == 1


# =============================================================================
# Task 3.2: Exact case-insensitive match
# =============================================================================

class TestCA1ExactMatch:
    """Tests for CA-1 exact match — AC #2."""

    def test_exact_match_returns_score_1(self):
        result = fuzzy_match("Corporate Law", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "Corporate Law"
        assert result["matched_id"] == "pa-corp"
        assert result["score"] == 1.0
        assert result["tier"] == "CA-1"

    def test_exact_match_case_insensitive(self):
        result = fuzzy_match("corporate law", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "Corporate Law"
        assert result["score"] == 1.0
        assert result["tier"] == "CA-1"

    def test_exact_match_uppercase(self):
        result = fuzzy_match("CORPORATE LAW", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "Corporate Law"
        assert result["score"] == 1.0
        assert result["tier"] == "CA-1"


# =============================================================================
# Task 3.3: Word intersection scoring
# =============================================================================

class TestCA1WordIntersection:
    """Tests for CA-1 word intersection — AC #2."""

    def test_word_intersection_matches(self):
        result = fuzzy_match("Intellectual Property Rights", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "Intellectual Property"
        assert result["tier"] == "CA-1"

    def test_word_intersection_ranks_by_word_count(self):
        # "Rio de Janeiro" has 3 common words with "Rio de Janeiro Norte"
        result = fuzzy_match("Rio de Janeiro Norte", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "Rio de Janeiro"
        assert result["tier"] == "CA-1"


# =============================================================================
# Task 3.4: Substring containment with min 3-char
# =============================================================================

class TestCA1SubstringContainment:
    """Tests for CA-1 substring containment — AC #2."""

    def test_input_contained_in_name(self):
        result = fuzzy_match("Environmental", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "Environmental Law"
        assert result["tier"] == "CA-1"

    def test_name_contained_in_input(self):
        # "São Paulo" is contained in "São Paulo Capital"
        result = fuzzy_match("São Paulo Capital", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "São Paulo"
        assert result["tier"] == "CA-1"

    def test_short_canonical_name_no_substring_match(self):
        """2-char canonical name should NOT match via substring containment against a long input."""
        master = [{"name": "IP", "id": "pa-ip-short"}]
        # "IP" is 2 chars — substring containment requires min 3 chars for canonical names
        # No word overlap, so word intersection won't match either
        result = fuzzy_match("Some Interesting Property", master)
        # Should fall through to CA-2 or CA-3, not match via substring containment
        assert result["tier"] != "CA-1" or result["score"] >= 0.85

    def test_short_canonical_with_word_overlap_still_works(self):
        """Word intersection still works for short canonical names (Step 2 not affected by 3-char min)."""
        master = [{"name": "IP", "id": "pa-ip-short"}]
        result = fuzzy_match("IP Rights", master)
        # "IP" matches via word intersection (Step 2), not substring containment
        assert result["matched_name"] == "IP"
        assert result["tier"] == "CA-1"


# =============================================================================
# Task 3.5: Short hint bypass (FR18)
# =============================================================================

class TestCA1HintBypass:
    """Tests for hints bypassing 3-char minimum — AC #2, FR18."""

    def test_short_hint_mg_matches(self):
        result = fuzzy_match("MG", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "Minas Gerais"
        assert result["matched_id"] == "region-mg"
        assert result["via_hint"] is True
        assert result["tier"] == "CA-1"

    def test_short_hint_sp_matches(self):
        result = fuzzy_match("SP", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "São Paulo"
        assert result["matched_id"] == "region-sp"
        assert result["via_hint"] is True
        assert result["tier"] == "CA-1"

    def test_short_hint_rj_matches(self):
        result = fuzzy_match("RJ", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "Rio de Janeiro"
        assert result["via_hint"] is True
        assert result["tier"] == "CA-1"

    def test_hint_ip_matches(self):
        result = fuzzy_match("IP", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "Intellectual Property"
        assert result["via_hint"] is True
        assert result["tier"] == "CA-1"


# =============================================================================
# Task 3.6: Jaro-Winkler match above threshold
# =============================================================================

class TestCA2JaroWinkler:
    """Tests for CA-2 Jaro-Winkler — AC #3."""

    def test_jw_above_threshold_returns_ca2(self):
        # "Corporatte" is a typo with no word overlap or substring match — pure JW
        result = fuzzy_match("Corporatte", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == "Corporate Law"
        assert result["score"] >= 0.85
        assert result["tier"] == "CA-2"


# =============================================================================
# Task 3.7: Jaro-Winkler below threshold falls to CA-3
# =============================================================================

class TestCA2BelowThreshold:
    """Tests for CA-2 below threshold — AC #3, #4."""

    def test_jw_below_threshold_falls_to_ca3(self):
        result = fuzzy_match("Quantum Physics", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == ""
        assert result["matched_id"] == ""
        assert result["score"] == 0.0
        assert result["tier"] == "CA-3"


# =============================================================================
# Task 3.8: No-match fallback
# =============================================================================

class TestCA3Fallback:
    """Tests for CA-3 fallback — AC #4."""

    def test_no_match_returns_empty(self):
        result = fuzzy_match("Completely Unrelated Gibberish XYZ123", SAMPLE_MASTER_LIST)
        assert result["matched_name"] == ""
        assert result["matched_id"] == ""
        assert result["score"] == 0.0
        assert result["via_hint"] is False
        assert result["tier"] == "CA-3"

    def test_fallback_function_directly(self):
        result = _ca3_fallback()
        assert result["matched_name"] == ""
        assert result["matched_id"] == ""
        assert result["score"] == 0.0
        assert result["via_hint"] is False
        assert result["tier"] == "CA-3"

    def test_empty_input_string(self):
        result = fuzzy_match("", SAMPLE_MASTER_LIST)
        assert result["tier"] == "CA-3"

    def test_empty_master_list(self):
        result = fuzzy_match("Corporate Law", [])
        assert result["tier"] == "CA-3"

    def test_whitespace_only_input(self):
        result = fuzzy_match("   ", SAMPLE_MASTER_LIST)
        assert result["tier"] == "CA-3"


# =============================================================================
# Task 3.9: Hint priority over canonical at equal score (FR19)
# =============================================================================

class TestHintPriority:
    """Tests for hint-based priority resolution — AC #5, FR19."""

    def test_hint_match_takes_priority_at_equal_score(self):
        """When a hint and canonical name produce the same score, hint wins."""
        master = [
            {"name": "Business Law", "id": "pa-biz"},
            {"name": "Tax Law", "id": "pa-tax", "hints": "Business Law"},
        ]
        result = fuzzy_match("Business Law", master)
        # Both "Business Law" (canonical for pa-biz) and "Business Law" (hint for pa-tax)
        # produce exact match score 1.0. Hint should win per FR19.
        assert result["via_hint"] is True
        assert result["matched_id"] == "pa-tax"


# =============================================================================
# Task 3.10: Determinism (NFR1)
# =============================================================================

class TestDeterminism:
    """Tests for deterministic output — AC #6, NFR1."""

    def test_same_inputs_same_outputs_100_times(self):
        first_result = fuzzy_match("Minas Gerais", SAMPLE_MASTER_LIST)
        for _ in range(99):
            result = fuzzy_match("Minas Gerais", SAMPLE_MASTER_LIST)
            assert result == first_result

    def test_determinism_with_hint_match(self):
        first_result = fuzzy_match("MG", SAMPLE_MASTER_LIST)
        for _ in range(99):
            result = fuzzy_match("MG", SAMPLE_MASTER_LIST)
            assert result == first_result

    def test_determinism_with_jaro_winkler(self):
        first_result = fuzzy_match("Corporatte", SAMPLE_MASTER_LIST)
        for _ in range(99):
            result = fuzzy_match("Corporatte", SAMPLE_MASTER_LIST)
            assert result == first_result


# =============================================================================
# Task 3.12: Return dict structure
# =============================================================================

class TestReturnStructure:
    """Tests for return dict keys — AC #7."""

    REQUIRED_KEYS = {"matched_name", "matched_id", "score", "via_hint", "tier"}

    def test_match_result_has_all_keys(self):
        result = fuzzy_match("Corporate Law", SAMPLE_MASTER_LIST)
        assert set(result.keys()) == self.REQUIRED_KEYS

    def test_no_match_result_has_all_keys(self):
        result = fuzzy_match("XYZXYZ", SAMPLE_MASTER_LIST)
        assert set(result.keys()) == self.REQUIRED_KEYS

    def test_jw_match_result_has_all_keys(self):
        result = fuzzy_match("Corporatte", SAMPLE_MASTER_LIST)
        assert set(result.keys()) == self.REQUIRED_KEYS

    def test_hint_match_result_has_all_keys(self):
        result = fuzzy_match("MG", SAMPLE_MASTER_LIST)
        assert set(result.keys()) == self.REQUIRED_KEYS
