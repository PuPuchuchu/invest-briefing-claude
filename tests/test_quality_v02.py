"""Quality v0.2 (P2 design v3 + v3.1) unit tests.

Pure-function tests of src.fundamentals.quality.calculate_quality_v02():
no orchestrator, no SEC history, no network. The v0.1 functions and
QUALITY_WEIGHTS are covered by tests/test_quality.py and are not touched.
"""

from __future__ import annotations

import copy
import itertools
import math

import pytest

from src.fundamentals import quality as q
from src.fundamentals.quality import (
    QUALITY_REASON_CODES,
    QUALITY_V02_INDEPENDENT_METRICS,
    QUALITY_V02_SCORE_WEIGHTS,
    QUALITY_WEIGHTS,
    calculate_quality_v02,
    validate_quality_v02,
)

EVAL = "2026-11-17"
FLOW = {
    "period_start": "2025-01-01",
    "period_end": "2025-12-31",
    "fy": 2025,
    "fp": "FY",
    "period_kind": "DURATION",
    "period_class": "ANNUAL_FY",
    "unit": "USD",
    "accession": "0000000000-26-000001",
}
BAL_DATE = "2026-09-30"


def _flow(value, **kw):
    return {"status": "OK", "value": value, **FLOW, **kw}


def _bal(value, date=BAL_DATE, **kw):
    return {"status": "OK", "value": value, "unit": "USD", "period_end": date, **kw}


def _total_debt(value, dates=(BAL_DATE, BAL_DATE), status="OK", **kw):
    comps = {
        "current_debt": {"status": "OK", "value": 1, "period_end": dates[0]},
        "noncurrent_debt": {"status": "OK", "value": 1, "period_end": dates[1]},
    }
    return {"status": status, "value": value if status == "OK" else None, "unit": "USD", "components": comps, **kw}


def _inputs(rev=1000.0, oi=200.0, ni=150.0, cfo=300.0, capex=-100.0, cash=500.0, debt=800.0, company_type="NON_FINANCIAL"):
    normalized = {
        "ticker": "TEST",
        "company_type": company_type,
        "metrics": {
            "revenue": _flow(rev),
            "operating_income": _flow(oi),
            "net_income": _flow(ni),
            "cfo": _flow(cfo),
            "capex": _flow(capex),
            "cash": _bal(cash),
            "total_debt": _total_debt(debt),
        },
    }
    fcf = cfo - abs(capex)
    derived = {"derived_metrics": {
        "fcf": {"status": "OK", "value": fcf},
        "operating_margin": {"status": "OK", "value": oi / rev},
        "net_margin": {"status": "OK", "value": ni / rev},
        "fcf_margin": {"status": "OK", "value": fcf / rev},
        "net_debt": {"status": "OK", "value": debt - cash},
        "debt_to_cash": {"status": "OK", "value": debt / cash},
    }}
    return normalized, derived


def _run(normalized, derived, evaluation_date=EVAL):
    return calculate_quality_v02(normalized, derived, evaluation_date=evaluation_date)


def _set_derived(derived, name, value, status="OK"):
    derived["derived_metrics"][name] = {"status": status, "value": value}


# ------------------------------------------------------------------ D1

def test_v02_weights_are_v01_independent_weights_renormalized():
    assert set(QUALITY_V02_SCORE_WEIGHTS) == set(QUALITY_V02_INDEPENDENT_METRICS)
    assert math.isclose(sum(QUALITY_V02_SCORE_WEIGHTS.values()), 1.0, abs_tol=1e-12)
    for name, w in QUALITY_V02_SCORE_WEIGHTS.items():
        assert math.isclose(w, QUALITY_WEIGHTS[name] / 0.90, rel_tol=1e-12)
    assert "profit_to_cash_consistency" not in QUALITY_V02_SCORE_WEIGHTS


def test_v01_quality_weights_unchanged():
    assert QUALITY_WEIGHTS == {
        "operating_margin": 0.20, "net_margin": 0.10, "fcf_margin": 0.20,
        "fcf_conversion": 0.20, "net_debt_to_fcf": 0.15, "debt_to_cash": 0.05,
        "profit_to_cash_consistency": 0.10,
    }


def test_informational_duplicate_is_excluded_from_score_counts_and_fraction():
    n, d = _inputs()
    r = _run(n, d)
    info = r["components"]["profit_to_cash_consistency"]
    assert info["role"] == "INFORMATIONAL" and info["duplicate_of"] == "fcf_conversion" and info["weight"] == 0.0
    assert info["value"] == pytest.approx(r["components"]["fcf_conversion"]["value"])
    e = r["element"]
    assert e["rated_count"] == 6 and e["independent_total"] == 6
    assert "profit_to_cash_consistency" not in e["normalized_score_weights"]
    assert e["scored_weight_fraction"] == pytest.approx(1.0)


# ------------------------------------------------------------------ D2 fcf_conversion

VALUES = [1.0, 0.0, -0.0, -1.0]


@pytest.mark.parametrize("ni,fcf", list(itertools.product(VALUES, VALUES)))
def test_fcf_conversion_sign_table(ni, fcf):
    n, d = _inputs()
    n["metrics"]["net_income"]["value"] = ni
    _set_derived(d, "fcf", fcf)
    c = _run(n, d)["components"]["fcf_conversion"]
    if ni > 0:
        assert c["status"] == "OK"
        if fcf <= 0:
            assert c["rating"] == 1.0 and c["stars"] == 1
    else:
        assert c["status"] == "NOT_MEANINGFUL" and c["reason_code"] == "NONPOSITIVE_NET_INCOME"
        assert c["value"] is None and c["rating"] is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("which", ["net_income", "fcf"])
def test_fcf_conversion_non_finite_input(bad, which):
    n, d = _inputs()
    if which == "net_income":
        n["metrics"]["net_income"]["value"] = bad
    else:
        _set_derived(d, "fcf", bad)
    c = _run(n, d)["components"]["fcf_conversion"]
    assert c["status"] == "INVALID" and c["reason_code"] == "NON_FINITE_INPUT"


def test_non_finite_result_is_invalid():
    n, d = _inputs()
    n["metrics"]["net_income"]["value"] = 1e-320  # subnormal: fcf / ni overflows to inf
    c = _run(n, d)["components"]["fcf_conversion"]
    assert c["status"] == "INVALID" and c["reason_code"] == "NON_FINITE_RESULT"


# ------------------------------------------------------------------ D2 net_debt_to_fcf

@pytest.mark.parametrize("nd,fcf", list(itertools.product(VALUES, VALUES)))
def test_net_debt_to_fcf_sign_table_and_sign_field(nd, fcf):
    n, d = _inputs()
    _set_derived(d, "net_debt", nd)
    _set_derived(d, "fcf", fcf)
    c = _run(n, d)["components"]["net_debt_to_fcf"]
    assert c["net_debt_sign"] == ("ZERO" if nd == 0 else "NEGATIVE" if nd < 0 else "POSITIVE")
    if fcf > 0:
        assert c["status"] == "OK" and c["reason_code"] is None
        if nd <= 0:
            assert c["stars"] == 5
    elif nd > 0:
        assert c["status"] == "OK" and c["rating"] == 1.0 and c["value"] is None
        assert c["reason_code"] == "NONPOSITIVE_FCF_WITH_NET_DEBT"
    else:
        assert c["status"] == "NOT_MEANINGFUL"
        assert c["reason_code"] == "NONPOSITIVE_FCF_WITH_NONPOSITIVE_NET_DEBT"


def test_net_debt_sign_is_null_when_net_debt_unavailable():
    n, d = _inputs()
    _set_derived(d, "net_debt", None, status="MISSING")
    assert _run(n, d)["components"]["net_debt_to_fcf"]["net_debt_sign"] is None


# ------------------------------------------------------------------ margins and debt_to_cash

@pytest.mark.parametrize("num", [100.0, 0.0, -100.0])
def test_margins_rate_any_numerator_sign_with_floor(num):
    n, d = _inputs()
    _set_derived(d, "operating_margin", num / 1000.0)
    c = _run(n, d)["components"]["operating_margin"]
    assert c["status"] == "OK"
    if num <= 0:
        assert c["rating"] == 1.0


def test_upstream_invalid_margin_is_passed_through():
    n, d = _inputs()
    _set_derived(d, "net_margin", None, status="INVALID")
    c = _run(n, d)["components"]["net_margin"]
    assert c["status"] == "INVALID" and c["reason_code"] == "INPUT_INVALID:net_margin"
    assert c["upstream_reason_code"] == "UPSTREAM_REASON_UNSPECIFIED"


@pytest.mark.parametrize("debt,cash,stars", [(0.0, 500.0, 5), (800.0, 500.0, 3)])
def test_debt_to_cash_rates(debt, cash, stars):
    n, d = _inputs(debt=debt, cash=cash)
    c = _run(n, d)["components"]["debt_to_cash"]
    assert c["status"] == "OK" and c["stars"] == stars
    assert c["liquidity_basis"] == "CASH_AND_CASH_EQUIVALENTS_ONLY"
    assert "mixed_period_ratio" not in c


def test_debt_to_cash_cash_zero_upstream_invalid():
    n, d = _inputs()
    _set_derived(d, "debt_to_cash", None, status="INVALID")
    c = _run(n, d)["components"]["debt_to_cash"]
    assert c["status"] == "INVALID" and c["reason_code"] == "INPUT_INVALID:debt_to_cash"


# ------------------------------------------------------------------ thresholds and D4

@pytest.mark.parametrize("value,stars", [
    (0.0499, 1), (0.05, 2), (0.0999, 2), (0.10, 3), (0.20, 4), (0.2999, 4), (0.30, 5),
])
def test_operating_margin_threshold_edges(value, stars):
    n, d = _inputs()
    _set_derived(d, "operating_margin", value)
    assert _run(n, d)["components"]["operating_margin"]["stars"] == stars


def test_every_rating_is_floored_at_one():
    n, d = _inputs(oi=-50.0, ni=1.0, cfo=10.0, capex=-200.0, debt=5000.0, cash=10.0)
    r = _run(n, d)
    for comp in r["components"].values():
        if comp["status"] == "OK":
            assert comp["rating"] >= 1.0


# ------------------------------------------------------------------ D3

def _with_status(n, d, ok, nm):
    """Make exactly `ok` independent metrics OK and `nm` NOT_MEANINGFUL."""
    # fcf_conversion NM via NI <= 0 (also needs net_margin to stay OK: set derived directly)
    if nm >= 1:
        n["metrics"]["net_income"]["value"] = -1.0
    if nm >= 2:
        _set_derived(d, "fcf", -10.0)
        _set_derived(d, "net_debt", -5.0)
    ok_now = 6 - nm
    to_drop = ok_now - ok
    droppable = ["debt_to_cash", "operating_margin", "fcf_margin", "net_margin"]
    for name in droppable[:to_drop]:
        _set_derived(d, name, None, status="MISSING")
    return n, d


@pytest.mark.parametrize("ok,nm,expected", [
    (3, 1, "OK"), (3, 0, "MISSING"), (2, 2, "MISSING"), (4, 0, "OK"), (4, 2, "OK"),
])
def test_d3_coverage_edges(ok, nm, expected):
    n, d = _with_status(*_inputs(), ok=ok, nm=nm)
    e = _run(n, d)["element"]
    assert (e["rated_count"], e["not_meaningful_count"]) == (ok, nm)
    assert e["status"] == expected
    if expected == "MISSING":
        assert e["reason_code"] == "INSUFFICIENT_INDEPENDENT_COMPONENTS"
        for key in ("score", "stars", "scored_weight_fraction", "normalized_score_weights"):
            assert e[key] is None
    else:
        assert math.isclose(sum(e["normalized_score_weights"].values()), 1.0, abs_tol=1e-12)
        rated = [m for m, c in _run(n, d)["components"].items()
                 if c["status"] == "OK" and m in QUALITY_V02_SCORE_WEIGHTS]
        assert e["scored_weight_fraction"] == pytest.approx(sum(QUALITY_V02_SCORE_WEIGHTS[m] for m in rated))


# ------------------------------------------------------------------ D5

def test_financial_is_not_applicable_and_computes_nothing():
    n, d = _inputs(company_type="FINANCIAL")
    r = _run(n, d)
    e = r["element"]
    assert e["status"] == "NOT_APPLICABLE" and e["reason_code"] == "COMPANY_TYPE_FINANCIAL"
    assert r["components"] == {} and r["periods"] is None
    for key in ("score", "stars", "scored_weight_fraction", "normalized_score_weights"):
        assert e[key] is None


# ------------------------------------------------------------------ D7

@pytest.mark.parametrize("field,value,code", [
    ("period_start", "2025-01-02", "FLOW_PERIOD_MISMATCH"),
    ("fy", 2024, "FLOW_PERIOD_MISMATCH"),
    ("fp", "Q4", "FLOW_PERIOD_MISMATCH"),
    ("period_kind", "INSTANT", "FLOW_PERIOD_MISMATCH"),
    ("period_class", "QUARTER", "FLOW_PERIOD_MISMATCH"),
    ("unit", "EUR", "FLOW_UNIT_MISMATCH"),
])
def test_flow_consistency_each_field(field, value, code):
    n, d = _inputs()
    n["metrics"]["capex"][field] = value  # period_end stays equal
    r = _run(n, d)
    assert r["periods"]["flow_inputs_consistent"] is False
    for name in ("operating_margin", "net_margin", "fcf_margin", "fcf_conversion", "net_debt_to_fcf"):
        assert r["components"][name]["status"] == "INVALID"
        assert r["components"][name]["reason_code"] == code
    assert r["components"]["debt_to_cash"]["status"] == "OK"  # balance/balance ratio


def test_same_period_end_alone_is_not_consistency():
    n, d = _inputs()
    n["metrics"]["revenue"]["period_start"] = "2025-04-01"
    r = _run(n, d)
    assert r["components"]["operating_margin"]["reason_code"] == "FLOW_PERIOD_MISMATCH"


def test_period_fields_and_ages():
    n, d = _inputs()
    r = _run(n, d)
    p = r["periods"]
    assert p["flow_basis"] == "ANNUAL_FY"
    assert (p["flow_period_start"], p["flow_period_end"]) == ("2025-01-01", "2025-12-31")
    assert p["flow_age_days"] == 321
    assert p["cash_as_of"] == BAL_DATE and p["debt_as_of"] == [BAL_DATE]
    assert p["cash_age_days"] == 48 and p["debt_age_days"] == [48]
    assert p["balance_sheet_as_of"] == BAL_DATE and p["balance_dates_consistent"] is True
    nd = r["components"]["net_debt_to_fcf"]
    assert nd["mixed_period_ratio"] is True
    assert nd["cash_flow_gap_days"] == 273 and nd["debt_flow_gap_days"] == [273]
    assert nd["balance_flow_gap_days"] == 273


def test_cash_and_debt_dates_differ_gap_per_component_and_single_gap_null():
    n, d = _inputs()
    n["metrics"]["total_debt"] = _total_debt(800.0, dates=("2026-06-30", "2026-06-30"))
    r = _run(n, d)
    p = r["periods"]
    assert p["balance_sheet_as_of"] is None and p["balance_dates_consistent"] is False
    nd = r["components"]["net_debt_to_fcf"]
    assert nd["cash_flow_gap_days"] == 273
    assert nd["debt_flow_gap_days"] == [181]
    assert nd["balance_flow_gap_days"] is None


def test_negative_gap_recorded_as_is():
    n, d = _inputs()
    for name in ("revenue", "operating_income", "net_income", "cfo", "capex"):
        n["metrics"][name].update(period_start="2025-10-01", period_end="2026-09-30")
    n["metrics"]["cash"]["period_end"] = "2026-06-30"
    n["metrics"]["total_debt"] = _total_debt(800.0, dates=("2026-06-30", "2026-06-30"))
    nd = _run(n, d)["components"]["net_debt_to_fcf"]
    assert nd["cash_flow_gap_days"] == -92 and nd["balance_flow_gap_days"] == -92


def test_debt_dates_include_component_sum_and_scope_alignment():
    n, d = _inputs()
    td = n["metrics"]["total_debt"]
    td["component_sum"] = {"applied": True, "components": [{"period_end": BAL_DATE}, {"period_end": BAL_DATE}]}
    td["scope_alignment"] = {"period_end": BAL_DATE}
    assert _run(n, d)["periods"]["debt_as_of"] == [BAL_DATE]


# ------------------------------------------------------------------ future period_end (v3.1 §2.2)

@pytest.mark.parametrize("offset,invalid", [(1, True), (0, False), (-1, False)])
@pytest.mark.parametrize("target", ["flow", "cash", "debt"])
def test_period_end_after_evaluation_date(target, offset, invalid):
    from datetime import date, timedelta
    when = (date.fromisoformat(EVAL) + timedelta(days=offset)).isoformat()
    n, d = _inputs()
    if target == "flow":
        for name in ("revenue", "operating_income", "net_income", "cfo", "capex"):
            n["metrics"][name]["period_end"] = when
        affected = ["operating_margin", "net_margin", "fcf_margin", "fcf_conversion", "net_debt_to_fcf"]
    elif target == "cash":
        n["metrics"]["cash"]["period_end"] = when
        affected = ["net_debt_to_fcf", "debt_to_cash"]
    else:
        n["metrics"]["total_debt"] = _total_debt(800.0, dates=(BAL_DATE, when))
        affected = ["net_debt_to_fcf", "debt_to_cash"]
    r = _run(n, d)
    for name in affected:
        comp = r["components"][name]
        if invalid:
            assert comp["status"] == "INVALID"
            assert comp["reason_code"] == "PERIOD_END_AFTER_EVALUATION_DATE"
        else:
            assert comp["reason_code"] != "PERIOD_END_AFTER_EVALUATION_DATE"


# ------------------------------------------------------------------ upstream pass-through and exposure

def test_upstream_reason_code_passed_through_and_unspecified():
    n, d = _inputs()
    n["metrics"]["total_debt"] = _total_debt(None, status="INCOMPLETE", reason_code="LEASE_SCOPE_MISMATCH")
    _set_derived(d, "net_debt", None, status="MISSING")
    _set_derived(d, "debt_to_cash", None, status="MISSING")
    r = _run(n, d)
    c = r["components"]["debt_to_cash"]
    assert c["status"] == "MISSING" and c["reason_code"] == "INPUT_INCOMPLETE:total_debt"
    assert c["upstream_reason_code"] == "LEASE_SCOPE_MISMATCH"
    n2, d2 = _inputs()
    n2["metrics"]["total_debt"] = _total_debt(None, status="INCOMPLETE")
    _set_derived(d2, "debt_to_cash", None, status="MISSING")
    assert _run(n2, d2)["components"]["debt_to_cash"]["upstream_reason_code"] == "UPSTREAM_REASON_UNSPECIFIED"


def test_withheld_takes_precedence_and_is_withheld():
    n, d = _inputs()
    n["metrics"]["cfo"] = {"status": "WITHHELD", "value": None, "reason_code": "GOVERNANCE_WITHHELD"}
    n["metrics"]["cash"] = {"status": "INCOMPLETE", "value": None, "reason_code": "STALE_PERIOD"}
    _set_derived(d, "fcf", None, status="WITHHELD")
    c = _run(n, d)["components"]["net_debt_to_fcf"]
    assert c["status"] == "WITHHELD" and c["reason_code"].startswith("INPUT_WITHHELD:")
    assert {u["metric"] for u in c["upstream_inputs"]} >= {"cfo", "cash"}


def test_dimensions_unavailable_and_flag_without_penalty():
    n, d = _inputs(ni=50.0, cfo=10.0, capex=-200.0)
    n["metrics"]["total_debt"] = _total_debt(None, status="INCOMPLETE")
    _set_derived(d, "net_debt", None, status="MISSING")
    _set_derived(d, "debt_to_cash", None, status="MISSING")
    r = _run(n, d)
    e = r["element"]
    assert e["assessed_dimensions"]["leverage"] == "NOT_ASSESSED"
    assert e["assessed_dimensions"]["profitability"] == "ASSESSED"
    assert {u["metric"] for u in e["unavailable_components"]} == {"net_debt_to_fcf", "debt_to_cash"}
    assert "NONPOSITIVE_FCF_LEVERAGE_UNASSESSED" in e["flags"]
    rated = {m: c for m, c in r["components"].items() if c["status"] == "OK" and m in QUALITY_V02_SCORE_WEIGHTS}
    assert e["scored_weight_fraction"] == pytest.approx(1 - QUALITY_V02_SCORE_WEIGHTS["net_debt_to_fcf"] - QUALITY_V02_SCORE_WEIGHTS["debt_to_cash"])
    expected = sum(c["rating"] * QUALITY_V02_SCORE_WEIGHTS[m] for m, c in rated.items()) / e["scored_weight_fraction"]
    assert e["score"] == pytest.approx(expected)


def test_partially_assessed_dimension():
    n, d = _inputs()
    _set_derived(d, "operating_margin", None, status="MISSING")
    assert _run(n, d)["element"]["assessed_dimensions"]["profitability"] == "PARTIALLY_ASSESSED"


# ------------------------------------------------------------------ registry and validation

def test_reason_code_registry_is_exactly_the_design_list():
    assert QUALITY_REASON_CODES == {
        "NONPOSITIVE_NET_INCOME", "NONPOSITIVE_FCF_WITH_NET_DEBT", "NONPOSITIVE_FCF_WITH_NONPOSITIVE_NET_DEBT",
        "FLOW_PERIOD_MISMATCH", "FLOW_UNIT_MISMATCH", "PERIOD_END_AFTER_EVALUATION_DATE",
        "NON_FINITE_INPUT", "NON_FINITE_RESULT", "INPUT_MISSING", "INPUT_INCOMPLETE", "INPUT_WITHHELD",
        "INPUT_INVALID", "UPSTREAM_REASON_UNSPECIFIED", "INSUFFICIENT_INDEPENDENT_COMPONENTS",
        "COMPANY_TYPE_FINANCIAL",
    }


def _all_codes(result):
    codes = set()
    e = result["element"]
    if e.get("reason_code"):
        codes.add(e["reason_code"].split(":")[0])
    for c in result["components"].values():
        for key in ("reason_code", "upstream_reason_code"):
            if c.get(key) and key == "reason_code":
                codes.add(c[key].split(":")[0])
        if c.get("upstream_reason_code") == "UPSTREAM_REASON_UNSPECIFIED":
            codes.add("UPSTREAM_REASON_UNSPECIFIED")
    for u in e.get("unavailable_components") or []:
        if u.get("upstream_reason_code") == "UPSTREAM_REASON_UNSPECIFIED":
            codes.add("UPSTREAM_REASON_UNSPECIFIED")
    return codes


def _scenarios():
    yield _inputs()
    yield _inputs(company_type="FINANCIAL")
    n, d = _inputs(); n["metrics"]["net_income"]["value"] = -1.0; yield n, d
    n, d = _inputs(); _set_derived(d, "fcf", -1.0); yield n, d
    n, d = _inputs(); _set_derived(d, "fcf", -1.0); _set_derived(d, "net_debt", -1.0); yield n, d
    n, d = _inputs(); n["metrics"]["capex"]["fy"] = 2024; yield n, d
    n, d = _inputs(); n["metrics"]["capex"]["unit"] = "EUR"; yield n, d
    n, d = _inputs(); n["metrics"]["cash"]["period_end"] = "2026-12-31"; yield n, d
    n, d = _inputs(); n["metrics"]["net_income"]["value"] = math.nan; yield n, d
    n, d = _inputs(); n["metrics"]["net_income"]["value"] = 1e-320; yield n, d
    n, d = _inputs(); n["metrics"]["operating_income"] = {"status": "MISSING", "value": None}; _set_derived(d, "operating_margin", None, "MISSING"); yield n, d
    n, d = _inputs(); n["metrics"]["total_debt"] = _total_debt(None, status="INCOMPLETE"); yield n, d
    n, d = _inputs(); n["metrics"]["cfo"] = {"status": "WITHHELD", "value": None}; yield n, d
    n, d = _inputs(); _set_derived(d, "net_margin", None, "INVALID"); yield n, d
    yield _with_status(*_inputs(), ok=2, nm=2)


def test_every_generated_code_is_registered_and_every_registered_code_is_reachable():
    seen = set()
    for n, d in _scenarios():
        r = _run(n, d)
        assert validate_quality_v02(r) == []
        codes = _all_codes(r)
        assert codes <= QUALITY_REASON_CODES
        seen |= codes
    assert seen == set(QUALITY_REASON_CODES)


def test_validator_rejects_unregistered_code_and_non_null_on_missing():
    n, d = _inputs()
    r = _run(n, d)
    bad = copy.deepcopy(r)
    bad["components"]["net_margin"]["status"] = "MISSING"
    bad["components"]["net_margin"]["value"] = None
    bad["components"]["net_margin"]["rating"] = None
    bad["components"]["net_margin"]["reason_code"] = "MADE_UP_CODE"
    assert "components.net_margin.reason_code" in validate_quality_v02(bad)
    bad2 = copy.deepcopy(r)
    bad2["element"]["status"] = "MISSING"
    assert "element.scored_weight_fraction" in validate_quality_v02(bad2)


def test_self_validation_failure_raises(monkeypatch):
    n, d = _inputs()
    monkeypatch.setattr(q, "validate_quality_v02", lambda data: ["forced"])
    with pytest.raises(ValueError, match="invalid output"):
        _run(n, d)


def test_inputs_are_not_mutated():
    n, d = _inputs()
    n0, d0 = copy.deepcopy(n), copy.deepcopy(d)
    _run(n, d)
    assert n == n0 and d == d0


def test_bad_evaluation_date_raises():
    n, d = _inputs()
    with pytest.raises(ValueError):
        _run(n, d, evaluation_date="not-a-date")
