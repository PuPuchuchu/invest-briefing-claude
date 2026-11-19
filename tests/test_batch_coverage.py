"""
Tests for STEP 23-B1 batch coverage (src/fundamentals/batch_coverage.py)
and its integration into batch_runner.run_batch() (PIT WATCHLIST selection,
coverage.json / coverage.md, additive summary fields).
"""

from __future__ import annotations

import copy
import json

import pytest

from src.fundamentals import batch_coverage as cov
from src.fundamentals import metric_eligibility_registry as reg
from src.fundamentals.batch_runner import (
    BATCH_SCHEMA_VERSION,
    STATUS_ERROR,
    STATUS_EXCLUDED_COMPANY_TYPE,
    STATUS_EXCLUDED_NOT_IN_WATCHLIST,
    STATUS_OK,
    run_batch,
)
from src.fundamentals.metric_eligibility_registry_io import read_metric_eligibility_csv

EVAL = "2026-11-05"
SECRET_VALUE = 987654321  # must never appear in coverage output


def _ok(value, **kw):
    return {"status": "OK", "value": value, "unit": "USD", **kw}


def _normalized(**overrides):
    metrics = {m: _ok(100 + i) for i, m in enumerate(cov.NORMALIZED_METRICS)}
    metrics.update(overrides)
    return metrics


def _result(metrics, gate=None, derived=None, growth=None, sufficiency=None, tier2=None):
    stages = {
        "normalization": {"status": "OK", "normalized": {"metrics": metrics}},
        "eligibility_gate": gate or {"status": "SKIPPED"},
    }
    if derived is not None:
        stages["derived_metrics"] = {"status": "OK", "derived": {"derived_metrics": derived}}
    if growth is not None:
        stages["growth"] = {"status": "OK", "growth": growth}
    if sufficiency is not None:
        stages["data_sufficiency"] = {"status": "OK", "sufficiency": sufficiency}
    if tier2 is not None:
        stages["debt_tiers"] = {"status": "OK", "total_debt_including_finance_leases": tier2}
    return {"stages": stages}


def _rec(ticker, status=STATUS_OK, company_type="NON_FINANCIAL", **kw):
    rec = {"ticker": ticker, "cik": "1", "status": status}
    if status != STATUS_EXCLUDED_NOT_IN_WATCHLIST:
        rec["company_type_lookup_status"] = kw.pop("ct_lookup", "OK")
        if status != STATUS_EXCLUDED_COMPANY_TYPE:
            rec["company_type"] = company_type
    if status == STATUS_OK:
        rec["raw_source"] = "CACHE"
    rec.update(kw)
    return rec


def _cov(records, results, **kw):
    return cov.build_coverage(records=records, results=results, evaluation_date=EVAL, **kw)


def _m(c, metric):
    return c["normalized_metrics"][metric]


# ---------------------------------------------------------------- 1, 3
def test_all_ok_and_zero_is_ok():
    metrics = _normalized(cash=_ok(0))
    c = _cov([_rec("A")], {"A": _result(metrics)})
    for metric in cov.NORMALIZED_METRICS:
        assert _m(c, metric)["data_status_counts"] == {"OK": 1}, metric
        assert _m(c, metric)["r1_data_acquisition_rate"] == {
            "numerator": 1, "denominator": 1, "percentage": 100.0, "calculation_status": "COMPUTED"}
    assert _m(c, "cash")["data_status_counts"] == {"OK": 1}  # zero value is data, not missing


# ---------------------------------------------------------------- 2, 11
def test_missing_incomplete_invalid_and_reason_codes():
    metrics = _normalized(
        cash={"status": "MISSING", "value": None, "reason": "free text that must not be copied"},
        total_debt={"status": "INCOMPLETE", "value": None, "reason_code": "LEASE_SCOPE_MISMATCH"},
        noncurrent_debt={"status": "INCOMPLETE", "value": None, "reason": "unit ambiguity"},
        revenue={"status": "INVALID", "value": None},
    )
    c = _cov([_rec("A")], {"A": _result(metrics)})
    assert _m(c, "cash")["data_status_counts"] == {"MISSING": 1}
    assert _m(c, "total_debt")["incomplete_by_reason_code"] == {"LEASE_SCOPE_MISMATCH": 1}
    assert _m(c, "noncurrent_debt")["incomplete_by_reason_code"] == {cov.NO_REASON_CODE: 1}
    assert _m(c, "revenue")["data_status_counts"] == {"INVALID": 1}
    assert _m(c, "cash")["r1_data_acquisition_rate"]["numerator"] == 0
    assert "free text" not in json.dumps(c)


# ---------------------------------------------------------------- 4, 5, 20
def test_withheld_uses_original_status_and_hides_values():
    metrics = _normalized(cfo=_ok(SECRET_VALUE), capex={"status": "INCOMPLETE", "value": None, "reason_code": "STALE_PERIOD"})
    rows = [
        {**_gov_row("cfo", "2", "WITHHELD")},
        {**_gov_row("capex", "3", "WITHHELD")},
    ]
    gate = reg.apply_metric_eligibility_gate({"metrics": metrics}, "1", EVAL, rows)
    c = _cov([_rec("A")], {"A": _result(metrics, gate=gate)})
    cfo, capex = _m(c, "cfo"), _m(c, "capex")
    assert cfo["data_status_counts"] == {"OK": 1}
    assert cfo["governance_status_counts"] == {"WITHHELD": 1}
    assert cfo["r1_data_acquisition_rate"]["numerator"] == 1
    assert cfo["r2_runtime_non_withheld_ok_rate"]["numerator"] == 0
    assert cfo["r2_runtime_non_withheld_ok_rate"]["denominator"] == 1
    assert capex["data_status_counts"] == {"INCOMPLETE": 1}
    assert capex["incomplete_by_reason_code"] == {"STALE_PERIOD": 1}
    text = json.dumps(c) + cov.render_coverage_markdown(c)
    assert str(SECRET_VALUE) not in text


def _gov_row(metric, outcome, status, **kw):
    row = {
        "ticker": "A", "cik": "1", "metric": metric, "outcome": outcome, "governance_status": status,
        "use_scope": None, "effective_from": "2026-10-29", "effective_to": None,
        "eligibility_available_date": "2026-11-02", "reviewed_at_utc": "2026-11-01T00:45:35Z",
        "policy_version": "T", "decision_ref": "T", "rationale": "T",
    }
    row.update(kw)
    return row


# ---------------------------------------------------------------- 6, 7, 8, 9
def test_governance_axes_scope_no_record_not_yet_available_and_non_governed():
    metrics = _normalized()
    rows = [_gov_row("total_debt", "1", "ELIGIBLE", use_scope="CONSOLIDATED_REPORTED"),
            _gov_row("cfo", "2", "WITHHELD", eligibility_available_date="2026-12-01")]
    gate = reg.apply_metric_eligibility_gate({"metrics": metrics}, "1", EVAL, rows)
    c = _cov([_rec("A")], {"A": _result(metrics, gate=gate)})
    td = _m(c, "total_debt")
    assert td["governance_status_counts"] == {"ELIGIBLE": 1}
    assert td["use_scope_counts"] == {"CONSOLIDATED_REPORTED": 1}
    assert _m(c, "cfo")["governance_status_counts"] == {"NOT_YET_AVAILABLE": 1}
    assert _m(c, "capex")["governance_status_counts"] == {"NO_RECORD": 1}
    assert _m(c, "revenue")["governance_status_counts"] == {cov.NOT_GOVERNED: 1}
    r2 = _m(c, "cfo")["r2_runtime_non_withheld_ok_rate"]
    assert r2["numerator"] == 1 and r2["numerator_breakdown"]["not_yet_available"] == 1
    assert _m(c, "capex")["r2_runtime_non_withheld_ok_rate"]["numerator_breakdown"]["no_record"] == 1
    assert _m(c, "revenue")["r2_runtime_non_withheld_ok_rate"]["numerator_breakdown"] == {cov.NOT_GOVERNED: 1}


def test_gate_not_applied_is_reported_as_such():
    c = _cov([_rec("A")], {"A": _result(_normalized())})
    assert _m(c, "cfo")["governance_status_counts"] == {cov.GATE_NOT_APPLIED: 1}
    assert _m(c, "cfo")["r2_runtime_non_withheld_ok_rate"]["numerator_breakdown"]["gate_not_applied"] == 1


# ---------------------------------------------------------------- 10
def test_not_applicable_excluded_from_denominators():
    na = {"status": "NOT_APPLICABLE", "value": None, "reason": "x"}
    metrics = _normalized(cfo=na)
    c = _cov([_rec("A", company_type="FINANCIAL"), _rec("B")],
             {"A": _result(metrics), "B": _result(_normalized())})
    cfo = _m(c, "cfo")
    assert cfo["data_status_counts"] == {"NOT_APPLICABLE": 1, "OK": 1}
    assert cfo["r1_data_acquisition_rate"]["denominator"] == 1
    assert cfo["r2_runtime_non_withheld_ok_rate"]["denominator"] == 1


# ---------------------------------------------------------------- 12, 13
def test_pipeline_error_and_company_type_lookup_counts():
    recs = [
        _rec("A"),
        _rec("E", status=STATUS_ERROR, error_type="ValueError", error="secret path /x/y"),
        _rec("F", status=STATUS_ERROR, company_type="FINANCIAL", error_type="KeyError", error="z"),
        _rec("X", status=STATUS_EXCLUDED_COMPANY_TYPE, ct_lookup="NO_MAPPING_FOR_DATE"),
        _rec("Y", status=STATUS_EXCLUDED_COMPANY_TYPE, ct_lookup="NOT_YET_AVAILABLE"),
    ]
    c = _cov(recs, {"A": _result(_normalized())})
    u = c["universe"]
    assert u["errors_by_type"] == {"ValueError": 1, "KeyError": 1}
    assert u["company_type_lookup_status_counts"] == {"OK": 3, "NO_MAPPING_FOR_DATE": 1, "NOT_YET_AVAILABLE": 1}
    assert u["evaluated_tickers"] == 3
    rev = _m(c, "revenue")
    assert rev["r1_data_acquisition_rate"]["denominator"] == 1          # ERROR excluded from R1
    assert rev["r2_runtime_non_withheld_ok_rate"]["denominator"] == 3   # ERROR included in R2
    assert rev["data_status_counts"]["NOT_EVALUATED_ERROR"] == 2
    cfo = _m(c, "cfo")  # FINANCIAL ERROR ticker: cfo not applicable by company type
    assert cfo["r2_runtime_non_withheld_ok_rate"]["denominator"] == 2
    assert "secret path" not in json.dumps(c)


def test_error_with_unknown_company_type_makes_r2_not_computable():
    recs = [_rec("A"), _rec("E", status=STATUS_ERROR, company_type=None, error_type="RuntimeError")]
    c = _cov(recs, {"A": _result(_normalized())})
    r2 = _m(c, "revenue")["r2_runtime_non_withheld_ok_rate"]
    assert r2["calculation_status"] == cov.NOT_COMPUTABLE and r2["percentage"] is None
    assert "APPLICABILITY_UNKNOWN" in r2["not_computable_reason"]


# ---------------------------------------------------------------- key absent
def test_metric_key_absent_counted_separately():
    metrics = _normalized()
    del metrics["shares_outstanding"]
    c = _cov([_rec("A")], {"A": _result(metrics)})
    so = _m(c, "shares_outstanding")
    assert so["data_status_counts"] == {cov.KEY_ABSENT: 1}
    assert so["r1_data_acquisition_rate"]["denominator"] == 1 and so["r1_data_acquisition_rate"]["numerator"] == 0


# ---------------------------------------------------------------- 17, 18
def test_all_excluded_gives_not_computable_ratios():
    recs = [_rec("X", status=STATUS_EXCLUDED_COMPANY_TYPE, ct_lookup="NO_MAPPING_FOR_DATE"),
            _rec("W", status=STATUS_EXCLUDED_NOT_IN_WATCHLIST, watchlist_lookup_status="NOT_YET_AVAILABLE")]
    c = _cov(recs, {})
    for metric in cov.NORMALIZED_METRICS:
        for key in ("r1_data_acquisition_rate", "r2_runtime_non_withheld_ok_rate"):
            r = _m(c, metric)[key]
            assert r["denominator"] == 0 and r["percentage"] is None and r["calculation_status"] == cov.NOT_COMPUTABLE
    assert c["universe"]["pit_watchlist_members"] == 1
    assert c["universe"]["watchlist_lookup_status_counts"] == {"NOT_YET_AVAILABLE": 1}
    assert "not computable" in cov.render_coverage_markdown(c)


# ---------------------------------------------------------------- 19
def test_r1_r2_numerators_and_denominators():
    rows = [_gov_row("cfo", "2", "WITHHELD")]
    m_a = _normalized()
    gate_a = reg.apply_metric_eligibility_gate({"metrics": m_a}, "1", EVAL, rows)
    m_b = _normalized(cfo={"status": "MISSING", "value": None})
    m_c = _normalized()
    recs = [_rec("A"), _rec("B"), _rec("C", cik="2"),
            _rec("E", status=STATUS_ERROR, error_type="ValueError")]
    c = _cov(recs, {"A": _result(m_a, gate=gate_a), "B": _result(m_b), "C": _result(m_c)})
    cfo = _m(c, "cfo")
    # Data axis: A OK (original), B MISSING, C OK -> R1 2/3.
    assert (cfo["r1_data_acquisition_rate"]["numerator"], cfo["r1_data_acquisition_rate"]["denominator"]) == (2, 3)
    # R2: A withheld (excluded from numerator), C OK; denominator includes ERROR -> 1/4.
    r2 = cfo["r2_runtime_non_withheld_ok_rate"]
    assert (r2["numerator"], r2["denominator"], r2["percentage"]) == (1, 4, 25.0)


# ---------------------------------------------------------------- derived / growth / sufficiency / tier 2
def test_derived_growth_sufficiency_and_tier2_sections():
    derived = {"fcf": {"status": "WITHHELD", "withheld_inputs": ["cfo"]}, "net_debt": {"status": "MISSING"},
               "net_margin": _ok(0.1)}
    growth = {"growth": {"status": "OK", "withheld_components": ["fcf_yoy"]},
              "components": {"fcf_yoy": {"status": "WITHHELD"}, "revenue_yoy": {"status": "OK"}}}
    suff = {"fcf_yoy": {"calculation_status": "WITHHELD", "reason_code": "GOVERNANCE_WITHHELD"},
            "revenue_yoy": {"calculation_status": "OK", "reason_code": None}}
    tier2 = {"status": "INCOMPLETE", "reason_code": "NO_FINANCE_LEASE_EVIDENCE"}
    c = _cov([_rec("A")], {"A": _result(_normalized(), derived=derived, growth=growth, sufficiency=suff, tier2=tier2)})
    assert c["derived_metrics"]["fcf"]["by_status"] == {"WITHHELD": 1}
    assert c["derived_metrics"]["net_debt"]["by_reason_code"] == {cov.NO_REASON_CODE: 1}
    assert c["growth"]["components"]["fcf_yoy"]["by_status"] == {"WITHHELD": 1}
    assert c["growth"]["aggregate_status_counts"] == {"OK": 1}
    assert c["growth"]["withheld_component_counts"] == {"fcf_yoy": 1}
    assert c["data_sufficiency"]["fcf_yoy"]["by_reason_code"] == {"GOVERNANCE_WITHHELD": 1}
    assert c["debt_tiers"]["total_debt_including_finance_leases"]["by_reason_code"] == {"NO_FINANCE_LEASE_EVIDENCE": 1}
    assert "r1_data_acquisition_rate" not in json.dumps(c["derived_metrics"])


# ================================================================ run_batch integration
def _member(ticker, cik, effective_from="2026-09-21", available="2026-09-21"):
    return {"cik": cik, "ticker": ticker, "universe_type": "WATCHLIST", "membership_status": "ACTIVE",
            "membership_source": "LEGACY_SEED", "membership_version": "1", "effective_from": effective_from,
            "effective_to": None, "membership_available_date": available}


def _ct(ticker, cik, company_type="NON_FINANCIAL"):
    return {"ticker": ticker, "cik": cik, "company_type": company_type, "effective_from": "2026-09-20",
            "effective_to": None, "classification_available_date": "2026-09-20",
            "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE", "mapping_rationale": "t",
            "reviewed_at_utc": "2026-10-26T00:00:00Z", "decision_ref": "T"}


def _pipeline(metrics_by_ticker, calls=None, fail_for=()):
    def pipeline(**kwargs):
        if calls is not None:
            calls.append(kwargs["ticker"])
        if kwargs["ticker"] in fail_for:
            raise ValueError("boom /secret/path")
        metrics = copy.deepcopy(metrics_by_ticker[kwargs["ticker"]])
        rows = kwargs.get("metric_eligibility_rows")
        gate = (reg.apply_metric_eligibility_gate({"metrics": metrics}, kwargs["cik"], kwargs["evaluation_date"], rows)
                if rows is not None else {"status": "SKIPPED"})
        return _result(metrics, gate=gate)
    return pipeline



# STEP 23 SEC PIT: run_batch refuses evaluation dates after the run's KST date;
# a fixed run start keeps these fixed-date tests independent of the real clock.
from datetime import datetime as _datetime, timezone as _timezone  # noqa: E402

_RUN_AT = _datetime(2026, 12, 1, tzinfo=_timezone.utc)


def _run(tmp_path, members, cts, metrics_by_ticker, **kw):
    out = tmp_path / "out"
    kw.setdefault("run_started_at", _RUN_AT)
    summary = run_batch(
        company_type_rows=cts, membership_rows=members, pipeline_fn=kw.pop("pipeline", _pipeline(metrics_by_ticker)),
        fetch_fn=lambda t, c: {}, raw_dir=tmp_path / "raw", output_dir=out, **kw,
    )
    return summary, json.loads((out / "coverage.json").read_text(encoding="utf-8")), out


# ---------------------------------------------------------------- 14
def test_pit_watchlist_before_and_after_effective_date(tmp_path):
    members = [_member("AAA", "0000000001"), _member("NEW", "0000000002", effective_from="2026-11-10", available="2026-11-10")]
    cts = [_ct("AAA", "0000000001"), _ct("NEW", "0000000002")]
    data = {"AAA": _normalized(), "NEW": _normalized()}
    calls = []
    summary, c, _ = _run(tmp_path, members, cts, data, evaluation_date="2026-11-05", pipeline=_pipeline(data, calls))
    assert calls == ["AAA"]  # NEW not yet a member: never run, never fetched
    new = next(r for r in summary["records"] if r["ticker"] == "NEW")
    assert new["status"] == STATUS_EXCLUDED_NOT_IN_WATCHLIST
    assert new["watchlist_lookup_status"] == "NO_MEMBERSHIP_FOR_DATE"
    assert c["universe"]["pit_watchlist_members"] == 1
    calls.clear()
    summary2, c2, _ = _run(tmp_path, members, cts, data, evaluation_date="2026-11-10", pipeline=_pipeline(data, calls))
    assert calls == ["AAA", "NEW"]
    assert c2["universe"]["pit_watchlist_members"] == 2


def test_past_date_before_any_membership_runs_nothing(tmp_path):
    members = [_member("AAA", "0000000001")]
    summary, c, _ = _run(tmp_path, members, [_ct("AAA", "0000000001")], {"AAA": _normalized()}, evaluation_date="2026-09-01")
    assert summary["status_counts"] == {STATUS_EXCLUDED_NOT_IN_WATCHLIST: 1}
    assert c["normalized_metrics"]["revenue"]["r1_data_acquisition_rate"]["calculation_status"] == cov.NOT_COMPUTABLE


# ---------------------------------------------------------------- 15, 16
def test_subset_and_requested_not_in_watchlist(tmp_path):
    members = [_member("AAA", "0000000001"), _member("BBB", "0000000002")]
    cts = [_ct("AAA", "0000000001"), _ct("BBB", "0000000002")]
    data = {"AAA": _normalized(), "BBB": _normalized()}
    summary, c, _ = _run(tmp_path, members, cts, data, evaluation_date=EVAL, tickers=["aaa", "ZZZ"])
    assert [r["ticker"] for r in summary["records"]] == ["AAA"]
    assert c["universe"]["requested_not_in_watchlist"] == ["ZZZ"]
    assert c["inputs"]["tickers_filter"] == "AAA,ZZZ"
    assert summary["coverage"]["requested_not_in_watchlist"] == ["ZZZ"]


# ---------------------------------------------------------------- GE before / after 2026-11-02
def test_ge_gate_before_and_after_availability(tmp_path):
    rows = read_metric_eligibility_csv()
    members = [_member("GE", "0000040545")]
    cts = [_ct("GE", "0000040545")]
    data = {"GE": _normalized(cfo=_ok(SECRET_VALUE))}
    _s1, before, _ = _run(tmp_path, members, cts, data, evaluation_date="2026-11-01", metric_eligibility_rows=rows)
    assert before["normalized_metrics"]["cfo"]["governance_status_counts"] == {"NOT_YET_AVAILABLE": 1}
    assert before["normalized_metrics"]["cfo"]["r2_runtime_non_withheld_ok_rate"]["numerator"] == 1
    _s2, after, out = _run(tmp_path, members, cts, data, evaluation_date="2026-11-02", metric_eligibility_rows=rows)
    cfo = after["normalized_metrics"]["cfo"]
    assert cfo["governance_status_counts"] == {"WITHHELD": 1}
    assert cfo["data_status_counts"] == {"OK": 1}
    assert cfo["r2_runtime_non_withheld_ok_rate"]["numerator"] == 0
    assert after["normalized_metrics"]["total_debt"]["use_scope_counts"] == {"CONSOLIDATED_REPORTED": 1}
    assert after["normalized_metrics"]["revenue"]["governance_status_counts"] == {cov.NOT_GOVERNED: 1}
    files = (out / "coverage.json").read_text() + (out / "coverage.md").read_text() + (out / "summary.json").read_text()
    assert str(SECRET_VALUE) not in files
    rec = _s2["records"][0]
    assert rec["eligibility"]["no_record"] == []
    assert set(rec["eligibility"]["withheld"]) == {"operating_income", "cfo", "capex"}


# ---------------------------------------------------------------- 12 via run_batch, 21
def test_backward_compatible_summary_and_error_text_not_in_coverage(tmp_path):
    members = [_member("AAA", "0000000001"), _member("BAD", "0000000002"), _member("UNREG", "0000000009")]
    cts = [_ct("AAA", "0000000001"), _ct("BAD", "0000000002")]
    data = {"AAA": _normalized(), "BAD": _normalized()}
    summary, c, out = _run(tmp_path, members, cts, data, evaluation_date=EVAL, pipeline=_pipeline(data, fail_for=("BAD",)))
    # Existing summary keys and record fields are unchanged; coverage is additive.
    assert summary["schema_version"] == BATCH_SCHEMA_VERSION == "fundamentals_batch.v2"  # STEP 23 SEC PIT
    for key in ("schema_version", "generated_at_utc", "evaluation_date", "ticker_count", "status_counts",
                "eligibility_note", "records"):
        assert key in summary
    assert summary["status_counts"] == {STATUS_OK: 1, STATUS_ERROR: 1, STATUS_EXCLUDED_COMPANY_TYPE: 1}
    bad = next(r for r in summary["records"] if r["ticker"] == "BAD")
    assert bad["error_type"] == "ValueError" and "boom" in bad["error"]  # unchanged record content
    assert "/secret/path" not in (out / "coverage.json").read_text()
    assert "/secret/path" not in (out / "coverage.md").read_text()
    assert c["universe"]["errors_by_type"] == {"ValueError": 1}
    assert summary["coverage"]["coverage_file"] == "coverage.json"
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "| AAA | OK |" in md and "coverage.md" in md
    assert (out / "AAA.json").exists()


# ---------------------------------------------------------------- P2 Quality v0.2 aggregation

def _quality_stage(status, leverage, flags=(), reason=None, comps=None):
    return {
        "status": "OK",
        "quality": {
            "schema_version": "quality_v0.2",
            "element": {
                "status": status,
                "reason_code": reason,
                "assessed_dimensions": {"profitability": "ASSESSED", "cash_conversion": "ASSESSED", "leverage": leverage},
                "flags": list(flags),
            },
            "components": comps or {
                "net_margin": {"status": "OK", "value": SECRET_VALUE},
                "debt_to_cash": {"status": "MISSING", "reason_code": "INPUT_INCOMPLETE:total_debt"},
            },
        },
    }


def test_quality_v02_counts_without_values():
    r1 = _result(_normalized())
    r1["stages"]["quality"] = _quality_stage("OK", "NOT_ASSESSED", flags=["NONPOSITIVE_FCF_LEVERAGE_UNASSESSED"])
    r2 = _result(_normalized())
    r2["stages"]["quality"] = _quality_stage("OK", "PARTIALLY_ASSESSED")
    r3 = _result(_normalized())
    r3["stages"]["quality"] = _quality_stage("MISSING", "NOT_ASSESSED", reason="INSUFFICIENT_INDEPENDENT_COMPONENTS")
    c = _cov([_rec("A"), _rec("B"), _rec("C")], {"A": r1, "B": r2, "C": r3})
    qc = c["quality"]
    assert qc["element_status_counts"] == {"OK": 2, "MISSING": 1}
    assert qc["element_reason_code_counts"] == {"INSUFFICIENT_INDEPENDENT_COMPONENTS": 1}
    assert qc["quality_ok_with_leverage_not_assessed"] == 1
    assert qc["quality_ok_with_leverage_partially_assessed"] == 1
    assert qc["flag_counts"] == {"NONPOSITIVE_FCF_LEVERAGE_UNASSESSED": 1}
    assert qc["components"]["debt_to_cash"]["by_reason_code"] == {"INPUT_INCOMPLETE:total_debt": 3}
    assert c["stage_status_counts"]["quality"] == {"OK": 3}
    text = json.dumps(c) + cov.render_coverage_markdown(c)
    assert str(SECRET_VALUE) not in text
    assert "## Quality (v0.2)" in cov.render_coverage_markdown(c)


def test_quality_block_present_and_empty_without_quality_stage():
    c = _cov([_rec("A")], {"A": _result(_normalized())})
    assert c["quality"]["element_status_counts"] == {}
    assert "quality" not in c["stage_status_counts"]
    assert c["quality"]["stage_error_count"] == 0 and c["quality"]["stage_error_tickers"] == []      # P2 N3 defaults


# ---------------------------------------------------------------- P2 N3: Quality stage ERROR aggregation
SENSITIVE_SENTINEL = "SENSITIVE_SENTINEL"
_QERR = {"status": "ERROR", "quality": None, "error": {"type": "RuntimeError"}}


def test_quality_stage_errors_counted_sorted_and_excluded_from_element_counts():
    rs = {}
    for t in ("ZED", "AAA", "MID"):
        rs[t] = _result(_normalized())
    rs["MID"]["stages"]["quality"] = _quality_stage("OK", "ASSESSED")
    for t in ("ZED", "AAA"):
        rs[t]["stages"]["quality"] = dict(_QERR)
    c = _cov([_rec("ZED"), _rec("AAA"), _rec("MID")], rs)
    qc = c["quality"]
    assert qc["stage_error_count"] == 2 and qc["stage_error_tickers"] == ["AAA", "ZED"]
    assert c["stage_status_counts"]["quality"] == {"ERROR": 2, "OK": 1}
    assert qc["stage_error_count"] == c["stage_status_counts"]["quality"]["ERROR"]
    assert qc["element_status_counts"] == {"OK": 1}                     # ERROR tickers not in element counts
    assert sum(b["by_status"].get("OK", 0) for b in qc["components"].values()) == 1
    md = cov.render_coverage_markdown(c)
    assert "Quality stage ERROR (exception isolated to the stage; type only): 2 (AAA, ZED)" in md


def test_quality_stage_error_text_never_copied():
    rs = {"AAA": _result(_normalized())}
    rs["AAA"]["stages"]["quality"] = {"status": "ERROR", "quality": None, "error": {"type": "RuntimeError", "message": SENSITIVE_SENTINEL}}
    c = _cov([_rec("AAA")], rs)
    assert c["quality"]["stage_error_tickers"] == ["AAA"]
    assert SENSITIVE_SENTINEL not in json.dumps(c) and SENSITIVE_SENTINEL not in cov.render_coverage_markdown(c)


# ---------------------------------------------------------------- P2 N3: contract with Checker v2.9 (Q6 / Q7)
def _checker():
    import importlib.util
    from pathlib import Path as _P
    path = _P(__file__).resolve().parents[1] / "scripts" / "step23_checker_v2_3.py"
    spec = importlib.util.spec_from_file_location("checker_v2_9_contract", path)
    mod = importlib.util.module_from_spec(spec)
    import sys as _sys
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _contract_stage(kind):
    if kind == "error":
        return dict(_QERR)
    if kind == "unavailable":
        return {"status": "OK", "quality": {"schema_version": "quality_v0.2", "element": None}}
    return _quality_stage("OK", "PARTIALLY_ASSESSED", flags=["NONPOSITIVE_FCF_LEVERAGE_UNASSESSED"])


@pytest.mark.parametrize("kind", ["ok", "error", "unavailable"])
def test_contract_production_summary_and_coverage_match_checker(tmp_path, kind):
    """Production batch_runner._quality_summary + batch_coverage output, written by run_batch, must equal what
    Checker v2.9 re-derives from the ticker files (q_summary_expected / recompute_quality_coverage), per state."""
    ck = _checker()
    members = [_member("AAA", "0000000001"), _member("BBB", "0000000002")]
    cts = [_ct("AAA", "0000000001"), _ct("BBB", "0000000002")]
    data = {"AAA": _normalized(), "BBB": _normalized()}
    base = _pipeline(data)

    def pipeline(**kw):
        r = base(**kw)
        r["ticker"] = kw["ticker"]                       # the real pipeline result names its ticker (Checker D10 joins on it)
        r["stages"]["quality"] = _contract_stage(kind) if kw["ticker"] == "AAA" else _contract_stage("ok")
        return r
    summary, c, out = _run(tmp_path, members, cts, data, evaluation_date=EVAL, pipeline=pipeline)

    class Run:
        pass
    run = Run()
    run.label, run.coverage = "NEW", c
    run.summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    run.results = {r["ticker"]: json.loads((out / f"{r['ticker']}.json").read_text(encoding="utf-8"))
                   for r in run.summary["records"] if r["status"] == STATUS_OK}
    assert ck.q_summary_problems(run) == []                                   # Q7
    for rec in run.summary["records"]:
        if rec["status"] == STATUS_OK:
            want = ck.q_summary_expected(run.results[rec["ticker"]])
            assert json.dumps(rec["quality"], sort_keys=True) == json.dumps(want, sort_keys=True)
            assert [type(rec["quality"][k]) for k in ck.Q_SUMMARY_KEYS] == [type(want[k]) for k in ck.Q_SUMMARY_KEYS]
    ss, qc = ck.recompute_quality_coverage(run)                                # Q6
    assert c["stage_status_counts"]["quality"] == ss
    assert json.dumps(c["quality"], sort_keys=True) == json.dumps(qc, sort_keys=True)
    want_err = ["AAA"] if kind == "error" else []
    assert c["quality"]["stage_error_tickers"] == want_err and c["quality"]["stage_error_count"] == len(want_err)
