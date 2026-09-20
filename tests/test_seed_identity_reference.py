"""
Offline regression test for scripts/seed_identity_reference.py.

The script itself needs live network (data.sec.gov, www.sec.gov -- see
its own module docstring for why that can't run in this sandbox). This
test mocks the two network calls with small synthetic fixtures so the
script's actual logic -- CIK resolution, issuer/security normalization,
SIC_DERIVED grouping, CSV writing -- is still exercised on every
`pytest -q` run, with zero network dependency.
"""

import json
from pathlib import Path
from unittest.mock import patch

import scripts.seed_identity_reference as seed_mod
from src.sec.reference_io import read_issuer_identity_csv, read_security_identity_csv
from src.fundamentals.peer_classification_io import read_peer_groups_csv


FAKE_COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1375365, "ticker": "SMCI", "title": "Super Micro Computer, Inc."},
    "2": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
}

FAKE_SUBMISSIONS = {
    "0000320193": {
        "cik": "0000320193", "name": "Apple Inc.", "sic": "3571",
        "sicDescription": "Electronic Computers", "tickers": ["AAPL"], "exchanges": ["Nasdaq"],
    },
    "0001375365": {
        "cik": "0001375365", "name": "Super Micro Computer, Inc.", "sic": "3571",
        "sicDescription": "Electronic Computers", "tickers": ["SMCI"], "exchanges": ["Nasdaq"],
    },
    "0001652044": {
        "cik": "0001652044", "name": "Alphabet Inc.", "sic": "7370",
        "sicDescription": "Services-Computer Programming, Data Processing, Etc.",
        "tickers": ["GOOGL", "GOOG", "GOOGM", "GOOGN"],
        "exchanges": ["Nasdaq", "Nasdaq", "Nasdaq", "Nasdaq"],
    },
}


def _fake_fetch_and_cache_submissions(cik, raw_dir, user_agent, verbose=True, **kw):
    data = FAKE_SUBMISSIONS[cik]
    raw_path = Path(raw_dir) / f"{cik}_submissions.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(data), encoding="utf-8")
    return data, raw_path


def _run_seed_script(tmp_path, watchlist, monkeypatch):
    monkeypatch.setattr(seed_mod, "WATCHLIST_TICKERS", watchlist)
    monkeypatch.setattr(seed_mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(seed_mod, "REFERENCE_DIR", tmp_path / "reference")

    with patch.object(seed_mod, "fetch_company_tickers", return_value=FAKE_COMPANY_TICKERS), \
         patch.object(seed_mod, "fetch_and_cache_submissions", side_effect=_fake_fetch_and_cache_submissions), \
         patch.object(seed_mod, "get_user_agent", return_value="test-agent"):
        seed_mod.main()

    return tmp_path / "reference"


def test_seed_script_writes_one_issuer_row_per_cik(tmp_path, monkeypatch):
    ref_dir = _run_seed_script(tmp_path, ["AAPL", "SMCI", "GOOGL"], monkeypatch)
    issuer_rows = read_issuer_identity_csv(ref_dir / "issuer_identity.csv")
    assert {row["cik"] for row in issuer_rows} == {"0000320193", "0001375365", "0001652044"}


def test_seed_script_multi_ticker_cik_produces_all_tickers_in_security_rows(tmp_path, monkeypatch):
    ref_dir = _run_seed_script(tmp_path, ["AAPL", "SMCI", "GOOGL"], monkeypatch)
    security_rows = read_security_identity_csv(ref_dir / "security_identity.csv")
    googl_tickers = {row["ticker"] for row in security_rows if row["cik"] == "0001652044"}
    assert googl_tickers == {"GOOGL", "GOOG", "GOOGM", "GOOGN"}


def test_seed_script_groups_shared_sic_into_same_sic_derived_peer_group(tmp_path, monkeypatch):
    # AAPL and SMCI both carry SIC 3571 -- the script must place them in
    # the same auto-generated candidate peer_group.
    ref_dir = _run_seed_script(tmp_path, ["AAPL", "SMCI", "GOOGL"], monkeypatch)
    peer_rows = read_peer_groups_csv(ref_dir / "peer_groups.csv")
    by_cik = {row["cik"]: row for row in peer_rows}
    assert by_cik["0000320193"]["peer_group"] == by_cik["0001375365"]["peer_group"] == "sic_3571"
    assert by_cik["0001652044"]["peer_group"] == "sic_7370"


def test_seed_script_never_writes_a_reviewed_row(tmp_path, monkeypatch):
    # Core design guarantee (see script's module docstring): this script
    # has no authority to assert a human-reviewed peer group.
    ref_dir = _run_seed_script(tmp_path, ["AAPL", "SMCI", "GOOGL"], monkeypatch)
    peer_rows = read_peer_groups_csv(ref_dir / "peer_groups.csv")
    assert all(row["classification_status"] == "SIC_DERIVED" for row in peer_rows)
    assert all(row["classification_source"] == "SIC_DERIVED" for row in peer_rows)
    assert all(row["reviewed_at"] is None for row in peer_rows)


def test_seed_script_issuer_rows_carry_provenance_back_to_raw_file(tmp_path, monkeypatch):
    ref_dir = _run_seed_script(tmp_path, ["AAPL"], monkeypatch)
    issuer_rows = read_issuer_identity_csv(ref_dir / "issuer_identity.csv")
    assert issuer_rows[0]["source_raw_path"].endswith("0000320193_submissions.json")


def test_seed_script_raises_on_unresolvable_ticker(tmp_path, monkeypatch):
    import pytest
    with pytest.raises(RuntimeError, match="ZZZNOTREAL"):
        _run_seed_script(tmp_path, ["AAPL", "ZZZNOTREAL"], monkeypatch)
