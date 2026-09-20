"""
SEC issuer / security identity normalization.

Why this module exists
-----------------------
SEC's Submissions API is CIK-keyed and a single CIK can legitimately carry
MULTIPLE tickers (confirmed live, 2026-09-19: Alphabet, CIK 0001652044,
returns tickers=["GOOGL","GOOG","GOOGM","GOOGN"]). "Issuer" (the business
entity, identified by CIK) and "Security" (one tradeable ticker on one
exchange) are therefore two different concepts and get two different
normalized record shapes here -- never conflated into one.

This module does NOT call the SEC API and does NOT write files. Those are
src/sec/fetcher.py's job (fetch_and_cache_submissions). This module's only
job is: given an already-fetched raw Submissions dict (and, for identity
resolution, an already-fetched fresh comparison record), produce normalized
records and validate them. Every function here is a pure function of its
inputs -- no wall-clock reads, no network, no disk I/O -- specifically so
that normalize_* output is deterministic and testable with a fixed fixture,
and so that a caller doing a live refresh controls timestamps explicitly
(SEC_USER_AGENT/network mocking is never a concern for testing this file).

Layering (matches the "Request Layer -> Fetcher -> Raw Storage ->
Normalizer -> Classification" split agreed with the ChatGPT-authored
design, 2026-09-19):

    src/sec/fetcher.py          (Request Layer, Fetcher, Raw Storage)
            |
    src/sec/identity.py         (Normalizer + Identity resolution -- HERE)
            |
    src/fundamentals/peer_classification.py   (Classification)
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "sec_identity_v0.1"

IDENTITY_MATCH = "IDENTITY_MATCH"
IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
IDENTITY_STALE = "IDENTITY_STALE"
IDENTITY_UNAVAILABLE = "IDENTITY_UNAVAILABLE"

VALID_IDENTITY_STATUSES = {
    IDENTITY_MATCH,
    IDENTITY_CONFLICT,
    IDENTITY_STALE,
    IDENTITY_UNAVAILABLE,
}


# ============================================================
# ISSUER IDENTITY (CIK-keyed)
# ============================================================

def normalize_issuer_identity(
    raw_submissions: dict,
    source_raw_path: str,
    normalized_at: str,
) -> dict:
    """
    Extract the issuer-level (CIK-keyed) identity record from one raw SEC
    Submissions response.

    `source_raw_path` and `normalized_at` are caller-supplied, not read
    internally (see module docstring) -- `source_raw_path` is the
    provenance trail required by the reference-data design (2026-09-19,
    point 5): it must point back at the exact raw file this record was
    derived from, so issuer_identity.csv rows are traceable to a specific
    cached SEC response without this module re-fetching anything.

    Returns a dict matching one row of data/reference/issuer_identity.csv:
        cik, issuer_name, sic, sic_description, sec_tickers, sec_exchanges,
        source_raw_path, normalized_at

    `sec_tickers` / `sec_exchanges` are informational, sourced straight
    from SEC's own arrays on this record -- they exist for cross-checking
    against security_identity.csv (see resolve_identity_status below), not
    as the authoritative per-security record themselves.
    """
    if not isinstance(raw_submissions, dict):
        raise TypeError("raw_submissions must be a dict.")

    tickers = raw_submissions.get("tickers")
    exchanges = raw_submissions.get("exchanges")

    return {
        "cik": raw_submissions.get("cik"),
        "issuer_name": raw_submissions.get("name"),
        "sic": raw_submissions.get("sic"),
        "sic_description": raw_submissions.get("sicDescription"),
        "sec_tickers": list(tickers) if isinstance(tickers, list) else [],
        "sec_exchanges": list(exchanges) if isinstance(exchanges, list) else [],
        "source_raw_path": source_raw_path,
        "normalized_at": normalized_at,
    }


def validate_issuer_identity(row: dict) -> list[str]:
    """
    Validate one issuer_identity.csv row's own schema.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures = []

    for key in ("cik", "issuer_name", "sic", "source_raw_path", "normalized_at"):
        if not row.get(key):
            failures.append(key)

    if not isinstance(row.get("sec_tickers"), list):
        failures.append("sec_tickers")

    if not isinstance(row.get("sec_exchanges"), list):
        failures.append("sec_exchanges")

    return failures


def validate_issuer_identity_provenance(row: dict) -> list[str]:
    """
    Stage-2 CIK existence check (2026-09-19 design, point 5): NOT a live
    re-verification against SEC (out of scope for this step), but a check
    that this issuer_identity.csv row is traceable to a specific raw
    Submissions artifact -- source_raw_path must be present and must
    reference the raw file for THIS row's own CIK, not some other CIK's
    file copy-pasted in by mistake.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures = []
    cik = row.get("cik")
    source_raw_path = row.get("source_raw_path")

    if not source_raw_path:
        failures.append("source_raw_path")
        return failures

    if not cik:
        failures.append("cik")
        return failures

    expected_suffix = f"{cik}_submissions.json"
    if not str(source_raw_path).endswith(expected_suffix):
        failures.append("source_raw_path_cik_mismatch")

    return failures


# ============================================================
# SECURITY IDENTITY (ticker-keyed)
# ============================================================

def normalize_security_identities(raw_submissions: dict) -> list[dict]:
    """
    Extract one security-level (ticker-keyed) identity record per ticker
    in the raw Submissions response's tickers[]/exchanges[] arrays (they
    are parallel arrays -- same index means "this ticker trades on this
    exchange").

    `security_id` is set equal to `ticker` in this v0.1 -- a deliberate,
    explicitly-scoped-down simplification (2026-09-19 design, point 3): a
    full security master (surviving ticker renames, share-class remaps,
    delistings as a distinct identity) is out of scope for this step. The
    column is kept SEPARATE from `ticker` in the schema specifically so
    that no calling code ever assumes security_id == ticker -- only this
    one function's current implementation does, and only this function
    would need to change if that assumption stops holding.
    """
    if not isinstance(raw_submissions, dict):
        raise TypeError("raw_submissions must be a dict.")

    cik = raw_submissions.get("cik")
    tickers = raw_submissions.get("tickers")
    exchanges = raw_submissions.get("exchanges")

    if not isinstance(tickers, list):
        return []

    exchanges = exchanges if isinstance(exchanges, list) else []

    records = []
    for index, ticker in enumerate(tickers):
        exchange = exchanges[index] if index < len(exchanges) else None
        records.append(
            {
                "security_id": ticker,
                "ticker": ticker,
                "cik": cik,
                "exchange": exchange,
            }
        )

    return records


def validate_security_identity(row: dict) -> list[str]:
    """
    Validate one security_identity.csv row's own schema.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures = []

    for key in ("security_id", "ticker", "cik"):
        if not row.get(key):
            failures.append(key)

    identity_status = row.get("identity_status")
    if identity_status is not None and identity_status not in VALID_IDENTITY_STATUSES:
        failures.append("identity_status")

    return failures


# ============================================================
# TICKER -> CIK RESOLUTION (pure lookup against a bulk fetch result)
# ============================================================

def resolve_cik_for_ticker(company_tickers: dict, ticker: str) -> str | None:
    """
    Look up `ticker`'s CIK in an already-fetched
    fetch_company_tickers() result (src/sec/fetcher.py), zero-padded to
    10 digits to match build_submissions_url's / build_companyfacts_url's
    convention (both confirmed live to expect/return this format).

    Pure function -- no network, no file I/O, matching this module's own
    contract. Case-insensitive on the ticker (SEC's own file uses
    upper-case tickers, but callers should not have to know that).

    Returns None if `ticker` is not found -- never guesses, never returns
    a partial/best-effort match.
    """
    if not isinstance(company_tickers, dict):
        raise TypeError("company_tickers must be a dict.")

    ticker_upper = ticker.upper()
    for entry in company_tickers.values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("ticker", "")).upper() == ticker_upper:
            cik_str = entry.get("cik_str")
            if cik_str is None:
                return None
            return str(cik_str).zfill(10)
    return None


# ============================================================
# IDENTITY RESOLUTION (cross-check our reference data vs fresh SEC data)
# ============================================================

def resolve_identity_status(
    claimed_cik: str,
    claimed_ticker: str,
    fresh_issuer_record: dict | None,
    ticker_seen_under_cik: str | None = None,
) -> tuple[str, str]:
    """
    Cross-check a claimed (ticker -> CIK) mapping (as stored in
    security_identity.csv) against a freshly-normalized issuer record for
    that same CIK (normalize_issuer_identity's output).

    Args:
        claimed_cik: the CIK our reference data says `claimed_ticker` maps to.
        claimed_ticker: the ticker being checked.
        fresh_issuer_record: normalize_issuer_identity() output for
            `claimed_cik`, fetched fresh from SEC -- or None if no fresh
            data could be obtained for that CIK at all (network failure,
            CIK no longer exists, etc.).
        ticker_seen_under_cik: if the caller independently knows (e.g. via
            a bulk ticker->CIK lookup) that `claimed_ticker` currently
            resolves to a DIFFERENT CIK than `claimed_cik`, pass that CIK
            here to get IDENTITY_STALE instead of a generic
            IDENTITY_CONFLICT. Optional -- omitting it never produces a
            false IDENTITY_STALE, only a less specific IDENTITY_CONFLICT.

    Returns:
        (status, reason) where status is one of IDENTITY_MATCH /
        IDENTITY_CONFLICT / IDENTITY_STALE / IDENTITY_UNAVAILABLE.
    """
    if fresh_issuer_record is None:
        return (
            IDENTITY_UNAVAILABLE,
            f"No fresh SEC submissions data available for CIK {claimed_cik!r}.",
        )

    sec_tickers = fresh_issuer_record.get("sec_tickers") or []

    if (
        fresh_issuer_record.get("cik") == claimed_cik
        and claimed_ticker in sec_tickers
    ):
        return (
            IDENTITY_MATCH,
            f"Ticker {claimed_ticker!r} found among CIK {claimed_cik!r}'s current SEC tickers.",
        )

    if ticker_seen_under_cik is not None and ticker_seen_under_cik != claimed_cik:
        return (
            IDENTITY_STALE,
            f"Ticker {claimed_ticker!r} now resolves to CIK {ticker_seen_under_cik!r}, "
            f"not the claimed CIK {claimed_cik!r}.",
        )

    return (
        IDENTITY_CONFLICT,
        f"Ticker {claimed_ticker!r} not found among CIK {claimed_cik!r}'s current "
        f"SEC tickers: {sec_tickers!r}.",
    )


__all__ = [
    "SCHEMA_VERSION",
    "IDENTITY_MATCH",
    "IDENTITY_CONFLICT",
    "IDENTITY_STALE",
    "IDENTITY_UNAVAILABLE",
    "VALID_IDENTITY_STATUSES",
    "normalize_issuer_identity",
    "validate_issuer_identity",
    "validate_issuer_identity_provenance",
    "normalize_security_identities",
    "validate_security_identity",
    "resolve_cik_for_ticker",
    "resolve_identity_status",
]
