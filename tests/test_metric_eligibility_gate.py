"""
Tests for the runtime metric eligibility gate (Decision Log DEC-055):
src/fundamentals/metric_eligibility_registry.py, its CSV I/O, the
orchestrator wiring and the batch-runner forwarding.
"""

from __future__ import annotations

import copy
import json

import pytest

from src.fundamentals import metric_eligibility_registry as reg
from src.fundamentals.metric_eligibility_registry_io import (
    METRIC_ELIGIBILITY_FIELDNAMES,
    read_metric_eligibility_csv,
    write_metric_eligibility_csv,
)
from src.fundamentals.orchestrator import STAGE_OK, STAGE_SKIPPED, run_fundamentals_pipeline

CIK = "0000999001"


def _row(metric, outcome, status, **kw):
    row = {
        "ticker": "TST",
        "cik": CIK,
        "metric": metric,
        "outcome": outcome,
        "governance_status": status,
        "use_scope": None,
        "effective_from": "2026-10-29",
        "effective_to": None,
        "eligibility_available_date": "2026-11-02",
        "reviewed_at_utc": "2026-11-01T00:45:35Z",
        "policy_version": "METRIC_ELIGIBILITY_V1",
        "decision_ref": "TEST",
        "rationale": "test",
    }
    row.update(kw)
    return row


ROWS = [
    _row("cfo", "2", "WITHHELD"),
    _row("capex", "3", "WITHHELD"),
    _row("operating_income", "3", "WITHHELD"),
    _row("total_debt", "1", "ELIGIBLE", use_scope="CONSOLIDATED_REPORTED"),
]


# ------------------------------------------------------------------
# Real reference data
# ------------------------------------------------------------------

def test_repository_csv_is_valid_and_matches_dec054():
    rows = read_metric_eligibility_csv()
    reg.assert_valid_metric_eligibility_reference_data(rows)
    by_metric = {r["metric"]: r for r in rows}
    assert {r["ticker"] for r in rows} == {"GE"}
    assert {r["cik"] for r in rows} == {"0000040545"}
    assert set(by_metric) == set(reg.GOVERNED_METRICS)
    assert {m: (by_metric[m]["outcome"], by_metric[m]["governance_status"]) for m in by_metric} == {
        "operating_income": ("3", "WITHHELD"),
        "cfo": ("2", "WITHHELD"),
        "capex": ("3", "WITHHELD"),
        "current_debt": ("1", "ELIGIBLE"),
        "noncurrent_debt": ("1", "ELIGIBLE"),
        "total_debt": ("1", "ELIGIBLE"),
    }
    for m in ("current_debt", "noncurrent_debt", "total_debt"):
        assert by_metric[m]["use_scope"] == "CONSOLIDATED_REPORTED"
    assert {r["eligibility_available_date"] for r in rows} == {"2026-11-02"}
    assert all(r["decision_ref"].startswith("DEC-054") for r in rows)


def test_csv_round_trip(tmp_path):
    path = write_metric_eligibility_csv(ROWS, tmp_path / "me.csv")
    back = read_metric_eligibility_csv(path)
    assert [r["metric"] for r in back] == [r["metric"] for r in ROWS]
    assert back[0]["use_scope"] is None
    assert list(back[0]) == METRIC_ELIGIBILITY_FIELDNAMES


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------

def test_valid_rows_pass():
    report = reg.validate_metric_eligibility_reference_data(ROWS)
    assert report == {"row_failures": {}, "overlaps": []}


@pytest.mark.parametrize(
    "override, failure",
    [
        ({"outcome": "1", "governance_status": "WITHHELD"}, "outcome_status_mismatch"),
        ({"outcome": "2", "governance_status": "ELIGIBLE"}, "outcome_status_mismatch"),
        ({"outcome": "5"}, "outcome"),
        ({"governance_status": "MAYBE"}, "governance_status"),
        ({"metric": "revenue"}, "metric"),
        ({"use_scope": "CONSOLIDATED_REPORTED"}, "use_scope_on_withheld"),
        ({"use_scope": "SEGMENT"}, "use_scope"),
        ({"eligibility_available_date": None}, "eligibility_available_date"),
        ({"reviewed_at_utc": "2026-11-01"}, "reviewed_at_utc"),
        ({"decision_ref": None}, "decision_ref"),
        ({"cik": "GE"}, "cik"),
        ({"effective_to": "2026-10-01"}, "effective_interval"),
    ],
)
def test_invalid_row_reports_failure(override, failure):
    row = _row("cfo", "2", "WITHHELD")
    row.update(override)
    assert failure in reg.validate_metric_eligibility_row(row)


def test_overlapping_intervals_detected_and_raise():
    rows = [_row("cfo", "2", "WITHHELD"), _row("cfo", "2", "WITHHELD", effective_from="2026-11-01")]
    assert reg.validate_metric_eligibility_reference_data(rows)["overlaps"] == [(0, 1)]
    with pytest.raises(ValueError):
        reg.assert_valid_metric_eligibility_reference_data(rows)


def test_adjacent_intervals_do_not_overlap():
    rows = [
        _row("cfo", "2", "WITHHELD", effective_to="2026-12-01"),
        _row("cfo", "1", "ELIGIBLE", effective_from="2026-12-01"),
    ]
    assert reg.validate_metric_eligibility_reference_data(rows)["overlaps"] == []


# ------------------------------------------------------------------
# PIT lookup
# ------------------------------------------------------------------

def test_lookup_respects_availability_date():
    assert reg.get_metric_eligibility_as_of(CIK, "cfo", "2026-11-01", ROWS)["lookup_status"] == reg.LOOKUP_NOT_YET_AVAILABLE
    assert reg.get_metric_eligibility_as_of(CIK, "cfo", "2026-11-02", ROWS)["lookup_status"] == reg.LOOKUP_WITHHELD


def test_lookup_respects_effective_interval_and_cik():
    rows = [_row("cfo", "2", "WITHHELD", effective_to="2026-12-01")]
    assert reg.get_metric_eligibility_as_of(CIK, "cfo", "2026-11-30", rows)["lookup_status"] == reg.LOOKUP_WITHHELD
    assert reg.get_metric_eligibility_as_of(CIK, "cfo", "2026-12-01", rows)["lookup_status"] == reg.LOOKUP_NO_RECORD
    assert reg.get_metric_eligibility_as_of("999001", "cfo", "2026-11-05", rows)["lookup_status"] == reg.LOOKUP_WITHHELD
    assert reg.get_metric_eligibility_as_of("0000000002", "cfo", "2026-11-05", rows)["lookup_status"] == reg.LOOKUP_NO_RECORD


def test_lookup_eligible():
    assert reg.get_metric_eligibility_as_of(CIK, "total_debt", "2026-11-05", ROWS)["lookup_status"] == reg.LOOKUP_ELIGIBLE


# ------------------------------------------------------------------
# Masking and relabelling (unit level)
# ------------------------------------------------------------------

def _normalized():
    ok = lambda v: {"status": "OK", "value": v, "unit": "USD", "concept": "X", "namespace": "us-gaap"}
    return {
        "metrics": {
            "cfo": ok(100),
            "capex": ok(-20),
            "operating_income": {"status": "INCOMPLETE", "value": None, "unit": None},
            "current_debt": ok(5),
            "noncurrent_debt": ok(15),
            "total_debt": ok(20),
            "revenue": ok(500),
        }
    }


def test_gate_masks_withheld_and_keeps_originals():
    norm = _normalized()
    gate = reg.apply_metric_eligibility_gate(norm, CIK, "2026-11-05", ROWS)
    assert gate["withheld"] == ["operating_income", "cfo", "capex"]
    assert gate["scoped"] == ["total_debt"]
    assert gate["no_record"] == ["current_debt", "noncurrent_debt"]
    cfo = norm["metrics"]["cfo"]
    assert cfo["status"] == "WITHHELD" and cfo["value"] is None and cfo["unit"] == "USD"
    assert cfo["original_status"] == "OK"
    assert cfo["eligibility"]["outcome"] == "2"
    assert gate["unmasked_originals"]["cfo"]["value"] == 100
    assert norm["metrics"]["operating_income"]["original_status"] == "INCOMPLETE"


def test_gate_scope_is_metadata_only():
    norm = _normalized()
    reg.apply_metric_eligibility_gate(norm, CIK, "2026-11-05", ROWS)
    td = norm["metrics"]["total_debt"]
    assert td["status"] == "OK" and td["value"] == 20
    assert td["eligibility"]["use_scope"] == "CONSOLIDATED_REPORTED"


def test_gate_before_availability_changes_nothing():
    norm = _normalized()
    before = copy.deepcopy(norm)
    gate = reg.apply_metric_eligibility_gate(norm, CIK, "2026-11-01", ROWS)
    assert norm == before
    assert gate["withheld"] == [] and set(gate["not_yet_available"]) == {"cfo", "capex", "operating_income", "total_debt"}


def test_gate_raises_on_invalid_reference_data():
    with pytest.raises(ValueError):
        reg.apply_metric_eligibility_gate(_normalized(), CIK, "2026-11-05", [_row("cfo", "1", "WITHHELD")])


def test_relabel_dependents():
    derived = {"derived_metrics": {
        "fcf": {"status": "MISSING"}, "fcf_margin": {"status": "MISSING"}, "cfo_yoy": {"status": "MISSING"},
        "capex_yoy": {"status": "MISSING"}, "operating_margin": {"status": "MISSING"},
        "operating_income_yoy": {"status": "MISSING"}, "net_debt": {"status": "OK", "value": 1},
        "revenue_yoy": {"status": "OK", "value": 0.1}, "net_margin": {"status": "OK", "value": 0.2},
    }}
    growth = {"growth": {"status": "OK"}, "components": {
        "fcf_yoy": {"status": "MISSING"}, "operating_income_yoy": {"status": "MISSING"}, "revenue_yoy": {"status": "OK"}}}
    suff = {"fcf_yoy": {"calculation_status": "MISSING", "reason_code": "CONCEPT_NOT_FOUND"},
            "revenue_yoy": {"calculation_status": "OK", "reason_code": None}}
    changed = reg.relabel_withheld_dependents(["cfo", "capex", "operating_income"], derived, growth, suff)
    dm = derived["derived_metrics"]
    for name in ("fcf", "fcf_margin", "cfo_yoy", "capex_yoy", "operating_margin", "operating_income_yoy"):
        assert dm[name]["status"] == "WITHHELD", name
    assert dm["fcf"]["withheld_inputs"] == ["cfo", "capex"]
    assert dm["net_debt"] == {"status": "OK", "value": 1}
    assert dm["revenue_yoy"]["status"] == "OK" and dm["net_margin"]["status"] == "OK"
    assert growth["components"]["fcf_yoy"]["status"] == "WITHHELD"
    assert growth["components"]["revenue_yoy"]["status"] == "OK"
    assert growth["growth"]["withheld_components"] == ["fcf_yoy", "operating_income_yoy"]
    assert suff["fcf_yoy"]["calculation_status"] == "WITHHELD"
    assert suff["fcf_yoy"]["reason_code"] == reg.SUFFICIENCY_REASON_WITHHELD
    assert suff["revenue_yoy"]["calculation_status"] == "OK"
    assert "net_debt" not in changed["derived"]


def test_relabel_nothing_when_no_withheld():
    derived = {"derived_metrics": {"fcf": {"status": "OK", "value": 1}}}
    reg.relabel_withheld_dependents([], derived, None, None)
    assert derived["derived_metrics"]["fcf"]["status"] == "OK"


# ------------------------------------------------------------------
# Orchestrator integration
# ------------------------------------------------------------------

def _fact(val, start, end, accn, filed, fy):
    out = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": "FY", "form": "10-K", "filed": filed}
    if start:
        out["start"] = start
    return out


def _facts(pairs, instant=False):
    a = ("0000999001-25-000001", "2025-02-01", 2024)
    b = ("0000999001-26-000001", "2026-02-01", 2025)
    rows = []
    for (acc, filed, fy), (start, end), val in zip((a, b), (("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31")), pairs):
        rows.append(_fact(val, None if instant else start, end, acc, filed, fy))
    return {"units": {"USD": rows}}


FIXTURE = {
    "cik": 999001,
    "entityName": "Test Co",
    "facts": {"us-gaap": {
        "Revenues": _facts((1000, 1200)),
        "NetCashProvidedByUsedInOperatingActivities": _facts((200, 240)),
        "PaymentsToAcquirePropertyPlantAndEquipment": _facts((50, 60)),
        "DebtCurrent": _facts((10, 12), instant=True),
        "LongTermDebtNoncurrent": _facts((90, 88), instant=True),
    }},
}

CT_ROWS = [{
    "ticker": "TST", "cik": CIK, "company_type": "NON_FINANCIAL", "effective_from": "2026-09-20",
    "effective_to": None, "classification_available_date": "2026-09-20",
    "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE", "mapping_rationale": "test fixture",
    "reviewed_at_utc": "2026-10-26T00:00:00Z", "decision_ref": "TEST",
}]


def _run(rows, evaluation_date="2026-11-05"):
    return run_fundamentals_pipeline(
        ticker="TST", cik=CIK, evaluation_date=evaluation_date, company_type="NON_FINANCIAL",
        company_type_rows=copy.deepcopy(CT_ROWS), raw_companyfacts=copy.deepcopy(FIXTURE),
        **({"metric_eligibility_rows": rows} if rows is not None else {}),
    )


def test_orchestrator_without_rows_skips_gate_and_is_unchanged():
    res = _run(None)
    assert res["stages"]["eligibility_gate"]["status"] == STAGE_SKIPPED
    metrics = res["stages"]["normalization"]["normalized"]["metrics"]
    assert metrics["cfo"]["status"] == "OK"
    assert res["stages"]["derived_metrics"]["derived"]["derived_metrics"]["fcf"]["status"] == "OK"


def test_orchestrator_with_rows_masks_and_relabels():
    plain = _run(None)
    res = _run(ROWS)
    gate = res["stages"]["eligibility_gate"]
    assert gate["status"] == STAGE_OK
    assert set(gate["withheld"]) == {"cfo", "capex", "operating_income"}
    metrics = res["stages"]["normalization"]["normalized"]["metrics"]
    assert metrics["cfo"]["status"] == "WITHHELD" and metrics["cfo"]["value"] is None
    assert gate["unmasked_originals"]["cfo"]["value"] == plain["stages"]["normalization"]["normalized"]["metrics"]["cfo"]["value"]
    dm = res["stages"]["derived_metrics"]["derived"]["derived_metrics"]
    assert dm["fcf"]["status"] == "WITHHELD"
    assert dm["fcf"]["withheld_inputs"] == ["cfo", "capex"]
    # total_debt eligible: same value as without the gate, scope tagged.
    td = metrics["total_debt"]
    plain_td = plain["stages"]["normalization"]["normalized"]["metrics"]["total_debt"]
    assert (td["status"], td["value"]) == (plain_td["status"], plain_td["value"])
    assert td["eligibility"]["use_scope"] == "CONSOLIDATED_REPORTED"
    assert dm["net_debt"] == plain["stages"]["derived_metrics"]["derived"]["derived_metrics"]["net_debt"]
    # unaffected outputs identical
    assert dm.get("revenue_yoy") == plain["stages"]["derived_metrics"]["derived"]["derived_metrics"].get("revenue_yoy")
    assert dm["net_margin"] == plain["stages"]["derived_metrics"]["derived"]["derived_metrics"]["net_margin"]
    suff = res["stages"]["data_sufficiency"]["sufficiency"]
    assert suff["fcf_yoy"]["calculation_status"] == "WITHHELD"
    assert res["stages"]["growth"]["growth"]["components"]["fcf_yoy"]["status"] == "WITHHELD"


def test_orchestrator_rows_not_yet_available_change_nothing_but_the_gate_stage():
    plain = _run(None, "2026-11-01")
    res = _run(ROWS, "2026-11-01")
    assert res["stages"]["eligibility_gate"]["withheld"] == []
    for r in (plain, res):
        r["stages"]["normalization"]["normalized"].pop("normalized_at", None)
    for stage in ("normalization", "derived_metrics", "growth", "data_sufficiency"):
        assert res["stages"][stage] == plain["stages"][stage], stage


def test_orchestrator_invalid_rows_raise():
    with pytest.raises(ValueError):
        _run([_row("cfo", "3", "ELIGIBLE")])


# ------------------------------------------------------------------
# Batch runner forwarding
# ------------------------------------------------------------------

# STEP 23 SEC PIT: run_batch refuses evaluation dates after the run's KST date;
# a fixed run start keeps these fixed-date tests independent of the real clock.
from datetime import datetime as _datetime, timezone as _timezone  # noqa: E402

_RUN_AT = _datetime(2026, 12, 1, tzinfo=_timezone.utc)

def test_batch_runner_forwards_rows_and_reports(tmp_path):
    from src.fundamentals.batch_runner import ELIGIBILITY_NOTE, ELIGIBILITY_NOTE_ENFORCED, run_batch

    seen = {}

    def pipeline(**kwargs):
        seen.update(kwargs)
        return {"stages": {
            "eligibility_gate": {"status": "OK", "withheld": ["cfo"], "scoped": [], "not_yet_available": []},
            "normalization": {"normalized": {"metrics": {"cfo": {"status": "WITHHELD", "value": None}}}},
        }}

    members = [{"cik": CIK, "ticker": "TST", "universe_type": "WATCHLIST", "membership_status": "ACTIVE",
                "membership_source": "LEGACY_SEED", "membership_version": "1", "effective_from": "2026-09-21",
                "effective_to": None, "membership_available_date": "2026-09-21"}]
    summary = run_batch(
        evaluation_date="2026-11-05", company_type_rows=CT_ROWS, membership_rows=members,
        pipeline_fn=pipeline, fetch_fn=lambda t, c: {}, raw_dir=tmp_path / "raw",
        output_dir=tmp_path / "out", metric_eligibility_rows=ROWS, run_started_at=_RUN_AT,
    )
    rec = summary["records"][0]
    assert seen.get("metric_eligibility_rows") is ROWS
    assert rec["eligibility"]["withheld"] == ["cfo"]
    assert rec["metrics"]["cfo"]["status"] == "WITHHELD"
    assert summary["eligibility_note"] == ELIGIBILITY_NOTE_ENFORCED
    md = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "WITHHELD" in md


def test_batch_runner_without_rows_keeps_old_note(tmp_path):
    from src.fundamentals.batch_runner import ELIGIBILITY_NOTE, run_batch

    calls = []

    def pipeline(**kwargs):
        calls.append(kwargs)
        return {"stages": {"normalization": {"normalized": {"metrics": {}}}}}

    members = [{"cik": CIK, "ticker": "TST", "universe_type": "WATCHLIST", "membership_status": "ACTIVE",
                "membership_source": "LEGACY_SEED", "membership_version": "1", "effective_from": "2026-09-21",
                "effective_to": None, "membership_available_date": "2026-09-21"}]
    summary = run_batch(
        evaluation_date="2026-11-05", company_type_rows=CT_ROWS, membership_rows=members,
        pipeline_fn=pipeline, fetch_fn=lambda t, c: {}, raw_dir=tmp_path / "raw", run_started_at=_RUN_AT,
    )
    assert "metric_eligibility_rows" not in calls[0]
    assert "eligibility" not in summary["records"][0]
    assert summary["eligibility_note"] == ELIGIBILITY_NOTE
