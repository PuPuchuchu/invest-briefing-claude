"""
US trading-calendar logic with INJECTED calendar data.

Nothing here downloads a calendar. The caller supplies the set of full
holidays and the early-close dates (with their close time, US/Eastern);
the source and version of that data are an open decision (P4 G10).

Times are handled with zoneinfo("America/New_York") so daylight-saving
transitions are respected.

Data availability (when a provider publishes a session's daily bar) is
NOT hard-coded: callers pass an `availability_rule(session_date) ->
aware datetime`. `midnight_et_after_session` implements the rule stated
in the Massive Stocks Basic knowledge-base article ("after midnight ET");
it is provided as an explicit, swappable rule, not as a verified fact
about live behaviour (PoC item).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
REGULAR_CLOSE_ET = time(16, 0)
MAX_LOOKBACK_DAYS = 31

AvailabilityRule = Callable[[date], datetime]


@dataclass(frozen=True)
class TradingCalendar:
    holidays: frozenset[date]
    early_closes: dict[date, time] = field(default_factory=dict)
    calendar_version: str = "UNSPECIFIED"

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def session_close_et(self, d: date) -> datetime:
        if not self.is_trading_day(d):
            raise ValueError("NOT_A_TRADING_DAY")
        close = self.early_closes.get(d, REGULAR_CLOSE_ET)
        return datetime.combine(d, close, tzinfo=NY_TZ)

    def session_close_utc(self, d: date) -> datetime:
        return self.session_close_et(d).astimezone(timezone.utc)

    def previous_trading_day(self, d: date) -> date:
        cur = d - timedelta(days=1)
        for _ in range(MAX_LOOKBACK_DAYS):
            if self.is_trading_day(cur):
                return cur
            cur -= timedelta(days=1)
        raise ValueError("NO_TRADING_DAY_IN_LOOKBACK")

    def last_trading_day_of_week(self, d: date) -> Optional[date]:
        """Last trading day of the Mon-Sun week containing `d` (None if none)."""
        monday = d - timedelta(days=d.weekday())
        for offset in range(6, -1, -1):
            cur = monday + timedelta(days=offset)
            if self.is_trading_day(cur):
                return cur
        return None


def midnight_et_after_session(session_date: date) -> datetime:
    """Availability rule: bar for `session_date` usable from next 00:00 ET."""
    return datetime.combine(session_date + timedelta(days=1), time(0, 0),
                            tzinfo=NY_TZ)


def expected_latest_session(calendar: TradingCalendar, now_utc: datetime,
                            availability_rule: AvailabilityRule) -> date:
    """
    Most recent trading day whose bar should already be available at
    `now_utc` under `availability_rule`.
    """
    if now_utc.tzinfo is None:
        raise ValueError("NOW_MUST_BE_TZ_AWARE")
    cur = now_utc.astimezone(NY_TZ).date()
    for _ in range(MAX_LOOKBACK_DAYS):
        if calendar.is_trading_day(cur) and availability_rule(cur) <= now_utc:
            return cur
        cur -= timedelta(days=1)
    raise ValueError("NO_AVAILABLE_SESSION_IN_LOOKBACK")
