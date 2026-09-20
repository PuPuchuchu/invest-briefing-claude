"""
CSV read/write/validation for data/reference/core_universe_candidates.csv
(Framework v2.0 Step 10B schema, 2026-09-20 ChatGPT design review, item 8).

Why this file is separate from data/reference/peer_groups.csv
---------------------------------------------------------------
peer_groups.csv (see peer_classification_io.py) holds CONFIRMED
Peer Group membership for CIKs that are already part of this system's
identity reference data (data/reference/issuer_identity.csv). It is
production data.

core_universe_candidates.csv is explicitly NOT production data. Per the
2026-09-20 design review: "후보를 바로 모두 production universe에 편입하지
말고... 관리할 수 있는 형태로 설계하세요." -- this file is a proposal /
tracking inventory for tickers being CONSIDERED for addition to the
Core Market Universe (a managed universe, separate from the 21-ticker
Watchlist -- see peer_classification.py's module docstring on why the
Watchlist itself must never be treated as the Core Universe).

A row here never becomes a peer_groups.csv row automatically. Promotion
is a separate, later, explicit step (10B "실제 peer expansion") that
requires:
    1. sec_availability == "CONFIRMED" (a real CIK resolved and SEC
       Submissions data actually fetched for it -- see
       src/sec/identity.py's resolve_cik_for_ticker() /
       src/sec/fetcher.py's fetch_and_cache_submissions(), same pattern
       already used by scripts/seed_identity_reference.py)
    2. a human/ChatGPT economic-comparability sign-off, recorded here
       via classification_rationale and expected_metric_comparability

Standing project rule enforced here: cik is never fabricated. A
candidate with no live-verified CIK yet keeps cik = None / "" and
sec_availability = "UNVERIFIED" -- never a guessed or hardcoded value.
"""

from __future__ import annotations

import csv
from pathlib import Path

CORE_UNIVERSE_FIELDNAMES = [
    "ticker",
    "cik",
    "sector",
    "industry",
    "proposed_peer_group",
    "classification_rationale",
    "sec_availability",
    "expected_metric_comparability",
    "inclusion_status",
    "added_date",
    "notes",
]

# sec_availability: has this candidate's CIK actually been resolved and
# its SEC Submissions data actually fetched (live), or is it still an
# unverified proposal?
SEC_AVAILABILITY_STATUSES = frozenset(
    {
        "UNVERIFIED",  # proposed only; no live SEC lookup performed yet
        "CONFIRMED",  # CIK resolved + Submissions fetched successfully
        "NOT_FOUND",  # live lookup was attempted and failed (delisted,
        # ticker typo, no SEC filer record, etc.)
    }
)

# inclusion_status: where this candidate sits in the promotion pipeline.
INCLUSION_STATUSES = frozenset(
    {
        "CANDIDATE_PROPOSED",  # listed here, not yet SEC-verified
        "CANDIDATE_VERIFIED",  # SEC-verified (sec_availability=CONFIRMED),
        # awaiting economic-comparability sign-off before promotion
        "PROMOTED",  # sign-off complete; a REVIEWED row now exists in
        # peer_groups.csv for this CIK -- this row is a historical record,
        # not a duplicate source of truth
        "REJECTED",  # considered and explicitly rejected (e.g. structural
        # comparability concerns found during review) -- kept for audit
        # trail rather than deleted
    }
)

# A CANDIDATE_VERIFIED or PROMOTED row must have a resolved CIK and
# CONFIRMED SEC availability; a fabricated/guessed CIK is never
# acceptable at any status.
_VALID_STATUS_AVAILABILITY_COMBINATIONS = frozenset(
    {
        ("CANDIDATE_PROPOSED", "UNVERIFIED"),
        ("CANDIDATE_PROPOSED", "NOT_FOUND"),
        ("CANDIDATE_VERIFIED", "CONFIRMED"),
        ("PROMOTED", "CONFIRMED"),
        ("REJECTED", "UNVERIFIED"),
        ("REJECTED", "CONFIRMED"),
        ("REJECTED", "NOT_FOUND"),
    }
)


def serialize_core_universe_row(row: dict) -> dict:
    """core_universe_candidates.csv-shaped dict -> flat dict of strings,
    ready for csv.DictWriter. Missing values become "" (never
    fabricated)."""
    out = {}
    for field in CORE_UNIVERSE_FIELDNAMES:
        value = row.get(field)
        out[field] = "" if value is None else str(value)
    return out


def deserialize_core_universe_row(row: dict) -> dict:
    """Flat dict of strings (as read by csv.DictReader) -> a row shaped
    exactly as validate_core_universe_row() expects: "" becomes None."""
    return {
        field: (row.get(field) if row.get(field) else None)
        for field in CORE_UNIVERSE_FIELDNAMES
    }


def write_core_universe_candidates_csv(rows: list[dict], path: Path) -> Path:
    """Write a list of core_universe_candidates.csv-shaped rows to
    `path`. Overwrites any existing file at `path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CORE_UNIVERSE_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(serialize_core_universe_row(row))
    return path


def read_core_universe_candidates_csv(path: Path) -> list[dict]:
    """Read core_universe_candidates.csv from `path` back into row
    dicts."""
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [deserialize_core_universe_row(row) for row in reader]


def validate_core_universe_row(row: dict) -> list[str]:
    """
    Validate one core_universe_candidates.csv row's own schema and
    internal consistency (does not check cross-row conditions such as
    duplicate tickers).

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures: list[str] = []

    for key in ("ticker", "inclusion_status", "sec_availability"):
        if not row.get(key):
            failures.append(key)

    inclusion_status = row.get("inclusion_status")
    if inclusion_status is not None and inclusion_status not in INCLUSION_STATUSES:
        failures.append("inclusion_status")

    sec_availability = row.get("sec_availability")
    if sec_availability is not None and sec_availability not in SEC_AVAILABILITY_STATUSES:
        failures.append("sec_availability")

    if (inclusion_status, sec_availability) not in _VALID_STATUS_AVAILABILITY_COMBINATIONS:
        failures.append("inclusion_status_sec_availability_combination")

    # A resolved CIK is only trustworthy once sec_availability is
    # CONFIRMED -- a "cik" value present alongside UNVERIFIED/NOT_FOUND
    # would look like a real, checked CIK when it is not.
    cik = row.get("cik")
    if cik and sec_availability != "CONFIRMED":
        failures.append("cik_present_without_confirmed_sec_availability")

    if sec_availability == "CONFIRMED" and not cik:
        failures.append("cik_missing_for_confirmed_sec_availability")

    if inclusion_status in ("CANDIDATE_VERIFIED", "PROMOTED") and not cik:
        failures.append("cik_missing_for_verified_or_promoted")

    return failures


__all__ = [
    "CORE_UNIVERSE_FIELDNAMES",
    "SEC_AVAILABILITY_STATUSES",
    "INCLUSION_STATUSES",
    "serialize_core_universe_row",
    "deserialize_core_universe_row",
    "write_core_universe_candidates_csv",
    "read_core_universe_candidates_csv",
    "validate_core_universe_row",
]

