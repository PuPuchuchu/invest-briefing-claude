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

NOT SUPPORTED (explicitly UNVERIFIED + UNSUPPORTED_SCENARIO, never OK):
  multiple splits in a window, stock dividends, split awaiting SEC
  confirmation (PENDING_SEC), SEC share-count conflict / ambiguity, event
  date mismatch beyond what QC reports, provider history corrections.
  In particular the R3 rows 2/4/5 "Momentum OK" entries are NOT
  implemented: GPT review found them inconsistent with section 2.2, so
  momentum is withheld until an independent rule is approved.

Eligibility:
  momentum_eligible        -- status in {CONFIRMED, NO_EVENT_VERIFIED}
  valuation_roll_eligible  -- same set (only CONFIRMED events may be used
                              to roll share counts)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Sequence

from src.price.adjustment_qc import QCResult
from src.price.schema import (
    COVERAGE_SOURCE_DOCUMENTED,
    ActionType,
    CorporateActionStatus,
    PriceAdjustmentQC,
    ProviderCoverage,
    Reason,
    SplitEventsResponse,
)

_ELIGIBLE = frozenset({CorporateActionStatus.CONFIRMED,
                       CorporateActionStatus.NO_EVENT_VERIFIED})


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
        return self.status in _ELIGIBLE

    @property
    def valuation_roll_eligible(self) -> bool:
        return self.status in _ELIGIBLE


def _result(status, reasons, n, supported, row):
    return CorporateActionAssessment(status, tuple(reasons), n, supported, row)


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

    return _result(CorporateActionStatus.UNVERIFIED,
                   [Reason.UNSUPPORTED_SCENARIO], n, False, None)
