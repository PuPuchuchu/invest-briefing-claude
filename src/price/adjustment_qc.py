"""
PRICE_ADJUSTMENT_QC (P3 v7 section 2.3).

For every trading date t present in BOTH the RAW and SPLIT_ADJUSTED
series of ONE symbol (events of other symbols are ignored):

    F(t) = product(split_to / split_from  for events with execution_date > t)

With prices published at precision p (e.g. 0.01), each displayed value
can be off by eps = p / 2, so the true RAW/ADJ ratio lies in

    [(RAW - eps_r) / (ADJ + eps_a),  (RAW + eps_r) / (ADJ - eps_a)]

F(t) inside the interval -> consistent at t. All dates consistent ->
CONSISTENT; any date outside -> INCONSISTENT; not computable ->
NOT_AVAILABLE. All arithmetic is Decimal.

Scope note: this checks the provider's split adjustment against its own
split events. It is NOT a general data-error detector.

`historical_adjustment_factor` cross-check: the factor's direction and
reference basis are UNVERIFIED, so no cross-check is ever reported as
done; the result carries FACTOR_CROSSCHECK_UNVERIFIED.

Results carry dates and reason codes only -- never price values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from src.price.schema import (
    ActionType,
    Basis,
    NormalizedBar,
    PriceAdjustmentQC,
    Reason,
    SplitEvent,
)

QC_FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class QCResult:
    status: PriceAdjustmentQC
    reasons: tuple[str, ...]
    dates_checked: int
    first_inconsistent_date: Optional[date] = None
    factor_crosscheck: str = field(default=Reason.FACTOR_CROSSCHECK_UNVERIFIED)


def cumulative_factor(t: date, events: Sequence[SplitEvent], symbol: str) -> Decimal:
    """Product of ratios of `symbol`'s events executed after t."""
    if not symbol:
        raise ValueError(Reason.SYMBOL_REQUIRED)
    f = Decimal(1)
    for e in events:
        if e.symbol == symbol and e.execution_date > t:
            f *= e.ratio
    return f


def _na(reason: str, checked: int = 0) -> QCResult:
    return QCResult(PriceAdjustmentQC.NOT_AVAILABLE, (reason,), checked)


def check_adjustment_qc(*, symbol: str,
                        raw: Sequence[NormalizedBar],
                        adjusted: Sequence[NormalizedBar],
                        events: Sequence[SplitEvent],
                        raw_precision: Optional[Decimal],
                        adjusted_precision: Optional[Decimal],
                        price_field: str = "close") -> QCResult:
    if price_field not in QC_FIELDS:
        raise ValueError("UNKNOWN_PRICE_FIELD")
    if not symbol:
        return _na(Reason.SYMBOL_REQUIRED)
    events = [e for e in events if e.symbol == symbol]
    if raw_precision is None or adjusted_precision is None \
            or raw_precision <= 0 or adjusted_precision <= 0:
        return _na(Reason.QC_PRECISION_UNKNOWN)
    if any(b.basis is not Basis.RAW for b in raw) \
            or any(b.basis is not Basis.SPLIT_ADJUSTED for b in adjusted):
        return _na(Reason.QC_BASIS_MISMATCH)
    if any(e.action_type is ActionType.STOCK_DIVIDEND for e in events):
        # Provider treatment of stock dividends in adjusted bars is unverified.
        return _na(Reason.UNSUPPORTED_SCENARIO)

    raw_by_date = {b.date: b for b in raw}
    adj_by_date = {b.date: b for b in adjusted}
    if set(raw_by_date) != set(adj_by_date):
        return _na(Reason.QC_DATE_MISMATCH)
    if not raw_by_date:
        return _na(Reason.QC_NO_OVERLAP)

    eps_r = raw_precision / 2
    eps_a = adjusted_precision / 2
    checked = 0
    for t in sorted(raw_by_date):
        r = getattr(raw_by_date[t], price_field)
        a = getattr(adj_by_date[t], price_field)
        lo_den = a + eps_a
        hi_den = a - eps_a
        if lo_den <= 0 or hi_den <= 0:
            return _na(Reason.QC_NONPOSITIVE_DENOMINATOR, checked)
        lower = (r - eps_r) / lo_den
        upper = (r + eps_r) / hi_den
        f = cumulative_factor(t, events, symbol)
        checked += 1
        if not (lower <= f <= upper):
            return QCResult(PriceAdjustmentQC.INCONSISTENT,
                            (Reason.QC_OUT_OF_INTERVAL,), checked,
                            first_inconsistent_date=t)
    return QCResult(PriceAdjustmentQC.CONSISTENT, (), checked)
