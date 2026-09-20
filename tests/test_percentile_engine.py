"""
Fixture-based regression tests for src/fundamentals/percentile_engine.py
(Framework v2.0 Step 10A, 2026-09-20 ChatGPT design review).

Per the design review, the Percentile Engine's structure and correctness
must be verifiable with synthetic fixtures alone -- it does not need a
populated Core Market Universe to be tested. Zero network dependency,
zero live SEC data.
"""

from __future__ import annotations

import pytest

from src.fundamentals.percentile_engine import (
    FACTOR_STATUS_INSUFFICIENT,
    FACTOR_STATUS_LIMITED,
    FACTOR_STATUS_OK,
    METRIC_STATUS_MISSING,
    METRIC_STATUS_NO_ELIGIBLE_PEERS,
    METRIC_STATUS_OK,
    calculate_factor_score,
    calculate_percentile,
    score_metric_percentile,
)
from src.fundamentals.metric_specs import (
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    FACTOR_WEIGHTS,
)
from src.fundamentals.peer_classification import classify_peer_adequacy


# ============================================================
# calculate_percentile -- mid-rank math
# ============================================================

def test_percentile_higher_is_better_best_in_group():
    # Target beats all 4 peers -> 100th percentile.
    result = calculate_percentile(50.0, [10.0, 20.0, 30.0, 40.0], HIGHER_IS_BETTER)
    assert result == 100.0


def test_percentile_higher_is_better_worst_in_group():
    # Target is beaten by all 4 peers -> 0th percentile.
    result = calculate_percentile(5.0, [10.0, 20.0, 30.0, 40.0], HIGHER_IS_BETTER)
    assert result == 0.0


def test_percentile_higher_is_better_mid_rank_with_ties():
    # peers = [50, 50, 10, 20]; target = 50.
    # worse (peers < 50) = 2 (10, 20); tied (peers == 50) = 2.
    # percentile = 100 * (2 + 0.5*2) / 4 = 75.0
    result = calculate_percentile(50.0, [50.0, 50.0, 10.0, 20.0], HIGHER_IS_BETTER)
    assert result == 75.0


def test_percentile_lower_is_better_best_in_group():
    # Target is the smallest (best under lower_is_better) -> 100th percentile.
    result = calculate_percentile(1.0, [2.0, 3.0, 4.0, 5.0], LOWER_IS_BETTER)
    assert result == 100.0


def test_percentile_lower_is_better_mid_rank():
    # target=1.0 (e.g. net_debt_to_fcf), peers=[2.0, 3.0, 0.5, 4.0]
    # worse (peers > target) = 3 (2.0, 3.0, 4.0); tied = 0.
    # percentile = 100 * 3 / 4 = 75.0
    result = calculate_percentile(1.0, [2.0, 3.0, 0.5, 4.0], LOWER_IS_BETTER)
    assert result == 75.0


def test_percentile_all_peers_tied_with_target():
    # target ties every peer -> percentile = 50.0 exactly, regardless of direction.
    assert calculate_percentile(10.0, [10.0, 10.0, 10.0], HIGHER_IS_BETTER) == 50.0
    assert calculate_percentile(10.0, [10.0, 10.0, 10.0], LOWER_IS_BETTER) == 50.0


def test_percentile_single_peer():
    assert calculate_percentile(10.0, [5.0], HIGHER_IS_BETTER) == 100.0
    assert calculate_percentile(10.0, [15.0], HIGHER_IS_BETTER) == 0.0


def test_percentile_rejects_unknown_direction():
    with pytest.raises(ValueError):
        calculate_percentile(10.0, [5.0], "sideways")


def test_percentile_rejects_empty_peer_list():
    with pytest.raises(ValueError):
        calculate_percentile(10.0, [], HIGHER_IS_BETTER)


# ============================================================
# score_metric_percentile -- metric-level eligibility & scoring
# ============================================================

def test_score_metric_percentile_ok_case():
    result = score_metric_percentile(
        ticker="NVDA",
        evaluation_date="2026-09-20",
        peer_group="AI & Compute Semiconductors",
        metric_name="revenue_yoy",
        raw_value=0.50,
        peer_raw_values={"AMD": 0.20, "AVGO": 0.15, "QCOM": 0.10},
        direction=HIGHER_IS_BETTER,
    )
    assert result["metric_status"] == METRIC_STATUS_OK
    assert result["eligible_peer_count"] == 3
    assert result["percentile"] == 100.0
    assert result["raw_value"] == 0.50
    assert result["exclusion_reason"] is None
    assert result["ticker"] == "NVDA"
    assert result["peer_group"] == "AI & Compute Semiconductors"
    assert result["direction"] == HIGHER_IS_BETTER


def test_score_metric_percentile_target_missing_never_zero_filled():
    result = score_metric_percentile(
        ticker="JPM",
        evaluation_date="2026-09-20",
        peer_group="Diversified / Money Center Banks",
        metric_name="operating_income_yoy",
        raw_value=None,  # e.g. OperatingIncomeLoss concept 404s for JPM
        peer_raw_values={"BAC": 0.10, "WFC": 0.05},
        direction=HIGHER_IS_BETTER,
    )
    assert result["metric_status"] == METRIC_STATUS_MISSING
    assert result["raw_value"] is None
    assert result["percentile"] is None
    assert result["eligible_peer_count"] is None
    assert result["exclusion_reason"] is not None


def test_score_metric_percentile_no_eligible_peers_never_zero_filled():
    result = score_metric_percentile(
        ticker="CRWV",
        evaluation_date="2026-09-20",
        peer_group="AI Cloud Infrastructure",
        metric_name="fcf_yoy",
        raw_value=0.30,
        peer_raw_values={},  # singleton peer group -- no comparison peers at all
        direction=HIGHER_IS_BETTER,
    )
    assert result["metric_status"] == METRIC_STATUS_NO_ELIGIBLE_PEERS
    assert result["eligible_peer_count"] == 0
    assert result["percentile"] is None
    assert result["peer_adequacy_status"] == "INSUFFICIENT_PEERS"
    assert result["raw_value"] == 0.30  # target's own value is still reported


def test_score_metric_percentile_excludes_invalid_peer_values_from_count():
    # peer_raw_values carries missing/invalid peers explicitly (None) rather
    # than having them pre-filtered upstream -- eligible_peer_count must
    # reflect only the genuinely numeric ones.
    result = score_metric_percentile(
        ticker="MSFT",
        evaluation_date="2026-09-20",
        peer_group="Enterprise Software & Cloud Platforms",
        metric_name="fcf_yoy",
        raw_value=0.12,
        peer_raw_values={"ORCL": 0.08, "SAP": None, "CRM": None, "ADBE": 0.05},
        direction=HIGHER_IS_BETTER,
    )
    assert result["metric_status"] == METRIC_STATUS_OK
    assert result["eligible_peer_count"] == 2  # only ORCL and ADBE are numeric


def test_score_metric_percentile_matches_chatgpt_worked_example():
    # 2026-09-20 design review, item 3 worked example: a 12-company group
    # (target + 11 peers) where Revenue has 10 valid peer values (->
    # SUFFICIENT_PEERS, 8-14) and FCF has 7 valid peer values (->
    # LIMITED_PEERS, 5-7). Verified here against the actual
    # classify_peer_adequacy() thresholds, not re-implemented locally.
    revenue_peers = {f"peer_{i}": float(i) for i in range(10)}  # 10 valid
    revenue_peers["peer_missing"] = None  # 11th peer, no valid revenue value

    fcf_peers = {f"peer_{i}": float(i) for i in range(7)}  # 7 valid
    for i in range(7, 11):
        fcf_peers[f"peer_{i}"] = None  # 4 more peers, no valid FCF value

    revenue_result = score_metric_percentile(
        ticker="TARGET",
        evaluation_date="2026-09-20",
        peer_group="Test Group",
        metric_name="revenue_yoy",
        raw_value=100.0,
        peer_raw_values=revenue_peers,
        direction=HIGHER_IS_BETTER,
    )
    fcf_result = score_metric_percentile(
        ticker="TARGET",
        evaluation_date="2026-09-20",
        peer_group="Test Group",
        metric_name="fcf_yoy",
        raw_value=100.0,
        peer_raw_values=fcf_peers,
        direction=HIGHER_IS_BETTER,
    )

    assert revenue_result["eligible_peer_count"] == 10
    assert revenue_result["peer_adequacy_status"] == "SUFFICIENT_PEERS"
    assert classify_peer_adequacy(10) == "SUFFICIENT_PEERS"

    assert fcf_result["eligible_peer_count"] == 7
    assert fcf_result["peer_adequacy_status"] == "LIMITED_PEERS"
    assert classify_peer_adequacy(7) == "LIMITED_PEERS"


def test_score_metric_percentile_rejects_unknown_direction():
    with pytest.raises(ValueError):
        score_metric_percentile(
            ticker="X",
            evaluation_date="2026-09-20",
            peer_group="G",
            metric_name="m",
            raw_value=1.0,
            peer_raw_values={"Y": 2.0},
            direction="sideways",
        )


# ============================================================
# calculate_factor_score -- Factor-level aggregation
# ============================================================

def _ok_metric_result(metric_name, percentile, eligible_peer_count=10):
    return {
        "metric_name": metric_name,
        "metric_status": METRIC_STATUS_OK,
        "percentile": percentile,
        "eligible_peer_count": eligible_peer_count,
    }


def _missing_metric_result(metric_name):
    return {
        "metric_name": metric_name,
        "metric_status": METRIC_STATUS_MISSING,
        "percentile": None,
        "eligible_peer_count": None,
    }


def test_factor_score_full_coverage_growth_weights():
    weights = FACTOR_WEIGHTS["growth"]  # revenue_yoy .25, operating_income_yoy .20,
    # eps_yoy .25, fcf_yoy .20, revenue_cagr_3y .10
    metric_results = [
        _ok_metric_result("revenue_yoy", 90.0),
        _ok_metric_result("operating_income_yoy", 80.0),
        _ok_metric_result("eps_yoy", 70.0),
        _ok_metric_result("fcf_yoy", 60.0),
        _ok_metric_result("revenue_cagr_3y", 50.0),
    ]
    result = calculate_factor_score(metric_results, weights)

    assert result["factor_status"] == FACTOR_STATUS_OK
    assert result["available_weight"] == 1.0
    assert result["contributing_metrics"] == 5
    assert result["total_metrics"] == 5

    expected = (
        90.0 * 0.25 + 80.0 * 0.20 + 70.0 * 0.25 + 60.0 * 0.20 + 50.0 * 0.10
    )
    assert result["percentile_score"] == round(expected, 4)


def test_factor_score_renormalizes_above_coverage_threshold():
    # Missing only revenue_cagr_3y (weight 0.10) -> available_weight = 0.90,
    # which is >= the 0.80 threshold, so it renormalizes and still scores.
    weights = FACTOR_WEIGHTS["growth"]
    metric_results = [
        _ok_metric_result("revenue_yoy", 90.0),
        _ok_metric_result("operating_income_yoy", 80.0),
        _ok_metric_result("eps_yoy", 70.0),
        _ok_metric_result("fcf_yoy", 60.0),
        _missing_metric_result("revenue_cagr_3y"),
    ]
    result = calculate_factor_score(metric_results, weights)

    assert result["factor_status"] == FACTOR_STATUS_OK
    assert result["available_weight"] == 0.90
    assert result["contributing_metrics"] == 4
    assert result["percentile_score"] is not None

    weighted_sum = 90.0 * 0.25 + 80.0 * 0.20 + 70.0 * 0.25 + 60.0 * 0.20
    expected = weighted_sum / 0.90
    assert result["percentile_score"] == round(expected, 4)


def test_factor_score_below_coverage_threshold_returns_limited_no_score():
    # Only revenue_yoy (0.25) + operating_income_yoy (0.20) available = 0.45,
    # below the 0.80 threshold -> LIMITED, percentile_score withheld.
    weights = FACTOR_WEIGHTS["growth"]
    metric_results = [
        _ok_metric_result("revenue_yoy", 90.0),
        _ok_metric_result("operating_income_yoy", 80.0),
        _missing_metric_result("eps_yoy"),
        _missing_metric_result("fcf_yoy"),
        _missing_metric_result("revenue_cagr_3y"),
    ]
    result = calculate_factor_score(metric_results, weights)

    assert result["factor_status"] == FACTOR_STATUS_LIMITED
    assert result["percentile_score"] is None
    assert result["available_weight"] == 0.45


def test_factor_score_zero_coverage_returns_insufficient():
    weights = FACTOR_WEIGHTS["growth"]
    metric_results = [
        _missing_metric_result(name) for name in weights
    ]
    result = calculate_factor_score(metric_results, weights)

    assert result["factor_status"] == FACTOR_STATUS_INSUFFICIENT
    assert result["percentile_score"] is None
    assert result["available_weight"] == 0.0
    assert result["contributing_metrics"] == 0


def test_factor_score_empty_metric_results_returns_insufficient():
    weights = FACTOR_WEIGHTS["growth"]
    result = calculate_factor_score([], weights)

    assert result["factor_status"] == FACTOR_STATUS_INSUFFICIENT
    assert result["percentile_score"] is None


def test_factor_score_peer_adequacy_is_weakest_contributing_metric():
    # revenue_yoy has 12 eligible peers (SUFFICIENT), fcf_yoy has only 6
    # (LIMITED) -- the factor-level peer_adequacy_status must reflect the
    # weaker of the two, not the stronger.
    weights = FACTOR_WEIGHTS["growth"]
    metric_results = [
        _ok_metric_result("revenue_yoy", 90.0, eligible_peer_count=12),
        _ok_metric_result("operating_income_yoy", 80.0, eligible_peer_count=12),
        _ok_metric_result("eps_yoy", 70.0, eligible_peer_count=12),
        _ok_metric_result("fcf_yoy", 60.0, eligible_peer_count=6),
        _ok_metric_result("revenue_cagr_3y", 50.0, eligible_peer_count=12),
    ]
    result = calculate_factor_score(metric_results, weights)

    assert result["peer_adequacy_status"] == classify_peer_adequacy(6)
    assert result["peer_adequacy_status"] == "LIMITED_PEERS"


def test_factor_score_ignores_metrics_outside_the_weight_scheme():
    # A metric_result for a metric not in `weights` must be silently
    # ignored, not raise or corrupt the aggregation -- this lets a caller
    # pass one combined list of metric_results across both Factors.
    weights = FACTOR_WEIGHTS["growth"]
    metric_results = [
        _ok_metric_result("revenue_yoy", 90.0),
        _ok_metric_result("operating_income_yoy", 80.0),
        _ok_metric_result("eps_yoy", 70.0),
        _ok_metric_result("fcf_yoy", 60.0),
        _ok_metric_result("revenue_cagr_3y", 50.0),
        _ok_metric_result("operating_margin", 40.0),  # belongs to "quality", not "growth"
    ]
    result = calculate_factor_score(metric_results, weights)

    assert result["contributing_metrics"] == 5
    assert result["total_metrics"] == 5


def test_factor_score_quality_weights_full_coverage():
    weights = FACTOR_WEIGHTS["quality"]
    metric_results = [
        _ok_metric_result("operating_margin", 80.0),
        _ok_metric_result("net_margin", 70.0),
        _ok_metric_result("fcf_margin", 60.0),
        _ok_metric_result("fcf_conversion", 90.0),
        _ok_metric_result("net_debt_to_fcf", 50.0),
        _ok_metric_result("debt_to_cash", 40.0),
        _ok_metric_result("profit_to_cash_consistency", 30.0),
    ]
    result = calculate_factor_score(metric_results, weights)

    assert result["factor_status"] == FACTOR_STATUS_OK
    assert result["available_weight"] == 1.0
    assert result["total_metrics"] == 7

    expected = (
        80.0 * 0.20 + 70.0 * 0.10 + 60.0 * 0.20 + 90.0 * 0.20
        + 50.0 * 0.15 + 40.0 * 0.05 + 30.0 * 0.10
    )
    assert result["percentile_score"] == round(expected, 4)


# ============================================================
# Composite Score is explicitly NOT implemented this round
# ============================================================

def test_composite_score_not_implemented():
    import src.fundamentals.percentile_engine as engine_module

    assert not hasattr(engine_module, "calculate_composite_score"), (
        "Composite Score must not be implemented before the 3-gate "
        "(Data Integrity / Statistical Robustness / Historical "
        "Validation) spec is satisfied -- see 2026-09-20 design review, "
        "item 10."
    )

