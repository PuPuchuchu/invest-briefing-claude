"""
Risk context feature (Framework v2.1 Step 10B, 2026-10-04).

Input: VIXCLS (CBOE Volatility Index, daily).

Per this round's section 15, this module produces ONLY descriptive,
threshold-free output -- it never classifies VIX into a state using a
fixed level (no "VIX > 20" / "VIX > 30" rule, which this round
explicitly forbids as an arbitrary threshold). There is therefore no
risk "state" enum in this module, by design -- unlike growth/inflation/
policy, which do have closed, deterministic state vocabularies, risk
stays data-only this round.

Output:

    current_value   latest available VIXCLS value, or None if no data
    change_1d        current_value - value one observation back, or None
    change_5d        current_value - value five observations back, or
                      None
    change_20d       current_value - value twenty observations back, or
                      None

These are simple point-to-point level differences (not percentage
changes, not smoothed) -- "change" is taken literally as a level
difference since this round's instruction does not specify a
percentage-change convention.

Historical percentile: this round's instruction says "가능하다면
historical percentile abstraction을 만들 수 있다" (optional: "if
possible"). This module deliberately does NOT implement it this round --
computing a meaningful historical percentile requires choosing a
lookback window, and the frozen v3 spec explicitly lists "historical-
percentile lookback" as a Remaining Validation Item deferred to a future
round, never decided here. Rather than silently pick a lookback window
to produce a number, this module exposes an explicit placeholder field
or percentile, mirroring this codebase's own established convention for
an intentionally-undefined field (see
src/fundamentals/factor_signal_production.py's `confidence` placeholder,
Step 9) -- a documented absence, never a fabricated number.
"""

from __future__ import annotations

SCHEMA_VERSION = "macro_risk_state_v0.1"

RISK_SERIES_ID = "VIXCLS"

HISTORICAL_PERCENTILE_NOT_IMPLEMENTED_REASON = (
    "Historical-percentile lookback window is an explicit Remaining "
    "Validation Item in the frozen Step 10 v3 spec, not yet decided -- "
    "never silently chosen here. See "
    "claude/2026-10-04-step10-market-regime-spec.md section 12."
)


def _change_n_back(values: list[float], n: int) -> float | None:
    if len(values) <= n:
        return None
    return values[-1] - values[-1 - n]


def compute_risk_context(risk_series: list[dict]) -> dict:
    """
    risk_series: PIT-gated VIXCLS observations, sorted ascending by
    observation_date.

    Returns a dict with current_value/change_1d/change_5d/change_20d
    (each float or None) plus an explicit, documented
    historical_percentile placeholder (value=None, status=
    "NOT_IMPLEMENTED") -- see module docstring.
    """
    values = [row["value"] for row in risk_series]

    current_value = values[-1] if values else None

    return {
        "current_value": current_value,
        "change_1d": _change_n_back(values, 1),
        "change_5d": _change_n_back(values, 5),
        "change_20d": _change_n_back(values, 20),
        "historical_percentile": {
            "value": None,
            "status": "NOT_IMPLEMENTED",
            "reason": HISTORICAL_PERCENTILE_NOT_IMPLEMENTED_REASON,
        },
    }


__all__ = [
    "SCHEMA_VERSION",
    "RISK_SERIES_ID",
    "HISTORICAL_PERCENTILE_NOT_IMPLEMENTED_REASON",
    "compute_risk_context",
]
