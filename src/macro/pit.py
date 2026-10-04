"""
Point-in-time gating layer for Raw Macro Observations (Framework v2.1
Step 10B, 2026-10-04).

Why this module exists, and why it does NOT reuse point_in_time.py
-----------------------------------------------------------------------
src/fundamentals/point_in_time.py gates SEC XBRL observations on a single
field (`filed <= evaluation_date`). Macro/FRED data has a structurally
different PIT problem: the same economic observation_date can have
MULTIPLE vintages over time (a government agency revises last month's
payroll number next month, and again next year). Step 10 v3 / Step 10B's
explicit instruction is to build this as a NEW, independent module rather
than extending or modifying the frozen point_in_time.py -- while
mirroring its design principles exactly:

    - fail-closed: a missing/unparseable knowability date means NOT
      available, never defaulted to available
    - never mutate input
    - self-validate the one invariant this module exists to protect
    - separate "which vintage is the right one" (selection) from
      "is this vintage knowable yet" (gating) -- mirrors
      point_in_time.py's own separation of concept-selection
      (sec_normalizer.py's job) from availability gating (this module's
      job)

Knowability date -- the single rule
-----------------------------------------------------------------------
For one raw observation (see src/macro/schema.py), the date on which it
became knowable to the system is:

    vintage_date            if present (the single most authoritative
                              signal when given -- see schema.py's module
                              docstring worked example:
                              observation_date=2020-09-01,
                              vintage_date=2020-10-10 means the number was
                              not knowable until 2020-10-10, regardless of
                              what period it describes)
    else realtime_start      (ALFRED/FRED real-time period start) when
                              vintage_date is absent

    else NOT AVAILABLE       (fails closed -- an observation with neither
                              field is never treated as always-available)

observation_date (the economic period) is NEVER used as a knowability
date by itself -- exactly the distinction schema.py's docstring exists to
preserve.

Vintage selection -- the second rule
-----------------------------------------------------------------------
A single series_id + observation_date can legitimately have several raw
rows (several vintages: an initial estimate, a revision, a second
revision, ...). "What was knowable as of as_of_date" for that
observation_date is: among the vintages whose knowability date <=
as_of_date, the one with the LATEST knowability date (the most recently
revised value that was already public by as_of_date). This is exactly the
"no look-ahead" requirement in Step 10B section 22: an as_of_date before a
later revision's knowability date must never see that revision; an
as_of_date on/after it must see it.

If `realtime_end` is present on a vintage, it is informational only
(when that vintage stopped being current) -- vintage selection here does
not need it, because selecting "latest knowability date <= as_of_date"
among candidates already produces the vintage that was current as of
as_of_date without needing to separately check realtime_end. It is kept
on the observation and never dropped.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

SCHEMA_VERSION = "macro_pit_v0.1"


# ============================================================
# DATE HELPERS (private to this module -- see module docstring on why
# each domain owns its own copy rather than sharing one)
# ============================================================

def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def coerce_as_of_date(value: Any) -> date:
    """Normalize the caller-supplied as_of_date into a date object. Raises
    ValueError rather than silently falling back to today's date or an
    unbounded gate -- mirrors point_in_time.coerce_evaluation_date()'s
    identical reasoning: an unparseable as_of_date directly controls
    look-ahead-bias protection and must never be papered over."""
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError(f"as_of_date could not be parsed as an ISO date: {value!r}")
    return parsed


def _knowability_date(observation: dict) -> date | None:
    """The single knowability date for one raw observation -- see module
    docstring. Returns None (fails closed) when neither vintage_date nor
    realtime_start is present/parseable."""
    vintage_date_raw = observation.get("vintage_date")
    if vintage_date_raw:
        return _parse_date(vintage_date_raw)

    realtime_start_raw = observation.get("realtime_start")
    if realtime_start_raw:
        return _parse_date(realtime_start_raw)

    return None


# ============================================================
# OBSERVATION-LEVEL GATING
# ============================================================

def is_observation_available_as_of(observation: dict, as_of_date: date) -> bool:
    """
    True only if this observation's knowability date is on or before
    as_of_date. An observation whose knowability date is missing or
    unparseable is treated as NOT available -- fails closed, never open
    (mirrors point_in_time.is_observation_available_as_of() exactly, with
    "filed" replaced by this module's own knowability-date rule).
    """
    knowable = _knowability_date(observation)
    if knowable is None:
        return False
    return knowable <= as_of_date


def filter_observations_as_of(observations: list[dict], as_of_date: date) -> list[dict]:
    """Keep only observations available as of as_of_date. Order and every
    field on the surviving observations are preserved unchanged. Input is
    never mutated."""
    return [
        observation
        for observation in observations
        if isinstance(observation, dict) and is_observation_available_as_of(observation, as_of_date)
    ]


# ============================================================
# VINTAGE SELECTION -- collapse multiple vintages of the same
# (series_id, observation_date) into the single one that was knowable and
# current as of as_of_date
# ============================================================

def select_latest_vintage_as_of(
    observations: list[dict],
    as_of_date: date,
) -> dict | None:
    """
    Among a list of candidate vintages ALL describing the SAME
    (series_id, observation_date) pair, return the single one that was
    knowable as of as_of_date AND has the latest knowability date among
    those (i.e. the most-recently-revised value already public by
    as_of_date). Returns None if no candidate is available as of
    as_of_date.

    Ties (two vintages with the identical knowability date for the same
    observation_date) are a reference-data integrity problem, not a
    silent "pick one" -- this is reported via
    detect_duplicate_knowability_dates() below; this function itself
    deterministically picks the first such row in input order rather than
    raising, so a caller who has not run the integrity check still gets a
    deterministic (if possibly-flagged-elsewhere) result.
    """
    available = filter_observations_as_of(observations, as_of_date)
    if not available:
        return None

    best: dict | None = None
    best_knowable: date | None = None

    for observation in available:
        knowable = _knowability_date(observation)
        if knowable is None:
            continue
        if best_knowable is None or knowable > best_knowable:
            best = observation
            best_knowable = knowable

    return best


def detect_duplicate_knowability_dates(observations: list[dict]) -> list[str]:
    """
    Reference-data integrity check: within one (series_id,
    observation_date) group, no two vintages should share the identical
    knowability date -- that would mean two different values both claim
    to have become knowable at the exact same moment, which is ambiguous
    and must be surfaced rather than silently resolved by
    select_latest_vintage_as_of()'s input-order tiebreak.

    Returns:
        [] when no duplicates found
        list[str] of human-readable duplicate descriptions otherwise
    """
    groups: dict[tuple, dict[date, int]] = {}
    failures: list[str] = []

    for observation in observations:
        if not isinstance(observation, dict):
            continue
        key = (observation.get("series_id"), observation.get("observation_date"))
        knowable = _knowability_date(observation)
        if knowable is None:
            continue
        bucket = groups.setdefault(key, {})
        bucket[knowable] = bucket.get(knowable, 0) + 1

    for (series_id, observation_date), by_date in groups.items():
        for knowable, count in by_date.items():
            if count > 1:
                failures.append(
                    f"series_id={series_id!r} observation_date={observation_date!r} "
                    f"has {count} vintages sharing knowability_date={knowable.isoformat()}"
                )

    return failures


# ============================================================
# SERIES-LEVEL: RAW -> PIT-GATED, DEDUPED, SORTED TIME SERIES
# ============================================================

def get_point_in_time_series(
    observations: list[dict],
    series_id: str,
    as_of_date: Any,
) -> list[dict]:
    """
    Given raw observations for potentially many series_ids and many
    vintages per observation_date, return the single PIT-correct time
    series for ONE series_id as of as_of_date: for every distinct
    observation_date present, the one vintage that was knowable and
    current as of as_of_date (via select_latest_vintage_as_of()), sorted
    ascending by observation_date. observation_dates with no vintage
    knowable yet as of as_of_date are simply absent from the result --
    not an error, the ordinary "not public yet" case (mirrors
    point_in_time.get_point_in_time_history()'s identical "empty but
    valid" semantics for a newly-listed filer).

    Input is never mutated. Rows for other series_ids are ignored.
    """
    as_of = coerce_as_of_date(as_of_date)

    by_observation_date: dict[str, list[dict]] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        if observation.get("series_id") != series_id:
            continue
        obs_date = observation.get("observation_date")
        if not obs_date:
            continue
        by_observation_date.setdefault(obs_date, []).append(observation)

    result: list[dict] = []
    for obs_date, candidates in by_observation_date.items():
        selected = select_latest_vintage_as_of(candidates, as_of)
        if selected is not None:
            result.append(selected)

    result.sort(key=lambda row: row["observation_date"])
    return result


__all__ = [
    "SCHEMA_VERSION",
    "coerce_as_of_date",
    "is_observation_available_as_of",
    "filter_observations_as_of",
    "select_latest_vintage_as_of",
    "detect_duplicate_knowability_dates",
    "get_point_in_time_series",
]
