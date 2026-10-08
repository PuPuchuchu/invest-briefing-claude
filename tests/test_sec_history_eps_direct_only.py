"""
Regression coverage for the 2026-10-07 EPS standalone-quarter
reconstruction fix (see claude/2026-10-07-sec-companyfacts-audit.md
section E.1, and the QA report this fix was approved under).

Confirmed root cause: `src/fundamentals/sec_history.py`'s
`reconstruct_standalone_quarters()` is metric-agnostic and
unconditionally computed Q2 = H1 - Q1, Q3 = 9M - H1, Q4 = FY - 9M for
every concept, including `EarningsPerShareDiluted`. Diluted EPS is a
per-share ratio (net income / weighted-average diluted shares), not an
additive cumulative flow -- subtracting cumulative EPS figures does
not yield the correct standalone-quarter EPS whenever the
weighted-average diluted share count differs between the two periods.

The fix: `reconstruct_standalone_quarters()` (and its caller
`extract_concept_history()`) now accept an optional `concept_name`.
When that name is in `NON_ADDITIVE_PER_SHARE_CONCEPTS` (currently just
`EarningsPerShareDiluted`), reconstruction is restricted to direct
(already non-cumulative) standalone-quarter observations only --
`_reconstruct_direct_only_quarters()`. A quarter with no genuinely
direct observation is simply absent from the result, never fabricated
from YTD data. Every other concept (revenue, net_income,
operating_income, cfo, capex) is completely unaffected -- the
`concept_name` parameter defaults to `None`, which preserves the
pre-fix subtraction-based behavior exactly.

This file never modifies, mocks, or bypasses
`src/fundamentals/sec_history.py`, `point_in_time.py`, or `growth.py`
-- every test below calls the real, unmodified production functions.
"""

from __future__ import annotations

from datetime import date

from src.fundamentals.derived_metrics import calculate_ttm_from_quarters
from src.fundamentals.growth import calculate_growth
from src.fundamentals.point_in_time import get_point_in_time_history
from src.fundamentals.sec_history import (
    NON_ADDITIVE_PER_SHARE_CONCEPTS,
    extract_concept_history,
    reconstruct_standalone_quarters,
)

EPS_CONCEPT = "EarningsPerShareDiluted"


# ============================================================
# FIXTURES
# ============================================================

def make_direct_quarter(
    *,
    fy,
    fp,
    start,
    end,
    value,
    filed,
    accession,
    form="10-Q",
):
    """A genuinely direct (non-cumulative, ~3-month) observation."""
    return {
        "start": start,
        "end": end,
        "filed": filed,
        "form": form,
        "fy": fy,
        "fp": fp,
        "value": value,
        "accession": accession,
    }


def make_ytd(
    *,
    fy,
    fp,
    start,
    end,
    value,
    filed,
    accession,
    form="10-Q",
):
    return {
        "start": start,
        "end": end,
        "filed": filed,
        "form": form,
        "fy": fy,
        "fp": fp,
        "value": value,
        "accession": accession,
    }


def make_fy_annual(
    *,
    fy,
    start,
    end,
    value,
    filed,
    accession,
):
    return {
        "start": start,
        "end": end,
        "filed": filed,
        "form": "10-K",
        "fy": fy,
        "fp": "FY",
        "value": value,
        "accession": accession,
    }


# ============================================================
# TEST 1 -- direct quarterly EPS is preserved
# ============================================================

def test_direct_quarterly_eps_is_preserved():
    observations = [
        make_direct_quarter(
            fy=2025, fp="Q1", start="2025-01-01", end="2025-03-31",
            value=0.50, filed="2025-05-01", accession="acc-q1",
        ),
        make_direct_quarter(
            fy=2025, fp="Q2", start="2025-04-01", end="2025-06-30",
            value=0.55, filed="2025-08-01", accession="acc-q2",
        ),
        make_direct_quarter(
            fy=2025, fp="Q3", start="2025-07-01", end="2025-09-30",
            value=0.60, filed="2025-11-01", accession="acc-q3",
        ),
        make_direct_quarter(
            fy=2025, fp="Q4", start="2025-10-01", end="2025-12-31",
            value=0.65, filed="2026-02-15", accession="acc-q4",
            form="10-K",
        ),
    ]

    result = reconstruct_standalone_quarters(
        observations,
        concept_name=EPS_CONCEPT,
    )

    assert len(result) == 4

    by_quarter = {record["quarter"]: record for record in result}

    assert by_quarter["Q1"]["value"] == 0.50
    assert by_quarter["Q2"]["value"] == 0.55
    assert by_quarter["Q3"]["value"] == 0.60
    assert by_quarter["Q4"]["value"] == 0.65

    for record in result:
        assert record["reconstructed"] is False
        assert record["source"]["previous"] is None
        assert record["source"]["current"] is not None


# ============================================================
# TEST 2 -- EPS YTD subtraction is forbidden
# ============================================================

def test_eps_ytd_subtraction_is_forbidden():
    # Deliberately NOT mathematically convenient -- cumulative EPS
    # does not move monotonically here, so a naive subtraction would
    # produce an obviously-wrong (even negative) "quarter" value if
    # it were ever computed. No direct Q2/Q3/Q4 observation is
    # supplied at all.
    observations = [
        make_direct_quarter(
            fy=2025, fp="Q1", start="2025-01-01", end="2025-03-31",
            value=0.50, filed="2025-05-01", accession="acc-q1",
        ),
        make_ytd(
            fy=2025, fp="Q2", start="2025-01-01", end="2025-06-30",
            value=1.30, filed="2025-08-01", accession="acc-h1",
        ),
        make_ytd(
            fy=2025, fp="Q3", start="2025-01-01", end="2025-09-30",
            value=1.50, filed="2025-11-01", accession="acc-9m",
        ),
        make_fy_annual(
            fy=2025, start="2025-01-01", end="2025-12-31",
            value=1.00, filed="2026-02-15", accession="acc-fy",
        ),
    ]

    result = reconstruct_standalone_quarters(
        observations,
        concept_name=EPS_CONCEPT,
    )

    quarters_present = {record["quarter"] for record in result}

    # Naive (forbidden) subtraction would have produced:
    #   Q2 = 1.30 - 0.50 = 0.80
    #   Q3 = 1.50 - 1.30 = 0.20
    #   Q4 = 1.00 - 1.50 = -0.50
    # None of these may appear.
    assert "Q2" not in quarters_present
    assert "Q3" not in quarters_present
    assert "Q4" not in quarters_present

    # Only the genuinely direct Q1 observation is retained.
    assert quarters_present == {"Q1"}

    for record in result:
        assert record["reconstructed"] is False


# ============================================================
# TEST 3 -- YTD-only EPS remains missing
# ============================================================

def test_ytd_only_eps_remains_missing():
    observations = [
        make_ytd(
            fy=2025, fp="Q2", start="2025-01-01", end="2025-06-30",
            value=1.10, filed="2025-08-01", accession="acc-h1",
        ),
        make_ytd(
            fy=2025, fp="Q3", start="2025-01-01", end="2025-09-30",
            value=1.70, filed="2025-11-01", accession="acc-9m",
        ),
        make_fy_annual(
            fy=2025, start="2025-01-01", end="2025-12-31",
            value=2.40, filed="2026-02-15", accession="acc-fy",
        ),
    ]

    result = reconstruct_standalone_quarters(
        observations,
        concept_name=EPS_CONCEPT,
    )

    # No direct standalone observation exists anywhere -- nothing is
    # synthesized from the YTD/FY data, so the result is empty. This
    # is the module's existing "absent = not available" convention,
    # not a fabricated zero or placeholder.
    assert result == []


# ============================================================
# TEST 4 -- mixed direct / YTD EPS
# ============================================================

def test_mixed_direct_and_ytd_eps():
    observations = [
        # Q1: direct only.
        make_direct_quarter(
            fy=2025, fp="Q1", start="2025-01-01", end="2025-03-31",
            value=0.40, filed="2025-05-01", accession="acc-q1",
        ),
        # Q2: BOTH a direct observation and the YTD (H1) cumulative
        # observation exist for the same fiscal year -- the direct
        # one must win, unmodified.
        make_direct_quarter(
            fy=2025, fp="Q2", start="2025-04-01", end="2025-06-30",
            value=0.45, filed="2025-08-01", accession="acc-q2-direct",
        ),
        make_ytd(
            fy=2025, fp="Q2", start="2025-01-01", end="2025-06-30",
            value=0.90, filed="2025-08-01", accession="acc-h1",
        ),
        # Q3: YTD (9M) only -- no direct Q3 observation at all.
        make_ytd(
            fy=2025, fp="Q3", start="2025-01-01", end="2025-09-30",
            value=1.50, filed="2025-11-01", accession="acc-9m",
        ),
        # Q4: direct observation only (no separate annual duplicate
        # confusion).
        make_direct_quarter(
            fy=2025, fp="Q4", start="2025-10-01", end="2025-12-31",
            value=0.55, filed="2026-02-15", accession="acc-q4",
            form="10-K",
        ),
    ]

    result = reconstruct_standalone_quarters(
        observations,
        concept_name=EPS_CONCEPT,
    )

    by_quarter = {record["quarter"]: record for record in result}

    # Direct quarterly EPS takes precedence; Q2 is not overwritten or
    # averaged with the YTD figure, and no "derived" value based on
    # 0.90 (H1) appears anywhere.
    assert set(by_quarter) == {"Q1", "Q2", "Q4"}
    assert by_quarter["Q1"]["value"] == 0.40
    assert by_quarter["Q2"]["value"] == 0.45
    assert by_quarter["Q4"]["value"] == 0.55

    # Q3, which had only a YTD observation, is simply absent -- never
    # derived from the 9M figure.
    assert "Q3" not in by_quarter

    for record in result:
        assert record["reconstructed"] is False


# ============================================================
# ADDITIONAL GUARD -- Q1-Q3 direct, Q4 missing: no zero-fill,
# no fabricated TTM/YoY (2026-10-07 commit-readiness review)
# ============================================================

def test_q1_to_q3_direct_q4_missing_no_zero_fill_or_ttm_fabrication():
    observations = [
        make_direct_quarter(
            fy=2025, fp="Q1", start="2025-01-01", end="2025-03-31",
            value=0.40, filed="2025-05-01", accession="acc-q1",
        ),
        make_direct_quarter(
            fy=2025, fp="Q2", start="2025-04-01", end="2025-06-30",
            value=0.45, filed="2025-08-01", accession="acc-q2",
        ),
        make_direct_quarter(
            fy=2025, fp="Q3", start="2025-07-01", end="2025-09-30",
            value=0.50, filed="2025-11-01", accession="acc-q3",
        ),
        # No Q4 observation at all -- neither direct nor FY/YTD.
    ]

    result = reconstruct_standalone_quarters(
        observations,
        concept_name=EPS_CONCEPT,
    )

    by_quarter = {record["quarter"]: record for record in result}

    assert set(by_quarter) == {"Q1", "Q2", "Q3"}
    assert "Q4" not in by_quarter

    # No entry anywhere carries a fabricated zero (or any) Q4 value.
    assert all(record["value"] != 0.0 for record in result)
    assert not any(record.get("quarter") == "Q4" for record in result)

    # The project's generic TTM helper (src/fundamentals/
    # derived_metrics.py) independently enforces the same "unknown is
    # not zero" rule: with only 3 of the 4 required standalone
    # quarters, it must report MISSING, never silently treat the
    # absent Q4 as 0 and sum the remaining three.
    quarterly_values = [record["value"] for record in result]
    ttm = calculate_ttm_from_quarters("diluted_eps", quarterly_values)

    assert ttm["status"] == "MISSING"
    assert ttm.get("value") is None


def test_fy_annual_only_does_not_become_q4():
    """
    Isolates the Q4 edge case from Test 3's broader fixture: with
    ONLY an annual (10-K, ~365-day, fp="FY") EPS observation and no
    direct standalone-quarter observation at all, no Q4 (or any
    other quarter) is fabricated from the annual figure.
    """
    observations = [
        make_fy_annual(
            fy=2025, start="2025-01-01", end="2025-12-31",
            value=2.00, filed="2026-02-15", accession="acc-fy",
        ),
    ]

    result = reconstruct_standalone_quarters(
        observations,
        concept_name=EPS_CONCEPT,
    )

    assert result == []


# ============================================================
# TEST 5 -- other additive metrics are unaffected
# ============================================================

def test_additive_metrics_still_reconstruct_normally():
    # Revenue: Q1 direct + H1 YTD -> Q2 must still be reconstructed via
    # subtraction, exactly as before this fix.
    revenue_observations = [
        make_direct_quarter(
            fy=2025, fp="Q1", start="2025-01-01", end="2025-03-31",
            value=100.0, filed="2025-05-01", accession="acc-rev-q1",
        ),
        make_ytd(
            fy=2025, fp="Q2", start="2025-01-01", end="2025-06-30",
            value=230.0, filed="2025-08-01", accession="acc-rev-h1",
        ),
    ]

    # Called with NO concept_name (the default every pre-existing
    # caller of this function already used) -- behavior must be
    # byte-for-byte unchanged.
    result_default = reconstruct_standalone_quarters(revenue_observations)

    # Also explicitly confirm passing a non-EPS concept_name does not
    # change anything either.
    result_named = reconstruct_standalone_quarters(
        revenue_observations,
        concept_name="NetIncomeLoss",
    )

    for result in (result_default, result_named):
        by_quarter = {record["quarter"]: record for record in result}

        assert by_quarter["Q1"]["value"] == 100.0
        assert by_quarter["Q1"]["reconstructed"] is False

        assert "Q2" in by_quarter
        assert by_quarter["Q2"]["value"] == 230.0 - 100.0
        assert by_quarter["Q2"]["reconstructed"] is True


def test_eps_fix_does_not_remove_other_metrics_from_reconstruction():
    """
    Explicit guard requested alongside Test 5: confirm the EPS
    gate itself is concept-scoped, not accidentally global, by
    checking every one of the other RECONSTRUCTABLE metrics named in
    the approved scope still reconstructs when called with its own
    real concept name.
    """
    for concept_name in (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "NetIncomeLoss",
        "OperatingIncomeLoss",
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ):
        assert concept_name not in NON_ADDITIVE_PER_SHARE_CONCEPTS

        observations = [
            make_direct_quarter(
                fy=2025, fp="Q1", start="2025-01-01", end="2025-03-31",
                value=10.0, filed="2025-05-01", accession="acc-q1",
            ),
            make_ytd(
                fy=2025, fp="Q2", start="2025-01-01", end="2025-06-30",
                value=22.0, filed="2025-08-01", accession="acc-h1",
            ),
        ]

        result = reconstruct_standalone_quarters(
            observations,
            concept_name=concept_name,
        )

        by_quarter = {record["quarter"]: record for record in result}

        assert "Q2" in by_quarter
        assert by_quarter["Q2"]["reconstructed"] is True
        assert by_quarter["Q2"]["value"] == 12.0


# ============================================================
# TEST 6 -- downstream EPS YoY (real production path)
# ============================================================

def _companyfacts_with_eps(observations):
    return {
        "cik": "0000000001",
        "entityName": "Test Co",
        "facts": {
            "us-gaap": {
                "EarningsPerShareDiluted": {
                    "label": "Earnings Per Share, Diluted",
                    "units": {
                        "USD/shares": observations,
                    },
                },
            },
        },
    }


def test_downstream_eps_yoy_uses_only_direct_quarters():
    """
    Executes the actual, unmodified production path:

        raw Companyfacts dict
        -> point_in_time.get_point_in_time_history()  (PIT gate +
           sec_history.extract_concept_history(), the exact call
           orchestrator.py makes)
        -> growth.calculate_growth()  (the same "quarterly" shape
           orchestrator._growth_historical_slice() builds from
           history["standalone_quarters"])

    and proves derived/YTD-subtraction EPS never reaches Growth.
    """
    observations = [
        # FY2025 Q2 direct (prior-year comparison quarter).
        make_direct_quarter(
            fy=2025, fp="Q2", start="2025-04-01", end="2025-06-30",
            value=1.00, filed="2025-08-01", accession="acc-2025-q2",
        ),
        # FY2025 Q2 YTD/H1 -- present alongside the direct observation,
        # with a value that would produce an obviously different
        # (wrong) number if ever subtracted against Q1.
        make_direct_quarter(
            fy=2025, fp="Q1", start="2025-01-01", end="2025-03-31",
            value=0.80, filed="2025-05-01", accession="acc-2025-q1",
        ),
        make_ytd(
            fy=2025, fp="Q2", start="2025-01-01", end="2025-06-30",
            value=5.00, filed="2025-08-01", accession="acc-2025-h1",
        ),
        # FY2026 Q2 direct (latest quarter).
        make_direct_quarter(
            fy=2026, fp="Q2", start="2026-04-01", end="2026-06-30",
            value=1.20, filed="2026-08-01", accession="acc-2026-q2",
        ),
        # FY2026 Q1 direct + a deliberately inconsistent H1 YTD value,
        # so that if subtraction-derived Q2 ever leaked through, it
        # would be numerically distinguishable (1.20 direct vs.
        # 9.00 - 0.90 = 8.10 "derived").
        make_direct_quarter(
            fy=2026, fp="Q1", start="2026-01-01", end="2026-03-31",
            value=0.90, filed="2026-05-01", accession="acc-2026-q1",
        ),
        make_ytd(
            fy=2026, fp="Q2", start="2026-01-01", end="2026-06-30",
            value=9.00, filed="2026-08-01", accession="acc-2026-h1",
        ),
    ]

    data = _companyfacts_with_eps(observations)

    history = get_point_in_time_history(
        data,
        "us-gaap",
        "EarningsPerShareDiluted",
        date(2026, 10, 1),
    )

    assert history is not None

    standalone = history["standalone_quarters"]

    # Every record the production PIT path produced must be direct
    # (reconstructed == False) -- no YTD-subtraction entry exists to
    # leak downstream.
    for record in standalone:
        assert record["reconstructed"] is False

    values_by_key = {
        (record["fy"], record["quarter"]): record["value"]
        for record in standalone
    }

    assert values_by_key[(2025, "Q2")] == 1.00
    assert values_by_key[(2026, "Q2")] == 1.20
    # The "derived" 8.10 value must never appear anywhere.
    assert 8.10 not in values_by_key.values()

    normalized = {
        "ticker": "TEST",
        "company_type": "NON_FINANCIAL",
        "metrics": {},
    }
    derived = {
        "schema_version": "derived_metrics_v0.1",
        "ticker": "TEST",
        "derived_metrics": {},
    }
    historical = {
        "diluted_eps": {
            "status": "OK",
            # Same shape orchestrator._growth_historical_slice() builds:
            # history.get("standalone_quarters") fed directly as the
            # "quarterly" list.
            "quarterly": standalone,
        },
    }

    result = calculate_growth(
        normalized,
        derived,
        historical,
        enabled_metrics={"eps_yoy"},
    )

    component = result["components"]["eps_yoy"]

    assert component["status"] == "OK"
    # (1.20 / 1.00) - 1 = 20%, using only the two direct observations.
    assert abs(component["value"] - 0.20) < 1e-9


def test_downstream_eps_yoy_missing_when_no_prior_year_direct_quarter():
    """
    Only one direct quarter exists at all (no prior-year comparable
    direct quarter) -- even though a full YTD/FY series is present
    that COULD have been used to fabricate a comparison, the
    production path must report this as insufficient rather than
    substituting anything.
    """
    observations = [
        make_direct_quarter(
            fy=2026, fp="Q2", start="2026-04-01", end="2026-06-30",
            value=1.20, filed="2026-08-01", accession="acc-2026-q2",
        ),
        # Only a YTD (not direct) prior-year Q2 exists -- this must
        # NOT be used to fabricate a comparison quarter.
        make_ytd(
            fy=2025, fp="Q2", start="2025-01-01", end="2025-06-30",
            value=5.00, filed="2025-08-01", accession="acc-2025-h1",
        ),
    ]

    data = _companyfacts_with_eps(observations)

    history = get_point_in_time_history(
        data,
        "us-gaap",
        "EarningsPerShareDiluted",
        date(2026, 10, 1),
    )

    standalone = history["standalone_quarters"]

    # No FY2025 Q2 direct observation exists, so only one EPS quarter
    # is available at all.
    assert len(standalone) == 1
    assert standalone[0]["fy"] == 2026
    assert standalone[0]["quarter"] == "Q2"

    normalized = {
        "ticker": "TEST",
        "company_type": "NON_FINANCIAL",
        "metrics": {},
    }
    derived = {
        "schema_version": "derived_metrics_v0.1",
        "ticker": "TEST",
        "derived_metrics": {},
    }
    historical = {
        "diluted_eps": {
            "status": "OK",
            "quarterly": standalone,
        },
    }

    result = calculate_growth(
        normalized,
        derived,
        historical,
        enabled_metrics={"eps_yoy"},
    )

    component = result["components"]["eps_yoy"]

    # Insufficient direct history -- must remain missing, never
    # zero-filled or substituted from the 5.00/0.80 YTD data sitting
    # right next to it.
    assert component["status"] != "OK"
    assert component.get("value") is None


# ============================================================
# extract_concept_history() integration (concept_name threading)
# ============================================================

def test_extract_concept_history_threads_concept_name():
    concept_data = {
        "label": "Earnings Per Share, Diluted",
        "units": {
            "USD/shares": [
                make_direct_quarter(
                    fy=2025, fp="Q1", start="2025-01-01", end="2025-03-31",
                    value=0.50, filed="2025-05-01", accession="acc-q1",
                ),
                make_ytd(
                    fy=2025, fp="Q2", start="2025-01-01", end="2025-06-30",
                    value=1.30, filed="2025-08-01", accession="acc-h1",
                ),
            ],
        },
    }

    with_concept = extract_concept_history(
        concept_data,
        concept_name=EPS_CONCEPT,
    )
    without_concept = extract_concept_history(concept_data)

    with_quarters = {r["quarter"] for r in with_concept["standalone_quarters"]}
    without_quarters = {r["quarter"] for r in without_concept["standalone_quarters"]}

    # With concept_name supplied: no Q2 (would require subtraction).
    assert with_quarters == {"Q1"}

    # Without concept_name (pre-fix default behavior, unchanged):
    # Q2 is still reconstructed via subtraction, exactly as before.
    assert "Q2" in without_quarters
