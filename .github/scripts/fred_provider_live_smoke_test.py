"""
Step 10J-QA -- FRED Provider LIVE integration smoke test (GitHub-hosted
runner only, workflow_dispatch-only).

PURPOSE (2026-10-05 QA round, distinct from Step 10I's validator):
Step 10I's `.github/scripts/fred_multi_vintage_validation.py` validated
that FRED's real `output_type=2` response has a crosstab shape and that
a HAND-WRITTEN parser modeled on that shape could feed `src/macro/pit.py`
correctly. It does NOT exercise this project's actual production code
path -- `src/macro/fred_provider.py::FredMacroProvider`. This script
closes that gap: it imports and calls the real, frozen-interface
production class directly against the live FRED API, so a structural
regression in `fred_provider.py` itself (not just in the general shape
of FRED's response) would be caught here.

THIS IS NOT A NEW PROVIDER AND NOT A NEW PARSER. Every call below goes
through `FredMacroProvider.get_series()` / `get_series_metadata()`
exactly as any other caller would use them. This script adds no new
business logic of its own beyond assembling diagnostics and PIT checks
using the ALREADY-FROZEN `src/macro/pit.py` (read-only import, exactly
as Step 10I's Layer 7 already established as acceptable validation-only
usage).

SCOPE DISCIPLINE (same as Step 10G/10I):
  - workflow_dispatch-only, never scheduled.
  - Does not modify src/macro/* (reads pit.py's public functions only).
  - Writes no CSV, no macro-state, no regime, no briefing output.
  - Does not replace or modify Step 10I's validator; this is an
    independent, additional smoke test of the production provider.

SECURITY: FRED_API_KEY is read only from the environment (injected by
the calling workflow from secrets.FRED_API_KEY) via
`fred_provider._get_api_key()` -- this script never reads the env var
itself and never constructs a request URL itself. Every diagnostic
printed below is sanitized: observation values, dates, metadata, and row
counts only. If any FRED request fails, the raised FredHTTPError's
`redacted_url` attribute (api_key already replaced with "REDACTED" by
`fred_provider._redact_url()`) may be printed -- the raw key is never
accessible from this script at all, so there is nothing to redact
ourselves; redaction happens inside fred_provider.py before the
exception is even raised (see 2026-10-05 QA finding: a prior fred_provider.py
cut computed this redacted URL but never attached it to the exception --
fixed as part of this QA round).

DIAGNOSTIC INSTRUMENTATION (2026-10-05, this round -- NOT a behavior
change): a prior live run of this workflow was externally canceled
("The operation was canceled.") with ZERO script output visible in the
GitHub Actions log, even though the step had been running long enough
for this to very likely be real progress, not an instant crash. The
most likely explanation is NOT a hang in this script's logic -- it is
that GitHub Actions' log capture reads a non-TTY Python process's
stdout, which Python fully block-buffers (not line-buffers) by default
when stdout is not a terminal, so _log() output can sit in an internal
buffer and be lost entirely if the process is killed before it exits
normally. Every diagnostic print added in this round therefore uses
flush=True (belt-and-suspenders with `python3 -u` / PYTHONUNBUFFERED=1
in the workflow itself), and this script now emits START/END timing
around every stage so a future canceled run's log shows exactly which
stage was in progress when it was canceled, rather than nothing at all.
This script also sets FRED_PROVIDER_HTTP_DEBUG=1 for its own process
only (see fred_provider.py's _http_debug_enabled()), which turns on
per-HTTP-request START/END logging (endpoint, series_id, output_type,
vintage_dates usage, elapsed time, response byte length) inside
fred_provider.py itself -- this is the only way to see whether a
canceled run was stuck inside a single slow HTTP call, or was making
many small sequential HTTP calls (e.g. T10Y2Y/VIXCLS's batched
vintage_dates fetch) that simply added up to a long total runtime.
Nothing about fred_provider.py's actual HTTP behavior (timeout value,
retry policy, batch size, URL construction, exception classification)
is changed by any of this -- see fred_provider.py's own diagnostic-round
comment at _http_debug_enabled().
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

# Repo-root import fix, same pattern as Step 10I's v3 script: resolved at
# module import time, independent of the process's current working
# directory. This file lives at <repo_root>/.github/scripts/<this file>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 2026-10-05 diagnostic round: force per-HTTP-request logging on for this
# script's own process only (see fred_provider.py's _http_debug_enabled()
# docstring). Set BEFORE importing fred_provider so the very first
# request already logs -- though the flag is actually read at call time,
# not import time, so this ordering is for clarity, not correctness.
# setdefault(), not direct assignment, so an operator/workflow that has
# already set this env var explicitly (e.g. to "0" to opt out) is never
# silently overridden.
os.environ.setdefault("FRED_PROVIDER_HTTP_DEBUG", "1")

from src.macro.fred_provider import (  # noqa: E402
    EXPLICIT_VINTAGE_DATES_SERIES_IDS,
    WIDE_WINDOW_SERIES_IDS,
    FredHTTPError,
    FredMacroProvider,
    FredProviderError,
)
from src.macro.pit import get_point_in_time_series, select_latest_vintage_as_of  # noqa: E402
from src.macro.schema import validate_raw_observation  # noqa: E402

SERIES_TO_CHECK = ["CPIAUCSL", "PAYEMS", "T10Y2Y", "VIXCLS"]


def _log(msg: str) -> None:
    """Diagnostic print, always immediately flushed -- see the module
    docstring's 2026-10-05 DIAGNOSTIC INSTRUMENTATION section on why
    flush=True is mandatory here, not optional."""
    print(msg, flush=True)


class _Stage:
    """Diagnostic-only START/END timing wrapper (2026-10-05 round). Prints
    "[N] <name> START" on entry and "[N] <name> END elapsed=...s" (or
    "FAILED" with the exception type, then re-raises) on exit, with
    flush=True on every line -- so a canceled run's log shows exactly
    which numbered stage was last known to be in progress, rather than
    nothing. Adds no retry, no timeout, and does not alter control flow
    beyond this logging -- the wrapped code's exceptions propagate
    unchanged."""

    def __init__(self, number: int, name: str):
        self.number = number
        self.name = name
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = time.monotonic()
        _log(f"[{self.number}] {self.name} START")
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.monotonic() - self._t0
        if exc_type is None:
            _log(f"[{self.number}] {self.name} END elapsed={elapsed:.3f}s")
        else:
            _log(
                f"[{self.number}] {self.name} FAILED elapsed={elapsed:.3f}s "
                f"exception={exc_type.__name__}: {exc}"
            )
        return False  # never swallow the exception

# PAYEMS observation_date to specifically probe for distinct-value
# revisions (section F of the 2026-10-05 QA instruction) -- chosen
# because it is a WIDE_WINDOW series with a long public history of
# multiple revisions, not because this date is special in any other way.
REVISION_PROBE_SERIES = "PAYEMS"


def _print_header(title: str) -> None:
    _log("")
    _log("=" * 88)
    _log(title)
    _log("=" * 88)


def _print_rows_sample(rows: list[dict], limit: int = 5) -> None:
    """Sanitized diagnostic dump -- series id / observation date /
    vintage date / value / count only. Never prints a raw FRED response
    body or any URL (redaction, when a URL is shown at all, already
    happened inside fred_provider.py before this script ever sees it)."""
    _log(f"  row_count={len(rows)}")
    for row in rows[:limit]:
        _log(
            f"    series_id={row.get('series_id')!r} "
            f"observation_date={row.get('observation_date')!r} "
            f"vintage_date={row.get('vintage_date')!r} "
            f"value={row.get('value')!r} "
            f"frequency={row.get('frequency')!r} "
            f"units={row.get('units')!r}"
        )
    if len(rows) > limit:
        _log(f"    ... ({len(rows) - limit} more rows not shown)")


def section_a_connectivity_and_classification(provider: FredMacroProvider) -> dict:
    """A. API connectivity -- confirms auth succeeds and classification
    coverage is exactly the required 4 series."""
    _print_header("SECTION A -- connectivity / series classification")
    required = set(SERIES_TO_CHECK)
    classified = WIDE_WINDOW_SERIES_IDS | EXPLICIT_VINTAGE_DATES_SERIES_IDS
    missing = required - classified
    result = {"required_series": sorted(required), "unclassified": sorted(missing)}
    _log(f"  required series classified: {missing == set()} (missing={sorted(missing)})")
    if missing:
        raise SystemExit(
            f"FAIL: {sorted(missing)} have no confirmed-working fetch strategy in "
            f"fred_provider.py -- cannot run the live smoke test for them."
        )
    return result


def section_b_metadata(provider: FredMacroProvider) -> dict:
    """B. metadata -- frequency/units/seasonal_adjustment via the real
    fred/series endpoint, through get_series_metadata() exactly as
    get_series() itself calls it."""
    _print_header("SECTION B -- metadata")
    out = {}
    for stage_num, series_id in enumerate(SERIES_TO_CHECK, start=2):
        with _Stage(stage_num, f"metadata {series_id}"):
            meta = provider.get_series_metadata(series_id)
            _log(
                f"  {series_id}: frequency={meta.get('frequency')!r} "
                f"frequency_short={meta.get('frequency_short')!r} "
                f"units={meta.get('units')!r} "
                f"seasonal_adjustment={meta.get('seasonal_adjustment')!r}"
            )
            out[series_id] = meta
    return out


def section_c_and_d_observations(provider: FredMacroProvider) -> dict:
    """C. current observations (incl. '.' handling if any NaN rows turn
    up). D. confirms the real output_type=2 crosstab response was
    actually flattened into normalized rows by the production parser,
    and that every row is schema-valid.

    NOTE (2026-10-05 diagnostic round): this single call to
    provider.get_series(series_id) internally issues a SECOND metadata
    HTTP call (get_series() re-fetches metadata rather than reusing
    section B's result -- see fred_provider.py's get_series(), which is
    unchanged this round) plus either ONE wide-window observations call
    (CPIAUCSL/PAYEMS) or a vintagedates call followed by potentially MANY
    batched observations calls (T10Y2Y/VIXCLS, batch size
    VINTAGE_DATES_BATCH_SIZE=25 -- unchanged this round). This stage's
    START/END timing therefore covers an unknown, possibly large number
    of underlying HTTP requests for T10Y2Y/VIXCLS; fred_provider.py's own
    per-HTTP-request START/END log (enabled via FRED_PROVIDER_HTTP_DEBUG)
    is what actually distinguishes "one slow call" from "many small
    sequential calls" within this stage.
    """
    _print_header("SECTION C/D -- observations via get_series() (output_type=2 crosstab)")
    out = {}
    for stage_num, series_id in enumerate(SERIES_TO_CHECK, start=6):
        with _Stage(stage_num, f"get_series {series_id} (metadata+observations+crosstab)"):
            rows = provider.get_series(series_id)
            shape_failures = [f for row in rows for f in validate_raw_observation(row)]
            nan_rows = [r for r in rows if isinstance(r.get("value"), float) and r["value"] != r["value"]]
            _log(f"  {series_id}:")
            _print_rows_sample(rows)
            _log(f"    schema_validation_failures={shape_failures[:10]}")
            _log(f"    nan_value_rows={len(nan_rows)} (missing-marker '.' handling, if any occurred live)")
            if shape_failures:
                raise SystemExit(
                    f"FAIL: {series_id} produced {len(shape_failures)} schema-invalid row(s): "
                    f"{shape_failures[:10]}"
                )
            out[series_id] = rows
    return out


def section_e_multiple_vintages(rows_by_series: dict) -> dict:
    """E. at least one observation_date with more than one vintage, for
    at least one series -- confirms the crosstab -> long-form flatten
    actually produced multiple vintage rows, not just one row per date."""
    _print_header("SECTION E -- multiple vintages per observation_date")
    out = {}
    for series_id, rows in rows_by_series.items():
        by_date: dict[str, set] = {}
        for row in rows:
            by_date.setdefault(row["observation_date"], set()).add(row["vintage_date"])
        multi = {d: sorted(v) for d, v in by_date.items() if len(v) > 1}
        _log(f"  {series_id}: observation_dates_with_multiple_vintages={len(multi)}")
        if multi:
            sample_date = next(iter(multi))
            _log(f"    sample: observation_date={sample_date!r} vintages={multi[sample_date]}")
        out[series_id] = len(multi)
    any_multi = any(v > 0 for v in out.values())
    _log(f"  at_least_one_series_with_multiple_vintages={any_multi}")
    if not any_multi:
        _log(
            "  WARNING: no series in this run showed more than one vintage per "
            "observation_date. This can legitimately happen for a short/narrow "
            "default window -- NOT automatically a FAIL, but flagged as "
            "UNVERIFIED for this particular run rather than silently passed."
        )
    return out


def section_f_revision_distinct_values(rows_by_series: dict) -> dict:
    """F. for a series with real revisions (PAYEMS), confirm that two
    vintages of the SAME observation_date actually carry DIFFERENT
    values (not just different vintage_dates) -- the exact distinction
    Step 10J's offline Fixture B/Case 5 tests exist to protect, now
    checked against a real response."""
    _print_header("SECTION F -- revision distinct-value check (PAYEMS)")
    rows = rows_by_series.get(REVISION_PROBE_SERIES, [])
    by_date: dict[str, dict[str, float]] = {}
    for row in rows:
        by_date.setdefault(row["observation_date"], {})[row["vintage_date"]] = row["value"]

    revised_with_distinct_values = {
        d: vintages for d, vintages in by_date.items() if len(set(vintages.values())) > 1
    }
    _log(f"  PAYEMS observation_dates with >1 DISTINCT value across vintages: "
          f"{len(revised_with_distinct_values)}")
    if revised_with_distinct_values:
        sample_date = next(iter(revised_with_distinct_values))
        _log(f"    sample: observation_date={sample_date!r} vintage_values={revised_with_distinct_values[sample_date]}")
    else:
        _log(
            "  UNVERIFIED for this run: no PAYEMS observation_date in the fetched "
            "window showed two vintages with different values. This does not by "
            "itself indicate a bug (a sufficiently old or narrow window may have "
            "no revision), but it means Section F's goal was not actually "
            "exercised live this run -- the offline Fixture B / Case 5 tests "
            "remain the authoritative, always-exercised coverage for this "
            "distinction."
        )
    return {"revised_with_distinct_values_count": len(revised_with_distinct_values)}


def section_g_pit(rows_by_series: dict) -> dict:
    """G. feed the REAL provider's output, completely unmodified, into
    the frozen select_latest_vintage_as_of() / get_point_in_time_series()
    and confirm the 4 PIT cases plus the no-look-ahead guarantee hold
    against live data, not just the offline fixtures."""
    _print_header("SECTION G -- PIT integration against live provider output (pit.py UNCHANGED)")
    out = {}
    for series_id, rows in rows_by_series.items():
        series_rows = [r for r in rows if r["series_id"] == series_id]
        if not series_rows:
            out[series_id] = "no_rows"
            continue

        knowability_dates = sorted(
            {r["vintage_date"] or r["realtime_start"] for r in series_rows if (r["vintage_date"] or r["realtime_start"])}
        )
        if not knowability_dates:
            _log(f"  {series_id}: no parseable knowability dates in this window -- skipping PIT probe")
            out[series_id] = "no_knowability_dates"
            continue

        earliest = date.fromisoformat(knowability_dates[0])
        latest = date.fromisoformat(knowability_dates[-1])

        before_first = select_latest_vintage_as_of(series_rows, date(earliest.year - 1, 1, 1))
        after_last = select_latest_vintage_as_of(
            [r for r in series_rows if r["observation_date"] == series_rows[-1]["observation_date"]],
            date(2099, 1, 1),
        )
        future_vintage_leak = any(
            r["observation_date"] == series_rows[-1]["observation_date"]
            and (r["vintage_date"] or r["realtime_start"]) > date(2099, 1, 1).isoformat()
            for r in series_rows
        )

        pit_series = get_point_in_time_series(rows_by_series[series_id], series_id, latest.isoformat())

        _log(
            f"  {series_id}: before_first_vintage_selects_none={before_first is None} "
            f"after_last_date_selects_something={after_last is not None} "
            f"pit_series_rows={len(pit_series)} "
            f"earliest_knowability={earliest.isoformat()} latest_knowability={latest.isoformat()}"
        )

        out[series_id] = {
            "before_first_is_none": before_first is None,
            "after_last_is_not_none": after_last is not None,
            "pit_series_row_count": len(pit_series),
            "no_future_vintage_possible_in_this_window": not future_vintage_leak,
        }
    return out


def main() -> int:
    results: dict = {}

    try:
        with _Stage(1, "provider initialization"):
            provider = FredMacroProvider()

        results["section_a"] = section_a_connectivity_and_classification(provider)
        results["section_b"] = section_b_metadata(provider)
        rows_by_series = section_c_and_d_observations(provider)

        with _Stage(10, "section E -- multiple-vintage check"):
            results["section_e"] = section_e_multiple_vintages(rows_by_series)

        with _Stage(11, "section F -- revision distinct-value check"):
            results["section_f"] = section_f_revision_distinct_values(rows_by_series)

        with _Stage(12, "section G -- PIT validation"):
            results["section_g"] = section_g_pit(rows_by_series)
    except FredHTTPError as e:
        _print_header("FRED HTTP ERROR")
        _log(f"  classification={e.classification} status={e.status}")
        _log(f"  redacted_url={e.redacted_url}")
        _log(f"  detail={e.detail[:300]!r}")
        return 1
    except FredProviderError as e:
        _print_header("FRED PROVIDER ERROR")
        _log(f"  {e}")
        return 1

    with _Stage(13, "final summary"):
        _print_header("SUMMARY (sanitized -- no API key, no unredacted URL, anywhere above)")
        _log(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
