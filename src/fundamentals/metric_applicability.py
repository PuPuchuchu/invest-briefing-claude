"""
Metric Applicability reference layer (Framework v2.1 Step 4, 2026-09-28
ChatGPT design round -- "Architecture v5", confirmed for implementation
2026-09-28 after a schema-review round).

Why this module exists
-----------------------
Framework v2.1's factor-weight pipeline is:

    Canonical QUALITY_WEIGHTS / GROWTH_WEIGHTS
    -> Base Applicability filter          <-- THIS module's job
    -> Profile-scoped weight
    -> Runtime Modifier                    <-- THIS module's job (resolution only,
                                                not wired into any caller yet)
    -> Company-scoped weight

This module is the reference-data + validation + pure-lookup layer for
the "Base Applicability filter" and "Runtime Modifier" stages -- it does
NOT compute Quality/Growth scores, does NOT touch percentile ranking, and
is not called from anywhere yet (see "Explicitly out of scope" below).
Wiring it into the production pipeline is Step 6/7 of the confirmed
9-step implementation order, per constraint (1) of ChatGPT's original
Architecture v5 approval: completing this reference layer must never be
mistaken for Production (10C) activation.

Four reference files, one module
---------------------------------
    metric_profiles.csv            -- profile_id -> description +
                                       default_applicability (DOCUMENTATION
                                       ONLY -- see "default_applicability is
                                       inert" below)
    peer_group_metric_profile.csv  -- peer_group -> profile_id, point-in-time
                                       versioned (mirrors
                                       peer_classification.py's PIT
                                       discipline exactly)
    metric_applicability.csv       -- (profile_id, metric_name) -> Base
                                       Applicability status. NOT point-in-time
                                       versioned (no effective_from/to columns
                                       in this file) -- it is a single current
                                       policy table, re-published wholesale
                                       when policy changes, not layered over
                                       time the way peer/profile mappings are.
    metric_applicability_modifiers.csv
                                    -- (profile_id, metric_name, trigger) ->
                                       result, PIT versioned. Resolves a
                                       CONDITIONAL Base Applicability entry
                                       into a concrete final status once a
                                       company's runtime facts (e.g.
                                       business_mix_status) are known.

All four are tightly coupled (peer_group_metric_profile feeds profile_id
into metric_applicability; metric_applicability's CONDITIONAL rows are
only meaningful together with metric_applicability_modifiers), so -- per
this module's own investigation-round proposal, confirmed by ChatGPT --
they share ONE logic module (this file) and one I/O module
(metric_applicability_io.py), mirroring how peer_classification.py owns
its whole domain (peer_groups.csv) as a single module rather than
splitting by column group.

default_applicability is inert
-------------------------------
metric_profiles.csv's default_applicability column is documentation
metadata ONLY. Production lookup NEVER reads it as a fallback. A missing
(profile_id, metric_name) row in metric_applicability.csv always resolves
to UNVERIFIED via get_metric_applicability_status() below -- silently
substituting metric_profiles.default_applicability for a missing row was
explicitly forbidden by the original Architecture v5 approval (constraint
2) and is reaffirmed unchanged by this round's confirmation.

PRODUCTION_METRICS, not metric_specs.METRIC_SPECS, is the Step 4 completeness anchor
--------------------------------------------------------------------------------------
src/fundamentals/metric_specs.py's METRIC_SPECS dict is the Percentile
Engine's own canonical metric registry (Step 10A prep) and could grow in
the future (e.g. a Research-tier metric added for percentile ranking
without necessarily needing a v2.1 Base Applicability policy on day one).
Framework v2.1 Step 4's "84 = 7 profiles x 12 metrics" completeness
requirement must NOT silently change shape if METRIC_SPECS grows later --
so this module defines its OWN canonical production-metric universe,
imported directly from quality.py/growth.py (never re-derived from
metric_specs.METRIC_SPECS), and only checks that this universe is a
SUBSET of METRIC_SPECS.keys() (see validate_production_metrics_configuration()
below) as a sanity cross-check, never the other way around.

Explicitly out of scope this round (per ChatGPT's confirmed instruction)
--------------------------------------------------------------------------
The following are NOT modified by this module or this implementation
round:
    - quality.py, growth.py, derived_metrics.py, percentile_engine.py,
      orchestrator.py, sec_normalizer.py, point_in_time.py
    - production_eligibility logic (core_universe_io.py)
Nothing in this module is called by any of the above. The
resolve_metric_applicability() / evaluate_modifiers() functions below are
fully built and tested, but unwired -- exactly like orchestrator.py's
NOT_WIRED stages (metric_applicability / relative_evaluation / factor).

Also explicitly out of scope this round: the real 18 peer_group ->
profile_id economic mappings and the real 84-row metric_applicability
policy. This round ships schema + validation + lookup logic only, proven
against synthetic/fixture data; the actual economic policy content is a
separate, later design round (ChatGPT's own explicit instruction).

Reused, not reinvented
------------------------
    - PIT date semantics (effective_from/effective_to half-open interval,
      classification_available_date as a separate, never-inferred
      availability gate) mirror src/fundamentals/peer_classification.py
      exactly, including its private _parse_date/_ranges_overlap helpers
      (duplicated here per this repo's established convention: each
      domain owns its own date-parsing helper rather than sharing one --
      see peer_classification.py's own module docstring on this point).
    - trigger field/value allow-lists are NOT reinvented here. They are
      imported directly from src/fundamentals/core_universe_io.py's
      BUSINESS_MIX_STATUSES / SEGMENT_REPORTING_AVAILABLE_VALUES -- the
      exact same company-level runtime facts already tracked there for
      the AVGO business-mix case (Step 8). This was the key structural
      finding of the investigation round that produced this module's
      design: the "trigger field allow-list" ChatGPT asked for already
      existed in this codebase and did not need to be invented.

Modifier conflict resolution (confirmed 2026-09-28)
------------------------------------------------------
Two independent situations, resolved differently:

    1. Same (profile_id, metric_name, trigger) with OVERLAPPING
       [effective_from, effective_to) windows -- this is a reference-data
       AUTHORING ERROR, never resolved by precedence. Caught at validation
       time by validate_metric_applicability_modifiers_data() (temporal
       overlap check on the modifier's own business key) -- evaluate_modifiers()
       below assumes this has already been enforced and does not re-detect
       it at runtime.
    2. DIFFERENT triggers for the same (profile_id, metric_name) both
       matching a company's runtime facts simultaneously -- resolved by a
       strict priority order: UNVERIFIED > NOT_APPLICABLE > APPLICABLE
       (MODIFIER_CONFLICT_PRIORITY below). Because MODIFIER_RESULT_STATUSES
       deliberately excludes CONDITIONAL (a modifier resolves a CONDITIONAL
       base status into one of exactly these three outcomes, never back
       into CONDITIONAL), this priority order is a total order over the
       full result vocabulary -- there is no same-priority tie case left to
       raise a validation error for.

Fail-closed behavior (confirmed 2026-09-28, restated here as the single
source of truth for this module's own behavior)
--------------------------------------------------------------------------
    Missing (profile_id, metric_name) row in metric_applicability.csv
        -> get_metric_applicability_status() returns "UNVERIFIED"
           (never metric_profiles.default_applicability)
    Unknown profile_id / metric_name          -> validation ERROR
    Unknown resolver_id                        -> validation ERROR
    Malformed trigger                          -> validation ERROR
    Unknown/unrecognized runtime trigger value -> evaluate_modifiers()
                                                   returns "UNVERIFIED"
                                                   (never an exception,
                                                   never silently ignored)
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from src.fundamentals.core_universe_io import (
    BUSINESS_MIX_STATUSES,
    SEGMENT_REPORTING_AVAILABLE_VALUES,
)
from src.fundamentals.growth import GROWTH_METRICS
from src.fundamentals.metric_specs import METRIC_SPECS
from src.fundamentals.point_in_time import coerce_evaluation_date
from src.fundamentals.quality import QUALITY_METRICS

SCHEMA_VERSION = "metric_applicability_v0.1"


# ============================================================
# DATE HELPERS (private to this module -- see peer_classification.py's
# module docstring on why each domain owns its own copy)
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
# CANONICAL ALLOW-LISTS
# ============================================================

# 2026-09-28: the 7 metric-applicability profiles. This is a NEW,
# deliberately small, closed vocabulary -- distinct from company_type
# (FINANCIAL/NON_FINANCIAL, sec_normalizer.py). Extending this set is a
# design decision, not something a caller or reference-data author can do
# by just writing a new value into a CSV.
PROFILE_IDS = frozenset(
    {
        "GENERAL_CORPORATE",
        "BANKING",
        "ASSET_MANAGEMENT",
        "FABLESS_SEMICONDUCTOR",
        "IDM_FOUNDRY_SEMICONDUCTOR",
        "MEMORY_SEMICONDUCTOR",
        "PHARMA_HEALTHCARE",
    }
)

# Shared by metric_profiles.csv's default_applicability (documentation
# only -- see module docstring) and metric_applicability.csv's
# applicability_status (the real, production-read column).
APPLICABILITY_STATUSES = frozenset(
    {"APPLICABLE", "NOT_APPLICABLE", "CONDITIONAL", "UNVERIFIED"}
)

# 2026-09-28: only MANUAL_REVIEW exists -- no automated profile classifier
# exists in this codebase, matching peer_classification.py's identical
# reservation of "RULE_BASED" (deliberately not added until such a
# classifier is actually built).
MAPPING_SOURCES = frozenset({"MANUAL_REVIEW"})

# 2026-09-28 (ChatGPT confirmation round): a code-side generic resolver
# key, never a Python function/module name written into a CSV. v2.1 has
# exactly one resolver implementation -- the modifier table lookup this
# module itself provides via evaluate_modifiers() -- so this registry is
# deliberately not pre-populated with speculative future resolvers.
RESOLVER_IDS = frozenset({"MODIFIER_TABLE_RESOLVER"})

# A modifier RESOLVES a CONDITIONAL base status into one of these three
# concrete outcomes -- CONDITIONAL is deliberately excluded here to avoid
# a circular definition (a modifier's job is to end the conditionality,
# never to restate it).
MODIFIER_RESULT_STATUSES = frozenset({"APPLICABLE", "NOT_APPLICABLE", "UNVERIFIED"})

# 2026-09-28: reused, not reinvented -- see module docstring. Only the
# fields already tracked as company-level runtime facts in
# core_universe_io.py are valid trigger fields. "UNVERIFIED" is excluded
# from the *value* allow-lists below: a modifier trigger is meant to react
# to a DETERMINED fact, never to "we don't know yet" (which instead falls
# through to evaluate_modifiers()'s own no-match -> UNVERIFIED behavior).
TRIGGER_FIELDS = frozenset({"business_mix_status", "segment_reporting_available"})

TRIGGER_FIELD_VALUES: dict[str, frozenset[str]] = {
    "business_mix_status": frozenset(BUSINESS_MIX_STATUSES - {"UNVERIFIED"}),
    "segment_reporting_available": frozenset(
        SEGMENT_REPORTING_AVAILABLE_VALUES - {"UNVERIFIED"}
    ),
}

# trigger format: "field=value", field lowercase snake_case, value
# alphanumeric/underscore. No spaces, no quotes, no operators beyond a
# single "=" -- this is a data format, never a Python/any-language
# expression. Rejects anything else outright (e.g. "a == b", "eval(...)",
# "field=value; other=value").
TRIGGER_PATTERN = re.compile(r"^([a-z][a-z0-9_]*)=([A-Za-z0-9_]+)$")

# 2026-09-28 (ChatGPT confirmation round): Production Base Applicability's
# own canonical metric universe -- imported directly from quality.py /
# growth.py, NEVER derived from metric_specs.METRIC_SPECS.keys() (see
# module docstring, "PRODUCTION_METRICS, not metric_specs.METRIC_SPECS").
PRODUCTION_METRICS = frozenset(QUALITY_METRICS) | frozenset(GROWTH_METRICS)


def validate_production_metrics_configuration() -> list[str]:
    """
    Sanity cross-check only (never the completeness driver itself, see
    module docstring): every metric this module treats as part of the
    v2.1 production applicability universe must also be known to
    metric_specs.METRIC_SPECS -- catches PRODUCTION_METRICS and
    METRIC_SPECS drifting apart (e.g. a metric renamed in quality.py/
    growth.py but not in metric_specs.py, or vice versa), without ever
    making METRIC_SPECS.keys() the source of PRODUCTION_METRICS itself.

    Returns:
        [] when PRODUCTION_METRICS is a subset of METRIC_SPECS.keys()
        list[str] of the metric names that are NOT known to METRIC_SPECS
        otherwise
    """
    known = set(METRIC_SPECS.keys())
    missing = sorted(PRODUCTION_METRICS - known)
    return [f"production_metric_not_in_metric_specs[{name}]" for name in missing]


# ============================================================
# ROW-LEVEL VALIDATION -- metric_profiles.csv
# ============================================================

def validate_metric_profile_row(row: dict) -> list[str]:
    """
    Validate one metric_profiles.csv row's own schema. Does not check
    cross-row conditions (duplicate profile_id, 7-profile completeness) --
    see validate_metric_profiles_data() for those.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures: list[str] = []

    profile_id = row.get("profile_id")
    if not profile_id:
        failures.append("profile_id")
    elif profile_id not in PROFILE_IDS:
        failures.append("profile_id_unknown")

    description = row.get("description")
    if not description:
        failures.append("description")

    default_applicability = row.get("default_applicability")
    if not default_applicability:
        failures.append("default_applicability")
    elif default_applicability not in APPLICABILITY_STATUSES:
        failures.append("default_applicability_unknown")

    return failures


def validate_metric_profiles_data(rows: list[dict]) -> list[str]:
    """
    Cross-row validation for the full metric_profiles.csv: every row's own
    schema, plus profile_id uniqueness and the "exactly the 7 PROFILE_IDS,
    each exactly once" completeness requirement.

    Returns:
        [] when valid
        list[str] of failure identifiers, row failures prefixed with the
        row index for traceability
    """
    failures: list[str] = []

    seen: dict[str, int] = {}

    for index, row in enumerate(rows):
        for row_failure in validate_metric_profile_row(row):
            failures.append(f"rows[{index}].{row_failure}")

        profile_id = row.get("profile_id") if isinstance(row, dict) else None
        if profile_id:
            if profile_id in seen:
                failures.append(f"rows[{index}].duplicate_profile_id")
            else:
                seen[profile_id] = index

    missing_profiles = sorted(PROFILE_IDS - set(seen.keys()))
    for profile_id in missing_profiles:
        failures.append(f"missing_profile[{profile_id}]")

    return failures


# ============================================================
# ROW-LEVEL VALIDATION -- peer_group_metric_profile.csv
# ============================================================

def validate_peer_group_metric_profile_row(row: dict) -> list[str]:
    """
    Validate one peer_group_metric_profile.csv row's own schema. Does not
    check cross-row conditions (peer_group existence, mapping_version
    uniqueness, temporal overlap) -- see
    validate_peer_group_metric_profile_data() for those.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures: list[str] = []

    # classification_available_date is deliberately NOT in this required
    # list -- unlike the other required fields, its presence is checked at
    # lookup time (get_metric_profile_for_peer_group_as_of() ->
    # LOOKUP_MISSING_AVAILABILITY_DATE), mirroring
    # peer_classification.py's identical design choice (there, gated on
    # classification_status; here, this file has no status column at all,
    # so every row is a lookup-time concern). Only its FORMAT is checked
    # here, below, when present.
    for key in ("peer_group", "profile_id", "effective_from", "mapping_source", "mapping_version"):
        if not row.get(key):
            failures.append(key)

    profile_id = row.get("profile_id")
    if profile_id and profile_id not in PROFILE_IDS:
        failures.append("profile_id_unknown")

    mapping_source = row.get("mapping_source")
    if mapping_source and mapping_source not in MAPPING_SOURCES:
        failures.append("mapping_source_unknown")

    effective_from = _parse_date(row.get("effective_from"))
    if row.get("effective_from") and effective_from is None:
        failures.append("effective_from_format")

    # 2026-09-28 confirmation: effective_to = null is open-ended (always
    # allowed). effective_from < effective_to is only checked when
    # effective_to IS present -- a deliberately stricter rule than
    # peer_classification.py's "> is a failure" (which permits an
    # equal/zero-length interval) for this new file only; see module
    # docstring.
    effective_to_raw = row.get("effective_to")
    effective_to = None
    if effective_to_raw:
        effective_to = _parse_date(effective_to_raw)
        if effective_to is None:
            failures.append("effective_to_format")

    if effective_from is not None and effective_to is not None and not (effective_from < effective_to):
        failures.append("effective_from_not_before_effective_to")

    available_raw = row.get("classification_available_date")
    if available_raw and _parse_date(available_raw) is None:
        failures.append("classification_available_date_format")

    reviewed_at_raw = row.get("reviewed_at")
    if reviewed_at_raw and _parse_date(reviewed_at_raw) is None:
        failures.append("reviewed_at_format")

    # [Claude proposal, confirmed as part of this round's schema] a
    # mapping with no stated rationale for why this peer_group maps to
    # this profile_id is exactly the kind of unexplained policy override
    # this project's conventions elsewhere guard against (review_reason,
    # eligibility_reason, mapping_rationale precedent in peer_classification.py).
    if not row.get("mapping_rationale"):
        failures.append("mapping_rationale")

    return failures


def validate_peer_group_metric_profile_data(
    rows: list[dict],
    peer_group_rows: list[dict],
) -> list[str]:
    """
    Cross-row / cross-file validation for the full peer_group_metric_profile.csv.

    peer_group_rows: data/reference/peer_groups.csv rows (REQUIRED,
    mirrors peer_classification.validate_reference_data()'s required
    issuer_rows argument) -- used only to confirm every peer_group value
    referenced here actually exists as a real, classified peer_group.
    This module never invents or assumes peer_group names.

    Checks:
        - every row's own schema (via validate_peer_group_metric_profile_row)
        - peer_group must exist in peer_group_rows (unknown_peer_group)
        - mapping_version must be unique within a given peer_group
        - no two rows for the same peer_group may have overlapping
          [effective_from, effective_to) windows

    Returns:
        [] when valid
        list[str] of failure identifiers, each prefixed with its row index
    """
    failures: list[str] = []

    known_peer_groups = {
        row.get("peer_group") for row in peer_group_rows if isinstance(row, dict) and row.get("peer_group")
    }

    seen_versions: dict[tuple[str, str], int] = {}
    rows_by_peer_group: dict[str, list[tuple[int, dict]]] = {}

    for index, row in enumerate(rows):
        for row_failure in validate_peer_group_metric_profile_row(row):
            failures.append(f"rows[{index}].{row_failure}")

        peer_group = row.get("peer_group") if isinstance(row, dict) else None

        if peer_group and known_peer_groups and peer_group not in known_peer_groups:
            failures.append(f"rows[{index}].unknown_peer_group")

        if peer_group:
            rows_by_peer_group.setdefault(peer_group, []).append((index, row))

            version = row.get("mapping_version")
            if version:
                key = (peer_group, version)
                if key in seen_versions:
                    failures.append(f"rows[{index}].duplicate_mapping_version")
                else:
                    seen_versions[key] = index

    for peer_group, group_rows in rows_by_peer_group.items():
        parsed = []
        for index, row in group_rows:
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
                    failures.append(f"rows[{idx_a}].overlaps_rows[{idx_b}]")

    return failures


# ============================================================
# PEER GROUP -> PROFILE PIT LOOKUP
# ============================================================

LOOKUP_OK = "OK"
LOOKUP_NO_MAPPING_FOR_DATE = "NO_MAPPING_FOR_DATE"
LOOKUP_MISSING_AVAILABILITY_DATE = "MISSING_AVAILABILITY_DATE"
LOOKUP_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
LOOKUP_OVERLAPPING_MAPPING = "OVERLAPPING_MAPPING"
LOOKUP_INVALID_REFERENCE = "INVALID_REFERENCE"


def get_metric_profile_for_peer_group_as_of(
    peer_group: str,
    evaluation_date: Any,
    mapping_rows: list[dict],
) -> dict:
    """
    Look up the single peer_group_metric_profile.csv row for `peer_group`
    that was both business-effective AND actually available to use as of
    `evaluation_date` -- mirrors
    peer_classification.get_peer_mapping_as_of() exactly (see that
    function's docstring for the full rationale). Unlike that function,
    there is no classification_status/usable_statuses concept here:
    peer_group_metric_profile.csv has no status column, so once a mapping
    is PIT-valid it is always usable.

    Returns:
        {
            "lookup_status": one of the LOOKUP_* constants above,
            "mapping": the matched row (dict), or None,
            "reason": a human-readable string, or None on LOOKUP_OK,
        }
    """
    eval_date = coerce_evaluation_date(evaluation_date)

    candidate_rows = [
        row for row in mapping_rows if isinstance(row, dict) and row.get("peer_group") == peer_group
    ]

    if not candidate_rows:
        return {
            "lookup_status": LOOKUP_NO_MAPPING_FOR_DATE,
            "mapping": None,
            "reason": f"No metric-profile mapping rows exist at all for peer_group {peer_group!r}.",
        }

    structurally_valid = [
        row for row in candidate_rows if not validate_peer_group_metric_profile_row(row)
    ]

    if not structurally_valid:
        return {
            "lookup_status": LOOKUP_INVALID_REFERENCE,
            "mapping": None,
            "reason": f"All metric-profile mapping rows for peer_group {peer_group!r} failed structural validation.",
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
                f"{len(covering)} metric-profile mapping rows for peer_group {peer_group!r} all "
                f"claim to cover {eval_date.isoformat()} -- this is a reference-data integrity "
                f"problem; run validate_peer_group_metric_profile_data()."
            ),
        }

    if not covering:
        return {
            "lookup_status": LOOKUP_NO_MAPPING_FOR_DATE,
            "mapping": None,
            "reason": f"No metric-profile mapping row for peer_group {peer_group!r} covers {eval_date.isoformat()}.",
        }

    row = covering[0]

    available_raw = row.get("classification_available_date")
    if not available_raw:
        return {
            "lookup_status": LOOKUP_MISSING_AVAILABILITY_DATE,
            "mapping": row,
            "reason": (
                "Matched mapping row has no classification_available_date -- cannot verify it was "
                "actually usable at evaluation_date, so it is never assumed available."
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
                f"Mapping's effective period covers {eval_date.isoformat()}, but it was not "
                f"available for use until {available.isoformat()} -- using it here would be a "
                f"look-ahead-bias violation."
            ),
        }

    return {"lookup_status": LOOKUP_OK, "mapping": row, "reason": None}


# ============================================================
# ROW-LEVEL VALIDATION -- metric_applicability.csv
# ============================================================

def validate_metric_applicability_row(row: dict) -> list[str]:
    """
    Validate one metric_applicability.csv row's own schema, including the
    bidirectional CONDITIONAL <-> resolver_id consistency rule. Does not
    check cross-row conditions (duplicate (profile_id, metric_name), 84
    completeness, CONDITIONAL <-> modifier-policy existence) -- see
    validate_metric_applicability_data() for those.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures: list[str] = []

    profile_id = row.get("profile_id")
    if not profile_id:
        failures.append("profile_id")
    elif profile_id not in PROFILE_IDS:
        failures.append("profile_id_unknown")

    metric_name = row.get("metric_name")
    if not metric_name:
        failures.append("metric_name")
    elif metric_name not in PRODUCTION_METRICS:
        failures.append("metric_name_unknown")

    applicability_status = row.get("applicability_status")
    if not applicability_status:
        failures.append("applicability_status")
    elif applicability_status not in APPLICABILITY_STATUSES:
        failures.append("applicability_status_unknown")

    resolver_id = row.get("resolver_id") or None

    if applicability_status == "CONDITIONAL":
        if not resolver_id:
            failures.append("resolver_id_required_for_conditional")
        elif resolver_id not in RESOLVER_IDS:
            failures.append("resolver_id_unknown")
    elif applicability_status in ("APPLICABLE", "NOT_APPLICABLE", "UNVERIFIED"):
        if resolver_id:
            failures.append("resolver_id_must_be_empty_for_non_conditional")

    if not row.get("policy_owner"):
        failures.append("policy_owner")

    return failures


def validate_metric_applicability_data(
    rows: list[dict],
    modifier_rows: list[dict],
) -> list[str]:
    """
    Cross-row / cross-file validation for the full metric_applicability.csv.

    modifier_rows: data/reference/metric_applicability_modifiers.csv rows
    (REQUIRED, not optional -- 2026-09-28 confirmation: a CONDITIONAL row
    with no matching modifier policy at all is a hard validation ERROR,
    not a soft/advisory check).

    Checks:
        - every row's own schema (via validate_metric_applicability_row)
        - (profile_id, metric_name) uniqueness
        - EXACT completeness: every (profile_id, metric_name) combination
          over PRODUCTION_METRICS x PROFILE_IDS must appear exactly once
          (missing combinations listed individually)
        - every CONDITIONAL row must have at least one structurally-valid
          matching row in modifier_rows for the same (profile_id, metric_name)

    Returns:
        [] when valid
        list[str] of failure identifiers, row failures prefixed with the
        row index
    """
    failures: list[str] = []

    seen: dict[tuple[str, str], int] = {}

    for index, row in enumerate(rows):
        for row_failure in validate_metric_applicability_row(row):
            failures.append(f"rows[{index}].{row_failure}")

        if not isinstance(row, dict):
            continue

        profile_id = row.get("profile_id")
        metric_name = row.get("metric_name")

        if profile_id and metric_name:
            key = (profile_id, metric_name)
            if key in seen:
                failures.append(f"rows[{index}].duplicate_profile_metric")
            else:
                seen[key] = index

    expected = {(profile_id, metric_name) for profile_id in PROFILE_IDS for metric_name in PRODUCTION_METRICS}
    missing = sorted(expected - set(seen.keys()))
    for profile_id, metric_name in missing:
        failures.append(f"missing_row[profile_id={profile_id},metric_name={metric_name}]")

    # CONDITIONAL <-> modifier-policy existence (hard requirement,
    # 2026-09-28 confirmation).
    valid_modifier_keys: set[tuple[str, str]] = set()
    for modifier_row in modifier_rows:
        if not isinstance(modifier_row, dict):
            continue
        if validate_metric_applicability_modifier_row(modifier_row):
            continue
        m_profile = modifier_row.get("profile_id")
        m_metric = modifier_row.get("metric_name")
        if m_profile and m_metric:
            valid_modifier_keys.add((m_profile, m_metric))

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if row.get("applicability_status") != "CONDITIONAL":
            continue
        profile_id = row.get("profile_id")
        metric_name = row.get("metric_name")
        if (profile_id, metric_name) not in valid_modifier_keys:
            failures.append(f"rows[{index}].conditional_missing_modifier_policy")

    return failures


# ============================================================
# ROW-LEVEL VALIDATION -- metric_applicability_modifiers.csv
# ============================================================

def validate_metric_applicability_modifier_row(row: dict) -> list[str]:
    """
    Validate one metric_applicability_modifiers.csv row's own schema,
    including trigger format/field/value checks. Does not check cross-row
    conditions (modifier_id global uniqueness, temporal overlap on the
    (profile_id, metric_name, trigger) business key) -- see
    validate_metric_applicability_modifiers_data() for those.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(row, dict):
        return ["root"]

    failures: list[str] = []

    profile_id = row.get("profile_id")
    if not profile_id:
        failures.append("profile_id")
    elif profile_id not in PROFILE_IDS:
        failures.append("profile_id_unknown")

    metric_name = row.get("metric_name")
    if not metric_name:
        failures.append("metric_name")
    elif metric_name not in PRODUCTION_METRICS:
        failures.append("metric_name_unknown")

    if not row.get("modifier_id"):
        failures.append("modifier_id")

    trigger = row.get("trigger")
    if not trigger:
        failures.append("trigger")
    else:
        match = TRIGGER_PATTERN.match(trigger)
        if match is None:
            failures.append("trigger_format")
        else:
            field, value = match.group(1), match.group(2)
            if field not in TRIGGER_FIELDS:
                failures.append("trigger_field_unknown")
            elif value not in TRIGGER_FIELD_VALUES.get(field, frozenset()):
                failures.append("trigger_value_unknown")

    result = row.get("result")
    if not result:
        failures.append("result")
    elif result not in MODIFIER_RESULT_STATUSES:
        failures.append("result_unknown")

    for key in ("reason_code", "policy_owner", "rationale"):
        if not row.get(key):
            failures.append(key)

    effective_from = _parse_date(row.get("effective_from"))
    if not row.get("effective_from"):
        failures.append("effective_from")
    elif effective_from is None:
        failures.append("effective_from_format")

    effective_to_raw = row.get("effective_to")
    effective_to = None
    if effective_to_raw:
        effective_to = _parse_date(effective_to_raw)
        if effective_to is None:
            failures.append("effective_to_format")

    if effective_from is not None and effective_to is not None and not (effective_from < effective_to):
        failures.append("effective_from_not_before_effective_to")

    return failures


def validate_metric_applicability_modifiers_data(rows: list[dict]) -> list[str]:
    """
    Cross-row validation for the full metric_applicability_modifiers.csv.

    Checks:
        - every row's own schema (via validate_metric_applicability_modifier_row)
        - modifier_id is globally unique across the whole file (it is this
          file's own surrogate row identity, not part of the business key)
        - no two rows sharing the same (profile_id, metric_name, trigger)
          business key may have overlapping [effective_from, effective_to)
          windows -- 2026-09-28 confirmation: this is ALWAYS a validation
          ERROR (same trigger, overlapping period), never resolved by
          precedence, regardless of whether the two rows' `result` values
          agree or differ.

    Returns:
        [] when valid
        list[str] of failure identifiers, row failures prefixed with the
        row index
    """
    failures: list[str] = []

    seen_modifier_ids: dict[str, int] = {}
    rows_by_key: dict[tuple[str, str, str], list[tuple[int, dict]]] = {}

    for index, row in enumerate(rows):
        for row_failure in validate_metric_applicability_modifier_row(row):
            failures.append(f"rows[{index}].{row_failure}")

        if not isinstance(row, dict):
            continue

        modifier_id = row.get("modifier_id")
        if modifier_id:
            if modifier_id in seen_modifier_ids:
                failures.append(f"rows[{index}].duplicate_modifier_id")
            else:
                seen_modifier_ids[modifier_id] = index

        profile_id = row.get("profile_id")
        metric_name = row.get("metric_name")
        trigger = row.get("trigger")
        if profile_id and metric_name and trigger:
            key = (profile_id, metric_name, trigger)
            rows_by_key.setdefault(key, []).append((index, row))

    for key, group_rows in rows_by_key.items():
        parsed = []
        for index, row in group_rows:
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
                    failures.append(f"rows[{idx_a}].same_trigger_overlaps_rows[{idx_b}]")

    return failures


# ============================================================
# RUNTIME LOOKUP / RESOLUTION -- pure functions, NOT wired into
# orchestrator.py or any other caller this round (see module docstring).
# ============================================================

def get_metric_applicability_status(
    profile_id: str,
    metric_name: str,
    applicability_rows: list[dict],
) -> str:
    """
    Base Applicability lookup. metric_applicability.csv is NOT point-in-time
    versioned (no effective_from/to columns), so this is a plain keyed
    lookup, not a PIT lookup.

    Fail-closed (2026-09-28 confirmation, unchanged from the original
    Architecture v5 approval's constraint 2): a missing (profile_id,
    metric_name) row returns "UNVERIFIED" -- NEVER
    metric_profiles.csv's default_applicability, which this function does
    not even accept as an argument.
    """
    for row in applicability_rows:
        if not isinstance(row, dict):
            continue
        if row.get("profile_id") == profile_id and row.get("metric_name") == metric_name:
            if validate_metric_applicability_row(row):
                # A structurally invalid row is never trusted -- treated
                # exactly like a missing row.
                return "UNVERIFIED"
            return row["applicability_status"]

    return "UNVERIFIED"


def evaluate_modifiers(
    profile_id: str,
    metric_name: str,
    runtime_facts: dict[str, str],
    evaluation_date: Any,
    modifier_rows: list[dict],
) -> dict:
    """
    Resolve a CONDITIONAL Base Applicability entry using
    metric_applicability_modifiers.csv (the sole RESOLVER_IDS =
    "MODIFIER_TABLE_RESOLVER" implementation that exists in v2.1).

    runtime_facts: e.g. {"business_mix_status": "DIVERSIFIED"} -- the
    company's actual, already-determined runtime attribute values (this
    function does not fetch them; callers own that, matching this
    codebase's established "callers own I/O, this module owns domain
    logic" convention).

    Conflict resolution (see module docstring):
        - same (profile_id, metric_name, trigger) rows are assumed already
          validated non-overlapping (validate_metric_applicability_modifiers_data()'s
          job, not re-checked here)
        - different triggers matching simultaneously are resolved by
          MODIFIER_CONFLICT_PRIORITY (UNVERIFIED > NOT_APPLICABLE > APPLICABLE)

    Fail-closed: an unrecognized runtime_facts value for a field that IS a
    known TRIGGER_FIELDS member (i.e. not even a member of that field's
    own source-of-truth enum, e.g. core_universe_io.BUSINESS_MIX_STATUSES)
    forces the result to "UNVERIFIED" -- this function never raises on a
    bad runtime fact and never silently ignores it. A runtime fact of
    "UNVERIFIED" itself (a legitimate "not yet determined" company-level
    fact) simply matches no trigger, which -- if nothing else matches
    either -- also falls through to "UNVERIFIED" via the ordinary
    no-match path below.

    Returns:
        {
            "status": one of MODIFIER_RESULT_STATUSES,
            "matched_modifier_ids": list[str] (empty when no match),
            "reason": human-readable string, or None when at least one
                modifier matched cleanly,
        }
    """
    eval_date = coerce_evaluation_date(evaluation_date)

    candidates = []
    for row in modifier_rows:
        if not isinstance(row, dict):
            continue
        if row.get("profile_id") != profile_id or row.get("metric_name") != metric_name:
            continue
        if validate_metric_applicability_modifier_row(row):
            continue

        start = _parse_date(row.get("effective_from"))
        if start is None:
            continue
        end_raw = row.get("effective_to")
        end = _parse_date(end_raw) if end_raw else None
        if not (start <= eval_date and (end is None or eval_date < end)):
            continue

        candidates.append(row)

    unknown_fact_seen = False
    matched: list[dict] = []

    for row in candidates:
        match = TRIGGER_PATTERN.match(row["trigger"])
        field, value = match.group(1), match.group(2)

        fact = runtime_facts.get(field)
        if fact is None:
            continue

        if fact == value:
            matched.append(row)
            continue

        # A fact that isn't even a recognized member of this field's own
        # source-of-truth enum (core_universe_io.py) is a malformed
        # runtime input -- fail closed rather than silently treat it as
        # "no match".
        known_values = TRIGGER_FIELD_VALUES.get(field, frozenset()) | {"UNVERIFIED"}
        if fact not in known_values:
            unknown_fact_seen = True

    if unknown_fact_seen:
        return {
            "status": "UNVERIFIED",
            "matched_modifier_ids": [],
            "reason": "A runtime trigger field value was not recognized; failing closed.",
        }

    if not matched:
        return {
            "status": "UNVERIFIED",
            "matched_modifier_ids": [],
            "reason": "No modifier trigger matched the supplied runtime facts; CONDITIONAL could not be resolved.",
        }

    winner = min(matched, key=lambda row: MODIFIER_CONFLICT_PRIORITY[row["result"]])
    winning_result = winner["result"]

    matched_ids = [
        row["modifier_id"] for row in matched if row["result"] == winning_result
    ]

    return {"status": winning_result, "matched_modifier_ids": matched_ids, "reason": None}


MODIFIER_CONFLICT_PRIORITY = {"UNVERIFIED": 0, "NOT_APPLICABLE": 1, "APPLICABLE": 2}


def resolve_metric_applicability(
    profile_id: str,
    metric_name: str,
    runtime_facts: dict[str, str],
    evaluation_date: Any,
    applicability_rows: list[dict],
    modifier_rows: list[dict],
) -> dict:
    """
    Top-level Base Applicability + Runtime Modifier resolution.

    Preserves the existing principle (restated, not introduced, by the
    2026-09-28 confirmation round): once Base Applicability is already
    APPLICABLE, NOT_APPLICABLE, or UNVERIFIED, runtime modifiers are never
    re-evaluated -- only a CONDITIONAL base status is ever handed to
    evaluate_modifiers().

    Returns:
        {
            "status": final applicability status,
            "source": "base_policy" or "modifier_resolution",
            "matched_modifier_ids": [] unless source == "modifier_resolution",
        }
    """
    base_status = get_metric_applicability_status(profile_id, metric_name, applicability_rows)

    if base_status != "CONDITIONAL":
        return {"status": base_status, "source": "base_policy", "matched_modifier_ids": []}

    modifier_result = evaluate_modifiers(
        profile_id, metric_name, runtime_facts, evaluation_date, modifier_rows
    )

    return {
        "status": modifier_result["status"],
        "source": "modifier_resolution",
        "matched_modifier_ids": modifier_result["matched_modifier_ids"],
    }


__all__ = [
    "SCHEMA_VERSION",
    "PROFILE_IDS",
    "APPLICABILITY_STATUSES",
    "MAPPING_SOURCES",
    "RESOLVER_IDS",
    "MODIFIER_RESULT_STATUSES",
    "TRIGGER_FIELDS",
    "TRIGGER_FIELD_VALUES",
    "TRIGGER_PATTERN",
    "PRODUCTION_METRICS",
    "MODIFIER_CONFLICT_PRIORITY",
    "LOOKUP_OK",
    "LOOKUP_NO_MAPPING_FOR_DATE",
    "LOOKUP_MISSING_AVAILABILITY_DATE",
    "LOOKUP_NOT_YET_AVAILABLE",
    "LOOKUP_OVERLAPPING_MAPPING",
    "LOOKUP_INVALID_REFERENCE",
    "validate_production_metrics_configuration",
    "validate_metric_profile_row",
    "validate_metric_profiles_data",
    "validate_peer_group_metric_profile_row",
    "validate_peer_group_metric_profile_data",
    "get_metric_profile_for_peer_group_as_of",
    "validate_metric_applicability_row",
    "validate_metric_applicability_data",
    "validate_metric_applicability_modifier_row",
    "validate_metric_applicability_modifiers_data",
    "get_metric_applicability_status",
    "evaluate_modifiers",
    "resolve_metric_applicability",
]
