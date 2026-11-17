"""STEP 24.6-N N-P1 integration tests (CI): the production orchestrator path for the EPS same-filing comparative.

These run the real run_fundamentals_pipeline() (gating -> history reconstruction -> annotation -> growth ->
data_sufficiency) on a synthetic ORCL-shaped companyfacts with a 10:1 split between two Q1 filings:

    Q1 FY2025 (own filing, filed 2024-09-15):  2024-06-01..2024-08-31  EPS 4.00   (pre-split)
    Q1 FY2026 (filed 2025-09-15):               2025-06-01..2025-08-31  EPS 0.50   (current)
                                   comparative 2024-06-01..2024-08-31  EPS 0.40   (restated, same accession)

Legacy would compute 0.50 / 4.00 - 1 = -87.5% (1 star); the same-filing policy computes 0.50 / 0.40 - 1 = +25%.
I1..I6 follow the GPT N-P1 approval; I7 (production guard) and I8 (PIT cutoff) are the design-v2 additions.
"""
from __future__ import annotations

import copy

import pytest

from src.fundamentals import orchestrator
from src.fundamentals import per_share_comparatives as psc
from src.fundamentals.orchestrator import run_fundamentals_pipeline

ORCL_CIK = "0001341439"
ACC_FY25_10K, ACC_FY26_10K = "0000950170-25-087926", "0001193125-26-277521"
ACC_Q1_FY25, ACC_Q1_FY26 = "0000950170-24-999001", "0000950170-25-999001"


def _ob(start, end, val, accn, filed, fy, fp, form):
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}


EPS_OWN_PREV = _ob("2024-06-01", "2024-08-31", 4.00, ACC_Q1_FY25, "2024-09-15", 2025, "Q1", "10-Q")
EPS_CURRENT = _ob("2025-06-01", "2025-08-31", 0.50, ACC_Q1_FY26, "2025-09-15", 2026, "Q1", "10-Q")
EPS_COMPARATIVE = _ob("2024-06-01", "2024-08-31", 0.40, ACC_Q1_FY26, "2025-09-15", 2026, "Q1", "10-Q")


def _fixture(eps_quarterly=(EPS_OWN_PREV, EPS_CURRENT, EPS_COMPARATIVE), extra_eps_units=None):
    revenue = [
        _ob("2024-06-01", "2025-05-31", 57399000000, ACC_FY25_10K, "2025-06-18", 2025, "FY", "10-K"),
        _ob("2025-06-01", "2026-05-31", 66894000000, ACC_FY26_10K, "2026-06-22", 2026, "FY", "10-K"),
        _ob("2024-06-01", "2024-08-31", 13300000000, ACC_Q1_FY25, "2024-09-15", 2025, "Q1", "10-Q"),
        _ob("2025-06-01", "2025-08-31", 14800000000, ACC_Q1_FY26, "2025-09-15", 2026, "Q1", "10-Q"),
    ]
    eps = [
        _ob("2024-06-01", "2025-05-31", 3.00, ACC_FY25_10K, "2025-06-18", 2025, "FY", "10-K"),
        _ob("2025-06-01", "2026-05-31", 0.35, ACC_FY26_10K, "2026-06-22", 2026, "FY", "10-K"),
    ] + [copy.deepcopy(o) for o in eps_quarterly]
    eps_units = {"USD/shares": eps}
    eps_units.update(copy.deepcopy(extra_eps_units or {}))
    return {"cik": 1341439, "entityName": "Oracle Corporation", "facts": {"us-gaap": {
        "LongTermNotesPayable": {"label": "Notes Payable, Noncurrent", "units": {"USD": [
            {"end": "2025-05-31", "val": 85297000000, "accn": ACC_FY25_10K, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-06-18"},
            {"end": "2026-05-31", "val": 122342000000, "accn": ACC_FY26_10K, "fy": 2026, "fp": "FY", "form": "10-K",
             "filed": "2026-06-22"}]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"label": "Revenue", "units": {"USD": revenue}},
        "EarningsPerShareDiluted": {"label": "EPS diluted", "units": eps_units},
    }}}


def _rows(available="2026-09-20"):
    return [{"ticker": "ORCL", "cik": ORCL_CIK, "company_type": "NON_FINANCIAL", "effective_from": available,
             "effective_to": None, "classification_available_date": available,
             "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE", "mapping_rationale": "STEP 24.6-N test fixture.",
             "reviewed_at_utc": "2026-11-14T00:00:00Z", "decision_ref": "STEP24-6N-TEST-FIXTURE"}]


def _run(companyfacts=None, evaluation_date="2026-09-28", rows=None):
    return run_fundamentals_pipeline(ticker="ORCL", cik=ORCL_CIK, evaluation_date=evaluation_date,
                                     company_type="NON_FINANCIAL", company_type_rows=rows or _rows(),
                                     raw_companyfacts=companyfacts if companyfacts is not None else _fixture())


def _eps(result):
    return result["stages"]["growth"]["growth"]["components"]["eps_yoy"]


# ---------------------------------------------------------------- I1: unique same-filing comparative selected
def test_i1_unique_same_filing_comparative_is_used():
    result = _run()
    assert result["stages"]["normalization"]["normalized"]["metrics"]["diluted_eps"]["status"] == "OK"
    c = _eps(result)
    assert c["status"] == "OK"
    assert c["value"] == pytest.approx(0.50 / 0.40 - 1)          # never the legacy 0.50 / 4.00 - 1
    assert c["previous_value_basis"] == psc.BASIS_FOUND
    assert (c["current_period_end"], c["previous_period_end"]) == ("2025-08-31", "2024-08-31")
    assert result["stages"]["data_sufficiency"]["sufficiency"]["eps_yoy"]["calculation_status"] == "OK"


# ---------------------------------------------------------------- I2: zero candidates -> fail-closed
def test_i2_no_comparative_is_fail_closed_without_fallback():
    result = _run(_fixture(eps_quarterly=(EPS_OWN_PREV, EPS_CURRENT)))
    c = _eps(result)
    assert (c["status"], c["value"], c["stars"], c["reason_code"], c["previous_value_basis"]) == (
        "INVALID", None, None, "SAME_FILING_COMPARATIVE_UNAVAILABLE", psc.BASIS_MISSING)
    assert c["reason"] == psc.REASONS[psc.MISSING]
    suff = result["stages"]["data_sufficiency"]["sufficiency"]["eps_yoy"]
    assert (suff["calculation_status"], suff["detail"]) == ("INVALID", psc.REASONS[psc.MISSING])
    assert "comparative" in result["stages"]["growth"]["growth"]["limitations"]["eps_yoy"]


# ---------------------------------------------------------------- I3: several candidates -> no arbitrary choice
@pytest.mark.parametrize("duplicate", [dict(EPS_COMPARATIVE), dict(EPS_COMPARATIVE, val=0.41)], ids=["equal", "different"])
def test_i3_multiple_candidates_are_never_chosen(duplicate):
    result = _run(_fixture(eps_quarterly=(EPS_OWN_PREV, EPS_CURRENT, EPS_COMPARATIVE, duplicate)))
    c = _eps(result)
    assert (c["status"], c["reason_code"], c["reason"], c["previous_value_basis"]) == (
        "INVALID", "SAME_FILING_COMPARATIVE_UNAVAILABLE", psc.REASONS[psc.AMBIGUOUS], psc.BASIS_MISSING)


# ---------------------------------------------------------------- I4: accession / period / concept / unit mismatches excluded
@pytest.mark.parametrize("bad", [
    dict(EPS_COMPARATIVE, accn="0000950170-25-999777"),                     # other filing (accession)
    dict(EPS_COMPARATIVE, start="2024-03-01", end="2024-08-31"),            # 6-month cumulative (same end)
    dict(EPS_COMPARATIVE, start="2024-06-02"),                              # period start mismatch
], ids=["accession", "cumulative", "start"])
def test_i4_mismatched_candidates_are_excluded(bad):
    result = _run(_fixture(eps_quarterly=(EPS_OWN_PREV, EPS_CURRENT, bad)))
    c = _eps(result)
    assert (c["status"], c["reason_code"], c["previous_value_basis"]) == (
        "INVALID", "SAME_FILING_COMPARATIVE_UNAVAILABLE", psc.BASIS_MISSING)


def test_i4_other_unit_and_other_concept_are_excluded():
    other_unit = _fixture(eps_quarterly=(EPS_OWN_PREV, EPS_CURRENT), extra_eps_units={"USD/share": [EPS_COMPARATIVE]})
    assert _eps(_run(other_unit))["reason_code"] == "SAME_FILING_COMPARATIVE_UNAVAILABLE"
    other_concept = _fixture(eps_quarterly=(EPS_OWN_PREV, EPS_CURRENT))
    other_concept["facts"]["us-gaap"]["EarningsPerShareBasic"] = {"label": "EPS basic",
                                                                  "units": {"USD/shares": [EPS_COMPARATIVE]}}
    assert _eps(_run(other_concept))["reason_code"] == "SAME_FILING_COMPARATIVE_UNAVAILABLE"


# ---------------------------------------------------------------- I5: comparative <= 0 keeps the existing non-positive policy
@pytest.mark.parametrize("value", [-0.10, 0])
def test_i5_nonpositive_comparative_uses_existing_prior_base_rule(value):
    result = _run(_fixture(eps_quarterly=(EPS_OWN_PREV, EPS_CURRENT, dict(EPS_COMPARATIVE, val=value))))
    c = _eps(result)
    assert (c["status"], c["reason_code"], c["previous_value_basis"]) == (
        "INVALID", "NONPOSITIVE_PRIOR_BASE", psc.BASIS_FOUND)
    assert result["stages"]["growth"]["growth"]["limitations"]["eps_yoy"] == (
        "EPS growth requires positive current and prior-year comparable-quarter values.")


# ---------------------------------------------------------------- I6: nothing outside eps_yoy moves
def test_i6_other_components_and_stages_unaffected():
    found = _run()
    failed = _run(_fixture(eps_quarterly=(EPS_OWN_PREV, EPS_CURRENT)))
    # the two inputs differ only by one comparative EPS observation: derived metrics and the normalized
    # (annual) metrics must be identical
    assert found["stages"]["derived_metrics"] == failed["stages"]["derived_metrics"]
    assert (found["stages"]["normalization"]["normalized"]["metrics"]
            == failed["stages"]["normalization"]["normalized"]["metrics"])
    comps_a = found["stages"]["growth"]["growth"]["components"]
    comps_b = failed["stages"]["growth"]["growth"]["components"]
    for m in ("revenue_yoy", "operating_income_yoy", "fcf_yoy", "revenue_cagr_3y"):
        assert comps_a[m] == comps_b[m], m
        assert "previous_value_basis" not in comps_a[m]
    assert comps_a["revenue_yoy"]["status"] == "OK"


# ---------------------------------------------------------------- I7: production guard (no silent legacy fallback)
def test_i7_guard_raises_when_the_policy_is_not_applied(monkeypatch):
    monkeypatch.setattr(orchestrator, "_with_same_filing_comparatives", lambda eps_slice, *a, **k: eps_slice)
    with pytest.raises(RuntimeError, match="STEP 24.6-N guard"):
        _run()


@pytest.mark.parametrize("basis", [psc.BASIS_MISSING, "latest_vintage"])
def test_i7_guard_rejects_a_contradictory_basis(monkeypatch, basis):
    real = orchestrator.calculate_growth

    def tampered(*a, **k):
        result = real(*a, **k)
        result["components"]["eps_yoy"]["previous_value_basis"] = basis      # OK result with a wrong basis
        return result

    monkeypatch.setattr(orchestrator, "calculate_growth", tampered)
    with pytest.raises(RuntimeError, match="STEP 24.6-N guard"):
        _run()


def test_i7_guard_rejects_missing_with_a_reason_code(monkeypatch):
    real = orchestrator.calculate_growth

    def tampered(*a, **k):
        result = real(*a, **k)
        component = result["components"]["eps_yoy"]
        assert component["status"] == "MISSING"
        component["reason_code"] = "SAME_FILING_COMPARATIVE_UNAVAILABLE"           # MISSING must carry no code
        return result

    monkeypatch.setattr(orchestrator, "calculate_growth", tampered)
    with pytest.raises(RuntimeError, match="STEP 24.6-N guard"):
        _run(_fixture(eps_quarterly=(EPS_CURRENT,)))


def test_i7_guard_accepts_missing_pair_without_provenance():
    result = _run(_fixture(eps_quarterly=(EPS_CURRENT,)))           # single quarter: no pair -> MISSING, no field
    c = _eps(result)
    assert c["status"] == "MISSING" and "previous_value_basis" not in c


# ---------------------------------------------------------------- I8: PIT cutoff
def test_i8_filing_after_cutoff_never_supplies_a_value():
    # evaluation 2025-09-14 -> cutoff 2025-09-13: the 2025-09-15 filing (current + its comparative) is not public yet
    result = _run(evaluation_date="2025-09-14", rows=_rows(available="2000-01-01"))
    c = _eps(result)
    assert c["status"] == "MISSING" and "previous_value_basis" not in c
    assert c.get("value") is None
