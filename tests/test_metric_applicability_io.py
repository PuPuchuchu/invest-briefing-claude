"""
Unit tests for src/fundamentals/metric_applicability_io.py (Framework
v2.1 Step 4, 2026-09-28). Read/write round-trip tests for all four Metric
Applicability reference files, mirroring test_peer_classification_io.py /
test_core_universe_io.py's structure. Uses pytest's tmp_path fixture --
never writes to data/reference/ directly.
"""

from __future__ import annotations

from src.fundamentals.metric_applicability_io import (
    METRIC_APPLICABILITY_FIELDNAMES,
    METRIC_APPLICABILITY_MODIFIERS_FIELDNAMES,
    METRIC_PROFILES_FIELDNAMES,
    PEER_GROUP_METRIC_PROFILE_FIELDNAMES,
    read_metric_applicability_csv,
    read_metric_applicability_modifiers_csv,
    read_metric_profiles_csv,
    read_peer_group_metric_profile_csv,
    write_metric_applicability_csv,
    write_metric_applicability_modifiers_csv,
    write_metric_profiles_csv,
    write_peer_group_metric_profile_csv,
)


def test_metric_profiles_fieldnames_match_confirmed_schema():
    assert METRIC_PROFILES_FIELDNAMES == ["profile_id", "description", "default_applicability"]


def test_peer_group_metric_profile_fieldnames_match_confirmed_schema():
    assert PEER_GROUP_METRIC_PROFILE_FIELDNAMES == [
        "peer_group",
        "profile_id",
        "effective_from",
        "effective_to",
        "classification_available_date",
        "mapping_source",
        "mapping_version",
        "reviewed_at",
        "mapping_rationale",
    ]


def test_metric_applicability_fieldnames_match_confirmed_schema():
    assert METRIC_APPLICABILITY_FIELDNAMES == [
        "profile_id",
        "metric_name",
        "applicability_status",
        "resolver_id",
        "policy_owner",
        "policy_version",
    ]


def test_metric_applicability_modifiers_fieldnames_match_confirmed_schema():
    assert METRIC_APPLICABILITY_MODIFIERS_FIELDNAMES == [
        "profile_id",
        "metric_name",
        "modifier_id",
        "trigger",
        "result",
        "reason_code",
        "policy_owner",
        "rationale",
        "effective_from",
        "effective_to",
        "policy_version",
    ]


def test_metric_profiles_csv_round_trip(tmp_path):
    rows = [
        {"profile_id": "GENERAL_CORPORATE", "description": "Default profile.", "default_applicability": "APPLICABLE"},
        {"profile_id": "BANKING", "description": "Banking profile.", "default_applicability": "UNVERIFIED"},
    ]
    path = tmp_path / "metric_profiles.csv"
    write_metric_profiles_csv(rows, path)
    read_back = read_metric_profiles_csv(path)
    assert read_back == rows


def test_metric_profiles_csv_round_trip_missing_value_becomes_none(tmp_path):
    rows = [{"profile_id": "GENERAL_CORPORATE", "description": None, "default_applicability": "APPLICABLE"}]
    path = tmp_path / "metric_profiles.csv"
    write_metric_profiles_csv(rows, path)
    read_back = read_metric_profiles_csv(path)
    assert read_back[0]["description"] is None


def test_peer_group_metric_profile_csv_round_trip(tmp_path):
    rows = [
        {
            "peer_group": "Memory Semiconductors",
            "profile_id": "MEMORY_SEMICONDUCTOR",
            "effective_from": "2026-09-28",
            "effective_to": None,
            "classification_available_date": "2026-09-28",
            "mapping_source": "MANUAL_REVIEW",
            "mapping_version": "v1",
            "reviewed_at": "2026-09-28",
            "mapping_rationale": "1:1 mapping.",
        }
    ]
    path = tmp_path / "peer_group_metric_profile.csv"
    write_peer_group_metric_profile_csv(rows, path)
    assert read_peer_group_metric_profile_csv(path) == rows


def test_metric_applicability_csv_round_trip(tmp_path):
    rows = [
        {
            "profile_id": "GENERAL_CORPORATE",
            "metric_name": "fcf_margin",
            "applicability_status": "APPLICABLE",
            "resolver_id": None,
            "policy_owner": "ChatGPT design round",
            "policy_version": "v1.0",
        },
        {
            "profile_id": "FABLESS_SEMICONDUCTOR",
            "metric_name": "operating_margin",
            "applicability_status": "CONDITIONAL",
            "resolver_id": "MODIFIER_TABLE_RESOLVER",
            "policy_owner": "ChatGPT design round",
            "policy_version": "v1.0",
        },
    ]
    path = tmp_path / "metric_applicability.csv"
    write_metric_applicability_csv(rows, path)
    assert read_metric_applicability_csv(path) == rows


def test_metric_applicability_modifiers_csv_round_trip(tmp_path):
    rows = [
        {
            "profile_id": "FABLESS_SEMICONDUCTOR",
            "metric_name": "operating_margin",
            "modifier_id": "MOD_01",
            "trigger": "business_mix_status=DIVERSIFIED",
            "result": "NOT_APPLICABLE",
            "reason_code": "BUSINESS_MIX_DIVERSIFIED_OVERRIDE",
            "policy_owner": "ChatGPT design round",
            "rationale": "Diversified business mix.",
            "effective_from": "2026-09-28",
            "effective_to": None,
            "policy_version": "v1.0",
        }
    ]
    path = tmp_path / "metric_applicability_modifiers.csv"
    write_metric_applicability_modifiers_csv(rows, path)
    assert read_metric_applicability_modifiers_csv(path) == rows


def test_write_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "metric_profiles.csv"
    write_metric_profiles_csv([], path)
    assert path.exists()


def test_empty_write_produces_header_only_file(tmp_path):
    path = tmp_path / "metric_applicability.csv"
    write_metric_applicability_csv([], path)
    content = path.read_text(encoding="utf-8")
    assert content.strip() == ",".join(METRIC_APPLICABILITY_FIELDNAMES)
