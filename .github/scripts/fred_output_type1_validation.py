"""
Step 10J-QA-5 -- FRED output_type=1 ("Observations by Real-Time Period")
LIVE validation script (GitHub-hosted runner only, workflow_dispatch-only).

PURPOSE: this is the Step 10I-equivalent live validation round for Design
D ("Production PIT Snapshot") from
claude/2026-10-05-step10j-qa4-design-d-pit-snapshot-review.md. Step 10I
validated output_type=2's real response shape against the live FRED API
BEFORE it was built into src/macro/fred_provider.py; this script performs
the same discipline for output_type=1 + realtime_start=realtime_end=as_of
BEFORE any such code is written. Exactly like Step 10G's
fred_connectivity_smoke_test.py and Step 10I's
fred_multi_vintage_validation.py, this script:

  - is intentionally NOT imported by anything under src/ and lives
    outside the package, so it is obviously disconnected from production
    code
  - does NOT modify, import for mutation, or touch src/macro/fred_provider.py,
    src/macro/pit.py, src/macro/schema.py, src/macro/engine.py, or any
    growth/inflation/policy/curve/risk state module
  - DOES import src/macro/pit.py's public functions READ-ONLY (exactly as
    fred_provider_live_smoke_test.py's Section G already does) to prove,
    against REAL FRED data, that pit.py's existing (unchanged)
    select_latest_vintage_as_of()/get_point_in_time_series() correctly
    consume output_type=1 rows via the vintage_date-absent ->
    realtime_start fallback path that already exists in pit.py today
  - writes no CSV, no macro-state, no regime, no briefing output
  - runs on workflow_dispatch only, never scheduled
  - makes its own raw urllib calls to FRED for output_type=1 (there is no
    output_type=1 code path in fred_provider.py to call -- that is
    exactly the gap this script exists to close before any is written)

SCOPE: this script performs LIVE VALIDATION ONLY. It does not implement
Design D in production code. See
claude/2026-10-05-step10j-qa4-design-d-pit-snapshot-review.md section I
for the (not-yet-written) minimal future code change set this validation
is meant to de-risk.

SECURITY: FRED_API_KEY is read only from the environment (injected by the
calling workflow from secrets.FRED_API_KEY). Every URL this script prints
has its api_key query-string value redacted (see _redact()) before
printing -- mirrors fred_connectivity_smoke_test.py's and
fred_multi_vintage_validation.py's identical discipline. This script
never dumps os.environ, never shells out to curl, and uses only Python's
standard library (urllib, json, os, re, sys, time, socket, ssl,
datetime) -- no new dependency installed.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# Repo-root import fix, same pattern as the other .github/scripts/*.py
# validators. Used ONLY to read-only-import src.macro.pit's public
# functions for section G's PIT-compatibility check below -- never to
# import or touch src.macro.fred_provider, src.macro.schema, or
# src.macro.engine.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.macro.pit import (  # noqa: E402
    get_point_in_time_series,
    select_latest_vintage_as_of,
)

FRED_OBSERVATIONS_BASE = "https://api.stlouisfed.org/fred/series/observations"

SERIES_TO_CHECK = ["CPIAUCSL", "PAYEMS", "T10Y2Y", "VIXCLS"]
DAILY_SERIES = {"T10Y2Y", "VIXCLS"}
MONTHLY_SERIES = {"CPIAUCSL", "PAYEMS"}

RESULTS: dict = {}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _print_header(title: str) -> None:
    _log("")
    _log("=" * 88)
    _log(title)
    _log("=" * 88)


def _redact(url: str) -> str:
    """Replace the api_key query-string value with REDACTED before this
    URL is ever printed. Applied unconditionally -- mirrors
    fred_connectivity_smoke_test.py's / fred_provider.py's identical
    discipline. Never relies on remembering to redact at each call
    site."""
    return re.sub(r"(api_key=)[^&]+", r"\1REDACTED", url)


def _get_api_key() -> str:
    value = os.environ.get("FRED_API_KEY")
    if not value:
        _log("FATAL: FRED_API_KEY environment variable is not set.")
        raise SystemExit(1)
    return value


def _build_url(params: dict) -> str:
    query = dict(params)
    query["api_key"] = _get_api_key()
    query["file_type"] = "json"
    return f"{FRED_OBSERVATIONS_BASE}?{urllib.parse.urlencode(query)}"


def _request(params: dict) -> dict:
    """Returns a dict: {ok, status, elapsed, response_bytes, body_json,
    classification, detail, redacted_url}. Never raises -- every failure
    mode is caught and classified, mirroring
    fred_connectivity_smoke_test.py's _request(). The api_key is never
    present in any field of this return value except indirectly inside
    the (never-printed) raw url, which is immediately discarded after
    building the redacted version."""
    url = _build_url(params)
    redacted = _redact(url)
    t0 = time.monotonic()
    req = urllib.request.Request(
        url, headers={"User-Agent": "invest-briefing-claude-step10j-qa5-validation/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            raw_bytes = resp.read()
            body = raw_bytes.decode("utf-8", errors="replace")
        elapsed = time.monotonic() - t0
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            return {
                "ok": False,
                "status": status,
                "elapsed": elapsed,
                "response_bytes": len(raw_bytes),
                "body_json": None,
                "classification": "RESPONSE_PARSE_FAILED",
                "detail": str(e)[:300],
                "redacted_url": redacted,
            }
        return {
            "ok": True,
            "status": status,
            "elapsed": elapsed,
            "response_bytes": len(raw_bytes),
            "body_json": parsed,
            "classification": None,
            "detail": None,
            "redacted_url": redacted,
        }
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - t0
        try:
            raw_err_bytes = e.read()
            body = raw_err_bytes.decode("utf-8", errors="replace")
        except Exception:
            raw_err_bytes = b""
            body = ""
        status = e.code
        if status == 400:
            lowered = body.lower()
            if "api_key" in lowered or "api key" in lowered:
                classification = "AUTH_FAILED"
            elif "does not exist" in lowered:
                classification = "SERIES_NOT_FOUND"
            else:
                classification = "INVALID_PARAMETER"
        elif status == 429:
            classification = "RATE_LIMITED"
        else:
            classification = "HTTP_FAILURE"
        return {
            "ok": False,
            "status": status,
            "elapsed": elapsed,
            "response_bytes": len(raw_err_bytes),
            "body_json": None,
            "classification": classification,
            "detail": body[:300],
            "redacted_url": redacted,
        }
    except ssl.SSLError as e:
        return {
            "ok": False,
            "status": None,
            "elapsed": time.monotonic() - t0,
            "response_bytes": 0,
            "body_json": None,
            "classification": "TLS_FAILURE",
            "detail": str(e)[:300],
            "redacted_url": redacted,
        }
    except urllib.error.URLError as e:
        reason = e.reason
        classification = "DNS_FAILURE" if isinstance(reason, socket.gaierror) else "NETWORK_FAILURE"
        return {
            "ok": False,
            "status": None,
            "elapsed": time.monotonic() - t0,
            "response_bytes": 0,
            "body_json": None,
            "classification": classification,
            "detail": str(reason)[:300],
            "redacted_url": redacted,
        }
    except socket.timeout:
        return {
            "ok": False,
            "status": None,
            "elapsed": time.monotonic() - t0,
            "response_bytes": 0,
            "body_json": None,
            "classification": "NETWORK_FAILURE",
            "detail": "socket timeout",
            "redacted_url": redacted,
        }


def _shift_months(d: date, months: int) -> date:
    """Pure stdlib month arithmetic (no dateutil dependency, matching
    this repo's existing "stdlib only" convention for .github/scripts/*).
    Clamps the day-of-month to the target month's last valid day."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    # last valid day of (year, month)
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    last_day = (next_month_first - timedelta(days=1)).day
    day = min(d.day, last_day)
    return date(year, month, day)


def _has_missing_marker(observations: list[dict]) -> bool:
    return any(obs.get("value") == "." for obs in observations)


def _summarize_observations(observations: list[dict], limit_sample: int = 3) -> dict:
    if not observations:
        return {
            "observation_count": 0,
            "first_observation": None,
            "last_observation": None,
            "returned_order": "n/a (empty)",
            "has_missing_marker": False,
            "sample": [],
        }
    first = observations[0]
    last = observations[-1]
    dates = [o.get("date") for o in observations if o.get("date")]
    order = "n/a"
    if len(dates) >= 2:
        order = "ascending" if dates == sorted(dates) else (
            "descending" if dates == sorted(dates, reverse=True) else "mixed/unsorted"
        )
    return {
        "observation_count": len(observations),
        "first_observation": first,
        "last_observation": last,
        "returned_order": order,
        "has_missing_marker": _has_missing_marker(observations),
        "sample": observations[:limit_sample],
    }


# ============================================================
# SECTION 2/9: raw JSON shape + core semantics (CASE A: realtime only)
# ============================================================


def section_case_a(series_id: str, as_of: str) -> dict:
    """CASE A: output_type=1, realtime_start=realtime_end=as_of, no
    observation_start/end, no limit/sort_order. This is the minimal
    "snapshot" call -- establishes the baseline response shape before any
    additional parameter is layered on."""
    params = {
        "series_id": series_id,
        "output_type": 1,
        "realtime_start": as_of,
        "realtime_end": as_of,
    }
    result = _request(params)
    _log(f"  CASE A ({series_id}, as_of={as_of}): redacted_url={result['redacted_url']}")
    _log(f"    ok={result['ok']} status={result['status']} elapsed={result['elapsed']:.3f}s "
         f"response_bytes={result['response_bytes']}")
    if not result["ok"]:
        _log(f"    FAILED: classification={result['classification']} detail={result['detail']!r}")
        return {"request": result, "summary": None}
    observations = result["body_json"].get("observations")
    if observations is None:
        _log("    WARNING: response has no 'observations' key -- recording raw body keys instead")
        _log(f"    top-level keys: {sorted(result['body_json'].keys())}")
        return {"request": result, "summary": None, "raw_top_level_keys": sorted(result["body_json"].keys())}
    summary = _summarize_observations(observations)
    _log(f"    observation_count={summary['observation_count']} "
         f"returned_order={summary['returned_order']} "
         f"has_missing_marker={summary['has_missing_marker']}")
    _log(f"    first_observation={summary['first_observation']}")
    _log(f"    last_observation={summary['last_observation']}")
    for i, row in enumerate(summary["sample"]):
        _log(f"    sample[{i}]={row}")
    return {"request": result, "summary": summary, "raw_observations": observations}


# ============================================================
# SECTION 4: CASE B (realtime + observation_start/end) and
# CASE C (realtime + limit + sort_order=desc)
# ============================================================


def section_case_b(series_id: str, as_of: str, observation_start: str, observation_end: str) -> dict:
    params = {
        "series_id": series_id,
        "output_type": 1,
        "realtime_start": as_of,
        "realtime_end": as_of,
        "observation_start": observation_start,
        "observation_end": observation_end,
    }
    result = _request(params)
    _log(f"  CASE B ({series_id}, as_of={as_of}, obs_window=[{observation_start}, {observation_end}]): "
         f"redacted_url={result['redacted_url']}")
    _log(f"    ok={result['ok']} status={result['status']} elapsed={result['elapsed']:.3f}s "
         f"response_bytes={result['response_bytes']}")
    if not result["ok"]:
        _log(f"    FAILED: classification={result['classification']} detail={result['detail']!r}")
        return {"request": result, "summary": None}
    observations = result["body_json"].get("observations", [])
    summary = _summarize_observations(observations)
    _log(f"    observation_count={summary['observation_count']} "
         f"returned_order={summary['returned_order']} "
         f"has_missing_marker={summary['has_missing_marker']}")
    _log(f"    first_observation={summary['first_observation']}")
    _log(f"    last_observation={summary['last_observation']}")
    return {"request": result, "summary": summary, "raw_observations": observations}


def section_case_c(series_id: str, as_of: str, limit: int) -> dict:
    params = {
        "series_id": series_id,
        "output_type": 1,
        "realtime_start": as_of,
        "realtime_end": as_of,
        "limit": limit,
        "sort_order": "desc",
    }
    result = _request(params)
    _log(f"  CASE C ({series_id}, as_of={as_of}, limit={limit}, sort_order=desc): "
         f"redacted_url={result['redacted_url']}")
    _log(f"    ok={result['ok']} status={result['status']} elapsed={result['elapsed']:.3f}s "
         f"response_bytes={result['response_bytes']}")
    if not result["ok"]:
        _log(f"    FAILED: classification={result['classification']} detail={result['detail']!r}")
        return {"request": result, "summary": None}
    observations = result["body_json"].get("observations", [])
    summary = _summarize_observations(observations)
    _log(f"    observation_count={summary['observation_count']} "
         f"returned_order={summary['returned_order']} "
         f"has_missing_marker={summary['has_missing_marker']}")
    _log(f"    first_observation={summary['first_observation']}")
    _log(f"    last_observation={summary['last_observation']}")
    if summary["observation_count"] != limit:
        _log(f"    NOTE: requested limit={limit} but observation_count={summary['observation_count']} "
             f"-- recording actual behavior, not assuming limit means exactly N valid rows")
    return {"request": result, "summary": summary, "raw_observations": observations}


# ============================================================
# SECTION 7: PIT compatibility -- feed REAL output_type=1 rows into the
# UNCHANGED src/macro/pit.py and verify select_latest_vintage_as_of() /
# get_point_in_time_series() behave as claimed in
# claude/2026-10-05-step10j-qa4-design-d-pit-snapshot-review.md section E.
# ============================================================


def _to_schema_like_rows(series_id: str, raw_observations: list[dict]) -> list[dict]:
    """Maps a real output_type=1 observation {realtime_start, realtime_end,
    date, value} onto this project's raw-observation shape (series_id,
    observation_date, value, realtime_start, realtime_end, source,
    frequency, units, vintage_date). vintage_date is deliberately left
    None -- this is the exact mapping proposed (not yet implemented) in
    the QA-4 design review section A (verification D) / section I item 2.
    This function lives ONLY in this validation script -- it is not
    src/macro/fred_provider.py code and is not imported by anything under
    src/."""
    rows = []
    for obs in raw_observations:
        raw_value = obs.get("value")
        try:
            value = float(raw_value) if raw_value != "." else float("nan")
        except (TypeError, ValueError):
            value = float("nan")
        rows.append(
            {
                "series_id": series_id,
                "observation_date": obs.get("date"),
                "value": value,
                "realtime_start": obs.get("realtime_start"),
                "realtime_end": obs.get("realtime_end"),
                "source": "FRED",
                "frequency": None,  # not fetched in this validation script; irrelevant to PIT logic
                "units": None,
                "vintage_date": None,
            }
        )
    return rows


def section_pit_compatibility(series_id: str, raw_observations: list[dict], as_of: str) -> dict:
    """Section G of the final report. Verifies, against REAL data, that:
      1. each observation_date has exactly one candidate row (no vintage
         collisions within a single output_type=1 snapshot)
      2. pit._knowability_date()'s vintage_date-absent fallback to
         realtime_start is exercised (indirectly -- that function is
         private, so this checks the OBSERVABLE behavior of
         select_latest_vintage_as_of() instead, which is the public
         contract)
      3. select_latest_vintage_as_of() returns that one candidate,
         unchanged, when given only that one candidate
      4. get_point_in_time_series() returns one row per observation_date,
         sorted ascending, matching row count to input distinct dates
    pit.py itself is never modified -- only its public functions are
    called, exactly as fred_provider_live_smoke_test.py's Section G
    already does with output_type=2 data.
    """
    rows = _to_schema_like_rows(series_id, raw_observations)
    by_date: dict[str, list[dict]] = {}
    for row in rows:
        by_date.setdefault(row["observation_date"], []).append(row)

    multi_candidate_dates = {d: len(v) for d, v in by_date.items() if len(v) > 1}

    as_of_date = date.fromisoformat(as_of)
    select_results = []
    for obs_date, candidates in list(by_date.items())[:5]:
        selected = select_latest_vintage_as_of(candidates, as_of_date)
        select_results.append(
            {
                "observation_date": obs_date,
                "candidate_count": len(candidates),
                "selected_is_the_only_candidate": (
                    selected is candidates[0] if len(candidates) == 1 else None
                ),
                "selected_value": selected.get("value") if selected else None,
                "selected_knowability_source": (
                    "realtime_start" if selected and selected.get("vintage_date") is None else "vintage_date"
                )
                if selected
                else None,
            }
        )

    pit_series = get_point_in_time_series(rows, series_id, as_of)

    result = {
        "distinct_observation_dates": len(by_date),
        "multi_candidate_observation_dates": multi_candidate_dates,
        "multi_candidate_count": len(multi_candidate_dates),
        "sample_select_latest_vintage_as_of_results": select_results,
        "pit_series_row_count": len(pit_series),
        "pit_series_row_count_matches_distinct_dates": len(pit_series) == len(by_date),
    }
    _log(f"  {series_id}: distinct_observation_dates={result['distinct_observation_dates']} "
         f"multi_candidate_observation_dates_count={result['multi_candidate_count']}")
    _log(f"    pit_series_row_count={result['pit_series_row_count']} "
         f"matches_distinct_dates={result['pit_series_row_count_matches_distinct_dates']}")
    for sample in select_results:
        _log(f"    sample: {sample}")
    return result


# ============================================================
# SECTION 8: multiple AS_OF comparison (revision detection)
# ============================================================


def section_multiple_as_of(series_id: str, observation_date: str, as_of_dates: list[str]) -> dict:
    """For ONE observation_date, fetch output_type=1 snapshots at several
    as_of dates and compare the value returned for that exact
    observation_date across snapshots. Does NOT assume a revision exists
    -- records whatever the real API returns, including "no revision
    detected" as a valid, expected outcome for a series/date pair that
    simply was never revised."""
    _log(f"  {series_id} observation_date={observation_date} across as_of_dates={as_of_dates}")
    values_by_as_of = {}
    for as_of in as_of_dates:
        params = {
            "series_id": series_id,
            "output_type": 1,
            "realtime_start": as_of,
            "realtime_end": as_of,
            "observation_start": observation_date,
            "observation_end": observation_date,
        }
        result = _request(params)
        if not result["ok"]:
            _log(f"    as_of={as_of}: FAILED classification={result['classification']} "
                 f"detail={result['detail']!r}")
            values_by_as_of[as_of] = {"error": result["classification"]}
            continue
        observations = result["body_json"].get("observations", [])
        if not observations:
            _log(f"    as_of={as_of}: no observation returned for this observation_date "
                 f"(not yet knowable as of this as_of, or no data)")
            values_by_as_of[as_of] = {"value": None, "realtime_start": None, "realtime_end": None}
            continue
        row = observations[0]
        _log(f"    as_of={as_of}: value={row.get('value')!r} realtime_start={row.get('realtime_start')!r} "
             f"realtime_end={row.get('realtime_end')!r}")
        values_by_as_of[as_of] = {
            "value": row.get("value"),
            "realtime_start": row.get("realtime_start"),
            "realtime_end": row.get("realtime_end"),
        }

    distinct_values = {v.get("value") for v in values_by_as_of.values() if "value" in v and v.get("value") is not None}
    revision_detected = len(distinct_values) > 1
    _log(f"    revision_detected_across_these_as_of_dates={revision_detected} "
         f"(distinct_values_seen={distinct_values})")
    return {
        "observation_date": observation_date,
        "values_by_as_of": values_by_as_of,
        "revision_detected": revision_detected,
    }


def main() -> int:
    today = date.today()
    as_of_today = today.isoformat()

    # Past AS_OF dates: 1 year back and 3 years back from "today" at
    # runtime -- computed dynamically (never hardcoded) so this script
    # stays valid regardless of when it is actually run.
    as_of_past_1 = _shift_months(today, -12).isoformat()
    as_of_past_2 = _shift_months(today, -36).isoformat()

    # Revision-probe target: an observation_date far enough in the past
    # that PAYEMS would have gone through its normal preliminary ->
    # second -> final revision cycle (and possibly an annual benchmark
    # revision) by "today", but recent enough to be well within FRED's
    # retained vintage history. ~14 months back from today, clamped to
    # the 1st of that month (PAYEMS observation_dates are always the
    # first of the month).
    revision_probe_month = _shift_months(today, -14)
    revision_probe_observation_date = date(revision_probe_month.year, revision_probe_month.month, 1).isoformat()
    revision_probe_as_of_dates = [
        _shift_months(revision_probe_month, 1).isoformat(),  # ~1 month after period end (initial release)
        _shift_months(revision_probe_month, 7).isoformat(),  # ~7 months after period end (post-revisions)
        as_of_today,  # current (latest known)
    ]

    _print_header("CONFIG")
    _log(f"  as_of_today={as_of_today}")
    _log(f"  as_of_past_1={as_of_past_1} (~1 year back)")
    _log(f"  as_of_past_2={as_of_past_2} (~3 years back)")
    _log(f"  revision_probe_observation_date={revision_probe_observation_date}")
    _log(f"  revision_probe_as_of_dates={revision_probe_as_of_dates}")

    RESULTS["config"] = {
        "as_of_today": as_of_today,
        "as_of_past_1": as_of_past_1,
        "as_of_past_2": as_of_past_2,
        "revision_probe_observation_date": revision_probe_observation_date,
        "revision_probe_as_of_dates": revision_probe_as_of_dates,
    }

    # ---- SECTION 2/9: CASE A for all 4 series at as_of_today, plus 2
    # past as_of dates (section 3's "at least 2" past-AS_OF requirement) ----
    _print_header("SECTION CASE-A -- realtime snapshot only (current + 2 past AS_OF)")
    case_a_results = {}
    for series_id in SERIES_TO_CHECK:
        case_a_results[series_id] = {}
        for label, as_of in [("today", as_of_today), ("past_1", as_of_past_1), ("past_2", as_of_past_2)]:
            _log(f"-- {series_id} / {label} --")
            case_a_results[series_id][label] = section_case_a(series_id, as_of)
    RESULTS["case_a"] = case_a_results

    # ---- SECTION 4: CASE B vs CASE C, one comparison per series at
    # as_of_today ----
    _print_header("SECTION CASE-B-vs-CASE-C -- observation window vs limit+sort_order")
    case_bc_results = {}
    for series_id in SERIES_TO_CHECK:
        if series_id in DAILY_SERIES:
            observation_start = _shift_months(today, 0).isoformat()
            observation_start = (today - timedelta(days=45)).isoformat()
            observation_end = as_of_today
            limit = 30
        else:
            observation_start = _shift_months(today, -21).isoformat()
            observation_end = as_of_today
            limit = 21
        _log(f"-- {series_id} (frequency class: {'daily' if series_id in DAILY_SERIES else 'monthly'}) --")
        case_b = section_case_b(series_id, as_of_today, observation_start, observation_end)
        case_c = section_case_c(series_id, as_of_today, limit)
        case_bc_results[series_id] = {"case_b": case_b, "case_c": case_c, "limit_requested": limit}
    RESULTS["case_b_vs_c"] = case_bc_results

    # ---- SECTION 7: PIT compatibility, using CASE A / today's snapshot
    # for each series (broadest sample available without re-fetching) ----
    _print_header("SECTION PIT-COMPATIBILITY -- real output_type=1 rows through UNCHANGED pit.py")
    pit_results = {}
    for series_id in SERIES_TO_CHECK:
        raw_observations = case_a_results[series_id]["today"].get("raw_observations")
        if not raw_observations:
            _log(f"  {series_id}: no raw_observations available from CASE A today -- skipping PIT check")
            pit_results[series_id] = "no_data"
            continue
        pit_results[series_id] = section_pit_compatibility(series_id, raw_observations, as_of_today)
    RESULTS["pit_compatibility"] = pit_results

    # ---- SECTION 8: multiple AS_OF revision check (PAYEMS primary
    # target -- known to be revised; still recorded honestly if not) ----
    _print_header("SECTION MULTIPLE-AS-OF -- revision detection (PAYEMS)")
    RESULTS["multiple_as_of_payems"] = section_multiple_as_of(
        "PAYEMS", revision_probe_observation_date, revision_probe_as_of_dates
    )

    # Also run the same multi-as_of probe for CPIAUCSL (also routinely
    # revised) as a second data point, same observation_date/as_of set.
    _print_header("SECTION MULTIPLE-AS-OF -- revision detection (CPIAUCSL, second data point)")
    RESULTS["multiple_as_of_cpiaucsl"] = section_multiple_as_of(
        "CPIAUCSL", revision_probe_observation_date, revision_probe_as_of_dates
    )

    # ---- SECTION 10: data volume (actual, this run) ----
    _print_header("SECTION DATA-VOLUME -- actual output_type=1 bytes/rows this run (CASE B)")
    for series_id in SERIES_TO_CHECK:
        cb = case_bc_results[series_id]["case_b"]
        req = cb["request"]
        summary = cb["summary"]
        if summary is None:
            _log(f"  {series_id}: CASE B failed, no volume data")
            continue
        _log(f"  {series_id}: response_bytes={req['response_bytes']} "
             f"observation_count={summary['observation_count']} elapsed={req['elapsed']:.3f}s")

    # ---- FINAL SUMMARY (sanitized) ----
    with_errors = any(
        case_a_results[s][label]["request"].get("ok") is False
        for s in SERIES_TO_CHECK
        for label in ("today", "past_1", "past_2")
    )
    RESULTS["any_case_a_request_failed"] = with_errors

    _print_header("SUMMARY (sanitized -- no API key, no unredacted URL, anywhere above)")
    _log(json.dumps(RESULTS, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
