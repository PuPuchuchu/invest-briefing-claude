"""
Regression tests for src/fundamentals/core_universe_io.py
(Framework v2.0 Step 10B schema, 2026-09-20 ChatGPT design review).
"""

from __future__ import annotations

from src.fundamentals.core_universe_io import (
    CORE_UNIVERSE_FIELDNAMES,
    read_core_universe_candidates_csv,
    validate_core_universe_row,
    write_core_universe_candidates_csv,
)


def _proposed_row(**overrides):
    row = {
        "ticker": "QCOM",
        "cik": None,
        "sector": "Information Technology",
        "industry": "Semiconductors",
        "proposed_peer_group": "AI & Compute Semiconductors",
        "classification_rationale": "Fabless semiconductor, comparable cost structure.",
        "sec_availability": "UNVERIFIED",
        "expected_metric_comparability": "Revenue/margin scale differs; still same business model.",
        "inclusion_status": "CANDIDATE_PROPOSED",
        "added_date": "2026-09-20",
        "notes": None,
    }
    row.update(overrides)
    return row


# ============================================================
# Round-trip I/O
# ============================================================

def test_round_trip_write_then_read(tmp_path):
    rows = [
        _proposed_row(),
        _proposed_row(ticker="AVGO", proposed_peer_group="IDM & Foundry Semiconductors"),
    ]
    path = tmp_path / "core_universe_candidates.csv"
    write_core_universe_candidates_csv(rows, path)

    read_back = read_core_universe_candidates_csv(path)

    assert len(read_back) == 2
    assert read_back[0]["ticker"] == "QCOM"
    assert read_back[0]["cik"] is None  # empty string round-trips to None
    assert read_back[1]["ticker"] == "AVGO"


def test_write_creates_header_matching_fieldnames(tmp_path):
    path = tmp_path / "core_universe_candidates.csv"
    write_core_universe_candidates_csv([], path)

    with path.open() as f:
        header_line = f.readline().strip()

    assert header_line.split(",") == CORE_UNIVERSE_FIELDNAMES


def test_missing_values_never_fabricated_as_empty_string_not_zero(tmp_path):
    row = _proposed_row(cik=None, notes=None)
    path = tmp_path / "core_universe_candidates.csv"
    write_core_universe_candidates_csv([row], path)

    read_back = read_core_universe_candidates_csv(path)[0]
    assert read_back["cik"] is None
    assert read_back["notes"] is None


# ============================================================
# validate_core_universe_row
# ============================================================

def test_valid_proposed_row_passes():
    assert validate_core_universe_row(_proposed_row()) == []


def test_valid_verified_row_passes():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="CANDIDATE_VERIFIED",
    )
    assert validate_core_universe_row(row) == []


def test_valid_promoted_row_passes():
    row = _proposed_row(
        cik="0000123456",
        sec_availability="CONFIRMED",
        inclusion_status="PROMOTED",
    )
    assert validate_core_universe_row(row) == []


def test_missing_ticker_fails():
    row = _proposed_row(ticker=None)
    assert "ticker" in validate_core_universe_row(row)


def test_unknown_inclusion_status_fails():
    row = _proposed_row(inclusion_status="MAYBE_LATER")
    assert "inclusion_status" in validate_core_universe_row(row)


def test_unknown_sec_availability_fails():
    row = _proposed_row(sec_availability="PROBABLY")
    assert "sec_availability" in validate_core_universe_row(row)


def test_cik_present_without_confirmed_availability_fails():
    # A hardcoded/guessed CIK sitting next to UNVERIFIED must be rejected
    # -- this is exactly the "never fabricate SEC data" rule.
    row = _proposed_row(cik="0000999999", sec_availability="UNVERIFIED")
    assert "cik_present_without_confirmed_sec_availability" in validate_core_universe_row(row)


def test_confirmed_availability_without_cik_fails():
    row = _proposed_row(cik=None, sec_availability="CONFIRMED")
    failures = validate_core_universe_row(row)
    assert "cik_missing_for_confirmed_sec_availability" in failures


def test_verified_status_without_cik_fails():
    row = _proposed_row(
        cik=None,
        sec_availability="UNVERIFIED",
        inclusion_status="CANDIDATE_VERIFIED",
    )
    failures = validate_core_universe_row(row)
    # Wrong status/availability combination AND missing CIK both flagged.
    assert "inclusion_status_sec_availability_combination" in failures


def test_rejected_row_may_have_any_availability():
    for availability in ("UNVERIFIED", "CONFIRMED", "NOT_FOUND"):
        row = _proposed_row(
            inclusion_status="REJECTED",
            sec_availability=availability,
            cik="0000123456" if availability == "CONFIRMED" else None,
        )
        assert validate_core_universe_row(row) == [], (
            f"REJECTED with sec_availability={availability!r} should be valid"
        )


def test_not_found_status_has_no_cik():
    row = _proposed_row(sec_availability="NOT_FOUND")
    assert validate_core_universe_row(row) == []

