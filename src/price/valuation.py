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
     the concept; AND quarter evidence (validated-input contract): exactly
     four consecutive fiscal quarters, each 70-110 days, contiguous (next
     start = previous end + 1 day; no gap, no overlap), same concept /
     currency / scope, each filed < D, values summing exactly to NI_TTM,
     TTM bounds equal to first/last quarter, every quarter carrying an
     accession number and the same entity id as the TTM (provenance). PIT
     uses the date-only strict rule filed < D (P4 v1.4 section 3,
     P4_FILED_STRICTLY_BEFORE_PRICE_DATE): a same-day filing is not used
     because filing time vs. price time is unknown. No evidence -> MISSING
     (NI_TTM_UNVALIDATED). Period length 350-380 days is only a
     non-blocking warning (NI_TTM_PERIOD_ANOMALY).
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
# complete TTM; 52/53-week fiscal years fit). Outside the range the value is
# still computed but flagged NI_TTM_PERIOD_ANOMALY. Completeness is proven
# only by the four-quarter contract below.
TTM_PERIOD_DAYS = (350, 380)
# Standalone quarter length, same bound as the approved F1/F2 rule.
QUARTER_DAYS = (70, 110)

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
class TtmQuarter:
    """One standalone fiscal quarter supplied (already built) by the caller."""

    fiscal_year: int
    fiscal_quarter: int            # 1..4
    period_start: date
    period_end: date
    value: Decimal
    concept: str
    currency: str
    consolidation_scope: str
    filed: date
    accession: str = ""            # SEC accession number (provenance)
    entity_id: str = ""            # issuer identifier, e.g. CIK


@dataclass(frozen=True)
class NiTtmInput:
    """
    Validated-input contract for NI_TTM. `quarters` is the evidence: exactly
    four consecutive fiscal quarters whose values sum EXACTLY to `value`.
    Without quarter evidence the NI_TTM is not used (NI_TTM_UNVALIDATED).
    """

    value: Optional[Decimal]
    concepts: tuple[str, ...]
    currency: str
    period_start: Optional[date]
    period_end: Optional[date]
    latest_filed: Optional[date]
    consolidation_scope: Optional[str]
    attribution: Optional[str]
    quarters: tuple[TtmQuarter, ...] = ()
    entity_id: str = ""            # issuer the TTM is claimed for, e.g. CIK


def _missing(*reasons: str) -> MetricResult:
    return MetricResult(METRIC, CalcStatus.MISSING, reasons=tuple(reasons))


def _invalid(*reasons: str) -> MetricResult:
    return MetricResult(METRIC, CalcStatus.INVALID, reasons=tuple(reasons))


def _validate_quarters(ni: NiTtmInput, price_session: date) -> list[str]:
    qs = ni.quarters
    if not qs:
        return [Reason.NI_TTM_UNVALIDATED]
    reasons: list[str] = []
    keys = [(q.fiscal_year, q.fiscal_quarter) for q in qs]
    if len(set(keys)) != len(keys):
        reasons.append(Reason.NI_TTM_QUARTER_DUPLICATE)
    if len(qs) != 4:
        reasons.append(Reason.NI_TTM_QUARTERS_INCOMPLETE)
    if any(not 1 <= q.fiscal_quarter <= 4 for q in qs):
        reasons.append(Reason.NI_TTM_QUARTERS_NOT_CONSECUTIVE)
        return reasons
    ordered = sorted(qs, key=lambda q: (q.fiscal_year, q.fiscal_quarter))
    idx = [q.fiscal_year * 4 + q.fiscal_quarter - 1 for q in ordered]
    if any(b - a != 1 for a, b in zip(idx, idx[1:])):
        reasons.append(Reason.NI_TTM_QUARTERS_NOT_CONSECUTIVE)
    for q in ordered:
        days = (q.period_end - q.period_start).days + 1
        if not QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]:
            reasons.append(Reason.NI_TTM_QUARTER_LENGTH)
        if not q.filed < price_session or q.period_end >= price_session:
            reasons.append(Reason.NI_TTM_PIT_VIOLATION)
        if q.currency != CURRENCY_USD:
            reasons.append(Reason.NI_TTM_CURRENCY)
        if q.consolidation_scope != SCOPE_CONSOLIDATED_NO_DIMENSION:
            reasons.append(Reason.NI_TTM_SCOPE_UNCLEAR)
        if len(set(ni.concepts)) == 1 and q.concept != ni.concepts[0]:
            reasons.append(Reason.NI_TTM_CONCEPT_MIXED)
        if not q.accession or not q.entity_id:
            reasons.append(Reason.NI_TTM_PROVENANCE_MISSING)
        elif not ni.entity_id or q.entity_id != ni.entity_id:
            reasons.append(Reason.NI_TTM_ENTITY_MISMATCH)
    for a, b in zip(ordered, ordered[1:]):
        step = (b.period_start - a.period_end).days
        if step < 1:
            reasons.append(Reason.NI_TTM_PERIOD_OVERLAP)
        elif step > 1:
            reasons.append(Reason.NI_TTM_PERIOD_GAP)
    if ni.period_start != ordered[0].period_start or ni.period_end != ordered[-1].period_end:
        reasons.append(Reason.NI_TTM_PERIOD_INVALID)
    if sum((q.value for q in qs), Decimal(0)) != ni.value:
        reasons.append(Reason.NI_TTM_SUM_MISMATCH)
    return reasons


def validate_ni_ttm(ni: Optional[NiTtmInput], price_session: date) -> list[str]:
    """Blocking reason codes for an unusable NI_TTM (empty list = usable)."""
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
    if ni.period_start is None or ni.period_end is None \
            or ni.period_end >= price_session:
        reasons.append(Reason.NI_TTM_PERIOD_INVALID)
    reasons.extend(_validate_quarters(ni, price_session))
    return list(dict.fromkeys(reasons))  # de-duplicate, keep order


def ttm_period_warnings(ni: NiTtmInput) -> list[str]:
    """Non-blocking sanity warnings (auxiliary period-length check)."""
    if ni.period_start is None or ni.period_end is None:
        return []
    days = (ni.period_end - ni.period_start).days + 1
    if TTM_PERIOD_DAYS[0] <= days <= TTM_PERIOD_DAYS[1]:
        return []
    return [Reason.NI_TTM_PERIOD_ANOMALY]


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
    if ni_reasons in ([Reason.NI_TTM_MISSING], [Reason.NI_TTM_UNVALIDATED]):
        return _missing(*ni_reasons)
    if ni_reasons:
        return _invalid(*ni_reasons)
    if ni_ttm is None or ni_ttm.value is None:  # unreachable; keeps types explicit
        return _missing(Reason.NI_TTM_MISSING)

    share_by_id = {s.class_id: s for s in shares}
    market_cap = sum((p.raw_close * share_by_id[p.class_id].shares  # type: ignore[operator]
                      for p in prices), Decimal(0))
    warnings = tuple(ttm_period_warnings(ni_ttm))
    if ni_ttm.value <= 0:
        return MetricResult(METRIC, CalcStatus.OK, None,
                            Interpretability.NOT_MEANINGFUL,
                            (Reason.NI_TTM_NONPOSITIVE,) + warnings)
    return MetricResult(METRIC, CalcStatus.OK, market_cap / ni_ttm.value,
                        Interpretability.MEANINGFUL, warnings)
