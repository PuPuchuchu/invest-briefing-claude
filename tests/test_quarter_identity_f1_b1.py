"""F1 + B1 (DEC-060, 2026-11-24): standalone-quarter identity in sec_history.reconstruct_standalone_quarters.

A1: the Q1 slot / H1 / 9M take only observations whose SEC fp is Q1 / Q2 / Q3 -- a 3-month fact from a Q2 filing can no
    longer occupy the Q1 slot (IPO year, missing Q1 tag, Q1 and Q2 10-Qs filed the same day).
B1: a quarter is derived by subtraction only when both operands share the fy label, the start date and the unit, and the
    derived period [previous end + 1, current end] is start <= end and 70-110 days (inclusive). Otherwise the quarter is
    absent. Two records can never share a period_end (all such records are dropped).
"""
from src.fundamentals import sec_history as sh


def ob(start, end, val, fy, fp, form="10-Q", filed="2025-08-01", unit="USD", accn="0000000001-25-000001"):
    return {"start": start, "end": end, "val": val, "fy": fy, "fp": fp, "form": form, "filed": filed, "_unit": unit, "accn": accn}


def quarters(observations):
    return {(r["quarter"], r["period_end"]): r for r in sh.reconstruct_standalone_quarters(observations)}


def full_year():
    """Calendar FY2025 with prior-year comparatives in each filing (normal case)."""
    return [
        ob("2025-01-01", "2025-03-31", 100, 2025, "Q1", filed="2025-05-01"),
        ob("2024-01-01", "2024-03-31", 90, 2025, "Q1", filed="2025-05-01"),            # comparative, same filing
        ob("2025-04-01", "2025-06-30", 110, 2025, "Q2", filed="2025-08-01"),
        ob("2025-01-01", "2025-06-30", 210, 2025, "Q2", filed="2025-08-01"),
        ob("2024-01-01", "2024-06-30", 190, 2025, "Q2", filed="2025-08-01"),            # comparative H1
        ob("2025-07-01", "2025-09-30", 120, 2025, "Q3", filed="2025-11-01"),
        ob("2025-01-01", "2025-09-30", 330, 2025, "Q3", filed="2025-11-01"),
        ob("2025-01-01", "2025-12-31", 460, 2025, "FY", form="10-K", filed="2026-02-01"),
    ]


# ---------------------------------------------------------------- regression: normal year
def test_normal_year_q1_to_q4():
    q = quarters(full_year())
    assert {k: v["value"] for k, v in q.items()} == {
        ("Q1", "2025-03-31"): 100, ("Q2", "2025-06-30"): 110, ("Q3", "2025-09-30"): 120, ("Q4", "2025-12-31"): 130}
    assert q[("Q2", "2025-06-30")]["period_start"] == "2025-04-01"
    assert q[("Q4", "2025-12-31")]["period_start"] == "2025-10-01"
    assert [r["reconstructed"] for r in sh.reconstruct_standalone_quarters(full_year())] == [False, True, True, True]


# ---------------------------------------------------------------- A1: Q1 slot needs fp == Q1
def test_no_q1_filing_leaves_q1_and_q2_absent():
    # IPO year / missing Q1 tag: only the Q2 10-Q (fp Q2) carries 3-month and H1 facts
    obs = [o for o in full_year() if o["fp"] != "Q1"]
    q = quarters(obs)
    assert ("Q1", "2025-03-31") not in q and ("Q1", "2025-06-30") not in q
    assert not any(k[0] == "Q2" for k in q)
    assert q[("Q3", "2025-09-30")]["value"] == 120             # 9M - H1 still valid
    for r in q.values():
        assert r["period_start"] <= r["period_end"]


def test_q1_and_q2_filed_the_same_day():
    # SMCI shape: Q1 and Q2 10-Qs filed on the same date; the old tie-break (latest end) put Q2 in the Q1 slot
    obs = [dict(o, filed="2025-08-01") if o["fp"] in ("Q1", "Q2") else o for o in full_year()]
    q = quarters(obs)
    assert q[("Q1", "2025-03-31")]["value"] == 100
    assert q[("Q2", "2025-06-30")]["value"] == 110
    assert q[("Q2", "2025-06-30")]["period_start"] == "2025-04-01"


def test_q2_three_month_fact_never_labelled_q1():
    obs = [o for o in full_year() if o["fp"] != "Q1"]
    for r in sh.reconstruct_standalone_quarters(obs):
        assert not (r["quarter"] == "Q1" and r["period_end"] == "2025-06-30")


def test_no_period_start_after_period_end():
    for obs in (full_year(), [o for o in full_year() if o["fp"] != "Q1"],
                [dict(o, filed="2025-08-01") if o["fp"] in ("Q1", "Q2") else o for o in full_year()]):
        for r in sh.reconstruct_standalone_quarters(obs):
            assert r["period_start"] <= r["period_end"], r


# ---------------------------------------------------------------- B1: operand consistency
def test_fy_mismatch_is_not_reconstructed():
    h1 = ob("2025-01-01", "2025-06-30", 210, 2025, "Q2")
    q1 = ob("2025-01-01", "2025-03-31", 100, 2024, "Q1")
    assert sh._reconstruction_operands_consistent(h1, q1) is False
    assert sh._reconstruction_operands_consistent(h1, dict(q1, fy=2025)) is True


def test_start_mismatch_is_not_reconstructed():
    obs = [dict(o, start="2025-01-02") if (o["fp"] == "Q2" and o["end"] == "2025-06-30" and o["start"] == "2025-01-01") else o
           for o in full_year()]
    q = quarters(obs)
    assert ("Q2", "2025-06-30") not in q
    assert ("Q3", "2025-09-30") not in q        # 9M - H1 also needs the same start
    assert q[("Q1", "2025-03-31")]["value"] == 100


def test_unit_mismatch_is_not_reconstructed():
    obs = [dict(o, _unit="EUR") if (o["fp"] == "Q2" and o["start"] == "2025-01-01") else o for o in full_year()]
    q = quarters(obs)
    assert ("Q2", "2025-06-30") not in q
    assert ("Q3", "2025-09-30") not in q


def test_concept_identity_is_per_call():
    # one concept per call: operands from another concept's data are never combined
    revenue = [o for o in full_year() if not (o["fp"] == "Q2" and o["start"] == "2025-01-01")]   # revenue lacks H1
    other_concept_h1 = [o for o in full_year() if o["fp"] == "Q2" and o["start"] == "2025-01-01"]
    assert ("Q2", "2025-06-30") not in quarters(revenue)
    assert quarters(other_concept_h1) == {}


def _derived_q2(h1_days):
    # Q1 = 90 days (2025-01-01..2025-03-31); H1 = h1_days from 2025-01-01; derived Q2 = h1_days - 90 days
    from datetime import date, timedelta
    h1_end = (date(2025, 1, 1) + timedelta(days=h1_days - 1)).isoformat()
    obs = [ob("2025-01-01", "2025-03-31", 100, 2025, "Q1", filed="2025-05-01"),
           ob("2025-01-01", h1_end, 250, 2025, "Q2", filed="2025-08-01")]
    return [r for r in sh.reconstruct_standalone_quarters(obs) if r["quarter"] == "Q2"]


def test_derived_length_boundaries_69_70_110_111():
    assert _derived_q2(159) == []                       # derived 69 days
    assert len(_derived_q2(160)) == 1                   # 70
    assert len(_derived_q2(200)) == 1                   # 110
    assert _derived_q2(201) == []                       # 111 (H1 201 days is still a 6-month YTD)


def test_derived_period_reversed_is_rejected():
    same_end = ob("2025-01-01", "2025-03-31", 100, 2025, "Q2")
    assert sh._reconstruction_operands_consistent(same_end, ob("2025-01-01", "2025-03-31", 90, 2025, "Q1")) is False


def test_duplicate_period_end_records_are_dropped():
    records = [{"quarter": "Q1", "period_end": "2025-06-30"}, {"quarter": "Q2", "period_end": "2025-06-30"},
               {"quarter": "Q3", "period_end": "2025-09-30"}]
    assert sh._drop_duplicate_period_ends(records) == [{"quarter": "Q3", "period_end": "2025-09-30"}]
    ends = [r["period_end"] for r in sh.reconstruct_standalone_quarters(full_year())]
    assert len(ends) == len(set(ends))


# ---------------------------------------------------------------- EPS direct-only path unchanged
def test_eps_direct_only_path_unchanged():
    obs = [ob("2025-01-01", "2025-03-31", 1.0, 2025, "Q1"), ob("2025-04-01", "2025-06-30", 1.1, 2025, "Q2"),
           ob("2025-01-01", "2025-06-30", 2.1, 2025, "Q2")]
    out = sh.reconstruct_standalone_quarters(obs, concept_name="EarningsPerShareDiluted")
    assert [(r["quarter"], r["value"], r["reconstructed"]) for r in out] == [("Q1", 1.0, False), ("Q2", 1.1, False)]
