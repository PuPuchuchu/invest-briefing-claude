"""
Metric direction / eligibility specification for the Percentile Engine
(Framework v2.0 Step 10A, 2026-09-20 ChatGPT design review).

This module is the single source of truth for two things the Percentile
Engine needs about every metric it can rank, and NOTHING else:

    1. direction  -- whether a higher raw value or a lower raw value is
       economically "better" for that metric. The Percentile Engine
       (src/fundamentals/percentile_engine.py) uses this to decide which
       side of a peer distribution counts as "worse than target" when
       computing a mid-rank percentile.

    2. factor / weight -- which Factor (quality / growth) a metric
       belongs to, and its weight within that Factor's percentile
       aggregation. These weights are NOT re-derived or independently
       chosen here -- they are imported directly from the same
       already-approved v0.1 absolute-score weights already used by
       quality.py (QUALITY_WEIGHTS) and growth.py (GROWTH_WEIGHTS), so
       there is exactly one place either weight set can be changed.

This module intentionally does NOT compute anything and does NOT read
any live data -- it is pure configuration, mirroring the style of
peer_classification.py's own configuration section.

Percentile ranking operates on the RAW, UNRATED numeric component
values already produced by quality.py / growth.py (their `components`
dict's `value` field) -- never on the derived 1-5 star ratings. Star
ratings are an absolute-threshold signal; percentile rank is a
separate, complementary peer-relative signal for the same underlying
metric.
"""

from __future__ import annotations

from src.fundamentals.growth import GROWTH_WEIGHTS
from src.fundamentals.quality import QUALITY_WEIGHTS


# ============================================================
# CONFIGURATION
# ============================================================

SCHEMA_VERSION = "metric_specs_v0.1"

HIGHER_IS_BETTER = "higher_is_better"
LOWER_IS_BETTER = "lower_is_better"

VALID_DIRECTIONS = frozenset({HIGHER_IS_BETTER, LOWER_IS_BETTER})


# ============================================================
# METRIC DIRECTION TABLE
# ============================================================
#
# One entry per metric that quality.py / growth.py already compute as
# a raw numeric component (see each module's `components` dict). The
# direction assignment mirrors the ALREADY-APPROVED absolute-threshold
# rating logic in each module (e.g. quality.rate_net_debt_to_fcf: lower
# value -> higher rating, so net_debt_to_fcf is lower_is_better here
# too) -- this table does not introduce any new economic judgment, it
# only makes each metric's existing direction machine-readable for the
# Percentile Engine.

METRIC_SPECS: dict[str, dict[str, str]] = {
    # ---- Quality factor components (src/fundamentals/quality.py) ----
    "operating_margin": {
        "factor": "quality",
        "direction": HIGHER_IS_BETTER,
    },
    "net_margin": {
        "factor": "quality",
        "direction": HIGHER_IS_BETTER,
    },
    "fcf_margin": {
        "factor": "quality",
        "direction": HIGHER_IS_BETTER,
    },
    "fcf_conversion": {
        "factor": "quality",
        "direction": HIGHER_IS_BETTER,
    },
    "net_debt_to_fcf": {
        "factor": "quality",
        # lower leverage relative to FCF generation is better; negative
        # net debt (net cash) is the strongest case -- see
        # quality.rate_net_debt_to_fcf.
        "direction": LOWER_IS_BETTER,
    },
    "debt_to_cash": {
        "factor": "quality",
        "direction": LOWER_IS_BETTER,
    },
    "profit_to_cash_consistency": {
        "factor": "quality",
        # quality.rate_profit_to_cash_consistency rates higher ratios
        # (FCF margin exceeding net margin) more favorably.
        "direction": HIGHER_IS_BETTER,
    },

    # ---- Growth factor components (src/fundamentals/growth.py) ----
    "revenue_yoy": {
        "factor": "growth",
        "direction": HIGHER_IS_BETTER,
    },
    "operating_income_yoy": {
        "factor": "growth",
        "direction": HIGHER_IS_BETTER,
    },
    "eps_yoy": {
        "factor": "growth",
        "direction": HIGHER_IS_BETTER,
    },
    "fcf_yoy": {
        "factor": "growth",
        "direction": HIGHER_IS_BETTER,
    },
    "revenue_cagr_3y": {
        "factor": "growth",
        "direction": HIGHER_IS_BETTER,
    },
}


# ============================================================
# FACTOR WEIGHTS (imported, not duplicated)
# ============================================================

FACTOR_WEIGHTS: dict[str, dict[str, float]] = {
    "quality": QUALITY_WEIGHTS,
    "growth": GROWTH_WEIGHTS,
}


# ============================================================
# HELPERS
# ============================================================

def get_direction(metric_name: str) -> str:
    """
    Return the direction ("higher_is_better" / "lower_is_better") for a
    known metric.

    Raises KeyError for an unknown metric name -- callers must not
    silently default a direction, since ranking a metric backwards is a
    correctness bug, not a data-availability question.
    """
    spec = METRIC_SPECS.get(metric_name)

    if spec is None:
        raise KeyError(
            f"No metric_specs entry for {metric_name!r}. "
            f"Known metrics: {sorted(METRIC_SPECS)}"
        )

    return spec["direction"]


def get_factor(metric_name: str) -> str:
    """
    Return which Factor ("quality" / "growth") a known metric belongs to.
    """
    spec = METRIC_SPECS.get(metric_name)

    if spec is None:
        raise KeyError(
            f"No metric_specs entry for {metric_name!r}. "
            f"Known metrics: {sorted(METRIC_SPECS)}"
        )

    return spec["factor"]


def metrics_for_factor(factor: str) -> list[str]:
    """
    Return the metric names belonging to a given Factor, in the same
    order as that Factor's weight dict (dict insertion order).
    """
    weights = FACTOR_WEIGHTS.get(factor)

    if weights is None:
        raise KeyError(
            f"Unknown factor {factor!r}. Known factors: "
            f"{sorted(FACTOR_WEIGHTS)}"
        )

    return list(weights.keys())


__all__ = [
    "SCHEMA_VERSION",
    "HIGHER_IS_BETTER",
    "LOWER_IS_BETTER",
    "VALID_DIRECTIONS",
    "METRIC_SPECS",
    "FACTOR_WEIGHTS",
    "get_direction",
    "get_factor",
    "metrics_for_factor",
]

