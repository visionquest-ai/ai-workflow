"""
matter_config.py — Pure Python gate evaluation for directory applicability.

Story RX.60.4

No I/O, no graph calls, no TEA dependency. Importable as a plain function
so YAML agent `run:` blocks can call it directly:
    from matter_config import evaluate_minimum_gate
"""


def evaluate_minimum_gate(criteria: dict, gate_rules: list) -> dict:
    """
    Evaluate whether a matter meets the minimum applicability gate for a directory.

    Args:
        criteria: Dict keyed by criterion code ("C1"-"C15"), each with:
                  { "applicable": bool, "strength": "strong"|"medium"|"weak"|"none",
                    "explicitness": "explicit"|"implicit"|"absent" }
        gate_rules: List of DirectoryGateRule dicts from the graph:
                    { "ruleType": "required"|"min_from_set",
                      "minCount": int, "roleName": str,
                      "gateIncludes": [{"code": str}] }

    Returns:
        {
          "passed": bool,
          "rules_met": [str],        # human-readable rule descriptions
          "rules_missing": [str],
          "weak_inferences": [str],  # codes present but only implicit+weak
          "blocking_gaps": [str]     # codes missing from required rules
        }
    """

    def _is_present(code: str) -> bool:
        """A criterion is 'present' if applicable AND explicitness is explicit or implicit."""
        entry = criteria.get(code, {})
        if not entry.get("applicable", False):
            return False
        return entry.get("explicitness", "absent") in ("explicit", "implicit")

    def _is_weakly_inferred(code: str) -> bool:
        """A criterion is 'weakly inferred' if implicit AND strength is weak."""
        entry = criteria.get(code, {})
        return (
            entry.get("explicitness", "absent") == "implicit"
            and entry.get("strength", "none") == "weak"
        )

    rules_met = []
    rules_missing = []
    weak_inferences = []
    blocking_gaps = []
    all_passed = True

    for rule in gate_rules:
        rule_type = rule.get("ruleType", "")
        role_name = rule.get("roleName", "")
        min_count = rule.get("minCount", 1)
        includes = [item.get("code", "") for item in rule.get("gateIncludes", [])]

        if rule_type == "required":
            # Single criterion must be present
            code = includes[0] if includes else ""
            if _is_present(code):
                rules_met.append(f"{role_name}: {code} must be present")
                if _is_weakly_inferred(code):
                    weak_inferences.append(code)
            else:
                rules_missing.append(f"{role_name}: {code} must be present")
                blocking_gaps.append(code)
                all_passed = False

        elif rule_type == "min_from_set":
            present_codes = [c for c in includes if _is_present(c)]
            missing_codes = [c for c in includes if not _is_present(c)]

            # Collect weak inferences among the present ones
            for code in present_codes:
                if _is_weakly_inferred(code):
                    weak_inferences.append(code)

            set_label = ", ".join(includes)
            if len(present_codes) >= min_count:
                rules_met.append(
                    f"{role_name}: {len(present_codes)}/{min_count} met from [{set_label}]"
                )
            else:
                rules_missing.append(
                    f"{role_name}: need {min_count} from [{set_label}], "
                    f"have {len(present_codes)} — missing: {', '.join(missing_codes)}"
                )
                all_passed = False

    # Deduplicate weak_inferences (a code can appear in multiple rules)
    seen = set()
    unique_weak = []
    for c in weak_inferences:
        if c not in seen:
            seen.add(c)
            unique_weak.append(c)

    return {
        "passed": all_passed,
        "rules_met": rules_met,
        "rules_missing": rules_missing,
        "weak_inferences": unique_weak,
        "blocking_gaps": blocking_gaps,
    }
