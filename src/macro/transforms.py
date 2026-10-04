"""
Pure numeric transforms for PIT-gated macro time series (Framework v2.1
Step 10B, 2026-10-04).

This module sits directly below the state-determination layer in the
pipeline the Step 10B instruction lays out:

    RAW -> TRANSFORM (this module) -> DERIVED FEATURES -> STATE -> REGIME

It deliberately knows NOTHING about economic meaning (no "IMPROVING" /
"DISINFLATING" vocabulary lives here -- that belongs to
src/macro/growth_state.py, src/macro/inflation_state.py, etc., each of
which applies its own naming on top of these same generic numbers,
mirroring Step 10B's explicit instruction not to let one state's
vocabulary bleed into another's). It also never invents a threshold,
tolerance, or smoothing window on its own -- every window size used here
is passed in by the caller, who got it from the frozen Step 10 v3 design
(the 3-month baseline).

Equality (Step 10B section 26)
-----------------------------------------------------------------------
This module uses EXACT equality (`==`) wherever the design calls for
"recent == prior" -> UNCHANGED. No floating-point tolerance is added. If
this ever produces a surprising result against real data, that is a
finding to report, not a reason to add epsilon comparison here --
Step 10B section 26 explicitly requires reporting first, and treating
tolerance as a separate future design decision rather than something
quietly patched in.

NaN handling (Step 10B Hardening round, 2026-10-04 -- fixes a genuine
implementation defect found during the first implementation pass)
-----------------------------------------------------------------------
NaN is NOT valid numeric evidence. Before this fix, `compare_exact(nan,
nan)` fell through to EQUAL (IEEE 754: `nan > nan` and `nan < nan` are
both False, so the original two-branch `if/elif/else` silently landed on
the "equal" branch) and `compare_exact(nan, 5.0)` likewise fell through
to EQUAL -- both are implementation bugs, not an intentional "tolerance",
and not something Step 10B's design ever specified: an invalid value
must never be treated as "no change" (EQUAL/UNCHANGED), because that is
itself a false, normal-looking signal manufactured from invalid input.

`compare_exact()` now returns a THIRD result, INVALID, whenever either
input is NaN -- never UP, never DOWN, never EQUAL. This is a correctness
fix, not a tolerance: finite-number comparisons are completely unchanged
(still exact `==`/`>`/`<`, no epsilon introduced). Every caller
(growth_state.py, inflation_state.py, curve_state.py, policy_state.py)
maps INVALID to its own module's UNVERIFIED-equivalent value, exactly
like it already does for "insufficient history" -- NaN evidence and
missing evidence both resolve to the same fail-closed outcome, which is
the behavior Step 10B's Hard Constraint #2 (missing/ambiguous data ->
UNVERIFIED, never a favorable/implicit fallback) already required.

All functions here operate on a plain list of PIT-gated observations
(each a dict with at least "observation_date" and "value", as returned
by src/macro/pit.get_point_in_time_series()) already sorted ascending by
observation_date -- they do not themselves call into src/macro/pit.py.
"""

from __future__ import annotations

import math
from typing import Any

SCHEMA_VERSION = "macro_transforms_v0.1"


def _is_invalid_number(value: Any) -> bool:
    """True for NaN (and, defensively, for anything math.isnan() cannot
    evaluate at all -- treated as invalid rather than crashing, since
    this codebase's established convention is to fail closed on
    malformed numeric input rather than raise deep inside a comparison).
    Never treats +-inf as invalid -- Step 10B's NaN hardening round
    scoped this fix to NaN specifically; infinite values are out of
    scope and behave exactly as before."""
    try:
        return math.isnan(value)
    except TypeError:
        return True


def _values(series: list[dict]) -> list[float]:
    return [row["value"] for row in series]


def monthly_differences(series: list[dict]) -> list[float]:
    """First difference of consecutive values: diff[i] = value[i] -
    value[i-1]. Series must already be sorted ascending by
    observation_date (caller's responsibility -- this is a pure numeric
    helper, not a sort). Returns a list one shorter than the input (empty
    if fewer than 2 points)."""
    values = _values(series)
    return [values[i] - values[i - 1] for i in range(1, len(values))]


def recent_prior_window_averages(
    values: list[float],
    window: int,
) -> tuple[float, float] | None:
    """
    Split the tail of `values` into two adjacent, non-overlapping windows
    of size `window` and return (recent_avg, prior_avg):

        prior window:  values[-2*window : -window]
        recent window: values[-window   :        ]

    Returns None if fewer than 2*window values are available -- this is
    the ONLY sufficiency rule applied here; callers (the *_state modules)
    are responsible for turning "None" into their own UNVERIFIED state,
    never into a silently-smaller window.
    """
    if window <= 0:
        raise ValueError(f"window must be a positive integer, got {window!r}")

    if len(values) < 2 * window:
        return None

    prior_window = values[-2 * window : -window]
    recent_window = values[-window:]

    recent_avg = sum(recent_window) / window
    prior_avg = sum(prior_window) / window

    return recent_avg, prior_avg


UP = "UP"
DOWN = "DOWN"
EQUAL = "EQUAL"
INVALID = "INVALID"

COMPARISON_RESULTS = frozenset({UP, DOWN, EQUAL, INVALID})


def compare_exact(recent_avg: float, prior_avg: float) -> str:
    """Pure numeric comparison, exact equality (no tolerance -- see
    module docstring). Returns one of UP / DOWN / EQUAL / INVALID.

    INVALID is returned whenever either input is NaN -- checked FIRST,
    before any ordering comparison, so NaN can never fall through to
    EQUAL via IEEE 754's "nan > nan" / "nan < nan" both being False (see
    module docstring's NaN Handling section for why the pre-hardening
    version of this function was a genuine bug, not a tolerance). This
    is the ONLY new return value; finite-number comparisons are
    byte-for-byte unchanged.

    This has no economic meaning by itself; each *_state module maps
    UP/DOWN/EQUAL/INVALID to its own vocabulary (e.g. growth_state.py
    maps a payroll UP to PAYROLL_IMPROVING, but maps an
    unemployment-rate UP to UNEMPLOYMENT_DETERIORATING -- the sign of
    "good" is state-specific, never decided here; EVERY caller maps
    INVALID to its own UNVERIFIED-equivalent value, never to its
    UNCHANGED-equivalent value)."""
    if _is_invalid_number(recent_avg) or _is_invalid_number(prior_avg):
        return INVALID
    if recent_avg > prior_avg:
        return UP
    if recent_avg < prior_avg:
        return DOWN
    return EQUAL


def yoy_rate(series: list[dict], periods_per_year: int) -> list[dict]:
    """
    Year-over-year rate of change: for each point with a same-point
    observation exactly `periods_per_year` observations earlier in the
    (sorted, gap-free-assumed) series, compute
    value[t] / value[t - periods_per_year] - 1.

    Returns a list of {"observation_date": ..., "value": <yoy rate>}
    dicts, one per point that had a valid comparison base (division by
    zero at the base skips that point rather than raising -- consistent
    with this codebase's existing growth.py convention of treating an
    unusable denominator as "no computable value" rather than a crash).

    `periods_per_year` is caller-supplied (12 for monthly CPI/PCE series)
    -- this module does not hardcode or infer frequency.
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year!r}")

    result: list[dict] = []
    for i in range(periods_per_year, len(series)):
        base = series[i - periods_per_year]["value"]
        current = series[i]["value"]
        if base == 0:
            continue
        result.append(
            {
                "observation_date": series[i]["observation_date"],
                "value": (current / base) - 1,
            }
        )
    return result


__all__ = [
    "SCHEMA_VERSION",
    "monthly_differences",
    "recent_prior_window_averages",
    "UP",
    "DOWN",
    "EQUAL",
    "INVALID",
    "COMPARISON_RESULTS",
    "compare_exact",
    "yoy_rate",
]
