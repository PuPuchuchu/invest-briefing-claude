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


def resp(events, start=date(2024, 11, 27), end=W1, partial=False, symbols=None):
    return SplitEventsResponse(start, end, tuple(events), "synthetic", SCHEMA_VERSION,
                               TS, partial, symbols)


def run(events, qc=OK, shares=SharesSplitEvidence.NOT_AVAILABLE, failed=False,
        w0=W0, cov=COV, dividends=(), response=None):
    if response is None and events is not None:
        response = resp(events)
    return assess_corporate_actions(symbol="AAA", window_start=w0, window_end=W1,
                                    response=response, fetch_failed=failed, qc=qc,
                                    coverage=cov, shares_evidence=shares,
                                    cash_dividend_dates=dividends)


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
        run([split(date(2026, 6, 3))]),                                   # pending SEC
        run([split(date(2026, 6, 3))], shares=SharesSplitEvidence.INCONSISTENT),
        run([split(date(2026, 6, 3))], shares=SharesSplitEvidence.AMBIGUOUS),
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
