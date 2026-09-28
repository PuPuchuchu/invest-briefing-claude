"""
Unit tests for src/fundamentals/metric_applicability.py (Framework v2.1
Step 4, 2026-09-28).

Pure unit tests: no network, no file I/O, synthetic fixtures only -- per
the 2026-09-28 confirmation round, this implementation ships schema +
validation + lookup/resolution logic only, proven against synthetic data.
The real 18 peer_group -> profile_id economic mappings and the real
84-row metric_applicability policy are a separate, later round.

These tests exist to prove the properties confirmed in that round:

    1. PRODUCTION_METRICS (imported from quality.py/growth.py directly)
       is the 84-completeness anchor -- NOT metric_specs.METRIC_SPECS.
    2. resolver_id is a generic code-side key (never a Python function
       name), CONDITIONAL <-> resolver_id is a bidirectional requirement,
       and unknown resolver ids are rejected.
    3. Same-trigger + overlapping effective interval is ALWAYS a
       validation error, never resolved by precedence -- distinct from
       different-trigger simultaneous matches, which ARE resolved by
       precedence (UNVERIFIED > NOT_APPLICABLE > APPLICABLE).
    4. A CONDITIONAL metric_applicability.csv row with no matching
       modifier policy at all is a hard validation error.
    5. effective_to = null is open-ended; effective_from < effective_to is
       only checked when effective_to is present.
    6. Fail-closed throughout: missing profile x metric -> UNVERIFIED,
       unknown profile/metric/resolver -> validation ERROR, malformed
       trigger -> validation ERROR, unknown runtime trigger value ->
       runtime UNVERIFIED.
"""

from __future__ import annotations

import copy

import pytest

from src.fundamentals.growth import GROWTH_METRICS
from src.fundamentals.metric_applicability import (
    APPLICABILITY_STATUSES,
    LOOKUP_INVALID_REFERENCE,
    LOOKUP_MISSING_AVAILABILITY_DATE,
    LOOKUP_NO_MAPPING_FOR_DATE,
    LOOKUP_NOT_YET_AVAILABLE,
    LOOKUP_OK,
    LOOKUP_OVERLAPPING_MAPPING,
    MAPPING_SOURCES,
    MODIFIER_CONFLICT_PRIORITY,
    MODIFIER_RESULT_STATUSES,
    PRODUCTION_METRICS,
    PROFILE_IDS,
    RESOLVER_IDS,
    TRIGGER_FIELD_VALUES,
    TRIGGER_FIELDS,
    evaluate_modifiers,
    get_metric_applicability_status,
    get_metric_profile_for_peer_group_as_of,
    resolve_metric_applicability,
    validate_metric_applicability_data,
    validate_metric_applicability_modifier_row,
    validate_metric_applicability_modifiers_data,
    validate_metric_applicability_row,
    validate_metric_profile_row,
    validate_metric_profiles_data,
    validate_peer_group_metric_profile_data,
    validate_peer_group_metric_profile_row,
    validate_production_metrics_configuration,
)
from src.fundamentals.quality import QUALITY_METRICS


# ============================================================
# CANONICAL UNIVERSE
# ============================================================

def test_production_metrics_is_union_of_quality_and_growth_metrics():
    assert PRODUCTION_METRICS == frozenset(QUALITY_METRICS) | frozenset(GROWTH_METRICS)


def test_production_metrics_has_twelve_members():
    assert len(PRODUCTION_METRICS) == 12


def test_profile_ids_has_seven_members():
    assert len(PROFILE_IDS) == 7


def test_seven_profiles_times_twelve_metrics_is_eighty_four():
    assert len(PROFILE_IDS) * len(PRODUCTION_METRICS) == 84


def test_production_metrics_configuration_is_currently_valid():
    """PRODUCTION_METRICS must be a subset of metric_specs.METRIC_SPECS.keys()
    -- sanity cross-check only, never the completeness driver itself (see
    module docstring)."""
    assert validate_production_metrics_configuration() == []


def test_trigger_fields_come_from_core_universe_io():
    assert TRIGGER_FIELDS == {"business_mix_status", "segment_reporting_available"}
    assert "UNVERIFIED" not in TRIGGER_FIELD_VALUES["business_mix_status"]
    assert "UNVERIFIED" not in TRIGGER_FIELD_VALUES["segment_reporting_available"]
    assert TRIGGER_FIELD_VALUES["business_mix_status"] == {"PURE_PLAY", "DIVERSIFIED"}
    assert TRIGGER_FIELD_VALUES["segment_reporting_available"] == {"TRUE", "FALSE"}


# ============================================================
# metric_profiles.csv
# ============================================================

def _valid_metric_profile_row(**overrides) -> dict:
    row = {
        "profile_id": "GENERAL_CORPORATE",
        "description": "Default profile.",
        "default_applicability": "APPLICABLE",
    }
    row.update(overrides)
    return row


def test_valid_metric_profile_row_passes():
    assert validate_metric_profile_row(_valid_metric_profile_row()) == []


def test_metric_profile_row_rejects_unknown_profile_id():
    row = _valid_metric_profile_row(profile_id="NOT_A_REAL_PROFILE")
    assert "profile_id_unknown" in validate_metric_profile_row(row)


def test_metric_profile_row_rejects_unknown_default_applicability():
    row = _valid_metric_profile_row(default_applicability="MAYBE")
    assert "default_applicability_unknown" in validate_metric_profile_row(row)


def test_metric_profile_row_requires_description():
    row = _valid_metric_profile_row(description=None)
    assert "description" in validate_metric_profile_row(row)


def _all_seven_profile_rows() -> list[dict]:
    return [
        _valid_metric_profile_row(profile_id=profile_id, description=f"{profile_id} profile.")
        for profile_id in sorted(PROFILE_IDS)
    ]


def test_metric_profiles_data_passes_when_all_seven_present_once():
    assert validate_metric_profiles_data(_all_seven_profile_rows()) == []


def test_metric_profiles_data_flags_duplicate_profile_id():
    rows = _all_seven_profile_rows()
    rows.append(_valid_metric_profile_row(profile_id=rows[0]["profile_id"]))
    failures = validate_metric_profiles_data(rows)
    assert any("duplicate_profile_id" in f for f in failures)


def test_metric_profiles_data_flags_missing_profile():
    rows = _all_seven_profile_rows()[:-1]
    failures = validate_metric_profiles_data(rows)
    assert any(f.startswith("missing_profile[") for f in failures)


# ============================================================
# peer_group_metric_profile.csv
# ============================================================

def _valid_peer_group_metric_profile_row(**overrides) -> dict:
    row = {
        "peer_group": "Memory Semiconductors",
        "profile_id": "MEMORY_SEMICONDUCTOR",
        "effective_from": "2026-09-28",
        "effective_to": None,
        "classification_available_date": "2026-09-28",
        "mapping_source": "MANUAL_REVIEW",
        "mapping_version": "v1",
        "reviewed_at": "2026-09-28",
        "mapping_rationale": "Direct 1:1 mapping.",
    }
    row.update(overrides)
    return row


def test_valid_peer_group_metric_profile_row_passes():
    assert validate_peer_group_metric_profile_row(_valid_peer_group_metric_profile_row()) == []


def test_peer_group_metric_profile_row_requires_mapping_rationale():
    row = _valid_peer_group_metric_profile_row(mapping_rationale=None)
    assert "mapping_rationale" in validate_peer_group_metric_profile_row(row)


def test_peer_group_metric_profile_row_rejects_unknown_profile_id():
    row = _valid_peer_group_metric_profile_row(profile_id="NOT_REAL")
    assert "profile_id_unknown" in validate_peer_group_metric_profile_row(row)


def test_peer_group_metric_profile_row_rejects_unknown_mapping_source():
    row = _valid_peer_group_metric_profile_row(mapping_source="RULE_BASED")
    assert "mapping_source_unknown" in validate_peer_group_metric_profile_row(row)


def test_peer_group_metric_profile_row_open_ended_effective_to_is_allowed():
    row = _valid_peer_group_metric_profile_row(effective_to=None)
    assert validate_peer_group_metric_profile_row(row) == []


def test_peer_group_metric_profile_row_equal_effective_from_to_fails():
    """2026-09-28 confirmation: effective_from < effective_to is strict --
    unlike peer_classification.py's existing '<=' allowance -- but ONLY
    checked when effective_to is present."""
    row = _valid_peer_group_metric_profile_row(effective_from="2026-09-28", effective_to="2026-09-28")
    assert "effective_from_not_before_effective_to" in validate_peer_group_metric_profile_row(row)


def test_peer_group_metric_profile_row_from_before_to_passes():
    row = _valid_peer_group_metric_profile_row(effective_from="2026-01-01", effective_to="2026-12-31")
    assert validate_peer_group_metric_profile_row(row) == []


def test_peer_group_metric_profile_row_bad_date_format_fails():
    row = _valid_peer_group_metric_profile_row(effective_from="not-a-date")
    assert "effective_from_format" in validate_peer_group_metric_profile_row(row)


_PEER_GROUPS_FIXTURE = [
    {"cik": "0000001", "peer_group": "Memory Semiconductors"},
    {"cik": "0000002", "peer_group": "Diversified / Money Center Banks"},
]


def test_peer_group_metric_profile_data_passes_for_known_peer_group():
    rows = [_valid_peer_group_metric_profile_row()]
    assert validate_peer_group_metric_profile_data(rows, _PEER_GROUPS_FIXTURE) == []


def test_peer_group_metric_profile_data_flags_unknown_peer_group():
    rows = [_valid_peer_group_metric_profile_row(peer_group="Not A Real Peer Group")]
    failures = validate_peer_group_metric_profile_data(rows, _PEER_GROUPS_FIXTURE)
    assert any("unknown_peer_group" in f for f in failures)


def test_peer_group_metric_profile_data_flags_duplicate_mapping_version():
    rows = [
        _valid_peer_group_metric_profile_row(effective_from="2020-01-01", effective_to="2021-01-01", mapping_version="v1"),
        _valid_peer_group_metric_profile_row(effective_from="2021-01-01", effective_to="2022-01-01", mapping_version="v1"),
    ]
    failures = validate_peer_group_metric_profile_data(rows, _PEER_GROUPS_FIXTURE)
    assert any("duplicate_mapping_version" in f for f in failures)


def test_peer_group_metric_profile_data_flags_overlapping_windows():
    rows = [
        _valid_peer_group_metric_profile_row(effective_from="2020-01-01", effective_to="2021-06-01", mapping_version="v1"),
        _valid_peer_group_metric_profile_row(effective_from="2021-01-01", effective_to=None, mapping_version="v2"),
    ]
    failures = validate_peer_group_metric_profile_data(rows, _PEER_GROUPS_FIXTURE)
    assert any("overlaps_rows" in f for f in failures)


def test_peer_group_metric_profile_data_allows_non_overlapping_sequential_windows():
    rows = [
        _valid_peer_group_metric_profile_row(effective_from="2020-01-01", effective_to="2021-01-01", mapping_version="v1"),
        _valid_peer_group_metric_profile_row(effective_from="2021-01-01", effective_to=None, mapping_version="v2"),
    ]
    assert validate_peer_group_metric_profile_data(rows, _PEER_GROUPS_FIXTURE) == []


# ============================================================
# get_metric_profile_for_peer_group_as_of
# ============================================================

def test_get_metric_profile_lookup_ok():
    rows = [_valid_peer_group_metric_profile_row()]
    result = get_metric_profile_for_peer_group_as_of("Memory Semiconductors", "2026-09-28", rows)
    assert result["lookup_status"] == LOOKUP_OK
    assert result["mapping"]["profile_id"] == "MEMORY_SEMICONDUCTOR"


def test_get_metric_profile_lookup_no_mapping_at_all():
    result = get_metric_profile_for_peer_group_as_of("Nonexistent Group", "2026-09-28", [])
    assert result["lookup_status"] == LOOKUP_NO_MAPPING_FOR_DATE


def test_get_metric_profile_lookup_not_yet_available():
    row = _valid_peer_group_metric_profile_row(
        effective_from="2020-01-01",
        classification_available_date="2026-12-31",
    )
    result = get_metric_profile_for_peer_group_as_of("Memory Semiconductors", "2026-09-28", [row])
    assert result["lookup_status"] == LOOKUP_NOT_YET_AVAILABLE


def test_get_metric_profile_lookup_missing_availability_date():
    row = _valid_peer_group_metric_profile_row(classification_available_date=None)
    result = get_metric_profile_for_peer_group_as_of("Memory Semiconductors", "2026-09-28", [row])
    assert result["lookup_status"] == LOOKUP_MISSING_AVAILABILITY_DATE


def test_get_metric_profile_lookup_overlapping_mapping():
    rows = [
        _valid_peer_group_metric_profile_row(mapping_version="v1"),
        _valid_peer_group_metric_profile_row(mapping_version="v2", profile_id="GENERAL_CORPORATE"),
    ]
    result = get_metric_profile_for_peer_group_as_of("Memory Semiconductors", "2026-09-28", rows)
    assert result["lookup_status"] == LOOKUP_OVERLAPPING_MAPPING


def test_get_metric_profile_lookup_invalid_reference():
    row = _valid_peer_group_metric_profile_row(profile_id="NOT_REAL")
    result = get_metric_profile_for_peer_group_as_of("Memory Semiconductors", "2026-09-28", [row])
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE


def test_get_metric_profile_lookup_no_mapping_before_effective_from():
    row = _valid_peer_group_metric_profile_row(effective_from="2026-09-28")
    result = get_metric_profile_for_peer_group_as_of("Memory Semiconductors", "2020-01-01", [row])
    assert result["lookup_status"] == LOOKUP_NO_MAPPING_FOR_DATE


# ============================================================
# metric_applicability.csv
# ============================================================

def _valid_applicable_row(**overrides) -> dict:
    row = {
        "profile_id": "GENERAL_CORPORATE",
        "metric_name": "fcf_margin",
        "applicability_status": "APPLICABLE",
        "resolver_id": None,
        "policy_owner": "ChatGPT design round",
    }
    row.update(overrides)
    return row


def _valid_conditional_row(**overrides) -> dict:
    row = {
        "profile_id": "FABLESS_SEMICONDUCTOR",
        "metric_name": "operating_margin",
        "applicability_status": "CONDITIONAL",
        "resolver_id": "MODIFIER_TABLE_RESOLVER",
        "policy_owner": "ChatGPT design round",
    }
    row.update(overrides)
    return row


def test_valid_applicable_row_passes():
    assert validate_metric_applicability_row(_valid_applicable_row()) == []


def test_valid_conditional_row_passes():
    assert validate_metric_applicability_row(_valid_conditional_row()) == []


def test_conditional_without_resolver_id_fails():
    row = _valid_conditional_row(resolver_id=None)
    assert "resolver_id_required_for_conditional" in validate_metric_applicability_row(row)


def test_conditional_with_unknown_resolver_id_fails():
    row = _valid_conditional_row(resolver_id="SOME_PYTHON_FUNCTION_NAME")
    assert "resolver_id_unknown" in validate_metric_applicability_row(row)


@pytest.mark.parametrize("status", ["APPLICABLE", "NOT_APPLICABLE", "UNVERIFIED"])
def test_non_conditional_with_resolver_id_fails(status):
    row = _valid_applicable_row(applicability_status=status, resolver_id="MODIFIER_TABLE_RESOLVER")
    assert "resolver_id_must_be_empty_for_non_conditional" in validate_metric_applicability_row(row)


def test_applicability_row_rejects_unknown_metric_name():
    row = _valid_applicable_row(metric_name="not_a_real_metric")
    assert "metric_name_unknown" in validate_metric_applicability_row(row)


def test_applicability_row_requires_policy_owner():
    row = _valid_applicable_row(policy_owner=None)
    assert "policy_owner" in validate_metric_applicability_row(row)


def _full_84_row_matrix(conditional_key: tuple[str, str] | None = None) -> list[dict]:
    """A synthetic, structurally-complete 84-row metric_applicability.csv
    fixture -- APPLICABLE everywhere except optionally one CONDITIONAL
    cell. This is fixture data for schema testing only, NOT the real
    economic policy (see module docstring / test file docstring)."""
    rows = []
    for profile_id in sorted(PROFILE_IDS):
        for metric_name in sorted(PRODUCTION_METRICS):
            if conditional_key == (profile_id, metric_name):
                rows.append(_valid_conditional_row(profile_id=profile_id, metric_name=metric_name))
            else:
                rows.append(_valid_applicable_row(profile_id=profile_id, metric_name=metric_name))
    return rows


def test_full_84_row_matrix_passes_completeness():
    rows = _full_84_row_matrix()
    assert validate_metric_applicability_data(rows, modifier_rows=[]) == []


def test_incomplete_matrix_flags_each_missing_combination():
    rows = _full_84_row_matrix()[:-3]
    failures = validate_metric_applicability_data(rows, modifier_rows=[])
    missing = [f for f in failures if f.startswith("missing_row[")]
    assert len(missing) == 3


def test_duplicate_profile_metric_combination_flagged():
    rows = _full_84_row_matrix()
    rows.append(_valid_applicable_row(profile_id=rows[0]["profile_id"], metric_name=rows[0]["metric_name"]))
    failures = validate_metric_applicability_data(rows, modifier_rows=[])
    assert any("duplicate_profile_metric" in f for f in failures)


def test_conditional_row_without_any_modifier_policy_is_hard_error():
    key = ("FABLESS_SEMICONDUCTOR", "operating_margin")
    rows = _full_84_row_matrix(conditional_key=key)
    failures = validate_metric_applicability_data(rows, modifier_rows=[])
    assert any("conditional_missing_modifier_policy" in f for f in failures)


def test_conditional_row_with_matching_modifier_policy_passes():
    key = ("FABLESS_SEMICONDUCTOR", "operating_margin")
    rows = _full_84_row_matrix(conditional_key=key)
    modifier_rows = [_valid_modifier_row(profile_id=key[0], metric_name=key[1])]
    assert validate_metric_applicability_data(rows, modifier_rows=modifier_rows) == []


def test_conditional_row_with_structurally_invalid_modifier_policy_still_fails():
    """A malformed modifier row is never counted as real coverage."""
    key = ("FABLESS_SEMICONDUCTOR", "operating_margin")
    rows = _full_84_row_matrix(conditional_key=key)
    broken_modifier = _valid_modifier_row(profile_id=key[0], metric_name=key[1], trigger="not a trigger")
    failures = validate_metric_applicability_data(rows, modifier_rows=[broken_modifier])
    assert any("conditional_missing_modifier_policy" in f for f in failures)


# ============================================================
# metric_applicability_modifiers.csv
# ============================================================

def _valid_modifier_row(**overrides) -> dict:
    row = {
        "profile_id": "FABLESS_SEMICONDUCTOR",
        "metric_name": "operating_margin",
        "modifier_id": "MOD_FABLESS_OPMARGIN_DIVERSIFIED_01",
        "trigger": "business_mix_status=DIVERSIFIED",
        "result": "NOT_APPLICABLE",
        "reason_code": "BUSINESS_MIX_DIVERSIFIED_OVERRIDE",
        "policy_owner": "ChatGPT design round",
        "rationale": "Diversified business mix distorts pure-play comparison.",
        "effective_from": "2026-09-28",
        "effective_to": None,
    }
    row.update(overrides)
    return row


def test_valid_modifier_row_passes():
    assert validate_metric_applicability_modifier_row(_valid_modifier_row()) == []


@pytest.mark.parametrize(
    "bad_trigger",
    [
        "business_mix_status == DIVERSIFIED",
        "business_mix_status=DIVERSIFIED;other=x",
        "business_mix_status",
        "eval(1)",
        "business_mix_status=DIVERSIFIED AND segment_reporting_available=TRUE",
        "",
    ],
)
def test_modifier_row_rejects_malformed_trigger_format(bad_trigger):
    row = _valid_modifier_row(trigger=bad_trigger)
    failures = validate_metric_applicability_modifier_row(row)
    assert ("trigger_format" in failures) or ("trigger" in failures)


def test_modifier_row_rejects_unknown_trigger_field():
    row = _valid_modifier_row(trigger="revenue_growth_bucket=HIGH")
    assert "trigger_field_unknown" in validate_metric_applicability_modifier_row(row)


def test_modifier_row_rejects_unknown_trigger_value():
    row = _valid_modifier_row(trigger="business_mix_status=SOMETHING_ELSE")
    assert "trigger_value_unknown" in validate_metric_applicability_modifier_row(row)


def test_modifier_row_rejects_unverified_as_trigger_value():
    """2026-09-28 confirmation: a trigger reacts to a DETERMINED fact --
    UNVERIFIED is deliberately excluded from trigger value allow-lists."""
    row = _valid_modifier_row(trigger="business_mix_status=UNVERIFIED")
    assert "trigger_value_unknown" in validate_metric_applicability_modifier_row(row)


def test_modifier_row_rejects_conditional_as_result():
    row = _valid_modifier_row(result="CONDITIONAL")
    assert "result_unknown" in validate_metric_applicability_modifier_row(row)


@pytest.mark.parametrize("field", ["reason_code", "policy_owner", "rationale"])
def test_modifier_row_requires_reason_fields(field):
    row = _valid_modifier_row(**{field: None})
    assert field in validate_metric_applicability_modifier_row(row)


def test_modifier_row_open_ended_effective_to_allowed():
    row = _valid_modifier_row(effective_to=None)
    assert validate_metric_applicability_modifier_row(row) == []


def test_modifier_row_equal_effective_from_to_fails():
    row = _valid_modifier_row(effective_from="2026-09-28", effective_to="2026-09-28")
    assert "effective_from_not_before_effective_to" in validate_metric_applicability_modifier_row(row)


def test_modifiers_data_flags_duplicate_modifier_id():
    rows = [
        _valid_modifier_row(modifier_id="MOD_01", trigger="business_mix_status=DIVERSIFIED"),
        _valid_modifier_row(modifier_id="MOD_01", trigger="segment_reporting_available=FALSE"),
    ]
    failures = validate_metric_applicability_modifiers_data(rows)
    assert any("duplicate_modifier_id" in f for f in failures)


def test_modifiers_data_flags_same_trigger_overlapping_interval_even_with_different_results():
    """2026-09-28 confirmation's headline example: same profile+metric+trigger,
    overlapping period, NOT_APPLICABLE vs UNVERIFIED -- always an error,
    never resolved by precedence."""
    rows = [
        _valid_modifier_row(modifier_id="MOD_A", effective_from="2026-01-01", effective_to=None, result="NOT_APPLICABLE"),
        _valid_modifier_row(modifier_id="MOD_B", effective_from="2026-06-01", effective_to=None, result="UNVERIFIED"),
    ]
    failures = validate_metric_applicability_modifiers_data(rows)
    assert any("same_trigger_overlaps_rows" in f for f in failures)


def test_modifiers_data_flags_same_trigger_overlapping_interval_even_with_same_result():
    rows = [
        _valid_modifier_row(modifier_id="MOD_A", effective_from="2026-01-01", effective_to="2026-12-01"),
        _valid_modifier_row(modifier_id="MOD_B", effective_from="2026-06-01", effective_to=None),
    ]
    failures = validate_metric_applicability_modifiers_data(rows)
    assert any("same_trigger_overlaps_rows" in f for f in failures)


def test_modifiers_data_allows_same_trigger_non_overlapping_sequential_versions():
    rows = [
        _valid_modifier_row(modifier_id="MOD_A", effective_from="2026-01-01", effective_to="2026-06-01"),
        _valid_modifier_row(modifier_id="MOD_B", effective_from="2026-06-01", effective_to=None),
    ]
    assert validate_metric_applicability_modifiers_data(rows) == []


def test_modifiers_data_allows_different_triggers_same_profile_metric_same_period():
    rows = [
        _valid_modifier_row(modifier_id="MOD_A", trigger="business_mix_status=DIVERSIFIED"),
        _valid_modifier_row(modifier_id="MOD_B", trigger="segment_reporting_available=FALSE"),
    ]
    assert validate_metric_applicability_modifiers_data(rows) == []


# ============================================================
# get_metric_applicability_status (fail-closed lookup)
# ============================================================

def test_get_status_returns_matching_row_status():
    rows = [_valid_applicable_row(profile_id="BANKING", metric_name="fcf_margin", applicability_status="NOT_APPLICABLE")]
    assert get_metric_applicability_status("BANKING", "fcf_margin", rows) == "NOT_APPLICABLE"


def test_get_status_missing_row_returns_unverified():
    assert get_metric_applicability_status("BANKING", "fcf_margin", []) == "UNVERIFIED"


def test_get_status_structurally_invalid_row_treated_as_missing():
    rows = [_valid_applicable_row(profile_id="BANKING", metric_name="fcf_margin", applicability_status="MAYBE")]
    assert get_metric_applicability_status("BANKING", "fcf_margin", rows) == "UNVERIFIED"


# ============================================================
# evaluate_modifiers / resolve_metric_applicability
# ============================================================

def test_evaluate_modifiers_single_match():
    rows = [_valid_modifier_row()]
    result = evaluate_modifiers(
        "FABLESS_SEMICONDUCTOR", "operating_margin",
        {"business_mix_status": "DIVERSIFIED"}, "2026-09-28", rows,
    )
    assert result["status"] == "NOT_APPLICABLE"
    assert result["matched_modifier_ids"] == ["MOD_FABLESS_OPMARGIN_DIVERSIFIED_01"]


def test_evaluate_modifiers_no_match_falls_to_unverified():
    rows = [_valid_modifier_row()]
    result = evaluate_modifiers(
        "FABLESS_SEMICONDUCTOR", "operating_margin",
        {"business_mix_status": "PURE_PLAY"}, "2026-09-28", rows,
    )
    assert result["status"] == "UNVERIFIED"
    assert result["matched_modifier_ids"] == []


def test_evaluate_modifiers_runtime_fact_of_unverified_falls_to_unverified():
    rows = [_valid_modifier_row()]
    result = evaluate_modifiers(
        "FABLESS_SEMICONDUCTOR", "operating_margin",
        {"business_mix_status": "UNVERIFIED"}, "2026-09-28", rows,
    )
    assert result["status"] == "UNVERIFIED"


def test_evaluate_modifiers_unrecognized_runtime_value_fails_closed():
    rows = [_valid_modifier_row()]
    result = evaluate_modifiers(
        "FABLESS_SEMICONDUCTOR", "operating_margin",
        {"business_mix_status": "GARBAGE_VALUE"}, "2026-09-28", rows,
    )
    assert result["status"] == "UNVERIFIED"
    assert result["reason"] is not None


def test_evaluate_modifiers_pit_gates_not_yet_effective_modifier():
    rows = [_valid_modifier_row(effective_from="2027-01-01")]
    result = evaluate_modifiers(
        "FABLESS_SEMICONDUCTOR", "operating_margin",
        {"business_mix_status": "DIVERSIFIED"}, "2026-09-28", rows,
    )
    assert result["status"] == "UNVERIFIED"


def test_evaluate_modifiers_pit_gates_expired_modifier():
    rows = [_valid_modifier_row(effective_from="2020-01-01", effective_to="2021-01-01")]
    result = evaluate_modifiers(
        "FABLESS_SEMICONDUCTOR", "operating_margin",
        {"business_mix_status": "DIVERSIFIED"}, "2026-09-28", rows,
    )
    assert result["status"] == "UNVERIFIED"


@pytest.mark.parametrize(
    "result_a,result_b,expected_winner",
    [
        ("NOT_APPLICABLE", "APPLICABLE", "NOT_APPLICABLE"),
        ("UNVERIFIED", "APPLICABLE", "UNVERIFIED"),
        ("UNVERIFIED", "NOT_APPLICABLE", "UNVERIFIED"),
        ("APPLICABLE", "APPLICABLE", "APPLICABLE"),
    ],
)
def test_evaluate_modifiers_different_trigger_conflict_priority(result_a, result_b, expected_winner):
    """Different triggers matching simultaneously -- resolved by
    MODIFIER_CONFLICT_PRIORITY, never a validation error (unlike the
    same-trigger-overlap case)."""
    rows = [
        _valid_modifier_row(
            modifier_id="MOD_A", trigger="business_mix_status=DIVERSIFIED", result=result_a,
        ),
        _valid_modifier_row(
            modifier_id="MOD_B", trigger="segment_reporting_available=FALSE", result=result_b,
        ),
    ]
    result = evaluate_modifiers(
        "FABLESS_SEMICONDUCTOR", "operating_margin",
        {"business_mix_status": "DIVERSIFIED", "segment_reporting_available": "FALSE"},
        "2026-09-28", rows,
    )
    assert result["status"] == expected_winner


def test_modifier_conflict_priority_is_a_total_order():
    assert len(set(MODIFIER_CONFLICT_PRIORITY.values())) == len(MODIFIER_RESULT_STATUSES)


def test_resolve_metric_applicability_base_applicable_never_consults_modifiers():
    applicability_rows = [
        _valid_applicable_row(profile_id="GENERAL_CORPORATE", metric_name="fcf_margin", applicability_status="APPLICABLE")
    ]
    # A "poisoned" modifier row that would resolve to NOT_APPLICABLE if it
    # were ever consulted -- it must not be, since base status is already
    # APPLICABLE (not CONDITIONAL).
    poisoned_modifier_rows = [
        _valid_modifier_row(profile_id="GENERAL_CORPORATE", metric_name="fcf_margin", result="NOT_APPLICABLE")
    ]
    result = resolve_metric_applicability(
        "GENERAL_CORPORATE", "fcf_margin", {"business_mix_status": "DIVERSIFIED"},
        "2026-09-28", applicability_rows, poisoned_modifier_rows,
    )
    assert result == {"status": "APPLICABLE", "source": "base_policy", "matched_modifier_ids": []}


def test_resolve_metric_applicability_base_conditional_delegates_to_modifiers():
    applicability_rows = [_valid_conditional_row()]
    modifier_rows = [_valid_modifier_row()]
    result = resolve_metric_applicability(
        "FABLESS_SEMICONDUCTOR", "operating_margin", {"business_mix_status": "DIVERSIFIED"},
        "2026-09-28", applicability_rows, modifier_rows,
    )
    assert result["status"] == "NOT_APPLICABLE"
    assert result["source"] == "modifier_resolution"


def test_resolve_metric_applicability_missing_row_is_unverified_never_default_applicability():
    result = resolve_metric_applicability(
        "BANKING", "fcf_margin", {}, "2026-09-28", applicability_rows=[], modifier_rows=[],
    )
    assert result["status"] == "UNVERIFIED"
    assert result["source"] == "base_policy"
