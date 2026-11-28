"""G6 EPS path: PE_TTM / PE_FY from directly reported diluted EPS (synthetic
CompanyFacts only, no network)."""

import json
from datetime import date
from decimal import Decimal as D

import pytest

from src.price.schema import CalcStatus, Interpretability, RawPriceStatus, Reason
from src.price.valuation import (
    EpsFact, SharesFact, SplitBasisEvidence, check_split_basis, compute_pe_eps,
    select_eps_fy, select_eps_ttm,
)
from src.price_providers.sec_eps import (
    EpsSourceError, load_eps_source, parse_companyfacts,
)

S = date(2026, 11, 27)                      # price session D
EV = SplitBasisEvidence(date(2025, 9, 23), S, ())
CLOSE = D("120")


def f(start, end, val, filed, form="10-Q", accn=None, unit="USD/shares",
      concept="EarningsPerShareDiluted"):
    return EpsFact(date.fromisoformat(start), date.fromisoformat(end), D(val),
                   date.fromisoformat(filed), accn or f"A-{end}-{filed}", form, unit, concept)


# calendar-year issuer, fiscal Q4 reported as a 3-month value in the 10-K
Q4_25 = f("2025-10-01", "2025-12-31", "1.10", "2026-02-10", "10-K")
FY_25 = f("2025-01-01", "2025-12-31", "4.00", "2026-02-10", "10-K")
Q1 = f("2026-01-01", "2026-03-31", "1.20", "2026-04-30")
H1 = f("2026-01-01", "2026-06-30", "2.50", "2026-07-30")
Q2 = f("2026-04-01", "2026-06-30", "1.30", "2026-07-30")
M9 = f("2026-01-01", "2026-09-30", "3.90", "2026-10-30")
Q3 = f("2026-07-01", "2026-09-30", "1.40", "2026-10-30")
Q1_25 = f("2025-01-01", "2025-03-31", "0.90", "2025-04-30")
Q2_25 = f("2025-04-01", "2025-06-30", "1.00", "2025-07-30")
Q3_25 = f("2025-07-01", "2025-09-30", "1.00", "2025-10-30")
BASE = [Q1_25, Q2_25, Q3_25, Q4_25, FY_25, Q1, H1, Q2, M9, Q3]


def pe(facts, close=CLOSE, ev=EV, shares=(), status=RawPriceStatus.OK, session=S):
    return compute_pe_eps(raw_close=close, raw_status=status, price_session=session,
                          facts=facts, split_evidence=ev, sec_shares=shares)


# ------------------------------------------------------------------ 1, 2, 18
def test_four_direct_quarters_including_direct_q4():
    out = pe(BASE)
    eps = out["EPS_TTM"]
    assert eps.calc_status is CalcStatus.OK and eps.value == D("5.00")
    assert eps.evidence["method"] == "DIRECT_4Q"
    assert [p["end"] for p in eps.evidence["periods"]] == \
        ["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
    assert all(p["accession"] and p["filed"] for p in eps.evidence["periods"])
    assert out["PE_TTM"].value == D(24) and Reason.EPS_ROUNDED_TO_CENTS in out["PE_TTM"].reasons
    assert out["EPS_FY"].value == D("4.00") and out["PE_FY"].value == D(30)
    assert out["PE_FY"].evidence["method"] == "FY_DIRECT"


# ------------------------------------------------------------------ 3, 4, 5
def test_fy_fallback_only_right_after_10k_with_exact_alignment():
    session = date(2026, 3, 2)                      # 10-K filed, no 10-Q yet
    facts = [Q1_25, Q2_25, Q3_25, FY_25]            # fiscal Q4 not reported
    out = pe(facts, session=session, ev=SplitBasisEvidence(date(2025, 1, 1), session, ()))
    assert out["EPS_TTM"].value == D("4.00")
    assert out["EPS_TTM"].evidence["method"] == "FY_DIRECT"
    assert Reason.EPS_TTM_FROM_FY_DIRECT in out["PE_TTM"].reasons


def test_fy_fallback_blocked_when_fy_misaligned_by_one_day():
    session = date(2026, 3, 2)
    fy = f("2025-01-02", "2025-12-31", "4.00", "2026-02-10", "10-K")
    r = select_eps_ttm([Q1_25, Q2_25, Q3_25, fy], session)
    assert r.status is CalcStatus.MISSING and r.reasons == (Reason.Q4_EPS_NOT_REPORTED,)


def test_fy_fallback_blocked_without_reported_quarters():
    r = select_eps_ttm([FY_25], date(2026, 3, 2))
    assert r.status is CalcStatus.MISSING and r.reasons == (Reason.Q4_EPS_NOT_REPORTED,)


def test_missing_q4_after_10q_is_not_available():
    facts = [x for x in BASE if x is not Q4_25]
    out = pe(facts)
    for m in ("EPS_TTM", "PE_TTM"):
        assert out[m].calc_status is CalcStatus.MISSING
        assert out[m].reasons == (Reason.Q4_EPS_NOT_REPORTED,)
    assert out["PE_FY"].calc_status is CalcStatus.OK          # FY metric independent


# ------------------------------------------------------------------ 6, 7
def test_no_reconstruction_from_cumulative_or_net_income():
    # H, 9M and FY values would allow FY-9M / 9M-6M style reconstruction of the
    # missing quarters; the result must still be NOT_AVAILABLE with no value.
    facts = [Q1_25, FY_25, H1, M9, f("2025-01-01", "2025-09-30", "2.90", "2025-10-30")]
    r = select_eps_ttm(facts, S)
    assert r.status is not CalcStatus.OK and r.value is None and r.method is None
    import src.price.valuation as v
    src = open(v.__file__, encoding="utf-8").read()
    eps_part = src.split("# EPS path (P4 G6")[1]
    for forbidden in ("NetIncome", "WeightedAverage", "Shares Outstanding Diluted"):
        assert forbidden not in eps_part


# ------------------------------------------------------------------ 8, 9
def test_gap_and_overlap_blocked():
    gap_q2 = f("2026-04-01", "2026-06-29", "1.30", "2026-07-30")
    r = select_eps_ttm([Q4_25, Q1, gap_q2, Q3, FY_25], S)
    assert r.status is CalcStatus.INVALID and r.reasons == (Reason.EPS_PERIOD_DISCONTINUITY,)
    dup = f("2026-03-31", "2026-06-30", "1.31", "2026-07-30", accn="B")
    r = select_eps_ttm([Q4_25, Q1, Q2, dup, Q3, FY_25], S)
    assert r.status is CalcStatus.INVALID and r.reasons == (Reason.EPS_PERIOD_OVERLAP,)


@pytest.mark.parametrize("start", ["2026-08-02", "2026-06-07"])   # 60 and 116 days
def test_quarter_length_outside_70_110_blocked(start):
    odd = f(start, "2026-09-30", "1.40", "2026-10-30")
    r = select_eps_ttm([Q4_25, Q1, Q2, odd], S)
    assert r.status is CalcStatus.INVALID and r.reasons == (Reason.EPS_PERIOD_LENGTH_INVALID,)


def test_three_month_and_cumulative_same_end_uses_three_month_only():
    r = select_eps_ttm(BASE, S)
    assert all(70 <= x.days <= 110 for x in r.facts)


# ------------------------------------------------------------------ 10, 11
def test_unit_and_concept_mismatch_blocked():
    r = select_eps_ttm(BASE + [f("2026-07-01", "2026-09-30", "1.4", "2026-10-30", unit="USD")], S)
    assert r.status is CalcStatus.INVALID and r.reasons == (Reason.EPS_UNIT_MISMATCH,)
    other = f("2026-07-01", "2026-09-30", "1.4", "2026-10-30",
              concept="IncomeLossFromContinuingOperationsPerDilutedShare")
    r = select_eps_fy(BASE + [other], S)
    assert r.status is CalcStatus.INVALID and r.reasons == (Reason.EPS_CONCEPT_MISMATCH,)


def test_loader_rejects_eps_reported_in_two_units():
    cf = companyfacts(BASE)
    cf["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD"] = [
        row(Q3)]
    with pytest.raises(EpsSourceError) as e:
        parse_companyfacts(cf)
    assert e.value.reason == Reason.EPS_UNIT_MISMATCH


# ------------------------------------------------------------------ 12, 13
def test_filed_on_or_after_session_excluded():
    late_q3 = f("2026-07-01", "2026-09-30", "1.40", "2026-11-27")      # same day as D
    r = select_eps_ttm([Q3_25, Q4_25, Q1, Q2, late_q3, FY_25], S)
    assert r.status is CalcStatus.OK
    assert [x.end for x in r.facts][-1] == date(2026, 6, 30)


def test_amendment_pit_selection_and_conflict():
    amended = f("2026-04-01", "2026-06-30", "1.35", "2026-08-15", "10-Q/A", accn="AMEND")
    future = f("2026-04-01", "2026-06-30", "9.99", "2026-12-01", "10-Q/A", accn="FUTURE")
    r = select_eps_ttm(BASE + [amended, future], S)
    q2 = [x for x in r.facts if x.end == date(2026, 6, 30)][0]
    assert q2.accession == "AMEND" and q2.value == D("1.35")
    clash = f("2026-04-01", "2026-06-30", "1.36", "2026-08-15", "10-Q/A", accn="CLASH")
    r = select_eps_ttm(BASE + [amended, clash], S)
    assert r.status is CalcStatus.INVALID and r.reasons == (Reason.EPS_VALUE_CONFLICT,)


def test_same_period_different_accession_same_value_is_fine():
    twin = f("2026-04-01", "2026-06-30", "1.30", "2026-07-30", accn="ZZZ")
    assert select_eps_ttm(BASE + [twin], S).status is CalcStatus.OK


# ------------------------------------------------------------------ 14, 15, 16
SPLIT = date(2026, 6, 15)


def test_split_with_restated_values_is_ok():
    # 10-Q for Q2 (after the split) restates Q4/Q1 on the new basis; latest filed wins
    restated = [f("2025-10-01", "2025-12-31", "0.55", "2026-07-30", accn="R1"),
                f("2026-01-01", "2026-03-31", "0.60", "2026-07-30", accn="R2"),
                f("2025-01-01", "2025-12-31", "2.00", "2026-07-30", "10-K/A", accn="R3")]
    q2 = f("2026-04-01", "2026-06-30", "0.65", "2026-07-30")
    q3 = f("2026-07-01", "2026-09-30", "0.70", "2026-10-30")
    ev = SplitBasisEvidence(date(2025, 9, 23), S, (SPLIT,))
    out = pe([Q4_25, FY_25, Q1, *restated, q2, q3], ev=ev)
    assert out["EPS_TTM"].value == D("2.50") and out["PE_TTM"].value == D(48)
    assert out["PE_FY"].value == D(60)


def test_split_with_unrestated_values_blocked_for_ttm_and_fy():
    ev = SplitBasisEvidence(date(2025, 9, 23), S, (SPLIT,))
    out = pe(BASE, ev=ev)                        # Q4/Q1/FY filed before the split
    for m in ("EPS_TTM", "PE_TTM", "EPS_FY", "PE_FY"):
        assert out[m].calc_status is CalcStatus.INVALID
        assert out[m].reasons == (Reason.EPS_SPLIT_BASIS_UNRESTATED,)


def test_split_basis_unverified_without_complete_evidence():
    for ev in (None, SplitBasisEvidence(None, None, ()),
               SplitBasisEvidence(date(2026, 3, 1), S, ()),        # starts after earliest filing
               SplitBasisEvidence(date(2025, 9, 23), date(2026, 11, 20), ())):
        out = pe(BASE, ev=ev)
        assert out["PE_TTM"].reasons == (Reason.EPS_SPLIT_BASIS_UNVERIFIED,)
        assert out["PE_FY"].calc_status is CalcStatus.INVALID


def test_sec_shares_jump_without_split_event_is_mismatch():
    shares = [SharesFact(date(2026, 1, 20), D(1000), date(2026, 2, 10)),
              SharesFact(date(2026, 7, 20), D(2000), date(2026, 7, 30))]
    out = pe(BASE, shares=shares)
    assert out["PE_TTM"].reasons == (Reason.EPS_SPLIT_BASIS_MISMATCH,)
    ok_shares = [shares[0], SharesFact(date(2026, 7, 20), D(1010), date(2026, 7, 30))]
    assert pe(BASE, shares=ok_shares)["PE_TTM"].calc_status is CalcStatus.OK
    out = pe(BASE, shares=())
    assert Reason.SEC_SPLIT_EVIDENCE_UNAVAILABLE in out["PE_TTM"].reasons


# ------------------------------------------------------------------ 17
def test_nonpositive_eps_not_meaningful_no_negative_pe():
    neg_q3 = f("2026-07-01", "2026-09-30", "-5.00", "2026-10-30")
    neg_fy = f("2025-01-01", "2025-12-31", "0", "2026-02-10", "10-K")
    out = pe([Q4_25, neg_fy, Q1, Q2, neg_q3])
    assert out["EPS_TTM"].calc_status is CalcStatus.OK and out["EPS_TTM"].value == D("-1.40")
    for m, reason in (("PE_TTM", Reason.EPS_NONPOSITIVE_TTM), ("PE_FY", Reason.EPS_NONPOSITIVE_FY)):
        assert out[m].calc_status is CalcStatus.OK and out[m].value is None
        assert out[m].interpretability is Interpretability.NOT_MEANINGFUL
        assert reason in out[m].reasons


def test_raw_price_not_ok_gives_missing_pe_but_keeps_eps():
    out = pe(BASE, status=RawPriceStatus.STALE)
    assert out["PE_TTM"].reasons == (Reason.RAW_PRICE_NOT_OK,)
    assert out["EPS_TTM"].calc_status is CalcStatus.OK


# ------------------------------------------------------------------ 19 (loader)
def row(x):
    return {"start": x.start.isoformat(), "end": x.end.isoformat(), "val": x.value,
            "accn": x.accession, "fy": 2026, "fp": "Q3", "form": x.form,
            "filed": x.filed.isoformat()}


def companyfacts(facts, shares=()):
    return {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {
        "USD/shares": [row(x) for x in facts]}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": list(shares)}}}}}


def write(tmp_path, ticker, cf, raw=False):
    p = tmp_path / f"{ticker}_companyfacts.json"
    p.write_text(cf if raw else json.dumps(cf, default=str), encoding="utf-8")
    return p


def test_loader_roundtrip(tmp_path):
    cf = companyfacts(BASE, shares=[{"end": "2026-07-20", "val": 1000, "filed": "2026-07-30",
                                     "form": "10-Q"}])
    txt = json.dumps(cf, default=float)
    write(tmp_path, "AAA", txt, raw=True)
    eps, shares = load_eps_source(tmp_path, "AAA")
    assert len(eps) == len(BASE) and isinstance(eps[0].value, D)
    assert shares == [SharesFact(date(2026, 7, 20), D(1000), date(2026, 7, 30))]
    assert pe(eps, shares=shares)["EPS_TTM"].value == D("5.00")


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "true", '"1.40"', "null"])
def test_loader_rejects_malformed_values(tmp_path, bad):
    txt = json.dumps(companyfacts(BASE), default=float).replace('"val": 1.4,', f'"val": {bad},', 1)
    assert f'"val": {bad},' in txt
    write(tmp_path, "AAA", txt, raw=True)
    with pytest.raises(EpsSourceError) as e:
        load_eps_source(tmp_path, "AAA")
    assert e.value.reason == Reason.EPS_SOURCE_MALFORMED


def test_loader_missing_file_and_bad_json(tmp_path):
    with pytest.raises(EpsSourceError) as e:
        load_eps_source(tmp_path, "NOPE")
    assert e.value.reason == Reason.EPS_SOURCE_MISSING
    write(tmp_path, "BAD", "{not json", raw=True)
    with pytest.raises(EpsSourceError) as e:
        load_eps_source(tmp_path, "BAD")
    assert e.value.reason == Reason.EPS_SOURCE_MALFORMED


def test_multiclass_cover_shares_ambiguous_dates_skipped():
    cf = companyfacts(BASE, shares=[
        {"end": "2026-07-20", "val": 100, "filed": "2026-07-30", "form": "10-Q"},
        {"end": "2026-07-20", "val": 900, "filed": "2026-07-30", "form": "10-Q"}])
    _, shares = parse_companyfacts(json.loads(json.dumps(cf, default=float), parse_float=D))
    assert shares == []


def test_split_check_ignores_events_before_earliest_filing():
    ev = SplitBasisEvidence(date(2024, 1, 1), S, (date(2025, 6, 1),))
    assert check_split_basis(select_eps_ttm(BASE, S).facts, S, ev, ())[0] is None


def test_fy_fallback_blocked_when_implied_q4_span_implausible():
    session = date(2026, 3, 2)
    qs = [f("2025-01-01", "2025-04-20", "1", "2025-05-10"),        # 110 days
          f("2025-04-21", "2025-08-08", "1", "2025-08-30"),        # 110 days
          f("2025-08-09", "2025-11-26", "1", "2025-12-10")]        # 110 days -> Q4 only 35 days
    r = select_eps_ttm(qs + [FY_25], session)
    assert r.status is CalcStatus.MISSING and r.reasons == (Reason.Q4_EPS_NOT_REPORTED,)
