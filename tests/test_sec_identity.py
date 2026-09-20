"""
Unit tests for src/sec/identity.py.

Pure Unit Tests in the taxonomy sense: no network, no file I/O, synthetic
fixtures only -- matching the module's own zero-I/O contract. These exist
specifically to prove the properties the 2026-09-19 design review required:
issuer (CIK) vs security (ticker) records stay separate, a single CIK can
carry multiple tickers, and identity-status resolution distinguishes MATCH /
CONFLICT / STALE / UNAVAILABLE rather than collapsing them into one flag.
"""

from src.sec.identity import (
    SCHEMA_VERSION,
    IDENTITY_MATCH,
    IDENTITY_CONFLICT,
    IDENTITY_STALE,
    IDENTITY_UNAVAILABLE,
    VALID_IDENTITY_STATUSES,
    normalize_issuer_identity,
    validate_issuer_identity,
    validate_issuer_identity_provenance,
    normalize_security_identities,
    validate_security_identity,
    resolve_cik_for_ticker,
    resolve_identity_status,
)


# ============================================================
# Fixtures
# ============================================================

def _googl_raw_submissions():
    """Real shape confirmed live 2026-09-19: Alphabet, CIK 0001652044,
    carries 4 tickers across 4 (identical, in this case) exchanges."""
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
# normalize_issuer_identity
# ============================================================

def test_normalize_issuer_identity_extracts_all_fields():
    row = normalize_issuer_identity(
        _googl_raw_submissions(),
        source_raw_path="data/raw/sec/0001652044_submissions.json",
        normalized_at="2026-09-19T00:00:00Z",
    )
    assert row["cik"] == "0001652044"
    assert row["issuer_name"] == "Alphabet Inc."
    assert row["sic"] == "7370"
    assert row["sic_description"] == "Services-Computer Programming, Data Processing, Etc."
    assert row["sec_tickers"] == ["GOOGL", "GOOG", "GOOGM", "GOOGN"]
    assert row["sec_exchanges"] == ["Nasdaq", "Nasdaq", "Nasdaq", "Nasdaq"]
    assert row["source_raw_path"] == "data/raw/sec/0001652044_submissions.json"
    assert row["normalized_at"] == "2026-09-19T00:00:00Z"


def test_normalize_issuer_identity_single_ticker_still_a_list():
    row = normalize_issuer_identity(
        _aapl_raw_submissions(), source_raw_path="x", normalized_at="y"
    )
    assert row["sec_tickers"] == ["AAPL"]


def test_normalize_issuer_identity_missing_tickers_defaults_to_empty_list():
    raw = {"cik": "0000000001", "name": "Nobody Inc.", "sic": "9999"}
    row = normalize_issuer_identity(raw, source_raw_path="x", normalized_at="y")
    assert row["sec_tickers"] == []
    assert row["sec_exchanges"] == []


def test_normalize_issuer_identity_rejects_non_dict():
    try:
        normalize_issuer_identity([], source_raw_path="x", normalized_at="y")
        assert False, "expected TypeError"
    except TypeError:
        pass


# ============================================================
# validate_issuer_identity
# ============================================================

def test_validate_issuer_identity_accepts_complete_row():
    row = normalize_issuer_identity(
        _googl_raw_submissions(),
        source_raw_path="data/raw/sec/0001652044_submissions.json",
        normalized_at="2026-09-19T00:00:00Z",
    )
    assert validate_issuer_identity(row) == []


def test_validate_issuer_identity_flags_missing_cik():
    row = normalize_issuer_identity(
        _googl_raw_submissions(), source_raw_path="x", normalized_at="y"
    )
    row["cik"] = None
    assert "cik" in validate_issuer_identity(row)


def test_validate_issuer_identity_flags_missing_source_raw_path():
    row = normalize_issuer_identity(_aapl_raw_submissions(), source_raw_path="", normalized_at="y")
    assert "source_raw_path" in validate_issuer_identity(row)


def test_validate_issuer_identity_flags_non_list_tickers():
    row = normalize_issuer_identity(_aapl_raw_submissions(), source_raw_path="x", normalized_at="y")
    row["sec_tickers"] = "AAPL"
    assert "sec_tickers" in validate_issuer_identity(row)


def test_validate_issuer_identity_rejects_non_dict():
    assert validate_issuer_identity(None) == ["root"]


# ============================================================
# validate_issuer_identity_provenance
# ============================================================

def test_validate_issuer_identity_provenance_accepts_matching_path():
    row = {"cik": "0001652044", "source_raw_path": "data/raw/sec/0001652044_submissions.json"}
    assert validate_issuer_identity_provenance(row) == []


def test_validate_issuer_identity_provenance_flags_mismatched_cik():
    row = {"cik": "0001652044", "source_raw_path": "data/raw/sec/0000320193_submissions.json"}
    assert "source_raw_path_cik_mismatch" in validate_issuer_identity_provenance(row)


def test_validate_issuer_identity_provenance_flags_missing_source_raw_path():
    row = {"cik": "0001652044", "source_raw_path": ""}
    assert "source_raw_path" in validate_issuer_identity_provenance(row)


def test_validate_issuer_identity_provenance_flags_missing_cik():
    row = {"cik": "", "source_raw_path": "data/raw/sec/0001652044_submissions.json"}
    assert "cik" in validate_issuer_identity_provenance(row)


def test_validate_issuer_identity_provenance_rejects_non_dict():
    assert validate_issuer_identity_provenance([]) == ["root"]


# ============================================================
# normalize_security_identities -- multi-ticker CIK is the core case
# ============================================================

def test_normalize_security_identities_multi_ticker_cik_produces_one_row_per_ticker():
    rows = normalize_security_identities(_googl_raw_submissions())
    assert len(rows) == 4
    tickers = [row["ticker"] for row in rows]
    assert tickers == ["GOOGL", "GOOG", "GOOGM", "GOOGN"]
    for row in rows:
        assert row["cik"] == "0001652044"
        assert row["exchange"] == "Nasdaq"
        # security_id and ticker are separate columns, deliberately kept
        # value-equal only by this v0.1 implementation choice.
        assert row["security_id"] == row["ticker"]


def test_normalize_security_identities_single_ticker_cik():
    rows = normalize_security_identities(_aapl_raw_submissions())
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["cik"] == "0000320193"


def test_normalize_security_identities_mismatched_exchange_array_length():
    raw = {
        "cik": "0000000001",
        "tickers": ["ZZZ", "ZZZP"],
        "exchanges": ["NYSE"],  # shorter than tickers -- index 1 has no exchange
    }
    rows = normalize_security_identities(raw)
    assert rows[0]["exchange"] == "NYSE"
    assert rows[1]["exchange"] is None


def test_normalize_security_identities_missing_tickers_returns_empty_list():
    raw = {"cik": "0000000001"}
    assert normalize_security_identities(raw) == []


def test_normalize_security_identities_rejects_non_dict():
    try:
        normalize_security_identities("nope")
        assert False, "expected TypeError"
    except TypeError:
        pass


# ============================================================
# validate_security_identity
# ============================================================

def test_validate_security_identity_accepts_complete_row():
    row = normalize_security_identities(_aapl_raw_submissions())[0]
    assert validate_security_identity(row) == []


def test_validate_security_identity_flags_missing_ticker():
    row = {"security_id": "X", "ticker": "", "cik": "0000320193"}
    assert "ticker" in validate_security_identity(row)


def test_validate_security_identity_flags_invalid_identity_status():
    row = {"security_id": "X", "ticker": "X", "cik": "1", "identity_status": "NOT_A_REAL_STATUS"}
    assert "identity_status" in validate_security_identity(row)


def test_validate_security_identity_accepts_valid_identity_status():
    row = {"security_id": "X", "ticker": "X", "cik": "1", "identity_status": IDENTITY_MATCH}
    assert validate_security_identity(row) == []


def test_validate_security_identity_none_identity_status_is_fine():
    # identity_status is optional -- absence is not itself a failure.
    row = {"security_id": "X", "ticker": "X", "cik": "1"}
    assert validate_security_identity(row) == []


def test_validate_security_identity_rejects_non_dict():
    assert validate_security_identity(42) == ["root"]


# ============================================================
# resolve_cik_for_ticker
# ============================================================

def _company_tickers_fixture():
    """Real shape confirmed live 2026-09-20 (www.sec.gov/files/company_tickers.json):
    object keyed by numeric string index, cik_str is an int, NOT zero-padded."""
    return {
        "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
        "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "2": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    }


def test_resolve_cik_for_ticker_zero_pads_to_ten_digits():
    assert resolve_cik_for_ticker(_company_tickers_fixture(), "AAPL") == "0000320193"


def test_resolve_cik_for_ticker_case_insensitive():
    assert resolve_cik_for_ticker(_company_tickers_fixture(), "aapl") == "0000320193"


def test_resolve_cik_for_ticker_returns_none_when_not_found():
    assert resolve_cik_for_ticker(_company_tickers_fixture(), "ZZZZZ") is None


def test_resolve_cik_for_ticker_rejects_non_dict():
    try:
        resolve_cik_for_ticker([], "AAPL")
        assert False, "expected TypeError"
    except TypeError:
        pass


def test_resolve_cik_for_ticker_seven_digit_cik_still_zero_pads():
    # NVDA's cik_str (1045810) is 7 digits -- confirms zfill(10) pads
    # correctly rather than assuming a fixed input width.
    assert resolve_cik_for_ticker(_company_tickers_fixture(), "NVDA") == "0001045810"


# ============================================================
# resolve_identity_status -- the four outcomes
# ============================================================

def test_resolve_identity_status_match():
    fresh = normalize_issuer_identity(
        _googl_raw_submissions(), source_raw_path="x", normalized_at="y"
    )
    status, reason = resolve_identity_status(
        claimed_cik="0001652044", claimed_ticker="GOOG", fresh_issuer_record=fresh
    )
    assert status == IDENTITY_MATCH
    assert "GOOG" in reason


def test_resolve_identity_status_unavailable_when_fresh_record_is_none():
    status, reason = resolve_identity_status(
        claimed_cik="0001652044", claimed_ticker="GOOG", fresh_issuer_record=None
    )
    assert status == IDENTITY_UNAVAILABLE


def test_resolve_identity_status_conflict_when_ticker_not_in_fresh_tickers():
    fresh = normalize_issuer_identity(
        _aapl_raw_submissions(), source_raw_path="x", normalized_at="y"
    )
    status, reason = resolve_identity_status(
        claimed_cik="0000320193", claimed_ticker="AAPLX", fresh_issuer_record=fresh
    )
    assert status == IDENTITY_CONFLICT


def test_resolve_identity_status_conflict_when_cik_itself_mismatches():
    fresh = normalize_issuer_identity(
        _aapl_raw_submissions(), source_raw_path="x", normalized_at="y"
    )
    status, reason = resolve_identity_status(
        claimed_cik="9999999999", claimed_ticker="AAPL", fresh_issuer_record=fresh
    )
    assert status == IDENTITY_CONFLICT


def test_resolve_identity_status_stale_when_ticker_now_resolves_elsewhere():
    fresh = normalize_issuer_identity(
        _aapl_raw_submissions(), source_raw_path="x", normalized_at="y"
    )
    status, reason = resolve_identity_status(
        claimed_cik="0000320193",
        claimed_ticker="AAPLX",
        fresh_issuer_record=fresh,
        ticker_seen_under_cik="0000999999",
    )
    assert status == IDENTITY_STALE
    assert "0000999999" in reason


def test_resolve_identity_status_stale_requires_different_cik_else_conflict():
    # ticker_seen_under_cik equal to claimed_cik is not evidence of
    # staleness -- must fall through to CONFLICT, never a false STALE.
    fresh = normalize_issuer_identity(
        _aapl_raw_submissions(), source_raw_path="x", normalized_at="y"
    )
    status, _ = resolve_identity_status(
        claimed_cik="0000320193",
        claimed_ticker="AAPLX",
        fresh_issuer_record=fresh,
        ticker_seen_under_cik="0000320193",
    )
    assert status == IDENTITY_CONFLICT


# ============================================================
# Module-level constants
# ============================================================

def test_valid_identity_statuses_contains_all_four():
    assert VALID_IDENTITY_STATUSES == {
        IDENTITY_MATCH,
        IDENTITY_CONFLICT,
        IDENTITY_STALE,
        IDENTITY_UNAVAILABLE,
    }


def test_schema_version_is_a_non_empty_string():
    assert isinstance(SCHEMA_VERSION, str) and SCHEMA_VERSION
