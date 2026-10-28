"""Stockholders' equity extraction on real SEC companyfacts.

Two data sources, chosen by the SEC_RAW_DIR environment variable:

* Not set (default, used by the Full Test Suite): fixed slices of real SEC
  companyfacts in tests/fixtures/sec_equity/, built by
  scripts/build_sec_equity_fixtures.py. Their source artifact and sha256 are
  in PROVENANCE.json. The extracted result must equal the pinned values in
  expected_stockholders_equity.json exactly.
* Set to a directory of full TICKER_companyfacts.json files (used by the
  Stockholders Equity Coverage Test workflow after a live SEC fetch): the
  structural checks below run on live data; pinned values are not compared,
  because live filings change.

A missing file is a test failure, never a skip.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from src.fundamentals.sec_normalizer import normalize_company


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "sec_equity"
SLICE_SUFFIX = "_companyfacts_equity_slice.json"
LIVE_RAW_DIR = os.environ.get("SEC_RAW_DIR")
USING_FIXTURES = not LIVE_RAW_DIR

TARGET_TICKERS = [
    "MSFT", "AAPL", "NVDA", "AVGO", "INTC", "MU",
    "AMD", "PLTR", "ORCL", "SMCI", "CRWV",
]


# ============================================================
# ALLOWED SEC CONCEPTS
# ============================================================

ALLOWED_STOCKHOLDERS_EQUITY_CONCEPTS = {
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
}


# ============================================================
# HELPERS
# ============================================================

def companyfacts_path(ticker: str) -> Path:
    if USING_FIXTURES:
        return FIXTURE_DIR / f"{ticker}{SLICE_SUFFIX}"
    return Path(LIVE_RAW_DIR) / f"{ticker}_companyfacts.json"


def load_companyfacts(ticker: str) -> dict:
    path = companyfacts_path(ticker)

    if not path.exists():
        raise FileNotFoundError(
            f"SEC raw file not found: {path}"
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_target(ticker: str) -> dict:
    return normalize_company(
        ticker=ticker,
        data=load_companyfacts(ticker),
        company_type="NON_FINANCIAL",
    )


# ============================================================
# TEST: REAL COMPANY SEC COVERAGE
# ============================================================

@pytest.mark.parametrize("ticker", TARGET_TICKERS)
def test_stockholders_equity_real_company(ticker):
    """
    Verify real SEC CompanyFacts coverage for StockholdersEquity.

    This test validates:
    - Successful extraction
    - Numeric value
    - Period end
    - Filing date
    - SEC namespace
    - Accepted SEC Concept provenance

    This tests extraction/provenance,
    not valuation quality.
    """

    try:
        normalized = normalize_target(ticker)

    except FileNotFoundError as exc:
        pytest.fail(str(exc))

    metric = normalized["metrics"]["stockholders_equity"]

    assert metric["status"] == "OK", (
        f"{ticker}: status={metric.get('status')}, "
        f"reason={metric.get('reason')}"
    )

    assert isinstance(metric.get("value"), (int, float)), (
        f"{ticker}: non-numeric value={metric.get('value')!r}"
    )

    assert metric.get("period_end"), (
        f"{ticker}: missing period_end"
    )

    assert metric.get("filing_date"), (
        f"{ticker}: missing filing_date"
    )

    assert metric.get("namespace") == "us-gaap", (
        f"{ticker}: namespace={metric.get('namespace')}"
    )

    assert metric.get("concept") in (
        ALLOWED_STOCKHOLDERS_EQUITY_CONCEPTS
    ), (
        f"{ticker}: concept={metric.get('concept')}"
    )

    if USING_FIXTURES:
        expected = json.loads(
            (FIXTURE_DIR / "expected_stockholders_equity.json").read_text(encoding="utf-8")
        )[ticker]
        assert metric == expected, (
            f"{ticker}: extracted stockholders_equity differs from the pinned "
            f"result in expected_stockholders_equity.json"
        )


# ============================================================
# TEST: FIXTURE INTEGRITY
# ============================================================

@pytest.mark.skipif(not USING_FIXTURES, reason="live SEC_RAW_DIR run; fixtures not used")
def test_equity_fixtures_match_provenance():
    """Every slice exists, matches its recorded sha256, and contains only the
    recorded concepts. Covers every ticker the coverage test uses."""
    prov = json.loads((FIXTURE_DIR / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert set(prov["tickers"]) == set(TARGET_TICKERS)
    src = prov["source"]
    assert src["artifact_name"] and src["artifact_zip_sha256"] and src["artifact_created_utc"]
    for ticker, rec in prov["tickers"].items():
        data = (FIXTURE_DIR / rec["slice_file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == rec["slice_sha256"], ticker
        doc = json.loads(data)
        assert set(doc) <= {"cik", "entityName", "facts"}, ticker
        assert set(doc["facts"]) == {"us-gaap"}, ticker
        concepts = set(doc["facts"]["us-gaap"])
        assert concepts == set(rec["concepts_present"]), ticker
        assert concepts <= set(prov["kept_concepts"]), ticker
        assert "Assets" in concepts, f"{ticker}: freshness anchor missing"


# ============================================================
# TEST: PRINT COVERAGE RESULTS
# ============================================================

def test_print_stockholders_equity_coverage():
    print()
    print("=" * 100)
    print("REAL SEC STOCKHOLDERS' EQUITY COVERAGE")
    print("=" * 100)

    print(
        f"{'Ticker':<8}"
        f"{'Status':<12}"
        f"{'Value':>20}"
        f"{'Period End':<14}"
        f"{'Filed':<14}"
        f"{'Concept':<28}"
    )

    print("-" * 100)

    for ticker in TARGET_TICKERS:
        normalized = normalize_target(ticker)
        metric = normalized["metrics"]["stockholders_equity"]

        value = metric.get("value")

        value_text = (
            f"{value:,.0f}"
            if isinstance(value, (int, float))
            else str(value)
        )

        print(
            f"{ticker:<8}"
            f"{metric.get('status', ''):<12}"
            f"{value_text:>20}"
            f"{str(metric.get('period_end', '')):<14}"
            f"{str(metric.get('filing_date', '')):<14}"
            f"{str(metric.get('concept', '')):<28}"
        )

    print("=" * 100)
    print()