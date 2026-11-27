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


def qc(raw, adj, events=(SPLIT,), rp=P, ap=P):
    return check_adjustment_qc(raw=raw, adjusted=adj, events=list(events),
                               raw_precision=rp, adjusted_precision=ap)


def test_cumulative_factor_direction():
    ev = [SPLIT, SplitEvent("AAA", date(2026, 9, 1), D(10), D(1),
                            ActionType.REVERSE_SPLIT)]
    assert cumulative_factor(date(2026, 5, 1), ev) == D(2) * D("0.1")
    assert cumulative_factor(E, ev) == D("0.1")          # execution day is post-split
    assert cumulative_factor(date(2026, 9, 1), ev) == D(1)


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
