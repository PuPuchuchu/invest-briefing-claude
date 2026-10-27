from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any


# ============================================================
# CONFIGURATION
# ============================================================

SCHEMA_VERSION = "derived_metrics_v0.1"


# Round AV: sentinel default for the optional unit-of-measure keyword
# arguments on unit-sensitive combinations below (calculate_margin,
# calculate_fcf, calculate_net_debt, calculate_debt_to_cash). Using a
# distinct sentinel -- rather than None -- for "the caller supplied no
# unit information at all" lets that case be told apart from "the
# caller explicitly recorded an unknown/absent unit" (a real SEC-
# normalized metric dict whose own "unit" field is None). Only the
# latter must be fail-closed (never silently treated as compatible);
# the former must leave every pre-existing caller -- direct numeric
# calls, and the historical-record shape used by YoY/CAGR/TTM, none of
# which model units at all -- completely unaffected.
_UNIT_UNCHECKED = object()


def _resolve_unit_pair(unit_a: Any, unit_b: Any) -> str:
    """
    Classify a pair of unit-of-measure arguments for a unit-sensitive
    combination of two independently-sourced monetary metrics (a sum,
    difference, or ratio).

    Returns one of:
        "unchecked"   Neither side supplied unit information at all
                       (both _UNIT_UNCHECKED). Pre-existing callers
                       that never modeled units behave exactly as
                       before this round.
        "missing"      At least one side's unit is unknown/empty once
                       checking is engaged (one side supplied
                       _UNIT_UNCHECKED while the other supplied real
                       unit information, or a supplied unit is
                       falsy/None). Never treated as compatible.
        "mismatched"  Both sides have a known, non-equal unit.
        "compatible"  Both sides have the same known unit.
    """
    if unit_a is _UNIT_UNCHECKED and unit_b is _UNIT_UNCHECKED:
        return "unchecked"

    resolved_a = None if unit_a is _UNIT_UNCHECKED else unit_a
    resolved_b = None if unit_b is _UNIT_UNCHECKED else unit_b

    if not resolved_a or not resolved_b:
        return "missing"

    if resolved_a != resolved_b:
        return "mismatched"

    return "compatible"


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


def _parse_period_end(value: Any) -> date | None:
    """
    Parse a "period_end" field as an actual calendar date.

    Returns None for anything that is not a valid ISO "YYYY-MM-DD"
    date string -- including None, non-string types, and malformed
    strings. A non-None, non-parseable value (e.g. "not-a-date") must
    never be treated as a usable date: a prior implementation only
    checked "is not None" here and then compared period_end values as
    plain strings, which let a malformed string outrank a real ISO
    date lexically (e.g. "not-a-date" > "2025-12-31" under ASCII
    ordering) and be silently selected as the "latest" observation
    (Round AW).
    """
    if not isinstance(value, str):
        return None

    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


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
    *,
    numerator_unit: Any = _UNIT_UNCHECKED,
    revenue_unit: Any = _UNIT_UNCHECKED,
) -> dict:
    """
    Calculate a revenue-based margin.

    Formula:
        numerator / revenue

    Examples:
        operating income / revenue -> operating margin
        net income / revenue       -> net margin
        FCF / revenue              -> FCF margin

    Round AV: numerator_unit/revenue_unit are optional. When supplied,
    a known mismatch between the two currencies is INVALID (refuses
    to divide across units without an approved FX conversion policy)
    and an unprovable/missing unit on either side is MISSING. When
    neither is supplied at all, behavior is unchanged from before this
    round -- see _resolve_unit_pair()'s docstring.
    """

    result_name = f"{metric_name}_margin"
    source_metrics = [metric_name, "revenue"]

    if not _is_number(numerator_value):
        return _build_missing_metric(
            result_name,
            "Numerator is missing or non-numeric.",
            source_metrics,
        )

    if not _is_number(revenue_value):
        return _build_missing_metric(
            result_name,
            "Revenue is missing or non-numeric.",
            source_metrics,
        )

    unit_relationship = _resolve_unit_pair(numerator_unit, revenue_unit)

    if unit_relationship == "missing":
        result = _build_missing_metric(
            result_name,
            f"Unit metadata is missing for {metric_name} and/or "
            "revenue; refusing to compute a margin without a known "
            "unit of measure for both inputs.",
            source_metrics,
        )
        result["unit_validation"] = "MISSING"
        return result

    if unit_relationship == "mismatched":
        result = _build_invalid_metric(
            result_name,
            f"{metric_name} unit ({numerator_unit!r}) does not match "
            f"revenue unit ({revenue_unit!r}); refusing to compute a "
            "margin across different units of measure without an "
            "approved FX conversion policy.",
            source_metrics,
        )
        result["unit_validation"] = "MISMATCHED"
        return result

    # Round AV-1: "unit_relationship" here is only ever "compatible"
    # (both sides supplied a matching unit) or "unchecked" (neither
    # side supplied unit information at all -- the entire pre-Round-
    # AV call surface). Both previously produced an indistinguishable
    # {"status": "OK", ...} shape; this field makes which one
    # actually happened externally observable, on every return from
    # this point on regardless of the eventual status, so a result
    # never silently implies validation that did not occur.
    unit_validation_state = (
        "VALIDATED" if unit_relationship == "compatible" else "UNCHECKED"
    )

    revenue_float = float(revenue_value)

    if revenue_float == 0:
        result = _build_invalid_metric(
            result_name,
            "Revenue is zero; margin cannot be calculated.",
            [metric_name, "revenue"],
        )
        result["unit_validation"] = unit_validation_state
        return result

    if revenue_float < 0:
        result = _build_invalid_metric(
            result_name,
            "Revenue is negative; margin cannot be calculated.",
            [metric_name, "revenue"],
        )
        result["unit_validation"] = unit_validation_state
        return result

    value = float(numerator_value) / revenue_float

    result = _build_ok_metric(
        result_name,
        value,
        [metric_name, "revenue"],
    )
    result["unit_validation"] = unit_validation_state
    return result


# ============================================================
# FREE CASH FLOW
# ============================================================

def calculate_fcf(
    cfo_value: Any,
    capex_value: Any,
    *,
    cfo_unit: Any = _UNIT_UNCHECKED,
    capex_unit: Any = _UNIT_UNCHECKED,
) -> dict:
    """
    Calculate Free Cash Flow.

    CapEx is treated as the magnitude of a capital expenditure --
    i.e. a cash outflow -- regardless of the sign the source concept
    happens to carry. sec_normalizer.py preserves SEC's raw observed
    value as-is (no sign normalization is applied upstream, by
    design -- see the module's own passthrough contract), and in
    practice different filers/concepts report this magnitude with
    different signs (e.g. positive for some real-world filings).
    Taking the absolute value here makes the formula correct under
    either convention, without requiring any upstream reinterpretation
    of the raw SEC value.

    Therefore:
        FCF = CFO - abs(CapEx)

    Example (CapEx reported as a negative cash-flow value):
        CFO   = 25,000
        CapEx = -5,000
        FCF   = 20,000

    Example (CapEx reported as a positive expenditure magnitude):
        CFO   = 25,000
        CapEx = 5,000
        FCF   = 20,000

    2026-10-18 fix (Round AC/AD): previously this function computed
    `CFO + CapEx` under the assumption that CapEx always arrived
    negative. That assumption does not hold for every filer/concept
    (confirmed against real SEC data for at least one issuer), which
    made the result CFO-overstated whenever CapEx was reported
    positive. Using abs() removes the dependency on that assumption
    entirely. This does not change sec_normalizer.py's own stored
    CapEx value, which remains the raw SEC observation unchanged.
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

    unit_relationship = _resolve_unit_pair(cfo_unit, capex_unit)

    if unit_relationship == "missing":
        result = _build_missing_metric(
            "fcf",
            "Unit metadata is missing for cfo and/or capex; refusing "
            "to combine cash-flow values without a known unit of "
            "measure for both inputs.",
            ["cfo", "capex"],
        )
        result["unit_validation"] = "MISSING"
        return result

    if unit_relationship == "mismatched":
        result = _build_invalid_metric(
            "fcf",
            f"cfo unit ({cfo_unit!r}) does not match capex unit "
            f"({capex_unit!r}); refusing to combine cash-flow values "
            "reported in different units of measure without an "
            "approved FX conversion policy.",
            ["cfo", "capex"],
        )
        result["unit_validation"] = "MISMATCHED"
        return result

    value = float(cfo_value) - abs(float(capex_value))

    result = _build_ok_metric(
        "fcf",
        value,
        ["cfo", "capex"],
    )

    if unit_relationship == "compatible":
        result["unit"] = cfo_unit
        result["unit_validation"] = "VALIDATED"
    else:
        result["unit_validation"] = "UNCHECKED"

    return result


# ============================================================
# DEBT / CASH
# ============================================================

def calculate_net_debt(
    total_debt_value: Any,
    cash_value: Any,
    *,
    total_debt_unit: Any = _UNIT_UNCHECKED,
    cash_unit: Any = _UNIT_UNCHECKED,
) -> dict:
    """
    Calculate net debt.

    Formula:
        Net Debt = Total Debt - Cash

    Round AV: total_debt_unit/cash_unit are optional; see
    _resolve_unit_pair()'s docstring for the exact contract. When both
    are known and equal, the resolved unit is propagated onto the
    output (mirrors sec_normalizer.py's calculate_total_debt()).
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

    unit_relationship = _resolve_unit_pair(total_debt_unit, cash_unit)

    if unit_relationship == "missing":
        result = _build_missing_metric(
            "net_debt",
            "Unit metadata is missing for total_debt and/or cash; "
            "refusing to combine values without a known unit of "
            "measure for both inputs.",
            ["total_debt", "cash"],
        )
        result["unit_validation"] = "MISSING"
        return result

    if unit_relationship == "mismatched":
        result = _build_invalid_metric(
            "net_debt",
            f"total_debt unit ({total_debt_unit!r}) does not match "
            f"cash unit ({cash_unit!r}); refusing to combine values "
            "reported in different units of measure without an "
            "approved FX conversion policy.",
            ["total_debt", "cash"],
        )
        result["unit_validation"] = "MISMATCHED"
        return result

    value = float(total_debt_value) - float(cash_value)

    result = _build_ok_metric(
        "net_debt",
        value,
        ["total_debt", "cash"],
    )

    if unit_relationship == "compatible":
        result["unit"] = total_debt_unit
        result["unit_validation"] = "VALIDATED"
    else:
        result["unit_validation"] = "UNCHECKED"

    return result


def calculate_debt_to_cash(
    total_debt_value: Any,
    cash_value: Any,
    *,
    total_debt_unit: Any = _UNIT_UNCHECKED,
    cash_unit: Any = _UNIT_UNCHECKED,
) -> dict:
    """
    Calculate debt-to-cash ratio.

    Formula:
        Total Debt / Cash

    Round AV: total_debt_unit/cash_unit are optional; see
    _resolve_unit_pair()'s docstring. The output is a dimensionless
    ratio and carries no unit, even when both inputs are known and
    equal.
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

    unit_relationship = _resolve_unit_pair(total_debt_unit, cash_unit)

    if unit_relationship == "missing":
        result = _build_missing_metric(
            "debt_to_cash",
            "Unit metadata is missing for total_debt and/or cash; "
            "refusing to divide values without a known unit of "
            "measure for both inputs.",
            ["total_debt", "cash"],
        )
        result["unit_validation"] = "MISSING"
        return result

    if unit_relationship == "mismatched":
        result = _build_invalid_metric(
            "debt_to_cash",
            f"total_debt unit ({total_debt_unit!r}) does not match "
            f"cash unit ({cash_unit!r}); refusing to divide values "
            "reported in different units of measure without an "
            "approved FX conversion policy.",
            ["total_debt", "cash"],
        )
        result["unit_validation"] = "MISMATCHED"
        return result

    unit_validation_state = (
        "VALIDATED" if unit_relationship == "compatible" else "UNCHECKED"
    )

    cash_float = float(cash_value)

    if cash_float == 0:
        result = _build_invalid_metric(
            "debt_to_cash",
            "Cash is zero; debt-to-cash ratio cannot be calculated.",
            ["total_debt", "cash"],
        )
        result["unit_validation"] = unit_validation_state
        return result

    value = float(total_debt_value) / cash_float

    result = _build_ok_metric(
        "debt_to_cash",
        value,
        ["total_debt", "cash"],
    )
    result["unit_validation"] = unit_validation_state
    return result


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

def _capex_scope_metadata(capex) -> dict:
    """Scope/source metadata to carry from a broader-scope CapEx input onto
    FCF-based derived metrics (STEP 15). Empty when CapEx has no
    concept_scope label, so PP&E-based results are unchanged."""
    if not isinstance(capex, dict) or not capex.get("concept_scope"):
        return {}
    return {
        "input_scope": {
            "capex": {
                "concept": capex.get("concept"),
                "concept_scope": capex.get("concept_scope"),
                "accession": capex.get("accession"),
                "period_end": capex.get("period_end"),
            }
        },
        "peer_comparison_eligibility": "RESTRICTED_INPUT_SCOPE",
    }


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

    # Round AV: extract each input's "unit" field (when the metric
    # dict carries one at all) so the unit-sensitive combinations
    # below can refuse to silently combine mismatched or unknown
    # currencies. Using .get("unit", _UNIT_UNCHECKED) -- rather than
    # .get("unit") -- means a caller whose metric dict has no "unit"
    # key at all (the pre-Round-AV fixture/interface shape) is told
    # apart from one that has the key with an explicit None value
    # (a real SEC-normalized observation with no unit of measure
    # recorded); only the latter is fail-closed. See
    # _resolve_unit_pair()'s docstring.
    cfo_unit = (
        cfo.get("unit", _UNIT_UNCHECKED)
        if isinstance(cfo, dict)
        else _UNIT_UNCHECKED
    )
    capex_unit = (
        capex.get("unit", _UNIT_UNCHECKED)
        if isinstance(capex, dict)
        else _UNIT_UNCHECKED
    )

    result["derived_metrics"]["fcf"] = calculate_fcf(
        cfo.get("value") if isinstance(cfo, dict) else None,
        capex.get("value") if isinstance(capex, dict) else None,
        cfo_unit=cfo_unit,
        capex_unit=capex_unit,
    )

    # STEP 15 (2026-10-27): metadata only. When CapEx was taken from a
    # broader-scope concept (e.g. PaymentsToAcquireProductiveAssets), carry
    # its scope and source onto FCF so a consumer cannot treat it as
    # PP&E-based FCF. The FCF formula, value and status are unchanged.
    capex_scope_meta = _capex_scope_metadata(capex)
    if capex_scope_meta:
        result["derived_metrics"]["fcf"].update(capex_scope_meta)

    # --------------------------------------------------------
    # Current-period margins
    # --------------------------------------------------------

    revenue = metrics.get("revenue", {})

    revenue_value = (
        revenue.get("value")
        if isinstance(revenue, dict)
        else None
    )

    revenue_unit = (
        revenue.get("unit", _UNIT_UNCHECKED)
        if isinstance(revenue, dict)
        else _UNIT_UNCHECKED
    )

    net_income = metrics.get("net_income", {})
    operating_income = metrics.get("operating_income", {})

    net_income_unit = (
        net_income.get("unit", _UNIT_UNCHECKED)
        if isinstance(net_income, dict)
        else _UNIT_UNCHECKED
    )
    operating_income_unit = (
        operating_income.get("unit", _UNIT_UNCHECKED)
        if isinstance(operating_income, dict)
        else _UNIT_UNCHECKED
    )

    result["derived_metrics"]["net_margin"] = calculate_margin(
        "net_income",
        net_income.get("value")
        if isinstance(net_income, dict)
        else None,
        revenue_value,
        numerator_unit=net_income_unit,
        revenue_unit=revenue_unit,
    )

    result["derived_metrics"]["operating_margin"] = calculate_margin(
        "operating_income",
        operating_income.get("value")
        if isinstance(operating_income, dict)
        else None,
        revenue_value,
        numerator_unit=operating_income_unit,
        revenue_unit=revenue_unit,
    )

    # --------------------------------------------------------
    # Current-period FCF margin
    # --------------------------------------------------------

    fcf_metric = result["derived_metrics"]["fcf"]
    fcf_value = fcf_metric.get("value")
    fcf_unit = fcf_metric.get("unit", _UNIT_UNCHECKED)

    result["derived_metrics"]["fcf_margin"] = calculate_margin(
        "fcf",
        fcf_value,
        revenue_value,
        numerator_unit=fcf_unit,
        revenue_unit=revenue_unit,
    )
    if capex_scope_meta:
        result["derived_metrics"]["fcf_margin"].update(capex_scope_meta)

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

    total_debt_unit = (
        total_debt.get("unit", _UNIT_UNCHECKED)
        if isinstance(total_debt, dict)
        else _UNIT_UNCHECKED
    )
    cash_unit = (
        cash.get("unit", _UNIT_UNCHECKED)
        if isinstance(cash, dict)
        else _UNIT_UNCHECKED
    )

    result["derived_metrics"]["net_debt"] = calculate_net_debt(
        total_debt_value,
        cash_value,
        total_debt_unit=total_debt_unit,
        cash_unit=cash_unit,
    )

    result["derived_metrics"]["debt_to_cash"] = calculate_debt_to_cash(
        total_debt_value,
        cash_value,
        total_debt_unit=total_debt_unit,
        cash_unit=cash_unit,
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

# Round AW: the defensible window (in days) for treating two
# "period_end" dates as "approximately one year apart" for a YoY
# comparison. 300-430 days is wide enough to accommodate real-world
# fiscal-calendar variation -- a 52/53-week fiscal year's period end
# can legitimately drift by a few weeks year to year -- while still
# excluding, with a wide margin on both sides, an adjacent-quarter gap
# (~90 days), a semi-annual gap (~182 days), and a multi-year gap from
# a missing intermediate observation (~730+ days). This is derived
# from the "period_end" dates already present in the input, not an
# invented tolerance chosen to force a particular test to pass.
_YOY_MIN_DAYS_APART = 300
_YOY_MAX_DAYS_APART = 430


def _calculate_history_yoy(
    metric_name: str,
    history: list[dict],
) -> dict:
    """
    Calculate YoY by comparing the LATEST valid historical observation
    against the best eligible prior-year comparator found anywhere
    else in the supplied history.

    Records must contain:
        period_end  (an ISO "YYYY-MM-DD" date string)
        value

    The list itself does not have to be sorted, and is not required
    to be annual-only. Nothing in this interface (no period_start,
    duration, or fiscal-period/frequency label on a record) states or
    implies that "history" must contain only annual observations, and
    neither calculate_derived_metrics()'s docstring nor this module's
    tests constrain it that way (Round AW-1, Part A) -- so quarterly,
    or mixed-frequency, history is within the supported contract.

    Round AW introduced period_end parsing (rather than lexical
    string comparison) and the _YOY_MIN_DAYS_APART.._YOY_MAX_DAYS_APART
    window, but only ever applied that window to the two MOST RECENT
    valid records (by date), rejecting the pair outright if that
    specific pair fell outside the window -- even when a genuine
    prior-year comparator for the latest observation existed further
    back in a longer (e.g. quarterly) history. That contradicted this
    function's own stated goal (finding a pair "approximately one
    year apart"), not merely an annual-only scope limitation: e.g. for
    quarterly history ending ...2024-12-31, 2025-03-31, the latest
    observation (2025-03-31) has a valid prior-year comparator at
    2024-03-31 sitting a few records further back, which the pre-
    Round-AW-1 implementation never looked for because it only ever
    compared 2025-03-31 against the second-most-recent record,
    2024-12-31 (~90 days apart) (Round AW-1).

    Round AW-1 fix: take the single latest valid observation as
    "current", then search EVERY other valid record for the one whose
    elapsed time from "current" falls within
    _YOY_MIN_DAYS_APART.._YOY_MAX_DAYS_APART days. When more than one
    candidate qualifies (e.g. annual history with several years of
    observations), the candidate whose elapsed time is closest to 365
    days is chosen, so a true prior-year comparator is always
    preferred over one that merely happens to also fall inside the
    tolerance window further back. When no candidate qualifies at all
    -- including when the two most-recent records are adjacent
    quarters, duplicates, or separated by a missing intermediate
    observation -- the result is INVALID: the immediately preceding
    record is never silently substituted for a real prior-year
    comparator just because it is the only one available.

    This function still cannot determine whether a given record is
    itself an annual or a quarterly observation (the {period_end,
    value} interface carries no period_start, duration, or fiscal-
    period/frequency label) -- only the elapsed time BETWEEN records
    is checked, which is the one piece of alignment information
    period_end actually carries.
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

        parsed_period_end = _parse_period_end(record.get("period_end"))
        value = record.get("value")

        if parsed_period_end is None or not _is_number(value):
            continue

        valid_records.append((parsed_period_end, record))

    if len(valid_records) < 2:
        return _build_missing_metric(
            result_name,
            "At least two valid historical observations with a "
            "parseable period_end are required.",
            [metric_name],
        )

    valid_records.sort(key=lambda item: item[0], reverse=True)

    current_period_end, current = valid_records[0]

    # Search every other valid record for an eligible prior-year
    # comparator to the latest observation, rather than only ever
    # considering the second-most-recent record. Candidates are
    # ranked by how close their elapsed time is to 365 days, so the
    # closest genuine prior-year match wins when more than one record
    # happens to fall inside the tolerance window (e.g. a long annual
    # history); ties are broken by the smaller elapsed-day count
    # (the nearer candidate).
    candidates = []

    for previous_period_end, previous in valid_records[1:]:
        days_apart = (current_period_end - previous_period_end).days

        if _YOY_MIN_DAYS_APART <= days_apart <= _YOY_MAX_DAYS_APART:
            candidates.append(
                (abs(days_apart - 365), days_apart, previous_period_end, previous)
            )

    if not candidates:
        other_period_ends = ", ".join(
            period_end.isoformat()
            for period_end, _ in valid_records[1:]
        )
        return _build_invalid_metric(
            result_name,
            f"No historical observation is approximately one year "
            f"before the latest observation ({current_period_end.isoformat()}); "
            f"the other valid period_end(s) ({other_period_ends}) are each "
            f"outside the {_YOY_MIN_DAYS_APART}-{_YOY_MAX_DAYS_APART} day "
            "window; refusing to label this a year-over-year comparison.",
            [metric_name],
        )

    candidates.sort(key=lambda item: (item[0], item[1]))
    _, _, previous_period_end, previous = candidates[0]

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