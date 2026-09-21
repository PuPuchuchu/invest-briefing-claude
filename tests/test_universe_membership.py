"""
Unit tests for src/fundamentals/universe_membership.py.

Pure Unit Tests in the taxonomy sense: no network, no file I/O, synthetic
fixtures only. These exist specifically to prove the properties required by
the 2026-09-21 ChatGPT design round (see
src/fundamentals/universe_membership.py's own module docstring):

    1. membership_status is validated as ACTIVE-only (v1) -- no
       PENDING_REVIEW/REMOVED state exists on this row at all.
    2. universe_type / membership_source are validated as their own enums.
    3. Overlap and membership_version-uniqueness validation are BOTH scoped
       to (cik, universe_type), never to cik alone -- WATCHLIST and CORE
       membership for the same CIK are expected to coexist (e.g. AVGO).
    4. ticker is cross-validated against security_identity.csv's (ticker,
       cik) pairs -- never trusted as a standalone key.
    5. get_universe_membership_as_of() returns a rich (lookup_status,
       membership, reason) result distinguishing all failure modes.
    6. effective_to is a half-open interval boundary: [effective_from,
       effective_to) -- effective_to itself already belongs to whatever
       comes next.
    7. CIK existence is checked (Stage 1: internal reference integrity).
    8. AVGO's dual-membership fixture (WATCHLIST+ACTIVE and CORE+ACTIVE,
       same CIK) is explicitly exercised as a first-class case, not just
       theoretically allowed.

These 17 named minimum cases from the 2026-09-21 ChatGPT message are all
covered below: valid row / invalid CIK / invalid universe_type / invalid
membership_status / invalid membership_source / invalid dates / effective
interval overlap / WATCHLIST+CORE simultaneous coexistence allowed /
membership_version uniqueness / ticker<->CIK cross-validation /
membership_available_date look-ahead prevention / PIT interval lookup /
pre-availability lookup / post-expiry lookup / ACTIVE-only lookup success /
AVGO dual-membership fixture / legacy seed provenance handling.
"""

import pytest

from src.fundamentals.universe_membership import (
    SCHEMA_VERSION,
    UNIVERSE_TYPES,
    MEMBERSHIP_STATUSES,
    MEMBERSHIP_SOURCES,
    LOOKUP_OK,
    LOOKUP_NO_MEMBERSHIP_FOR_DATE,
    LOOKUP_MISSING_AVAILABILITY_DATE,
    LOOKUP_NOT_YET_AVAILABLE,
    LOOKUP_OVERLAPPING_MEMBERSHIP,
    LOOKUP_INVALID_REFERENCE,
    validate_universe_membership_row,
    validate_universe_membership_data,
    get_universe_membership_as_of,
)


def _active_row(**overrides):
    row = {
        "cik": "0001730168",
        "ticker": "AVGO",
        "universe_type": "WATCHLIST",
        "membership_status": "ACTIVE",
        "membership_source": "LEGACY_SEED",
        "membership_version": "1",
        "effective_from": "2026-09-21",
        "effective_to": None,
        "membership_available_date": "2026-09-21",
        "reviewed_at": None,
        "membership_reason": None,
        "notes": None,
    }
    row.update(overrides)
    return row


def _issuer_rows():
    return [{"cik": "0001730168", "issuer_name": "Broadcom Inc."}]


def _security_rows():
    return [{"security_id": "sec-1", "ticker": "AVGO", "cik": "0001730168"}]


# ============================================================
# validate_universe_membership_row -- own-row schema (case: valid row)
# ============================================================

def test_validate_universe_membership_row_accepts_valid_active_row():
    assert validate_universe_membership_row(_active_row()) == []


def test_validate_universe_membership_row_rejects_non_dict():
    assert validate_universe_membership_row(None) == ["root"]


def test_validate_universe_membership_row_rejects_missing_required_keys():
    failures = validate_universe_membership_row({})
    for key in (
        "cik",
        "ticker",
        "universe_type",
        "membership_status",
        "membership_source",
        "membership_version",
        "effective_from",
        "membership_available_date",
    ):
        assert key in failures


# ---- case: invalid universe_type ----

def test_validate_universe_membership_row_rejects_bad_universe_type():
    row = _active_row(universe_type="MADE_UP_UNIVERSE")
    assert "universe_type" in validate_universe_membership_row(row)


# ---- case: invalid membership_status (v1: ACTIVE-only) ----

def test_validate_universe_membership_row_rejects_bad_membership_status():
    row = _active_row(membership_status="PENDING_REVIEW")
    assert "membership_status" in validate_universe_membership_row(row)


def test_membership_statuses_is_active_only_v1():
    # 2026-09-21 decision: no PENDING_REVIEW/REMOVED state on this row --
    # see module docstring.
    assert MEMBERSHIP_STATUSES == frozenset({"ACTIVE"})


# ---- case: invalid membership_source ----

def test_validate_universe_membership_row_rejects_bad_membership_source():
    row = _active_row(membership_source="MADE_UP_SOURCE")
    assert "membership_source" in validate_universe_membership_row(row)


def test_membership_sources_are_as_documented():
    assert MEMBERSHIP_SOURCES == frozenset({"LEGACY_SEED", "MANUAL_REVIEW"})


def test_universe_types_are_as_documented():
    assert UNIVERSE_TYPES == frozenset({"WATCHLIST", "CORE"})


# ---- case: invalid dates ----

def test_validate_universe_membership_row_rejects_unparseable_effective_from():
    row = _active_row(effective_from="not-a-date")
    assert "effective_from_format" in validate_universe_membership_row(row)


def test_validate_universe_membership_row_rejects_unparseable_effective_to():
    row = _active_row(effective_to="not-a-date")
    assert "effective_to_format" in validate_universe_membership_row(row)


def test_validate_universe_membership_row_rejects_effective_from_after_effective_to():
    row = _active_row(effective_from="2026-06-01", effective_to="2026-01-01")
    assert "effective_from_after_effective_to" in validate_universe_membership_row(row)


def test_validate_universe_membership_row_rejects_unparseable_availability_date():
    row = _active_row(membership_available_date="not-a-date")
    assert "membership_available_date_format" in validate_universe_membership_row(row)


def test_validate_universe_membership_row_rejects_unparseable_reviewed_at():
    row = _active_row(reviewed_at="not-a-date")
    assert "reviewed_at_format" in validate_universe_membership_row(row)


def test_validate_universe_membership_row_does_not_require_reviewed_at():
    row = _active_row(reviewed_at=None)
    assert validate_universe_membership_row(row) == []


# ============================================================
# validate_universe_membership_data -- cross-row/cross-file checks
# ============================================================

def test_validate_universe_membership_data_accepts_clean_set():
    rows = [_active_row()]
    assert validate_universe_membership_data(_issuer_rows(), _security_rows(), rows) == []


# ---- case: invalid CIK ----

def test_validate_universe_membership_data_flags_unknown_cik():
    row = _active_row(cik="9999999999")
    failures = validate_universe_membership_data(_issuer_rows(), _security_rows(), [row])
    assert any("unknown_cik" in f for f in failures)


# ---- case: ticker <-> CIK cross-validation ----

def test_validate_universe_membership_data_flags_ticker_not_linked_to_cik():
    row = _active_row(ticker="NOTREAL")
    failures = validate_universe_membership_data(_issuer_rows(), _security_rows(), [row])
    assert any("ticker_not_linked_to_cik" in f for f in failures)


def test_validate_universe_membership_data_allows_ticker_linked_via_security_rows():
    row = _active_row(ticker="AVGO")
    failures = validate_universe_membership_data(_issuer_rows(), _security_rows(), [row])
    assert not any("ticker_not_linked_to_cik" in f for f in failures)


def test_validate_universe_membership_data_does_not_assume_1to1_cik_ticker():
    # A CIK legitimately associated with more than one ticker in
    # security_identity.csv must not trip the cross-check for either.
    security_rows = [
        {"security_id": "s1", "ticker": "AVGO", "cik": "0001730168"},
        {"security_id": "s2", "ticker": "AVGOP", "cik": "0001730168"},
    ]
    row = _active_row(ticker="AVGOP")
    failures = validate_universe_membership_data(_issuer_rows(), security_rows, [row])
    assert not any("ticker_not_linked_to_cik" in f for f in failures)


# ---- case: effective interval overlap (scoped to (cik, universe_type)) ----

def test_validate_universe_membership_data_flags_overlapping_effective_ranges_same_universe_type():
    row_a = _active_row(
        membership_version="1", effective_from="2024-01-01", effective_to="2025-06-01"
    )
    row_b = _active_row(
        membership_version="2", effective_from="2025-01-01", effective_to=None
    )
    failures = validate_universe_membership_data(_issuer_rows(), _security_rows(), [row_a, row_b])
    assert any("overlaps_membership_rows" in f for f in failures)


def test_validate_universe_membership_data_allows_adjacent_non_overlapping_ranges():
    row_a = _active_row(
        membership_version="1", effective_from="2024-01-01", effective_to="2025-01-01"
    )
    row_b = _active_row(
        membership_version="2", effective_from="2025-01-01", effective_to=None
    )
    failures = validate_universe_membership_data(_issuer_rows(), _security_rows(), [row_a, row_b])
    assert not any("overlaps_membership_rows" in f for f in failures)


# ---- case: WATCHLIST + CORE simultaneous coexistence allowed ----

def test_validate_universe_membership_data_allows_watchlist_and_core_overlap_same_cik():
    # Same CIK, same effective window, but DIFFERENT universe_type --
    # this is expected, not an overlap (AVGO's real-world fixture shape).
    watchlist_row = _active_row(
        universe_type="WATCHLIST",
        membership_version="1",
        membership_source="LEGACY_SEED",
        effective_from="2026-09-21",
        effective_to=None,
    )
    core_row = _active_row(
        universe_type="CORE",
        membership_version="1",
        membership_source="MANUAL_REVIEW",
        effective_from="2026-09-21",
        effective_to=None,
    )
    failures = validate_universe_membership_data(
        _issuer_rows(), _security_rows(), [watchlist_row, core_row]
    )
    assert failures == []


# ---- case: membership_version uniqueness (scoped to (cik, universe_type)) ----

def test_validate_universe_membership_data_flags_duplicate_membership_version_within_cik_and_type():
    row_a = _active_row(effective_from="2024-01-01", effective_to="2025-01-01")
    row_b = _active_row(effective_from="2025-01-01", effective_to=None)  # same membership_version "1"
    failures = validate_universe_membership_data(_issuer_rows(), _security_rows(), [row_a, row_b])
    assert any("duplicate_membership_version" in f for f in failures)


def test_validate_universe_membership_data_allows_same_version_across_different_universe_types():
    watchlist_row = _active_row(universe_type="WATCHLIST", membership_version="1")
    core_row = _active_row(universe_type="CORE", membership_version="1")
    failures = validate_universe_membership_data(
        _issuer_rows(), _security_rows(), [watchlist_row, core_row]
    )
    assert not any("duplicate_membership_version" in f for f in failures)


def test_validate_universe_membership_data_allows_same_version_across_different_ciks():
    row_a = _active_row(cik="0001730168")
    row_b = _active_row(cik="0000320193")
    issuer_rows = [
        {"cik": "0001730168", "issuer_name": "Broadcom Inc."},
        {"cik": "0000320193", "issuer_name": "Apple Inc."},
    ]
    security_rows = [
        {"security_id": "s1", "ticker": "AVGO", "cik": "0001730168"},
        {"security_id": "s2", "ticker": "AVGO", "cik": "0000320193"},
    ]
    failures = validate_universe_membership_data(issuer_rows, security_rows, [row_a, row_b])
    assert not any("duplicate_membership_version" in f for f in failures)


def test_validate_universe_membership_data_propagates_row_level_failures_with_index_prefix():
    bad_row = _active_row(membership_status="MADE_UP_STATUS")
    failures = validate_universe_membership_data(_issuer_rows(), _security_rows(), [bad_row])
    assert "membership_rows[0].membership_status" in failures


# ============================================================
# get_universe_membership_as_of -- all lookup_status outcomes
# ============================================================

# ---- case: PIT interval lookup (basic OK) ----

def test_get_universe_membership_as_of_ok():
    row = _active_row(effective_from="2025-01-01", effective_to=None, membership_available_date="2025-01-05")
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_OK
    assert result["membership"] == row
    assert result["reason"] is None


def test_get_universe_membership_as_of_no_rows_at_all():
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-06-01", [])
    assert result["lookup_status"] == LOOKUP_NO_MEMBERSHIP_FOR_DATE
    assert result["membership"] is None


def test_get_universe_membership_as_of_no_row_covers_evaluation_date():
    row = _active_row(effective_from="2025-01-01", effective_to="2025-03-01")
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_NO_MEMBERSHIP_FOR_DATE


def test_get_universe_membership_as_of_evaluation_date_before_effective_from_is_no_membership():
    row = _active_row(effective_from="2025-01-01", effective_to=None)
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2024-12-31", [row])
    assert result["lookup_status"] == LOOKUP_NO_MEMBERSHIP_FOR_DATE


def test_get_universe_membership_as_of_filters_by_universe_type_not_just_cik():
    # A CORE row for this CIK must not satisfy a WATCHLIST lookup.
    row = _active_row(universe_type="CORE", effective_from="2025-01-01", effective_to=None)
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_NO_MEMBERSHIP_FOR_DATE


def test_get_universe_membership_as_of_invalid_reference_when_all_rows_structurally_bad():
    bad_row = _active_row(membership_status="MADE_UP_STATUS")
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-06-01", [bad_row])
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE


def test_get_universe_membership_as_of_overlapping_membership_when_two_rows_both_cover_date():
    row_a = _active_row(membership_version="1", effective_from="2024-01-01", effective_to=None)
    row_b = _active_row(membership_version="2", effective_from="2024-06-01", effective_to=None)
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-01-01", [row_a, row_b])
    assert result["lookup_status"] == LOOKUP_OVERLAPPING_MEMBERSHIP


# ---- case: membership_available_date look-ahead prevention / pre-availability lookup ----

def test_get_universe_membership_as_of_not_yet_available():
    row = _active_row(effective_from="2025-01-01", effective_to=None, membership_available_date="2025-06-15")
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_NOT_YET_AVAILABLE
    assert result["membership"] == row


def test_get_universe_membership_as_of_availability_date_exactly_on_evaluation_date_is_ok():
    row = _active_row(effective_from="2025-01-01", effective_to=None, membership_available_date="2025-06-01")
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_OK


def test_get_universe_membership_as_of_retroactive_use_prevention():
    # Legacy-seed row: effective period covers a PAST date, but was only
    # made available (migrated) LATER -- must never be usable for that
    # past date (core anti-look-ahead-bias guarantee).
    row = _active_row(
        membership_source="LEGACY_SEED",
        effective_from="2020-01-01",
        effective_to=None,
        membership_available_date="2026-09-21",
    )
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2021-01-01", [row])
    assert result["lookup_status"] == LOOKUP_NOT_YET_AVAILABLE


# ---- case: post-expiry lookup ----

def test_get_universe_membership_as_of_evaluation_date_exactly_at_effective_to_is_not_covered():
    # effective_to is EXCLUSIVE -- a row with effective_to="2025-01-01"
    # does not cover 2025-01-01 itself (membership already ended by then).
    row = _active_row(effective_from="2024-01-01", effective_to="2025-01-01", membership_available_date="2024-01-01")
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-01-01", [row])
    assert result["lookup_status"] == LOOKUP_NO_MEMBERSHIP_FOR_DATE


def test_get_universe_membership_as_of_evaluation_date_one_day_before_effective_to_is_covered():
    row = _active_row(effective_from="2024-01-01", effective_to="2025-01-01", membership_available_date="2024-01-01")
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2024-12-31", [row])
    assert result["lookup_status"] == LOOKUP_OK


def test_get_universe_membership_as_of_lookup_after_effective_to_finds_nothing_when_no_successor_row():
    row = _active_row(effective_from="2024-01-01", effective_to="2025-01-01", membership_available_date="2024-01-01")
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2026-01-01", [row])
    assert result["lookup_status"] == LOOKUP_NO_MEMBERSHIP_FOR_DATE


# ---- case: only ACTIVE membership lookups succeed ----

def test_get_universe_membership_as_of_missing_availability_date():
    row = _active_row(membership_available_date=None)
    # membership_available_date is structurally required, so a row missing
    # it fails validate_universe_membership_row entirely and never reaches
    # the lookup's own MISSING_AVAILABILITY_DATE branch -- mirrors
    # peer_classification's REVIEWED-row-missing-availability-date case.
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE


def test_get_universe_membership_as_of_unparseable_availability_date():
    row = _active_row(membership_available_date="not-a-date")
    result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2025-06-01", [row])
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE


def test_get_universe_membership_as_of_uses_coerce_evaluation_date_and_rejects_garbage():
    row = _active_row()
    with pytest.raises(ValueError):
        get_universe_membership_as_of("0001730168", "WATCHLIST", "not-a-date", [row])


# ============================================================
# AVGO dual-membership fixture (case: AVGO dual-membership fixture)
# ============================================================

def test_avgo_dual_membership_watchlist_and_core_both_lookup_ok_independently():
    watchlist_row = _active_row(
        universe_type="WATCHLIST",
        membership_source="LEGACY_SEED",
        membership_version="1",
        effective_from="2026-09-21",
        effective_to=None,
        membership_available_date="2026-09-21",
    )
    core_row = _active_row(
        universe_type="CORE",
        membership_source="MANUAL_REVIEW",
        membership_version="1",
        effective_from="2026-09-21",
        effective_to=None,
        membership_available_date="2026-09-21",
    )
    rows = [watchlist_row, core_row]

    assert validate_universe_membership_data(_issuer_rows(), _security_rows(), rows) == []

    watchlist_result = get_universe_membership_as_of("0001730168", "WATCHLIST", "2026-09-22", rows)
    core_result = get_universe_membership_as_of("0001730168", "CORE", "2026-09-22", rows)

    assert watchlist_result["lookup_status"] == LOOKUP_OK
    assert watchlist_result["membership"] == watchlist_row
    assert core_result["lookup_status"] == LOOKUP_OK
    assert core_result["membership"] == core_row


# ============================================================
# legacy seed provenance handling (case: legacy seed provenance handling)
# ============================================================

def test_legacy_seed_row_is_structurally_valid_with_migration_date_as_effective_from():
    # membership_source=LEGACY_SEED carries no special validation exemption
    # -- it is provenance, not a status (see module docstring) -- but it
    # must still validate cleanly when effective_from is set to the actual
    # migration date, not backdated.
    row = _active_row(membership_source="LEGACY_SEED", effective_from="2026-09-21", membership_available_date="2026-09-21")
    assert validate_universe_membership_row(row) == []


def test_legacy_seed_and_manual_review_are_both_valid_sources():
    for source in ("LEGACY_SEED", "MANUAL_REVIEW"):
        row = _active_row(membership_source=source)
        assert validate_universe_membership_row(row) == []


# ============================================================
# Module-level constants
# ============================================================

def test_schema_version_is_a_non_empty_string():
    assert isinstance(SCHEMA_VERSION, str) and SCHEMA_VERSION

