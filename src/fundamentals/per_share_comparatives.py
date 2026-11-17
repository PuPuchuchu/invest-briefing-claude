"""
STEP 24.6-N: same-filing comparative values for per-share YoY (diluted EPS).

Problem this solves
-------------------
The standalone-quarter EPS history takes every quarter from that quarter's
OWN original filing. After a stock split this compares a pre-split prior-year
EPS with a post-split current EPS (NFLX 2026 Q3: 0.87 vs 5.87 -> -85%), even
though the current quarter's own filing already re-presents the prior-year
quarter on the same per-share basis (0.59 -> +47%).

Policy (S2a, approved 2026-11-14)
---------------------------------
For a per-share YoY pair, the prior-year value is the comparative that the
CURRENT quarter's filing reports for the selected prior-year period. It is
accepted only when exactly ONE observation satisfies all of:

    same accession ("accn") and same "filed" date as the current observation
    same concept and the EXPLICITLY selected unit (the normalized metric's
    unit, passed in by the caller; candidates come from that unit's list
    only, and a current observation tagged with another unit fails closed)
    "start" and "end" equal to the selected prior-year record's period
    a direct ~3-month duration (inclusive days within QUARTER_DAYS)
    filed <= the PIT cutoff
    a finite numeric value

Zero eligible observations -> MISSING; two or more (even with equal values)
-> AMBIGUOUS. Both are fail-closed in growth.py: no fallback to the
own-filing value is ever made.

This module is pure: it imports no other project module (in particular not
sec_history.py, point_in_time.py or orchestrator.py), reads only the
already-PIT-gated concept data it is given, and never mutates its inputs.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any


POLICY_SAME_FILING_COMPARATIVE = "same_filing_comparative"
POLICY_KEY = "per_share_comparative_policy"          # key on growth's eps history dict
ANNOTATION_KEY = "same_filing_comparatives"           # key on each quarterly record copy

BASIS_FOUND = "same_filing_comparative"
BASIS_MISSING = "same_filing_comparative_missing"

FOUND = "FOUND"
MISSING = "MISSING"
AMBIGUOUS = "AMBIGUOUS"
CURRENT_SOURCE_UNAVAILABLE = "CURRENT_SOURCE_UNAVAILABLE"
CURRENT_UNIT_MISMATCH = "CURRENT_UNIT_MISMATCH"
ANNOTATION_ABSENT = "ANNOTATION_ABSENT"

# Same inclusive-day range sec_history uses for a direct standalone quarter (DURATION_RANGES["q1"]).
QUARTER_DAYS = (70, 110)

# Reason texts, one per fail-closed cause (reason_code is SAME_FILING_COMPARATIVE_UNAVAILABLE for all).
REASONS = {
    MISSING: "The current quarter's filing reports no prior-year comparative for the selected period.",
    AMBIGUOUS: "The current quarter's filing reports more than one prior-year comparative for the selected period.",
    CURRENT_SOURCE_UNAVAILABLE: "The filing (accession) of the current quarter could not be identified.",
    CURRENT_UNIT_MISMATCH: "The current quarter's observation is not in the selected EPS unit.",
    ANNOTATION_ABSENT: "Same-filing comparative policy is active but the current record carries no comparative annotation.",
}
MISMATCH_REASON = ("The current quarter's filing reports the prior-year period end only with a different "
                   "start date, duration, unit or filing date.")


def _parse(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _inclusive_days(observation: dict) -> int | None:
    start, end = _parse(observation.get("start")), _parse(observation.get("end"))
    if start is None or end is None or end < start:
        return None
    return (end - start).days + 1


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _is_direct_quarter(observation: dict) -> bool:
    days = _inclusive_days(observation)
    return days is not None and QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]


def _current_observation(record: dict) -> dict | None:
    source = record.get("source")
    current = source.get("current") if isinstance(source, dict) else None
    if not isinstance(current, dict):
        return None
    if not all(isinstance(current.get(k), str) and current.get(k) for k in ("accn", "filed", "end")):
        return None
    return current


def annotate_records(
    quarterly_records: list[dict],
    concept_data: dict | None,
    cutoff: date | str,
    unit: str | None,
) -> list[dict]:
    """
    Return COPIES of the standalone-quarter records, each with an
    ANNOTATION_KEY entry:

        {"status": "OK" | CURRENT_SOURCE_UNAVAILABLE,
         "accn": ..., "filed": ..., "unit": ...,
         "candidates": [{"start", "end", "value"}, ...],   # same accn/filed/unit, direct quarter, filed <= cutoff
         "other_end_matches": [{"start", "end", "unit", "filed"}, ...]}  # same accn, failed another condition

    The prior-year period is not chosen here (growth.py chooses the pair);
    match_comparative() resolves it against the chosen prior record.

    `unit` is the selected (normalized) unit of the concept. It is passed in
    explicitly so this module never depends on how the history builder tags
    observations; a current observation that carries a different "_unit" tag
    is annotated CURRENT_UNIT_MISMATCH (fail-closed), and a missing tag is
    accepted (the observation then counts as the selected unit's).
    """
    cutoff_s = cutoff.isoformat() if isinstance(cutoff, date) else str(cutoff)
    units = (concept_data or {}).get("units") if isinstance(concept_data, dict) else None
    units = units if isinstance(units, dict) else {}

    out = []
    for record in quarterly_records or []:
        if not isinstance(record, dict):
            continue
        copy_ = dict(record)
        current = _current_observation(record)
        if current is None or not isinstance(unit, str) or not unit:
            copy_[ANNOTATION_KEY] = {"status": CURRENT_SOURCE_UNAVAILABLE, "candidates": [], "other_end_matches": []}
            out.append(copy_)
            continue
        if current.get("_unit") is not None and current.get("_unit") != unit:
            copy_[ANNOTATION_KEY] = {"status": CURRENT_UNIT_MISMATCH, "candidates": [], "other_end_matches": []}
            out.append(copy_)
            continue
        candidates, others = [], []
        for unit_name, observations in units.items():
            if not isinstance(observations, list):
                continue
            for o in observations:
                if not isinstance(o, dict) or o.get("accn") != current["accn"]:
                    continue
                if o.get("end") == current["end"] and o.get("start") == current.get("start"):
                    continue                                  # the current period itself
                ok = (unit_name == unit and o.get("filed") == current["filed"]
                      and isinstance(o.get("filed"), str) and o["filed"] <= cutoff_s
                      and _is_direct_quarter(o) and _is_finite_number(o.get("val")))
                entry = {"start": o.get("start"), "end": o.get("end")}
                if ok:
                    candidates.append(dict(entry, value=o["val"]))
                else:
                    others.append(dict(entry, unit=unit_name, filed=o.get("filed")))
        copy_[ANNOTATION_KEY] = {"status": "OK", "accn": current["accn"], "filed": current["filed"],
                                 "unit": unit, "candidates": candidates, "other_end_matches": others}
        out.append(copy_)
    return out


def match_comparative(latest: dict, previous: dict) -> tuple[str, Any, str | None]:
    """
    Resolve the same-filing comparative of `latest` for the prior-year
    record `previous` (as selected by growth.py). Returns
    (FOUND, value, None) or (status, None, reason) with status in
    MISSING / AMBIGUOUS / CURRENT_SOURCE_UNAVAILABLE / ANNOTATION_ABSENT.
    Cardinality rule: exactly one eligible observation; equal duplicates
    are AMBIGUOUS, never collapsed.
    """
    annotation = latest.get(ANNOTATION_KEY) if isinstance(latest, dict) else None
    if not isinstance(annotation, dict):
        return ANNOTATION_ABSENT, None, REASONS[ANNOTATION_ABSENT]
    if annotation.get("status") == CURRENT_UNIT_MISMATCH:
        return CURRENT_UNIT_MISMATCH, None, REASONS[CURRENT_UNIT_MISMATCH]
    if annotation.get("status") != "OK":
        return CURRENT_SOURCE_UNAVAILABLE, None, REASONS[CURRENT_SOURCE_UNAVAILABLE]
    start, end = previous.get("period_start"), previous.get("period_end")
    hits = [c for c in annotation.get("candidates") or [] if c.get("start") == start and c.get("end") == end]
    if len(hits) == 1:
        return FOUND, hits[0]["value"], None
    if len(hits) > 1:
        return AMBIGUOUS, None, REASONS[AMBIGUOUS]
    if any(o.get("end") == end for o in annotation.get("other_end_matches") or []) or \
            any(c.get("end") == end for c in annotation.get("candidates") or []):
        return MISSING, None, MISMATCH_REASON
    return MISSING, None, REASONS[MISSING]


# ------------------------------------------------------------------ production guard rule (GPT N-P1 review P0-3)
# calculate_positive_yoy() codes that can follow a FOUND comparative (the policy only replaced the prior value).
_CODES_AFTER_FOUND = frozenset({"NONPOSITIVE_PRIOR_BASE", "NONPOSITIVE_CURRENT", "NON_NUMERIC_INPUT",
                                "NON_FINITE_INPUT", "NON_FINITE_RESULT"})


def basis_consistency_problem(component: dict | None) -> str | None:
    """
    Status / reason_code / previous_value_basis must agree once the policy
    is active (orchestrator guard; None = consistent):

        OK                                          -> same_filing_comparative
        INVALID SAME_FILING_COMPARATIVE_UNAVAILABLE -> same_filing_comparative_missing
        INVALID NONPOSITIVE_* / NON_NUMERIC_INPUT /
                NON_FINITE_INPUT / NON_FINITE_RESULT -> same_filing_comparative
        MISSING (no pair)                           -> no reason_code and no previous_value_basis key
    Anything else (including a missing basis on a computed pair) is a problem.
    """
    if not isinstance(component, dict):
        return "eps_yoy component is missing"
    status, code, basis = component.get("status"), component.get("reason_code"), component.get("previous_value_basis", None)
    has_basis = "previous_value_basis" in component
    if status == "MISSING":                       # no pair: no reason_code (absent or None) and no basis key
        if code is not None:
            return f"MISSING eps_yoy must not carry a reason_code (got {code!r})"
        return None if not has_basis else "MISSING eps_yoy must not carry previous_value_basis"
    if status == "OK":
        return None if (basis == BASIS_FOUND and code is None) else f"OK eps_yoy requires basis {BASIS_FOUND!r}"
    if status == "INVALID":
        if code == "SAME_FILING_COMPARATIVE_UNAVAILABLE":
            return None if basis == BASIS_MISSING else f"{code} requires basis {BASIS_MISSING!r}"
        if code in _CODES_AFTER_FOUND:
            return None if basis == BASIS_FOUND else f"{code} requires basis {BASIS_FOUND!r}"
        return f"INVALID eps_yoy with unexpected reason_code {code!r}"
    return f"unexpected eps_yoy status {status!r}"
