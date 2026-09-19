from __future__ import annotations

from math import isfinite
from typing import Any


# ============================================================
# CONFIGURATION
# ============================================================

SCHEMA_VERSION = "quality_v0.1"

VALID_STATUSES = {
    "OK",
    "MISSING",
    "INVALID",
    "NOT_APPLICABLE",
}


# ============================================================
# QUALITY METRIC CONFIGURATION
# ============================================================

QUALITY_METRICS = [
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "fcf_conversion",
    "net_debt_to_fcf",
    "debt_to_cash",
    "profit_to_cash_consistency",
]


# ============================================================
# BASIC HELPERS
# ============================================================

def _is_number(value: Any) -> bool:
    """
    Return True only for finite numeric values.

    bool is intentionally excluded.
    """

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(float(value))
    )


def _build_ok_metric(
    metric_name: str,
    value: float | int,
    rating: float | None = None,
    stars: int | None = None,
    reason: str | None = None,
) -> dict:
    result = {
        "metric": metric_name,
        "status": "OK",
        "value": value,
    }

    if rating is not None:
        result["rating"] = rating

    if stars is not None:
        result["stars"] = stars

    if reason is not None:
        result["reason"] = reason

    return result


def _build_missing_metric(
    metric_name: str,
    reason: str,
) -> dict:
    return {
        "metric": metric_name,
        "status": "MISSING",
        "value": None,
        "rating": None,
        "stars": None,
        "reason": reason,
    }


def _build_invalid_metric(
    metric_name: str,
    reason: str,
) -> dict:
    return {
        "metric": metric_name,
        "status": "INVALID",
        "value": None,
        "rating": None,
        "stars": None,
        "reason": reason,
    }


def _build_not_applicable_metric(
    metric_name: str,
    reason: str,
) -> dict:
    return {
        "metric": metric_name,
        "status": "NOT_APPLICABLE",
        "value": None,
        "rating": None,
        "stars": None,
        "reason": reason,
    }


# ============================================================
# STAR RATING
# ============================================================

def _stars_from_rating(
    rating: float,
) -> int:
    """
    Convert a 0-5 continuous quality rating into
    a 1-5 star rating.

    The rating is intentionally kept separate from
    the stars so future versions can use more granular
    scoring without changing the underlying metric.
    """

    if rating >= 4.5:
        return 5

    if rating >= 3.5:
        return 4

    if rating >= 2.5:
        return 3

    if rating >= 1.5:
        return 2

    return 1


def _rating_from_thresholds(
    value: float,
    thresholds: list[tuple[float, float]],
) -> float:
    """
    Convert a raw metric into a 0-5 quality rating.

    thresholds:
        [
            (minimum_value, rating),
            ...
        ]

    The highest matching threshold is used.
    """

    rating = 0.0

    for minimum_value, candidate_rating in thresholds:

        if value >= minimum_value:
            rating = candidate_rating

    return rating


# ============================================================
# PROFITABILITY
# ============================================================

def rate_operating_margin(
    value: Any,
) -> dict:
    """
    Rate operating margin.

    v0.1 absolute thresholds:

        >= 30%  -> 5★
        >= 20%  -> 4★
        >= 10%  -> 3★
        >= 5%   -> 2★
        < 5%    -> 1★

    NOTE:
    These are broad absolute-quality bands only.
    Sector-relative evaluation is intentionally deferred.
    """

    metric_name = "operating_margin"

    if not _is_number(value):
        return _build_missing_metric(
            metric_name,
            "Operating margin is missing or non-numeric.",
        )

    value_float = float(value)

    rating = _rating_from_thresholds(
        value_float,
        [
            (0.05, 2.0),
            (0.10, 3.0),
            (0.20, 4.0),
            (0.30, 5.0),
        ],
    )

    stars = _stars_from_rating(rating)

    return _build_ok_metric(
        metric_name,
        value_float,
        rating,
        stars,
    )


def rate_net_margin(
    value: Any,
) -> dict:
    """
    Rate net margin.

    v0.1 absolute thresholds:

        >= 20%  -> 5★
        >= 12%  -> 4★
        >= 6%   -> 3★
        >= 2%   -> 2★
        < 2%    -> 1★
    """

    metric_name = "net_margin"

    if not _is_number(value):
        return _build_missing_metric(
            metric_name,
            "Net margin is missing or non-numeric.",
        )

    value_float = float(value)

    rating = _rating_from_thresholds(
        value_float,
        [
            (0.02, 2.0),
            (0.06, 3.0),
            (0.12, 4.0),
            (0.20, 5.0),
        ],
    )

    stars = _stars_from_rating(rating)

    return _build_ok_metric(
        metric_name,
        value_float,
        rating,
        stars,
    )


# ============================================================
# CASH QUALITY
# ============================================================

def rate_fcf_margin(
    value: Any,
) -> dict:
    """
    Rate FCF margin.

    v0.1 absolute thresholds:

        >= 20%  -> 5★
        >= 12%  -> 4★
        >= 6%   -> 3★
        >= 2%   -> 2★
        < 2%    -> 1★

    Negative FCF therefore receives 1★.
    """

    metric_name = "fcf_margin"

    if not _is_number(value):
        return _build_missing_metric(
            metric_name,
            "FCF margin is missing or non-numeric.",
        )

    value_float = float(value)

    rating = _rating_from_thresholds(
        value_float,
        [
            (0.02, 2.0),
            (0.06, 3.0),
            (0.12, 4.0),
            (0.20, 5.0),
        ],
    )

    stars = _stars_from_rating(rating)

    return _build_ok_metric(
        metric_name,
        value_float,
        rating,
        stars,
    )


def calculate_fcf_conversion(
    net_income_value: Any,
    fcf_value: Any,
) -> dict:
    """
    Calculate FCF conversion.

    Formula:

        FCF / Net Income

    Example:

        Net Income = 100
        FCF        = 90

        Conversion = 0.90

    Policy:

    - Net income = 0 -> INVALID
    - Missing input -> MISSING
    - Negative net income -> INVALID

    Negative FCF with positive net income is valid and
    will produce a negative conversion ratio.
    """

    metric_name = "fcf_conversion"

    if not _is_number(net_income_value):
        return _build_missing_metric(
            metric_name,
            "Net income is missing or non-numeric.",
        )

    if not _is_number(fcf_value):
        return _build_missing_metric(
            metric_name,
            "FCF is missing or non-numeric.",
        )

    net_income_float = float(net_income_value)
    fcf_float = float(fcf_value)

    if net_income_float == 0:
        return _build_invalid_metric(
            metric_name,
            "Net income is zero; FCF conversion cannot be calculated.",
        )

    if net_income_float < 0:
        return _build_invalid_metric(
            metric_name,
            "FCF conversion is not evaluated when net income is negative.",
        )

    value = fcf_float / net_income_float

    return _build_ok_metric(
        metric_name,
        value,
    )


def rate_fcf_conversion(
    value: Any,
) -> dict:
    """
    Rate FCF conversion.

    v0.1 thresholds:

        >= 100% -> 5★
        >= 80%  -> 4★
        >= 60%  -> 3★
        >= 40%  -> 2★
        < 40%   -> 1★

    This is a cash-conversion quality metric, not a growth metric.
    """

    metric_name = "fcf_conversion"

    if not _is_number(value):
        return _build_missing_metric(
            metric_name,
            "FCF conversion is missing or invalid.",
        )

    value_float = float(value)

    rating = _rating_from_thresholds(
        value_float,
        [
            (0.40, 2.0),
            (0.60, 3.0),
            (0.80, 4.0),
            (1.00, 5.0),
        ],
    )

    stars = _stars_from_rating(rating)

    return _build_ok_metric(
        metric_name,
        value_float,
        rating,
        stars,
    )


# ============================================================
# BALANCE SHEET QUALITY
# ============================================================

def calculate_net_debt_to_fcf(
    net_debt_value: Any,
    fcf_value: Any,
) -> dict:
    """
    Calculate Net Debt / FCF.

    Formula:

        Net Debt / FCF

    Interpretation:

        lower = better

    Negative net debt means the company has net cash.
    """

    metric_name = "net_debt_to_fcf"

    if not _is_number(net_debt_value):
        return _build_missing_metric(
            metric_name,
            "Net debt is missing or non-numeric.",
        )

    if not _is_number(fcf_value):
        return _build_missing_metric(
            metric_name,
            "FCF is missing or non-numeric.",
        )

    net_debt_float = float(net_debt_value)
    fcf_float = float(fcf_value)

    if fcf_float == 0:
        return _build_invalid_metric(
            metric_name,
            "FCF is zero; Net Debt / FCF cannot be calculated.",
        )

    if fcf_float < 0:
        return _build_invalid_metric(
            metric_name,
            "Net Debt / FCF is not evaluated when FCF is negative.",
        )

    value = net_debt_float / fcf_float

    return _build_ok_metric(
        metric_name,
        value,
    )


def rate_net_debt_to_fcf(
    value: Any,
) -> dict:
    """
    Rate Net Debt / FCF.

    Lower is better.

    v0.1 bands:

        <= -1.0x -> 5★
        <=  0.0x -> 5★
        <=  1.0x -> 5★
        <=  2.0x -> 4★
        <=  3.0x -> 3★
        <=  4.0x -> 2★
        >   4.0x -> 1★

    Net cash therefore receives the strongest rating.
    """

    metric_name = "net_debt_to_fcf"

    if not _is_number(value):
        return _build_missing_metric(
            metric_name,
            "Net Debt / FCF is missing or invalid.",
        )

    value_float = float(value)

    if value_float <= 1.0:
        rating = 5.0
    elif value_float <= 2.0:
        rating = 4.0
    elif value_float <= 3.0:
        rating = 3.0
    elif value_float <= 4.0:
        rating = 2.0
    else:
        rating = 1.0

    stars = _stars_from_rating(rating)

    return _build_ok_metric(
        metric_name,
        value_float,
        rating,
        stars,
    )


def rate_debt_to_cash(
    value: Any,
) -> dict:
    """
    Rate Total Debt / Cash.

    Lower is better.

    v0.1 bands:

        <= 0.5x -> 5★
        <= 1.0x -> 4★
        <= 2.0x -> 3★
        <= 4.0x -> 2★
        >  4.0x -> 1★
    """

    metric_name = "debt_to_cash"

    if not _is_number(value):
        return _build_missing_metric(
            metric_name,
            "Debt-to-cash ratio is missing or invalid.",
        )

    value_float = float(value)

    if value_float <= 0.5:
        rating = 5.0
    elif value_float <= 1.0:
        rating = 4.0
    elif value_float <= 2.0:
        rating = 3.0
    elif value_float <= 4.0:
        rating = 2.0
    else:
        rating = 1.0

    stars = _stars_from_rating(rating)

    return _build_ok_metric(
        metric_name,
        value_float,
        rating,
        stars,
    )


# ============================================================
# PROFIT-TO-CASH CONSISTENCY
# ============================================================

def calculate_profit_to_cash_consistency(
    net_margin_value: Any,
    fcf_margin_value: Any,
) -> dict:
    """
    Calculate a simple profit-to-cash consistency indicator.

    Formula:

        FCF Margin / Net Margin

    Interpretation:

        Around 1.0:
            accounting profitability and cash profitability
            are broadly aligned.

        > 1.0:
            FCF generation exceeds accounting net margin.

        < 1.0:
            cash conversion is weaker than accounting profitability.

    This metric is intentionally used as a supporting quality
    indicator rather than a standalone valuation metric.
    """

    metric_name = "profit_to_cash_consistency"

    if not _is_number(net_margin_value):
        return _build_missing_metric(
            metric_name,
            "Net margin is missing or non-numeric.",
        )

    if not _is_number(fcf_margin_value):
        return _build_missing_metric(
            metric_name,
            "FCF margin is missing or non-numeric.",
        )

    net_margin_float = float(net_margin_value)
    fcf_margin_float = float(fcf_margin_value)

    if net_margin_float <= 0:
        return _build_invalid_metric(
            metric_name,
            "Net margin must be positive for consistency analysis.",
        )

    value = fcf_margin_float / net_margin_float

    return _build_ok_metric(
        metric_name,
        value,
    )


def rate_profit_to_cash_consistency(
    value: Any,
) -> dict:
    """
    Rate profit-to-cash consistency.

    v0.1 bands:

        >= 1.20x -> 5★
        >= 1.00x -> 4★
        >= 0.80x -> 3★
        >= 0.60x -> 2★
        <  0.60x -> 1★

    This metric is deliberately moderate in weight because
    working-capital-heavy businesses can naturally show
    temporary divergence.
    """

    metric_name = "profit_to_cash_consistency"

    if not _is_number(value):
        return _build_missing_metric(
            metric_name,
            "Profit-to-cash consistency is missing or invalid.",
        )

    value_float = float(value)

    rating = _rating_from_thresholds(
        value_float,
        [
            (0.60, 2.0),
            (0.80, 3.0),
            (1.00, 4.0),
            (1.20, 5.0),
        ],
    )

    stars = _stars_from_rating(rating)

    return _build_ok_metric(
        metric_name,
        value_float,
        rating,
        stars,
    )


# ============================================================
# QUALITY COMPONENT CALCULATION
# ============================================================

def _get_derived_value(
    derived_metrics: dict,
    metric_name: str,
) -> Any:
    """
    Safely retrieve a derived metric value.
    """

    metric = derived_metrics.get(
        metric_name,
        {},
    )

    if not isinstance(metric, dict):
        return None

    if metric.get("status") != "OK":
        return None

    return metric.get("value")


# ============================================================
# QUALITY SCORE
# ============================================================

def _calculate_quality_score(
    components: list[dict],
) -> dict:
    """
    Calculate weighted Quality score.

    v0.1 weights:

        Operating Margin              20%
        Net Margin                    10%
        FCF Margin                    20%
        FCF Conversion                20%
        Net Debt / FCF                15%
        Debt / Cash                    5%
        Profit-to-Cash Consistency    10%

    Missing / invalid metrics are excluded from the
    denominator rather than treated as zero.

    A score is considered reliable only when at least
    four of the seven components are available.
    """

    weights = {
        "operating_margin": 0.20,
        "net_margin": 0.10,
        "fcf_margin": 0.20,
        "fcf_conversion": 0.20,
        "net_debt_to_fcf": 0.15,
        "debt_to_cash": 0.05,
        "profit_to_cash_consistency": 0.10,
    }

    weighted_sum = 0.0
    available_weight = 0.0
    available_count = 0

    for component in components:

        metric_name = component.get(
            "metric"
        )

        if component.get("status") != "OK":
            continue

        rating = component.get(
            "rating"
        )

        if not _is_number(rating):
            continue

        weight = weights.get(
            metric_name
        )

        if weight is None:
            continue

        weighted_sum += (
            float(rating)
            * weight
        )

        available_weight += weight
        available_count += 1

    if available_count == 0:

        return {
            "status": "MISSING",
            "value": None,
            "stars": None,
            "available_components": 0,
            "total_components": len(weights),
            "coverage": 0.0,
            "reason": (
                "No valid Quality components are available."
            ),
        }

    if available_count < 4:

        return {
            "status": "MISSING",
            "value": None,
            "stars": None,
            "available_components": available_count,
            "total_components": len(weights),
            "coverage": available_weight,
            "reason": (
                "Insufficient Quality component coverage. "
                "At least four valid components are required."
            ),
        }

    normalized_score = (
        weighted_sum
        /
        available_weight
    )

    stars = _stars_from_rating(
        normalized_score
    )

    return {
        "status": "OK",
        "value": normalized_score,
        "stars": stars,
        "available_components": available_count,
        "total_components": len(weights),
        "coverage": available_weight,
    }


# ============================================================
# MAIN QUALITY CALCULATION
# ============================================================

def calculate_quality(
    normalized: dict,
    derived: dict,
) -> dict:
    """
    Calculate Quality Factor v0.1.

    Inputs:

        normalized:
            Output from sec_normalizer.py

        derived:
            Output from derived_metrics.py

    Important:

        This function does NOT perform sector-relative
        evaluation.

        It produces an absolute Quality assessment only.

        Historical and peer-relative evaluation belongs
        to a later stage.
    """

    if not isinstance(
        normalized,
        dict,
    ):
        raise TypeError(
            "normalized must be a dict."
        )

    if not isinstance(
        derived,
        dict,
    ):
        raise TypeError(
            "derived must be a dict."
        )

    ticker = normalized.get(
        "ticker"
    )

    company_type = normalized.get(
        "company_type"
    )

    normalized_metrics = normalized.get(
        "metrics",
        {},
    )

    derived_metrics = derived.get(
        "derived_metrics",
        {},
    )

    if not isinstance(
        normalized_metrics,
        dict,
    ):
        raise ValueError(
            "normalized['metrics'] must be a dict."
        )

    if not isinstance(
        derived_metrics,
        dict,
    ):
        raise ValueError(
            "derived['derived_metrics'] must be a dict."
        )

    # --------------------------------------------------------
    # Extract current derived values
    # --------------------------------------------------------

    operating_margin = _get_derived_value(
        derived_metrics,
        "operating_margin",
    )

    net_margin = _get_derived_value(
        derived_metrics,
        "net_margin",
    )

    fcf_margin = _get_derived_value(
        derived_metrics,
        "fcf_margin",
    )

    fcf = _get_derived_value(
        derived_metrics,
        "fcf",
    )

    net_income_metric = normalized_metrics.get(
        "net_income",
        {},
    )

    net_income = (
        net_income_metric.get("value")
        if isinstance(
            net_income_metric,
            dict,
        )
        and net_income_metric.get("status") == "OK"
        else None
    )

    net_debt = _get_derived_value(
        derived_metrics,
        "net_debt",
    )

    debt_to_cash = _get_derived_value(
        derived_metrics,
        "debt_to_cash",
    )

    # --------------------------------------------------------
    # Individual components
    # --------------------------------------------------------

    operating_margin_component = (
        rate_operating_margin(
            operating_margin
        )
    )

    net_margin_component = (
        rate_net_margin(
            net_margin
        )
    )

    fcf_margin_component = (
        rate_fcf_margin(
            fcf_margin
        )
    )

    fcf_conversion_raw = (
        calculate_fcf_conversion(
            net_income,
            fcf,
        )
    )

    if fcf_conversion_raw["status"] == "OK":

        fcf_conversion_component = (
            rate_fcf_conversion(
                fcf_conversion_raw["value"]
            )
        )

    else:

        fcf_conversion_component = (
            fcf_conversion_raw
        )

    net_debt_to_fcf_raw = (
        calculate_net_debt_to_fcf(
            net_debt,
            fcf,
        )
    )

    if net_debt_to_fcf_raw["status"] == "OK":

        net_debt_to_fcf_component = (
            rate_net_debt_to_fcf(
                net_debt_to_fcf_raw["value"]
            )
        )

    else:

        net_debt_to_fcf_component = (
            net_debt_to_fcf_raw
        )

    debt_to_cash_component = (
        rate_debt_to_cash(
            debt_to_cash
        )
    )

    profit_to_cash_raw = (
        calculate_profit_to_cash_consistency(
            net_margin,
            fcf_margin,
        )
    )

    if profit_to_cash_raw["status"] == "OK":

        profit_to_cash_component = (
            rate_profit_to_cash_consistency(
                profit_to_cash_raw["value"]
            )
        )

    else:

        profit_to_cash_component = (
            profit_to_cash_raw
        )

    components = [
        operating_margin_component,
        net_margin_component,
        fcf_margin_component,
        fcf_conversion_component,
        net_debt_to_fcf_component,
        debt_to_cash_component,
        profit_to_cash_component,
    ]

    # --------------------------------------------------------
    # Financial-company handling
    # --------------------------------------------------------

    if company_type == "FINANCIAL":

        financial_note = (
            "Generic industrial Quality metrics such as "
            "FCF, CapEx and debt-to-cash are not fully "
            "applicable to financial companies."
        )

    else:

        financial_note = None

    # --------------------------------------------------------
    # Overall Quality
    # --------------------------------------------------------

    overall = _calculate_quality_score(
        components
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "company_type": company_type,

        "quality": {
            "status": overall["status"],
            "score": overall["value"],
            "stars": overall["stars"],
            "coverage": overall["coverage"],
            "available_components": overall[
                "available_components"
            ],
            "total_components": overall[
                "total_components"
            ],
        },

        "components": {
            component["metric"]: component
            for component in components
        },

        "limitations": {
            "roic": {
                "status": "NOT_IMPLEMENTED",
                "reason": (
                    "Equity, tax and invested-capital inputs "
                    "are not yet available in the normalized "
                    "v0.1 schema."
                ),
            },
            "roe": {
                "status": "NOT_IMPLEMENTED",
                "reason": (
                    "Equity is not yet available in the "
                    "normalized v0.1 schema."
                ),
            },
            "relative_evaluation": {
                "status": "NOT_IMPLEMENTED",
                "reason": (
                    "Sector and peer-relative evaluation "
                    "belongs to a later framework stage."
                ),
            },
            "historical_evaluation": {
                "status": "NOT_IMPLEMENTED",
                "reason": (
                    "Historical Quality trend evaluation "
                    "belongs to a later framework stage."
                ),
            },
        },
    }

    if financial_note is not None:

        result["limitations"][
            "financial_company"
        ] = {
            "status": "WARNING",
            "reason": financial_note,
        }

    # --------------------------------------------------------
    # Self-validation (2026-09-19): validate_quality() below already
    # existed and was already exercised by tests/test_quality.py, but
    # was never actually called from within calculate_quality() itself
    # -- so a real bug that produced malformed output would have gone
    # uncaught outside of the specific fixtures the test suite happens
    # to cover. A validate_quality() failure here means this function's
    # OWN output doesn't match its own documented schema, which is a
    # programming defect, not a data-availability question (those are
    # already handled per-component via OK/MISSING/INVALID status) --
    # so it fails loudly rather than silently returning malformed data.
    validation_failures = validate_quality(result)

    if validation_failures:
        raise ValueError(
            f"calculate_quality() produced invalid output for "
            f"{ticker!r}: {validation_failures}"
        )

    return result


# ============================================================
# VALIDATION
# ============================================================

def validate_quality(
    data: dict,
) -> list[str]:
    """
    Validate Quality Factor v0.1 output.

    Returns:

        [] when valid
        list[str] of failure identifiers otherwise
    """

    failures = []

    if not isinstance(
        data,
        dict,
    ):
        return ["root"]

    required_top_level = [
        "schema_version",
        "ticker",
        "company_type",
        "quality",
        "components",
        "limitations",
    ]

    for key in required_top_level:

        if key not in data:
            failures.append(key)

    quality = data.get(
        "quality"
    )

    if not isinstance(
        quality,
        dict,
    ):
        failures.append("quality")
    else:

        for key in [
            "status",
            "score",
            "stars",
            "coverage",
            "available_components",
            "total_components",
        ]:

            if key not in quality:
                failures.append(
                    f"quality.{key}"
                )

        status = quality.get(
            "status"
        )

        if status not in {
            "OK",
            "MISSING",
        }:

            failures.append(
                "quality.status"
            )

        if status == "OK":

            if not _is_number(
                quality.get("score")
            ):
                failures.append(
                    "quality.score"
                )

            stars = quality.get(
                "stars"
            )

            if stars not in {
                1,
                2,
                3,
                4,
                5,
            }:

                failures.append(
                    "quality.stars"
                )

        if status == "MISSING":

            if quality.get(
                "score"
            ) is not None:

                failures.append(
                    "quality.score"
                )

            if quality.get(
                "stars"
            ) is not None:

                failures.append(
                    "quality.stars"
                )

    components = data.get(
        "components"
    )

    if not isinstance(
        components,
        dict,
    ):
        failures.append(
            "components"
        )

    else:

        for metric_name, metric in components.items():

            if not isinstance(
                metric,
                dict,
            ):

                failures.append(
                    f"components.{metric_name}"
                )
                continue

            if metric.get(
                "status"
            ) not in VALID_STATUSES:

                failures.append(
                    f"components.{metric_name}.status"
                )

            if "value" not in metric:

                failures.append(
                    f"components.{metric_name}.value"
                )

            status = metric.get(
                "status"
            )

            if status == "OK":

                if not _is_number(
                    metric.get("value")
                ):

                    failures.append(
                        f"components.{metric_name}.value"
                    )

                if metric.get(
                    "stars"
                ) not in {
                    1,
                    2,
                    3,
                    4,
                    5,
                }:

                    failures.append(
                        f"components.{metric_name}.stars"
                    )

            elif status in {
                "MISSING",
                "INVALID",
                "NOT_APPLICABLE",
            }:

                if metric.get(
                    "value"
                ) is not None:

                    failures.append(
                        f"components.{metric_name}.value"
                    )

    return failures


# ============================================================
# PUBLIC API
# ============================================================

__all__ = [
    "SCHEMA_VERSION",
    "QUALITY_METRICS",
    "calculate_fcf_conversion",
    "calculate_net_debt_to_fcf",
    "calculate_profit_to_cash_consistency",
    "rate_operating_margin",
    "rate_net_margin",
    "rate_fcf_margin",
    "rate_fcf_conversion",
    "rate_net_debt_to_fcf",
    "rate_debt_to_cash",
    "rate_profit_to_cash_consistency",
    "calculate_quality",
    "validate_quality",
]