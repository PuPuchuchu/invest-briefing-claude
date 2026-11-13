"""STEP 24.4 P1: calculate_standard_yoy() with a negative previous value -> INVALID / NONPOSITIVE_PRIOR_BASE.

Scope (approved policy; nothing else changes):
  * previous < 0  -> INVALID, reason_code NONPOSITIVE_PRIOR_BASE, for numeric current inputs that pass the existing
    validation (a non-numeric current or previous keeps NON_NUMERIC_INPUT, which is checked first);
  * previous == 0 (including -0.0) -> unchanged ZERO_PRIOR_BASE;
  * previous > 0  -> unchanged formula (current / previous) - 1 and unchanged stars;
  * applies to revenue_yoy and operating_income_yoy only (the two users of calculate_standard_yoy);
  * a limitation is added only for INVALID + NONPOSITIVE_PRIOR_BASE on those two metrics;
  * weights, thresholds, minimum-3 rule and re-normalisation are untouched; data_sufficiency keeps
    INVALID -> reason_code None.
Only growth.py, data_sufficiency.py and batch_coverage.py are imported (no network, no SEC history module).
"""
from __future__ import annotations

import copy

import pytest

from src.fundamentals import batch_coverage, growth
from src.fundamentals.data_sufficiency import classify_component_sufficiency
from src.fundamentals.growth import calculate_standard_yoy

METRICS = ("revenue_yoy", "operating_income_yoy")
REASON = "Previous value must be greater than zero for conventional YoY calculation."
LIMITATION = "YoY growth is not defined when the prior-year comparable-quarter value is not positive."
RATE = {"revenue_yoy": growth.rate_revenue_yoy, "operating_income_yoy": growth.rate_operating_income_yoy}

BOUNDARY = [
    (-100, 100, "INVALID", "NONPOSITIVE_PRIOR_BASE", None),
    (-100, -50, "INVALID", "NONPOSITIVE_PRIOR_BASE", None),
    (-100, -200, "INVALID", "NONPOSITIVE_PRIOR_BASE", None),
    (-100, 0, "INVALID", "NONPOSITIVE_PRIOR_BASE", None),
    (0, 50, "INVALID", "ZERO_PRIOR_BASE", None),
    (-0.0, 50, "INVALID", "ZERO_PRIOR_BASE", None),
    (100, -50, "OK", None, -1.5),
    (100, 120, "OK", None, 0.2),
]


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("previous,current,status,code,value", BOUNDARY)
def test_boundary_table(metric, previous, current, status, code, value):
    r = calculate_standard_yoy(metric, current, previous)
    assert r["status"] == status and r["metric"] == metric
    assert r.get("reason_code") == code
    assert r["value"] == value
    if code == "NONPOSITIVE_PRIOR_BASE":
        assert r["reason"] == REASON


@pytest.mark.parametrize("metric", METRICS)
def test_positive_prior_to_negative_current_keeps_one_star(metric):
    r = calculate_standard_yoy(metric, -50, 100)
    assert r["value"] == -1.5 and RATE[metric](r["value"])["stars"] == 1


@pytest.mark.parametrize("metric", METRICS)
def test_positive_prior_formula_unchanged(metric):
    for prev in (1, 7, 134, 2.5, 10**12):
        for cur in (-3, 0, 0.5, 1, 134, 1990, 10**12):
            r = calculate_standard_yoy(metric, cur, prev)
            assert r["status"] == "OK" and r["value"] == round(float(cur) / prev - 1.0, 10) and "reason_code" not in r


def test_negative_prior_checked_after_non_numeric_and_zero():
    assert calculate_standard_yoy("m", None, -1)["reason_code"] == "NON_NUMERIC_INPUT"
    assert calculate_standard_yoy("m", "3", -1)["reason_code"] == "NON_NUMERIC_INPUT"
    assert calculate_standard_yoy("m", 5, -0.0)["reason_code"] == "ZERO_PRIOR_BASE"


def test_other_growth_functions_unchanged():
    p = growth.calculate_positive_yoy("eps_yoy", 5, -1)
    assert (p["status"], p["reason_code"]) == ("INVALID", "NONPOSITIVE_PRIOR_BASE")
    assert growth.calculate_positive_yoy("fcf_yoy", 12, 10)["value"] == 0.2
    assert growth.calculate_cagr_3y("revenue_cagr_3y", -1, 100)["reason_code"] == "NONPOSITIVE_PRIOR_BASE"
    assert growth.GROWTH_WEIGHTS == {"revenue_yoy": 0.25, "operating_income_yoy": 0.20, "eps_yoy": 0.25,
                                     "fcf_yoy": 0.20, "revenue_cagr_3y": 0.10}
    assert growth.MIN_VALID_COMPONENTS == 3


# ---------------------------------------------------------------- calculate_growth integration
def q(v, end, fy, quarter="Q3"):
    return {"status": "OK", "period_end": end, "value": v, "fy": fy, "quarter": quarter, "period_type": "quarterly"}


def history(opinc=(-61, -57), revenue=(100, 123)):
    return {
        "revenue": {"quarterly": [q(revenue[0], "2025-09-30", 2025), q(revenue[1], "2026-09-30", 2026)]},
        "operating_income": {"quarterly": [q(opinc[0], "2025-09-30", 2025), q(opinc[1], "2026-09-30", 2026)]},
        "cfo": {"quarterly": [q(10, "2025-09-30", 2025), q(30, "2026-09-30", 2026)]},
        "capex": {"quarterly": [q(-4, "2025-09-30", 2025), q(-5, "2026-09-30", 2026)]},
        "diluted_eps": {"quarterly": [q(1.0, "2025-09-30", 2025), q(1.5, "2026-09-30", 2026)]},
    }


def run(hist):
    return growth.calculate_growth({"ticker": "T", "metrics": {}}, {}, historical=hist)


def expected_score(components):
    ok = {k: w for k, w in growth.GROWTH_WEIGHTS.items() if components[k]["status"] == "OK"}
    return round(sum(components[k]["stars"] * w for k, w in ok.items()) / sum(ok.values()), 4)


@pytest.mark.parametrize("opinc", [(-61, -57), (-134, 1990), (-10, -40), (-10, 0)])
def test_negative_prior_operating_income_in_calculate_growth(opinc):
    g = run(history(opinc=opinc))
    c = g["components"]["operating_income_yoy"]
    assert (c["status"], c["reason_code"], c["value"], c["stars"]) == ("INVALID", "NONPOSITIVE_PRIOR_BASE", None, None)
    assert "source" not in c and "current_period_end" not in c        # OK-only metadata, as for every INVALID
    assert g["limitations"]["operating_income_yoy"] == LIMITATION
    assert "revenue_yoy" not in g["limitations"]
    # revenue, eps, fcf valid; CAGR has no annual history here; operating income excluded and re-normalised
    assert g["growth"]["valid_components"] == 3 and g["growth"]["score"] == expected_score(g["components"])
    # no false "revenue up / operating income down" warning from the undefined ratio
    assert "revenue_operating_income_divergence" not in g["warnings"]


def test_other_components_identical_to_positive_prior_run():
    neg, pos = run(history(opinc=(-61, -57))), run(history(opinc=(61, 57)))
    for k in ("revenue_yoy", "eps_yoy", "fcf_yoy", "revenue_cagr_3y"):
        assert neg["components"][k] == pos["components"][k]
    assert pos["components"]["operating_income_yoy"]["status"] == "OK"
    assert "operating_income_yoy" not in pos["limitations"]


def test_negative_prior_revenue_in_calculate_growth():
    g = run(history(revenue=(-5, 123), opinc=(20, 25)))
    c = g["components"]["revenue_yoy"]
    assert (c["status"], c["reason_code"]) == ("INVALID", "NONPOSITIVE_PRIOR_BASE")
    assert g["limitations"]["revenue_yoy"] == LIMITATION and "operating_income_yoy" not in g["limitations"]


def test_limitation_only_for_negative_prior_invalid():
    missing = run({k: v for k, v in history().items() if k != "operating_income"})
    assert missing["components"]["operating_income_yoy"]["status"] == "MISSING"
    assert "operating_income_yoy" not in missing["limitations"]
    zero = run(history(opinc=(0, 25)))
    assert zero["components"]["operating_income_yoy"]["reason_code"] == "ZERO_PRIOR_BASE"
    assert "operating_income_yoy" not in zero["limitations"]
    ok = run(history(opinc=(20, 25)))
    assert set(ok["limitations"]) == {"revenue_cagr_3y"}           # unchanged pre-existing keys only


def test_positive_prior_to_negative_current_still_scored_with_warning():
    g = run(history(opinc=(20, -10), revenue=(100, 123)))
    c = g["components"]["operating_income_yoy"]
    assert c["status"] == "OK" and c["value"] == -1.5 and c["stars"] == 1
    assert "revenue_operating_income_divergence" in g["warnings"]
    assert "operating_income_yoy" not in g["limitations"]


def test_minimum_three_rule_unchanged():
    hist = history(opinc=(-61, -57))
    hist["diluted_eps"] = {"quarterly": [q(-1.0, "2025-09-30", 2025), q(-0.5, "2026-09-30", 2026)]}
    hist["cfo"] = {"quarterly": [q(-10, "2025-09-30", 2025), q(-5, "2026-09-30", 2026)]}
    g = run(hist)
    assert g["growth"]["valid_components"] == 1 and g["growth"]["status"] == "MISSING" and g["growth"]["score"] is None


# ---------------------------------------------------------------- downstream: data_sufficiency, batch coverage
def test_data_sufficiency_keeps_invalid_without_reason_code():
    comp = calculate_standard_yoy("operating_income_yoy", 1990, -134)
    s = classify_component_sufficiency(comp)
    assert (s["calculation_status"], s["reason_code"], s["detail"]) == ("INVALID", None, REASON)


def test_batch_coverage_counts_the_new_code():
    comps = run(history(opinc=(-61, -57)))["components"]
    block = batch_coverage._status_block(comps)
    assert block["operating_income_yoy"] == {"by_status": {"INVALID": 1}, "by_reason_code": {"NONPOSITIVE_PRIOR_BASE": 1}}
    assert block["revenue_yoy"] == {"by_status": {"OK": 1}, "by_reason_code": {}}
    pos = batch_coverage._status_block(run(history(opinc=(61, 57)))["components"])
    assert pos["operating_income_yoy"] == {"by_status": {"OK": 1}, "by_reason_code": {}}


def test_inputs_not_mutated():
    hist = history(opinc=(-61, -57)); before = copy.deepcopy(hist)
    run(hist)
    assert hist == before
