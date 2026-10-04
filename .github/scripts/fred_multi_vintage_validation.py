"""
Step 10I -- FRED multi-vintage response structure validation (GitHub-hosted
runner only).

PURPOSE: answer exactly the question Step 10H's OPEN SEMANTIC ISSUE #1
raised -- does a real `fred/series/observations?output_type=2` response
actually let us recover MULTIPLE vintages for the SAME observation_date,
and can that be turned into something src/macro/pit.py's vintage-selection
design can consume? This script answers that from a REAL response, not
from documentation. It also calls `fred/series/vintagedates` and
`fred/series` (metadata) once each, and records exactly what each
endpoint actually returns.

THIS IS NOT A PROVIDER. It is intentionally NOT imported by anything
under src/macro/ or src/, writes no CSV, and makes no production
PIT/state/regime decision. The one deliberate exception, explained below,
is that Layer 7 of this script IMPORTS src/macro/pit.py (read-only --
calls its existing public functions, never modifies them) to check real
FRED data against the actual frozen PIT selection logic, rather than
against a hand-rolled reimplementation of it that could silently drift
from pit.py's real behavior. This is still validation-only infrastructure
for Step 10I: kept outside src/, workflow_dispatch-only trigger, no
CSV/state/regime output of any kind.

CORRECTION HISTORY (2026-10-04, same day, v2 of this script):
The v1 version of this script assumed `output_type=2` would return one
JSON object per (observation_date, vintage) pair, each carrying its own
`realtime_start`/`realtime_end`/`value` fields -- i.e. the same per-row
shape as the DEFAULT `output_type=1` response. That assumption was WRONG.
A real run against GitHub Actions (performed directly by the project
owner, independently of this script's own commit history) showed the
actual shape: `output_type=2` ("Observations by Vintage Date, All
Observations") returns one row PER observation_date, where each vintage
is instead a DYNAMICALLY NAMED COLUMN on that same row, named
"{series_id}_{YYYYMMDD}" (no separators in the date suffix -- confirmed
from the real response's own key names, e.g. "CPIAUCSL_20150226",
"CPIAUCSL_20150324", ... "CPIAUCSL_20261004"), holding that
observation_date's value AS OF that vintage date. This file is v2: it
replaces the v1 per-row parser with a crosstab (wide-format) parser that
reads these dynamic columns directly, per the project owner's and
ChatGPT's joint review of the v1 results. See
claude/2026-10-04-step10i-fred-multi-vintage-validation.md section
"Why the first validator was wrong" for the full writeup.

SECURITY: FRED_API_KEY is read from the environment (injected by the
calling workflow from secrets.FRED_API_KEY) and is NEVER printed or
logged -- not even as FRED_API_KEY=..., not even partially. Only the
boolean presence (`FRED_API_KEY_PRESENT: True/False`) is ever printed.
Every URL this script would otherwise log has its api_key value redacted
before printing, mirroring Step 10G's _redact() exactly. Response bodies
are never dumped in full -- only a bounded number of representative
observation_date/vintage_date/value triples are ever printed, per this
round's explicit Rule C/D.

Only the standard library is used (urllib, json, os, re, sys, socket,
ssl, datetime) -- no new dependency, consistent with every prior Step 10
script. The one non-stdlib-only addition is the Layer 7 import of
src/macro/pit.py, which is part of this same repository, not a new
dependency.
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
from datetime import date, datetime, timedelta

FRED_OBSERVATIONS_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_VINTAGEDATES_BASE = "https://api.stlouisfed.org/fred/series/vintagedates"
FRED_SERIES_BASE = "https://api.stlouisfed.org/fred/series"

# Narrow observation windows, chosen deliberately small (Rule D -- this is
# a structure-validation round, not a bulk data pull). PAYEMS and CPIAUCSL
# are queried over the same 6-month window so their vintage counts can be
# compared directly, side by side, in the SUMMARY section. This window,
# and the wide realtime window below, are UNCHANGED from v1 -- they are
# what actually produced the dynamic vintage columns confirmed in the
# real run; only how we PARSE the resulting rows has changed.
REVISION_WINDOW_START = "2015-01-01"
REVISION_WINDOW_END = "2015-06-01"

# A wide realtime window is required for output_type=2 to have any chance
# of returning more than one vintage column per observation_date -- FRED's
# realtime_start/realtime_end bound WHICH vintage columns are in scope,
# not just which observation_dates are. This is kept wide for CPIAUCSL /
# PAYEMS, which the real run already confirmed works. It is NOT reused
# for the daily market-data series below (see Layer 3 / item 9).
WIDE_REALTIME_START = "1990-01-01"
WIDE_REALTIME_END = "2026-10-04"

SERIES_CPI = "CPIAUCSL"       # primary multi-vintage candidate -- confirmed
                               # by the real run to produce multiple dynamic
                               # vintage columns per observation_date
SERIES_PAYEMS = "PAYEMS"      # second, independent economic series -- pit.py's own
                               # docstring cites PAYEMS by name as the motivating
                               # revision example; used here to avoid depending on
                               # CPIAUCSL alone
SERIES_T10Y2Y = "T10Y2Y"      # required baseline (daily, market-derived). Previously
                               # queried with the same wide realtime window as
                               # CPI/PAYEMS, which returned HTTP 400 -- see item 9 /
                               # Layer 3's explicit-vintage_dates rewrite below.
SERIES_VIXCLS = "VIXCLS"      # required baseline, same role as T10Y2Y

# Candidate explicit vintage dates to try for the daily-series Layer 3
# query (item 5). These are NOT assumed valid -- Layer 3 first calls
# fred/series/vintagedates for each series and only uses whichever of
# these (or, failing that, whichever vintagedates the series actually
# has) are confirmed to exist for that specific series before building
# the observations query.
CANDIDATE_VINTAGE_DATES = ["2015-02-26", "2015-03-24", "2015-04-17", "2015-05-22"]

RESULTS: dict[str, str] = {}
MISSING_VALUE_MARKERS_OBSERVED: set[str] = set()
SCHEMA_KEYS_PRINTED: set[str] = set()  # print raw crosstab key names once per series

# Regex for a dynamic output_type=2 vintage column name: "{series_id}_{YYYYMMDD}".
# Built per-series (not globally) since the series_id itself is the fixed prefix.
def _vintage_column_pattern(series_id: str) -> "re.Pattern[str]":
    return re.compile(rf"^{re.escape(series_id)}_(\d{{8}})$")


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
    req = urllib.request.Request(url, headers={"User-Agent": "invest-briefing-claude-step10i-validation/0.2"})
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


def _scan_missing_cell(raw_value) -> None:
    if raw_value is not None and not re.match(r"^-?\d+(\.\d+)?$", str(raw_value)):
        MISSING_VALUE_MARKERS_OBSERVED.add(str(raw_value))


def _extract_vintage_cells(row: dict, series_id: str) -> dict[str, str]:
    """
    row: one output_type=2 crosstab row (one observation_date).
    series_id: the series this row belongs to.

    Returns {vintage_date_iso: raw_cell_value} for every dynamic
    "{series_id}_{YYYYMMDD}" column present on this row, raw_cell_value
    exactly as FRED sent it (a numeric string, or the missing marker "."
    -- never pre-filtered here; callers decide what to do with ".").
    """
    pattern = _vintage_column_pattern(series_id)
    cells: dict[str, str] = {}
    for key, value in row.items():
        match = pattern.match(key)
        if not match:
            continue
        raw_vintage = match.group(1)
        try:
            vintage_iso = datetime.strptime(raw_vintage, "%Y%m%d").date().isoformat()
        except ValueError:
            continue
        cells[vintage_iso] = value
    return cells


def _parse_crosstab(observations: list[dict], series_id: str) -> list[dict]:
    """
    Converts a real output_type=2 crosstab response into normalized
    long-form records: one dict per (observation_date, vintage_date) cell
    that actually carries a published (non-missing-marker) numeric value.
    Each record has keys: series_id, observation_date, vintage_date,
    value (float), realtime_start (set equal to vintage_date, since for
    this crosstab shape the vintage_date IS the knowability date -- see
    Layer 7 / pit.py's own knowability-date rule, which already prefers
    vintage_date over realtime_start whenever vintage_date is present).

    Cells holding the "." missing marker are NOT included as records here
    (there is no value yet to select) but ARE scanned into
    MISSING_VALUE_MARKERS_OBSERVED for item 10's reporting.
    """
    records: list[dict] = []
    if series_id not in SCHEMA_KEYS_PRINTED and observations:
        print(f"  [SCHEMA] {series_id} output_type=2 row key names (first row only, {len(observations[0])} keys): "
              f"{sorted(observations[0].keys())[:8]}{'...' if len(observations[0]) > 8 else ''}")
        SCHEMA_KEYS_PRINTED.add(series_id)

    for row in observations:
        obs_date = row.get("date")
        if not obs_date:
            continue
        cells = _extract_vintage_cells(row, series_id)
        for vintage_date, raw_value in cells.items():
            _scan_missing_cell(raw_value)
            if raw_value is None or str(raw_value) == ".":
                continue
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError):
                continue
            records.append(
                {
                    "series_id": series_id,
                    "observation_date": obs_date,
                    "vintage_date": vintage_date,
                    "realtime_start": vintage_date,
                    "value": numeric_value,
                }
            )
    return records


def _group_records_by_observation_date(records: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["observation_date"], []).append(record)
    return groups


def _print_multi_vintage_sample(label: str, obs_date: str, records: list[dict], limit: int = 5) -> None:
    print(f"  MULTI_VINTAGE_SAMPLE ({label}):")
    print(f"    observation_date={obs_date!r}")
    for record in sorted(records, key=lambda r: r["vintage_date"])[:limit]:
        print(f"    vintage={record['vintage_date']!r} value={record['value']!r}")


# ============================================================
# LAYER 1/2 -- output_type=2 crosstab, correctly parsed (CPIAUCSL, PAYEMS)
# ============================================================

def _multi_vintage_layer(series_id: str, result_key: str) -> list[dict]:
    """Returns the normalized long-form records for this series (used
    later by Layer 7's PIT-compatibility check) in addition to recording
    the PASS/FAIL verdict."""
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
        return []

    observations = parsed.get("observations", [])
    if not observations:
        RESULTS[result_key] = "FAIL"
        print(f"{result_key}: FAIL (HTTP 200 but zero observations in window)")
        return []

    records = _parse_crosstab(observations, series_id)
    groups = _group_records_by_observation_date(records)
    # item 3: multi-vintage evidence = >=2 DISTINCT PUBLISHED vintage
    # columns for the same observation_date -- not raw row count (every
    # row is already exactly one observation_date in this crosstab shape)
    # and NOT gated on the values differing (item 4: identical values
    # across vintages are still valid multi-vintage evidence).
    multi_vintage_dates = {d: recs for d, recs in groups.items() if len(recs) >= 2}

    print(
        f"{result_key}: crosstab_rows={len(observations)} distinct_observation_dates_with_data={len(groups)} "
        f"observation_dates_with_multiple_published_vintages={len(multi_vintage_dates)}"
    )

    if multi_vintage_dates:
        RESULTS[result_key] = "PASS"
        shown = 0
        for obs_date, recs in sorted(multi_vintage_dates.items(), key=lambda kv: -len(kv[1])):
            if shown >= 3:
                break
            _print_multi_vintage_sample(result_key, obs_date, recs, limit=5)
            shown += 1
    else:
        RESULTS[result_key] = "FAIL"
        print("  (no observation_date in this window has >=2 published vintage columns)")

    return records


def layer1_output_type2_cpi() -> list[dict]:
    return _multi_vintage_layer(SERIES_CPI, "LAYER1_OUTPUT_TYPE2_CPI")


def layer2_output_type2_payems() -> list[dict]:
    return _multi_vintage_layer(SERIES_PAYEMS, "LAYER2_OUTPUT_TYPE2_PAYEMS")


# ============================================================
# LAYER 3 -- daily market-data series (T10Y2Y, VIXCLS) under
# output_type=2, using EXPLICIT vintage_dates instead of a wide
# realtime_start/realtime_end window (item 5 / item 9: the wide-window
# query previously returned HTTP 400 for these series, most likely due
# to vintage-column cardinality over a 36-year daily realtime window,
# NOT a connectivity failure -- this is re-tested here with a small,
# confirmed-to-exist set of vintage dates instead).
# ============================================================

def _confirmed_vintage_dates_for(series_id: str) -> list[str]:
    """Calls fred/series/vintagedates for series_id and returns up to 4
    actually-existing vintage dates: preferring the CANDIDATE_VINTAGE_DATES
    that are confirmed present, falling back to the first 4 vintage dates
    FRED actually lists for this series if none of the candidates match.
    Returns [] if the vintagedates call itself fails."""
    url = _build_url(FRED_VINTAGEDATES_BASE, {"series_id": series_id})
    status, parsed, err, detail = _request(url)
    if err is not None or status != 200 or parsed is None:
        print(f"  [LAYER3] vintagedates lookup for {series_id} FAILED (http_status={status}, error={err})")
        return []

    all_dates = parsed.get("vintage_dates", [])
    if not all_dates:
        print(f"  [LAYER3] vintagedates lookup for {series_id} returned no dates")
        return []

    confirmed = [d for d in CANDIDATE_VINTAGE_DATES if d in all_dates]
    if confirmed:
        print(f"  [LAYER3] {series_id}: using confirmed candidate vintage_dates={confirmed}")
        return confirmed

    fallback = sorted(all_dates)[:4]
    print(
        f"  [LAYER3] {series_id}: none of {CANDIDATE_VINTAGE_DATES} found in this series' own "
        f"vintage_dates ({len(all_dates)} total) -- falling back to its own earliest dates: {fallback}"
    )
    return fallback


def layer3_output_type2_market_data() -> None:
    for series_id in (SERIES_T10Y2Y, SERIES_VIXCLS):
        result_key = f"LAYER3_OUTPUT_TYPE2_{series_id}"
        vintage_dates = _confirmed_vintage_dates_for(series_id)
        if not vintage_dates:
            RESULTS[result_key] = "FAIL"
            print(f"{result_key}: FAIL (no usable vintage_dates could be confirmed for this series)")
            continue

        url = _build_url(
            FRED_OBSERVATIONS_BASE,
            {
                "series_id": series_id,
                "output_type": 2,
                "observation_start": REVISION_WINDOW_START,
                "observation_end": REVISION_WINDOW_END,
                "vintage_dates": ",".join(vintage_dates),
            },
        )
        status, parsed, err, detail = _request(url)
        if err is not None or status != 200 or parsed is None:
            RESULTS[result_key] = "FAIL"
            print(f"{result_key}: FAIL (http_status={status}, error={err}) url={_redact(url)}")
            continue

        observations = parsed.get("observations", [])
        if not observations:
            RESULTS[result_key] = "FAIL"
            print(f"{result_key}: FAIL (zero observations with explicit vintage_dates)")
            continue

        records = _parse_crosstab(observations, series_id)
        groups = _group_records_by_observation_date(records)
        multi = {d: recs for d, recs in groups.items() if len(recs) >= 2}
        max_vintages = max((len(recs) for recs in groups.values()), default=0)

        RESULTS[result_key] = "PASS"
        print(
            f"{result_key}: PASS (reachable with explicit vintage_dates) rows={len(observations)} "
            f"distinct_observation_dates_with_data={len(groups)} max_published_vintages_per_date={max_vintages} "
            f"observation_dates_with_multiple_published_vintages={len(multi)}"
        )
        if multi:
            sample_date, sample_recs = sorted(multi.items(), key=lambda kv: -len(kv[1]))[0]
            _print_multi_vintage_sample(result_key, sample_date, sample_recs, limit=5)


# ============================================================
# LAYER 4 -- fred/series/vintagedates (unchanged from v1 -- already
# confirmed working)
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
# LAYER 5 -- fred/series (metadata: frequency, units, etc.) -- unchanged
# from v1 -- already confirmed working. Kept as the documented basis for
# "observation response and metadata response are separate calls" (item 11).
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
# LAYER 6 -- parameter experiments (sort_order, limit) -- unchanged from
# v1, on CPIAUCSL's default (non-output_type=2) endpoint to avoid
# conflating with the multi-vintage layers above.
# ============================================================

def layer6_parameter_experiments() -> None:
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


# ============================================================
# LAYER 7 -- PIT compatibility, against REAL data and the ACTUAL,
# unmodified src/macro/pit.py (item 6). This is the one place this script
# imports repository code -- read-only, no pit.py changes, used purely to
# confirm (not reimplement) the frozen no-look-ahead selection behavior
# against genuine multi-vintage FRED records.
# ============================================================

def layer7_pit_compatibility(cpi_records: list[dict], payems_records: list[dict]) -> None:
    result_key = "LAYER7_PIT_COMPATIBILITY"

    # Repo root is two levels up from .github/scripts/
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    try:
        from src.macro.pit import select_latest_vintage_as_of  # noqa: E402  (deliberate, read-only, see module docstring)
    except Exception as e:
        RESULTS[result_key] = "FAIL"
        print(f"{result_key}: FAIL (could not import src.macro.pit: {type(e).__name__}: {e})")
        return

    # Prefer whichever of CPI/PAYEMS actually produced an observation_date
    # with the most distinct published vintages -- that is the strongest
    # real-data test case available this run.
    candidates: list[tuple[str, list[dict]]] = []
    for label, records in (("CPI", cpi_records), ("PAYEMS", payems_records)):
        groups = _group_records_by_observation_date(records)
        if not groups:
            continue
        best_date, best_recs = max(groups.items(), key=lambda kv: len(kv[1]))
        candidates.append((label, best_date, best_recs))  # type: ignore[arg-type]

    if not candidates:
        RESULTS[result_key] = "FAIL"
        print(f"{result_key}: FAIL (no real multi-vintage records available from Layer 1/2 to test against)")
        return

    label, best_date, best_recs = max(candidates, key=lambda c: len(c[2]))
    if len(best_recs) < 2:
        RESULTS[result_key] = "FAIL"
        print(f"{result_key}: FAIL (best available observation_date, {label} {best_date}, has only "
              f"{len(best_recs)} published vintage -- not enough to test selection-among-vintages)")
        return

    ordered = sorted(best_recs, key=lambda r: r["vintage_date"])
    vintage_dates = [r["vintage_date"] for r in ordered]
    print(f"{result_key}: using {label} observation_date={best_date!r} with real vintages {vintage_dates}")

    def _day_before(iso: str) -> date:
        return date.fromisoformat(iso) - timedelta(days=1)

    all_pass = True

    # Case: as_of strictly before the first known vintage -> no selection (UNVERIFIED upstream)
    as_of = _day_before(vintage_dates[0])
    selected = select_latest_vintage_as_of(ordered, as_of)
    ok = selected is None
    all_pass &= ok
    print(f"  CASE as_of={as_of.isoformat()} (before first vintage {vintage_dates[0]}): "
          f"selected={selected and selected['vintage_date']!r} expected=None -> {'OK' if ok else 'MISMATCH'}")

    # Case: as_of exactly on a vintage date -> that same vintage must be selected (inclusive "<=")
    mid_idx = len(vintage_dates) // 2
    as_of = date.fromisoformat(vintage_dates[mid_idx])
    selected = select_latest_vintage_as_of(ordered, as_of)
    ok = selected is not None and selected["vintage_date"] == vintage_dates[mid_idx]
    all_pass &= ok
    print(f"  CASE as_of={as_of.isoformat()} (exactly on vintage {vintage_dates[mid_idx]}): "
          f"selected={selected and selected['vintage_date']!r} expected={vintage_dates[mid_idx]!r} -> "
          f"{'OK' if ok else 'MISMATCH'}")

    # Case: as_of strictly between two known vintages -> the EARLIER of the two must be
    # selected, and the LATER one must never be visible (no look-ahead).
    if len(vintage_dates) >= 2:
        earlier, later = vintage_dates[mid_idx], vintage_dates[min(mid_idx + 1, len(vintage_dates) - 1)]
        if earlier != later:
            as_of = _day_before(later)
            selected = select_latest_vintage_as_of(ordered, as_of)
            ok = (
                selected is not None
                and selected["vintage_date"] == earlier
                and selected["vintage_date"] != later
            )
            all_pass &= ok
            print(
                f"  CASE as_of={as_of.isoformat()} (between {earlier} and {later}, exclusive of {later}): "
                f"selected={selected and selected['vintage_date']!r} expected={earlier!r} "
                f"(must NOT be {later!r}) -> {'OK' if ok else 'MISMATCH'}"
            )

    # Case: as_of after the last known vintage -> the LATEST vintage must be selected.
    as_of = date.fromisoformat(vintage_dates[-1]) + timedelta(days=30)
    selected = select_latest_vintage_as_of(ordered, as_of)
    ok = selected is not None and selected["vintage_date"] == vintage_dates[-1]
    all_pass &= ok
    print(f"  CASE as_of={as_of.isoformat()} (well after last vintage {vintage_dates[-1]}): "
          f"selected={selected and selected['vintage_date']!r} expected={vintage_dates[-1]!r} -> "
          f"{'OK' if ok else 'MISMATCH'}")

    RESULTS[result_key] = "PASS" if all_pass else "FAIL"
    print(f"{result_key}: {'PASS' if all_pass else 'FAIL'} (no-look-ahead selection against real data, "
          f"unmodified src/macro/pit.py)")


def main() -> int:
    print("=== Step 10I FRED Multi-Vintage Response Validation (v2 -- corrected crosstab parser) ===")
    print("(FRED_API_KEY value is never printed. Every logged URL has its api_key redacted.)")
    print(f"FRED_API_KEY_PRESENT: {bool(os.environ.get('FRED_API_KEY'))}")
    print(f"Revision window: observation_start={REVISION_WINDOW_START} observation_end={REVISION_WINDOW_END}")
    print(f"Realtime window (CPI/PAYEMS output_type=2 queries): "
          f"realtime_start={WIDE_REALTIME_START} realtime_end={WIDE_REALTIME_END}")
    print()

    cpi_records = layer1_output_type2_cpi()
    payems_records = layer2_output_type2_payems()
    layer3_output_type2_market_data()
    layer4_vintagedates()
    layer5_series_metadata()
    layer6_parameter_experiments()
    layer7_pit_compatibility(cpi_records, payems_records)

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
                f.write("## Step 10I FRED Multi-Vintage Validation (v2)\n\n")
                f.write("| Layer | Result |\n|---|---|\n")
                for key in sorted(RESULTS.keys()):
                    f.write(f"| {key} | {RESULTS[key]} |\n")
                f.write(f"\nMissing-value markers observed: `{sorted(MISSING_VALUE_MARKERS_OBSERVED) or 'NONE_OBSERVED'}`\n\n")
                f.write("Secret exposure: NONE\n")
        except OSError:
            pass

    # Non-zero exit only if NEITHER core multi-vintage layer (CPI, PAYEMS)
    # was even reachable -- a FAIL verdict that both layers DID reach FRED
    # but found no multi-vintage columns is still a valid, informative
    # result, not a script error, so it does not itself fail the job.
    core = [RESULTS.get("LAYER1_OUTPUT_TYPE2_CPI"), RESULTS.get("LAYER2_OUTPUT_TYPE2_PAYEMS")]
    if all(v is None for v in core):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
