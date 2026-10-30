"""
CLI: run the fundamentals pipeline for every WATCHLIST ticker.

Usage (from the repository root, with PYTHONPATH set to it):

    SEC_USER_AGENT="name email" python scripts/run_fundamentals_batch.py \
        --evaluation-date 2026-10-27 \
        --output-dir out/fundamentals_batch \
        --raw-dir data/raw/sec

Writes per-ticker result JSON, summary.json and summary.md to --output-dir.
Cached companyfacts in --raw-dir are reused; missing ones are fetched from
SEC EDGAR (requires SEC_USER_AGENT). Exits 1 if any ticker ended in ERROR,
so a CI run surfaces failures; EXCLUDED_COMPANY_TYPE is expected and does
not fail the run.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.fundamentals.batch_runner import (
    STATUS_ERROR,
    load_reference_rows,
    run_batch,
)
from src.fundamentals.finance_lease_evidence_registry_io import (
    read_finance_lease_evidence_csv,
)
from src.fundamentals.orchestrator import run_fundamentals_pipeline
from src.sec.fetcher import fetch_companyfacts, get_user_agent

# SEC fair-access guidance: stay well under 10 requests/second.
SEC_REQUEST_INTERVAL_SECONDS = 0.25


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluation-date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="ISO date (default: today, UTC).",
    )
    parser.add_argument("--output-dir", default="out/fundamentals_batch")
    parser.add_argument("--raw-dir", default="data/raw/sec")
    parser.add_argument(
        "--tickers",
        default="",
        help="Optional comma-separated subset, e.g. GE,MSFT.",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Use cached companyfacts only; never call SEC.",
    )
    args = parser.parse_args(argv)

    company_type_rows, membership_rows = load_reference_rows()
    finance_lease_evidence_rows = read_finance_lease_evidence_csv()

    fetch_fn = None
    if not args.no_fetch:
        user_agent = get_user_agent()

        def fetch_fn(ticker: str, cik: str) -> dict:
            return fetch_companyfacts(cik, user_agent=user_agent)

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or None

    summary = run_batch(
        evaluation_date=args.evaluation_date,
        company_type_rows=company_type_rows,
        membership_rows=membership_rows,
        pipeline_fn=run_fundamentals_pipeline,
        fetch_fn=fetch_fn,
        raw_dir=Path(args.raw_dir),
        output_dir=Path(args.output_dir),
        sleep_seconds=SEC_REQUEST_INTERVAL_SECONDS,
        tickers=tickers,
        finance_lease_evidence_rows=finance_lease_evidence_rows,
    )

    print(f"evaluation_date={summary['evaluation_date']} tickers={summary['ticker_count']}")
    print(f"status_counts={summary['status_counts']}")
    for rec in summary["records"]:
        extra = rec.get("error") or rec.get("company_type_lookup_status") or ""
        print(f"  {rec['ticker']:6s} {rec['status']:22s} {extra}")

    return 1 if summary["status_counts"].get(STATUS_ERROR) else 0


if __name__ == "__main__":
    sys.exit(main())
