import json

from src.fundamentals.sec_normalizer import (
    validate_companyfacts,
    get_all_concepts,
    get_observations,
    is_annual_observation,
    is_instant_observation,
    is_duration_observation,
    sort_observations,
    select_latest_annual_observation,
    select_latest_instant_observation,
    find_concept,
    find_best_annual_concept,
    find_best_instant_concept,
    extract_metric,
    extract_instant_metric,
    calculate_total_debt,
    normalize_company,
    validate_normalized_data,
    save_normalized_data,
    load_raw_companyfacts,
    normalize_from_cache,
)


# ============================================================
# SYNTHETIC COMPANY FACTS FIXTURE
# ============================================================

def make_companyfacts_fixture():
    return {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "description": "Test revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 100000,
                                "accn": "0000000000-25-000001",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-02-15",
                                "frame": "CY2024",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                                "frame": "CY2025",
                            },
                        ]
                    },
                },
                "NetIncomeLoss": {
                    "label": "Net Income",
                    "description": "Test net income",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 20000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                                "frame": "CY2025",
                            }
                        ]
                    },
                },
                "EarningsPerShareDiluted": {
                    "label": "Diluted EPS",
                    "description": "Test diluted EPS",
                    "units": {
                        "USD/shares": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 2.50,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                                "frame": "CY2025",
                            }
                        ]
                    },
                },
                "OperatingIncomeLoss": {
                    "label": "Operating Income",
                    "description": "Test operating income",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 30000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                                "frame": "CY2025",
                            }
                        ]
                    },
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "label": "Operating Cash Flow",
                    "description": "Test CFO",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 25000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                                "frame": "CY2025",
                            }
                        ]
                    },
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "label": "Capital Expenditures",
                    "description": "Test capex",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": -5000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                                "frame": "CY2025",
                            }
                        ]
                    },
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "label": "Cash",
                    "description": "Test cash",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 50000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                                "frame": "CY2025",
                            }
                        ]
                    },
                },
                "ShortTermBorrowings": {
                    "label": "Current Debt",
                    "description": "Test current debt",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 10000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                                "frame": "CY2025",
                            }
                        ]
                    },
                },
                "LongTermDebtNoncurrent": {
                    "label": "Noncurrent Debt",
                    "description": "Test noncurrent debt",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 30000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                                "frame": "CY2025",
                            }
                        ]
                    },
                },
                # P/B valuation input.
                "StockholdersEquity": {
                    "label": "Stockholders' Equity",
                    "description": "Test stockholders' equity",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 80000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                                "frame": "CY2025",
                            }
                        ]
                    },
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "label": "Shares Outstanding",
                    "description": "Test shares outstanding",
                    "units": {
                        "shares": [
                            {
                                "end": "2025-12-31",
                                "val": 10000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                }
            },
        },
    }


# ============================================================
# BASIC VALIDATION
# ============================================================

def test_validate_companyfacts():
    data = make_companyfacts_fixture()
    assert validate_companyfacts(data) is None


def test_validate_companyfacts_missing_key():
    data = make_companyfacts_fixture()
    del data["facts"]

    try:
        validate_companyfacts(data)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "facts" in str(exc)


# ============================================================
# CONCEPT DISCOVERY
# ============================================================

def test_get_all_concepts():
    data = make_companyfacts_fixture()
    concepts = get_all_concepts(data)

    assert len(concepts) > 0

    names = {
        (item["namespace"], item["concept"])
        for item in concepts
    }

    assert ("us-gaap", "Revenues") in names
    assert ("us-gaap", "NetIncomeLoss") in names
    assert ("us-gaap", "StockholdersEquity") in names
    assert (
        "dei",
        "EntityCommonStockSharesOutstanding",
    ) in names


def test_find_concept():
    data = make_companyfacts_fixture()

    concept = find_concept(
        data,
        "us-gaap",
        "Revenues",
    )

    assert concept is not None
    assert "units" in concept


def test_find_concept_missing():
    data = make_companyfacts_fixture()

    concept = find_concept(
        data,
        "us-gaap",
        "DoesNotExist",
    )

    assert concept is None


# ============================================================
# OBSERVATION HANDLING
# ============================================================

def test_get_observations():
    data = make_companyfacts_fixture()

    concept = find_concept(
        data,
        "us-gaap",
        "Revenues",
    )

    observations = get_observations(concept)

    assert len(observations) == 2
    assert observations[0]["val"] == 100000
    assert observations[1]["val"] == 120000
    assert observations[0]["unit"] == "USD"


def test_observation_classification():
    data = make_companyfacts_fixture()

    revenue = find_concept(
        data,
        "us-gaap",
        "Revenues",
    )

    observations = get_observations(revenue)

    for obs in observations:
        assert is_duration_observation(obs)
        assert is_annual_observation(obs)
        assert not is_instant_observation(obs)


def test_instant_observation_classification():
    data = make_companyfacts_fixture()

    cash = find_concept(
        data,
        "us-gaap",
        "CashAndCashEquivalentsAtCarryingValue",
    )

    observations = get_observations(cash)

    assert len(observations) == 1
    assert is_instant_observation(observations[0])
    assert not is_duration_observation(observations[0])
    assert not is_annual_observation(observations[0])


# ============================================================
# OBSERVATION SELECTION
# ============================================================

def test_sort_observations():
    data = make_companyfacts_fixture()

    revenue = find_concept(
        data,
        "us-gaap",
        "Revenues",
    )

    sorted_obs = sort_observations(
        get_observations(revenue)
    )

    assert sorted_obs[0]["end"] == "2025-12-31"
    assert sorted_obs[0]["filed"] == "2026-02-15"


def test_select_latest_annual_observation():
    data = make_companyfacts_fixture()

    revenue = find_concept(
        data,
        "us-gaap",
        "Revenues",
    )

    latest = select_latest_annual_observation(revenue)

    assert latest is not None
    assert latest["val"] == 120000
    assert latest["end"] == "2025-12-31"
    assert latest["filed"] == "2026-02-15"


def test_select_latest_instant_observation():
    data = make_companyfacts_fixture()

    cash = find_concept(
        data,
        "us-gaap",
        "CashAndCashEquivalentsAtCarryingValue",
    )

    latest = select_latest_instant_observation(cash)

    assert latest is not None
    assert latest["val"] == 50000
    assert latest["end"] == "2025-12-31"


# ============================================================
# CONCEPT PRIORITY
# ============================================================

def test_find_best_annual_concept():
    data = make_companyfacts_fixture()

    result = find_best_annual_concept(
        data,
        [
            "NonExistingRevenueConcept",
            "Revenues",
        ],
    )

    assert result is not None
    assert result["namespace"] == "us-gaap"
    assert result["concept"] == "Revenues"
    assert result["observation"]["val"] == 120000


def test_find_best_instant_concept():
    data = make_companyfacts_fixture()

    result = find_best_instant_concept(
        data,
        [
            "NonExistingCashConcept",
            "CashAndCashEquivalentsAtCarryingValue",
        ],
    )

    assert result is not None
    assert result["namespace"] == "us-gaap"
    assert (
        result["concept"]
        == "CashAndCashEquivalentsAtCarryingValue"
    )
    assert result["observation"]["val"] == 50000


def test_find_best_instant_equity_concept():
    data = make_companyfacts_fixture()

    result = find_best_instant_concept(
        data,
        ["StockholdersEquity"],
    )

    assert result is not None
    assert result["namespace"] == "us-gaap"
    assert result["concept"] == "StockholdersEquity"
    assert result["observation"]["val"] == 80000


# ============================================================
# UNIT-SELECTION AMBIGUITY (Round AU)
#
# These tests use dedicated single-concept fixtures (rather than
# make_companyfacts_fixture(), which deliberately has only one unit
# per concept everywhere -- see Round AS) so that competing units for
# the same reporting context can be constructed explicitly.
# ============================================================

def _make_single_concept_annual_fixture(units_payload):
    return {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": units_payload,
                },
            },
        },
    }


def test_find_best_annual_concept_single_nonusd_unit_not_rejected():
    # A single, unambiguous non-USD unit must not be rejected just
    # because it is not USD -- the Round AU design constraint
    # explicitly forbids a USD-first policy.
    data = _make_single_concept_annual_fixture({
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 500000,
                "accn": "0000000000-26-000002",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-20",
            }
        ]
    })

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 500000
    assert result["observation"]["unit"] == "CAD"


def test_find_best_annual_concept_competing_units_same_period_is_incomplete():
    # Same reporting context (identical start/end), two different
    # units competing -- must not silently pick either one.
    units_payload = {
        "USD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 162000,
                "accn": "0000000000-26-000003",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_competing_units_order_independent():
    # Swapping the order of the competing units in the source "units"
    # dict (and thus the order get_observations() would encounter
    # them in) must not change the outcome.
    obs_usd = {
        "start": "2025-01-01",
        "end": "2025-12-31",
        "val": 120000,
        "accn": "0000000000-26-000001",
        "fy": 2025,
        "fp": "FY",
        "form": "10-K",
        "filed": "2026-02-15",
    }
    obs_cad = {
        "start": "2025-01-01",
        "end": "2025-12-31",
        "val": 162000,
        "accn": "0000000000-26-000003",
        "fy": 2025,
        "fp": "FY",
        "form": "10-K",
        "filed": "2026-02-15",
    }

    data_usd_first = _make_single_concept_annual_fixture({
        "USD": [obs_usd],
        "CAD": [obs_cad],
    })
    data_cad_first = _make_single_concept_annual_fixture({
        "CAD": [obs_cad],
        "USD": [obs_usd],
    })

    result_usd_first = find_best_annual_concept(
        data_usd_first, ["Revenues"]
    )
    result_cad_first = find_best_annual_concept(
        data_cad_first, ["Revenues"]
    )

    assert result_usd_first["ambiguous"] is True
    assert result_cad_first["ambiguous"] is True
    assert (
        set(result_usd_first["units"])
        == set(result_cad_first["units"])
        == {"USD", "CAD"}
    )


def test_find_best_annual_concept_different_periods_different_units_not_merged():
    # Different reporting periods using different units is NOT the
    # same question as competing units within one period -- it must
    # not be folded into the same ambiguity group, and the genuinely
    # latest (here: single-unit) period must still be selected.
    units_payload = {
        "CAD": [
            {
                "start": "2023-01-01",
                "end": "2023-12-31",
                "val": 90000,
                "accn": "0000000000-24-000001",
                "fy": 2023,
                "fp": "FY",
                "form": "10-K",
                "filed": "2024-02-15",
            }
        ],
        "USD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 120000
    assert result["observation"]["unit"] == "USD"
    assert result["observation"]["end"] == "2025-12-31"


def test_find_best_annual_concept_stale_ambiguous_period_does_not_block_newer_clean_period():
    # An older period with a unit conflict must not block selection
    # of a genuinely newer, unambiguous period -- unit-ambiguity
    # handling must not override the existing filed/AS_OF recency
    # rule for a period that isn't actually in competition.
    units_payload = {
        "USD": [
            {
                "start": "2023-01-01",
                "end": "2023-12-31",
                "val": 90000,
                "accn": "0000000000-24-000001",
                "fy": 2023,
                "fp": "FY",
                "form": "10-K",
                "filed": "2024-02-15",
            },
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            },
        ],
        "CAD": [
            {
                "start": "2023-01-01",
                "end": "2023-12-31",
                "val": 121500,
                "accn": "0000000000-24-000002",
                "fy": 2023,
                "fp": "FY",
                "form": "10-K",
                "filed": "2024-02-15",
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 120000
    assert result["observation"]["end"] == "2025-12-31"


def test_find_best_annual_concept_newer_ambiguous_period_is_not_masked_by_older_clean_period():
    # The reverse of the previous test: a more recent period that IS
    # ambiguous must not be silently papered over by an older,
    # resolvable period -- that would present a stale number as if
    # it were current.
    units_payload = {
        "USD": [
            {
                "start": "2023-01-01",
                "end": "2023-12-31",
                "val": 90000,
                "accn": "0000000000-24-000001",
                "fy": 2023,
                "fp": "FY",
                "form": "10-K",
                "filed": "2024-02-15",
            },
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            },
        ],
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 162000,
                "accn": "0000000000-26-000003",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result["ambiguous"] is True


def test_find_best_instant_concept_competing_units_same_point_in_time_is_incomplete():
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "CashAndCashEquivalentsAtCarryingValue": {
                    "label": "Cash",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 50000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ],
                        "CAD": [
                            {
                                "end": "2025-12-31",
                                "val": 67500,
                                "accn": "0000000000-26-000004",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ],
                    },
                },
            },
        },
    }

    result = find_best_instant_concept(
        data,
        ["CashAndCashEquivalentsAtCarryingValue"],
    )

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_extract_metric_ambiguous_unit_reports_incomplete_with_reason():
    # The extraction-layer result must surface INCOMPLETE with a
    # traceable reason and the competing units, not collapse to a
    # bare MISSING.
    units_payload = {
        "USD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 162000,
                "accn": "0000000000-26-000003",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = extract_metric(data, "revenue", ["Revenues"])

    assert result["status"] == "INCOMPLETE"
    assert result["status"] != "MISSING"
    assert result["value"] is None
    assert "reason" in result
    assert result["reason"]
    assert set(result["units_observed"]) == {"USD", "CAD"}


# ============================================================
# CROSS-CONCEPT UNIT CONFLICT (Round AU-R1)
#
# Round AU's ambiguity guard only compares units WITHIN a single
# concept's own observations. These tests cover the separate case of
# two different concept candidates for the same logical metric (as
# tried in priority order by normalize_company()) that each resolve,
# on their own, to a single unambiguous unit, but report the SAME
# reporting period in DIFFERENT units from each other.
# ============================================================

def test_find_best_annual_concept_cross_concept_unit_conflict_is_incomplete():
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                # Higher-priority candidate (index 0): single USD
                # observation, unambiguous on its own.
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
                # Lower-priority candidate (index 1): single CAD
                # observation, also unambiguous on its own, but for
                # the exact same reporting period.
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "CAD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 162000,
                                "accn": "0000000000-26-000005",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    # Each concept candidate, taken alone, is unambiguous (single
    # unit each) -- so this can only be caught by comparing across
    # candidates, not by the per-concept guard alone.
    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_cross_concept_same_period_same_unit_not_ambiguous():
    # Two different concept candidates reporting the SAME period in
    # the SAME unit must not be flagged -- existing concept-priority
    # and accession tie-break behavior must still decide the winner.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000002",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 120000
    # Highest concept_priority (index 0 -> priority 0, which wins
    # the "-concept_priority" tie-break) is selected, exactly as
    # before this round's change.
    assert result["concept"] == (
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    )


def test_find_best_annual_concept_cross_concept_conflict_does_not_block_newer_unconflicted_concept():
    # A unit conflict between two concept candidates for an OLDER
    # period must not prevent a genuinely newer, unconflicted period
    # (reported under either concept) from being selected -- existing
    # PIT/recency behavior must be preserved for non-competing data.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-24-000001",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-02-15",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            },
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "CAD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 121500,
                                "accn": "0000000000-24-000002",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 120000
    assert result["observation"]["end"] == "2025-12-31"


def test_find_best_instant_concept_cross_concept_unit_conflict_is_incomplete():
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "LongTermDebtNoncurrent": {
                    "label": "Noncurrent Debt",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 30000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
                "LongTermNotesPayable": {
                    "label": "Noncurrent Debt (fallback tag)",
                    "units": {
                        "CAD": [
                            {
                                "end": "2025-12-31",
                                "val": 40500,
                                "accn": "0000000000-26-000006",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_instant_concept(
        data,
        [
            "LongTermDebtNoncurrent",
            "LongTermNotesPayable",
        ],
    )

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


# ============================================================
# FILING-RECENCY-AWARE UNIT AMBIGUITY (Round AU-R3)
#
# Round AU-R2 found that Round AU/AU-R1's ambiguity guard treated
# ALL observations sharing a reporting period as one undifferentiated
# pool, regardless of how long ago each was filed -- so a single
# stale, superseded observation in a different unit could force a
# real "INCOMPLETE" on a period the latest filing reports
# unambiguously. These tests cover the filing-recency-aware policy:
# only the most-recently-FILED cohort within a reporting context is
# checked for unit competition.
# ============================================================

def test_find_best_annual_concept_older_unit_does_not_block_newer_single_unit_same_period():
    # Same reporting period (2025), but the CAD observation was filed
    # earlier than the USD one -- the latest-filed cohort (USD only)
    # must win; the older CAD tag for this same period must not
    # force INCOMPLETE.
    units_payload = {
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 162000,
                "accn": "0000000000-26-000009",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-01-20",
            }
        ],
        "USD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 120000
    assert result["observation"]["unit"] == "USD"


def test_find_best_annual_concept_same_latest_filed_cohort_conflict_is_still_incomplete():
    # Same period, same latest "filed" date for both units -- the
    # latest cohort itself is internally conflicted, so this must
    # still be INCOMPLETE (recency-awareness must not weaken the
    # same-cohort guard).
    units_payload = {
        "USD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 162000,
                "accn": "0000000000-26-000003",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_same_filed_date_different_accession_conflict_is_incomplete():
    # Same "filed" date, different accession numbers -- these must
    # be treated as ONE cohort (not disambiguated by accession), so
    # the conflict still produces INCOMPLETE.
    units_payload = {
        "USD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 162000,
                "accn": "0000000000-26-000999",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_unprovable_filing_date_keeps_conflict_incomplete():
    # Same period, two competing units, but one observation's
    # "filed" is missing entirely -- it cannot be proven older than
    # the other, so this must stay INCOMPLETE rather than assuming
    # the dated one is "the latest".
    units_payload = {
        "USD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 162000,
                "accn": "0000000000-26-000003",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                # "filed" intentionally omitted -- cannot be proven
                # to be an older, superseded observation.
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_invalid_filing_date_keeps_conflict_incomplete():
    # Same as above, but with a malformed "filed" value rather than a
    # missing one -- must be treated identically (unprovable, not
    # "oldest").
    units_payload = {
        "USD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ],
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 162000,
                "accn": "0000000000-26-000003",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "not-a-date",
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_recency_resolution_order_independent():
    # Swapping which unit appears first in the source "units" dict
    # must not change which cohort is treated as "latest".
    obs_old_cad = {
        "start": "2025-01-01",
        "end": "2025-12-31",
        "val": 162000,
        "accn": "0000000000-26-000009",
        "fy": 2025,
        "fp": "FY",
        "form": "10-K",
        "filed": "2026-01-20",
    }
    obs_new_usd = {
        "start": "2025-01-01",
        "end": "2025-12-31",
        "val": 120000,
        "accn": "0000000000-26-000001",
        "fy": 2025,
        "fp": "FY",
        "form": "10-K",
        "filed": "2026-02-15",
    }

    data_cad_first = _make_single_concept_annual_fixture({
        "CAD": [obs_old_cad],
        "USD": [obs_new_usd],
    })
    data_usd_first = _make_single_concept_annual_fixture({
        "USD": [obs_new_usd],
        "CAD": [obs_old_cad],
    })

    result_cad_first = find_best_annual_concept(
        data_cad_first, ["Revenues"]
    )
    result_usd_first = find_best_annual_concept(
        data_usd_first, ["Revenues"]
    )

    for result in (result_cad_first, result_usd_first):
        assert result.get("ambiguous") is not True
        assert result["observation"]["val"] == 120000
        assert result["observation"]["unit"] == "USD"


def test_find_best_instant_concept_older_unit_does_not_block_newer_single_unit_same_point_in_time():
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "CashAndCashEquivalentsAtCarryingValue": {
                    "label": "Cash",
                    "units": {
                        "CAD": [
                            {
                                "end": "2025-12-31",
                                "val": 67500,
                                "accn": "0000000000-26-000004",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-20",
                            }
                        ],
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 50000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ],
                    },
                },
            },
        },
    }

    result = find_best_instant_concept(
        data,
        ["CashAndCashEquivalentsAtCarryingValue"],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 50000
    assert result["observation"]["unit"] == "USD"


def test_find_best_annual_concept_cross_concept_older_unit_does_not_block_newer_single_unit():
    # Cross-concept version of the recency policy: concept A (higher
    # priority) reported this period in USD but filed EARLIER;
    # concept B (lower priority) reported the SAME period in CAD but
    # filed LATER. The latest-filed cohort for this period has only
    # one unit (CAD, from concept B) -- that must resolve cleanly,
    # not be blocked by the older USD tag under concept A.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000009",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-20",
                            }
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "CAD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 162000,
                                "accn": "0000000000-26-000005",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 162000
    assert result["observation"]["unit"] == "CAD"
    assert result["concept"] == "Revenues"


def test_find_best_annual_concept_cross_concept_recency_resolution_order_independent():
    # Same scenario as above, but the concept_candidates order is
    # reversed -- the latest-filed cohort (CAD, filed later) must
    # still win regardless of which concept is tried first.
    def make_data():
        return {
            "cik": "0000000000",
            "entityName": "Test Company",
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "label": "Revenue (ASC 606)",
                        "units": {
                            "USD": [
                                {
                                    "start": "2025-01-01",
                                    "end": "2025-12-31",
                                    "val": 120000,
                                    "accn": "0000000000-26-000009",
                                    "fy": 2025,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2026-01-20",
                                }
                            ]
                        },
                    },
                    "Revenues": {
                        "label": "Revenue",
                        "units": {
                            "CAD": [
                                {
                                    "start": "2025-01-01",
                                    "end": "2025-12-31",
                                    "val": 162000,
                                    "accn": "0000000000-26-000005",
                                    "fy": 2025,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2026-02-15",
                                }
                            ]
                        },
                    },
                },
            },
        }

    result_a_first = find_best_annual_concept(
        make_data(),
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )
    result_b_first = find_best_annual_concept(
        make_data(),
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ],
    )

    for result in (result_a_first, result_b_first):
        assert result.get("ambiguous") is not True
        assert result["observation"]["val"] == 162000
        assert result["observation"]["unit"] == "CAD"


def test_normalize_company_latest_single_unit_not_blocked_by_older_unit_same_period():
    # Full normalize_company() integration: the fixture's existing
    # "Revenues" concept already has an older 2024 USD observation
    # and a current 2025 USD observation. Here we additionally
    # inject an EARLIER-filed CAD observation for the SAME 2025
    # period under the lower-priority "Revenues" tag's sibling
    # candidate, confirming that this older, differently-unit'd
    # observation for the current period does not turn a genuinely
    # resolvable "revenue" into INCOMPLETE.
    data = make_companyfacts_fixture()

    data["facts"]["us-gaap"][
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    ] = {
        "label": "Revenue (ASC 606)",
        "units": {
            "CAD": [
                {
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "val": 162000,
                    "accn": "0000000000-26-000009",
                    "fy": 2025,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": "2026-01-20",
                }
            ]
        },
    }

    normalized = normalize_company(
        "TEST",
        data,
        "NON_FINANCIAL",
    )

    revenue = normalized["metrics"]["revenue"]

    # The fixture's own "Revenues" USD observation for 2025 is filed
    # 2026-02-15, strictly later than the injected CAD tag's
    # 2025-06-01 -- so the latest-filed cohort for the 2025 period
    # has exactly one unit (USD) and must resolve normally.
    assert revenue["status"] == "OK"
    assert revenue["value"] == 120000
    assert revenue["unit"] == "USD"

    failures = validate_normalized_data(normalized)
    assert "revenue" not in failures


# ============================================================
# FILING-RECENCY-AWARE UNIT AMBIGUITY -- EDGE CASES (Round AU-R4)
#
# Round AU-R3 made the within-concept and cross-concept unit guards
# aware of filing recency (only the latest-"filed" cohort is checked
# for unit competition). These tests cover two classes of edge cases
# that were not yet directly exercised by name:
#
#   (A) ALL competing observations in a reporting context lack a
#       provable "filed" date -- either missing entirely or
#       malformed. dated_peers/dated_items is then empty, so the
#       "latest_filed = max(...)" computation must not be reached
#       (or must not raise) and the result must stay a safe
#       INCOMPLETE/ambiguous outcome rather than an arbitrary pick,
#       a crash, or a silent "OK".
#
#   (B) Reversing the input order of competing observations/concept
#       candidates must not change whether a conflict is reported as
#       ambiguous -- order-independence must hold for the CONFLICT
#       outcome, not just the already-tested resolved-outcome cases.
# ============================================================

def test_find_best_annual_concept_all_filed_dates_missing_keeps_conflict_incomplete():
    # Neither competing unit has a "filed" key at all -- dated_peers
    # is empty, so there is no "latest cohort" that can be proven
    # more current than the other. Must stay INCOMPLETE, and must
    # not raise (max() over an empty sequence) internally.
    units_payload = {
        "USD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                # "filed" intentionally omitted.
            }
        ],
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 162000,
                "accn": "0000000000-26-000003",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                # "filed" intentionally omitted.
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_all_filed_dates_invalid_keeps_conflict_incomplete():
    # Both competing units have a "filed" value, but neither is a
    # parseable date -- same as the missing-date case: no cohort can
    # be proven current, so the result must stay INCOMPLETE rather
    # than falling back to dict/list order.
    units_payload = {
        "USD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 120000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "not-a-date",
            }
        ],
        "CAD": [
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 162000,
                "accn": "0000000000-26-000003",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "also-not-a-date",
            }
        ],
    }

    data = _make_single_concept_annual_fixture(units_payload)

    result = find_best_annual_concept(data, ["Revenues"])

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_instant_concept_all_filed_dates_missing_keeps_conflict_incomplete():
    # Instant-observation analog of the annual all-missing-"filed"
    # case -- the instant path shares the same
    # _select_latest_with_ambiguity() helper, but is verified
    # independently since is_instant_observation()/sort_instant_
    # observations() differ from the annual path.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "CashAndCashEquivalentsAtCarryingValue": {
                    "label": "Cash",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 50000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted.
                            }
                        ],
                        "CAD": [
                            {
                                "end": "2025-12-31",
                                "val": 67500,
                                "accn": "0000000000-26-000004",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted.
                            }
                        ],
                    },
                },
            },
        },
    }

    result = find_best_instant_concept(
        data,
        ["CashAndCashEquivalentsAtCarryingValue"],
    )

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_cross_concept_all_filed_dates_missing_keeps_conflict_incomplete():
    # Cross-concept analog: two different concept candidates each
    # resolve, on their own, to a single unambiguous observation for
    # the same period, but neither observation has a provable "filed"
    # date. _resolve_cross_concept_unit_conflicts() must not raise on
    # the empty dated_items case and must report the conflict as
    # ambiguous rather than picking one concept's unit arbitrarily.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted.
                            }
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "CAD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 162000,
                                "accn": "0000000000-26-000005",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted.
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_same_latest_filed_cohort_conflict_order_independent():
    # Reversing which unit appears first in the source "units" dict
    # must not change the CONFLICT outcome (not just the resolved-
    # outcome case already covered elsewhere): both units share the
    # same latest "filed" date, so this must stay ambiguous regardless
    # of dict insertion order.
    obs_usd = {
        "start": "2025-01-01",
        "end": "2025-12-31",
        "val": 120000,
        "accn": "0000000000-26-000001",
        "fy": 2025,
        "fp": "FY",
        "form": "10-K",
        "filed": "2026-02-15",
    }
    obs_cad = {
        "start": "2025-01-01",
        "end": "2025-12-31",
        "val": 162000,
        "accn": "0000000000-26-000003",
        "fy": 2025,
        "fp": "FY",
        "form": "10-K",
        "filed": "2026-02-15",
    }

    data_usd_first = _make_single_concept_annual_fixture({
        "USD": [obs_usd],
        "CAD": [obs_cad],
    })
    data_cad_first = _make_single_concept_annual_fixture({
        "CAD": [obs_cad],
        "USD": [obs_usd],
    })

    result_usd_first = find_best_annual_concept(
        data_usd_first, ["Revenues"]
    )
    result_cad_first = find_best_annual_concept(
        data_cad_first, ["Revenues"]
    )

    for result in (result_usd_first, result_cad_first):
        assert result is not None
        assert result["ambiguous"] is True
        assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_instant_concept_same_latest_filed_cohort_conflict_order_independent():
    # Instant-observation analog: same latest "filed" date, competing
    # units, dict order reversed -- must stay ambiguous either way.
    def make_data(first_unit_payload, second_unit_payload):
        units = {}
        units.update(first_unit_payload)
        units.update(second_unit_payload)
        return {
            "cik": "0000000000",
            "entityName": "Test Company",
            "facts": {
                "us-gaap": {
                    "CashAndCashEquivalentsAtCarryingValue": {
                        "label": "Cash",
                        "units": units,
                    },
                },
            },
        }

    usd_payload = {
        "USD": [
            {
                "end": "2025-12-31",
                "val": 50000,
                "accn": "0000000000-26-000001",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ]
    }
    cad_payload = {
        "CAD": [
            {
                "end": "2025-12-31",
                "val": 67500,
                "accn": "0000000000-26-000004",
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-15",
            }
        ]
    }

    result_usd_first = find_best_instant_concept(
        make_data(usd_payload, cad_payload),
        ["CashAndCashEquivalentsAtCarryingValue"],
    )
    result_cad_first = find_best_instant_concept(
        make_data(cad_payload, usd_payload),
        ["CashAndCashEquivalentsAtCarryingValue"],
    )

    for result in (result_usd_first, result_cad_first):
        assert result is not None
        assert result["ambiguous"] is True
        assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_cross_concept_unit_conflict_order_independent():
    # Reversing the concept_candidates priority order for a genuine
    # same-latest-filed-cohort cross-concept conflict must not change
    # the conflict outcome.
    def make_data():
        return {
            "cik": "0000000000",
            "entityName": "Test Company",
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "label": "Revenue (ASC 606)",
                        "units": {
                            "USD": [
                                {
                                    "start": "2025-01-01",
                                    "end": "2025-12-31",
                                    "val": 120000,
                                    "accn": "0000000000-26-000001",
                                    "fy": 2025,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2026-02-15",
                                }
                            ]
                        },
                    },
                    "Revenues": {
                        "label": "Revenue",
                        "units": {
                            "CAD": [
                                {
                                    "start": "2025-01-01",
                                    "end": "2025-12-31",
                                    "val": 162000,
                                    "accn": "0000000000-26-000005",
                                    "fy": 2025,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2026-02-15",
                                }
                            ]
                        },
                    },
                },
            },
        }

    result_a_first = find_best_annual_concept(
        make_data(),
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )
    result_b_first = find_best_annual_concept(
        make_data(),
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ],
    )

    for result in (result_a_first, result_b_first):
        assert result is not None
        assert result["ambiguous"] is True
        assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_same_filed_date_different_accession_conflict_order_independent():
    # Same "filed" date, different accession numbers, dict order
    # reversed -- must stay one cohort (not disambiguated by
    # accession or by dict order), so the conflict outcome must be
    # identical either way.
    obs_usd = {
        "start": "2025-01-01",
        "end": "2025-12-31",
        "val": 120000,
        "accn": "0000000000-26-000001",
        "fy": 2025,
        "fp": "FY",
        "form": "10-K",
        "filed": "2026-02-15",
    }
    obs_cad = {
        "start": "2025-01-01",
        "end": "2025-12-31",
        "val": 162000,
        "accn": "0000000000-26-000999",
        "fy": 2025,
        "fp": "FY",
        "form": "10-K",
        "filed": "2026-02-15",
    }

    data_usd_first = _make_single_concept_annual_fixture({
        "USD": [obs_usd],
        "CAD": [obs_cad],
    })
    data_cad_first = _make_single_concept_annual_fixture({
        "CAD": [obs_cad],
        "USD": [obs_usd],
    })

    result_usd_first = find_best_annual_concept(
        data_usd_first, ["Revenues"]
    )
    result_cad_first = find_best_annual_concept(
        data_cad_first, ["Revenues"]
    )

    for result in (result_usd_first, result_cad_first):
        assert result is not None
        assert result["ambiguous"] is True
        assert set(result["units"]) == {"USD", "CAD"}


# ============================================================
# CROSS-CONCEPT FILING-RECENCY ARBITRATION (Round AU-R5)
#
# Round AU-R3/AU-R4 made the filing-recency-aware latest-cohort check
# consistent WITHIN one concept's own observations and WITHIN the
# cross-concept comparison of already-resolved candidates. But a
# concept that was internally ambiguous on its own was shunted
# straight into "ambiguous_candidates" before ever being compared,
# unit-by-unit and filing-date-by-filing-date, against a DIFFERENT
# concept candidate reporting the exact same period. Because the
# final cross-period tie-break only compares reporting-PERIOD END
# dates (identical for same-period entries), a stale internal
# conflict could permanently block a genuinely newer, single-unit
# candidate reported under a different concept for that same period.
#
# These tests cover the fix: every concept candidate's own
# current-period peer observations are now pooled together (not just
# its already-reduced pick) before any within-concept-vs-cross-
# concept distinction is made, so one unified latest-filed-cohort
# check decides the outcome for each reporting context regardless of
# how many different concepts contributed to it.
# ============================================================

def test_find_best_annual_concept_cross_concept_resolves_internal_conflict_via_newer_concept():
    # Requirement A: Concept A has an internal USD/CAD conflict for
    # the 2025 period, both observations filed on the SAME date.
    # Concept B (lower priority) reports the SAME 2025 period in CAD
    # alone, filed LATER than concept A's conflicting pair. The
    # overall latest-filed cohort for this period is concept B's
    # single CAD observation -- concept A's older, internally-
    # conflicting pair must not block it.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-20",
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 162000,
                                "accn": "0000000000-26-000002",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-20",
                            }
                        ],
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "CAD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 170000,
                                "accn": "0000000000-26-000003",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 170000
    assert result["observation"]["unit"] == "CAD"
    assert result["concept"] == "Revenues"


def test_find_best_instant_concept_cross_concept_resolves_internal_conflict_via_newer_concept():
    # Instant-observation analog of the annual test above.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "LongTermDebtNoncurrent": {
                    "label": "Noncurrent Debt",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 30000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-20",
                            }
                        ],
                        "CAD": [
                            {
                                "end": "2025-12-31",
                                "val": 40500,
                                "accn": "0000000000-26-000002",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-20",
                            }
                        ],
                    },
                },
                "LongTermNotesPayable": {
                    "label": "Noncurrent Debt (fallback tag)",
                    "units": {
                        "CAD": [
                            {
                                "end": "2025-12-31",
                                "val": 45000,
                                "accn": "0000000000-26-000003",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_instant_concept(
        data,
        [
            "LongTermDebtNoncurrent",
            "LongTermNotesPayable",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 45000
    assert result["observation"]["unit"] == "CAD"
    assert result["concept"] == "LongTermNotesPayable"


def test_find_best_annual_concept_cross_concept_same_latest_cohort_conflict_is_incomplete():
    # Requirement B: two different concepts report the SAME period at
    # the SAME latest "filed" date, in DIFFERENT units -- the merged
    # latest cohort itself is conflicted, so this must stay
    # INCOMPLETE regardless of concept priority.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "CAD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 162000,
                                "accn": "0000000000-26-000005",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_cross_concept_unprovable_filing_date_keeps_conflict_incomplete():
    # Requirement C: two different concepts report the SAME period in
    # DIFFERENT units, and one side's "filed" date is missing -- it
    # cannot be proven older than the other concept's observation, so
    # this must stay INCOMPLETE rather than arbitrarily picking either
    # unit.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "CAD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 162000,
                                "accn": "0000000000-26-000005",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted --
                                # cannot be proven older than the USD
                                # observation above.
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_cross_concept_invalid_filing_date_keeps_conflict_incomplete():
    # Same as above, but with a malformed "filed" value rather than a
    # missing one.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "CAD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 162000,
                                "accn": "0000000000-26-000005",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "not-a-date",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    assert result is not None
    assert result["ambiguous"] is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_older_end_with_newer_filed_wins_cross_concept():
    # Requirement D (case 1): concept A's own current period ends in
    # 2023 but was filed very recently (a late amendment); concept B's
    # own current period ends in 2025 but was filed earlier. Neither
    # period is internally or cross-concept unit-ambiguous (both
    # single-unit, non-conflicting contexts). The existing annual
    # selection rule sorts primarily by "filed" date (unchanged from
    # before Round AU, preserved through Round AU-R5's refactor), so
    # the OLDER-end/NEWER-filed candidate wins. This is pre-existing,
    # intentional behavior -- not something this round introduces or
    # changes -- documented here so a future round cannot mistake it
    # for a regression without checking this test first.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-26-000010",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-25-000002",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-03-01",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 90000
    assert result["observation"]["end"] == "2023-12-31"
    assert result["observation"]["filed"] == "2026-05-01"


def test_find_best_annual_concept_newer_end_with_newer_filed_wins_cross_concept():
    # Requirement D (case 2, the "반대" / reverse pairing): the
    # ordinary, aligned case -- the candidate with the newer period
    # end ALSO has the newer filed date -- still wins. This confirms
    # the Round AU-R5 pooling refactor did not disturb the common,
    # non-conflicting cross-concept recency comparison.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-24-000001",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-02-15",
                            }
                        ]
                    },
                },
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000002",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 120000
    assert result["observation"]["end"] == "2025-12-31"


def test_find_best_instant_concept_same_end_resolved_vs_ambiguous_filing_date_tiebreak():
    # Requirement E: two concept candidates share the same point-in-
    # time "end". Concept A alone, in isolation, would be internally
    # ambiguous (USD/CAD at the same filed date); concept B reports
    # the same "end" in a single unit (USD) at a LATER filed date.
    # The existing instant selection rule sorts primarily by "end"
    # (both tie here) then by "filed" -- after Round AU-R5's pooling
    # fix, the merged latest-filed cohort for this "end" is concept
    # B's single USD observation alone, which must win cleanly
    # instead of concept A's stale internal conflict forcing
    # INCOMPLETE.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "LongTermDebtNoncurrent": {
                    "label": "Noncurrent Debt",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 30000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-20",
                            }
                        ],
                        "CAD": [
                            {
                                "end": "2025-12-31",
                                "val": 40500,
                                "accn": "0000000000-26-000002",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-20",
                            }
                        ],
                    },
                },
                "LongTermNotesPayable": {
                    "label": "Noncurrent Debt (fallback tag)",
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": 31000,
                                "accn": "0000000000-26-000003",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_instant_concept(
        data,
        [
            "LongTermDebtNoncurrent",
            "LongTermNotesPayable",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 31000
    assert result["observation"]["unit"] == "USD"
    assert result["concept"] == "LongTermNotesPayable"


# ============================================================
# ANNUAL AMBIGUITY ARBITRATION (Round AU-R6)
#
# Round AU-R5 unified within-concept and cross-concept ambiguity
# detection, but the FINAL decision of whether an ambiguous reporting
# context should "win" over an already-resolved candidate still
# compared only by reporting-period END date (find_best_annual_
# concept()'s "ambiguous_end >= resolved_end" check). This is
# inconsistent with the established annual ordering, which ranks
# resolved candidates by FILING DATE FIRST, period end second (see
# the existing candidates.sort() key: (filed, end, -concept_priority,
# accn)). An ambiguous context whose latest conflicting filing is
# actually the most RECENTLY FILED data available (even if its
# period end is an older fiscal year) could therefore be silently
# overridden by a resolved candidate for a nominally newer period but
# an actually OLDER filing -- hiding a live, unresolved ambiguity.
# ============================================================

def test_find_best_annual_concept_ambiguous_older_end_newer_filed_not_silently_overridden():
    # Part A, primary defect scenario. Annual context A (Revenues)
    # has a genuine USD/CAD conflict at its latest-filed cohort
    # ("2026-05-01") for an OLDER period (end "2023-12-31"). Annual
    # context B (RevenueFromContractWithCustomerExcludingAssessedTax)
    # is unambiguous (single USD observation) for a NEWER period (end
    # "2025-12-31"), but was filed EARLIER ("2025-03-01") than A's
    # conflicting cohort.
    #
    # Under the established annual ordering (filed first, end
    # second), A's cohort ("2026-05-01") is more recent than B's
    # filing ("2025-03-01"), so A -- despite its older period end --
    # must be treated as the more current context. Since A is
    # ambiguous, the result must surface A's ambiguity; it must NOT
    # silently fall back to B just because B's period end is nominally
    # newer.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-26-000011",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 121500,
                                "accn": "0000000000-26-000012",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-25-000002",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-03-01",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_ambiguous_older_filed_does_not_block_newer_resolved():
    # Part A, reverse control case. Annual context A (Revenues) has a
    # USD/CAD conflict for an older period (end "2023-12-31"), filed
    # EARLIER ("2024-02-15"). Annual context B
    # (RevenueFromContractWithCustomerExcludingAssessedTax) is
    # unambiguous for a newer period (end "2025-12-31"), filed LATER
    # ("2026-02-15"). Under the established filed-first annual
    # ordering, B is unambiguously the more current context, so B
    # must be returned -- A's older, lower-priority ambiguity must
    # not block it.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-24-000001",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-02-15",
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 121500,
                                "accn": "0000000000-24-000002",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-02-15",
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 120000
    assert result["observation"]["end"] == "2025-12-31"


def test_find_best_annual_concept_fully_undated_ambiguity_falls_back_to_end_date_when_newer():
    # Part C edge case: an ambiguous context where EVERY competing
    # peer lacks a provable "filed" date at all (not just one side --
    # see the existing Round AU-R3/AU-R4 tests for the one-sided
    # case). Filing-date-based arbitration is impossible here without
    # inventing a date, so this exercises the documented conservative
    # fallback: compare by the one fact that IS directly observed,
    # the reporting-period end date. Here the ambiguous context's end
    # ("2024-12-31") is newer than the resolved candidate's end
    # ("2023-12-31"), so the ambiguity must win rather than be hidden.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 95000,
                                "accn": "0000000000-25-000001",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted on
                                # BOTH competing units.
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 128250,
                                "accn": "0000000000-25-000002",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted.
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-24-000001",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is True
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_fully_undated_ambiguity_does_not_block_newer_resolved():
    # Reverse of the above: the fully-undated ambiguous context's end
    # ("2022-12-31") is OLDER than the resolved candidate's end
    # ("2025-12-31"). With no provable filing date on either
    # competing unit, the conservative end-date fallback must not let
    # this stale, undated ambiguity block the genuinely newer,
    # resolved candidate.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2022-01-01",
                                "end": "2022-12-31",
                                "val": 80000,
                                "accn": "0000000000-23-000001",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted.
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2022-01-01",
                                "end": "2022-12-31",
                                "val": 108000,
                                "accn": "0000000000-23-000002",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted.
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 120000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-15",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 120000
    assert result["observation"]["end"] == "2025-12-31"


# ============================================================
# MULTI-AMBIGUITY ANNUAL ARBITRATION (Round AU-R7)
#
# Round AU-R6 fixed the comparison between a single pre-selected
# "best_ambiguous" context and the resolved candidate, but the step
# that picked that single context in the first place still sorted ALL
# ambiguous contexts by reporting-period END date only
# ("ambiguous_candidates.sort(key=lambda item: (_date_string(item
# ["context"][-1]) or "", -item["concept_priority"]), ...)"). That
# end-first pre-selection could discard a genuinely more-current (by
# filing date) ambiguous context in favor of a merely newer-ending
# one, before the Round AU-R6 filed-first comparison ever got a
# chance to consider it at all.
# ============================================================

def test_find_best_annual_concept_multi_ambiguous_most_recently_filed_wins_over_newer_ending_and_resolved():
    # Part B, primary defect scenario (reproduced against the
    # pre-fix code first; see the Round AU-R7 report for the exact
    # pre-fix observed result).
    #
    # Ambiguous A (Revenues): period end "2023-12-31" (older),
    # latest conflicting filing cohort "2026-05-01" (USD/CAD).
    # Ambiguous B (RevenueFromContractWithCustomerExcludingAssessedTax):
    # period end "2025-12-31" (newer), conflicting filing cohort
    # "2025-04-01" (USD/EUR).
    # Resolved C (SalesRevenueNet): period end "2024-12-31", filed
    # "2025-10-01", single unit.
    #
    # Under the established annual ordering (filed first, end
    # second), A's cohort ("2026-05-01") is the most recently filed
    # data of the three, so A's ambiguity must win -- even though B's
    # period end is nominally newer and C is a fully resolved
    # candidate. The pre-fix end-first ambiguous pre-selection would
    # have picked B over A (B's end "2025-12-31" > A's end
    # "2023-12-31"), and B would then have lost to C under the
    # Round AU-R6 filed-first comparison ("2025-04-01" < "2025-10-01"),
    # silently returning C and hiding A's live ambiguity entirely.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-26-000011",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 121500,
                                "accn": "0000000000-26-000012",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 200000,
                                "accn": "0000000000-25-000003",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-04-01",
                            }
                        ],
                        "EUR": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 184000,
                                "accn": "0000000000-25-000004",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-04-01",
                            }
                        ],
                    },
                },
                "SalesRevenueNet": {
                    "label": "Sales Revenue, Net",
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 150000,
                                "accn": "0000000000-25-000005",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-10-01",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is True
    assert result["concept"] == "Revenues"
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_multi_ambiguous_highest_ranked_resolved_still_wins():
    # Part B, required reverse/control case: the same three-context
    # shape, but with filing dates rearranged so the RESOLVED
    # candidate is now the most recently filed of all three. The
    # resolved candidate must still win -- adding more ambiguous
    # contexts to the pool must never unconditionally block
    # resolution.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-24-000001",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-01-01",
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 121500,
                                "accn": "0000000000-24-000002",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-01-01",
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 200000,
                                "accn": "0000000000-24-000003",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-06-01",
                            }
                        ],
                        "EUR": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 184000,
                                "accn": "0000000000-24-000004",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-06-01",
                            }
                        ],
                    },
                },
                "SalesRevenueNet": {
                    "label": "Sales Revenue, Net",
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 150000,
                                "accn": "0000000000-26-000005",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-01",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is not True
    assert result["observation"]["val"] == 150000
    assert result["observation"]["end"] == "2024-12-31"


def test_find_best_annual_concept_two_dated_ambiguous_no_resolved_later_filed_older_end_wins():
    # Part D #1/#2: two ambiguous contexts only (no resolved
    # candidate at all), with DIFFERENT filed dates and DIFFERENT
    # period ends, isolating pure ambiguous-vs-ambiguous ranking.
    # Context A is filed LATER but ends OLDER; context B is filed
    # EARLIER but ends NEWER. Filed date must win: A's ambiguity must
    # surface, not B's.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-26-000001",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-03-01",
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 121500,
                                "accn": "0000000000-26-000002",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-03-01",
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 200000,
                                "accn": "0000000000-25-000003",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-01-01",
                            }
                        ],
                        "EUR": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 184000,
                                "accn": "0000000000-25-000004",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-01-01",
                            }
                        ],
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is True
    assert result["concept"] == "Revenues"
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_two_dated_ambiguous_equal_filed_tiebreak_by_end_then_priority():
    # Part D #3: two ambiguous contexts share the EXACT same latest-
    # filed-cohort filing date. The end-date tiebreaker must then
    # decide: the context with the NEWER period end wins, even though
    # it is listed second (lower priority) in concept_candidates.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-26-000001",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-03-01",
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 121500,
                                "accn": "0000000000-26-000002",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-03-01",
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 200000,
                                "accn": "0000000000-26-000003",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-03-01",
                            }
                        ],
                        "EUR": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 184000,
                                "accn": "0000000000-26-000004",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-03-01",
                            }
                        ],
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is True
    assert result["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert set(result["units"]) == {"USD", "EUR"}


def test_find_best_annual_concept_undated_ambiguous_vs_dated_ambiguous_newer_end_wins():
    # Part D #4, mixed-provability case A: an UNDATED ambiguous
    # context (no peer in its cohort has a provable "filed" date)
    # competes against a DATED ambiguous context, with no resolved
    # candidate present. Round AU-R7's explicit, narrowly-scoped rule
    # (see the production code comment in find_best_annual_concept()):
    # the undated context is compared ONLY by its own reporting-period
    # end date against whichever dated-comparable entry currently
    # leads -- here, the dated ambiguous context, since there is no
    # resolved candidate. The undated context's end ("2026-12-31") is
    # newer than the dated context's end ("2023-12-31"), so the
    # undated ambiguity must win.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-26-000001",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 121500,
                                "accn": "0000000000-26-000002",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2026-01-01",
                                "end": "2026-12-31",
                                "val": 200000,
                                "accn": "0000000000-27-000003",
                                "fy": 2026,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted on
                                # BOTH competing units.
                            }
                        ],
                        "EUR": [
                            {
                                "start": "2026-01-01",
                                "end": "2026-12-31",
                                "val": 184000,
                                "accn": "0000000000-27-000004",
                                "fy": 2026,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted.
                            }
                        ],
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is True
    assert result["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert set(result["units"]) == {"USD", "EUR"}


def test_find_best_annual_concept_undated_ambiguous_vs_dated_ambiguous_older_end_loses():
    # Part D #4, mixed-provability case B (reverse control): the same
    # shape, but the undated context's end is now OLDER than the
    # dated ambiguous context's end, so the dated context must win
    # instead -- the undated context must not unconditionally win
    # just because it lacks a provable filing date.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 90000,
                                "accn": "0000000000-26-000001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 121500,
                                "accn": "0000000000-26-000002",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2022-01-01",
                                "end": "2022-12-31",
                                "val": 200000,
                                "accn": "0000000000-23-000003",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted on
                                # BOTH competing units.
                            }
                        ],
                        "EUR": [
                            {
                                "start": "2022-01-01",
                                "end": "2022-12-31",
                                "val": 184000,
                                "accn": "0000000000-23-000004",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted.
                            }
                        ],
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is True
    assert result["concept"] == "Revenues"
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_undated_ambiguous_vs_dated_resolved_and_dated_ambiguous_together():
    # Part D #4 (full three-way mix): a resolved candidate, a DATED
    # ambiguous context, and an UNDATED ambiguous context are all
    # present together. The dated ambiguous context's filing date is
    # the most recent of all, so it leads the dated-comparable axis
    # (resolved does not win); the undated context's end is older
    # than the leading (dated-ambiguous) entry's end, so it must not
    # override it. The dated ambiguous context must win overall.
    data = {
        "cik": "0000000000",
        "entityName": "Test Company",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 90000,
                                "accn": "0000000000-26-000001",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ],
                        "CAD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 121500,
                                "accn": "0000000000-26-000002",
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-05-01",
                            }
                        ],
                    },
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue (ASC 606)",
                    "units": {
                        "USD": [
                            {
                                "start": "2022-01-01",
                                "end": "2022-12-31",
                                "val": 200000,
                                "accn": "0000000000-23-000003",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted on
                                # BOTH competing units.
                            }
                        ],
                        "EUR": [
                            {
                                "start": "2022-01-01",
                                "end": "2022-12-31",
                                "val": 184000,
                                "accn": "0000000000-23-000004",
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                # "filed" intentionally omitted.
                            }
                        ],
                    },
                },
                "SalesRevenueNet": {
                    "label": "Sales Revenue, Net",
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 150000,
                                "accn": "0000000000-25-000005",
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2025-10-01",
                            }
                        ]
                    },
                },
            },
        },
    }

    result = find_best_annual_concept(
        data,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
        ],
    )

    assert result is not None
    assert result.get("ambiguous") is True
    assert result["concept"] == "Revenues"
    assert set(result["units"]) == {"USD", "CAD"}


def test_find_best_annual_concept_multi_ambiguous_order_independent_unit_dict_order():
    # Part D #5: input-order independence. Swapping which unit
    # appears first in each concept's "units" dict, across all three
    # contexts in the primary multi-ambiguity scenario, must not
    # change the outcome.
    def make_data(revenues_unit_order, asc606_unit_order):
        revenues_units_full = {
            "USD": [
                {
                    "start": "2023-01-01",
                    "end": "2023-12-31",
                    "val": 90000,
                    "accn": "0000000000-26-000011",
                    "fy": 2023,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": "2026-05-01",
                }
            ],
            "CAD": [
                {
                    "start": "2023-01-01",
                    "end": "2023-12-31",
                    "val": 121500,
                    "accn": "0000000000-26-000012",
                    "fy": 2023,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": "2026-05-01",
                }
            ],
        }
        asc606_units_full = {
            "USD": [
                {
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "val": 200000,
                    "accn": "0000000000-25-000003",
                    "fy": 2025,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": "2025-04-01",
                }
            ],
            "EUR": [
                {
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "val": 184000,
                    "accn": "0000000000-25-000004",
                    "fy": 2025,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": "2025-04-01",
                }
            ],
        }

        return {
            "cik": "0000000000",
            "entityName": "Test Company",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenue",
                        "units": {
                            unit: revenues_units_full[unit]
                            for unit in revenues_unit_order
                        },
                    },
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "label": "Revenue (ASC 606)",
                        "units": {
                            unit: asc606_units_full[unit]
                            for unit in asc606_unit_order
                        },
                    },
                    "SalesRevenueNet": {
                        "label": "Sales Revenue, Net",
                        "units": {
                            "USD": [
                                {
                                    "start": "2024-01-01",
                                    "end": "2024-12-31",
                                    "val": 150000,
                                    "accn": "0000000000-25-000005",
                                    "fy": 2024,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "filed": "2025-10-01",
                                }
                            ]
                        },
                    },
                },
            },
        }

    data_a = make_data(["USD", "CAD"], ["USD", "EUR"])
    data_b = make_data(["CAD", "USD"], ["EUR", "USD"])

    concept_candidates = [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ]

    result_a = find_best_annual_concept(data_a, concept_candidates)
    result_b = find_best_annual_concept(data_b, concept_candidates)

    for result in (result_a, result_b):
        assert result is not None
        assert result.get("ambiguous") is True
        assert result["concept"] == "Revenues"
        assert set(result["units"]) == {"USD", "CAD"}


# ============================================================
# TOTAL DEBT
# ============================================================

def test_calculate_total_debt_nonfinancial():
    current_debt = {
        "metric": "current_debt",
        "status": "OK",
        "value": 10000,
        "unit": "USD",
    }

    noncurrent_debt = {
        "metric": "noncurrent_debt",
        "status": "OK",
        "value": 30000,
        "unit": "USD",
    }

    result = calculate_total_debt(
        current_debt,
        noncurrent_debt,
        "NON_FINANCIAL",
    )

    assert result["status"] == "OK"
    assert result["value"] == 40000
    assert result["unit"] == "USD"


def test_calculate_total_debt_incomplete():
    current_debt = {
        "metric": "current_debt",
        "status": "OK",
        "value": 10000,
        "unit": "USD",
    }

    noncurrent_debt = {
        "metric": "noncurrent_debt",
        "status": "MISSING",
        "value": None,
    }

    result = calculate_total_debt(
        current_debt,
        noncurrent_debt,
        "NON_FINANCIAL",
    )

    assert result["status"] == "INCOMPLETE"
    assert result["value"] is None


def test_calculate_total_debt_unit_mismatch_is_incomplete():
    # Round AU requirement C: current_debt and noncurrent_debt must
    # not be summed when their units disagree, even though both
    # statuses are OK and both values are numeric.
    current_debt = {
        "metric": "current_debt",
        "status": "OK",
        "value": 10000,
        "unit": "USD",
    }

    noncurrent_debt = {
        "metric": "noncurrent_debt",
        "status": "OK",
        "value": 30000,
        "unit": "CAD",
    }

    result = calculate_total_debt(
        current_debt,
        noncurrent_debt,
        "NON_FINANCIAL",
    )

    assert result["status"] == "INCOMPLETE"
    assert result["value"] is None
    assert "reason" in result
    assert result.get("unit") is None


def test_calculate_total_debt_missing_unit_metadata_is_incomplete():
    # Round AU requirement C: values present and numeric, but unit
    # metadata absent on both inputs -- must not be assumed
    # compatible and summed as "OK".
    current_debt = {
        "metric": "current_debt",
        "status": "OK",
        "value": 10000,
    }

    noncurrent_debt = {
        "metric": "noncurrent_debt",
        "status": "OK",
        "value": 30000,
    }

    result = calculate_total_debt(
        current_debt,
        noncurrent_debt,
        "NON_FINANCIAL",
    )

    assert result["status"] == "INCOMPLETE"
    assert result["value"] is None
    assert "reason" in result


def test_calculate_total_debt_financial():
    current_debt = {
        "metric": "current_debt",
        "status": "NOT_APPLICABLE",
        "value": None,
    }

    noncurrent_debt = {
        "metric": "noncurrent_debt",
        "status": "NOT_APPLICABLE",
        "value": None,
    }

    result = calculate_total_debt(
        current_debt,
        noncurrent_debt,
        "FINANCIAL",
    )

    assert result["status"] == "NOT_APPLICABLE"
    assert result["value"] is None
    assert "components" in result


# ============================================================
# NORMALIZATION
# ============================================================

def test_normalize_nonfinancial_company():
    data = make_companyfacts_fixture()

    normalized = normalize_company(
        "TEST",
        data,
        "NON_FINANCIAL",
    )

    assert normalized["schema_version"] == "sec_fundamentals_v0.2"
    assert normalized["ticker"] == "TEST"
    assert normalized["entity_name"] == "Test Company"
    assert normalized["cik"] == "0000000000"
    assert normalized["company_type"] == "NON_FINANCIAL"

    assert normalized["source"]["provider"] == "SEC"
    assert normalized["source"]["dataset"] == "Company Facts"
    assert "normalized_at" in normalized

    metrics = normalized["metrics"]

    assert metrics["revenue"]["status"] == "OK"
    assert metrics["revenue"]["value"] == 120000

    assert metrics["net_income"]["status"] == "OK"
    assert metrics["net_income"]["value"] == 20000

    assert metrics["diluted_eps"]["status"] == "OK"
    assert metrics["diluted_eps"]["value"] == 2.50

    assert metrics["operating_income"]["status"] == "OK"
    assert metrics["operating_income"]["value"] == 30000

    assert metrics["cfo"]["status"] == "OK"
    assert metrics["cfo"]["value"] == 25000

    assert metrics["capex"]["status"] == "OK"
    assert metrics["capex"]["value"] == -5000

    assert metrics["cash"]["status"] == "OK"
    assert metrics["cash"]["value"] == 50000

    assert metrics["current_debt"]["status"] == "OK"
    assert metrics["current_debt"]["value"] == 10000

    assert metrics["noncurrent_debt"]["status"] == "OK"
    assert metrics["noncurrent_debt"]["value"] == 30000

    assert metrics["total_debt"]["status"] == "OK"
    assert metrics["total_debt"]["value"] == 40000

    assert metrics["stockholders_equity"]["status"] == "OK"
    assert metrics["stockholders_equity"]["value"] == 80000

    assert metrics["shares_outstanding"]["status"] == "OK"
    assert metrics["shares_outstanding"]["value"] == 10000


def test_normalize_financial_company():
    data = make_companyfacts_fixture()

    normalized = normalize_company(
        "TEST",
        data,
        "FINANCIAL",
    )

    metrics = normalized["metrics"]

    assert metrics["revenue"]["status"] == "OK"
    assert metrics["net_income"]["status"] == "OK"
    assert metrics["diluted_eps"]["status"] == "OK"
    assert metrics["cash"]["status"] == "OK"
    assert metrics["shares_outstanding"]["status"] == "OK"

    assert metrics["operating_income"]["status"] == "NOT_APPLICABLE"
    assert metrics["cfo"]["status"] == "NOT_APPLICABLE"
    assert metrics["capex"]["status"] == "NOT_APPLICABLE"
    assert metrics["current_debt"]["status"] == "NOT_APPLICABLE"
    assert metrics["noncurrent_debt"]["status"] == "NOT_APPLICABLE"
    assert metrics["total_debt"]["status"] == "NOT_APPLICABLE"

    # Equity remains independently extractable for P/B.
    assert metrics["stockholders_equity"]["status"] == "OK"
    assert metrics["stockholders_equity"]["value"] == 80000


# ============================================================
# CROSS-CONCEPT CONFLICT THROUGH normalize_company() (Round AU-R1)
# ============================================================

def test_normalize_company_cross_concept_unit_conflict_propagates_as_incomplete():
    data = make_companyfacts_fixture()

    # Inject a second revenue concept candidate reporting the SAME
    # latest reporting period (2025-01-01..2025-12-31) in a different
    # unit. normalize_company()'s revenue candidate list is
    # ["RevenueFromContractWithCustomerExcludingAssessedTax",
    #  "SalesRevenueNet", "Revenues"], and the fixture's existing
    # "Revenues" concept already supplies the 2025-12-31 USD
    # observation -- so this creates a genuine cross-concept conflict
    # reached through the real normalize_company() entry point, not
    # a synthetic single-function call.
    data["facts"]["us-gaap"][
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    ] = {
        "label": "Revenue (ASC 606)",
        "units": {
            "CAD": [
                {
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "val": 162000,
                    "accn": "0000000000-26-000005",
                    "fy": 2025,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": "2026-02-15",
                }
            ]
        },
    }

    normalized = normalize_company(
        "TEST",
        data,
        "NON_FINANCIAL",
    )

    revenue = normalized["metrics"]["revenue"]

    # 1 & 3: status stays INCOMPLETE, not silently MISSING or OK.
    assert revenue["status"] == "INCOMPLETE"
    assert revenue["status"] != "MISSING"
    assert revenue["status"] != "OK"
    assert revenue["value"] is None

    # 2: ambiguity reason and observed units are preserved end-to-end.
    assert "reason" in revenue
    assert revenue["reason"]
    assert set(revenue["units_observed"]) == {"USD", "CAD"}

    # 5: unaffected single-unit metrics keep their normal OK
    # behavior -- the conflict is isolated to revenue.
    assert normalized["metrics"]["net_income"]["status"] == "OK"
    assert normalized["metrics"]["cfo"]["status"] == "OK"
    assert normalized["metrics"]["cash"]["status"] == "OK"
    assert normalized["metrics"]["total_debt"]["status"] == "OK"
    assert normalized["metrics"]["total_debt"]["value"] == 40000

    # 4: validate_normalized_data() handles the INCOMPLETE revenue
    # under its existing contract (any required metric whose status
    # is not "OK" is a failure) without raising, and does not
    # incorrectly flag unaffected required metrics.
    failures = validate_normalized_data(normalized)

    assert "revenue" in failures
    assert "net_income" not in failures
    assert "cash" not in failures
    assert "total_debt" not in failures


# ============================================================
# NORMALIZED VALIDATION
# ============================================================

def test_validate_normalized_nonfinancial():
    data = make_companyfacts_fixture()

    normalized = normalize_company(
        "TEST",
        data,
        "NON_FINANCIAL",
    )

    failures = validate_normalized_data(normalized)

    assert failures == []


def test_validate_normalized_financial():
    data = make_companyfacts_fixture()

    normalized = normalize_company(
        "TEST",
        data,
        "FINANCIAL",
    )

    failures = validate_normalized_data(normalized)

    assert failures == []


def test_missing_equity_does_not_fail_v01_validation():
    data = make_companyfacts_fixture()
    del data["facts"]["us-gaap"]["StockholdersEquity"]

    normalized = normalize_company(
        "TEST",
        data,
        "NON_FINANCIAL",
    )

    assert (
        normalized["metrics"]["stockholders_equity"]["status"]
        == "MISSING"
    )

    failures = validate_normalized_data(normalized)
    assert failures == []


# ============================================================
# PROVENANCE
# ============================================================

def test_normalized_provenance():
    data = make_companyfacts_fixture()

    normalized = normalize_company(
        "TEST",
        data,
        "NON_FINANCIAL",
    )

    revenue = normalized["metrics"]["revenue"]

    assert revenue["period_start"] == "2025-01-01"
    assert revenue["period_end"] == "2025-12-31"
    assert revenue["filing_date"] == "2026-02-15"
    assert revenue["form"] == "10-K"
    assert revenue["fy"] == 2025
    assert revenue["fp"] == "FY"
    assert revenue["frame"] == "CY2025"
    assert revenue["accession"] == "0000000000-26-000001"


def test_instant_provenance():
    data = make_companyfacts_fixture()

    normalized = normalize_company(
        "TEST",
        data,
        "NON_FINANCIAL",
    )

    cash = normalized["metrics"]["cash"]

    assert cash["period_end"] == "2025-12-31"
    assert cash["filing_date"] == "2026-02-15"
    assert cash["form"] == "10-K"
    assert cash["accession"] == "0000000000-26-000001"


def test_equity_provenance():
    data = make_companyfacts_fixture()

    normalized = normalize_company(
        "TEST",
        data,
        "NON_FINANCIAL",
    )

    equity = normalized["metrics"]["stockholders_equity"]

    assert equity["namespace"] == "us-gaap"
    assert equity["concept"] == "StockholdersEquity"
    assert equity["unit"] == "USD"
    assert equity["period_end"] == "2025-12-31"
    assert equity["filing_date"] == "2026-02-15"
    assert equity["form"] == "10-K"
    assert equity["fy"] == 2025
    assert equity["fp"] == "FY"
    assert equity["frame"] == "CY2025"
    assert equity["accession"] == "0000000000-26-000001"


# ============================================================
# FILE HELPERS
# ============================================================

def test_save_and_load_raw_companyfacts(tmp_path):
    data = make_companyfacts_fixture()

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    path = raw_dir / "TEST_companyfacts.json"

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    loaded = load_raw_companyfacts(
        "TEST",
        raw_dir,
    )

    assert loaded == data


def test_save_normalized_data(tmp_path):
    data = make_companyfacts_fixture()

    normalized = normalize_company(
        "TEST",
        data,
        "NON_FINANCIAL",
    )

    processed_dir = tmp_path / "processed"

    path = save_normalized_data(
        "TEST",
        normalized,
        processed_dir,
    )

    assert path.exists()

    with path.open("r", encoding="utf-8") as f:
        loaded = json.load(f)

    assert loaded["ticker"] == "TEST"
    assert loaded["schema_version"] == "sec_fundamentals_v0.2"
    assert (
        loaded["metrics"]["stockholders_equity"]["value"]
        == 80000
    )


# ============================================================
# NORMALIZE FROM CACHE
# ============================================================

def test_normalize_from_cache(tmp_path):
    data = make_companyfacts_fixture()

    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()

    raw_path = raw_dir / "TEST_companyfacts.json"

    with raw_path.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    result = normalize_from_cache(
        "TEST",
        "NON_FINANCIAL",
        raw_dir,
        processed_dir,
    )

    assert result["ticker"] == "TEST"
    assert result["failures"] == []

    output_path = processed_dir / "TEST_fundamentals.json"

    assert output_path.exists()
    assert result["output_path"] == str(output_path)

    normalized = result["normalized"]

    assert normalized["ticker"] == "TEST"
    assert normalized["metrics"]["revenue"]["value"] == 120000
    assert (
        normalized["metrics"]["stockholders_equity"]["value"]
        == 80000
    )