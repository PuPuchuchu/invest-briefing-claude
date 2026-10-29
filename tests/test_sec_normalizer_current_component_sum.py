"""
DEC-038 (2026-10-29): same-filing current-component sum for total debt.

When current_debt is LongTermDebtCurrent (current maturities only) and the
same filing reports exactly one additional short-term borrowing component
(e.g. CommercialPaper), total debt may be computed as the sum of the three,
inside the total-debt calculation only. Every other case keeps the
conservative INCOMPLETE result. Synthetic fixtures; the AAPL case uses the
values of AAPL's 10-Q for the quarter ended 2026-06-27 (accession
0000320193-26-000020): commercial paper 1,997, term debt current 11,007,
term debt non-current 71,340 (USD millions).
"""

from __future__ import annotations

from src.fundamentals.sec_normalizer import (
    calculate_total_debt,
    normalize_company,
)

FY_END = "2025-09-27"
BS = "2026-06-27"
ACCN = "0000320193-26-000020"
M = 1_000_000


def _dur(val, start, end):
    return {"start": start, "end": end, "val": val, "form": "10-K", "fp": "FY",
            "fy": int(end[:4]), "filed": "2025-10-31", "accn": "0000320193-25-000079"}


def _inst(val, end=BS, accn=ACCN, filed="2026-07-31"):
    return {"end": end, "val": val, "form": "10-Q", "fp": "Q3", "fy": 2026,
            "filed": filed, "accn": accn}


def _c(*obs, unit="USD"):
    return {"units": {unit: list(obs)}}


def _aapl():
    full = ("2024-09-29", FY_END)
    return {
        "Revenues": _c(_dur(400_000 * M, *full)),
        "NetIncomeLoss": _c(_dur(100_000 * M, *full)),
        "NetCashProvidedByUsedInOperatingActivities": _c(_dur(110_000 * M, *full)),
        "PaymentsToAcquirePropertyPlantAndEquipment": _c(_dur(10_000 * M, *full)),
        "Assets": _c(_inst(331_000 * M)),
        "LongTermDebtCurrent": _c(_inst(11_007 * M)),
        "CommercialPaper": _c(_inst(1_997 * M)),
        "LongTermDebtNoncurrent": _c(_inst(71_340 * M)),
    }


def _norm(g):
    data = {"cik": 320193, "entityName": "Synthetic AAPL", "facts": {"us-gaap": g, "dei": {}}}
    return normalize_company("AAPL", data, "NON_FINANCIAL")


# ---------------------------------------------------------------- positive

def test_aapl_commercial_paper_is_summed_into_total_debt():
    m = _norm(_aapl())["metrics"]
    td = m["total_debt"]
    assert td["status"] == "OK"
    assert td["value"] == 84_344 * M
    assert td["unit"] == "USD"
    assert td["lease_scope"] == "CAPITAL_FINANCE_LEASE_EXCLUDED"
    cs = td["component_sum"]
    assert cs["applied"] is True
    assert cs["basis"] == "SAME_FILING_CURRENT_COMPONENT_SUM"
    assert cs["resolved_reason_code"] == "CURRENT_COMPONENT_NOT_COVERED"
    assert [(c["role"], c["concept"], c["value"]) for c in cs["components"]] == [
        ("current_debt", "LongTermDebtCurrent", 11_007 * M),
        ("short_term_component", "CommercialPaper", 1_997 * M),
        ("noncurrent_debt", "LongTermDebtNoncurrent", 71_340 * M),
    ]
    assert all(c["accession"] == ACCN and c["period_end"] == BS and c["unit"] == "USD"
               for c in cs["components"])
    assert "reason_code" not in td and "unqualified_sum" not in td


def test_individual_current_debt_keeps_value_and_is_labelled():
    m = _norm(_aapl())["metrics"]
    cd = m["current_debt"]
    assert cd["concept"] == "LongTermDebtCurrent"
    assert cd["value"] == 11_007 * M
    assert cd["component_scope"] == "CURRENT_MATURITIES_ONLY"
    assert cd["uncovered_components"][0]["concept"] == "CommercialPaper"
    assert cd.get("scope_coverage") != "PARTIAL"
    assert m["noncurrent_debt"]["value"] == 71_340 * M


def test_matching_total_debt_aggregate_verifies_the_sum():
    g = _aapl()
    g["DebtLongtermAndShorttermCombinedAmount"] = _c(_inst(84_344 * M))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "OK"
    assert td["component_sum"]["verification"] == [{
        "concept": "DebtLongtermAndShorttermCombinedAmount", "value": 84_344 * M,
        "unit": "USD", "accession": ACCN, "equals_sum": True}]
    assert td["component_sum"]["verification_status"] == "VERIFIED"


def test_aggregate_from_another_filing_is_evidence_only():
    # A same-date aggregate from a different accession neither verifies nor
    # fails the same-filing sum, even when its value differs.
    g = _aapl()
    g["DebtLongtermAndShorttermCombinedAmount"] = _c(
        _inst(99_999 * M, accn="0000320193-26-000055", filed="2026-07-01"))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "OK"
    assert td["value"] == 84_344 * M
    cs = td["component_sum"]
    assert cs["verification"] == []
    assert cs["verification_status"] == "NO_SAME_FILING_AGGREGATE"
    assert cs["aggregates_other_filings_evidence_only"][0]["accession"] == "0000320193-26-000055"


def test_lease_including_aggregate_is_never_used_for_the_lease_excluded_sum():
    g = _aapl()
    g["DebtAndCapitalLeaseObligations"] = _c(_inst(99_999 * M))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "OK"
    assert td["component_sum"]["verification"] == []


def test_long_term_subtotal_is_evidence_only_and_does_not_fail_the_sum():
    # AAPL also tags LongTermDebt (82,300): a long-term subtotal, not total debt.
    g = _aapl()
    g["LongTermDebt"] = _c(_inst(82_300 * M))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "OK"
    assert td["value"] == 84_344 * M
    assert td["component_sum"]["verification"] == []
    rec = {r["concept"]: r for r in td["reconciliation"]}
    assert rec["LongTermDebt"]["equals_total"] is False


# ---------------------------------------------------------------- negative

def test_mismatching_total_debt_aggregate_keeps_incomplete():
    g = _aapl()
    g["DebtLongtermAndShorttermCombinedAmount"] = _c(_inst(82_347 * M))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "INCOMPLETE"
    assert td["value"] is None
    assert td["reason_code"] == "COMPONENT_SUM_RECONCILIATION_FAILED"
    att = td["component_sum_attempt"]
    assert att["applied"] is False and att["computed_sum"] == 84_344 * M
    assert att["verification"][0]["equals_sum"] is False


def test_explicit_zero_commercial_paper_is_ignored_total_unchanged():
    g = _aapl()
    g["CommercialPaper"] = _c(_inst(0))
    m = _norm(g)["metrics"]
    assert m["total_debt"]["status"] == "OK"
    assert m["total_debt"]["value"] == (11_007 + 71_340) * M
    assert "component_sum" not in m["total_debt"]
    # The field still holds current maturities only, so the label is kept.
    assert m["current_debt"]["component_scope"] == "CURRENT_MATURITIES_ONLY"


def test_missing_commercial_paper_is_not_treated_as_zero_or_summed():
    # Scope of this test: with no same-date short-term fact at all, the
    # existing rule applies (an OK total means no known omission detected,
    # DEC-024); it does not prove that no commercial paper exists, and the
    # component-sum rule adds nothing (no zero substitution).
    g = _aapl()
    del g["CommercialPaper"]
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "OK"
    assert td["value"] == (11_007 + 71_340) * M
    assert "component_sum" not in td


def test_commercial_paper_from_a_different_filing_is_not_summed():
    g = _aapl()
    g["CommercialPaper"] = _c(_inst(1_997 * M, accn="0000320193-26-000099"))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "INCOMPLETE"
    assert td["reason_code"] == "COMPONENT_SUM_FILING_MISMATCH"
    assert td["unqualified_sum"] == (11_007 + 71_340) * M


def test_commercial_paper_in_a_different_unit_is_not_summed_or_ignored():
    g = _aapl()
    g["CommercialPaper"] = _c(_inst(1_997 * M), unit="EUR")
    m = _norm(g)["metrics"]
    # Detected (never silently dropped), not converted, and total debt is
    # not reported as complete.
    assert m["current_debt"]["uncovered_components"][0]["unit"] == "EUR"
    td = m["total_debt"]
    assert td["status"] == "INCOMPLETE"
    assert td["value"] is None
    assert td["reason_code"] == "COMPONENT_SUM_UNIT_MISMATCH"


def test_commercial_paper_at_a_different_date_is_not_summed():
    g = _aapl()
    g["CommercialPaper"] = _c(_inst(1_997 * M, end="2026-03-28"))
    m = _norm(g)["metrics"]
    assert "uncovered_components" not in m["current_debt"]
    assert m["total_debt"]["value"] == (11_007 + 71_340) * M
    assert "component_sum" not in m["total_debt"]


def test_noncurrent_from_a_different_filing_blocks_the_sum():
    g = _aapl()
    g["LongTermDebtNoncurrent"] = _c(_inst(71_340 * M, accn="0000320193-26-000077"))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "INCOMPLETE"
    assert td["reason_code"] == "COMPONENT_SUM_FILING_MISMATCH"


def test_ambiguous_short_term_components_are_not_summed():
    # AMD-shaped overlap: ShortTermBorrowings may already contain the
    # current maturities; with two unequal short-term facts nothing is added.
    g = _aapl()
    g["ShortTermBorrowings"] = _c(_inst(13_004 * M))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "INCOMPLETE"
    assert td["reason_code"] == "AMBIGUOUS_SHORT_TERM_COMPONENTS"
    concepts = {c["concept"] for c in td["component_sum_attempt"]["short_term_components"]}
    assert concepts == {"CommercialPaper", "ShortTermBorrowings"}


def test_equal_values_of_different_short_term_concepts_are_not_collapsed():
    # Equal amounts are not evidence of the same obligation.
    g = _aapl()
    g["ShortTermBorrowings"] = _c(_inst(1_997 * M))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "INCOMPLETE"
    assert td["value"] is None
    assert td["reason_code"] == "AMBIGUOUS_SHORT_TERM_COMPONENTS"


def test_non_commercial_paper_component_alone_is_not_summed():
    # DEC-038 scope: only CommercialPaper may be added.
    g = _aapl()
    del g["CommercialPaper"]
    g["ShortTermBorrowings"] = _c(_inst(500 * M))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "INCOMPLETE"
    assert td["reason_code"] == "SHORT_TERM_COMPONENT_NOT_APPROVED"


def test_other_short_term_borrowings_is_detected_and_blocks_completeness():
    # Previously not in the detection list, so total debt was OK without it.
    g = _aapl()
    del g["CommercialPaper"]
    g["OtherShortTermBorrowings"] = _c(_inst(300 * M))
    m = _norm(g)["metrics"]
    assert m["current_debt"]["uncovered_components"][0]["concept"] == "OtherShortTermBorrowings"
    assert m["total_debt"]["status"] == "INCOMPLETE"
    assert m["total_debt"]["reason_code"] == "SHORT_TERM_COMPONENT_NOT_APPROVED"


def test_amd_2023_overlap_shape_is_not_summed():
    # AMD 2023 shape: ShortTermBorrowings equals current debt including the
    # current portion; the combined aggregate exposes the double count.
    g = _aapl()
    del g["CommercialPaper"]
    g["LongTermDebtCurrent"] = _c(_inst(750 * M))
    g["ShortTermBorrowings"] = _c(_inst(753 * M))
    g["LongTermDebtNoncurrent"] = _c(_inst(1_714 * M))
    g["DebtLongtermAndShorttermCombinedAmount"] = _c(_inst(2_467 * M))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "INCOMPLETE"
    # Blocked before any sum: ShortTermBorrowings is not a summable component.
    assert td["reason_code"] == "SHORT_TERM_COMPONENT_NOT_APPROVED"


def test_debtcurrent_aggregate_prevents_double_count():
    g = _aapl()
    g["DebtCurrent"] = _c(_inst(13_004 * M))
    m = _norm(g)["metrics"]
    assert m["current_debt"]["concept"] == "DebtCurrent"
    td = m["total_debt"]
    assert "component_sum" not in td
    assert td["value"] in (None, (13_004 + 71_340) * M)


def test_aggregate_current_debt_present_guard_in_rule():
    # Direct call: a LongTermDebtCurrent selection with a same-date DebtCurrent
    # fact (e.g. DebtCurrent replaced by scope alignment) is not summed.
    from src.fundamentals.sec_normalizer import apply_current_component_sum
    g = _aapl()
    g["DebtCurrent"] = _c(_inst(13_004 * M))
    data = {"cik": 1, "entityName": "S", "facts": {"us-gaap": g, "dei": {}}}
    cur = {"metric": "current_debt", "status": "OK", "concept": "LongTermDebtCurrent",
           "value": 11_007 * M, "unit": "USD", "period_end": BS, "accession": ACCN,
           "lease_scope": "CAPITAL_FINANCE_LEASE_EXCLUDED",
           "uncovered_components": [{"concept": "CommercialPaper"}]}
    non = {"metric": "noncurrent_debt", "status": "OK", "concept": "LongTermDebtNoncurrent",
           "value": 71_340 * M, "unit": "USD", "period_end": BS, "accession": ACCN,
           "lease_scope": "CAPITAL_FINANCE_LEASE_EXCLUDED"}
    total = calculate_total_debt(cur, non, "NON_FINANCIAL")
    out = apply_current_component_sum(total, cur, non, data)
    assert out["status"] == "INCOMPLETE"
    assert out["reason_code"] == "CURRENT_COMPONENT_NOT_COVERED"
    assert out["component_sum_attempt"]["reason_code"] == "AGGREGATE_CURRENT_DEBT_PRESENT"
    assert out["component_sum_attempt"]["applied"] is False


def test_lease_scope_mismatch_takes_precedence_and_is_unchanged():
    g = _aapl()
    del g["LongTermDebtNoncurrent"]
    g["LongTermDebtAndCapitalLeaseObligations"] = _c(_inst(71_500 * M))
    td = _norm(g)["metrics"]["total_debt"]
    assert td["status"] == "INCOMPLETE"
    assert td["reason_code"] == "LEASE_SCOPE_MISMATCH"
    assert "component_sum" not in td and "component_sum_attempt" not in td


def test_rule_does_not_apply_to_other_reason_codes():
    from src.fundamentals.sec_normalizer import apply_current_component_sum
    total = {"metric": "total_debt", "status": "INCOMPLETE", "value": None,
             "reason_code": "PARTIAL_COMPONENT_SCOPE"}
    assert apply_current_component_sum(total, {}, {}, {"facts": {"us-gaap": {}}}) is total


def test_financial_company_total_debt_is_not_affected():
    from src.fundamentals.sec_normalizer import apply_current_component_sum
    total = calculate_total_debt({"status": "OK"}, {"status": "OK"}, "FINANCIAL")
    assert apply_current_component_sum(total, {}, {}, {"facts": {"us-gaap": {}}}) is total
    assert total["status"] == "NOT_APPLICABLE"


# ------------------------------------------------- unit-mismatch guards (r3)

def test_zero_in_selected_unit_does_not_hide_non_zero_other_unit_cp():
    # CP reported as 0 in USD and non-zero in EUR on the same date: the EUR
    # fact is detected, nothing is converted, total debt is not OK.
    g = _aapl()
    g["CommercialPaper"] = {"units": {"USD": [_inst(0)], "EUR": [_inst(1_800 * M)]}}
    m = _norm(g)["metrics"]
    unc = m["current_debt"]["uncovered_components"]
    assert [(u["concept"], u["unit"], u["value"]) for u in unc] == [
        ("CommercialPaper", "EUR", 1_800 * M)]
    td = m["total_debt"]
    assert td["status"] == "INCOMPLETE"
    assert td["value"] is None
    assert td["reason_code"] == "COMPONENT_SUM_UNIT_MISMATCH"


def test_zero_in_every_unit_is_still_ignored():
    g = _aapl()
    g["CommercialPaper"] = {"units": {"USD": [_inst(0)], "EUR": [_inst(0)]}}
    m = _norm(g)["metrics"]
    assert "uncovered_components" not in m["current_debt"]
    assert m["total_debt"]["status"] == "OK"
    assert m["total_debt"]["value"] == (11_007 + 71_340) * M


def test_debtcurrent_in_another_unit_blocks_the_sum_directly():
    # Direct call (the rule's own guard, independent of concept selection).
    from src.fundamentals.sec_normalizer import apply_current_component_sum
    g = _aapl()
    g["DebtCurrent"] = _c(_inst(13_004 * M), unit="EUR")
    data = {"cik": 1, "entityName": "S", "facts": {"us-gaap": g, "dei": {}}}
    cur = {"metric": "current_debt", "status": "OK", "concept": "LongTermDebtCurrent",
           "value": 11_007 * M, "unit": "USD", "period_end": BS, "accession": ACCN,
           "lease_scope": "CAPITAL_FINANCE_LEASE_EXCLUDED",
           "uncovered_components": [{"concept": "CommercialPaper"}]}
    non = {"metric": "noncurrent_debt", "status": "OK", "concept": "LongTermDebtNoncurrent",
           "value": 71_340 * M, "unit": "USD", "period_end": BS, "accession": ACCN,
           "lease_scope": "CAPITAL_FINANCE_LEASE_EXCLUDED"}
    total = calculate_total_debt(cur, non, "NON_FINANCIAL")
    out = apply_current_component_sum(total, cur, non, data)
    assert out["status"] == "INCOMPLETE"
    assert out["value"] is None
    assert out["reason_code"] == "CURRENT_COMPONENT_NOT_COVERED"
    att = out["component_sum_attempt"]
    assert att["applied"] is False
    assert att["reason_code"] == "AGGREGATE_CURRENT_DEBT_OTHER_UNIT"
    assert att["debt_current_units"] == ["EUR"]


def test_debtcurrent_in_another_unit_never_yields_an_ok_component_sum():
    # End to end through normalize_company(): whatever concept selection
    # does with a mixed-unit DebtCurrent, total debt is never an OK
    # component sum that ignores it.
    g = _aapl()
    g["DebtCurrent"] = _c(_inst(13_004 * M), unit="EUR")
    td = _norm(g)["metrics"]["total_debt"]
    assert not (td["status"] == "OK" and td.get("component_sum", {}).get("applied"))
