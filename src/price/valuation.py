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
from datetime import date, timedelta
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


# ===========================================================================
# EPS path (P4 G6, GPT-approved conditional scope 2026-11-28)
#
#   PE_TTM = latest-session raw close / sum of FOUR directly reported
#            3-month diluted EPS values (consecutive, non-overlapping)
#            -- or, only when fiscal Q4 is not reported as a 3-month value,
#            the directly reported FY diluted EPS whose period is EXACTLY
#            the TTM target period (method FY_DIRECT).
#   PE_FY  = latest-session raw close / latest directly reported FY
#            diluted EPS (10-K / 10-K/A).
#
# Never reconstructed: no FY - 9M, no 9M - 6M, no NI / shares, no share
# estimate. Missing fiscal Q4 -> MISSING / Q4_EPS_NOT_REPORTED.
#
# Period choice (PIT): only facts filed strictly before the price session
# (same rule as NI_TTM: P4_FILED_STRICTLY_BEFORE_PRICE_DATE). For each
# (start, end) the latest such filing is used (amendments and split
# restatements included); equal latest filing date with different values
# -> INVALID / EPS_VALUE_CONFLICT.
#
# Split basis (both metrics): the price is today's raw close, so every EPS
# value used must be on the post-split basis of every split executed after
# it was filed. Evidence = provider split events (they define the PRICE
# basis) plus SEC cover-page shares outstanding (SEC-internal check):
#   * split evidence for the interval not verified -> EPS_SPLIT_BASIS_UNVERIFIED
#   * a split executed after an EPS value's filing -> EPS_SPLIT_BASIS_UNRESTATED
#   * SEC shares jump >= 1.5x (or <= 1/1.5x) between filings with no split
#     event in between -> EPS_SPLIT_BASIS_MISMATCH
#   * SEC shares not available -> limitation SEC_SPLIT_EVIDENCE_UNAVAILABLE
# No manual split-ratio adjustment is ever applied.
# ===========================================================================

EPS_CONCEPT_DILUTED = "EarningsPerShareDiluted"
EPS_UNIT = "USD/shares"
FY_DAYS = (350, 380)
SPLIT_SHARE_JUMP = Decimal("1.5")
METRIC_PE_TTM, METRIC_PE_FY = "PE_TTM", "PE_FY"
METRIC_EPS_TTM, METRIC_EPS_FY = "EPS_TTM", "EPS_FY"
_FY_FORMS = frozenset({"10-K", "10-K/A"})


@dataclass(frozen=True)
class EpsFact:
    start: date
    end: date
    value: Decimal
    filed: date
    accession: str
    form: str
    unit: str = EPS_UNIT
    concept: str = EPS_CONCEPT_DILUTED
    fy: Optional[int] = None
    fp: Optional[str] = None

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class SharesFact:
    """SEC cover-page shares outstanding (dei), instant value."""
    as_of: date
    value: Decimal
    filed: date


@dataclass(frozen=True)
class SplitBasisEvidence:
    """Price-basis split events for the symbol, from the price provider."""
    verified_from: Optional[date]     # events are known complete in (from, to]
    verified_to: Optional[date]
    execution_dates: tuple[date, ...] = ()


@dataclass(frozen=True)
class EpsSelection:
    status: CalcStatus
    value: Optional[Decimal]
    facts: tuple[EpsFact, ...]
    method: Optional[str]
    reasons: tuple[str, ...]


def _sel_fail(status: CalcStatus, *reasons: str) -> EpsSelection:
    return EpsSelection(status, None, (), None, tuple(reasons))


def _validate_eps_facts(facts: Sequence[EpsFact]) -> Optional[str]:
    for f in facts:
        if f.concept != EPS_CONCEPT_DILUTED:
            return Reason.EPS_CONCEPT_MISMATCH
        if f.unit != EPS_UNIT:
            return Reason.EPS_UNIT_MISMATCH
        if isinstance(f.value, bool) or not isinstance(f.value, Decimal) \
                or not f.value.is_finite() or f.end < f.start:
            return Reason.EPS_SOURCE_MALFORMED
    return None


def _pit_latest(facts: Sequence[EpsFact], session: date):
    """(start, end) -> latest fact filed strictly before session, or a conflict."""
    by: dict[tuple[date, date], list[EpsFact]] = {}
    for f in facts:
        if f.filed < session:
            by.setdefault((f.start, f.end), []).append(f)
    out: dict[tuple[date, date], EpsFact] = {}
    for k, fs in by.items():
        last = max(f.filed for f in fs)
        latest = [f for f in fs if f.filed == last]
        if len({f.value for f in latest}) > 1:
            return None
        out[k] = sorted(latest, key=lambda f: f.accession)[-1]
    return out


def _is_ytd(days: int) -> bool:
    """Six- or nine-month year-to-date span (never used, never a target)."""
    return 170 <= days <= 200 or 255 <= days <= 290


def select_eps_fy(facts: Sequence[EpsFact], session: date) -> EpsSelection:
    bad = _validate_eps_facts(facts)
    if bad:
        return _sel_fail(CalcStatus.INVALID, bad)
    latest = _pit_latest(facts, session)
    if latest is None:
        return _sel_fail(CalcStatus.INVALID, Reason.EPS_VALUE_CONFLICT)
    fys = [f for f in latest.values()
           if f.form in _FY_FORMS and FY_DAYS[0] <= f.days <= FY_DAYS[1]]
    if not fys:
        return _sel_fail(CalcStatus.MISSING, Reason.EPS_NOT_REPORTED)
    fy = max(fys, key=lambda f: f.end)
    return EpsSelection(CalcStatus.OK, fy.value, (fy,), "FY_DIRECT", ())


def select_eps_ttm(facts: Sequence[EpsFact], session: date) -> EpsSelection:
    bad = _validate_eps_facts(facts)
    if bad:
        return _sel_fail(CalcStatus.INVALID, bad)
    latest = _pit_latest(facts, session)
    if latest is None:
        return _sel_fail(CalcStatus.INVALID, Reason.EPS_VALUE_CONFLICT)
    if not latest:
        return _sel_fail(CalcStatus.MISSING, Reason.EPS_NOT_REPORTED)
    quarters = {k: f for k, f in latest.items() if QUARTER_DAYS[0] <= f.days <= QUARTER_DAYS[1]}
    fys = {k: f for k, f in latest.items()
           if f.form in _FY_FORMS and FY_DAYS[0] <= f.days <= FY_DAYS[1]}
    # latest reported period end: 3-month values, 10-K FY values, and any
    # non-standard-length period (so it is reported as LENGTH_INVALID rather
    # than silently skipped); H / 9M year-to-date values never set the target
    reported = [f for f in latest.values() if not _is_ytd(f.days)
                and not (FY_DAYS[0] <= f.days <= FY_DAYS[1] and f.form not in _FY_FORMS)]
    if not reported:
        return _sel_fail(CalcStatus.MISSING, Reason.EPS_NOT_REPORTED)
    target_end = max(f.end for f in reported)
    fy_ends = {f.end for f in fys.values()}

    chain: list[EpsFact] = []
    need_end = target_end
    failure: Optional[str] = None
    while len(chain) < 4:
        cands = [f for f in quarters.values() if f.end == need_end]
        if len(cands) > 1:
            failure = Reason.EPS_PERIOD_OVERLAP
            break
        if not cands:
            others = [f for (s, e), f in latest.items()
                      if e == need_end and not (QUARTER_DAYS[0] <= f.days <= QUARTER_DAYS[1])
                      and not _is_ytd(f.days)
                      and not (FY_DAYS[0] <= f.days <= FY_DAYS[1])]
            if others:
                failure = Reason.EPS_PERIOD_LENGTH_INVALID
            elif need_end in fy_ends:
                failure = Reason.Q4_EPS_NOT_REPORTED
            elif any(f.start <= need_end < f.end for f in quarters.values()):
                failure = Reason.EPS_PERIOD_OVERLAP
            else:
                failure = Reason.EPS_PERIOD_DISCONTINUITY
            break
        q = cands[0]
        chain.append(q)
        need_end = q.start - timedelta(days=1)

    if failure is None:
        chain.reverse()
        total = sum((f.value for f in chain), Decimal(0))
        return EpsSelection(CalcStatus.OK, total, tuple(chain), "DIRECT_4Q", ())

    # FY_DIRECT fallback: only when fiscal Q4 is not reported as 3 months and
    # the latest 10-K FY period IS the TTM target period exactly.
    if failure == Reason.Q4_EPS_NOT_REPORTED:
        exact = [f for f in fys.values() if f.end == target_end]
        if len(exact) == 1 and _fy_matches_quarters(exact[0], quarters.values()):
            fy = exact[0]
            return EpsSelection(CalcStatus.OK, fy.value, (fy,), "FY_DIRECT",
                                (Reason.EPS_TTM_FROM_FY_DIRECT,))
        return _sel_fail(CalcStatus.MISSING, Reason.Q4_EPS_NOT_REPORTED)
    return _sel_fail(CalcStatus.INVALID, failure)


def _fy_matches_quarters(fy: EpsFact, quarters) -> bool:
    """The FY period must be exactly the TTM period implied by its own
    reported quarters: Q1 starts on FY start, Q1-Q3 are contiguous, and the
    remaining fiscal Q4 span (Q3 end -> FY end) is a plausible quarter.
    A one-day misalignment, or no reported quarters, blocks the fallback."""
    qs = {q.start: q for q in quarters if fy.start <= q.start and q.end < fy.end}
    chain, cur = [], fy.start
    while cur in qs and len(chain) < 3:
        chain.append(qs[cur])
        cur = qs[cur].end + timedelta(days=1)
    if len(chain) != 3:
        return False
    q4_days = (fy.end - chain[-1].end).days
    return QUARTER_DAYS[0] <= q4_days <= QUARTER_DAYS[1]


def check_split_basis(used: Sequence[EpsFact], session: date,
                      evidence: Optional[SplitBasisEvidence],
                      sec_shares: Sequence[SharesFact]) -> tuple[Optional[str], tuple[str, ...]]:
    """(blocking reason or None, non-blocking limitations)."""
    if not used:
        return None, ()
    earliest = min(f.filed for f in used)
    if evidence is None or evidence.verified_from is None or evidence.verified_to is None \
            or evidence.verified_from > earliest or evidence.verified_to < session:
        return Reason.EPS_SPLIT_BASIS_UNVERIFIED, ()
    events = [d for d in evidence.execution_dates if earliest < d <= session]
    for d in events:
        if any(f.filed < d for f in used):
            return Reason.EPS_SPLIT_BASIS_UNRESTATED, ()
    shares = sorted((s for s in sec_shares if s.filed < session and s.value > 0),
                    key=lambda s: (s.as_of, s.filed))
    if len(shares) < 2:
        return None, (Reason.SEC_SPLIT_EVIDENCE_UNAVAILABLE,)
    for a, b in zip(shares, shares[1:]):
        if b.as_of <= earliest:
            continue
        ratio = b.value / a.value
        if ratio >= SPLIT_SHARE_JUMP or ratio <= 1 / SPLIT_SHARE_JUMP:
            if not any(a.as_of < d <= b.as_of for d in evidence.execution_dates):
                return Reason.EPS_SPLIT_BASIS_MISMATCH, ()
    return None, ()


def _evidence(sel: EpsSelection) -> dict:
    return {
        "method": sel.method,
        "concept": EPS_CONCEPT_DILUTED,
        "unit": EPS_UNIT,
        "periods": [{"start": f.start.isoformat(), "end": f.end.isoformat(),
                     "accession": f.accession, "filed": f.filed.isoformat(),
                     "form": f.form, "fy": f.fy, "fp": f.fp} for f in sel.facts],
    }


def compute_pe_eps(*, raw_close: Optional[Decimal], raw_status: RawPriceStatus,
                   price_session: date, facts: Optional[Sequence[EpsFact]],
                   split_evidence: Optional[SplitBasisEvidence],
                   sec_shares: Sequence[SharesFact] = (),
                   source_reason: Optional[str] = None) -> dict[str, MetricResult]:
    """PE_TTM, PE_FY, EPS_TTM, EPS_FY for one ticker (EPS path)."""
    names = ((METRIC_EPS_TTM, METRIC_PE_TTM, select_eps_ttm, Reason.EPS_NONPOSITIVE_TTM),
             (METRIC_EPS_FY, METRIC_PE_FY, select_eps_fy, Reason.EPS_NONPOSITIVE_FY))
    out: dict[str, MetricResult] = {}
    if facts is None:
        reason = source_reason or Reason.EPS_SOURCE_MISSING
        status = CalcStatus.INVALID if reason == Reason.EPS_SOURCE_MALFORMED else CalcStatus.MISSING
        for eps_name, pe_name, _, _ in names:
            out[eps_name] = MetricResult(eps_name, status, reasons=(reason,))
            out[pe_name] = MetricResult(pe_name, status, reasons=(reason,))
        return out
    for eps_name, pe_name, select, nonpos in names:
        sel = select(facts, price_session)
        if sel.status is not CalcStatus.OK or sel.value is None:
            out[eps_name] = MetricResult(eps_name, sel.status, reasons=sel.reasons)
            out[pe_name] = MetricResult(pe_name, sel.status, reasons=sel.reasons)
            continue
        ev = _evidence(sel)
        notes = sel.reasons + (Reason.EPS_ROUNDED_TO_CENTS,)
        blocking, lims = check_split_basis(sel.facts, price_session, split_evidence, sec_shares)
        notes += lims
        if blocking:
            out[eps_name] = MetricResult(eps_name, CalcStatus.INVALID, reasons=(blocking,),
                                         evidence=ev)
            out[pe_name] = MetricResult(pe_name, CalcStatus.INVALID, reasons=(blocking,),
                                        evidence=ev)
            continue
        out[eps_name] = MetricResult(eps_name, CalcStatus.OK, sel.value,
                                     Interpretability.MEANINGFUL, notes, evidence=ev)
        if raw_status is not RawPriceStatus.OK or raw_close is None or raw_close <= 0:
            out[pe_name] = MetricResult(pe_name, CalcStatus.MISSING,
                                        reasons=(Reason.RAW_PRICE_NOT_OK,), evidence=ev)
        elif sel.value <= 0:
            out[pe_name] = MetricResult(pe_name, CalcStatus.OK, None,
                                        Interpretability.NOT_MEANINGFUL,
                                        (nonpos,) + notes, evidence=ev)
        else:
            out[pe_name] = MetricResult(pe_name, CalcStatus.OK, raw_close / sel.value,
                                        Interpretability.MEANINGFUL, notes, evidence=ev)
    return out
