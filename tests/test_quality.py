from src.fundamentals.quality import (
    QUALITY_METRICS,
    calculate_fcf_conversion,
    calculate_net_debt_to_fcf,
    calculate_profit_to_cash_consistency,
    calculate_quality,
    rate_operating_margin,
    rate_net_margin,
    rate_fcf_margin,
    rate_fcf_conversion,
    rate_net_debt_to_fcf,
    rate_debt_to_cash,
    rate_profit_to_cash_consistency,
    validate_quality,
)


# ============================================================
# HELPERS
# ============================================================

def _ok_metric(value):
    return {
        "status": "OK",
        "value": value,
    }


def _missing_metric():
    return {
        "status": "MISSING",
        "value": None,
    }


def _build_derived(
    *,
    operating_margin=0.20,
    net_margin=0.15,
    fcf_margin=0.18,
    fcf=18.0,
    net_debt=0.0,
    debt_to_cash=0.0,
):
    return {
        "schema_version": "derived_metrics_v0.1",
        "ticker": "TEST",
        "derived_metrics": {
            "operating_margin": _ok_metric(
                operating_margin
            ),
            "net_margin": _ok_metric(
                net_margin
            ),
            "fcf_margin": _ok_metric(
                fcf_margin
            ),
            "fcf": _ok_metric(
                fcf
            ),
            "net_debt": _ok_metric(
                net_debt
            ),
            "debt_to_cash": _ok_metric(
                debt_to_cash
            ),
        },
    }


def _build_normalized(
    *,
    company_type="NON_FINANCIAL",
    net_income=15.0,
):
    return {
        "schema_version": "sec_normalized_v0.1",
        "ticker": "TEST",
        "company_type": company_type,
        "metrics": {
            "net_income": _ok_metric(
                net_income
            ),
        },
    }


# ============================================================
# CONFIGURATION TESTS
# ============================================================

def test_quality_metrics_configuration():
    expected_metrics = {
        "operating_margin",
        "net_margin",
        "fcf_margin",
        "fcf_conversion",
        "net_debt_to_fcf",
        "debt_to_cash",
        "profit_to_cash_consistency",
    }

    assert set(QUALITY_METRICS) == expected_metrics


# ============================================================
# STAR THRESHOLD TESTS
# ============================================================

def test_operating_margin_thresholds():
    assert rate_operating_margin(0.049)["stars"] == 1
    assert rate_operating_margin(0.05)["stars"] == 2
    assert rate_operating_margin(0.099)["stars"] == 2
    assert rate_operating_margin(0.10)["stars"] == 3
    assert rate_operating_margin(0.199)["stars"] == 3
    assert rate_operating_margin(0.20)["stars"] == 4
    assert rate_operating_margin(0.299)["stars"] == 4
    assert rate_operating_margin(0.30)["stars"] == 5


def test_net_margin_thresholds():
    assert rate_net_margin(0.019)["stars"] == 1
    assert rate_net_margin(0.02)["stars"] == 2
    assert rate_net_margin(0.059)["stars"] == 2
    assert rate_net_margin(0.06)["stars"] == 3
    assert rate_net_margin(0.119)["stars"] == 3
    assert rate_net_margin(0.12)["stars"] == 4
    assert rate_net_margin(0.199)["stars"] == 4
    assert rate_net_margin(0.20)["stars"] == 5


def test_fcf_margin_thresholds():
    assert rate_fcf_margin(0.019)["stars"] == 1
    assert rate_fcf_margin(0.02)["stars"] == 2
    assert rate_fcf_margin(0.059)["stars"] == 2
    assert rate_fcf_margin(0.06)["stars"] == 3
    assert rate_fcf_margin(0.119)["stars"] == 3
    assert rate_fcf_margin(0.12)["stars"] == 4
    assert rate_fcf_margin(0.199)["stars"] == 4
    assert rate_fcf_margin(0.20)["stars"] == 5


def test_fcf_conversion_thresholds():
    assert rate_fcf_conversion(0.399)["stars"] == 1
    assert rate_fcf_conversion(0.40)["stars"] == 2
    assert rate_fcf_conversion(0.599)["stars"] == 2
    assert rate_fcf_conversion(0.60)["stars"] == 3
    assert rate_fcf_conversion(0.799)["stars"] == 3
    assert rate_fcf_conversion(0.80)["stars"] == 4
    assert rate_fcf_conversion(0.999)["stars"] == 4
    assert rate_fcf_conversion(1.00)["stars"] == 5


def test_net_debt_to_fcf_thresholds():
    assert rate_net_debt_to_fcf(-1.0)["stars"] == 5
    assert rate_net_debt_to_fcf(0.0)["stars"] == 5
    assert rate_net_debt_to_fcf(1.0)["stars"] == 5
    assert rate_net_debt_to_fcf(1.5)["stars"] == 4
    assert rate_net_debt_to_fcf(2.0)["stars"] == 4
    assert rate_net_debt_to_fcf(2.5)["stars"] == 3
    assert rate_net_debt_to_fcf(3.0)["stars"] == 3
    assert rate_net_debt_to_fcf(3.5)["stars"] == 2
    assert rate_net_debt_to_fcf(4.0)["stars"] == 2
    assert rate_net_debt_to_fcf(4.1)["stars"] == 1


def test_debt_to_cash_thresholds():
    assert rate_debt_to_cash(0.5)["stars"] == 5
    assert rate_debt_to_cash(0.75)["stars"] == 4
    assert rate_debt_to_cash(1.0)["stars"] == 4
    assert rate_debt_to_cash(1.5)["stars"] == 3
    assert rate_debt_to_cash(2.0)["stars"] == 3
    assert rate_debt_to_cash(3.0)["stars"] == 2
    assert rate_debt_to_cash(4.0)["stars"] == 2
    assert rate_debt_to_cash(4.1)["stars"] == 1


def test_profit_to_cash_consistency_thresholds():
    assert rate_profit_to_cash_consistency(0.599)["stars"] == 1
    assert rate_profit_to_cash_consistency(0.60)["stars"] == 2
    assert rate_profit_to_cash_consistency(0.799)["stars"] == 2
    assert rate_profit_to_cash_consistency(0.80)["stars"] == 3
    assert rate_profit_to_cash_consistency(0.999)["stars"] == 3
    assert rate_profit_to_cash_consistency(1.00)["stars"] == 4
    assert rate_profit_to_cash_consistency(1.199)["stars"] == 4
    assert rate_profit_to_cash_consistency(1.20)["stars"] == 5


# ============================================================
# RAW CALCULATION TESTS
# ============================================================

def test_fcf_conversion_calculation():
    result = calculate_fcf_conversion(
        100.0,
        90.0,
    )

    assert result["status"] == "OK"
    assert result["value"] == 0.90


def test_fcf_conversion_zero_net_income():
    result = calculate_fcf_conversion(
        0.0,
        10.0,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_fcf_conversion_negative_net_income():
    result = calculate_fcf_conversion(
        -10.0,
        5.0,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_net_debt_to_fcf_calculation():
    result = calculate_net_debt_to_fcf(
        20.0,
        10.0,
    )

    assert result["status"] == "OK"
    assert result["value"] == 2.0


def test_net_debt_to_fcf_negative_net_debt():
    result = calculate_net_debt_to_fcf(
        -20.0,
        20.0,
    )

    assert result["status"] == "OK"
    assert result["value"] == -1.0


def test_net_debt_to_fcf_negative_fcf():
    result = calculate_net_debt_to_fcf(
        20.0,
        -10.0,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_profit_to_cash_consistency_calculation():
    result = calculate_profit_to_cash_consistency(
        0.10,
        0.12,
    )

    assert result["status"] == "OK"
    assert result["value"] == 1.2


def test_profit_to_cash_consistency_negative_net_margin():
    result = calculate_profit_to_cash_consistency(
        -0.10,
        0.12,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


# ============================================================
# MAIN QUALITY CALCULATION
# ============================================================

def test_calculate_quality_returns_expected_structure():
    normalized = _build_normalized()

    derived = _build_derived(
        operating_margin=0.25,
        net_margin=0.15,
        fcf_margin=0.18,
        fcf=18.0,
        net_debt=10.0,
        debt_to_cash=0.5,
    )

    result = calculate_quality(
        normalized,
        derived,
    )

    assert result["schema_version"] == "quality_v0.1"
    assert result["ticker"] == "TEST"
    assert result["company_type"] == "NON_FINANCIAL"

    assert "quality" in result
    assert "components" in result
    assert "limitations" in result


def test_calculate_quality_contains_all_quality_metrics():
    result = calculate_quality(
        _build_normalized(),
        _build_derived(),
    )

    assert set(result["components"].keys()) == set(
        QUALITY_METRICS
    )


def test_calculate_quality_score_is_bounded():
    result = calculate_quality(
        _build_normalized(),
        _build_derived(),
    )

    quality = result["quality"]

    assert quality["status"] == "OK"
    assert 0.0 <= quality["score"] <= 5.0
    assert quality["stars"] in {
        1,
        2,
        3,
        4,
        5,
    }


# ============================================================
# COMPONENT CALCULATION TESTS
# ============================================================

def test_quality_component_values_are_present():
    result = calculate_quality(
        _build_normalized(),
        _build_derived(),
    )

    components = result["components"]

    for metric_name in QUALITY_METRICS:
        assert metric_name in components
        assert "status" in components[metric_name]
        assert "value" in components[metric_name]


def test_fcf_conversion_component_is_calculated():
    result = calculate_quality(
        _build_normalized(net_income=15.0),
        _build_derived(fcf=18.0),
    )

    component = result["components"]["fcf_conversion"]

    assert component["status"] == "OK"
    assert component["value"] == 1.2
    assert component["stars"] == 5


def test_net_debt_to_fcf_component_is_calculated():
    result = calculate_quality(
        _build_normalized(),
        _build_derived(
            fcf=20.0,
            net_debt=10.0,
        ),
    )

    component = result["components"]["net_debt_to_fcf"]

    assert component["status"] == "OK"
    assert component["value"] == 0.5
    assert component["stars"] == 5


def test_profit_to_cash_component_is_calculated():
    result = calculate_quality(
        _build_normalized(),
        _build_derived(
            net_margin=0.10,
            fcf_margin=0.12,
        ),
    )

    component = result["components"][
        "profit_to_cash_consistency"
    ]

    assert component["status"] == "OK"
    assert component["value"] == 1.2
    assert component["stars"] == 5


# ============================================================
# MISSING COMPONENT HANDLING
# ============================================================

def test_missing_components_are_excluded_from_score():
    derived = _build_derived()

    derived["derived_metrics"][
        "operating_margin"
    ] = _missing_metric()

    derived["derived_metrics"][
        "net_margin"
    ] = _missing_metric()

    result = calculate_quality(
        _build_normalized(),
        derived,
    )

    assert (
        result["components"]["operating_margin"]["status"]
        == "MISSING"
    )

    assert (
        result["components"]["net_margin"]["status"]
        == "MISSING"
    )

    assert result["quality"]["status"] == "OK"
    assert (
        result["quality"]["available_components"]
        >= 4
    )


def test_quality_requires_minimum_four_valid_components():
    derived = _build_derived()

    for metric_name in [
        "operating_margin",
        "net_margin",
        "fcf_margin",
        "debt_to_cash",
    ]:
        derived["derived_metrics"][metric_name] = (
            _missing_metric()
        )

    result = calculate_quality(
        _build_normalized(),
        derived,
    )

    assert result["quality"]["status"] == "MISSING"
    assert result["quality"]["score"] is None
    assert result["quality"]["stars"] is None


# ============================================================
# EDGE CASES
# ============================================================

def test_negative_net_income_does_not_crash_quality():
    normalized = _build_normalized(
        net_income=-10.0
    )

    derived = _build_derived(
        operating_margin=-0.05,
        net_margin=-0.10,
        fcf_margin=-0.02,
        fcf=-2.0,
        net_debt=20.0,
        debt_to_cash=2.0,
    )

    result = calculate_quality(
        normalized,
        derived,
    )

    assert result["quality"]["status"] in {
        "OK",
        "MISSING",
    }


def test_negative_net_debt_is_not_treated_as_bad():
    derived = _build_derived(
        fcf=20.0,
        net_debt=-20.0,
    )

    result = calculate_quality(
        _build_normalized(),
        derived,
    )

    metric = result["components"][
        "net_debt_to_fcf"
    ]

    assert metric["status"] == "OK"
    assert metric["value"] == -1.0
    assert metric["stars"] == 5


# ============================================================
# NEGATIVE-REVENUE MARGIN PROPAGATION (Round AN/AO)
# ============================================================

def test_invalid_margin_component_is_not_rated_ok():
    # Round AN/AO: when a revenue-based margin comes back INVALID
    # upstream (e.g. derived_metrics.calculate_margin() rejecting a
    # negative revenue denominator), quality.py's existing (unmodified)
    # _get_derived_value() only passes through status == "OK" values,
    # so a non-OK upstream status reaches rate_operating_margin() as
    # None and is reported as MISSING here -- the exact status label
    # quality.py already uses for "no usable value", not "INVALID".
    # The actual requirement under test is that the component is never
    # upgraded to "OK" / a positive value / a 5-star rating.
    derived = _build_derived()

    derived["derived_metrics"]["operating_margin"] = {
        "status": "INVALID",
        "value": None,
        "reason": "Revenue is negative; margin cannot be calculated.",
    }

    result = calculate_quality(
        _build_normalized(),
        derived,
    )

    operating_margin_component = result["components"]["operating_margin"]

    assert operating_margin_component["status"] != "OK"
    assert operating_margin_component["status"] == "MISSING"
    assert operating_margin_component["value"] is None
    assert operating_margin_component.get("stars") is None

    # Enough other valid components remain for an overall score --
    # one INVALID component does not force the whole Quality result
    # to MISSING under the existing >=4-valid-components design.
    assert result["quality"]["status"] in {"OK", "MISSING"}

    if result["quality"]["status"] == "OK":
        assert result["quality"]["stars"] in {1, 2, 3, 4, 5}


def test_invalid_fcf_margin_component_is_not_rated_ok():
    # Same propagation check for fcf_margin, which also flows through
    # calculate_margin() and the same revenue-sign guard. See the note
    # in test_invalid_margin_component_is_not_rated_ok() above on why
    # the resulting component status is "MISSING", not "INVALID".
    derived = _build_derived()

    derived["derived_metrics"]["fcf_margin"] = {
        "status": "INVALID",
        "value": None,
        "reason": "Revenue is negative; margin cannot be calculated.",
    }

    result = calculate_quality(
        _build_normalized(),
        derived,
    )

    fcf_margin_component = result["components"]["fcf_margin"]

    assert fcf_margin_component["status"] != "OK"
    assert fcf_margin_component["status"] == "MISSING"
    assert fcf_margin_component["value"] is None
    assert fcf_margin_component.get("stars") is None


# ============================================================
# FINANCIAL COMPANY HANDLING
# ============================================================

def test_financial_company_contains_limitation():
    normalized = _build_normalized(
        company_type="FINANCIAL",
    )

    result = calculate_quality(
        normalized,
        _build_derived(),
    )

    assert "limitations" in result
    assert "financial_company" in result["limitations"]

    limitation = result["limitations"][
        "financial_company"
    ]

    assert limitation["status"] == "WARNING"


# ============================================================
# VALIDATION
# ============================================================

def test_validate_quality_accepts_valid_result():
    result = calculate_quality(
        _build_normalized(),
        _build_derived(),
    )

    failures = validate_quality(result)

    assert failures == []


def test_validate_quality_rejects_invalid_result():
    result = calculate_quality(
        _build_normalized(),
        _build_derived(),
    )

    result["quality"]["score"] = "invalid"

    failures = validate_quality(result)

    assert "quality.score" in failures


def test_validate_quality_rejects_invalid_status():
    result = calculate_quality(
        _build_normalized(),
        _build_derived(),
    )

    result["quality"]["status"] = "UNKNOWN"

    failures = validate_quality(result)

    assert "quality.status" in failures


# ============================================================
# TYPE VALIDATION
# ============================================================

def test_rate_functions_handle_non_numeric_values():
    rate_functions = [
        rate_operating_margin,
        rate_net_margin,
        rate_fcf_margin,
        rate_fcf_conversion,
        rate_net_debt_to_fcf,
        rate_debt_to_cash,
        rate_profit_to_cash_consistency,
    ]

    for rate_function in rate_functions:
        result = rate_function(None)

        assert isinstance(result, dict)
        assert result["status"] == "MISSING"
        assert result["value"] is None