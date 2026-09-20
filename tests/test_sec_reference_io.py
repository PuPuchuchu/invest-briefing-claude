"""
Unit tests for src/sec/reference_io.py.

No network -- exercises the CSV read/write round trip for
issuer_identity.csv and security_identity.csv against synthetic
identity.py-shaped fixtures and a temp directory (tmp_path).
"""

from src.sec.identity import normalize_issuer_identity, normalize_security_identities
from src.sec.reference_io import (
    LIST_DELIMITER,
    ISSUER_IDENTITY_FIELDNAMES,
    SECURITY_IDENTITY_FIELDNAMES,
    serialize_issuer_identity_row,
    deserialize_issuer_identity_row,
    write_issuer_identity_csv,
    read_issuer_identity_csv,
    serialize_security_identity_row,
    deserialize_security_identity_row,
    write_security_identity_csv,
    read_security_identity_csv,
)


def _googl_raw_submissions():
    return {
        "cik": "0001652044",
        "name": "Alphabet Inc.",
        "sic": "7370",
        "sicDescription": "Services-Computer Programming, Data Processing, Etc.",
        "tickers": ["GOOGL", "GOOG", "GOOGM", "GOOGN"],
        "exchanges": ["Nasdaq", "Nasdaq", "Nasdaq", "Nasdaq"],
    }


def _aapl_raw_submissions():
    return {
        "cik": "0000320193",
        "name": "Apple Inc.",
        "sic": "3571",
        "sicDescription": "Electronic Computers",
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
    }


# ============================================================
# issuer_identity.csv -- serialize/deserialize (in-memory)
# ============================================================

def test_serialize_issuer_identity_row_joins_lists_with_pipe():
    row = normalize_issuer_identity(
        _googl_raw_submissions(), source_raw_path="x", normalized_at="y"
    )
    serialized = serialize_issuer_identity_row(row)
    assert serialized["sec_tickers"] == "GOOGL|GOOG|GOOGM|GOOGN"
    assert serialized["sec_exchanges"] == "Nasdaq|Nasdaq|Nasdaq|Nasdaq"
    assert all(isinstance(v, str) for v in serialized.values())


def test_serialize_issuer_identity_row_empty_list_becomes_empty_string():
    row = {"cik": "1", "issuer_name": "X", "sic": "9", "sic_description": None,
           "sec_tickers": [], "sec_exchanges": [], "source_raw_path": "p", "normalized_at": "t"}
    serialized = serialize_issuer_identity_row(row)
    assert serialized["sec_tickers"] == ""
    assert serialized["sec_exchanges"] == ""


def test_serialize_issuer_identity_row_none_scalar_becomes_empty_string():
    row = {"cik": "1", "issuer_name": None, "sic": None, "sic_description": None,
           "sec_tickers": [], "sec_exchanges": [], "source_raw_path": "p", "normalized_at": "t"}
    serialized = serialize_issuer_identity_row(row)
    assert serialized["issuer_name"] == ""
    assert serialized["sic"] == ""


def test_deserialize_issuer_identity_row_round_trips_multi_ticker():
    original = normalize_issuer_identity(
        _googl_raw_submissions(),
        source_raw_path="data/raw/sec/0001652044_submissions.json",
        normalized_at="2026-09-20T00:00:00Z",
    )
    round_tripped = deserialize_issuer_identity_row(serialize_issuer_identity_row(original))
    assert round_tripped == original


def test_deserialize_issuer_identity_row_empty_string_becomes_none_for_scalars():
    flat = {"cik": "1", "issuer_name": "", "sic": "", "sic_description": "",
            "sec_tickers": "", "sec_exchanges": "", "source_raw_path": "", "normalized_at": ""}
    row = deserialize_issuer_identity_row(flat)
    assert row["issuer_name"] is None
    assert row["sic"] is None
    assert row["sec_tickers"] == []
    assert row["sec_exchanges"] == []


def test_ticker_symbols_never_contain_the_list_delimiter():
    # Sanity check on the design assumption in reference_io.py's module
    # docstring: if this ever fires, the pipe-delimited convention needs
    # to change to something that escapes the delimiter.
    for raw in (_googl_raw_submissions(), _aapl_raw_submissions()):
        for ticker in raw["tickers"]:
            assert LIST_DELIMITER not in ticker
        for exchange in raw["exchanges"]:
            assert LIST_DELIMITER not in exchange


# ============================================================
# issuer_identity.csv -- file round trip (tmp_path)
# ============================================================

def test_write_then_read_issuer_identity_csv_round_trips(tmp_path):
    rows = [
        normalize_issuer_identity(
            _googl_raw_submissions(),
            source_raw_path="data/raw/sec/0001652044_submissions.json",
            normalized_at="2026-09-20T00:00:00Z",
        ),
        normalize_issuer_identity(
            _aapl_raw_submissions(),
            source_raw_path="data/raw/sec/0000320193_submissions.json",
            normalized_at="2026-09-20T00:00:00Z",
        ),
    ]
    path = tmp_path / "issuer_identity.csv"
    write_issuer_identity_csv(rows, path)
    read_back = read_issuer_identity_csv(path)
    assert read_back == rows


def test_write_issuer_identity_csv_header_matches_fieldnames(tmp_path):
    path = tmp_path / "issuer_identity.csv"
    write_issuer_identity_csv([], path)
    header_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert header_line.split(",") == ISSUER_IDENTITY_FIELDNAMES


def test_read_issuer_identity_csv_header_only_file_returns_empty_list(tmp_path):
    path = tmp_path / "issuer_identity.csv"
    path.write_text(",".join(ISSUER_IDENTITY_FIELDNAMES) + "\n", encoding="utf-8")
    assert read_issuer_identity_csv(path) == []


def test_write_issuer_identity_csv_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "issuer_identity.csv"
    write_issuer_identity_csv([], path)
    assert path.exists()


# ============================================================
# security_identity.csv
# ============================================================

def test_serialize_security_identity_row():
    row = normalize_security_identities(_aapl_raw_submissions())[0]
    serialized = serialize_security_identity_row(row)
    assert serialized["ticker"] == "AAPL"
    assert serialized["cik"] == "0000320193"
    assert serialized["exchange"] == "Nasdaq"
    assert serialized["identity_status"] == ""  # not set by normalize_security_identities


def test_deserialize_security_identity_row_empty_string_becomes_none():
    flat = {field: "" for field in SECURITY_IDENTITY_FIELDNAMES}
    row = deserialize_security_identity_row(flat)
    assert all(value is None for value in row.values())


def test_write_then_read_security_identity_csv_round_trips_multi_ticker(tmp_path):
    rows = normalize_security_identities(_googl_raw_submissions())
    path = tmp_path / "security_identity.csv"
    write_security_identity_csv(rows, path)
    read_back = read_security_identity_csv(path)

    # normalize_security_identities() doesn't set identity_status/
    # identity_status_reason/verified_at at all -- round trip through CSV
    # adds them back as explicit None (the CSV schema always carries all
    # columns), so compare field-by-field on the columns that were set.
    assert len(read_back) == len(rows)
    for original, restored in zip(rows, read_back):
        assert restored["security_id"] == original["security_id"]
        assert restored["ticker"] == original["ticker"]
        assert restored["cik"] == original["cik"]
        assert restored["exchange"] == original["exchange"]


def test_write_security_identity_csv_header_matches_fieldnames(tmp_path):
    path = tmp_path / "security_identity.csv"
    write_security_identity_csv([], path)
    header_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert header_line.split(",") == SECURITY_IDENTITY_FIELDNAMES
