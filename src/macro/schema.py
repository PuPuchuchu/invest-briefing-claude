"""
Raw Macro Observation schema (Framework v2.1 Step 10B, 2026-10-04:
"Offline Macro State & Regime Engine Implementation").

Why this module exists
-----------------------
Step 10B's own instruction (section 3/4 of the delivered design document)
requires Raw macro data to be kept structurally separate from any derived
value (YoY rate, 3-month momentum, state, confidence, ...) -- never
"deepened in place" the way the instruction explicitly forbids:

    RAW
     v
    TRANSFORM
     v
    DERIVED FEATURES
     v
    STATE
     v
    REGIME

This module owns exactly the first box: what a single Raw Macro
Observation looks like, and the structural (not economic) validation of
one. It does NOT compute any derived feature, does NOT resolve
point-in-time availability (see src/macro/pit.py -- a deliberately
separate, independent module; this project's Step 10B instruction is
explicit that PIT logic for macro data must NOT reuse or modify
src/fundamentals/point_in_time.py, which exists only for SEC XBRL
observations), and does NOT fetch or know about any live data source
(see src/macro/provider.py).

Mirrors this codebase's established convention (see
src/fundamentals/runtime_facts.py's module docstring): one small module
owns one schema + its own structural validation; a separate module owns
PIT lookup; a separate module owns the production-facing resolver/engine.
No central config module exists in this codebase -- this module defines
its own SCHEMA_VERSION constant and its own frozenset vocabularies,
exactly like every other module here (growth.py, quality.py,
runtime_facts.py, ...).

Field semantics (Step 10B section 3, verbatim distinction preserved)
-----------------------------------------------------------------------
    series_id         FRED/ALFRED series identifier, e.g. "PAYEMS".
    observation_date   The economic period this value describes (ISO
                        'YYYY-MM-DD'). This is NEVER the same concept as
                        when the value became knowable -- see below.
    value               The numeric observation value.
    realtime_start      When this particular vintage of the value started
                        being the "current" publicly-known value for this
                        observation_date (ALFRED/FRED real-time period
                        start).
    realtime_end        When this vintage stopped being current (a later
                        revision superseded it), or None if it is still
                        the latest known vintage.
    source              Data source identifier (e.g. "FRED"). Free text,
                        not a fetch instruction -- this module never
                        fetches anything.
    frequency           Reporting frequency, e.g. "MONTHLY", "DAILY".
    units               Series units label (e.g. "Thousands of Persons",
                        "Percent", "Index 1982-1984=100"), kept as given,
                        never normalized or converted here.
    vintage_date        OPTIONAL. When present, this is the single most
                        authoritative "when did this exact number become
                        knowable" date for this observation -- see
                        src/macro/pit.py's docstring for exactly how this
                        interacts with realtime_start/realtime_end when
                        both are present (vintage_date wins when present,
                        per this round's explicit worked example:
                        observation_date=2020-09-01,
                        vintage_date=2020-10-10 means the economic period
                        is 2020-09 but the number was not knowable until
                        2020-10-10).

Example of the observation_date vs. knowability-date distinction this
whole schema exists to preserve (Step 10B section 3's own example):

    observation_date = "2020-09-01"   (what period the data describes)
    vintage_date     = "2020-10-10"   (when it became knowable)

A caller must never treat observation_date as a usable "as of" cutoff by
itself -- doing so is exactly how look-ahead bias enters a backtest (same
principle point_in_time.py enforces for SEC data, independently
re-implemented here for macro data).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

SCHEMA_VERSION = "macro_schema_v0.1"


# ============================================================
# RAW OBSERVATION FIELDS
# ============================================================

RAW_OBSERVATION_REQUIRED_FIELDS = [
    "series_id",
    "observation_date",
    "value",
    "realtime_start",
    "source",
    "frequency",
    "units",
]

# Optional fields: a row missing these is still structurally valid.
RAW_OBSERVATION_OPTIONAL_FIELDS = [
    "realtime_end",
    "vintage_date",
]

RAW_OBSERVATION_FIELDS = RAW_OBSERVATION_REQUIRED_FIELDS + RAW_OBSERVATION_OPTIONAL_FIELDS

VALID_FREQUENCIES = frozenset({"DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "ANNUAL"})


# ============================================================
# DATE / NUMBER HELPERS (private to this module -- this codebase's
# established convention is that each domain owns its own copy of these
# helpers rather than sharing one; see point_in_time.py / runtime_facts.py
# / peer_classification.py, each of which independently defines the same
# shape of helper)
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


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ============================================================
# STRUCTURAL VALIDATION
# ============================================================

def validate_raw_observation(observation: dict) -> list[str]:
    """
    Validate ONE raw macro observation's own structural shape. This is
    deliberately shallow -- it does not know what "a valid PAYEMS value"
    looks like economically, only that the schema's required fields are
    present, well-typed, and internally consistent
    (realtime_start <= realtime_end when both are given).

    A row failing this is treated everywhere downstream exactly like a
    row that does not exist -- never partially trusted, never defaulted
    (same fail-closed principle as every other validate_*_row() function
    in this codebase).

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(observation, dict):
        return ["root"]

    failures: list[str] = []

    for field in RAW_OBSERVATION_REQUIRED_FIELDS:
        if observation.get(field) in (None, ""):
            failures.append(field)

    series_id = observation.get("series_id")
    if series_id is not None and not isinstance(series_id, str):
        failures.append("series_id_type")

    observation_date_raw = observation.get("observation_date")
    observation_date = _parse_date(observation_date_raw) if observation_date_raw else None
    if observation_date_raw and observation_date is None:
        failures.append("observation_date_format")

    value = observation.get("value")
    if value is not None and not _is_number(value):
        failures.append("value_type")

    realtime_start_raw = observation.get("realtime_start")
    realtime_start = _parse_date(realtime_start_raw) if realtime_start_raw else None
    if realtime_start_raw and realtime_start is None:
        failures.append("realtime_start_format")

    realtime_end_raw = observation.get("realtime_end")
    realtime_end = None
    if realtime_end_raw:
        realtime_end = _parse_date(realtime_end_raw)
        if realtime_end is None:
            failures.append("realtime_end_format")

    if realtime_start is not None and realtime_end is not None and realtime_start > realtime_end:
        failures.append("realtime_start_after_realtime_end")

    vintage_date_raw = observation.get("vintage_date")
    if vintage_date_raw and _parse_date(vintage_date_raw) is None:
        failures.append("vintage_date_format")

    frequency = observation.get("frequency")
    if frequency is not None and frequency not in VALID_FREQUENCIES:
        failures.append("frequency_unknown")

    return failures


def validate_raw_series(observations: list[dict]) -> list[str]:
    """
    Cross-row structural validation for a list of raw observations
    (normally: all observations for one series_id, though this function
    does not require that -- it validates whatever list it is given).

    Checks, beyond each row's own validate_raw_observation():
        - every row's own structural validity
        - no two rows are byte-identical duplicates (same series_id,
          observation_date, realtime_start, vintage_date) -- a genuine
          duplicate is a reference-data integrity problem, never silently
          collapsed into one

    Deliberately does NOT check sort order -- callers (src/macro/pit.py)
    are required to handle out-of-order input defensively rather than
    assume this module enforces ordering.

    Returns:
        [] when valid
        list[str] of failure identifiers, each prefixed with its row index
    """
    failures: list[str] = []

    seen_keys: dict[tuple, int] = {}

    for index, observation in enumerate(observations):
        for row_failure in validate_raw_observation(observation):
            failures.append(f"rows[{index}].{row_failure}")

        if not isinstance(observation, dict):
            continue

        key = (
            observation.get("series_id"),
            observation.get("observation_date"),
            observation.get("realtime_start"),
            observation.get("vintage_date"),
        )

        if key in seen_keys:
            failures.append(f"rows[{index}].duplicate_of_rows[{seen_keys[key]}]")
        else:
            seen_keys[key] = index

    return failures


__all__ = [
    "SCHEMA_VERSION",
    "RAW_OBSERVATION_REQUIRED_FIELDS",
    "RAW_OBSERVATION_OPTIONAL_FIELDS",
    "RAW_OBSERVATION_FIELDS",
    "VALID_FREQUENCIES",
    "validate_raw_observation",
    "validate_raw_series",
]
