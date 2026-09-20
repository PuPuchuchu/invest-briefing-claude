"""
Unit tests for src/fundamentals/peer_classification.py.

Pure Unit Tests in the taxonomy sense: no network, no file I/O, synthetic
fixtures only. These exist specifically to prove the properties required by
the 2026-09-19 design review's final 6-point round:

    1. classification_status vs classification_source are validated as two
       separate axes with an explicit valid/invalid combination allow-list.
    2. Peer-comparison usability is a policy (REVIEWED usable primary,
       SIC_DERIVED candidate-only, PENDING_REVIEW/UNCLASSIFIED blocked).
    3. get_peer_mapping_as_of() returns a rich (lookup_status, mapping,
       reason) result distinguishing all failure modes, never one flat
       UNVERIFIED.
    4. effective_to is a half-open interval boundary: [effective_from,
       effective_to) -- effective_to itself already belongs to whatever
       comes next.
    5. CIK existence is checked (Stage 1: internal reference integrity,
       here; Stage 2: provenance is src.sec.identity's job, tested there).
    6. (offline fixture integration -- see test_peer_classification_integration.py)
"""

from datetime import date

import pytest

from src.fundamentals.peer_classification import (
    SCHEMA_VERSION,
    CLASSIFICATION_SOURCES,
    CLASSIFICATION_STATUSES,
    PRIMARY_COMPARISON_STATUSES,
    CANDIDATE_COMPARISON_STATUSES,
    LOOKUP_OK,
    LOOKUP_NO_MAPPING_FOR_DATE,
    LOOKUP_UNVERIFIED,
    LOOKUP_MISSING_AVAILABILITY_DATE,
    LOOKUP_NOT_YET_AVAILABLE,
    LOOKUP_OVERLAPPING_MAPPING,
    LOOKUP_INVALID_REFERENCE,
    classify_peer_adequacy,
    validate_peer_mapping_row,
    validate_reference_data,
    get_peer_mapping_as_of,
    build_comparison_peers,
)


# ============================================================
# classify_peer_adequacy
# ============================================================

@pytest.mark.parametrize(
    "count,expected",
    [
        (0, "INSUFFICIENT_PEERS"),
        (4, "INSUFFICIENT_PEERS"),
        (5, "LIMITED_PEERS"),
        (7, "LIMITED_PEERS"),
        (8, "SUFFICIENT_PEERS"),
        (14, "SUFFICIENT_PEERS"),
        (15, "ROBUST_PEERS"),
        (100, "ROBUST_PEERS"),
    ],
)
def test_classify_peer_adequacy_thresholds(count, expected):
    assert classify_peer_adequacy(count) == expected


# ============================================================
# validate_peer_mapping_row -- source/status combination allow-list
# ============================================================

def _reviewed_row(**overrides):
    row = {
        "cik": "0000320193",
        "mapping_version": "v1",
        "classification_status": "REVIEWED",
        "classification_source": "MANUAL",
        "peer_group": "hardware_devices",
        "effective_from": "2025-01-01",
        "effective_to": None,
        "classification_available_date": "2025-01-05",
        "reviewed_at": "2025-01-05",
    }
    row.update(overrides)
    return row


def test_validate_peer_mapping_row_accepts_valid_reviewed_row():
    assert validate_peer_mapping_row(_reviewed_row()) == []


def test_validate_peer_mapping_row_accepts_valid_sic_derived_row():
    row = _reviewed_row(
        classification_status="SIC_DERIVED",
        classification_source="SIC_DERIVED",
    )
    assert validate_peer_mapping_row(row) == []


def test_validate_peer_mapping_row_accepts_valid_unclassified_row():
    row = {
        "cik": "0000000001",
        "mapping_version": "v1",
        "classification_status": "UNCLASSIFIED",
        "classification_source": None,
        "peer_group": None,
        "effective_from": "2025-01-01",
    }
    assert validate_peer_mapping_row(row) == []


def test_validate_peer_mapping_row_rejects_manual_source_with_sic_derived_status():
    # A MANUAL-sourced row cannot sit in SIC_DERIVED status -- that status
    # specifically means "mechanically derived, no human has touched this".
    row = _reviewed_row(classification_status="SIC_DERIVED", classification_source="MANUAL")
    assert "classification_source_status_combination" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_rejects_unclassified_with_a_source():
    row = _reviewed_row(classification_status="UNCLASSIFIED", classification_source="MANUAL")
    assert "classification_source_status_combination" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_rejects_sic_derived_source_with_unclassified_status():
    row = _reviewed_row(classification_status="UNCLASSIFIED", classification_source="SIC_DERIVED")
    assert "classification_source_status_combination" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_rejects_reviewed_without_peer_group():
    row = _reviewed_row(peer_group=None)
    assert "peer_group_missing_for_reviewed" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_rejects_unclassified_with_peer_group():
    row = {
        "cik": "0000000001",
        "mapping_version": "v1",
        "classification_status": "UNCLASSIFIED",
        "classification_source": None,
        "peer_group": "some_group",
        "effective_from": "2025-01-01",
    }
    assert "peer_group_present_for_unclassified" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_requires_classification_available_date_for_reviewed():
    row = _reviewed_row(classification_available_date=None)
    assert "classification_available_date_missing" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_requires_classification_available_date_for_sic_derived():
    row = _reviewed_row(
        classification_status="SIC_DERIVED",
        classification_source="SIC_DERIVED",
        classification_available_date=None,
    )
    assert "classification_available_date_missing" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_does_not_require_availability_date_for_pending_review():
    row = _reviewed_row(
        classification_status="PENDING_REVIEW",
        classification_source="SIC_DERIVED",
        peer_group=None,
        classification_available_date=None,
    )
    failures = validate_peer_mapping_row(row)
    assert "classification_available_date_missing" not in failures


def test_validate_peer_mapping_row_rejects_bad_status():
    row = _reviewed_row(classification_status="MADE_UP_STATUS")
    assert "classification_status" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_rejects_bad_source():
    row = _reviewed_row(classification_source="MADE_UP_SOURCE")
    assert "classification_source" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_rejects_unparseable_effective_from():
    row = _reviewed_row(effective_from="not-a-date")
    assert "effective_from_format" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_rejects_unparseable_effective_to():
    row = _reviewed_row(effective_to="not-a-date")
    assert "effective_to_format" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_rejects_effective_from_after_effective_to():
    row = _reviewed_row(effective_from="2025-06-01", effective_to="2025-01-01")
    assert "effective_from_after_effective_to" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_rejects_unparseable_availability_date():
    row = _reviewed_row(classification_available_date="not-a-date")
    assert "classification_available_date_format" in validate_peer_mapping_row(row)


def test_validate_peer_mapping_row_rejects_missing_required_keys():
    row = {}
    failures = validate_peer_mapping_row(row)
    for key in ("cik", "mapping_version", "classification_status", "effective_from"):
        assert key in failures


def test_validate_peer_mapping_row_rejects_non_dict():
    assert validate_peer_mapping_row(None) == ["root"]


# ============================================================
# validate_reference_data -- cross-row checks
# ============================================================

def _issuer_rows():
    return [{"cik": "0000320193", "issuer_name": "Apple Inc."}]


def test_validate_reference_data_accepts_clean_set():
    peer_rows = [_reviewed_row()]
    assert validate_reference_data(_issuer_rows(), peer_rows) == []


def test_validate_reference_data_flags_unknown_cik():
    row = _reviewed_row(cik="9999999999")
    failures = validate_reference_data(_issuer_rows(), [row])
    assert any("unknown_cik" in f for f in failures)


def test_validate_reference_data_flags_duplicate_mapping_version_within_cik():
    row_a = _reviewed_row(effective_from="2024-01-01", effective_to="2025-01-01")
    row_b = _reviewed_row(effective_from="2025-01-01", effective_to=None)  # same mapping_version "v1"
    failures = validate_reference_data(_issuer_rows(), [row_a, row_b])
    assert any("duplicate_mapping_version" in f for f in failures)


def test_validate_reference_data_allows_same_mapping_version_across_different_ciks():
    row_a = _reviewed_row(cik="0000320193")
    row_b = _reviewed_row(cik="0000789019")
    failures = validate_reference_data(
        [{"cik": "0000320193"}, {"cik": "0000789019"}], [row_a, row_b]
    )
    assert not any("duplicate_mapping_version" in f for f in failures)


def test_validate_reference_data_flags_overlapping_effective_ranges():
    row_a = _reviewed_row(mapping_version="v1", effective_from="2024-01-01", effective_to="2025-06-01")
    row_b = _reviewed_row(mapping_version="v2", effective_from="2025-01-01", effective_to=None)
    failures = validate_reference_data(_issuer_rows(), [row_a, row_b])
    assert any("overlaps_peer_rows" in f for f in failures)


def test_validate_reference_data_allows_adjacent_non_overlapping_ranges():
    # row_a ends exactly when row_b begins -- half-open interval means
    # this is NOT an overlap.
    row_a = _reviewed_row(mapping_version="v1", effective_from="2024-01-01", effective_to="2025-01-01")
    row_b = _reviewed_row(mapping_version="v2", effective_from="2025-01-01", effective_to=None)
    failures = validate_reference_data(_issuer_rows(), [row_a, row_b])
    assert not any("overlaps_peer_rows" in f for f in failures)


def test_validate_reference_data_propagates_row_level_failures_with_index_prefix():
    bad_row = _reviewed_row(classification_status="MADE_UP_STATUS")
    failures = validate_reference_data(_issuer_rows(), [bad_row])
    assert "peer_rows[0].classification_status" in failures


# ============================================================
# get_peer_mapping_as_of -- all lookup_status outcomes
# ============================================================

def test_get_peer_mapping_as_of_ok():
    row = _reviewed_row(effective_from="2025-01-01", effective_to=None, classification_available_date="2025-01-05")
    result = get_peer_mapping_as_of("0000320193", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_OK
    assert result["mapping"] == row
    assert result["reason"] is None


def test_get_peer_mapping_as_of_no_mapping_rows_at_all():
    result = get_peer_mapping_as_of("0000320193", "2025-06-01", [])
    assert result["lookup_status"] == LOOKUP_NO_MAPPING_FOR_DATE
    assert result["mapping"] is None


def test_get_peer_mapping_as_of_no_mapping_covers_evaluation_date():
    row = _reviewed_row(effective_from="2025-01-01", effective_to="2025-03-01")
    result = get_peer_mapping_as_of("0000320193", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_NO_MAPPING_FOR_DATE


def test_get_peer_mapping_as_of_evaluation_date_before_effective_from_is_no_mapping():
    row = _reviewed_row(effective_from="2025-01-01", effective_to=None)
    result = get_peer_mapping_as_of("0000320193", "2024-12-31", [row])
    assert result["lookup_status"] == LOOKUP_NO_MAPPING_FOR_DATE


def test_get_peer_mapping_as_of_invalid_reference_when_all_rows_structurally_bad():
    bad_row = _reviewed_row(classification_status="MADE_UP_STATUS")
    result = get_peer_mapping_as_of("0000320193", "2025-06-01", [bad_row])
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE


def test_get_peer_mapping_as_of_overlapping_mapping_when_two_rows_both_cover_date():
    row_a = _reviewed_row(mapping_version="v1", effective_from="2024-01-01", effective_to=None)
    row_b = _reviewed_row(mapping_version="v2", effective_from="2024-06-01", effective_to=None)
    result = get_peer_mapping_as_of("0000320193", "2025-01-01", [row_a, row_b])
    assert result["lookup_status"] == LOOKUP_OVERLAPPING_MAPPING


def test_get_peer_mapping_as_of_reviewed_row_missing_availability_date_fails_structural_validation():
    # REVIEWED status structurally REQUIRES classification_available_date
    # (validate_peer_mapping_row), so a REVIEWED row missing it never even
    # reaches the availability check -- it is filtered out of
    # structurally_valid entirely, and with no other rows for this CIK the
    # lookup reports INVALID_REFERENCE, not MISSING_AVAILABILITY_DATE.
    row = _reviewed_row(classification_available_date=None)
    result = get_peer_mapping_as_of("0000320193", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE


def test_get_peer_mapping_as_of_missing_availability_date():
    # PENDING_REVIEW does NOT structurally require classification_available_date,
    # so a PENDING_REVIEW/SIC_DERIVED row lacking it CAN pass structural
    # validation and be selected as "covering" -- this is the real path
    # that reaches the MISSING_AVAILABILITY_DATE branch: the availability
    # gate is checked before the usable_statuses gate, so this fires ahead
    # of an UNVERIFIED verdict.
    row = _reviewed_row(
        classification_status="PENDING_REVIEW",
        classification_source="SIC_DERIVED",
        peer_group=None,
        classification_available_date=None,
    )
    result = get_peer_mapping_as_of(
        "0000320193", "2025-06-01", [row], usable_statuses=CANDIDATE_COMPARISON_STATUSES
    )
    assert result["lookup_status"] == LOOKUP_MISSING_AVAILABILITY_DATE
    assert result["mapping"] == row


def test_get_peer_mapping_as_of_unparseable_availability_date_on_a_structurally_invalid_row():
    # A row whose classification_available_date is present but unparseable
    # fails validate_peer_mapping_row's classification_available_date_format
    # check, so (mirroring the REVIEWED case above) it never reaches the
    # lookup's own MISSING_AVAILABILITY_DATE branch when it's the only row
    # for this CIK -- confirms the two layers (structural vs lookup-time)
    # don't silently double up in a way that hides which layer caught it.
    row = _reviewed_row(classification_available_date="not-a-date")
    result = get_peer_mapping_as_of("0000320193", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE


def test_get_peer_mapping_as_of_not_yet_available():
    row = _reviewed_row(effective_from="2025-01-01", effective_to=None, classification_available_date="2025-06-15")
    result = get_peer_mapping_as_of("0000320193", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_NOT_YET_AVAILABLE
    assert result["mapping"] == row


def test_get_peer_mapping_as_of_availability_date_exactly_on_evaluation_date_is_ok():
    row = _reviewed_row(effective_from="2025-01-01", effective_to=None, classification_available_date="2025-06-01")
    result = get_peer_mapping_as_of("0000320193", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_OK


def test_get_peer_mapping_as_of_unverified_when_status_not_usable():
    row = _reviewed_row(
        classification_status="SIC_DERIVED",
        classification_source="SIC_DERIVED",
        effective_from="2025-01-01",
        effective_to=None,
        classification_available_date="2025-01-05",
    )
    result = get_peer_mapping_as_of("0000320193", "2025-06-01", [row])  # default usable_statuses = PRIMARY (REVIEWED only)
    assert result["lookup_status"] == LOOKUP_UNVERIFIED


def test_get_peer_mapping_as_of_sic_derived_is_ok_under_candidate_comparison_statuses():
    row = _reviewed_row(
        classification_status="SIC_DERIVED",
        classification_source="SIC_DERIVED",
        effective_from="2025-01-01",
        effective_to=None,
        classification_available_date="2025-01-05",
    )
    result = get_peer_mapping_as_of(
        "0000320193", "2025-06-01", [row], usable_statuses=CANDIDATE_COMPARISON_STATUSES
    )
    assert result["lookup_status"] == LOOKUP_OK


# ---- half-open interval boundary tests (design point 4) ----

def test_get_peer_mapping_as_of_evaluation_date_exactly_at_effective_from_is_covered():
    row = _reviewed_row(effective_from="2025-01-01", effective_to=None, classification_available_date="2025-01-01")
    result = get_peer_mapping_as_of("0000320193", "2025-01-01", [row])
    assert result["lookup_status"] == LOOKUP_OK


def test_get_peer_mapping_as_of_evaluation_date_exactly_at_effective_to_is_not_covered():
    # effective_to is EXCLUSIVE -- a row with effective_to="2025-01-01"
    # does not cover 2025-01-01 itself.
    row = _reviewed_row(effective_from="2024-01-01", effective_to="2025-01-01", classification_available_date="2024-01-01")
    result = get_peer_mapping_as_of("0000320193", "2025-01-01", [row])
    assert result["lookup_status"] == LOOKUP_NO_MAPPING_FOR_DATE


def test_get_peer_mapping_as_of_evaluation_date_one_day_before_effective_to_is_covered():
    row = _reviewed_row(effective_from="2024-01-01", effective_to="2025-01-01", classification_available_date="2024-01-01")
    result = get_peer_mapping_as_of("0000320193", "2024-12-31", [row])
    assert result["lookup_status"] == LOOKUP_OK


def test_get_peer_mapping_as_of_retroactive_use_prevention():
    # A mapping whose effective period covers a PAST evaluation date, but
    # was only reviewed/available LATER, must never be usable for that past
    # date -- this is the core anti-look-ahead-bias guarantee.
    row = _reviewed_row(effective_from="2020-01-01", effective_to=None, classification_available_date="2026-01-01")
    result = get_peer_mapping_as_of("0000320193", "2021-01-01", [row])
    assert result["lookup_status"] == LOOKUP_NOT_YET_AVAILABLE


def test_get_peer_mapping_as_of_uses_coerce_evaluation_date_and_rejects_garbage():
    row = _reviewed_row()
    with pytest.raises(ValueError):
        get_peer_mapping_as_of("0000320193", "not-a-date", [row])


# ============================================================
# build_comparison_peers -- self-exclusion
# ============================================================

def test_build_comparison_peers_excludes_target_from_comparison_set():
    result = build_comparison_peers(
        "0000320193", ["0000320193", "0000789019", "0001045810"]
    )
    assert "0000320193" not in result["comparison_peers"]
    assert result["comparison_peer_count"] == 2
    assert result["peer_group_size"] == 3


def test_build_comparison_peers_reports_peer_adequacy_status():
    members = ["target"] + [f"peer{i}" for i in range(6)]  # 6 comparison peers
    result = build_comparison_peers("target", members)
    assert result["comparison_peer_count"] == 6
    assert result["peer_adequacy_status"] == "LIMITED_PEERS"


def test_build_comparison_peers_target_not_in_membership_still_works():
    # Defensive: target_cik absent from the membership list (a data bug
    # upstream) should not crash -- comparison set is simply the full list.
    result = build_comparison_peers("target", ["peer1", "peer2"])
    assert result["comparison_peers"] == ["peer1", "peer2"]
    assert result["peer_group_size"] == 2


def test_build_comparison_peers_insufficient_when_no_peers_besides_self():
    result = build_comparison_peers("target", ["target"])
    assert result["comparison_peer_count"] == 0
    assert result["peer_adequacy_status"] == "INSUFFICIENT_PEERS"


# ============================================================
# Module-level constants
# ============================================================

def test_schema_version_is_a_non_empty_string():
    assert isinstance(SCHEMA_VERSION, str) and SCHEMA_VERSION


def test_primary_and_candidate_comparison_statuses_are_consistent():
    assert PRIMARY_COMPARISON_STATUSES == {"REVIEWED"}
    assert CANDIDATE_COMPARISON_STATUSES == {"REVIEWED", "SIC_DERIVED"}
    assert PRIMARY_COMPARISON_STATUSES.issubset(CANDIDATE_COMPARISON_STATUSES)


def test_classification_sources_and_statuses_are_as_documented():
    assert CLASSIFICATION_SOURCES == {"SIC_DERIVED", "MANUAL"}
    assert CLASSIFICATION_STATUSES == {
        "REVIEWED",
        "PENDING_REVIEW",
        "SIC_DERIVED",
        "UNCLASSIFIED",
    }