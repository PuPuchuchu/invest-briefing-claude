"""
Yield Curve context feature (Framework v2.1 Step 10B, 2026-10-04).

Input: T10Y2Y (10-year minus 2-year Treasury yield spread, daily).

Per this round's section 14, this module stays a CONTEXT FEATURE -- it is
deliberately NOT combined with Growth/Inflation state to create any new
regime threshold this round. It produces three descriptive outputs:

    curve_level      latest available T10Y2Y value (the spread itself,
                      in percentage points -- already pre-computed by
                      FRED, not re-derived from separate 10Y/2Y series)
    curve_direction  "RISING" / "FALLING" / "UNCHANGED" / "UNVERIFIED",
                      computed by comparing the average of the latest
                      CURVE_WINDOW raw observations against the average
                      of the CURVE_WINDOW raw observations immediately
                      before that (transforms.recent_prior_window_averages,
                      re-used for implementation consistency with
                      growth/inflation's identical call pattern -- not
                      because T10Y2Y's own instruction specifies this
                      window size; see CURVE_WINDOW's docstring below for
                      exactly what "window" means here)
    curve_inverted   latest value < 0. This is the literal mathematical
                      definition of a yield-curve inversion (the 10Y
                      yield below the 2Y yield), not an arbitrary
                      threshold choice -- zero is not a tunable
                      parameter here.

Design note for the Step 10B report: this round's instruction for yield
curve ("최소 derived output: curve_level / curve_direction /
curve_inverted") does not specify a smoothing window for curve_direction
the way growth/inflation explicitly do. This module calls the same
transforms.recent_prior_window_averages() helper that growth/inflation
use, with CURVE_WINDOW=3, purely for implementation consistency -- not
because the frozen v3 spec states any window for T10Y2Y specifically.
Recorded as a Remaining Validation Item, not an already-closed decision.

Documentation correction (Step 10E, 2026-10-04): earlier revisions of
this docstring described CURVE_WINDOW=3 as "the same 3-month
recent-vs-prior convention" used by growth/inflation. That description
was inaccurate and has been removed. growth_state.MOMENTUM_WINDOW=3 and
inflation_state.CONSENSUS_WINDOW=3 genuinely mean 3 months only because
their input series (PAYEMS/UNRATE/CPI/PCE) are monthly -- each raw
observation already IS one month, so 3 raw observations = 3 months.
transforms.recent_prior_window_averages() itself is a pure raw-observation-
count function: it slices the tail of whatever list it is given into two
adjacent blocks of `window` elements each and averages them. It has no
concept of calendar dates, trading-day calendars, or series frequency --
it never reads observation_date, only list position. T10Y2Y is a DAILY
series, so calling the identical helper with CURVE_WINDOW=3 here means
"the average of the latest 3 raw daily observations vs. the average of
the 3 raw daily observations immediately before that" -- typically 3
trading observations (since T10Y2Y, as published upstream, has no rows
for non-trading days), not 3 calendar months. This was confirmed both by
re-tracing this exact code path and by direct empirical measurement
against ~50 years of real T10Y2Y data (see
claude/2026-10-04-step10d-curve-window-empirical-validation.md section 9
and claude/2026-10-04-step10e-curve-window-semantics-audit.md), which
found CURVE_WINDOW=3's actual reversal-rate behavior to be close to a
5-trading-day comparison and nothing like a ~63-trading-day/~3-month
comparison.

This correction changes documentation only. CURVE_WINDOW's value (3),
recent_prior_window_averages()'s behavior, and every runtime output this
module produces are all byte-for-byte unchanged. It does not select,
endorse, or rule in any particular window size as correct for Curve --
the Curve comparison window remains an open Remaining Validation Item /
DEFERRED per claude/2026-10-04-step10c-macro-semantic-validation.md
(Issue C) and claude/2026-10-04-step10d-curve-window-empirical-validation.md.
This paragraph only replaces a false claim about what the CURRENT,
still-unvalidated CURVE_WINDOW=3 happens to compute -- it does not argue
that 3 raw observations is a good or bad choice.
"""

from __future__ import annotations

import math

from src.macro.transforms import DOWN, EQUAL, INVALID, UP, compare_exact, recent_prior_window_averages

SCHEMA_VERSION = "macro_curve_state_v0.1"

CURVE_SERIES_ID = "T10Y2Y"

# CURVE_WINDOW=3 means 3 raw observations on the input series -- it is a
# pure observation count, not a calendar duration. Because T10Y2Y is a
# daily series, this is typically three trading observations, not three
# calendar months. transforms.recent_prior_window_averages() (the helper
# this value is passed into) is observation-count based and has no
# calendar/frequency awareness; it compares the average of the latest
# CURVE_WINDOW observations against the average of the CURVE_WINDOW
# observations immediately before that. See the module docstring's
# "Documentation correction (Step 10E, 2026-10-04)" section for the full
# explanation and why this does not endorse any particular window value.
CURVE_WINDOW = 3

CURVE_RISING = "RISING"
CURVE_FALLING = "FALLING"
CURVE_UNCHANGED = "UNCHANGED"
CURVE_UNVERIFIED = "UNVERIFIED"

CURVE_DIRECTIONS = frozenset({CURVE_RISING, CURVE_FALLING, CURVE_UNCHANGED, CURVE_UNVERIFIED})


def compute_curve_context(curve_series: list[dict]) -> dict:
    """
    curve_series: PIT-gated T10Y2Y observations, sorted ascending by
    observation_date.

    Returns {"curve_level": float | None, "curve_direction": str,
    "curve_inverted": bool | None}. curve_level / curve_inverted are
    None when curve_series is empty (no data at all -- fails closed,
    never fabricated as 0.0) OR (Hardening round, 2026-10-04) when the
    latest observation's own value is NaN -- a NaN latest value must
    never silently produce curve_inverted=False via Python's
    `nan < 0 is False`, which would be the exact same class of bug this
    round fixed in transforms.compare_exact() (an invalid value masking
    itself as a normal, "not inverted" result).
    """
    if not curve_series:
        return {
            "curve_level": None,
            "curve_direction": CURVE_UNVERIFIED,
            "curve_inverted": None,
        }

    latest_value = curve_series[-1]["value"]
    values = [row["value"] for row in curve_series]

    if isinstance(latest_value, float) and math.isnan(latest_value):
        return {
            "curve_level": None,
            "curve_direction": CURVE_UNVERIFIED,
            "curve_inverted": None,
        }

    averages = recent_prior_window_averages(values, CURVE_WINDOW)
    if averages is None:
        direction = CURVE_UNVERIFIED
    else:
        recent_avg, prior_avg = averages
        comparison = compare_exact(recent_avg, prior_avg)
        if comparison == INVALID:
            direction = CURVE_UNVERIFIED
        elif comparison == UP:
            direction = CURVE_RISING
        elif comparison == DOWN:
            direction = CURVE_FALLING
        else:
            direction = CURVE_UNCHANGED

    return {
        "curve_level": latest_value,
        "curve_direction": direction,
        "curve_inverted": latest_value < 0,
    }


__all__ = [
    "SCHEMA_VERSION",
    "CURVE_SERIES_ID",
    "CURVE_WINDOW",
    "CURVE_RISING",
    "CURVE_FALLING",
    "CURVE_UNCHANGED",
    "CURVE_UNVERIFIED",
    "CURVE_DIRECTIONS",
    "compute_curve_context",
]
