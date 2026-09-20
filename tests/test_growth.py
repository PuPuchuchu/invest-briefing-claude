import math

from src.fundamentals.growth import (
    GROWTH_METRICS,
    GROWTH_WEIGHTS,
    MIN_VALID_COMPONENTS,
    calculate_positive_yoy,
    calculate_standard_yoy,
    calculate_cagr_3y,
    calculate_growth,
    detect_growth_warnings,
    rate_revenue_yoy,
    rate_operating_income_yoy,
    rate_eps_yoy,
    rate_fcf_yoy,
    rate_revenue_cagr_3y,
    validate_growth,
)


# ============================================================
# HELPERS
# ============================================================

def _record(
    value,
    period_end,
    *,
    quarter=None,
    fy=None,
    period_type="quarterly",
):
    return {
        "period_type": period_type,
        "quarter": quarter,
        "fy": fy,
        "period_end": period_end,
        "value": value,
    }


def _historical():
    revenue_quarterly = [
        _record(
            100.0,
            "2025-03-31",
            quarter="Q1",
            fy=2025,
        ),
        _record(
            110.0,
            "2025-06-30",
            quarter="Q2",
            fy=2025,
        ),
        _record(
            120.0,
            "2025-09-30",
            quarter="Q3",
            fy=2025,
        ),
        _record(
            130.0,
            "2025-12-31",
            quarter="Q4",
            fy=2025,
        ),
        _record(
            120.0,
            "2026-03-31",
            quarter="Q1",
            fy=2026,
        ),
        _record(
            132.0,
            "2026-06-30",
            quarter="Q2",
            fy=2026,
        ),
    ]

    revenue_annual = [
        _record(
            400.0,
            "2023-12-31",
            fy=2023,
            period_type="annual",
        ),
        _record(
            460.0,
            "2024-12-31",
            fy=2024,
            period_type="annual",
        ),
        _record(
            529.0,
            "2025-12-31",
            fy=2025,
            period_type="annual",
        ),
        _record(
            610.0,
            "2026-12-31",
            fy=2026,
            period_type="annual",
        ),
    ]

    operating_income = [
        _record(
            20.0,
            "2025-06-30",
            quarter="Q2",
            fy=2025,
        ),
        _record(
            26.0,
            "2026-06-30",
            quarter="Q2",
            fy=2026,
        ),
    ]

    eps = [
        _record(
            1.00,
            "2025-06-30",
            quarter="Q2",
            fy=2025,
        ),
        _record(
            1.20,
            "2026-06-30",
            quarter="Q2",
            fy=2026,
        ),
    ]

    cfo = [
        _record(
            30.0,
            "2025-06-30",
            quarter="Q2",
            fy=2025,
        ),
        _record(
            40.0,
            "2026-06-30",
            quarter="Q2",
            fy=2026,
        ),
    ]

    capex = [
        _record(
            -10.0,
            "2025-06-30",
            quarter="Q2",
            fy=2025,
        ),
        _record(
            -12.0,
            "2026-06-30",
            quarter="Q2",
            fy=2026,
        ),
    ]

    return {
        "revenue": {
            "status": "OK",
            "quarterly": revenue_quarterly,
            "annual": revenue_annual,
        },
        "operating_income": {
            "status": "OK",
            "quarterly": operating_income,
        },
        "diluted_eps": {
            "status": "OK",
            "quarterly": eps,
        },
        "cfo": {
            "status": "OK",
            "quarterly": cfo,
        },
        "capex": {
            "status": "OK",
            "quarterly": capex,
        },
    }


def _normalized():
    return {
        "ticker": "TEST",
        "company_type": "NON_FINANCIAL",
        "metrics": {},
    }


def _derived():
    return {
        "schema_version": "derived_metrics_v0.1",
        "ticker": "TEST",
        "derived_metrics": {},
    }


# ============================================================
# CONFIGURATION
# ============================================================

def test_growth_configuration():
    assert set(GROWTH_METRICS) == {
        "revenue_yoy",
        "operating_income_yoy",
        "eps_yoy",
        "fcf_yoy",
        "revenue_cagr_3y",
    }

    # 2026-09-20: direct == 1.0 comparison is unsafe here -- Python's binary
    # floating-point representation makes 0.25 + 0.20 + 0.25 + 0.20 + 0.10
    # evaluate to 0.9999999999999999, not exactly 1.0, even though the
    # weights are mathematically correct. math.isclose() with a tight
    # abs_tol is the correct comparison for a sum of literal float weights.
    # This is a pure floating-point-comparison fix (ChatGPT design review,
    # 2026-09-20) -- it does NOT change or finalize the weight values
    # themselves, which remain a provisional policy pending Historical
    # Validation (see GROWTH_WEIGHTS' own module-level comment).
    assert math.isclose(sum(GROWTH_WEIGHTS.values()), 1.0, rel_tol=0.0, abs_tol=1e-12)
    assert MIN_VALID_COMPONENTS == 3


# ============================================================
# YOY CALCULATIONS
# ============================================================

def test_standard_yoy():
    result = calculate_standard_yoy(
        "revenue",
        120.0,
        100.0,
    )

    assert result["status"] == "OK"
    assert result["value"] == 0.20


def test_standard_yoy_zero_previous():
    result = calculate_standard_yoy(
        "revenue",
        120.0,
        0.0,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_positive_yoy():
    result = calculate_positive_yoy(
        "eps",
        1.20,
        1.00,
    )

    assert result["status"] == "OK"
    assert result["value"] == 0.20


def test_positive_yoy_rejects_negative_previous():
    result = calculate_positive_yoy(
        "eps",
        1.20,
        -1.00,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_positive_yoy_rejects_zero_previous():
    result = calculate_positive_yoy(
        "fcf",
        10.0,
        0.0,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_positive_yoy_rejects_negative_current():
    result = calculate_positive_yoy(
        "fcf",
        -10.0,
        20.0,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


# ============================================================
# CAGR
# ============================================================

def test_cagr_3y():
    result = calculate_cagr_3y(
        "revenue",
        100.0,
        133.1,
        3,
    )

    assert result["status"] == "OK"
    assert abs(result["value"] - 0.10) < 1e-10


def test_cagr_rejects_non_positive_start():
    result = calculate_cagr_3y(
        "revenue",
        0.0,
        100.0,
        3,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_cagr_rejects_negative_end():
    result = calculate_cagr_3y(
        "revenue",
        100.0,
        -10.0,
        3,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


# ============================================================
# STAR RATING
# ============================================================

def test_revenue_yoy_rating():
    assert rate_revenue_yoy(-0.01)["stars"] == 1
    assert rate_revenue_yoy(0.00)["stars"] == 2
    assert rate_revenue_yoy(0.05)["stars"] == 3
    assert rate_revenue_yoy(0.10)["stars"] == 4
    assert rate_revenue_yoy(0.20)["stars"] == 5


def test_operating_income_yoy_rating():
    assert rate_operating_income_yoy(-0.01)["stars"] == 1
    assert rate_operating_income_yoy(0.00)["stars"] == 2
    assert rate_operating_income_yoy(0.05)["stars"] == 3
    assert rate_operating_income_yoy(0.15)["stars"] == 4
    assert rate_operating_income_yoy(0.30)["stars"] == 5


def test_eps_yoy_rating():
    assert rate_eps_yoy(-0.01)["stars"] == 1
    assert rate_eps_yoy(0.00)["stars"] == 2
    assert rate_eps_yoy(0.05)["stars"] == 3
    assert rate_eps_yoy(0.15)["stars"] == 4
    assert rate_eps_yoy(0.30)["stars"] == 5


def test_fcf_yoy_rating():
    assert rate_fcf_yoy(-0.01)["stars"] == 1
    assert rate_fcf_yoy(0.00)["stars"] == 2
    assert rate_fcf_yoy(0.05)["stars"] == 3
    assert rate_fcf_yoy(0.15)["stars"] == 4
    assert rate_fcf_yoy(0.30)["stars"] == 5


def test_revenue_cagr_rating():
    assert rate_revenue_cagr_3y(-0.01)["stars"] == 1
    assert rate_revenue_cagr_3y(0.00)["stars"] == 2
    assert rate_revenue_cagr_3y(0.05)["stars"] == 3
    assert rate_revenue_cagr_3y(0.10)["stars"] == 4
    assert rate_revenue_cagr_3y(0.20)["stars"] == 5


# ============================================================
# FULL GROWTH CALCULATION
# ============================================================

def test_calculate_growth():
    result = calculate_growth(
        _normalized(),
        _derived(),
        _historical(),
    )

    assert result["schema_version"] == "growth_v0.1"
    assert result["ticker"] == "TEST"

    assert "growth" in result
    assert "components" in result
    assert "warnings" in result
    assert "limitations" in result


def test_growth_components():
    result = calculate_growth(
        _normalized(),
        _derived(),
        _historical(),
    )

    components = result["components"]

    assert set(components.keys()) == set(
        GROWTH_METRICS
    )


def test_revenue_quarterly_yoy():
    result = calculate_growth(
        _normalized(),
        _derived(),
        _historical(),
    )

    component = result["components"]["revenue_yoy"]

    # 132 / 110 - 1 = 20%
    assert component["status"] == "OK"
    assert abs(component["value"] - 0.20) < 1e-10
    assert component["stars"] == 5


def test_operating_income_quarterly_yoy():
    result = calculate_growth(
        _normalized(),
        _derived(),
        _historical(),
    )

    component = result["components"][
        "operating_income_yoy"
    ]

    # 26 / 20 - 1 = 30%
    assert component["status"] == "OK"
    assert abs(component["value"] - 0.30) < 1e-10
    assert component["stars"] == 5


def test_eps_quarterly_yoy():
    result = calculate_growth(
        _normalized(),
        _derived(),
        _historical(),
    )

    component = result["components"]["eps_yoy"]

    # 1.20 / 1.00 - 1 = 20%
    assert component["status"] == "OK"
    assert abs(component["value"] - 0.20) < 1e-10
    assert component["stars"] == 4


def test_fcf_quarterly_yoy():
    result = calculate_growth(
        _normalized(),
        _derived(),
        _historical(),
    )

    component = result["components"]["fcf_yoy"]

    # 2025 FCF = 30 - 10 = 20
    # 2026 FCF = 40 - 12 = 28
    # 28 / 20 - 1 = 40%
    assert component["status"] == "OK"
    assert abs(component["value"] - 0.40) < 1e-10
    assert component["stars"] == 5


def test_revenue_cagr_3y():
    result = calculate_growth(
        _normalized(),
        _derived(),
        _historical(),
    )

    component = result["components"][
        "revenue_cagr_3y"
    ]

    # 610 / 400 over 3 years
    expected = (610.0 / 400.0) ** (1.0 / 3.0) - 1.0

    assert component["status"] == "OK"
    assert abs(component["value"] - expected) < 1e-10


# ============================================================
# FCF SPECIAL CASES
# ============================================================

def test_fcf_negative_prior_period_is_not_converted_to_fake_growth():
    history = _historical()

    history["cfo"]["quarterly"] = [
        _record(
            5.0,
            "2025-06-30",
            quarter="Q2",
            fy=2025,
        ),
        _record(
            20.0,
            "2026-06-30",
            quarter="Q2",
            fy=2026,
        ),
    ]

    history["capex"]["quarterly"] = [
        _record(
            -10.0,
            "2025-06-30",
            quarter="Q2",
            fy=2025,
        ),
        _record(
            -5.0,
            "2026-06-30",
            quarter="Q2",
            fy=2026,
        ),
    ]

    result = calculate_growth(
        _normalized(),
        _derived(),
        history,
    )

    component = result["components"]["fcf_yoy"]

    # Prior FCF = -5
    assert component["status"] == "INVALID"
    assert component["value"] is None


# ============================================================
# EPS SPECIAL CASES
# ============================================================

def test_eps_negative_prior_period_is_not_converted_to_fake_growth():
    history = _historical()

    history["diluted_eps"]["quarterly"] = [
        _record(
            -1.00,
            "2025-06-30",
            quarter="Q2",
            fy=2025,
        ),
        _record(
            1.00,
            "2026-06-30",
            quarter="Q2",
            fy=2026,
        ),
    ]

    result = calculate_growth(
        _normalized(),
        _derived(),
        history,
    )

    component = result["components"]["eps_yoy"]

    assert component["status"] == "INVALID"
    assert component["value"] is None


# ============================================================
# MISSING DATA
# ============================================================

def test_missing_historical_data():
    result = calculate_growth(
        _normalized(),
        _derived(),
        {},
    )

    assert result["growth"]["status"] == "MISSING"

    for metric in GROWTH_METRICS:
        assert (
            result["components"][metric]["status"]
            in {"MISSING", "INVALID"}
        )


def test_minimum_three_valid_components():
    history = _historical()

    history["diluted_eps"] = {
        "status": "MISSING",
        "quarterly": [],
    }

    history["cfo"] = {
        "status": "MISSING",
        "quarterly": [],
    }

    history["capex"] = {
        "status": "MISSING",
        "quarterly": [],
    }

    result = calculate_growth(
        _normalized(),
        _derived(),
        history,
    )

    assert result["growth"]["status"] == "OK"
    assert (
        result["growth"]["available_components"]
        >= 3
    )


# ============================================================
# WARNING TESTS
# ============================================================

def test_revenue_operating_income_divergence_warning():
    components = {
        "revenue_yoy": {
            "status": "OK",
            "value": 0.20,
        },
        "operating_income_yoy": {
            "status": "OK",
            "value": -0.10,
        },
        "eps_yoy": {
            "status": "MISSING",
            "value": None,
        },
        "fcf_yoy": {
            "status": "MISSING",
            "value": None,
        },
    }

    warnings = detect_growth_warnings(
        components
    )

    assert (
        "revenue_operating_income_divergence"
        in warnings
    )


def test_revenue_fcf_divergence_warning():
    components = {
        "revenue_yoy": {
            "status": "OK",
            "value": 0.20,
        },
        "operating_income_yoy": {
            "status": "OK",
            "value": 0.10,
        },
        "eps_yoy": {
            "status": "OK",
            "value": 0.10,
        },
        "fcf_yoy": {
            "status": "OK",
            "value": -0.10,
        },
    }

    warnings = detect_growth_warnings(
        components
    )

    assert (
        "revenue_fcf_divergence"
        in warnings
    )


def test_eps_revenue_divergence_warning():
    components = {
        "revenue_yoy": {
            "status": "OK",
            "value": 0.02,
        },
        "operating_income_yoy": {
            "status": "OK",
            "value": 0.10,
        },
        "eps_yoy": {
            "status": "OK",
            "value": 0.40,
        },
        "fcf_yoy": {
            "status": "OK",
            "value": 0.10,
        },
    }

    warnings = detect_growth_warnings(
        components
    )

    assert (
        "eps_revenue_divergence"
        in warnings
    )


# ============================================================
# VALIDATION
# ============================================================

def test_validate_growth_valid_result():
    result = calculate_growth(
        _normalized(),
        _derived(),
        _historical(),
    )

    failures = validate_growth(result)

    assert failures == []


def test_validate_growth_rejects_invalid_score():
    result = calculate_growth(
        _normalized(),
        _derived(),
        _historical(),
    )

    result["growth"]["score"] = "invalid"

    failures = validate_growth(result)

    assert "growth.score" in failures


def test_validate_growth_rejects_invalid_status():
    result = calculate_growth(
        _normalized(),
        _derived(),
        _historical(),
    )

    result["growth"]["status"] = "UNKNOWN"

    failures = validate_growth(result)

    assert "growth.status" in failures
