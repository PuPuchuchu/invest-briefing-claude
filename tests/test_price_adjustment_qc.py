from datetime import date, datetime, timezone
from decimal import Decimal as D

from src.price.adjustment_qc import check_adjustment_qc, cumulative_factor
from src.price.schema import (
    ActionType, Basis, NormalizedBar, PriceAdjustmentQC, Reason, SplitEvent,
)

TS = datetime(2026, 11, 27, 5, 10, tzinfo=timezone.utc)
E = date(2026, 6, 3)
SPLIT = SplitEvent("AAA", E, D(1), D(2), ActionType.FORWARD_SPLIT)
P = D("0.01")


def bar(day, close, basis):
    c = D(close)
    return NormalizedBar(day, c, c, c, c, 1, basis, "synthetic", TS)


def series(pre_raw="200.00", pre_adj="100.00"):
    days_pre = [date(2026, 6, 1), date(2026, 6, 2)]
    days_post = [date(2026, 6, 3), date(2026, 6, 4)]
    raw = [bar(d, pre_raw, Basis.RAW) for d in days_pre] + \
          [bar(d, "101.00", Basis.RAW) for d in days_post]
    adj = [bar(d, pre_adj, Basis.SPLIT_ADJUSTED) for d in days_pre] + \
          [bar(d, "101.00", Basis.SPLIT_ADJUSTED) for d in days_post]
    return raw, adj


def qc(raw, adj, events=(SPLIT,), rp=P, ap=P, symbol="AAA"):
    return check_adjustment_qc(symbol=symbol, raw=raw, adjusted=adj, events=list(events),
                               raw_precision=rp, adjusted_precision=ap)


def test_cumulative_factor_direction():
    ev = [SPLIT, SplitEvent("AAA", date(2026, 9, 1), D(10), D(1),
                            ActionType.REVERSE_SPLIT)]
    assert cumulative_factor(date(2026, 5, 1), ev, "AAA") == D(2) * D("0.1")
    assert cumulative_factor(E, ev, "AAA") == D("0.1")    # execution day is post-split
    assert cumulative_factor(date(2026, 9, 1), ev, "AAA") == D(1)


def test_consistent_split():
    r = qc(*series())
    assert r.status is PriceAdjustmentQC.CONSISTENT and r.dates_checked == 4
    assert r.factor_crosscheck == Reason.FACTOR_CROSSCHECK_UNVERIFIED


def test_interval_boundary():
    assert qc(*series(pre_raw="200.01")).status is PriceAdjustmentQC.CONSISTENT
    r = qc(*series(pre_raw="200.02"))
    assert r.status is PriceAdjustmentQC.INCONSISTENT
    assert r.first_inconsistent_date == date(2026, 6, 1)


def test_missing_event_is_inconsistent():
    r = qc(*series(), events=())
    assert r.status is PriceAdjustmentQC.INCONSISTENT


def test_precision_unknown():
    assert qc(*series(), rp=None).reasons == (Reason.QC_PRECISION_UNKNOWN,)
    assert qc(*series(), ap=D(0)).status is PriceAdjustmentQC.NOT_AVAILABLE


def test_date_mismatch_and_no_overlap():
    raw, adj = series()
    r = qc(raw, adj[:-1])
    assert r.status is PriceAdjustmentQC.NOT_AVAILABLE
    assert r.reasons == (Reason.QC_DATE_MISMATCH,)
    assert qc([], []).reasons == (Reason.QC_NO_OVERLAP,)


def test_nonpositive_denominator():
    raw = [bar(date(2026, 6, 1), "0.00", Basis.RAW)]
    adj = [bar(date(2026, 6, 1), "0.00", Basis.SPLIT_ADJUSTED)]
    r = qc(raw, adj, events=())
    assert r.reasons == (Reason.QC_NONPOSITIVE_DENOMINATOR,)


def test_basis_mismatch():
    raw, adj = series()
    r = qc(adj, adj)
    assert r.reasons == (Reason.QC_BASIS_MISMATCH,)


def test_stock_dividend_not_supported():
    ev = SplitEvent("AAA", E, D(100), D(105), ActionType.STOCK_DIVIDEND)
    r = qc(*series(), events=(ev,))
    assert r.status is PriceAdjustmentQC.NOT_AVAILABLE
    assert r.reasons == (Reason.UNSUPPORTED_SCENARIO,)


def test_denominator_exactly_zero_after_rounding_band():
    raw = [bar(date(2026, 6, 1), "0.010", Basis.RAW)]
    adj = [bar(date(2026, 6, 1), "0.005", Basis.SPLIT_ADJUSTED)]
    r = qc(raw, adj, events=())
    assert r.reasons == (Reason.QC_NONPOSITIVE_DENOMINATOR,)


BBB_SPLIT = SplitEvent("BBB", E, D(1), D(2), ActionType.FORWARD_SPLIT)


def test_other_symbol_events_do_not_affect_factor():
    assert cumulative_factor(date(2026, 5, 1), [BBB_SPLIT], "AAA") == D(1)
    assert cumulative_factor(date(2026, 5, 1), [SPLIT, BBB_SPLIT], "AAA") == D(2)
    import pytest
    with pytest.raises(ValueError):
        cumulative_factor(date(2026, 5, 1), [SPLIT], "")


def test_qc_ignores_other_symbol_split_adversarial():
    # AAA has NO split: raw == adj. A BBB 2:1 split must not make AAA inconsistent.
    raw, adj = series(pre_raw="100.00", pre_adj="100.00")
    assert qc(raw, adj, events=(BBB_SPLIT,)).status is PriceAdjustmentQC.CONSISTENT
    # AAA has a split; adding BBB's split must not double-count.
    assert qc(*series(), events=(SPLIT, BBB_SPLIT)).status is PriceAdjustmentQC.CONSISTENT
    # Only BBB's event supplied while AAA really split -> missing event detected.
    assert qc(*series(), events=(BBB_SPLIT,)).status is PriceAdjustmentQC.INCONSISTENT
    # Evaluating as BBB with AAA's event list -> AAA event ignored.
    raw, adj = series(pre_raw="100.00", pre_adj="100.00")
    assert qc(raw, adj, events=(SPLIT,), symbol="BBB").status is PriceAdjustmentQC.CONSISTENT


def test_qc_requires_symbol():
    r = qc(*series(), symbol="")
    assert r.status is PriceAdjustmentQC.NOT_AVAILABLE
    assert r.reasons == (Reason.SYMBOL_REQUIRED,)


def test_multiple_same_symbol_events_accumulate():
    rev = SplitEvent("AAA", date(2026, 6, 4), D(4), D(1), ActionType.REVERSE_SPLIT)
    # 2:1 on 06-03 then 1:4 on 06-04: F before 06-03 = 2 * 0.25 = 0.5
    raw = [bar(date(2026, 6, 2), "50.00", Basis.RAW), bar(date(2026, 6, 3), "50.00", Basis.RAW),
           bar(date(2026, 6, 4), "200.00", Basis.RAW)]
    adj = [bar(date(2026, 6, 2), "100.00", Basis.SPLIT_ADJUSTED),
           bar(date(2026, 6, 3), "200.00", Basis.SPLIT_ADJUSTED),
           bar(date(2026, 6, 4), "200.00", Basis.SPLIT_ADJUSTED)]
    assert qc(raw, adj, events=(SPLIT, rev)).status is PriceAdjustmentQC.CONSISTENT


def test_other_symbol_stock_dividend_does_not_block_qc():
    bbb_sd = SplitEvent("BBB", E, D(100), D(105), ActionType.STOCK_DIVIDEND)
    assert qc(*series(), events=(SPLIT, bbb_sd)).status is PriceAdjustmentQC.CONSISTENT
