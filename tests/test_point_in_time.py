"""
Unit tests for src/fundamentals/point_in_time.py.

Pure Unit Tests in the taxonomy sense: no network, synthetic fixtures
only. These specifically exercise the look-ahead-bias gate (filed_date
<= evaluation_date), which is the one piece of discipline this module
adds on top of the already-tested sec_normalizer.find_concept() and
sec_history.extract_concept_history().
"""

from datetime import date

import pytest

from src.fundamentals.point_in_time import (
    SCHEMA_VERSION,
    coerce_evaluation_date,
    is_observation_available_as_of,
    filter_observations_as_of,
    as_of_concept_data,
    get_point_in_time_history,
    validate_point_in_time_history,
)


# ============================================================
# coerce_evaluation_date
# ============================================================

def test_coerce_evaluation_date_from_string():
    assert coerce_evaluation_date("2026-06-15") == date(2026, 6, 15)


def test_coerce_evaluation_date_from_date_object():
    d = date(2026, 6, 15)
    assert coerce_evaluation_date(d) == d


def test_coerce_evaluation_date_rejects_garbage():
    with pytest.raises(ValueError):
        coerce_evaluation_date("not-a-date")


def test_coerce_evaluation_date_rejects_none():
    with pytest.raises(ValueError):
        coerce_evaluation_date(None)


# ============================================================
# is_observation_available_as_of
# ============================================================

def test_observation_filed_before_evaluation_date_is_available():
    obs = {"filed": "2025-02-01", "val": 100}
    assert is_observation_available_as_of(obs, date(2025, 3, 1)) is True


def test_observation_filed_exactly_on_evaluation_date_is_available():
    obs = {"filed": "2025-02-01", "val": 100}
    assert is_observation_available_as_of(obs, date(2025, 2, 1)) is True


def test_observation_filed_after_evaluation_date_is_not_available():
    obs = {"filed": "2025-02-01", "val": 100}
    assert is_observation_available_as_of(obs, date(2025, 1, 1)) is False


def test_observation_missing_filed_date_fails_closed():
    obs = {"val": 100}
    assert is_observation_available_as_of(obs, date(2099, 1, 1)) is False


def test_observation_unparseable_filed_date_fails_closed():
    obs = {"filed": "not-a-date", "val": 100}
    assert is_observation_available_as_of(obs, date(2099, 1, 1)) is False


# ============================================================
# filter_observations_as_of
# ============================================================

def test_filter_observations_as_of_keeps_only_available():
    observations = [
        {"filed": "2024-02-01", "val": 1},
        {"filed": "2025-02-01", "val": 2},
        {"filed": "2026-02-01", "val": 3},
    ]

    result = filter_observations_as_of(observations, date(2025, 6, 1))

    assert [o["val"] for o in result] == [1, 2]


def test_filter_observations_as_of_preserves_fields_unchanged():
    observations = [
        {"filed": "2024-02-01", "val": 1, "accn": "acc-1", "end": "2023-12-31"},
    ]

    result = filter_observations_as_of(observations, date(2025, 1, 1))

    assert result[0] == observations[0]


# ============================================================
# as_of_concept_data
# ============================================================

def test_as_of_concept_data_gates_each_unit():
    concept_data = {
        "label": "Revenues",
        "units": {
            "USD": [
                {"filed": "2024-02-01", "val": 100},
                {"filed": "2026-02-01", "val": 200},
            ],
        },
    }

    result = as_of_concept_data(concept_data, date(2025, 1, 1))

    assert [o["val"] for o in result["units"]["USD"]] == [100]
    assert result["label"] == "Revenues"


def test_as_of_concept_data_does_not_mutate_input():
    concept_data = {
        "label": "Revenues",
        "units": {
            "USD": [
                {"filed": "2026-02-01", "val": 200},
            ],
        },
    }

    as_of_concept_data(concept_data, date(2020, 1, 1))

    # Original must be untouched.
    assert len(concept_data["units"]["USD"]) == 1
    assert concept_data["units"]["USD"][0]["val"] == 200


def test_as_of_concept_data_drops_malformed_entries_defensively():
    concept_data = {
        "units": {
            "USD": [
                {"filed": "2024-02-01", "val": 100},
                "not-a-dict",
            ],
            "shares": "not-a-list",
        },
    }

    result = as_of_concept_data(concept_data, date(2025, 1, 1))

    assert [o["val"] for o in result["units"]["USD"]] == [100]
    # A non-list "units" entry is skipped entirely (not fabricated as an
    # empty list) -- it never had observations to gate in the first place.
    assert "shares" not in result["units"]


def test_as_of_concept_data_requires_units_dict():
    with pytest.raises(ValueError, match="units"):
        as_of_concept_data({"label": "x"}, date(2025, 1, 1))


def test_as_of_concept_data_requires_dict_input():
    with pytest.raises(ValueError):
        as_of_concept_data(None, date(2025, 1, 1))


# ============================================================
# get_point_in_time_history (end-to-end: lookup -> gate -> reconstruct)
# ============================================================

def _companyfacts_with_fy2024_and_fy2025():
    return {
        "cik": 1,
        "entityName": "TEST CO",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 1000,
                                "filed": "2025-02-01",
                                "form": "10-K",
                                "fy": 2024,
                                "fp": "FY",
                                "accn": "0000000000-25-000001",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 1200,
                                "filed": "2026-02-01",
                                "form": "10-K",
                                "fy": 2025,
                                "fp": "FY",
                                "accn": "0000000000-26-000001",
                            },
                        ],
                    },
                },
            },
        },
    }


def test_get_point_in_time_history_returns_none_for_missing_concept():
    data = {"cik": 1, "entityName": "TEST CO", "facts": {"us-gaap": {}}}

    result = get_point_in_time_history(data, "us-gaap", "Revenues", "2026-01-01")

    assert result is None


def test_get_point_in_time_history_excludes_not_yet_filed_fy():
    """Evaluating as of a date BEFORE the FY2025 10-K was filed must not
    see FY2025 -- this is the actual look-ahead-bias check."""
    data = _companyfacts_with_fy2024_and_fy2025()

    result = get_point_in_time_history(data, "us-gaap", "Revenues", "2025-06-01")

    fiscal_years = [row["fy"] for row in result["annual"]]
    assert fiscal_years == [2024]
    assert 2025 not in fiscal_years


def test_get_point_in_time_history_includes_fy_once_filed():
    """Evaluating as of a date AFTER the FY2025 10-K was filed must see
    both fiscal years."""
    data = _companyfacts_with_fy2024_and_fy2025()

    result = get_point_in_time_history(data, "us-gaap", "Revenues", "2026-03-01")

    fiscal_years = sorted(row["fy"] for row in result["annual"])
    assert fiscal_years == [2024, 2025]


def test_get_point_in_time_history_before_any_filing_is_empty_not_error():
    """A newly-listed company evaluated before its first filing existed
    must come back as an empty-but-valid history, not an exception."""
    data = _companyfacts_with_fy2024_and_fy2025()

    result = get_point_in_time_history(data, "us-gaap", "Revenues", "2024-01-01")

    assert result is not None
    assert result["annual"] == []
    # extract_quarterly_history() always returns these four buckets,
    # each empty rather than the dict being empty outright.
    assert result["quarterly"] == {
        "q1": [],
        "ytd_6m": [],
        "ytd_9m": [],
        "unknown": [],
    }
    assert result["standalone_quarters"] == []


def test_get_point_in_time_history_carries_metadata():
    data = _companyfacts_with_fy2024_and_fy2025()

    result = get_point_in_time_history(data, "us-gaap", "Revenues", "2026-03-01")

    assert result["evaluation_date"] == "2026-03-01"
    assert result["namespace"] == "us-gaap"
    assert result["concept"] == "Revenues"
    assert result["schema_version"] == "sec_history_v0.1"  # from sec_history.py, unchanged


def test_module_has_its_own_schema_version():
    assert SCHEMA_VERSION == "point_in_time_v0.1"


# ============================================================
# validate_point_in_time_history
# ============================================================
# Regression coverage (2026-09-19): validate_point_in_time_history()
# was added alongside wiring get_point_in_time_history() to
# self-validate its own output (see that function's docstring). These
# tests exercise the validator directly with hand-crafted results,
# since get_point_in_time_history()'s own gating already prevents a
# real look-ahead-bias leak through the public API -- the whole point
# of this validator is to catch that leak if a future bug in
# sec_normalizer.find_concept or sec_history.extract_concept_history
# ever reintroduces one upstream of it.

def _valid_synthetic_result():
    """A hand-built, already-valid get_point_in_time_history()-shaped
    result, used as the baseline that individual tests below corrupt
    one field at a time."""
    return {
        "schema_version": "sec_history_v0.1",
        "label": "Revenues",
        "description": None,
        "annual": [
            {
                "start": "2024-01-01",
                "end": "2024-12-31",
                "val": 1000,
                "filed": "2025-02-01",
                "form": "10-K",
                "fy": 2024,
                "fp": "FY",
                "accn": "0000000000-25-000001",
            },
        ],
        "quarterly": {
            "q1": [
                {
                    "start": "2025-01-01",
                    "end": "2025-03-31",
                    "val": 250,
                    "filed": "2025-05-01",
                    "form": "10-Q",
                    "fy": 2025,
                    "fp": "Q1",
                    "accn": "0000000000-25-000002",
                },
            ],
            "ytd_6m": [],
            "ytd_9m": [],
            "unknown": [],
        },
        "standalone_quarters": [
            {
                "quarter": "Q1",
                "value": 250,
                "period_start": "2025-01-01",
                "period_end": "2025-03-31",
                "filed": "2025-05-01",
                "form": "10-Q",
                "fy": 2025,
                "accession": "0000000000-25-000002",
                "reconstructed": False,
                "source": {
                    "current": {
                        "filed": "2025-05-01",
                        "val": 250,
                    },
                    "previous": None,
                },
            },
        ],
        "evaluation_date": "2025-06-01",
        "namespace": "us-gaap",
        "concept": "Revenues",
    }


def test_validate_point_in_time_history_passes_for_valid_result():
    result = _valid_synthetic_result()

    assert validate_point_in_time_history(result, "2025-06-01") == []


def test_validate_point_in_time_history_rejects_none():
    assert validate_point_in_time_history(None, "2025-06-01") == ["root"]


def test_validate_point_in_time_history_passes_for_real_get_point_in_time_history():
    """End-to-end sanity check: a real result produced by
    get_point_in_time_history() itself must also validate clean --
    otherwise get_point_in_time_history() would already be raising
    (it self-validates before returning), so this mainly guards
    against the two functions' expectations silently drifting apart."""
    data = _companyfacts_with_fy2024_and_fy2025()

    result = get_point_in_time_history(data, "us-gaap", "Revenues", "2026-03-01")

    assert validate_point_in_time_history(result, "2026-03-01") == []


def test_validate_point_in_time_history_catches_evaluation_date_mismatch():
    result = _valid_synthetic_result()
    result["evaluation_date"] = "2025-06-01"

    # Validating against a DIFFERENT evaluation_date than the one
    # baked into the result must be flagged, not silently accepted.
    failures = validate_point_in_time_history(result, "2026-01-01")

    assert "evaluation_date_mismatch" in failures


def test_validate_point_in_time_history_catches_look_ahead_bias_in_annual():
    result = _valid_synthetic_result()

    # Corrupt the annual record's filed date to be AFTER the
    # evaluation_date it's supposed to respect.
    result["annual"][0]["filed"] = "2025-12-31"

    failures = validate_point_in_time_history(result, "2025-06-01")

    assert "annual.look_ahead_bias" in failures


def test_validate_point_in_time_history_catches_look_ahead_bias_in_quarterly_bucket():
    result = _valid_synthetic_result()

    result["quarterly"]["q1"][0]["filed"] = "2025-12-31"

    failures = validate_point_in_time_history(result, "2025-06-01")

    assert "quarterly.q1.look_ahead_bias" in failures


def test_validate_point_in_time_history_catches_look_ahead_bias_in_derived_quarter_source():
    """The top-level 'filed' on a standalone quarter can look fine while
    a cumulative observation buried in its own "source" block (used to
    derive it, e.g. Q2 = H1 - Q1) was actually filed too late -- this
    must still be caught, not just the top-level field."""
    result = _valid_synthetic_result()

    result["standalone_quarters"][0]["source"]["previous"] = {
        "filed": "2025-12-31",
        "val": 100,
    }

    failures = validate_point_in_time_history(result, "2025-06-01")

    assert "standalone_quarters.look_ahead_bias" in failures


def test_validate_point_in_time_history_reuses_base_structural_checks():
    """validate_point_in_time_history() must not reinvent
    sec_history.validate_history()'s own structural checks -- a missing
    required key should still be caught via that reuse."""
    result = _valid_synthetic_result()
    del result["annual"]

    failures = validate_point_in_time_history(result, "2025-06-01")

    assert "annual" in failures