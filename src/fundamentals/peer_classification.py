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

Date semantics (2026-09-19 design, points 4 and 5 -- do not conflate
these three):

    effective_from / effective_to
        When this classification is considered to describe the business,
        as a HALF-OPEN interval: [effective_from, effective_to). A row
        with effective_to = "2025-01-01" is expired exactly AT
        2025-01-01 -- that date itself already belongs to whatever comes
        next, not to this row.

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

SCHEMA_VERSION = "peer_classification_v0.1"


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

CLASSIFICATION_SOURCES = {"SIC_DERIVED", "MANUAL"}

CLASSIFICATION_STATUSES = {
    "REVIEWED",
    "PENDING_REVIEW",
    "SIC_DERIVED",
    "UNCLASSIFIED",
}

# classification_source describes WHERE a classification came from.
# classification_status describes its review/usability state. These are
# deliberately two separate axes (2026-09-19 design, point 1) -- but not
# every combination is meaningful. A MANUAL-sourced row sitting in
# SIC_DERIVED status is a contradiction (SIC_DERIVED status specifically
# means "mechanically derived, no human has touched this yet"); an
# UNCLASSIFIED row must have no source at all, since nothing was actually
# classified. validate_peer_mapping_row() rejects any combination not
# listed here.
_VALID_SOURCE_STATUS_COMBINATIONS = {
    ("SIC_DERIVED", "SIC_DERIVED"),
    ("SIC_DERIVED", "PENDING_REVIEW"),
    ("SIC_DERIVED", "REVIEWED"),
    ("MANUAL", "REVIEWED"),
    ("MANUAL", "PENDING_REVIEW"),
    (None, "UNCLASSIFIED"),
}

# Which classification_status values make a mapping usable for actual
# Primary Peer Group comparison, vs merely a review candidate (2026-09-19
# design, point 2). SIC_DERIVED is deliberately NOT given the same trust
# as a human-REVIEWED mapping for the default (primary) comparison mode --
# it is a candidate pool, not a confirmed peer group.
PRIMARY_COMPARISON_STATUSES = frozenset({"REVIEWED"})
CANDIDATE_COMPARISON_STATUSES = frozenset({"REVIEWED", "SIC_DERIVED"})


# ============================================================
# PEER ADEQUACY
# ============================================================

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

    # A mapping actually in force (REVIEWED or SIC_DERIVED -- i.e.
    # anything that could be looked up and used, even at the candidate
    # tier) must carry classification_available_date. reviewed_at is
    # never an acceptable substitute (2026-09-19 design, point 5).
    if status in ("REVIEWED", "SIC_DERIVED") and not row.get("classification_available_date"):
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
    only). Pass CANDIDATE_COMPARISON_STATUSES to also accept SIC_DERIVED
    candidates for non-primary, exploratory use.
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