"""
Common output schema for both execution paths (R1 GitHub runner, R2
Claude scheduled task).

Sections: Fact / Status / Freshness / Limitation / Validation, plus
reason codes, capture class, provider metadata, calculation and config
versions.

Capture class is always EPHEMERAL_ONLY. This module only builds an
in-memory dict; it has no function that writes anywhere.

Facts are restricted to ALLOWED_FACTS. Raw / adjusted OHLCV, price
series and price-level numbers (e.g. an SMA level) are never facts --
price-vs-SMA is reported as a percentage distance instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping, Optional, Sequence

from src.price.freshness import FreshnessResult
from src.price.schema import (
    CALCULATION_VERSION,
    SCHEMA_VERSION,
    AdjustedPriceStatus,
    CalcStatus,
    CaptureClass,
    CorporateActionStatus,
    Freshness,
    MetricResult,
    PriceAdjustmentQC,
    RawPriceStatus,
    Reason,
    ShareCountStatus,
)

RESULT_SCHEMA_VERSION = "price_result_v0.1"

# metric -> (unit, decimal places)
ALLOWED_FACTS: dict[str, tuple[str, int]] = {
    "RSI_14D": ("INDEX_0_100", 1),
    "PRICE_VS_SMA50_PCT": ("PERCENT", 2),
    "PRICE_VS_SMA200_PCT": ("PERCENT", 2),
    "PRICE_VS_SMA40W_PCT": ("PERCENT", 2),
    "PE_TTM": ("RATIO", 2),
}
ROUNDING_RULE = "ROUND_HALF_UP"

STANDING_LIMITATIONS = (
    "EPHEMERAL_ONLY_NO_PRICE_RESULT_RETENTION",
    "PROVIDER_REVISIONS_BETWEEN_RUNS_UNDETECTABLE",
    "HISTORY_INPUTS_NON_PIT_REFETCHED",
)


@dataclass(frozen=True)
class StatusBlock:
    corporate_action: CorporateActionStatus
    price_adjustment_qc: PriceAdjustmentQC
    share_count: ShareCountStatus
    raw_price: RawPriceStatus
    adjusted_price: AdjustedPriceStatus


@dataclass(frozen=True)
class ProviderMetadata:
    provider_id: str
    provider_schema_version: str
    adjustment_method: str
    data_as_of: Optional[date]
    retrieved_at: Optional[datetime]


def _round(value: Decimal, places: int) -> str:
    q = Decimal(1).scaleb(-places)
    return str(value.quantize(q, rounding=ROUND_HALF_UP))


def build_result(*, ticker: str, evaluation_date: date,
                 metrics: Mapping[str, MetricResult],
                 statuses: StatusBlock,
                 freshness: FreshnessResult,
                 provider: ProviderMetadata,
                 config_version: str,
                 extra_limitations: Sequence[str] = ()) -> dict:
    facts = []
    reason_codes: list[str] = []
    any_invalid = False
    for name, m in metrics.items():
        if name not in ALLOWED_FACTS or m.name != name:
            raise ValueError(Reason.FACT_NOT_ALLOWED)
        unit, places = ALLOWED_FACTS[name]
        value = None
        if m.calc_status is CalcStatus.OK and m.value is not None:
            value = _round(m.value, places)
        if m.calc_status is CalcStatus.INVALID:
            any_invalid = True
        facts.append({
            "metric": name,
            "calc_status": m.calc_status.value,
            "interpretability": None if m.interpretability is None
            else m.interpretability.value,
            "value": value,
            "unit": unit,
            "rounding": f"{ROUNDING_RULE}:{places}",
            "state": m.state,
            "reasons": list(m.reasons),
        })
        reason_codes.extend(m.reasons)
    if freshness.reason:
        reason_codes.append(freshness.reason)

    data_success = freshness.status is Freshness.FRESH and not any_invalid
    return {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "capture_class": CaptureClass.EPHEMERAL_ONLY.value,
        "ticker": ticker,
        "evaluation_date": evaluation_date.isoformat(),
        "calculation_version": CALCULATION_VERSION,
        "config_version": config_version,
        "provider": {
            "provider_id": provider.provider_id,
            "provider_schema_version": provider.provider_schema_version,
            "engine_schema_version": SCHEMA_VERSION,
            "adjustment_method": provider.adjustment_method,
            "data_as_of": None if provider.data_as_of is None
            else provider.data_as_of.isoformat(),
            "retrieved_at": None if provider.retrieved_at is None
            else provider.retrieved_at.isoformat(),
        },
        "facts": facts,
        "status": {
            "corporate_action": statuses.corporate_action.value,
            "price_adjustment_qc": statuses.price_adjustment_qc.value,
            "share_count": statuses.share_count.value,
            "raw_price": statuses.raw_price.value,
            "adjusted_price": statuses.adjusted_price.value,
        },
        "freshness": {
            "status": freshness.status.value,
            "expected_session": freshness.expected_session.isoformat(),
            "actual_session": None if freshness.actual_session is None
            else freshness.actual_session.isoformat(),
        },
        "limitations": list(STANDING_LIMITATIONS) + list(extra_limitations),
        "validation": {
            "facts_allowed_only": True,
            "data_success": data_success,
        },
        "reason_codes": sorted(set(reason_codes)),
    }
