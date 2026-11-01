"""
STEP 23-B0: default evaluation date of scripts/run_fundamentals_batch.py is
today's date in Asia/Seoul (KST); an explicit --evaluation-date wins.
All times are fixed (no dependency on the real clock or the machine zone).
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_fundamentals_batch.py"
_spec = importlib.util.spec_from_file_location("run_fundamentals_batch_cli", _SCRIPT)
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

KST = ZoneInfo("Asia/Seoul")


@pytest.mark.parametrize(
    "kst_wall_time, expected",
    [
        (datetime(2026, 11, 1, 23, 59, tzinfo=KST), "2026-11-01"),
        (datetime(2026, 11, 2, 0, 0, tzinfo=KST), "2026-11-02"),
        (datetime(2026, 11, 2, 8, 59, tzinfo=KST), "2026-11-02"),
    ],
)
def test_kst_day_boundaries(kst_wall_time, expected):
    assert cli.default_evaluation_date(kst_wall_time) == expected


def test_kst_date_chosen_when_utc_date_differs():
    # 2026-11-01 23:59:59 UTC is 2026-11-02 08:59:59 KST.
    now_utc = datetime(2026, 11, 1, 23, 59, 59, tzinfo=timezone.utc)
    assert now_utc.date().isoformat() == "2026-11-01"
    assert cli.default_evaluation_date(now_utc) == "2026-11-02"
    # 2026-11-01 15:00 UTC is 2026-11-02 00:00 KST (first KST second of the day).
    assert cli.default_evaluation_date(datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)) == "2026-11-02"
    # 2026-11-01 14:59:59 UTC is still 2026-11-01 KST.
    assert cli.default_evaluation_date(datetime(2026, 11, 1, 14, 59, 59, tzinfo=timezone.utc)) == "2026-11-01"


def test_other_input_zones_are_converted_to_kst():
    # 2026-11-01 10:00 in UTC-05:00 is 2026-11-02 00:00 KST.
    now = datetime(2026, 11, 1, 10, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert cli.default_evaluation_date(now) == "2026-11-02"


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        cli.default_evaluation_date(datetime(2026, 11, 2, 0, 0))


def test_explicit_evaluation_date_wins_over_default():
    now = datetime(2026, 11, 2, 0, 0, tzinfo=KST)
    assert cli.resolve_evaluation_date("2026-10-27", now) == "2026-10-27"
    assert cli.resolve_evaluation_date(None, now) == "2026-11-02"
    assert cli.resolve_evaluation_date("", now) == "2026-11-02"


def test_parser_default_is_unset_and_explicit_is_passed_through():
    parser = cli.build_parser()
    assert parser.parse_args([]).evaluation_date is None
    assert parser.parse_args(["--evaluation-date", "2026-10-27"]).evaluation_date == "2026-10-27"


def test_main_passes_resolved_date_to_run_batch(monkeypatch):
    seen = {}

    def fake_run_batch(**kwargs):
        seen.update(kwargs)
        return {"evaluation_date": kwargs["evaluation_date"], "ticker_count": 0, "status_counts": {}, "records": []}

    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    monkeypatch.setattr(cli, "load_reference_rows", lambda: ([], []))
    monkeypatch.setattr(cli, "read_finance_lease_evidence_csv", lambda: [])
    monkeypatch.setattr(cli, "read_metric_eligibility_csv", lambda: [])
    monkeypatch.setattr(cli, "default_evaluation_date", lambda now=None: "2026-11-02")

    assert cli.main(["--no-fetch"]) == 0
    assert seen["evaluation_date"] == "2026-11-02"
    assert cli.main(["--no-fetch", "--evaluation-date", "2026-10-27"]) == 0
    assert seen["evaluation_date"] == "2026-10-27"
