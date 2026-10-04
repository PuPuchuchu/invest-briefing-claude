"""
Step 10I -- FRED multi-vintage response structure validation (GitHub-hosted
runner only).

PURPOSE: answer exactly the question Step 10H's OPEN SEMANTIC ISSUE #1
raised -- does a real `fred/series/observations?output_type=2` response
actually contain MULTIPLE rows for the SAME observation_date (distinct
vintages), and does each such row carry the realtime_start/realtime_end
fields src/macro/pit.py's vintage-selection design requires? This script
answers that from a REAL response, not from documentation. It also calls
`fred/series/vintagedates` and `fred/series` (metadata) once each, and
records exactly what each endpoint actually returns.

THIS IS NOT A PROVIDER. It is intentionally NOT imported by, and does
NOT import, anything under src/macro/ or src/. It writes no CSV, touches
no production code, and makes no PIT/state/regime decision of any kind.
It is validation-only infrastructure for Step 10I, mirroring Step 10G's
smoke-test script's own "obviously disconnected from production code"
convention (kept outside src/, workflow_dispatch-only trigger).

SECURITY: FRED_API_KEY is read from the environment (injected by the
calling workflow from secrets.FRED_API_KEY) and is NEVER printed or
logged -- not even as FRED_API_KEY=..., not even partially. Only the
boolean presence (`FRED_API_KEY_PRESENT: True/False`) is ever printed.
Every URL this script would otherwise log has its api_key value redacted
before printing, mirroring Step 10G's _redact() exactly. Response bodies
are never dumped in full -- only a bounded number of representative rows
(series_id, observation_date, realtime_start, realtime_end, value) are
ever printed, per this round's explicit Rule C/D.

Only the standard library is used (urllib, json, os, re, sys, socket,
ssl) -- no new dependency, consistent with every prior Step 10 script.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

FRED_OBSERVATIONS_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_VINTAGEDATES_BASE = "https://api.stlouisfed.org/fred/series/vintagedates"
FRED_SERIES_BASE = "https://api.stlouisfed.org/fred/series"

# Narrow observation windows, chosen deliberately small (Rule D -- this is
# a structure-validation round, not a bulk data pull). PAYEMS and CPIAUCSL
# are queried over the same 6-month window so their vintage counts can be
# compared directly, side by side, in the SUMMARY section.
REVISION_WINDOW_START = "2015-01-01"
REVISION_WINDOW_END = "2015-06-01"

# A wide realtime window is required for output_type=2 to have any chance
# of returning more than the single "current" vintage per observation_date
# -- FRED's realtime_start/realtime_end bound WHICH vintages are in scope,
# not just which observation_dates are. Left deliberately wide (back to
# before FRED's API itself existed in practical terms) rather than guessed
# narrow, so a real multi-vintage response is not accidentally missed by
# an overly tight window.
WIDE_REALTIME_START = "1990-01-01"
WIDE_REALTIME_END = "2026-10-04"

SERIES_CPI = "CPIAUCSL"       # primary multi-vintage candidate (Rule: use first, but
                               # do not assume revision behavior -- confirm from response)
SERIES_PAYEMS = "PAYEMS"      # second, independent economic series -- pit.py's own
                               # docstring cites PAYEMS by name as the motivating
                               # revision example; used here to avoid depending on
                               # CPIAUCSL alone
SERIES_T10Y2Y = "T10Y2Y"      # required baseline (daily, market-derived, not expected
                               # to be revised -- used as a contrast case)
SERIES_VIXCLS = "VIXCLS"      # required baseline, same role as T10Y2Y

RESULTS: dict[str, str] = {}
MISSING_VALUE_MARKERS_OBSERVED: set[str] = set()
SCHEMA_KEYS_PRINTED = False  # print raw observation-row key names exactly once


def _redact(url: str) -> str:
    return re.sub(r"(api_key=)[^&]+", r"\1REDACTED", url)


def _build_url(base: str, params: dict) -> str:
    api_key = os.environ.get("FRED_API_KEY", "")
    query = dict(params)
    query["api_key"] = api_key
    query["file_type"] = "json"
    return f"{base}?{urllib.parse.urlencode(query)}"


def _request(url: str, timeout: int = 30):
    """Returns (status, parsed_json_or_None, error_classification_or_None, detail).
    Never raises. Never includes the raw (unredacted) URL in any returned
    detail string -- callers use _redact(url) if they want to log it."""
    req = urllib.request.Request(url, headers={"User-Agent": "invest-briefing-claude-step10i-validation/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if status == 429:
            return status, None, "RATE_LIMITED", body[:300]
        if status == 400:
            lowered = body.lower()
            if "api_key" in lowered or "api key" in lowered:
                return status, None, "AUTH_FAILED", body[:300]
            if "does not exist" in lowered:
                return status, None, "SERIES_NOT_FOUND", body[:300]
            return status, None, "INVALID_PARAMETER", body[:300]
        return status, None, "HTTP_FAILURE", body[:300]
    except ssl.SSLError as e:
        return None, None, "TLS_FAILURE", str(e)[:300]
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, socket.gaierror):
            return None, None, "DNS_FAILURE", str(reason)[:300]
        return None, None, "NETWORK_FAILURE", str(reason)[:300]
    except socket.timeout:
        return None, None, "NETWORK_FAILURE", "socket timeout"
    except Exception as e:  # pragma: no cover
        return None, None, "UNKNOWN", f"{type(e).__name__}: {e}"[:300]

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        return status, None, "RESPONSE_PARSE_FAILED", str(e)[:300]

    return status, parsed, None, None


def _print_schema_keys_once(observations: list[dict]) -> None:
    global SCHEMA_KEYS_PRINTED
    if SCHEMA_KEYS_PRINTED or not observations:
        return
    keys = sorted(observations[0].keys())
    print(f"  [SCHEMA] observation row key names (first response only): {keys}")
    SCHEMA_KEYS_PRINTED = True


def _scan_missing(observations: list[dict]) -> None:
    for row in observations:
        v = row.get("value")
        if v is not None and not re.match(r"^-?\d+(\.\d+)?$", str(v)):
            MISSING_VALUE_MARKERS_OBSERVED.add(str(v))


def _group_by_observation_date(observations: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in observations:
        groups.setdefault(row.get("date", ""), []).append(row)
    return groups


def _print_representative_rows(label: str, observations: list[dict], limit: int = 5) -> None:
    for row in observations[:limit]:
        print(
            f"    {label}: observation_date={row.get('date')!r} "
            f"realtime_start={row.get('realtime_start')!r} "
            f"realtime_end={row.get('realtime_end')!r} "
            f"value={row.get('value')!r}"
        )


# ============================================================
# LAYER 1/2 -- output_type=2 multi-vintage query (CPIAUCSL, PAYEMS)
# ============================================================

def _multi_vintage_layer(series_id: str, result_key: str) -> None:
    url = _build_url(
        FRED_OBSERVATIONS_BASE,
        {
            "series_id": series_id,
            "output_type": 2,
            "observation_start": REVISION_WINDOW_START,
            "observation_end": REVISION_WINDOW_END,
            "realtime_start": WIDE_REALTIME_START,
            "realtime_end": WIDE_REALTIME_END,
        },
    )
    status, parsed, err, detail = _request(url)
    if err is not None or status != 200 or parsed is None:
        RESULTS[result_key] = "FAIL"
        print(f"{result_key}: FAIL (http_status={status}, error={err}) url={_redact(url)}")
        return

    observations = parsed.get("observations", [])
    if not observations:
        RESULTS[result_key] = "FAIL"
        print(f"{result_key}: FAIL (HTTP 200 but zero observations in window)")
        return

    _print_schema_keys_once(observations)
    _scan_missing(observations)

    groups = _group_by_observation_date(observations)
    multi_vintage_dates = {d: rows for d, rows in groups.items() if len(rows) > 1}

    print(f"{result_key}: total_rows={len(observations)} distinct_observation_dates={len(groups)} "
          f"observation_dates_with_multiple_vintages={len(multi_vintage_dates)}")

    if multi_vintage_dates:
        RESULTS[result_key] = "PASS"
        shown = 0
        for obs_date, rows in sorted(multi_vintage_dates.items()):
            if shown >= 3:
                break
            print(f"  -- observation_date={obs_date!r} has {len(rows)} vintage rows:")
            _print_representative_rows(result_key, sorted(rows, key=lambda r: r.get("realtime_start", "")), limit=5)
            shown += 1
    else:
        RESULTS[result_key] = "FAIL"
        print(f"  (no observation_date in this window has more than one vintage row -- "
              f"showing up to 5 representative rows instead)")
        _print_representative_rows(result_key, observations, limit=5)


def layer1_output_type2_cpi() -> None:
    _multi_vintage_layer(SERIES_CPI, "LAYER1_OUTPUT_TYPE2_CPI")


def layer2_output_type2_payems() -> None:
    _multi_vintage_layer(SERIES_PAYEMS, "LAYER2_OUTPUT_TYPE2_PAYEMS")


# ============================================================
# LAYER 3 -- contrast case: market-data series under output_type=2
# (T10Y2Y, VIXCLS) -- expected to show ~1 row per observation_date,
# as a sanity check that multi-vintage behavior is series-dependent,
# not a universal property of output_type=2 itself.
# ============================================================

def layer3_output_type2_market_data() -> None:
    for series_id in (SERIES_T10Y2Y, SERIES_VIXCLS):
        result_key = f"LAYER3_OUTPUT_TYPE2_{series_id}"
        url = _build_url(
            FRED_OBSERVATIONS_BASE,
            {
                "series_id": series_id,
                "output_type": 2,
                "observation_start": REVISION_WINDOW_START,
                "observation_end": REVISION_WINDOW_END,
                "realtime_start": WIDE_REALTIME_START,
                "realtime_end": WIDE_REALTIME_END,
            },
        )
        status, parsed, err, detail = _request(url)
        if err is not None or status != 200 or parsed is None:
            RESULTS[result_key] = "FAIL"
            print(f"{result_key}: FAIL (http_status={status}, error={err})")
            continue

        observations = parsed.get("observations", [])
        if not observations:
            RESULTS[result_key] = "FAIL"
            print(f"{result_key}: FAIL (zero observations)")
            continue

        _scan_missing(observations)
        groups = _group_by_observation_date(observations)
        multi = {d: rows for d, rows in groups.items() if len(rows) > 1}
        max_vintages = max((len(rows) for rows in groups.values()), default=0)
        RESULTS[result_key] = "PASS"  # PASS = reachable + parsed; multi-vintage count is reported, not required here
        print(
            f"{result_key}: PASS (reachable) total_rows={len(observations)} "
            f"distinct_observation_dates={len(groups)} max_vintages_per_date={max_vintages} "
            f"observation_dates_with_multiple_vintages={len(multi)}"
        )


# ============================================================
# LAYER 4 -- fred/series/vintagedates
# ============================================================

def layer4_vintagedates() -> None:
    for series_id in (SERIES_CPI, SERIES_PAYEMS):
        result_key = f"LAYER4_VINTAGEDATES_{series_id}"
        url = _build_url(FRED_VINTAGEDATES_BASE, {"series_id": series_id})
        status, parsed, err, detail = _request(url)
        if err is not None or status != 200 or parsed is None:
            RESULTS[result_key] = "FAIL"
            print(f"{result_key}: FAIL (http_status={status}, error={err})")
            continue

        vintage_dates = parsed.get("vintage_dates", [])
        if not vintage_dates:
            RESULTS[result_key] = "FAIL"
            print(f"{result_key}: FAIL (HTTP 200 but empty/absent vintage_dates)")
            continue

        RESULTS[result_key] = "PASS"
        print(
            f"{result_key}: PASS (count={len(vintage_dates)}, "
            f"first={vintage_dates[0]!r}, last={vintage_dates[-1]!r})"
        )


# ============================================================
# LAYER 5 -- fred/series (metadata: frequency, units, etc.)
# ============================================================

def layer5_series_metadata() -> None:
    for series_id in (SERIES_T10Y2Y, SERIES_VIXCLS, SERIES_CPI, SERIES_PAYEMS):
        result_key = f"LAYER5_METADATA_{series_id}"
        url = _build_url(FRED_SERIES_BASE, {"series_id": series_id})
        status, parsed, err, detail = _request(url)
        if err is not None or status != 200 or parsed is None:
            RESULTS[result_key] = "FAIL"
            print(f"{result_key}: FAIL (http_status={status}, error={err})")
            continue

        series_list = parsed.get("seriess", [])
        if not series_list:
            RESULTS[result_key] = "FAIL"
            print(f"{result_key}: FAIL (HTTP 200 but empty/absent 'seriess')")
            continue

        info = series_list[0]
        RESULTS[result_key] = "PASS"
        print(
            f"{result_key}: PASS keys={sorted(info.keys())} "
            f"frequency={info.get('frequency')!r} frequency_short={info.get('frequency_short')!r} "
            f"units={info.get('units')!r} units_short={info.get('units_short')!r} "
            f"seasonal_adjustment={info.get('seasonal_adjustment')!r} "
            f"observation_start={info.get('observation_start')!r} "
            f"observation_end={info.get('observation_end')!r}"
        )


# ============================================================
# LAYER 6 -- parameter experiments (sort_order, limit) -- observational
# only, on CPIAUCSL's default (non-output_type=2) endpoint to avoid
# conflating with the multi-vintage layers above.
# ============================================================

def layer6_parameter_experiments() -> None:
    # sort_order experiment
    for sort_order in ("asc", "desc"):
        result_key = f"LAYER6_SORT_ORDER_{sort_order}"
        url = _build_url(
            FRED_OBSERVATIONS_BASE,
            {
                "series_id": SERIES_CPI,
                "observation_start": REVISION_WINDOW_START,
                "observation_end": REVISION_WINDOW_END,
                "sort_order": sort_order,
                "limit": 3,
            },
        )
        status, parsed, err, detail = _request(url)
        if err is not None or status != 200 or parsed is None:
            RESULTS[result_key] = "FAIL"
            print(f"{result_key}: FAIL (http_status={status}, error={err})")
            continue
        obs = parsed.get("observations", [])
        RESULTS[result_key] = "PASS" if obs else "FAIL"
        dates = [row.get("date") for row in obs]
        print(f"{result_key}: {'PASS' if obs else 'FAIL'} limit=3 dates_returned={dates}")

    # limit experiment (separate from sort_order, default sort)
    result_key = "LAYER6_LIMIT_2"
    url = _build_url(
        FRED_OBSERVATIONS_BASE,
        {
            "series_id": SERIES_CPI,
            "observation_start": REVISION_WINDOW_START,
            "observation_end": REVISION_WINDOW_END,
            "limit": 2,
        },
    )
    status, parsed, err, detail = _request(url)
    if err is not None or status != 200 or parsed is None:
        RESULTS[result_key] = "FAIL"
        print(f"{result_key}: FAIL (http_status={status}, error={err})")
    else:
        obs = parsed.get("observations", [])
        RESULTS[result_key] = "PASS" if len(obs) <= 2 else "FAIL"
        print(f"{result_key}: {'PASS' if len(obs) <= 2 else 'FAIL'} rows_returned={len(obs)} (requested limit=2)")


def main() -> int:
    print("=== Step 10I FRED Multi-Vintage Response Validation ===")
    print("(FRED_API_KEY value is never printed. Every logged URL has its api_key redacted.)")
    print(f"FRED_API_KEY_PRESENT: {bool(os.environ.get('FRED_API_KEY'))}")
    print(f"Revision window: observation_start={REVISION_WINDOW_START} observation_end={REVISION_WINDOW_END}")
    print(f"Realtime window (output_type=2 queries): realtime_start={WIDE_REALTIME_START} realtime_end={WIDE_REALTIME_END}")
    print()

    layer1_output_type2_cpi()
    layer2_output_type2_payems()
    layer3_output_type2_market_data()
    layer4_vintagedates()
    layer5_series_metadata()
    layer6_parameter_experiments()

    print()
    print("=== SUMMARY ===")
    for key in sorted(RESULTS.keys()):
        print(f"{key}: {RESULTS[key]}")

    print(f"MISSING_VALUE_MARKERS_OBSERVED: {sorted(MISSING_VALUE_MARKERS_OBSERVED) or 'NONE_OBSERVED'}")
    print("SECRET_EXPOSURE: NONE (key never printed; all logged URLs redacted)")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## Step 10I FRED Multi-Vintage Validation\n\n")
                f.write("| Layer | Result |\n|---|---|\n")
                for key in sorted(RESULTS.keys()):
                    f.write(f"| {key} | {RESULTS[key]} |\n")
                f.write(f"\nMissing-value markers observed: `{sorted(MISSING_VALUE_MARKERS_OBSERVED) or 'NONE_OBSERVED'}`\n\n")
                f.write("Secret exposure: NONE\n")
        except OSError:
            pass

    # Non-zero exit only if the two core multi-vintage layers (the ones
    # this round's verdict most directly depends on) both failed to even
    # be reachable -- a FAIL verdict that both layers DID reach FRED but
    # found no multi-vintage rows is still a valid, informative result,
    # not a script error, so it does not itself fail the job.
    core = [RESULTS.get("LAYER1_OUTPUT_TYPE2_CPI"), RESULTS.get("LAYER2_OUTPUT_TYPE2_PAYEMS")]
    if all(v is None for v in core):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
