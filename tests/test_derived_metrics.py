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


def test_calculate_margin_negative_revenue_positive_numerator():
    # Round AN: positive numerator / negative revenue is a sign
    # combination that is not economically meaningful as a margin
    # (Round AN reachability audit). Must be INVALID, not an "OK"
    # negative margin.
    result = calculate_margin(
        "operating_income",
        30,
        -100,
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_margin_negative_revenue_negative_numerator():
    # Round AN: negative numerator / negative revenue would otherwise
    # divide out to a positive ratio, which could be misread as a
    # healthy margin despite both inputs representing a loss on
    # negative revenue. Must be INVALID, not an "OK" positive margin.
    result = calculate_margin(
        "operating_income",
        -30,
        -100,
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


def test_calculate_derived_metrics_negative_revenue_margins_are_invalid():
    # Round AN/AO: a negative revenue value must not be divided through
    # into an "OK" margin for any of the three revenue-based margins.
    # Unrelated metrics (fcf, net_debt, debt_to_cash) must be unaffected.
    normalized = make_normalized_fixture()

    normalized["metrics"]["revenue"] = make_metric(-120000)

    result = calculate_derived_metrics(normalized)

    derived = result["derived_metrics"]

    assert derived["net_margin"]["status"] == "INVALID"
    assert derived["net_margin"]["value"] is None

    assert derived["operating_margin"]["status"] == "INVALID"
    assert derived["operating_margin"]["value"] is None

    assert derived["fcf_margin"]["status"] == "INVALID"
    assert derived["fcf_margin"]["value"] is None

    # Metrics that do not depend on revenue are unaffected.
    assert derived["fcf"]["status"] == "OK"
    assert derived["fcf"]["value"] == pytest.approx(20000)

    assert derived["net_debt"]["status"] == "OK"
    assert derived["debt_to_cash"]["status"] == "OK"


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


def make_metric_with_unit(value, unit, status="OK"):
    return {
        "status": status,
        "value": value,
        "unit": unit,
    }


def make_normalized_fixture_with_units(
    *,
    revenue_unit="USD",
    net_income_unit="USD",
    operating_income_unit="USD",
    cfo_unit="USD",
    capex_unit="USD",
    cash_unit="USD",
    total_debt_unit="USD",
):
    return {
        "schema_version": "sec_fundamentals_v0.1",
        "ticker": "TEST",
        "entity_name": "Test Company",
        "cik": "0000000000",
        "company_type": "NON_FINANCIAL",
        "metrics": {
            "revenue": make_metric_with_unit(120000, revenue_unit),
            "net_income": make_metric_with_unit(20000, net_income_unit),
            "operating_income": make_metric_with_unit(
                30000, operating_income_unit
            ),
            "cfo": make_metric_with_unit(25000, cfo_unit),
            "capex": make_metric_with_unit(-5000, capex_unit),
            "cash": make_metric_with_unit(50000, cash_unit),
            "total_debt": make_metric_with_unit(40000, total_debt_unit),
        },
    }


# ============================================================
# UNIT CONSISTENCY (Round AV)
#
# None of calculate_margin()/calculate_fcf()/calculate_net_debt()/
# calculate_debt_to_cash() previously accepted or checked unit-of-
# measure metadata at all -- and calculate_derived_metrics(), the
# orchestration layer, discarded the "unit" field that real SEC-
# normalized metric dicts carry (see sec_normalizer.py's
# build_metric_result()/build_instant_result(), which always set a
# "unit" key) before calling them. Two independently-sourced monetary
# values with different units of measure (e.g. one filer concept
# reported in USD, another in CAD for a foreign subsidiary) could
# therefore be silently summed, subtracted, or divided as if they
# were the same currency, producing a numerically "OK" but
# economically meaningless result -- with no diagnostic signal.
#
# Contract adopted here (mirrors the existing calculate_total_debt()
# convention in sec_normalizer.py, Round AU design constraint A-4):
#   - A caller that never supplies unit information at all (every
#     pre-Round-AV call site, and the simplified historical-record
#     shape used by YoY/CAGR/TTM, which carries no "unit" field by
#     design and is out of scope this round) sees NO behavior change.
#   - Once unit information is supplied on at least one side:
#       - an unknown/missing unit on either side -> MISSING (cannot
#         prove compatibility; never assumed compatible by default);
#       - two known, different units -> INVALID (refuses to combine
#         without an approved FX conversion policy -- no implicit
#         conversion is ever performed);
#       - two known, equal units -> OK, and for fcf/net_debt (which
#         combine two same-kind monetary values into another
#         monetary value) the resolved unit is propagated onto the
#         output, exactly as calculate_total_debt() does. Margin and
#         debt_to_cash outputs are dimensionless ratios and carry no
#         unit, matching their existing (unchanged) output shape.
# ============================================================

def test_calculate_margin_compatible_units_ok():
    result = calculate_margin(
        "net_income",
        20,
        100,
        numerator_unit="USD",
        revenue_unit="USD",
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(0.20)


def test_calculate_margin_incompatible_currencies_is_invalid():
    result = calculate_margin(
        "net_income",
        20,
        100,
        numerator_unit="USD",
        revenue_unit="CAD",
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_margin_missing_unit_metadata_is_missing():
    result = calculate_margin(
        "net_income",
        20,
        100,
        numerator_unit=None,
        revenue_unit="USD",
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


def test_calculate_margin_without_unit_kwargs_is_unchanged():
    # Backward compatibility: the entire pre-Round-AV call surface
    # never supplies unit information at all and must see no
    # behavior change whatsoever.
    result = calculate_margin("net_income", 20, 100)

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(0.20)


def test_calculate_fcf_compatible_units_ok_and_propagates_unit():
    result = calculate_fcf(
        25000,
        -5000,
        cfo_unit="USD",
        capex_unit="USD",
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(20000)
    assert result["unit"] == "USD"


def test_calculate_fcf_incompatible_currencies_is_invalid():
    result = calculate_fcf(
        25000,
        -5000,
        cfo_unit="USD",
        capex_unit="CAD",
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_fcf_missing_unit_metadata_is_missing():
    result = calculate_fcf(
        25000,
        -5000,
        cfo_unit="USD",
        capex_unit=None,
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


def test_calculate_fcf_without_unit_kwargs_is_unchanged():
    result = calculate_fcf(25000, -5000)

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(20000)
    assert "unit" not in result


def test_calculate_net_debt_compatible_units_ok_and_propagates_unit():
    result = calculate_net_debt(
        40000,
        50000,
        total_debt_unit="USD",
        cash_unit="USD",
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(-10000)
    assert result["unit"] == "USD"


def test_calculate_net_debt_incompatible_currencies_is_invalid():
    result = calculate_net_debt(
        40000,
        50000,
        total_debt_unit="USD",
        cash_unit="CAD",
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_net_debt_missing_unit_metadata_is_missing():
    result = calculate_net_debt(
        40000,
        50000,
        total_debt_unit="USD",
        cash_unit=None,
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


def test_calculate_net_debt_without_unit_kwargs_is_unchanged():
    result = calculate_net_debt(40000, 50000)

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(-10000)
    assert "unit" not in result


def test_calculate_debt_to_cash_compatible_units_ok():
    result = calculate_debt_to_cash(
        40000,
        50000,
        total_debt_unit="USD",
        cash_unit="USD",
    )

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(0.8)


def test_calculate_debt_to_cash_incompatible_currencies_is_invalid():
    result = calculate_debt_to_cash(
        40000,
        50000,
        total_debt_unit="USD",
        cash_unit="CAD",
    )

    assert result["status"] == "INVALID"
    assert result["value"] is None


def test_calculate_debt_to_cash_missing_unit_metadata_is_missing():
    result = calculate_debt_to_cash(
        40000,
        50000,
        total_debt_unit=None,
        cash_unit="USD",
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


def test_calculate_debt_to_cash_without_unit_kwargs_is_unchanged():
    result = calculate_debt_to_cash(40000, 50000)

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(0.8)


def test_calculate_derived_metrics_all_units_consistent_remains_ok():
    normalized = make_normalized_fixture_with_units()

    result = calculate_derived_metrics(normalized)
    derived = result["derived_metrics"]

    assert derived["fcf"]["status"] == "OK"
    assert derived["net_margin"]["status"] == "OK"
    assert derived["operating_margin"]["status"] == "OK"
    assert derived["fcf_margin"]["status"] == "OK"
    assert derived["net_debt"]["status"] == "OK"
    assert derived["debt_to_cash"]["status"] == "OK"


def test_calculate_derived_metrics_mismatched_cash_currency_blocks_net_debt_and_debt_to_cash():
    # Round AV primary defect scenario (run against the unfixed
    # implementation first; see the Round AV report for the exact
    # pre-fix observed result). total_debt is reported in USD, cash
    # in a different currency (CAD). The pre-fix orchestrator
    # discarded unit metadata entirely before calling
    # calculate_net_debt()/calculate_debt_to_cash(), silently
    # subtracting/dividing the numerically mismatched-currency values
    # and returning "OK".
    normalized = make_normalized_fixture_with_units(cash_unit="CAD")

    result = calculate_derived_metrics(normalized)
    derived = result["derived_metrics"]

    assert derived["net_debt"]["status"] == "INVALID"
    assert derived["net_debt"]["value"] is None

    assert derived["debt_to_cash"]["status"] == "INVALID"
    assert derived["debt_to_cash"]["value"] is None

    # Unrelated metrics (revenue-based margins, fcf) are unaffected.
    assert derived["fcf"]["status"] == "OK"
    assert derived["net_margin"]["status"] == "OK"


def test_calculate_derived_metrics_mismatched_capex_currency_blocks_fcf_and_fcf_margin():
    # cfo and capex reported in different currencies. fcf itself must
    # refuse to combine them, and fcf_margin (which depends on fcf's
    # own output) must likewise refuse rather than dividing a
    # mismatched-currency fcf by revenue.
    normalized = make_normalized_fixture_with_units(capex_unit="EUR")

    result = calculate_derived_metrics(normalized)
    derived = result["derived_metrics"]

    assert derived["fcf"]["status"] == "INVALID"
    assert derived["fcf"]["value"] is None

    assert derived["fcf_margin"]["status"] in {"MISSING", "INVALID"}
    assert derived["fcf_margin"]["value"] is None

    # net_margin/operating_margin do not depend on fcf and are
    # unaffected.
    assert derived["net_margin"]["status"] == "OK"
    assert derived["operating_margin"]["status"] == "OK"


def test_calculate_derived_metrics_mismatched_net_income_currency_blocks_net_margin_only():
    normalized = make_normalized_fixture_with_units(net_income_unit="GBP")

    result = calculate_derived_metrics(normalized)
    derived = result["derived_metrics"]

    assert derived["net_margin"]["status"] == "INVALID"
    assert derived["net_margin"]["value"] is None

    # operating_margin uses a different numerator and is unaffected.
    assert derived["operating_margin"]["status"] == "OK"


def test_calculate_derived_metrics_missing_unit_metadata_is_missing_not_ok():
    # A real normalized metric dict whose "unit" key is explicitly
    # None (e.g. an SEC observation with no XBRL unit of measure
    # recorded) must not be silently treated as compatible with a
    # known unit on the other side.
    normalized = make_normalized_fixture_with_units()
    normalized["metrics"]["cash"]["unit"] = None

    result = calculate_derived_metrics(normalized)
    derived = result["derived_metrics"]

    assert derived["net_debt"]["status"] == "MISSING"
    assert derived["debt_to_cash"]["status"] == "MISSING"


def test_calculate_derived_metrics_without_unit_fields_is_fully_backward_compatible():
    # The pre-Round-AV fixture shape (no "unit" key anywhere) must
    # continue to produce the same status/value results as before
    # this round's change.
    normalized = make_normalized_fixture()  # no "unit" keys at all

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2024-12-31", "value": 100000},
            {"period_end": "2025-12-31", "value": 120000},
        ],
    )
    derived = result["derived_metrics"]

    assert derived["fcf"]["status"] == "OK"
    assert derived["fcf"]["value"] == pytest.approx(20000)
    assert derived["net_margin"]["status"] == "OK"
    assert derived["net_debt"]["status"] == "OK"
    assert derived["debt_to_cash"]["status"] == "OK"
    assert derived["revenue_yoy"]["status"] == "OK"


# ============================================================
# UNIT-VALIDATION STATE DISCLOSURE (Round AV-1)
#
# Round AV added unit checking to calculate_margin()/calculate_fcf()/
# calculate_net_debt()/calculate_debt_to_cash(), but a legacy direct
# call that omits both unit keyword arguments falls through the same
# "_resolve_unit_pair() -> compatible" code path's SHAPE as a call
# that actually supplied two matching units -- both currently produce
# {"status": "OK", "value": ..., ...} with no field distinguishing
# "units were checked and found compatible" from "units were never
# checked at all". For calculate_margin()/calculate_debt_to_cash()
# (whose OK output has never carried a "unit" key, since the output
# is a dimensionless ratio) these two cases are, before this round's
# fix, byte-for-byte identical result dicts -- a caller cannot tell
# them apart by inspecting the returned object. For calculate_fcf()/
# calculate_net_debt(), the presence/absence of the "unit" key
# happens to correlate with validated-vs-unchecked today, but that is
# an incidental side effect of unit propagation, not a declared,
# intentional diagnostic -- and it still provides no "MISSING"/
# "MISMATCHED" alignment with the status for a caller inspecting
# fields rather than status/reason text.
# ============================================================

def test_calculate_margin_legacy_call_is_tagged_unchecked():
    # Case 1 (Part A): both unit arguments omitted entirely.
    result = calculate_margin("net_income", 20, 100)

    assert result["status"] == "OK"
    assert result.get("unit_validation") == "UNCHECKED"


def test_calculate_margin_validated_compatible_is_tagged_validated():
    # Case 4 (Part A): both units present and equal. Must be provably
    # distinguishable from the legacy "UNCHECKED" case above despite
    # both being status "OK".
    result = calculate_margin(
        "net_income",
        20,
        100,
        numerator_unit="USD",
        revenue_unit="USD",
    )

    assert result["status"] == "OK"
    assert result.get("unit_validation") == "VALIDATED"


def test_calculate_margin_partial_unit_args_is_not_mistaken_for_validated():
    # Case 2 (Part A): one unit argument supplied, the other omitted.
    # Must never be reported as "OK"/"VALIDATED" -- that would imply
    # a compatibility check that could not actually be performed.
    result = calculate_margin(
        "net_income",
        20,
        100,
        numerator_unit="USD",
        # revenue_unit intentionally omitted.
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None
    assert result.get("unit_validation") != "VALIDATED"
    assert result.get("unit_validation") == "MISSING"


def test_calculate_margin_both_units_explicitly_none_is_missing():
    # Case 3 (Part A): both unit arguments explicitly None (not
    # omitted). Must be treated the same as "missing", never as
    # compatible.
    result = calculate_margin(
        "net_income",
        20,
        100,
        numerator_unit=None,
        revenue_unit=None,
    )

    assert result["status"] == "MISSING"
    assert result.get("unit_validation") == "MISSING"


def test_calculate_margin_mismatched_units_tagged_mismatched():
    # Case 5 (Part A).
    result = calculate_margin(
        "net_income",
        20,
        100,
        numerator_unit="USD",
        revenue_unit="CAD",
    )

    assert result["status"] == "INVALID"
    assert result.get("unit_validation") == "MISMATCHED"


def test_calculate_fcf_legacy_call_is_tagged_unchecked_and_has_no_unit_key():
    result = calculate_fcf(25000, -5000)

    assert result["status"] == "OK"
    assert result.get("unit_validation") == "UNCHECKED"
    assert "unit" not in result


def test_calculate_fcf_validated_compatible_is_tagged_validated():
    result = calculate_fcf(
        25000,
        -5000,
        cfo_unit="USD",
        capex_unit="USD",
    )

    assert result["status"] == "OK"
    assert result.get("unit_validation") == "VALIDATED"
    assert result["unit"] == "USD"


def test_calculate_fcf_partial_unit_args_is_not_mistaken_for_validated():
    result = calculate_fcf(
        25000,
        -5000,
        cfo_unit="USD",
        # capex_unit intentionally omitted.
    )

    assert result["status"] == "MISSING"
    assert result.get("unit_validation") == "MISSING"
    assert "unit" not in result


def test_calculate_net_debt_legacy_call_is_tagged_unchecked_and_has_no_unit_key():
    result = calculate_net_debt(40000, 50000)

    assert result["status"] == "OK"
    assert result.get("unit_validation") == "UNCHECKED"
    assert "unit" not in result


def test_calculate_net_debt_validated_compatible_is_tagged_validated():
    result = calculate_net_debt(
        40000,
        50000,
        total_debt_unit="USD",
        cash_unit="USD",
    )

    assert result["status"] == "OK"
    assert result.get("unit_validation") == "VALIDATED"
    assert result["unit"] == "USD"


def test_calculate_net_debt_partial_unit_args_is_not_mistaken_for_validated():
    result = calculate_net_debt(
        40000,
        50000,
        # total_debt_unit intentionally omitted.
        cash_unit="USD",
    )

    assert result["status"] == "MISSING"
    assert result.get("unit_validation") == "MISSING"


def test_calculate_debt_to_cash_legacy_call_is_tagged_unchecked():
    result = calculate_debt_to_cash(40000, 50000)

    assert result["status"] == "OK"
    assert result.get("unit_validation") == "UNCHECKED"


def test_calculate_debt_to_cash_validated_compatible_is_tagged_validated():
    result = calculate_debt_to_cash(
        40000,
        50000,
        total_debt_unit="USD",
        cash_unit="USD",
    )

    assert result["status"] == "OK"
    assert result.get("unit_validation") == "VALIDATED"


def test_calculate_debt_to_cash_partial_unit_args_is_not_mistaken_for_validated():
    result = calculate_debt_to_cash(
        40000,
        50000,
        total_debt_unit="USD",
        # cash_unit intentionally omitted.
    )

    assert result["status"] == "MISSING"
    assert result.get("unit_validation") == "MISSING"


def test_calculate_margin_zero_revenue_still_reports_unit_validation_state():
    # A status-INVALID result reached for a non-unit reason (zero
    # revenue) after units were confirmed compatible must still
    # honestly report that compatibility was checked, not silently
    # omit the diagnostic just because the ultimate status was
    # INVALID for an unrelated reason.
    result = calculate_margin(
        "operating_income",
        30,
        0,
        numerator_unit="USD",
        revenue_unit="USD",
    )

    assert result["status"] == "INVALID"
    assert result.get("unit_validation") == "VALIDATED"


def test_calculate_derived_metrics_legacy_fixture_results_are_tagged_unchecked():
    # End-to-end: the pre-Round-AV fixture shape (no "unit" key
    # anywhere in the normalized metrics) must flow through
    # calculate_derived_metrics() and surface "UNCHECKED" on every
    # unit-sensitive derived metric, not a silent, unmarked "OK".
    normalized = make_normalized_fixture()  # no "unit" keys at all

    result = calculate_derived_metrics(normalized)
    derived = result["derived_metrics"]

    assert derived["fcf"]["unit_validation"] == "UNCHECKED"
    assert derived["net_margin"]["unit_validation"] == "UNCHECKED"
    assert derived["operating_margin"]["unit_validation"] == "UNCHECKED"
    assert derived["fcf_margin"]["unit_validation"] == "UNCHECKED"
    assert derived["net_debt"]["unit_validation"] == "UNCHECKED"
    assert derived["debt_to_cash"]["unit_validation"] == "UNCHECKED"


def test_calculate_derived_metrics_consistent_units_are_tagged_validated():
    normalized = make_normalized_fixture_with_units()

    result = calculate_derived_metrics(normalized)
    derived = result["derived_metrics"]

    assert derived["fcf"]["unit_validation"] == "VALIDATED"
    assert derived["net_margin"]["unit_validation"] == "VALIDATED"
    assert derived["operating_margin"]["unit_validation"] == "VALIDATED"
    assert derived["fcf_margin"]["unit_validation"] == "VALIDATED"
    assert derived["net_debt"]["unit_validation"] == "VALIDATED"
    assert derived["debt_to_cash"]["unit_validation"] == "VALIDATED"


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


# ============================================================
# HISTORICAL PERIOD ALIGNMENT (Round AW)
#
# _calculate_history_yoy() -- the only function among calculate_yoy(),
# calculate_cagr(), calculate_ttm_from_quarters(), and
# calculate_ttm_from_ytd() that actually receives per-observation date
# information ({period_end, value} records) -- selected the two
# largest "period_end" strings by lexical sort and fed them straight
# into calculate_yoy(), without ever checking that the gap between
# them is actually close to one year. calculate_cagr() takes a
# caller-supplied "years" float with no record list at all;
# calculate_ttm_from_quarters() takes a bare list[Any] of four numbers
# with no period_end; calculate_ttm_from_ytd() takes three bare
# scalars with no period_end either. None of those three can be
# audited for period alignment from their own inputs -- see the Round
# AW report's Part B2/B3/B4 for why each is a genuine interface
# limitation rather than a demonstrated defect, and why only
# _calculate_history_yoy() is corrected below.
#
# Omitted categories (see Round AW report for the full reasoning):
#   7. YTD crossing a fiscal-year boundary, 8. YTD with a missing
#      intermediate period -- calculate_ttm_from_ytd() takes three
#      bare scalars with no period_end field at all, so a "fiscal-
#      year boundary" or "missing intermediate period" cannot be
#      represented in its actual input, let alone detected.
# ============================================================

def test_history_yoy_duplicate_period_end_is_invalid_not_ok():
    # Category 2: two records sharing the exact same period_end (e.g.
    # a restated figure for the same fiscal year). Zero elapsed time
    # between "current" and "previous" can never be a meaningful YoY
    # comparison.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2025-12-31", "value": 100},
            {"period_end": "2025-12-31", "value": 120},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "INVALID"
    assert yoy["value"] is None


def test_history_yoy_malformed_period_end_is_excluded_not_selected_as_latest():
    # Category 3: a record with a non-None but unparseable
    # "period_end" string. Round AW primary defect scenario (see the
    # report for the exact pre-fix observed result): a lexical string
    # sort ranks "not-a-date" ahead of any real ISO date (since 'n' >
    # '2' in ASCII), so the pre-fix implementation picked the garbage
    # record as "current" and used its unrelated value as if it were
    # the latest observation.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2025-12-31", "value": 100},
            {"period_end": "not-a-date", "value": 999},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    # Only one record has a parseable period_end, so there are not
    # two valid historical observations to compare.
    assert yoy["status"] == "MISSING"
    assert yoy["value"] is None


def test_history_yoy_missing_intermediate_period_two_year_gap_is_invalid():
    # Category 4: the two most-recent records are actually two years
    # apart (an intermediate fiscal year's observation is absent from
    # the supplied history), not one year apart.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2023-12-31", "value": 80},
            {"period_end": "2025-12-31", "value": 120},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "INVALID"
    assert yoy["value"] is None


def test_history_yoy_adjacent_quarters_is_invalid_not_mislabeled_as_yoy():
    # Category 5: the two most-recent records are adjacent fiscal
    # quarters (~92 days apart), not a year apart. The pre-fix
    # implementation could not distinguish this from a genuine annual
    # comparison and would silently label a QoQ change as "YoY".
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2025-09-30", "value": 120},
            {"period_end": "2025-12-31", "value": 130},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "INVALID"
    assert yoy["value"] is None


def test_history_yoy_insufficient_observations_still_missing():
    # Category 6: preserve the existing, already-correct behavior --
    # a single valid historical observation can never be enough for
    # YoY, independent of the new period-gap check.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2025-12-31", "value": 100},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "MISSING"
    assert yoy["value"] is None


def test_history_yoy_reverse_chronological_input_order_unaffected():
    # Category 1: input order must not matter -- records supplied
    # newest-first must select the same pair as oldest-first.
    normalized = make_normalized_fixture()

    result_newest_first = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2025-12-31", "value": 120},
            {"period_end": "2024-12-31", "value": 100},
        ],
    )
    result_oldest_first = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2024-12-31", "value": 100},
            {"period_end": "2025-12-31", "value": 120},
        ],
    )

    for result in (result_newest_first, result_oldest_first):
        yoy = result["derived_metrics"]["revenue_yoy"]
        assert yoy["status"] == "OK"
        assert yoy["value"] == pytest.approx(0.20)


def test_history_yoy_tolerates_real_fiscal_calendar_drift():
    # Category 10 (preservation): a 52/53-week fiscal calendar's
    # year-end date legitimately drifts by a few days year to year
    # (364 days apart here, not exactly 365). This must still be
    # accepted as a valid YoY comparison, not rejected by an
    # overly-strict exact-365-day requirement.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2024-12-28", "value": 100},
            {"period_end": "2025-12-27", "value": 120},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "OK"
    assert yoy["value"] == pytest.approx(0.20)


def test_calculate_cagr_years_is_independent_of_any_record_count():
    # Category 9 (closure, not a defect): calculate_cagr() has no
    # record-list parameter at all -- "years" is a bare caller-
    # supplied float. This confirms there is no hidden assumption
    # tying elapsed time to a record count inside the function
    # itself; a non-integer "years" value is used exactly as given.
    result = calculate_cagr("revenue", 80, 120, 2.5)

    expected = (120 / 80) ** (1 / 2.5) - 1

    assert result["status"] == "OK"
    assert result["value"] == pytest.approx(expected)


# ============================================================
# YoY COMPARABLE-PERIOD SELECTION (Round AW-1)
#
# Part A finding: nothing in derived_metrics.py or this test file
# constrains "history" to annual-only observations. The
# {period_end, value} record shape is frequency-agnostic (no
# fiscal-period label, no period_start, no observation-frequency
# field anywhere in the interface), and _calculate_history_yoy()'s
# own docstring describes its goal as finding "the two most-recent
# historical records whose period_end dates are approximately one
# year apart" -- it does not say "the two most recent records,
# which must happen to be approximately one year apart". Round AW's
# implementation conflated those two things: it only ever examined
# valid_records[0] and valid_records[1] (the two most recent by
# date, full stop) and rejected the pair if THAT SPECIFIC pair
# wasn't ~1 year apart, even when a genuine prior-year comparator
# existed further back in a longer (e.g. quarterly) history list.
# That is a real gap against the function's own stated contract,
# not a deliberate annual-only scope limitation -- so this round
# fixes _calculate_history_yoy() to search the full valid-record
# list for the best eligible prior-year comparator to the LATEST
# observation, rather than only ever looking at the second-most-
# recent record.
# ============================================================

def test_history_yoy_quarterly_history_finds_prior_year_comparator():
    # Part B.1: quarterly history where the two most-recent records
    # are only ~1 quarter apart, but a genuine prior-year comparator
    # (exactly 365 days before the latest observation) exists
    # further back in the list. Pre-fix (Round AW) behavior: INVALID,
    # because the pre-fix implementation only ever compares
    # valid_records[0] (2025-03-31) against valid_records[1]
    # (2024-12-31), which are ~90 days apart, and never looks further
    # back. The correct result -- once the implementation searches
    # the whole list -- is a 50% YoY from 2024-03-31:100 to
    # 2025-03-31:150.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2024-03-31", "value": 100},
            {"period_end": "2024-06-30", "value": 110},
            {"period_end": "2024-09-30", "value": 120},
            {"period_end": "2024-12-31", "value": 130},
            {"period_end": "2025-03-31", "value": 150},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "OK"
    assert yoy["value"] == pytest.approx(0.50)


def test_history_yoy_quarterly_history_input_order_unaffected():
    # Part B.4 (for quarterly history specifically): shuffling the
    # same quarterly records must not change which pair is selected.
    normalized = make_normalized_fixture()

    shuffled_history = [
        {"period_end": "2024-12-31", "value": 130},
        {"period_end": "2024-03-31", "value": 100},
        {"period_end": "2025-03-31", "value": 150},
        {"period_end": "2024-09-30", "value": 120},
        {"period_end": "2024-06-30", "value": 110},
    ]

    result = calculate_derived_metrics(
        normalized,
        revenue_history=shuffled_history,
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "OK"
    assert yoy["value"] == pytest.approx(0.50)


def test_history_yoy_quarterly_history_without_eligible_comparator_stays_invalid():
    # Part B.3: quarterly history where NO record is actually ~1 year
    # before the latest observation (the history simply doesn't reach
    # back far enough). The latest observation (2025-03-31) must not
    # have the immediately preceding quarter (2024-12-31, ~90 days
    # apart) silently substituted for a real prior-year comparator.
    # This must stay INVALID both before and after the Round AW-1
    # fix -- it is a preservation check, not a demonstrated defect.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2024-09-30", "value": 120},
            {"period_end": "2024-12-31", "value": 130},
            {"period_end": "2025-03-31", "value": 150},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "INVALID"
    assert yoy["value"] is None


def test_history_yoy_annual_history_picks_closest_prior_year_not_oldest():
    # Part B.2 (annual regression, extended): with more than two
    # annual observations, the latest record must be compared against
    # the CLOSEST eligible prior-year comparator (2024-12-31, ~365
    # days back), not an older one further back (2022-12-31 or
    # 2023-12-31) that also happens to be reachable via repeated
    # ~1-year steps. This guards against a fix that searches the list
    # but picks the wrong (non-closest) eligible candidate.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2022-12-31", "value": 50},
            {"period_end": "2023-12-31", "value": 65},
            {"period_end": "2024-12-31", "value": 100},
            {"period_end": "2025-12-31", "value": 120},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "OK"
    assert yoy["value"] == pytest.approx(0.20)


# ============================================================
# YoY DATE-TOLERANCE SEMANTICS AND FALSE-POSITIVE AUDIT (Round AW-2)
#
# Part A finding (from direct inspection of derived_metrics.py's
# candidate-selection check --
# "_YOY_MIN_DAYS_APART <= days_apart <= _YOY_MAX_DAYS_APART" -- and of
# candidate ranking by "(abs(days_apart - 365), days_apart)"):
#
#   - Both tolerance endpoints are INCLUSIVE: a candidate exactly 300
#     or exactly 430 days from the latest observation is accepted;
#     299 or 431 days is rejected. The tests below exercise this
#     boundary directly, rather than merely asserting it from reading
#     the source.
#   - 364, 365, and 366 days are all inside the window and are all
#     individually sufficient to produce an OK result when no closer
#     candidate exists.
#   - Tie-breaking IS deterministic, but only as an artifact of
#     Python's stable sort over "(abs(days_apart - 365), days_apart)"
#     plus the fact that "days_apart" is a date delta -- two distinct
#     real period_end values essentially never produce identical
#     "days_apart" unless they are themselves date-duplicates. When
#     two candidates are merely close to (but not exactly at) the
#     365-day mark on opposite sides, the one with the SMALLER raw
#     "days_apart" wins the tie on distance-from-365, which is a
#     specific, verifiable rule (not merely "deterministic" in the
#     abstract) -- see
#     test_history_yoy_equidistant_candidates_break_tie_toward_nearer_date
#     below.
#
#   What the implementation-verified behavior above does NOT, and
#   cannot, establish: that a candidate accepted by this window is
#   actually a valid year-over-year comparator in the accounting
#   sense (the same fiscal-period type -- e.g. both annual, or both
#   the "same" fiscal quarter -- as the latest observation). The
#   {period_end, value} interface carries no period_start, duration,
#   or fiscal-period/frequency label (Round AW's own docstring already
#   says this; Round AW-1 did not change it). A record 300-430 days
#   before the latest observation is only known to be APPROXIMATELY
#   one calendar year earlier by date arithmetic -- it is a heuristic
#   anniversary proxy, not proof that the two periods are comparable.
#   This limitation is architectural (the input has no way to express
#   fiscal-period identity) and is not something a tolerance-window
#   adjustment, alone, can fix. See Part C in the round report for the
#   full assessment of whether 300-430 is "justified" given this.
# ============================================================

def test_history_yoy_boundary_299_days_is_invalid():
    # Part A: one day outside the window (just below the minimum) is
    # rejected. 2025-03-07 is exactly 299 days before 2025-12-31.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2025-03-07", "value": 100},
            {"period_end": "2025-12-31", "value": 150},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "INVALID"
    assert yoy["value"] is None


def test_history_yoy_boundary_300_days_is_ok_inclusive():
    # Part A: the minimum (300 days) is inclusive and accepted.
    # 2025-03-06 is exactly 300 days before 2025-12-31.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2025-03-06", "value": 100},
            {"period_end": "2025-12-31", "value": 150},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "OK"
    assert yoy["value"] == pytest.approx(0.50)


def test_history_yoy_boundary_430_days_is_ok_inclusive():
    # Part A: the maximum (430 days) is inclusive and accepted.
    # 2024-10-27 is exactly 430 days before 2025-12-31.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2024-10-27", "value": 100},
            {"period_end": "2025-12-31", "value": 150},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "OK"
    assert yoy["value"] == pytest.approx(0.50)


def test_history_yoy_boundary_431_days_is_invalid():
    # Part A: one day outside the window (just above the maximum) is
    # rejected. 2024-10-26 is exactly 431 days before 2025-12-31.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2024-10-26", "value": 100},
            {"period_end": "2025-12-31", "value": 150},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "INVALID"
    assert yoy["value"] is None


def test_history_yoy_near_anniversary_days_364_365_366_all_accepted():
    # Part A: 364, 365, and 366 days apart are each individually
    # sufficient to produce OK when it is the only candidate -- the
    # window is not narrowed to exactly 365.
    normalized = make_normalized_fixture()

    for previous_period_end, days_apart in (
        ("2025-01-01", 364),
        ("2024-12-31", 365),
        ("2024-12-30", 366),
    ):
        result = calculate_derived_metrics(
            normalized,
            revenue_history=[
                {"period_end": previous_period_end, "value": 100},
                {"period_end": "2025-12-31", "value": 150},
            ],
        )

        yoy = result["derived_metrics"]["revenue_yoy"]

        assert yoy["status"] == "OK", (
            f"expected OK for a candidate {days_apart} days apart"
        )
        assert yoy["value"] == pytest.approx(0.50)


def test_history_yoy_accepts_date_only_approximate_anniversary_without_fiscal_proof():
    # Part B.1: a candidate 310 days before the latest observation
    # falls inside the tolerance window and IS accepted under the
    # current, existing contract -- the {period_end, value} interface
    # has no period_start/duration/fiscal-label field with which to
    # confirm (or deny) that the two records represent comparable
    # fiscal periods. This test does NOT assert the case is invalid
    # merely because 310 days is an "unusual" anniversary gap -- per
    # the round's own instruction, that would be inventing certainty
    # the input cannot support. It documents the heuristic's actual,
    # supported behavior: date-proximity alone is accepted as an
    # approximate-anniversary proxy, which is a known limitation (see
    # the module-level docstring and Part C of the round report), not
    # a defect this round corrects.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2025-02-24", "value": 100},  # 310 days before
            {"period_end": "2025-12-31", "value": 150},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "OK"
    assert yoy["value"] == pytest.approx(0.50)


def test_history_yoy_multiple_in_window_candidates_prefers_closer_to_365_regardless_of_type():
    # Part B.2 and B.4: two prior observations both fall inside the
    # tolerance window -- one 310 days back, one 400 days back. The
    # implementation has no way to know which one (if either) is
    # actually the "same" fiscal-period type as the latest
    # observation; it picks whichever is numerically closer to 365
    # days, with no regard for period type. Here 400 days (distance
    # 35) is closer to 365 than 310 days (distance 55), so the
    # 400-day candidate wins -- documenting that "closest to 365"
    # is a pure date-arithmetic rule, not a fiscal-period match.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2025-02-24", "value": 999},  # 310 days before (farther from 365)
            {"period_end": "2024-11-26", "value": 100},  # 400 days before (closer to 365)
            {"period_end": "2025-12-31", "value": 150},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "OK"
    assert yoy["value"] == pytest.approx(0.50)


def test_history_yoy_equidistant_candidates_break_tie_toward_nearer_date():
    # Part B.3: two candidates are equally distant from the 365-day
    # mark on opposite sides (350 days apart => distance 15; 380 days
    # apart => distance 15). The implementation's documented
    # tie-break is the smaller raw "days_apart", i.e. the
    # chronologically NEARER candidate -- so the 350-day candidate
    # must win, not the 380-day one.
    normalized = make_normalized_fixture()

    result = calculate_derived_metrics(
        normalized,
        revenue_history=[
            {"period_end": "2025-01-15", "value": 100},  # 350 days before (distance 15, nearer)
            {"period_end": "2024-12-16", "value": 999},  # 380 days before (distance 15, farther)
            {"period_end": "2025-12-31", "value": 150},
        ],
    )

    yoy = result["derived_metrics"]["revenue_yoy"]

    assert yoy["status"] == "OK"
    assert yoy["value"] == pytest.approx(0.50)