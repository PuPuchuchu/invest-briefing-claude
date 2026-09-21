"""
Unit + integration tests for src/fundamentals/core_promotion_consistency.py.

Required by the 2026-09-21 ChatGPT design round: cross-check
core_universe_candidates.csv's PROMOTED candidates against
universe_membership.csv's actual, point-in-time-gated CORE+ACTIVE
membership. Minimum cases specified:

    1. PROMOTED + CORE ACTIVE 정상 (clean, no failures)
    2. PROMOTED인데 CORE membership 없음
    3. CORE ACTIVE인데 PROMOTED candidate 없음
    4. membership이 evaluation date 이후에만 available
    5. effective interval 만료
    6. 동일 CIK 중복/모호 membership
    7. 실제 core_universe_candidates.csv + universe_membership.csv
       integration consistency

All covered below. No file I/O in the unit-test section (synthetic
fixtures only); the integration section at the bottom reads the real
committed CSVs, mirroring tests/test_universe_membership_integration.py's
pattern.
"""

from pathlib import Path

import pytest

from src.fundamentals.core_promotion_consistency import (
    SCHEMA_VERSION,
    CORE_UNIVERSE_TYPE,
    PROMOTED_STATUS,
    validate_promotion_consistency,
)
from src.fundamentals.core_universe_io import read_core_universe_candidates_csv
from src.fundamentals.universe_membership_io import read_universe_membership_csv

AVGO_CIK = "0001730168"


def _candidate(**overrides):
    row = {
        "ticker": "AVGO",
        "cik": AVGO_CIK,
        "sector": "Information Technology",
        "industry": "Semiconductors",
        "proposed_peer_group": "Diversified Semiconductor & Infrastructure",
        "classification_rationale": "test fixture",
        "sec_availability": "CONFIRMED",
        "expected_metric_comparability": "test fixture",
        "inclusion_status": "PROMOTED",
        "added_date": "2026-09-20",
        "notes": None,
        "sec_filing_type": "10-K",
        "accounting_basis": "US_GAAP",
        "normalization_status": "SUPPORTED",
        "business_model_status": "CONDITIONAL",
        "business_mix_status": "DIVERSIFIED",
        "segment_reporting_available": "TRUE",
        "metric_eligibility_policy": "DIVERSIFIED_GROUP_PERCENTILE_ONLY",
        "production_eligibility": "ELIGIBLE",
        "eligibility_reason": "test fixture",
    }
    row.update(overrides)
    return row


def _core_membership(**overrides):
    row = {
        "cik": AVGO_CIK,
        "ticker": "AVGO",
        "universe_type": "CORE",
        "membership_status": "ACTIVE",
        "membership_source": "MANUAL_REVIEW",
        "membership_version": "1",
        "effective_from": "2026-09-20",
        "effective_to": None,
        "membership_available_date": "2026-09-21",
        "reviewed_at": "2026-09-20",
        "membership_reason": None,
        "notes": None,
    }
    row.update(overrides)
    return row


# ============================================================
# 1. PROMOTED + CORE ACTIVE 정상
# ============================================================

def test_promoted_with_active_core_membership_is_clean():
    candidates = [_candidate()]
    membership = [_core_membership()]
    assert validate_promotion_consistency(candidates, membership, "2026-09-21") == []


# ============================================================
# 2. PROMOTED인데 CORE membership 없음
# ============================================================

def test_promoted_with_no_core_membership_at_all_fails():
    candidates = [_candidate()]
    membership: list[dict] = []
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert f"cik={AVGO_CIK}.promoted_candidate_missing_active_core_membership" in failures


def test_promoted_with_only_watchlist_membership_fails():
    # A WATCHLIST row for the same CIK must never satisfy the CORE check.
    candidates = [_candidate()]
    membership = [_core_membership(universe_type="WATCHLIST", membership_source="LEGACY_SEED")]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert f"cik={AVGO_CIK}.promoted_candidate_missing_active_core_membership" in failures


def test_promoted_candidate_missing_cik_is_flagged_without_crashing():
    candidates = [_candidate(cik=None)]
    membership: list[dict] = []
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert "candidate_rows[0].promoted_candidate_missing_cik" in failures


# ============================================================
# 3. CORE ACTIVE인데 PROMOTED candidate 없음
# ============================================================

def test_core_active_with_no_candidate_row_at_all_fails():
    candidates: list[dict] = []
    membership = [_core_membership()]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert f"cik={AVGO_CIK}.core_membership_missing_promoted_candidate" in failures


def test_core_active_with_candidate_row_not_promoted_fails():
    candidates = [_candidate(inclusion_status="UNDER_REVIEW")]
    membership = [_core_membership()]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert f"cik={AVGO_CIK}.core_membership_missing_promoted_candidate" in failures


def test_core_active_with_candidate_row_rejected_fails():
    candidates = [_candidate(inclusion_status="REJECTED")]
    membership = [_core_membership()]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert f"cik={AVGO_CIK}.core_membership_missing_promoted_candidate" in failures


# ============================================================
# 4. membership이 evaluation date 이후에만 available
# ============================================================

def test_promoted_with_membership_not_yet_available_fails():
    # effective_from covers the evaluation date, but
    # membership_available_date is still in the future relative to it --
    # this is the exact AVGO NOT_YET_AVAILABLE shape confirmed by
    # ChatGPT's 2026-09-21 message.
    candidates = [_candidate()]
    membership = [_core_membership(effective_from="2026-09-20", membership_available_date="2026-09-21")]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-20")
    assert f"cik={AVGO_CIK}.promoted_candidate_missing_active_core_membership" in failures


def test_promoted_with_membership_available_on_evaluation_date_is_clean():
    candidates = [_candidate()]
    membership = [_core_membership(effective_from="2026-09-20", membership_available_date="2026-09-21")]
    assert validate_promotion_consistency(candidates, membership, "2026-09-21") == []


def test_core_active_not_yet_available_does_not_require_promoted_candidate_yet():
    # Direction 2 only reconciles CIKs whose CORE membership is actually
    # LOOKUP_OK as of evaluation_date -- a not-yet-available membership
    # has nothing to reconcile against yet.
    candidates: list[dict] = []
    membership = [_core_membership(effective_from="2026-09-20", membership_available_date="2026-09-21")]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-20")
    assert not any("core_membership_missing_promoted_candidate" in f for f in failures)


# ============================================================
# 5. effective interval 만료
# ============================================================

def test_promoted_with_expired_core_membership_fails():
    candidates = [_candidate()]
    membership = [_core_membership(effective_from="2024-01-01", effective_to="2025-01-01", membership_available_date="2024-01-01")]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert f"cik={AVGO_CIK}.promoted_candidate_missing_active_core_membership" in failures


def test_core_membership_expired_does_not_require_promoted_candidate():
    candidates: list[dict] = []
    membership = [_core_membership(effective_from="2024-01-01", effective_to="2025-01-01", membership_available_date="2024-01-01")]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert not any("core_membership_missing_promoted_candidate" in f for f in failures)


# ============================================================
# 6. 동일 CIK 중복/모호 membership
# ============================================================

def test_promoted_with_overlapping_core_membership_rows_is_flagged_ambiguous():
    candidates = [_candidate()]
    membership = [
        _core_membership(membership_version="1", effective_from="2024-01-01", effective_to=None, membership_available_date="2024-01-01"),
        _core_membership(membership_version="2", effective_from="2024-06-01", effective_to=None, membership_available_date="2024-06-01"),
    ]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert f"cik={AVGO_CIK}.ambiguous_core_membership_for_promoted_candidate" in failures
    # Ambiguity is reported instead of, not in addition to, the ordinary
    # "missing" failure for the same CIK/direction.
    assert f"cik={AVGO_CIK}.promoted_candidate_missing_active_core_membership" not in failures


def test_overlapping_core_membership_without_candidate_row_is_flagged_ambiguous_not_missing():
    candidates: list[dict] = []
    membership = [
        _core_membership(membership_version="1", effective_from="2024-01-01", effective_to=None, membership_available_date="2024-01-01"),
        _core_membership(membership_version="2", effective_from="2024-06-01", effective_to=None, membership_available_date="2024-06-01"),
    ]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert f"cik={AVGO_CIK}.ambiguous_core_membership_for_promoted_candidate" in failures
    assert failures.count(f"cik={AVGO_CIK}.ambiguous_core_membership_for_promoted_candidate") == 1


# ============================================================
# Multi-candidate: independent CIKs never cross-contaminate
# ============================================================

def test_one_bad_candidate_does_not_affect_a_clean_one():
    clean = _candidate(ticker="AVGO", cik=AVGO_CIK)
    bad = _candidate(ticker="MRVL", cik="0001835632", inclusion_status="PROMOTED")
    candidates = [clean, bad]
    membership = [_core_membership(cik=AVGO_CIK, ticker="AVGO")]
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert not any(f"cik={AVGO_CIK}" in f for f in failures)
    assert any("cik=0001835632" in f for f in failures)


# ============================================================
# Read-only / no mutation
# ============================================================

def test_validate_promotion_consistency_does_not_mutate_inputs():
    candidates = [_candidate()]
    membership = [_core_membership()]
    candidates_copy = [dict(row) for row in candidates]
    membership_copy = [dict(row) for row in membership]
    validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert candidates == candidates_copy
    assert membership == membership_copy


# ============================================================
# Module-level constants
# ============================================================

def test_schema_version_is_a_non_empty_string():
    assert isinstance(SCHEMA_VERSION, str) and SCHEMA_VERSION


def test_core_universe_type_and_promoted_status_are_as_documented():
    assert CORE_UNIVERSE_TYPE == "CORE"
    assert PROMOTED_STATUS == "PROMOTED"


# ============================================================
# 7. 실제 core_universe_candidates.csv + universe_membership.csv
#    integration consistency
# ============================================================

REFERENCE_DIR = Path("data/reference")
CANDIDATES_CSV = REFERENCE_DIR / "core_universe_candidates.csv"
MEMBERSHIP_CSV = REFERENCE_DIR / "universe_membership.csv"


def _require_real_reference_files() -> None:
    if not CANDIDATES_CSV.exists() or not MEMBERSHIP_CSV.exists():
        pytest.skip(
            f"{CANDIDATES_CSV} and/or {MEMBERSHIP_CSV} not present -- run "
            f"scripts/seed_universe_membership.py first."
        )


def test_real_reference_data_is_promotion_consistent_as_of_seed_date():
    _require_real_reference_files()
    candidates = read_core_universe_candidates_csv(CANDIDATES_CSV)
    membership = read_universe_membership_csv(MEMBERSHIP_CSV)
    failures = validate_promotion_consistency(candidates, membership, "2026-09-21")
    assert failures == []


def test_real_reference_data_avgo_is_the_only_promoted_and_only_core_member():
    # Confirms the current real data matches the narrow, intentional
    # state described in the 2026-09-21 report: AVGO is the only
    # PROMOTED candidate and the only CORE+ACTIVE member -- MRVL/CRM/
    # ADBE/PINS/SNAP have classification filled in but were never
    # promoted or given a CORE membership row.
    _require_real_reference_files()
    candidates = read_core_universe_candidates_csv(CANDIDATES_CSV)
    membership = read_universe_membership_csv(MEMBERSHIP_CSV)

    promoted_ciks = {row["cik"] for row in candidates if row["inclusion_status"] == "PROMOTED"}
    core_ciks = {row["cik"] for row in membership if row["universe_type"] == "CORE"}

    assert promoted_ciks == {AVGO_CIK}
    assert core_ciks == {AVGO_CIK}

