from datetime import date, datetime, time, timezone

from src.price.calendar import TradingCalendar, midnight_et_after_session
from src.price.freshness import assess_freshness
from src.price.schema import Freshness, Reason

CAL = TradingCalendar(holidays=frozenset({date(2026, 11, 26)}),
                      early_closes={date(2026, 11, 27): time(13, 0)})
NOW_TUE = datetime(2026, 11, 24, 5, 10, tzinfo=timezone.utc)   # expects Mon 11-23
NOW_MON = datetime(2026, 11, 30, 5, 10, tzinfo=timezone.utc)   # expects Fri 11-27


def run(latest, now=NOW_TUE, last=None):
    return assess_freshness(calendar=CAL, now_utc=now, latest_bar_date=latest,
                            availability_rule=midnight_et_after_session,
                            last_evaluated_session=last)


def test_fresh():
    r = run(date(2026, 11, 23))
    assert r.status is Freshness.FRESH and r.reason is None


def test_stale_old_session_only():
    r = run(date(2026, 11, 20))
    assert r.status is Freshness.STALE and r.reason == Reason.PRICE_STALE
    assert r.actual_session == date(2026, 11, 20)


def test_unavailable_no_data():
    r = run(None)
    assert r.status is Freshness.UNAVAILABLE and r.reason == Reason.PRICE_UNAVAILABLE


def test_unavailable_future_bar():
    r = run(date(2026, 11, 24))
    assert r.status is Freshness.UNAVAILABLE
    assert r.reason == Reason.BAR_AFTER_EXPECTED_SESSION


def test_no_new_session_after_weekend():
    r = run(date(2026, 11, 27), now=NOW_MON, last=date(2026, 11, 27))
    assert r.status is Freshness.NO_NEW_SESSION


def test_new_session_when_last_evaluated_older():
    r = run(date(2026, 11, 27), now=NOW_MON, last=date(2026, 11, 25))
    assert r.status is Freshness.FRESH
