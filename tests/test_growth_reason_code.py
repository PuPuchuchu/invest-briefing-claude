"""STEP 24.3 A-1: structured reason_code on every Growth INVALID result.

Scope guarantees tested here:
  * each of the 9 INVALID branches returns its fixed, registered code;
  * the code is a literal at the branch (never derived from the reason text), so rewording a message
    cannot change it;
  * status / value / reason (and the exception behaviour) are identical to growth.py at main 64e0f1d
    (golden file generated from that commit);
  * data_sufficiency still never puts a reason_code on INVALID;
  * the Checker v2.4 mirror of the code set equals production.
No finite-input guard, no negative-base change: those are separate decisions.
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
    (calculate_cagr_3y, ("m", True, 100), "NON_NUMERIC_INPUT"),
    (calculate_cagr_3y, ("m", 0, 100), "NONPOSITIVE_PRIOR_BASE"),
    (calculate_cagr_3y, ("m", 100, -1), "NEGATIVE_END_VALUE"),
]


def test_registered_code_set_is_closed_and_disjoint_from_sufficiency_codes():
    assert GROWTH_REASON_CODES == frozenset({
        "NON_NUMERIC_INPUT", "NON_FINITE_INPUT", "NONPOSITIVE_PRIOR_BASE", "NONPOSITIVE_CURRENT",
        "ZERO_PRIOR_BASE", "NEGATIVE_END_VALUE", "INVALID_PERIOD_PARAMETER"})
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
    """9 call sites; the third argument is a string literal in the registered set (never computed)."""
    tree = ast.parse(GROWTH_PY.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_build_invalid_metric"]
    assert len(calls) == 9
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


def test_production_parity_with_main_64e0f1d():
    """status / value / reason / exception identical to the pre-A-1 production (golden); only reason_code is added."""
    gold = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert gold["growth_py_sha256"] == "01e05f6268c28966361eecf37334b6b7f82ac15b1403a456099fd047b8d18b15"
    inputs = {"0": 0, "0.0": 0.0, "-0.0": -0.0, "1": 1, "-1": -1, "2.5": 2.5, "-2.5": -2.5, "1e12": 10**12,
              "nan": float("nan"), "inf": float("inf"), "-inf": float("-inf"), "None": None, "str3": "3",
              "True": True, "False": False, "list": []}
    assert sorted(inputs) == sorted(gold["inputs"])
    assert len(gold["cases"]) == 3 * 16 * 16
    for case in gold["cases"]:
        fn = getattr(growth, case["function"])
        exp = case["result"]
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


def test_finite_guard_not_enabled():
    """A-2 is not approved: NaN / Infinity still behave exactly as before (OK path)."""
    assert calculate_positive_yoy("m", float("nan"), 1.0)["status"] == "OK"
    assert calculate_positive_yoy("m", float("inf"), 1.0)["status"] == "OK"


def test_negative_base_standard_yoy_unchanged():
    """STEP 24.4 is a separate policy step: negative previous values are still computed as before."""
    r = calculate_standard_yoy("m", 1990, -134)
    assert r["status"] == "OK" and r["value"] == round(1990 / -134 - 1.0, 10)


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
