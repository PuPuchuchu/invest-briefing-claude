"""
Unit + integration tests for src/fundamentals/data_sufficiency.py
(Framework v2.1 Step 5B, 2026-09-28 confirmation round).

Two layers of coverage, mirroring the module's own docstring:

    1. Unit tests for classify_component_sufficiency() /
       classify_growth_data_sufficiency() against hand-built component
       dicts -- these never touch growth.py's real calculation logic.

    2. One integration test using a real, unmodified
       src.fundamentals.growth.calculate_growth() call against a
       SNDK-style "recently spun off / newly listed" historical fixture
       (current-period observation exists, no PIT-gated comparison
       observation exists) -- the explicit 2026-09-28 confirmation-round
       regression case (section 5): must resolve to MISSING /
       INSUFFICIENT_HISTORY, never NOT_APPLICABLE, never UNVERIFIED. This
       also demonstrates Data Sufficiency and Applicability
       (metric_applicability.py) are orthogonal: a metric can be fully
       APPLICABLE and still be data-insufficient.
"""

from __future__ import annotations

import pytest

from src.fundamentals.data_sufficiency import (
    DATA_SUFFICIENCY_STATUSES,
    HISTORY_DEPENDENT_METRICS,
    REASON_CODES,
    classify_component_sufficiency,
    classify_growth_data_sufficiency,
)
from src.fundamentals.growth import GROWTH_METRICS, calculate_growth
from src.fundamentals.metric_applicability import resolve_metric_applicability


# ============================================================
# CONSTANTS
# ============================================================

def test_data_sufficiency_statuses_is_ok_missing_invalid():
    assert DATA_SUFFICIENCY_STATUSES == {"OK", "MISSING", "INVALID"}


def test_reason_codes_has_insufficient_history_and_concept_not_found():
    """CONCEPT_NOT_FOUND added 2026-09-29 (production-path verification
    round) alongside the original 2026-09-28 INSUFFICIENT_HISTORY --
    see data_sufficiency.py's module docstring."""
    assert REASON_CODES == {"INSUFFICIENT_HISTORY", "CONCEPT_NOT_FOUND"}


def test_history_dependent_metrics_is_exactly_growth_metrics():
    assert HISTORY_DEPENDENT_METRICS == frozenset(GROWTH_METRICS)


def test_data_sufficiency_statuses_never_overlap_applicability_statuses():
    """Data Sufficiency and Applicability are deliberately separate
    vocabularies (module docstring) -- neither NOT_APPLICABLE nor
    UNVERIFIED nor APPLICABLE nor CONDITIONAL belongs in this one."""
    assert DATA_SUFFICIENCY_STATUSES.isdisjoint(
        {"APPLICABLE", "NOT_APPLICABLE", "CONDITIONAL", "UNVERIFIED"}
    )


# ============================================================
# classify_component_sufficiency (unit)
# ============================================================

def test_classify_ok_component():
    component = {"status": "OK", "metric": "revenue_yoy", "value": 0.12, "stars": 4}
    result = classify_component_sufficiency(component)
    assert result == {
        "metric": "revenue_yoy",
        "calculation_status": "OK",
        "reason_code": None,
        "detail": None,
    }


def test_classify_missing_component_maps_to_insufficient_history():
    component = {
        "status": "MISSING",
        "metric": "revenue_yoy",
        "value": None,
        "reason": "No prior-year comparable quarter found.",
    }
    result = classify_component_sufficiency(component)
    assert result["calculation_status"] == "MISSING"
    assert result["reason_code"] == "INSUFFICIENT_HISTORY"
    assert result["detail"] == "No prior-year comparable quarter found."


def test_classify_invalid_component_never_gets_insufficient_history_reason_code():
    component = {
        "status": "INVALID",
        "metric": "revenue_cagr_3y",
        "value": None,
        "reason": "Starting value must be greater than zero.",
    }
    result = classify_component_sufficiency(component)
    assert result["calculation_status"] == "INVALID"
    assert result["reason_code"] is None
    assert result["detail"] == "Starting value must be greater than zero."


def test_classify_rejects_non_history_dependent_metric():
    component = {"status": "MISSING", "metric": "operating_margin", "value": None, "reason": "x"}
    with pytest.raises(ValueError):
        classify_component_sufficiency(component)


def test_classify_rejects_non_dict_input():
    with pytest.raises(ValueError):
        classify_component_sufficiency("not a dict")


def test_classify_unrecognized_status_fails_closed_to_invalid():
    component = {"status": "SOMETHING_NEW", "metric": "fcf_yoy", "value": None}
    result = classify_component_sufficiency(component)
    assert result["calculation_status"] == "INVALID"
    assert result["reason_code"] is None


# ============================================================
# concept_found (2026-09-29 verification round): MISSING must not be
# unconditionally relabeled INSUFFICIENT_HISTORY when the caller knows
# the underlying SEC concept was never found at all -- a data-coverage
# gap, not a temporal-history gap. See classify_component_sufficiency()'s
# own docstring for the full rationale.
# ============================================================

def _missing_component(metric="revenue_yoy", reason="No usable quarterly history found."):
    return {"status": "MISSING", "metric": metric, "value": None, "reason": reason}


def test_missing_defaults_to_insufficient_history_when_concept_found_unspecified():
    """Unchanged default behavior -- matches the 2026-09-28 confirmation's
    literal MISSING -> INSUFFICIENT_HISTORY mapping when the caller says
    nothing about concept coverage."""
    result = classify_component_sufficiency(_missing_component())
    assert result["calculation_status"] == "MISSING"
    assert result["reason_code"] == "INSUFFICIENT_HISTORY"


def test_missing_stays_insufficient_history_when_concept_found_true():
    result = classify_component_sufficiency(_missing_component(), concept_found=True)
    assert result["calculation_status"] == "MISSING"
    assert result["reason_code"] == "INSUFFICIENT_HISTORY"


def test_missing_is_not_insufficient_history_when_concept_found_false():
    """The core fix: a concept that was never found at all (point_in_time's
    get_point_in_time_history() returned None) is a coverage gap, not a
    history-depth gap -- must never be labeled INSUFFICIENT_HISTORY.
    2026-09-29 production-path verification round: this now gets its own
    structured reason_code (CONCEPT_NOT_FOUND) rather than None, so a
    caller can distinguish the two MISSING causes without parsing
    "detail" text."""
    result = classify_component_sufficiency(_missing_component(), concept_found=False)
    assert result["calculation_status"] == "MISSING"
    assert result["reason_code"] == "CONCEPT_NOT_FOUND"
    assert result["detail"] is not None
    assert "concept" in result["detail"].lower()


def test_concept_found_is_ignored_for_ok_components():
    component = {"status": "OK", "metric": "revenue_yoy", "value": 0.1, "stars": 3}
    result = classify_component_sufficiency(component, concept_found=False)
    assert result["calculation_status"] == "OK"
    assert result["reason_code"] is None


def test_concept_found_is_ignored_for_invalid_components():
    component = {"status": "INVALID", "metric": "revenue_yoy", "value": None, "reason": "bad"}
    result = classify_component_sufficiency(component, concept_found=False)
    assert result["calculation_status"] == "INVALID"
    assert result["reason_code"] is None


def test_classify_growth_data_sufficiency_applies_concept_found_per_metric():
    growth_result = _minimal_growth_result(
        revenue_yoy={"status": "MISSING", "metric": "revenue_yoy", "value": None, "reason": "x"},
        eps_yoy={"status": "MISSING", "metric": "eps_yoy", "value": None, "reason": "y"},
    )
    result = classify_growth_data_sufficiency(
        growth_result, concept_found_by_metric={"revenue_yoy": False}
    )
    assert result["revenue_yoy"]["reason_code"] == "CONCEPT_NOT_FOUND"
    assert result["eps_yoy"]["reason_code"] == "INSUFFICIENT_HISTORY"


# ============================================================
# classify_growth_data_sufficiency (unit)
# ============================================================

def _minimal_growth_result(**component_overrides) -> dict:
    components = {
        metric: {"status": "OK", "metric": metric, "value": 0.1, "stars": 3}
        for metric in GROWTH_METRICS
    }
    components.update(component_overrides)
    return {"schema_version": "growth_v0.1", "ticker": "TEST", "components": components}


def test_classify_growth_data_sufficiency_covers_all_five_metrics():
    result = classify_growth_data_sufficiency(_minimal_growth_result())
    assert set(result.keys()) == frozenset(GROWTH_METRICS)
    assert all(v["calculation_status"] == "OK" for v in result.values())


def test_classify_growth_data_sufficiency_rejects_non_dict():
    with pytest.raises(ValueError):
        classify_growth_data_sufficiency([])


def test_classify_growth_data_sufficiency_rejects_missing_components_key():
    with pytest.raises(ValueError):
        classify_growth_data_sufficiency({"ticker": "TEST"})


# ============================================================
# INTEGRATION: SNDK-style spin-off / newly-listed company
# (2026-09-28 confirmation round, section 5 -- the required regression case)
# ============================================================

def _record(value, period_end, *, quarter=None, fy=None, period_type="quarterly"):
    return {
        "period_type": period_type,
        "quarter": quarter,
        "fy": fy,
        "period_end": period_end,
        "value": value,
    }


def _newly_spun_off_historical() -> dict:
    """A company with exactly ONE quarterly observation and ONE annual
    observation -- current-period data exists (it is a real, filing
    public company), but no PIT-gated comparison observation exists from
    a year (or three fiscal years) earlier, because the company simply
    did not exist as a public filer back then. This is the SNDK-style
    spin-off/newly-listed shape the 2026-09-28 confirmation round
    requires as the core Step 5B regression case."""
    revenue_quarterly = [_record(150.0, "2026-06-30", quarter="Q2", fy=2026)]
    revenue_annual = [_record(600.0, "2026-12-31", fy=2026, period_type="annual")]
    operating_income = [_record(30.0, "2026-06-30", quarter="Q2", fy=2026)]
    eps = [_record(1.10, "2026-06-30", quarter="Q2", fy=2026)]
    cfo = [_record(40.0, "2026-06-30", quarter="Q2", fy=2026)]
    capex = [_record(-12.0, "2026-06-30", quarter="Q2", fy=2026)]

    return {
        "revenue": {"status": "OK", "quarterly": revenue_quarterly, "annual": revenue_annual},
        "operating_income": {"status": "OK", "quarterly": operating_income},
        "diluted_eps": {"status": "OK", "quarterly": eps},
        "cfo": {"status": "OK", "quarterly": cfo},
        "capex": {"status": "OK", "quarterly": capex},
    }


def test_sndk_style_spinoff_growth_metrics_are_missing_insufficient_history():
    normalized = {"ticker": "SNDK_LIKE", "company_type": "NON_FINANCIAL", "metrics": {}}
    growth_result = calculate_growth(normalized, derived={}, historical=_newly_spun_off_historical())

    sufficiency = classify_growth_data_sufficiency(growth_result)

    assert set(sufficiency.keys()) == frozenset(GROWTH_METRICS)

    for metric_name, classification in sufficiency.items():
        assert classification["calculation_status"] == "MISSING", (
            f"{metric_name} should be MISSING for a newly-listed company with "
            f"no PIT-gated historical comparison observation, got "
            f"{classification['calculation_status']!r}"
        )
        assert classification["reason_code"] == "INSUFFICIENT_HISTORY"
        # The 2026-09-28 confirmation's explicit prohibition: insufficiency
        # must never surface as an Applicability-vocabulary status.
        assert classification["calculation_status"] not in ("NOT_APPLICABLE", "UNVERIFIED")


def test_sndk_style_spinoff_current_observation_exists_only_comparison_is_missing():
    """Sanity check on the fixture itself: the current-period observation
    IS present (this is a real, filing company, not a company with no data
    at all) -- only the historical comparison is absent. Distinguishes
    "insufficient history" from "no data whatsoever"."""
    historical = _newly_spun_off_historical()
    assert len(historical["revenue"]["quarterly"]) == 1
    assert len(historical["revenue"]["annual"]) == 1


def test_sndk_style_spinoff_applicability_is_independent_of_data_sufficiency():
    """2026-09-28 confirmation, section 5's full principle: Applicability
    and Data Sufficiency are orthogonal. A metric can be fully APPLICABLE
    (a pure Base Applicability / Runtime Modifier question,
    metric_applicability.py's domain) while simultaneously being
    data-insufficient (this module's domain) -- neither system may be used
    to infer or override the other."""
    applicability_rows = [
        {
            "profile_id": "GENERAL_CORPORATE",
            "metric_name": "revenue_yoy",
            "applicability_status": "APPLICABLE",
            "resolver_id": None,
            "policy_owner": "ChatGPT design round",
            "policy_version": "v1.0",
        }
    ]
    applicability_result = resolve_metric_applicability(
        "GENERAL_CORPORATE", "revenue_yoy", {}, "2026-09-28", applicability_rows, modifier_rows=[],
    )
    assert applicability_result["status"] == "APPLICABLE"

    normalized = {"ticker": "SNDK_LIKE", "company_type": "NON_FINANCIAL", "metrics": {}}
    growth_result = calculate_growth(normalized, derived={}, historical=_newly_spun_off_historical())
    sufficiency = classify_growth_data_sufficiency(growth_result)

    # Applicability says APPLICABLE; Data Sufficiency independently says
    # MISSING/INSUFFICIENT_HISTORY. Both are correct and neither overrides
    # the other -- this is the expected, intended coexistence, not a
    # contradiction to be resolved.
    assert applicability_result["status"] == "APPLICABLE"
    assert sufficiency["revenue_yoy"]["calculation_status"] == "MISSING"
    assert sufficiency["revenue_yoy"]["reason_code"] == "INSUFFICIENT_HISTORY"
