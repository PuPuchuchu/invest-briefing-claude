"""
STEP 23 (2026-11-03): governance-WITHHELD dependents are always listed.

When a metric is withheld (DEC-055), every entry of the gate's dependency
maps that depends on it is recorded as WITHHELD -- including outputs the
stage never produced because the input was masked upstream (no history is
extracted for a masked metric, so e.g. cfo_yoy is not computed). Such
entries carry materialized=False (a display field, not a status), value
None, reason_code GOVERNANCE_WITHHELD and withheld_inputs. Nothing outside
the dependency maps is created, nothing is created in a stage that did not
run, and R1/R2 (normalized metrics) are unaffected.
"""

from __future__ import annotations

import copy

from src.fundamentals import metric_eligibility_registry as reg
from src.fundamentals.batch_coverage import build_coverage

W = ["cfo", "capex", "operating_income"]


def _stages():
    derived = {"derived_metrics": {
        "fcf": {"status": "MISSING", "value": None},
        "fcf_margin": {"status": "MISSING", "value": None},
        "operating_margin": {"status": "MISSING", "value": None},
        "net_debt": {"status": "OK", "value": 5},
        "debt_to_cash": {"status": "OK", "value": 0.5},
        "revenue_yoy": {"status": "OK", "value": 0.1},
        # cfo_yoy / capex_yoy / operating_income_yoy NOT produced (no history)
    }}
    growth = {"growth": {"status": "OK"}, "components": {
        "revenue_yoy": {"status": "OK", "value": 0.1},
        "fcf_yoy": {"status": "MISSING", "value": None},
        # operating_income_yoy not produced
    }}
    suff = {"revenue_yoy": {"metric": "revenue_yoy", "calculation_status": "OK", "reason_code": None},
            "fcf_yoy": {"metric": "fcf_yoy", "calculation_status": "MISSING", "reason_code": "CONCEPT_NOT_FOUND"}}
    return derived, growth, suff


def test_unproduced_dependents_are_listed_as_withheld():
    derived, growth, suff = _stages()
    changed = reg.relabel_withheld_dependents(W, derived, growth, suff)
    dm = derived["derived_metrics"]
    for name, inputs in (("cfo_yoy", ["cfo"]), ("capex_yoy", ["capex"]), ("operating_income_yoy", ["operating_income"])):
        m = dm[name]
        assert m["status"] == "WITHHELD" and m["value"] is None, name
        assert m["materialized"] is False and m["original_status"] is None
        assert m["reason_code"] == reg.DEPENDENT_REASON_WITHHELD == "GOVERNANCE_WITHHELD"
        assert m["withheld_inputs"] == inputs
        assert "Not produced" in m["reason"]
    for name in ("fcf", "fcf_margin", "operating_margin"):
        assert dm[name]["status"] == "WITHHELD" and dm[name]["materialized"] is True
        assert dm[name]["original_status"] == "MISSING" and dm[name]["value"] is None
    assert sorted(changed["derived"]) == sorted(
        ["fcf", "fcf_margin", "cfo_yoy", "capex_yoy", "operating_margin", "operating_income_yoy"])


def test_only_dependency_map_entries_are_added_and_controls_untouched():
    derived, growth, suff = _stages()
    before_keys = set(derived["derived_metrics"])
    reg.relabel_withheld_dependents(W, derived, growth, suff)
    added = set(derived["derived_metrics"]) - before_keys
    assert added == {"cfo_yoy", "capex_yoy", "operating_income_yoy"}
    assert added <= set(reg.DERIVED_DEPENDENCIES)
    assert derived["derived_metrics"]["net_debt"] == {"status": "OK", "value": 5}
    assert derived["derived_metrics"]["debt_to_cash"] == {"status": "OK", "value": 0.5}
    assert derived["derived_metrics"]["revenue_yoy"] == {"status": "OK", "value": 0.1}


def test_withheld_total_debt_lists_its_dependents():
    derived = {"derived_metrics": {"net_debt": {"status": "OK", "value": 5}}}
    reg.relabel_withheld_dependents(["total_debt"], derived, None, None)
    dm = derived["derived_metrics"]
    assert dm["net_debt"]["status"] == "WITHHELD" and dm["net_debt"]["materialized"] is True and dm["net_debt"]["value"] is None
    assert dm["debt_to_cash"]["status"] == "WITHHELD" and dm["debt_to_cash"]["materialized"] is False
    assert set(dm) == {"net_debt", "debt_to_cash"}


def test_growth_and_sufficiency_unproduced_entries():
    derived, growth, suff = _stages()
    reg.relabel_withheld_dependents(W, derived, growth, suff)
    comp = growth["components"]
    assert comp["operating_income_yoy"]["status"] == "WITHHELD" and comp["operating_income_yoy"]["materialized"] is False
    assert comp["fcf_yoy"]["status"] == "WITHHELD" and comp["fcf_yoy"]["materialized"] is True
    assert comp["revenue_yoy"] == {"status": "OK", "value": 0.1}
    assert growth["growth"]["withheld_components"] == ["fcf_yoy", "operating_income_yoy"]
    assert suff["operating_income_yoy"]["calculation_status"] == "WITHHELD"
    assert suff["operating_income_yoy"]["materialized"] is False
    assert suff["fcf_yoy"]["materialized"] is True
    assert suff["revenue_yoy"]["calculation_status"] == "OK"


def test_nothing_created_in_a_stage_that_did_not_run():
    reg.relabel_withheld_dependents(W, None, None, None)  # no exception
    derived = {"status": "ERROR"}  # stage container without results
    growth = {"growth": {"status": "MISSING"}}  # no components dict
    changed = reg.relabel_withheld_dependents(W, derived, growth, None)
    assert derived == {"status": "ERROR"} and growth == {"growth": {"status": "MISSING"}}
    assert changed == {"derived": [], "growth": [], "data_sufficiency": []}


def test_no_withheld_changes_nothing():
    derived, growth, suff = _stages()
    snapshot = copy.deepcopy((derived, growth, suff))
    changed = reg.relabel_withheld_dependents([], derived, growth, suff)
    assert (derived, growth, suff) == snapshot
    assert changed == {"derived": [], "growth": [], "data_sufficiency": []}


def test_idempotent():
    derived, growth, suff = _stages()
    reg.relabel_withheld_dependents(W, derived, growth, suff)
    once = copy.deepcopy((derived, growth, suff))
    reg.relabel_withheld_dependents(W, derived, growth, suff)
    # re-marking keeps status/value/inputs; materialized flips to True only
    # because the entry now exists -- the masked content is unchanged
    for name in ("cfo_yoy", "capex_yoy", "operating_income_yoy"):
        a, b = once[0]["derived_metrics"][name], derived["derived_metrics"][name]
        assert (a["status"], a["value"], a["withheld_inputs"]) == (b["status"], b["value"], b["withheld_inputs"])


# ------------------------------------------------------------------ coverage effect

def _result(derived, growth, suff, normalized_metrics):
    return {"stages": {
        "normalization": {"normalized": {"metrics": normalized_metrics}},
        "eligibility_gate": {"status": "OK", "withheld": W, "scoped": [], "not_yet_available": [], "no_record": [],
                             "unmasked_originals": {m: {"status": "OK", "value": 1} for m in W}},
        "derived_metrics": {"derived": derived},
        "growth": {"growth": growth},
        "data_sufficiency": {"sufficiency": suff},
    }}


def _coverage(results):
    records = [{"ticker": t, "status": "OK", "company_type": "NON_FINANCIAL", "raw_source": "CACHE"} for t in results]
    return build_coverage(records=records, results=results, evaluation_date="2026-11-03")


def test_coverage_counts_withheld_dependents_and_r1_r2_unchanged():
    norm = {m: {"status": "WITHHELD", "value": None} for m in W}
    plain = _stages()
    gated = _stages()
    reg.relabel_withheld_dependents(W, *gated)
    other = {"derived_metrics": {k: {"status": "OK", "value": 1} for k in
                                 ("fcf", "fcf_margin", "cfo_yoy", "capex_yoy", "operating_margin", "revenue_yoy")}}
    ok_norm = {m: {"status": "OK", "value": 1} for m in W}
    c_before = _coverage({"GE": _result(*plain, norm), "AAA": _result(copy.deepcopy(other), *_stages()[1:], ok_norm)})
    c_after = _coverage({"GE": _result(*gated, norm), "AAA": _result(copy.deepcopy(other), *_stages()[1:], ok_norm)})
    # Previously GE's unproduced cfo_yoy simply vanished from the count (1 of 2 tickers).
    assert sum(c_before["derived_metrics"]["cfo_yoy"]["by_status"].values()) == 1
    assert c_after["derived_metrics"]["cfo_yoy"]["by_status"] == {"OK": 1, "WITHHELD": 1}
    assert c_after["derived_metrics"]["cfo_yoy"]["by_reason_code"] == {"GOVERNANCE_WITHHELD": 1}
    assert c_after["growth"]["components"]["operating_income_yoy"]["by_status"].get("WITHHELD") == 1
    # R1/R2 are computed from normalized metrics only: identical.
    assert c_before["normalized_metrics"] == c_after["normalized_metrics"]


# ------------------------------------------------------------------ orchestrator integration

def _row(metric, outcome, status, **kw):
    row = {"ticker": "TST", "cik": "0000999001", "metric": metric, "outcome": outcome, "governance_status": status,
           "use_scope": None, "effective_from": "2026-10-29", "effective_to": None,
           "eligibility_available_date": "2026-11-02", "reviewed_at_utc": "2026-11-01T00:45:35Z",
           "policy_version": "METRIC_ELIGIBILITY_V1", "decision_ref": "TEST", "rationale": "test"}
    row.update(kw)
    return row


def _fact(val, start, end, accn, filed, fy):
    out = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": "FY", "form": "10-K", "filed": filed}
    if start:
        out["start"] = start
    return out


def _facts(pairs, instant=False):
    a = ("0000999001-25-000001", "2025-02-01", 2024)
    b = ("0000999001-26-000001", "2026-02-01", 2025)
    return {"units": {"USD": [
        _fact(v, None if instant else s, e, acc, filed, fy)
        for (acc, filed, fy), (s, e), v in zip((a, b), (("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31")), pairs)
    ]}}


def test_orchestrator_lists_unproduced_dependents_as_withheld():
    from src.fundamentals.orchestrator import run_fundamentals_pipeline
    fixture = {"cik": 999001, "entityName": "Test Co", "facts": {"us-gaap": {
        "Revenues": _facts((1000, 1200)),
        "OperatingIncomeLoss": _facts((100, 130)),
        "NetCashProvidedByUsedInOperatingActivities": _facts((200, 240)),
        "PaymentsToAcquirePropertyPlantAndEquipment": _facts((50, 60)),
    }}}
    ct = [{"ticker": "TST", "cik": "0000999001", "company_type": "NON_FINANCIAL", "effective_from": "2026-09-20",
           "effective_to": None, "classification_available_date": "2026-09-20",
           "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE", "mapping_rationale": "test fixture",
           "reviewed_at_utc": "2026-10-26T00:00:00Z", "decision_ref": "TEST"}]
    rows = [_row("cfo", "2", "WITHHELD"), _row("capex", "3", "WITHHELD"), _row("operating_income", "3", "WITHHELD")]
    res = run_fundamentals_pipeline(ticker="TST", cik="0000999001", evaluation_date="2026-11-05",
                                    company_type="NON_FINANCIAL", company_type_rows=ct,
                                    raw_companyfacts=copy.deepcopy(fixture), metric_eligibility_rows=rows)
    stages = res["stages"]
    # masked inputs: no history is extracted for them ...
    assert not {"cfo", "capex", "operating_income"} & set(stages["annual_history"]["history"])
    dm = stages["derived_metrics"]["derived"]["derived_metrics"]
    # ... and their history-based dependents are still listed, as not produced.
    for name in ("cfo_yoy", "capex_yoy", "operating_income_yoy"):
        assert dm[name]["status"] == "WITHHELD" and dm[name]["value"] is None, name
        assert dm[name]["materialized"] is False, name
    assert dm["fcf"]["status"] == "WITHHELD" and dm["fcf"]["materialized"] is True
    relabelled = stages["eligibility_gate"]["relabelled"]
    assert set(relabelled["derived"]) == {"fcf", "fcf_margin", "cfo_yoy", "capex_yoy", "operating_margin",
                                          "operating_income_yoy"}
    assert set(relabelled["growth"]) == {"fcf_yoy", "operating_income_yoy"}
    for name in ("fcf_yoy", "operating_income_yoy"):
        assert stages["growth"]["growth"]["components"][name]["status"] == "WITHHELD"
        assert stages["data_sufficiency"]["sufficiency"][name]["calculation_status"] == "WITHHELD"
    # an unrelated output is untouched
    assert dm["net_margin"]["status"] != "WITHHELD"


def test_batch_reports_governance_policy_v2():
    from src.fundamentals import batch_runner
    assert batch_runner.GOVERNANCE_POLICY_VERSION_GATED == "DEC055_GATE_V2_MATERIALIZED_WITHHELD"
