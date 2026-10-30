"""
Batch runner for run_fundamentals_pipeline() across the WATCHLIST universe.

This is the first production caller of
src.fundamentals.orchestrator.run_fundamentals_pipeline(). It:

1. Loads the reference data the pipeline needs (company_type.csv,
   universe_membership.csv).
2. Builds the ticker list from universe_membership.csv (distinct CIK/ticker
   pairs with an ACTIVE WATCHLIST row).
3. For each ticker, checks the Company Type registry BEFORE any SEC request.
   A ticker whose lookup is not OK (e.g. BLK/UNH/JPM, which are unregistered
   by design) is recorded as EXCLUDED_COMPANY_TYPE and is not fetched.
4. Otherwise obtains raw companyfacts (from a local cache file if present,
   else via an injected fetch function), runs the pipeline, and records the
   result. Any exception for one ticker is recorded as ERROR for that ticker
   only; the batch continues.

What this module deliberately does NOT do:
- It does not compute any score, factor, or buy/sell signal.
- It does not enforce Production Eligibility (e.g. GE's withheld six
  extraction branches under Decision B-1.D). No such runtime gate exists;
  the summary reports this explicitly instead of implying one.
- It does not change any pipeline semantics; it only calls the pipeline.

Network access happens only through the injected fetch function, so tests
can run fully offline.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.fundamentals.company_type_registry import (
    LOOKUP_OK,
    get_company_type_as_of,
)
from src.fundamentals.company_type_registry_io import read_company_type_csv
from src.fundamentals.finance_lease_evidence_registry import (
    compute_total_debt_including_finance_leases,
    finance_lease_flag_for_total_debt,
)
from src.fundamentals.universe_membership_io import read_universe_membership_csv

BATCH_SCHEMA_VERSION = "fundamentals_batch.v1"

STATUS_OK = "OK"
STATUS_EXCLUDED_COMPANY_TYPE = "EXCLUDED_COMPANY_TYPE"
STATUS_ERROR = "ERROR"

DEFAULT_COMPANY_TYPE_CSV = Path("data/reference/company_type.csv")
DEFAULT_MEMBERSHIP_CSV = Path("data/reference/universe_membership.csv")
RAW_FILENAME_TEMPLATE = "{ticker}_companyfacts.json"

# Metrics surfaced in the summary table, from the NORMALIZATION stage.
SUMMARY_METRICS = (
    "revenue",
    "net_income",
    "operating_income",
    "cfo",
    "capex",
    "total_debt",
)

ELIGIBILITY_NOTE = (
    "Production Eligibility is NOT enforced by this runner. Governance "
    "status (e.g. GE's six classification-governed extraction branches "
    "withheld under Decision B-1.D, Decision Log DEC-023) must be applied "
    "by any consumer of these results."
)


def load_watchlist_entries(membership_rows: list[dict]) -> list[dict]:
    """Distinct (cik, ticker) pairs that have an ACTIVE WATCHLIST row,
    sorted by ticker. Effective/availability dates are not filtered here:
    the pipeline's own UNIVERSE_MEMBERSHIP stage evaluates them PIT."""
    seen: dict[str, str] = {}
    for row in membership_rows:
        if row.get("universe_type") != "WATCHLIST":
            continue
        if row.get("membership_status") != "ACTIVE":
            continue
        cik = row.get("cik")
        ticker = row.get("ticker")
        if cik and ticker:
            seen[cik] = ticker
    return sorted(
        ({"cik": cik, "ticker": ticker} for cik, ticker in seen.items()),
        key=lambda item: item["ticker"],
    )


def _metric_summary(result: dict) -> dict:
    """Status/value/concept for the summary metrics, read from the
    pipeline's NORMALIZATION stage. Missing structure is reported as
    absent rather than guessed."""
    stage = (result.get("stages") or {}).get("normalization") or {}
    normalized = stage.get("normalized") or {}
    metrics = normalized.get("metrics") if isinstance(normalized, dict) else None
    if not isinstance(metrics, dict):
        return {"_available": False}
    out: dict[str, Any] = {"_available": True}
    for name in SUMMARY_METRICS:
        metric = metrics.get(name) or {}
        out[name] = {
            key: metric.get(key)
            for key in (
                "status",
                "value",
                "concept",
                "unit",
                "period_end",
                "filing_date",
                "form",
                "accession",
            )
        }
    return out


def _stage_statuses(result: dict) -> dict:
    stages = result.get("stages") or {}
    return {
        name: (stage.get("status") if isinstance(stage, dict) else None)
        for name, stage in stages.items()
    }


def _attach_debt_tiers(result: dict, cik: str, evaluation_date: Any, evidence_rows: list[dict]) -> dict:
    """DEC-045: add the tier-2 metric as its own stage and, when evidence
    places finance leases outside total_debt, the D4 metadata flag on
    total_debt. total_debt's value and status are never changed. Returns a
    short summary for the batch record."""
    stages = result.setdefault("stages", {})
    normalized = ((stages.get("normalization") or {}).get("normalized") or {})
    metrics = normalized.get("metrics") if isinstance(normalized, dict) else None
    total_debt = metrics.get("total_debt") if isinstance(metrics, dict) else None
    tier2 = compute_total_debt_including_finance_leases(total_debt or {}, cik, evaluation_date, evidence_rows)
    stages["debt_tiers"] = {"status": STATUS_OK, "total_debt_including_finance_leases": tier2}
    flag = finance_lease_flag_for_total_debt(tier2)
    if flag is not None and isinstance(total_debt, dict):
        total_debt.update(flag)
    return {key: tier2.get(key) for key in ("status", "value", "reason_code", "finance_lease_added", "accession")}


def run_batch(
    *,
    evaluation_date: Any,
    company_type_rows: list[dict],
    membership_rows: list[dict],
    pipeline_fn: Callable[..., dict],
    fetch_fn: Callable[[str, str], dict] | None,
    raw_dir: Path | None = None,
    output_dir: Path | None = None,
    sleep_seconds: float = 0.0,
    tickers: list[str] | None = None,
    finance_lease_evidence_rows: list[dict] | None = None,
) -> dict:
    """
    Run the pipeline for every WATCHLIST ticker and return a summary dict.

    pipeline_fn: normally orchestrator.run_fundamentals_pipeline.
    fetch_fn(ticker, cik) -> raw companyfacts dict; used only when no cached
        file exists in raw_dir. If None and no cache exists, the ticker is
        recorded as ERROR (no implicit network access).
    raw_dir: where cached companyfacts are read from and fetched ones saved.
    output_dir: if given, per-ticker result JSON files are written there.
    tickers: optional subset filter (upper-case tickers).
    finance_lease_evidence_rows: data/reference/finance_lease_evidence.csv
        rows (DEC-045). When given, each OK result gains a "debt_tiers" stage
        with total_debt_including_finance_leases, and total_debt gains the D4
        metadata flag when evidence places finance leases outside it. When
        None, results are unchanged.
    """
    entries = load_watchlist_entries(membership_rows)
    if tickers:
        wanted = {t.upper() for t in tickers}
        entries = [e for e in entries if e["ticker"] in wanted]

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    if raw_dir is not None:
        raw_dir = Path(raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    fetched_count = 0

    for entry in entries:
        ticker, cik = entry["ticker"], entry["cik"]
        record: dict[str, Any] = {"ticker": ticker, "cik": cik}

        # 1. Company Type gate first: no SEC request for excluded tickers.
        lookup = get_company_type_as_of(cik, evaluation_date, company_type_rows)
        record["company_type_lookup_status"] = lookup["lookup_status"]
        if lookup["lookup_status"] != LOOKUP_OK:
            record["status"] = STATUS_EXCLUDED_COMPANY_TYPE
            record["reason"] = lookup.get("reason")
            records.append(record)
            continue
        company_type = lookup["mapping"]["company_type"]
        record["company_type"] = company_type

        try:
            # 2. Raw companyfacts: cache first, then injected fetch.
            raw = None
            source = None
            cache_path = (
                raw_dir / RAW_FILENAME_TEMPLATE.format(ticker=ticker)
                if raw_dir is not None
                else None
            )
            if cache_path is not None and cache_path.exists():
                with cache_path.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
                source = "CACHE"
            elif fetch_fn is not None:
                if fetched_count and sleep_seconds:
                    time.sleep(sleep_seconds)
                raw = fetch_fn(ticker, cik)
                fetched_count += 1
                source = "FETCHED"
                if cache_path is not None:
                    with cache_path.open("w", encoding="utf-8") as f:
                        json.dump(raw, f, ensure_ascii=False)
            else:
                raise RuntimeError(
                    "No cached companyfacts and no fetch function provided."
                )
            record["raw_source"] = source

            # 3. Pipeline.
            result = pipeline_fn(
                ticker=ticker,
                cik=cik,
                evaluation_date=evaluation_date,
                company_type=company_type,
                company_type_rows=company_type_rows,
                raw_companyfacts=raw,
                membership_rows=membership_rows,
            )
            if finance_lease_evidence_rows is not None:
                record["total_debt_including_finance_leases"] = _attach_debt_tiers(
                    result, cik, evaluation_date, finance_lease_evidence_rows
                )
            record["status"] = STATUS_OK
            record["stage_statuses"] = _stage_statuses(result)
            record["metrics"] = _metric_summary(result)
            if output_dir is not None:
                with (output_dir / f"{ticker}.json").open("w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        except Exception as exc:  # one ticker's failure never stops the batch
            record["status"] = STATUS_ERROR
            record["error_type"] = type(exc).__name__
            record["error"] = str(exc)[:500]
        records.append(record)

    counts: dict[str, int] = {}
    for rec in records:
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1

    eval_str = (
        evaluation_date.isoformat()
        if isinstance(evaluation_date, (date, datetime))
        else str(evaluation_date)
    )
    summary = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_date": eval_str,
        "ticker_count": len(records),
        "status_counts": counts,
        "eligibility_note": ELIGIBILITY_NOTE,
        "records": records,
    }
    if output_dir is not None:
        with (output_dir / "summary.json").open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
        (output_dir / "summary.md").write_text(render_summary_markdown(summary), encoding="utf-8")
    return summary


def _fmt_value(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value / 1e9:,.2f}B"
    return "—"


def render_summary_markdown(summary: dict) -> str:
    lines = [
        f"# Fundamentals batch — evaluation date {summary['evaluation_date']}",
        "",
        f"Tickers: {summary['ticker_count']} · status counts: "
        + ", ".join(f"{k}={v}" for k, v in sorted(summary["status_counts"].items())),
        "",
        f"> {summary['eligibility_note']}",
        "",
        "| Ticker | Status | " + " | ".join(SUMMARY_METRICS) + " | Note |",
        "|" + "---|" * (len(SUMMARY_METRICS) + 3),
    ]
    for rec in summary["records"]:
        cells = []
        metrics = rec.get("metrics") or {}
        for name in SUMMARY_METRICS:
            m = metrics.get(name) if metrics.get("_available") else None
            if not m:
                cells.append("—")
            elif m.get("status") == "OK":
                cells.append(_fmt_value(m.get("value")))
            else:
                cells.append(str(m.get("status")))
        if rec["status"] == STATUS_EXCLUDED_COMPANY_TYPE:
            note = f"Company Type {rec.get('company_type_lookup_status')}"
        elif rec["status"] == STATUS_ERROR:
            note = f"{rec.get('error_type')}: {rec.get('error', '')[:80]}"
        else:
            note = rec.get("raw_source", "")
        lines.append(
            f"| {rec['ticker']} | {rec['status']} | " + " | ".join(cells) + f" | {note} |"
        )
    lines.append("")
    return "\n".join(lines)


def load_reference_rows(
    company_type_csv: Path = DEFAULT_COMPANY_TYPE_CSV,
    membership_csv: Path = DEFAULT_MEMBERSHIP_CSV,
) -> tuple[list[dict], list[dict]]:
    return read_company_type_csv(company_type_csv), read_universe_membership_csv(membership_csv)


__all__ = [
    "BATCH_SCHEMA_VERSION",
    "STATUS_OK",
    "STATUS_EXCLUDED_COMPANY_TYPE",
    "STATUS_ERROR",
    "ELIGIBILITY_NOTE",
    "load_watchlist_entries",
    "run_batch",
    "render_summary_markdown",
    "load_reference_rows",
]
