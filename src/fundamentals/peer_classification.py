"""
Managed Peer Mapping, Point-in-Time Peer Lookup, and Reference Data
integrity validation.

Why this module exists
-----------------------
Framework v2.0 step 9 (Peer Classification), finalized 2026-09-19 across
several rounds of design review. The load-bearing principle, stated by the
design review itself:

    SIC = Raw Source (src/sec/identity.py's job)
    Peer Group = Managed Analytical Classification (THIS module's job)
    Peer Eligibility = Metric/evaluation-date-specific verification layer

This module does NOT call the SEC API and does NOT read or write files --
it operates purely on already-loaded row lists (dicts), matching this
repo's established convention (see e.g. src/fundamentals/sec_history.py):
callers own I/O, this module owns the domain logic on top of it.

Peer Mapping is keyed by CIK (issuer), not ticker (security) -- Peer
Classification is a judgment about a BUSINESS, not about which ticker
happens to trade it (see src/sec/identity.py's module docstring for why
those are different things).

2026-09-20 revision (ChatGPT design round, applied by 명호's explicit
direction)
------------------------------------------------------------------------
The original 2026-09-19 design used classification_status = "SIC_DERIVED"
as both a *source* label and a *review-state* label at once -- a row
mechanically derived from SIC sat in a status literally named
"SIC_DERIVED", conflating "where this classification came from" with
"how trustworthy/reviewed it is". This round removes that overload:

    classification_source  -- HOW a row's sector/industry/peer_group was
                               produced: "SEC_SIC" (mechanically derived
                               from the raw SIC code, no judgment) or
                               "MANUAL_REVIEW" (a human, informed by
                               ChatGPT's economic analysis, chose it).
                               ("RULE_BASED" is reserved for a future
                               automated-but-non-SIC classifier and is
                               deliberately NOT added to
                               CLASSIFICATION_SOURCES yet -- there is no
                               such classifier implemented.)

    classification_status  -- ONLY the review-state: "REVIEWED" (human-
                               confirmed, usable for Primary comparison),
                               "PENDING_REVIEW" (a row exists -- source
                               may be SEC_SIC or MANUAL_REVIEW -- but has
                               not been confirmed), or "UNCLASSIFIED"
                               (nothing at all yet).

A second, independent change (same round): whether a Peer Group is
REVIEWED must never be conflated with whether that Peer Group currently
has enough OTHER watchlist members to compare against. A single-member
Peer Group (e.g. "Defense Primes" today has only LMT in the 21-stock
Watchlist Universe) can still be REVIEWED -- its economic classification
is settled even though its *current comparison coverage*, tracked
separately as peer_adequacy_status (see classify_peer_adequacy() /
build_comparison_peers() below), is INSUFFICIENT_PEERS. Widening that
coverage is the job of a future Core Market Universe expansion (adding
real, financial-data-tracked external peers) -- deliberately OUT OF SCOPE
for this module and for this design round. See
scripts/seed_identity_reference.py's module docstring for the Watchlist
Universe vs Core Market Universe distinction this implies.

Date semantics (2026-09-19 design, points 4 and 5 -- do not conflate
these three; next_review_due below is a fourth, deliberately separate,
concept added 2026-09-20):

    effective_from / effective_to
        When this classification is considered to describe the business,
        as a HALF-OPEN interval: [effective_from, effective_to). A row
        with effective_to = "2025-01-01" is expired exactly AT
        2025-01-01 -- that date itself already belongs to whatever comes
        next, not to this row. effective_to is set ONLY when the
        classification actually stopped describing the business (a real
        reclassification event -- M&A, spinoff, segment reorg, SIC
        change, etc.), never as a stand-in for a review deadline.

    classification_available_date
        When this classification became something the SYSTEM could
        actually have used -- i.e. when a human reviewed/approved it, or
        when it was mechanically derived. This is deliberately a
        SEPARATE, explicitly-required column, never inferred from
        reviewed_at or from effective_from. A mapping that covers
        evaluation_date on the effective_from/effective_to axis but whose
        classification_available_date is missing, unparseable, or AFTER
        evaluation_date must never be treated as usable -- see
        get_peer_mapping_as_of() below, which is the single place this
        two-part gate is enforced.

    next_review_due (2026-09-20)
        When this classification is next SCHEDULED to be re-examined --
        completely separate from effective_to. Baseline cadence is
        semi-annual (반기); an event trigger (new watchlist addition,
        IPO, business-structure change, segment reorg, M&A/spinoff, SIC
        change, economic peer relationship shift) can force an earlier
        review regardless of what next_review_due says. Reaching
        next_review_due does NOT expire the mapping and does NOT require
        closing effective_to -- a row can sail past its next_review_due
        date and remain fully in force (effective_to still None) until
        an actual reclassification event happens. This column is
        advisory/scheduling metadata only; get_peer_mapping_as_of() below
        does not read it at all.

    evaluation_date
        The point-in-time an evaluation is being made as of (caller-
        supplied, via src.fundamentals.point_in_time.coerce_evaluation_date).

This mirrors, deliberately, the exact same point-in-time discipline
src/fundamentals/point_in_time.py already enforces for XBRL observations
(filed <= evaluation_date, fail closed on anything unparseable or
missing) -- this module reuses that module's coerce_evaluation_date
directly, but does NOT reuse get_point_in_time_history() itself, which is
tightly coupled to XBRL concept lookup and reconstruction and has nothing
to do with reference-table row gating. Per this repo's established
convention (each domain owns its own private date-parsing helper -- see
sec_history.py and point_in_time.py, which each have their own private
_parse_date rather than sharing one), this module has its own _parse_date
too.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.fundamentals.point_in_time import coerce_evaluation_date

SCHEMA_VERSION = "peer_classification_v0.2"


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

# 2026-09-20: RULE_BASED intentionally NOT included yet -- no automated,
# non-SIC classifier exists in this codebase. Add it only when that
# classifier is actually implemented (see module docstring).
CLASSIFICATION_SOURCES = {"SEC_SIC", "MANUAL_REVIEW"}

# 2026-09-20: "SIC_DERIVED" removed as a status value -- it described a
# classification METHOD, not a REVIEW STATE, and belongs on
# classification_source instead (as "SEC_SIC"). A mechanically-derived
# row now sits in PENDING_REVIEW status until a human confirms it.
CLASSIFICATION_STATUSES = {
    "REVIEWED",
    "PENDING_REVIEW",
    "UNCLASSIFIED",
}

# classification_source describes WHERE a classification came from.
# classification_status describes its review/usability state. These are
# deliberately two separate axes (2026-09-19 design, point 1) -- but not
# every combination is meaningful. An UNCLASSIFIED row must have no
# source at all, since nothing was actually classified.
# validate_peer_mapping_row() rejects any combination not listed here.
_VALID_SOURCE_STATUS_COMBINATIONS = {
    ("SEC_SIC", "PENDING_REVIEW"),
    ("SEC_SIC", "REVIEWED"),
    ("MANUAL_REVIEW", "PENDING_REVIEW"),
    ("MANUAL_REVIEW", "REVIEWED"),
    (None, "UNCLASSIFIED"),
}

# Which classification_status values make a mapping usable for actual
# Primary Peer Group comparison, vs merely a review candidate (2026-09-19
# design, point 2). PENDING_REVIEW -- whether its source is SEC_SIC
# (mechanically derived) or MANUAL_REVIEW (proposed but not yet
# confirmed) -- is deliberately NOT given the same trust as a human-
# REVIEWED mapping for the default (primary) comparison mode; it is a
# candidate pool, not a confirmed peer group.
PRIMARY_COMPARISON_STATUSES = frozenset({"REVIEWED"})
CANDIDATE_COMPARISON_STATUSES = frozenset({"REVIEWED", "PENDING_REVIEW"})


# ============================================================
# PEER ADEQUACY
# ============================================================
#
# IMPORTANT -- peer_adequacy_status (below, and as returned by
# build_comparison_peers()) is a COMPLETELY SEPARATE axis from
# classification_status. classification_status answers "has this
# business's Peer Group been economically reviewed and confirmed?";
# peer_adequacy_status answers "how many OTHER members of that (already-
# confirmed) Peer Group currently exist inside the comparison universe
# passed in?". A row can be classification_status="REVIEWED" with
# peer_adequacy_status="INSUFFICIENT_PEERS" at the same time -- e.g. "AI
# Cloud Infrastructure" (CRWV) or "Defense Primes" (LMT) are both settled,
# confirmed classifications in the 21-stock Watchlist Universe that
# simply don't have another watchlist member sharing them yet. This is
# expected and correct; it is not a reason to leave the classification
# itself at PENDING_REVIEW. Widening peer_adequacy_status for these
# groups is a Core Market Universe scope question (adding real, financial
# -data-tracked external peers), not a re-classification question.

def classify_peer_adequacy(valid_peer_count: int) -> str:
    """
    Peer-count-to-status mapping. These thresholds are an OPERATIONAL
    policy for this system, not a claim of universal statistical validity
    (2026-09-19 design, point 7 -- there is no single sample size at which
    percentile ranking becomes "statistically valid" in general).
    """
    if valid_peer_count <= 4:
        return "INSUFFICIENT_PEERS"
    if valid_peer_count <= 7:
        return "LIMITED_PEERS"
    if valid_peer_count <= 14:
        return "SUFFICIENT_PEERS"
    return "ROBUST_PEERS"


# ============================================================
# ROW-LEVEL VALIDATION
# ============================================================

def validate_peer_mapping_row(row: dict) -> list[str]:
    """
    Validate one peer_groups.csv row's own schema and internal
    consistency. Does NOT check cross-row conditions (duplicate
    mapping_version, overlapping effective dates, unknown CIK) -- see
    validate_reference_data() for those.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures: list[str] = []

    for key in ("cik", "mapping_version", "classification_status", "effective_from"):
        if not row.get(key):
            failures.append(key)

    status = row.get("classification_status")
    if status not in CLASSIFICATION_STATUSES:
        failures.append("classification_status")

    source = row.get("classification_source") or None
    if source is not None and source not in CLASSIFICATION_SOURCES:
        failures.append("classification_source")

    if (source, status) not in _VALID_SOURCE_STATUS_COMBINATIONS:
        failures.append("classification_source_status_combination")

    peer_group = row.get("peer_group")
    if status == "REVIEWED" and not peer_group:
        failures.append("peer_group_missing_for_reviewed")
    if status == "UNCLASSIFIED" and peer_group:
        failures.append("peer_group_present_for_unclassified")

    # A mapping actually in force (REVIEWED or PENDING_REVIEW -- i.e.
    # anything that could be looked up and used, even at the candidate
    # tier via CANDIDATE_COMPARISON_STATUSES) must carry
    # classification_available_date. reviewed_at is never an acceptable
    # substitute (2026-09-19 design, point 5).
    if status in ("REVIEWED", "PENDING_REVIEW") and not row.get("classification_available_date"):
        failures.append("classification_available_date_missing")

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

    available_raw = row.get("classification_available_date")
    if available_raw and _parse_date(available_raw) is None:
        failures.append("classification_available_date_format")

    # 2026-09-20: next_review_due is advisory scheduling metadata (see
    # module docstring) -- never required, but if present it must parse
    # as an ISO date, same as effective_to.
    next_review_due_raw = row.get("next_review_due")
    if next_review_due_raw and _parse_date(next_review_due_raw) is None:
        failures.append("next_review_due_format")

    return failures


# ============================================================
# CROSS-ROW / REFERENCE-DATA-WIDE VALIDATION
# ============================================================

def validate_reference_data(
    issuer_rows: list[dict],
    peer_rows: list[dict],
) -> list[str]:
    """
    Validate the full Reference Data set: every peer_groups.csv row's own
    schema (via validate_peer_mapping_row), plus cross-row conditions that
    no single row can be checked for in isolation:

        - every peer_rows[i].cik must exist in issuer_rows (Stage 1 of the
          two-stage CIK existence check -- Stage 2, provenance, is
          src.sec.identity.validate_issuer_identity_provenance)
        - no two rows for the same CIK may have overlapping
          [effective_from, effective_to) windows
        - mapping_version must be unique within a given CIK

    Returns:
        [] when valid
        list[str] of failure identifiers, each prefixed with its row
        index for traceability (e.g. "peer_rows[3].classification_status")
    """
    failures: list[str] = []

    issuer_ciks = {row.get("cik") for row in issuer_rows if row.get("cik")}

    seen_versions: dict[tuple[str, str], int] = {}
    rows_by_cik: dict[str, list[tuple[int, dict]]] = {}

    for index, row in enumerate(peer_rows):
        for row_failure in validate_peer_mapping_row(row):
            failures.append(f"peer_rows[{index}].{row_failure}")

        cik = row.get("cik") if isinstance(row, dict) else None

        if cik and issuer_ciks and cik not in issuer_ciks:
            failures.append(f"peer_rows[{index}].unknown_cik")

        if cik:
            rows_by_cik.setdefault(cik, []).append((index, row))

            version = row.get("mapping_version")
            if version:
                key = (cik, version)
                if key in seen_versions:
                    failures.append(f"peer_rows[{index}].duplicate_mapping_version")
                else:
                    seen_versions[key] = index

    for cik, rows in rows_by_cik.items():
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
                        f"peer_rows[{idx_a}].overlaps_peer_rows[{idx_b}]"
                    )

    return failures


# ============================================================
# POINT-IN-TIME PEER LOOKUP
# ============================================================

LOOKUP_OK = "OK"
LOOKUP_NO_MAPPING_FOR_DATE = "NO_MAPPING_FOR_DATE"
LOOKUP_UNVERIFIED = "UNVERIFIED"
LOOKUP_MISSING_AVAILABILITY_DATE = "MISSING_AVAILABILITY_DATE"
LOOKUP_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
LOOKUP_OVERLAPPING_MAPPING = "OVERLAPPING_MAPPING"
LOOKUP_INVALID_REFERENCE = "INVALID_REFERENCE"


def get_peer_mapping_as_of(
    cik: str,
    evaluation_date: Any,
    peer_rows: list[dict],
    *,
    usable_statuses: frozenset[str] = PRIMARY_COMPARISON_STATUSES,
) -> dict:
    """
    Look up the single Peer Mapping row for `cik` that was both
    business-effective AND actually available to use as of
    `evaluation_date`. This is the ONE place all of the following are
    enforced together -- callers must not reimplement any part of this
    gate elsewhere:

        effective_from <= evaluation_date < effective_to  (or effective_to
            is open-ended)
        AND classification_available_date <= evaluation_date
        AND classification_status in usable_statuses

    Never returns a single flat "UNVERIFIED" for every failure mode --
    per the 2026-09-19 design review, the caller needs to distinguish WHY
    a lookup didn't produce a usable mapping. Returns a dict:

        {
            "lookup_status": one of the LOOKUP_* constants above,
            "mapping": the matched row (dict), or None,
            "reason": a human-readable string, or None on LOOKUP_OK,
        }

    usable_statuses defaults to PRIMARY_COMPARISON_STATUSES (REVIEWED
    only). Pass CANDIDATE_COMPARISON_STATUSES to also accept
    PENDING_REVIEW candidates for non-primary, exploratory use.
    """
    eval_date = coerce_evaluation_date(evaluation_date)

    candidate_rows = [
        row for row in peer_rows if isinstance(row, dict) and row.get("cik") == cik
    ]

    if not candidate_rows:
        return {
            "lookup_status": LOOKUP_NO_MAPPING_FOR_DATE,
            "mapping": None,
            "reason": f"No peer mapping rows exist at all for CIK {cik!r}.",
        }

    structurally_valid = [
        row for row in candidate_rows if not validate_peer_mapping_row(row)
    ]

    if not structurally_valid:
        return {
            "lookup_status": LOOKUP_INVALID_REFERENCE,
            "mapping": None,
            "reason": f"All peer mapping rows for CIK {cik!r} failed structural validation.",
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
            "lookup_status": LOOKUP_OVERLAPPING_MAPPING,
            "mapping": None,
            "reason": (
                f"{len(covering)} peer mapping rows for CIK {cik!r} all claim to "
                f"cover {eval_date.isoformat()} -- this is a reference-data "
                f"integrity problem; run validate_reference_data()."
            ),
        }

    if not covering:
        return {
            "lookup_status": LOOKUP_NO_MAPPING_FOR_DATE,
            "mapping": None,
            "reason": f"No peer mapping row for CIK {cik!r} covers {eval_date.isoformat()}.",
        }

    row = covering[0]

    available_raw = row.get("classification_available_date")
    if not available_raw:
        return {
            "lookup_status": LOOKUP_MISSING_AVAILABILITY_DATE,
            "mapping": row,
            "reason": (
                "Matched mapping row has no classification_available_date -- "
                "cannot verify it was actually usable at evaluation_date, "
                "so it is never assumed available."
            ),
        }

    available = _parse_date(available_raw)
    if available is None:
        return {
            "lookup_status": LOOKUP_MISSING_AVAILABILITY_DATE,
            "mapping": row,
            "reason": f"classification_available_date {available_raw!r} could not be parsed as an ISO date.",
        }

    if available > eval_date:
        return {
            "lookup_status": LOOKUP_NOT_YET_AVAILABLE,
            "mapping": row,
            "reason": (
                f"Mapping's effective period covers {eval_date.isoformat()}, but it "
                f"was not available for use until {available.isoformat()} -- using it "
                f"here would be a look-ahead-bias violation."
            ),
        }

    status = row.get("classification_status")
    if status not in usable_statuses:
        return {
            "lookup_status": LOOKUP_UNVERIFIED,
            "mapping": row,
            "reason": (
                f"Matched mapping has classification_status={status!r}, which is "
                f"not in the usable set {sorted(usable_statuses)} for this "
                f"comparison mode."
            ),
        }

    return {"lookup_status": LOOKUP_OK, "mapping": row, "reason": None}


# ============================================================
# COMPARISON SET (self-exclusion)
# ============================================================

def build_comparison_peers(target_cik: str, peer_group_members: list[str]) -> dict:
    """
    peer_group_members: every CIK sharing the target's peer_group,
    INCLUDING target_cik itself (Peer Group Membership includes self --
    2026-09-19 design). The returned comparison set EXCLUDES target_cik,
    so a company is never compared against itself.

    peer_adequacy_status here reflects ONLY how many other CIKs were
    passed in `peer_group_members` -- it says nothing about whether the
    Peer Group itself is REVIEWED (that is classification_status, on the
    peer_groups.csv row, checked separately via get_peer_mapping_as_of()).
    See the "PEER ADEQUACY" section comment above for why these two axes
    must never be conflated.
    """
    peer_group_size = len(peer_group_members)
    comparison_peers = [cik for cik in peer_group_members if cik != target_cik]
    comparison_peer_count = len(comparison_peers)

    return {
        "peer_group_size": peer_group_size,
        "comparison_peers": comparison_peers,
        "comparison_peer_count": comparison_peer_count,
        "peer_adequacy_status": classify_peer_adequacy(comparison_peer_count),
    }


__all__ = [
    "SCHEMA_VERSION",
    "CLASSIFICATION_SOURCES",
    "CLASSIFICATION_STATUSES",
    "PRIMARY_COMPARISON_STATUSES",
    "CANDIDATE_COMPARISON_STATUSES",
    "LOOKUP_OK",
    "LOOKUP_NO_MAPPING_FOR_DATE",
    "LOOKUP_UNVERIFIED",
    "LOOKUP_MISSING_AVAILABILITY_DATE",
    "LOOKUP_NOT_YET_AVAILABLE",
    "LOOKUP_OVERLAPPING_MAPPING",
    "LOOKUP_INVALID_REFERENCE",
    "classify_peer_adequacy",
    "validate_peer_mapping_row",
    "validate_reference_data",
    "get_peer_mapping_as_of",
    "build_comparison_peers",
]
