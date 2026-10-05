"""
FRED Provider -- production MacroDataProvider implementation (Framework
v2.1 Step 10J, 2026-10-04: "FRED Provider + PIT Production Integration").

Builds the ONE new box this round is allowed to add, per Step 10H's
design (claude/2026-10-04-step10h-fred-provider-pit-integration-design.md)
and Step 10I's real, GitHub-Actions-validated empirical findings
(.github/scripts/fred_multi_vintage_validation.py v3, actually run against
the live FRED API):

    FRED API (HTTP/JSON)
       |
       v
    [THIS MODULE] FredMacroProvider.get_series()
       |  (returns list[dict] matching schema.RAW_OBSERVATION_FIELDS)
       v
    src/macro/pit.py                           <- UNCHANGED, frozen
       v
    src/macro/*_state.py                       <- UNCHANGED, frozen
       v
    src/macro/engine.py :: compute_macro_regime()   <- UNCHANGED, frozen

Nothing below the FredMacroProvider line in this diagram is touched by
this module. Every numeric/economic decision already made by pit.py /
transforms.py / *_state.py / regime.py / engine.py stays exactly as
frozen -- this module's only job is to produce schema-shaped raw
observations from real FRED responses, correctly.

What changed between Step 10H's design and this implementation
-----------------------------------------------------------------------
Step 10H's design (written before any real output_type=2 response had
been inspected) proposed a FIELD MAPPING TABLE assuming output_type=2
returns one JSON object per (observation_date, vintage) pair, each
carrying its own realtime_start/realtime_end/value fields -- i.e. the
same per-row shape as the default output_type=1 response. Step 10I's
real GitHub Actions run (against the live FRED API, with a real
FRED_API_KEY) proved that assumption WRONG: output_type=2 ("Observations
by Vintage Date, All Observations") actually returns ONE ROW PER
observation_date, where each vintage is a DYNAMICALLY NAMED COLUMN on
that row, named "{series_id}_{YYYYMMDD}" (no separators in the date
suffix -- e.g. "CPIAUCSL_20150226"). This module's crosstab parser
(_parse_crosstab_response, _extract_vintage_cells, _vintage_column_pattern
below) is therefore ported directly from the validated
.github/scripts/fred_multi_vintage_validation.py v3 script -- not
reimplemented from Step 10H's incorrect table -- because that script's
parser is the one actually exercised against real FRED data and
confirmed correct via Step 10I's Layer 1/2/3/7 results (crosstab parsing
PASS, PIT compatibility PASS on all 4 cases: CASE_BEFORE_FIRST,
CASE_EXACT_VINTAGE, CASE_BETWEEN_VINTAGES, CASE_AFTER_LAST, including
real PAYEMS revision VALUES, not just vintage dates).

Frozen contracts this module reads but never modifies
-----------------------------------------------------------------------
    src/macro/schema.py     -- RAW_OBSERVATION_FIELDS shape this module
                                must produce
    src/macro/pit.py        -- select_latest_vintage_as_of()'s semantics;
                                this module supplies candidates, never
                                selects among them itself (see
                                VINTAGE SELECTION POLICY below)
    src/macro/provider.py   -- the MacroDataProvider ABC this module's
                                FredMacroProvider implements, unchanged
    src/macro/transforms.py -- compare_exact()'s NaN=INVALID handling,
                                which is why missing values are mapped to
                                float("nan") here, not None or 0.0 or a
                                dropped row (see MISSING-VALUE POLICY)

No regime_confidence field is introduced anywhere in this module. No
Curve Window decision is made or implied anywhere in this module --
CURVE_WINDOW stays exactly DEFERRED in curve_state.py, untouched.

VINTAGE SELECTION POLICY (what this module decides vs. what it never
decides)
-----------------------------------------------------------------------
This module's get_series() is NOT PIT-aware -- exactly like
provider.py's existing InMemoryMacroProvider. It returns the FULL set of
raw observation rows (every vintage it was able to fetch within the
requested window), unfiltered by any as_of_date. Which vintage is
"correct" for a given as_of_date is decided ENTIRELY by
src/macro/pit.py's select_latest_vintage_as_of(), downstream, exactly as
frozen. This module never picks a vintage, never discards a vintage as
"too old" or "not recent enough", and never limits itself to the most
recent N vintages -- see VINTAGE_DATES FETCH POLICY below for the one
place a bounding decision IS made, and why it is a transport-layer
concern, not a data-availability policy.

VINTAGE_DATES FETCH POLICY (why this is NOT an arbitrary "last N
vintages" policy)
-----------------------------------------------------------------------
Two different real, Step 10I-confirmed FRED behaviors require two
different fetch strategies:

1. For series where output_type=2 + a realtime_start/realtime_end WINDOW
   works directly (CPIAUCSL, PAYEMS, and by the same confirmed mechanism
   the other monthly series this project needs -- CPILFESL, PCEPI,
   PCEPILFE, UNRATE, DFEDTARU, DFEDTARL): the provider passes the
   caller-supplied realtime_start/realtime_end straight through as the
   query's realtime window, exactly as Step 10I's Layer 1/2 did. The
   ENTIRE window the caller asked for is requested in one call -- no
   "most recent N vintages" narrowing happens here.

2. For series where that wide-window approach is confirmed to fail
   (T10Y2Y, VIXCLS -- Step 10I Layer 3's wide-window output_type=2 query
   returned HTTP 400, while an explicit `vintage_dates` parameter
   combined with output_type=2 was confirmed to work): the provider
   first calls fred/series/vintagedates to retrieve the COMPLETE list of
   vintage dates FRED has for that series, filters that list to the
   caller-supplied realtime_start/realtime_end window (never to "the
   most recent N" -- every vintage date inside the requested window is
   kept), and then fetches ALL of them via one or more output_type=2 +
   vintage_dates calls, batched into fixed-size chunks
   (_VINTAGE_DATES_BATCH_SIZE below) purely because neither Step 10F,
   10G, nor 10I ever empirically confirmed FRED's actual per-request
   limit on how many vintage_dates can be listed in a single call, and a
   single unbounded comma-joined list risks an unconfirmed URL-length or
   server-side cap. Batching here is a TRANSPORT mechanism to fetch the
   complete set across multiple HTTP calls -- it does not discard, skip,
   or prioritize any vintage date; every vintage date in the requested
   window is eventually fetched. This is flagged explicitly as an OPEN
   ITEM in the Step 10J report: the exact per-request vintage_dates
   limit remains unconfirmed, and _VINTAGE_DATES_BATCH_SIZE is a
   conservative, documented placeholder, not an empirically-tuned value.

MISSING-VALUE POLICY ("." -> NaN, never 0.0/None/dropped)
-----------------------------------------------------------------------
Confirmed live by Step 10G/10I: FRED's JSON responses use the literal
string "." for a missing/unavailable observation value. This module
converts any FRED value cell that is "." (or, defensively, any string
that does not parse as a finite decimal number) to float("nan") -- NEVER
0.0, NEVER None, and the row is NEVER silently dropped from the
returned list. This is a direct application of Step 10H section 5's
already-reasoned rule: transforms.compare_exact() already has a
complete, frozen NaN-handling path (NaN as either input -> INVALID,
which every *_state.py module already maps to its own UNVERIFIED-
equivalent value), so routing FRED's missing marker through NaN reuses
existing, already-correct logic rather than adding any new branch to a
frozen module.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from src.macro.provider import MacroDataProvider
from src.macro.schema import VALID_FREQUENCIES

SCHEMA_VERSION = "macro_fred_provider_v0.1"

# FRED's fred/series metadata endpoint returns "frequency" as a
# human-readable word (confirmed live, Step 10I Layer 5: e.g.
# "Monthly", "Daily") -- NOT the same vocabulary as
# src/macro/schema.py's VALID_FREQUENCIES ({"DAILY", "WEEKLY",
# "MONTHLY", "QUARTERLY", "ANNUAL"}, all upper-case, no "Biweekly"/
# "Semiannual" members). This maps FRED's word to the frozen schema
# vocabulary; a FRED frequency this project has never required (e.g.
# "Biweekly") intentionally has no entry here and
# _normalize_frequency() returns None for it -- a row with None
# frequency then fails schema.validate_raw_observation()'s required-
# field check, which is the correct fail-closed behavior (never guess
# a schema value this project has not actually validated it needs).
_FRED_FREQUENCY_TO_SCHEMA = {
    "Daily": "DAILY",
    "Weekly": "WEEKLY",
    "Monthly": "MONTHLY",
    "Quarterly": "QUARTERLY",
    "Annual": "ANNUAL",
}


def _normalize_frequency(fred_frequency: str | None) -> str | None:
    if fred_frequency is None:
        return None
    mapped = _FRED_FREQUENCY_TO_SCHEMA.get(fred_frequency)
    if mapped is not None:
        return mapped
    # Defensive fallback: some FRED responses may already use the
    # upper-case form directly -- accept it only if it is one of this
    # schema's own valid values, never a value this schema has not
    # already approved.
    upper = fred_frequency.upper()
    return upper if upper in VALID_FREQUENCIES else None

FRED_OBSERVATIONS_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_VINTAGEDATES_BASE = "https://api.stlouisfed.org/fred/series/vintagedates"
FRED_SERIES_BASE = "https://api.stlouisfed.org/fred/series"

DEFAULT_TIMEOUT_SECONDS = 30

# Default realtime window used when a caller does not supply
# realtime_start/realtime_end for a "wide window" series (policy #1
# above). FRED's earliest vintage data predates this by decades for
# every series this project uses; this is deliberately wide, not a
# narrowing policy -- it exists only so get_series() has SOME window to
# request when the caller passes none, mirroring
# InMemoryMacroProvider's own "no filter means no filter" default
# (provider.py's get_series() interface does not mandate a default --
# this is this concrete provider's own choice, documented here).
DEFAULT_REALTIME_START = "1900-01-01"
DEFAULT_REALTIME_END_SENTINEL = "9999-12-31"

# Conservative, DOCUMENTED placeholder for how many vintage_dates are
# requested in one output_type=2 call for "explicit vintage_dates"
# series (policy #2 above). NOT empirically confirmed against FRED's
# actual server-side limit in Step 10F/10G/10I -- flagged as an open
# item in the Step 10J report. Chosen conservatively (well below the 4
# dates Step 10I's Layer 3 confirmed working) to reduce the chance of
# silently exceeding an unconfirmed limit; this is a transport batch
# size, not a data-availability cap -- every vintage date in the
# requested window is still eventually fetched, across possibly many
# batches.
VINTAGE_DATES_BATCH_SIZE = 25

# Series confirmed (Step 10I Layer 1/2, and by direct mechanism-identity
# for the rest of this project's required series -- see this module's
# docstring policy #1) to work with a direct realtime_start/realtime_end
# window under output_type=2.
WIDE_WINDOW_SERIES_IDS = frozenset(
    {
        "PAYEMS",
        "UNRATE",
        "CPIAUCSL",
        "CPILFESL",
        "PCEPI",
        "PCEPILFE",
        "DFEDTARU",
        "DFEDTARL",
    }
)

# Series confirmed (Step 10I Layer 3) to return HTTP 400 under a wide
# realtime_start/realtime_end window with output_type=2, and confirmed
# working via an explicit vintage_dates parameter instead.
EXPLICIT_VINTAGE_DATES_SERIES_IDS = frozenset({"T10Y2Y", "VIXCLS"})

# Regex for a valid FRED numeric observation value. Anything not matching
# this (including, but not limited to, the "." missing marker) is
# treated as missing -- see MISSING-VALUE POLICY in the module docstring.
_NUMERIC_VALUE_PATTERN = re.compile(r"^-?\d+(\.\d+)?$")


# ============================================================
# ERRORS
# ============================================================


class FredProviderError(RuntimeError):
    """Base class for every error this module raises. Never raised with
    an API key, full request URL, or raw secret value in its message --
    see _redact_url()."""


class FredAPIKeyMissing(FredProviderError):
    """Raised when FRED_API_KEY is not set in the environment at the
    point a real request would be made. Never raised at module import
    time (mirrors src/sec/fetcher.py's get_user_agent() -- see that
    module's docstring on why import-time env-var checks break pytest
    collection for the whole suite)."""


class FredHTTPError(FredProviderError):
    """A classified HTTP/network/protocol failure. `classification` is
    one of the vocabulary already validated live in
    .github/scripts/fred_connectivity_smoke_test.py (Step 10G) and
    .github/scripts/fred_multi_vintage_validation.py (Step 10I):
    DNS_FAILURE, TLS_FAILURE, NETWORK_FAILURE, HTTP_FAILURE, AUTH_FAILED,
    RATE_LIMITED, SERIES_NOT_FOUND, INVALID_PARAMETER,
    RESPONSE_PARSE_FAILED, UNKNOWN."""

    def __init__(
        self,
        classification: str,
        detail: str,
        status: int | None = None,
        *,
        redacted_url: str | None = None,
    ):
        self.classification = classification
        self.detail = detail
        self.status = status
        self.redacted_url = redacted_url
        suffix = f" url={redacted_url}" if redacted_url else ""
        super().__init__(
            f"FRED request failed: {classification} (status={status}): {detail}{suffix}"
        )


class FredResponseShapeError(FredProviderError):
    """The HTTP request succeeded (status 200, valid JSON) but the
    response did not have the shape this module requires (e.g. missing
    'observations' / 'vintage_dates' / 'seriess' key). Distinguished from
    FredHTTPError because this is a data-shape problem on an otherwise-
    successful call, never conflated with a network/protocol failure."""


class FredMalformedVintageColumnError(FredProviderError):
    """Raised when a crosstab row's column name looks like it SHOULD be a
    vintage column for this series_id (same prefix) but does not parse
    as "{series_id}_{YYYYMMDD}". Per this round's explicit requirement
    (section 13, Fixture D) the provider must fail loudly here rather
    than silently skip or mis-parse a malformed column."""


# ============================================================
# SECRET HANDLING -- the only place FRED_API_KEY is ever read
# ============================================================


def _redact_url(url: str) -> str:
    """Redacts the api_key query parameter value. Mirrors
    .github/scripts/fred_connectivity_smoke_test.py's _redact() and
    fred_multi_vintage_validation.py's identical helper -- same
    discipline, reused, not reinvented."""
    return re.sub(r"(api_key=)[^&]+", r"\1REDACTED", url)


def _http_debug_enabled() -> bool:
    """2026-10-05 diagnostic-round addition (NOT a behavior change): gates
    the HTTP-level START/END logging below. Defaults to OFF (os.environ
    lookup only, no hardcoded True) so the existing offline test suite
    and any normal production use of this module produce byte-identical
    program behavior/output to before this round -- the only thing this
    flag controls is whether diagnostic print() lines are emitted.
    Enabled explicitly by fred_provider_live_smoke_test.py for this
    diagnostic round only; never required for this module's own
    correctness."""
    return os.environ.get("FRED_PROVIDER_HTTP_DEBUG", "").strip() == "1"


def _http_log(msg: str) -> None:
    """Diagnostic-only stdout line, always flushed immediately (2026-10-05
    diagnostic round: GitHub Actions log output is block-buffered when a
    Python process's stdout is not a TTY, so an unflushed print() can be
    silently lost entirely if the job is later cancelled before the
    process exits normally -- this is why every diagnostic print in this
    module and in fred_provider_live_smoke_test.py uses flush=True rather
    than relying on process-exit flushing)."""
    print(msg, flush=True)


def _get_api_key() -> str:
    """Reads FRED_API_KEY from the environment. Raises
    FredAPIKeyMissing (never a generic KeyError/None) when unset -- this
    check happens only when a real request is about to be made, never at
    module import time, exactly mirroring
    src/sec/fetcher.py.get_user_agent()'s documented reasoning. The key
    value itself never appears in the raised exception's message."""
    value = os.environ.get("FRED_API_KEY")
    if not value:
        raise FredAPIKeyMissing(
            "FRED_API_KEY environment variable is not set. This provider "
            "never falls back to a hardcoded or test key for a real "
            "request."
        )
    return value


# ============================================================
# LAYER 1: _FredHttpClient -- stateless HTTP/parsing only. Knows nothing
# about series_id semantics, observation_date vs. vintage_date, or this
# codebase's schema. Directly descended from
# .github/scripts/fred_connectivity_smoke_test.py's _request()/
# _build_url()/_redact() (Step 10G, already validated live) --
# reused, not redesigned from scratch.
# ============================================================


class _FredHttpClient:
    """The ONLY place this module ever touches FRED_API_KEY or makes a
    network call. `get_series()`'s schema-mapping logic (below) never
    constructs a urllib request directly."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self._timeout = timeout

    def _build_url(self, base: str, params: dict[str, Any]) -> str:
        query = dict(params)
        query["api_key"] = _get_api_key()
        query["file_type"] = "json"
        return f"{base}?{urllib.parse.urlencode(query)}"

    def request_json(self, base: str, params: dict[str, Any]) -> dict:
        """
        Builds the URL, makes the GET request, and returns the parsed
        JSON body. Raises FredHTTPError (never a bare urllib/json
        exception) on any failure, classified exactly per the vocabulary
        already validated in Step 10G/10I's scripts -- reusing proven
        classification logic, not reinventing it. The raised exception's
        `redacted_url` attribute (and its message) carry the REDACTED
        request URL for diagnosis (section 6 / 2026-10-05 QA finding --
        the original Step 10J cut never attached the URL at all, so a
        failure could not be traced back to which endpoint/series_id
        produced it); the unredacted URL, and therefore the api_key, is
        never included anywhere in a raised exception.
        """
        url = self._build_url(base, params)
        redacted = _redact_url(url)

        # 2026-10-05 diagnostic round: opt-in only (see _http_debug_enabled),
        # does not alter the request itself -- series_id/output_type/
        # vintage_dates-usage are read from `params` purely for logging.
        debug = _http_debug_enabled()
        series_id_for_log = params.get("series_id")
        output_type_for_log = params.get("output_type")
        uses_vintage_dates = "vintage_dates" in params
        t0 = time.monotonic()
        if debug:
            _http_log(
                f"HTTP START endpoint={redacted} series_id={series_id_for_log!r} "
                f"output_type={output_type_for_log!r} uses_vintage_dates={uses_vintage_dates} "
                f"timeout={self._timeout}s"
            )

        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "invest-briefing-claude-fred-provider/1.0"}
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                status = resp.status
                raw_bytes = resp.read()
                body = raw_bytes.decode("utf-8", errors="replace")
            if debug:
                elapsed = time.monotonic() - t0
                _http_log(
                    f"HTTP END status={status} elapsed={elapsed:.3f}s "
                    f"response_bytes={len(raw_bytes)} series_id={series_id_for_log!r}"
                )
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                raw_err_bytes = e.read()
                body = raw_err_bytes.decode("utf-8", errors="replace")
            except Exception:
                raw_err_bytes = b""
                body = ""
            if debug:
                elapsed = time.monotonic() - t0
                _http_log(
                    f"HTTP END status={status} elapsed={elapsed:.3f}s "
                    f"response_bytes={len(raw_err_bytes)} series_id={series_id_for_log!r} "
                    f"(HTTPError, classification pending)"
                )
            if status == 429:
                raise FredHTTPError("RATE_LIMITED", body[:300], status, redacted_url=redacted) from None
            if status == 400:
                lowered = body.lower()
                if "api_key" in lowered or "api key" in lowered:
                    raise FredHTTPError("AUTH_FAILED", body[:300], status, redacted_url=redacted) from None
                if "does not exist" in lowered:
                    raise FredHTTPError(
                        "SERIES_NOT_FOUND", body[:300], status, redacted_url=redacted
                    ) from None
                raise FredHTTPError(
                    "INVALID_PARAMETER", body[:300], status, redacted_url=redacted
                ) from None
            raise FredHTTPError("HTTP_FAILURE", body[:300], status, redacted_url=redacted) from None
        except ssl.SSLError as e:
            if debug:
                elapsed = time.monotonic() - t0
                _http_log(
                    f"HTTP END status=None elapsed={elapsed:.3f}s classification=TLS_FAILURE "
                    f"series_id={series_id_for_log!r}"
                )
            raise FredHTTPError("TLS_FAILURE", str(e)[:300], None, redacted_url=redacted) from None
        except urllib.error.URLError as e:
            reason = e.reason
            if debug:
                elapsed = time.monotonic() - t0
                _http_log(
                    f"HTTP END status=None elapsed={elapsed:.3f}s classification=URL_ERROR "
                    f"reason={str(reason)[:120]!r} series_id={series_id_for_log!r}"
                )
            if isinstance(reason, socket.gaierror):
                raise FredHTTPError(
                    "DNS_FAILURE", str(reason)[:300], None, redacted_url=redacted
                ) from None
            raise FredHTTPError(
                "NETWORK_FAILURE", str(reason)[:300], None, redacted_url=redacted
            ) from None
        except socket.timeout:
            if debug:
                elapsed = time.monotonic() - t0
                _http_log(
                    f"HTTP END status=None elapsed={elapsed:.3f}s classification=SOCKET_TIMEOUT "
                    f"series_id={series_id_for_log!r} (request's own {self._timeout}s timeout fired)"
                )
            raise FredHTTPError(
                "NETWORK_FAILURE", "socket timeout", None, redacted_url=redacted
            ) from None

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            raise FredHTTPError(
                "RESPONSE_PARSE_FAILED", str(e)[:300], status, redacted_url=redacted
            ) from None

        if status != 200:
            # Defensive -- urlopen() only reaches here on 2xx, but kept
            # explicit rather than assumed.
            raise FredHTTPError(
                "HTTP_FAILURE", f"unexpected status {status}", status, redacted_url=redacted
            )

        return parsed


# ============================================================
# LAYER 2: crosstab parsing -- ported verbatim (same regex, same
# algorithm) from .github/scripts/fred_multi_vintage_validation.py v3's
# _vintage_column_pattern/_extract_vintage_cells/_parse_crosstab, which
# is the parser actually validated against real FRED output_type=2
# responses in Step 10I. Generalized here to not be hardcoded to
# CPIAUCSL/PAYEMS -- series_id is always a parameter.
# ============================================================


def _vintage_column_pattern(series_id: str) -> "re.Pattern[str]":
    return re.compile(rf"^{re.escape(series_id)}_(\d{{8}})$")


def _looks_like_vintage_column(key: str, series_id: str) -> bool:
    """True if `key` starts with the series_id prefix the way a genuine
    vintage column would, even if the date suffix itself fails to
    parse. Used to distinguish "not a vintage column at all" (silently
    skipped -- e.g. the 'date' key) from "looks like a vintage column
    but is malformed" (raises FredMalformedVintageColumnError -- Fixture
    D's explicit requirement)."""
    prefix = f"{series_id}_"
    return key.startswith(prefix) and key != "date"


def _extract_vintage_cells(row: dict, series_id: str) -> dict[str, str]:
    """
    row: one output_type=2 crosstab row (one observation_date).
    series_id: the series this row belongs to.

    Returns {vintage_date_iso: raw_cell_value} for every dynamic
    "{series_id}_{YYYYMMDD}" column present on this row, raw_cell_value
    exactly as FRED sent it (a numeric string, or the missing marker "."
    -- never pre-filtered here). Raises FredMalformedVintageColumnError
    if a column's name has this series' vintage-column prefix but its
    date suffix does not parse as YYYYMMDD -- per this round's explicit
    requirement that a malformed vintage column must be a loud failure,
    never a silent skip or a mis-parse.
    """
    pattern = _vintage_column_pattern(series_id)
    cells: dict[str, str] = {}
    for key, value in row.items():
        if key == "date":
            continue
        match = pattern.match(key)
        if match:
            raw_vintage = match.group(1)
            try:
                vintage_iso = datetime.strptime(raw_vintage, "%Y%m%d").date().isoformat()
            except ValueError:
                raise FredMalformedVintageColumnError(
                    f"column {key!r} has series prefix {series_id!r} but its date "
                    f"suffix {raw_vintage!r} does not parse as YYYYMMDD"
                ) from None
            cells[vintage_iso] = value
            continue
        if _looks_like_vintage_column(key, series_id):
            raise FredMalformedVintageColumnError(
                f"column {key!r} has series prefix {series_id!r} but does not "
                f"match the expected '{series_id}_YYYYMMDD' pattern"
            )
    return cells


def _parse_numeric_cell(raw_value: Any) -> float:
    """Converts one raw FRED cell value to a float, applying the
    MISSING-VALUE POLICY (module docstring): "." or any non-numeric
    string -> float("nan"). Never raises on a missing marker -- NaN is
    the correct, documented representation, not an error."""
    if raw_value is None:
        return float("nan")
    text = str(raw_value)
    if _NUMERIC_VALUE_PATTERN.match(text):
        return float(text)
    return float("nan")


def _parse_crosstab_response(observations: list[dict], series_id: str) -> list[dict]:
    """
    Converts a real output_type=2 crosstab response (parsed["observations"])
    into normalized long-form records: one dict per (observation_date,
    vintage_date) cell, INCLUDING cells holding the "." missing marker
    (unlike the Step 10I validation script, which excluded missing cells
    for its own structure-inspection purposes -- this production parser
    includes them as NaN-valued records, per the MISSING-VALUE POLICY:
    never silently dropped).

    Each record: {series_id, observation_date, vintage_date, value
    (float, possibly NaN), realtime_start}. realtime_start is set equal
    to vintage_date -- for this crosstab shape, the vintage_date IS the
    knowability date, and src/macro/pit.py's own knowability-date rule
    already prefers vintage_date over realtime_start whenever
    vintage_date is present, so this is consistent with (not a
    deviation from) the frozen PIT contract, exactly as Step 10I's Layer
    7 confirmed end-to-end.
    """
    records: list[dict] = []
    for row in observations:
        obs_date = row.get("date")
        if not obs_date:
            continue
        cells = _extract_vintage_cells(row, series_id)
        for vintage_date, raw_value in cells.items():
            records.append(
                {
                    "series_id": series_id,
                    "observation_date": obs_date,
                    "vintage_date": vintage_date,
                    "realtime_start": vintage_date,
                    "value": _parse_numeric_cell(raw_value),
                }
            )
    return records


# ============================================================
# RAW RESPONSE PRESERVATION -- modeled on src/sec/fetcher.py's
# save_raw_json()/fetch_and_cache_*() pattern (Step 10H section 10.2),
# reimplemented here rather than imported, per this codebase's
# established "each domain owns its own copy" convention (see
# pit.py's / schema.py's own module docstrings making the same choice).
# Never writes the API key to disk.
# ============================================================


def save_raw_fred_response(
    path: Path,
    *,
    series_id: str,
    query: dict[str, Any],
    fred_response: dict,
    fetched_at: str,
) -> Path:
    """
    Writes a reproducibility record for one FRED fetch: the query that
    was made (api_key always excluded -- see below), when it was made,
    and FRED's raw response body verbatim. Mirrors
    src/sec/fetcher.py.save_raw_json()'s UTF-8/indent=2/ensure_ascii=False
    convention and parent-directory creation.

    `query` must never contain "api_key" -- this function defensively
    strips it if present, rather than trusting every caller to have
    already done so, since an accidental key-on-disk would be a security
    issue, not merely a cosmetic one.
    """
    safe_query = {k: v for k, v in query.items() if k != "api_key"}
    payload = {
        "fetched_at": fetched_at,
        "query": {"series_id": series_id, **safe_query},
        "fred_response": fred_response,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def default_raw_response_path(raw_dir: Path, series_id: str, query_descriptor: str) -> Path:
    """data/raw/fred/{series_id}/{query_descriptor}.json -- per Step 10H
    section 10.2's proposed layout. `query_descriptor` names the KIND of
    query (e.g. "output_type2_full", "vintage_dates_batch_0"), not a
    single point-in-time snapshot -- a refetch with the same descriptor
    overwrites, mirroring the SEC pattern's "one current file per
    entity" convention."""
    return Path(raw_dir) / "fred" / series_id / f"{query_descriptor}.json"


# ============================================================
# LAYER 3: FredMacroProvider -- implements src/macro/provider.py's
# MacroDataProvider ABC. The only place in this module that knows about
# this codebase's raw-observation schema (src/macro/schema.py).
# ============================================================


class FredMacroProvider(MacroDataProvider):
    """
    Production MacroDataProvider backed by the real FRED API. Implements
    exactly the interface src/macro/provider.py already defines -- no
    change to that interface, per provider.py's own module docstring's
    stated design goal ("a real FredMacroProvider can be dropped in
    without touching transforms.py, *_state.py, regime.py, or engine.py
    at all").

    get_series() is NOT PIT-aware (see VINTAGE SELECTION POLICY in the
    module docstring): it returns every raw observation row it could
    fetch for the requested window, unfiltered by any as_of_date.
    Callers feed the result into src/macro/pit.py exactly as they would
    InMemoryMacroProvider's output.

    raw_dir: optional. When given, every successful FRED HTTP response
    this provider makes is preserved via save_raw_fred_response() under
    raw_dir/fred/{series_id}/... (Step 10H section 10). When None
    (the default), no raw-response file is written -- this keeps the
    provider usable in tests/CI contexts that should not touch the
    filesystem, without making raw preservation mandatory at the
    interface level (a decision Step 10H section 10.3 explicitly left
    open; this constructor parameter is this round's resolution:
    opt-in, not forced).
    """

    def __init__(
        self,
        *,
        http_client: _FredHttpClient | None = None,
        raw_dir: Path | None = None,
        vintage_dates_batch_size: int = VINTAGE_DATES_BATCH_SIZE,
    ):
        self._http = http_client or _FredHttpClient()
        self._raw_dir = Path(raw_dir) if raw_dir is not None else None
        self._vintage_dates_batch_size = vintage_dates_batch_size

    # ---- raw storage helper -------------------------------------------------

    def _maybe_save_raw(self, series_id: str, query_descriptor: str, query: dict, response: dict) -> None:
        if self._raw_dir is None:
            return
        path = default_raw_response_path(self._raw_dir, series_id, query_descriptor)
        save_raw_fred_response(
            path,
            series_id=series_id,
            query=query,
            fred_response=response,
            fetched_at=datetime.utcnow().isoformat() + "Z",
        )

    # ---- metadata (frequency / units / ...) ----------------------------------

    def get_series_metadata(self, series_id: str) -> dict:
        """
        Calls fred/series (separate endpoint from fred/series/observations
        -- Step 10H section 4.5 / section 9 of this round's instructions:
        metadata is NEVER hardcoded per series here, always fetched).
        Returns a dict with at least: series_id, frequency,
        frequency_short, units, units_short, seasonal_adjustment,
        observation_start, observation_end, last_updated.

        Raises FredResponseShapeError if the response's "seriess" key is
        missing or empty -- an HTTP-successful-but-shape-wrong response,
        distinguished from a network/protocol failure.
        """
        params = {"series_id": series_id}
        response = self._http.request_json(FRED_SERIES_BASE, params)
        self._maybe_save_raw(series_id, "metadata", params, response)

        series_list = response.get("seriess")
        if not series_list:
            raise FredResponseShapeError(
                f"{series_id}: fred/series response has no 'seriess' entries"
            )

        info = series_list[0]
        return {
            "series_id": series_id,
            "frequency": info.get("frequency"),
            "frequency_short": info.get("frequency_short"),
            "units": info.get("units"),
            "units_short": info.get("units_short"),
            "seasonal_adjustment": info.get("seasonal_adjustment"),
            "observation_start": info.get("observation_start"),
            "observation_end": info.get("observation_end"),
            "last_updated": info.get("last_updated"),
        }

    # ---- vintage_dates lookup (policy #2 series only) ------------------------

    def _confirmed_vintage_dates(
        self, series_id: str, realtime_start: str | None, realtime_end: str | None
    ) -> list[str]:
        """Calls fred/series/vintagedates and returns every vintage date
        FRED has for this series, filtered to [realtime_start,
        realtime_end] when given (never narrowed to "the most recent
        N" -- see VINTAGE_DATES FETCH POLICY in the module docstring)."""
        params = {"series_id": series_id}
        response = self._http.request_json(FRED_VINTAGEDATES_BASE, params)
        self._maybe_save_raw(series_id, "vintagedates", params, response)

        all_dates = response.get("vintage_dates")
        if all_dates is None:
            raise FredResponseShapeError(
                f"{series_id}: fred/series/vintagedates response has no 'vintage_dates' key"
            )

        if realtime_start is None and realtime_end is None:
            return list(all_dates)

        lo = realtime_start or DEFAULT_REALTIME_START
        hi = realtime_end or DEFAULT_REALTIME_END_SENTINEL
        return [d for d in all_dates if lo <= d <= hi]

    @staticmethod
    def _batch(items: list[str], size: int) -> list[list[str]]:
        return [items[i : i + size] for i in range(0, len(items), max(1, size))]

    # ---- main entry point ------------------------------------------------

    def get_series(
        self,
        series_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        realtime_start: str | None = None,
        realtime_end: str | None = None,
    ) -> list[dict]:
        """
        Returns raw macro observations for `series_id` matching
        src/macro/schema.py's RAW_OBSERVATION_FIELDS shape. `source` is
        always the literal "FRED" (never derived from the response --
        Step 10H section 4.2). `frequency`/`units` are populated from a
        separate get_series_metadata() call (never hardcoded -- Step 10H
        section 4.5's hardcode mitigation is explicitly NOT adopted here,
        since this round has live API access to confirm the real values
        instead).

        Dispatches to one of two confirmed-working fetch strategies
        depending on series_id -- see VINTAGE_DATES FETCH POLICY in the
        module docstring for why each exists and why neither is an
        arbitrary "last N vintages" policy.
        """
        # Classification is checked FIRST, before any HTTP call (including
        # the metadata call) -- an unclassified series_id must fail loudly
        # with zero network activity, never a partial fetch.
        if series_id in WIDE_WINDOW_SERIES_IDS:
            fetch_strategy = self._fetch_wide_window
        elif series_id in EXPLICIT_VINTAGE_DATES_SERIES_IDS:
            fetch_strategy = self._fetch_explicit_vintage_dates
        else:
            # Not yet classified into either confirmed-working strategy.
            # Per this round's explicit instruction (section 23): a
            # frozen-contract-adjacent ambiguity is reported, never
            # silently guessed at. A caller requesting an unclassified
            # series_id gets a loud, explicit failure rather than a
            # provider inventing a third, unvalidated fetch strategy.
            raise FredProviderError(
                f"{series_id!r} is not in WIDE_WINDOW_SERIES_IDS or "
                f"EXPLICIT_VINTAGE_DATES_SERIES_IDS -- this provider has no "
                f"confirmed-working fetch strategy for it. Classify it "
                f"explicitly (per Step 10I's empirical findings) before "
                f"requesting it."
            )

        metadata = self.get_series_metadata(series_id)
        frequency = _normalize_frequency(metadata.get("frequency"))
        units = metadata.get("units_short") or metadata.get("units")

        records = fetch_strategy(series_id, start_date, end_date, realtime_start, realtime_end)

        observations: list[dict] = []
        for record in records:
            if start_date is not None and record["observation_date"] < start_date:
                continue
            if end_date is not None and record["observation_date"] > end_date:
                continue
            observations.append(
                {
                    "series_id": record["series_id"],
                    "observation_date": record["observation_date"],
                    "value": record["value"],
                    "realtime_start": record["realtime_start"],
                    "realtime_end": None,
                    "source": "FRED",
                    "frequency": frequency,
                    "units": units,
                    "vintage_date": record["vintage_date"],
                }
            )
        return observations

    def _fetch_wide_window(
        self,
        series_id: str,
        start_date: str | None,
        end_date: str | None,
        realtime_start: str | None,
        realtime_end: str | None,
    ) -> list[dict]:
        params: dict[str, Any] = {
            "series_id": series_id,
            "output_type": 2,
            "realtime_start": realtime_start or DEFAULT_REALTIME_START,
            "realtime_end": realtime_end or DEFAULT_REALTIME_END_SENTINEL,
        }
        if start_date is not None:
            params["observation_start"] = start_date
        if end_date is not None:
            params["observation_end"] = end_date

        response = self._http.request_json(FRED_OBSERVATIONS_BASE, params)
        self._maybe_save_raw(series_id, "output_type2_full", params, response)

        observations = response.get("observations")
        if observations is None:
            raise FredResponseShapeError(
                f"{series_id}: output_type=2 response has no 'observations' key"
            )

        return _parse_crosstab_response(observations, series_id)

    def _fetch_explicit_vintage_dates(
        self,
        series_id: str,
        start_date: str | None,
        end_date: str | None,
        realtime_start: str | None,
        realtime_end: str | None,
    ) -> list[dict]:
        vintage_dates = self._confirmed_vintage_dates(series_id, realtime_start, realtime_end)
        if not vintage_dates:
            return []

        all_records: list[dict] = []
        for batch_index, batch in enumerate(self._batch(vintage_dates, self._vintage_dates_batch_size)):
            params: dict[str, Any] = {
                "series_id": series_id,
                "output_type": 2,
                "vintage_dates": ",".join(batch),
            }
            if start_date is not None:
                params["observation_start"] = start_date
            if end_date is not None:
                params["observation_end"] = end_date

            response = self._http.request_json(FRED_OBSERVATIONS_BASE, params)
            self._maybe_save_raw(
                series_id, f"vintage_dates_batch_{batch_index}", params, response
            )

            observations = response.get("observations")
            if observations is None:
                raise FredResponseShapeError(
                    f"{series_id}: vintage_dates batch response has no 'observations' key"
                )

            all_records.extend(_parse_crosstab_response(observations, series_id))

        return all_records


__all__ = [
    "SCHEMA_VERSION",
    "FRED_OBSERVATIONS_BASE",
    "FRED_VINTAGEDATES_BASE",
    "FRED_SERIES_BASE",
    "WIDE_WINDOW_SERIES_IDS",
    "EXPLICIT_VINTAGE_DATES_SERIES_IDS",
    "VINTAGE_DATES_BATCH_SIZE",
    "FredProviderError",
    "FredAPIKeyMissing",
    "FredHTTPError",
    "FredResponseShapeError",
    "FredMalformedVintageColumnError",
    "save_raw_fred_response",
    "default_raw_response_path",
    "FredMacroProvider",
]
