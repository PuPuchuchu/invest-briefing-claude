"""
Offline integration test: real data/reference/universe_membership.csv ->
validate_universe_membership_data() -> get_universe_membership_as_of(),
cross-checked against the real data/reference/issuer_identity.csv and
data/reference/security_identity.csv this repo ships.

Required by the 2026-09-21 ChatGPT design round, item 7: the 55 existing
unit/I-O tests (test_universe_membership.py, test_universe_membership_io.py)
are all synthetic-fixture based -- this file instead reads the ACTUAL
seeded CSVs off disk (as written by scripts/seed_universe_membership.py),
proving the real data, not just the schema logic, is internally
consistent. Mirrors the intent of
tests/test_peer_classification_integration.py, but against real seeded
reference data rather than a hand-built fixture (this repo has no
existing precedent for tests reading data/reference/*.csv directly --
this is the first).

No network -- data/reference/*.csv are committed files, read directly.
If universe_membership.csv has not been seeded yet, these tests are
skipped rather than failed (see _require_universe_membership_csv below),
so a fresh checkout before scripts/seed_universe_membership.py has been
run does not break the suite.
"""

from pathlib import Path

import pytest

from src.fundamentals.universe_membership import (
    LOOKUP_OK,
    LOOKUP_NOT_YET_AVAILABLE,
    validate_universe_membership_data,
    get_universe_membership_as_of,
)
from src.fundamentals.universe_membership_io import read_universe_membership_csv
from src.sec.reference_io import read_issuer_identity_csv, read_security_identity_csv

REFERENCE_DIR = Path("data/reference")
UNIVERSE_MEMBERSHIP_CSV = REFERENCE_DIR / "universe_membership.csv"

# Same 21 tickers as scripts/seed_identity_reference.py's WATCHLIST_TICKERS
# / scripts/seed_universe_membership.py's WATCHLIST_TICKERS.
EXPECTED_WATCHLIST_TICKERS = {
    "MSFT", "AAPL", "NVDA", "AVGO", "INTC", "MU", "AMD", "PLTR", "ORCL", "SMCI", "CRWV",
    "GOOGL", "META", "NFLX",
    "BLK", "JPM",
    "LLY", "UNH", "TEM",
    "LMT", "GE",
}

AVGO_CIK = "0001730168"


def _require_universe_membership_csv() -> None:
    if not UNIVERSE_MEMBERSHIP_CSV.exists():
        pytest.skip(
            f"{UNIVERSE_MEMBERSHIP_CSV} not seeded yet -- run "
            f"scripts/seed_universe_membership.py first."
        )


@pytest.fixture
def real_rows():
    _require_universe_membership_csv()
    return read_universe_membership_csv(UNIVERSE_MEMBERSHIP_CSV)


@pytest.fixture
def real_issuer_rows():
    return read_issuer_identity_csv(REFERENCE_DIR / "issuer_identity.csv")


@pytest.fixture
def real_security_rows():
    return read_security_identity_csv(REFERENCE_DIR / "security_identity.csv")


# ============================================================
# read + validate against the real reference data
# ============================================================

def test_real_universe_membership_csv_has_all_21_watchlist_tickers(real_rows):
    watchlist_tickers = {
        row["ticker"] for row in real_rows if row["universe_type"] == "WATCHLIST"
    }
    assert watchlist_tickers == EXPECTED_WATCHLIST_TICKERS


def test_real_universe_membership_csv_passes_validate_universe_membership_data(
    real_rows, real_issuer_rows, real_security_rows
):
    failures = validate_universe_membership_data(real_issuer_rows, real_security_rows, real_rows)
    assert failures == []


# ============================================================
# AVGO dual membership (real data)
# ============================================================

def test_real_avgo_has_both_watchlist_and_core_active_rows(real_rows):
    avgo_rows = [row for row in real_rows if row["cik"] == AVGO_CIK]
    universe_types = {row["universe_type"] for row in avgo_rows}
    assert universe_types == {"WATCHLIST", "CORE"}
    assert all(row["membership_status"] == "ACTIVE" for row in avgo_rows)


def test_real_avgo_watchlist_row_is_legacy_seed_and_core_row_is_manual_review(real_rows):
    avgo_rows = {row["universe_type"]: row for row in real_rows if row["cik"] == AVGO_CIK}
    assert avgo_rows["WATCHLIST"]["membership_source"] == "LEGACY_SEED"
    assert avgo_rows["CORE"]["membership_source"] == "MANUAL_REVIEW"


# ============================================================
# CIK cross-check (real data)
# ============================================================

def test_real_rows_all_have_a_cik_present_in_issuer_identity(real_rows, real_issuer_rows):
    issuer_ciks = {row["cik"] for row in real_issuer_rows}
    for row in real_rows:
        assert row["cik"] in issuer_ciks, f"{row['ticker']} ({row['cik']}) missing from issuer_identity.csv"


def test_real_rows_ticker_cik_pairs_all_exist_in_security_identity(real_rows, real_security_rows):
    security_pairs = {(row["ticker"], row["cik"]) for row in real_security_rows}
    for row in real_rows:
        assert (row["ticker"], row["cik"]) in security_pairs, (
            f"({row['ticker']}, {row['cik']}) not found together in security_identity.csv"
        )


# ============================================================
# PIT lookup against real data -- the exact AVGO CORE example
# confirmed by ChatGPT's 2026-09-21 message
# ============================================================

def test_real_avgo_core_lookup_not_yet_available_on_promotion_decision_date(real_rows):
    # effective_from=2026-09-20 (the real Core-promotion decision date)
    # but membership_available_date=2026-09-21 (the real seed date) --
    # a lookup AT the decision date itself must be NOT_YET_AVAILABLE, per
    # ChatGPT's explicit confirmation this is the intended
    # look-ahead-bias-prevention behavior, not a bug.
    result = get_universe_membership_as_of(AVGO_CIK, "CORE", "2026-09-20", real_rows)
    assert result["lookup_status"] == LOOKUP_NOT_YET_AVAILABLE


def test_real_avgo_core_lookup_ok_on_seed_date(real_rows):
    result = get_universe_membership_as_of(AVGO_CIK, "CORE", "2026-09-21", real_rows)
    assert result["lookup_status"] == LOOKUP_OK


def test_real_avgo_watchlist_lookup_ok_independently_of_core_lookup(real_rows):
    result = get_universe_membership_as_of(AVGO_CIK, "WATCHLIST", "2026-09-21", real_rows)
    assert result["lookup_status"] == LOOKUP_OK


@pytest.mark.parametrize("ticker", sorted(EXPECTED_WATCHLIST_TICKERS - {"AVGO"}))
def test_real_non_avgo_watchlist_tickers_have_no_core_membership(ticker, real_rows, real_security_rows):
    # Only AVGO should have a CORE row this round -- every other legacy
    # ticker is WATCHLIST-only (classification != automatic promotion,
    # per ChatGPT's 2026-09-21 item 4/item 3 instructions).
    security_pairs = {row["ticker"]: row["cik"] for row in real_security_rows}
    cik = security_pairs[ticker]
    result = get_universe_membership_as_of(cik, "CORE", "2026-09-21", real_rows)
    assert result["lookup_status"] != LOOKUP_OK

