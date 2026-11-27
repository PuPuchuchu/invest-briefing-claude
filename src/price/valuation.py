"""
PE_TTM valuation (P3 v7 section 2.1, P4 v1.3 section 3.4).

    PE_TTM(D) = sum_c RawClose_c(D) * SharesRolled_c(D) / NI_TTM(D)

This module does NOT import any SEC module and does NOT build a TTM.
NI_TTM and share counts are passed in by the caller together with their
status and PIT metadata; this module only validates them and computes.
In operation PE_TTM stays unavailable until P4 G6 (quarterly TTM block)
is lifted.

Gates (all must pass, otherwise no value):
  1. class mapping resolved, identical class sets for prices and shares
     (never one ticker's price x all-class shares)
  2. every class: RawPriceStatus OK, price session == D
  3. every class: ShareCountStatus OK
  4. split basis: shares rolled to exactly session D (basis_session == D);
     unknown basis is rejected
  5. share PIT: filed < D, as_of <= D, as_of <= filed
  6. corporate actions in the roll interval eligible (CONFIRMED or
     NO_EVENT_VERIFIED); PENDING_SEC / UNVERIFIED events are never rolled
  7. NI_TTM: present; latest_filed < D; single allowed concept;
     USD; consolidated, no-dimension scope; attribution consistent with
     the concept; period length within TTM_PERIOD_DAYS
  8. NI_TTM <= 0 -> OK + NOT_MEANINGFUL (no negative PE is ever produced)

Absent inputs -> MISSING; integrity violations -> INVALID.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from src.price.corporate_actions import CorporateActionAssessment
from src.price.schema import (
    CURRENCY_USD,
    CalcStatus,
    Interpretability,
    MetricResult,
    RawPriceStatus,
    Reason,
    ShareCountStatus,
)

NI_CONCEPT_COMMON = "NetIncomeLossAvailableToCommonStockholdersBasic"
NI_CONCEPT_PARENT = "NetIncomeLoss"
ATTRIBUTION_FOR_CONCEPT = {
    NI_CONCEPT_COMMON: "COMMON_STOCKHOLDERS",
    NI_CONCEPT_PARENT: "PARENT",
}
SCOPE_CONSOLIDATED_NO_DIMENSION = "CONSOLIDATED_NO_DIMENSION"
# Auxiliary sanity check only (NOT an approved threshold, NOT a proof of a
# complete TTM): covers 52/53-week fiscal years. TTM completeness (four
# consecutive fiscal quarters, no gaps/overlaps) must be validated upstream.
TTM_PERIOD_DAYS = (350, 380)

METRIC = "PE_TTM"


@dataclass(frozen=True)
class ClassPriceInput:
    class_id: str
    raw_close: Optional[Decimal]
    status: RawPriceStatus
    session: Optional[date]


@dataclass(frozen=True)
class ClassSharesInput:
    class_id: str
    shares: Optional[Decimal]
    status: ShareCountStatus
    shares_as_of: Optional[date]
    shares_filed: Optional[date]
    basis_session: Optional[date]  # session the count was rolled to


@dataclass(frozen=True)
class NiTtmInput:
    value: Optional[Decimal]
    concepts: tuple[str, ...]
    currency: str
    period_start: Optional[date]
    period_end: Optional[date]
    latest_filed: Optional[date]
    consolidation_scope: Optional[str]
    attribution: Optional[str]


def _missing(*reasons: str) -> MetricResult:
    return MetricResult(METRIC, CalcStatus.MISSING, reasons=tuple(reasons))


def _invalid(*reasons: str) -> MetricResult:
    return MetricResult(METRIC, CalcStatus.INVALID, reasons=tuple(reasons))


def validate_ni_ttm(ni: Optional[NiTtmInput], price_session: date) -> list[str]:
    """Reason codes for an unusable NI_TTM input (empty list = usable)."""
    if ni is None or ni.value is None:
        return [Reason.NI_TTM_MISSING]
    reasons: list[str] = []
    if ni.latest_filed is None or not ni.latest_filed < price_session:
        reasons.append(Reason.NI_TTM_PIT_VIOLATION)
    if len(set(ni.concepts)) != 1:
        reasons.append(Reason.NI_TTM_CONCEPT_MIXED)
    else:
        concept = ni.concepts[0]
        if concept not in ATTRIBUTION_FOR_CONCEPT:
            reasons.append(Reason.NI_TTM_CONCEPT_NOT_ALLOWED)
        elif ni.attribution != ATTRIBUTION_FOR_CONCEPT[concept]:
            reasons.append(Reason.NI_TTM_ATTRIBUTION_UNCLEAR)
    if ni.currency != CURRENCY_USD:
        reasons.append(Reason.NI_TTM_CURRENCY)
    if ni.consolidation_scope != SCOPE_CONSOLIDATED_NO_DIMENSION:
        reasons.append(Reason.NI_TTM_SCOPE_UNCLEAR)
    if ni.period_start is None or ni.period_end is None:
        reasons.append(Reason.NI_TTM_PERIOD_INVALID)
    else:
        days = (ni.period_end - ni.period_start).days + 1
        if not (TTM_PERIOD_DAYS[0] <= days <= TTM_PERIOD_DAYS[1]) \
                or ni.period_end >= price_session:
            reasons.append(Reason.NI_TTM_PERIOD_INVALID)
    return reasons


def compute_pe_ttm(*, prices: Sequence[ClassPriceInput],
                   shares: Sequence[ClassSharesInput],
                   class_mapping_resolved: bool,
                   corporate_actions: CorporateActionAssessment,
                   ni_ttm: Optional[NiTtmInput],
                   price_session: date) -> MetricResult:
    if not class_mapping_resolved or not prices or not shares:
        return _missing(Reason.CLASS_MAPPING_MISSING)
    price_ids = [p.class_id for p in prices]
    share_ids = [s.class_id for s in shares]
    if sorted(price_ids) != sorted(share_ids) or len(set(price_ids)) != len(price_ids):
        return _invalid(Reason.CLASS_SET_MISMATCH)

    for p in prices:
        if p.status is not RawPriceStatus.OK or p.raw_close is None \
                or p.session != price_session or p.raw_close <= 0:
            return _missing(Reason.RAW_PRICE_NOT_OK)

    for s in shares:
        if s.status is ShareCountStatus.NOT_AVAILABLE or s.shares is None:
            return _missing(Reason.SHARE_COUNT_NOT_AVAILABLE)
        if s.status is not ShareCountStatus.OK or s.shares <= 0:
            return _invalid(Reason.SHARE_COUNT_NOT_OK)
        if s.basis_session is None:
            return _invalid(Reason.SPLIT_BASIS_UNKNOWN)
        if s.basis_session != price_session:
            return _invalid(Reason.SPLIT_BASIS_MISMATCH)
        if s.shares_as_of is None or s.shares_filed is None \
                or not s.shares_filed < price_session \
                or not s.shares_as_of <= price_session \
                or not s.shares_as_of <= s.shares_filed:
            return _invalid(Reason.SHARES_PIT_VIOLATION)

    if not corporate_actions.valuation_roll_eligible:
        return _invalid(Reason.CORPORATE_ACTION_BLOCKS_VALUATION,
                        *corporate_actions.reasons)

    ni_reasons = validate_ni_ttm(ni_ttm, price_session)
    if ni_reasons == [Reason.NI_TTM_MISSING]:
        return _missing(Reason.NI_TTM_MISSING)
    if ni_reasons:
        return _invalid(*ni_reasons)
    if ni_ttm is None or ni_ttm.value is None:  # unreachable; keeps types explicit
        return _missing(Reason.NI_TTM_MISSING)

    share_by_id = {s.class_id: s for s in shares}
    market_cap = sum((p.raw_close * share_by_id[p.class_id].shares  # type: ignore[operator]
                      for p in prices), Decimal(0))
    if ni_ttm.value <= 0:
        return MetricResult(METRIC, CalcStatus.OK, None,
                            Interpretability.NOT_MEANINGFUL,
                            (Reason.NI_TTM_NONPOSITIVE,))
    return MetricResult(METRIC, CalcStatus.OK, market_cap / ni_ttm.value,
                        Interpretability.MEANINGFUL)
