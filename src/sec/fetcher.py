"""
Unified SEC EDGAR raw-data fetch layer.

Why this module exists
-----------------------
Three scripts in this repository each implemented their own, functionally
identical, copy of "GET a JSON URL from SEC EDGAR, handle gzip/deflate
Content-Encoding by hand (urllib does not auto-decompress), check the HTTP
status, and parse the body":

    - tests/test_sec_companyfacts.py       (fetch_json)
    - tests/test_sec_fact_diagnostics.py   (fetch_json)
    - tests/test_sec_fundamentals_pipeline.py (fetch_json + download_companyfacts)

This module is the single place that logic now lives. It owns exactly one
responsibility: getting raw bytes off data.sec.gov onto disk / into memory,
correctly. It intentionally does NOT know anything about XBRL concepts,
economic-priority concept selection, or normalization -- that logic already
lives in src/fundamentals/sec_normalizer.py and must not be duplicated here.

Behavioral notes carried over from the three original implementations
(preserved deliberately, not incidentally):
    - Non-200 HTTP status raises RuntimeError("HTTP status: {status}").
    - gzip / deflate Content-Encoding is decompressed explicitly.
    - Network-level failures (HTTPError, URLError, socket.timeout) are left
      to propagate unchanged -- none of the three originals caught them.

One deliberate behavioral FIX relative to the originals:
    - tests/test_sec_fundamentals_pipeline.py read `SEC_USER_AGENT` and
      raised RuntimeError at *module import time* if it was unset. That
      breaks `pytest` collection for the entire test suite (not just that
      file) whenever the env var isn't exported first -- confirmed via a
      clean `pytest -q` run in this environment, which aborts with
      "Interrupted: 1 error during collection, no tests collected" before a
      single test runs. `get_user_agent()` below performs the same check,
      but only when a caller actually calls it, never on import.
"""

from __future__ import annotations

import gzip
import json
import os
import zlib
from pathlib import Path
from urllib.request import Request, urlopen

SEC_COMPANYFACTS_BASE_URL = "https://data.sec.gov/api/xbrl/companyfacts"
SEC_SUBMISSIONS_BASE_URL = "https://data.sec.gov/submissions"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RAW_FILENAME_TEMPLATE = "{ticker}_companyfacts.json"
DEFAULT_SUBMISSIONS_FILENAME_TEMPLATE = "{cik}_submissions.json"


class SECUserAgentNotConfigured(RuntimeError):
    """Raised by get_user_agent() when SEC_USER_AGENT is unset and no
    default was supplied. Never raised at import time."""


def get_user_agent(default: str | None = None) -> str:
    """
    Resolve the User-Agent string SEC EDGAR requires
    (https://www.sec.gov/os/webmaster-faq#developers).

    Reads SEC_USER_AGENT from the environment first; falls back to
    `default` if provided; otherwise raises. This check happens only when
    this function is called, not at module import time.
    """
    value = os.environ.get("SEC_USER_AGENT")
    if value:
        return value
    if default:
        return default
    raise SECUserAgentNotConfigured(
        "SEC_USER_AGENT environment variable is not set, and no default "
        "User-Agent was provided."
    )


def build_companyfacts_url(cik: str) -> str:
    """Build the SEC XBRL Company Facts API URL for a given CIK."""
    return f"{SEC_COMPANYFACTS_BASE_URL}/CIK{cik}.json"


def build_submissions_url(cik: str) -> str:
    """Build the SEC Submissions API URL for a given CIK.

    Unlike Company Facts, which has no notion of SIC / industry classification
    or ticker-exchange mappings, the Submissions endpoint
    (data.sec.gov/submissions/CIK{cik}.json) is the ONLY SEC source this
    repository uses that carries "sic" / "sicDescription" / "name" /
    "tickers" (array) / "exchanges" (array). Confirmed via live fetch
    (2026-09-19): a single CIK can legitimately map to MULTIPLE tickers --
    e.g. Alphabet (CIK 0001652044) returns
    tickers=["GOOGL","GOOG","GOOGM","GOOGN"] -- so this endpoint's own data
    shape is fundamentally CIK-keyed, not ticker-keyed. See
    src/sec/identity.py for how the ticker-level records are derived from
    this. `cik` is expected zero-padded to 10 digits, matching the existing
    build_companyfacts_url convention (confirmed live: the response's own
    top-level "cik" field is itself a zero-padded string, e.g.
    "0000320193").
    """
    return f"{SEC_SUBMISSIONS_BASE_URL}/CIK{cik}.json"


def fetch_company_tickers(
    user_agent: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    verbose: bool = True,
) -> dict:
    """Fetch SEC's bulk ticker->CIK mapping file
    (www.sec.gov/files/company_tickers.json). Confirmed shape (live,
    2026-09-20): an object keyed by numeric string index, each value
    {"cik_str": <int, NOT zero-padded>, "ticker": <str>, "title": <str>}
    -- e.g. {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}, ...}.

    This is the ONLY SEC source this repo uses for ticker->CIK
    resolution -- deliberately separate from build_submissions_url's CIK-
    keyed lookup, which requires already knowing the CIK. No validation,
    no normalization -- just the fetch. See src/sec/identity.py's
    resolve_cik_for_ticker() for the pure lookup logic against this data.
    """
    return fetch_json(
        SEC_COMPANY_TICKERS_URL, user_agent=user_agent, timeout=timeout, verbose=verbose
    )


def fetch_json(
    url: str,
    user_agent: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    verbose: bool = True,
) -> dict:
    """
    GET `url` with a compliant User-Agent / Accept-Encoding header set and
    return the parsed JSON body.

    Raises RuntimeError on a non-200 HTTP status. Explicitly decompresses
    gzip/deflate bodies (urllib does not do this automatically). Does not
    catch urllib.error.HTTPError / URLError / socket.timeout -- those
    propagate to the caller, matching all three original implementations.
    """
    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
        method="GET",
    )

    with urlopen(request, timeout=timeout) as response:
        status = response.status
        content_encoding = response.headers.get("Content-Encoding", "").lower()
        raw_data = response.read()

        if verbose:
            print(f"HTTP status: {status}")
            print(f"Content-Encoding: {content_encoding or 'none'}")
            print(f"Response bytes: {len(raw_data)}")

        if status != 200:
            raise RuntimeError(f"HTTP status: {status}")

        if content_encoding == "gzip":
            raw_data = gzip.decompress(raw_data)
        elif content_encoding == "deflate":
            raw_data = zlib.decompress(raw_data)

        return json.loads(raw_data.decode("utf-8"))


def fetch_companyfacts(
    cik: str,
    user_agent: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    verbose: bool = True,
) -> dict:
    """Fetch the raw XBRL Company Facts JSON for a CIK. No validation,
    no normalization -- just the fetch."""
    url = build_companyfacts_url(cik)
    return fetch_json(url, user_agent=user_agent, timeout=timeout, verbose=verbose)


def fetch_submissions(
    cik: str,
    user_agent: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    verbose: bool = True,
) -> dict:
    """Fetch the raw Submissions JSON for a CIK. No validation, no
    normalization -- just the fetch (mirrors fetch_companyfacts). SIC /
    identity normalization lives in src/sec/identity.py, not here -- this
    module's only responsibility is getting raw bytes off data.sec.gov."""
    url = build_submissions_url(cik)
    return fetch_json(url, user_agent=user_agent, timeout=timeout, verbose=verbose)


def save_raw_json(path: Path, data: dict) -> Path:
    """
    Write `data` as UTF-8 JSON (indent=2, ensure_ascii=False), creating
    parent directories as needed. Matches the write pattern that was
    duplicated across the legacy scripts. Returns the path written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def fetch_and_cache_companyfacts(
    ticker: str,
    cik: str,
    raw_dir: Path,
    user_agent: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    filename_template: str = DEFAULT_RAW_FILENAME_TEMPLATE,
    required_keys: tuple[str, ...] = ("cik", "entityName", "facts"),
    verbose: bool = True,
) -> tuple[dict, Path]:
    """
    Fetch a company's raw XBRL Company Facts JSON from SEC EDGAR, check
    that the required top-level keys are present, and write it to
    `raw_dir`. This is the fetch -> validate-keys -> save sequence shared
    by tests/test_sec_companyfacts.py, tests/test_sec_fact_diagnostics.py
    and tests/test_sec_fundamentals_pipeline.py.

    Returns (data, path_written). Deliberately does NOT perform XBRL-level
    validation or normalization -- callers needing that continue to use
    src/fundamentals/sec_normalizer.py.
    """
    data = fetch_companyfacts(cik, user_agent=user_agent, timeout=timeout, verbose=verbose)

    for key in required_keys:
        if key not in data:
            raise ValueError(f"{ticker}: missing required key '{key}' in SEC response")

    raw_path = Path(raw_dir) / filename_template.format(ticker=ticker)
    save_raw_json(raw_path, data)

    if verbose:
        print(f"[PASS] Raw cache written: {raw_path}")

    return data, raw_path


def fetch_and_cache_submissions(
    cik: str,
    raw_dir: Path,
    user_agent: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    filename_template: str = DEFAULT_SUBMISSIONS_FILENAME_TEMPLATE,
    required_keys: tuple[str, ...] = ("cik", "sic", "tickers", "exchanges"),
    verbose: bool = True,
) -> tuple[dict, Path]:
    """
    Fetch a company's raw Submissions JSON from SEC EDGAR, check that the
    required top-level keys are present, and write it to `raw_dir`. Mirrors
    fetch_and_cache_companyfacts's fetch -> validate-keys -> save sequence.

    Deliberately keyed by CIK alone (no `ticker` parameter) -- unlike
    Company Facts, one Submissions response legitimately covers multiple
    tickers (see build_submissions_url's docstring), so keying the cache
    filename by a single caller-chosen ticker would misrepresent what the
    cached file actually contains. Callers that need a ticker-level record
    derive it from this cached response via src/sec/identity.py.

    Returns (data, path_written). Deliberately does NOT normalize --
    callers needing that use src/sec/identity.py.
    """
    data = fetch_submissions(cik, user_agent=user_agent, timeout=timeout, verbose=verbose)

    for key in required_keys:
        if key not in data:
            raise ValueError(f"CIK {cik}: missing required key '{key}' in SEC submissions response")

    raw_path = Path(raw_dir) / filename_template.format(cik=cik)
    save_raw_json(raw_path, data)

    if verbose:
        print(f"[PASS] Raw submissions cache written: {raw_path}")

    return data, raw_path
