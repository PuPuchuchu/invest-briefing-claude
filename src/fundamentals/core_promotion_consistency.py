"""
Cross-file consistency check between data/reference/core_universe_candidates.csv
(the candidate promotion lifecycle, src/fundamentals/core_universe_io.py) and
data/reference/universe_membership.csv (the canonical Universe Membership
ledger, src/fundamentals/universe_membership.py).

Why this module exists
-----------------------
Both files describe the same underlying fact -- "is this CIK a Core
Universe member" -- from two different angles: core_universe_candidates.csv
tracks the human/ChatGPT REVIEW DECISION (inclusion_status=PROMOTED means
"Core Universe inclusion was approved"), while universe_membership.csv
tracks the ACTUAL, point-in-time-gated membership fact (a
universe_type="CORE", membership_status="ACTIVE" row that is currently
effective and available). Per universe_membership.py's own module
docstring: "a PROMOTED candidate SHOULD have a corresponding CORE+ACTIVE
row here (a future consistency check, not built this round)" -- this
module is that consistency check, confirmed in scope by the 2026-09-21
ChatGPT design round.

Nothing in this codebase currently cross-references these two files
programmatically (confirmed by grep across src/ and scripts/ before this
module was written) -- the two files staying in sync today (AVGO is the
only PROMOTED candidate and the only CIK with a CORE+ACTIVE row) is
purely a result of careful manual bookkeeping, not a guarantee this
module makes automatic. This module only DETECTS drift; it never
corrects it.

Explicitly out of scope (2026-09-21 instruction)
---------------------------------------------------
- No automatic mutation of either file's status/membership rows -- this
  module is read-only and returns failure identifiers, exactly like
  every other validate_*() function in this codebase
  (peer_classification.validate_reference_data(),
  universe_membership.validate_universe_membership_data(), etc.)
- No change to production_eligibility's meaning or to the Percentile
  Engine -- see core_universe_io.py's own module docstring and
  percentile_engine.py's module docstring for that boundary.
- No new INCLUSION_STATUSES or MEMBERSHIP_STATUSES values.

Point-in-Time correctness
----------------------------
"Is CIK X currently a CORE member" is itself a PIT question -- a CORE
membership row can exist in universe_membership.csv but not yet be
effective (effective_from in the future), not yet available
(membership_available_date in the future), or already expired
(effective_to in the past), or ambiguous (two overlapping rows for the
same (cik, universe_type)). This module therefore never scans
membership_rows by hand -- every "is this CIK a CORE member as of
evaluation_date" question is delegated to
universe_membership.get_universe_membership_as_of(), so this module
inherits that function's full PIT gating (effective interval,
availability date, overlap detection) for free and cannot silently
diverge from it.

Two independent directions are checked, both PIT-gated by the same
evaluation_date:

    1. Every PROMOTED candidate (in candidate_rows) must have a CORE
       membership row that is actually OK (LOOKUP_OK) as of
       evaluation_date. Any other lookup_status (no membership, not yet
       available, expired/no coverage, overlapping/ambiguous, or
       structurally invalid) is a failure -- PROMOTED with no
       real, currently-effective CORE membership is exactly the drift
       this module exists to catch.
    2. Every CIK that DOES have an OK (LOOKUP_OK) CORE membership as of
       evaluation_date must have a corresponding candidate_rows entry
       whose inclusion_status is PROMOTED. A CIK with active CORE
       membership but no PROMOTED candidate row at all (orphan
       membership, or a candidate row that regressed to some other
       status) is flagged the same way.

Ambiguous membership (two overlapping CORE rows for the same CIK,
LOOKUP_OVERLAPPING_MEMBERSHIP) is flagged as its own failure in
whichever direction it is encountered, distinct from an ordinary
missing-membership mismatch -- the difference between "no evidence this
promotion is backed by membership" and "the membership ledger itself is
internally inconsistent for this CIK" is useful for a human resolving
the failure list.
"""

from __future__ import annotations

from typing import Any

from src.fundamentals.universe_membership import (
    LOOKUP_OK,
    LOOKUP_OVERLAPPING_MEMBERSHIP,
    get_universe_membership_as_of,
)

SCHEMA_VERSION = "core_promotion_consistency_v0.1"

# The single universe_type this module cares about -- Core Universe
# promotion, never Watchlist (a candidate is never "promoted" into the
# Watchlist; that membership type is LEGACY_SEED/MANUAL_REVIEW only, see
# universe_membership.py's module docstring).
CORE_UNIVERSE_TYPE = "CORE"

PROMOTED_STATUS = "PROMOTED"


def validate_promotion_consistency(
    candidate_rows: list[dict],
    membership_rows: list[dict],
    evaluation_date: Any,
) -> list[str]:
    """
    Cross-check core_universe_candidates.csv's PROMOTED candidates
    against universe_membership.csv's actual CORE+ACTIVE membership, as
    of `evaluation_date` (point-in-time gated via
    universe_membership.get_universe_membership_as_of() -- see module
    docstring).

    Read-only: never mutates candidate_rows or membership_rows, never
    infers or corrects a status. Returns failure identifiers only.

    Returns:
        [] when every PROMOTED candidate has real CORE+ACTIVE membership
        as of evaluation_date, and every real CORE+ACTIVE membership as
        of evaluation_date has a PROMOTED candidate row
        list[str] of failure identifiers otherwise, each prefixed with
        the CIK it concerns for traceability (e.g.
        "cik=0001730168.promoted_candidate_missing_active_core_membership")
    """
    failures: list[str] = []

    # ---- direction 1: every PROMOTED candidate must have OK CORE membership ----
    for index, candidate in enumerate(candidate_rows):
        if not isinstance(candidate, dict):
            failures.append(f"candidate_rows[{index}].root")
            continue

        if candidate.get("inclusion_status") != PROMOTED_STATUS:
            continue

        cik = candidate.get("cik")
        if not cik:
            # A PROMOTED row with no CIK is already a
            # core_universe_io.validate_core_universe_row() failure
            # (cik_missing_for_verified_or_promoted) -- this module does
            # not re-check that, but it also cannot look up membership
            # for a CIK that does not exist, so it is reported here too
            # rather than silently skipped.
            failures.append(
                f"candidate_rows[{index}].promoted_candidate_missing_cik"
            )
            continue

        result = get_universe_membership_as_of(
            cik, CORE_UNIVERSE_TYPE, evaluation_date, membership_rows
        )

        if result["lookup_status"] == LOOKUP_OK:
            continue

        if result["lookup_status"] == LOOKUP_OVERLAPPING_MEMBERSHIP:
            failures.append(
                f"cik={cik}.ambiguous_core_membership_for_promoted_candidate"
            )
        else:
            failures.append(
                f"cik={cik}.promoted_candidate_missing_active_core_membership"
            )

    # ---- direction 2: every OK CORE membership must have a PROMOTED candidate ----
    candidate_by_cik: dict[str, dict] = {}
    for candidate in candidate_rows:
        if isinstance(candidate, dict) and candidate.get("cik"):
            # Multiple rows per CIK are not expected in
            # core_universe_candidates.csv, but this module does not
            # assume uniqueness -- last-write-wins here only decides
            # which row's inclusion_status is inspected below, it is not
            # a correctness claim about the candidate file itself.
            candidate_by_cik[candidate["cik"]] = candidate

    core_ciks = {
        row.get("cik")
        for row in membership_rows
        if isinstance(row, dict) and row.get("cik") and row.get("universe_type") == CORE_UNIVERSE_TYPE
    }

    for cik in sorted(core_ciks):
        result = get_universe_membership_as_of(
            cik, CORE_UNIVERSE_TYPE, evaluation_date, membership_rows
        )

        if result["lookup_status"] == LOOKUP_OVERLAPPING_MEMBERSHIP:
            failure = f"cik={cik}.ambiguous_core_membership_for_promoted_candidate"
            if failure not in failures:
                failures.append(failure)
            continue

        if result["lookup_status"] != LOOKUP_OK:
            # Not currently OK (not yet available, expired, no coverage,
            # invalid reference) -- nothing to reconcile against a
            # candidate for a membership that is not actually active
            # right now.
            continue

        candidate = candidate_by_cik.get(cik)
        if candidate is None or candidate.get("inclusion_status") != PROMOTED_STATUS:
            failures.append(f"cik={cik}.core_membership_missing_promoted_candidate")

    return failures


__all__ = [
    "SCHEMA_VERSION",
    "CORE_UNIVERSE_TYPE",
    "PROMOTED_STATUS",
    "validate_promotion_consistency",
]

