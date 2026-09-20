"""
Percentile Engine (Framework v2.0 Step 10A, 2026-09-20 ChatGPT design
review).

Scope of this module, per the 2026-09-20 design review:

    - mid-rank percentile calculation of one company's raw metric value
      against its Primary Peer Group's comparison set
      (src/fundamentals/peer_classification.py's `build_comparison_peers`
      is the caller-side source of that comparison set; this module does
      not fetch or resolve peers itself)
    - metric-level eligibility (a peer only counts if it has a valid
      value for THAT metric -- never a raw headcount of the peer group)
    - Factor-level weighted aggregation of per-metric percentiles, with
      missing/invalid coverage tracked and a provisional coverage
      threshold gating whether a production Factor percentile score is
      returned at all

Explicitly OUT of scope for this module (per the same design review):

    - Composite Score (any cross-Factor aggregation) -- not implemented,
      not even a stub. See the module-level docstring convention used
      elsewhere in this codebase: absence of a `calculate_composite_score`
      function IS the enforcement mechanism (see
      tests/test_percentile_engine.py::test_composite_score_not_implemented).
    - Wiring this engine's inputs directly out of calculate_quality() /
      calculate_growth()'s live output, or out of real Core Market
      Universe peer data -- this module is exercised by fixtures only in
      this round (10A). Production activation (10C) is a later step,
      gated on Core Market Universe expansion (10B) actually producing
      enough real peers per metric.

Standing project rule enforced throughout: a missing, invalid, or
not-applicable value is NEVER treated as 0. It is excluded from both the
percentile population and the Factor weight denominator.
"""

from __future__ import annotations

from typing import Any

from src.fundamentals.metric_specs import VALID_DIRECTIONS, HIGHER_IS_BETTER
from src.fundamentals.peer_classification import classify_peer_adequacy


# ============================================================
# CONFIGURATION
# ============================================================

SCHEMA_VERSION = "percentile_engine_v0.1"

# 2026-09-20 ChatGPT design review, item 5: this is a PROVISIONAL POLICY
# value, not a statistically derived cutoff -- it is itself a subject of
# future Historical Validation (see the Composite Score 3-gate spec),
# same as GROWTH_WEIGHTS' own provisional status. Do not treat this as
# finalized.
FACTOR_COVERAGE_RENORMALIZE_THRESHOLD = 0.80

METRIC_STATUS_OK = "OK"
METRIC_STATUS_MISSING = "MISSING"
METRIC_STATUS_NO_ELIGIBLE_PEERS = "NO_ELIGIBLE_PEERS"

VALID_METRIC_STATUSES = frozenset(
    {
        METRIC_STATUS_OK,
        METRIC_STATUS_MISSING,
        METRIC_STATUS_NO_ELIGIBLE_PEERS,
    }
)

FACTOR_STATUS_OK = "OK"
FACTOR_STATUS_LIMITED = "LIMITED"
FACTOR_STATUS_INSUFFICIENT = "INSUFFICIENT"

VALID_FACTOR_STATUSES = frozenset(
    {
        FACTOR_STATUS_OK,
        FACTOR_STATUS_LIMITED,
        FACTOR_STATUS_INSUFFICIENT,
    }
)


# ============================================================
# HELPERS
# ============================================================

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ============================================================
# MID-RANK PERCENTILE
# ============================================================

def calculate_percentile(
    target_value: float,
    peer_values: list[float],
    direction: str,
) -> float:
    """
    Mid-rank percentile of `target_value` within `peer_values`.

        percentile = 100 * (count(worse) + 0.5 * count(tied)) / N

    "worse" depends on `direction`:
        higher_is_better -> peer values STRICTLY LESS than target
        lower_is_better  -> peer values STRICTLY GREATER than target

    A higher returned percentile always means "target ranks better than
    a larger share of its peers," for EITHER direction -- callers never
    need to invert the result themselves.

    `peer_values` must already be the eligible, self-excluded comparison
    set (numeric values only) -- this function does no filtering and
    raises on an empty list rather than silently returning a degenerate
    percentile.
    """
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"Unknown direction: {direction!r}")

    if not peer_values:
        raise ValueError(
            "calculate_percentile() requires at least one eligible peer "
            "value. Callers must check eligibility (e.g. via "
            "score_metric_percentile()) before calling this directly."
        )

    if direction == HIGHER_IS_BETTER:
        worse = sum(1 for value in peer_values if value < target_value)
    else:
        worse = sum(1 for value in peer_values if value > target_value)

    tied = sum(1 for value in peer_values if value == target_value)

    n = len(peer_values)

    return 100.0 * (worse + 0.5 * tied) / n


# ============================================================
# METRIC-LEVEL PERCENTILE SCORING
# ============================================================

def score_metric_percentile(
    *,
    ticker: str,
    evaluation_date: str,
    peer_group: str | None,
    metric_name: str,
    raw_value: Any,
    peer_raw_values: dict[str, Any],
    direction: str,
    security: str | None = None,
) -> dict[str, Any]:
    """
    Score one metric's percentile for one company against its
    comparison peer set.

    peer_raw_values: {peer_identifier: raw_value_or_None, ...} -- the
    FULL comparison set (already self-excluded by the caller, e.g. via
    peer_classification.build_comparison_peers()), including peers whose
    value for THIS metric is missing/invalid/None. Passing them in
    explicitly (rather than pre-filtering upstream) keeps the exclusion
    auditable: eligible_peer_count here is always a real count of
    peer_raw_values entries that had a genuine numeric value, never an
    assumption.

    Returns a dict with (at minimum) the fields required by the
    2026-09-20 design review, item 6:
        ticker, security, evaluation_date, peer_group, metric_name,
        raw_value, eligible_peer_count, percentile, direction,
        metric_status, exclusion_reason
    plus peer_adequacy_status (the classify_peer_adequacy() tier for
    this metric's eligible peer count -- informational; production
    gating happens at the Factor level, see calculate_factor_score()).

    Never substitutes 0 for a missing/invalid raw_value or peer value.
    """
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"Unknown direction: {direction!r}")

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "security": security,
        "evaluation_date": evaluation_date,
        "peer_group": peer_group,
        "metric_name": metric_name,
        "raw_value": raw_value if _is_number(raw_value) else None,
        "direction": direction,
        "eligible_peer_count": None,
        "percentile": None,
        "peer_adequacy_status": None,
        "metric_status": None,
        "exclusion_reason": None,
    }

    if not _is_number(raw_value):
        result["metric_status"] = METRIC_STATUS_MISSING
        result["exclusion_reason"] = (
            f"Target's own {metric_name!r} value is missing, invalid, "
            f"or non-numeric."
        )
        return result

    eligible_values = [
        value for value in peer_raw_values.values() if _is_number(value)
    ]
    eligible_peer_count = len(eligible_values)

    result["eligible_peer_count"] = eligible_peer_count
    result["peer_adequacy_status"] = classify_peer_adequacy(
        eligible_peer_count
    )

    if eligible_peer_count == 0:
        result["metric_status"] = METRIC_STATUS_NO_ELIGIBLE_PEERS
        result["exclusion_reason"] = (
            f"No peer in the comparison set has a valid {metric_name!r} "
            f"value; percentile cannot be computed."
        )
        return result

    result["percentile"] = calculate_percentile(
        float(raw_value),
        eligible_values,
        direction,
    )
    result["metric_status"] = METRIC_STATUS_OK

    return result


# ============================================================
# FACTOR-LEVEL AGGREGATION
# ============================================================

def calculate_factor_score(
    metric_results: list[dict[str, Any]],
    weights: dict[str, float],
    *,
    coverage_threshold: float = FACTOR_COVERAGE_RENORMALIZE_THRESHOLD,
) -> dict[str, Any]:
    """
    Aggregate per-metric percentile scores (score_metric_percentile()
    outputs) into one Factor-level weighted percentile score, using
    that Factor's already-approved absolute-score weights (e.g.
    metric_specs.FACTOR_WEIGHTS["growth"] == growth.GROWTH_WEIGHTS).

    Only metrics with metric_status == "OK" contribute to the weighted
    sum and to `available_weight` -- MISSING / NO_ELIGIBLE_PEERS metrics
    are excluded from both the numerator and denominator, never treated
    as 0 (standing project rule).

    Coverage policy (2026-09-20 design review, item 5 -- PROVISIONAL,
    pending Historical Validation, same status as GROWTH_WEIGHTS):

        available_weight >= coverage_threshold (default 0.80)
            -> renormalize the contributing weights to sum to 1.0 and
               return factor_status="OK" with a percentile_score.
        0 < available_weight < coverage_threshold
            -> factor_status="LIMITED", percentile_score=None.
        available_weight == 0
            -> factor_status="INSUFFICIENT", percentile_score=None.

    factor-level peer_adequacy_status is the classify_peer_adequacy()
    tier of the SMALLEST eligible_peer_count among contributing metrics
    -- a Factor score is only as reliable as its thinnest-covered
    component.
    """
    weighted_sum = 0.0
    available_weight = 0.0
    contributing_metrics = 0
    eligible_peer_counts: list[int] = []

    for metric_result in metric_results:
        metric_name = metric_result.get("metric_name")
        weight = weights.get(metric_name)

        if weight is None:
            # Not part of this Factor's weight scheme -- ignore rather
            # than error, so callers may pass a mixed list of metric
            # results and let each calculate_factor_score() call pick
            # out only the metrics relevant to its own Factor.
            continue

        if metric_result.get("metric_status") != METRIC_STATUS_OK:
            continue

        percentile = metric_result.get("percentile")

        if not _is_number(percentile):
            continue

        weighted_sum += percentile * weight
        available_weight += weight
        contributing_metrics += 1

        peer_count = metric_result.get("eligible_peer_count")

        if _is_number(peer_count):
            eligible_peer_counts.append(int(peer_count))

    total_metrics = len(weights)

    if eligible_peer_counts:
        factor_peer_adequacy_status = classify_peer_adequacy(
            min(eligible_peer_counts)
        )
    else:
        factor_peer_adequacy_status = classify_peer_adequacy(0)

    base_result = {
        "schema_version": SCHEMA_VERSION,
        "available_weight": round(available_weight, 4),
        "contributing_metrics": contributing_metrics,
        "total_metrics": total_metrics,
        "peer_adequacy_status": factor_peer_adequacy_status,
    }

    if contributing_metrics == 0:
        return {
            **base_result,
            "factor_status": FACTOR_STATUS_INSUFFICIENT,
            "percentile_score": None,
            "reason": "No metrics produced a valid percentile.",
        }

    if available_weight < coverage_threshold:
        return {
            **base_result,
            "factor_status": FACTOR_STATUS_LIMITED,
            "percentile_score": None,
            "reason": (
                f"Available weight coverage "
                f"{available_weight:.2%} is below the "
                f"{coverage_threshold:.0%} production threshold "
                f"(provisional policy value, pending Historical "
                f"Validation)."
            ),
        }

    renormalized_score = weighted_sum / available_weight

    return {
        **base_result,
        "factor_status": FACTOR_STATUS_OK,
        "percentile_score": round(renormalized_score, 4),
        "reason": None,
    }


__all__ = [
    "SCHEMA_VERSION",
    "FACTOR_COVERAGE_RENORMALIZE_THRESHOLD",
    "METRIC_STATUS_OK",
    "METRIC_STATUS_MISSING",
    "METRIC_STATUS_NO_ELIGIBLE_PEERS",
    "VALID_METRIC_STATUSES",
    "FACTOR_STATUS_OK",
    "FACTOR_STATUS_LIMITED",
    "FACTOR_STATUS_INSUFFICIENT",
    "VALID_FACTOR_STATUSES",
    "calculate_percentile",
    "score_metric_percentile",
    "calculate_factor_score",
]

