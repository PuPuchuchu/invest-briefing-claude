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
import hashlib
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from src.fundamentals.batch_runner import (
    DEFAULT_COMPANY_TYPE_CSV,
    DEFAULT_MEMBERSHIP_CSV,
    STATUS_ERROR,
    FutureEvaluationDateError,
    check_evaluation_date_not_future,
    load_reference_rows,
    run_batch,
)
from src.fundamentals.metric_eligibility_registry import assert_valid_metric_eligibility_reference_data
from src.fundamentals.metric_eligibility_registry_io import read_metric_eligibility_csv
from src.fundamentals.finance_lease_evidence_registry_io import (
    DEFAULT_FINANCE_LEASE_EVIDENCE_CSV,
    read_finance_lease_evidence_csv,
)
from src.fundamentals.metric_eligibility_registry_io import DEFAULT_METRIC_ELIGIBILITY_CSV
from src.fundamentals.orchestrator import run_fundamentals_pipeline
from src.sec.fetcher import fetch_companyfacts, get_user_agent

# SEC fair-access guidance: stay well under 10 requests/second.
SEC_REQUEST_INTERVAL_SECONDS = 0.25

# STEP 23-B0: the default evaluation date is today's date in Korea (KST).
# PIT availability dates in the reference data (e.g. DEC-020's next-
# calendar-day KST convention) are KST calendar days; the previous UTC
# default lagged by up to 9 hours. An explicit --evaluation-date always
# wins and is passed through unchanged.
EVALUATION_DATE_TIMEZONE = ZoneInfo("Asia/Seoul")


def default_evaluation_date(now: datetime | None = None) -> str:
    """Today's date in Asia/Seoul as YYYY-MM-DD.

    now: an aware datetime (any zone) for reproducible tests; defaults to
    the current instant. A naive datetime is rejected so the result never
    depends on the machine's local time zone.
    """
    if now is None:
        now = datetime.now(EVALUATION_DATE_TIMEZONE)
    elif now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    return now.astimezone(EVALUATION_DATE_TIMEZONE).date().isoformat()


def resolve_evaluation_date(explicit: str | None, now: datetime | None = None) -> str:
    """The explicit --evaluation-date if given (unchanged), else the KST default."""
    if explicit:
        return explicit
    return default_evaluation_date(now)


# STEP 23 SEC PIT (2026-11-02): run provenance. GITHUB_* variables are set
# by GitHub Actions itself (no workflow change needed); outside Actions they
# are null. The workflow SHA identifies the workflow file's commit and is NOT
# treated as the code SHA.
GITHUB_ENV_FIELDS = {
    "github_sha": "GITHUB_SHA",
    "github_workflow_sha": "GITHUB_WORKFLOW_SHA",
    "github_run_id": "GITHUB_RUN_ID",
    "github_run_attempt": "GITHUB_RUN_ATTEMPT",
    "github_workflow_ref": "GITHUB_WORKFLOW_REF",
    "github_event_name": "GITHUB_EVENT_NAME",
}

REFERENCE_FILES = {
    "company_type.csv": DEFAULT_COMPANY_TYPE_CSV,
    "universe_membership.csv": DEFAULT_MEMBERSHIP_CSV,
    "finance_lease_evidence.csv": DEFAULT_FINANCE_LEASE_EVIDENCE_CSV,
    "metric_eligibility.csv": DEFAULT_METRIC_ELIGIBILITY_CSV,
}


def checkout_head_sha() -> str | None:
    """`git rev-parse HEAD` of the working directory, or None."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha or None


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def collect_run_metadata(env: dict | None = None, head_sha: str | None = None) -> dict:
    """Provenance for summary["reproducibility"]["provenance"]. A missing or
    mismatched code SHA becomes an operational flag (batch-level REVIEW)."""
    env = os.environ if env is None else env
    meta = {key: (env.get(var) or None) for key, var in GITHUB_ENV_FIELDS.items()}
    meta["checkout_head_sha"] = head_sha
    meta["python_version"] = platform.python_version()
    meta["reference_data_hashes"] = {name: file_sha256(path) for name, path in REFERENCE_FILES.items()}
    flags = []
    if meta["github_sha"] and head_sha:
        meta["commit_sha_consistency"] = "MATCH" if meta["github_sha"] == head_sha else "MISMATCH"
    else:
        meta["commit_sha_consistency"] = "UNKNOWN"
    if meta["commit_sha_consistency"] == "MISMATCH":
        flags.append("COMMIT_SHA_MISMATCH")
    elif meta["commit_sha_consistency"] == "UNKNOWN":
        flags.append("COMMIT_SHA_UNVERIFIED")
    if any(h is None for h in meta["reference_data_hashes"].values()):
        flags.append("REFERENCE_HASH_UNAVAILABLE")
    meta["operational_flags"] = flags
    return meta


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluation-date",
        default=None,
        help="ISO date (default: today in Asia/Seoul, KST).",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Read the clock once: the default evaluation date, the future-date
    # refusal and the run-timing status all use this same instant.
    run_started_at = datetime.now(timezone.utc)
    evaluation_date = resolve_evaluation_date(args.evaluation_date, run_started_at)
    try:
        check_evaluation_date_not_future(evaluation_date, run_started_at)
    except FutureEvaluationDateError as exc:
        print(f"Refused before any SEC request: {exc}", file=sys.stderr)
        return 2

    company_type_rows, membership_rows = load_reference_rows()
    finance_lease_evidence_rows = read_finance_lease_evidence_csv()
    metric_eligibility_rows = read_metric_eligibility_csv()
    assert_valid_metric_eligibility_reference_data(metric_eligibility_rows)

    fetch_fn = None
    if not args.no_fetch:
        user_agent = get_user_agent()

        def fetch_fn(ticker: str, cik: str) -> dict:
            return fetch_companyfacts(cik, user_agent=user_agent)

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or None

    summary = run_batch(
        evaluation_date=evaluation_date,
        company_type_rows=company_type_rows,
        membership_rows=membership_rows,
        pipeline_fn=run_fundamentals_pipeline,
        fetch_fn=fetch_fn,
        raw_dir=Path(args.raw_dir),
        output_dir=Path(args.output_dir),
        sleep_seconds=SEC_REQUEST_INTERVAL_SECONDS,
        tickers=tickers,
        finance_lease_evidence_rows=finance_lease_evidence_rows,
        metric_eligibility_rows=metric_eligibility_rows,
        run_started_at=run_started_at,
        run_metadata=collect_run_metadata(head_sha=checkout_head_sha()),
    )

    print(f"evaluation_date={summary['evaluation_date']} tickers={summary['ticker_count']}")
    print(f"status_counts={summary['status_counts']}")
    review = summary.get("operational_review") or {}
    if review:
        print(f"operational_review={review.get('status')} "
              + ",".join(r.get("code", "") for r in review.get("reasons") or []))
    for rec in summary["records"]:
        extra = rec.get("error") or rec.get("company_type_lookup_status") or ""
        print(f"  {rec['ticker']:6s} {rec['status']:22s} {extra}")

    return 1 if summary["status_counts"].get(STATUS_ERROR) else 0


if __name__ == "__main__":
    sys.exit(main())
