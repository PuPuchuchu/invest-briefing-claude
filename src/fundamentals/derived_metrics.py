from __future__ import annotations

from math import isfinite
from typing import Any


# ============================================================
# CONFIGURATION
# ============================================================

SCHEMA_VERSION = "derived_metrics_v0.1"


# ============================================================
# BASIC HELPERS
# ============================================================

def _is_number(value: Any) -> bool:
    """
    Return True only for finite int/float values.
    bool is intentionally excluded because bool is a subclass of int.
    """
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(float(value))
    )


def _build_ok_metric(
    metric_name: str,
    value: float | int,
    source_metrics: list[str] | None = None,
    reason: str | None = None,
) -> dict:
    result = {
        "metric": metric_name,
        "status": "OK",
        "value": value,
    }

    if source_metrics is not None:
        result["source_metrics"] = source_metrics

    if reason is not None:
        result["reason"] = reason

    return result


def _build_missing_metric(
    metric_name: str,
    reason: str,
    source_metrics: list[str] | None = None,
) -> dict:
    result = {
        "metric": metric_name,
        "status": "MISSING",
        "value": None,
        "reason": reason,
    }

    if source_metrics is not None:
        result["source_metrics"] = source_metrics

    return result


def _build_invalid_metric(
    metric_name: str,
    reason: str,
    source_metrics: list[str] | None = None,
) -> dict:
    result = {
        "metric": metric_name,
        "status": "INVALID",
        "value": None,
        "reason": reason,
    }

    if source_metrics is not None:
        result["source_metrics"] = source_metrics

    return result


# ============================================================
# GROWTH
# ============================================================

def calculate_yoy(
    metric_name: str,
    current_value: Any,
    previous_value: Any,
) -> dict:
    """
    Calculate year-over-year growth.

    Formula:
        (current / previous) - 1

    Returned value is decimal form.
    Example:
        120 / 100 - 1 = 0.20
    """

    if not _is_number(current_value):
        return _build_missing_metric(
            metric_name=f"{metric_name}_yoy",
            reason="Current value is missing or non-numeric.",
            source_metrics=[metric_name],
        )

    if not _is_number(previous_value):
        return _build_missing_metric(
            metric_name=f"{metric_name}_yoy",
            reason="Previous value is missing or non-numeric.",
            source_metrics=[metric_name],
        )

    if float(previous_value) == 0:
        return _build_invalid_metric(
            metric_name=f"{metric_name}_yoy",
            reason="Previous value is zero; YoY cannot be calculated.",
            source_metrics=[metric_name],
        )

    value = float(current_value) / float(previous_value) - 1.0

    return _build_ok_metric(
        metric_name=f"{metric_name}_yoy",
        value=value,
        source_metrics=[metric_name],
    )


# ============================================================
# CAGR
# ============================================================

def calculate_cagr(
    metric_name: str,
    start_value: Any,
    end_value: Any,
    years: Any,
) -> dict:
    """
    Calculate CAGR.

    Formula:
        (end / start) ** (1 / years) - 1

    Policy:
    - years must be positive.
    - start must be strictly positive.
    - end must be non-negative.
    - CAGR is not calculated when the starting value is <= 0.
    """

    result_name = f"{metric_name}_cagr"

    if not _is_number(start_value):
        return _build_missing_metric(
            result_name,
            "Start value is missing or non-numeric.",
            [metric_name],
        )

    if not _is_number(end_value):
        return _build_missing_metric(
            result_name,
            "End value is missing or non-numeric.",
            [metric_name],
        )

    if not _is_number(years):
        return _build_missing_metric(
            result_name,
            "Years is missing or non-numeric.",
            [metric_name],
        )

    years_float = float(years)

    if years_float <= 0:
        return _build_invalid_metric(
            result_name,
            "Years must be greater than zero.",
            [metric_name],
        )

    start_float = float(start_value)
    end_float = float(end_value)

    if start_float <= 0:
        return _build_invalid_metric(
            result_name,
            "CAGR requires a strictly positive starting value.",
            [metric_name],
        )

    if end_float < 0:
        return _build_invalid_metric(
            result_name,
            "CAGR requires a non-negative ending value.",
            [metric_name],
        )

    value = (end_float / start_float) ** (1.0 / years_float) - 1.0

    return _build_ok_metric(
        result_name,
        value,
        [metric_name],
    )


# ============================================================
# MARGINS
# ============================================================

def calculate_margin(
    metric_name: str,
    numerator_value: Any,
    revenue_value: Any,
) -> dict:
    """
    Calculate a revenue-based margin.

    Formula:
        numerator / revenue

    Examples:
        operating income / revenue -> operating margin
        net income / revenue       -> net margin
        FCF / revenue              -> FCF margin
    """

    result_name = f"{metric_name}_margin"

    if not _is_number(numerator_value):
        return _build_missing_metric(
            result_name,
            "Numerator is missing or non-numeric.",
            [metric_name, "revenue"],
        )

    if not _is_number(revenue_value):
        return _build_missing_metric(
            result_name,
            "Revenue is missing or non-numeric.",
            [metric_name, "revenue"],
        )

    revenue_float = float(revenue_value)

    if revenue_float == 0:
        return _build_invalid_metric(
            result_name,
            "Revenue is zero; margin cannot be calculated.",
            [metric_name, "revenue"],
        )

    value = float(numerator_value) / revenue_float

    return _build_ok_metric(
        result_name,
        value,
        [metric_name, "revenue"],
    )


# ============================================================
# FREE CASH FLOW
# ============================================================

def calculate_fcf(
    cfo_value: Any,
    capex_value: Any,
) -> dict:
    """
    Calculate Free Cash Flow.

    SEC CapEx values are commonly represented as negative cash-flow
    values because they are cash outflows.

    Therefore:
        FCF = CFO + CapEx

    Example:
        CFO  = 25,000
        CapEx = -5,000
        FCF  = 20,000

    This function assumes the normalized CapEx convention used by
    sec_normalizer.py.
    """

    if not _is_number(cfo_value):
        return _build_missing_metric(
            "fcf",
            "CFO is missing or non-numeric.",
            ["cfo", "capex"],
        )

    if not _is_number(capex_value):
        return _build_missing_metric(
            "fcf",
            "CapEx is missing or non-numeric.",
            ["cfo", "capex"],
        )

    value = float(cfo_value) + float(capex_value)

    return _build_ok_metric(
        "fcf",
        value,
        ["cfo", "capex"],
    )


# ============================================================
# DEBT / CASH
# ============================================================

def calculate_net_debt(
    total_debt_value: Any,
    cash_value: Any,
) -> dict:
    """
    Calculate net debt.

    Formula:
        Net Debt = Total Debt - Cash
    """

    if not _is_number(total_debt_value):
        return _build_missing_metric(
            "net_debt",
            "Total debt is missing or non-numeric.",
            ["total_debt", "cash"],
        )

    if not _is_number(cash_value):
        return _build_missing_metric(
            "net_debt",
            "Cash is missing or non-numeric.",
            ["total_debt", "cash"],
        )

    value = float(total_debt_value) - float(cash_value)

    return _build_ok_metric(
        "net_debt",
        value,
        ["total_debt", "cash"],
    )


def calculate_debt_to_cash(
    total_debt_value: Any,
    cash_value: Any,
) -> dict:
    """
    Calculate debt-to-cash ratio.

    Formula:
        Total Debt / Cash
    """

    if not _is_number(total_debt_value):
        return _build_missing_metric(
            "debt_to_cash",
            "Total debt is missing or non-numeric.",
            ["total_debt", "cash"],
        )

    if not _is_number(cash_value):
        return _build_missing_metric(
            "debt_to_cash",
            "Cash is missing or non-numeric.",
            ["total_debt", "cash"],
        )

    cash_float = float(cash_value)

    if cash_float == 0:
        return _build_invalid_metric(
            "debt_to_cash",
            "Cash is zero; debt-to-cash ratio cannot be calculated.",
            ["total_debt", "cash"],
        )

    value = float(total_debt_value) / cash_float

    return _build_ok_metric(
        "debt_to_cash",
        value,
        ["total_debt", "cash"],
    )


# ============================================================
# TTM
# ============================================================

def calculate_ttm_from_quarters(
    metric_name: str,
    quarterly_values: list[Any],
) -> dict:
    """
    Calculate TTM from four standalone quarterly values.

    IMPORTANT:
    This function intentionally requires exactly four valid
    standalone-quarter values.

    It does NOT attempt to interpret YTD SEC observations.

    That prevents accidental double counting such as:
        Q1 + H1 + 9M + FY

    Such SEC duration observations are not standalone quarters.

    A separate SEC-specific TTM reconstruction layer can later
    transform YTD observations into standalone quarters.
    """

    result_name = f"{metric_name}_ttm"

    if not isinstance(quarterly_values, list):
        return _build_missing_metric(
            result_name,
            "Quarterly values must be provided as a list.",
            [metric_name],
        )

    if len(quarterly_values) != 4:
        return _build_missing_metric(
            result_name,
            "TTM requires exactly four standalone quarterly values.",
            [metric_name],
        )

    if not all(_is_number(value) for value in quarterly_values):
        return _build_missing_metric(
            result_name,
            "One or more quarterly values are missing or non-numeric.",
            [metric_name],
        )

    value = sum(float(value) for value in quarterly_values)

    return _build_ok_metric(
        result_name,
        value,
        [metric_name],
    )


# ============================================================
# TTM FROM YTD + PRIOR FY
# ============================================================

def calculate_ttm_from_ytd(
    metric_name: str,
    latest_ytd_value: Any,
    prior_fy_value: Any,
    prior_ytd_value: Any,
) -> dict:
    """
    Calculate TTM using:

        Latest YTD
        + Prior FY
        - Prior-year comparable YTD

    Formula:
        TTM = latest YTD + prior FY - prior YTD

    Example:
        Current 9M = 90
        Prior FY   = 120
        Prior 9M   = 85

        TTM = 90 + 120 - 85 = 125

    This function assumes that the three inputs represent
    comparable fiscal periods.
    """

    result_name = f"{metric_name}_ttm"

    values = [
        latest_ytd_value,
        prior_fy_value,
        prior_ytd_value,
    ]

    if not all(_is_number(value) for value in values):
        return _build_missing_metric(
            result_name,
            "One or more YTD/FY inputs are missing or non-numeric.",
            [metric_name],
        )

    value = (
        float(latest_ytd_value)
        + float(prior_fy_value)
        - float(prior_ytd_value)
    )

    return _build_ok_metric(
        result_name,
        value,
        [metric_name],
    )


# ============================================================
# COMBINED DERIVED METRICS
# ============================================================

def calculate_derived_metrics(
    normalized: dict,
    *,
    revenue_history: list[dict] | None = None,
    net_income_history: list[dict] | None = None,
    operating_income_history: list[dict] | None = None,
    cfo_history: list[dict] | None = None,
    capex_history: list[dict] | None = None,
) -> dict:
    """
    Calculate derived metrics from normalized fundamentals.

    This v0.1 function deliberately keeps historical calculations
    separate from the normalized current snapshot.

    Expected normalized structure:

        {
            "ticker": "MSFT",
            "metrics": {
                "revenue": {"status": "OK", "value": ...},
                "net_income": {"status": "OK", "value": ...},
                "operating_income": {"status": "OK", "value": ...},
                "cfo": {"status": "OK", "value": ...},
                "capex": {"status": "OK", "value": ...},
                "cash": {"status": "OK", "value": ...},
                "total_debt": {"status": "OK", "value": ...},
            }
        }

    Historical lists are reserved for the next stage of
    SEC historical extraction and should contain records such as:

        {
            "period_end": "2025-12-31",
            "value": 120000,
            ...
        }
    """

    if not isinstance(normalized, dict):
        raise TypeError("normalized must be a dict.")

    ticker = normalized.get("ticker")
    metrics = normalized.get("metrics")

    if not isinstance(metrics, dict):
        raise ValueError("normalized['metrics'] must be a dict.")

    result = {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "derived_metrics": {},
    }

    # --------------------------------------------------------
    # Current-period FCF
    # --------------------------------------------------------

    cfo = metrics.get("cfo", {})
    capex = metrics.get("capex", {})

    result["derived_metrics"]["fcf"] = calculate_fcf(
        cfo.get("value") if isinstance(cfo, dict) else None,
        capex.get("value") if isinstance(capex, dict) else None,
    )

    # --------------------------------------------------------
    # Current-period margins
    # --------------------------------------------------------

    revenue = metrics.get("revenue", {})

    revenue_value = (
        revenue.get("value")
        if isinstance(revenue, dict)
        else None
    )

    net_income = metrics.get("net_income", {})
    operating_income = metrics.get("operating_income", {})

    result["derived_metrics"]["net_margin"] = calculate_margin(
        "net_income",
        net_income.get("value")
        if isinstance(net_income, dict)
        else None,
        revenue_value,
    )

    result["derived_metrics"]["operating_margin"] = calculate_margin(
        "operating_income",
        operating_income.get("value")
        if isinstance(operating_income, dict)
        else None,
        revenue_value,
    )

    # --------------------------------------------------------
    # Current-period FCF margin
    # --------------------------------------------------------

    fcf_value = result["derived_metrics"]["fcf"].get("value")

    result["derived_metrics"]["fcf_margin"] = calculate_margin(
        "fcf",
        fcf_value,
        revenue_value,
    )

    # --------------------------------------------------------
    # Debt / Cash
    # --------------------------------------------------------

    total_debt = metrics.get("total_debt", {})
    cash = metrics.get("cash", {})

    total_debt_value = (
        total_debt.get("value")
        if isinstance(total_debt, dict)
        else None
    )

    cash_value = (
        cash.get("value")
        if isinstance(cash, dict)
        else None
    )

    result["derived_metrics"]["net_debt"] = calculate_net_debt(
        total_debt_value,
        cash_value,
    )

    result["derived_metrics"]["debt_to_cash"] = calculate_debt_to_cash(
        total_debt_value,
        cash_value,
    )

    # --------------------------------------------------------
    # Historical YoY
    # --------------------------------------------------------

    if revenue_history:
        result["derived_metrics"]["revenue_yoy"] = _calculate_history_yoy(
            "revenue",
            revenue_history,
        )

    if net_income_history:
        result["derived_metrics"]["net_income_yoy"] = _calculate_history_yoy(
            "net_income",
            net_income_history,
        )

    if operating_income_history:
        result["derived_metrics"]["operating_income_yoy"] = (
            _calculate_history_yoy(
                "operating_income",
                operating_income_history,
            )
        )

    if cfo_history:
        result["derived_metrics"]["cfo_yoy"] = _calculate_history_yoy(
            "cfo",
            cfo_history,
        )

    if capex_history:
        result["derived_metrics"]["capex_yoy"] = _calculate_history_yoy(
            "capex",
            capex_history,
        )

    # --------------------------------------------------------
    # Self-validation (2026-09-19): validate_derived_metrics() below
    # already existed and was already exercised by
    # tests/test_derived_metrics.py, but was never actually called from
    # within calculate_derived_metrics() itself -- so a real bug that
    # produced malformed output would have gone uncaught outside of the
    # specific fixtures the test suite happens to cover. A
    # validate_derived_metrics() failure here means this function's OWN
    # output doesn't match its own documented schema, which is a
    # programming defect, not a data-availability question (those are
    # already handled per-metric via OK/MISSING/INVALID status) -- so
    # it fails loudly rather than silently returning malformed data.
    validation_failures = validate_derived_metrics(result)

    if validation_failures:
        raise ValueError(
            f"calculate_derived_metrics() produced invalid output for "
            f"{ticker!r}: {validation_failures}"
        )

    return result


# ============================================================
# HISTORICAL HELPERS
# ============================================================

def _calculate_history_yoy(
    metric_name: str,
    history: list[dict],
) -> dict:
    """
    Calculate YoY from the two latest annual records.

    Records must contain:
        period_end
        value

    The list itself does not have to be sorted.
    """

    result_name = f"{metric_name}_yoy"

    if not isinstance(history, list):
        return _build_missing_metric(
            result_name,
            "History must be a list.",
            [metric_name],
        )

    valid_records = []

    for record in history:
        if not isinstance(record, dict):
            continue

        period_end = record.get("period_end")
        value = record.get("value")

        if period_end is None or not _is_number(value):
            continue

        valid_records.append(record)

    if len(valid_records) < 2:
        return _build_missing_metric(
            result_name,
            "At least two valid historical observations are required.",
            [metric_name],
        )

    valid_records.sort(
        key=lambda x: str(x["period_end"]),
        reverse=True,
    )

    current = valid_records[0]
    previous = valid_records[1]

    return calculate_yoy(
        metric_name,
        current["value"],
        previous["value"],
    )


# ============================================================
# VALIDATION
# ============================================================

def validate_derived_metrics(data: dict) -> list[str]:
    """
    Validate the basic derived-metric output schema.

    Returns:
        [] when valid
        list of failure names otherwise
    """

    failures = []

    if not isinstance(data, dict):
        return ["root"]

    required_top_level = [
        "schema_version",
        "ticker",
        "derived_metrics",
    ]

    for key in required_top_level:
        if key not in data:
            failures.append(key)

    derived_metrics = data.get("derived_metrics")

    if not isinstance(derived_metrics, dict):
        failures.append("derived_metrics")
        return failures

    for metric_name, metric in derived_metrics.items():
        if not isinstance(metric, dict):
            failures.append(metric_name)
            continue

        if "metric" not in metric:
            failures.append(f"{metric_name}.metric")

        if "status" not in metric:
            failures.append(f"{metric_name}.status")

        if "value" not in metric:
            failures.append(f"{metric_name}.value")

        status = metric.get("status")

        if status not in {"OK", "MISSING", "INVALID"}:
            failures.append(f"{metric_name}.status")

        if status == "OK":
            if not _is_number(metric.get("value")):
                failures.append(f"{metric_name}.value")

        if status in {"MISSING", "INVALID"}:
            if metric.get("value") is not None:
                failures.append(f"{metric_name}.value")

            if not metric.get("reason"):
                failures.append(f"{metric_name}.reason")

    return failures