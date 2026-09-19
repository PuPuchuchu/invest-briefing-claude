from __future__ import annotations

from datetime import date, datetime
from typing import Any


# ============================================================
# CONFIGURATION
# ============================================================

SCHEMA_VERSION = "growth_v0.1"

GROWTH_METRICS = [
    "revenue_yoy",
    "operating_income_yoy",
    "eps_yoy",
    "fcf_yoy",
    "revenue_cagr_3y",
]

GROWTH_WEIGHTS = {
    "revenue_yoy": 0.25,
    "operating_income_yoy": 0.20,
    "eps_yoy": 0.25,
    "fcf_yoy": 0.20,
    "revenue_cagr_3y": 0.10,
}

MIN_VALID_COMPONENTS = 3


# ============================================================
# GENERIC HELPERS
# ============================================================

def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _round_growth(value: float) -> float:
    """
    Normalize floating-point noise.

    Example:
        120 / 100 - 1
        -> 0.19999999999999996

    becomes:
        0.2
    """
    return round(float(value), 10)


def _build_ok_metric(
    metric: str,
    value: float,
    source: list[str] | None = None,
) -> dict[str, Any]:
    result = {
        "status": "OK",
        "metric": metric,
        "value": value,
    }

    if source:
        result["source"] = source

    return result


def _build_missing_metric(
    metric: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "status": "MISSING",
        "metric": metric,
        "value": None,
        "reason": reason,
    }


def _build_invalid_metric(
    metric: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "status": "INVALID",
        "metric": metric,
        "value": None,
        "reason": reason,
    }


def _date_string(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, str):
        return value

    return None


def _year_value(record: dict[str, Any]) -> int | None:
    value = record.get("fy")

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None

    return None


def _quarter_value(record: dict[str, Any]) -> str | None:
    value = record.get("quarter")

    if value is None:
        return None

    return str(value)


def _record_period_end(
    record: dict[str, Any],
) -> str | None:
    for key in ("period_end", "end", "date"):
        value = _date_string(record.get(key))

        if value:
            return value

    return None


# ============================================================
# HISTORY STRUCTURE HELPERS
# ============================================================

def _extract_history_records(
    history: Any,
    period: str = "quarterly",
) -> list[dict[str, Any]]:
    """
    Accept both:

        [
            {...},
            {...},
        ]

    and Historical Extraction's:

        {
            "status": "OK",
            "quarterly": [...],
            "annual": [...],
        }
    """

    if isinstance(history, dict):
        records = history.get(period, [])

    elif isinstance(history, list):
        records = history

    else:
        return []

    if not isinstance(records, list):
        return []

    return records


def _valid_history_records(
    history: Any,
    period: str = "quarterly",
) -> list[dict[str, Any]]:
    records = _extract_history_records(
        history,
        period=period,
    )

    result = []

    for record in records:
        if not isinstance(record, dict):
            continue

        if record.get("status") not in (None, "OK"):
            continue

        if not _is_number(record.get("value")):
            continue

        if _record_period_end(record) is None:
            continue

        result.append(record)

    return result


# ============================================================
# GROWTH CALCULATIONS
# ============================================================

def calculate_positive_yoy(
    metric_name: str,
    current: Any,
    previous: Any,
) -> dict[str, Any]:
    """
    Conventional YoY calculation for metrics where both
    current and prior values must be positive.

    Used for:
        - EPS
        - FCF
    """

    if not _is_number(current) or not _is_number(previous):
        return _build_invalid_metric(
            metric_name,
            "Current and previous values must be numeric.",
        )

    if previous <= 0:
        return _build_invalid_metric(
            metric_name,
            "Previous value must be greater than zero "
            "for conventional YoY calculation.",
        )

    if current <= 0:
        return _build_invalid_metric(
            metric_name,
            "Current value must be greater than zero "
            "for conventional YoY calculation.",
        )

    growth = _round_growth(
        (current / previous) - 1.0
    )

    return _build_ok_metric(
        metric_name,
        growth,
    )


def calculate_standard_yoy(
    metric_name: str,
    current: Any,
    previous: Any,
) -> dict[str, Any]:
    """
    Standard YoY calculation.

    Used for:
        - Revenue
        - Operating Income

    Negative growth is allowed.
    """

    if not _is_number(current) or not _is_number(previous):
        return _build_invalid_metric(
            metric_name,
            "Current and previous values must be numeric.",
        )

    if previous == 0:
        return _build_invalid_metric(
            metric_name,
            "Previous value cannot be zero.",
        )

    growth = _round_growth(
        (current / previous) - 1.0
    )

    return _build_ok_metric(
        metric_name,
        growth,
    )


def calculate_cagr_3y(
    metric_name: str,
    start_value: Any,
    end_value: Any,
    years: int = 3,
) -> dict[str, Any]:
    """
    Calculate CAGR.
    """

    if not _is_number(start_value) or not _is_number(end_value):
        return _build_invalid_metric(
            metric_name,
            "Start and end values must be numeric.",
        )

    if start_value <= 0:
        return _build_invalid_metric(
            metric_name,
            "Starting value must be greater than zero.",
        )

    if end_value < 0:
        return _build_invalid_metric(
            metric_name,
            "Ending value cannot be negative.",
        )

    if years <= 0:
        return _build_invalid_metric(
            metric_name,
            "Years must be greater than zero.",
        )

    cagr = (
        end_value / start_value
    ) ** (1.0 / years) - 1.0

    return _build_ok_metric(
        metric_name,
        _round_growth(cagr),
    )


# ============================================================
# QUARTER PAIR SELECTION
# ============================================================

def _select_latest_quarter_pair(
    history: Any,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """
    Select latest quarterly observation and prior-year
    comparable quarter.

    Priority:
        1. Same quarter + prior fiscal year
        2. Same calendar year-offset period
    """

    records = _valid_history_records(
        history,
        period="quarterly",
    )

    if not records:
        return None, None

    records = sorted(
        records,
        key=lambda x: _record_period_end(x) or "",
    )

    latest = records[-1]

    latest_quarter = _quarter_value(latest)
    latest_fy = _year_value(latest)

    candidates = []

    for record in records[:-1]:
        record_quarter = _quarter_value(record)
        record_fy = _year_value(record)

        if (
            latest_quarter is not None
            and record_quarter == latest_quarter
            and latest_fy is not None
            and record_fy == latest_fy - 1
        ):
            candidates.append(record)

    if candidates:
        previous = sorted(
            candidates,
            key=lambda x: _record_period_end(x) or "",
        )[-1]

        return latest, previous

    latest_end = _record_period_end(latest)

    if not latest_end:
        return latest, None

    try:
        latest_date = date.fromisoformat(
            latest_end
        )
    except ValueError:
        return latest, None

    target_year = latest_date.year - 1

    year_candidates = []

    for record in records[:-1]:
        period_end = _record_period_end(record)

        if not period_end:
            continue

        try:
            record_date = date.fromisoformat(
                period_end
            )
        except ValueError:
            continue

        if record_date.year == target_year:
            year_candidates.append(record)

    if not year_candidates:
        return latest, None

    previous = min(
        year_candidates,
        key=lambda x: abs(
            (
                date.fromisoformat(
                    _record_period_end(x)
                )
                - latest_date
            ).days
        ),
    )

    return latest, previous


# ============================================================
# THREE-YEAR CAGR SELECTION
# ============================================================

def _select_three_year_revenue_pair(
    history: Any,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """
    Select annual revenue observations exactly three fiscal
    years apart.
    """

    records = _valid_history_records(
        history,
        period="annual",
    )

    if not records:
        return None, None

    records = sorted(
        records,
        key=lambda x: _year_value(x) or 0,
    )

    latest = records[-1]
    latest_fy = _year_value(latest)

    if latest_fy is None:
        return None, None

    target_fy = latest_fy - 3

    candidates = [
        record
        for record in records
        if _year_value(record) == target_fy
    ]

    if not candidates:
        return latest, None

    previous = sorted(
        candidates,
        key=lambda x: _record_period_end(x) or "",
    )[-1]

    return latest, previous


# ============================================================
# FCF HISTORY
# ============================================================

def _build_fcf_history(
    cfo_history: Any,
    capex_history: Any,
) -> list[dict[str, Any]]:
    """
    FCF = CFO + CapEx

    Historical CapEx is expected to be negative.
    """

    cfo_records = _valid_history_records(
        cfo_history,
        period="quarterly",
    )

    capex_records = _valid_history_records(
        capex_history,
        period="quarterly",
    )

    if not cfo_records or not capex_records:
        return []

    capex_map = {}

    for record in capex_records:
        key = (
            _record_period_end(record),
            _quarter_value(record),
            _year_value(record),
            record.get("period_type"),
        )

        capex_map[key] = record

    result = []

    for cfo in cfo_records:
        key = (
            _record_period_end(cfo),
            _quarter_value(cfo),
            _year_value(cfo),
            cfo.get("period_type"),
        )

        capex = capex_map.get(key)

        if capex is None:
            continue

        fcf = (
            cfo["value"]
            + capex["value"]
        )

        result.append(
            {
                "status": "OK",
                "period_end": _record_period_end(cfo),
                "quarter": _quarter_value(cfo),
                "fy": _year_value(cfo),
                "period_type": cfo.get(
                    "period_type"
                ),
                "value": fcf,
            }
        )

    return result


# ============================================================
# COMPONENT CALCULATIONS
# ============================================================

def _calculate_history_yoy(
    metric_name: str,
    history: Any,
    positive_only: bool,
) -> dict[str, Any]:
    latest, previous = _select_latest_quarter_pair(
        history
    )

    if latest is None:
        return _build_missing_metric(
            metric_name,
            "No usable quarterly history found.",
        )

    if previous is None:
        return _build_missing_metric(
            metric_name,
            "No prior-year comparable quarter found.",
        )

    if positive_only:
        result = calculate_positive_yoy(
            metric_name,
            latest["value"],
            previous["value"],
        )
    else:
        result = calculate_standard_yoy(
            metric_name,
            latest["value"],
            previous["value"],
        )

    if result["status"] == "OK":
        result["source"] = [
            "historical",
            "quarterly",
        ]

        result["current_period_end"] = (
            _record_period_end(latest)
        )

        result["previous_period_end"] = (
            _record_period_end(previous)
        )

    return result


def _calculate_revenue_cagr(
    revenue_history: Any,
) -> dict[str, Any]:
    latest, previous = (
        _select_three_year_revenue_pair(
            revenue_history
        )
    )

    if latest is None:
        return _build_missing_metric(
            "revenue_cagr_3y",
            "No usable annual revenue history found.",
        )

    if previous is None:
        return _build_missing_metric(
            "revenue_cagr_3y",
            "Exact three-year annual revenue history "
            "is not available.",
        )

    result = calculate_cagr_3y(
        "revenue_cagr_3y",
        previous["value"],
        latest["value"],
        years=3,
    )

    if result["status"] == "OK":
        result["source"] = [
            "historical",
            "annual",
        ]

        result["start_period_end"] = (
            _record_period_end(previous)
        )

        result["end_period_end"] = (
            _record_period_end(latest)
        )

    return result


# ============================================================
# STAR RATING
# ============================================================

def _rate_growth_value(
    metric_name: str,
    value: Any,
) -> dict[str, Any]:
    if not _is_number(value):
        return {
            "status": "INVALID",
            "metric": metric_name,
            "value": None,
            "stars": None,
        }

    value = _round_growth(value)

    if metric_name == "revenue_yoy":
        thresholds = (
            0.00,
            0.05,
            0.10,
            0.20,
        )

    elif metric_name == "operating_income_yoy":
        thresholds = (
            0.00,
            0.05,
            0.15,
            0.30,
        )

    elif metric_name == "eps_yoy":
        thresholds = (
            0.00,
            0.05,
            0.15,
            0.30,
        )

    elif metric_name == "fcf_yoy":
        thresholds = (
            0.00,
            0.05,
            0.15,
            0.30,
        )

    elif metric_name == "revenue_cagr_3y":
        thresholds = (
            0.00,
            0.05,
            0.10,
            0.20,
        )

    else:
        return {
            "status": "INVALID",
            "metric": metric_name,
            "value": value,
            "stars": None,
            "reason": "Unknown growth metric.",
        }

    if value < thresholds[0]:
        stars = 1
    elif value < thresholds[1]:
        stars = 2
    elif value < thresholds[2]:
        stars = 3
    elif value < thresholds[3]:
        stars = 4
    else:
        stars = 5

    return {
        "status": "OK",
        "metric": metric_name,
        "value": value,
        "stars": stars,
    }


# ------------------------------------------------------------
# Public rating functions
# ------------------------------------------------------------

def rate_revenue_yoy(
    value: Any,
) -> dict[str, Any]:
    return _rate_growth_value(
        "revenue_yoy",
        value,
    )


def rate_operating_income_yoy(
    value: Any,
) -> dict[str, Any]:
    return _rate_growth_value(
        "operating_income_yoy",
        value,
    )


def rate_eps_yoy(
    value: Any,
) -> dict[str, Any]:
    return _rate_growth_value(
        "eps_yoy",
        value,
    )


def rate_fcf_yoy(
    value: Any,
) -> dict[str, Any]:
    return _rate_growth_value(
        "fcf_yoy",
        value,
    )


def rate_revenue_cagr_3y(
    value: Any,
) -> dict[str, Any]:
    return _rate_growth_value(
        "revenue_cagr_3y",
        value,
    )


# ============================================================
# WARNINGS
# ============================================================

def detect_growth_warnings(
    components: dict[str, dict[str, Any]],
) -> list[str]:
    """
    Return machine-readable warning codes.

    Codes:
        revenue_operating_income_divergence
        revenue_fcf_divergence
        eps_revenue_divergence
    """

    warnings: list[str] = []

    revenue = components.get(
        "revenue_yoy",
        {},
    )

    operating_income = components.get(
        "operating_income_yoy",
        {},
    )

    eps = components.get(
        "eps_yoy",
        {},
    )

    fcf = components.get(
        "fcf_yoy",
        {},
    )

    revenue_value = revenue.get("value")
    operating_income_value = (
        operating_income.get("value")
    )
    eps_value = eps.get("value")
    fcf_value = fcf.get("value")

    if (
        _is_number(revenue_value)
        and _is_number(operating_income_value)
        and revenue_value >= 0.10
        and operating_income_value < 0
    ):
        warnings.append(
            "revenue_operating_income_divergence"
        )

    if (
        _is_number(revenue_value)
        and _is_number(fcf_value)
        and revenue_value >= 0.10
        and fcf_value < 0
    ):
        warnings.append(
            "revenue_fcf_divergence"
        )

    if (
        _is_number(eps_value)
        and _is_number(revenue_value)
        and eps_value >= 0.30
        and revenue_value < 0.05
    ):
        warnings.append(
            "eps_revenue_divergence"
        )

    return warnings


# ============================================================
# WEIGHTED SCORE
# ============================================================

def _calculate_weighted_score(
    components: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    weighted_sum = 0.0
    weight_sum = 0.0
    valid_components = 0

    for metric_name, weight in GROWTH_WEIGHTS.items():
        component = components.get(
            metric_name,
            {},
        )

        if (
            component.get("status") != "OK"
            or not _is_number(
                component.get("stars")
            )
        ):
            continue

        weighted_sum += (
            component["stars"] * weight
        )

        weight_sum += weight
        valid_components += 1

    if valid_components < MIN_VALID_COMPONENTS:
        return {
            "status": "MISSING",
            "score": None,
            "stars": None,
            "available_components": valid_components,
            "valid_components": valid_components,
            "total_components": len(
                GROWTH_METRICS
            ),
            "coverage": round(
                valid_components
                / len(GROWTH_METRICS),
                4,
            ),
        }

    score = weighted_sum / weight_sum

    stars = int(
        score + 0.5
    )

    stars = max(
        1,
        min(5, stars),
    )

    return {
        "status": "OK",
        "score": round(score, 4),
        "stars": stars,
        "available_components": valid_components,
        "valid_components": valid_components,
        "total_components": len(
            GROWTH_METRICS
        ),
        "coverage": round(
            valid_components
            / len(GROWTH_METRICS),
            4,
        ),
    }


# ============================================================
# MAIN CALCULATION
# ============================================================

def calculate_growth(
    normalized: dict[str, Any],
    derived: dict[str, Any],
    historical: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Growth Factor v0.1

    Components:
        Revenue YoY              25%
        Operating Income YoY     20%
        EPS YoY                  25%
        FCF YoY                  20%
        Revenue 3Y CAGR          10%

    Minimum valid components:
        3 / 5
    """

    if not isinstance(normalized, dict):
        raise TypeError(
            "normalized must be a dictionary."
        )

    if not isinstance(derived, dict):
        raise TypeError(
            "derived must be a dictionary."
        )

    if historical is None:
        historical = {}

    if not isinstance(historical, dict):
        raise TypeError(
            "historical must be a dictionary."
        )

    ticker = normalized.get("ticker")

    revenue_history = historical.get(
        "revenue",
        {},
    )

    operating_income_history = historical.get(
        "operating_income",
        {},
    )

    eps_history = historical.get(
        "diluted_eps",
        {},
    )

    cfo_history = historical.get(
        "cfo",
        {},
    )

    capex_history = historical.get(
        "capex",
        {},
    )

    components: dict[str, dict[str, Any]] = {}

    # --------------------------------------------------------
    # Revenue YoY
    # --------------------------------------------------------

    components["revenue_yoy"] = (
        _calculate_history_yoy(
            "revenue_yoy",
            revenue_history,
            positive_only=False,
        )
    )

    # --------------------------------------------------------
    # Operating Income YoY
    # --------------------------------------------------------

    components["operating_income_yoy"] = (
        _calculate_history_yoy(
            "operating_income_yoy",
            operating_income_history,
            positive_only=False,
        )
    )

    # --------------------------------------------------------
    # EPS YoY
    # --------------------------------------------------------

    components["eps_yoy"] = (
        _calculate_history_yoy(
            "eps_yoy",
            eps_history,
            positive_only=True,
        )
    )

    # --------------------------------------------------------
    # FCF YoY
    # --------------------------------------------------------

    fcf_history = _build_fcf_history(
        cfo_history,
        capex_history,
    )

    components["fcf_yoy"] = (
        _calculate_history_yoy(
            "fcf_yoy",
            fcf_history,
            positive_only=True,
        )
    )

    # --------------------------------------------------------
    # Revenue 3Y CAGR
    # --------------------------------------------------------

    components["revenue_cagr_3y"] = (
        _calculate_revenue_cagr(
            revenue_history
        )
    )

    # --------------------------------------------------------
    # Apply ratings
    # --------------------------------------------------------

    for metric_name in GROWTH_METRICS:
        component = components[
            metric_name
        ]

        if component.get("status") != "OK":
            component["stars"] = None
            continue

        rated = _rate_growth_value(
            metric_name,
            component.get("value"),
        )

        component["stars"] = rated.get(
            "stars"
        )

    # --------------------------------------------------------
    # Warnings
    # --------------------------------------------------------

    warnings = detect_growth_warnings(
        components
    )

    # --------------------------------------------------------
    # Score
    # --------------------------------------------------------

    score_result = _calculate_weighted_score(
        components
    )

    # --------------------------------------------------------
    # Limitations
    # --------------------------------------------------------

    limitations: dict[str, Any] = {}

    if (
        components["eps_yoy"].get("status")
        != "OK"
    ):
        limitations["eps_yoy"] = (
            "EPS growth requires positive current "
            "and prior-year comparable-quarter values."
        )

    if (
        components["fcf_yoy"].get("status")
        != "OK"
    ):
        limitations["fcf_yoy"] = (
            "FCF growth requires positive current "
            "and prior-year comparable-quarter values."
        )

    if (
        components["revenue_cagr_3y"].get(
            "status"
        )
        != "OK"
    ):
        limitations["revenue_cagr_3y"] = (
            "Revenue CAGR requires exact "
            "three-fiscal-year annual history."
        )

    result = {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "growth": score_result,
        "components": components,
        "warnings": warnings,
        "limitations": limitations,
    }

    # --------------------------------------------------------
    # Self-validation (2026-09-19): validate_growth() below already
    # existed and was already exercised by tests/test_growth.py, but
    # was never actually called from within calculate_growth() itself
    # -- so a real bug that produced malformed output would have gone
    # uncaught outside of the specific fixtures the test suite happens
    # to cover. A validate_growth() failure here means this function's
    # OWN output doesn't match its own documented schema, which is a
    # programming defect, not a data-availability question (those are
    # already handled per-component via OK/MISSING status) -- so it
    # fails loudly rather than silently returning malformed data.
    validation_failures = validate_growth(result)

    if validation_failures:
        raise ValueError(
            f"calculate_growth() produced invalid output for "
            f"{ticker!r}: {validation_failures}"
        )

    return result


# ============================================================
# VALIDATION
# ============================================================

def validate_growth(
    data: dict[str, Any],
) -> list[str]:
    """
    Validate Growth Factor output.

    Returns:
        [] when valid
        list[str] containing field paths when invalid
    """

    errors: list[str] = []

    if not isinstance(data, dict):
        return ["growth"]

    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            "schema_version"
        )

    if "ticker" not in data:
        errors.append(
            "ticker"
        )

    growth = data.get("growth")

    if not isinstance(growth, dict):
        errors.append(
            "growth"
        )
    else:
        status = growth.get("status")

        if status not in (
            "OK",
            "MISSING",
        ):
            errors.append(
                "growth.status"
            )

        if status == "OK":
            if not _is_number(
                growth.get("score")
            ):
                errors.append(
                    "growth.score"
                )

            stars = growth.get("stars")

            if not isinstance(
                stars,
                int,
            ):
                errors.append(
                    "growth.stars"
                )
            elif stars < 1 or stars > 5:
                errors.append(
                    "growth.stars"
                )

            available = growth.get(
                "available_components"
            )

            if not isinstance(
                available,
                int,
            ):
                errors.append(
                    "growth.available_components"
                )

    components = data.get(
        "components"
    )

    if not isinstance(
        components,
        dict,
    ):
        errors.append(
            "components"
        )
    else:
        for metric_name in GROWTH_METRICS:
            if metric_name not in components:
                errors.append(
                    f"components.{metric_name}"
                )
                continue

            component = components[
                metric_name
            ]

            if not isinstance(
                component,
                dict,
            ):
                errors.append(
                    f"components.{metric_name}"
                )
                continue

            status = component.get(
                "status"
            )

            if status not in (
                "OK",
                "MISSING",
                "INVALID",
            ):
                errors.append(
                    f"components.{metric_name}.status"
                )

            if status == "OK":
                if not _is_number(
                    component.get("value")
                ):
                    errors.append(
                        f"components.{metric_name}.value"
                    )

                stars = component.get(
                    "stars"
                )

                if not isinstance(
                    stars,
                    int,
                ):
                    errors.append(
                        f"components.{metric_name}.stars"
                    )

                elif stars < 1 or stars > 5:
                    errors.append(
                        f"components.{metric_name}.stars"
                    )

    warnings = data.get(
        "warnings"
    )

    if not isinstance(
        warnings,
        list,
    ):
        errors.append(
            "warnings"
        )

    limitations = data.get(
        "limitations"
    )

    if not isinstance(
        limitations,
        dict,
    ):
        errors.append(
            "limitations"
        )

    return errors