"""
Corporate-action status for a calculation window (R3, P3 v7 section 2.4).

SUPPORTED in this round (GPT implementation instruction, section 9):
  * row 1   -- one forward/reverse split, adjustment QC CONSISTENT and the
               (externally computed) SEC share-count evidence CONSISTENT
               -> CONFIRMED
  * row 10  -- cash dividends: never alter split status (accepted and
               ignored by design; split-only series are not dividend-
               adjusted)
  * row 15  -- split-event fetch failure -> UNVERIFIED
  * row 16/17 -- no event in window and QC CONSISTENT -> NO_EVENT_VERIFIED
               (share-count availability is a SEPARATE status, see
               valuation.py; "no split" never implies "shares available").
               An empty event list counts as evidence ONLY if the response
               is complete (not partial; adapters must mark incomplete
               pagination as partial), its requested range covers the whole
               window, it was not filtered to other symbols, and the window
               lies inside documented coverage (source DOCUMENTED, both
               bounds known, inclusive). Otherwise UNVERIFIED.

Fail-closed classifications that need no extra rules:
  * no event in window but QC INCONSISTENT -> CANDIDATE
    (SPLIT_EVENT_MISSING); QC NOT_AVAILABLE -> UNVERIFIED
  * window outside documented split coverage -> UNVERIFIED
    (COVERAGE_UNVERIFIED)

  * row 2   -- one forward/reverse split, SEC share-count evidence NOT
               YET AVAILABLE, but the PRICE evidence is complete:
                 (a) adjustment QC CONSISTENT (adjusted == raw / factor on
                     every overlapping session, so the adjusted series
                     applies exactly this event), and
                 (b) RAW JUMP: the raw close moves from the previous
                     trading session (per the injected calendar; a data gap
                     fails) to execution_date by roughly
                     the split ratio -- |ln(raw_ex / raw_prev) + ln(ratio)|
                     < |ln(ratio)| / 2 -- so the split really happened in
                     the traded prices on that date (a fabricated or
                     mis-dated event fails here even if QC passes).
               -> PENDING_SEC. Momentum depends only on the adjusted price
               series, which (a)+(b) verify; share counts are untouched,
               so VALUATION STAYS BLOCKED until SEC evidence confirms
               (row 1). Missing bars for (b) -> not verified.

NOT SUPPORTED (explicitly UNVERIFIED + UNSUPPORTED_SCENARIO, never OK):
  multiple splits in a window, stock dividends, SEC share-count conflict /
  ambiguity / no-change (rows 4/5: the SEC evidence contradicts the event,
  so the event itself is in doubt), event date mismatch beyond what QC
  reports, provider history corrections.

Eligibility:
  momentum_eligible        -- status in {CONFIRMED, NO_EVENT_VERIFIED,
                              PENDING_SEC (row 2 only, price-verified)}
  valuation_roll_eligible  -- {CONFIRMED, NO_EVENT_VERIFIED} (only
                              CONFIRMED events may be used to roll share
                              counts)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Sequence

from src.price.adjustment_qc import QCResult
from src.price.calendar import TradingCalendar
from src.price.schema import (
    COVERAGE_SOURCE_DOCUMENTED,
    Basis,
    NormalizedBar,
    SplitEvent,
    ActionType,
    CorporateActionStatus,
    PriceAdjustmentQC,
    ProviderCoverage,
    Reason,
    SplitEventsResponse,
)

_ELIGIBLE = frozenset({CorporateActionStatus.CONFIRMED,
                       CorporateActionStatus.NO_EVENT_VERIFIED})
_MOMENTUM_ELIGIBLE = _ELIGIBLE | {CorporateActionStatus.PENDING_SEC}


class SharesSplitEvidence(str, Enum):
    """SEC share-count evidence for an event (computed outside this round)."""

    CONSISTENT = "CONSISTENT"
    NO_CHANGE = "NO_CHANGE"
    AMBIGUOUS = "AMBIGUOUS"
    INCONSISTENT = "INCONSISTENT"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True)
class CorporateActionAssessment:
    status: CorporateActionStatus
    reasons: tuple[str, ...]
    events_in_window: int
    supported: bool
    r3_row: Optional[str]

    @property
    def momentum_eligible(self) -> bool:
        return self.status in _MOMENTUM_ELIGIBLE

    @property
    def valuation_roll_eligible(self) -> bool:
        return self.status in _ELIGIBLE


def _result(status, reasons, n, supported, row):
    return CorporateActionAssessment(status, tuple(reasons), n, supported, row)


def raw_jump_matches_split(raw_bars: Sequence[NormalizedBar], event: SplitEvent,
                           calendar: Optional[TradingCalendar]) -> bool:
    """Row 2 condition (b): the raw close jump into execution_date is closer
    to the split ratio than to no move at all (half-log-ratio band).
    Requires execution_date to be a trading day and the bar before it to be
    exactly the previous trading session (no gap); no calendar -> False."""
    if calendar is None or not calendar.is_trading_day(event.execution_date):
        return False
    bars = [b for b in raw_bars if b.basis is Basis.RAW]
    idx = next((i for i, b in enumerate(bars) if b.date == event.execution_date), None)
    if idx is None or idx == 0:
        return False
    if bars[idx - 1].date != calendar.previous_trading_day(event.execution_date):
        return False
    prev, ex = bars[idx - 1].close, bars[idx].close
    if prev <= 0 or ex <= 0 or event.ratio == 1:
        return False
    log_ratio = event.ratio.ln()
    return abs((ex / prev).ln() + log_ratio) < abs(log_ratio) / 2


def assess_corporate_actions(
    *,
    symbol: str,
    window_start: date,
    window_end: date,
    response: Optional[SplitEventsResponse],
    fetch_failed: bool,
    qc: QCResult,
    coverage: ProviderCoverage,
    shares_evidence: SharesSplitEvidence = SharesSplitEvidence.NOT_AVAILABLE,
    cash_dividend_dates: Sequence[date] = (),
    raw_bars: Sequence[NormalizedBar] = (),
    calendar: Optional[TradingCalendar] = None,
) -> CorporateActionAssessment:
    """
    Classify split-type corporate actions affecting (window_start, window_end].

    `cash_dividend_dates` is accepted so callers can pass dividends
    explicitly; they never change the split status (row 10).
    """
    del cash_dividend_dates  # row 10: by design no effect on split status

    if fetch_failed or response is None:
        return _result(CorporateActionStatus.UNVERIFIED,
                       [Reason.SPLIT_FETCH_FAILED], 0, True, "15")

    cov_start = coverage.splits_history_start
    cov_end = coverage.splits_history_end
    if coverage.coverage_source != COVERAGE_SOURCE_DOCUMENTED \
            or cov_start is None or cov_end is None \
            or window_start < cov_start or window_end > cov_end:
        return _result(CorporateActionStatus.UNVERIFIED,
                       [Reason.COVERAGE_UNVERIFIED], 0, True, "13")

    if response.is_partial \
            or response.requested_start > window_start \
            or response.requested_end < window_end \
            or (response.symbols is not None and symbol not in response.symbols):
        return _result(CorporateActionStatus.UNVERIFIED,
                       [Reason.PARTIAL_RESPONSE], 0, True, None)
    events = response.events

    in_window = [e for e in events
                 if e.symbol == symbol and window_start < e.execution_date <= window_end]

    if not in_window:
        if qc.status is PriceAdjustmentQC.CONSISTENT:
            return _result(CorporateActionStatus.NO_EVENT_VERIFIED, [], 0, True, "16")
        if qc.status is PriceAdjustmentQC.INCONSISTENT:
            return _result(CorporateActionStatus.CANDIDATE,
                           [Reason.SPLIT_EVENT_MISSING], 0, True, "3")
        return _result(CorporateActionStatus.UNVERIFIED, list(qc.reasons), 0, True, None)

    n = len(in_window)
    if n == 1 and in_window[0].action_type in (ActionType.FORWARD_SPLIT,
                                              ActionType.REVERSE_SPLIT):
        if qc.status is PriceAdjustmentQC.INCONSISTENT:
            return _result(CorporateActionStatus.CANDIDATE,
                           [Reason.QC_OUT_OF_INTERVAL], n, True, None)
        if qc.status is PriceAdjustmentQC.CONSISTENT \
                and shares_evidence is SharesSplitEvidence.CONSISTENT:
            return _result(CorporateActionStatus.CONFIRMED, [], n, True, "1")
        if qc.status is PriceAdjustmentQC.CONSISTENT \
                and shares_evidence is SharesSplitEvidence.NOT_AVAILABLE:
            if raw_jump_matches_split(raw_bars, in_window[0], calendar):
                return _result(CorporateActionStatus.PENDING_SEC,
                               [Reason.SPLIT_PENDING_SEC], n, True, "2")
            return _result(CorporateActionStatus.UNVERIFIED,
                           [Reason.SPLIT_RAW_JUMP_UNVERIFIED], n, True, "2")

    return _result(CorporateActionStatus.UNVERIFIED,
                   [Reason.UNSUPPORTED_SCENARIO], n, False, None)
