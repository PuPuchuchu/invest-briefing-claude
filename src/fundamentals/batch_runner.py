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
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

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
from src.fundamentals.universe_membership import (
    LOOKUP_OK as MEMBERSHIP_LOOKUP_OK,
    get_universe_membership_as_of,
)
from src.fundamentals.batch_coverage import (
    build_coverage,
    coverage_headline,
    render_coverage_markdown,
)
from src.fundamentals.orchestrator import SEC_PIT_POLICY_VERSION, sec_filed_cutoff_for
from src.fundamentals import sec_snapshot

BATCH_SCHEMA_VERSION = "fundamentals_batch.v2"

# STEP 23 SEC PIT (2026-11-02): batch-level operational metadata.
KST = ZoneInfo("Asia/Seoul")
# Operational minimum run time for the CURRENT KST evaluation date. EDGAR's
# latest same-day acceptance (22:00 US Eastern) is 11:00/12:00 KST the next
# day; 13:00 adds margin. It is NOT a guarantee that SEC data is complete
# (companyfacts processing can lag); exact reproducibility is defined by the
# stored snapshot (sec_snapshot), not by the run time.
RECOMMENDED_EARLIEST_RUN_KST = dtime(13, 0)
RUN_TIMING_EARLY_RUN = "EARLY_RUN"
RUN_TIMING_RECOMMENDED_WINDOW = "RECOMMENDED_WINDOW"
RUN_TIMING_HISTORICAL = "HISTORICAL"

OPERATIONAL_PASS = "PASS"
OPERATIONAL_REVIEW = "REVIEW"
OPERATIONAL_FAIL = "FAIL"

# The governance behaviour these results were produced under (DEC-055 gate:
# WITHHELD metrics masked; every dependency-map dependent of a withheld input
# listed as WITHHELD, including outputs not produced -- materialized=False).
# V1 (until 2026-11-03, main 83276c5) relabelled only produced dependents.
# Kept separate from SEC_PIT_POLICY_VERSION; changes only with the gate.
GOVERNANCE_POLICY_VERSION_GATED = "DEC055_GATE_V2_RELABEL_ALL_DEPENDENTS"
GOVERNANCE_POLICY_VERSION_NOT_APPLIED = "NO_METRIC_ELIGIBILITY_GATE"


class FutureEvaluationDateError(ValueError):
    """evaluation_date is later than the current KST date: refused before
    any SEC request."""

STATUS_OK = "OK"
STATUS_EXCLUDED_COMPANY_TYPE = "EXCLUDED_COMPANY_TYPE"
STATUS_ERROR = "ERROR"
# STEP 23-B1: ACTIVE WATCHLIST row exists but the ticker is not a WATCHLIST
# member as of the evaluation date (PIT). No SEC request, no pipeline run.
STATUS_EXCLUDED_NOT_IN_WATCHLIST = "EXCLUDED_NOT_IN_WATCHLIST"

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
# DEC-055: used instead of ELIGIBILITY_NOTE when metric_eligibility_rows
# are supplied.
ELIGIBILITY_NOTE_ENFORCED = (
    "Metric eligibility gate applied (DEC-055): metrics withheld in "
    "data/reference/metric_eligibility.csv are shown as WITHHELD, and "
    "their dependent outputs are relabelled WITHHELD. Metrics without a "
    "row are not reviewed by the gate (NO_RECORD). Scoped ELIGIBLE "
    "metrics (e.g. GE debt: consolidated reported borrowings only) carry "
    "their use_scope in the per-ticker results."
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


def _coerce_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError(f"Unparseable evaluation_date: {value!r}")


def _run_started_at(run_started_at: datetime | None) -> datetime:
    started = run_started_at or datetime.now(timezone.utc)
    if started.tzinfo is None or started.utcoffset() is None:
        raise ValueError("run_started_at must be timezone-aware.")
    return started.astimezone(timezone.utc)


def check_evaluation_date_not_future(evaluation_date: Any, run_started_at: datetime) -> date:
    """Refuse an evaluation date later than the KST date at run start."""
    eval_date = _coerce_date(evaluation_date)
    kst_today = run_started_at.astimezone(KST).date()
    if eval_date > kst_today:
        raise FutureEvaluationDateError(
            f"evaluation_date {eval_date.isoformat()} is after the current KST date {kst_today.isoformat()}."
        )
    return eval_date


def run_timing_status(evaluation_date: date, run_started_at: datetime) -> str:
    kst_now = run_started_at.astimezone(KST)
    if evaluation_date < kst_now.date():
        return RUN_TIMING_HISTORICAL
    if kst_now.time() < RECOMMENDED_EARLIEST_RUN_KST:
        return RUN_TIMING_EARLY_RUN
    return RUN_TIMING_RECOMMENDED_WINDOW


def _sec_cutoff_consistency(results: dict[str, dict], expected: str) -> dict:
    seen = {
        t: ((r.get("stages") or {}).get("pit_gating") or {}).get("sec_filed_cutoff_date")
        for t, r in sorted(results.items())
    }
    mismatched = sorted(t for t, v in seen.items() if v != expected)
    if not seen:
        status = "NO_RESULTS"
    elif mismatched:
        status = "MISMATCH"
    else:
        status = "MATCH"
    return {"status": status, "expected": expected, "mismatched_tickers": mismatched}


MANIFEST_FILE_WRITTEN_AND_REREAD = "WRITTEN_AND_REREAD"
MANIFEST_FILE_NOT_WRITTEN = "NOT_WRITTEN"
MANIFEST_FILE_WRITE_FAILED = "WRITE_FAILED"
MANIFEST_FILE_UNREADABLE = "UNREADABLE"


def _self_verify_snapshot(manifest: dict, raw_dir: Path | None, output_dir: Path | None) -> dict:
    """C2: verify the snapshot at the end of the run, before results are
    published. The expected hashes are computed in memory from the manifest
    the run built; the manifest file is written, read back from disk and
    checked against them (never against a hash stored inside itself), then
    every listed SEC input file in raw_dir is checked.

    manifest_file_status: WRITTEN_AND_REREAD (the only state that can be
    OK), NOT_WRITTEN (no output_dir: at best NOT_VERIFIABLE, never OK),
    WRITE_FAILED or UNREADABLE (always FAIL)."""
    expected_manifest = sec_snapshot.manifest_sha256(manifest)
    expected_content = sec_snapshot.snapshot_content_sha256(manifest)
    file_errors: list[str] = []
    to_verify: Any = manifest
    manifest_file_status = MANIFEST_FILE_NOT_WRITTEN
    if output_dir is not None:
        try:
            path = sec_snapshot.write_manifest(manifest, output_dir)
        except (OSError, TypeError, ValueError) as exc:
            file_errors.append(f"manifest could not be written: {type(exc).__name__}: {exc}")
            manifest_file_status = MANIFEST_FILE_WRITE_FAILED
        else:
            try:
                stored = path.read_bytes()
                to_verify = json.loads(stored.decode("utf-8"))
                if sec_snapshot.sha256_bytes(stored) != expected_manifest:
                    file_errors.append("stored manifest file bytes do not match the expected manifest_sha256")
                manifest_file_status = MANIFEST_FILE_WRITTEN_AND_REREAD
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                file_errors.append(f"stored manifest could not be re-read: {type(exc).__name__}: {exc}")
                to_verify = None
                manifest_file_status = MANIFEST_FILE_UNREADABLE
    if raw_dir is None:
        result = sec_snapshot.verify_manifest_with_reader(
            to_verify, lambda p: None,
            expected_manifest_sha256=expected_manifest, expected_snapshot_content_sha256=expected_content,
        )
    else:
        result = sec_snapshot.verify_manifest(
            to_verify, raw_dir,
            expected_manifest_sha256=expected_manifest, expected_snapshot_content_sha256=expected_content,
        )
    if file_errors:
        result["status"] = sec_snapshot.VERIFY_FAIL
        result["validation_errors"] = file_errors + list(result.get("validation_errors") or [])
        result["reason"] = "integrity check failed"
    elif manifest_file_status == MANIFEST_FILE_NOT_WRITTEN and result["status"] == sec_snapshot.VERIFY_OK:
        # R4: only the in-memory manifest and the raw files were checked;
        # without a stored manifest this is not the verified state.
        result["status"] = sec_snapshot.VERIFY_NOT_VERIFIABLE
        result["reason"] = "manifest file not written (no output_dir); stored manifest not verified"
    return {
        "verification_status": result["status"],
        "manifest_file_status": manifest_file_status,
        "reason": result.get("reason"),
        "expected_manifest_sha256": expected_manifest,
        "actual_manifest_sha256": result.get("actual_manifest_sha256"),
        "expected_snapshot_content_sha256": expected_content,
        "actual_snapshot_content_sha256": result.get("actual_snapshot_content_sha256"),
        "missing_files": result.get("missing_files") or [],
        "mismatched_files": result.get("mismatched_files") or [],
        "validation_errors": result.get("validation_errors") or [],
    }


def _reproducibility_block(
    *,
    eval_date: date,
    started: datetime,
    expected_sec_cutoff: str,
    results: dict[str, dict],
    manifest: dict,
    self_verification: dict,
    metric_eligibility_rows: list[dict] | None,
    run_metadata: dict | None,
) -> dict:
    timing = run_timing_status(eval_date, started)
    consistency = _sec_cutoff_consistency(results, expected_sec_cutoff)
    provenance = dict(run_metadata or {})
    flags = list(provenance.pop("operational_flags", []) or [])

    reasons: list[dict] = []
    if timing == RUN_TIMING_EARLY_RUN:
        reasons.append({"level": OPERATIONAL_REVIEW, "code": "EARLY_RUN",
                        "detail": "Current-date run before 13:00 KST; SEC filings of the previous US day may be incomplete."})
    if consistency["status"] == "MISMATCH":
        reasons.append({"level": OPERATIONAL_FAIL, "code": "SEC_CUTOFF_MISMATCH",
                        "detail": f"Tickers gated to a different SEC cutoff: {consistency['mismatched_tickers']}"})
    vstatus = self_verification["verification_status"]
    if vstatus == sec_snapshot.VERIFY_FAIL:
        reasons.append({"level": OPERATIONAL_FAIL, "code": "SNAPSHOT_VERIFICATION_FAILED",
                        "detail": f"{self_verification.get('reason')}: missing={self_verification['missing_files']} "
                                  f"mismatched={self_verification['mismatched_files']} "
                                  f"errors={self_verification['validation_errors'][:5]}"})
    if (vstatus == sec_snapshot.VERIFY_NOT_VERIFIABLE
            and self_verification.get("manifest_file_status") == MANIFEST_FILE_NOT_WRITTEN
            and manifest["status"] == sec_snapshot.SNAPSHOT_OK):
        reasons.append({"level": OPERATIONAL_REVIEW, "code": "SNAPSHOT_MANIFEST_NOT_WRITTEN",
                        "detail": "No output_dir: the manifest was not stored, so the snapshot is not verified."})
    if manifest["status"] == sec_snapshot.SNAPSHOT_INCOMPLETE:
        reasons.append({"level": OPERATIONAL_REVIEW, "code": "SNAPSHOT_INCOMPLETE",
                        "detail": "SEC inputs used without a stored file (no raw_dir)."})
    elif manifest["status"] == sec_snapshot.SNAPSHOT_NO_SEC_INPUTS:
        reasons.append({"level": OPERATIONAL_REVIEW, "code": "NO_SEC_INPUTS",
                        "detail": "No SEC input file was loaded in this batch."})
    for flag in flags:
        reasons.append({"level": OPERATIONAL_REVIEW, "code": str(flag), "detail": "Reported by the caller (provenance)."})
    levels = {r["level"] for r in reasons}
    status = OPERATIONAL_FAIL if OPERATIONAL_FAIL in levels else (OPERATIONAL_REVIEW if levels else OPERATIONAL_PASS)

    kst = started.astimezone(KST)
    return {
        "sec_pit_policy_version": SEC_PIT_POLICY_VERSION,
        "sec_filed_cutoff_date": expected_sec_cutoff,
        "sec_cutoff_consistency": consistency,
        "governance_policy_version": (
            GOVERNANCE_POLICY_VERSION_GATED if metric_eligibility_rows is not None
            else GOVERNANCE_POLICY_VERSION_NOT_APPLIED
        ),
        "run_started_at_utc": started.isoformat(),
        "run_started_at_kst": kst.isoformat(),
        "run_timing_status": timing,
        "sec_snapshot": {
            "manifest_version": manifest["manifest_version"],
            "manifest_file": sec_snapshot.MANIFEST_FILENAME,
            "status": manifest["status"],
            "file_count": manifest["file_count"],
            "snapshot_content_sha256": sec_snapshot.snapshot_content_sha256(manifest),
            "manifest_sha256": sec_snapshot.manifest_sha256(manifest),
            "inputs_without_stored_file": manifest["inputs_without_stored_file"],
            # C2: end-of-run verification (OK / FAIL / NO_SEC_INPUTS / NOT_VERIFIABLE).
            # Only OK is a pass; FAIL makes the operational review FAIL.
            "self_verification": self_verification,
        },
        "provenance": provenance,
        "operational_review": {"status": status, "reasons": reasons},
    }


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
    metric_eligibility_rows: list[dict] | None = None,
    run_started_at: datetime | None = None,
    run_metadata: dict | None = None,
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
    metric_eligibility_rows: data/reference/metric_eligibility.csv rows
        (DEC-055). When given, they are forwarded to pipeline_fn and each OK
        record gains an "eligibility" summary (withheld / scoped metrics).
        When None, results are unchanged.
    run_started_at: aware datetime of the batch start (default: now). Read
        once; used for the future-date refusal and the run-timing status.
    run_metadata: optional provenance (commit SHAs, reference-data hashes,
        Python version) collected by the caller; stored under
        summary["reproducibility"]["provenance"]. Its "operational_flags"
        list, if any, feeds the batch-level operational review.

    STEP 23 SEC PIT: an evaluation_date later than the current KST date is
    refused (FutureEvaluationDateError) before any lookup or SEC request.
    Every SEC input is read back from its stored file in raw_dir, and those
    exact bytes are hashed into the snapshot manifest and parsed as the
    pipeline input. The operational review (run timing, provenance,
    snapshot, cutoff consistency) is batch-level only: it never changes a
    ticker record, metric or score.
    """
    started = _run_started_at(run_started_at)
    eval_date_obj = check_evaluation_date_not_future(evaluation_date, started)
    expected_sec_cutoff = sec_filed_cutoff_for(eval_date_obj).isoformat()

    entries = load_watchlist_entries(membership_rows)
    requested_not_in_watchlist: list[str] = []
    if tickers:
        wanted = {t.upper() for t in tickers}
        known = {e["ticker"] for e in entries}
        requested_not_in_watchlist = sorted(wanted - known)
        entries = [e for e in entries if e["ticker"] in wanted]

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    if raw_dir is not None:
        raw_dir = Path(raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    results: dict[str, dict] = {}
    fetched_count = 0
    snapshot_entries: list[dict] = []
    inputs_without_stored_file: list[str] = []

    for entry in entries:
        ticker, cik = entry["ticker"], entry["cik"]
        record: dict[str, Any] = {"ticker": ticker, "cik": cik}

        # 0. STEP 23-B1: PIT WATCHLIST membership as of the evaluation date.
        membership = get_universe_membership_as_of(cik, "WATCHLIST", evaluation_date, membership_rows)
        if membership["lookup_status"] != MEMBERSHIP_LOOKUP_OK:
            record["status"] = STATUS_EXCLUDED_NOT_IN_WATCHLIST
            record["watchlist_lookup_status"] = membership["lookup_status"]
            records.append(record)
            continue

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
            fetched_at = None
            if cache_path is not None and cache_path.exists():
                source = sec_snapshot.SOURCE_CACHE
            elif fetch_fn is not None:
                if fetched_count and sleep_seconds:
                    time.sleep(sleep_seconds)
                fetched = fetch_fn(ticker, cik)
                fetched_at = datetime.now(timezone.utc).isoformat()
                fetched_count += 1
                source = sec_snapshot.SOURCE_FETCHED
                if cache_path is not None:
                    with cache_path.open("w", encoding="utf-8") as f:
                        json.dump(fetched, f, ensure_ascii=False)
                else:
                    raw = fetched  # no raw_dir: no stored file, snapshot INCOMPLETE
            else:
                raise RuntimeError(
                    "No cached companyfacts and no fetch function provided."
                )
            if cache_path is not None:
                # Read the stored file once: these bytes are hashed AND
                # parsed as the pipeline input (FETCHED included).
                raw, file_entry = sec_snapshot.load_input_file(cache_path, raw_dir)
                file_entry.update({"ticker": ticker, "cik": cik, "source": source})
                snapshot_entries.append(file_entry)
                record["raw_input"] = {
                    "path": file_entry["path"],
                    "sha256": file_entry["sha256"],
                    "size_bytes": file_entry["size_bytes"],
                    # Original acquisition time is unknown for a cached file.
                    "fetched_at_utc": fetched_at,
                    "loaded_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            else:
                inputs_without_stored_file.append(ticker)
                record["raw_input"] = {"path": None, "sha256": None, "size_bytes": None,
                                       "fetched_at_utc": fetched_at, "loaded_at_utc": None}
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
                **(
                    {"finance_lease_evidence_rows": finance_lease_evidence_rows}
                    if finance_lease_evidence_rows is not None
                    else {}
                ),
                **(
                    {"metric_eligibility_rows": metric_eligibility_rows}
                    if metric_eligibility_rows is not None
                    else {}
                ),
            )
            if finance_lease_evidence_rows is not None:
                record["total_debt_including_finance_leases"] = _attach_debt_tiers(
                    result, cik, evaluation_date, finance_lease_evidence_rows
                )
            if metric_eligibility_rows is not None:
                gate = (result.get("stages") or {}).get("eligibility_gate") or {}
                record["eligibility"] = {
                    key: gate.get(key) for key in ("status", "withheld", "scoped", "not_yet_available", "no_record")
                }
            record["status"] = STATUS_OK
            record["stage_statuses"] = _stage_statuses(result)
            record["metrics"] = _metric_summary(result)
            results[ticker] = result
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
    coverage = build_coverage(
        records=records,
        results=results,
        evaluation_date=eval_str,
        inputs={
            "metric_eligibility_rows_supplied": metric_eligibility_rows is not None,
            "finance_lease_evidence_rows_supplied": finance_lease_evidence_rows is not None,
            "tickers_filter": ",".join(sorted({t.upper() for t in tickers})) if tickers else None,
        },
        requested_not_in_watchlist=requested_not_in_watchlist,
        batch_schema_version=BATCH_SCHEMA_VERSION,
    )
    manifest = sec_snapshot.build_manifest(snapshot_entries, missing_stored_inputs=inputs_without_stored_file)
    self_verification = _self_verify_snapshot(manifest, raw_dir, output_dir)
    reproducibility = _reproducibility_block(
        eval_date=eval_date_obj,
        started=started,
        expected_sec_cutoff=expected_sec_cutoff,
        results=results,
        manifest=manifest,
        self_verification=self_verification,
        metric_eligibility_rows=metric_eligibility_rows,
        run_metadata=run_metadata,
    )
    summary = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_date": eval_str,
        "ticker_count": len(records),
        "status_counts": counts,
        "eligibility_note": (
            ELIGIBILITY_NOTE_ENFORCED if metric_eligibility_rows is not None else ELIGIBILITY_NOTE
        ),
        "records": records,
        "coverage": coverage_headline(coverage),
        # Batch-level only; ticker records, metrics and scores never read it.
        "operational_review": reproducibility.pop("operational_review"),
        "reproducibility": reproducibility,
    }
    if output_dir is not None:
        with (output_dir / "coverage.json").open("w", encoding="utf-8") as f:
            json.dump(coverage, f, ensure_ascii=False, indent=2, default=str)
        (output_dir / "coverage.md").write_text(render_coverage_markdown(coverage), encoding="utf-8")
        with (output_dir / "summary.json").open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
        (output_dir / "summary.md").write_text(render_summary_markdown(summary), encoding="utf-8")
    return summary


def _fmt_value(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value / 1e9:,.2f}B"
    return "—"


def _reproducibility_markdown(summary: dict) -> list[str]:
    rep = summary["reproducibility"]
    review = summary.get("operational_review") or {}
    snap = rep.get("sec_snapshot") or {}
    prov = rep.get("provenance") or {}
    lines = [
        f"Operational review (batch-level, does not change ticker results): **{review.get('status')}**"
        + ("".join(f"; {r['level']} {r['code']}" for r in review.get("reasons") or [])),
        "",
        f"SEC PIT: filed <= {rep.get('sec_filed_cutoff_date')} ({rep.get('sec_pit_policy_version')}); "
        f"cutoff consistency {rep.get('sec_cutoff_consistency', {}).get('status')}; "
        f"run timing {rep.get('run_timing_status')} (started {rep.get('run_started_at_kst')}).",
        "",
        f"SEC snapshot: {snap.get('status')}, {snap.get('file_count')} file(s), "
        f"content sha256 {snap.get('snapshot_content_sha256')}, manifest {snap.get('manifest_file')}; "
        f"self-verification {(snap.get('self_verification') or {}).get('verification_status')}.",
        "",
    ]
    if prov:
        lines += [
            f"Code: checkout {prov.get('checkout_head_sha')} · GITHUB_SHA {prov.get('github_sha')} "
            f"({prov.get('commit_sha_consistency')}) · Python {prov.get('python_version')}.",
            "",
        ]
    return lines


def render_summary_markdown(summary: dict) -> str:
    lines = [
        f"# Fundamentals batch — evaluation date {summary['evaluation_date']}",
        "",
        f"Tickers: {summary['ticker_count']} · status counts: "
        + ", ".join(f"{k}={v}" for k, v in sorted(summary["status_counts"].items())),
        "",
        f"> {summary['eligibility_note']}",
        "",
        *(
            [
                "Coverage: see coverage.md / coverage.json — PIT WATCHLIST members "
                f"{summary['coverage']['pit_watchlist_members']}, evaluated "
                f"{summary['coverage']['evaluated_tickers']}.",
                "",
            ]
            if summary.get("coverage")
            else []
        ),
        *(_reproducibility_markdown(summary) if summary.get("reproducibility") else []),
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
        if rec["status"] == STATUS_EXCLUDED_NOT_IN_WATCHLIST:
            note = f"WATCHLIST {rec.get('watchlist_lookup_status')}"
        elif rec["status"] == STATUS_EXCLUDED_COMPANY_TYPE:
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
    "FutureEvaluationDateError",
    "OPERATIONAL_PASS",
    "OPERATIONAL_REVIEW",
    "OPERATIONAL_FAIL",
    "check_evaluation_date_not_future",
    "run_timing_status",
    "STATUS_OK",
    "STATUS_EXCLUDED_COMPANY_TYPE",
    "STATUS_ERROR",
    "STATUS_EXCLUDED_NOT_IN_WATCHLIST",
    "ELIGIBILITY_NOTE",
    "ELIGIBILITY_NOTE_ENFORCED",
    "load_watchlist_entries",
    "run_batch",
    "render_summary_markdown",
    "load_reference_rows",
]
