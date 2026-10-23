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

Step 10J-QA-7 additions (this round, validation-infrastructure-only --
src/macro/*.py and all SEC production code remain untouched):
  - section_b2_monthly_precision(): isolates FRED's own server-side
    observation_start filtering behavior for a monthly series across
    day-01/day-15/day-28 of the same month, and separately shows what
    src/macro/fred_provider.py's own get_series() client-side exact-date
    post-filter (reproduced here read-only, as a 2-line comparison --
    not imported, not modified) would keep from each raw response.
  - section_i_vintage_dates_cross_check(): independent cross-check of
    (output_type=1, realtime_start=realtime_end=as_of) against
    (output_type=1, vintage_dates=as_of) over the same observation
    window, compared pair-by-pair on (observation_date, value) -- not
    just counts.
  - _redact_text(): new, applied to every `detail` field _request() now
    populates from external text (HTTP error bodies, exception
    messages). QA-6's adversarial error-path testing found that FRED's
    HTTPError response body is not guaranteed to omit the submitted
    api_key value (the documented "not registered" message does not
    echo it, but this is not guaranteed for every FRED error variant);
    _request()'s prior behavior logged that body verbatim via `detail`
    without ever redacting it. This masks every literal occurrence of
    the real key value in any such text before it is stored or printed.

Round QA-7-CLOSE-2 addition (this round, validation-infrastructure-only
-- src/macro/*.py remains untouched, imported read-only exactly as
fred_provider_live_smoke_test.py's Section G already does):
  - section_m_production_output_type2_pit_selector_probe(): unlike every
    section above (which makes this script's OWN raw urllib calls for
    output_type=1, by design -- see PURPOSE above), this section calls
    the REAL, unmodified src.macro.fred_provider.FredMacroProvider.
    get_series() for a WIDE_WINDOW_SERIES_IDS member -- i.e. the actual
    production output_type=2 fetch + crosstab-parsing path, not a
    reimplementation of it. The real parsed records are then handed to
    the real, unmodified src.macro.pit.select_latest_vintage_as_of() /
    get_point_in_time_series(). This closes the one remaining gap QA-7-
    CLOSE-1's read-only code audit identified: every prior PIT-selector
    test exercised that logic only against a synthetic (if
    real-response-shaped) fixture; this section exercises it against
    whatever FRED actually returns live, this run, with no success
    guaranteed -- it fails closed to "INCONCLUSIVE" (never fabricates a
    PASS) when the live response does not happen to contain a usable
    multi-vintage observation_date, when FRED_API_KEY is unavailable in
    the running environment, or on any other error. The network-free
    evidence-building logic (_build_production_pit_evidence()) is
    covered by offline regression tests against synthetic,
    production-shaped fixtures in
    tests/test_fred_output_type1_validation_production_probe.py -- those
    tests are fixture-based regression evidence for the evidence-
    builder's own correctness, never a substitute for, or claim of,
    live FRED evidence.
"""

from __future__ import annotations

import json
import math
import os
import re
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

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

# Round QA-7-CLOSE-2: read-only import of the REAL production provider,
# to exercise its actual output_type=2 fetch + crosstab-parsing path
# (see section_m_production_output_type2_pit_selector_probe() below).
# Mirrors fred_provider_live_smoke_test.py's existing identical import --
# this is not a new pattern for this project, just new to this script.
from src.macro.fred_provider import (  # noqa: E402
    WIDE_WINDOW_SERIES_IDS,
    FredAPIKeyMissing,
    FredHTTPError,
    FredMacroProvider,
    FredProviderError,
)

FRED_OBSERVATIONS_BASE = "https://api.stlouisfed.org/fred/series/observations"

SERIES_TO_CHECK = ["CPIAUCSL", "PAYEMS", "T10Y2Y", "VIXCLS"]
DAILY_SERIES = {"T10Y2Y", "VIXCLS"}
MONTHLY_SERIES = {"CPIAUCSL", "PAYEMS"}

# Round QA-7-CLOSE-2 Part C: compact evidence artifact filename, written
# alongside this script's own directory when the live probe runs under
# the GitHub Actions workflow (see the "Upload QA-7-CLOSE-2 evidence
# artifact" step in fred-output-type1-validation.yml).
PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH = "fred_qa7_close2_production_pit_evidence.json"

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


def _redact_text(text: str | None) -> str | None:
    """Step 10J-QA-7 section 5: mask every literal occurrence of the real
    FRED_API_KEY value inside arbitrary text (HTTP error response
    bodies, exception messages) before that text is ever stored in
    `_request()`'s returned `detail` field or printed. This is distinct
    from _redact(), which only targets the api_key= query-string
    parameter of a URL this script itself built -- _redact_text() covers
    text this script did NOT construct (e.g. FRED's own error response
    body, or a raw exception message), which can in principle contain
    the key in any position/format. Never raises -- reads the key
    directly from the environment (not via _get_api_key(), which exits
    the whole process on a missing key; a redaction helper used inside
    exception handlers must never itself be a new failure mode)."""
    if text is None:
        return None
    key = os.environ.get("FRED_API_KEY")
    if key:
        text = text.replace(key, "REDACTED")
    return text


def _safe_redact_text(text: str | None) -> str:
    """Round QA-7-CLOSE-2-R5, Part A. Defensive wrapper around
    _redact_text() for use in _write_evidence_artifact_atomically()'s
    error-reporting path: _redact_text() is already documented to
    "never raise", but this wrapper exists so that even an unforeseen
    failure INSIDE redaction itself can never propagate out of the
    artifact writer, and -- critically -- never falls back to
    returning the raw, unredacted text. On any failure here, the
    fallback is a fixed, generic placeholder that carries no exception
    content at all, never the original string."""
    try:
        redacted = _redact_text(text)
    except Exception:  # noqa: BLE001 -- redaction itself must never leak the raw text
        return "<error detail withheld: redaction failed>"
    return redacted if redacted is not None else "<error detail unavailable>"


def _redact_exception_text(exc: BaseException) -> str:
    """Step 10J-QA-8: build a safe, fully-redacted text summary of an
    arbitrary exception -- including its own message AND any chained
    cause/context exception (`raise X from Y`, or an exception raised
    while handling another one), since a nested exception's own message
    can independently contain the real secret even when the outer
    exception's message does not. Every text fragment collected here is
    redacted individually before being joined, so a secret split across
    an exception and its __cause__/__context__ cannot survive by being
    assembled only after redaction. Never raises, never logs/prints
    anything itself, and never returns the real FRED_API_KEY value."""
    parts: list[str] = []

    def collect(e: BaseException | None, seen: set[int]) -> None:
        if e is None or id(e) in seen:
            return
        seen.add(id(e))
        safe = _redact_text(f"{type(e).__name__}: {e}")
        if safe:
            parts.append(safe)
        # Nested/chained exceptions (explicit "raise ... from ..." cause,
        # or the implicit context captured during exception handling)
        # are walked too -- a secret can live in either.
        collect(getattr(e, "__cause__", None), seen)
        collect(getattr(e, "__context__", None), seen)

    collect(exc, set())
    return " | ".join(parts) if parts else f"{type(exc).__name__} (no message)"


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
                "detail": _redact_text(str(e))[:300],
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
        # Step 10J-QA-7 section 5: redact the FULL body text (the real
        # key value, wherever it appears) BEFORE truncating to 300 chars
        # -- truncating first could cut a key value in half and leave
        # the surviving fragment unredacted. FRED's own documented
        # "not registered" error message does not echo the submitted
        # api_key, but this is not a guarantee covering every FRED error
        # response variant, so this body is never trusted to be clean.
        safe_detail = _redact_text(body)[:300]
        return {
            "ok": False,
            "status": status,
            "elapsed": elapsed,
            "response_bytes": len(raw_err_bytes),
            "body_json": None,
            "classification": classification,
            "detail": safe_detail,
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
            "detail": _redact_text(str(e))[:300],
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
            "detail": _redact_text(str(reason))[:300],
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
    except Exception as e:
        # Step 10J-QA-8: catch-all for any exception type NOT explicitly
        # handled above (e.g. RuntimeError, ValueError, or any other
        # exception the urllib/ssl/json stack does not document but
        # could in principle raise). Before this round, such an
        # exception was not caught here at all -- it propagated
        # uncaught up through main(), and Python's own default
        # unhandled-exception traceback (printed to stderr, landing in
        # the public GitHub Actions log) would have printed the
        # exception's raw __str__ with no redaction whatsoever. This
        # branch closes that gap: _redact_exception_text() redacts the
        # exception's own message AND every chained cause/context
        # exception's message (never the raw exception object, never
        # its original traceback) before anything is returned. This is
        # the LAST except clause -- it must never be moved above a more
        # specific handler, or it would silently swallow and
        # misclassify failures that already have correct, tested
        # handling above.
        return {
            "ok": False,
            "status": None,
            "elapsed": time.monotonic() - t0,
            "response_bytes": 0,
            "body_json": None,
            "classification": "UNEXPECTED_ERROR",
            "detail": _redact_exception_text(e)[:300],
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


# ============================================================
# Round QA-7-CLOSE-3: FRED real-time date-boundary diagnosis and
# recovery. Diagnosed via a real GitHub Actions run on 2026-10-23
# (job started 2026-10-23T04:49:38Z, i.e. runner-local `date.today()`
# == 2026-10-23): every CASE A / SECTION B-2 / SECTION I request using
# realtime_start=realtime_end=as_of_today (or vintage_dates=as_of_today)
# failed with a 400 INVALID_PARAMETER response whose body was exactly
#   {"error_code":400,"error_message":"Bad Request.  Variable
#   realtime_start can not be after today's date (2026-10-22) unless
#   it's equal to the real-time max date 9999-12-31."}
# (Section I's vintage_dates=as_of_today path separately failed with a
# 500 HTTP_FAILURE citing the same 2026-10-22 upper bound: "The vintage
# date ... cannot be outside the range 1776-07-04 to 2026-10-22.") This
# was reproducible and stable across the whole ~24-second run (04:49:38
# to 04:50:02 UTC) -- every "today" request failed with this exact
# message and date, every "past_1"/"past_2" request (one calendar year
# and three calendar years back) succeeded with status 200. No
# AUTH_FAILED, RATE_LIMITED, or other classification was observed. This
# is consistent with FRED computing its own real-time max date in a
# timezone behind UTC (the St. Louis Fed is US Central time; at
# 2026-10-23T04:49 UTC it was still 2026-10-22 in Central time), while
# this script's `date.today()` (SECTION 10) uses the runner's (UTC)
# calendar date -- NOT a bug in this script's date arithmetic, and NOT
# an auth/network/rate-limit problem; a one-calendar-day timezone
# mismatch between this script's notion of "today" and FRED's real-time
# max date, observed only close to UTC midnight.
#
# The recovery below is validation-infrastructure-only: it does not
# touch src/macro/fred_provider.py, src/macro/pit.py, or any other
# production module, and it does not change observation_start/
# observation_end semantics (a different FRED parameter, never treated
# as the same variable as the realtime as_of value below). It performs
# ONE lightweight probe request up front; if FRED accepts the
# originally requested as_of date, nothing changes (requirement 1). If
# FRED rejects it with exactly the recognized "today's date (D)"
# INVALID_PARAMETER pattern, and D passes strict validation, D becomes
# the single `effective_realtime_as_of` reused by CASE A ("today"),
# SECTION B-2, and SECTION I for the rest of this run (requirement 5);
# otherwise this fails closed to the original, unmodified as_of value
# (requirement 4) and every downstream section behaves exactly as it
# did before this round -- including still failing, visibly, if the
# probe's own failure cannot be safely classified as this specific
# date-boundary condition.
# ============================================================

_REALTIME_DATE_BOUNDARY_RE = re.compile(
    r"today.s date \((\d{4}-\d{2}-\d{2})\) unless it.s equal to the real-time max date"
)


def _validate_realtime_boundary_date(candidate_str: str, requested_as_of: str) -> date | None:
    """Strict validation of a server-reported real-time boundary date,
    confined to this validation script -- never imported from or fed
    into any production module. Returns None (fail-closed) unless ALL
    of the following hold:
      1. `candidate_str` is the exact canonical "YYYY-MM-DD" form
         (reuses `_parse_iso_date_for_evidence`'s strict round-trip
         check -- the same parser already used for observation_date/
         vintage_date elsewhere in this file, not a new, looser one).
      2. `requested_as_of` is itself a valid canonical date (defensive;
         it is always `date.today().isoformat()` or a value derived
         from it in this script, but this function makes no assumption
         about its caller).
      3. The candidate is not AFTER the requested date -- a server-
         reported "maximum allowed" date that is somehow later than
         what was actually requested is nonsensical for this specific
         error pattern and is never accepted.
      4. The candidate is within 3 calendar days BEFORE the requested
         date -- this diagnosis identified a ONE-day UTC/Central-time
         skew as the cause; a larger gap is outside what this specific,
         diagnosed condition explains, so it is rejected rather than
         guessed at. This bound is deliberately tight and never
         widened to "whatever FRED says" without renewed evidence.
    """
    candidate = _parse_iso_date_for_evidence(candidate_str)
    if candidate is None:
        return None
    requested = _parse_iso_date_for_evidence(requested_as_of)
    if requested is None:
        return None
    if candidate > requested:
        return None
    if (requested - candidate).days > 3:
        return None
    return candidate


def _determine_effective_realtime_as_of(requested_as_of: str, probe_series_id: str) -> dict:
    """Round QA-7-CLOSE-3. Issues exactly one real, lightweight FRED
    request (realtime_start=realtime_end=`requested_as_of`, no
    observation_start/observation_end -- deliberately NOT the same
    request CASE A makes for `probe_series_id`, so this function's own
    probe never silently substitutes for, or is mistaken for, that
    section's own recorded result) to determine whether FRED currently
    accepts `requested_as_of` as a real-time as_of value.

    Returns a dict always containing both `requested_realtime_as_of`
    and `effective_realtime_as_of` as SEPARATE fields (never merged
    into one), plus `adjusted` (bool) and a human-readable `reason`
    string recorded unconditionally -- including when no adjustment is
    made, so the reason nothing changed is as visible as the reason
    something did. The api_key is never present in any returned field;
    `probe` only carries the same `classification`/`status` shape
    `_request()` already returns elsewhere in this file, and any detail
    text has already passed through `_redact_text()` inside `_request()`
    itself.
    """
    probe = _request({
        "series_id": probe_series_id,
        "output_type": 1,
        "realtime_start": requested_as_of,
        "realtime_end": requested_as_of,
    })
    result = {
        "requested_realtime_as_of": requested_as_of,
        "effective_realtime_as_of": requested_as_of,
        "adjusted": False,
        "reason": None,
        "probe_series_id": probe_series_id,
        "probe_status": probe["status"],
        "probe_classification": probe["classification"],
    }
    if probe["ok"]:
        result["reason"] = (
            "FRED accepted the requested realtime_start/realtime_end as-is; "
            "no adjustment made."
        )
        return result
    if probe["classification"] != "INVALID_PARAMETER":
        result["reason"] = (
            "Probe request failed, but not with the recognized date-boundary "
            f"INVALID_PARAMETER pattern (classification={probe['classification']!r}, "
            f"status={probe['status']!r}); not treated as a date-boundary issue, "
            "no adjustment made."
        )
        return result
    detail = probe.get("detail") or ""
    match = _REALTIME_DATE_BOUNDARY_RE.search(detail)
    if not match:
        result["reason"] = (
            "Request failed with classification=INVALID_PARAMETER, but the "
            "error text did not match the recognized \"today's date (D)\" "
            "real-time boundary pattern; no adjustment made."
        )
        return result
    candidate_str = match.group(1)
    candidate = _validate_realtime_boundary_date(candidate_str, requested_as_of)
    if candidate is None:
        result["reason"] = (
            f"Server-reported boundary date {candidate_str!r} failed strict "
            "validation (unparseable, not before the requested date, or more "
            "than 3 days earlier); fail-closed, no adjustment made."
        )
        return result
    result["effective_realtime_as_of"] = candidate.isoformat()
    result["adjusted"] = True
    result["reason"] = (
        f"FRED rejected realtime_start=realtime_end={requested_as_of} with the "
        f"recognized real-time date-boundary error; server-reported boundary "
        f"date {candidate.isoformat()} passed validation and is used instead "
        "for CASE A (today), SECTION B-2, and SECTION I in this run. This is "
        "reported for this run's adjusted date only -- it is not a claim that "
        f"PIT correctness was verified for {requested_as_of}."
    )
    return result


def _run_case_a_with_effective_as_of(
    series_to_check,
    effective_realtime_as_of_today: str,
    as_of_past_1: str,
    as_of_past_2: str,
) -> dict:
    """Round QA-7-CLOSE-4 orchestration seam, extracted from `main()`'s
    CASE A block with NO behavior change and NO new call site: the exact
    same section_case_a() calls, same arguments, same order, same _log
    lines `main()` made inline before this round -- only the "today"
    entry uses `effective_realtime_as_of_today` (an ALREADY-COMPUTED
    value this function does not derive and makes no FRED probe request
    to obtain); "past_1"/"past_2" always use the untouched past as_of
    values passed in. Exists to give the wiring between
    `_determine_effective_realtime_as_of()`'s output and this APPROVED
    call site a stable, directly-testable seam -- addressing the
    QA-7-CLOSE-3 external-review finding that no test previously
    exercised this wiring itself."""
    case_a_results: dict = {}
    for series_id in series_to_check:
        case_a_results[series_id] = {}
        for label, as_of in [
            ("today", effective_realtime_as_of_today),
            ("past_1", as_of_past_1),
            ("past_2", as_of_past_2),
        ]:
            _log(f"-- {series_id} / {label} --")
            case_a_results[series_id][label] = section_case_a(series_id, as_of)
    return case_a_results


def _run_section_b2_with_effective_as_of(
    monthly_series,
    effective_realtime_as_of_today: str,
    b2_test_month: date,
) -> dict:
    """Round QA-7-CLOSE-4 orchestration seam, extracted from `main()`'s
    SECTION B-2 block with NO behavior change and NO new call site --
    see `_run_case_a_with_effective_as_of()`'s docstring for the shared
    rationale. `b2_test_month` (and the observation_start/observation_end
    window it derives inside section_b2_monthly_precision()) is a
    different, untouched variable, never conflated with the realtime
    as_of value here."""
    b2_results: dict = {}
    for series_id in monthly_series:
        _log(f"-- {series_id} (test_month={b2_test_month.year:04d}-{b2_test_month.month:02d}) --")
        b2_results[series_id] = section_b2_monthly_precision(
            series_id, effective_realtime_as_of_today, b2_test_month
        )
    return b2_results


def _run_section_i_with_effective_as_of(
    i_series,
    effective_realtime_as_of_today: str,
    i_window_start: str,
    i_window_end: str,
) -> dict:
    """Round QA-7-CLOSE-4 orchestration seam, extracted from `main()`'s
    SECTION I block with NO behavior change and NO new call site -- see
    `_run_case_a_with_effective_as_of()`'s docstring for the shared
    rationale. `i_window_start`/`i_window_end` are the untouched
    observation window (derived from `as_of_today`, never from the
    effective realtime as_of value); section_i_vintage_dates_cross_check()
    itself already applies the SAME `effective_realtime_as_of_today`
    argument to both its Path A (realtime_start/realtime_end) and Path B
    (vintage_dates) requests -- unchanged by this refactor."""
    i_results: dict = {}
    for series_id in i_series:
        _log(f"-- {series_id} (as_of={effective_realtime_as_of_today}, "
             f"window=[{i_window_start}, {i_window_end}]) --")
        i_results[series_id] = section_i_vintage_dates_cross_check(
            series_id, effective_realtime_as_of_today, i_window_start, i_window_end
        )
    return i_results


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


# ============================================================
# SECTION B-2 (Step 10J-QA-7 section 3): FRED server-side observation_start
# filtering behavior for a MONTHLY series, isolated across day-01/15/28 of
# the SAME calendar month, plus a read-only reproduction of
# src/macro/fred_provider.py's own get_series() client-side exact-date
# post-filter to show what production would actually keep.
# ============================================================


def section_b2_monthly_precision(series_id: str, as_of: str, test_month: date) -> dict:
    """Does NOT treat 'FRED represents monthly observation_date as the
    1st of the month' (QA-6 section B-1) and 'observation_start filtering
    semantics' (this section) as the same concept -- this section probes
    filtering behavior only. Three observation_start values (day 01, 15,
    28 of test_month), all paired with the SAME observation_end
    (test_month's last day), isolate exactly how far into the month the
    start date can move before FRED's server-side filter stops returning
    that month's single observation_date. For each probe, also computes
    what src/macro/fred_provider.py's get_series() (lines ~865-870: an
    unconditional client-side `observation_date >= start_date` /
    `<= end_date` string comparison on the RETURNED records, applied
    identically regardless of which fetch strategy produced them) would
    keep -- that exact 2-line comparison is reproduced here, read-only,
    as this validation script's own local variable; src/macro/fred_provider.py
    itself is never imported or modified by this section."""
    month_start = date(test_month.year, test_month.month, 1)
    next_month = _shift_months(month_start, 1)
    month_end = next_month - timedelta(days=1)
    day28 = date(test_month.year, test_month.month, min(28, month_end.day))

    probes = [
        ("day_01", month_start.isoformat()),
        ("day_15", date(test_month.year, test_month.month, 15).isoformat()),
        ("day_28", day28.isoformat()),
    ]

    results: dict = {}
    for label, observation_start in probes:
        observation_end = month_end.isoformat()
        params = {
            "series_id": series_id,
            "output_type": 1,
            "realtime_start": as_of,
            "realtime_end": as_of,
            "observation_start": observation_start,
            "observation_end": observation_end,
        }
        result = _request(params)
        _log(f"  B-2 ({series_id}, {label}, observation_start={observation_start}, "
             f"observation_end={observation_end}): redacted_url={result['redacted_url']}")
        _log(f"    ok={result['ok']} status={result['status']}")
        if not result["ok"]:
            _log(f"    FAILED: classification={result['classification']} detail={result['detail']!r}")
            results[label] = {
                "observation_start_requested": observation_start,
                "observation_end_requested": observation_end,
                "request": result,
            }
            continue
        observations = result["body_json"].get("observations", [])
        before = _summarize_observations(observations)
        # Read-only reproduction of get_series()'s own client-side exact-date
        # post-filter (fred_provider.py lines ~865-870), applied to THIS
        # response only -- not a call into fred_provider.py.
        after_rows = [
            o for o in observations
            if o.get("date", "") >= observation_start and o.get("date", "") <= observation_end
        ]
        after = _summarize_observations(after_rows)
        _log(f"    BEFORE client-side filter: count={before['observation_count']} "
             f"first={before['first_observation']} last={before['last_observation']}")
        _log(f"    AFTER  client-side filter: count={after['observation_count']} "
             f"first={after['first_observation']} last={after['last_observation']}")
        results[label] = {
            "observation_start_requested": observation_start,
            "observation_end_requested": observation_end,
            "before_client_side_filter": before,
            "after_client_side_filter": after,
        }
    return results


# ============================================================
# SECTION I (Step 10J-QA-7 section 4): independent vintage_dates
# cross-check -- (output_type=1, realtime_start=realtime_end=as_of) vs
# (output_type=1, vintage_dates=as_of), same observation window, compared
# pair-by-pair on (observation_date, value), not just counts.
# ============================================================


def section_i_vintage_dates_cross_check(
    series_id: str, as_of: str, observation_start: str, observation_end: str
) -> dict:
    path_a_params = {
        "series_id": series_id,
        "output_type": 1,
        "realtime_start": as_of,
        "realtime_end": as_of,
        "observation_start": observation_start,
        "observation_end": observation_end,
    }
    path_a = _request(path_a_params)
    _log(f"  I Path A ({series_id}, realtime_start=realtime_end={as_of}): "
         f"redacted_url={path_a['redacted_url']}")
    _log(f"    ok={path_a['ok']} status={path_a['status']}")
    if not path_a["ok"]:
        _log(f"    FAILED: classification={path_a['classification']} detail={path_a['detail']!r}")

    path_b_params = {
        "series_id": series_id,
        "output_type": 1,
        "vintage_dates": as_of,
        "observation_start": observation_start,
        "observation_end": observation_end,
    }
    path_b = _request(path_b_params)
    _log(f"  I Path B ({series_id}, vintage_dates={as_of}): redacted_url={path_b['redacted_url']}")
    _log(f"    ok={path_b['ok']} status={path_b['status']}")
    if not path_b["ok"]:
        _log(f"    FAILED: classification={path_b['classification']} detail={path_b['detail']!r}")

    if not path_a["ok"] or not path_b["ok"]:
        return {
            "path_a_ok": path_a["ok"],
            "path_a_classification": path_a["classification"],
            "path_b_ok": path_b["ok"],
            "path_b_classification": path_b["classification"],
            "comparison": "INCONCLUSIVE -- at least one path failed; see classification above. "
                          "Not treated as a PASS or FAIL of the cross-check itself.",
        }

    obs_a = path_a["body_json"].get("observations", [])
    obs_b = path_b["body_json"].get("observations", [])
    pairs_a = {o.get("date"): o.get("value") for o in obs_a}
    pairs_b = {o.get("date"): o.get("value") for o in obs_b}

    only_in_a = {d: v for d, v in pairs_a.items() if d not in pairs_b}
    only_in_b = {d: v for d, v in pairs_b.items() if d not in pairs_a}
    common_dates = sorted(set(pairs_a) & set(pairs_b))
    value_mismatches = {d: [pairs_a[d], pairs_b[d]] for d in common_dates if pairs_a[d] != pairs_b[d]}

    identical = not only_in_a and not only_in_b and not value_mismatches
    _log(f"    path_a_count={len(pairs_a)} path_b_count={len(pairs_b)} "
         f"common={len(common_dates)} only_in_a={len(only_in_a)} only_in_b={len(only_in_b)} "
         f"value_mismatches={len(value_mismatches)} identical={identical}")
    if only_in_a:
        _log(f"    only_in_a_sample={dict(list(only_in_a.items())[:5])}")
    if only_in_b:
        _log(f"    only_in_b_sample={dict(list(only_in_b.items())[:5])}")
    if value_mismatches:
        _log(f"    value_mismatch_sample={dict(list(value_mismatches.items())[:5])}")

    return {
        "path_a_count": len(pairs_a),
        "path_b_count": len(pairs_b),
        "common_date_count": len(common_dates),
        "only_in_a_count": len(only_in_a),
        "only_in_b_count": len(only_in_b),
        "only_in_a_sample": dict(list(only_in_a.items())[:5]),
        "only_in_b_sample": dict(list(only_in_b.items())[:5]),
        "value_mismatch_count": len(value_mismatches),
        "value_mismatch_sample": dict(list(value_mismatches.items())[:5]),
        "identical": identical,
        "comparison": "IDENTICAL" if identical else "DIFFERENCES_FOUND -- see counts/samples above",
    }


# ============================================================
# SECTION M (Round QA-7-CLOSE-2): live production output_type=2 PIT
# selector validation. See the module docstring's "Round QA-7-CLOSE-2
# addition" entry for the full rationale.
# ============================================================

def _parse_iso_date_for_evidence(value: Any) -> date | None:
    """
    Round QA-7-CLOSE-2-R1, Part B (tightened in Round QA-7-CLOSE-2-R2,
    Part C). Validation-infrastructure-only ISO date parser
    (deliberately NOT imported from any production module --
    src/macro/*.py remains untouched). Returns None for anything that is
    not the exact documented "YYYY-MM-DD" canonical representation,
    including None, non-str types, and malformed strings. Mirrors the
    same fail-closed principle already established elsewhere in this
    project (e.g. derived_metrics.py's _parse_period_end()): a non-None,
    non-parseable value must never be lexically compared against a real
    date or silently treated as sortable -- it is excluded from grouping
    and chronological comparison entirely.

    Round QA-7-CLOSE-2-R2, Part C: `date.fromisoformat()` on Python
    3.11+ also accepts other ISO 8601 representations this project does
    NOT document as the wire format -- e.g. the no-separator basic
    format "YYYYMMDD" and ISO week-date form "YYYY-Www-D" -- both of
    which `date.fromisoformat()` happily parses to a real date. Silently
    accepting either would mean two different observation_date/
    vintage_date *strings* could parse to the identical calendar date,
    which must never be decided implicitly. The round-trip check below
    (`parsed.isoformat() == value`) rejects both: for "20240101" and
    "2024-W01-1", `parsed.isoformat()` always re-renders as "2024-01-01"
    (or whatever canonical date it parsed to), which never equals the
    original non-canonical input string, so both are excluded exactly
    like any other malformed value -- never a false acceptance, never an
    uncaught exception.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return parsed


def _json_sanitize(obj: Any) -> Any:
    """
    Round QA-7-CLOSE-2-R2, Part B. Recursively replace any non-finite
    float (NaN, +Infinity, -Infinity) with JSON "null" so the written
    evidence artifact is always standards-compliant JSON. Python's
    json.dump()/json.dumps() accept NaN/Infinity/-Infinity by default
    (emitting the literal, non-standard tokens "NaN"/"Infinity"/
    "-Infinity"), which a strict JSON parser rejects. A "null" produced
    here means "the production-parsed value was missing/non-finite",
    exactly FRED's and this project's existing missing-value
    convention (see _is_nan_value's docstring) -- it is never a
    fabricated numeric value.

    Preserves ordinary strings, finite numbers, booleans, None, dicts,
    and lists/tuples (tuples become lists, matching what json.dump()
    itself would already do) via recursion. Any other object type is
    returned unchanged and left to json.dumps(..., default=str)'s own
    fallback -- this function's only job is the NaN/Infinity case.
    """
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_sanitize(v) for v in obj]
    return obj


def _write_evidence_artifact_atomically(
    destination_path: str, payload: dict
) -> tuple[bool, str | None]:
    """
    Round QA-7-CLOSE-2-R3, Part A. Standard-library-only, atomic
    evidence-artifact write helper, factored out of main() so it can be
    tested directly without invoking main() or touching the network.

    Lifecycle (every step fails closed -- this function never raises):

      1. If `destination_path` already exists (e.g. a stale artifact
         left over from a PREVIOUS run of this script), it is removed
         BEFORE this run attempts to produce a new one. This is the
         key guarantee: if everything below fails, there is no old
         artifact sitting at `destination_path` for the calling
         workflow to upload and mistake for THIS run's evidence.
      2. `payload` is sanitized (_json_sanitize(), Round
         QA-7-CLOSE-2-R2 Part B) and serialized with
         `allow_nan=False` as a strict belt-and-suspenders check. A
         serialization failure here returns False immediately --
         nothing has touched the filesystem beyond step 1 yet.
      3. The serialized text is written to a NEW temporary file in the
         SAME directory as the destination (so the final
         `os.replace()` is a same-filesystem rename, which is atomic),
         then flushed and fsync'd, then closed.
      4. `os.replace(tmp_path, destination_path)` atomically publishes
         the completed temp file as the destination -- there is no
         window in which the destination exists in a partially-written
         state: it is either absent, or it is the complete, valid file.
      5. On ANY exception in steps 2-4, the temporary file (if it was
         created) is removed, and the destination is left absent
         (step 1 already removed whatever was there before this run
         started) -- never a partial file, never a stale one.

    Round QA-7-CLOSE-2-R4, Part A hardening on top of the above:

      - The write/replace step (3-4) now catches Exception broadly,
        not just OSError -- a non-OSError failure (e.g. an unexpected
        error from write()/fsync(), or anything os.fdopen() itself
        might raise) is still caught and cleaned up here, never left
        to propagate to the caller. BaseException (KeyboardInterrupt,
        SystemExit) is deliberately NOT caught.
      - Raw file-descriptor ownership is now explicit:
        tempfile.mkstemp() hands back a raw OS-level fd that THIS
        function owns until os.fdopen() successfully wraps it in a
        file object -- at which point the file object (and the `with`
        block around it) owns it instead. If os.fdopen() itself
        raises, ownership was NEVER transferred, so the raw fd is
        still open and would otherwise leak; this function closes it
        directly in that case before re-raising into the outer
        handler.
      - A SECONDARY failure while removing the temp file during
        cleanup is never swallowed silently -- it is appended
        (separately redacted) to the returned diagnostic, so a
        cleanup failure is visible rather than hidden behind the
        original error. The function still never raises, and never
        claims a file was removed when removal demonstrably failed.

    Round QA-7-CLOSE-2-R5, Part A hardening on top of the above:

      - Step 1 (destination existence check / removal) now catches
        Exception broadly, not just OSError -- any unexpected failure
        there is reported, never propagated.
      - The temp-file cleanup check itself (os.path.exists(), then
        os.remove()) is now wrapped so that EITHER call raising --
        not just os.remove() -- is caught and reported rather than
        propagating or crashing this "never raises" helper. When
        os.path.exists() itself cannot determine whether the temp
        file remains, that uncertainty is stated explicitly rather
        than silently assuming removal succeeded.
      - os.close(tmp_fd) (invoked when os.fdopen() fails, since
        ownership of the raw fd was never transferred) is no longer
        silently swallowed on failure -- a failure there is captured
        and reported as its OWN, clearly distinguished diagnostic
        fragment, separate from the original os.fdopen() error.
      - Every piece of diagnostic text returned by this function goes
        through _safe_redact_text() (not _redact_text() directly) --
        a defensive wrapper that itself never raises and never falls
        back to exposing the unredacted original text, even if
        redaction itself were to fail unexpectedly.

    Round QA-7-CLOSE-2-R6, Part C hardening on top of the above:

      - The initial `Path(destination_path)` construction (and the
        `directory` derived from it) is now itself inside a try/except,
        matching every step after it -- previously this construction
        sat before step 1 with no exception handling at all, so a
        `destination_path` value Path() cannot accept would have
        propagated out of this "never raises" helper uncaught. No
        other step's behavior changes.

    Returns (True, None) on full success, or (False, redacted_detail)
    on any failure -- `redacted_detail` has already been passed through
    _safe_redact_text() and is safe to log directly; the caller never
    receives or logs a raw, unredacted exception.
    """
    # Round QA-7-CLOSE-2-R6, Part C: constructing `Path(destination_path)`
    # itself sat OUTSIDE any try/except in every prior round -- if
    # `destination_path` were ever something Path() cannot accept (for
    # example a non-str/bytes/os.PathLike value, which raises TypeError),
    # that exception would have propagated straight out of this
    # function, violating its own documented "never raises" contract
    # before step 1 even begins. Wrapped here, narrowly, so that this
    # one extra failure mode is reported via the same (False, detail)
    # contract as every other step, without changing any of the
    # already-tested behavior of steps 1-5 below (stale-file removal,
    # serialization, write/flush/fsync, os.replace(), and temp-file
    # cleanup are untouched). Still catches Exception only, never
    # BaseException -- KeyboardInterrupt/SystemExit are not swallowed.
    try:
        destination = Path(destination_path)
        directory = destination.parent if str(destination.parent) else Path(".")
    except Exception as e:  # noqa: BLE001 -- fail closed: any Exception, never BaseException
        return False, _safe_redact_text(str(e))

    # Step 1: remove any pre-existing (possibly stale, from a prior
    # run) destination file before this run attempts anything else.
    # If this removal itself fails, return immediately -- never claim
    # the destination was cleared when it demonstrably was not. Catches
    # Exception broadly (not just OSError): destination.exists()/
    # .unlink() are not guaranteed to raise only OSError on every
    # platform/path, and this helper's "never raises" contract must
    # hold regardless.
    try:
        if destination.exists():
            destination.unlink()
    except Exception as e:  # noqa: BLE001 -- fail closed: any Exception, never BaseException
        return False, _safe_redact_text(str(e))

    # Step 2: sanitize + strictly serialize. Nothing on disk changes
    # here beyond the removal already performed in step 1. Catches
    # Exception broadly (not just TypeError/ValueError) -- _json_sanitize()
    # recurses into dicts/lists with no cycle detection, so a
    # pathological payload (e.g. a circular reference) raises
    # RecursionError here, not TypeError/ValueError; this function's
    # "never raises" contract must hold for ANY payload shape, not just
    # the ones a narrower except clause happens to anticipate.
    try:
        serialized = json.dumps(_json_sanitize(payload), indent=2, default=str, allow_nan=False)
    except Exception as e:  # noqa: BLE001 -- fail closed for any payload pathology
        return False, _safe_redact_text(str(e))

    # Steps 3-4: write to a sibling temp file, then atomically replace.
    tmp_path: str | None = None
    tmp_fd: int | None = None
    fdopen_close_failure: Exception | None = None
    primary_error: Exception | None = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(directory), prefix=f".{destination.name}.", suffix=".tmp"
        )
        try:
            f = os.fdopen(tmp_fd, "w", encoding="utf-8")
        except Exception as fdopen_error:
            # os.fdopen() never took ownership of tmp_fd -- it is
            # still a raw, open fd that THIS function owns and must
            # close itself here, or it leaks. A failure while closing
            # it is NOT silently dropped -- it is captured as its own
            # diagnostic, distinct from fdopen_error, and surfaced
            # below alongside it. Re-raise fdopen_error so the outer
            # handler still does the usual temp-file cleanup.
            try:
                os.close(tmp_fd)
            except Exception as close_error:  # noqa: BLE001
                fdopen_close_failure = close_error
            raise fdopen_error
        tmp_fd = None  # ownership transferred to `f`; `with f:` below closes it.
        with f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(destination))
        return True, None
    except Exception as e:  # noqa: BLE001 -- fail closed: any Exception, never BaseException
        primary_error = e

    # Step 5 (reached only on failure above): never leave a partial
    # temp file around, and never leave a partially-written
    # destination -- os.replace() is atomic, so if it did not
    # complete, the destination (already cleared in step 1) is simply
    # absent, exactly as intended. The existence check AND the removal
    # are both wrapped -- either can raise -- and any failure here is
    # appended to the diagnostic (redacted), never hidden behind the
    # original error, and the temp file's actual fate is never
    # overstated when its status could not be confirmed.
    cleanup_detail = ""
    if tmp_path is not None:
        try:
            tmp_still_present = os.path.exists(tmp_path)
        except Exception as exists_error:  # noqa: BLE001
            cleanup_detail = (
                " (could not determine whether the temp file still exists, "
                f"so its removal is NOT confirmed: {_safe_redact_text(str(exists_error))})"
            )
        else:
            if tmp_still_present:
                try:
                    os.remove(tmp_path)
                except Exception as cleanup_error:  # noqa: BLE001
                    cleanup_detail = (
                        f" (temp file cleanup also failed: {_safe_redact_text(str(cleanup_error))})"
                    )

    detail = _safe_redact_text(str(primary_error))
    if fdopen_close_failure is not None:
        detail += (
            " (additionally, closing the raw file descriptor after an "
            f"os.fdopen() failure also failed: {_safe_redact_text(str(fdopen_close_failure))})"
        )
    detail += cleanup_detail
    return False, detail


def _is_nan_value(value: Any) -> bool:
    """True only for an actual float NaN (FRED's documented missing-value
    representation after production parsing). Used so that several rows
    all carrying the missing-value marker are correctly treated as
    CONSISTENT duplicates, never as a value conflict -- NaN != NaN under
    ordinary equality, which would otherwise make every genuine missing-
    value duplicate look like a disagreement."""
    return isinstance(value, float) and value != value


def _is_malformed_evidence_value(value: Any) -> bool:
    """
    Round QA-7-CLOSE-2-R4, Part B. True for any "value" that is NOT a
    float. Per src/macro/fred_provider.py's documented contract
    (_parse_numeric_cell(), read-only -- never modified or imported
    here, this function is a local, independent check): the "value"
    field FredMacroProvider.get_series() actually returns is ALWAYS a
    float -- either a real finite number, or float("nan") for FRED's
    "." missing-value marker. It is NEVER None, an int, a str, a list,
    or a dict.

    A value of any other type is therefore malformed relative to that
    contract. The most urgent reason this check exists: {row.get("value")
    for row in rows ...} (the distinct-value set used for duplicate/
    conflict detection) raises an uncaught TypeError the moment an
    unhashable value (a list or dict) reaches it -- this function lets
    the caller detect and exclude such rows BEFORE they ever reach a
    set comprehension, so a malformed value can never crash evidence
    building, never be silently coerced into a number or a string, and
    never be ignored in a way that lets the REMAINING, well-formed
    values at that same cell produce a false "PASS" on their own."""
    return not isinstance(value, float)


def _is_infinite_value(value: Any) -> bool:
    """Round QA-7-CLOSE-2-R3, Part B. True only for an actual float
    +Infinity or -Infinity. Deliberately DISTINCT from _is_nan_value():
    FRED's missing-value marker is NaN, never Infinity, so an infinite
    value is never treated the same way NaN is (it is not a documented
    "missing" representation to tolerate as a consistent duplicate --
    see _build_production_pit_evidence()'s status decision, which fails
    closed to INCONCLUSIVE whenever this is True for the expected or
    actual selected value, rather than letting
    _values_reconcile(inf, inf)'s ordinary-equality "True" silently
    establish evidence integrity)."""
    return isinstance(value, float) and math.isinf(value)


def _values_reconcile(a: Any, b: Any) -> bool:
    """Value-equality check for reconciling a selected record's value
    against the independently-known expected value -- handles the NaN
    case explicitly (see _is_nan_value) rather than relying on `==`.
    Intentionally does NOT special-case Infinity: `inf == inf` is
    already True under ordinary equality, which is mathematically
    correct for THIS equality check alone -- the separate, additional
    requirement that an infinite value must never by itself support a
    confident PASS is enforced one layer up, by
    _build_production_pit_evidence()'s status decision (see
    _is_infinite_value()), not by weakening this function."""
    if _is_nan_value(a) and _is_nan_value(b):
        return True
    return a == b


def _build_production_pit_evidence(observations: list, series_id: str) -> dict:
    """
    Network-free, pure evidence builder -- never fetches anything and
    never parses a crosstab itself (that is exclusively
    src.macro.fred_provider._parse_crosstab_response()'s job, already
    exercised by whichever caller produced `observations`). `observations`
    must already be in the exact shape FredMacroProvider.get_series()
    returns: a list of dicts with at least series_id, observation_date,
    value, vintage_date, realtime_start.

    This function:
      1. (Round QA-7-CLOSE-2-R1, Part B) validates observation_date and
         vintage_date as actual ISO calendar dates via
         _parse_iso_date_for_evidence() before anything is grouped or
         chronologically compared -- a row with either field missing or
         unparseable is excluded entirely, never lexically compared
         against a real date;
      2. (Part C) groups the remaining valid rows by
         (observation_date, vintage_date). An exact duplicate (same
         pair, same value -- NaN-aware, see _is_nan_value()) is
         deduplicated to one representative row. A CONFLICTING
         duplicate (same pair, genuinely different values) disqualifies
         that one vintage_date from being counted as a usable candidate
         at all -- it is never silently resolved by picking whichever
         row the selector's own internal tie-break happens to prefer;
      3. keeps only observation_date groups with >=2 DISTINCT, conflict-
         free vintage_date values;
      4. (Part D) picks the group with the MOST distinct usable
         vintages; ties are broken by an explicit, deterministic rule
         (the most recent observation_date by parsed calendar date),
         never implicit dict/insertion order;
      5. chooses as_of from that group's own actually-returned vintage
         dates (never an invented date) -- specifically the
         second-newest distinct vintage, which guarantees at least one
         vintage is "future" relative to as_of (demonstrating exclusion)
         while also being itself eligible (demonstrating selection);
      6. independently computes which vintage AND VALUE should be
         selected, for verification purposes only -- this check-oracle
         is computed directly from the candidate list's own (already
         deduplicated/conflict-checked) dates and values, not a
         reimplementation of pit.py's internals;
      7. calls the REAL, unmodified pit.select_latest_vintage_as_of()
         directly on this one observation_date's real candidates, AND
         separately calls the REAL, unmodified
         pit.get_point_in_time_series() over the full `observations`
         list (the actual series-level production entry point),
         extracting its row for the chosen observation_date -- both are
         real production calls, neither is reimplemented here. Both
         calls, and the comparison that follows, are wrapped so that any
         unexpected exception fails closed to INCONCLUSIVE rather than
         propagating (Part A; the outer live wrapper also wraps this
         entire function as defense-in-depth);
      8. compares both real results' SELECTED VINTAGE *and* VALUE
         against the independently-computed expectation and reports
         whether they reconcile.

    Returns a dict with "status": "PASS" only when a usable, conflict-
    free multi-vintage case was found AND both real selector calls agree
    with the independently-computed expected vintage AND VALUE AND
    neither selected a later-than-as_of ("future") vintage. Otherwise
    "status": "INCONCLUSIVE" with an explicit "reason" and a "stage"
    field distinguishing why (see callers of this function for the full
    stage vocabulary). This function never raises -- an ordinary "no
    suitable case in this data" condition, a date-validation exclusion,
    a detected value conflict, or an unexpected exception from the real
    selector calls are all reported as INCONCLUSIVE, never an uncaught
    exception and never a false PASS.
    """
    valid_rows: list[tuple[str, str, dict]] = []
    excluded_invalid_date_row_count = 0
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        obs_date_raw = obs.get("observation_date")
        vintage_date_raw = obs.get("vintage_date")
        if (
            _parse_iso_date_for_evidence(obs_date_raw) is None
            or _parse_iso_date_for_evidence(vintage_date_raw) is None
        ):
            excluded_invalid_date_row_count += 1
            continue
        valid_rows.append((obs_date_raw, vintage_date_raw, obs))

    by_obs_date: dict[str, dict[str, list]] = {}
    for obs_date_raw, vintage_date_raw, obs in valid_rows:
        by_obs_date.setdefault(obs_date_raw, {}).setdefault(vintage_date_raw, []).append(obs)

    candidate_groups: dict[str, list] = {}
    conflicted_cell_count = 0
    malformed_value_cell_count = 0
    for obs_date_raw, by_vintage in by_obs_date.items():
        usable_rows = []
        for vintage_date_raw, rows in by_vintage.items():
            # Round QA-7-CLOSE-2-R4, Part B: check for a malformed
            # (non-float) "value" BEFORE building any set from these
            # rows -- {row.get("value") for row in rows ...} below
            # would raise an uncaught TypeError the instant an
            # unhashable value (list/dict) reached it. A malformed
            # value at this cell -- whether alone or alongside an
            # otherwise-fine finite/NaN value -- disqualifies the
            # WHOLE cell from the usable candidate set, exactly like a
            # conflicting duplicate: it is never silently dropped so
            # that the remaining well-formed rows at this same cell
            # can produce a PASS on their own.
            if any(_is_malformed_evidence_value(row.get("value")) for row in rows):
                malformed_value_cell_count += 1
                continue
            distinct_values = {row.get("value") for row in rows if not _is_nan_value(row.get("value"))}
            nan_present = any(_is_nan_value(row.get("value")) for row in rows)
            # Round QA-7-CLOSE-2-R2, Part A: a NaN row and a finite-value
            # row at the SAME (observation_date, vintage_date) pair are
            # themselves a disagreement -- one row claims "missing",
            # another claims a real value -- and must be treated as a
            # conflicting duplicate exactly like two different finite
            # values would be, never as a consistent duplicate decided
            # by whichever row happens to be first in `rows`. Only when
            # EVERY row at this cell is NaN (distinct_values empty, no
            # finite value ever claimed) is it a genuine consistent
            # missing-value duplicate, preserved as before.
            mixed_nan_and_finite = nan_present and bool(distinct_values)
            if len(distinct_values) > 1 or mixed_nan_and_finite:
                # Conflicting duplicate: same (observation_date,
                # vintage_date) pair, genuinely different values (or a
                # NaN/finite mix). This one vintage_date is excluded
                # from the usable candidate set entirely -- never
                # resolved by date equality alone or by input order.
                conflicted_cell_count += 1
                continue
            usable_rows.append(rows[0])  # dedup: one representative row
        distinct_usable_vintages = {row["vintage_date"] for row in usable_rows}
        if len(distinct_usable_vintages) >= 2:
            candidate_groups[obs_date_raw] = usable_rows

    if not candidate_groups:
        reason = (
            "No observation_date in the returned window has two or more "
            "distinct, internally-consistent vintage_date candidates; "
            "cannot exercise the production PIT selector's tie-break on "
            "this data."
        )
        if conflicted_cell_count:
            reason += (
                f" {conflicted_cell_count} observation_date/vintage_date "
                "pair(s) had conflicting duplicate values and were "
                "excluded as unusable rather than silently resolved."
            )
        if malformed_value_cell_count:
            reason += (
                f" {malformed_value_cell_count} observation_date/vintage_date "
                "pair(s) contained a malformed (non-float) value and were "
                "excluded as unusable rather than silently ignored."
            )
        if excluded_invalid_date_row_count:
            reason += (
                f" {excluded_invalid_date_row_count} row(s) were excluded "
                "for an unparseable observation_date or vintage_date."
            )
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_succeeded_no_usable_candidates",
            "reason": reason,
            "series_id": series_id,
            "observation_dates_examined": len(by_obs_date),
            "excluded_invalid_date_row_count": excluded_invalid_date_row_count,
            "conflicted_cell_count": conflicted_cell_count,
            "malformed_value_cell_count": malformed_value_cell_count,
        }

    # Part D: deterministic tie-break -- most distinct usable vintages
    # wins; ties broken by the MOST RECENT observation_date (max by
    # parsed calendar date), never implicit dict/insertion order. This
    # selects only which real example is reported; it does not alter
    # PIT selector semantics.
    def _group_sort_key(obs_date_raw: str) -> tuple:
        vintage_count = len({row["vintage_date"] for row in candidate_groups[obs_date_raw]})
        return (vintage_count, _parse_iso_date_for_evidence(obs_date_raw))

    chosen_obs_date = max(candidate_groups, key=_group_sort_key)
    group = candidate_groups[chosen_obs_date]
    distinct_vintages = sorted(
        {row["vintage_date"] for row in group},
        key=_parse_iso_date_for_evidence,
    )

    if len(distinct_vintages) < 2:
        # Defensive only -- the candidate_groups filter above already
        # guarantees this; fail closed rather than index-error if that
        # invariant is ever violated.
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_succeeded_no_usable_candidates",
            "reason": "Selected observation_date unexpectedly has fewer than two distinct vintages.",
            "series_id": series_id,
            "observation_date": chosen_obs_date,
        }

    as_of = distinct_vintages[-2]
    as_of_parsed = _parse_iso_date_for_evidence(as_of)
    eligible_vintages = [
        v for v in distinct_vintages if _parse_iso_date_for_evidence(v) <= as_of_parsed
    ]
    excluded_future_vintages = [v for v in distinct_vintages if v not in eligible_vintages]
    expected_selected_vintage = max(eligible_vintages, key=_parse_iso_date_for_evidence)
    expected_row = next(
        (row for row in group if row["vintage_date"] == expected_selected_vintage), None
    )
    expected_value = expected_row.get("value") if expected_row else None

    # ---- Part A: the real selector calls and their comparison are
    # wrapped so an unexpected exception here fails closed to
    # INCONCLUSIVE rather than escaping -- never silently claims parsing
    # or selection occurred when it did not.
    try:
        # select_latest_vintage_as_of() requires an actual date object
        # (it does not coerce a string itself -- only
        # get_point_in_time_series() does that, via
        # pit.coerce_as_of_date()), so "as_of" is parsed here before
        # this one direct call; the call itself is still the real,
        # unmodified production function.
        selected_direct = select_latest_vintage_as_of(group, date.fromisoformat(as_of))
        pit_series = get_point_in_time_series(observations, series_id, as_of)
        selected_via_series = next(
            (row for row in pit_series if row.get("observation_date") == chosen_obs_date),
            None,
        )

        selected_direct_vintage = selected_direct.get("vintage_date") if selected_direct else None
        selected_series_vintage = (
            selected_via_series.get("vintage_date") if selected_via_series else None
        )
        selected_direct_value = selected_direct.get("value") if selected_direct else None
        selected_series_value = selected_via_series.get("value") if selected_via_series else None

        agrees_with_expected = (
            selected_direct_vintage == expected_selected_vintage
            and selected_series_vintage == expected_selected_vintage
        )
        values_reconcile = _values_reconcile(
            selected_direct_value, expected_value
        ) and _values_reconcile(selected_series_value, expected_value)
        no_future_vintage_selected = (
            selected_direct_vintage not in excluded_future_vintages
            and selected_series_vintage not in excluded_future_vintages
        )
        # Round QA-7-CLOSE-2-R3, Part B: positive or negative infinity
        # must never, by itself, support a confident PASS. inf == inf
        # is ordinary, correct float equality -- _values_reconcile()
        # above is not wrong to report True for it -- but that alone
        # must not be allowed to "silently establish evidence
        # integrity". If infinity appears in EITHER the independently-
        # computed expected value OR either real selector's returned
        # value for this example, the result is forced to INCONCLUSIVE
        # below, with an explicit reason, regardless of what
        # agrees_with_expected/values_reconcile/no_future_vintage_selected
        # otherwise say. Never converts infinity to a missing value
        # first (that remains purely a serialization-time concern, see
        # _json_sanitize()) -- this check runs on the real float value.
        any_infinite_value = (
            _is_infinite_value(expected_value)
            or _is_infinite_value(selected_direct_value)
            or _is_infinite_value(selected_series_value)
        )
    except Exception as e:  # noqa: BLE001 -- fail closed, never propagate
        # Round QA-7-CLOSE-2-R5, Part B: the diagnostic counters below
        # were ALL already computed earlier in this function (grouping,
        # date validation, conflict/malformed-value detection all
        # happen before the real selector calls in the try block
        # above) -- they are carried over here rather than omitted,
        # so an exception from the real selector invocation doesn't
        # also throw away evidence this function had already gathered.
        # Nothing here is guessed or estimated: every value is the
        # exact count this function computed, using the same field
        # names the successful-path result dict already uses.
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_succeeded_evidence_build_error",
            "reason": "Production selector invocation raised an unexpected exception.",
            "series_id": series_id,
            "observation_date": chosen_obs_date,
            "as_of": as_of,
            "detail": _redact_exception_text(e)[:300],
            "excluded_invalid_date_row_count": excluded_invalid_date_row_count,
            "conflicted_cell_count": conflicted_cell_count,
            "malformed_value_cell_count": malformed_value_cell_count,
            "observation_dates_examined": len(by_obs_date),
            "multi_vintage_observation_dates_found": len(candidate_groups),
        }

    # Round QA-7-CLOSE-2-R6, Part B: "PASS" here is scoped strictly to
    # the ONE observation_date/candidate group actually selected above
    # (`chosen_obs_date` / `group`) -- it is never a statement about
    # the integrity or data quality of the full response window this
    # evidence was built from. A malformed-value cell at a different,
    # unrelated observation_date (counted in `malformed_value_cell_count`
    # below, which is always present in this result regardless of
    # status) does not and must not block this PASS; a malformed cell
    # belonging to THIS SAME group, by contrast, was already excluded
    # before `group` was ever assembled (see the per-cell filtering
    # loop above), so it can never be the thing silently "passing"
    # here either. This status is not changed or widened by this
    # round -- only this comment, and the tests immediately above, make
    # that scoping explicit.
    status = (
        "PASS"
        if (
            agrees_with_expected
            and values_reconcile
            and no_future_vintage_selected
            and selected_direct is not None
            and selected_via_series is not None
            and not any_infinite_value
        )
        else "INCONCLUSIVE"
    )

    candidate_sample = [
        {"vintage_date": g["vintage_date"], "value": g.get("value")}
        for g in sorted(group, key=lambda g: _parse_iso_date_for_evidence(g["vintage_date"]))
    ]

    result = {
        "status": status,
        "stage": "request_succeeded_evidence_built",
        "series_id": series_id,
        "observation_date": chosen_obs_date,
        "as_of": as_of,
        "candidate_vintage_dates": distinct_vintages,
        "candidate_sample": candidate_sample,
        "eligible_vintage_dates": eligible_vintages,
        "excluded_future_vintage_dates": excluded_future_vintages,
        "expected_selected_vintage": expected_selected_vintage,
        "expected_value": expected_value,
        "actual_selected_vintage_direct": selected_direct_vintage,
        "actual_selected_vintage_via_series": selected_series_vintage,
        "actual_selected_value_direct": selected_direct_value,
        "actual_selected_value_via_series": selected_series_value,
        "agrees_with_expected": agrees_with_expected,
        "values_reconcile": values_reconcile,
        "no_future_vintage_selected": no_future_vintage_selected,
        "any_infinite_value": any_infinite_value,
        "observation_dates_examined": len(by_obs_date),
        "multi_vintage_observation_dates_found": len(candidate_groups),
        "excluded_invalid_date_row_count": excluded_invalid_date_row_count,
        "conflicted_cell_count": conflicted_cell_count,
        "malformed_value_cell_count": malformed_value_cell_count,
    }
    if status == "INCONCLUSIVE":
        if any_infinite_value:
            # Round QA-7-CLOSE-2-R3, Part B: a specific, explicit reason
            # -- never collapsed into the generic reconciliation message
            # below -- so a reader (or a future test) can distinguish
            # "an infinite value made this evidence invalid" from an
            # ordinary reconciliation failure.
            result["reason"] = (
                "Positive or negative infinity appeared in the "
                "independently-computed expected value or in a real "
                "selector's returned value for this example; an "
                "infinite value is never treated as a valid numeric "
                "observation for a confident PASS, even when it "
                "trivially reconciles against itself under ordinary "
                "float equality."
            )
        else:
            result["reason"] = (
                "Real pit.py selector output did not reconcile with the "
                "independently-computed expected vintage/value, or selected "
                "a future vintage, or returned None -- see the fields above."
            )
    return result


def section_m_production_output_type2_pit_selector_probe(
    series_id: str, start_date: str, end_date: str
) -> dict:
    """
    Round QA-7-CLOSE-2, Part B (hardened in Round QA-7-CLOSE-2-R1,
    Part A). LIVE-ONLY wrapper around _build_production_pit_evidence():
    calls the REAL, unmodified
    src.macro.fred_provider.FredMacroProvider.get_series() (the actual
    production output_type=2 fetch + crosstab-parsing path) for
    `series_id`, then hands the real result to the network-free builder
    above.

    Fails closed to {"status": "INCONCLUSIVE", "reason": ..., "stage":
    ...} -- never raises -- when FRED_API_KEY is unavailable, on any
    classified FRED HTTP/response-shape error, on any other exception
    raised by the fetch itself, OR on any unexpected exception raised by
    _build_production_pit_evidence() (defense-in-depth: that function
    already fails closed internally, but this call is wrapped here too,
    so a failure can never escape this wrapper regardless of where it
    originates). Every error detail is passed through
    _redact_text()/_redact_exception_text() before being stored, exactly
    like every other section in this script; no API key, credential, or
    unredacted exception text is ever included in the returned dict.

    The "stage" field on every returned dict distinguishes:
      - "request_not_attempted_unsupported_series" (series_id is not in
        WIDE_WINDOW_SERIES_IDS, so no request was ever attempted --
        distinct from the API-key case below, which DOES target a
        supported series but could not authenticate)
      - "request_not_attempted_api_key_missing" (FRED_API_KEY absent)
      - "request_failed" (fetch itself raised)
      - "request_succeeded_no_usable_candidates" (fetch OK, no usable
        multi-vintage case in the data)
      - "request_succeeded_evidence_build_error" (fetch OK, but building
        or invoking the real selector raised unexpectedly)
      - "request_succeeded_evidence_built" (fetch OK, real selector
        path fully exercised -- "status" says whether it reconciled)
    "production_path_exercised" is True only once the real fetch
    actually returned data; it is False whenever the fetch itself never
    completed.
    """
    if series_id not in WIDE_WINDOW_SERIES_IDS:
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_not_attempted_unsupported_series",
            "reason": (
                f"{series_id!r} is not in WIDE_WINDOW_SERIES_IDS; this probe "
                "only targets the production output_type=2 wide-window path."
            ),
            "series_id": series_id,
            "production_path_exercised": False,
        }

    provider = FredMacroProvider()
    try:
        observations = provider.get_series(series_id, start_date=start_date, end_date=end_date)
    except FredAPIKeyMissing as e:
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_not_attempted_api_key_missing",
            "reason": (
                "FRED_API_KEY is not set in this execution environment; the "
                "real production output_type=2 request was never made."
            ),
            "series_id": series_id,
            "detail": _redact_text(str(e)),
            "production_path_exercised": False,
        }
    except FredHTTPError as e:
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_failed",
            "reason": f"Production fetch failed: classification={e.classification} status={e.status}",
            "series_id": series_id,
            "detail": (_redact_text(e.detail) or "")[:300],
            "production_path_exercised": False,
        }
    except FredProviderError as e:
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_failed",
            "reason": "Production fetch raised a FredProviderError.",
            "series_id": series_id,
            "detail": _redact_text(str(e)),
            "production_path_exercised": False,
        }
    except Exception as e:  # noqa: BLE001 -- fail closed, never crash main()
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_failed",
            "reason": "Production fetch raised an unexpected exception.",
            "series_id": series_id,
            "detail": _redact_exception_text(e)[:300],
            "production_path_exercised": False,
        }

    # Part A, defense-in-depth: _build_production_pit_evidence() already
    # fails closed internally (see its own try/except around the real
    # selector calls), but this call is wrapped too, so nothing it
    # raises can ever escape this wrapper.
    try:
        evidence = _build_production_pit_evidence(observations, series_id)
    except Exception as e:  # noqa: BLE001 -- fail closed, never crash main()
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_succeeded_evidence_build_error",
            "reason": "_build_production_pit_evidence() raised an unexpected exception.",
            "series_id": series_id,
            "detail": _redact_exception_text(e)[:300],
            "production_path_exercised": True,
            "total_raw_records_returned": len(observations) if isinstance(observations, list) else None,
        }

    evidence["production_path_exercised"] = True
    evidence["request_params"] = {
        "series_id": series_id,
        "start_date": start_date,
        "end_date": end_date,
        "fetch_strategy": (
            "output_type=2 (WIDE_WINDOW_SERIES_IDS path, via the real, "
            "unmodified FredMacroProvider.get_series())"
        ),
    }
    evidence["total_raw_records_returned"] = len(observations)
    return evidence


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

    # ---- Round QA-7-CLOSE-3: determine the single effective real-time
    # as_of value CASE A ("today"), SECTION B-2, and SECTION I will all
    # use below -- see the long comment above _determine_effective_
    # realtime_as_of() for the diagnosed cause. This does NOT change
    # as_of_today itself (still used, unmodified, for observation_start/
    # observation_end elsewhere in this function and for SECTION M,
    # which is unaffected by this diagnosis and is out of this round's
    # scope) -- only the separate `effective_realtime_as_of_today`
    # value below is threaded into the three in-scope sections. ----
    _print_header("REALTIME AS_OF PROBE (Round QA-7-CLOSE-3)")
    realtime_as_of_probe = _determine_effective_realtime_as_of(as_of_today, SERIES_TO_CHECK[0])
    effective_realtime_as_of_today = realtime_as_of_probe["effective_realtime_as_of"]
    _log(f"  requested_realtime_as_of={realtime_as_of_probe['requested_realtime_as_of']}")
    _log(f"  effective_realtime_as_of={effective_realtime_as_of_today} "
         f"adjusted={realtime_as_of_probe['adjusted']}")
    _log(f"  probe_status={realtime_as_of_probe['probe_status']} "
         f"probe_classification={realtime_as_of_probe['probe_classification']}")
    _log(f"  reason={realtime_as_of_probe['reason']}")
    RESULTS["realtime_as_of_probe"] = realtime_as_of_probe

    # ---- SECTION 2/9: CASE A for all 4 series at as_of_today, plus 2
    # past as_of dates (section 3's "at least 2" past-AS_OF requirement).
    # "today" uses effective_realtime_as_of_today (Round QA-7-CLOSE-3);
    # past_1/past_2 are untouched -- they already succeed and are not
    # part of the diagnosed date-boundary condition. Round QA-7-CLOSE-4:
    # the actual per-series/per-label calls below are now made by
    # _run_case_a_with_effective_as_of() (a directly-testable orchestration
    # seam) instead of an inline loop -- same arguments, same order, same
    # _log lines as before this round; no behavior change. ----
    _print_header("SECTION CASE-A -- realtime snapshot only (current + 2 past AS_OF)")
    case_a_results = _run_case_a_with_effective_as_of(
        SERIES_TO_CHECK, effective_realtime_as_of_today, as_of_past_1, as_of_past_2
    )
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

    # ---- SECTION B-2 (Step 10J-QA-7 section 3): monthly observation_start
    # day-01/15/28 probe, isolated to a single safely-in-the-past month so
    # the data is fully released/stable (not an in-progress release
    # month). Run for both monthly series. ----
    # "today"'s realtime as_of uses effective_realtime_as_of_today
    # (Round QA-7-CLOSE-3) -- b2_test_month and the observation window
    # it derives are untouched (a different, observation-window
    # variable, never conflated with the realtime as_of value). Round
    # QA-7-CLOSE-4: the actual call is now made by
    # _run_section_b2_with_effective_as_of() -- same arguments, same
    # order, same _log lines as before this round; no behavior change.
    _print_header("SECTION B-2 -- monthly observation_start day-01/15/28 probe (FRED server-side behavior)")
    b2_test_month = _shift_months(today, -6)
    b2_results = _run_section_b2_with_effective_as_of(
        MONTHLY_SERIES, effective_realtime_as_of_today, b2_test_month
    )
    RESULTS["b2_monthly_precision"] = b2_results

    # ---- SECTION I (Step 10J-QA-7 section 4): vintage_dates independent
    # cross-check for PAYEMS and CPIAUCSL, same as_of + observation
    # window, compared pair-by-pair. Path A (realtime_start=realtime_end)
    # and Path B (vintage_dates) inside section_i_vintage_dates_cross_check()
    # both receive the SAME `as_of` argument, so passing
    # effective_realtime_as_of_today here (Round QA-7-CLOSE-3) covers
    # both paths without any further change to that function.
    # i_window_start/i_window_end remain the observation window (as_of_today,
    # unmodified) -- a different FRED parameter, never treated as the
    # same variable as the realtime as_of value above. Round QA-7-CLOSE-4:
    # the actual call is now made by _run_section_i_with_effective_as_of()
    # -- same arguments, same order, same _log lines as before this
    # round; no behavior change. ----
    _print_header("SECTION I -- vintage_dates independent cross-check (PAYEMS, CPIAUCSL)")
    i_window_start = _shift_months(today, -13).isoformat()
    i_window_end = as_of_today
    i_results = _run_section_i_with_effective_as_of(
        ("PAYEMS", "CPIAUCSL"), effective_realtime_as_of_today, i_window_start, i_window_end
    )
    RESULTS["i_vintage_dates_cross_check"] = i_results

    # ---- SECTION M (Round QA-7-CLOSE-2): live production output_type=2
    # PIT selector probe. Uses the real, unmodified FredMacroProvider.
    # get_series() + pit.select_latest_vintage_as_of()/
    # get_point_in_time_series() -- not a reimplementation. Fails closed
    # to INCONCLUSIVE (never a fabricated PASS) if no usable multi-
    # vintage case is found, FRED_API_KEY is unavailable, or any other
    # error occurs. PAYEMS is the primary target (confirmed revised --
    # see SECTION MULTIPLE-AS_OF above); same ~13-month window as
    # SECTION I, reused for consistency, not independently re-derived. ----
    _print_header("SECTION M -- live production output_type=2 PIT selector probe (PAYEMS)")
    m_window_start = _shift_months(today, -13).isoformat()
    m_window_end = as_of_today
    m_result = section_m_production_output_type2_pit_selector_probe(
        "PAYEMS", m_window_start, m_window_end
    )
    _log(json.dumps(m_result, indent=2, default=str))
    RESULTS["m_production_output_type2_pit_selector_probe"] = m_result

    # Round QA-7-CLOSE-2 Part C: write the compact evidence artifact to
    # disk (in addition to the RESULTS JSON already logged to stdout
    # above) so the calling workflow can upload it as a named,
    # downloadable GitHub Actions artifact -- see the "Upload QA-7-
    # CLOSE-2 evidence artifact" step in fred-output-type1-validation.yml.
    # Never includes an API key, a URL containing one, or raw unredacted
    # exception text -- m_result already went through the same
    # _redact_text()/_redact_exception_text() discipline as every other
    # section above before reaching this point.
    evidence_artifact = {
        "validation_timestamp": datetime.utcnow().isoformat() + "Z",
        "round": "QA-7-CLOSE-2",
        "probe": "m_production_output_type2_pit_selector_probe",
        "result": m_result,
    }
    # Round QA-7-CLOSE-2-R3, Part A: the write itself is delegated to
    # _write_evidence_artifact_atomically(), which (1) removes any
    # stale artifact left over from a previous run BEFORE attempting
    # this run's write, (2) sanitizes + strictly serializes, (3) writes
    # a same-directory temp file and atomically os.replace()s it into
    # place, and (4) on any failure, removes the temp file and leaves
    # the destination absent -- never partial, never stale. The success
    # log line below is reached only once that helper reports True.
    artifact_written, artifact_error_detail = _write_evidence_artifact_atomically(
        PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH, evidence_artifact
    )
    if artifact_written:
        _log(f"  evidence artifact written: {PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH}")
    else:
        # Never let a filesystem problem, or an unsanitizable non-finite
        # value, fail the whole validation run -- the evidence is
        # already in RESULTS/stdout above regardless. artifact_error_detail
        # has already been redacted by the helper above.
        _log(f"  WARNING: could not write evidence artifact file: {artifact_error_detail}")
        # Round QA-7-CLOSE-2-R5 Test Review Follow-up, Part C: the
        # calling workflow's "Upload QA-7-CLOSE-2 production PIT
        # evidence artifact" step uploads whatever file sits at
        # PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH unconditionally
        # (if: always(), if-no-files-found: warn) -- it has no way to
        # know this run's write attempt failed. The one scenario in
        # which a STALE file left over from a PREVIOUS run can survive
        # a failed write here is when _write_evidence_artifact_atomically()'s
        # own step 1 (its destination existence check / removal,
        # attempted before anything else) itself raised -- every later
        # failure point inside that helper already guarantees the
        # destination ends up absent. This is a second, independent,
        # best-effort attempt (never claimed to be guaranteed -- see
        # the WARNING below if it too cannot confirm removal) to clear
        # whatever is left at that path, so a stale artifact from an
        # earlier run is never uploaded and mistaken for THIS run's
        # (failed) evidence. Deliberately does not modify
        # _write_evidence_artifact_atomically() itself or the workflow
        # file -- this is the smallest change that closes the gap
        # without touching either.
        try:
            if os.path.exists(PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH):
                os.remove(PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH)
                _log(
                    "  additionally removed a leftover/stale file at "
                    f"{PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH} so it is never "
                    "uploaded as if it were this (failed) run's evidence"
                )
        except Exception as cleanup_error:  # noqa: BLE001 -- best-effort only, never fail the run over this
            _log(
                "  WARNING: could not confirm removal of a possibly-stale "
                f"artifact file: {_safe_redact_text(str(cleanup_error))}"
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
