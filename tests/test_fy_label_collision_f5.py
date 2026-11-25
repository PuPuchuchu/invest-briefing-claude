"""F5 (2026-11-25): SEC fy-label collision in sec_history.reconstruct_standalone_quarters.

A later 10-Q that repeats the (fy, fp) label of the same fiscal quarter one year earlier (ORCL FY2027 Q1, period ending
2026-08-31, filed 2026-09-11 with fy=2026 like the FY2026 Q1 10-Q) used to be dropped: both filings fell into one fiscal
year and the earlier one won. Rule: exactly two filings with the same (fy, fp), latest period ends 330-400 days apart ->
every observation of the later filing gets fy + 1, unless (fy + 1, fp) is already present. Anything else is unchanged.
"""
import copy

from src.fundamentals import sec_history as sh

EPS = "EarningsPerShareDiluted"


def ob(start, end, val, fy, fp, accn, filed, form="10-Q", unit="USD"):
    return {"start": start, "end": end, "val": val, "fy": fy, "fp": fp, "form": form, "filed": filed, "_unit": unit,
            "accn": accn}


A = "0001193125-25-200095"      # FY2026 Q1 10-Q (correct label)
B = "0001193125-26-389274"      # FY2027 Q1 10-Q (mislabelled fy=2026)
K = "0001193125-26-277521"      # FY2026 10-K


def orcl(b_fy=2026, b_end="2026-08-31", b_start="2026-06-01", b_prev_end="2025-08-31", b_prev_start="2025-06-01"):
    return [
        ob("2024-06-01", "2024-08-31", 13307, 2026, "Q1", A, "2025-09-10"),     # comparative
        ob("2025-06-01", "2025-08-31", 14926, 2026, "Q1", A, "2025-09-10"),
        ob("2025-06-01", "2026-05-31", 67357, 2026, "FY", K, "2026-06-22", form="10-K"),
        ob(b_prev_start, b_prev_end, 14926, b_fy, "Q1", B, "2026-09-11"),        # comparative of the later filing
        ob(b_start, b_end, 19345, b_fy, "Q1", B, "2026-09-11"),
    ]


def q1_ends(observations, concept_name=None):
    return [r["period_end"] for r in sh.reconstruct_standalone_quarters(observations, concept_name=concept_name)
            if r["quarter"] == "Q1"]


def relabelled(observations):
    return {o["accn"]: o["fy"] for o in sh._relabel_fiscal_year_collisions(observations) if o.get("form") == "10-Q"}


# ---------------------------------------------------------------- the ORCL case
def test_orcl_latest_quarter_is_recovered():
    out = {r["period_end"]: r for r in sh.reconstruct_standalone_quarters(orcl()) if r["quarter"] == "Q1"}
    assert sorted(out) == ["2025-08-31", "2026-08-31"]
    assert out["2026-08-31"]["value"] == 19345
    assert out["2025-08-31"]["value"] == 14926


def test_whole_later_filing_moves_including_its_comparative():
    out = sh._relabel_fiscal_year_collisions(orcl())
    assert [o["fy"] for o in out if o["accn"] == B] == [2027, 2027]
    assert [o["fy"] for o in out if o["accn"] == A] == [2026, 2026]
    assert [o["fy"] for o in out if o["accn"] == K] == [2026]                 # 10-K never relabelled


def test_eps_direct_path_recovers_the_quarter_too():
    obs = [dict(o, _unit="USD/shares", val=v) for o, v in zip(orcl(), (1.03, 1.01, 5.83, 1.01, 1.56))]
    assert q1_ends(obs, concept_name=EPS) == ["2025-08-31", "2026-08-31"]


# ---------------------------------------------------------------- no collision -> unchanged
def test_correct_labels_are_untouched():
    obs = orcl(b_fy=2027)
    assert sh._relabel_fiscal_year_collisions(obs) is obs
    assert q1_ends(obs) == ["2025-08-31", "2026-08-31"]


def test_single_filing_is_untouched():
    obs = orcl()[:3]
    assert sh._relabel_fiscal_year_collisions(obs) is obs


def test_three_filings_with_one_label_are_untouched():
    obs = orcl() + [ob("2027-06-01", "2027-08-31", 21000, 2026, "Q1", "0001193125-27-000001", "2027-09-10")]
    assert sh._relabel_fiscal_year_collisions(obs) is obs


def test_occupied_target_is_untouched():
    obs = orcl() + [ob("2026-09-01", "2026-11-30", 20000, 2027, "Q1", "0001193125-26-999999", "2026-12-10")]
    assert sh._relabel_fiscal_year_collisions(obs) is obs


# ---------------------------------------------------------------- gap boundaries (latest ends of the two filings)
def _gap(days):
    from datetime import date, timedelta
    end = (date(2025, 8, 31) + timedelta(days=days)).isoformat()
    start = (date.fromisoformat(end) - timedelta(days=91)).isoformat()
    return [ob("2025-06-01", "2025-08-31", 14926, 2026, "Q1", A, "2025-09-10"),
            ob(start, end, 19345, 2026, "Q1", B, "2026-09-11")]


def test_gap_boundaries_329_330_400_401():
    assert relabelled(_gap(329)) == {A: 2026, B: 2026}
    assert relabelled(_gap(330)) == {A: 2026, B: 2027}
    assert relabelled(_gap(400)) == {A: 2026, B: 2027}
    assert relabelled(_gap(401)) == {A: 2026, B: 2026}


# ---------------------------------------------------------------- fail-closed inputs
def test_missing_accn_or_end_makes_the_group_unusable():
    obs = orcl(); obs.append(ob("2026-06-01", "2026-08-31", 1, 2026, "Q1", None, "2026-09-11"))
    assert sh._relabel_fiscal_year_collisions(obs) is obs
    obs = orcl(); obs.append(ob("2026-06-01", "not-a-date", 1, 2026, "Q1", "0001193125-26-555555", "2026-09-11"))
    assert sh._relabel_fiscal_year_collisions(obs) is obs


def test_bool_or_missing_fy_is_ignored():
    obs = [dict(o, fy=True) if o["accn"] == B else o for o in orcl()]
    assert sh._relabel_fiscal_year_collisions(obs) is obs
    obs = [dict(o, fy=True) for o in orcl()]                      # a bool label is not a fiscal year, even when it "collides"
    assert sh._relabel_fiscal_year_collisions(obs) is obs
    obs = [dict(o, fy=None) if o["accn"] == B else o for o in orcl()]
    assert sh._relabel_fiscal_year_collisions(obs) is obs


def test_input_is_never_mutated_and_only_fy_changes():
    obs = orcl(); before = copy.deepcopy(obs)
    out = sh._relabel_fiscal_year_collisions(obs)
    assert obs == before
    for o, n in zip(before, out):
        assert {k: v for k, v in o.items() if k != "fy"} == {k: v for k, v in n.items() if k != "fy"}


def test_non_list_input_is_returned_unchanged():
    assert sh._relabel_fiscal_year_collisions(None) is None


def test_other_fp_labels_are_independent():
    # a correct Q2 pair next to the colliding Q1 pair: only the later Q1 filing moves
    q2a, q2b = "0001193125-25-315925", "0001193125-26-500000"
    obs = orcl() + [ob("2025-06-01", "2025-11-30", 30983, 2026, "Q2", q2a, "2025-12-11"),
                    ob("2026-06-01", "2026-11-30", 40000, 2027, "Q2", q2b, "2026-12-11")]
    assert relabelled(obs) == {A: 2026, B: 2027, q2a: 2026, q2b: 2027}


def test_10k_filings_are_never_relabelled():
    k2 = "0001193125-27-000002"
    obs = [ob("2024-06-01", "2025-05-31", 57399, 2026, "FY", K, "2025-06-18", form="10-K"),
           ob("2025-06-01", "2026-05-31", 67357, 2026, "FY", k2, "2026-06-22", form="10-K")]
    assert sh._relabel_fiscal_year_collisions(obs) is obs


def test_10q_amendments_neither_count_nor_move():
    # a 10-Q/A next to the colliding pair is not a third filing (only form "10-Q" is grouped) and is never relabelled
    amend = "0001193125-26-400000"
    obs = orcl() + [ob("2026-06-01", "2026-08-31", 19345, 2026, "Q1", amend, "2026-10-01", form="10-Q/A")]
    out = sh._relabel_fiscal_year_collisions(obs)
    assert {o["accn"]: o["fy"] for o in out} == {A: 2026, K: 2026, B: 2027, amend: 2026}


def test_relabelling_is_idempotent():
    once = sh._relabel_fiscal_year_collisions(orcl())
    twice = sh._relabel_fiscal_year_collisions(once)
    assert twice is once
