"""
Growth State determination (Framework v2.1 Step 10B, 2026-10-04).

Implements, with no new interpretation added, the Growth State /
Growth Confidence rules frozen in
claude/2026-10-04-step10-market-regime-spec.md (v3), sections 3-1 and the
closed "Growth State" decision table:

Inputs: PAYEMS (nonfarm payrolls, monthly level) and UNRATE
(unemployment rate, monthly level).

Payroll momentum (v3, worked via monthly change, then a 3-month baseline
-- NOT a raw-level comparison):

    PAYEMS_change[t] = PAYEMS[t] - PAYEMS[t-1]
    recent_3m_avg  = mean of the latest 3 PAYEMS_change values
    prior_3m_avg   = mean of the 3 PAYEMS_change values before that

    recent_3m_avg > prior_3m_avg  -> PAYROLL_IMPROVING
    recent_3m_avg < prior_3m_avg  -> PAYROLL_DETERIORATING
    recent_3m_avg == prior_3m_avg -> PAYROLL_UNCHANGED   (exact equality,
                                       no tolerance -- see
                                       transforms.compare_exact)

Unemployment momentum (v3 -- a raw-LEVEL 3-month baseline, no
differencing first; a falling unemployment rate is the economically
improving direction):

    recent_3m_avg = mean of the latest 3 UNRATE levels
    prior_3m_avg  = mean of the 3 UNRATE levels before that

    recent_3m_avg < prior_3m_avg  -> UNEMPLOYMENT_IMPROVING
    recent_3m_avg > prior_3m_avg  -> UNEMPLOYMENT_DETERIORATING
    recent_3m_avg == prior_3m_avg -> UNEMPLOYMENT_UNCHANGED

Growth State combination (v3, closed, gap-free -- UNVERIFIED checked
first; the two "one signal unchanged, the other directional" cells are
the explicit v2 editor's-note gap-fill extending the general
"disagreement -> MIXED" principle to the unchanged case):

    (IMPROVING, IMPROVING)       -> IMPROVING,     HIGH
    (DETERIORATING, DETERIORATING) -> DETERIORATING, HIGH
    (UNCHANGED, UNCHANGED)       -> UNCHANGED,     HIGH
    any other fully-determined combination (including one signal
        UNCHANGED and the other directional, or the two disagreeing
        outright) -> MIXED, MIXED
    either signal UNVERIFIED (insufficient data) -> UNVERIFIED, UNVERIFIED

Growth Confidence is NOT a separately-invented scoring formula -- per
Step 10 v3 section 3-1 and this round's section 9, it is nothing more
than the agreement/disagreement outcome of the SAME two-signal check used
to pick growth_state, exposed as its own component-level field. It is
never aggregated with inflation_confidence (see src/macro/regime.py's
module docstring on why regime_confidence was rejected, not deferred).

The 3-month window is the frozen Step 10 v3 engineering baseline, not a
validated-optimal economic constant (v3's own required phrasing:
"Implementation baseline != Validated optimal parameter"). This module
hardcodes window=3 for exactly that reason -- it is specified, not
rediscovered here.
"""

from __future__ import annotations

from src.macro.transforms import (
    DOWN,
    EQUAL,
    INVALID,
    UP,
    compare_exact,
    monthly_differences,
    recent_prior_window_averages,
)

SCHEMA_VERSION = "macro_growth_state_v0.1"

MOMENTUM_WINDOW = 3

PAYROLL_IMPROVING = "PAYROLL_IMPROVING"
PAYROLL_DETERIORATING = "PAYROLL_DETERIORATING"
PAYROLL_UNCHANGED = "PAYROLL_UNCHANGED"
PAYROLL_UNVERIFIED = "PAYROLL_UNVERIFIED"

PAYROLL_MOMENTUM_VALUES = frozenset(
    {PAYROLL_IMPROVING, PAYROLL_DETERIORATING, PAYROLL_UNCHANGED, PAYROLL_UNVERIFIED}
)

UNEMPLOYMENT_IMPROVING = "UNEMPLOYMENT_IMPROVING"
UNEMPLOYMENT_DETERIORATING = "UNEMPLOYMENT_DETERIORATING"
UNEMPLOYMENT_UNCHANGED = "UNEMPLOYMENT_UNCHANGED"
UNEMPLOYMENT_UNVERIFIED = "UNEMPLOYMENT_UNVERIFIED"

UNEMPLOYMENT_MOMENTUM_VALUES = frozenset(
    {
        UNEMPLOYMENT_IMPROVING,
        UNEMPLOYMENT_DETERIORATING,
        UNEMPLOYMENT_UNCHANGED,
        UNEMPLOYMENT_UNVERIFIED,
    }
)

GROWTH_IMPROVING = "IMPROVING"
GROWTH_DETERIORATING = "DETERIORATING"
GROWTH_MIXED = "MIXED"
GROWTH_UNCHANGED = "UNCHANGED"
GROWTH_UNVERIFIED = "UNVERIFIED"

GROWTH_STATES = frozenset(
    {GROWTH_IMPROVING, GROWTH_DETERIORATING, GROWTH_MIXED, GROWTH_UNCHANGED, GROWTH_UNVERIFIED}
)

GROWTH_CONFIDENCE_HIGH = "HIGH"
GROWTH_CONFIDENCE_MIXED = "MIXED"
GROWTH_CONFIDENCE_UNVERIFIED = "UNVERIFIED"

GROWTH_CONFIDENCE_VALUES = frozenset(
    {GROWTH_CONFIDENCE_HIGH, GROWTH_CONFIDENCE_MIXED, GROWTH_CONFIDENCE_UNVERIFIED}
)


# ============================================================
# PAYROLL MOMENTUM
# ============================================================

def compute_payroll_momentum(payems_series: list[dict]) -> str:
    """
    payems_series: PIT-gated PAYEMS observations, sorted ascending by
    observation_date (as returned by
    src/macro/pit.get_point_in_time_series()).

    Requires at least 2*MOMENTUM_WINDOW + 1 raw level points (to produce
    2*MOMENTUM_WINDOW monthly-change points); returns
    PAYROLL_UNVERIFIED when insufficient, never a smaller window. Also
    returns PAYROLL_UNVERIFIED (never PAYROLL_UNCHANGED) when a NaN
    value anywhere in the comparison windows makes the averages
    uncomparable -- see transforms.compare_exact's INVALID result and
    this module's own Hardening-round docstring note; NaN is invalid
    evidence, never a "no change" signal.
    """
    differences = monthly_differences(payems_series)
    averages = recent_prior_window_averages(differences, MOMENTUM_WINDOW)

    if averages is None:
        return PAYROLL_UNVERIFIED

    recent_avg, prior_avg = averages
    comparison = compare_exact(recent_avg, prior_avg)

    if comparison == INVALID:
        return PAYROLL_UNVERIFIED
    if comparison == UP:
        return PAYROLL_IMPROVING
    if comparison == DOWN:
        return PAYROLL_DETERIORATING
    return PAYROLL_UNCHANGED


# ============================================================
# UNEMPLOYMENT MOMENTUM
# ============================================================

def compute_unemployment_momentum(unrate_series: list[dict]) -> str:
    """
    unrate_series: PIT-gated UNRATE observations, sorted ascending by
    observation_date. Uses the raw level directly (no differencing) per
    Step 10 v3 section 3-1 / this round's section 7. A falling rate is
    UNEMPLOYMENT_IMPROVING (lower unemployment = economically improving).

    Requires at least 2*MOMENTUM_WINDOW level points; returns
    UNEMPLOYMENT_UNVERIFIED when insufficient. Also returns
    UNEMPLOYMENT_UNVERIFIED (never UNEMPLOYMENT_UNCHANGED) on a NaN-
    induced INVALID comparison -- same Hardening-round fix as
    compute_payroll_momentum().
    """
    values = [row["value"] for row in unrate_series]
    averages = recent_prior_window_averages(values, MOMENTUM_WINDOW)

    if averages is None:
        return UNEMPLOYMENT_UNVERIFIED

    recent_avg, prior_avg = averages
    comparison = compare_exact(recent_avg, prior_avg)

    if comparison == INVALID:
        return UNEMPLOYMENT_UNVERIFIED

    # Note the inverted sign vs. payroll: a falling (DOWN) unemployment
    # rate is the economically improving direction.
    if comparison == DOWN:
        return UNEMPLOYMENT_IMPROVING
    if comparison == UP:
        return UNEMPLOYMENT_DETERIORATING
    return UNEMPLOYMENT_UNCHANGED


# ============================================================
# GROWTH STATE + CONFIDENCE (combination table)
# ============================================================

_DIRECTIONAL = {
    PAYROLL_IMPROVING: "IMPROVING",
    PAYROLL_DETERIORATING: "DETERIORATING",
    PAYROLL_UNCHANGED: "UNCHANGED",
    UNEMPLOYMENT_IMPROVING: "IMPROVING",
    UNEMPLOYMENT_DETERIORATING: "DETERIORATING",
    UNEMPLOYMENT_UNCHANGED: "UNCHANGED",
}


def determine_growth_state(
    payroll_momentum: str,
    unemployment_momentum: str,
) -> tuple[str, str]:
    """
    Pure combination function: maps the two already-computed momentum
    signals to (growth_state, growth_confidence) per the frozen Step 10
    v3 table (see module docstring). Never recomputes the signals itself.

    Raises ValueError on an unrecognized input value -- a caller passing
    something outside PAYROLL_MOMENTUM_VALUES / UNEMPLOYMENT_MOMENTUM_VALUES
    is a programming bug, not a data-quality case to paper over.
    """
    if payroll_momentum not in PAYROLL_MOMENTUM_VALUES:
        raise ValueError(f"Unrecognized payroll_momentum: {payroll_momentum!r}")
    if unemployment_momentum not in UNEMPLOYMENT_MOMENTUM_VALUES:
        raise ValueError(f"Unrecognized unemployment_momentum: {unemployment_momentum!r}")

    if payroll_momentum == PAYROLL_UNVERIFIED or unemployment_momentum == UNEMPLOYMENT_UNVERIFIED:
        return GROWTH_UNVERIFIED, GROWTH_CONFIDENCE_UNVERIFIED

    payroll_direction = _DIRECTIONAL[payroll_momentum]
    unemployment_direction = _DIRECTIONAL[unemployment_momentum]

    if payroll_direction == unemployment_direction == "IMPROVING":
        return GROWTH_IMPROVING, GROWTH_CONFIDENCE_HIGH
    if payroll_direction == unemployment_direction == "DETERIORATING":
        return GROWTH_DETERIORATING, GROWTH_CONFIDENCE_HIGH
    if payroll_direction == unemployment_direction == "UNCHANGED":
        return GROWTH_UNCHANGED, GROWTH_CONFIDENCE_HIGH

    # Everything else -- outright disagreement, or one UNCHANGED paired
    # with the other directional -- is MIXED/MIXED (v2 editor's-note
    # gap-fill extending the disagreement principle to the UNCHANGED
    # case; see module docstring).
    return GROWTH_MIXED, GROWTH_CONFIDENCE_MIXED


__all__ = [
    "SCHEMA_VERSION",
    "MOMENTUM_WINDOW",
    "PAYROLL_IMPROVING",
    "PAYROLL_DETERIORATING",
    "PAYROLL_UNCHANGED",
    "PAYROLL_UNVERIFIED",
    "PAYROLL_MOMENTUM_VALUES",
    "UNEMPLOYMENT_IMPROVING",
    "UNEMPLOYMENT_DETERIORATING",
    "UNEMPLOYMENT_UNCHANGED",
    "UNEMPLOYMENT_UNVERIFIED",
    "UNEMPLOYMENT_MOMENTUM_VALUES",
    "GROWTH_IMPROVING",
    "GROWTH_DETERIORATING",
    "GROWTH_MIXED",
    "GROWTH_UNCHANGED",
    "GROWTH_UNVERIFIED",
    "GROWTH_STATES",
    "GROWTH_CONFIDENCE_HIGH",
    "GROWTH_CONFIDENCE_MIXED",
    "GROWTH_CONFIDENCE_UNVERIFIED",
    "GROWTH_CONFIDENCE_VALUES",
    "compute_payroll_momentum",
    "compute_unemployment_momentum",
    "determine_growth_state",
]
