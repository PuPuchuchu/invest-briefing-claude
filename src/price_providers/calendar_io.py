"""
Load the injected US trading calendar from data/reference/us_market_calendar.csv.

Columns: date (YYYY-MM-DD), kind (HOLIDAY | EARLY_CLOSE), close_et (HH:MM
for EARLY_CLOSE), source. 2026-2028 rows come from the NYSE "Holidays &
Trading Hours" page (read 2026-11-27); 2025 rows are marked
UNVERIFIED_2025 (the page does not list 2025). A wrong or missing
holiday makes the engine fail closed (STALE / missing week bar), never
silently wrong.
"""

from __future__ import annotations

import csv
from datetime import date, time
from pathlib import Path

from src.price.calendar import TradingCalendar

DEFAULT_CALENDAR_CSV = Path(__file__).resolve().parents[2] / "data" / "reference" \
    / "us_market_calendar.csv"


def load_calendar(path: Path = DEFAULT_CALENDAR_CSV) -> TradingCalendar:
    holidays: set[date] = set()
    early: dict[date, time] = {}
    sources: set[str] = set()
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            d = date.fromisoformat(row["date"])
            kind = row["kind"].strip()
            if kind == "HOLIDAY":
                holidays.add(d)
            elif kind == "EARLY_CLOSE":
                hh, mm = row["close_et"].split(":")
                early[d] = time(int(hh), int(mm))
            else:
                raise ValueError("UNKNOWN_CALENDAR_KIND")
            sources.add(row["source"].strip())
    if holidays & set(early):
        raise ValueError("DATE_BOTH_HOLIDAY_AND_EARLY_CLOSE")
    version = "us_market_calendar:" + ",".join(sorted(sources))
    return TradingCalendar(holidays=frozenset(holidays), early_closes=early,
                           calendar_version=version)
