"""
Tests for src/fundamentals/company_type_registry.py -- the Company Type
PIT registry and lookup (STEP 14, 2026-10-26; boundary tests added in the
STEP 14 Acceptance Remediation round, also 2026-10-26).

These are deliberately ISOLATED unit tests of get_company_type_as_of()
and the two row/reference-data validators, using small synthetic rows --
not the real ORCL SEC fixture used by tests/test_orchestrator.py. This
keeps Company-Type-level PIT semantics (what this module is actually
responsible for) separate from SEC-filing-level PIT semantics (what
point_in_time.py / the orchestrator's PIT_GATING stage is responsible
for) -- the two were conflated by nothing here; see the STEP 14
Acceptance Remediation report for why 3 pre-existing orchestrator tests
now fail at the orchestrator level once Company Type availability dates
are evidence-based rather than fabricated.

Note on company_type_registry.py's own imports: this module imports
src.fundamentals.point_in_time.coerce_evaluation_date, which in turn
imports src.fundamentals.sec_history (an absolutely protected file under
this project's standing rules). This test file never reads, searches,
compares, or inspects sec_history.py itself -- it only exercises
company_type_registry.py's own, already-existing import of
point_in_time.coerce_evaluation_date, exactly as every other test of
this module's pre-existing sibling modules (peer_classification.py,
universe_membership.py, orchestrator.py) already does.
"""

from __future__ import annotations

import pytest

from src.fundamentals.company_type_registry import (
    MAPPING_SOURCES,
    LOOKUP_INVALID_REFERENCE,
    LOOKUP_MISSING_AVAILABILITY_DATE,
    LOOKUP_NO_MAPPING_FOR_DATE,
    LOOKUP_NOT_YET_AVAILABLE,
    LOOKUP_OK,
    LOOKUP_OVERLAPPING_MAPPING,
    get_company_type_as_of,
    validate_company_type_reference_data,
    validate_company_type_row,
)

CIK = "0001234567"


def _row(**overrides):
    row = {
        "ticker": "TEST",
        "cik": CIK,
        "company_type": "NON_FINANCIAL",
        "effective_from": "2026-09-20",
        "effective_to": None,
        "classification_available_date": "2026-09-20",
        "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE",
        "mapping_rationale": "Synthetic test row.",
        "reviewed_at_utc": "2026-10-26T00:00:00Z",
        "decision_ref": "test",
    }
    row.update(overrides)
    return row


# ============================================================
# PIT BOUNDARY -- availability-date edge (the supported date itself,
# and one day on each side). This is the core semantic STEP 14
# Acceptance Remediation Section 3 requires: before/on/after the
# supported date, with no fabricated sentinel making anything earlier
# "look" available.
# ============================================================

def test_lookup_fails_closed_one_day_before_supported_availability_date():
    rows = [_row()]
    result = get_company_type_as_of(CIK, "2026-09-19", rows)
    assert result["lookup_status"] != LOOKUP_OK
    assert result["mapping"] is None


def test_lookup_ok_exactly_on_supported_availability_date():
    rows = [_row()]
    result = get_company_type_as_of(CIK, "2026-09-20", rows)
    assert result["lookup_status"] == LOOKUP_OK
    assert result["mapping"]["company_type"] == "NON_FINANCIAL"


def test_lookup_ok_one_day_after_supported_availability_date():
    rows = [_row()]
    result = get_company_type_as_of(CIK, "2026-09-21", rows)
    assert result["lookup_status"] == LOOKUP_OK


def test_no_fabricated_historical_availability_far_before_supported_date():
    """The exact defect this remediation round fixes: a date decades
    before the real supported date (e.g. the withdrawn 1980-01-01
    sentinel, or any other early historical date) must NOT resolve to
    OK just because the classification is later known to be true. This
    directly re-asserts the old bug is gone, not just the new happy
    path."""
    rows = [_row()]
    for far_past_date in ("1980-01-01", "2000-01-01", "2020-01-01"):
        result = get_company_type_as_of(CIK, far_past_date, rows)
        assert result["lookup_status"] != LOOKUP_OK, (
            f"{far_past_date} must not resolve OK -- no evidence "
            f"supports availability that far back."
        )


# ============================================================
# EFFECTIVE-DATE BOUNDARY -- separate from classification_available_date.
# A row can be business-effective starting on one date but not become
# AVAILABLE (knowable to the system) until a later date -- the exact
# distinction STEP 14 Section 2.1 draws. Constructed here with
# effective_from BEFORE classification_available_date specifically so
# the two gates are tested independently (the real company_type.csv
# currently sets them equal, which is why this distinction needs its
# own synthetic fixture to exercise at all).
# ============================================================

def test_business_effective_but_not_yet_available_fails_closed_as_not_yet_available():
    rows = [_row(effective_from="2020-01-01", classification_available_date="2026-09-20")]
    # 2024-06-01 is inside the effective window (>= 2020-01-01) but
    # before the classification_available_date -- must be
    # LOOKUP_NOT_YET_AVAILABLE specifically (not NO_MAPPING_FOR_DATE),
    # since a covering row genuinely exists.
    result = get_company_type_as_of(CIK, "2024-06-01", rows)
    assert result["lookup_status"] == LOOKUP_NOT_YET_AVAILABLE
    assert result["mapping"] is not None  # the row was found, just not usable yet


def test_evaluation_date_before_effective_from_is_no_mapping_for_date():
    rows = [_row(effective_from="2026-09-20", classification_available_date="2020-01-01")]
    # Here availability predates effectiveness (synthetic, to isolate
    # the OTHER gate) -- an evaluation_date before effective_from must
    # fail as NO_MAPPING_FOR_DATE, not NOT_YET_AVAILABLE, since no
    # covering row exists at all for that date.
    result = get_company_type_as_of(CIK, "2020-06-01", rows)
    assert result["lookup_status"] == LOOKUP_NO_MAPPING_FOR_DATE


def test_evaluation_date_at_effective_to_boundary_is_exclusive():
    rows = [_row(effective_from="2020-01-01", effective_to="2024-01-01",
                  classification_available_date="2020-01-01")]
    on_boundary = get_company_type_as_of(CIK, "2024-01-01", rows)
    just_before = get_company_type_as_of(CIK, "2023-12-31", rows)
    assert on_boundary["lookup_status"] != LOOKUP_OK  # [start, end) -- end is exclusive
    assert just_before["lookup_status"] == LOOKUP_OK


# ============================================================
# MISSING / UNSUPPORTED AVAILABILITY DATES
# ============================================================

def test_missing_classification_available_date_fails_closed():
    rows = [_row(classification_available_date=None)]
    result = get_company_type_as_of(CIK, "2026-09-20", rows)
    assert result["lookup_status"] == LOOKUP_MISSING_AVAILABILITY_DATE


def test_unparseable_classification_available_date_fails_closed():
    """An unparseable (but non-empty) classification_available_date is
    caught earlier, by validate_company_type_row()'s own structural
    check ("classification_available_date_unparseable") -- the row
    never reaches get_company_type_as_of()'s covering-window logic at
    all, so this resolves as LOOKUP_INVALID_REFERENCE (no structurally
    valid row exists for this cik), not LOOKUP_MISSING_AVAILABILITY_DATE
    (which is reserved for a structurally-valid row whose availability
    date is simply absent -- see the next test). Either way: fails
    closed, never silently treated as available."""
    rows = [_row(classification_available_date="not-a-date")]
    result = get_company_type_as_of(CIK, "2026-09-20", rows)
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE
    assert "classification_available_date_unparseable" in validate_company_type_row(rows[0])


def test_missing_classification_available_date_row_is_structurally_invalid():
    """validate_company_type_row() itself must also flag an empty
    mapping_rationale/mapping_source/reviewed_at_utc as invalid (checked
    separately from the availability-date gate above, since a
    structurally invalid row is rejected before the PIT gate even runs
    -- see validate_company_type_row())."""
    row = _row(mapping_rationale="")
    assert "mapping_rationale" in validate_company_type_row(row)


# ============================================================
# UNREGISTERED CIK -- no row at all (the real BLK/UNH/JPM situation)
# ============================================================

def test_unregistered_cik_is_no_mapping_for_date_never_a_default():
    result = get_company_type_as_of("0009999999", "2026-09-20", [])
    assert result["lookup_status"] == LOOKUP_NO_MAPPING_FOR_DATE
    assert result["mapping"] is None


# ============================================================
# OVERLAPPING / AMBIGUOUS REFERENCE DATA
# ============================================================

def test_two_overlapping_rows_for_same_cik_fail_closed_as_overlapping_mapping():
    rows = [_row(), _row(decision_ref="duplicate")]
    result = get_company_type_as_of(CIK, "2026-09-25", rows)
    assert result["lookup_status"] == LOOKUP_OVERLAPPING_MAPPING


def test_reference_data_validator_detects_the_same_overlap():
    rows = [_row(), _row(decision_ref="duplicate")]
    overlaps = validate_company_type_reference_data(rows)
    assert CIK in overlaps


def test_all_structurally_invalid_rows_for_a_cik_is_invalid_reference():
    rows = [_row(company_type="NOT_A_REAL_VALUE")]
    result = get_company_type_as_of(CIK, "2026-09-20", rows)
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE


# ============================================================
# mapping_source ALLOW-LIST (STEP 14F-R3, Decision Log DEC-022).
# Exactly one permitted value; the former SIC-only value and any
# unrecognized value are rejected by validate_company_type_row(), and a
# row rejected that way fails closed in get_company_type_as_of() through
# the existing LOOKUP_INVALID_REFERENCE path (no new status string).
# ============================================================

def test_mapping_sources_allow_list_is_exactly_the_10k_evidence_value():
    assert MAPPING_SOURCES == frozenset({"SEC_10K_BUSINESS_MODEL_EVIDENCE"})


def test_sec_10k_business_model_evidence_source_is_accepted():
    row = _row(mapping_source="SEC_10K_BUSINESS_MODEL_EVIDENCE")
    assert validate_company_type_row(row) == []
    result = get_company_type_as_of(CIK, "2026-09-20", [row])
    assert result["lookup_status"] == LOOKUP_OK


def test_former_sic_only_source_is_rejected():
    """Decision B: SIC code alone is not sufficient evidence for a
    Company Type assignment, so the former value must not validate."""
    row = _row(mapping_source="SIC_CODE_UNAMBIGUOUS_NON_FINANCIAL_DIVISION")
    assert "mapping_source" in validate_company_type_row(row)
    result = get_company_type_as_of(CIK, "2026-09-20", [row])
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE
    assert result["mapping"] is None


@pytest.mark.parametrize(
    "unknown_source",
    ["MANUAL_REVIEW", "SEC_SIC", "sec_10k_business_model_evidence", "UNKNOWN"],
)
def test_unrecognized_non_empty_source_is_rejected(unknown_source):
    row = _row(mapping_source=unknown_source)
    assert "mapping_source" in validate_company_type_row(row)
    result = get_company_type_as_of(CIK, "2026-09-20", [row])
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE


@pytest.mark.parametrize("missing_source", ["", None])
def test_empty_or_missing_source_remains_invalid(missing_source):
    row = _row(mapping_source=missing_source)
    assert "mapping_source" in validate_company_type_row(row)
    row_without_key = _row()
    del row_without_key["mapping_source"]
    assert "mapping_source" in validate_company_type_row(row_without_key)
    result = get_company_type_as_of(CIK, "2026-09-20", [row])
    assert result["lookup_status"] == LOOKUP_INVALID_REFERENCE
