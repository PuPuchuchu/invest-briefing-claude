"""A-2: non-finite numbers in Growth (design v3, approved 2026-11-13).

Order in all three growth functions (positive YoY, standard YoY, CAGR):
  NON_NUMERIC_INPUT -> NON_FINITE_INPUT (NaN, +/-inf, ints beyond float range) -> existing policy (P1 kept)
  -> computed result not finite -> NON_FINITE_RESULT.
CAGR: after the existing start/end checks, a non-numeric / non-finite `years` is INVALID_PERIOD_PARAMETER.
Rating guard: _rate_growth_value never turns a non-finite value into a star; calculate_growth makes such a
component INVALID / NON_FINITE_RESULT, excluded by the unchanged re-normalisation.
_is_number (also used to filter history records) is unchanged. Only growth, data_sufficiency and batch_coverage
are imported.
"""
from __future__ import annotations

import copy
import itertools
import math
import random

import pytest

from src.fundamentals import batch_coverage, growth
from src.fundamentals.data_sufficiency import classify_component_sufficiency
from src.fundamentals.growth import calculate_cagr_3y, calculate_positive_yoy, calculate_standard_yoy

NAN, INF = float("nan"), float("inf")
NON_FINITE = [NAN, INF, -INF, 10 ** 400, -(10 ** 400)]
YOY = [calculate_positive_yoy, calculate_standard_yoy]
ALL = YOY + [calculate_cagr_3y]
METRICS = ["revenue_yoy", "operating_income_yoy", "eps_yoy", "fcf_yoy", "revenue_cagr_3y"]


def code(r):
    return (r["status"], r.get("reason_code"), r["value"])


# ---------------------------------------------------------------- 1. NON_FINITE_INPUT
@pytest.mark.parametrize("fn", ALL)
@pytest.mark.parametrize("bad", NON_FINITE, ids=["nan", "inf", "-inf", "1e400", "-1e400"])
def test_non_finite_input_either_side(fn, bad):
    for args in ((bad, 100), (100, bad), (bad, bad)):
        assert code(fn("m", *args)) == ("INVALID", "NON_FINITE_INPUT", None)


@pytest.mark.parametrize("fn", ALL)
def test_underflow_from_huge_int_is_blocked(fn):
    # 1 / 10**400 used to give a plausible -1.0 (-100%) for the YoY functions
    assert code(fn("m", 1, 10 ** 400))[1] == "NON_FINITE_INPUT"


def test_huge_int_no_longer_raises():
    assert code(calculate_standard_yoy("m", 10 ** 400, 1))[1] == "NON_FINITE_INPUT"


# ---------------------------------------------------------------- 2. precedence
@pytest.mark.parametrize("fn", ALL)
def test_non_numeric_still_first(fn):
    for a, b in ((None, NAN), (NAN, "3"), (True, INF), ([], -INF)):
        assert code(fn("m", a, b))[1] == "NON_NUMERIC_INPUT"


def test_existing_policies_after_the_finite_check():
    assert code(calculate_standard_yoy("m", 5, -0.0))[1] == "ZERO_PRIOR_BASE"
    assert code(calculate_standard_yoy("m", 5, 0))[1] == "ZERO_PRIOR_BASE"
    assert code(calculate_standard_yoy("m", 1990, -134))[1] == "NONPOSITIVE_PRIOR_BASE"      # P1 kept
    assert code(calculate_positive_yoy("m", 5, -1))[1] == "NONPOSITIVE_PRIOR_BASE"
    assert code(calculate_positive_yoy("m", -1, 5))[1] == "NONPOSITIVE_CURRENT"
    assert code(calculate_cagr_3y("m", 0, 100))[1] == "NONPOSITIVE_PRIOR_BASE"
    assert code(calculate_cagr_3y("m", 100, -1))[1] == "NEGATIVE_END_VALUE"
    # a non-finite previous is NON_FINITE_INPUT, not the P1 code (A-2 check comes first)
    assert code(calculate_standard_yoy("m", 5, -INF))[1] == "NON_FINITE_INPUT"


# ---------------------------------------------------------------- 3. NON_FINITE_RESULT
@pytest.mark.parametrize("fn,a,b", [(calculate_positive_yoy, 1e308, 1e-10), (calculate_standard_yoy, 1e308, 1e-10),
                                    (calculate_standard_yoy, -1e308, 1e-10), (calculate_cagr_3y, 1e-300, 1e300)])
def test_non_finite_result(fn, a, b):
    r = fn("m", a, b)
    assert code(r) == ("INVALID", "NON_FINITE_RESULT", None)
    assert r["reason"] == "Computed growth is not a finite number."


# ---------------------------------------------------------------- 4. CAGR period parameter
@pytest.mark.parametrize("years", [NAN, INF, -INF, 10 ** 400, "3", True, None, []],
                         ids=["nan", "inf", "-inf", "1e400", "str", "bool", "none", "list"])
def test_cagr_years_must_be_a_finite_number(years):
    assert code(calculate_cagr_3y("c", 100, 120, years=years))[1] == "INVALID_PERIOD_PARAMETER"


def test_cagr_years_existing_results_unchanged():
    assert calculate_cagr_3y("c", 100, 120, years=0)["reason"] == "Years must be greater than zero."
    assert calculate_cagr_3y("c", 100, 120, years=-1)["reason_code"] == "INVALID_PERIOD_PARAMETER"
    assert calculate_cagr_3y("c", 100, 120)["value"] == round((120 / 100) ** (1 / 3) - 1, 10)
    assert calculate_cagr_3y("c", 100, 120, years=3)["value"] == 0.0626585692
    for y in (1, 2.5, 5):
        assert calculate_cagr_3y("c", 100, 120, years=y)["value"] == round((120 / 100) ** (1 / y) - 1, 10)
    # start/end checks keep precedence over the period check
    assert code(calculate_cagr_3y("c", 0, 120, years=INF))[1] == "NONPOSITIVE_PRIOR_BASE"
    assert code(calculate_cagr_3y("c", 100, -1, years=NAN))[1] == "NEGATIVE_END_VALUE"


# ---------------------------------------------------------------- 5. finite results unchanged
@pytest.mark.parametrize("fn", YOY)
def test_finite_yoy_formula_unchanged(fn):
    for prev, cur in itertools.product((1, 7, 2.5, 134, 10 ** 12), (0.5, 1, 134, 1990, 10 ** 12)):
        assert fn("m", cur, prev)["value"] == round(cur / prev - 1.0, 10)


# ---------------------------------------------------------------- 6. rating guard
@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("bad", [NAN, INF, -INF, 10 ** 400], ids=["nan", "inf", "-inf", "1e400"])
def test_rating_guard(metric, bad):
    r = growth._rate_growth_value(metric, bad)
    assert (r["status"], r["stars"], r["value"], r["reason_code"]) == ("INVALID", None, None, "NON_FINITE_RESULT")


def test_finite_ratings_unchanged():
    expected = {  # value -> stars for the YoY thresholds (0, .05, .15, .30) / revenue & CAGR (0, .05, .10, .20)
        "eps_yoy": [(-1.0, 1), (-1e-9, 1), (0.0, 2), (0.0499, 2), (0.05, 3), (0.1499, 3), (0.15, 4), (0.2999, 4), (0.3, 5), (1e12, 5)],
        "revenue_yoy": [(-1.0, 1), (0.0, 2), (0.05, 3), (0.0999, 3), (0.1, 4), (0.1999, 4), (0.2, 5)],
    }
    for metric, rows in expected.items():
        for v, stars in rows:
            assert growth._rate_growth_value(metric, v) == {"status": "OK", "metric": metric, "value": v, "stars": stars}
    assert growth._rate_growth_value("fcf_yoy", None) == {"status": "INVALID", "metric": "fcf_yoy", "value": None, "stars": None}


def test_rating_guard_unreachable_through_public_functions():
    rnd = random.Random(24)
    pool = [0, 1, -1, 2.5, 1e308, 1e-308, 5e-324, -1e308, 10 ** 300, 10 ** 400, NAN, INF, -INF, None, "3", True, -0.0]
    for _ in range(5000):
        a, b = rnd.choice(pool), rnd.choice(pool)
        for fn in ALL:
            r = fn("eps_yoy", a, b)
            if r["status"] == "OK":
                assert math.isfinite(r["value"])
                assert growth._rate_growth_value("eps_yoy", r["value"])["status"] == "OK"


# ---------------------------------------------------------------- 7. calculate_growth integration
def q(v, end, fy):
    return {"status": "OK", "period_end": end, "value": v, "fy": fy, "quarter": "Q3", "period_type": "quarterly"}


def history(**over):
    h = {"revenue": [100, 123], "operating_income": [20, 25], "cfo": [10, 30], "capex": [-4, -5], "diluted_eps": [1.0, 1.5]}
    h.update(over)
    return {k: {"quarterly": [q(v[0], "2025-09-30", 2025), q(v[1], "2026-09-30", 2026)]} for k, v in h.items()}


def run(hist):
    return growth.calculate_growth({"ticker": "T", "metrics": {}}, {}, historical=hist)


def test_nan_history_record_is_flagged_not_substituted():
    # _is_number is unchanged, so the NaN record is still selected for its period; A-2 flags it at the calculation
    g = run(history(diluted_eps=[1.0, NAN]))
    c = g["components"]["eps_yoy"]
    assert (c["status"], c["reason_code"], c["stars"]) == ("INVALID", "NON_FINITE_INPUT", None)
    assert g["growth"]["valid_components"] == 3


def test_forced_non_finite_ok_component_becomes_invalid(monkeypatch):
    orig = growth.calculate_positive_yoy
    monkeypatch.setattr(growth, "calculate_positive_yoy",
                        lambda m, c, p: {"status": "OK", "metric": m, "value": NAN} if m == "eps_yoy" else orig(m, c, p))
    g = run(history())
    c = g["components"]["eps_yoy"]
    assert (c["status"], c["reason_code"], c["stars"], c["value"]) == ("INVALID", "NON_FINITE_RESULT", None, None)
    comps = g["components"]
    ok = {k: w for k, w in growth.GROWTH_WEIGHTS.items() if comps[k]["status"] == "OK"}
    assert g["growth"]["score"] == round(sum(comps[k]["stars"] * w for k, w in ok.items()) / sum(ok.values()), 4)
    assert g["growth"]["valid_components"] == 3          # revenue, operating income, fcf (no annual history for CAGR)


def test_finite_calculate_growth_unchanged_shape():
    g = run(history())
    assert all(g["components"][k]["status"] == "OK" for k in ("revenue_yoy", "operating_income_yoy", "eps_yoy", "fcf_yoy"))
    assert "NON_FINITE" not in str(g)


def test_inputs_not_mutated():
    h = history(diluted_eps=[1.0, NAN]); before = copy.deepcopy(h)
    run(h)
    assert str(h) == str(before)                         # NaN != NaN, so compare the representation


# ---------------------------------------------------------------- 8. downstream
def test_data_sufficiency_and_batch_coverage():
    for r in (calculate_positive_yoy("eps_yoy", NAN, 1), calculate_standard_yoy("revenue_yoy", 1e308, 1e-10)):
        s = classify_component_sufficiency(r)
        assert (s["calculation_status"], s["reason_code"], s["detail"]) == ("INVALID", None, r["reason"])
    block = batch_coverage._status_block(run(history(diluted_eps=[1.0, NAN]))["components"])
    assert block["eps_yoy"] == {"by_status": {"INVALID": 1}, "by_reason_code": {"NON_FINITE_INPUT": 1}}


def test_registered_codes():
    assert {"NON_FINITE_INPUT", "NON_FINITE_RESULT"} <= growth.GROWTH_REASON_CODES
    assert len(growth.GROWTH_REASON_CODES) == 9                  # STEP 24.6-N: + SAME_FILING_COMPARATIVE_UNAVAILABLE
