"""
Tests for populate_region_hints.py (Story 3.3).

Tests cover:
- Data file validity and completeness (AC1-AC9)
- Hint format correctness (AC9)
- Idempotency of population logic (AC10)
- Script argument handling
- populate_hints logic with mocked GraphQL
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add scripts directory to path for imports
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from populate_region_hints import (
    load_hints_file,
    populate_hints,
)

DATA_DIR = Path(__file__).parent.parent / "data"
REGION_HINTS_PATH = DATA_DIR / "region_hints.json"
COUNTRY_HINTS_PATH = DATA_DIR / "country_hints.json"


# =============================================================================
# DATA FILE TESTS
# =============================================================================


class TestRegionHintsData:
    """Validate region_hints.json structure and content."""

    @pytest.fixture
    def region_hints(self) -> dict[str, str]:
        with open(REGION_HINTS_PATH) as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if not k.startswith("_")}

    def test_file_is_valid_json(self):
        """Region hints file loads as valid JSON."""
        with open(REGION_HINTS_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_all_values_are_pipe_delimited_strings(self, region_hints):
        """All hint values are non-empty strings with pipe delimiters."""
        for name, hints in region_hints.items():
            assert isinstance(hints, str), f"{name} hints is not a string"
            assert len(hints) > 0, f"{name} has empty hints"

    def test_hints_are_trimmed(self, region_hints):
        """AC9: Each alias is non-empty and trimmed after pipe-split."""
        for name, hints in region_hints.items():
            for alias in hints.split("|"):
                assert alias == alias.strip(), f"{name}: alias '{alias}' has whitespace"
                assert len(alias) > 0, f"{name}: empty alias after split"

    def test_sao_paulo_has_uf(self, region_hints):
        """AC1: São Paulo hints include UF abbreviation."""
        hints = region_hints["São Paulo"]
        aliases = hints.split("|")
        assert "SP" in aliases, "São Paulo missing UF 'SP'"

    def test_sao_paulo_accent_free(self, region_hints):
        """AC1: São Paulo hints include accent-free variant."""
        hints = region_hints["São Paulo"]
        aliases = hints.split("|")
        assert "Sao Paulo" in aliases

    def test_sao_paulo_leaders_league(self, region_hints):
        """AC8: São Paulo hints include 'State of' format."""
        hints = region_hints["São Paulo"]
        assert "State of São Paulo" in hints
        assert "State of Sao Paulo" in hints

    def test_minas_gerais_hints(self, region_hints):
        """AC1/AC3: Minas Gerais has UF + city mapping."""
        hints = region_hints["Minas Gerais"]
        aliases = hints.split("|")
        assert "MG" in aliases
        assert "Belo Horizonte" in aliases
        assert "BH" in aliases

    def test_brasilia_distrito_federal_crosslink(self, region_hints):
        """AC4: Brasília and Distrito Federal are cross-linked."""
        brasilia_hints = region_hints["Brasília"].split("|")
        df_hints = region_hints["Distrito Federal"].split("|")

        assert "DF" in brasilia_hints
        assert "Distrito Federal" in brasilia_hints
        assert "Brasilia" in df_hints or "Brasília" in df_hints

    def test_brazil_national_hints(self, region_hints):
        """AC5: Brazil has national-level hints."""
        hints = region_hints["Brazil"]
        aliases = hints.split("|")
        assert "Brasil" in aliases
        assert "BR" in aliases
        assert "Nacional" in aliases
        assert "National" in aliases

    def test_brazil_itr_format(self, region_hints):
        """AC7: Brazil hints include ITR format."""
        hints = region_hints["Brazil"]
        assert "Brazil - Regional" in hints

    def test_northeast_regional_grouping(self, region_hints):
        """AC2: North East hints include member states (UF codes on state nodes only to avoid ambiguity)."""
        hints = region_hints["North East"]
        aliases = hints.split("|")
        assert "Nordeste" in aliases
        assert "Northeast" in aliases
        assert "Pernambuco" in aliases
        assert "Bahia" in aliases
        assert "Ceará" in aliases or "Ceara" in aliases

    def test_belo_horizonte_city(self, region_hints):
        """AC3: City node has parent state UF and directory prefix."""
        hints = region_hints["Belo Horizonte"]
        aliases = hints.split("|")
        assert "MG" in aliases
        assert "BH" in aliases
        assert "City focus - Belo Horizonte" in aliases


class TestCountryHintsData:
    """Validate country_hints.json structure and content."""

    @pytest.fixture
    def country_hints(self) -> dict[str, str]:
        with open(COUNTRY_HINTS_PATH) as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if not k.startswith("_")}

    def test_file_is_valid_json(self):
        """Country hints file loads as valid JSON."""
        with open(COUNTRY_HINTS_PATH) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_united_states_hints(self, country_hints):
        """AC6: United States has ISO code and Portuguese name."""
        hints = country_hints["United States"]
        aliases = hints.split("|")
        assert "US" in aliases
        assert "Estados Unidos" in aliases
        assert "EUA" in aliases

    def test_all_countries_have_iso_code(self, country_hints):
        """AC6: Every country has a 2-letter ISO code."""
        for name, hints in country_hints.items():
            aliases = hints.split("|")
            has_iso = any(len(a) == 2 and a.isupper() for a in aliases)
            assert has_iso, f"{name} missing 2-letter ISO code"

    def test_hints_are_trimmed(self, country_hints):
        """AC9: Each alias is non-empty and trimmed."""
        for name, hints in country_hints.items():
            for alias in hints.split("|"):
                assert alias == alias.strip(), f"{name}: alias '{alias}' not trimmed"
                assert len(alias) > 0, f"{name}: empty alias"


# =============================================================================
# SCRIPT LOGIC TESTS (mocked GraphQL)
# =============================================================================


class TestPopulateHints:
    """Test populate_hints function with mocked GraphQL."""

    def _make_nodes(self, names: list[str]) -> list[dict]:
        return [{"id": f"id-{i}", "name": n, "hints": None} for i, n in enumerate(names)]

    def test_updates_matching_nodes(self):
        """Matching nodes get their update_fn called."""
        hints_map = {"São Paulo": "SP|Sao Paulo", "Rio de Janeiro": "RJ"}
        nodes = self._make_nodes(["São Paulo", "Rio de Janeiro", "Unknown"])
        update_fn = MagicMock()
        config = {"graphql_url": "http://test", "graphql_api_key": "key"}

        ok, fail, warnings = populate_hints(
            "Region", hints_map, nodes, update_fn, config, dry_run=False,
        )

        assert ok == 2
        assert fail == 0
        assert update_fn.call_count == 2

    def test_dry_run_no_mutations(self):
        """Dry run does not call update_fn."""
        hints_map = {"São Paulo": "SP|Sao Paulo"}
        nodes = self._make_nodes(["São Paulo"])
        update_fn = MagicMock()
        config = {"graphql_url": "http://test", "graphql_api_key": "key"}

        ok, fail, warnings = populate_hints(
            "Region", hints_map, nodes, update_fn, config, dry_run=True,
        )

        assert ok == 1
        assert fail == 0
        assert update_fn.call_count == 0

    def test_missing_node_warns(self):
        """Nodes in hints_map but not in graph produce warnings."""
        hints_map = {"Missing Region": "alias1|alias2"}
        nodes = self._make_nodes(["Other Region"])
        update_fn = MagicMock()
        config = {"graphql_url": "http://test", "graphql_api_key": "key"}

        ok, fail, warnings = populate_hints(
            "Region", hints_map, nodes, update_fn, config, dry_run=False,
        )

        assert ok == 0
        assert any("Missing Region" in w for w in warnings)

    def test_unconfigured_node_warns(self):
        """Graph nodes without hints configured produce warnings."""
        hints_map = {"São Paulo": "SP"}
        nodes = self._make_nodes(["São Paulo", "Unconfigured Region"])
        update_fn = MagicMock()
        config = {"graphql_url": "http://test", "graphql_api_key": "key"}

        ok, fail, warnings = populate_hints(
            "Region", hints_map, nodes, update_fn, config, dry_run=False,
        )

        assert any("Unconfigured Region" in w for w in warnings)

    def test_filter_name(self):
        """--name filter only updates the specified node."""
        hints_map = {"São Paulo": "SP", "Rio de Janeiro": "RJ"}
        nodes = self._make_nodes(["São Paulo", "Rio de Janeiro"])
        update_fn = MagicMock()
        config = {"graphql_url": "http://test", "graphql_api_key": "key"}

        ok, fail, warnings = populate_hints(
            "Region", hints_map, nodes, update_fn, config,
            dry_run=False, filter_name="São Paulo",
        )

        assert ok == 1
        assert update_fn.call_count == 1

    def test_idempotent_rerun(self):
        """AC10: Re-running with same hints produces same result."""
        hints_map = {"São Paulo": "SP|Sao Paulo"}
        nodes = [{"id": "id-0", "name": "São Paulo", "hints": "SP|Sao Paulo"}]
        update_fn = MagicMock()
        config = {"graphql_url": "http://test", "graphql_api_key": "key"}

        # First run
        ok1, _, _ = populate_hints(
            "Region", hints_map, nodes, update_fn, config, dry_run=False,
        )
        # Second run (same data)
        ok2, _, _ = populate_hints(
            "Region", hints_map, nodes, update_fn, config, dry_run=False,
        )

        assert ok1 == ok2 == 1
        # Both runs call update_fn (idempotent overwrite)
        assert update_fn.call_count == 2

    def test_update_failure_counted(self):
        """Failed updates are counted."""
        hints_map = {"São Paulo": "SP"}
        nodes = self._make_nodes(["São Paulo"])
        update_fn = MagicMock(side_effect=RuntimeError("GraphQL error"))
        config = {"graphql_url": "http://test", "graphql_api_key": "key"}

        ok, fail, warnings = populate_hints(
            "Region", hints_map, nodes, update_fn, config, dry_run=False,
        )

        assert ok == 0
        assert fail == 1

    def test_case_insensitive_matching(self):
        """Node names are matched case-insensitively."""
        hints_map = {"são paulo": "SP"}
        nodes = [{"id": "id-0", "name": "São Paulo", "hints": None}]
        update_fn = MagicMock()
        config = {"graphql_url": "http://test", "graphql_api_key": "key"}

        ok, fail, warnings = populate_hints(
            "Region", hints_map, nodes, update_fn, config, dry_run=False,
        )

        assert ok == 1


class TestLoadHintsFile:
    """Test load_hints_file helper."""

    def test_loads_valid_file(self, tmp_path):
        """Loads and filters _comment keys."""
        f = tmp_path / "test.json"
        f.write_text(json.dumps({"_comment": "ignore", "Region A": "hint1|hint2"}))

        result = load_hints_file(f)

        assert "_comment" not in result
        assert result["Region A"] == "hint1|hint2"

    def test_exits_on_missing_file(self, tmp_path):
        """Exits with error on missing file."""
        with pytest.raises(SystemExit):
            load_hints_file(tmp_path / "nonexistent.json")


# =============================================================================
# FUZZY MATCH INTEGRATION
# =============================================================================


class TestFuzzyMatchIntegration:
    """Verify hints work correctly with the fuzzy_match module."""

    @pytest.fixture
    def region_hints(self) -> dict[str, str]:
        with open(REGION_HINTS_PATH) as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if not k.startswith("_")}

    def _build_master_list(self, hints_map: dict[str, str]) -> list[dict]:
        return [
            {"name": name, "id": f"region-{i}", "hints": hints}
            for i, (name, hints) in enumerate(hints_map.items())
        ]

    def test_uf_matches_state(self, region_hints):
        """Fuzzy match 'MG' resolves to Minas Gerais via hint."""
        from actions.fuzzy_match import fuzzy_match

        master = self._build_master_list(region_hints)
        result = fuzzy_match("MG", master)

        assert result["matched_name"] == "Minas Gerais"
        assert result["via_hint"] is True
        assert result["tier"] == "CA-1"

    def test_brazil_regional_matches(self, region_hints):
        """Fuzzy match 'Brazil - Regional' resolves to Brazil via hint."""
        from actions.fuzzy_match import fuzzy_match

        master = self._build_master_list(region_hints)
        result = fuzzy_match("Brazil - Regional", master)

        assert result["matched_name"] == "Brazil"
        assert result["via_hint"] is True

    def test_accent_free_matches(self, region_hints):
        """Fuzzy match 'Brasilia' resolves to Brasília or Distrito Federal."""
        from actions.fuzzy_match import fuzzy_match

        master = self._build_master_list(region_hints)
        result = fuzzy_match("Brasilia", master)

        assert result["matched_name"] in ("Brasília", "Distrito Federal")
        assert result["score"] > 0

    def test_state_of_format_matches(self, region_hints):
        """Fuzzy match 'State of São Paulo' resolves to São Paulo."""
        from actions.fuzzy_match import fuzzy_match

        master = self._build_master_list(region_hints)
        result = fuzzy_match("State of São Paulo", master)

        assert result["matched_name"] == "São Paulo"
        assert result["via_hint"] is True

    def test_northeast_variant_matches(self, region_hints):
        """Fuzzy match 'Northeast' resolves to North East via hint."""
        from actions.fuzzy_match import fuzzy_match

        master = self._build_master_list(region_hints)
        result = fuzzy_match("Northeast", master)

        assert result["matched_name"] == "North East"
        assert result["via_hint"] is True

    def test_us_country_matches(self):
        """Fuzzy match 'US' resolves to United States."""
        from actions.fuzzy_match import fuzzy_match

        with open(COUNTRY_HINTS_PATH) as f:
            data = json.load(f)
        country_hints = {k: v for k, v in data.items() if not k.startswith("_")}

        master = [
            {"name": name, "id": f"country-{i}", "hints": hints}
            for i, (name, hints) in enumerate(country_hints.items())
        ]
        result = fuzzy_match("US", master)

        assert result["matched_name"] == "United States"
        assert result["via_hint"] is True
