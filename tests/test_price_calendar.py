from datetime import date, datetime, time, timezone

import pytest

from src.price.calendar import (
    NY_TZ, TradingCalendar, expected_latest_session, midnight_et_after_session,
)

# Synthetic calendar data (injected; not a verified exchange calendar).
CAL = TradingCalendar(
    holidays=frozenset({date(2026, 11, 26)}),          # Thanksgiving 2026
    early_closes={date(2026, 11, 27): time(13, 0)},    # day after Thanksgiving
    calendar_version="synthetic-test",
)


def utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_trading_day_rules():
    assert CAL.is_trading_day(date(2026, 11, 25))       # Wed
    assert not CAL.is_trading_day(date(2026, 11, 26))   # holiday
    assert not CAL.is_trading_day(date(2026, 11, 28))   # Sat
    assert not CAL.is_trading_day(date(2026, 11, 29))   # Sun
    assert CAL.previous_trading_day(date(2026, 11, 30)) == date(2026, 11, 27)
    assert CAL.previous_trading_day(date(2026, 11, 27)) == date(2026, 11, 25)


def test_early_close_and_regular_close():
    assert CAL.session_close_et(date(2026, 11, 27)).time() == time(13, 0)
    assert CAL.session_close_et(date(2026, 11, 25)).time() == time(16, 0)
    with pytest.raises(ValueError):
        CAL.session_close_et(date(2026, 11, 26))


def test_last_trading_day_of_week_with_holiday_friday():
    cal = TradingCalendar(holidays=frozenset({date(2026, 4, 3)}))  # Good Friday 2026
    assert cal.last_trading_day_of_week(date(2026, 3, 30)) == date(2026, 4, 2)


@pytest.mark.parametrize("now,expected", [
    (utc(2026, 11, 24, 5, 10), date(2026, 11, 23)),  # Tue 14:10 KST -> Mon session
    (utc(2026, 11, 28, 5, 10), date(2026, 11, 27)),  # Sat 14:10 KST -> early-close Fri
    (utc(2026, 11, 27, 5, 10), date(2026, 11, 25)),  # Fri 14:10 KST -> skip holiday
    (utc(2026, 11, 29, 5, 10), date(2026, 11, 27)),  # Sun -> Fri
    (utc(2026, 11, 30, 5, 10), date(2026, 11, 27)),  # Mon 14:10 KST -> still Fri
])
def test_expected_latest_session(now, expected):
    assert expected_latest_session(CAL, now, midnight_et_after_session) == expected


def test_dst_aware_availability():
    cal = TradingCalendar(holidays=frozenset())
    # 2026-03-08 US DST starts. 04:30 UTC Mar 10 = 00:30 EDT -> Mar 9 available.
    assert expected_latest_session(cal, utc(2026, 3, 10, 4, 30),
                                   midnight_et_after_session) == date(2026, 3, 9)
    # 03:30 UTC Mar 10 = 23:30 EDT Mar 9 -> Mar 9 not yet available.
    assert expected_latest_session(cal, utc(2026, 3, 10, 3, 30),
                                   midnight_et_after_session) == date(2026, 3, 6)
    # Winter (EST): 05:10 UTC Nov 24 = 00:10 EST.
    assert midnight_et_after_session(date(2026, 11, 23)).astimezone(timezone.utc) \
        == utc(2026, 11, 24, 5, 0)


def test_naive_now_rejected():
    with pytest.raises(ValueError):
        expected_latest_session(CAL, datetime(2026, 11, 24, 5, 10),
                                midnight_et_after_session)


def test_availability_rule_is_injectable():
    def at_close(d):
        return CAL.session_close_et(d)
    now = datetime(2026, 11, 25, 16, 30, tzinfo=NY_TZ).astimezone(timezone.utc)
    assert expected_latest_session(CAL, now, at_close) == date(2026, 11, 25)


def test_availability_boundary_is_inclusive():
    exact = midnight_et_after_session(date(2026, 11, 23)).astimezone(timezone.utc)
    assert expected_latest_session(CAL, exact, midnight_et_after_session) \
        == date(2026, 11, 23)
