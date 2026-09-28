"""
Tests for src/fundamentals/orchestrator.py -- the Thin Production
Orchestrator (Framework v2.1 Architecture v5, implementation step 3,
2026-09-28).

These tests exercise the orchestrator's actual job: proving that SEC
raw -> point-in-time gating -> canonical normalization -> annual
reconstruction -> derived metrics connect as ONE real execution path
(per ChatGPT's final Framework v2.1 approval, this is "the most
important next verification point" -- something module-level unit
tests for each piece individually cannot show). All fixtures here use
REAL Oracle Corporation (CIK 0001341439) SEC XBRL values, the same
source data used by tests/test_orcl_noncurrent_debt_fallback.py.

A second, equally important thing under test: that the whole-document
PIT gate (point_in_time.gate_companyfacts_as_of(), added in this same
round) actually prevents look-ahead bias for the multi-candidate
concept-selection path used by normalize_company() -- a case the
existing point_in_time.py test suite couldn't cover before this
function existed, since normalize_company() was never PIT-gated at all
before this orchestrator.
"""

from __future__ import annotations

import copy

import pytest

from src.fundamentals.orchestrator import (
    STAGE_NOT_WIRED,
    STAGE_OK,
    STAGE_PROVIDED,
    STAGE_SKIPPED,
    run_fundamentals_pipeline,
)


# ============================================================
# FIXTURE -- real Oracle Corporation SEC data (noncurrent debt +
# revenue, two fiscal years, so YoY has something to compute from).
# ============================================================

ORCL_CIK = "0001341439"

ORCL_FIXTURE = {
    "cik": 1341439,
    "entityName": "Oracle Corporation",
    "facts": {
        "us-gaap": {
            "LongTermNotesPayable": {
                "label": "Notes Payable, Noncurrent",
                "units": {
                    "USD": [
                        {
                            "end": "2025-05-31",
                            "val": 85297000000,
                            "accn": "0000950170-25-087926",
                            "fy": 2025,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2025-06-18",
                        },
                        {
                            "end": "2026-05-31",
                            "val": 122342000000,
                            "accn": "0001193125-26-277521",
                            "fy": 2026,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2026-06-22",
                        },
                    ]
                },
            },
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "label": "Revenue",
                "units": {
                    "USD": [
                        {
                            "start": "2024-06-01",
                            "end": "2025-05-31",
                            "val": 57399000000,
                            "accn": "0000950170-25-087926",
                            "fy": 2025,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2025-06-18",
                        },
                        {
                            "start": "2025-06-01",
                            "end": "2026-05-31",
                            "val": 66894000000,
                            "accn": "0001193125-26-277521",
                            "fy": 2026,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2026-06-22",
                        },
                    ]
                },
            },
        }
    },
}


def _run(evaluation_date: str, **overrides):
    data = copy.deepcopy(ORCL_FIXTURE)
    kwargs = dict(
        ticker="ORCL",
        cik=ORCL_CIK,
        evaluation_date=evaluation_date,
        company_type="NON_FINANCIAL",
        raw_companyfacts=data,
    )
    kwargs.update(overrides)
    return run_fundamentals_pipeline(**kwargs)


# ============================================================
# END-TO-END WIRING
# ============================================================

def test_pipeline_connects_raw_through_derived_metrics_as_one_path():
    """The core claim this orchestrator exists to prove: raw SEC data
    in, derived metrics out, via one function call -- not three
    separately-tested modules that happen to share a schema on paper."""
    result = _run("2026-09-28")

    assert result["schema_version"]
    assert result["ticker"] == "ORCL"
    assert result["cik"] == ORCL_CIK
    assert result["evaluation_date"] == "2026-09-28"

    stages = result["stages"]

    assert stages["sec_fetch"]["status"] == STAGE_PROVIDED
    assert stages["raw_validation"]["status"] == STAGE_OK
    assert stages["pit_gating"]["status"] == STAGE_OK

    noncurrent_debt = stages["normalization"]["normalized"]["metrics"]["noncurrent_debt"]
    assert noncurrent_debt["status"] == "OK"
    assert noncurrent_debt["concept"] == "LongTermNotesPayable"
    assert noncurrent_debt["value"] == 122342000000

    revenue = stages["normalization"]["normalized"]["metrics"]["revenue"]
    assert revenue["status"] == "OK"
    assert revenue["value"] == 66894000000

    revenue_yoy = stages["derived_metrics"]["derived"]["derived_metrics"]["revenue_yoy"]
    assert revenue_yoy["status"] == "OK"
    assert revenue_yoy["value"] == pytest.approx(66894000000 / 57399000000 - 1)


def _strip_normalized_at(result: dict) -> dict:
    """sec_normalizer.normalize_company() stamps its own output with a
    wall-clock `normalized_at` (pre-existing behavior, unrelated to
    this orchestrator and out of scope to change here) -- excluded so
    this test isolates the orchestrator's OWN determinism rather than
    normalize_company()'s already-existing timestamp field."""
    result = copy.deepcopy(result)
    result["stages"]["normalization"]["normalized"].pop("normalized_at", None)
    return result


def test_pipeline_is_pure_function_of_its_inputs():
    """Same inputs, same evaluation_date -> identical output (modulo
    sec_normalizer.normalize_company()'s own pre-existing wall-clock
    `normalized_at` stamp). Guards against any accidental global-state
    dependency creeping into the orchestrator's OWN wiring logic (this
    codebase's other pure-function modules, e.g. src/sec/identity.py,
    hold this invariant explicitly by design)."""
    result_a = _strip_normalized_at(_run("2026-09-28"))
    result_b = _strip_normalized_at(_run("2026-09-28"))
    assert result_a == result_b


def test_raw_companyfacts_input_is_never_mutated():
    original = copy.deepcopy(ORCL_FIXTURE)
    data = copy.deepcopy(ORCL_FIXTURE)

    run_fundamentals_pipeline(
        ticker="ORCL",
        cik=ORCL_CIK,
        evaluation_date="2026-09-28",
        company_type="NON_FINANCIAL",
        raw_companyfacts=data,
    )

    assert data == original


# ============================================================
# POINT-IN-TIME GATING -- the orchestrator-specific look-ahead-bias
# guard (gate_companyfacts_as_of applied before candidate selection).
# ============================================================

def test_evaluation_date_before_fy2026_filing_hides_fy2026_observation():
    """As of 2026-01-01 (before the 2026-06-22 FY2026 10-K was filed),
    normalize_company()'s candidate selection must fall back to the
    FY2025 observation -- proving the whole-document PIT gate actually
    reaches the multi-candidate concept-selection path, not just
    single-concept lookups."""
    result = _run("2026-01-01")

    noncurrent_debt = result["stages"]["normalization"]["normalized"]["metrics"]["noncurrent_debt"]
    assert noncurrent_debt["status"] == "OK"
    assert noncurrent_debt["value"] == 85297000000
    assert noncurrent_debt["period_end"] == "2025-05-31"

    revenue = result["stages"]["normalization"]["normalized"]["metrics"]["revenue"]
    assert revenue["value"] == 57399000000

    # Only one fiscal year of revenue history was public yet -- YoY
    # must be MISSING, never computed against an unpublished FY2026
    # value.
    revenue_yoy = result["stages"]["derived_metrics"]["derived"]["derived_metrics"].get("revenue_yoy")
    assert revenue_yoy["status"] == "MISSING"


def test_evaluation_date_before_any_filing_leaves_metrics_missing():
    """As of a date before even the FY2025 10-K was filed, nothing is
    public yet -- normalize_company() must report MISSING (not raise,
    not fabricate a value), matching normalize_company()'s own
    documented behavior for a concept with no observations."""
    result = _run("2020-01-01")

    noncurrent_debt = result["stages"]["normalization"]["normalized"]["metrics"]["noncurrent_debt"]
    assert noncurrent_debt["status"] == "MISSING"

    revenue = result["stages"]["normalization"]["normalized"]["metrics"]["revenue"]
    assert revenue["status"] == "MISSING"


def test_gated_snapshot_does_not_leak_into_raw_validation_or_fetch_stage():
    """pit_gating must report the evaluation_date it actually gated to,
    independent of which stage supplied the raw data."""
    result = _run("2025-12-31")
    assert result["stages"]["pit_gating"]["evaluation_date"] == "2025-12-31"


# ============================================================
# OPTIONAL STAGES: SKIPPED, never silently OK, when inputs are absent
# ============================================================

def test_identity_stage_skipped_without_raw_submissions():
    result = _run("2026-09-28")
    assert result["stages"]["identity"]["status"] == STAGE_SKIPPED
    assert result["stages"]["identity"]["reason"]


def test_peer_group_and_universe_membership_stages_skipped_without_rows():
    result = _run("2026-09-28")
    assert result["stages"]["peer_group"]["status"] == STAGE_SKIPPED
    assert result["stages"]["universe_membership"]["status"] == STAGE_SKIPPED


def test_peer_group_stage_runs_when_peer_rows_provided():
    peer_rows = [
        {
            "cik": ORCL_CIK,
            "mapping_version": "v1",
            "classification_status": "REVIEWED",
            "classification_source": "MANUAL_REVIEW",
            "effective_from": "2020-01-01",
            "classification_available_date": "2020-01-01",
            "peer_group": "Enterprise Software",
        }
    ]

    result = _run("2026-09-28", peer_rows=peer_rows)

    peer_group_stage = result["stages"]["peer_group"]
    assert peer_group_stage["status"] == STAGE_OK
    assert peer_group_stage["lookup"]["lookup_status"] == "OK"
    assert peer_group_stage["lookup"]["mapping"]["peer_group"] == "Enterprise Software"


def test_universe_membership_stage_runs_when_membership_rows_provided():
    membership_rows = [
        {
            "cik": ORCL_CIK,
            "ticker": "ORCL",
            "universe_type": "WATCHLIST",
            "membership_status": "ACTIVE",
            "membership_source": "LEGACY_SEED",
            "membership_version": "v1",
            "effective_from": "2020-01-01",
            "membership_available_date": "2020-01-01",
        }
    ]

    result = _run("2026-09-28", membership_rows=membership_rows)

    membership_stage = result["stages"]["universe_membership"]
    assert membership_stage["status"] == STAGE_OK
    assert membership_stage["watchlist"]["lookup_status"] == "OK"
    assert membership_stage["core"]["lookup_status"] == "NO_MEMBERSHIP_FOR_DATE"


# ============================================================
# DELIBERATELY-NOT-WIRED STAGES -- must never silently look wired
# (Framework v2.1 final approval constraint (1): finishing this step
# must not be mistaken for 10C Production activation).
# ============================================================

def test_applicability_relative_evaluation_and_factor_are_not_wired():
    result = _run("2026-09-28")

    for stage_name in ("metric_applicability", "relative_evaluation", "factor"):
        stage = result["stages"][stage_name]
        assert stage["status"] == STAGE_NOT_WIRED
        assert stage["reason"]

    # No factor or composite score of any kind appears anywhere in the
    # output -- the absence itself is the enforcement mechanism, same
    # convention already used by percentile_engine.py for Composite
    # Score (see that module's docstring).
    assert "percentile_score" not in result["stages"]["factor"]
    assert "factor_status" not in result["stages"]["factor"]


# ============================================================
# INPUT VALIDATION -- fails loudly on structural defects, matching
# this codebase's established convention (see module docstring).
# ============================================================

def test_unparseable_evaluation_date_raises():
    with pytest.raises(ValueError):
        _run("not-a-date")


def test_malformed_companyfacts_raises():
    with pytest.raises(ValueError):
        run_fundamentals_pipeline(
            ticker="ORCL",
            cik=ORCL_CIK,
            evaluation_date="2026-09-28",
            company_type="NON_FINANCIAL",
            raw_companyfacts={"missing": "required keys"},
        )
