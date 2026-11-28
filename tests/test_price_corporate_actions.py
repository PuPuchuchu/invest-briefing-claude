import pytest
from datetime import date, datetime, timezone
from decimal import Decimal as D

from src.price.adjustment_qc import QCResult
from src.price.corporate_actions import SharesSplitEvidence, assess_corporate_actions
from src.price.schema import (
    ActionType, CorporateActionStatus as S, PriceAdjustmentQC as Q,
    ProviderCoverage, Reason, SCHEMA_VERSION, SplitEvent, SplitEventsResponse,
)

COV = ProviderCoverage("synthetic", SCHEMA_VERSION, date(2024, 11, 27),
                       date(2024, 11, 27), "DOCUMENTED", date(2026, 11, 26))
W0, W1 = date(2026, 1, 2), date(2026, 11, 25)
OK = QCResult(Q.CONSISTENT, (), 10)
BAD = QCResult(Q.INCONSISTENT, (Reason.QC_OUT_OF_INTERVAL,), 3)
NA = QCResult(Q.NOT_AVAILABLE, (Reason.QC_PRECISION_UNKNOWN,), 0)


def split(day, frm=1, to=2, t=ActionType.FORWARD_SPLIT, sym="AAA"):
    return SplitEvent(sym, day, D(frm), D(to), t)


TS = datetime(2026, 11, 27, 5, 10, tzinfo=timezone.utc)
from src.price.calendar import TradingCalendar  # noqa: E402
CAL = TradingCalendar(holidays=frozenset(), early_closes={}, calendar_version="test")


def resp(events, start=date(2024, 11, 27), end=W1, partial=False, symbols=None):
    return SplitEventsResponse(start, end, tuple(events), "synthetic", SCHEMA_VERSION,
                               TS, partial, symbols)


def raw_series(ex_day, before, after, n_before=5, n_after=5):
    """RAW bars on consecutive weekdays: `before` closes up to the session
    preceding ex_day, `after` closes from ex_day on."""
    from datetime import timedelta
    from src.price.schema import Basis, NormalizedBar
    days, d = [], ex_day
    while len([x for x in days if x < ex_day]) < n_before:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
    d = ex_day
    while len([x for x in days if x >= ex_day]) < n_after:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    out = []
    for day in sorted(days):
        c = before if day < ex_day else after
        out.append(NormalizedBar(day, c, c, c, c, 1, Basis.RAW, "synthetic", TS))
    return tuple(out)


def run(events, qc=OK, shares=SharesSplitEvidence.NOT_AVAILABLE, failed=False,
        w0=W0, cov=COV, dividends=(), response=None, raw=(), cal=CAL):
    if response is None and events is not None:
        response = resp(events)
    return assess_corporate_actions(symbol="AAA", window_start=w0, window_end=W1,
                                    response=response, fetch_failed=failed, qc=qc,
                                    coverage=cov, shares_evidence=shares,
                                    cash_dividend_dates=dividends, raw_bars=raw,
                                    calendar=cal)


def test_row1_confirmed():
    r = run([split(date(2026, 6, 3))], shares=SharesSplitEvidence.CONSISTENT)
    assert r.status is S.CONFIRMED and r.r3_row == "1"
    assert r.momentum_eligible and r.valuation_roll_eligible


def test_reverse_split_confirmed():
    r = run([split(date(2026, 6, 3), 10, 1, ActionType.REVERSE_SPLIT)],
            shares=SharesSplitEvidence.CONSISTENT)
    assert r.status is S.CONFIRMED


def test_row10_cash_dividend_has_no_effect():
    a = run([], dividends=[date(2026, 3, 1), date(2026, 9, 1)])
    b = run([])
    assert a == b and a.status is S.NO_EVENT_VERIFIED


def test_row15_fetch_failed():
    for r in (run(None, failed=True), run(None)):
        assert r.status is S.UNVERIFIED and r.reasons == (Reason.SPLIT_FETCH_FAILED,)
        assert not r.momentum_eligible


def test_row16_no_event_verified():
    r = run([split(date(2026, 6, 3), sym="BBB")])   # other symbol ignored
    assert r.status is S.NO_EVENT_VERIFIED and r.r3_row == "16"


def test_row3_missing_event_fail_closed():
    r = run([], qc=BAD)
    assert r.status is S.CANDIDATE and r.reasons == (Reason.SPLIT_EVENT_MISSING,)
    assert not r.momentum_eligible and not r.valuation_roll_eligible


def test_no_event_qc_not_available_is_unverified():
    assert run([], qc=NA).status is S.UNVERIFIED


def test_row13_coverage():
    r = run([], w0=date(2024, 1, 2))
    assert r.status is S.UNVERIFIED and r.reasons == (Reason.COVERAGE_UNVERIFIED,)
    cov_unknown = ProviderCoverage("s", SCHEMA_VERSION, None, None, "UNKNOWN")
    assert run([], cov=cov_unknown).status is S.UNVERIFIED


def test_event_on_window_start_is_outside_window():
    assert run([split(W0)]).status is S.NO_EVENT_VERIFIED


def test_unsupported_scenarios_never_ok():
    cases = [
        run([split(date(2026, 3, 2)), split(date(2026, 8, 3))],
            shares=SharesSplitEvidence.CONSISTENT),                       # multiple
        run([split(date(2026, 6, 3), 100, 105, ActionType.STOCK_DIVIDEND)],
            shares=SharesSplitEvidence.CONSISTENT),                       # stock div
        run([split(date(2026, 6, 3))], shares=SharesSplitEvidence.INCONSISTENT),
        run([split(date(2026, 6, 3))], shares=SharesSplitEvidence.AMBIGUOUS),
        run([split(date(2026, 6, 3))], shares=SharesSplitEvidence.NO_CHANGE),
        # rows 4/5 stay blocked even with a perfect raw jump
        run([split(date(2026, 6, 3))], shares=SharesSplitEvidence.INCONSISTENT,
            raw=raw_series(date(2026, 6, 3), D(100), D(50))),
    ]
    for r in cases:
        assert r.status is S.UNVERIFIED and not r.supported
        assert r.reasons == (Reason.UNSUPPORTED_SCENARIO,)
        assert not r.momentum_eligible and not r.valuation_roll_eligible


def test_single_split_qc_inconsistent_is_candidate():
    r = run([split(date(2026, 6, 3))], qc=BAD, shares=SharesSplitEvidence.CONSISTENT)
    assert r.status is S.CANDIDATE and not r.momentum_eligible


def test_empty_response_is_evidence_only_if_complete():
    cases = [resp([], partial=True),                       # partial / unfinished pages
             resp([], start=date(2026, 3, 1)),             # range starts after window
             resp([], end=date(2026, 11, 1)),              # range ends before window
             resp([], symbols=("BBB",))]                   # filtered to other symbols
    for r in cases:
        a = run(None, response=r)
        assert a.status is S.UNVERIFIED and a.reasons == (Reason.PARTIAL_RESPONSE,)
        assert not a.momentum_eligible
    ok = run(None, response=resp([], symbols=("AAA",)))
    assert ok.status is S.NO_EVENT_VERIFIED


def test_coverage_upper_and_lower_bounds():
    def cov(start, end, source="DOCUMENTED"):
        return ProviderCoverage("s", SCHEMA_VERSION, start, start, source, end)
    c = date(2024, 11, 27)
    cases_bad = [cov(c, date(2026, 11, 24)),            # window_end after coverage end
                 cov(date(2026, 1, 3), W1),             # window_start before coverage
                 cov(c, None),                          # end unknown
                 cov(c, W1, source="UNKNOWN"),          # not documented
                 cov(c, W1, source="SYNTHETIC")]
    for cv in cases_bad:
        r = run([], cov=cv)
        assert r.status is S.UNVERIFIED and r.reasons == (Reason.COVERAGE_UNVERIFIED,)
    # inclusive boundaries: window_start == coverage start, window_end == coverage end
    assert run([], cov=cov(W0, W1)).status is S.NO_EVENT_VERIFIED


# ---------------------------------------------------------------- R3 row 2
EX = date(2026, 6, 3)   # Wednesday


def test_row2_pending_sec_when_price_evidence_complete():
    r = run([split(EX)], raw=raw_series(EX, D(100), D("50.6")))          # 2:1, +1.2% move
    assert r.status is S.PENDING_SEC and r.r3_row == "2" and r.supported
    assert r.reasons == (Reason.SPLIT_PENDING_SEC,)
    assert r.momentum_eligible and not r.valuation_roll_eligible


def test_row2_ten_for_one_and_reverse():
    r = run([split(EX, 1, 10)], raw=raw_series(EX, D(1000), D("96")))     # NFLX-like
    assert r.status is S.PENDING_SEC
    r = run([split(EX, 10, 1, ActionType.REVERSE_SPLIT)],
            raw=raw_series(EX, D(2), D("21")))
    assert r.status is S.PENDING_SEC


def test_row2_without_raw_jump_is_blocked():
    cases = [
        raw_series(EX, D(100), D(100)),         # event but no jump (fabricated)
        raw_series(EX, D(100), D(74)),          # -26%: closer to "no split" than 2:1
        raw_series(date(2026, 6, 4), D(100), D(50)),   # jump on another date
        raw_series(EX, D(100), D(50), n_before=0),     # no session before ex date
        (),                                     # no raw bars passed
    ]
    for raw in cases:
        r = run([split(EX)], raw=raw)
        assert r.status is S.UNVERIFIED and r.r3_row == "2"
        assert r.reasons == (Reason.SPLIT_RAW_JUMP_UNVERIFIED,)
        assert not r.momentum_eligible and not r.valuation_roll_eligible


def test_row2_requires_qc_consistent():
    r = run([split(EX)], qc=NA, raw=raw_series(EX, D(100), D(50)))
    assert r.status is S.UNVERIFIED and not r.momentum_eligible
    r = run([split(EX)], qc=BAD, raw=raw_series(EX, D(100), D(50)))
    assert r.status is S.CANDIDATE and not r.momentum_eligible


def test_row2_band_edges():
    from src.price.corporate_actions import raw_jump_matches_split
    ev = split(EX)                                   # 2:1 -> |ln 2|/2 ~= 0.3466
    assert raw_jump_matches_split(raw_series(EX, D(100), D("70.6")), ev, CAL)   # 0.348 from 0
    assert not raw_jump_matches_split(raw_series(EX, D(100), D("70.8")), ev, CAL)


def test_row2_ex_date_on_first_bar_is_not_verified():
    from src.price.corporate_actions import raw_jump_matches_split
    from src.price.schema import Basis, NormalizedBar
    b = [NormalizedBar(d, c, c, c, c, 1, Basis.RAW, "synthetic", TS)
         for d, c in ((EX, D(50)), (date(2026, 6, 4), D(100)))]
    assert not raw_jump_matches_split(b, split(EX), CAL)



# ------------------------------------------- GPT re-review: adversarial matrix
def _jump(ratio_true, ev, move, gap_before=False, cal=CAL):
    """Raw closes 100 before EX; on/after EX: 100/ratio_true*(1+move)."""
    raw = raw_series(EX, D(100), (D(100) / D(ratio_true) * (1 + D(str(move)))).quantize(D("0.0001")))
    if gap_before:
        raw = tuple(b for b in raw if b.date != date(2026, 6, 2))
    return run([ev], raw=raw, cal=cal)


@pytest.mark.parametrize("r", [2, 3, 4, 10, 20])
@pytest.mark.parametrize("move", [-0.2, -0.1, -0.05, 0.05, 0.1, 0.2])
def test_real_split_with_extra_move_is_pending(r, move):
    assert _jump(r, split(EX, 1, r), move).status is S.PENDING_SEC


@pytest.mark.parametrize("r,move", [(2, -0.2), (2, 0.0), (3, -0.4), (4, -0.45),
                                    (10, -0.6), (20, -0.75), (2, 0.3)])
def test_no_real_split_moves_outside_band_rejected(r, move):
    a = _jump(1, split(EX, 1, r), move)
    assert a.status is S.UNVERIFIED and a.reasons == (Reason.SPLIT_RAW_JUMP_UNVERIFIED,)


@pytest.mark.parametrize("r,move", [(2, -0.4), (10, -0.8)])
def test_known_limit_crash_inside_band_is_accepted(r, move):
    """Documented residual risk: a crash of split-like size on the exact
    execution_date of a provider-recorded split is indistinguishable by price."""
    assert _jump(1, split(EX, 1, r), move).status is S.PENDING_SEC


@pytest.mark.parametrize("r", [2, 10])
def test_reversed_ratio_rejected(r):
    a = _jump(r, split(EX, r, 1, ActionType.REVERSE_SPLIT), 0)
    assert a.status is S.UNVERIFIED and not a.momentum_eligible


@pytest.mark.parametrize("day", [date(2026, 6, 2), date(2026, 6, 4)])
def test_execution_date_off_by_one_rejected(day):
    a = _jump(10, split(day, 1, 10), 0)
    assert a.status is S.UNVERIFIED and not a.momentum_eligible


def test_gap_before_execution_date_rejected():
    a = _jump(10, split(EX, 1, 10), 0, gap_before=True)
    assert a.status is S.UNVERIFIED and a.reasons == (Reason.SPLIT_RAW_JUMP_UNVERIFIED,)


def test_holiday_aware_previous_session():
    cal = TradingCalendar(holidays=frozenset({date(2026, 6, 2)}), early_closes={},
                          calendar_version="t")
    raw = tuple(b for b in raw_series(EX, D(100), D(10)) if b.date != date(2026, 6, 2))
    assert run([split(EX, 1, 10)], raw=raw, cal=cal).status is S.PENDING_SEC


def test_no_calendar_or_non_trading_execution_date_rejected():
    raw = raw_series(EX, D(100), D(10))
    assert run([split(EX, 1, 10)], raw=raw, cal=None).status is S.UNVERIFIED
    sat = date(2026, 6, 6)
    assert run([split(sat, 1, 10)], raw=raw).status is S.UNVERIFIED


def test_qc_inconsistent_with_passing_jump_is_candidate():
    a = run([split(EX, 1, 10)], qc=BAD, raw=raw_series(EX, D(100), D(10)))
    assert a.status is S.CANDIDATE and not a.momentum_eligible
