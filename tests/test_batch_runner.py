"""
Offline tests for src/fundamentals/batch_runner.py.

The pipeline and the SEC fetch are injected fakes, so these tests never
touch the network and never run real extraction. One test uses the real
reference CSVs to check the batch's ticker selection and Company Type
gating against the actual registry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.fundamentals.batch_runner import (
    STATUS_ERROR,
    STATUS_EXCLUDED_COMPANY_TYPE,
    STATUS_OK,
    load_reference_rows,
    load_watchlist_entries,
    run_batch,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

CT_ROW_BASE = {
    "company_type": "NON_FINANCIAL",
    "effective_from": "2026-09-20",
    "effective_to": None,
    "classification_available_date": "2026-09-20",
    "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE",
    "mapping_rationale": "Synthetic test row.",
    "reviewed_at_utc": "2026-10-26T00:00:00Z",
    "decision_ref": "test",
}


def _ct_row(ticker, cik):
    return {"ticker": ticker, "cik": cik, **CT_ROW_BASE}


def _member(ticker, cik, universe_type="WATCHLIST"):
    return {
        "cik": cik,
        "ticker": ticker,
        "universe_type": universe_type,
        "membership_status": "ACTIVE",
        # STEP 23-B1: run_batch() now selects tickers by PIT WATCHLIST
        # membership, so fixtures carry the PIT fields (mirroring the real
        # CSV's 2026-09-21 effective/available dates).
        "membership_source": "LEGACY_SEED",
        "membership_version": "1",
        "effective_from": "2026-09-21",
        "effective_to": None,
        "membership_available_date": "2026-09-21",
    }


MEMBERS = [
    _member("AAA", "0000000001"),
    _member("BBB", "0000000002"),
    _member("UNREG", "0000000009"),
    _member("CCC", "0000000003", universe_type="CORE"),  # not WATCHLIST
]
CT_ROWS = [_ct_row("AAA", "0000000001"), _ct_row("BBB", "0000000002")]


def _fake_pipeline(calls, fail_for=()):
    def pipeline(**kwargs):
        calls.append(kwargs)
        if kwargs["ticker"] in fail_for:
            raise ValueError(f"boom {kwargs['ticker']}")
        return {
            "ticker": kwargs["ticker"],
            "stages": {
                "company_type_validation": {"status": "OK"},
                "normalization": {
                    "status": "OK",
                    "normalized": {
                        "metrics": {
                            "revenue": {
                                "status": "OK",
                                "value": 1.5e9,
                                "concept": "Revenues",
                                "accession": "0000000001-26-000001",
                            },
                            "cfo": {"status": "MISSING", "value": None},
                        }
                    },
                },
            },
        }

    return pipeline


def _fake_fetch(fetches):
    def fetch(ticker, cik):
        fetches.append(ticker)
        return {"cik": cik, "entityName": ticker, "facts": {}}

    return fetch


def test_watchlist_entries_are_distinct_active_watchlist_only():
    entries = load_watchlist_entries(MEMBERS)
    assert [e["ticker"] for e in entries] == ["AAA", "BBB", "UNREG"]


def test_unregistered_ticker_is_excluded_and_never_fetched(tmp_path):
    calls, fetches = [], []
    summary = run_batch(
        evaluation_date="2026-10-27",
        company_type_rows=CT_ROWS,
        membership_rows=MEMBERS,
        pipeline_fn=_fake_pipeline(calls),
        fetch_fn=_fake_fetch(fetches),
        raw_dir=tmp_path / "raw",
        output_dir=tmp_path / "out",
    )
    by = {r["ticker"]: r for r in summary["records"]}
    assert by["UNREG"]["status"] == STATUS_EXCLUDED_COMPANY_TYPE
    assert by["UNREG"]["company_type_lookup_status"] == "NO_MAPPING_FOR_DATE"
    assert "UNREG" not in fetches
    assert "UNREG" not in [c["ticker"] for c in calls]
    assert summary["status_counts"] == {STATUS_OK: 2, STATUS_EXCLUDED_COMPANY_TYPE: 1}


def test_pipeline_receives_registry_company_type_and_rows(tmp_path):
    calls = []
    run_batch(
        evaluation_date="2026-10-27",
        company_type_rows=CT_ROWS,
        membership_rows=MEMBERS,
        pipeline_fn=_fake_pipeline(calls),
        fetch_fn=_fake_fetch([]),
        raw_dir=tmp_path / "raw",
    )
    first = calls[0]
    assert first["company_type"] == "NON_FINANCIAL"
    assert first["company_type_rows"] is CT_ROWS
    assert first["membership_rows"] is MEMBERS
    assert first["raw_companyfacts"]["entityName"] == first["ticker"]


def test_fetched_raw_is_cached_and_reused(tmp_path):
    raw_dir = tmp_path / "raw"
    fetches = []
    run_batch(
        evaluation_date="2026-10-27",
        company_type_rows=CT_ROWS,
        membership_rows=MEMBERS,
        pipeline_fn=_fake_pipeline([]),
        fetch_fn=_fake_fetch(fetches),
        raw_dir=raw_dir,
    )
    assert sorted(fetches) == ["AAA", "BBB"]
    assert (raw_dir / "AAA_companyfacts.json").exists()

    fetches2 = []
    summary = run_batch(
        evaluation_date="2026-10-27",
        company_type_rows=CT_ROWS,
        membership_rows=MEMBERS,
        pipeline_fn=_fake_pipeline([]),
        fetch_fn=_fake_fetch(fetches2),
        raw_dir=raw_dir,
    )
    assert fetches2 == []
    assert {r.get("raw_source") for r in summary["records"] if r["status"] == STATUS_OK} == {"CACHE"}


def test_one_ticker_error_does_not_stop_the_batch(tmp_path):
    summary = run_batch(
        evaluation_date="2026-10-27",
        company_type_rows=CT_ROWS,
        membership_rows=MEMBERS,
        pipeline_fn=_fake_pipeline([], fail_for={"AAA"}),
        fetch_fn=_fake_fetch([]),
        raw_dir=tmp_path / "raw",
    )
    by = {r["ticker"]: r for r in summary["records"]}
    assert by["AAA"]["status"] == STATUS_ERROR
    assert by["AAA"]["error_type"] == "ValueError"
    assert by["BBB"]["status"] == STATUS_OK


def test_no_cache_and_no_fetch_is_error_not_silent_network(tmp_path):
    summary = run_batch(
        evaluation_date="2026-10-27",
        company_type_rows=CT_ROWS,
        membership_rows=MEMBERS,
        pipeline_fn=_fake_pipeline([]),
        fetch_fn=None,
        raw_dir=tmp_path / "raw",
    )
    statuses = {r["ticker"]: r["status"] for r in summary["records"]}
    assert statuses["AAA"] == STATUS_ERROR
    assert statuses["BBB"] == STATUS_ERROR


def test_outputs_written_with_metric_provenance(tmp_path):
    out = tmp_path / "out"
    run_batch(
        evaluation_date="2026-10-27",
        company_type_rows=CT_ROWS,
        membership_rows=MEMBERS,
        pipeline_fn=_fake_pipeline([]),
        fetch_fn=_fake_fetch([]),
        raw_dir=tmp_path / "raw",
        output_dir=out,
    )
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    aaa = next(r for r in summary["records"] if r["ticker"] == "AAA")
    assert aaa["metrics"]["revenue"]["accession"] == "0000000001-26-000001"
    assert aaa["metrics"]["cfo"]["status"] == "MISSING"
    assert (out / "AAA.json").exists()
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "| AAA | OK | 1.50B |" in md
    assert "NOT enforced" in md


def test_ticker_subset_filter(tmp_path):
    summary = run_batch(
        evaluation_date="2026-10-27",
        company_type_rows=CT_ROWS,
        membership_rows=MEMBERS,
        pipeline_fn=_fake_pipeline([]),
        fetch_fn=_fake_fetch([]),
        raw_dir=tmp_path / "raw",
        tickers=["bbb"],
    )
    assert [r["ticker"] for r in summary["records"]] == ["BBB"]


@pytest.mark.parametrize(
    "evaluation_date, expected_ok",
    [("2026-10-26", 0), ("2026-10-27", 18)],
)
def test_real_reference_data_gating(tmp_path, evaluation_date, expected_ok):
    """Against the real CSVs: 21 WATCHLIST tickers; on 2026-10-27 the 18
    registered rows are usable and BLK/JPM/UNH are excluded; on
    2026-10-26 nothing is usable yet (next-day availability, DEC-020)."""
    ct_rows, members = load_reference_rows(
        REPO_ROOT / "data/reference/company_type.csv",
        REPO_ROOT / "data/reference/universe_membership.csv",
    )
    summary = run_batch(
        evaluation_date=evaluation_date,
        company_type_rows=ct_rows,
        membership_rows=members,
        pipeline_fn=_fake_pipeline([]),
        fetch_fn=_fake_fetch([]),
        raw_dir=tmp_path / "raw",
    )
    assert summary["ticker_count"] == 21
    assert summary["status_counts"].get(STATUS_OK, 0) == expected_ok
    excluded = sorted(
        r["ticker"] for r in summary["records"] if r["status"] == STATUS_EXCLUDED_COMPANY_TYPE
    )
    if expected_ok == 18:
        assert excluded == ["BLK", "JPM", "UNH"]
    else:
        assert len(excluded) == 21


def test_quality_summary_reads_quality_stage_and_reports_absence():
    from src.fundamentals.batch_runner import QUALITY_SUMMARY_KEYS, _quality_summary
    absent = _quality_summary({"stages": {}})
    assert tuple(absent) == QUALITY_SUMMARY_KEYS and len(absent) == 13
    assert absent["_available"] is False and absent["flags"] == [] and absent["stage_status"] is None
    assert all(absent[k] is None for k in QUALITY_SUMMARY_KEYS if k not in ("_available", "flags"))
    result = {"stages": {"quality": {"status": "OK", "quality": {
        "schema_version": "quality_v0.2",
        "element": {
            "status": "OK", "reason_code": None, "score": 4.2, "stars": 4,
            "scored_weight_fraction": 0.7778, "rated_count": 4, "not_meaningful_count": 0,
            "assessed_dimensions": {"leverage": "NOT_ASSESSED"}, "flags": [],
        },
    }}}}
    s = _quality_summary(result)
    assert s["_available"] is True and s["status"] == "OK" and s["stars"] == 4
    assert s["leverage_assessment"] == "NOT_ASSESSED" and s["schema_version"] == "quality_v0.2"



# ---------------------------------------------------------------- P2 N3: Quality stage ERROR isolation (13-key summary)
_Q_ELEMENT = {"status": "OK", "reason_code": None, "score": 4.2, "stars": 4, "scored_weight_fraction": 0.7777777777777778,
              "rated_count": 4, "not_meaningful_count": 1,
              "assessed_dimensions": {"profitability": "ASSESSED", "cash_conversion": "ASSESSED", "leverage": "PARTIALLY_ASSESSED"},
              "flags": ["NONPOSITIVE_FCF_LEVERAGE_UNASSESSED"]}
_Q_OK = {"status": "OK", "quality": {"schema_version": "quality_v0.2", "element": _Q_ELEMENT, "components": {}}}
_Q_ERROR = {"status": "ERROR", "quality": None, "error": {"type": "RuntimeError"}}
_Q_NO_ELEMENT = {"status": "OK", "quality": {"schema_version": "quality_v0.2", "element": None}}


def test_quality_summary_thirteen_keys_per_state():
    from src.fundamentals.batch_runner import QUALITY_SUMMARY_KEYS, _quality_summary
    assert len(QUALITY_SUMMARY_KEYS) == 13 and len(set(QUALITY_SUMMARY_KEYS)) == 13
    ok = _quality_summary({"stages": {"quality": _Q_OK}})
    err = _quality_summary({"stages": {"quality": _Q_ERROR}})
    none = _quality_summary({"stages": {"quality": _Q_NO_ELEMENT}})
    for s in (ok, err, none):
        assert tuple(s) == QUALITY_SUMMARY_KEYS
    assert ok == {"_available": True, "schema_version": "quality_v0.2", "status": "OK", "reason_code": None, "score": 4.2,
                  "stars": 4, "scored_weight_fraction": 0.7777777777777778, "rated_count": 4, "not_meaningful_count": 1,
                  "leverage_assessment": "PARTIALLY_ASSESSED", "flags": ["NONPOSITIVE_FCF_LEVERAGE_UNASSESSED"],
                  "stage_status": "OK", "error_type": None}
    assert type(ok["stars"]) is int and type(ok["score"]) is float
    assert err == {**{k: None for k in QUALITY_SUMMARY_KEYS}, "_available": False, "flags": [], "stage_status": "ERROR",
                   "error_type": "RuntimeError"}
    assert none == {**{k: None for k in QUALITY_SUMMARY_KEYS}, "_available": False, "flags": [], "stage_status": "OK"}
    ok["flags"].append("x")                                     # the summary owns its list
    assert _Q_ELEMENT["flags"] == ["NONPOSITIVE_FCF_LEVERAGE_UNASSESSED"]


def _quality_pipeline(stage_by_ticker):
    inner = _fake_pipeline([])
    def pipeline(**kwargs):
        result = inner(**kwargs)
        result["stages"]["quality"] = json.loads(json.dumps(stage_by_ticker[kwargs["ticker"]]))
        return result
    return pipeline


def test_quality_error_ticker_stays_ok_and_others_unaffected(tmp_path):
    out = tmp_path / "out"
    summary = run_batch(
        evaluation_date="2026-10-27", company_type_rows=CT_ROWS, membership_rows=MEMBERS,
        pipeline_fn=_quality_pipeline({"AAA": _Q_OK, "BBB": _Q_ERROR}), fetch_fn=_fake_fetch([]),
        raw_dir=tmp_path / "raw", output_dir=out,
    )
    by = {r["ticker"]: r for r in summary["records"]}
    assert by["AAA"]["status"] == STATUS_OK and by["BBB"]["status"] == STATUS_OK
    assert by["BBB"]["stage_statuses"]["quality"] == "ERROR" and by["AAA"]["stage_statuses"]["quality"] == "OK"
    assert by["BBB"]["quality"]["error_type"] == "RuntimeError" and by["BBB"]["quality"]["_available"] is False
    assert by["AAA"]["quality"]["_available"] is True and by["AAA"]["quality"]["stars"] == 4
    for t in ("AAA", "BBB"):
        doc = json.loads((out / f"{t}.json").read_text(encoding="utf-8"))
        assert doc["stages"]["quality"]["status"] == ("ERROR" if t == "BBB" else "OK")
    assert json.loads((out / "BBB.json").read_text(encoding="utf-8"))["stages"]["quality"] == _Q_ERROR
