"""STEP 24.3 A-1: structured reason_code on every Growth INVALID result.

Scope guarantees tested here:
  * each of the 9 INVALID branches returns its fixed, registered code;
  * the code is a literal at the branch (never derived from the reason text), so rewording a message
    cannot change it;
  * status / value / reason (and the exception behaviour) are identical to growth.py at main 64e0f1d
    (golden file generated from that commit);
  * data_sufficiency still never puts a reason_code on INVALID;
  * the Checker v2.4 mirror of the code set equals production.
A-2 (explicit revision): a numeric but non-finite input is NON_FINITE_INPUT (checked right after NON_NUMERIC_INPUT),
a non-finite computed result is NON_FINITE_RESULT (8th registered code), and the CAGR period must be finite. Exactly
K_A2 golden cases change expectation; 17 of them are P1 cases that are re-coded.
STEP 24.4 P1 (explicit revision): calculate_standard_yoy() with a negative previous value is now INVALID /
NONPOSITIVE_PRIOR_BASE. This adds a 10th call site, and exactly K_NEGATIVE_PRIOR golden cases change
expectation; every other golden case must still match. The golden file itself is not modified.
"""
from __future__ import annotations

import ast
import importlib.util
import itertools
import json
import math
from pathlib import Path

import pytest

from src.fundamentals import growth
from src.fundamentals.data_sufficiency import classify_component_sufficiency
from src.fundamentals.growth import (
    GROWTH_REASON_CODES,
    calculate_cagr_3y,
    calculate_positive_yoy,
    calculate_standard_yoy,
)

REPO = Path(__file__).resolve().parents[1]
GROWTH_PY = REPO / "src" / "fundamentals" / "growth.py"
GOLDEN = REPO / "tests" / "fixtures" / "growth_reason_code" / "parity_64e0f1d.json"

BRANCHES = [
    (calculate_positive_yoy, ("m", None, 1), "NON_NUMERIC_INPUT"),
    (calculate_positive_yoy, ("m", 5, 0), "NONPOSITIVE_PRIOR_BASE"),
    (calculate_positive_yoy, ("m", 5, -1), "NONPOSITIVE_PRIOR_BASE"),
    (calculate_positive_yoy, ("m", 0, 5), "NONPOSITIVE_CURRENT"),
    (calculate_positive_yoy, ("m", -1, 5), "NONPOSITIVE_CURRENT"),
    (calculate_positive_yoy, ("m", -1, -1), "NONPOSITIVE_PRIOR_BASE"),   # prior checked first: deterministic
    (calculate_standard_yoy, ("m", "3", 1), "NON_NUMERIC_INPUT"),
    (calculate_standard_yoy, ("m", 5, 0), "ZERO_PRIOR_BASE"),
    (calculate_standard_yoy, ("m", 5, -1), "NONPOSITIVE_PRIOR_BASE"),        # STEP 24.4 P1
    (calculate_cagr_3y, ("m", True, 100), "NON_NUMERIC_INPUT"),
    (calculate_cagr_3y, ("m", 0, 100), "NONPOSITIVE_PRIOR_BASE"),
    (calculate_cagr_3y, ("m", 100, -1), "NEGATIVE_END_VALUE"),
]


def test_registered_code_set_is_closed_and_disjoint_from_sufficiency_codes():
    assert GROWTH_REASON_CODES == frozenset({
        "NON_NUMERIC_INPUT", "NON_FINITE_INPUT", "NONPOSITIVE_PRIOR_BASE", "NONPOSITIVE_CURRENT",
        "ZERO_PRIOR_BASE", "NEGATIVE_END_VALUE", "INVALID_PERIOD_PARAMETER", "NON_FINITE_RESULT",
        "SAME_FILING_COMPARATIVE_UNAVAILABLE"})                     # A-2: 8 codes; STEP 24.6-N: 9
    from src.fundamentals.data_sufficiency import REASON_CODES
    assert not (GROWTH_REASON_CODES & set(REASON_CODES))


@pytest.mark.parametrize("fn,args,code", BRANCHES)
def test_each_invalid_branch_returns_its_code(fn, args, code):
    r = fn(*args)
    assert r["status"] == "INVALID"
    assert r["reason_code"] == code
    assert r["value"] is None


def test_years_parameter_branch():
    r = calculate_cagr_3y("m", 100, 120, years=0)
    assert (r["status"], r["reason_code"]) == ("INVALID", "INVALID_PERIOD_PARAMETER")


def test_every_invalid_call_site_passes_a_registered_literal_code():
    """19 call sites: 9 from A-1, 1 STEP 24.4 P1 negative-prior branch, 8 from A-2 (3 NON_FINITE_INPUT,
    3 NON_FINITE_RESULT result guards, 1 CAGR period check, 1 rating-guard propagation in calculate_growth),
    and 1 STEP 24.6-N same-filing comparative fail-closed branch (explicit revision).
    The third argument is a string literal in the registered set (never computed)."""
    tree = ast.parse(GROWTH_PY.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_build_invalid_metric"]
    assert len(calls) == 19
    for c in calls:
        assert len(c.args) == 3 and not c.keywords
        code = c.args[2]
        assert isinstance(code, ast.Constant) and isinstance(code.value, str) and code.value in GROWTH_REASON_CODES


def test_reason_code_is_invariant_under_message_rewording():
    """Rewrite every human-readable reason literal in a copy of growth.py; codes must not change."""
    src = GROWTH_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for c in ast.walk(tree):
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "_build_invalid_metric":
            c.args[1] = ast.Constant(value="REWORDED MESSAGE")
    ns: dict = {"__name__": "growth_reworded"}
    exec(compile(ast.fix_missing_locations(tree), "growth_reworded.py", "exec"), ns)
    vals = [0, -1, 1, 2.5, None, "3", True]
    for name in ("calculate_positive_yoy", "calculate_standard_yoy", "calculate_cagr_3y"):
        for a, b in itertools.product(vals, vals):
            r1, r2 = getattr(growth, name)("m", a, b), ns[name]("m", a, b)
            assert r1["status"] == r2["status"]
            assert r1.get("reason_code") == r2.get("reason_code")
            if r1["status"] == "INVALID":
                assert r2["reason"] == "REWORDED MESSAGE" and r1["reason"] != r2["reason"]


def _decode(v):
    return float(v["float"]) if isinstance(v, dict) and "float" in v else v


# STEP 24.4 P1: golden cases whose expectation changes, fixed before implementation from the golden file:
# calculate_standard_yoy, previous in {-1, -2.5, -inf} (the negative inputs), current any numeric input (11) -> 3 x 11.
# All 33 were OK in the golden; they are now INVALID / NONPOSITIVE_PRIOR_BASE. 17 of them involve a non-finite input
# (previous -inf, or current nan / inf / -inf): the rule is "previous < 0", which -inf satisfies.
K_NEGATIVE_PRIOR = 33
NEGATIVE_INPUTS = {"-1", "-2.5", "-inf"}
NUMERIC_INPUTS = {"0", "0.0", "-0.0", "1", "-1", "2.5", "-2.5", "1e12", "nan", "inf", "-inf"}
NEG_PRIOR_REASON = "Previous value must be greater than zero for conventional YoY calculation."

# A-2: golden cases whose expectation changes, fixed before implementation (design v3 §5): both inputs numeric and at
# least one of them nan / inf / -inf -> 11*11 - 8*8 = 57 per function, 3 functions -> 171. All other cases keep their
# previous expectation (597). 17 of the 33 P1 cases are among the 171 (re-coded), so 16 keep the P1 expectation.
K_A2 = 171
K_P1_REMAINING = 16
NON_FINITE_INPUTS = {"nan", "inf", "-inf"}
A2_REASON = {"calculate_positive_yoy": "Current and previous values must be finite numbers.",
             "calculate_standard_yoy": "Current and previous values must be finite numbers.",
             "calculate_cagr_3y": "Start and end values must be finite numbers."}


def test_production_parity_with_main_64e0f1d():
    """status / value / reason / exception identical to the pre-A-1 production (golden); only reason_code is added.
    STEP 24.4 P1: exactly K_NEGATIVE_PRIOR standard-YoY negative-previous cases take the new expectation.
    A-2: exactly K_A2 numeric + non-finite cases take NON_FINITE_INPUT; it is checked before the P1 branch."""
    gold = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert gold["growth_py_sha256"] == "01e05f6268c28966361eecf37334b6b7f82ac15b1403a456099fd047b8d18b15"
    inputs = {"0": 0, "0.0": 0.0, "-0.0": -0.0, "1": 1, "-1": -1, "2.5": 2.5, "-2.5": -2.5, "1e12": 10**12,
              "nan": float("nan"), "inf": float("inf"), "-inf": float("-inf"), "None": None, "str3": "3",
              "True": True, "False": False, "list": []}
    assert sorted(inputs) == sorted(gold["inputs"])
    assert len(gold["cases"]) == 3 * 16 * 16
    substituted = a2 = p1_recoded = 0
    for case in gold["cases"]:
        fn = getattr(growth, case["function"])
        exp = case["result"]
        is_p1 = (case["function"] == "calculate_standard_yoy" and case["arg2"] in NEGATIVE_INPUTS
                 and case["arg1"] in NUMERIC_INPUTS)
        is_a2 = (case["arg1"] in NUMERIC_INPUTS and case["arg2"] in NUMERIC_INPUTS
                 and (case["arg1"] in NON_FINITE_INPUTS or case["arg2"] in NON_FINITE_INPUTS))
        if is_p1:
            assert exp.get("status") == "OK", case          # the golden (old) result being replaced
            substituted += 1
        if is_a2:
            exp = {"status": "INVALID", "value": None, "reason": A2_REASON[case["function"]],
                   "keys": ["metric", "reason", "status", "value"]}
            r = fn("m", inputs[case["arg1"]], inputs[case["arg2"]])
            assert r["reason_code"] == "NON_FINITE_INPUT", case
            a2 += 1
            p1_recoded += is_p1
        elif is_p1:
            exp = {"status": "INVALID", "value": None, "reason": NEG_PRIOR_REASON, "keys": ["metric", "reason", "status", "value"]}
            r = fn("m", inputs[case["arg1"]], inputs[case["arg2"]])
            assert r["reason_code"] == "NONPOSITIVE_PRIOR_BASE", case
        try:
            r = fn("m", inputs[case["arg1"]], inputs[case["arg2"]])
        except Exception as e:  # noqa: BLE001 - parity includes the exception type
            assert exp == {"exception": type(e).__name__}, case
            continue
        assert "exception" not in exp, case
        assert r["status"] == exp["status"] and r.get("reason") == exp["reason"], case
        ev, gv = r["value"], _decode(exp["value"])
        assert (ev == gv) or (isinstance(ev, float) and isinstance(gv, float) and math.isnan(ev) and math.isnan(gv)), case
        added = set(r) - set(exp["keys"])
        assert added == ({"reason_code"} if r["status"] == "INVALID" else set()), case
        assert set(exp["keys"]) <= set(r), case
        if r["status"] == "INVALID":
            assert r["reason_code"] in GROWTH_REASON_CODES, case
    assert substituted == K_NEGATIVE_PRIOR
    assert a2 == K_A2
    assert p1_recoded == K_NEGATIVE_PRIOR - K_P1_REMAINING == 17
    assert len(gold["cases"]) - a2 - (substituted - p1_recoded) == 768 - 171 - 16   # 581 cases exactly as golden


def test_finite_guard_not_enabled():
    """Explicitly revised for A-2 (name kept for traceability). Until A-2 this asserted that NaN / Infinity still took
    the OK path (NaN was then rated 5 stars). The approved policy: NON_FINITE_INPUT."""
    for v in (float("nan"), float("inf"), float("-inf")):
        r = calculate_positive_yoy("m", v, 1.0)
        assert (r["status"], r["reason_code"], r["value"]) == ("INVALID", "NON_FINITE_INPUT", None)


def test_negative_base_standard_yoy_unchanged():
    """Explicitly revised for STEP 24.4 P1 (name kept for traceability). Until P1 this asserted that a negative
    previous value was still computed (AMD-type -134 -> 1990 gave -15.85 = -1,585%). The approved policy makes
    it INVALID / NONPOSITIVE_PRIOR_BASE; a positive previous value keeps the old formula."""
    r = calculate_standard_yoy("m", 1990, -134)
    assert (r["status"], r["reason_code"], r["value"]) == ("INVALID", "NONPOSITIVE_PRIOR_BASE", None)
    r = calculate_standard_yoy("m", 1990, 134)
    assert r["status"] == "OK" and r["value"] == round(1990 / 134 - 1.0, 10)


def test_unregistered_code_is_rejected():
    with pytest.raises(ValueError):
        growth._build_invalid_metric("m", "x", "NOT_A_CODE")


def test_data_sufficiency_still_never_codes_invalid():
    comp = calculate_positive_yoy("fcf_yoy", 5, -1)
    assert comp["reason_code"] == "NONPOSITIVE_PRIOR_BASE"
    s = classify_component_sufficiency(comp)
    assert s["calculation_status"] == "INVALID" and s["reason_code"] is None


def test_calculate_growth_invalid_component_carries_code_and_score_unchanged():
    def q(v, end, fy, quarter="Q3"):
        return {"status": "OK", "period_end": end, "value": v, "fy": fy, "quarter": quarter, "period_type": "quarterly"}
    hist = {
        "revenue": {"quarterly": [q(100, "2024-09-30", 2024), q(120, "2025-09-30", 2025)]},
        "cfo": {"quarterly": [q(10, "2024-09-30", 2024), q(30, "2025-09-30", 2025)]},
        "capex": {"quarterly": [q(-15, "2024-09-30", 2024), q(-5, "2025-09-30", 2025)]},   # prior FCF -5 -> INVALID
        "operating_income": {"quarterly": [q(20, "2024-09-30", 2024), q(25, "2025-09-30", 2025)]},
        "diluted_eps": {"quarterly": [q(1.0, "2024-09-30", 2024), q(1.5, "2025-09-30", 2025)]},
    }
    g = growth.calculate_growth({"ticker": "T", "metrics": {}}, {}, historical=hist)
    fcf = g["components"]["fcf_yoy"]
    assert (fcf["status"], fcf["reason_code"]) == ("INVALID", "NONPOSITIVE_PRIOR_BASE")
    # score from the remaining four components only (unchanged rule: non-OK excluded and re-normalised)
    c = g["components"]
    exp = sum(c[k]["stars"] * w for k, w in growth.GROWTH_WEIGHTS.items() if c[k]["status"] == "OK") / \
        sum(w for k, w in growth.GROWTH_WEIGHTS.items() if c[k]["status"] == "OK")
    assert g["growth"]["score"] == round(exp, 4)


def test_checker_v24_mirror_matches_production():
    spec = importlib.util.spec_from_file_location("checker_mirror", REPO / "scripts" / "step23_checker_v2_3.py")
    ck = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ck)
    assert ck.GROWTH_REASON_CODES == GROWTH_REASON_CODES
