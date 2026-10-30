"""
DEC-049 (2026-10-30): tier-1 lease-scope resolution from filing-specific
evidence, and the NOT_DISCLOSED_IMMATERIAL evidence form. LLY shapes use the
FY2024 10-K (accession 0000059478-25-000067) values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.fundamentals.orchestrator as orchestrator
from src.fundamentals.batch_runner import run_batch
from src.fundamentals.finance_lease_evidence_registry import (
    compute_total_debt_including_finance_leases,
    finance_lease_flag_for_total_debt,
    resolve_lease_scope_mismatch_with_evidence,
    validate_finance_lease_evidence_reference_data,
    validate_finance_lease_evidence_row,
)
from src.fundamentals.finance_lease_evidence_registry_io import read_finance_lease_evidence_csv

REPO_ROOT = Path(__file__).resolve().parents[1]
CSV = REPO_ROOT / "data" / "reference" / "finance_lease_evidence.csv"
EVAL = "2026-11-02"
ACC = "0000059478-25-000067"
END = "2024-12-31"
CUR = 5_117_100_000
NON = 28_527_100_000


def _lly_row(**over):
    row = {
        "ticker": "LLY", "cik": "0000059478", "accession": ACC, "form": "10-K",
        "filing_date": "2025-02-19", "period_end": END, "unit": "USD",
        "finance_lease_status": "PRESENT", "finance_lease_amount_status": "NOT_DISCLOSED_IMMATERIAL",
        "finance_lease_liability_current": None, "finance_lease_liability_noncurrent": None,
        "finance_lease_liability_total": None,
        "current_location": "Short-term borrowings and current maturities of long-term debt",
        "noncurrent_location": "Long-term debt",
        "current_in_total_debt": "INCLUDED", "noncurrent_in_total_debt": "INCLUDED",
        "tag_scope_capability": "x", "evidence_basis": "LEASE_NOTE_STATES_INCLUDED_IN_DEBT_LINES",
        "source_urls": "https://www.sec.gov/x", "evidence_rationale": "synthetic",
        "evidence_available_date": "2026-10-31", "reviewed_at_utc": "2026-10-30T15:11:00Z",
        "policy_version": "LEASE_TIER2_V1", "decision_ref": "DEC-049",
    }
    row.update(over)
    return row


def _mismatch(cur=CUR, non=NON, acc=ACC, end=END, cur_acc=None):
    def comp(v, a, scope):
        return {"status": "OK", "value": v, "unit": "USD", "accession": a, "period_end": end,
                "lease_scope": scope}
    return {
        "metric": "total_debt", "status": "INCOMPLETE", "value": None,
        "reason_code": "LEASE_SCOPE_MISMATCH", "reason": "mismatch", "unqualified_sum": cur + non,
        "components": {
            "current_debt": comp(cur, cur_acc or acc, "CAPITAL_FINANCE_LEASE_INCLUDED"),
            "noncurrent_debt": comp(non, acc, "CAPITAL_FINANCE_LEASE_EXCLUDED"),
        },
    }


def _facts(val=CUR + NON, acc=ACC, end=END):
    return {"facts": {"us-gaap": {"DebtLongtermAndShorttermCombinedAmount": {"units": {"USD": [
        {"accn": acc, "end": end, "val": val, "form": "10-K"}]}}}}}


# ------------------------------------------------------------ validation

def test_not_disclosed_immaterial_row_is_valid():
    assert validate_finance_lease_evidence_row(_lly_row()) == []


def test_not_disclosed_rejects_amounts_excluded_parts_and_other_bases():
    assert "finance_lease_liability_total" in validate_finance_lease_evidence_row(
        _lly_row(finance_lease_liability_total="1"))
    assert "noncurrent_in_total_debt" in validate_finance_lease_evidence_row(
        _lly_row(noncurrent_in_total_debt="EXCLUDED"))
    assert "evidence_basis" in validate_finance_lease_evidence_row(
        _lly_row(evidence_basis="DEBT_TABLE_FINANCE_LEASE_ROW"))


def test_present_row_requires_amount_status():
    assert "finance_lease_amount_status" in validate_finance_lease_evidence_row(
        _lly_row(finance_lease_amount_status=None))
    assert "finance_lease_amount_status" in validate_finance_lease_evidence_row(
        _lly_row(finance_lease_amount_status="ESTIMATED"))


def test_committed_csv_is_valid_and_has_three_lly_rows():
    rows = read_finance_lease_evidence_csv(CSV)
    report = validate_finance_lease_evidence_reference_data(rows)
    assert report == {"row_failures": {}, "duplicates": []}
    lly = [r for r in rows if r["ticker"] == "LLY"]
    assert sorted(r["period_end"] for r in lly) == ["2022-12-31", "2023-12-31", "2024-12-31"]
    assert all(r["finance_lease_amount_status"] == "NOT_DISCLOSED_IMMATERIAL" for r in lly)
    assert all(r["finance_lease_amount_status"] == "DISCLOSED" for r in rows if r["ticker"] != "LLY")


# ------------------------------------------------------------ resolution

def test_resolves_mismatch_with_evidence_and_same_filing_reconciliation():
    td = _mismatch()
    out = resolve_lease_scope_mismatch_with_evidence(td, "59478", EVAL, [_lly_row()], _facts())
    assert out["status"] == "OK" and out["value"] == CUR + NON and out["unit"] == "USD"
    assert out["lease_scope"] == "CAPITAL_FINANCE_LEASE_INCLUDED"
    assert "reason_code" not in out and "unqualified_sum" not in out
    res = out["lease_scope_resolution"]
    assert res["status"] == "RESOLVED" and res["accession"] == ACC and res["decision_ref"] == "DEC-049"
    assert td["status"] == "INCOMPLETE"  # input not mutated


def test_no_matching_evidence_returns_same_object():
    td = _mismatch()
    assert resolve_lease_scope_mismatch_with_evidence(td, "59478", EVAL, [], _facts()) is td
    other = _lly_row(accession="0000059478-24-000065", period_end="2023-12-31")
    assert resolve_lease_scope_mismatch_with_evidence(td, "59478", EVAL, [other], _facts()) is td


def test_only_lease_scope_mismatch_is_considered():
    ok = dict(_mismatch(), status="OK", value=1, reason_code=None)
    assert resolve_lease_scope_mismatch_with_evidence(ok, "59478", EVAL, [_lly_row()], _facts()) is ok
    other = dict(_mismatch(), reason_code="CURRENT_COMPONENT_NOT_COVERED")
    assert resolve_lease_scope_mismatch_with_evidence(other, "59478", EVAL, [_lly_row()], _facts()) is other


def test_components_from_different_filings_are_not_resolved():
    td = _mismatch(cur_acc="0000059478-26-000081")
    assert resolve_lease_scope_mismatch_with_evidence(td, "59478", EVAL, [_lly_row()], _facts()) is td


@pytest.mark.parametrize("facts,code_part", [
    (_facts(val=CUR + NON + 1), "does not equal"),
    ({"facts": {"us-gaap": {}}}, "absent"),
    (_facts(acc="0000059478-26-000013"), "absent"),
])
def test_reconciliation_failure_keeps_incomplete(facts, code_part):
    out = resolve_lease_scope_mismatch_with_evidence(_mismatch(), "59478", EVAL, [_lly_row()], facts)
    assert out["status"] == "INCOMPLETE" and out["reason_code"] == "LEASE_SCOPE_MISMATCH"
    assert out["lease_scope_resolution"]["status"] == "NOT_RESOLVED"
    assert code_part in out["lease_scope_resolution"]["reason"]


def test_evidence_not_yet_available_is_not_resolved():
    out = resolve_lease_scope_mismatch_with_evidence(_mismatch(), "59478", "2026-10-30", [_lly_row()], _facts())
    assert out["status"] == "INCOMPLETE"
    assert "NOT_YET_AVAILABLE" in out["lease_scope_resolution"]["reason"]


def test_other_included_bases_do_not_resolve_scope():
    row = _lly_row(finance_lease_amount_status="DISCLOSED", finance_lease_liability_current="1",
                   finance_lease_liability_noncurrent="2", finance_lease_liability_total="3",
                   evidence_basis="DEBT_TABLE_FINANCE_LEASE_ROW")
    out = resolve_lease_scope_mismatch_with_evidence(_mismatch(), "59478", EVAL, [row], _facts())
    assert out["status"] == "INCOMPLETE" and out["lease_scope_resolution"]["status"] == "NOT_RESOLVED"


def test_tier2_equals_resolved_tier1_without_flag():
    resolved = resolve_lease_scope_mismatch_with_evidence(_mismatch(), "59478", EVAL, [_lly_row()], _facts())
    t2 = compute_total_debt_including_finance_leases(resolved, "59478", EVAL, [_lly_row()])
    assert t2["status"] == "OK" and t2["value"] == CUR + NON and t2["finance_lease_added"] == 0
    assert finance_lease_flag_for_total_debt(t2) is None


# ------------------------------------------------------------ wiring

class _Stop(Exception):
    pass


def _run_orchestrator(monkeypatch, rows):
    seen = {}
    monkeypatch.setattr(orchestrator, "validate_companyfacts", lambda raw: None)
    monkeypatch.setattr(orchestrator, "gate_companyfacts_as_of", lambda raw, d: raw)
    monkeypatch.setattr(orchestrator, "normalize_company",
                        lambda t, data, ct: {"metrics": {"total_debt": _mismatch()}})

    def stop(normalized):
        seen["total_debt"] = normalized["metrics"]["total_debt"]
        raise _Stop

    monkeypatch.setattr(orchestrator, "validate_normalized_data", stop)
    ct = [{"ticker": "LLY", "cik": "0000059478", "company_type": "NON_FINANCIAL",
           "effective_from": "2026-09-20", "effective_to": None, "classification_available_date": "2026-09-20",
           "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE", "mapping_rationale": "t",
           "reviewed_at_utc": "2026-10-26T00:00:00Z", "decision_ref": "t"}]
    kwargs = {} if rows is None else {"finance_lease_evidence_rows": rows}
    with pytest.raises(_Stop):
        orchestrator.run_fundamentals_pipeline(
            ticker="LLY", cik="0000059478", evaluation_date=EVAL, company_type="NON_FINANCIAL",
            company_type_rows=ct, raw_companyfacts=_facts(), **kwargs)
    return seen["total_debt"]


def test_orchestrator_resolves_before_derived_metrics(monkeypatch):
    td = _run_orchestrator(monkeypatch, [_lly_row()])
    assert td["status"] == "OK" and td["value"] == CUR + NON


def test_orchestrator_without_rows_is_unchanged(monkeypatch):
    td = _run_orchestrator(monkeypatch, None)
    assert td["status"] == "INCOMPLETE" and "lease_scope_resolution" not in td


def test_batch_passes_rows_to_pipeline_only_when_given(tmp_path):
    calls = []

    def pipeline(**kwargs):
        calls.append(kwargs)
        return {"ticker": kwargs["ticker"], "stages": {"normalization": {"status": "OK", "normalized": {
            "metrics": {"total_debt": _mismatch()}}}}}

    members = [{"cik": "0000059478", "ticker": "LLY", "universe_type": "WATCHLIST", "membership_status": "ACTIVE"}]
    ct = [{"ticker": "LLY", "cik": "0000059478", "company_type": "NON_FINANCIAL", "effective_from": "2026-09-20",
           "effective_to": None, "classification_available_date": "2026-09-20",
           "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE", "mapping_rationale": "t",
           "reviewed_at_utc": "2026-10-26T00:00:00Z", "decision_ref": "t"}]
    common = dict(evaluation_date=EVAL, company_type_rows=ct, membership_rows=members, pipeline_fn=pipeline,
                  fetch_fn=lambda t, c: {}, raw_dir=tmp_path / "raw")
    run_batch(**common)
    run_batch(**common, finance_lease_evidence_rows=[_lly_row()])
    assert "finance_lease_evidence_rows" not in calls[0]
    assert calls[1]["finance_lease_evidence_rows"] == [_lly_row()]
