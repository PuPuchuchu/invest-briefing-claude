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

# 2026-09-20: promoted out of _calculate_quality_score()'s local `weights`
# dict into a public, importable constant -- src/fundamentals/metric_specs.py
# (Percentile Engine's Factor Score weighting) needs the SAME already-approved
# v0.1 weights rather than a second, hand-copied set that could drift out of
# sync. This is a pure refactor: the values themselves are unchanged, and
# _calculate_quality_score() below now reads from this constant instead of
# its own local dict.
QUALITY_WEIGHTS = {
    "operating_margin": 0.20,
    "net_margin": 0.10,
    "fcf_margin": 0.20,
    "fcf_conversion": 0.20,
    "net_debt_to_fcf": 0.15,
    "debt_to_cash": 0.05,
    "profit_to_cash_consistency": 0.10,
}


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

    weights = QUALITY_WEIGHTS

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
# QUALITY v0.2 (P2 Quality wiring, design v3 + v3.1)
#
# Added alongside v0.1. Nothing above this block is changed: v0.1's
# calculate_quality(), its rating helpers' thresholds and QUALITY_WEIGHTS
# (imported by metric_specs.py) keep their behaviour. v0.2 reuses the v0.1
# rate_* functions read-only for the threshold bands and applies its own
# rules on top:
#   D1  profit_to_cash_consistency is informational (weight 0; algebraically
#       identical to fcf_conversion) and is excluded from score, counts and
#       coverage. The six independent weights are the v0.1 values divided by
#       their sum (0.90).
#   D2  sign table for fcf_conversion / net_debt_to_fcf.
#   D3  element OK needs >= 3 rated and >= 4 rated + NOT_MEANINGFUL among the
#       six independent metrics.
#   D4  every rating is floored at 1.0.
#   D5  company_type FINANCIAL -> whole element NOT_APPLICABLE.
#   D6  liquidity basis is cash and cash equivalents only (labelled).
#   D7  flow inputs must agree on period_start, period_end, fy, fp,
#       period_kind, period_class and unit; balance/flow dates are recorded,
#       never scored.
#   v3.1 central reason-code registry; inputs dated after evaluation_date are
#       INVALID here (the normalizer's freshness rule is not changed).
# ============================================================

from datetime import date as _date, datetime as _datetime

QUALITY_V02_SCHEMA_VERSION = "quality_v0.2"

# v0.1 weight values, copied (not referenced) so that a later change to
# QUALITY_WEIGHTS for metric_specs.py cannot silently change v0.2.
_QUALITY_V02_BASE_WEIGHTS = {
    "operating_margin": 0.20,
    "net_margin": 0.10,
    "fcf_margin": 0.20,
    "fcf_conversion": 0.20,
    "net_debt_to_fcf": 0.15,
    "debt_to_cash": 0.05,
}
_V02_BASE_TOTAL = sum(_QUALITY_V02_BASE_WEIGHTS.values())

QUALITY_V02_INDEPENDENT_METRICS = (
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "fcf_conversion",
    "net_debt_to_fcf",
    "debt_to_cash",
)

QUALITY_V02_SCORE_WEIGHTS = {
    name: _QUALITY_V02_BASE_WEIGHTS[name] / _V02_BASE_TOTAL
    for name in QUALITY_V02_INDEPENDENT_METRICS
}

QUALITY_V02_INFORMATIONAL_METRICS = {
    "profit_to_cash_consistency": "fcf_conversion",
}

QUALITY_V02_DIMENSIONS = {
    "profitability": ("operating_margin", "net_margin", "fcf_margin"),
    "cash_conversion": ("fcf_conversion",),
    "leverage": ("net_debt_to_fcf", "debt_to_cash"),
}

QUALITY_V02_MIN_RATED = 3
QUALITY_V02_MIN_RATED_PLUS_NOT_MEANINGFUL = 4

# Central registry of reason codes Quality v0.2 itself generates (design
# v3.1 §1.1). Parameterised codes are written "<CODE>:<metric>"; the part
# before ":" must be in this set.
QUALITY_REASON_CODES = frozenset({
    "NONPOSITIVE_NET_INCOME",
    "NONPOSITIVE_FCF_WITH_NET_DEBT",
    "NONPOSITIVE_FCF_WITH_NONPOSITIVE_NET_DEBT",
    "FLOW_PERIOD_MISMATCH",
    "FLOW_UNIT_MISMATCH",
    "PERIOD_END_AFTER_EVALUATION_DATE",
    "NON_FINITE_INPUT",
    "NON_FINITE_RESULT",
    "INPUT_MISSING",
    "INPUT_INCOMPLETE",
    "INPUT_WITHHELD",
    "INPUT_INVALID",
    "UPSTREAM_REASON_UNSPECIFIED",
    "INSUFFICIENT_INDEPENDENT_COMPONENTS",
    "COMPANY_TYPE_FINANCIAL",
})

QUALITY_V02_FLAGS = frozenset({
    "NONPOSITIVE_FCF_LEVERAGE_UNASSESSED",
})

LIQUIDITY_BASIS_CASH_ONLY = "CASH_AND_CASH_EQUIVALENTS_ONLY"
FLOW_BASIS_ANNUAL_FY = "ANNUAL_FY"

_V02_COMPONENT_STATUSES = frozenset({
    "OK", "MISSING", "INVALID", "WITHHELD", "NOT_MEANINGFUL",
})
_V02_ELEMENT_STATUSES = frozenset({"OK", "MISSING", "NOT_APPLICABLE"})

_FLOW_INPUTS = ("revenue", "operating_income", "net_income", "cfo", "capex")
_FLOW_SIGNATURE_KEYS = (
    "period_start", "period_end", "fy", "fp", "period_kind", "period_class",
)

# Per metric: (normalized inputs, derived inputs, uses flow, uses balance).
_V02_INPUTS = {
    "operating_margin": (("operating_income", "revenue"), ("operating_margin",), True, False),
    "net_margin": (("net_income", "revenue"), ("net_margin",), True, False),
    "fcf_margin": (("cfo", "capex", "revenue"), ("fcf", "fcf_margin"), True, False),
    "fcf_conversion": (("net_income", "cfo", "capex"), ("fcf",), True, False),
    "net_debt_to_fcf": (("total_debt", "cash", "cfo", "capex"), ("net_debt", "fcf"), True, True),
    "debt_to_cash": (("total_debt", "cash"), ("debt_to_cash",), False, True),
    "profit_to_cash_consistency": (("net_income", "cfo", "capex", "revenue"), ("fcf_margin", "net_margin"), True, False),
}

_V02_RATERS = {
    "operating_margin": rate_operating_margin,
    "net_margin": rate_net_margin,
    "fcf_margin": rate_fcf_margin,
    "fcf_conversion": rate_fcf_conversion,
    "net_debt_to_fcf": rate_net_debt_to_fcf,
    "debt_to_cash": rate_debt_to_cash,
    "profit_to_cash_consistency": rate_profit_to_cash_consistency,
}

# Upstream non-OK precedence when several inputs are unusable: governance
# first, then contradictions, then incompleteness, then absence.
_UPSTREAM_PRECEDENCE = ("WITHHELD", "INVALID", "INCOMPLETE", "MISSING")
_UPSTREAM_TO_V02 = {
    "WITHHELD": ("WITHHELD", "INPUT_WITHHELD"),
    "INVALID": ("INVALID", "INPUT_INVALID"),
    "INCOMPLETE": ("MISSING", "INPUT_INCOMPLETE"),
    "MISSING": ("MISSING", "INPUT_MISSING"),
}


def _v02_finite(value: Any) -> bool:
    return _is_number(value)


def _v02_raw_number(value: Any) -> bool:
    """Numeric (including NaN/inf), bool excluded."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _v02_date(value: Any):
    if isinstance(value, _datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    if isinstance(value, str):
        try:
            return _date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _v02_metric(container: dict, name: str) -> dict:
    item = container.get(name) if isinstance(container, dict) else None
    return item if isinstance(item, dict) else {}


def _v02_upstream_class(status: Any) -> str | None:
    """Map an upstream status to MISSING/INCOMPLETE/WITHHELD/INVALID, or
    None when OK. Any other non-OK status is treated as INVALID (it is not
    a usable value and its meaning is not one of the known absences)."""
    if status == "OK":
        return None
    if status in ("MISSING", "INCOMPLETE", "WITHHELD", "INVALID"):
        return status
    return "INVALID"


def _v02_flow_signature(metric: dict) -> tuple:
    return tuple(metric.get(key) for key in _FLOW_SIGNATURE_KEYS)


def _v02_debt_dates(total_debt: dict) -> list[str]:
    """period_end of every component actually summed into an OK total_debt:
    components.*, component_sum.components[*] and scope_alignment. Only
    fields that exist are read; nothing is inferred."""
    if total_debt.get("status") != "OK":
        return []
    dates = set()
    components = total_debt.get("components")
    if isinstance(components, dict):
        for comp in components.values():
            if isinstance(comp, dict) and comp.get("status") == "OK" and comp.get("period_end"):
                dates.add(comp["period_end"])
    component_sum = total_debt.get("component_sum")
    if isinstance(component_sum, dict) and component_sum.get("applied"):
        for comp in component_sum.get("components") or []:
            if isinstance(comp, dict) and comp.get("period_end"):
                dates.add(comp["period_end"])
    alignment = total_debt.get("scope_alignment")
    if isinstance(alignment, dict) and alignment.get("period_end"):
        dates.add(alignment["period_end"])
    return sorted(dates)


def _v02_days(later, earlier) -> int | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).days


def _v02_component(name: str) -> dict:
    out = {
        "metric": name,
        "status": None,
        "value": None,
        "rating": None,
        "stars": None,
        "weight": QUALITY_V02_SCORE_WEIGHTS.get(name, 0.0),
        "reason_code": None,
        "upstream_reason_code": None,
        "upstream_inputs": [],
    }
    if name in QUALITY_V02_INFORMATIONAL_METRICS:
        out["role"] = "INFORMATIONAL"
        out["duplicate_of"] = QUALITY_V02_INFORMATIONAL_METRICS[name]
    else:
        out["role"] = "INDEPENDENT"
    return out


def _v02_rate(name: str, value: float) -> tuple[float, int]:
    rated = _V02_RATERS[name](value)
    rating = rated.get("rating")
    rating = float(rating) if _is_number(rating) else 0.0
    rating = max(1.0, rating)  # D4
    return rating, _stars_from_rating(rating)


def _v02_set_ok(comp: dict, value: float | None, rating: float) -> None:
    comp["status"] = "OK"
    comp["value"] = value
    comp["rating"] = rating
    comp["stars"] = _stars_from_rating(rating)


def calculate_quality_v02(
    normalized: dict,
    derived: dict,
    *,
    evaluation_date: Any,
) -> dict:
    """Quality v0.2 (P2 design v3 + v3.1). Pure function of the already
    computed normalized and derived results; no new input is fetched.

    A defect in this function's own output raises ValueError (fail
    closed); data problems are statuses, never exceptions."""

    if not isinstance(normalized, dict):
        raise TypeError("normalized must be a dict.")
    if not isinstance(derived, dict):
        raise TypeError("derived must be a dict.")
    eval_date = _v02_date(evaluation_date)
    if eval_date is None:
        raise ValueError(f"Unparseable evaluation_date: {evaluation_date!r}")

    n_metrics = normalized.get("metrics")
    d_metrics = derived.get("derived_metrics")
    if not isinstance(n_metrics, dict):
        raise ValueError("normalized['metrics'] must be a dict.")
    if not isinstance(d_metrics, dict):
        raise ValueError("derived['derived_metrics'] must be a dict.")

    company_type = normalized.get("company_type")
    result = {
        "schema_version": QUALITY_V02_SCHEMA_VERSION,
        "ticker": normalized.get("ticker"),
        "company_type": company_type,
        "evaluation_date": eval_date.isoformat(),
        "element": None,
        "components": {},
        "periods": None,
        "limitations": _v02_limitations(),
    }

    # D5: FINANCIAL -> whole element NOT_APPLICABLE, nothing computed.
    if company_type == "FINANCIAL":
        result["element"] = _v02_empty_element("NOT_APPLICABLE", "COMPANY_TYPE_FINANCIAL")
        _v02_validate_or_raise(result)
        return result

    # ---------------- periods (D7, recorded facts) ----------------
    ok_flows = {
        name: _v02_metric(n_metrics, name)
        for name in _FLOW_INPUTS
        if _v02_metric(n_metrics, name).get("status") == "OK"
    }
    signatures = {name: _v02_flow_signature(m) for name, m in ok_flows.items()}
    units = {name: m.get("unit") for name, m in ok_flows.items()}
    period_sigs = set(signatures.values())
    unit_set = set(units.values())
    flow_period_consistent = len(period_sigs) <= 1
    flow_unit_consistent = len(unit_set) <= 1
    flow_consistent = flow_period_consistent and flow_unit_consistent
    accessions = {m.get("accession") for m in ok_flows.values()}

    flow_start = flow_end = None
    if ok_flows and flow_consistent:
        sample = next(iter(ok_flows.values()))
        flow_start = sample.get("period_start")
        flow_end = sample.get("period_end")
    flow_end_d = _v02_date(flow_end)

    cash = _v02_metric(n_metrics, "cash")
    total_debt = _v02_metric(n_metrics, "total_debt")
    cash_as_of = cash.get("period_end") if cash.get("status") == "OK" else None
    cash_as_of_d = _v02_date(cash_as_of)
    debt_as_of = _v02_debt_dates(total_debt)
    debt_as_of_d = [_v02_date(x) for x in debt_as_of]

    if cash_as_of and debt_as_of:
        balance_dates_consistent = set(debt_as_of) == {cash_as_of}
    else:
        balance_dates_consistent = None
    balance_sheet_as_of = cash_as_of if balance_dates_consistent else None

    result["periods"] = {
        "flow_basis": FLOW_BASIS_ANNUAL_FY,
        "flow_inputs_consistent": flow_consistent if ok_flows else None,
        "flow_period_start": flow_start,
        "flow_period_end": flow_end,
        "flow_age_days": _v02_days(eval_date, flow_end_d),
        "flow_accession_consistent": (len(accessions) == 1) if ok_flows else None,
        "cash_as_of": cash_as_of,
        "cash_age_days": _v02_days(eval_date, cash_as_of_d),
        "debt_as_of": debt_as_of,
        "debt_age_days": [_v02_days(eval_date, d) for d in debt_as_of_d],
        "balance_sheet_as_of": balance_sheet_as_of,
        "balance_dates_consistent": balance_dates_consistent,
    }

    # Future-date guard (v3.1 §2.2): flow end, cash date, every debt date.
    flow_in_future = any(
        (d := _v02_date(m.get("period_end"))) is not None and d > eval_date
        for m in ok_flows.values()
    )
    balance_in_future = (
        (cash_as_of_d is not None and cash_as_of_d > eval_date)
        or any(d is not None and d > eval_date for d in debt_as_of_d)
    )

    # ---------------- components ----------------
    components = {}
    for name in tuple(QUALITY_V02_INDEPENDENT_METRICS) + tuple(QUALITY_V02_INFORMATIONAL_METRICS):
        n_inputs, d_inputs, uses_flow, uses_balance = _V02_INPUTS[name]
        comp = _v02_component(name)
        components[name] = comp

        if name == "net_debt_to_fcf":
            nd = _v02_metric(d_metrics, "net_debt")
            nd_value = nd.get("value")
            comp["net_debt_sign"] = (
                ("ZERO" if nd_value == 0 else "NEGATIVE" if nd_value < 0 else "POSITIVE")
                if nd.get("status") == "OK" and _v02_finite(nd_value) else None
            )
            comp["liquidity_basis"] = LIQUIDITY_BASIS_CASH_ONLY
            comp["mixed_period_ratio"] = True
            comp["cash_flow_gap_days"] = _v02_days(cash_as_of_d, flow_end_d)
            comp["debt_flow_gap_days"] = [_v02_days(d, flow_end_d) for d in debt_as_of_d] if flow_end_d else []
            comp["balance_flow_gap_days"] = (
                _v02_days(_v02_date(balance_sheet_as_of), flow_end_d) if balance_sheet_as_of else None
            )
        elif name == "debt_to_cash":
            comp["liquidity_basis"] = LIQUIDITY_BASIS_CASH_ONLY

        # (1) upstream status, worst first.
        unusable = []
        for src, container in [(x, n_metrics) for x in n_inputs] + [(x, d_metrics) for x in d_inputs]:
            item = _v02_metric(container, src)
            klass = _v02_upstream_class(item.get("status"))
            if klass is not None:
                unusable.append((klass, src, item))
        if unusable:
            comp["upstream_inputs"] = [
                {"metric": src, "status": item.get("status"), "reason_code": item.get("reason_code")}
                for _k, src, item in unusable
            ]
            unusable.sort(key=lambda t: _UPSTREAM_PRECEDENCE.index(t[0]))
            klass, src, item = unusable[0]
            status, code = _UPSTREAM_TO_V02[klass]
            comp["status"] = status
            comp["reason_code"] = f"{code}:{src}"
            comp["upstream_reason_code"] = item.get("reason_code") or "UPSTREAM_REASON_UNSPECIFIED"
            continue

        # (2) non-finite inputs.
        values = {}
        nonfinite = False
        for src, container in [(x, n_metrics) for x in n_inputs] + [(x, d_metrics) for x in d_inputs]:
            v = _v02_metric(container, src).get("value")
            if not _v02_finite(v):
                nonfinite = True
            values[src] = v
        if nonfinite:
            comp["status"] = "INVALID"
            comp["reason_code"] = "NON_FINITE_INPUT"
            continue

        # (3) dates after evaluation_date.
        if (uses_flow and flow_in_future) or (uses_balance and balance_in_future):
            comp["status"] = "INVALID"
            comp["reason_code"] = "PERIOD_END_AFTER_EVALUATION_DATE"
            continue

        # (4) flow consistency (D7).
        if uses_flow and not flow_period_consistent:
            comp["status"] = "INVALID"
            comp["reason_code"] = "FLOW_PERIOD_MISMATCH"
            continue
        if uses_flow and not flow_unit_consistent:
            comp["status"] = "INVALID"
            comp["reason_code"] = "FLOW_UNIT_MISMATCH"
            continue

        # (5) sign rules (D2) and value.
        value = None
        if name in ("operating_margin", "net_margin", "fcf_margin", "debt_to_cash"):
            value = float(values[name])
        elif name == "fcf_conversion":
            ni, fcf = float(values["net_income"]), float(values["fcf"])
            if ni <= 0:
                comp["status"] = "NOT_MEANINGFUL"
                comp["reason_code"] = "NONPOSITIVE_NET_INCOME"
                continue
            value = fcf / ni
        elif name == "profit_to_cash_consistency":
            nm, fm = float(values["net_margin"]), float(values["fcf_margin"])
            if nm <= 0:
                comp["status"] = "NOT_MEANINGFUL"
                comp["reason_code"] = "NONPOSITIVE_NET_INCOME"
                continue
            value = fm / nm
        elif name == "net_debt_to_fcf":
            nd_v, fcf = float(values["net_debt"]), float(values["fcf"])
            if fcf <= 0:
                if nd_v > 0:
                    _v02_set_ok(comp, None, 1.0)
                    comp["reason_code"] = "NONPOSITIVE_FCF_WITH_NET_DEBT"
                else:
                    comp["status"] = "NOT_MEANINGFUL"
                    comp["reason_code"] = "NONPOSITIVE_FCF_WITH_NONPOSITIVE_NET_DEBT"
                continue
            value = nd_v / fcf

        if not _v02_finite(value):
            comp["status"] = "INVALID"
            comp["reason_code"] = "NON_FINITE_RESULT"
            continue
        rating, _stars = _v02_rate(name, value)
        _v02_set_ok(comp, value, rating)

    result["components"] = components

    # ---------------- element (D3) ----------------
    independent = [components[n] for n in QUALITY_V02_INDEPENDENT_METRICS]
    rated = [c for c in independent if c["status"] == "OK"]
    not_meaningful = [c for c in independent if c["status"] == "NOT_MEANINGFUL"]
    element = _v02_empty_element(None, None)
    element["rated_count"] = len(rated)
    element["not_meaningful_count"] = len(not_meaningful)
    element["assessed_dimensions"] = {
        dim: _v02_dimension_status([components[m] for m in members])
        for dim, members in QUALITY_V02_DIMENSIONS.items()
    }
    element["unavailable_components"] = [
        {
            "metric": c["metric"],
            "status": c["status"],
            "reason_code": c["reason_code"],
            "upstream_reason_code": c["upstream_reason_code"],
        }
        for c in independent
        if c["status"] not in ("OK", "NOT_MEANINGFUL")
    ]
    fcf_item = _v02_metric(d_metrics, "fcf")
    nd_item = _v02_metric(d_metrics, "net_debt")
    if (
        fcf_item.get("status") == "OK"
        and _v02_finite(fcf_item.get("value"))
        and float(fcf_item["value"]) <= 0
        and nd_item.get("status") != "OK"
    ):
        element["flags"].append("NONPOSITIVE_FCF_LEVERAGE_UNASSESSED")

    if (
        len(rated) >= QUALITY_V02_MIN_RATED
        and len(rated) + len(not_meaningful) >= QUALITY_V02_MIN_RATED_PLUS_NOT_MEANINGFUL
    ):
        scored_fraction = sum(QUALITY_V02_SCORE_WEIGHTS[c["metric"]] for c in rated)
        weights = {c["metric"]: QUALITY_V02_SCORE_WEIGHTS[c["metric"]] / scored_fraction for c in rated}
        score = sum(c["rating"] * weights[c["metric"]] for c in rated)
        element.update({
            "status": "OK",
            "score": score,
            "stars": _stars_from_rating(score),
            "scored_weight_fraction": scored_fraction,
            "normalized_score_weights": weights,
        })
    else:
        element["status"] = "MISSING"
        element["reason_code"] = "INSUFFICIENT_INDEPENDENT_COMPONENTS"
    result["element"] = element

    _v02_validate_or_raise(result)
    return result


def _v02_empty_element(status, reason_code) -> dict:
    return {
        "status": status,
        "reason_code": reason_code,
        "score": None,
        "stars": None,
        "scored_weight_fraction": None,
        "normalized_score_weights": None,
        "rated_count": 0,
        "not_meaningful_count": 0,
        "independent_total": len(QUALITY_V02_INDEPENDENT_METRICS),
        "assessed_dimensions": None,
        "unavailable_components": [],
        "flags": [],
    }


def _v02_dimension_status(members: list[dict]) -> str:
    assessed = [m for m in members if m["status"] in ("OK", "NOT_MEANINGFUL")]
    if len(assessed) == len(members):
        return "ASSESSED"
    if assessed:
        return "PARTIALLY_ASSESSED"
    return "NOT_ASSESSED"


def _v02_limitations() -> dict:
    return {
        "liquidity_basis": {
            "status": "LIMITATION",
            "reason": (
                "Cash is CashAndCashEquivalentsAtCarryingValue only; current "
                "marketable securities are not included (P2 design D6)."
            ),
        },
        "roic": {"status": "NOT_IMPLEMENTED", "reason": "Invested-capital inputs are not in the normalized schema."},
        "roe": {"status": "NOT_IMPLEMENTED", "reason": "Not part of Quality v0.2."},
        "relative_evaluation": {"status": "NOT_IMPLEMENTED", "reason": "Peer-relative evaluation is deferred (DEC-011)."},
        "historical_evaluation": {"status": "NOT_IMPLEMENTED", "reason": "Own-history comparison belongs to a later stage."},
        "staleness_policy": {
            "status": "NOT_IMPLEMENTED",
            "reason": "Ages are recorded only; an evaluation-date staleness policy is decided before P7.",
        },
    }


def _v02_code_ok(code: Any) -> bool:
    if code is None:
        return True
    if not isinstance(code, str):
        return False
    return code.split(":", 1)[0] in QUALITY_REASON_CODES


def validate_quality_v02(data: dict) -> list[str]:
    """Structural self-check of calculate_quality_v02() output. Returns a
    list of failure strings (empty when valid)."""
    failures: list[str] = []
    if not isinstance(data, dict):
        return ["root"]
    if data.get("schema_version") != QUALITY_V02_SCHEMA_VERSION:
        failures.append("schema_version")
    element = data.get("element")
    if not isinstance(element, dict):
        return failures + ["element"]
    status = element.get("status")
    if status not in _V02_ELEMENT_STATUSES:
        failures.append("element.status")
    if not _v02_code_ok(element.get("reason_code")):
        failures.append("element.reason_code")
    for flag in element.get("flags") or []:
        if flag not in QUALITY_V02_FLAGS:
            failures.append(f"element.flags.{flag}")
    weights = element.get("normalized_score_weights")
    fraction = element.get("scored_weight_fraction")
    if status == "OK":
        if not _is_number(element.get("score")) or not isinstance(element.get("stars"), int):
            failures.append("element.score")
        if not (_is_number(fraction) and 0 < fraction <= 1 + 1e-12):
            failures.append("element.scored_weight_fraction")
        if not isinstance(weights, dict) or abs(sum(weights.values()) - 1.0) > 1e-12:
            failures.append("element.normalized_score_weights")
    else:
        for key in ("score", "stars", "scored_weight_fraction", "normalized_score_weights"):
            if element.get(key) is not None:
                failures.append(f"element.{key}")
    if status == "NOT_APPLICABLE":
        if data.get("components") != {} or data.get("periods") is not None:
            failures.append("not_applicable.computed")
        return failures
    components = data.get("components")
    expected = set(QUALITY_V02_INDEPENDENT_METRICS) | set(QUALITY_V02_INFORMATIONAL_METRICS)
    if not isinstance(components, dict) or set(components) != expected:
        return failures + ["components.keys"]
    for name, comp in components.items():
        cs = comp.get("status")
        if cs not in _V02_COMPONENT_STATUSES:
            failures.append(f"components.{name}.status")
        if not _v02_code_ok(comp.get("reason_code")):
            failures.append(f"components.{name}.reason_code")
        if cs == "OK":
            policy_floor = comp.get("reason_code") == "NONPOSITIVE_FCF_WITH_NET_DEBT"
            if not policy_floor and not _is_number(comp.get("value")):
                failures.append(f"components.{name}.value")
            if not (_is_number(comp.get("rating")) and 1.0 <= comp["rating"] <= 5.0):
                failures.append(f"components.{name}.rating")
        elif comp.get("value") is not None or comp.get("rating") is not None:
            failures.append(f"components.{name}.value_on_non_ok")
        if cs != "OK" and comp.get("reason_code") is None:
            failures.append(f"components.{name}.reason_code_missing")
    if "mixed_period_ratio" in components.get("debt_to_cash", {}):
        failures.append("debt_to_cash.mixed_period_ratio")
    return failures


def _v02_validate_or_raise(result: dict) -> None:
    failures = validate_quality_v02(result)
    if failures:
        raise ValueError(
            f"calculate_quality_v02() produced invalid output for "
            f"{result.get('ticker')!r}: {failures}"
        )


# ============================================================
# PUBLIC API
# ============================================================

__all__ = [
    "SCHEMA_VERSION",
    "QUALITY_METRICS",
    "QUALITY_WEIGHTS",
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
    "QUALITY_V02_SCHEMA_VERSION",
    "QUALITY_V02_INDEPENDENT_METRICS",
    "QUALITY_V02_SCORE_WEIGHTS",
    "QUALITY_V02_INFORMATIONAL_METRICS",
    "QUALITY_V02_DIMENSIONS",
    "QUALITY_REASON_CODES",
    "QUALITY_V02_FLAGS",
    "calculate_quality_v02",
    "validate_quality_v02",
]
