"""F2 (DEC-060, 2026-11-24): prior-year comparable-quarter selection in growth._select_latest_quarter_pair.

A wrong comparison quarter is worse than a missing one: the pair must end 351-379 days apart (365 +- 14, both ends
included; 52-week year = 364, 53-week year = 371). The label match (same quarter, prior fiscal-year label) is tried
first; otherwise the single earlier record in that window is used; anything else is no pair (MISSING). The previous
fallback (nearest record in the prior calendar year) is gone.
"""
from datetime import date, timedelta

from src.fundamentals import growth

MISSING_REASON = "No prior-year comparable quarter found."


def rec(end, value, quarter, fy):
    return {"status": "OK", "period_end": end, "value": value, "quarter": quarter, "fy": fy}


def days_before(end, days):
    return (date.fromisoformat(end) - timedelta(days=days)).isoformat()


def pair(records):
    latest, previous = growth._select_latest_quarter_pair({"quarterly": records})
    return (latest and latest["period_end"], previous and previous["period_end"])


LATEST = "2026-06-30"


# ---------------------------------------------------------------- window boundaries (label match)
def test_label_match_350_days_rejected():
    prev = days_before(LATEST, 350)
    assert pair([rec(prev, 90, "Q2", 2025), rec(LATEST, 100, "Q2", 2026)]) == (LATEST, None)


def test_label_match_351_days_accepted():
    prev = days_before(LATEST, 351)
    assert pair([rec(prev, 90, "Q2", 2025), rec(LATEST, 100, "Q2", 2026)]) == (LATEST, prev)


def test_label_match_379_days_accepted():
    prev = days_before(LATEST, 379)
    assert pair([rec(prev, 90, "Q2", 2025), rec(LATEST, 100, "Q2", 2026)]) == (LATEST, prev)


def test_label_match_380_days_rejected():
    prev = days_before(LATEST, 380)
    assert pair([rec(prev, 90, "Q2", 2025), rec(LATEST, 100, "Q2", 2026)]) == (LATEST, None)


def test_label_match_two_years_apart_is_not_a_prior_year_pair():
    # a fy label off by one: the labelled record is 730 days back -> rejected; nothing else in the window -> no pair
    prev = days_before(LATEST, 730)
    assert pair([rec(prev, 90, "Q2", 2025), rec(LATEST, 100, "Q2", 2026)]) == (LATEST, None)


def test_label_match_out_of_window_falls_back_to_the_single_window_record():
    far = days_before(LATEST, 730)
    good = days_before(LATEST, 365)
    records = [rec(far, 80, "Q2", 2025), rec(good, 90, "Q2", 2024), rec(LATEST, 100, "Q2", 2026)]
    assert pair(records) == (LATEST, good)


# ---------------------------------------------------------------- fallback cardinality
def test_fallback_no_candidate_is_missing():
    records = [rec(days_before(LATEST, 200), 90, "Q4", 2025), rec(LATEST, 100, "Q2", 2026)]
    assert pair(records) == (LATEST, None)


def test_fallback_one_candidate_is_used():
    prev = days_before(LATEST, 364)
    records = [rec(prev, 90, None, None), rec(LATEST, 100, "Q2", 2026)]
    assert pair(records) == (LATEST, prev)


def test_fallback_two_candidates_is_missing():
    records = [rec(days_before(LATEST, 360), 90, None, None), rec(days_before(LATEST, 370), 91, None, None),
               rec(LATEST, 100, "Q2", 2026)]
    assert pair(records) == (LATEST, None)


def test_duplicate_period_end_with_different_values_is_ambiguous():
    prev = days_before(LATEST, 365)
    records = [rec(prev, 90, "Q2", 2025), rec(prev, 91, "Q2", 2025), rec(LATEST, 100, "Q2", 2026)]
    assert pair(records) == (LATEST, None)


def test_duplicate_period_end_with_the_same_value_is_one_candidate():
    prev = days_before(LATEST, 365)
    records = [rec(prev, 90, "Q2", 2025), rec(prev, 90, "Q2", 2025), rec(LATEST, 100, "Q2", 2026)]
    assert pair(records) == (LATEST, prev)


# ---------------------------------------------------------------- real-data shapes (DEC-060 audit)
def test_googl_shape_q1_missing_does_not_pair_with_prior_q4():
    # FY2025 Q1/Q2 absent (no Q1 tag); the old fallback paired 2026 Q1 with 2025-12-31 (a quarter-over-quarter "YoY")
    records = [rec("2025-09-30", 102, "Q3", 2025), rec("2025-12-31", 113, "Q4", 2025), rec("2026-03-31", 110, "Q1", 2026)]
    assert pair(records) == ("2026-03-31", None)
    result = growth._calculate_history_yoy("revenue_yoy", {"quarterly": records}, positive_only=False)
    assert result["status"] == "MISSING"
    assert result["reason"] == MISSING_REASON


def test_tem_shape_ipo_year_does_not_pair_with_another_quarter():
    # IPO year: no prior-year Q1; the old fallback picked 2024-09-30 (182 days)
    records = [rec("2024-06-30", 166, "Q2", 2024), rec("2024-09-30", 181, "Q3", 2024), rec("2025-03-31", 256, "Q1", 2025)]
    assert pair(records) == ("2025-03-31", None)


def test_52_week_year_pair_is_accepted():
    records = [rec("2025-09-27", 90, "Q4", 2025), rec("2026-09-26", 100, "Q4", 2026)]   # 364 days
    assert pair(records) == ("2026-09-26", "2025-09-27")


def test_53_week_year_pair_is_accepted():
    records = [rec("2022-09-24", 90, "Q4", 2022), rec("2023-09-30", 100, "Q4", 2023)]   # 371 days
    assert pair(records) == ("2023-09-30", "2022-09-24")


def test_calendar_and_leap_year_pairs_are_accepted():
    assert pair([rec("2023-03-31", 90, "Q1", 2023), rec("2024-03-31", 100, "Q1", 2024)]) == ("2024-03-31", "2023-03-31")  # 366
    assert pair([rec("2024-09-30", 90, "Q3", 2024), rec("2025-09-30", 100, "Q3", 2025)]) == ("2025-09-30", "2024-09-30")  # 365


def test_normal_yoy_value_unchanged():
    records = [rec("2025-06-30", 80, "Q2", 2025), rec("2025-09-30", 85, "Q3", 2025), rec("2026-06-30", 100, "Q2", 2026)]
    result = growth._calculate_history_yoy("revenue_yoy", {"quarterly": records}, positive_only=False)
    assert result["status"] == "OK"
    assert result["value"] == 0.25
    assert (result["current_period_end"], result["previous_period_end"]) == ("2026-06-30", "2025-06-30")


def test_latest_missing_quarter_limitation_is_unchanged():
    # sec_history may drop a newest quarter (F5); growth still treats the newest record it receives as "latest"
    records = [rec("2025-03-31", 80, "Q1", 2025), rec("2026-03-31", 100, "Q1", 2026)]
    assert pair(records) == ("2026-03-31", "2025-03-31")


def test_unparsable_latest_date_is_no_pair():
    records = [rec("2025-06-30", 90, "Q2", 2025), rec("2026-13-40", 100, "Q2", 2026)]
    assert pair(records)[1] is None


# ---------------------------------------------------------------- EPS N-P1 path keeps its own guard
def test_eps_same_filing_path_reports_missing_without_a_pair():
    history = {"quarterly": [rec("2024-09-30", 1.0, "Q3", 2024), rec("2025-03-31", 1.2, "Q1", 2025)]}
    result = growth._calculate_eps_yoy_same_filing(history)
    assert result["status"] == "MISSING"
    assert result["reason"] == MISSING_REASON
    assert "previous_value_basis" not in result
