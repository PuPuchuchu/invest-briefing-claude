"""
Managed Company Type registry and Point-in-Time Company Type lookup.

Why this module exists
-----------------------
STEP 14 implementation (2026-10-26), approved per the user's explicit
Production Implementation Authorization document ("STEP 14 -- Company
Type Source of Truth & Fail-Closed Validation").

Before this round, `company_type` ("NON_FINANCIAL" or "FINANCIAL") was a
bare, required, UNVALIDATED parameter accepted by both
sec_normalizer.normalize_company() and orchestrator.run_fundamentals_
pipeline() -- nothing in the production call path ever checked it
against any source of truth, and no source of truth for it existed
anywhere in this repository (confirmed across multiple audit rounds:
zero references to "company_type" in universe_membership.csv,
peer_groups.csv, issuer_identity.csv, or core_universe_io.py). The one
function capable of rejecting an invalid value,
sec_normalizer.validate_normalized_data(), was wired only into the
non-production normalize_from_cache() CLI path.

This module is where that gap is closed. It mirrors
src/fundamentals/peer_classification.py's get_peer_mapping_as_of()
pattern exactly -- same PIT gate shape, same LOOKUP_* status
granularity, same "callers own I/O, this module owns the domain logic"
convention (see data/reference/company_type.csv's own CSV; this module
does NOT read or write files itself).

Company Type registration is keyed by CIK (issuer), never ticker --
matching every other reference table in this repo (peer_groups.csv,
universe_membership.csv) for the same reason: Company Type is a
judgment about a BUSINESS, not about which ticker happens to trade it.

Approved Company Type values (V1, unchanged by this round): exactly
"NON_FINANCIAL" and "FINANCIAL" -- no third value is introduced here.
A ticker whose classification is genuinely unresolved (as of this
round: BLK, UNH) is simply NOT registered in data/reference/
company_type.csv -- it is never assigned a default, and any pipeline
call requiring its Company Type fails closed with LOOKUP_NO_MAPPING_FOR_DATE,
never a guessed value.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.fundamentals.point_in_time import coerce_evaluation_date

COMPANY_TYPE_VALUES = frozenset({"NON_FINANCIAL", "FINANCIAL"})

# 2026-10-26, STEP 14F-R3 (Decision Log DEC-022): closed, explicit
# allow-list for mapping_source. Exactly one value is defined:
#
#   "SEC_10K_BUSINESS_MODEL_EVIDENCE" -- the Company Type mapping is
#   supported by issuer-specific business-model evidence reviewed from
#   the issuer's SEC Form 10-K business description or functionally
#   equivalent business section; it is not based on SIC code alone.
#
# The former value "SIC_CODE_UNAMBIGUOUS_NON_FINANCIAL_DIVISION" is
# deliberately NOT accepted: under Decision B, SIC code alone is not
# sufficient evidence for a Company Type assignment. Values used by
# other tables' schemas (e.g. peer_classification.py's "MANUAL_REVIEW")
# are not borrowed here. Add a value only by a separate, explicitly
# approved schema decision.
MAPPING_SOURCES = frozenset({"SEC_10K_BUSINESS_MODEL_EVIDENCE"})

LOOKUP_OK = "OK"
LOOKUP_NO_MAPPING_FOR_DATE = "NO_MAPPING_FOR_DATE"
LOOKUP_MISSING_AVAILABILITY_DATE = "MISSING_AVAILABILITY_DATE"
LOOKUP_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
LOOKUP_OVERLAPPING_MAPPING = "OVERLAPPING_MAPPING"
LOOKUP_INVALID_REFERENCE = "INVALID_REFERENCE"


def _parse_date(value: Any) -> date | None:
    """Best-effort ISO-date parse. Returns None (never raises) on
    anything that isn't a clean ISO date string -- callers treat None
    as "could not be verified", never as "today" or any other silent
    default. Deliberately duplicated from peer_classification.py's own
    _parse_date rather than shared, matching that module's own
    documented convention of not sharing this trivial helper across
    reference-data modules."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def validate_company_type_row(row: dict) -> list[str]:
    """
    Validate one company_type.csv row's own schema and internal
    consistency. Does NOT check cross-row conditions (overlapping
    effective windows for the same CIK) -- see
    validate_company_type_reference_data() for that.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures: list[str] = []

    for key in ("cik", "company_type", "effective_from"):
        if not row.get(key):
            failures.append(key)

    company_type = row.get("company_type")
    if company_type is not None and company_type not in COMPANY_TYPE_VALUES:
        failures.append("company_type")

    if row.get("effective_from") and _parse_date(row.get("effective_from")) is None:
        failures.append("effective_from_unparseable")

    effective_to_raw = row.get("effective_to")
    if effective_to_raw and _parse_date(effective_to_raw) is None:
        failures.append("effective_to_unparseable")

    available_raw = row.get("classification_available_date")
    if available_raw and _parse_date(available_raw) is None:
        failures.append("classification_available_date_unparseable")

    for key in ("mapping_source", "mapping_rationale", "reviewed_at_utc"):
        if not row.get(key):
            failures.append(key)

    mapping_source = row.get("mapping_source")
    if mapping_source and mapping_source not in MAPPING_SOURCES:
        failures.append("mapping_source")

    return failures


def validate_company_type_reference_data(rows: list[dict]) -> dict[str, list[str]]:
    """
    Cross-row structural check: no two rows for the SAME cik may have
    overlapping [effective_from, effective_to) windows. Returns
    {cik: [overlap-failure-strings]} for every cik with a detected
    overlap; an empty dict means no cross-row overlap was found (this
    does NOT mean every individual row is itself valid -- run
    validate_company_type_row() on each row too).
    """
    by_cik: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        cik = row.get("cik")
        if not cik:
            continue
        by_cik.setdefault(cik, []).append(row)

    overlaps: dict[str, list[str]] = {}
    for cik, cik_rows in by_cik.items():
        structurally_valid = [r for r in cik_rows if not validate_company_type_row(r)]
        windows = []
        for row in structurally_valid:
            start = _parse_date(row.get("effective_from"))
            end_raw = row.get("effective_to")
            end = _parse_date(end_raw) if end_raw else None
            if start is not None:
                windows.append((start, end))

        for i in range(len(windows)):
            for j in range(i + 1, len(windows)):
                a_start, a_end = windows[i]
                b_start, b_end = windows[j]
                a_end_eff = a_end or date.max
                b_end_eff = b_end or date.max
                if a_start < b_end_eff and b_start < a_end_eff:
                    overlaps.setdefault(cik, []).append(
                        f"rows {i} and {j} overlap"
                    )

    return overlaps


def get_company_type_as_of(
    cik: str,
    evaluation_date: Any,
    company_type_rows: list[dict],
) -> dict:
    """
    Look up the single Company Type row for `cik` that was both
    business-effective AND actually available to use as of
    `evaluation_date`. Mirrors peer_classification.py's
    get_peer_mapping_as_of() exactly. This is the ONE place all of the
    following are enforced together -- callers (the orchestrator) must
    not reimplement any part of this gate elsewhere:

        effective_from <= evaluation_date < effective_to  (or
            effective_to is open-ended)
        AND classification_available_date <= evaluation_date
        AND company_type in COMPANY_TYPE_VALUES

    Never returns a single flat failure for every failure mode -- the
    caller needs to distinguish WHY a lookup didn't produce a usable
    Company Type (unregistered CIK, vs. not-yet-available, vs. a
    reference-data integrity problem). Returns a dict:

        {
            "lookup_status": one of the LOOKUP_* constants above,
            "mapping": the matched row (dict), or None,
            "reason": a human-readable string, or None on LOOKUP_OK,
        }
    """
    eval_date = coerce_evaluation_date(evaluation_date)

    candidate_rows = [
        row
        for row in company_type_rows
        if isinstance(row, dict) and row.get("cik") == cik
    ]

    if not candidate_rows:
        return {
            "lookup_status": LOOKUP_NO_MAPPING_FOR_DATE,
            "mapping": None,
            "reason": (
                f"No Company Type registration exists for CIK {cik!r}. "
                f"This Company Type is either genuinely unresolved "
                f"(e.g. BLK, UNH as of this round) or simply not yet "
                f"registered -- either way, no default is assumed."
            ),
        }

    structurally_valid = [
        row for row in candidate_rows if not validate_company_type_row(row)
    ]

    if not structurally_valid:
        return {
            "lookup_status": LOOKUP_INVALID_REFERENCE,
            "mapping": None,
            "reason": f"All Company Type rows for CIK {cik!r} failed structural validation.",
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
                f"{len(covering)} Company Type rows for CIK {cik!r} all claim to "
                f"cover {eval_date.isoformat()} -- this is a reference-data "
                f"integrity problem; run validate_company_type_reference_data()."
            ),
        }

    if not covering:
        return {
            "lookup_status": LOOKUP_NO_MAPPING_FOR_DATE,
            "mapping": None,
            "reason": f"No Company Type row for CIK {cik!r} covers {eval_date.isoformat()}.",
        }

    row = covering[0]

    available_raw = row.get("classification_available_date")
    if not available_raw:
        return {
            "lookup_status": LOOKUP_MISSING_AVAILABILITY_DATE,
            "mapping": row,
            "reason": (
                "Matched Company Type row has no classification_available_date -- "
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
                f"Company Type's effective period covers {eval_date.isoformat()}, "
                f"but it was not available for use until {available.isoformat()} -- "
                f"using it here would be a look-ahead-bias violation."
            ),
        }

    return {"lookup_status": LOOKUP_OK, "mapping": row, "reason": None}


__all__ = [
    "COMPANY_TYPE_VALUES",
    "MAPPING_SOURCES",
    "LOOKUP_OK",
    "LOOKUP_NO_MAPPING_FOR_DATE",
    "LOOKUP_MISSING_AVAILABILITY_DATE",
    "LOOKUP_NOT_YET_AVAILABLE",
    "LOOKUP_OVERLAPPING_MAPPING",
    "LOOKUP_INVALID_REFERENCE",
    "validate_company_type_row",
    "validate_company_type_reference_data",
    "get_company_type_as_of",
]
