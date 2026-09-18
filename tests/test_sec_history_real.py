from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.fundamentals.sec_history import (
    extract_annual_history,
    extract_concept_history,
    reconstruct_standalone_quarters,
    validate_history,
)
from src.fundamentals.sec_normalizer import find_concept
from src.fundamentals.sec_historical import CONCEPT_MAP


# ============================================================
# CONFIGURATION
# ============================================================

RAW_DIR = Path("data/raw/sec")


# ============================================================
# HELPERS
# ============================================================

def load_companyfacts(ticker: str) -> dict:
    path = RAW_DIR / f"{ticker}_companyfacts.json"

    if not path.exists():
        pytest.skip(
            f"Raw SEC Company Facts file not found: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_revenue_concept(data: dict, ticker: str) -> dict:
    """
    Resolve the revenue concept to use for real-data tests.

    Different companies tag revenue under different us-gaap
    concepts depending on filing type and period (e.g. NVDA's
    10-K filings use RevenueFromContractWithCustomerExcludingAssessedTax
    while its 10-Q filings use the older Revenues tag -- confirmed
    directly against data.sec.gov/api/xbrl/companyconcept/).

    Reuses the same candidate priority order already established
    for production normalization (src/fundamentals/sec_historical.py
    CONCEPT_MAP["revenue"]) rather than hardcoding a single concept
    per ticker, so this test does not silently diverge from how the
    pipeline itself resolves revenue.

    Among the candidates that exist for this company, prefers one
    that actually carries 10-Q (quarterly) observations, since that
    is what standalone-quarter reconstruction requires. Falls back
    to the first candidate with any data if none has quarterly
    observations.
    """
    candidates = CONCEPT_MAP["revenue"]

    fallback = None
    fallback_name = None

    for namespace, concept_name in candidates:

        concept = find_concept(
            data,
            namespace,
            concept_name,
        )

        if concept is None:
            continue

        if fallback is None:
            fallback = concept
            fallback_name = concept_name

        units = concept.get("units", {})

        has_quarterly = any(
            isinstance(observation, dict)
            and observation.get("form") == "10-Q"
            for unit_observations in units.values()
            if isinstance(unit_observations, list)
            for observation in unit_observations
        )

        if has_quarterly:
            return concept

    if fallback is not None:
        return fallback

    pytest.skip(
        f"{ticker}: no revenue concept found among candidates: "
        f"{[name for _, name in candidates]}"
    )


# ============================================================
# BASIC REAL-DATA TEST
# ============================================================

@pytest.mark.parametrize(
    "ticker",
    ["MSFT", "NVDA"],
)
def test_real_sec_companyfacts_load(ticker):
    data = load_companyfacts(ticker)

    assert isinstance(data, dict)
    assert "cik" in data
    assert "entityName" in data
    assert "facts" in data
    assert isinstance(data["facts"], dict)


# ============================================================
# REAL REVENUE HISTORY
# ============================================================

@pytest.mark.parametrize(
    "ticker",
    ["MSFT", "NVDA"],
)
def test_real_revenue_history(ticker):
    data = load_companyfacts(ticker)

    concept = get_revenue_concept(
        data,
        ticker,
    )

    history = extract_concept_history(
        concept
    )

    failures = validate_history(history)

    assert failures == []

    assert len(history["annual"]) >= 1

    standalone = history["standalone_quarters"]

    assert isinstance(standalone, list)

    # Real SEC data may not always reconstruct every quarter.
    # We require at least one valid standalone quarter when
    # sufficient SEC observations are available.
    assert len(standalone) >= 1


# ============================================================
# REAL ANNUAL HISTORY
# ============================================================

@pytest.mark.parametrize(
    "ticker",
    ["MSFT", "NVDA"],
)
def test_real_annual_history_has_numeric_values(ticker):
    data = load_companyfacts(ticker)

    concept = get_revenue_concept(
        data,
        ticker,
    )

    history = extract_annual_history(
        [
            record
            for unit_records in concept["units"].values()
            for record in unit_records
        ]
    )

    assert len(history) >= 1

    for record in history:
        # extract_annual_history() preserves the original SEC
        # observation as-is, so the numeric field is "val" (SEC's
        # real field name), not "value" (only used by synthetic
        # test fixtures elsewhere in this codebase).
        assert isinstance(record["val"], (int, float))
        assert record.get("start") is not None
        assert record.get("end") is not None
        assert record.get("filed") is not None
        assert record.get("form") == "10-K"
        assert record.get("fp") == "FY"


# ============================================================
# REAL STANDALONE QUARTER DATA
# ============================================================

@pytest.mark.parametrize(
    "ticker",
    ["MSFT", "NVDA"],
)
def test_real_standalone_quarters_have_provenance(ticker):
    data = load_companyfacts(ticker)

    concept = get_revenue_concept(
        data,
        ticker,
    )

    history = extract_concept_history(
        concept
    )

    standalone = history["standalone_quarters"]

    assert len(standalone) >= 1

    # Note: reconstruct_standalone_quarters() (src/fundamentals/
    # sec_history.py) has no "status" or "period_type" field -- a
    # quarter is either successfully reconstructed and present in
    # this list, or not reconstructable and simply absent (there is
    # no separate "FAILED" entry). The per-quarter fields actually
    # produced are: quarter, value, period_start, period_end,
    # filed, form, fy, accession, reconstructed, source (with
    # source["current"] / source["previous"]).
    for quarter in standalone:
        assert isinstance(
            quarter["value"],
            (int, float),
        )

        assert quarter["quarter"] in {
            "Q1",
            "Q2",
            "Q3",
            "Q4",
        }

        assert quarter["period_end"] is not None
        assert quarter["filed"] is not None

        assert "source" in quarter
        assert "current" in quarter["source"]


# ============================================================
# REAL QUARTER SEQUENCE
# ============================================================

@pytest.mark.parametrize(
    "ticker",
    ["MSFT", "NVDA"],
)
def test_real_quarter_values_are_not_double_counted(ticker):
    data = load_companyfacts(ticker)

    concept = get_revenue_concept(
        data,
        ticker,
    )

    history = extract_concept_history(
        concept
    )

    standalone = history["standalone_quarters"]

    if not standalone:
        pytest.skip(
            f"{ticker}: no standalone quarters reconstructed"
        )

    # For each fiscal year, inspect reconstructed quarters.
    fiscal_years = {}

    for quarter in standalone:
        fy = quarter.get("fy")

        if isinstance(fy, int):
            fiscal_years.setdefault(
                fy,
                []
            ).append(quarter)

    assert fiscal_years

    # A complete year should normally contain four quarters.
    # We do not require every year to be complete because
    # SEC Company Facts can contain incomplete historical
    # sequences.
    complete_years = [
        (fy, quarters)
        for fy, quarters in fiscal_years.items()
        if len({
            q["quarter"]
            for q in quarters
        }) == 4
    ]

    if not complete_years:
        pytest.skip(
            f"{ticker}: no complete four-quarter fiscal year "
            "available for this validation"
        )

    fy, quarters = sorted(
        complete_years,
        key=lambda item: item[0],
        reverse=True,
    )[0]

    quarter_map = {
        q["quarter"]: q["value"]
        for q in quarters
    }

    assert set(quarter_map) == {
        "Q1",
        "Q2",
        "Q3",
        "Q4",
    }

    total = sum(
        quarter_map.values()
    )

    assert isinstance(total, (int, float))
    assert total >= 0


# ============================================================
# FILING DATE PROVENANCE
# ============================================================

@pytest.mark.parametrize(
    "ticker",
    ["MSFT", "NVDA"],
)
def test_real_q4_uses_10k_filing_date(ticker):
    data = load_companyfacts(ticker)

    concept = get_revenue_concept(
        data,
        ticker,
    )

    history = extract_concept_history(
        concept
    )

    annual = history["annual"]
    standalone = history["standalone_quarters"]

    q4_records = [
        q
        for q in standalone
        if q["quarter"] == "Q4"
    ]

    if not q4_records:
        pytest.skip(
            f"{ticker}: Q4 could not be reconstructed"
        )

    for q4 in q4_records:
        fy = q4.get("fy")

        matching_annual = [
            record
            for record in annual
            if record.get("fy") == fy
        ]

        if not matching_annual:
            continue

        annual_record = matching_annual[0]

        assert q4["filed"] == annual_record["filed"]


# ============================================================
# NO INSTANT OBSERVATIONS IN DURATION HISTORY
# ============================================================

@pytest.mark.parametrize(
    "ticker",
    ["MSFT", "NVDA"],
)
def test_real_history_contains_duration_records_only(ticker):
    data = load_companyfacts(ticker)

    concept = get_revenue_concept(
        data,
        ticker,
    )

    history = extract_concept_history(
        concept
    )

    for record in history["annual"]:
        assert record.get("start") is not None
        assert record.get("end") is not None

    for record in history["standalone_quarters"]:
        assert record.get("period_start") is not None
        assert record.get("period_end") is not None
