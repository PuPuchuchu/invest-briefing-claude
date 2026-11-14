"""STEP 24.6-N: growth.calculate_growth with the same-filing comparative policy (opt-in, fail-closed).

Records are annotated with per_share_comparatives.annotate_records() exactly as the orchestrator does; this file
imports only growth, per_share_comparatives, data_sufficiency and batch_coverage (no orchestrator / sec_history).
"""
from __future__ import annotations

import copy
import math

import pytest

from src.fundamentals import batch_coverage, growth
from src.fundamentals import per_share_comparatives as psc
from src.fundamentals.data_sufficiency import classify_component_sufficiency

A26, A25 = "A-2026-Q3", "A-2025-Q3"


def o(start, end, val, accn, filed, fy, fp="Q3"):
    return {"start": start, "end": end, "val": val, "accn": accn, "filed": filed, "fy": fy, "fp": fp, "form": "10-Q"}


CUR = o("2026-07-01", "2026-09-30", 0.87, A26, "2026-10-21", 2026)
COMP = o("2025-07-01", "2025-09-30", 0.59, A26, "2026-10-21", 2026)
OWN = o("2025-07-01", "2025-09-30", 5.87, A25, "2025-10-22", 2025)


def rec(ob, q="Q3"):
    return {"quarter": q, "fy": ob["fy"], "value": ob["val"], "period_start": ob["start"], "period_end": ob["end"],
            "filed": ob["filed"], "form": "10-Q", "accession": None, "reconstructed": False,
            "source": {"current": dict(ob), "previous": None}}          # no "_unit" tag needed (explicit unit)


def q(v, end, fy):
    return {"status": "OK", "period_end": end, "value": v, "fy": fy, "quarter": "Q3", "period_type": "quarterly"}


def base_history():
    h = {"revenue": [100, 123], "operating_income": [20, 25], "cfo": [10, 30], "capex": [-4, -5]}
    return {k: {"quarterly": [q(v[0], "2025-09-30", 2025), q(v[1], "2026-09-30", 2026)]} for k, v in h.items()}


def eps_history(observations, records=None, policy=psc.POLICY_SAME_FILING_COMPARATIVE, cutoff="2026-11-12"):
    records = records if records is not None else [rec(OWN), rec(CUR)]
    data = {"units": {"USD/shares": copy.deepcopy(list(observations))}}
    h = {"annual": [], "quarterly": psc.annotate_records(records, data, cutoff, "USD/shares")}
    if policy is not None:
        h[psc.POLICY_KEY] = policy
    return h


def run(eps):
    hist = base_history(); hist["diluted_eps"] = eps
    return growth.calculate_growth({"ticker": "T", "metrics": {}}, {}, historical=hist)


# ---------------------------------------------------------------- FOUND
def test_split_uses_same_filing_comparative():
    g = run(eps_history([CUR, COMP, OWN]))
    c = g["components"]["eps_yoy"]
    assert (c["status"], c["value"], c["stars"], c["previous_value_basis"]) == ("OK", round(0.87 / 0.59 - 1, 10), 5,
                                                                                 psc.BASIS_FOUND)
    assert (c["source"], c["current_period_end"], c["previous_period_end"]) == (["historical", "quarterly"],
                                                                                 "2026-09-30", "2025-09-30")
    assert "eps_yoy" not in g["limitations"]


def test_legacy_without_policy_is_byte_identical_to_main_behaviour():
    legacy = run({"annual": [], "quarterly": [rec(OWN), rec(CUR)]})          # no annotation, no policy key
    c = legacy["components"]["eps_yoy"]
    assert (c["status"], c["value"], c["stars"]) == ("OK", round(0.87 / 5.87 - 1, 10), 1)
    assert "previous_value_basis" not in c
    annotated_but_off = run(eps_history([CUR, COMP, OWN], policy=None))       # annotations alone change nothing
    assert annotated_but_off == legacy


def test_equal_comparative_changes_only_provenance():
    comp = dict(COMP, val=5.87)
    on, off = run(eps_history([CUR, comp, OWN])), run({"annual": [], "quarterly": [rec(OWN), rec(CUR)]})
    on_c = dict(on["components"]["eps_yoy"]); assert on_c.pop("previous_value_basis") == psc.BASIS_FOUND
    assert on_c == off["components"]["eps_yoy"]
    assert {k: v for k, v in on.items() if k != "components"} == {k: v for k, v in off.items() if k != "components"}


def test_nonpositive_comparative_keeps_existing_code_with_found_basis():
    for v in (-0.10, 0, 0.0, -0.0):
        g = run(eps_history([CUR, dict(COMP, val=v), OWN]))
        c = g["components"]["eps_yoy"]
        assert (c["status"], c["reason_code"], c["previous_value_basis"]) == ("INVALID", "NONPOSITIVE_PRIOR_BASE",
                                                                               psc.BASIS_FOUND), v
        assert "current_period_end" not in c and "source" not in c
        assert g["limitations"]["eps_yoy"] == growth.EPS_LIMITATION_NONPOSITIVE


def test_nonpositive_current_keeps_existing_code():
    cur = dict(CUR, val=-0.2)
    g = run(eps_history([cur, COMP, OWN], records=[rec(OWN), rec(cur)]))
    c = g["components"]["eps_yoy"]
    assert (c["reason_code"], c["previous_value_basis"]) == ("NONPOSITIVE_CURRENT", psc.BASIS_FOUND)
    assert g["limitations"]["eps_yoy"] == growth.EPS_LIMITATION_NONPOSITIVE


# ---------------------------------------------------------------- fail-closed
@pytest.mark.parametrize("observations,reason,limitation", [
    ([CUR, OWN], psc.REASONS[psc.MISSING], "EPS_LIMITATION_COMPARATIVE_MISSING"),
    ([CUR, COMP, dict(COMP), OWN], psc.REASONS[psc.AMBIGUOUS], "EPS_LIMITATION_COMPARATIVE_AMBIGUOUS"),
    ([CUR, dict(COMP, start="2025-01-01"), OWN], psc.MISMATCH_REASON, "EPS_LIMITATION_COMPARATIVE_MISSING"),
])
def test_fail_closed_never_falls_back(observations, reason, limitation):
    g = run(eps_history(observations))
    c = g["components"]["eps_yoy"]
    assert (c["status"], c["value"], c["stars"], c["reason_code"], c["reason"], c["previous_value_basis"]) == (
        "INVALID", None, None, "SAME_FILING_COMPARATIVE_UNAVAILABLE", reason, psc.BASIS_MISSING)
    assert g["limitations"]["eps_yoy"] == getattr(growth, limitation)
    assert "SAME_FILING_COMPARATIVE_UNAVAILABLE" in growth.GROWTH_REASON_CODES


def test_annotation_absent_under_policy_is_fail_closed():
    h = {"annual": [], "quarterly": [rec(OWN), rec(CUR)], psc.POLICY_KEY: psc.POLICY_SAME_FILING_COMPARATIVE}
    c = run(h)["components"]["eps_yoy"]
    assert (c["status"], c["reason"], c["previous_value_basis"]) == ("INVALID", psc.REASONS[psc.ANNOTATION_ABSENT],
                                                                     psc.BASIS_MISSING)


def test_current_source_unavailable_is_fail_closed():
    r = rec(CUR); r["source"] = {}
    c = run(eps_history([CUR, COMP, OWN], records=[rec(OWN), r]))["components"]["eps_yoy"]
    assert (c["reason_code"], c["reason"]) == ("SAME_FILING_COMPARATIVE_UNAVAILABLE",
                                               psc.REASONS[psc.CURRENT_SOURCE_UNAVAILABLE])


def test_unknown_policy_raises():
    with pytest.raises(ValueError, match="Unknown per-share comparative policy"):
        run(eps_history([CUR, COMP, OWN], policy="latest_vintage"))


# ---------------------------------------------------------------- no pair -> no provenance (NOT_APPLICABLE)
def test_no_pair_is_missing_without_provenance():
    for records in ([rec(CUR)], []):
        g = run(eps_history([CUR, COMP], records=records))
        c = g["components"]["eps_yoy"]
        assert c["status"] == "MISSING" and "previous_value_basis" not in c
        assert g["limitations"]["eps_yoy"] == growth.EPS_LIMITATION_MISSING


# ---------------------------------------------------------------- scope
def test_other_components_and_weights_untouched():
    on, off = run(eps_history([CUR, COMP, OWN])), run({"annual": [], "quarterly": [rec(OWN), rec(CUR)]})
    for m in ("revenue_yoy", "operating_income_yoy", "fcf_yoy", "revenue_cagr_3y"):
        assert on["components"][m] == off["components"][m], m
    for m in ("revenue_yoy", "operating_income_yoy", "fcf_yoy", "revenue_cagr_3y"):
        assert "previous_value_basis" not in on["components"][m]
    comps = on["components"]; ok = {k: w for k, w in growth.GROWTH_WEIGHTS.items() if comps[k]["status"] == "OK"}
    assert on["growth"]["score"] == round(sum(comps[k]["stars"] * w for k, w in ok.items()) / sum(ok.values()), 4)


def test_a2_rating_guard_keeps_provenance(monkeypatch):
    real = growth.calculate_positive_yoy
    monkeypatch.setattr(growth, "calculate_positive_yoy",
                        lambda m, c, p: {"status": "OK", "metric": m, "value": math.nan} if m == "eps_yoy" else real(m, c, p))
    c = run(eps_history([CUR, COMP, OWN]))["components"]["eps_yoy"]
    assert (c["status"], c["reason_code"], c["previous_value_basis"]) == ("INVALID", "NON_FINITE_RESULT", psc.BASIS_FOUND)


def test_inputs_not_mutated():
    h = eps_history([CUR, COMP, OWN]); before = copy.deepcopy(h)
    run(h)
    assert h == before


# ---------------------------------------------------------------- downstream
def test_data_sufficiency_and_batch_coverage_count_the_new_code():
    g = run(eps_history([CUR, OWN]))
    s = classify_component_sufficiency(g["components"]["eps_yoy"])
    assert (s["calculation_status"], s["reason_code"], s["detail"]) == ("INVALID", None, psc.REASONS[psc.MISSING])
    block = batch_coverage._status_block(g["components"])
    assert block["eps_yoy"] == {"by_status": {"INVALID": 1}, "by_reason_code": {"SAME_FILING_COMPARATIVE_UNAVAILABLE": 1}}


def test_unidentified_filing_uses_the_neutral_limitation():
    r = rec(CUR); r["source"] = {}
    g = run(eps_history([CUR, COMP, OWN], records=[rec(OWN), r]))
    assert g["limitations"]["eps_yoy"] == growth.EPS_LIMITATION_COMPARATIVE_MISSING
    assert "verifiable" in growth.EPS_LIMITATION_COMPARATIVE_MISSING and "it does not" not in growth.EPS_LIMITATION_COMPARATIVE_MISSING


def test_every_policy_result_satisfies_the_guard_rule():
    cases = [[CUR, COMP, OWN], [CUR, OWN], [CUR, COMP, dict(COMP), OWN], [CUR, dict(COMP, val=-1), OWN]]
    for observations in cases:
        c = run(eps_history(observations))["components"]["eps_yoy"]
        assert psc.basis_consistency_problem(c) is None, (observations, c)
    for records in ([rec(CUR)], []):
        assert psc.basis_consistency_problem(run(eps_history([CUR], records=records))["components"]["eps_yoy"]) is None
