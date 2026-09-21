"""
Universe Membership: which named universe(s) (Watchlist, Core, ...) a
business is currently, or was historically, a member of -- and, given a
CIK + universe_type + evaluation_date, the single Point-in-Time lookup
answering "was this business a member as of that date".

Why this module exists (2026-09-21 ChatGPT design round)
-----------------------------------------------------------
peer_groups.csv / peer_classification.py answer "what Peer Group does
this business's economics belong to" -- a classification question, not a
membership question. Historically the 21-stock Watchlist Universe itself
had no canonical, versioned record at all: it existed only as a plain
Python list hard-coded in scripts/seed_identity_reference.py (see that
script's module docstring), sourced from a memory document, with no
effective dates, no availability date, no review trail.

This module (and its I/O sibling, universe_membership_io.py) fixes that
by giving Universe Membership its own PIT-aware ledger, deliberately
built as a close structural mirror of peer_classification.py:

    "어디에 속하는가"      (what Peer Group)  -> peer_groups.csv
    "어디에서 평가 대상인가" (which Universe)   -> universe_membership.csv

Membership is NOT mutually exclusive across universe_type. The same CIK
can simultaneously be WATCHLIST + CORE (e.g. AVGO) -- these are two
independent ledger entries, not two states of one entry. Overlap
validation is therefore always scoped to (cik, universe_type), never to
cik alone (see validate_universe_membership_data() below).

membership_status (v1): ACTIVE only (2026-09-21 decision)
-----------------------------------------------------------
An earlier draft of this schema proposed ACTIVE / PENDING_REVIEW /
REMOVED, mirroring peer_classification's classification_status axis.
ChatGPT's final design review rejected that: this file is the CANONICAL
ledger of already-approved membership, so a not-yet-approved membership
has no business being a row here at all --

    - a PROPOSED-but-not-yet-approved candidate lives in
      core_universe_candidates.csv's inclusion_status lifecycle
      (CANDIDATE_PROPOSED -> ... -> PROMOTED) instead
    - a membership that has ENDED is expressed by setting effective_to on
      the existing ACTIVE row, never by writing a new row with a
      "REMOVED" status
    - a past membership period is reconstructed purely from an ACTIVE
      row's [effective_from, effective_to) interval, exactly like any
      other point-in-time lookup in this codebase

So MEMBERSHIP_STATUSES == {"ACTIVE"} for now. The enum can be widened
later if a real need for another membership state actually arises
(2026-09-21 instruction: "불필요하게 상태를 늘리지 않는다") -- it is
deliberately NOT pre-expanded on spec alone.

core_universe_candidates.csv's PROMOTED status (see that module's
2026-09-21 docstring update) means "Core Universe inclusion was approved"
-- it is a candidate-lifecycle fact, not a membership fact. The
CANONICAL record of actual Core membership is a
universe_type="CORE" row in *this* file; a PROMOTED candidate SHOULD have
a corresponding CORE+ACTIVE row here (a future consistency check, not
built this round -- see the module's own validation surface for what IS
checked now).

membership_source: provenance, not a status
-----------------------------------------------------------
    "LEGACY_SEED"    -- migrated from the pre-existing hard-coded
                        21-ticker Watchlist list (scripts/
                        seed_identity_reference.py), which carries NO
                        reliable historical effective-date evidence.
                        Per the 2026-09-21 instruction, a LEGACY_SEED
                        row's effective_from / membership_available_date
                        must be set to the ACTUAL MIGRATION DATE, never
                        backdated to when the ticker was first added to
                        that hard-coded list -- "현재 시스템에 기록된
                        날짜"와 "과거부터 실제로 membership이었다는
                        날짜"를 혼동하지 않는다. A more accurate interval
                        can be substituted later, as a new/amended
                        versioned row, once real historical evidence is
                        available.
    "MANUAL_REVIEW"   -- granted through this Framework's own explicit
                        review process (e.g. a promoted Core Universe
                        candidate).

Ticker cross-validation
-----------------------------------------------------------
`ticker` on a membership row is a denormalized convenience reference --
`cik` remains the only authoritative key (Peer Classification's own
long-standing principle: a business's identity is its CIK, not whichever
ticker happens to trade it today; see peer_classification.py's module
docstring). validate_universe_membership_data() below cross-checks that
the (ticker, cik) pair actually appears together in security_identity.csv
-- never assuming CIK<->ticker is 1:1, and never treating ticker as a
usable key on its own. security_identity.csv currently carries no
temporal-validity columns (identity_status/verified_at are unpopulated in
production data today), so this cross-check is NOT date-aware yet; it
will be extended to be date-aware once security_identity.csv actually
carries that information (2026-09-21 instruction).

Point-in-Time lookup key is explicitly cik + universe_type +
evaluation_date -- NEVER ticker (get_universe_membership_as_of() below).

Half-open interval convention, PIT lookup gating, and the private
_parse_date/_ranges_overlap helpers are all deliberately copied from
src/fundamentals/peer_classification.py rather than imported/shared, per
this repo's established per-domain-owns-its-own-date-helper convention
(see peer_classification.py's own module docstring on this point).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.fundamentals.point_in_time import coerce_evaluation_date

SCHEMA_VERSION = "universe_membership_v0.1"


# ============================================================
# DATE HELPERS (private to this module -- see module docstring)
# ============================================================

def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _ranges_overlap(
    start_a: date,
    end_a: date | None,
    start_b: date,
    end_b: date | None,
) -> bool:
    """Half-open interval overlap check: [start, end). end=None means
    open-ended (still in force)."""
    a_end = end_a if end_a is not None else date.max
    b_end = end_b if end_b is not None else date.max
    return start_a < b_end and start_b < a_end


# ============================================================
# CONFIGURATION / ALLOWED VALUES
# ============================================================

# Extensible later (e.g. a future non-US or ETF-tracked universe), but
# deliberately NOT pre-expanded on spec alone.
UNIVERSE_TYPES = frozenset({"WATCHLIST", "CORE"})

# v1: ACTIVE only -- see module docstring for why PENDING_REVIEW/REMOVED
# were deliberately rejected for this file.
MEMBERSHIP_STATUSES = frozenset({"ACTIVE"})

# Provenance, not a status -- see module docstring.
MEMBERSHIP_SOURCES = frozenset({"LEGACY_SEED", "MANUAL_REVIEW"})


# ============================================================
# ROW-LEVEL VALIDATION
# ============================================================

def validate_universe_membership_row(row: dict) -> list[str]:
    """
    Validate one universe_membership.csv row's own schema and internal
    consistency. Does NOT check cross-row or cross-file conditions
    (duplicate membership_version, overlapping effective dates, unknown
    CIK, ticker/CIK linkage) -- see validate_universe_membership_data()
    for those.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures: list[str] = []

    for key in (
        "cik",
        "ticker",
        "universe_type",
        "membership_status",
        "membership_source",
        "membership_version",
        "effective_from",
        "membership_available_date",
    ):
        if not row.get(key):
            failures.append(key)

    universe_type = row.get("universe_type")
    if universe_type is not None and universe_type not in UNIVERSE_TYPES:
        failures.append("universe_type")

    membership_status = row.get("membership_status")
    if membership_status is not None and membership_status not in MEMBERSHIP_STATUSES:
        failures.append("membership_status")

    membership_source = row.get("membership_source")
    if membership_source is not None and membership_source not in MEMBERSHIP_SOURCES:
        failures.append("membership_source")

    effective_from = _parse_date(row.get("effective_from"))
    if row.get("effective_from") and effective_from is None:
        failures.append("effective_from_format")

    effective_to_raw = row.get("effective_to")
    effective_to = None
    if effective_to_raw:
        effective_to = _parse_date(effective_to_raw)
        if effective_to is None:
            failures.append("effective_to_format")

    if effective_from is not None and effective_to is not None and effective_from > effective_to:
        failures.append("effective_from_after_effective_to")

    available_raw = row.get("membership_available_date")
    if available_raw and _parse_date(available_raw) is None:
        failures.append("membership_available_date_format")

    reviewed_at_raw = row.get("reviewed_at")
    if reviewed_at_raw and _parse_date(reviewed_at_raw) is None:
        failures.append("reviewed_at_format")

    return failures


# ============================================================
# CROSS-ROW / CROSS-FILE VALIDATION
# ============================================================

def validate_universe_membership_data(
    issuer_rows: list[dict],
    security_rows: list[dict],
    membership_rows: list[dict],
) -> list[str]:
    """
    Validate the full Universe Membership ledger: every
    universe_membership.csv row's own schema (via
    validate_universe_membership_row), plus cross-row/cross-file
    conditions no single row can be checked for in isolation:

        - every membership_rows[i].cik must exist in issuer_rows (Stage 1
          CIK existence check, same as peer_classification.py's
          validate_reference_data() -- Stage 2 provenance is out of
          scope here, as there)
        - every membership_rows[i].(ticker, cik) pair must actually
          appear together in security_rows -- ticker is never trusted as
          a standalone key (see module docstring)
        - no two rows for the same (cik, universe_type) may have
          overlapping [effective_from, effective_to) windows --
          overlap is scoped to (cik, universe_type), NOT to cik alone,
          since WATCHLIST + CORE membership for the same CIK is expected
          to coexist
        - membership_version must be unique within a given
          (cik, universe_type) pair, not globally

    Returns:
        [] when valid
        list[str] of failure identifiers, each prefixed with its row
        index for traceability (e.g.
        "membership_rows[3].membership_status")
    """
    failures: list[str] = []

    issuer_ciks = {row.get("cik") for row in issuer_rows if row.get("cik")}
    security_pairs = {
        (row.get("ticker"), row.get("cik"))
        for row in security_rows
        if row.get("ticker") and row.get("cik")
    }

    seen_versions: dict[tuple[str, str, str], int] = {}
    rows_by_cik_type: dict[tuple[str, str], list[tuple[int, dict]]] = {}

    for index, row in enumerate(membership_rows):
        for row_failure in validate_universe_membership_row(row):
            failures.append(f"membership_rows[{index}].{row_failure}")

        if not isinstance(row, dict):
            continue

        cik = row.get("cik")
        ticker = row.get("ticker")
        universe_type = row.get("universe_type")

        if cik and issuer_ciks and cik not in issuer_ciks:
            failures.append(f"membership_rows[{index}].unknown_cik")

        if ticker and cik and security_pairs and (ticker, cik) not in security_pairs:
            failures.append(f"membership_rows[{index}].ticker_not_linked_to_cik")

        if cik and universe_type:
            rows_by_cik_type.setdefault((cik, universe_type), []).append((index, row))

            version = row.get("membership_version")
            if version:
                key = (cik, universe_type, version)
                if key in seen_versions:
                    failures.append(f"membership_rows[{index}].duplicate_membership_version")
                else:
                    seen_versions[key] = index

    for (cik, universe_type), rows in rows_by_cik_type.items():
        parsed = []
        for index, row in rows:
            start = _parse_date(row.get("effective_from"))
            if start is None:
                continue
            end_raw = row.get("effective_to")
            end = _parse_date(end_raw) if end_raw else None
            parsed.append((index, start, end))

        for i in range(len(parsed)):
            for j in range(i + 1, len(parsed)):
                idx_a, start_a, end_a = parsed[i]
                idx_b, start_b, end_b = parsed[j]
                if _ranges_overlap(start_a, end_a, start_b, end_b):
                    failures.append(
                        f"membership_rows[{idx_a}].overlaps_membership_rows[{idx_b}]"
                    )

    return failures


# ============================================================
# POINT-IN-TIME MEMBERSHIP LOOKUP
# ============================================================

LOOKUP_OK = "OK"
LOOKUP_NO_MEMBERSHIP_FOR_DATE = "NO_MEMBERSHIP_FOR_DATE"
LOOKUP_MISSING_AVAILABILITY_DATE = "MISSING_AVAILABILITY_DATE"
LOOKUP_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
LOOKUP_OVERLAPPING_MEMBERSHIP = "OVERLAPPING_MEMBERSHIP"
LOOKUP_INVALID_REFERENCE = "INVALID_REFERENCE"


def get_universe_membership_as_of(
    cik: str,
    universe_type: str,
    evaluation_date: Any,
    membership_rows: list[dict],
) -> dict:
    """
    Look up the single Universe Membership row for (`cik`, `universe_type`)
    that was both effective AND actually available to use as of
    `evaluation_date`. Mirrors peer_classification.py's
    get_peer_mapping_as_of() exactly, minus the usable-status-set
    complexity: MEMBERSHIP_STATUSES has only one value (ACTIVE), so any
    row that passes validate_universe_membership_row() already has
    membership_status == "ACTIVE" by construction -- no separate
    "matched but not usable" lookup state is needed (2026-09-21
    instruction: "불필요한 status state를 추가하지 않는다").

    The gate enforced here, all in one place:

        effective_from <= evaluation_date < effective_to  (or effective_to
            is open-ended)
        AND membership_available_date <= evaluation_date

    PIT lookup key is (cik, universe_type, evaluation_date) -- never
    ticker (see module docstring).

    Returns a dict:

        {
            "lookup_status": one of the LOOKUP_* constants above,
            "membership": the matched row (dict), or None,
            "reason": a human-readable string, or None on LOOKUP_OK,
        }
    """
    eval_date = coerce_evaluation_date(evaluation_date)

    candidate_rows = [
        row
        for row in membership_rows
        if isinstance(row, dict)
        and row.get("cik") == cik
        and row.get("universe_type") == universe_type
    ]

    if not candidate_rows:
        return {
            "lookup_status": LOOKUP_NO_MEMBERSHIP_FOR_DATE,
            "membership": None,
            "reason": (
                f"No universe_membership rows exist at all for CIK {cik!r} "
                f"in universe_type {universe_type!r}."
            ),
        }

    structurally_valid = [
        row for row in candidate_rows if not validate_universe_membership_row(row)
    ]

    if not structurally_valid:
        return {
            "lookup_status": LOOKUP_INVALID_REFERENCE,
            "membership": None,
            "reason": (
                f"All universe_membership rows for CIK {cik!r} / "
                f"{universe_type!r} failed structural validation."
            ),
        }

    covering = []
    for row in structurally_valid:
        start = _parse_date(row.get("effective_from"))
        end_raw = row.get("effective_to")
        end = _parse_date(end_raw) if end_raw else None
        if start is None:
            continue
        if start <= eval_date and (end is None or eval_date < end):
            covering.append(row)

    if len(covering) > 1:
        return {
            "lookup_status": LOOKUP_OVERLAPPING_MEMBERSHIP,
            "membership": None,
            "reason": (
                f"{len(covering)} universe_membership rows for CIK {cik!r} / "
                f"{universe_type!r} all claim to cover {eval_date.isoformat()} "
                f"-- this is a reference-data integrity problem; run "
                f"validate_universe_membership_data()."
            ),
        }

    if not covering:
        return {
            "lookup_status": LOOKUP_NO_MEMBERSHIP_FOR_DATE,
            "membership": None,
            "reason": (
                f"No universe_membership row for CIK {cik!r} / "
                f"{universe_type!r} covers {eval_date.isoformat()}."
            ),
        }

    row = covering[0]

    available_raw = row.get("membership_available_date")
    if not available_raw:
        return {
            "lookup_status": LOOKUP_MISSING_AVAILABILITY_DATE,
            "membership": row,
            "reason": (
                "Matched membership row has no membership_available_date -- "
                "cannot verify it was actually usable at evaluation_date, "
                "so it is never assumed available."
            ),
        }

    available = _parse_date(available_raw)
    if available is None:
        return {
            "lookup_status": LOOKUP_MISSING_AVAILABILITY_DATE,
            "membership": row,
            "reason": f"membership_available_date {available_raw!r} could not be parsed as an ISO date.",
        }

    if available > eval_date:
        return {
            "lookup_status": LOOKUP_NOT_YET_AVAILABLE,
            "membership": row,
            "reason": (
                f"Membership's effective period covers {eval_date.isoformat()}, "
                f"but it was not available for use until "
                f"{available.isoformat()} -- using it here would be a "
                f"look-ahead-bias violation."
            ),
        }

    return {"lookup_status": LOOKUP_OK, "membership": row, "reason": None}


__all__ = [
    "SCHEMA_VERSION",
    "UNIVERSE_TYPES",
    "MEMBERSHIP_STATUSES",
    "MEMBERSHIP_SOURCES",
    "validate_universe_membership_row",
    "validate_universe_membership_data",
    "LOOKUP_OK",
    "LOOKUP_NO_MEMBERSHIP_FOR_DATE",
    "LOOKUP_MISSING_AVAILABILITY_DATE",
    "LOOKUP_NOT_YET_AVAILABLE",
    "LOOKUP_OVERLAPPING_MEMBERSHIP",
    "LOOKUP_INVALID_REFERENCE",
    "get_universe_membership_as_of",
]

