"""
Macro State & Regime Engine -- top-level composition (Framework v2.1
Step 10B, 2026-10-04).

This is the single production-facing entry point for this round:

    RAW (caller-supplied observations, from any MacroDataProvider --
         src/macro/provider.py -- real or synthetic)
      -> PIT gating + vintage selection      (src/macro/pit.py)
      -> numeric transforms                   (src/macro/transforms.py)
      -> per-axis state determination         (src/macro/growth_state.py,
                                                 src/macro/inflation_state.py,
                                                 src/macro/policy_state.py,
                                                 src/macro/curve_state.py,
                                                 src/macro/risk_state.py)
      -> Overall Regime                        (src/macro/regime.py)
      -> structured output (this module)

Mirrors this codebase's established convention of a thin orchestration
layer that calls already-validated building blocks and does not
reimplement any of their logic itself (see
src/fundamentals/orchestrator.py's own module docstring, and
src/fundamentals/factor_signal_production.py's "pure assembly" framing
from Step 9).

Caller contract: `observations` is the FULL pool of raw macro
observations (see src/macro/schema.py) across every series_id this
engine needs, already obtained from a MacroDataProvider (real or
synthetic -- this module never fetches anything itself, never imports
src/macro/provider.py, and has zero network dependency). This mirrors
this codebase's "callers own I/O" convention (run_fundamentals_pipeline()
takes raw_companyfacts/peer_rows/etc. as already-loaded parameters, not a
fetcher).

Evidence Traceability (this round's section 20): the returned
`evidence` block records, for growth and inflation, the intermediate
value at every step from raw observation count through to the final
state -- computed structurally, in the same order the calculation
itself runs, never written as a post-hoc explanation of an
already-decided result (this round's explicit prohibition: "LLM이나
문자열 설명을 먼저 생성한 후 결과에 맞춃 근거를 만드는 구조는 금지").

Data Quality / Coverage Status (this round's section 27): computed
purely from raw observation COUNTS per required series (independent of
any state/confidence value) -- never inferred from, and never used to
infer, growth_state/inflation_state/overall_regime. A TRANSITION or
UNVERIFIED overall_regime does not change how data_quality is computed,
and vice versa (see src/macro/regime.py's module docstring for the
State/Confidence/Data-Quality independence this preserves).

No Composite Score, no Buy/Sell Score interaction, no numeric merge with
any stock-level factor score exists anywhere in this module or this
package -- Market Regime output here is a standalone Context Layer
object, consumed (if at all, in a future round, not this one) by reading
its fields, never by arithmetic combination.
"""

from __future__ import annotations

from typing import Any

from src.macro import growth_state as gs
from src.macro import inflation_state as ins
from src.macro import pit
from src.macro import policy_state as ps
from src.macro import regime as rg
from src.macro.curve_state import compute_curve_context
from src.macro.risk_state import compute_risk_context

SCHEMA_VERSION = "macro_engine_v0.1"

PAYEMS_SERIES_ID = "PAYEMS"
UNRATE_SERIES_ID = "UNRATE"

_GROWTH_MIN_RAW_POINTS = 2 * gs.MOMENTUM_WINDOW + 1  # PAYEMS: differenced first
_UNRATE_MIN_RAW_POINTS = 2 * gs.MOMENTUM_WINDOW  # UNRATE: used as raw level
_INFLATION_MIN_RAW_POINTS = ins.YOY_PERIODS_PER_YEAR + 2 * ins.CONSENSUS_WINDOW
_POLICY_MIN_RAW_POINTS = 2
_CURVE_MIN_RAW_POINTS = 1
_RISK_MIN_RAW_POINTS = 1


def _pit_series(observations: list[dict], series_id: str, as_of_date: Any) -> list[dict]:
    return pit.get_point_in_time_series(observations, series_id, as_of_date)


def _coverage_entry(series_id: str, pit_series: list[dict], min_required: int) -> dict:
    count = len(pit_series)
    return {
        "series_id": series_id,
        "pit_point_count": count,
        "min_required": min_required,
        "sufficient": count >= min_required,
    }


def compute_macro_regime(*, as_of_date: Any, observations: list[dict]) -> dict:
    """
    observations: the full raw macro observation pool (all series_ids,
    all vintages -- see module docstring). as_of_date: ISO date string or
    date -- the PIT cutoff this entire computation is gated to.

    Returns the Step 10B Output Schema (section 19):

        {
          "schema_version": ...,
          "as_of_date": "YYYY-MM-DD",
          "growth_state": ..., "growth_confidence": ...,
          "inflation_state": ..., "inflation_confidence": ...,
          "policy_state": ...,
          "curve_state": {curve_level, curve_direction, curve_inverted},
          "risk_state": {current_value, change_1d, change_5d, change_20d,
                          historical_percentile},
          "overall_regime": ...,
          "data_quality": "GOOD" | "PARTIAL" | "INSUFFICIENT",
          "coverage_status": {series_id: {...}, ...},
          "evidence": {"growth": {...}, "inflation": {...}},
        }

    No `regime_confidence` field exists anywhere in this output -- see
    src/macro/regime.py's module docstring for why that is a rejected
    design, not an oversight.
    """
    as_of = pit.coerce_as_of_date(as_of_date)

    # ---- PIT-gated series for every required input ----
    payems = _pit_series(observations, PAYEMS_SERIES_ID, as_of)
    unrate = _pit_series(observations, UNRATE_SERIES_ID, as_of)

    inflation_series_by_id = {
        sid: _pit_series(observations, sid, as_of) for sid in ins.REQUIRED_INFLATION_SERIES_IDS
    }

    upper = _pit_series(observations, ps.TARGET_UPPER_SERIES_ID, as_of)
    lower = _pit_series(observations, ps.TARGET_LOWER_SERIES_ID, as_of)

    curve = _pit_series(observations, "T10Y2Y", as_of)
    risk = _pit_series(observations, "VIXCLS", as_of)

    # ---- Growth axis ----
    payroll_momentum = gs.compute_payroll_momentum(payems)
    unemployment_momentum = gs.compute_unemployment_momentum(unrate)
    growth_state, growth_confidence = gs.determine_growth_state(
        payroll_momentum, unemployment_momentum
    )

    # ---- Inflation axis ----
    inflation_directions = {
        sid: ins.compute_inflation_direction(series) for sid, series in inflation_series_by_id.items()
    }
    inflation_state, inflation_confidence = ins.determine_inflation_state(inflation_directions)

    # ---- Policy axis ----
    policy_state = ps.determine_policy_state(upper, lower)

    # ---- Curve / Risk context features ----
    curve_context = compute_curve_context(curve)
    risk_context = compute_risk_context(risk)

    # ---- Overall Regime ----
    overall_regime = rg.determine_overall_regime(growth_state, inflation_state)

    # ---- Coverage / Data Quality (purely count-based, independent of
    # the state/confidence values computed above) ----
    coverage_status = {
        PAYEMS_SERIES_ID: _coverage_entry(PAYEMS_SERIES_ID, payems, _GROWTH_MIN_RAW_POINTS),
        UNRATE_SERIES_ID: _coverage_entry(UNRATE_SERIES_ID, unrate, _UNRATE_MIN_RAW_POINTS),
        **{
            sid: _coverage_entry(sid, series, _INFLATION_MIN_RAW_POINTS)
            for sid, series in inflation_series_by_id.items()
        },
        ps.TARGET_UPPER_SERIES_ID: _coverage_entry(ps.TARGET_UPPER_SERIES_ID, upper, _POLICY_MIN_RAW_POINTS),
        ps.TARGET_LOWER_SERIES_ID: _coverage_entry(ps.TARGET_LOWER_SERIES_ID, lower, _POLICY_MIN_RAW_POINTS),
        "T10Y2Y": _coverage_entry("T10Y2Y", curve, _CURVE_MIN_RAW_POINTS),
        "VIXCLS": _coverage_entry("VIXCLS", risk, _RISK_MIN_RAW_POINTS),
    }

    sufficient_flags = [entry["sufficient"] for entry in coverage_status.values()]
    if all(sufficient_flags):
        data_quality = "GOOD"
    elif any(sufficient_flags):
        data_quality = "PARTIAL"
    else:
        data_quality = "INSUFFICIENT"

    evidence = {
        "growth": {
            "payems_pit_point_count": len(payems),
            "unrate_pit_point_count": len(unrate),
            "payroll_momentum": payroll_momentum,
            "unemployment_momentum": unemployment_momentum,
            "growth_state": growth_state,
            "growth_confidence": growth_confidence,
        },
        "inflation": {
            "pit_point_counts": {sid: len(series) for sid, series in inflation_series_by_id.items()},
            "directions": inflation_directions,
            "inflation_state": inflation_state,
            "inflation_confidence": inflation_confidence,
        },
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of_date": as_of.isoformat(),
        "growth_state": growth_state,
        "growth_confidence": growth_confidence,
        "inflation_state": inflation_state,
        "inflation_confidence": inflation_confidence,
        "policy_state": policy_state,
        "curve_state": curve_context,
        "risk_state": risk_context,
        "overall_regime": overall_regime,
        "data_quality": data_quality,
        "coverage_status": coverage_status,
        "evidence": evidence,
    }


__all__ = [
    "SCHEMA_VERSION",
    "PAYEMS_SERIES_ID",
    "UNRATE_SERIES_ID",
    "compute_macro_regime",
]
