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
