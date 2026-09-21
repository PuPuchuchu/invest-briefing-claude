"""
Regression tests for src/fundamentals/core_universe_io.py
(Framework v2.0 Step 10B schema, 2026-09-20 ChatGPT design review;
 extended 2026-09-20 for the SAP/IFRS + AVGO/business-mix SEC
 verification metadata fields).
"""

from __future__ import annotations

from src.fundamentals.core_universe_io import (
    CORE_UNIVERSE_FIELDNAMES,
    read_core_universe_candidates_csv,
    validate_core_universe_row,
    write_core_universe_candidates_csv,
)


def _proposed_row(**overrides):
    row = {
        "ticker": "QCOM",
        "cik": None,
        "sector": "Information Technology",
        "industry": "Semiconductors",
        "proposed_peer_group": "AI & Compute Semiconductors",
        "classification_rationale": "Fabless semiconductor, comparable cost structure.",
        "sec_availability": "UNVERIFIED",
        "expected_metric_comparability": "Revenue/margin scale differs; still same business model.",
        "inclusion_status": "CANDIDATE_PROPOSED",
        "added_date": "2026-09-20",
        "notes": None,
        # -- SEC verification metadata (2026-09-20 SAP/IFRS + AVGO policy round) --
        "sec_filing_type": None,
        "accounting_basis": None,
        "normalization_status": None,
        "business_model_status": None,
        "business_mix_status": None,
        "segment_reporting_available": None,
        "metric_eligibility_policy": None,
        "production_eligibility": None,
        "eligibility_reason": None,
    }
    row.update(overrides)
    return row


# ============================================================
# Round-trip I/O
# ============================================================

def test_round_trip_write_then_read(tmp_path):
    rows = [
        _proposed_row(),
        _proposed_row(ticker="AVGO", proposed_peer_group="IDM & Foundry Semiconductors"),
    ]
    path = tmp_path / "core_universe_candidates.csv"
    write_core_universe_candidates_csv(rows, path)

    read_back = read_core_universe_candidates_csv(path)

    assert len(read_back) == 2
    assert read_back[0]["ticker"] == "QCOM"
    assert read_back[0]["cik"] is None  # empty string round-trips to None
    assert read_back[1]["ticker"] == "AVGO"


def test_write_creates_header_matching_fieldnames(tmp_path):
    path = tmp_path / "core_universe_candidates.csv"
    write_core_universe_candidates_csv([], path)

    with path.open() as f:
        header_line = f.readline().strip()

    assert header_line.split(",") == CORE_UNIVERSE_FIELDNAMES


def test_missing_values_never_fabricated_as_empty_string_not_zero(tmp_path):
    row = _proposed_row(cik=None, notes=None)
    path = tmp_path / "core_universe_candidates.csv"
    write_core_universe_candidates_csv([row], path)

    read_back = read_core_universe_candidates_csv(path)[0]
    assert read_back["cik"] is None
    assert read_back["notes"] is None


def test_sec_verification_metadata_round_trips(tmp_path):
    row = _proposed_row(
        ticker="AVGO",
        cik="0001730168",
        sec_availability="CONFIRMED",
        inclusion_status="PROMOTED",
        sec_filing_type="10-K",
        accounting_basis="US_GAAP",
        normalization_status="SUPPORTED",
        business_model_status="CONDITIONAL",
        business_mix_status="DIVERSIFIED",
        segment_reporting_available="TRUE",
        metric_eligibility_policy="DIVERSIFIED_GROUP_PERCENTILE_ONLY",
        production_eligibility="ELIGIBLE",
        eligibility_reason=None,
    )
    path = tmp_path / "core_universe_candidates.csv"
    write_core_universe_candidates_csv([row], path)

    read_back = read_core_universe_candidates_csv(path)[0]
    assert read_back["sec_filing_type"] == "10-K"
    assert read_back["accounting_basis"] == "US_GAAP"
    assert read_back["business_mix_status"] == "DIVERSIFIED"
    assert read_back["segment_reporting_available"] == "TRUE"
    assert read_back["metric_eligibility_policy"] == "DIVERSIFIED_GROUP_PERCENTILE_ONLY"
    assert read_back["production_eligibility"] == "ELIGIBLE"


# ============================================================
# validate_core_universe_row
# ============================================================

def test_valid_proposed_row_passes():
    assert validate_core_universe_row(_proposed_row()) == []


def test_valid_verified_row_passes():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
    )
    assert validate_core_universe_row(row) == []


def test_valid_promoted_row_passes():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="PROMOTED",
    )
    assert validate_core_universe_row(row) == []


def test_missing_ticker_fails():
    row = _proposed_row(ticker=None)
    assert "ticker" in validate_core_universe_row(row)


def test_unknown_inclusion_status_fails():
    row = _proposed_row(inclusion_status="MAYBE_LATER")
    assert "inclusion_status" in validate_core_universe_row(row)


def test_unknown_sec_availability_fails():
    row = _proposed_row(sec_availability="PROBABLY")
    assert "sec_availability" in validate_core_universe_row(row)


def test_cik_present_without_confirmed_availability_fails():
    # A hardcoded/guessed CIK sitting next to UNVERIFIED must be rejected
    # -- this is exactly the "never fabricate SEC data" rule.
    row = _proposed_row(cik="0000999999", sec_availability="UNVERIFIED")
    assert "cik_present_without_confirmed_sec_availability" in validate_core_universe_row(row)


def test_confirmed_availability_without_cik_fails():
    row = _proposed_row(cik=None, sec_availability="CONFIRMED")
    failures = validate_core_universe_row(row)
    assert "cik_missing_for_confirmed_sec_availability" in failures


def test_verified_status_without_cik_fails():
    row = _proposed_row(
        cik=None,
        sec_availability="UNVERIFIED",
        inclusion_status="CANDIDATE_VERIFIED",
    )
    failures = validate_core_universe_row(row)
    # Wrong status/availability combination AND missing CIK both flagged.
    assert "inclusion_status_sec_availability_combination" in failures


def test_rejected_row_may_have_any_availability():
    for availability in ("UNVERIFIED", "CONFIRMED", "NOT_FOUND"):
        row = _proposed_row(
            inclusion_status="REJECTED",
            sec_availability=availability,
            cik="0000123456" if availability == "CONFIRMED" else None,
        )
        assert validate_core_universe_row(row) == [], (
            f"REJECTED with sec_availability={availability!r} should be valid"
        )


def test_not_found_status_has_no_cik():
    row = _proposed_row(sec_availability="NOT_FOUND")
    assert validate_core_universe_row(row) == []


# ============================================================
# SEC verification metadata (2026-09-20 SAP/IFRS + AVGO policy round)
# ============================================================

def test_valid_confirmed_row_with_full_sec_metadata_passes():
    # The AVGO case: DIVERSIFIED business mix is only CONDITIONAL-
    # comparable to a pure-play peer group, but ELIGIBLE once properly
    # scoped to a dedicated diversified peer group.
    row = _proposed_row(
        ticker="AVGO",
        cik="0001730168",
        sec_availability="CONFIRMED",
        inclusion_status="PROMOTED",
        sec_filing_type="10-K",
        accounting_basis="US_GAAP",
        normalization_status="SUPPORTED",
        business_model_status="CONDITIONAL",
        business_mix_status="DIVERSIFIED",
        segment_reporting_available="TRUE",
        metric_eligibility_policy="DIVERSIFIED_GROUP_PERCENTILE_ONLY",
        production_eligibility="ELIGIBLE",
    )
    assert validate_core_universe_row(row) == []


def test_valid_pure_play_comparable_eligible_row_passes():
    # The NVDA-style case ChatGPT gave: PURE_PLAY + COMPARABLE.
    row = _proposed_row(
        ticker="MRVL",
        cik="0001835632",
        sec_availability="CONFIRMED",
        inclusion_status="PROMOTED",
        sec_filing_type="10-K",
        accounting_basis="US_GAAP",
        normalization_status="SUPPORTED",
        business_model_status="COMPARABLE",
        business_mix_status="PURE_PLAY",
        segment_reporting_available="FALSE",
        metric_eligibility_policy="STANDARD_PURE_PLAY_PERCENTILE",
        production_eligibility="ELIGIBLE",
    )
    assert validate_core_universe_row(row) == []


def test_valid_ifrs_unsupported_not_eligible_row_passes():
    # The SAP case: SEC-verified, but IFRS + current US-GAAP-only
    # normalizer => UNSUPPORTED => NOT_ELIGIBLE, with a reason recorded.
    row = _proposed_row(
        ticker="SAP",
        cik="0001000184",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        sec_filing_type="20-F",
        accounting_basis="IFRS",
        normalization_status="UNSUPPORTED",
        business_model_status="UNVERIFIED",
        business_mix_status="PURE_PLAY",
        segment_reporting_available="UNVERIFIED",
        metric_eligibility_policy="UNSUPPORTED_ACCOUNTING_BASIS",
        production_eligibility="NOT_ELIGIBLE",
        eligibility_reason="IFRS filer; current normalizer is US-GAAP-only.",
    )
    assert validate_core_universe_row(row) == []


# ============================================================
# ELIGIBLE preconditions / cross-field non-duplication
# (2026-09-21 ChatGPT review: business_mix_status / business_model_
#  status / normalization_status / metric_eligibility_policy /
#  production_eligibility must not carry overlapping/contradictory
#  meanings, and ELIGIBLE requires every precondition satisfied.)
# ============================================================

def test_unknown_business_model_status_fails():
    row = _proposed_row(business_model_status="OPERATING_COMPANY")
    assert "business_model_status" in validate_core_universe_row(row)


def test_eligible_requires_supported_normalization():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        normalization_status="UNVERIFIED",
        business_model_status="COMPARABLE",
        business_mix_status="PURE_PLAY",
        metric_eligibility_policy="STANDARD_PURE_PLAY_PERCENTILE",
        production_eligibility="ELIGIBLE",
    )
    assert "production_eligible_requires_supported_normalization" in validate_core_universe_row(row)


def test_eligible_requires_comparable_or_conditional_business_model():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        normalization_status="SUPPORTED",
        business_model_status="NOT_COMPARABLE",
        business_mix_status="PURE_PLAY",
        metric_eligibility_policy="STANDARD_PURE_PLAY_PERCENTILE",
        production_eligibility="ELIGIBLE",
    )
    assert "production_eligible_requires_comparable_or_conditional_business_model" in validate_core_universe_row(row)


def test_eligible_requires_determined_business_mix():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        normalization_status="SUPPORTED",
        business_model_status="COMPARABLE",
        business_mix_status="UNVERIFIED",
        metric_eligibility_policy="STANDARD_PURE_PLAY_PERCENTILE",
        production_eligibility="ELIGIBLE",
    )
    assert "production_eligible_requires_determined_business_mix" in validate_core_universe_row(row)


def test_eligible_requires_determined_metric_eligibility_policy():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        normalization_status="SUPPORTED",
        business_model_status="COMPARABLE",
        business_mix_status="PURE_PLAY",
        metric_eligibility_policy="UNVERIFIED",
        production_eligibility="ELIGIBLE",
    )
    assert "production_eligible_requires_determined_metric_eligibility_policy" in validate_core_universe_row(row)


def test_metric_eligibility_policy_business_mix_mismatch_fails():
    row = _proposed_row(
        business_mix_status="PURE_PLAY",
        metric_eligibility_policy="DIVERSIFIED_GROUP_PERCENTILE_ONLY",
    )
    assert "metric_eligibility_policy_business_mix_mismatch" in validate_core_universe_row(row)


def test_metric_eligibility_policy_normalization_status_mismatch_fails():
    row = _proposed_row(
        normalization_status="SUPPORTED",
        metric_eligibility_policy="UNSUPPORTED_ACCOUNTING_BASIS",
    )
    assert "metric_eligibility_policy_normalization_status_mismatch" in validate_core_universe_row(row)


def test_unknown_sec_filing_type_fails():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        sec_filing_type="40-F",
    )
    assert "sec_filing_type" in validate_core_universe_row(row)


def test_unknown_accounting_basis_fails():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        accounting_basis="LOCAL_GAAP",
    )
    assert "accounting_basis" in validate_core_universe_row(row)


def test_unknown_business_mix_status_fails():
    row = _proposed_row(business_mix_status="MOSTLY_PURE_PLAY")
    assert "business_mix_status" in validate_core_universe_row(row)


def test_unknown_production_eligibility_fails():
    row = _proposed_row(production_eligibility="MAYBE")
    assert "production_eligibility" in validate_core_universe_row(row)


def test_sec_filing_type_present_without_confirmed_availability_fails():
    # Same discipline as the CIK check: a raw SEC-filer fact recorded
    # next to an UNVERIFIED/NOT_FOUND availability looks checked when
    # it is not.
    row = _proposed_row(sec_availability="UNVERIFIED", sec_filing_type="10-K")
    assert "sec_filing_type_present_without_confirmed_sec_availability" in validate_core_universe_row(row)


def test_accounting_basis_present_without_confirmed_availability_fails():
    row = _proposed_row(sec_availability="UNVERIFIED", accounting_basis="US_GAAP")
    assert "accounting_basis_present_without_confirmed_sec_availability" in validate_core_universe_row(row)


def test_business_mix_status_present_without_confirmed_availability_fails():
    row = _proposed_row(sec_availability="UNVERIFIED", business_mix_status="DIVERSIFIED")
    assert "business_mix_status_present_without_confirmed_sec_availability" in validate_core_universe_row(row)


def test_ifrs_cannot_be_normalization_supported():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        accounting_basis="IFRS",
        normalization_status="SUPPORTED",
    )
    assert "ifrs_accounting_basis_cannot_be_normalization_supported" in validate_core_universe_row(row)


def test_unsupported_normalization_cannot_be_production_eligible():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        normalization_status="UNSUPPORTED",
        production_eligibility="ELIGIBLE",
    )
    assert "unsupported_normalization_cannot_be_production_eligible" in validate_core_universe_row(row)


def test_production_eligible_requires_confirmed_sec_availability():
    row = _proposed_row(sec_availability="UNVERIFIED", production_eligibility="ELIGIBLE")
    assert "production_eligible_requires_confirmed_sec_availability" in validate_core_universe_row(row)


def test_not_eligible_without_reason_fails():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        production_eligibility="NOT_ELIGIBLE",
        eligibility_reason=None,
    )
    assert "not_eligible_requires_eligibility_reason" in validate_core_universe_row(row)


def test_not_eligible_with_reason_passes_that_check():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
        normalization_status="UNSUPPORTED",
        accounting_basis="IFRS",
        production_eligibility="NOT_ELIGIBLE",
        eligibility_reason="IFRS filer; current normalizer is US-GAAP-only.",
    )
    assert validate_core_universe_row(row) == []
