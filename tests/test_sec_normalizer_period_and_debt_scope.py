"""
STEP 15 (2026-10-27): annual-duration check (A) and the partially
approved debt/capex tag policy (B) in sec_normalizer.normalize_company().

Synthetic fixtures only. Shapes mirror cases seen in real companyfacts
(GE, AVGO, NVDA, INTC, MU, ORCL) without hard-coding real values.
"""

from __future__ import annotations

from src.fundamentals.derived_metrics import calculate_derived_metrics
from src.fundamentals.sec_normalizer import (
    ANNUAL_MAX_DAYS,
    ANNUAL_MIN_DAYS,
    annual_duration_days,
    calculate_total_debt,
    normalize_company,
)

FY_END = "2025-12-31"
BS = "2026-06-30"


def _dur(val, start, end, form="10-K", fp="FY", filed="2026-02-01"):
    return {
        "start": start,
        "end": end,
        "val": val,
        "form": form,
        "fp": fp,
        "fy": int(end[:4]),
        "filed": filed,
        "accn": "0000000000-26-000001",
    }


def _inst(val, end, form="10-Q"):
    return {
        "end": end,
        "val": val,
        "form": form,
        "fp": "Q2",
        "fy": int(end[:4]),
        "filed": end[:8] + "28",
        "accn": "0000000000-26-000002",
    }


def _c(obs, unit="USD"):
    return {"units": {unit: list(obs)}}


def _base():
    full = ("2025-01-01", FY_END)
    return {
        "Revenues": _c([_dur(100.0, *full)]),
        "NetIncomeLoss": _c([_dur(10.0, *full)]),
        "OperatingIncomeLoss": _c([_dur(15.0, *full)]),
        "NetCashProvidedByUsedInOperatingActivities": _c([_dur(20.0, *full)]),
        "PaymentsToAcquirePropertyPlantAndEquipment": _c([_dur(5.0, *full)]),
        "Assets": _c([_inst(300.0, BS)]),
        "CashAndCashEquivalentsAtCarryingValue": _c([_inst(30.0, BS)]),
        "LongTermDebtCurrent": _c([_inst(2.0, BS)]),
        "LongTermDebtNoncurrent": _c([_inst(40.0, BS)]),
        "StockholdersEquity": _c([_inst(150.0, BS)]),
    }


def _norm(g):
    data = {"cik": 1, "entityName": "Synthetic", "facts": {"us-gaap": g, "dei": {}}}
    return normalize_company("SYN", data, "NON_FINANCIAL")


# ==================================================================
# A. Annual duration
# ==================================================================

def test_day_count_rule_and_range_constants():
    assert (ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS) == (350, 380)
    assert annual_duration_days({"start": "2025-01-01", "end": "2025-12-31"}) == 364
    assert annual_duration_days({"start": "2024-01-01", "end": "2024-12-31"}) == 365
    assert annual_duration_days({"start": "bad", "end": "2025-12-31"}) is None


def _revenue_with_days(days):
    from datetime import date, timedelta
    end = date(2025, 12, 31)
    start = end - timedelta(days=days)
    g = _base()
    g["Revenues"] = _c([_dur(100.0, start.isoformat(), end.isoformat())])
    return _norm(g)["metrics"]["revenue"]


def test_boundary_349_rejected():
    m = _revenue_with_days(349)
    assert m["status"] == "INCOMPLETE"
    assert m["reason_code"] == "ANNUAL_DURATION_OUT_OF_RANGE"
    assert m["duration_days"] == 349
    assert m["value"] is None
    assert m["rejected_observation"]["value"] == 100.0


def test_boundary_350_and_380_accepted():
    for days in (350, 380):
        m = _revenue_with_days(days)
        assert m["status"] == "OK", days
        assert m["value"] == 100.0


def test_boundary_381_rejected():
    m = _revenue_with_days(381)
    assert m["status"] == "INCOMPLETE"
    assert m["reason_code"] == "ANNUAL_DURATION_OUT_OF_RANGE"


def test_52_and_53_week_fiscal_years_accepted():
    for days in (363, 370):
        assert _revenue_with_days(days)["status"] == "OK", days


def test_leap_year_calendar_year_accepted():
    g = _base()
    g["Revenues"] = _c([_dur(100.0, "2024-01-01", "2024-12-31", filed="2025-02-01")])
    for name in ("NetIncomeLoss", "OperatingIncomeLoss",
                 "NetCashProvidedByUsedInOperatingActivities",
                 "PaymentsToAcquirePropertyPlantAndEquipment"):
        g[name] = _c([_dur(1.0, "2024-01-01", "2024-12-31", filed="2025-02-01")])
    m = _norm(g)["metrics"]["revenue"]
    assert m["status"] == "OK"
    assert m["period_start"] == "2024-01-01"


def test_short_q4_value_in_10k_does_not_replace_full_year_value():
    """Regression: the shared selector prefers a 3-month 10-K/FY value
    ending on the same date; the full-year view must select the annual
    value instead."""
    g = _base()
    g["Revenues"] = _c([
        _dur(100.0, "2025-01-01", FY_END),
        _dur(30.0, "2025-10-01", FY_END),
    ])
    m = _norm(g)["metrics"]["revenue"]
    assert m["status"] == "OK"
    assert m["value"] == 100.0
    assert m["period_start"] == "2025-01-01"


def test_only_short_value_is_incomplete_not_missing():
    g = _base()
    g["OperatingIncomeLoss"] = _c([_dur(30.0, "2025-10-01", FY_END)])
    m = _norm(g)["metrics"]["operating_income"]
    assert m["status"] == "INCOMPLETE"
    assert m["reason_code"] == "ANNUAL_DURATION_OUT_OF_RANGE"
    assert m["duration_days"] == 91


def test_unparseable_start_is_unverifiable():
    g = _base()
    g["OperatingIncomeLoss"] = _c([_dur(15.0, "not-a-date", FY_END)])
    m = _norm(g)["metrics"]["operating_income"]
    assert m["status"] != "OK"
    assert m["value"] is None


def test_observation_without_start_is_not_annual():
    g = _base()
    obs = _dur(15.0, "2025-01-01", FY_END)
    del obs["start"]
    g["OperatingIncomeLoss"] = _c([obs])
    m = _norm(g)["metrics"]["operating_income"]
    assert m["status"] == "MISSING"


def test_period_labels():
    m = _norm(_base())["metrics"]
    assert m["revenue"]["period_kind"] == "DURATION"
    assert m["revenue"]["period_class"] == "ANNUAL_FY"
    assert m["cash"]["period_kind"] == "INSTANT"
    assert "period_class" not in m["cash"]
    assert "period_type" not in m["revenue"]  # reserved by growth.py history records


# ==================================================================
# B. Debt / capex tag policy
# ==================================================================

def test_debtcurrent_outranks_shortterm_borrowings_and_is_not_summed():
    # GE shape: tiny ShortTermBorrowings plus aggregate DebtCurrent, same date.
    g = _base()
    del g["LongTermDebtCurrent"]
    del g["LongTermDebtNoncurrent"]
    g["ShortTermBorrowings"] = _c([_inst(0.02, BS)])
    g["DebtCurrent"] = _c([_inst(2.1, BS)])
    g["LongTermDebtAndCapitalLeaseObligations"] = _c([_inst(18.0, BS)])
    m = _norm(g)["metrics"]
    assert m["current_debt"]["concept"] == "DebtCurrent"
    assert m["current_debt"]["value"] == 2.1
    assert m["total_debt"]["status"] == "OK"
    assert m["total_debt"]["value"] == 2.1 + 18.0
    assert m["total_debt"]["lease_scope"] == "CAPITAL_FINANCE_LEASE_INCLUDED"


def test_mixed_scope_without_any_aligned_pair_is_incomplete():
    # INTC / LLY shape: DebtCurrent (finance leases included) and
    # LongTermDebtNoncurrent (excluded); no same-date LongTermDebtCurrent
    # and no lease-including noncurrent fact.
    g = _base()
    del g["LongTermDebtCurrent"]
    g["DebtCurrent"] = _c([_inst(2.0, BS)])
    m = _norm(g)["metrics"]
    assert m["current_debt"]["concept"] == "DebtCurrent"
    assert m["total_debt"]["status"] == "INCOMPLETE"
    assert m["total_debt"]["reason_code"] == "LEASE_SCOPE_MISMATCH"
    assert m["total_debt"]["value"] is None
    assert m["total_debt"]["unqualified_sum"] == 42.0


def test_equal_values_do_not_prove_absence_of_leases():
    # NVDA latest shape: DebtCurrent == LongTermDebtCurrent, noncurrent only
    # LongTermDebtNoncurrent. No inference from equality. The individual
    # current_debt field keeps DebtCurrent; total debt alone uses the
    # same-date lease-excluded pair and is labelled as excluding leases.
    g = _base()
    g["DebtCurrent"] = _c([_inst(2.0, BS)])
    g["LongTermDebt"] = _c([_inst(42.0, BS)])
    m = _norm(g)["metrics"]
    assert "lease_scope_evidence" not in m["current_debt"]
    assert m["current_debt"]["concept"] == "DebtCurrent"
    assert m["noncurrent_debt"]["concept"] == "LongTermDebtNoncurrent"
    td = m["total_debt"]
    assert td["status"] == "OK"
    assert td["value"] == 42.0
    assert td["lease_scope"] == "CAPITAL_FINANCE_LEASE_EXCLUDED"
    assert td["components"]["current_debt"]["concept"] == "LongTermDebtCurrent"
    align = td["scope_alignment"]
    assert align["original"]["current_debt"] == {
        "concept": "DebtCurrent", "lease_scope": "CAPITAL_FINANCE_LEASE_INCLUDED"}
    assert align["selected"]["current_debt"] == {
        "concept": "LongTermDebtCurrent", "lease_scope": "CAPITAL_FINANCE_LEASE_EXCLUDED"}
    assert align["period_end"] == BS
    assert align["unit"] == "USD"
    rec = {r["concept"]: r for r in td["reconciliation"]}
    assert rec["LongTermDebt"]["equals_total"] is True


def test_aligning_away_from_larger_aggregate_flags_uncovered_difference():
    # AVGO historical shape without a lease-including noncurrent fact:
    # DebtCurrent (incl. commercial paper) > LongTermDebtCurrent.
    g = _base()
    g["DebtCurrent"] = _c([_inst(5.6, BS)])
    m = _norm(g)["metrics"]
    assert m["current_debt"]["concept"] == "DebtCurrent"
    assert m["current_debt"]["value"] == 5.6
    td = m["total_debt"]
    assert td["components"]["current_debt"]["concept"] == "LongTermDebtCurrent"
    assert td["status"] == "INCOMPLETE"
    assert td["reason_code"] == "CURRENT_COMPONENT_NOT_COVERED"
    assert td["value"] is None


def test_same_date_lease_including_pair_is_preferred():
    # AVGO latest shape: DebtCurrent plus both LongTermDebtNoncurrent and
    # LongTermDebtAndCapitalLeaseObligations at the same date.
    g = _base()
    g["DebtCurrent"] = _c([_inst(2.0, BS)])
    g["LongTermDebtAndCapitalLeaseObligations"] = _c([_inst(40.0, BS)])
    g["LongTermDebt"] = _c([_inst(42.0, BS)])
    g["DebtLongtermAndShorttermCombinedAmount"] = _c([_inst(43.5, BS)])
    m = _norm(g)["metrics"]
    # individual fields keep the default selection
    assert m["current_debt"]["concept"] == "DebtCurrent"
    assert m["noncurrent_debt"]["concept"] == "LongTermDebtNoncurrent"
    td = m["total_debt"]
    assert td["status"] == "OK"
    assert td["value"] == 42.0
    assert td["lease_scope"] == "CAPITAL_FINANCE_LEASE_INCLUDED"
    assert td["components"]["noncurrent_debt"]["concept"] == "LongTermDebtAndCapitalLeaseObligations"
    assert td["components"]["noncurrent_debt"]["replaces_default"]["concept"] == "LongTermDebtNoncurrent"
    assert td["scope_alignment"]["selected"]["noncurrent_debt"]["concept"] == "LongTermDebtAndCapitalLeaseObligations"
    rec = {r["concept"]: r["equals_total"] for r in td["reconciliation"]}
    # recorded as evidence only, no tolerance; a non-matching aggregate
    # does not change the status
    assert rec == {"LongTermDebt": True, "DebtLongtermAndShorttermCombinedAmount": False}


def test_no_alignment_when_default_pair_is_consistent():
    m = _norm(_base())["metrics"]
    assert "scope_alignment" not in m["total_debt"]


def test_avgo_reconciled_aggregate_with_lease_including_noncurrent_is_ok():
    # AVGO 2025-02 shape: DebtCurrent == LongTermDebtCurrent + CommercialPaper
    # + FinanceLeaseLiabilityCurrent, paired with lease-including noncurrent.
    g = _base()
    del g["LongTermDebtNoncurrent"]
    g["LongTermDebtCurrent"] = _c([_inst(1.65, BS)])
    g["CommercialPaper"] = _c([_inst(3.992, BS)])
    g["FinanceLeaseLiabilityCurrent"] = _c([_inst(0.011, BS)])
    g["DebtCurrent"] = _c([_inst(5.653, BS)])
    g["LongTermDebtAndCapitalLeaseObligations"] = _c([_inst(60.9, BS)])
    m = _norm(g)["metrics"]
    assert m["current_debt"]["concept"] == "DebtCurrent"
    assert m["total_debt"]["status"] == "OK"
    assert m["total_debt"]["value"] == 5.653 + 60.9


def test_commercial_paper_not_covered_by_ltd_current_blocks_complete_total():
    # AAPL shape: LongTermDebtCurrent plus a same-date CommercialPaper fact.
    # DEC-038: a same-filing CommercialPaper fact is now summed (see
    # tests/test_sec_normalizer_current_component_sum.py). A fact from a
    # different filing still cannot complete total debt.
    g = _base()
    cp = _inst(2.0, BS)
    cp["accn"] = "0000000000-26-000099"
    g["CommercialPaper"] = _c([cp])
    m = _norm(g)["metrics"]
    assert m["current_debt"]["concept"] == "LongTermDebtCurrent"
    assert m["current_debt"]["uncovered_components"][0]["concept"] == "CommercialPaper"
    assert m["total_debt"]["status"] == "INCOMPLETE"
    assert m["total_debt"]["reason_code"] == "COMPONENT_SUM_FILING_MISMATCH"
    assert m["total_debt"]["unqualified_sum"] == 42.0


def test_short_term_borrowings_without_uncovered_facts_stays_ok():
    # NFLX shape: ShortTermBorrowings + LongTermDebtNoncurrent, no
    # LongTermDebtCurrent at the same date.
    g = _base()
    del g["LongTermDebtCurrent"]
    g["ShortTermBorrowings"] = _c([_inst(2.5, BS)])
    m = _norm(g)["metrics"]
    assert m["current_debt"]["concept"] == "ShortTermBorrowings"
    assert m["total_debt"]["status"] == "OK"
    assert m["total_debt"]["value"] == 42.5


def test_short_term_borrowings_does_not_cover_same_date_ltd_current():
    # If ShortTermBorrowings were selected while a same-date, non-zero
    # LongTermDebtCurrent exists, the uncovered component is recorded.
    from src.fundamentals.sec_normalizer import detect_uncovered_current_components
    g = _base()
    data = {"cik": 1, "entityName": "S", "facts": {"us-gaap": g, "dei": {}}}
    selected = {"metric": "current_debt", "status": "OK", "concept": "ShortTermBorrowings",
                "value": 2.5, "unit": "USD", "period_end": BS}
    out = detect_uncovered_current_components(selected, data)
    assert out["uncovered_components"][0]["concept"] == "LongTermDebtCurrent"
    total = calculate_total_debt(
        dict(out, lease_scope="CAPITAL_FINANCE_LEASE_EXCLUDED"),
        {"metric": "noncurrent_debt", "status": "OK", "value": 40.0, "unit": "USD",
         "lease_scope": "CAPITAL_FINANCE_LEASE_EXCLUDED"},
        "NON_FINANCIAL",
    )
    assert total["status"] == "INCOMPLETE"
    assert total["reason_code"] == "CURRENT_COMPONENT_NOT_COVERED"


def test_existing_issuer_without_debtcurrent_is_unchanged():
    m = _norm(_base())["metrics"]
    assert m["current_debt"]["concept"] == "LongTermDebtCurrent"
    assert m["total_debt"]["status"] == "OK"
    assert m["total_debt"]["value"] == 42.0
    assert m["total_debt"]["lease_scope"] == "CAPITAL_FINANCE_LEASE_EXCLUDED"


def test_notes_payable_current_is_partial_and_blocks_total_debt():
    g = _base()
    del g["LongTermDebtCurrent"]
    g["NotesPayableCurrent"] = _c([_inst(7.0, BS)])
    n = _norm(g)
    m = n["metrics"]
    assert m["current_debt"]["status"] == "OK"
    assert m["current_debt"]["concept_scope"] == "CURRENT_PORTION_OF_LONG_TERM_NOTES"
    assert m["current_debt"]["scope_coverage"] == "PARTIAL"
    assert m["total_debt"]["status"] == "INCOMPLETE"
    assert m["total_debt"]["reason_code"] == "PARTIAL_COMPONENT_SCOPE"
    assert m["total_debt"]["value"] is None
    flags = [f for f in n["scope_flags"] if f["metric"] == "current_debt"]
    assert flags and flags[0]["scoring_eligibility"] == "RESTRICTED"
    derived = calculate_derived_metrics(n)["derived_metrics"]
    assert derived["net_debt"]["status"] != "OK"


def test_fcf_carries_capex_scope_metadata_without_changing_value():
    g = _base()
    del g["PaymentsToAcquirePropertyPlantAndEquipment"]
    g["PaymentsToAcquireProductiveAssets"] = _c([_dur(6.0, "2025-01-01", FY_END)])
    derived = calculate_derived_metrics(_norm(g))["derived_metrics"]
    fcf = derived["fcf"]
    assert fcf["status"] == "OK"
    assert fcf["value"] == 20.0 - 6.0
    assert fcf["input_scope"]["capex"]["concept"] == "PaymentsToAcquireProductiveAssets"
    assert fcf["input_scope"]["capex"]["concept_scope"] == "PRODUCTIVE_ASSETS"
    assert fcf["peer_comparison_eligibility"] == "RESTRICTED_INPUT_SCOPE"
    assert derived["fcf_margin"]["peer_comparison_eligibility"] == "RESTRICTED_INPUT_SCOPE"
    assert derived["fcf_margin"]["input_scope"]["capex"]["concept_scope"] == "PRODUCTIVE_ASSETS"


def test_pp_and_e_fcf_has_no_scope_metadata_and_same_value():
    derived = calculate_derived_metrics(_norm(_base()))["derived_metrics"]
    assert derived["fcf"]["value"] == 20.0 - 5.0
    assert "input_scope" not in derived["fcf"]
    assert "peer_comparison_eligibility" not in derived["fcf"]


def test_productive_assets_capex_is_flagged_for_fcf():
    g = _base()
    del g["PaymentsToAcquirePropertyPlantAndEquipment"]
    g["PaymentsToAcquireProductiveAssets"] = _c([_dur(6.0, "2025-01-01", FY_END)])
    n = _norm(g)
    assert n["metrics"]["capex"]["concept_scope"] == "PRODUCTIVE_ASSETS"
    flag = next(f for f in n["scope_flags"] if f["metric"] == "capex")
    assert flag["scoring_eligibility"] == "RESTRICTED"
    assert "fcf" in flag["affected_derived_metrics"]


def test_pp_and_e_capex_has_no_scope_flag():
    assert _norm(_base())["scope_flags"] == []


def test_calculate_total_debt_unlabelled_components_backward_compatible():
    current = {"metric": "current_debt", "status": "OK", "value": 1.0, "unit": "USD"}
    noncurrent = {"metric": "noncurrent_debt", "status": "OK", "value": 2.0, "unit": "USD"}
    result = calculate_total_debt(current, noncurrent, "NON_FINANCIAL")
    assert result["status"] == "OK"
    assert result["value"] == 3.0
