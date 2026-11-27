"""
Data-freshness assessment (P3 v3 section 5, P3 v7).

Decision order (first match wins):
  1. no bar at all                         -> UNAVAILABLE
  2. bar dated after the expected session  -> UNAVAILABLE (anomaly)
  3. bar older than the expected session   -> STALE
     (also when that older session was already evaluated: lagging or
     regressed data is never masked as NO_NEW_SESSION)
  4. bar == expected AND already evaluated -> NO_NEW_SESSION (idempotent exit)
  5. bar == expected, not yet evaluated    -> FRESH

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
    # Data anomalies are reported first so they are never hidden behind
    # the idempotency outcome.
    if latest_bar_date is None:
        return FreshnessResult(Freshness.UNAVAILABLE, expected, None,
                               Reason.PRICE_UNAVAILABLE)
    if latest_bar_date > expected:
        return FreshnessResult(Freshness.UNAVAILABLE, expected, latest_bar_date,
                               Reason.BAR_AFTER_EXPECTED_SESSION)
    if latest_bar_date < expected:
        return FreshnessResult(Freshness.STALE, expected, latest_bar_date,
                               Reason.PRICE_STALE)
    # latest_bar_date == expected from here on
    if last_evaluated_session is not None and latest_bar_date <= last_evaluated_session:
        return FreshnessResult(Freshness.NO_NEW_SESSION, expected,
                               latest_bar_date, Reason.NO_NEW_SESSION)
    return FreshnessResult(Freshness.FRESH, expected, latest_bar_date, None)
