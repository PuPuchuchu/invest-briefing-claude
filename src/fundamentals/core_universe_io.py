"""
CSV read/write/validation for data/reference/core_universe_candidates.csv
(Framework v2.0 Step 10B schema, 2026-09-20 ChatGPT design review, item 8;
 extended 2026-09-20 per ChatGPT's SAP/IFRS + AVGO/business-mix policy
 round -- see the new "SEC verification metadata" block below).

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

SEC verification metadata (added 2026-09-20, ChatGPT SAP/IFRS + AVGO
policy round)
---------------------------------------------------------------------
ChatGPT's design message formally split "can this system see SEC data
for this filer" into three independent axes, using SAP as the driving
example: SAP is a Foreign Private Issuer that files Form 20-F under
IFRS Accounting Standards (as issued by the IASB, via the IFRS
Taxonomy) rather than Form 10-K under US GAAP. A candidate can be a
fully valid, SEC-verified Core Universe member (sec_availability =
CONFIRMED) while still being NOT production-eligible for this system's
factor calculations, because the current normalizer
(src/sec_normalizer.py's successor / the fundamentals pipeline) only
understands US-GAAP XBRL tags. The three axes, each tracked as its own
column so none of them can silently stand in for another:

    sec_filing_type          -- which SEC form this filer actually
                                 submits (10-K vs 20-F). This is a raw
                                 fact read off the fetched Submissions
                                 JSON, not an inference from ticker or
                                 sector.
    accounting_basis         -- US_GAAP vs IFRS. Also a raw fact, not
                                 assumed from filing_type alone (a
                                 20-F filer could in principle still
                                 report US GAAP; the two are checked
                                 independently rather than mapped
                                 1:1). Standing rule: never map IFRS
                                 concepts onto US GAAP tag names by
                                 name-similarity alone.
    normalization_status     -- whether THIS system's current
                                 normalizer supports that
                                 accounting_basis. This is a system
                                 capability fact, not a filer fact --
                                 it changes only when a normalization
                                 engine is actually built, never by
                                 assumption.

A separate, orthogonal axis handles business-mix / segment structure
(the AVGO case: Semiconductor Solutions ~58% / Infrastructure Software
~42% incl. VMware -- two economically dissimilar businesses under one
consolidated filing):

    business_model_status     -- NOT a generic "is this a normal
                                 operating company" sanity flag (an
                                 earlier draft of this schema used that
                                 framing; ChatGPT's 2026-09-21 review
                                 corrected it). It means: is this
                                 candidate's economic business model
                                 actually comparable to its PROPOSED
                                 Primary Peer Group? Values:
                                 COMPARABLE / CONDITIONAL /
                                 NOT_COMPARABLE / UNVERIFIED.
                                 CONDITIONAL means the business
                                 structure is not fully identical to
                                 the peer group but is still usable
                                 under a properly scoped diversified
                                 peer group or specific metric
                                 eligibility policy (AVGO's case).
                                 Examples ChatGPT gave: NVDA ->
                                 PURE_PLAY + COMPARABLE; AVGO ->
                                 DIVERSIFIED + CONDITIONAL.
    business_mix_status       -- PURE_PLAY vs DIVERSIFIED. Drives which
                                 peer group a candidate's CONSOLIDATED
                                 metrics may be percentiled within.
                                 Per ChatGPT's explicit instruction,
                                 this round does NOT build a
                                 segment-level extraction engine --
                                 DIVERSIFIED candidates are handled by
                                 routing them to their own diversified
                                 peer group (see AVGO below), not by
                                 splitting their financials.
    segment_reporting_available -- TRUE/FALSE/UNVERIFIED: whether the
                                 filer reports segment-level data at
                                 all (kept as metadata for a possible
                                 future segment-extraction feature;
                                 not acted on this round).

Two downstream fields summarize the above into what the percentile
engine (10A) is actually allowed to do with a candidate once it is
promoted:

    metric_eligibility_policy -- which percentile policy applies:
        STANDARD_PURE_PLAY_PERCENTILE      -- normal peer-group percentile
        DIVERSIFIED_GROUP_PERCENTILE_ONLY  -- percentile only within a
                                               dedicated diversified peer
                                               group (never against
                                               pure-play peers)
        UNSUPPORTED_ACCOUNTING_BASIS       -- normalizer cannot process
                                               this filer's data at all
        UNVERIFIED                          -- not yet determined
    production_eligibility / eligibility_reason -- the final ELIGIBLE /
        NOT_ELIGIBLE / PENDING_VERIFICATION verdict this round's report
        must show per candidate, with a human-readable reason recorded
        whenever the verdict is NOT_ELIGIBLE (never a silent exclusion).

All nine of these new fields default to None ("" on disk) exactly like
every other not-yet-determined field in this schema -- a value is only
ever recorded once it is actually confirmed (from a live SEC fetch, or,
for the two AVGO business-mix fields, from ChatGPT's own explicit
design-review statement about AVGO's reported segment split, which is
the sanctioned source for that specific fact this round). Nothing here
is populated from general knowledge/assumption; see the report
delivered alongside this change for exactly which fields are filled in
for which candidates and why.
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
    # -- SEC verification metadata (2026-09-20 SAP/IFRS + AVGO policy round) --
    "sec_filing_type",
    "accounting_basis",
    "normalization_status",
    "business_model_status",
    "business_mix_status",
    "segment_reporting_available",
    "metric_eligibility_policy",
    "production_eligibility",
    "eligibility_reason",
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

# -- SEC verification metadata enums (2026-09-20 SAP/IFRS + AVGO policy round) --

# Which SEC form this filer actually submits. A raw fact read off a
# fetched Submissions JSON -- never inferred from sector/ticker.
SEC_FILING_TYPES = frozenset({"10-K", "20-F", "UNVERIFIED"})

# Accounting standard the filer's SEC-furnished financials are prepared
# under. Checked independently of sec_filing_type (never assumed 1:1).
ACCOUNTING_BASIS_VALUES = frozenset({"US_GAAP", "IFRS", "UNVERIFIED"})

# Whether THIS system's current normalizer supports that accounting
# basis. A system-capability fact, not a filer fact.
NORMALIZATION_STATUSES = frozenset({"SUPPORTED", "UNSUPPORTED", "UNVERIFIED"})

# Whether this candidate's economic business model is comparable to
# its PROPOSED Primary Peer Group (not a generic "operating company"
# flag -- corrected 2026-09-21 per ChatGPT review). CONDITIONAL means
# comparable only under a properly scoped diversified peer group /
# metric eligibility policy (AVGO's case), not comparable outright.
BUSINESS_MODEL_STATUSES = frozenset(
    {"COMPARABLE", "CONDITIONAL", "NOT_COMPARABLE", "UNVERIFIED"}
)

# PURE_PLAY vs DIVERSIFIED (AVGO: Semiconductor Solutions ~58% /
# Infrastructure Software ~42% incl. VMware -- two dissimilar
# businesses under one filing). Drives which peer group a candidate's
# consolidated metrics may be percentiled within.
BUSINESS_MIX_STATUSES = frozenset({"PURE_PLAY", "DIVERSIFIED", "UNVERIFIED"})

# Whether the filer reports segment-level financial data at all.
# Tracked as metadata only -- no segment-extraction engine this round.
SEGMENT_REPORTING_AVAILABLE_VALUES = frozenset({"TRUE", "FALSE", "UNVERIFIED"})

# Which percentile policy the Percentile Engine (10A) is allowed to
# apply to this candidate once promoted.
METRIC_ELIGIBILITY_POLICIES = frozenset(
    {
        "STANDARD_PURE_PLAY_PERCENTILE",
        "DIVERSIFIED_GROUP_PERCENTILE_ONLY",
        "UNSUPPORTED_ACCOUNTING_BASIS",
        "UNVERIFIED",
    }
)

# Final per-candidate verdict this round's report must show.
PRODUCTION_ELIGIBILITY_STATUSES = frozenset(
    {"ELIGIBLE", "NOT_ELIGIBLE", "PENDING_VERIFICATION"}
)

# Fields that are raw SEC-filer facts (as opposed to downstream policy
# conclusions computed from them): a non-UNVERIFIED value here is only
# trustworthy once sec_availability is CONFIRMED -- exactly the same
# principle already enforced for `cik` below.
_SEC_DERIVED_FACT_FIELDS = (
    "sec_filing_type",
    "accounting_basis",
    "business_model_status",
    "business_mix_status",
    "segment_reporting_available",
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

    # -- SEC verification metadata (2026-09-20 SAP/IFRS + AVGO policy round) --

    for field, valid_values in (
        ("sec_filing_type", SEC_FILING_TYPES),
        ("accounting_basis", ACCOUNTING_BASIS_VALUES),
        ("normalization_status", NORMALIZATION_STATUSES),
        ("business_model_status", BUSINESS_MODEL_STATUSES),
        ("business_mix_status", BUSINESS_MIX_STATUSES),
        ("segment_reporting_available", SEGMENT_REPORTING_AVAILABLE_VALUES),
        ("metric_eligibility_policy", METRIC_ELIGIBILITY_POLICIES),
        ("production_eligibility", PRODUCTION_ELIGIBILITY_STATUSES),
    ):
        value = row.get(field)
        if value is not None and value not in valid_values:
            failures.append(field)

    # A raw SEC-filer fact (filing type, accounting basis, business
    # model/mix, segment reporting) is only trustworthy once
    # sec_availability is CONFIRMED -- same principle as the cik check
    # above, applied to each of these fields independently.
    for field in _SEC_DERIVED_FACT_FIELDS:
        value = row.get(field)
        if value and value != "UNVERIFIED" and sec_availability != "CONFIRMED":
            failures.append(f"{field}_present_without_confirmed_sec_availability")

    accounting_basis = row.get("accounting_basis")
    normalization_status = row.get("normalization_status")
    business_model_status = row.get("business_model_status")
    business_mix_status = row.get("business_mix_status")
    metric_eligibility_policy = row.get("metric_eligibility_policy")
    production_eligibility = row.get("production_eligibility")
    eligibility_reason = row.get("eligibility_reason")

    # IFRS filings are never treated as normalizer-SUPPORTED under the
    # current (US-GAAP-only) normalizer -- this system does not map
    # IFRS concepts onto US GAAP tag names by name-similarity, so this
    # combination can only mean a data-entry error.
    if accounting_basis == "IFRS" and normalization_status == "SUPPORTED":
        failures.append("ifrs_accounting_basis_cannot_be_normalization_supported")

    # metric_eligibility_policy must not silently disagree with the
    # facts that determine it (2026-09-21 ChatGPT review: these five
    # fields -- business_mix_status / business_model_status /
    # normalization_status / metric_eligibility_policy /
    # production_eligibility -- must never carry overlapping,
    # contradictory meanings).
    if metric_eligibility_policy == "UNSUPPORTED_ACCOUNTING_BASIS" and normalization_status not in (
        None,
        "UNSUPPORTED",
    ):
        failures.append("metric_eligibility_policy_normalization_status_mismatch")
    if metric_eligibility_policy == "DIVERSIFIED_GROUP_PERCENTILE_ONLY" and business_mix_status not in (
        None,
        "DIVERSIFIED",
    ):
        failures.append("metric_eligibility_policy_business_mix_mismatch")
    if metric_eligibility_policy == "STANDARD_PURE_PLAY_PERCENTILE" and business_mix_status not in (
        None,
        "PURE_PLAY",
    ):
        failures.append("metric_eligibility_policy_business_mix_mismatch")

    # production_eligibility = ELIGIBLE is only allowed once every
    # precondition it summarizes is actually satisfied -- per ChatGPT's
    # explicit 2026-09-21 instruction, it must never be set ahead of
    # business model, accounting basis, normalization, and metric
    # eligibility all being determined and mutually consistent.
    if production_eligibility == "ELIGIBLE":
        if sec_availability != "CONFIRMED":
            failures.append("production_eligible_requires_confirmed_sec_availability")
        if normalization_status != "SUPPORTED":
            failures.append("production_eligible_requires_supported_normalization")
        if business_model_status not in ("COMPARABLE", "CONDITIONAL"):
            failures.append("production_eligible_requires_comparable_or_conditional_business_model")
        if business_mix_status in (None, "UNVERIFIED"):
            failures.append("production_eligible_requires_determined_business_mix")
        if metric_eligibility_policy in (None, "UNVERIFIED", "UNSUPPORTED_ACCOUNTING_BASIS"):
            failures.append("production_eligible_requires_determined_metric_eligibility_policy")

    # A candidate the normalizer cannot process at all can never be
    # marked production-ELIGIBLE (redundant with the block above when
    # ELIGIBLE, but also catches UNSUPPORTED never being paired with
    # ELIGIBLE via a stale/partial row).
    if normalization_status == "UNSUPPORTED" and production_eligibility == "ELIGIBLE":
        failures.append("unsupported_normalization_cannot_be_production_eligible")

    # A NOT_ELIGIBLE verdict must always carry a human-readable reason
    # -- never a silent exclusion.
    if production_eligibility == "NOT_ELIGIBLE" and not eligibility_reason:
        failures.append("not_eligible_requires_eligibility_reason")

    return failures


__all__ = [
    "CORE_UNIVERSE_FIELDNAMES",
    "SEC_AVAILABILITY_STATUSES",
    "INCLUSION_STATUSES",
    "SEC_FILING_TYPES",
    "ACCOUNTING_BASIS_VALUES",
    "NORMALIZATION_STATUSES",
    "BUSINESS_MODEL_STATUSES",
    "BUSINESS_MIX_STATUSES",
    "SEGMENT_REPORTING_AVAILABLE_VALUES",
    "METRIC_ELIGIBILITY_POLICIES",
    "PRODUCTION_ELIGIBILITY_STATUSES",
    "serialize_core_universe_row",
    "deserialize_core_universe_row",
    "write_core_universe_candidates_csv",
    "read_core_universe_candidates_csv",
    "validate_core_universe_row",
]
