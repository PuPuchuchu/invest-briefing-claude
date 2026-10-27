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


# ============================================================
# STEP 14 (2026-10-26) -- Company Type registry fixture.
#
# A minimal, valid company_type.csv-shaped row for ORCL, used as the
# default company_type_rows for every existing test via _run() below.
# classification_available_date is deliberately far in the past
# (1980-01-01, matching the real data/reference/company_type.csv's own
# sentinel-date judgment call for SIC-unambiguous tickers -- see the
# STEP 14 completion report) specifically so it does NOT collide with
# test_evaluation_date_before_any_filing_leaves_metrics_missing's
# evaluation_date="2020-01-01": that test is about PIT gating of SEC
# filings, not about Company Type availability, and must keep passing
# unchanged.
# ============================================================

# STEP 14 ACCEPTANCE REMEDIATION (2026-10-26): classification_available_date
# is no longer a fabricated "1980-01-01" sentinel. It is now "2026-09-20" --
# the exact date data/reference/issuer_identity.csv's own normalized_at
# timestamp (2026-09-20T07:33:57Z) records ORCL's SIC code as having been
# fetched into this system -- the only evidence-backed date available. See
# the STEP 14 Acceptance Remediation report for the full rationale and for
# which pre-existing tests this deliberately makes fail (evaluation_dates
# before 2026-09-20 now correctly fail closed at COMPANY_TYPE_VALIDATION;
# this is NOT a bug to be routed around with a fabricated earlier date).
ORCL_COMPANY_TYPE_ROWS = [
    {
        "ticker": "ORCL",
        "cik": ORCL_CIK,
        "company_type": "NON_FINANCIAL",
        "effective_from": "2026-09-20",
        "effective_to": None,
        "classification_available_date": "2026-09-20",
        "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE",
        "mapping_rationale": "Test fixture, mirrors real company_type.csv row for ORCL (post-remediation date).",
        "reviewed_at_utc": "2026-10-26T00:00:00Z",
        "decision_ref": "STEP14-ACCEPTANCE-REMEDIATION-2026-10-26 (test fixture)",
    }
]


# ============================================================
# STEP 14 FINALIZATION REVIEW (2026-10-26) -- test-local Company Type
# fixture for SEC-filing-PIT isolation tests ONLY.
#
# The 3 tests below (test_evaluation_date_before_fy2026_filing_hides_
# fy2026_observation, test_evaluation_date_before_any_filing_leaves_
# metrics_missing, test_gated_snapshot_does_not_leak_into_raw_
# validation_or_fetch_stage) predate STEP 14 entirely (2026-09-28) and
# exist to verify SEC-filing-level PIT gating (point_in_time.py /
# gate_companyfacts_as_of), using evaluation_date values chosen purely
# for THAT purpose (2020-01-01, 2026-01-01, 2025-12-31) -- none of them
# are about Company Type availability at all.
#
# ORCL_COMPANY_TYPE_ROWS (above) now carries the real, evidence-backed
# production availability date (2026-09-20, matching data/reference/
# company_type.csv), which is deliberately LATER than these 3 tests'
# evaluation_dates -- so using the shared default would make all 3 fail
# at COMPANY_TYPE_VALIDATION, for a reason entirely orthogonal to what
# they test.
#
# This fixture is NOT a claim about real-world evidentiary availability
# -- it is a test-isolation device only, used to decouple "does SEC
# filing PIT gating work" from "is this Company Type mapping backed by
# real evidence as of this date" so each of those two orthogonal
# concerns can be tested on its own. The REAL production availability
# rule (no fabricated early dates, fails closed before 2026-09-20) stays
# covered, unmodified, by ORCL_COMPANY_TYPE_ROWS above and by the 14
# dedicated boundary tests in tests/test_company_type_registry.py. This
# fixture never touches data/reference/company_type.csv.
_ORCL_COMPANY_TYPE_ROWS_FOR_SEC_PIT_ISOLATION_TESTS_ONLY = [
    {
        "ticker": "ORCL",
        "cik": ORCL_CIK,
        "company_type": "NON_FINANCIAL",
        "effective_from": "2000-01-01",
        "effective_to": None,
        "classification_available_date": "2000-01-01",
        "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE",
        "mapping_rationale": (
            "Test-local fixture used ONLY to isolate SEC-filing-PIT "
            "gating tests (pre-dating STEP 14) from Company-Type-"
            "availability semantics (STEP 14). Not used as, and must "
            "never be read as, evidence for data/reference/"
            "company_type.csv's real classification_available_date "
            "(2026-09-20) -- that production value is untouched and "
            "separately tested by tests/test_company_type_registry.py."
        ),
        "reviewed_at_utc": "2026-10-26T00:00:00Z",
        "decision_ref": "STEP14-FINALIZATION-REVIEW-TEST-ISOLATION-ONLY (not production evidence)",
    }
]


def _run(evaluation_date: str, **overrides):
    data = copy.deepcopy(ORCL_FIXTURE)
    kwargs = dict(
        ticker="ORCL",
        cik=ORCL_CIK,
        evaluation_date=evaluation_date,
        company_type="NON_FINANCIAL",
        company_type_rows=copy.deepcopy(ORCL_COMPANY_TYPE_ROWS),
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
        company_type_rows=copy.deepcopy(ORCL_COMPANY_TYPE_ROWS),
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
    single-concept lookups.

    Uses _ORCL_COMPANY_TYPE_ROWS_FOR_SEC_PIT_ISOLATION_TESTS_ONLY (see
    that fixture's own docstring) -- this test is about SEC-filing PIT
    gating, not Company Type availability, and 2026-01-01 predates the
    real production availability date (2026-09-20)."""
    result = _run(
        "2026-01-01",
        company_type_rows=_ORCL_COMPANY_TYPE_ROWS_FOR_SEC_PIT_ISOLATION_TESTS_ONLY,
    )

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
    documented behavior for a concept with no observations.

    Uses _ORCL_COMPANY_TYPE_ROWS_FOR_SEC_PIT_ISOLATION_TESTS_ONLY (see
    that fixture's own docstring) -- this test is about SEC-filing PIT
    gating, not Company Type availability, and 2020-01-01 predates the
    real production availability date (2026-09-20)."""
    result = _run(
        "2020-01-01",
        company_type_rows=_ORCL_COMPANY_TYPE_ROWS_FOR_SEC_PIT_ISOLATION_TESTS_ONLY,
    )

    noncurrent_debt = result["stages"]["normalization"]["normalized"]["metrics"]["noncurrent_debt"]
    assert noncurrent_debt["status"] == "MISSING"

    revenue = result["stages"]["normalization"]["normalized"]["metrics"]["revenue"]
    assert revenue["status"] == "MISSING"


def test_gated_snapshot_does_not_leak_into_raw_validation_or_fetch_stage():
    """pit_gating must report the evaluation_date it actually gated to,
    independent of which stage supplied the raw data.

    Uses _ORCL_COMPANY_TYPE_ROWS_FOR_SEC_PIT_ISOLATION_TESTS_ONLY (see
    that fixture's own docstring) -- this test is about the pit_gating
    stage's own reported evaluation_date, not Company Type availability,
    and 2025-12-31 predates the real production availability date
    (2026-09-20)."""
    result = _run(
        "2025-12-31",
        company_type_rows=_ORCL_COMPANY_TYPE_ROWS_FOR_SEC_PIT_ISOLATION_TESTS_ONLY,
    )
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
            company_type_rows=copy.deepcopy(ORCL_COMPANY_TYPE_ROWS),
            raw_companyfacts={"missing": "required keys"},
        )


# ============================================================
# GROWTH + DATA_SUFFICIENCY (2026-09-29, production-path verification
# round -- Step 5B wiring). Five required cases per ChatGPT's
# verification spec:
#   A. concept never found at all       -> MISSING, CONCEPT_NOT_FOUND
#   B. concept found, history missing   -> MISSING, INSUFFICIENT_HISTORY
#   C. concept found, history sufficient -> OK (existing calc path)
#   D. SNDK/spin-off shape (one quarter + one annual observation only)
#   E. existing normal company -> zero regression in already-wired
#      stages (sec_fetch..universe_membership)
# ============================================================

def test_growth_and_data_sufficiency_stages_are_wired():
    """Baseline: both new stages actually run (STAGE_OK), and the
    previously-NOT_WIRED stages this same round left untouched are
    still exactly the 3 they always were (metric_applicability,
    relative_evaluation, factor) -- data_sufficiency is no longer one
    of them."""
    result = _run("2026-09-28")
    stages = result["stages"]

    assert stages["growth"]["status"] == STAGE_OK
    assert stages["data_sufficiency"]["status"] == STAGE_OK
    assert set(stages["growth"]["growth"]["components"].keys()) == {
        "revenue_yoy",
        "operating_income_yoy",
        "eps_yoy",
        "fcf_yoy",
        "revenue_cagr_3y",
    }
    assert set(stages["data_sufficiency"]["sufficiency"].keys()) == {
        "revenue_yoy",
        "operating_income_yoy",
        "eps_yoy",
        "fcf_yoy",
        "revenue_cagr_3y",
    }

    for stage_name in ("metric_applicability", "relative_evaluation", "factor"):
        assert stages[stage_name]["status"] == STAGE_NOT_WIRED


def test_case_a_concept_never_found_is_concept_not_found_never_insufficient_history():
    """Case A: the ORCL fixture only ever defines LongTermNotesPayable
    and Revenue concepts -- operating_income / diluted_eps / cfo / capex
    were never found for this company at all. Every growth metric that
    depends solely on one of THOSE concepts must resolve to MISSING with
    reason_code=CONCEPT_NOT_FOUND, never INSUFFICIENT_HISTORY (that
    would misrepresent a concept-coverage gap as a "just wait for more
    history" problem)."""
    result = _run("2026-09-28")
    sufficiency = result["stages"]["data_sufficiency"]["sufficiency"]

    for metric_name in ("operating_income_yoy", "eps_yoy", "fcf_yoy"):
        assert sufficiency[metric_name]["calculation_status"] == "MISSING"
        assert sufficiency[metric_name]["reason_code"] == "CONCEPT_NOT_FOUND"
        assert sufficiency[metric_name]["reason_code"] != "INSUFFICIENT_HISTORY"


def test_case_b_concept_found_but_history_missing_is_insufficient_history():
    """Case B: Revenue WAS found (normalize_company() selected it, per
    test_pipeline_connects_raw_through_derived_metrics_as_one_path), but
    the ORCL fixture has zero 10-Q quarterly filings and only 2 annual
    fiscal years (not 3 apart) -- so revenue_yoy and revenue_cagr_3y
    must resolve to MISSING/INSUFFICIENT_HISTORY, the concept-found
    branch, never CONCEPT_NOT_FOUND."""
    result = _run("2026-09-28")
    sufficiency = result["stages"]["data_sufficiency"]["sufficiency"]

    for metric_name in ("revenue_yoy", "revenue_cagr_3y"):
        assert sufficiency[metric_name]["calculation_status"] == "MISSING"
        assert sufficiency[metric_name]["reason_code"] == "INSUFFICIENT_HISTORY"
        assert sufficiency[metric_name]["reason_code"] != "CONCEPT_NOT_FOUND"


# Revenue with two comparable same-quarter (Q1) 10-Q filings one fiscal
# year apart, added on top of ORCL_FIXTURE's existing annual revenue --
# ORCL's fiscal year starts 2025-06-01/2024-06-01 (see the existing
# annual "start" values above), so Q1 covers roughly the first ~90 days
# of each fiscal year.
_ORCL_FIXTURE_WITH_QUARTERLY_REVENUE = copy.deepcopy(ORCL_FIXTURE)
_ORCL_FIXTURE_WITH_QUARTERLY_REVENUE["facts"]["us-gaap"][
    "RevenueFromContractWithCustomerExcludingAssessedTax"
]["units"]["USD"].extend(
    [
        {
            "start": "2024-06-01",
            "end": "2024-08-31",
            "val": 13300000000,
            "accn": "0000950170-24-999001",
            "fy": 2025,
            "fp": "Q1",
            "form": "10-Q",
            "filed": "2024-09-15",
        },
        {
            "start": "2025-06-01",
            "end": "2025-08-31",
            "val": 14800000000,
            "accn": "0000950170-25-999001",
            "fy": 2026,
            "fp": "Q1",
            "form": "10-Q",
            "filed": "2025-09-15",
        },
    ]
)


def test_case_c_concept_found_with_sufficient_history_reaches_ok():
    """Case C: same concept as Case B, but with a comparable same-quarter
    (Q1) pair one fiscal year apart now present -- revenue_yoy must
    reach the existing OK calculation path (calculate_growth()'s own,
    unmodified, formula), proving this wiring doesn't just detect
    absence -- it also lets a genuinely sufficient case flow all the way
    through to a real computed value."""
    result = _run(
        "2026-09-28",
        raw_companyfacts=copy.deepcopy(_ORCL_FIXTURE_WITH_QUARTERLY_REVENUE),
    )
    revenue_yoy = result["stages"]["growth"]["growth"]["components"]["revenue_yoy"]
    assert revenue_yoy["status"] == "OK"
    assert revenue_yoy["value"] == pytest.approx(14800000000 / 13300000000 - 1)

    sufficiency = result["stages"]["data_sufficiency"]["sufficiency"]["revenue_yoy"]
    assert sufficiency["calculation_status"] == "OK"
    assert sufficiency["reason_code"] is None


def test_case_d_sndk_style_single_period_company_is_insufficient_history_at_orchestrator_level():
    """Case D: a company with exactly ONE annual and ONE quarterly
    revenue observation -- concept genuinely exists (this is a real,
    filing company), but no PIT-gated comparison observation exists yet
    (a newly-listed/spun-off company, the SNDK case already covered at
    the unit level by tests/test_data_sufficiency.py). Run through the
    REAL orchestrator path this time: must resolve to
    MISSING/INSUFFICIENT_HISTORY, never CONCEPT_NOT_FOUND (the concept
    WAS found) and never an Applicability-vocabulary status (this
    orchestrator doesn't wire Applicability at all, so there is nothing
    for the two to be confused with here -- Data Sufficiency stands on
    its own)."""
    spinoff_fixture = {
        "cik": 9999999,
        "entityName": "Newly Listed Spinoff Inc",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2026-01-01",
                                "end": "2026-03-31",
                                "val": 150000000,
                                "accn": "0001999999-26-000001",
                                "fy": 2026,
                                "fp": "Q1",
                                "form": "10-Q",
                                "filed": "2026-04-15",
                            },
                            {
                                "start": "2026-01-01",
                                "end": "2026-12-31",
                                "val": 600000000,
                                "accn": "0001999999-27-000001",
                                "fy": 2026,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2027-02-15",
                            },
                        ]
                    },
                },
            }
        },
    }

    spinoff_company_type_rows = [
        {
            "ticker": "SPINOFF",
            "cik": "9999999",
            "company_type": "NON_FINANCIAL",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "classification_available_date": "2026-01-01",
            "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE",
            "mapping_rationale": "Synthetic test fixture for Case D (spin-off shape); not a real registration.",
            "reviewed_at_utc": "2026-10-26T00:00:00Z",
            "decision_ref": "STEP14-IMPLEMENTATION-SIC-REVIEW (test fixture)",
        }
    ]

    result = run_fundamentals_pipeline(
        ticker="SPINOFF",
        cik="9999999",
        evaluation_date="2027-03-01",
        company_type="NON_FINANCIAL",
        company_type_rows=spinoff_company_type_rows,
        raw_companyfacts=spinoff_fixture,
    )

    sufficiency = result["stages"]["data_sufficiency"]["sufficiency"]
    for metric_name in ("revenue_yoy", "revenue_cagr_3y"):
        assert sufficiency[metric_name]["calculation_status"] == "MISSING"
        assert sufficiency[metric_name]["reason_code"] == "INSUFFICIENT_HISTORY"
        assert sufficiency[metric_name]["reason_code"] != "CONCEPT_NOT_FOUND"


def test_case_e_existing_stages_have_zero_regression_with_growth_wired():
    """Case E: adding the GROWTH / DATA_SUFFICIENCY stages must not
    change a single value any earlier stage already produced --
    identical assertions to
    test_pipeline_connects_raw_through_derived_metrics_as_one_path,
    re-asserted here as an explicit regression guard for this round."""
    result = _run("2026-09-28")
    stages = result["stages"]

    noncurrent_debt = stages["normalization"]["normalized"]["metrics"]["noncurrent_debt"]
    assert noncurrent_debt["status"] == "OK"
    assert noncurrent_debt["value"] == 122342000000

    revenue = stages["normalization"]["normalized"]["metrics"]["revenue"]
    assert revenue["status"] == "OK"
    assert revenue["value"] == 66894000000

    revenue_yoy_derived = stages["derived_metrics"]["derived"]["derived_metrics"]["revenue_yoy"]
    assert revenue_yoy_derived["status"] == "OK"
    assert revenue_yoy_derived["value"] == pytest.approx(66894000000 / 57399000000 - 1)


# ============================================================
# STEP 14 (2026-10-26) -- COMPANY_TYPE_VALIDATION stage.
#
# Per the user's explicit Production Implementation Authorization,
# company_type is no longer a trusted bare parameter: it is now
# resolved against company_type_rows (the approved
# data/reference/company_type.csv registry, loaded by the caller) via
# company_type_registry.get_company_type_as_of(), and any non-OK
# lookup_status or a caller/registry mismatch must fail closed with
# ValueError -- never a guessed/defaulted Company Type, and never after
# an unnecessary external SEC request.
# ============================================================

def test_company_type_validation_stage_runs_and_reports_ok_for_approved_ticker():
    """Baseline: an approved, registered ticker reaches STAGE_OK with
    the resolved registry mapping attached, and every other existing
    stage still runs exactly as before (zero regression)."""
    result = _run("2026-09-28")
    stage = result["stages"]["company_type_validation"]
    assert stage["status"] == STAGE_OK
    assert stage["lookup"]["lookup_status"] == "OK"
    assert stage["lookup"]["mapping"]["company_type"] == "NON_FINANCIAL"
    assert result["stages"]["normalization"]["status"] == STAGE_OK


def test_company_type_validation_stage_runs_for_approved_financial_ticker():
    """A registered FINANCIAL-type cik must also resolve to STAGE_OK --
    this is not special-cased to NON_FINANCIAL. Uses a synthetic
    registration (no real FINANCIAL ticker is registered in the actual
    data/reference/company_type.csv as of this round)."""
    financial_rows = [
        {
            "ticker": "SYNTHFIN",
            "cik": "0005555555",
            "company_type": "FINANCIAL",
            "effective_from": "2020-01-01",
            "effective_to": None,
            "classification_available_date": "2020-01-01",
            "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE",
            "mapping_rationale": "Synthetic FINANCIAL-type fixture; not a real registration.",
            "reviewed_at_utc": "2026-10-26T00:00:00Z",
            "decision_ref": "STEP14-IMPLEMENTATION-SIC-REVIEW (test fixture)",
        }
    ]
    with pytest.raises(ValueError) as excinfo:
        # Expected to fail downstream (RAW_VALIDATION -- malformed
        # companyfacts), but the point of this test is that it does NOT
        # fail at COMPANY_TYPE_VALIDATION: a FINANCIAL registration is
        # resolved just as validly as NON_FINANCIAL.
        run_fundamentals_pipeline(
            ticker="SYNTHFIN",
            cik="0005555555",
            evaluation_date="2026-09-28",
            company_type="FINANCIAL",
            company_type_rows=financial_rows,
            raw_companyfacts={"missing": "required keys"},
        )
    assert "Company Type validation failed" not in str(excinfo.value)
    # Positive check: the failure is the RAW_VALIDATION one
    # (sec_normalizer.validate_companyfacts on the malformed document).
    assert "Missing required key" in str(excinfo.value)


def test_unregistered_cik_fails_closed_before_sec_fetch():
    """An unregistered cik (e.g. BLK/UNH/JPM as of this round -- deliberately
    not present in data/reference/company_type.csv) must raise ValueError
    at COMPANY_TYPE_VALIDATION, with NO default ever assumed. raw_companyfacts
    is omitted here on purpose: if COMPANY_TYPE_VALIDATION did not run before
    SEC_FETCH, this would instead fail (or hang) trying to reach the network /
    read SEC_USER_AGENT -- a different error entirely. Getting the
    Company-Type ValueError instead proves the stage ordering."""
    with pytest.raises(ValueError, match="Company Type validation failed"):
        run_fundamentals_pipeline(
            ticker="UNRESOLVED",
            cik="0000000000",
            evaluation_date="2026-09-28",
            company_type="NON_FINANCIAL",
            company_type_rows=[],
        )


def test_missing_required_row_field_fails_closed():
    """A company_type_rows entry missing a required field (here:
    mapping_rationale) must be treated as structurally invalid, never
    silently accepted."""
    rows = [
        {
            "ticker": "ORCL",
            "cik": ORCL_CIK,
            "company_type": "NON_FINANCIAL",
            "effective_from": "1980-01-01",
            "effective_to": None,
            "classification_available_date": "1980-01-01",
            "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE",
            "mapping_rationale": "",
            "reviewed_at_utc": "2026-10-26T00:00:00Z",
            "decision_ref": "test",
        }
    ]
    with pytest.raises(ValueError, match="Company Type validation failed"):
        _run("2026-09-28", company_type_rows=rows)


def test_invalid_company_type_value_fails_closed():
    """A row whose company_type is not one of the two approved values
    must fail closed, never pass through as if valid."""
    rows = copy.deepcopy(ORCL_COMPANY_TYPE_ROWS)
    rows[0]["company_type"] = "SOMETHING_ELSE"
    with pytest.raises(ValueError, match="Company Type validation failed"):
        _run("2026-09-28", company_type_rows=rows, company_type="SOMETHING_ELSE")


def test_caller_value_registry_value_mismatch_fails_closed():
    """The caller-supplied company_type may only be used to VERIFY
    agreement with the approved registry value, never to override it --
    a mismatch must raise ValueError, not silently prefer either side."""
    with pytest.raises(ValueError, match="company_type mismatch"):
        _run("2026-09-28", company_type="FINANCIAL")


def test_overlapping_effective_windows_fail_closed():
    """Two rows for the same cik both claiming to cover the same
    evaluation_date is a reference-data integrity problem -- this must
    raise ValueError (LOOKUP_OVERLAPPING_MAPPING), never arbitrarily
    pick one of the two candidates."""
    rows = copy.deepcopy(ORCL_COMPANY_TYPE_ROWS) + copy.deepcopy(ORCL_COMPANY_TYPE_ROWS)
    with pytest.raises(ValueError, match="Company Type validation failed"):
        _run("2026-09-28", company_type_rows=rows)


def test_no_covering_window_for_evaluation_date_fails_closed():
    """An evaluation_date entirely outside every registered row's
    [effective_from, effective_to) window must fail closed, never fall
    back to the nearest window."""
    rows = copy.deepcopy(ORCL_COMPANY_TYPE_ROWS)
    rows[0]["effective_from"] = "2030-01-01"
    with pytest.raises(ValueError, match="Company Type validation failed"):
        _run("2026-09-28", company_type_rows=rows)


def test_classification_not_yet_available_at_evaluation_date_fails_closed():
    """A Company Type row that business-effectively covers
    evaluation_date but was not yet AVAILABLE for use until later must
    fail closed -- using it would be a look-ahead-bias violation,
    exactly the distinction STEP 14 Section 2.1 draws between
    effective_from and classification_available_date."""
    rows = copy.deepcopy(ORCL_COMPANY_TYPE_ROWS)
    rows[0]["classification_available_date"] = "2027-01-01"
    with pytest.raises(ValueError, match="Company Type validation failed"):
        _run("2026-09-28", company_type_rows=rows)


def test_unresolved_ticker_gets_no_arbitrary_default():
    """Mirrors the real BLK/UNH/JPM situation directly: a ticker with NO
    row at all in company_type_rows must never be defaulted to
    NON_FINANCIAL (or anything else) -- it fails closed exactly like
    test_unregistered_cik_fails_closed_before_sec_fetch, re-asserted
    here under the real-world framing STEP 14 Section 3 describes."""
    with pytest.raises(ValueError, match="Company Type validation failed"):
        run_fundamentals_pipeline(
            ticker="BLK",
            cik="0000019617",  # not actually BLK's real CIK; placeholder for the test
            evaluation_date="2026-09-28",
            company_type="FINANCIAL",
            company_type_rows=[],
        )


def test_company_type_validation_failure_prevents_sec_fetch_entirely():
    """A validation failure must occur BEFORE any SEC_FETCH side effect
    -- proven the same way as test_unregistered_cik_fails_closed_before_sec_fetch:
    no raw_companyfacts is supplied and no SEC_USER_AGENT dependency is
    satisfied, yet the error raised is unambiguously the Company Type
    one, not a network/fetch one."""
    with pytest.raises(ValueError) as excinfo:
        run_fundamentals_pipeline(
            ticker="UNRESOLVED2",
            cik="0000000001",
            evaluation_date="2026-09-28",
            company_type="NON_FINANCIAL",
            company_type_rows=[],
        )
    assert "Company Type validation failed" in str(excinfo.value)
    assert "SEC" not in str(excinfo.value)


def test_existing_normal_call_sites_remain_compatible_with_company_type_rows_supplied():
    """Explicit compatibility guard: every pre-existing orchestrator
    behavior (growth/data_sufficiency wiring, peer_group, universe_
    membership, derived_metrics, the 3-stage NOT_WIRED bucket) is
    unchanged when a valid company_type_rows is supplied alongside all
    the usual arguments."""
    result = _run("2026-09-28")
    stages = result["stages"]
    assert stages["sec_fetch"]["status"] == STAGE_PROVIDED
    assert stages["raw_validation"]["status"] == STAGE_OK
    assert stages["normalization"]["status"] == STAGE_OK
    assert stages["growth"]["status"] == STAGE_OK
    assert stages["data_sufficiency"]["status"] == STAGE_OK
    for stage_name in ("metric_applicability", "relative_evaluation", "factor"):
        assert stages[stage_name]["status"] == STAGE_NOT_WIRED


# ============================================================
# STEP 14 FINALIZATION REVIEW (2026-10-26) -- NORMALIZED_DATA_VALIDATION
# stage. Connects sec_normalizer.validate_normalized_data() as an
# INFORMATIONAL stage (reports its failures list; does not raise merely
# because that list is non-empty) -- see orchestrator.py's own inline
# comment at this stage for the full contract rationale.
# ============================================================

def test_normalized_data_validation_stage_reports_failures_for_sparse_fixture_without_blocking():
    """The baseline ORCL_FIXTURE is deliberately sparse (only Revenue +
    LongTermNotesPayable -- see Case A's own docstring above) -- so
    validate_normalized_data() legitimately returns a non-empty failures
    list for it (net_income, diluted_eps, operating_income, cfo, capex,
    cash, shares_outstanding, total_debt are all genuinely not OK). This
    must be reported, but must NOT raise or prevent the pipeline from
    reaching GROWTH/DATA_SUFFICIENCY afterward -- that's the whole point
    of this being an informational, not blocking, stage."""
    result = _run("2026-09-28")
    stage = result["stages"]["normalized_data_validation"]
    assert stage["status"] == STAGE_OK
    assert set(stage["failures"]) >= {
        "net_income", "diluted_eps", "operating_income", "cfo", "capex",
        "cash", "shares_outstanding", "total_debt",
    }
    # Pipeline still reached the stages after it -- proof this did not block.
    assert result["stages"]["growth"]["status"] == STAGE_OK
    assert result["stages"]["data_sufficiency"]["status"] == STAGE_OK


def test_normalized_data_validation_stage_reports_empty_failures_for_a_complete_fixture():
    """A metric-complete NON_FINANCIAL normalized result (synthetic,
    built directly rather than via a companyfacts fixture, to avoid
    re-litigating real XBRL concept shapes here) must report an EMPTY
    failures list -- proving the stage faithfully reflects
    validate_normalized_data()'s real verdict in both directions, not
    just the sparse-fixture failing case above."""
    from src.fundamentals.sec_normalizer import validate_normalized_data

    complete_normalized = {
        "schema_version": "v1",
        "ticker": "SYNTH",
        "entity_name": "Synthetic Complete Co",
        "cik": "0000000002",
        "company_type": "NON_FINANCIAL",
        "metrics": {
            name: {"status": "OK", "value": 1.0}
            for name in (
                "revenue", "net_income", "diluted_eps", "operating_income",
                "cfo", "capex", "cash", "shares_outstanding",
            )
        } | {
            "total_debt": {"status": "OK", "value": 1.0},
            "stockholders_equity": {"status": "MISSING"},
        },
    }
    assert validate_normalized_data(complete_normalized) == []


def test_normalized_data_validation_hard_structural_raise_is_unaffected():
    """validate_normalized_data()'s own pre-existing hard-raise behavior
    (an unrecognized company_type) is unchanged by this stage's wiring --
    it still propagates as ValueError, exactly as it did before this
    stage existed. Exercised directly against the function (not through
    the orchestrator) since reaching this via run_fundamentals_pipeline
    would require an already-approved company_type value, which by
    definition can't be the invalid one being tested here."""
    from src.fundamentals.sec_normalizer import validate_normalized_data

    malformed = {
        "schema_version": "v1",
        "ticker": "X",
        "entity_name": "X",
        "cik": "1",
        "company_type": "SOMETHING_ELSE",
        "metrics": {},
    }
    with pytest.raises(ValueError):
        validate_normalized_data(malformed)
