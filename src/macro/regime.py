"""
Overall Market Regime determination (Framework v2.1 Step 10B, 2026-10-04).

Implements the closed, gap-free Growth State x Inflation State mapping
frozen in claude/2026-10-04-step10-market-regime-spec.md (v3), section
"Overall Regime mapping". UNVERIFIED on EITHER axis is checked and
propagated FIRST, before any quadrant/TRANSITION logic -- v3's own
explicit rule: "information absence != directional ambiguity", so an
UNVERIFIED axis must never be silently treated as one more case folded
into TRANSITION.

    EITHER axis UNVERIFIED                     -> UNVERIFIED
    IMPROVING   + DISINFLATING                 -> EXPANSIONARY_DISINFLATION
    IMPROVING   + REACCELERATING               -> EXPANSIONARY_INFLATION
    DETERIORATING + DISINFLATING               -> GROWTH_SCARE_DISINFLATION
    DETERIORATING + REACCELERATING             -> STAGFLATION_RISK
    everything else (any MIXED/UNCHANGED
        involvement not covered above)         -> TRANSITION

TRANSITION vs UNVERIFIED (v3's required verbatim semantics, preserved
exactly -- neither is "confidence level", both are reproduced here so
any future reader of this module sees the same definition the design
doc fixed):

    TRANSITION:  "Data is available and valid, but macro states do not
                  produce a stable directional quadrant."
    UNVERIFIED:  "There is insufficient or invalid evidence to establish
                  the required state."

regime_confidence -- explicitly REJECTED, not implemented
-----------------------------------------------------------------------
Step 10 v3 formally rejects a single top-level `regime_confidence`
scalar (not deferred -- rejected). Reason, preserved from the frozen
spec: growth_confidence's basis (a binary 2-signal agreement check) and
inflation_confidence's basis (a 4-series quorum count) are structurally
heterogeneous, and no deterministic aggregation rule
(MIN / AVERAGE / MAX / WEIGHTED AVERAGE / LOWEST-WINS / 50:50) can
combine them without smuggling in an arbitrary, unjustified weighting
decision. This module therefore:

    - NEVER computes, returns, or accepts a `regime_confidence` field
    - keeps growth_confidence and inflation_confidence as fully
      independent, sibling fields (computed by growth_state.py /
      inflation_state.py respectively, never touched here)
    - treats determine_overall_regime()'s result as PURELY the
      deterministic state-mapping above -- it is not, and must never be
      read as, a confidence-aggregation result

State / Confidence / Data Quality independence (v3's required examples,
preserved): overall_regime=TRANSITION does NOT imply low data quality --
TRANSITION can coexist with GOOD data quality when the underlying data is
complete and valid but simply does not converge to one quadrant.
overall_regime/axis=UNVERIFIED DOES imply a data-sufficiency problem by
definition (see growth_state.py / inflation_state.py: UNVERIFIED is only
ever produced when the underlying momentum/quorum computation itself
could not be completed). These three axes (state, confidence, data
quality) are never conflated in this module or computed from one
another.
"""

from __future__ import annotations

from src.macro.growth_state import GROWTH_UNVERIFIED, GROWTH_IMPROVING, GROWTH_DETERIORATING
from src.macro.inflation_state import (
    INFLATION_UNVERIFIED,
    INFLATION_DISINFLATING,
    INFLATION_REACCELERATING,
)

SCHEMA_VERSION = "macro_regime_v0.1"

REGIME_EXPANSIONARY_DISINFLATION = "EXPANSIONARY_DISINFLATION"
REGIME_EXPANSIONARY_INFLATION = "EXPANSIONARY_INFLATION"
REGIME_GROWTH_SCARE_DISINFLATION = "GROWTH_SCARE_DISINFLATION"
REGIME_STAGFLATION_RISK = "STAGFLATION_RISK"
REGIME_TRANSITION = "TRANSITION"
REGIME_UNVERIFIED = "UNVERIFIED"

OVERALL_REGIMES = frozenset(
    {
        REGIME_EXPANSIONARY_DISINFLATION,
        REGIME_EXPANSIONARY_INFLATION,
        REGIME_GROWTH_SCARE_DISINFLATION,
        REGIME_STAGFLATION_RISK,
        REGIME_TRANSITION,
        REGIME_UNVERIFIED,
    }
)

# Verbatim semantics from the frozen v3 spec -- exposed as data (not just
# comments) so a caller/report can quote the exact required phrasing
# rather than re-paraphrase it.
TRANSITION_DEFINITION = (
    "Data is available and valid, but macro states do not produce a "
    "stable directional quadrant."
)
UNVERIFIED_DEFINITION = (
    "There is insufficient or invalid evidence to establish the "
    "required state."
)

_CLEAN_QUADRANTS = {
    (GROWTH_IMPROVING, INFLATION_DISINFLATING): REGIME_EXPANSIONARY_DISINFLATION,
    (GROWTH_IMPROVING, INFLATION_REACCELERATING): REGIME_EXPANSIONARY_INFLATION,
    (GROWTH_DETERIORATING, INFLATION_DISINFLATING): REGIME_GROWTH_SCARE_DISINFLATION,
    (GROWTH_DETERIORATING, INFLATION_REACCELERATING): REGIME_STAGFLATION_RISK,
}


def determine_overall_regime(growth_state: str, inflation_state: str) -> str:
    """
    Pure, deterministic mapping -- see module docstring. UNVERIFIED on
    either axis is checked first and always wins over any quadrant/
    TRANSITION logic, per v3's explicit ordering requirement.

    This function does NOT validate that growth_state / inflation_state
    are members of their respective frozensets (callers -- growth_state.py
    / inflation_state.py -- already guarantee that); it only implements
    the mapping itself.
    """
    if growth_state == GROWTH_UNVERIFIED or inflation_state == INFLATION_UNVERIFIED:
        return REGIME_UNVERIFIED

    return _CLEAN_QUADRANTS.get((growth_state, inflation_state), REGIME_TRANSITION)


__all__ = [
    "SCHEMA_VERSION",
    "REGIME_EXPANSIONARY_DISINFLATION",
    "REGIME_EXPANSIONARY_INFLATION",
    "REGIME_GROWTH_SCARE_DISINFLATION",
    "REGIME_STAGFLATION_RISK",
    "REGIME_TRANSITION",
    "REGIME_UNVERIFIED",
    "OVERALL_REGIMES",
    "TRANSITION_DEFINITION",
    "UNVERIFIED_DEFINITION",
    "determine_overall_regime",
]
