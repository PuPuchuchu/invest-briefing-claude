import json
from pathlib import Path

import pytest

from src.fundamentals.sec_historical import (
    SCHEMA_VERSION,
    CONCEPT_MAP,
    DURATION_METRICS,
    INSTANT_METRICS,
    RECONSTRUCTABLE_QUARTERLY_METRICS,
    validate_companyfacts,
    get_concept_data,
    get_observations,
    find_best_concept,
    extract_annual_history,
    extract_instant_history,
    extract_quarterly_history,
    extract_historical_data,
    validate_historical_data,
    save_historical_data,
)


# ============================================================
# SYNTHETIC FIXTURE
# ============================================================

def make_observation(
    value,
    start=None,
    end="2025-12-31",
    filed="2026-02-01",
    form="10-K",
    fy=2025,
    fp="FY",
    frame=None,
    accn="0000000000-26-000001",
    unit="USD",
):
    observation = {
        "val": value,
        "unit": unit,
        "filed": filed,
        "form": form,
        "fy": fy,
        "fp": fp,
        "accn": accn,
    }

    if start is not None:
        observation["start"] = start

    if end is not None:
        observation["end"] = end

    if frame is not None:
        observation["frame"] = frame

    return observation, unit


def build_synthetic_companyfacts():
    """
    Synthetic Company Facts fixture.

    Revenue:
        FY2024 = 100
        Q1     = 10
        H1     = 25
        9M     = 45
        FY     = 70

    Therefore:
        Q1 = 10
        Q2 = 25 - 10 = 15
        Q3 = 45 - 25 = 20
        Q4 = 70 - 45 = 25
    """

    def records_to_units(records):
        result = {}

        for observation, unit in records:
            result.setdefault(unit, []).append(observation)

        return result

    revenue_records = [
        # FY2024
        make_observation(
            100,
            start="2024-01-01",
            end="2024-12-31",
            filed="2025-02-01",
            form="10-K",
            fy=2024,
            fp="FY",
            accn="0000000000-25-000001",
        ),

        # FY2025 Q1
        make_observation(
            10,
            start="2025-01-01",
            end="2025-03-31",
            filed="2025-05-01",
            form="10-Q",
            fy=2025,
            fp="Q1",
            frame="CY2025Q1",
            accn="0000000000-25-000002",
        ),

        # FY2025 H1
        make_observation(
            25,
            start="2025-01-01",
            end="2025-06-30",
            filed="2025-08-01",
            form="10-Q",
            fy=2025,
            fp="Q2",
            frame="CY2025Q2",
            accn="0000000000-25-000003",
        ),

        # FY2025 9M
        make_observation(
            45,
            start="2025-01-01",
            end="2025-09-30",
            filed="2025-11-01",
            form="10-Q",
            fy=2025,
            fp="Q3",
            frame="CY2025Q3",
            accn="0000000000-25-000004",
        ),

        # FY2025
        make_observation(
            70,
            start="2025-01-01",
            end="2025-12-31",
            filed="2026-02-01",
            form="10-K",
            fy=2025,
            fp="FY",
            frame="CY2025",
            accn="0000000000-26-000001",
        ),
    ]

    net_income_records = [
        # FY2024
        make_observation(
            50,
            start="2024-01-01",
            end="2024-12-31",
            filed="2025-02-01",
            form="10-K",
            fy=2024,
            fp="FY",
            accn="0000000000-25-000001",
        ),

        # Q1
        make_observation(
            1,
            start="2025-01-01",
            end="2025-03-31",
            filed="2025-05-01",
            form="10-Q",
            fy=2025,
            fp="Q1",
            frame="CY2025Q1",
            accn="0000000000-25-000002",
        ),

        # H1
        make_observation(
            3,
            start="2025-01-01",
            end="2025-06-30",
            filed="2025-08-01",
            form="10-Q",
            fy=2025,
            fp="Q2",
            frame="CY2025Q2",
            accn="0000000000-25-000003",
        ),

        # 9M
        make_observation(
            6,
            start="2025-01-01",
            end="2025-09-30",
            filed="2025-11-01",
            form="10-Q",
            fy=2025,
            fp="Q3",
            frame="CY2025Q3",
            accn="0000000000-25-000004",
        ),

        # FY
        make_observation(
            10,
            start="2025-01-01",
            end="2025-12-31",
            filed="2026-02-01",
            form="10-K",
            fy=2025,
            fp="FY",
            frame="CY2025",
            accn="0000000000-26-000001",
        ),
    ]

    # Direct quarterly EPS observations.
    #
    # IMPORTANT:
    # EPS is intentionally provided as standalone quarters.
    # The historical extractor must never calculate:
    #
    #     H1 EPS - Q1 EPS
    #
    # because EPS is not additive.
    eps_records = [
        make_observation(
            0.50,
            start="2025-01-01",
            end="2025-03-31",
            filed="2025-05-01",
            form="10-Q",
            fy=2025,
            fp="Q1",
            frame="CY2025Q1",
            accn="0000000000-25-000002",
            unit="USD/shares",
        ),

        make_observation(
            0.75,
            start="2025-04-01",
            end="2025-06-30",
            filed="2025-08-01",
            form="10-Q",
            fy=2025,
            fp="Q2",
            frame="CY2025Q2",
            accn="0000000000-25-000003",
            unit="USD/shares",
        ),

        make_observation(
            1.00,
            start="2025-07-01",
            end="2025-09-30",
            filed="2025-11-01",
            form="10-Q",
            fy=2025,
            fp="Q3",
            frame="CY2025Q3",
            accn="0000000000-25-000004",
            unit="USD/shares",
        ),

        make_observation(
            1.25,
            start="2025-10-01",
            end="2025-12-31",
            filed="2026-02-01",
            form="10-K",
            fy=2025,
            fp="FY",
            frame="CY2025Q4",
            accn="0000000000-26-000001",
            unit="USD/shares",
        ),
    ]

    operating_income_records = [
        make_observation(
            20,
            start="2025-01-01",
            end="2025-03-31",
            filed="2025-05-01",
            form="10-Q",
            fy=2025,
            fp="Q1",
            frame="CY2025Q1",
            accn="0000000000-25-000002",
        ),
        make_observation(
            45,
            start="2025-01-01",
            end="2025-06-30",
            filed="2025-08-01",
            form="10-Q",
            fy=2025,
            fp="Q2",
            frame="CY2025Q2",
            accn="0000000000-25-000003",
        ),
        make_observation(
            65,
            start="2025-01-01",
            end="2025-09-30",
            filed="2025-11-01",
            form="10-Q",
            fy=2025,
            fp="Q3",
            frame="CY2025Q3",
            accn="0000000000-25-000004",
        ),
        make_observation(
            90,
            start="2025-01-01",
            end="2025-12-31",
            filed="2026-02-01",
            form="10-K",
            fy=2025,
            fp="FY",
            frame="CY2025",
            accn="0000000000-26-000001",
        ),
    ]

    cfo_records = [
        make_observation(
            12,
            start="2025-01-01",
            end="2025-03-31",
            filed="2025-05-01",
            form="10-Q",
            fy=2025,
            fp="Q1",
            frame="CY2025Q1",
            accn="0000000000-25-000002",
        ),
        make_observation(
            30,
            start="2025-01-01",
            end="2025-06-30",
            filed="2025-08-01",
            form="10-Q",
            fy=2025,
            fp="Q2",
            frame="CY2025Q2",
            accn="0000000000-25-000003",
        ),
        make_observation(
            51,
            start="2025-01-01",
            end="2025-09-30",
            filed="2025-11-01",
            form="10-Q",
            fy=2025,
            fp="Q3",
            frame="CY2025Q3",
            accn="0000000000-25-000004",
        ),
        make_observation(
            70,
            start="2025-01-01",
            end="2025-12-31",
            filed="2026-02-01",
            form="10-K",
            fy=2025,
            fp="FY",
            frame="CY2025",
            accn="0000000000-26-000001",
        ),
    ]

    capex_records = [
        make_observation(
            -5,
            start="2025-01-01",
            end="2025-03-31",
            filed="2025-05-01",
            form="10-Q",
            fy=2025,
            fp="Q1",
            frame="CY2025Q1",
            accn="0000000000-25-000002",
        ),
        make_observation(
            -11,
            start="2025-01-01",
            end="2025-06-30",
            filed="2025-08-01",
            form="10-Q",
            fy=2025,
            fp="Q2",
            frame="CY2025Q2",
            accn="0000000000-25-000003",
        ),
        make_observation(
            -17,
            start="2025-01-01",
            end="2025-09-30",
            filed="2025-11-01",
            form="10-Q",
            fy=2025,
            fp="Q3",
            frame="CY2025Q3",
            accn="0000000000-25-000004",
        ),
        make_observation(
            -24,
            start="2025-01-01",
            end="2025-12-31",
            filed="2026-02-01",
            form="10-K",
            fy=2025,
            fp="FY",
            frame="CY2025",
            accn="0000000000-26-000001",
        ),
    ]

    cash_records = [
        # Same period, older filing
        make_observation(
            80,
            end="2025-12-31",
            start=None,
            filed="2026-01-15",
            form="10-K",
            fy=2025,
            fp="FY",
            accn="0000000000-26-000000",
        ),

        # Same period, newer filing -> should win
        make_observation(
            85,
            end="2025-12-31",
            start=None,
            filed="2026-02-01",
            form="10-K",
            fy=2025,
            fp="FY",
            accn="0000000000-26-000001",
        ),

        # Newer period
        make_observation(
            90,
            end="2026-03-31",
            start=None,
            filed="2026-05-01",
            form="10-Q",
            fy=2026,
            fp="Q1",
            accn="0000000000-26-000002",
        ),
    ]

    shares_records = [
        make_observation(
            1000,
            end="2025-12-31",
            start=None,
            filed="2026-02-01",
            form="10-K",
            fy=2025,
            fp="FY",
            accn="0000000000-26-000001",
            unit="shares",
        ),
        make_observation(
            1020,
            end="2026-03-31",
            start=None,
            filed="2026-05-01",
            form="10-Q",
            fy=2026,
            fp="Q1",
            accn="0000000000-26-000002",
            unit="shares",
        ),
    ]

    data = {
        "cik": "0000000000",
        "entityName": "SYNTHETIC TEST COMPANY",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue",
                    "units": records_to_units(
                        revenue_records
                    ),
                },
                "NetIncomeLoss": {
                    "label": "Net Income",
                    "units": records_to_units(
                        net_income_records
                    ),
                },
                "EarningsPerShareDiluted": {
                    "label": "Diluted EPS",
                    "units": records_to_units(
                        eps_records
                    ),
                },
                "OperatingIncomeLoss": {
                    "label": "Operating Income",
                    "units": records_to_units(
                        operating_income_records
                    ),
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "label": "CFO",
                    "units": records_to_units(
                        cfo_records
                    ),
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "label": "Capital Expenditures",
                    "units": records_to_units(
                        capex_records
                    ),
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "label": "Cash",
                    "units": records_to_units(
                        cash_records
                    ),
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "label": "Shares Outstanding",
                    "units": records_to_units(
                        shares_records
                    ),
                },
            },
        },
    }

    return data


@pytest.fixture
def synthetic_companyfacts():
    return build_synthetic_companyfacts()


# ============================================================
# BASIC VALIDATION
# ============================================================

def test_validate_companyfacts(synthetic_companyfacts):
    assert validate_companyfacts(
        synthetic_companyfacts
    ) is True


def test_validate_companyfacts_invalid():
    with pytest.raises(ValueError):
        validate_companyfacts(None)

    with pytest.raises(ValueError):
        validate_companyfacts({})

    with pytest.raises(ValueError):
        validate_companyfacts(
            {
                "entityName": "TEST",
                "facts": [],
            }
        )


# ============================================================
# RAW CONCEPT HELPERS
# ============================================================

def test_get_concept_data(synthetic_companyfacts):
    concept = get_concept_data(
        synthetic_companyfacts,
        "us-gaap",
        "NetIncomeLoss",
    )

    assert concept is not None
    assert "units" in concept


def test_get_concept_data_missing(synthetic_companyfacts):
    concept = get_concept_data(
        synthetic_companyfacts,
        "us-gaap",
        "DoesNotExist",
    )

    assert concept is None


def test_get_observations(synthetic_companyfacts):
    concept = get_concept_data(
        synthetic_companyfacts,
        "us-gaap",
        "NetIncomeLoss",
    )

    observations = get_observations(concept)

    assert len(observations) > 0

    for observation in observations:
        assert "val" in observation
        assert "unit" in observation


# ============================================================
# CONCEPT SELECTION
# ============================================================

def test_find_best_concept_duration(
    synthetic_companyfacts,
):
    result = find_best_concept(
        synthetic_companyfacts,
        "revenue",
        duration=True,
    )

    assert result is not None
    assert result["namespace"] == "us-gaap"
    assert (
        result["concept"]
        == "RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    assert len(result["observations"]) > 0


def test_find_best_concept_instant(
    synthetic_companyfacts,
):
    result = find_best_concept(
        synthetic_companyfacts,
        "cash",
        instant=True,
    )

    assert result is not None
    assert result["namespace"] == "us-gaap"
    assert (
        result["concept"]
        == "CashAndCashEquivalentsAtCarryingValue"
    )


def test_find_best_concept_missing(
    synthetic_companyfacts,
):
    result = find_best_concept(
        synthetic_companyfacts,
        "not_a_real_metric",
        duration=True,
    )

    assert result is None


# ============================================================
# ANNUAL HISTORY
# ============================================================

def test_extract_annual_history(
    synthetic_companyfacts,
):
    result = extract_annual_history(
        synthetic_companyfacts,
        "revenue",
    )

    assert result["status"] == "OK"
    assert result["metric"] == "revenue"
    assert len(result["annual"]) == 2

    latest = result["annual"][-1]

    assert latest["period_type"] == "annual"
    assert latest["period_start"] == "2025-01-01"
    assert latest["period_end"] == "2025-12-31"
    assert latest["value"] == 70
    assert latest["fy"] == 2025
    assert latest["fp"] == "FY"
    assert latest["form"] == "10-K"
    assert latest["derived"] is False


def test_annual_history_sorted_oldest_to_newest(
    synthetic_companyfacts,
):
    result = extract_annual_history(
        synthetic_companyfacts,
        "revenue",
    )

    annual = result["annual"]

    assert annual[0]["period_end"] == "2024-12-31"
    assert annual[1]["period_end"] == "2025-12-31"


# ============================================================
# Q1
# ============================================================

def test_q1_standalone(
    synthetic_companyfacts,
):
    result = extract_quarterly_history(
        synthetic_companyfacts,
        "revenue",
    )

    q1 = next(
        record
        for record in result["quarterly"]
        if record["quarter"] == "Q1"
    )

    assert q1["value"] == 10
    assert q1["period_start"] == "2025-01-01"
    assert q1["period_end"] == "2025-03-31"
    assert q1.get("derived", False) is False


# ============================================================
# Q2
# ============================================================

def test_q2_reconstructed_from_h1_minus_q1(
    synthetic_companyfacts,
):
    result = extract_quarterly_history(
        synthetic_companyfacts,
        "revenue",
    )

    q2 = next(
        record
        for record in result["quarterly"]
        if record["quarter"] == "Q2"
    )

    assert q2["value"] == 15
    assert q2["derived"] is True

    assert q2["period_start"] == "2025-04-01"
    assert q2["period_end"] == "2025-06-30"

    assert len(q2["source_observations"]) == 2


# ============================================================
# Q3
# ============================================================

def test_q3_reconstructed_from_9m_minus_h1(
    synthetic_companyfacts,
):
    result = extract_quarterly_history(
        synthetic_companyfacts,
        "revenue",
    )

    q3 = next(
        record
        for record in result["quarterly"]
        if record["quarter"] == "Q3"
    )

    assert q3["value"] == 20
    assert q3["derived"] is True

    assert q3["period_start"] == "2025-07-01"
    assert q3["period_end"] == "2025-09-30"

    assert len(q3["source_observations"]) == 2


# ============================================================
# Q4
# ============================================================

def test_q4_reconstructed_from_fy_minus_9m(
    synthetic_companyfacts,
):
    result = extract_quarterly_history(
        synthetic_companyfacts,
        "revenue",
    )

    q4 = next(
        record
        for record in result["quarterly"]
        if record["quarter"] == "Q4"
    )

    assert q4["value"] == 25
    assert q4["derived"] is True
    assert q4["quarter"] == "Q4"

    # Critical regression test:
    # Q4 must begin immediately after 9M ends.
    assert q4["period_start"] == "2025-10-01"
    assert q4["period_end"] == "2025-12-31"

    assert len(q4["source_observations"]) == 2


# ============================================================
# DIRECT QUARTER PREFERENCE
# ============================================================

def test_direct_quarter_is_preferred_over_derived(
    synthetic_companyfacts,
):
    data = json.loads(
        json.dumps(synthetic_companyfacts)
    )

    revenue_units = data["facts"]["us-gaap"][
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    ]["units"]["USD"]

    # Add direct Q2 observation.
    revenue_units.append(
        {
            "val": 16,
            "unit": "USD",
            "start": "2025-04-01",
            "end": "2025-06-30",
            "filed": "2025-08-15",
            "form": "10-Q",
            "fy": 2025,
            "fp": "Q2",
            "frame": "CY2025Q2",
            "accn": "0000000000-25-000010",
        }
    )

    result = extract_quarterly_history(
        data,
        "revenue",
    )

    q2 = next(
        record
        for record in result["quarterly"]
        if record["quarter"] == "Q2"
    )

    assert q2["value"] == 16
    assert q2["derived"] is False


# ============================================================
# DUPLICATE PERIOD / LATEST FILING
# ============================================================

def test_duplicate_period_uses_latest_filing(
    synthetic_companyfacts,
):
    data = json.loads(
        json.dumps(synthetic_companyfacts)
    )

    revenue_units = data["facts"]["us-gaap"][
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    ]["units"]["USD"]

    revenue_units.extend(
        [
            {
                "val": 110,
                "unit": "USD",
                "start": "2024-01-01",
                "end": "2024-12-31",
                "filed": "2025-02-01",
                "form": "10-K",
                "fy": 2024,
                "fp": "FY",
                "accn": "0000000000-25-000001",
            },
            {
                "val": 111,
                "unit": "USD",
                "start": "2024-01-01",
                "end": "2024-12-31",
                "filed": "2025-03-01",
                "form": "10-K",
                "fy": 2024,
                "fp": "FY",
                "accn": "0000000000-25-000005",
            },
        ]
    )

    result = extract_annual_history(
        data,
        "revenue",
    )

    annual_2024 = next(
        record
        for record in result["annual"]
        if record["period_end"] == "2024-12-31"
    )

    assert annual_2024["value"] == 111
    assert annual_2024["filing_date"] == "2025-03-01"


# ============================================================
# EPS SPECIAL RULE
# ============================================================

def test_eps_uses_direct_quarterly_observations(
    synthetic_companyfacts,
):
    result = extract_quarterly_history(
        synthetic_companyfacts,
        "diluted_eps",
    )

    assert result["status"] == "OK"

    quarters = {
        record["quarter"]: record
        for record in result["quarterly"]
    }

    assert quarters["Q1"]["value"] == 0.50
    assert quarters["Q2"]["value"] == 0.75
    assert quarters["Q3"]["value"] == 1.00
    assert quarters["Q4"]["value"] == 1.25

    for record in quarters.values():
        assert record["derived"] is False


def test_eps_is_not_reconstructed_from_ytd(
    synthetic_companyfacts,
):
    data = json.loads(
        json.dumps(synthetic_companyfacts)
    )

    eps_units = data["facts"]["us-gaap"][
        "EarningsPerShareDiluted"
    ]["units"]["USD/shares"]

    # Remove direct Q2/Q3/FY observations.
    eps_units[:] = [
        observation
        for observation in eps_units
        if observation.get("fp") == "Q1"
    ]

    # Add cumulative EPS observations.
    eps_units.extend(
        [
            {
                "val": 0.90,
                "unit": "USD/shares",
                "start": "2025-01-01",
                "end": "2025-06-30",
                "filed": "2025-08-01",
                "form": "10-Q",
                "fy": 2025,
                "fp": "Q2",
                "frame": "CY2025H1",
                "accn": "0000000000-25-000003",
            },
            {
                "val": 1.80,
                "unit": "USD/shares",
                "start": "2025-01-01",
                "end": "2025-09-30",
                "filed": "2025-11-01",
                "form": "10-Q",
                "fy": 2025,
                "fp": "Q3",
                "frame": "CY2025Q3",
                "accn": "0000000000-25-000004",
            },
            {
                "val": 2.50,
                "unit": "USD/shares",
                "start": "2025-01-01",
                "end": "2025-12-31",
                "filed": "2026-02-01",
                "form": "10-K",
                "fy": 2025,
                "fp": "FY",
                "frame": "CY2025",
                "accn": "0000000000-26-000001",
            },
        ]
    )

    result = extract_quarterly_history(
        data,
        "diluted_eps",
    )

    quarters = result["quarterly"]

    # Only Q1 should remain.
    assert len(quarters) == 1
    assert quarters[0]["quarter"] == "Q1"
    assert quarters[0]["value"] == 0.50


def test_eps_missing_without_direct_quarter(
    synthetic_companyfacts,
):
    data = json.loads(
        json.dumps(synthetic_companyfacts)
    )

    eps_units = data["facts"]["us-gaap"][
        "EarningsPerShareDiluted"
    ]["units"]["USD/shares"]

    eps_units[:] = []

    result = extract_quarterly_history(
        data,
        "diluted_eps",
    )

    assert result["status"] == "MISSING"
    assert result["quarterly"] == []
    assert "EPS reconstruction is disabled" in result["reason"]


# ============================================================
# UNIT CONSISTENCY
# ============================================================

def test_unit_mismatch_prevents_reconstruction(
    synthetic_companyfacts,
):
    data = json.loads(
        json.dumps(synthetic_companyfacts)
    )

    revenue = data["facts"]["us-gaap"][
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    ]

    # Move H1 to a different unit.
    for observation in revenue["units"]["USD"]:
        if (
            observation.get("fp") == "Q2"
            and observation.get("fy") == 2025
        ):
            observation["unit"] = "EUR"

    # Rebuild units so H1 is actually in a separate unit.
    original = revenue["units"]["USD"]

    usd_records = []
    eur_records = []

    for observation in original:
        if (
            observation.get("fp") == "Q2"
            and observation.get("fy") == 2025
        ):
            eur_records.append(observation)
        else:
            usd_records.append(observation)

    revenue["units"] = {
        "USD": usd_records,
        "EUR": eur_records,
    }

    result = extract_quarterly_history(
        data,
        "revenue",
    )

    q2_records = [
        record
        for record in result["quarterly"]
        if record["quarter"] == "Q2"
    ]

    # No invalid cross-unit subtraction should be produced.
    assert q2_records == []


# ============================================================
# INSTANT HISTORY
# ============================================================

def test_instant_history_latest_filing(
    synthetic_companyfacts,
):
    result = extract_instant_history(
        synthetic_companyfacts,
        "cash",
    )

    assert result["status"] == "OK"

    records = result["instant"]

    assert len(records) == 2

    dec_2025 = next(
        record
        for record in records
        if record["period_end"] == "2025-12-31"
    )

    assert dec_2025["value"] == 85
    assert dec_2025["filing_date"] == "2026-02-01"


def test_instant_history_keeps_multiple_period_ends(
    synthetic_companyfacts,
):
    result = extract_instant_history(
        synthetic_companyfacts,
        "cash",
    )

    period_ends = [
        record["period_end"]
        for record in result["instant"]
    ]

    assert "2025-12-31" in period_ends
    assert "2026-03-31" in period_ends


def test_shares_instant_history(
    synthetic_companyfacts,
):
    result = extract_instant_history(
        synthetic_companyfacts,
        "shares_outstanding",
    )

    assert result["status"] == "OK"

    records = result["instant"]

    assert len(records) == 2

    latest = records[-1]

    assert latest["period_end"] == "2026-03-31"
    assert latest["value"] == 1020
    assert latest["unit"] == "shares"


# ============================================================
# MISSING CONCEPT
# ============================================================

def test_missing_concept_is_not_zero(
    synthetic_companyfacts,
):
    data = json.loads(
        json.dumps(synthetic_companyfacts)
    )

    del data["facts"]["us-gaap"][
        "OperatingIncomeLoss"
    ]

    result = extract_quarterly_history(
        data,
        "operating_income",
    )

    assert result["status"] == "MISSING"
    assert result["quarterly"] == []


# ============================================================
# FULL HISTORICAL EXTRACTION
# ============================================================

def test_extract_historical_data(
    synthetic_companyfacts,
):
    result = extract_historical_data(
        ticker="TEST",
        data=synthetic_companyfacts,
        company_type="NON_FINANCIAL",
    )

    assert result["schema_version"] == SCHEMA_VERSION
    assert result["ticker"] == "TEST"
    assert (
        result["entity_name"]
        == "SYNTHETIC TEST COMPANY"
    )
    assert result["cik"] == "0000000000"
    assert (
        result["company_type"]
        == "NON_FINANCIAL"
    )

    assert "history" in result

    for metric in DURATION_METRICS:
        assert metric in result["history"]
        assert "annual" in result["history"][metric]
        assert "quarterly" in result["history"][metric]

    for metric in INSTANT_METRICS:
        assert metric in result["history"]
        assert "instant" in result["history"][metric]


def test_full_revenue_history(
    synthetic_companyfacts,
):
    result = extract_historical_data(
        ticker="TEST",
        data=synthetic_companyfacts,
        company_type="NON_FINANCIAL",
    )

    revenue = result["history"]["revenue"]

    assert len(revenue["annual"]) == 2
    assert len(revenue["quarterly"]) == 4

    quarterly_values = {
        record["quarter"]: record["value"]
        for record in revenue["quarterly"]
    }

    assert quarterly_values["Q1"] == 10
    assert quarterly_values["Q2"] == 15
    assert quarterly_values["Q3"] == 20
    assert quarterly_values["Q4"] == 25


def test_full_cash_history(
    synthetic_companyfacts,
):
    result = extract_historical_data(
        ticker="TEST",
        data=synthetic_companyfacts,
        company_type="NON_FINANCIAL",
    )

    cash = result["history"]["cash"]

    assert len(cash["instant"]) == 2

    latest = cash["instant"][-1]

    assert latest["period_end"] == "2026-03-31"
    assert latest["value"] == 90


# ============================================================
# HISTORICAL VALIDATION
# ============================================================

def test_validate_historical_data(
    synthetic_companyfacts,
):
    result = extract_historical_data(
        ticker="TEST",
        data=synthetic_companyfacts,
        company_type="NON_FINANCIAL",
    )

    assert validate_historical_data(
        result
    ) is True


def test_validate_historical_data_rejects_invalid_schema(
    synthetic_companyfacts,
):
    result = extract_historical_data(
        ticker="TEST",
        data=synthetic_companyfacts,
        company_type="NON_FINANCIAL",
    )

    result["schema_version"] = "wrong_version"

    with pytest.raises(ValueError):
        validate_historical_data(result)


def test_validate_historical_data_rejects_missing_period_end(
    synthetic_companyfacts,
):
    result = extract_historical_data(
        ticker="TEST",
        data=synthetic_companyfacts,
        company_type="NON_FINANCIAL",
    )

    result["history"]["revenue"]["annual"][0][
        "period_end"
    ] = None

    with pytest.raises(ValueError):
        validate_historical_data(result)


def test_validate_historical_data_rejects_derived_without_provenance(
    synthetic_companyfacts,
):
    result = extract_historical_data(
        ticker="TEST",
        data=synthetic_companyfacts,
        company_type="NON_FINANCIAL",
    )

    q4 = next(
        record
        for record in result["history"]["revenue"]["quarterly"]
        if record["quarter"] == "Q4"
    )

    q4.pop("source_observations", None)

    with pytest.raises(ValueError):
        validate_historical_data(result)


# ============================================================
# SAVE
# ============================================================

def test_save_historical_data(
    synthetic_companyfacts,
    tmp_path,
):
    result = extract_historical_data(
        ticker="TEST",
        data=synthetic_companyfacts,
        company_type="NON_FINANCIAL",
    )

    path = save_historical_data(
        ticker="TEST",
        historical_data=result,
        processed_dir=tmp_path,
    )

    assert path.exists()
    assert path.name == "TEST_historical.json"

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        saved = json.load(f)

    assert saved["ticker"] == "TEST"
    assert (
        saved["schema_version"]
        == SCHEMA_VERSION
    )


# ============================================================
# STRUCTURAL TESTS
# ============================================================

def test_reconstructable_metrics_are_expected():
    expected = {
        "revenue",
        "net_income",
        "operating_income",
        "cfo",
        "capex",
    }

    assert set(
        RECONSTRUCTABLE_QUARTERLY_METRICS
    ) == expected


def test_eps_is_not_reconstructable():
    assert (
        "diluted_eps"
        not in RECONSTRUCTABLE_QUARTERLY_METRICS
    )


def test_concept_map_contains_all_metrics():
    expected_metrics = set(
        DURATION_METRICS
        + INSTANT_METRICS
    )

    assert expected_metrics.issubset(
        set(CONCEPT_MAP.keys())
    )


def test_revenue_concept_map_includes_salesrevenuenet():
    """
    Regression test (2026-09-18): CONCEPT_MAP's revenue candidates
    previously only listed RevenueFromContractWithCustomerExcludingAssessedTax
    and Revenues, omitting SalesRevenueNet -- the tag commonly used by
    filings from before ~2018. src/fundamentals/sec_normalizer.py's own
    revenue candidate list has always included it
    (["RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet", "Revenues"]), so a company tagging revenue only
    under SalesRevenueNet would silently resolve revenue in
    sec_normalizer.py but come back as missing here -- an inconsistency
    no existing test caught, since the two modules' concept-selection
    logic is fully independent and never cross-checked.
    """
    assert (
        "us-gaap",
        "SalesRevenueNet",
    ) in CONCEPT_MAP["revenue"]

    # Priority order must match sec_normalizer.py: the modern tag first,
    # the legacy SalesRevenueNet tag as a fallback before the generic
    # Revenues tag.
    revenue_candidates = CONCEPT_MAP["revenue"]
    assert revenue_candidates.index(
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax")
    ) < revenue_candidates.index(
        ("us-gaap", "SalesRevenueNet")
    ) < revenue_candidates.index(
        ("us-gaap", "Revenues")
    )


def test_find_best_concept_resolves_salesrevenuenet_only_company():
    """
    A company that only ever tagged revenue as SalesRevenueNet (no
    RevenueFromContractWithCustomerExcludingAssessedTax, no generic
    Revenues) must still resolve via find_best_concept(). Before the
    CONCEPT_MAP fix, this returned None -- a silent revenue-data loss
    for exactly the pre-2018-filer case this candidate list exists to
    handle.
    """
    observation, unit = make_observation(
        500,
        start="2016-01-01",
        end="2016-12-31",
        filed="2017-02-01",
        form="10-K",
        fy=2016,
        fp="FY",
        accn="0000000000-17-000001",
    )

    data = {
        "cik": 1,
        "entityName": "LEGACY TAG CO",
        "facts": {
            "us-gaap": {
                "SalesRevenueNet": {
                    "label": "Sales Revenue, Net",
                    "units": {
                        unit: [observation],
                    },
                },
            },
        },
    }

    result = find_best_concept(
        data,
        "revenue",
        duration=True,
    )

    assert result is not None
    assert result["concept"] == "SalesRevenueNet"
    assert result["observations"][0]["val"] == 500


def test_noncurrent_debt_concept_map_includes_longtermnotespayable():
    """
    Regression test (2026-09-18): CONCEPT_MAP's noncurrent_debt
    candidates previously only listed LongTermDebtNoncurrent.
    Confirmed against real SEC data that Oracle Corporation
    (CIK 0001341439) has zero observations under LongTermDebtNoncurrent
    (404 from data.sec.gov/api/xbrl/companyconcept/) and instead tags
    its noncurrent debt under LongTermNotesPayable (86 real
    observations spanning 2009-2026, e.g. $122.3B as of
    FY2026-05-31, filed 2026-06-22, form 10-K) -- a company relying
    solely on that tag would previously resolve noncurrent_debt as
    missing here even though the real data exists.
    """
    assert (
        "us-gaap",
        "LongTermNotesPayable",
    ) in CONCEPT_MAP["noncurrent_debt"]

    # LongTermDebtNoncurrent stays the primary (semantically precise)
    # candidate; LongTermNotesPayable is the fallback for companies
    # (like Oracle) that only tag the alternate concept.
    noncurrent_debt_candidates = CONCEPT_MAP["noncurrent_debt"]
    assert noncurrent_debt_candidates.index(
        ("us-gaap", "LongTermDebtNoncurrent")
    ) < noncurrent_debt_candidates.index(
        ("us-gaap", "LongTermNotesPayable")
    )


def test_find_best_concept_resolves_longtermnotespayable_only_company():
    """
    A company that only ever tagged noncurrent debt as
    LongTermNotesPayable (no LongTermDebtNoncurrent) must still
    resolve via find_best_concept(). Before the CONCEPT_MAP fix,
    this returned None -- the exact Oracle case confirmed against
    real SEC data (see test above).
    """
    observation, unit = make_observation(
        122_342_000_000,
        start=None,
        end="2026-05-31",
        filed="2026-06-22",
        form="10-K",
        fy=2026,
        fp="FY",
        accn="0000000000-26-000002",
    )

    data = {
        "cik": 2,
        "entityName": "ORACLE-LIKE CO",
        "facts": {
            "us-gaap": {
                "LongTermNotesPayable": {
                    "label": "Long-term Notes Payable",
                    "units": {
                        unit: [observation],
                    },
                },
            },
        },
    }

    result = find_best_concept(
        data,
        "noncurrent_debt",
        instant=True,
    )

    assert result is not None
    assert result["concept"] == "LongTermNotesPayable"
    assert result["observations"][0]["val"] == 122_342_000_000


# ============================================================
# OPTIONAL REAL SEC CACHE SMOKE TESTS
# ============================================================

def _real_cache_path(ticker):
    return Path(
        "data/raw/sec"
    ) / f"{ticker}_companyfacts.json"


@pytest.mark.parametrize(
    "ticker",
    [
        "NVDA",
        "LLY",
        "PLTR",
    ],
)
def test_real_sec_cache_smoke(
    ticker,
):
    """
    Optional smoke test.

    If the real SEC cache file does not exist,
    pytest skips the test.

    This prevents the normal unit-test suite from
    failing merely because real SEC data has not
    been downloaded yet.
    """

    path = _real_cache_path(ticker)

    if not path.exists():
        pytest.skip(
            f"Real SEC cache not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    validate_companyfacts(data)

    result = extract_historical_data(
        ticker=ticker,
        data=data,
        company_type="NON_FINANCIAL",
    )

    assert result["ticker"] == ticker
    assert result["schema_version"] == SCHEMA_VERSION
    assert "history" in result

    validate_historical_data(result)


@pytest.mark.parametrize(
    "ticker",
    [
        "NVDA",
        "LLY",
        "PLTR",
    ],
)
def test_real_sec_cache_revenue_history(
    ticker,
):
    path = _real_cache_path(ticker)

    if not path.exists():
        pytest.skip(
            f"Real SEC cache not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    result = extract_quarterly_history(
        data,
        "revenue",
    )

    assert result["status"] == "OK"

    quarterly = result["quarterly"]

    assert len(quarterly) > 0

    for record in quarterly:
        assert record["period_end"] is not None
        assert record["period_start"] is not None
        assert record["value"] is not None

        if record["derived"]:
            assert record[
                "source_observations"
            ]


@pytest.mark.parametrize(
    "ticker",
    [
        "NVDA",
        "LLY",
        "PLTR",
    ],
)
def test_real_sec_cache_eps_never_derived(
    ticker,
):
    path = _real_cache_path(ticker)

    if not path.exists():
        pytest.skip(
            f"Real SEC cache not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    result = extract_quarterly_history(
        data,
        "diluted_eps",
    )

    for record in result.get(
        "quarterly",
        [],
    ):
        assert record["derived"] is False
