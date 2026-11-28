from datetime import date, datetime, time, timezone

from src.price.calendar import expected_latest_session, midnight_et_after_session
from src.price_providers.calendar_io import load_calendar


def test_reference_calendar_loads_and_matches_nyse_2026():
    cal = load_calendar()
    assert date(2026, 11, 26) in cal.holidays          # Thanksgiving
    assert cal.early_closes[date(2026, 11, 27)] == time(13, 0)
    assert date(2026, 7, 3) in cal.holidays             # Independence Day observed
    assert date(2027, 12, 24) in cal.holidays
    assert "NYSE_HOURS_CALENDARS" in cal.calendar_version
    assert "UNVERIFIED_2025" in cal.calendar_version
    now = datetime(2026, 11, 27, 5, 10, tzinfo=timezone.utc)   # Fri 14:10 KST
    assert expected_latest_session(cal, now, midnight_et_after_session) == date(2026, 11, 25)


def test_rejects_bad_rows(tmp_path):
    import pytest
    p = tmp_path / "c.csv"
    p.write_text("date,kind,close_et,source\n2026-01-01,FOO,,x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_calendar(p)
    p.write_text("date,kind,close_et,source\n2026-01-01,HOLIDAY,,x\n"
                 "2026-01-01,EARLY_CLOSE,13:00,x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_calendar(p)
