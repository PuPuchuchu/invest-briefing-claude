"""
Policy State determination (Framework v2.1 Step 10B, 2026-10-04).

Implements the Policy State rule frozen in Step 10 v3 / this round's
section 13: a CHANGE-DIRECTION-ONLY classification of the Fed's target
rate range (DFEDTARU / DFEDTARL), never an absolute-level
RESTRICTIVE/ACCOMMODATIVE judgment (that would require a measured neutral
rate / r-star, which this project does not have and this round
explicitly forbids inventing).

Design note flagged for the Step 10B report (UNRESOLVED / VALIDATION
ITEM, not silently decided): unlike growth/inflation, this round's
instruction for Policy State ("target range의 변화 방향") does not specify
a 3-month smoothing window the way it explicitly does for growth and
inflation. The Fed's target range is a discrete step function (it only
changes on FOMC decision dates, typically 8x/year) -- averaging it over
a rolling 3-month window would blend together periods that are
genuinely, constantly flat between meetings with the rare months a
change actually occurs, which is a materially different smoothing
assumption than growth/inflation's continuously-varying monthly series.
This module therefore compares the LATEST available observation directly
against the PRIOR available observation (simple consecutive-level
comparison), not a rolling window average. This is recorded here as an
explicit engineering choice -- not inferred from the frozen v3 spec,
which is silent on this specific point -- and is listed as a Remaining
Validation Item in the Step 10B report rather than treated as an
already-closed design decision.

DFEDTARU and DFEDTARL are required to move in the SAME direction
(both up, both down, or both unchanged) to produce a determinate
Policy State. Historically the Fed always moves the full target range
together, so disagreement between upper/lower would itself be a data
anomaly. Per Hard Constraint #2 (missing/ambiguous data -> UNVERIFIED,
never an invented fallback), and because Step 10 v3's Policy State
vocabulary has no MIXED value (only EASING / TIGHTENING / UNCHANGED /
UNVERIFIED -- confirmed against the frozen spec), a disagreement between
the two series resolves to UNVERIFIED rather than a newly-invented MIXED
value.
"""

from __future__ import annotations

SCHEMA_VERSION = "macro_policy_state_v0.1"

TARGET_UPPER_SERIES_ID = "DFEDTARU"
TARGET_LOWER_SERIES_ID = "DFEDTARL"

REQUIRED_POLICY_SERIES_IDS = (TARGET_UPPER_SERIES_ID, TARGET_LOWER_SERIES_ID)

POLICY_EASING = "EASING"
POLICY_TIGHTENING = "TIGHTENING"
POLICY_UNCHANGED = "UNCHANGED"
POLICY_UNVERIFIED = "UNVERIFIED"

POLICY_STATES = frozenset({POLICY_EASING, POLICY_TIGHTENING, POLICY_UNCHANGED, POLICY_UNVERIFIED})


def _consecutive_direction(series: list[dict]) -> str | None:
    """Latest observation vs. the immediately preceding one. Returns
    "UP"/"DOWN"/"EQUAL", or None if fewer than 2 points are available.
    Deliberately does not reuse transforms.compare_exact's naming
    directly in its return value (kept as plain UP/DOWN/EQUAL via the
    same comparison semantics) -- imported lazily below to avoid a
    module-level dependency cycle risk and to keep this function's
    signature self-contained for the single, simple comparison it does."""
    if len(series) < 2:
        return None
    from src.macro.transforms import compare_exact

    latest = series[-1]["value"]
    prior = series[-2]["value"]
    return compare_exact(latest, prior)


def determine_policy_state(
    upper_series: list[dict],
    lower_series: list[dict],
) -> str:
    """
    upper_series / lower_series: PIT-gated DFEDTARU / DFEDTARL
    observations, sorted ascending by observation_date.

    Returns one of POLICY_EASING / POLICY_TIGHTENING / POLICY_UNCHANGED /
    POLICY_UNVERIFIED. See module docstring for the consecutive-level
    comparison rule and the "both series must agree" rule.

    Hardening round (2026-10-04): a NaN latest/prior value makes
    _consecutive_direction() return transforms.INVALID. Before this
    fix, if BOTH series independently produced INVALID (e.g. both had a
    NaN latest value), `upper_direction == lower_direction` (both
    INVALID) would pass the "both series must agree" check and fall
    through to `return POLICY_UNCHANGED` -- the identical class of bug
    fixed in transforms.compare_exact() itself, just one level up. This
    is now checked explicitly, before the agreement check, so NaN
    evidence can never produce POLICY_UNCHANGED.
    """
    from src.macro.transforms import DOWN, EQUAL, INVALID, UP

    upper_direction = _consecutive_direction(upper_series)
    lower_direction = _consecutive_direction(lower_series)

    if upper_direction is None or lower_direction is None:
        return POLICY_UNVERIFIED

    if upper_direction == INVALID or lower_direction == INVALID:
        return POLICY_UNVERIFIED

    if upper_direction != lower_direction:
        return POLICY_UNVERIFIED

    if upper_direction == UP:
        return POLICY_TIGHTENING
    if upper_direction == DOWN:
        return POLICY_EASING
    return POLICY_UNCHANGED


__all__ = [
    "SCHEMA_VERSION",
    "TARGET_UPPER_SERIES_ID",
    "TARGET_LOWER_SERIES_ID",
    "REQUIRED_POLICY_SERIES_IDS",
    "POLICY_EASING",
    "POLICY_TIGHTENING",
    "POLICY_UNCHANGED",
    "POLICY_UNVERIFIED",
    "POLICY_STATES",
    "determine_policy_state",
]
