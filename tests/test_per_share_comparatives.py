"""STEP 24.6-N: per_share_comparatives (pure module; no project imports, runs locally).

Same-filing comparative = exactly one observation with the current record's accession and filed date, the same
unit, a direct ~3-month duration, start/end equal to the selected prior-year record, filed <= cutoff, finite value.
"""
from __future__ import annotations

import copy
import sys

import pytest

from src.fundamentals import per_share_comparatives as psc

CUR_ACCN, OLD_ACCN = "0001065280-26-000291", "0001065280-25-000406"


def obs(start, end, val, accn=CUR_ACCN, filed="2026-10-21", fy=2026, fp="Q3", form="10-Q"):
    return {"start": start, "end": end, "val": val, "accn": accn, "filed": filed, "fy": fy, "fp": fp, "form": form}


CUR = obs("2026-07-01", "2026-09-30", 0.87)
COMP = obs("2025-07-01", "2025-09-30", 0.59)                                   # restated, same filing
OWN_PREV = obs("2025-07-01", "2025-09-30", 5.87, accn=OLD_ACCN, filed="2025-10-22", fy=2025)


def concept(*observations, unit="USD/shares", extra_units=None):
    units = {unit: [copy.deepcopy(o) for o in observations]}
    units.update(extra_units or {})
    return {"label": "EPS diluted", "units": units}


def rec(o, quarter="Q3", fy=None, unit=None):
    # production records carry no reliance on "_unit": the selected unit is passed to annotate_records() explicitly
    cur = dict(o) if unit is None else dict(o, _unit=unit)
    return {"quarter": quarter, "fy": fy if fy is not None else o["fy"], "value": o["val"], "period_start": o["start"],
            "period_end": o["end"], "filed": o["filed"], "form": o["form"], "accession": None, "reconstructed": False,
            "source": {"current": cur, "previous": None}}


LATEST, PREV = rec(CUR), rec(OWN_PREV)


def resolve(concept_data, latest=LATEST, previous=PREV, cutoff="2026-11-12", unit="USD/shares"):
    annotated = psc.annotate_records([latest, previous], concept_data, cutoff, unit)
    return psc.match_comparative(annotated[0], previous), annotated


# ---------------------------------------------------------------- module boundary
def test_module_imports_nothing_from_the_project():
    import subprocess
    src = open(psc.__file__, encoding="utf-8").read()
    assert "import src" not in src and "from src" not in src
    code = ("import sys; import src.fundamentals.per_share_comparatives; "
            "print(sorted(m for m in sys.modules if m.startswith('src.')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "['src.fundamentals', 'src.fundamentals.per_share_comparatives']"


# ---------------------------------------------------------------- FOUND
def test_ten_for_one_split_uses_restated_comparative():
    (status, value, reason), _ = resolve(concept(CUR, COMP, OWN_PREV))
    assert (status, value, reason) == (psc.FOUND, 0.59, None)


def test_reverse_split_uses_restated_comparative():
    cur, comp = obs("2026-07-01", "2026-09-30", 12.0), obs("2025-07-01", "2025-09-30", 9.0)      # 1:10 reverse split
    own = obs("2025-07-01", "2025-09-30", 0.9, accn=OLD_ACCN, filed="2025-10-22", fy=2025)
    (status, value, _), _ = resolve(concept(cur, comp, own), latest=rec(cur), previous=rec(own))
    assert (status, value) == (psc.FOUND, 9.0)


def test_restatement_uses_the_current_filing_presentation():
    comp = obs("2025-07-01", "2025-09-30", 5.40)          # restated (not a split): current filing wins
    (status, value, _), _ = resolve(concept(CUR, comp, OWN_PREV))
    assert (status, value) == (psc.FOUND, 5.40)


def test_no_split_ordinary_company_and_equal_values():
    comp = obs("2025-07-01", "2025-09-30", 5.87)
    (status, value, _), _ = resolve(concept(CUR, comp, OWN_PREV))
    assert (status, value) == (psc.FOUND, 5.87)            # identical to the own-filing value: no change


def test_q4_direct_quarter_in_a_10k():
    cur = obs("2026-10-01", "2026-12-31", 1.1, fp="FY", form="10-K", filed="2027-01-25", fy=2026, accn="A-10K")
    comp = obs("2025-10-01", "2025-12-31", 0.9, fp="FY", form="10-K", filed="2027-01-25", fy=2026, accn="A-10K")
    own = obs("2025-10-01", "2025-12-31", 9.0, fp="FY", form="10-K", filed="2026-01-27", fy=2025, accn="B-10K")
    (status, value, _), _ = resolve(concept(cur, comp, own), latest=rec(cur, quarter="Q4"),
                                    previous=rec(own, quarter="Q4"), cutoff="2027-02-01")
    assert (status, value) == (psc.FOUND, 0.9)


# ---------------------------------------------------------------- MISSING / AMBIGUOUS (cardinality)
def test_missing_comparative():
    (status, value, reason), _ = resolve(concept(CUR, OWN_PREV))
    assert (status, value, reason) == (psc.MISSING, None, psc.REASONS[psc.MISSING])


def test_duplicate_equal_candidates_are_ambiguous():
    (status, value, reason), _ = resolve(concept(CUR, COMP, dict(COMP), OWN_PREV))
    assert (status, value, reason) == (psc.AMBIGUOUS, None, psc.REASONS[psc.AMBIGUOUS])


def test_duplicate_different_candidates_are_ambiguous():
    (status, value, _), _ = resolve(concept(CUR, COMP, dict(COMP, val=0.6), OWN_PREV))
    assert (status, value) == (psc.AMBIGUOUS, None)


@pytest.mark.parametrize("bad,label", [
    (dict(COMP, accn=OLD_ACCN), "other accession"),
    (dict(COMP, filed="2026-10-22"), "other filed date"),
    (dict(COMP, start="2025-01-01"), "cumulative 9-month period"),
    (dict(COMP, start="2025-07-02"), "start mismatch"),
    (dict(COMP, end="2025-09-29"), "end mismatch"),
    (dict(COMP, val=float("nan")), "non-finite value"),
    (dict(COMP, val="0.59"), "non-numeric value"),
    (dict(COMP, val=True), "bool value"),
])
def test_ineligible_candidates_are_excluded(bad, label):
    (status, value, _), _ = resolve(concept(CUR, bad, OWN_PREV))
    assert (status, value) == (psc.MISSING, None), label


def test_cumulative_period_is_never_a_candidate_even_if_it_matches_the_previous_record():
    # defence in depth: start/end equality alone would accept a 9-month value if the selected previous record were
    # itself cumulative; the direct-quarter duration rule rejects it
    cum = obs("2025-01-01", "2025-09-30", 4.4)
    prev_cum = rec(dict(OWN_PREV, start="2025-01-01"))
    (status, value, _), _ = resolve(concept(CUR, cum, OWN_PREV), previous=prev_cum)
    assert (status, value) == (psc.MISSING, None)


def test_mismatch_reason_when_the_period_end_exists_with_other_attributes():
    for bad in (dict(COMP, start="2025-01-01"), dict(COMP, filed="2026-10-22")):
        (status, _, reason), _ = resolve(concept(CUR, bad, OWN_PREV))
        assert (status, reason) == (psc.MISSING, psc.MISMATCH_REASON)


def test_wrong_unit_is_excluded():
    (status, value, reason), _ = resolve(concept(CUR, OWN_PREV, extra_units={"USD/share": [COMP]}))
    assert (status, value, reason) == (psc.MISSING, None, psc.MISMATCH_REASON)


def test_filed_after_cutoff_is_excluded():
    (status, value, _), _ = resolve(concept(CUR, COMP, OWN_PREV), cutoff="2026-10-20")
    assert (status, value) == (psc.MISSING, None)


def test_current_source_unavailable():
    latest = copy.deepcopy(LATEST); latest["source"]["current"].pop("accn")
    (status, value, reason), _ = resolve(concept(CUR, COMP, OWN_PREV), latest=latest)
    assert (status, value, reason) == (psc.CURRENT_SOURCE_UNAVAILABLE, None, psc.REASONS[psc.CURRENT_SOURCE_UNAVAILABLE])
    latest = copy.deepcopy(LATEST); latest.pop("source")
    assert resolve(concept(CUR, COMP), latest=latest)[0][0] == psc.CURRENT_SOURCE_UNAVAILABLE


def test_annotation_absent():
    status, value, reason = psc.match_comparative(dict(LATEST), PREV)
    assert (status, value, reason) == (psc.ANNOTATION_ABSENT, None, psc.REASONS[psc.ANNOTATION_ABSENT])


def test_missing_concept_data_annotates_without_candidates():
    for data in (None, {}, {"units": None}, {"units": {"USD/shares": None}}):
        (status, _, _), annotated = resolve(data)
        assert status == psc.MISSING and annotated[0][psc.ANNOTATION_KEY]["candidates"] == []


# ---------------------------------------------------------------- hygiene
def test_inputs_are_not_mutated_and_records_are_copies():
    data = concept(CUR, COMP, OWN_PREV); before_data, before_recs = copy.deepcopy(data), copy.deepcopy([LATEST, PREV])
    annotated = psc.annotate_records([LATEST, PREV], data, "2026-11-12", "USD/shares")
    assert data == before_data and [LATEST, PREV] == before_recs
    assert annotated[0] is not LATEST and psc.ANNOTATION_KEY not in LATEST


def test_current_period_itself_is_not_a_candidate():
    _, annotated = resolve(concept(CUR, COMP, OWN_PREV))
    ends = {c["end"] for c in annotated[0][psc.ANNOTATION_KEY]["candidates"]}
    assert ends == {"2025-09-30"}


def test_cutoff_accepts_date_objects():
    from datetime import date
    (status, value, _), _ = resolve(concept(CUR, COMP, OWN_PREV), cutoff=date(2026, 11, 12))
    assert (status, value) == (psc.FOUND, 0.59)


# ---------------------------------------------------------------- explicit unit (GPT N-P1 review P0-1)
def test_works_without_unit_tag_on_the_current_observation():
    assert "_unit" not in LATEST["source"]["current"]
    (status, value, _), _ = resolve(concept(CUR, COMP, OWN_PREV))
    assert (status, value) == (psc.FOUND, 0.59)


def test_matching_unit_tag_is_accepted_and_other_tag_fails_closed():
    (status, value, _), _ = resolve(concept(CUR, COMP, OWN_PREV), latest=rec(CUR, unit="USD/shares"))
    assert (status, value) == (psc.FOUND, 0.59)
    (status, value, reason), _ = resolve(concept(CUR, COMP, OWN_PREV), latest=rec(CUR, unit="USD/share"))
    assert (status, value, reason) == (psc.CURRENT_UNIT_MISMATCH, None, psc.REASONS[psc.CURRENT_UNIT_MISMATCH])


def test_candidates_come_from_the_explicit_unit_only():
    data = concept(CUR, OWN_PREV, extra_units={"USD/share": [COMP]})
    (status, value, _), _ = resolve(data, unit="USD/share")             # comparative lives in the selected unit
    assert (status, value) == (psc.FOUND, 0.59)
    (status, value, _), _ = resolve(data, unit="USD/shares")
    assert (status, value) == (psc.MISSING, None)


@pytest.mark.parametrize("unit", [None, ""])
def test_no_selected_unit_fails_closed(unit):
    (status, value, _), _ = resolve(concept(CUR, COMP, OWN_PREV), unit=unit)
    assert (status, value) == (psc.CURRENT_SOURCE_UNAVAILABLE, None)


# ---------------------------------------------------------------- guard rule (GPT N-P1 review P0-3)
F, M = psc.BASIS_FOUND, psc.BASIS_MISSING


@pytest.mark.parametrize("component", [
    {"status": "OK", "value": 0.4, "previous_value_basis": F},
    {"status": "INVALID", "reason_code": "SAME_FILING_COMPARATIVE_UNAVAILABLE", "previous_value_basis": M},
    {"status": "INVALID", "reason_code": "NONPOSITIVE_PRIOR_BASE", "previous_value_basis": F},
    {"status": "INVALID", "reason_code": "NONPOSITIVE_CURRENT", "previous_value_basis": F},
    {"status": "INVALID", "reason_code": "NON_NUMERIC_INPUT", "previous_value_basis": F},
    {"status": "INVALID", "reason_code": "NON_FINITE_INPUT", "previous_value_basis": F},
    {"status": "INVALID", "reason_code": "NON_FINITE_RESULT", "previous_value_basis": F},
    {"status": "MISSING", "value": None},
    {"status": "MISSING", "value": None, "reason_code": None},             # v3: reason_code None, no basis key
])
def test_guard_accepts_the_consistent_combinations(component):
    assert psc.basis_consistency_problem(component) is None


@pytest.mark.parametrize("component", [
    None,
    {"status": "OK", "value": 0.4},                                                         # policy not applied
    {"status": "OK", "value": 0.4, "previous_value_basis": M},
    {"status": "OK", "value": 0.4, "previous_value_basis": F, "reason_code": "NONPOSITIVE_PRIOR_BASE"},
    {"status": "INVALID", "reason_code": "SAME_FILING_COMPARATIVE_UNAVAILABLE", "previous_value_basis": F},
    {"status": "INVALID", "reason_code": "SAME_FILING_COMPARATIVE_UNAVAILABLE"},
    {"status": "INVALID", "reason_code": "NONPOSITIVE_PRIOR_BASE", "previous_value_basis": M},
    {"status": "INVALID", "reason_code": "NONPOSITIVE_PRIOR_BASE"},
    {"status": "INVALID", "reason_code": "ZERO_PRIOR_BASE", "previous_value_basis": F},
    {"status": "INVALID", "reason_code": None, "previous_value_basis": F},
    {"status": "MISSING", "previous_value_basis": F},
    {"status": "MISSING", "previous_value_basis": None},
    {"status": "WITHHELD", "previous_value_basis": F},
    {"status": "OK", "value": 0.4, "previous_value_basis": "latest_vintage"},
])
def test_guard_rejects_contradictions(component):
    assert isinstance(psc.basis_consistency_problem(component), str)


# ---------------------------------------------------------------- v3: MISSING must carry no reason_code (GPT 2nd review)
@pytest.mark.parametrize("code", ["SAME_FILING_COMPARATIVE_UNAVAILABLE", "NONPOSITIVE_PRIOR_BASE", "NON_FINITE_INPUT", "X", ""])
def test_guard_rejects_missing_with_any_reason_code(code):
    assert isinstance(psc.basis_consistency_problem({"status": "MISSING", "reason_code": code}), str)


def test_guard_rejects_missing_with_reason_code_and_basis():
    for basis in (psc.BASIS_FOUND, psc.BASIS_MISSING, None):
        c = {"status": "MISSING", "reason_code": "SAME_FILING_COMPARATIVE_UNAVAILABLE", "previous_value_basis": basis}
        assert isinstance(psc.basis_consistency_problem(c), str)


def test_guard_accepts_missing_with_reason_code_none_or_absent_and_no_basis():
    assert psc.basis_consistency_problem({"status": "MISSING", "reason_code": None}) is None
    assert psc.basis_consistency_problem({"status": "MISSING"}) is None
