"""
Momentum metrics on SPLIT_ADJUSTED daily closes (P4 design, P3 v7).

RSI_14D (Wilder):
  * first average gain / loss = simple mean of the first 14 changes
  * then avg = (previous_avg * 13 + current) / 14
  * needs >= 15 closes; fewer -> MISSING / INSUFFICIENT_HISTORY
  * 15..99 closes -> value returned, state WARMUP (seed influence still
    material); >= 100 closes -> state STABLE
  * avg_gain == avg_loss == 0 -> 50; avg_gain == 0 < avg_loss -> 0;
    avg_loss == 0 < avg_gain -> 100
SMA_50D / SMA_200D: mean of the last N completed daily closes.
SMA_40W: mean of the last 40 COMPLETED US trading weeks; a week's close
  is the close of its last trading day (per the injected calendar); the
  in-progress week is excluded; a completed week whose final-session bar
  is missing makes the metric MISSING (no substitution).

Price-vs-SMA facts are reported as percentage distance, never as the SMA
level (a price-level number), see result.py.

Gating: a metric is computed only if the adjusted price status is OK,
the corporate-action assessment for the window is eligible and the
adjustment QC is not INCONSISTENT. Momentum never depends on valuation
inputs.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from src.price.calendar import TradingCalendar
from src.price.corporate_actions import CorporateActionAssessment
from src.price.adjustment_qc import QCResult
from src.price.schema import (
    AdjustedPriceStatus,
    Basis,
    CalcStatus,
    Interpretability,
    MetricResult,
    NormalizedBar,
    PriceAdjustmentQC,
    PriceDataValidationError,
    Reason,
    RsiState,
    validate_series,
)

RSI_PERIOD = 14
RSI_MIN_CLOSES = RSI_PERIOD + 1          # 15
RSI_STABLE_MIN_CLOSES = 100
SMA_40W_WEEKS = 40

_HUNDRED = Decimal(100)


def rsi_wilder(closes: Sequence[Decimal]) -> MetricResult:
    n = len(closes)
    if n < RSI_MIN_CLOSES:
        return MetricResult("RSI_14D", CalcStatus.MISSING,
                            reasons=(Reason.INSUFFICIENT_HISTORY,),
                            state=RsiState.INSUFFICIENT_HISTORY.value)
    changes = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [c if c > 0 else Decimal(0) for c in changes]
    losses = [-c if c < 0 else Decimal(0) for c in changes]
    avg_gain = sum(gains[:RSI_PERIOD], Decimal(0)) / RSI_PERIOD
    avg_loss = sum(losses[:RSI_PERIOD], Decimal(0)) / RSI_PERIOD
    for g, l in zip(gains[RSI_PERIOD:], losses[RSI_PERIOD:]):
        avg_gain = (avg_gain * (RSI_PERIOD - 1) + g) / RSI_PERIOD
        avg_loss = (avg_loss * (RSI_PERIOD - 1) + l) / RSI_PERIOD
    if avg_gain == 0 and avg_loss == 0:
        value = Decimal(50)
    elif avg_loss == 0:
        value = _HUNDRED
    elif avg_gain == 0:
        value = Decimal(0)
    else:
        rs = avg_gain / avg_loss
        value = _HUNDRED - _HUNDRED / (1 + rs)
    if n < RSI_STABLE_MIN_CLOSES:
        return MetricResult("RSI_14D", CalcStatus.OK, value,
                            Interpretability.MEANINGFUL,
                            (Reason.RSI_WARMUP,), RsiState.WARMUP.value)
    return MetricResult("RSI_14D", CalcStatus.OK, value,
                        Interpretability.MEANINGFUL, (), RsiState.STABLE.value)


def sma(closes: Sequence[Decimal], window: int, name: str) -> MetricResult:
    if window <= 0:
        raise ValueError("WINDOW_MUST_BE_POSITIVE")
    if len(closes) < window:
        return MetricResult(name, CalcStatus.MISSING,
                            reasons=(Reason.INSUFFICIENT_HISTORY,))
    tail = closes[-window:]
    return MetricResult(name, CalcStatus.OK, sum(tail, Decimal(0)) / window,
                        Interpretability.MEANINGFUL)


def completed_week_closes(bars: Sequence[NormalizedBar], calendar: TradingCalendar,
                          as_of_session: date) -> list[Decimal | None]:
    """
    One entry per completed week (oldest first) from the week of the first
    bar up to as_of_session. A week is completed if its last trading day
    <= as_of_session. The entry is that day's close, or None if the bar is
    absent (never substituted). Weeks with no trading day are skipped.
    """
    by_date = {b.date: b for b in bars if b.date <= as_of_session}
    if not by_date:
        return []
    first = min(by_date)
    monday = first - timedelta(days=first.weekday())
    weeks: list[Decimal | None] = []
    while monday <= as_of_session:
        last_td = calendar.last_trading_day_of_week(monday)
        if last_td is not None and last_td <= as_of_session:
            bar = by_date.get(last_td)
            weeks.append(None if bar is None else bar.close)
        monday += timedelta(days=7)
    return weeks


def sma_40w(bars: Sequence[NormalizedBar], calendar: TradingCalendar,
            as_of_session: date) -> MetricResult:
    weeks = completed_week_closes(bars, calendar, as_of_session)
    if len(weeks) < SMA_40W_WEEKS:
        return MetricResult("SMA_40W", CalcStatus.MISSING,
                            reasons=(Reason.INSUFFICIENT_HISTORY,))
    tail = weeks[-SMA_40W_WEEKS:]
    if any(c is None for c in tail):
        return MetricResult("SMA_40W", CalcStatus.MISSING,
                            reasons=(Reason.WEEK_FINAL_BAR_MISSING,))
    return sma([c for c in tail if c is not None], SMA_40W_WEEKS, "SMA_40W")


def pct_distance(price: Decimal, reference: MetricResult, name: str) -> MetricResult:
    """(price / reference - 1) * 100 as a percentage fact."""
    if reference.calc_status is not CalcStatus.OK or reference.value is None:
        return MetricResult(name, reference.calc_status, reasons=reference.reasons)
    if reference.value <= 0:
        return MetricResult(name, CalcStatus.OK, None,
                            Interpretability.NOT_MEANINGFUL, (Reason.ZERO_REFERENCE,))
    return MetricResult(name, CalcStatus.OK,
                        (price / reference.value - 1) * _HUNDRED,
                        Interpretability.MEANINGFUL)


MOMENTUM_METRICS = ("RSI_14D", "PRICE_VS_SMA50_PCT", "PRICE_VS_SMA200_PCT",
                    "PRICE_VS_SMA40W_PCT")


def _all(status: CalcStatus, reasons: tuple[str, ...]) -> dict[str, MetricResult]:
    return {m: MetricResult(m, status, reasons=reasons) for m in MOMENTUM_METRICS}


def compute_momentum(*, adjusted_bars: Sequence[NormalizedBar],
                     adjusted_status: AdjustedPriceStatus,
                     corporate_actions: CorporateActionAssessment,
                     qc: QCResult,
                     calendar: TradingCalendar,
                     as_of_session: date) -> dict[str, MetricResult]:
    """
    All momentum facts for one symbol, gated per module docstring.

    Input contract (checked here, not only at the provider):
      * every bar is SPLIT_ADJUSTED; dates strictly ascending, no duplicates
      * no bar dated after as_of_session (explicitly rejected, not dropped)
      * the last bar is exactly as_of_session -- otherwise no value is
        produced (MISSING / LATEST_SESSION_BAR_MISSING)
    Contract violations -> INVALID with the validation reason code.
    """
    if any(b.basis is not Basis.SPLIT_ADJUSTED for b in adjusted_bars):
        return _all(CalcStatus.INVALID, (Reason.BASIS_MIXED,))
    try:
        validate_series(tuple(adjusted_bars), as_of=as_of_session)
    except PriceDataValidationError as exc:
        return _all(CalcStatus.INVALID, (exc.reason,))

    block: tuple[str, ...] = ()
    if adjusted_status is not AdjustedPriceStatus.OK:
        block = (Reason.ADJUSTED_PRICE_NOT_OK,)
    elif not corporate_actions.momentum_eligible:
        block = (Reason.CORPORATE_ACTION_BLOCKS_MOMENTUM,) + corporate_actions.reasons
    elif qc.status is PriceAdjustmentQC.INCONSISTENT:
        block = (Reason.QC_BLOCKS_MOMENTUM,)
    if block:
        status = (CalcStatus.MISSING
                  if adjusted_status is not AdjustedPriceStatus.OK
                  else CalcStatus.INVALID)
        return {m: MetricResult(m, status, reasons=block) for m in MOMENTUM_METRICS}

    used = list(adjusted_bars)
    if not used or used[-1].date != as_of_session:
        return _all(CalcStatus.MISSING, (Reason.LATEST_SESSION_BAR_MISSING,))
    closes = [b.close for b in used]
    last = closes[-1]
    return {
        "RSI_14D": rsi_wilder(closes),
        "PRICE_VS_SMA50_PCT": pct_distance(last, sma(closes, 50, "SMA_50D"),
                                           "PRICE_VS_SMA50_PCT"),
        "PRICE_VS_SMA200_PCT": pct_distance(last, sma(closes, 200, "SMA_200D"),
                                            "PRICE_VS_SMA200_PCT"),
        "PRICE_VS_SMA40W_PCT": pct_distance(last, sma_40w(used, calendar, as_of_session),
                                            "PRICE_VS_SMA40W_PCT"),
    }
