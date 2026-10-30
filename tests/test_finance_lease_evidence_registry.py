"""
DEC-045 (2026-10-30): finance-lease evidence registry and the tier-2 debt
metric total_debt_including_finance_leases. Synthetic rows except where the
committed CSV is checked; MSFT/MU shapes use their FY2026 10-K values.
"""

from __future__ import annotations

from pathlib import Path

from src.fundamentals.batch_runner import STATUS_OK, run_batch
from src.fundamentals.finance_lease_evidence_registry import (
    LOOKUP_DUPLICATE_EVIDENCE,
    LOOKUP_INVALID_REFERENCE,
    LOOKUP_NO_EVIDENCE,
    LOOKUP_NOT_YET_AVAILABLE,
    LOOKUP_OK,
    compute_total_debt_including_finance_leases,
    finance_lease_flag_for_total_debt,
    get_finance_lease_evidence,
    validate_finance_lease_evidence_reference_data,
    validate_finance_lease_evidence_row,
)
from src.fundamentals.finance_lease_evidence_registry_io import (
    FINANCE_LEASE_EVIDENCE_FIELDNAMES,
    read_finance_lease_evidence_csv,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CSV = REPO_ROOT / "data" / "reference" / "finance_lease_evidence.csv"
M = 1_000_000
AVAILABLE = "2026-10-31"
EVAL = "2026-11-02"


def _row(**over):
    row = {
        "ticker": "MSFT", "cik": "0000789019", "accession": "0001193125-26-323660",
        "form": "10-K", "filing_date": "2026-07-29", "period_end": "2026-06-30", "unit": "USD",
        "finance_lease_status": "PRESENT", "finance_lease_amount_status": "DISCLOSED",
        "finance_lease_liability_current": str(4290 * M),
        "finance_lease_liability_noncurrent": str(62304 * M),
        "finance_lease_liability_total": str(66594 * M),
        "current_location": "Other current liabilities",
        "noncurrent_location": "Other long-term liabilities",
        "current_in_total_debt": "EXCLUDED", "noncurrent_in_total_debt": "EXCLUDED",
        "tag_scope_capability": "LongTermDebtCurrent:CAPITAL_FINANCE_LEASE_EXCLUDED",
        "evidence_basis": "LEASE_NOTE_CAPTION_MAPPING_AND_DEBT_RECONCILIATION",
        "source_urls": "https://www.sec.gov/x", "evidence_rationale": "synthetic",
        "evidence_available_date": AVAILABLE, "reviewed_at_utc": "2026-10-30T14:45:46Z",
        "policy_version": "LEASE_TIER2_V1", "decision_ref": "DEC-045",
    }
    row.update(over)
    return row


def _mu_row(**over):
    base = dict(
        ticker="MU", cik="0000723125", accession="0000723125-26-000023", filing_date="2026-10-09",
        period_end="2026-09-03",
        finance_lease_liability_current=str(491 * M), finance_lease_liability_noncurrent=str(1896 * M),
        finance_lease_liability_total=str(2387 * M),
        current_location="Current debt", noncurrent_location="Long-term debt",
        current_in_total_debt="INCLUDED", noncurrent_in_total_debt="INCLUDED",
        evidence_basis="DEBT_TABLE_FINANCE_LEASE_ROW",
    )
    base.update(over)
    return _row(**base)


def _total(cur_val, non_val, acc="0001193125-26-323660", end="2026-06-30", unit="USD", status="OK"):
    comp = lambda v: {"status": "OK", "value": v, "unit": unit, "accession": acc, "period_end": end}
    return {"metric": "total_debt", "status": status, "value": cur_val + non_val if status == "OK" else None,
            "unit": unit, "components": {"current_debt": comp(cur_val), "noncurrent_debt": comp(non_val)}}


MSFT_TOTAL = _total(9227 * M, 31067 * M)
MU_TOTAL = _total(491 * M, 4688 * M, acc="0000723125-26-000023", end="2026-09-03")


# ------------------------------------------------------------ committed CSV

def test_committed_csv_is_valid_and_has_the_approved_seed_rows():
    rows = read_finance_lease_evidence_csv(CSV)
    report = validate_finance_lease_evidence_reference_data(rows)
    assert report == {"row_failures": {}, "duplicates": []}
    assert sorted(r["ticker"] for r in rows) == ["AAPL", "GOOGL", "LLY", "LLY", "LLY", "META", "MSFT", "MU"]
    header = CSV.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header == FINANCE_LEASE_EVIDENCE_FIELDNAMES
    for r in rows:
        assert r["policy_version"] == "LEASE_TIER2_V1"
        assert r["decision_ref"].startswith("DEC-049" if r["ticker"] == "LLY" else "DEC-045")
        assert r["form"] == "10-K"  # seeded from 10-K evidence only
        assert r["source_urls"].startswith("https://www.sec.gov/")


def test_committed_csv_placements():
    rows = {r["ticker"]: r for r in read_finance_lease_evidence_csv(CSV)}
    assert rows["MU"]["current_in_total_debt"] == rows["MU"]["noncurrent_in_total_debt"] == "INCLUDED"
    for t in ("AAPL", "GOOGL", "META", "MSFT"):
        assert rows[t]["current_in_total_debt"] == rows[t]["noncurrent_in_total_debt"] == "EXCLUDED"


# ------------------------------------------------------------ validator

def test_valid_rows_pass():
    assert validate_finance_lease_evidence_row(_row()) == []
    assert validate_finance_lease_evidence_row(_mu_row()) == []


def test_parts_must_add_up():
    assert "finance_lease_liability_total" in validate_finance_lease_evidence_row(
        _row(finance_lease_liability_total=str(66593 * M)))


def test_excluded_requires_caption_mapping_basis_d2():
    assert "evidence_basis" in validate_finance_lease_evidence_row(
        _row(evidence_basis="DEBT_TABLE_FINANCE_LEASE_ROW"))


def test_placed_part_requires_location():
    assert "noncurrent_location" in validate_finance_lease_evidence_row(_row(noncurrent_location=None))


def test_bad_placement_value_rejected():
    assert "current_in_total_debt" in validate_finance_lease_evidence_row(_row(current_in_total_debt="YES"))


def test_verified_none_requires_d1_basis_and_no_amounts():
    ok = _row(finance_lease_status="VERIFIED_NONE", evidence_basis="NO_FINANCE_LEASE_D1", finance_lease_amount_status=None,
              finance_lease_liability_current=None, finance_lease_liability_noncurrent=None,
              finance_lease_liability_total=None, current_in_total_debt=None,
              noncurrent_in_total_debt=None, current_location=None, noncurrent_location=None)
    assert validate_finance_lease_evidence_row(ok) == []
    assert "evidence_basis" in validate_finance_lease_evidence_row(
        dict(ok, evidence_basis="LEASE_NOTE_CAPTION_MAPPING_AND_DEBT_RECONCILIATION"))
    assert "finance_lease_liability_total" in validate_finance_lease_evidence_row(
        dict(ok, finance_lease_liability_total="0"))


def test_unknown_requires_insufficient_basis():
    row = _row(finance_lease_status="UNKNOWN", evidence_basis="INSUFFICIENT_EVIDENCE")
    assert validate_finance_lease_evidence_row(row) == []
    assert "evidence_basis" in validate_finance_lease_evidence_row(
        dict(row, evidence_basis="NO_FINANCE_LEASE_D1"))


def test_review_timestamp_and_availability_rules():
    assert "reviewed_at_utc" in validate_finance_lease_evidence_row(_row(reviewed_at_utc="2026-10-30"))
    assert "evidence_available_date" in validate_finance_lease_evidence_row(
        _row(evidence_available_date="2026-10-30"))  # not after the review day


def test_unknown_policy_and_missing_fields_rejected():
    assert "policy_version" in validate_finance_lease_evidence_row(_row(policy_version="V0"))
    assert "source_urls" in validate_finance_lease_evidence_row(_row(source_urls=None))
    assert "cik" in validate_finance_lease_evidence_row(_row(cik="MSFT"))


def test_duplicate_keys_reported():
    report = validate_finance_lease_evidence_reference_data([_row(), _row()])
    assert report["duplicates"] == [(0, 1)]


# ------------------------------------------------------------ lookup

def test_lookup_statuses():
    acc, end = "0001193125-26-323660", "2026-06-30"
    assert get_finance_lease_evidence("789019", acc, end, "USD", EVAL, [_row()])["lookup_status"] == LOOKUP_OK
    assert get_finance_lease_evidence("789019", acc, end, "USD", "2026-10-30", [_row()])["lookup_status"] == LOOKUP_NOT_YET_AVAILABLE
    assert get_finance_lease_evidence("789019", "other", end, "USD", EVAL, [_row()])["lookup_status"] == LOOKUP_NO_EVIDENCE
    assert get_finance_lease_evidence("789019", acc, end, "EUR", EVAL, [_row()])["lookup_status"] == LOOKUP_NO_EVIDENCE
    assert get_finance_lease_evidence("789019", acc, end, "USD", EVAL, [_row(), _row()])["lookup_status"] == LOOKUP_DUPLICATE_EVIDENCE
    bad = _row(finance_lease_liability_total="1")
    assert get_finance_lease_evidence("789019", acc, end, "USD", EVAL, [bad])["lookup_status"] == LOOKUP_INVALID_REFERENCE


# ------------------------------------------------------------ tier 2

def test_msft_excluded_finance_leases_are_added():
    t2 = compute_total_debt_including_finance_leases(MSFT_TOTAL, "0000789019", EVAL, [_row()])
    assert t2["status"] == "OK"
    assert t2["value"] == (40294 + 66594) * M
    assert t2["total_debt"] == 40294 * M and t2["finance_lease_added"] == 66594 * M
    assert MSFT_TOTAL["value"] == 40294 * M  # tier 1 untouched


def test_mu_included_finance_leases_are_not_double_counted():
    t2 = compute_total_debt_including_finance_leases(MU_TOTAL, "723125", EVAL, [_mu_row()])
    assert t2["status"] == "OK"
    assert t2["value"] == 5179 * M
    assert t2["finance_lease_added"] == 0


def test_equal_amount_without_evidence_is_not_inferred():
    # MU shape (DebtCurrent equals the current finance lease) but no row.
    t2 = compute_total_debt_including_finance_leases(MU_TOTAL, "723125", EVAL, [])
    assert t2["status"] == "INCOMPLETE" and t2["reason_code"] == "NO_FINANCE_LEASE_EVIDENCE"


def test_split_placement_adds_only_the_excluded_part():
    row = _row(current_in_total_debt="INCLUDED")
    t2 = compute_total_debt_including_finance_leases(MSFT_TOTAL, "789019", EVAL, [row])
    assert t2["value"] == (40294 + 62304) * M


def test_unknown_placement_is_incomplete():
    row = _row(noncurrent_in_total_debt="UNKNOWN")
    t2 = compute_total_debt_including_finance_leases(MSFT_TOTAL, "789019", EVAL, [row])
    assert t2["status"] == "INCOMPLETE" and t2["reason_code"] == "FINANCE_LEASE_SCOPE_UNKNOWN"


def test_unknown_status_row_is_incomplete():
    row = _row(finance_lease_status="UNKNOWN", evidence_basis="INSUFFICIENT_EVIDENCE")
    t2 = compute_total_debt_including_finance_leases(MSFT_TOTAL, "789019", EVAL, [row])
    assert t2["reason_code"] == "FINANCE_LEASE_SCOPE_UNKNOWN"


def test_verified_none_equals_tier1():
    row = _row(finance_lease_status="VERIFIED_NONE", evidence_basis="NO_FINANCE_LEASE_D1", finance_lease_amount_status=None,
               finance_lease_liability_current=None, finance_lease_liability_noncurrent=None,
               finance_lease_liability_total=None, current_in_total_debt=None,
               noncurrent_in_total_debt=None, current_location=None, noncurrent_location=None)
    t2 = compute_total_debt_including_finance_leases(MSFT_TOTAL, "789019", EVAL, [row])
    assert t2["status"] == "OK" and t2["value"] == 40294 * M and t2["finance_lease_added"] == 0


def test_evidence_for_another_filing_is_not_carried_forward():
    later_quarter = _total(9000 * M, 31000 * M, acc="0001193125-26-999999", end="2026-09-30")
    t2 = compute_total_debt_including_finance_leases(later_quarter, "789019", EVAL, [_row()])
    assert t2["status"] == "INCOMPLETE" and t2["reason_code"] == "NO_FINANCE_LEASE_EVIDENCE"


def test_unit_mismatch_is_incomplete():
    eur = _total(9227 * M, 31067 * M, unit="EUR")
    t2 = compute_total_debt_including_finance_leases(eur, "789019", EVAL, [_row()])
    assert t2["status"] == "INCOMPLETE"


def test_tier1_incomplete_is_never_substituted():
    t2 = compute_total_debt_including_finance_leases(_total(1, 2, status="INCOMPLETE"), "789019", EVAL, [_row()])
    assert t2["status"] == "INCOMPLETE" and t2["reason_code"] == "TIER1_INCOMPLETE" and t2["value"] is None


def test_components_from_different_filings_are_ambiguous():
    td = _total(9227 * M, 31067 * M)
    td["components"]["noncurrent_debt"]["accession"] = "0001193125-26-000001"
    t2 = compute_total_debt_including_finance_leases(td, "789019", EVAL, [_row()])
    assert t2["reason_code"] == "TIER1_PROVENANCE_AMBIGUOUS"


def test_not_yet_available_evidence_is_incomplete():
    t2 = compute_total_debt_including_finance_leases(MSFT_TOTAL, "789019", "2026-10-30", [_row()])
    assert t2["reason_code"] == "EVIDENCE_NOT_YET_AVAILABLE"


# ------------------------------------------------------------ D4 flag

def test_flag_only_when_finance_leases_are_outside():
    msft = compute_total_debt_including_finance_leases(MSFT_TOTAL, "789019", EVAL, [_row()])
    flag = finance_lease_flag_for_total_debt(msft)
    assert flag["finance_leases_outside_total_debt"] is True
    assert flag["finance_lease_liabilities_outside_total_debt"] == 66594 * M
    mu = compute_total_debt_including_finance_leases(MU_TOTAL, "723125", EVAL, [_mu_row()])
    assert finance_lease_flag_for_total_debt(mu) is None
    incomplete = compute_total_debt_including_finance_leases(MSFT_TOTAL, "789019", EVAL, [])
    assert finance_lease_flag_for_total_debt(incomplete) is None


# ------------------------------------------------------------ batch wiring

def _pipeline_with(total_debt):
    def pipeline(**kwargs):
        return {"ticker": kwargs["ticker"], "stages": {
            "normalization": {"status": "OK", "normalized": {"metrics": {"total_debt": dict(total_debt)}}}}}
    return pipeline


_MEMBERS = [{"cik": "0000789019", "ticker": "MSFT", "universe_type": "WATCHLIST", "membership_status": "ACTIVE"}]
_CT = [{"ticker": "MSFT", "cik": "0000789019", "company_type": "NON_FINANCIAL", "effective_from": "2026-09-20",
        "effective_to": None, "classification_available_date": "2026-09-20",
        "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE", "mapping_rationale": "t",
        "reviewed_at_utc": "2026-10-26T00:00:00Z", "decision_ref": "t"}]


def test_batch_adds_debt_tiers_and_flag_without_changing_total_debt(tmp_path):
    summary = run_batch(evaluation_date=EVAL, company_type_rows=_CT, membership_rows=_MEMBERS,
                        pipeline_fn=_pipeline_with(MSFT_TOTAL), fetch_fn=lambda t, c: {},
                        raw_dir=tmp_path / "raw", output_dir=tmp_path / "out",
                        finance_lease_evidence_rows=[_row()])
    rec = summary["records"][0]
    assert rec["status"] == STATUS_OK
    assert rec["total_debt_including_finance_leases"]["value"] == (40294 + 66594) * M
    assert rec["stage_statuses"]["debt_tiers"] == "OK"
    import json
    out = json.loads((tmp_path / "out" / "MSFT.json").read_text())
    td = out["stages"]["normalization"]["normalized"]["metrics"]["total_debt"]
    assert td["value"] == 40294 * M and td["status"] == "OK"
    assert td["finance_leases_outside_total_debt"] is True
    assert out["stages"]["debt_tiers"]["total_debt_including_finance_leases"]["status"] == "OK"


def test_batch_without_evidence_rows_is_unchanged(tmp_path):
    summary = run_batch(evaluation_date=EVAL, company_type_rows=_CT, membership_rows=_MEMBERS,
                        pipeline_fn=_pipeline_with(MSFT_TOTAL), fetch_fn=lambda t, c: {},
                        raw_dir=tmp_path / "raw", output_dir=None)
    rec = summary["records"][0]
    assert "total_debt_including_finance_leases" not in rec
    assert "debt_tiers" not in rec["stage_statuses"]
