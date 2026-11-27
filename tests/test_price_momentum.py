from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from src.price.adjustment_qc import QCResult
from src.price.calendar import TradingCalendar
from src.price.corporate_actions import CorporateActionAssessment
from src.price.momentum import (
    MOMENTUM_METRICS, compute_momentum, rsi_wilder, sma, sma_40w,
)
from src.price.schema import (
    AdjustedPriceStatus, Basis, CalcStatus, CorporateActionStatus,
    NormalizedBar, PriceAdjustmentQC, Reason,
)

TS = datetime(2026, 11, 27, 5, 10, tzinfo=timezone.utc)
CAL = TradingCalendar(holidays=frozenset())
OKQC = QCResult(PriceAdjustmentQC.CONSISTENT, (), 1)
CA_OK = CorporateActionAssessment(CorporateActionStatus.NO_EVENT_VERIFIED, (), 0, True, "16")


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def adj_bars(days, closes):
    return [NormalizedBar(d, D(c), D(c), D(c), D(c), 1, Basis.SPLIT_ADJUSTED,
                          "synthetic", TS) for d, c in zip(days, closes)]


def float_rsi(closes):
    ch = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    g = [max(c, 0) for c in ch]
    l = [max(-c, 0) for c in ch]
    ag, al = sum(g[:14]) / 14, sum(l[:14]) / 14
    for gi, li in zip(g[14:], l[14:]):
        ag, al = (ag * 13 + gi) / 14, (al * 13 + li) / 14
    return 100 - 100 / (1 + ag / al)


def test_rsi_hand_computed_vector():
    # 14 changes: seven +1 and seven -1 -> avg gain = avg loss = 0.5 -> RSI 50
    closes = [D(100)]
    for i in range(14):
        closes.append(closes[-1] + (1 if i % 2 == 0 else -1))
    assert rsi_wilder(closes).value == D(50)
    # one more change of +2: avg_gain = (0.5*13+2)/14 = 8.5/14, avg_loss = 6.5/14
    # RSI = 100 * 8.5 / (8.5 + 6.5) = 56.666...
    r = rsi_wilder(closes + [closes[-1] + 2])
    assert abs(r.value - D("56.6666666666666666666666667")) < D("1e-20")


def test_rsi_matches_independent_float_implementation():
    closes = [100 + ((i * 37) % 11) - 5 + i * 0.1 for i in range(120)]
    r = rsi_wilder([D(str(c)) for c in closes])
    assert abs(float(r.value) - float_rsi(closes)) < 1e-9


@pytest.mark.parametrize("n,status,state", [
    (14, CalcStatus.MISSING, "INSUFFICIENT_HISTORY"),
    (15, CalcStatus.OK, "WARMUP"),
    (99, CalcStatus.OK, "WARMUP"),
    (100, CalcStatus.OK, "STABLE"),
])
def test_rsi_length_boundaries(n, status, state):
    closes = [D(100 + (i % 3)) for i in range(n)]
    r = rsi_wilder(closes)
    assert r.calc_status is status and r.state == state
    if state == "WARMUP":
        assert Reason.RSI_WARMUP in r.reasons


def test_rsi_zero_cases():
    assert rsi_wilder([D(10)] * 20).value == D(50)
    assert rsi_wilder([D(10 + i) for i in range(20)]).value == D(100)
    assert rsi_wilder([D(100 - i) for i in range(20)]).value == D(0)


def test_sma_boundaries():
    closes = [D(i) for i in range(1, 51)]
    assert sma(closes, 50, "SMA_50D").value == D("25.5")
    assert sma(closes[:-1], 50, "SMA_50D").calc_status is CalcStatus.MISSING
    with pytest.raises(ValueError):
        sma(closes, 0, "X")


def test_sma_40w_excludes_incomplete_week():
    # 41 full weeks Mon-Fri, then Mon-Wed of the in-progress week
    start = date(2026, 1, 5)                      # Monday
    days = weekdays(start, 41 * 5 + 3)
    closes = []
    for d in days:
        week = (d - start).days // 7
        closes.append(1000 if (d - start).days // 7 == 41 else week)
    bars = adj_bars(days, closes)
    as_of = days[-1]                               # Wednesday of week 41
    r = sma_40w(bars, CAL, as_of)
    # completed weeks 0..40 -> last 40 Friday closes are weeks 1..40
    assert r.calc_status is CalcStatus.OK
    assert r.value == D(sum(range(1, 41))) / 40


def test_sma_40w_missing_final_bar_and_short_history():
    start = date(2026, 1, 5)
    days = [d for d in weekdays(start, 40 * 5) if d != date(2026, 6, 5)]  # drop a Friday
    bars = adj_bars(days, [10] * len(days))
    r = sma_40w(bars, CAL, days[-1])
    assert r.calc_status is CalcStatus.MISSING
    assert r.reasons == (Reason.WEEK_FINAL_BAR_MISSING,)
    short = adj_bars(weekdays(start, 39 * 5), [10] * 195)
    assert sma_40w(short, CAL, short[-1].date).reasons == (Reason.INSUFFICIENT_HISTORY,)


def test_holiday_friday_week_uses_thursday():
    cal = TradingCalendar(holidays=frozenset({date(2026, 4, 3)}))
    start = date(2026, 1, 5)
    days = [d for d in weekdays(start, 41 * 5) if d != date(2026, 4, 3)]
    bars = adj_bars(days, [10] * len(days))
    assert sma_40w(bars, cal, days[-1]).calc_status is CalcStatus.OK


def run(bars, status=AdjustedPriceStatus.OK, ca=CA_OK, qc=OKQC):
    return compute_momentum(adjusted_bars=bars, adjusted_status=status,
                            corporate_actions=ca, qc=qc, calendar=CAL,
                            as_of_session=bars[-1].date if bars else date(2026, 11, 25))


def test_compute_momentum_full():
    days = weekdays(date(2025, 6, 2), 260)
    bars = adj_bars(days, [100 + (i % 7) for i in range(260)])
    out = run(bars)
    assert set(out) == set(MOMENTUM_METRICS)
    assert all(m.calc_status is CalcStatus.OK for m in out.values())
    assert out["RSI_14D"].state == "STABLE"


def test_gating_is_independent_of_valuation_and_fail_closed():
    days = weekdays(date(2025, 6, 2), 260)
    bars = adj_bars(days, [100] * 260)
    out = run(bars, status=AdjustedPriceStatus.STALE)
    assert all(m.calc_status is CalcStatus.MISSING for m in out.values())
    blocked = CorporateActionAssessment(CorporateActionStatus.UNVERIFIED,
                                        (Reason.UNSUPPORTED_SCENARIO,), 1, False, None)
    out = run(bars, ca=blocked)
    assert all(m.calc_status is CalcStatus.INVALID for m in out.values())
    assert Reason.CORPORATE_ACTION_BLOCKS_MOMENTUM in out["RSI_14D"].reasons
    pending = CorporateActionAssessment(CorporateActionStatus.PENDING_SEC, (), 1, False, "2")
    assert run(bars, ca=pending)["RSI_14D"].calc_status is CalcStatus.INVALID
    bad = QCResult(PriceAdjustmentQC.INCONSISTENT, (Reason.QC_OUT_OF_INTERVAL,), 1)
    assert run(bars, qc=bad)["RSI_14D"].reasons == (Reason.QC_BLOCKS_MOMENTUM,)


def test_input_contract_rejects_bad_series():
    days = weekdays(date(2025, 6, 2), 30)
    good = adj_bars(days, [100] * 30)
    raw_basis = [NormalizedBar(b.date, b.open, b.high, b.low, b.close, 1, Basis.RAW,
                               "synthetic", TS) for b in good]
    cases = {
        Reason.BASIS_MIXED: raw_basis,
        Reason.DATES_NOT_ASCENDING: good[:10] + [good[12], good[11]] + good[13:],
        Reason.DUPLICATE_DATE: good[:10] + [good[9]] + good[10:],
    }
    for reason, bars in cases.items():
        out = compute_momentum(adjusted_bars=bars, adjusted_status=AdjustedPriceStatus.OK,
                               corporate_actions=CA_OK, qc=OKQC, calendar=CAL,
                               as_of_session=days[-1])
        assert all(m.calc_status is CalcStatus.INVALID for m in out.values()), reason
        assert out["RSI_14D"].reasons == (reason,)


def test_bar_after_as_of_is_rejected_not_dropped():
    days = weekdays(date(2025, 6, 2), 30)
    bars = adj_bars(days, [100] * 30)
    out = compute_momentum(adjusted_bars=bars, adjusted_status=AdjustedPriceStatus.OK,
                           corporate_actions=CA_OK, qc=OKQC, calendar=CAL,
                           as_of_session=days[-2])
    assert out["RSI_14D"].calc_status is CalcStatus.INVALID
    assert out["RSI_14D"].reasons == (Reason.FUTURE_DATE,)


def test_latest_session_bar_missing_produces_no_value():
    days = weekdays(date(2025, 6, 2), 30)
    bars = adj_bars(days[:-1], [100] * 29)
    out = compute_momentum(adjusted_bars=bars, adjusted_status=AdjustedPriceStatus.OK,
                           corporate_actions=CA_OK, qc=OKQC, calendar=CAL,
                           as_of_session=days[-1])
    assert all(m.calc_status is CalcStatus.MISSING and m.value is None
               for m in out.values())
    assert out["RSI_14D"].reasons == (Reason.LATEST_SESSION_BAR_MISSING,)
    empty = compute_momentum(adjusted_bars=[], adjusted_status=AdjustedPriceStatus.OK,
                             corporate_actions=CA_OK, qc=OKQC, calendar=CAL,
                             as_of_session=days[-1])
    assert empty["RSI_14D"].reasons == (Reason.LATEST_SESSION_BAR_MISSING,)
