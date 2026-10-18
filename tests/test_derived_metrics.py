from __future__ import annotations

import math

import pytest

from src.fundamentals.derived_metrics import (
    calculate_cagr,
    calculate_debt_to_cash,
    calculate_derived_metrics,
    calculate_fcf,
    calculate_margin,
    calculate_net_debt,
    calculate_ttm_from_quarters,
    calculate_ttm_from_ytd,
    calculate_yoy,
    validate_derived_metrics,
)


# ============================================================
# HELPERS
# ============================================================

def make_metric(value, status="OK"):
    return {
        "status": status,
        "value": value,
    }


def make_normalized_fixture():
    return {
        "schema_version": "sec_fundamentals_v0.1",
        "ticker": "TEST",
        "entity_name": "Test Company",
        "cik": "0000000000",
        "company_type": "NON_FINANCIAL",
        "metrics": {
            "revenue": make_metric(120000),
            "net_income": make_metric(20000),
            "operating_income": make_metric(30000),
            "cfo": make_metric(25000),
            "capex": make_metric(-5000),
            "cash": make_metric(50000),
            "total_debt": make_metric(40000),
        },
    }


# ============================================================
# YoY
# ============================================================

def test_calculate_yoy_normal():
    result = calculate_yoy(
        "revenue",
        120,
        100,
    )

    assert result["metric"] == "revenue_yoy"
    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(0.20)


def test_calculate_yoy_negative_growth():
    result = calculate_yoy(
        "revenue",
        80,
        100,
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(-0.20)


def test_calculate_yoy_missing_current():
    result = calculate_yoy(
        "revenue",
        None,
        100,
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


def test_calculate_yoy_missing_previous():
    result = calculate_yoy(
        "revenue",
        120,
        None,
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


def test_calculate_yoy_zero_previous():
    result = calculate_yoy(
        "revenue",
        120,
        0,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_yoy_bool_is_not_valid_number():
    result = calculate_yoy(
        "revenue",
        True,
        100,
    )

    assert result["status"] == "MISSING"


# ============================================================
# CAGR
# ============================================================

def test_calculate_cagr_normal():
    result = calculate_cagr(
        "revenue",
        80,
        120,
        2,
    )

    expected = (120 / 80) ** (1 / 2) - 1

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(expected)


def test_calculate_cagr_negative_start():
    result = calculate_cagr(
        "revenue",
        -100,
        200,
        2,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_cagr_zero_start():
    result = calculate_cagr(
        "revenue",
        0,
        200,
        2,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_cagr_negative_end():
    result = calculate_cagr(
        "revenue",
        100,
        -50,
        2,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_cagr_zero_years():
    result = calculate_cagr(
        "revenue",
        100,
        200,
        0,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_cagr_negative_years():
    result = calculate_cagr(
        "revenue",
        100,
        200,
        -2,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_cagr_missing_start():
    result = calculate_cagr(
        "revenue",
        None,
        200,
        2,
    )

    assert result["status"] == "MISSING"


# ============================================================
# Margin
# ============================================================

def test_calculate_operating_margin():
    result = calculate_margin(
        "operating_income",
        30,
        100,
    )

    assert result["metric"] == "operating_income_margin"
    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(0.30)


def test_calculate_net_margin():
    result = calculate_margin(
        "net_income",
        20,
        100,
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(0.20)


def test_calculate_negative_margin():
    result = calculate_margin(
        "net_income",
        -10,
        100,
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(-0.10)


def test_calculate_margin_zero_revenue():
    result = calculate_margin(
        "operating_income",
        30,
        0,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_margin_missing_numerator():
    result = calculate_margin(
        "operating_income",
        None,
        100,
    )

    assert result["status"] == "MISSING"


def test_calculate_margin_missing_revenue():
    result = calculate_margin(
        "operating_income",
        30,
        None,
    )

    assert result["status"] == "MISSING"


# ============================================================
# FCF
# ============================================================

def test_calculate_fcf():
    result = calculate_fcf(
        25000,
        -5000,
    )

    assert result["metric"] == "fcf"
    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(20000)


def test_calculate_fcf_negative_fcf():
    result = calculate_fcf(
        10000,
        -15000,
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(-5000)


def test_calculate_fcf_missing_cfo():
    result = calculate_fcf(
        None,
        -5000,
    )

    assert result["status"] == "MISSING"


def test_calculate_fcf_missing_capex():
    result = calculate_fcf(
        25000,
        None,
    )

    assert result["status"] == "MISSING"


def test_calculate_fcf_positive_capex():
    """
    2026-10-18 regression (Round AB/AC/AD): real SEC data for at
    least one issuer reports PaymentsToAcquirePropertyPlantAndEquipment
    as a positive expenditure magnitude, not a negative cash-flow
    value. CapEx must still be subtracted as an outflow regardless of
    its reported sign. Figures are the real AVGO FY2025 CFO/CapEx
    values cited in Round Y/AB (CFO=27,537,000,000,
    CapEx=623,000,000), used here purely as realistic test values --
    this test does not call out to SEC or re-fetch anything.
    """
    result = calculate_fcf(
        27_537_000_000,
        623_000_000,
    )

    assert result["metric"] == "fcf"
    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(26_914_000_000)


def test_calculate_fcf_zero_capex():
    result = calculate_fcf(
        10000,
        0,
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(10000)


def test_calculate_fcf_negative_cfo():
    result = calculate_fcf(
        -1000,
        500,
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(-1500)


def test_calculate_fcf_sign_invariant_for_same_expenditure_magnitude():
    """
    CapEx reported as a positive expenditure magnitude must produce
    the same FCF as the same magnitude reported with a negative
    sign -- the sign of the source concept must not change the
    economic result.
    """
    positive_sign_result = calculate_fcf(25000, 5000)
    negative_sign_result = calculate_fcf(25000, -5000)

    assert positive_sign_result["status"] == "OK"
    assert negative_sign_result["status"] == "OK"
    assert positive_sign_result["value"] == pytest.approx(
        negative_sign_result["value"]
    )


# ============================================================
# DEBT / CASH
# ============================================================

def test_calculate_net_debt():
    result = calculate_net_debt(
        40000,
        50000,
    )

    assert result["metric"] == "net_debt"
    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(-10000)


def test_calculate_net_debt_positive():
    result = calculate_net_debt(
        70000,
        50000,
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(20000)


def test_calculate_debt_to_cash():
    result = calculate_debt_to_cash(
        40000,
        50000,
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(0.8)


def test_calculate_debt_to_cash_zero_cash():
    result = calculate_debt_to_cash(
        40000,
        0,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


# ============================================================
# TTM — FOUR STANDALONE QUARTERS
# ============================================================

def test_calculate_ttm_from_four_quarters():
    result = calculate_ttm_from_quarters(
        "revenue",
        [25, 30, 35, 40],
    )

    assert result["metric"] == "revenue_ttm"
    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(130)


def test_calculate_ttm_requires_four_quarters():
    result = calculate_ttm_from_quarters(
        "revenue",
        [25, 30, 35],
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


def test_calculate_ttm_rejects_more_than_four_quarters():
    result = calculate_ttm_from_quarters(
        "revenue",
        [20, 25, 30, 35, 40],
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


def test_calculate_ttm_rejects_invalid_quarter():
    result = calculate_ttm_from_quarters(
        "revenue",
        [25, None, 35, 40],
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


# ============================================================
# TTM — YTD + PRIOR FY - PRIOR YTD
# ============================================================

def test_calculate_ttm_from_ytd():
    result = calculate_ttm_from_ytd(
        "revenue",
        latest_ytd_value=90,
        prior_fy_value=120,
        prior_ytd_value=85,
    )

    assert result["metric"] == "revenue_ttm"
    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(125)


def test_calculate_ttm_from_ytd_missing_input():
    result = calculate_ttm_from_ytd(
        "revenue",
        latest_ytd_value=90,
        prior_fy_value=None,
        prior_ytd_value=85,
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


# ============================================================
# COMBINED DERIVED METRICS
# ============================================================

def test_calculate_derived_metrics():
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {
                "period_end": "2024-12-31",
                "value": 100000,
            },
            {
                "period_end": "2025-12-31",
                "value": 120000,
            },
        ],
        net_income_history=[
            {
                "period_end": "2024-12-31",
                "value": 15000,
            },
            {
                "period_end": "2025-12-31",
                "value": 20000,
            },
        ],
    )

    derived = result["derived_metrics"]

    assert result["schema_version"] == "derived_metrics_v0.1"
    assert result["ticker"] == "TEST"

    assert derived["fcf"]["value"] == pytest.approx(20000)

    assert derived["net_margin"]["value"] == pytest.approx(
        20000 / 120000
    )

    assert derived["operating_margin"]["value"] == pytest.approx(
        30000 / 120000
    )

    assert derived["fcf_margin"]["value"] == pytest.approx(
        20000 / 120000
    )

    assert derived["net_debt"]["value"] == pytest.approx(
        -10000
    )

    assert derived["debt_to_cash"]["value"] == pytest.approx(
        0.8
    )

    assert derived["revenue_yoy"]["value"] == pytest.approx(
        0.20
    )

    assert derived["net_income_yoy"]["value"] == pytest.approx(
        20000 / 15000 - 1
    )


def test_calculate_derived_metrics_missing_metric():
    normalized = make_normalized_fixture()

    normalized["metrics"]["cfo"] = {
        "status": "MISSING",
        "value": None,
    }

    result = calculate_derived_metrics(normalized)

    assert result["derived_metrics"]["fcf"]["status"] == "MISSING"
    assert result["derived_metrics"]["fcf"]["value"] is None


def test_calculate_derived_metrics_requires_metrics():
    normalized = {
        "schema_version": "sec_fundamentals_v0.1",
        "ticker": "TEST",
    }

    with pytest.raises(ValueError):
        calculate_derived_metrics(normalized)


# ============================================================
# VALIDATION
# ============================================================

def test_validate_derived_metrics_valid():
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(normalized)

    failures = validate_derived_metrics(result)

    assert failures == []


def test_validate_derived_metrics_missing_top_level():
    data = {
        "schema_version": "derived_metrics_v0.1",
        "ticker": "TEST",
    }

    failures = validate_derived_metrics(data)

    assert "derived_metrics" in failures


def test_validate_derived_metrics_invalid_status():
    data = {
        "schema_version": "derived_metrics_v0.1",
        "ticker": "TEST",
        "derived_metrics": {
            "fcf": {
                "metric": "fcf",
                "status": "UNKNOWN",
                "value": 100,
            }
        },
    }

    failures = validate_derived_metrics(data)

    assert "fcf.status" in failures


def test_validate_derived_metrics_ok_requires_numeric_value():
    data = {
        "schema_version": "derived_metrics_v0.1",
        "ticker": "TEST",
        "derived_metrics": {
            "fcf": {
                "metric": "fcf",
                "status": "OK",
                "value": None,
            }
        },
    }

    failures = validate_derived_metrics(data)

    assert "fcf.value" in failures


def test_validate_derived_metrics_missing_requires_reason():
    data = {
        "schema_version": "derived_metrics_v0.1",
        "ticker": "TEST",
        "derived_metrics": {
            "fcf": {
                "metric": "fcf",
                "status": "MISSING",
                "value": None,
            }
        },
    }

    failures = validate_derived_metrics(data)

    assert "fcf.reason" in failures


# ============================================================
# EDGE CASES
# ============================================================

def test_nan_is_not_valid_number():
    result = calculate_yoy(
        "revenue",
        math.nan,
        100,
    )

    assert result["status"] == "MISSING"


def test_infinity_is_not_valid_number():
    result = calculate_yoy(
        "revenue",
        math.inf,
        100,
    )

    assert result["status"] == "MISSING"


def test_boolean_is_not_valid_number_for_margin():
    result = calculate_margin(
        "net_income",
        True,
        100,
    )

    assert result["status"] == "MISSING"


def test_history_yoy_uses_latest_two_periods():
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {
                "period_end": "2023-12-31",
                "value": 80,
            },
            {
                "period_end": "2024-12-31",
                "value": 100,
            },
            {
                "period_end": "2025-12-31",
                "value": 120,
            },
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "OK"
    assert yoy["value"] == pytest.approx(0.20)
