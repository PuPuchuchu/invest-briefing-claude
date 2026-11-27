"""
Data-freshness assessment (P3 v3 section 5, P3 v7).

Outcomes:
  NO_NEW_SESSION -- the expected latest session was already evaluated
                    (caller passes `last_evaluated_session`); normal exit.
  FRESH          -- latest bar date == expected latest session.
  STALE          -- latest bar date is older than expected.
  UNAVAILABLE    -- no bar at all, or a bar dated AFTER the expected
                    session (calendar / provider anomaly).

Stale data is never presented as fresh. No retry count or retry time is
fixed here: provider publication delay is unverified (PoC item), so
retry policy belongs to the operating layer and is decided later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from src.price.calendar import AvailabilityRule, TradingCalendar, expected_latest_session
from src.price.schema import Freshness, Reason


@dataclass(frozen=True)
class FreshnessResult:
    status: Freshness
    expected_session: date
    actual_session: Optional[date]
    reason: Optional[str]


def assess_freshness(*, calendar: TradingCalendar, now_utc: datetime,
                     latest_bar_date: Optional[date],
                     availability_rule: AvailabilityRule,
                     last_evaluated_session: Optional[date] = None
                     ) -> FreshnessResult:
    expected = expected_latest_session(calendar, now_utc, availability_rule)
    if last_evaluated_session is not None and expected <= last_evaluated_session:
        return FreshnessResult(Freshness.NO_NEW_SESSION, expected,
                               latest_bar_date, Reason.NO_NEW_SESSION)
    if latest_bar_date is None:
        return FreshnessResult(Freshness.UNAVAILABLE, expected, None,
                               Reason.PRICE_UNAVAILABLE)
    if latest_bar_date > expected:
        return FreshnessResult(Freshness.UNAVAILABLE, expected, latest_bar_date,
                               Reason.BAR_AFTER_EXPECTED_SESSION)
    if latest_bar_date < expected:
        return FreshnessResult(Freshness.STALE, expected, latest_bar_date,
                               Reason.PRICE_STALE)
    return FreshnessResult(Freshness.FRESH, expected, latest_bar_date, None)
