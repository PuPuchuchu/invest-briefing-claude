"""
STEP 15 (2026-10-27): fcf_yoy carries CapEx scope restrictions per compared
period (metadata only). Value and status of fcf_yoy are unchanged.
"""

from __future__ import annotations

from src.fundamentals.growth import calculate_growth


def _rec(value, period_end, quarter, fy, scope=None, concept=None):
    record = {
        "period_type": "quarterly",
        "quarter": quarter,
        "fy": fy,
        "period_end": period_end,
        "value": value,
    }
    if scope:
        record["concept_scope"] = scope
        record["concept"] = concept
    return record


PA = ("PRODUCTIVE_ASSETS", "PaymentsToAcquireProductiveAssets")


def _historical(capex_prev_scope=None, capex_cur_scope=None):
    cfo = [
        _rec(100.0, "2025-06-30", "Q2", 2025),
        _rec(130.0, "2026-06-30", "Q2", 2026),
    ]
    capex = [
        _rec(-20.0, "2025-06-30", "Q2", 2025, *(capex_prev_scope or (None, None))),
        _rec(-30.0, "2026-06-30", "Q2", 2026, *(capex_cur_scope or (None, None))),
    ]
    return {
        "cfo": {"status": "OK", "quarterly": cfo},
        "capex": {"status": "OK", "quarterly": capex},
    }


def _normalized(capex_scope=None):
    capex = {"status": "OK", "concept": "PaymentsToAcquirePropertyPlantAndEquipment", "value": 1.0}
    if capex_scope:
        capex = {"status": "OK", "concept": PA[1], "concept_scope": capex_scope, "value": 1.0}
    return {"ticker": "T", "company_type": "NON_FINANCIAL", "metrics": {"capex": capex}}


def _fcf_yoy(normalized, historical):
    return calculate_growth(normalized, {"derived_metrics": {}}, historical=historical)["components"]["fcf_yoy"]


EXPECTED_VALUE = (100.0 - 80.0) / 80.0  # (130-30) vs (100-20)


def test_unrestricted_output_unchanged():
    m = _fcf_yoy(_normalized(), _historical())
    assert m["status"] == "OK"
    assert m["value"] == EXPECTED_VALUE
    assert "input_scope" not in m
    assert "peer_comparison_eligibility" not in m


def test_current_period_only_restricted():
    m = _fcf_yoy(_normalized(), _historical(capex_cur_scope=PA))
    assert m["value"] == EXPECTED_VALUE
    assert m["peer_comparison_eligibility"] == "RESTRICTED_INPUT_SCOPE"
    scope = m["input_scope"]["capex"]
    assert scope["current_period"]["concept_scope"] == "PRODUCTIVE_ASSETS"
    assert scope["current_period"]["basis"] == "HISTORY_RECORD"
    assert scope["current_period"]["period_end"] == "2026-06-30"
    assert scope["previous_period"] is None


def test_previous_period_only_restricted():
    m = _fcf_yoy(_normalized(), _historical(capex_prev_scope=PA))
    assert m["value"] == EXPECTED_VALUE
    assert m["peer_comparison_eligibility"] == "RESTRICTED_INPUT_SCOPE"
    scope = m["input_scope"]["capex"]
    assert scope["current_period"] is None
    assert scope["previous_period"]["period_end"] == "2025-06-30"


def test_both_periods_restricted_by_history_records():
    m = _fcf_yoy(_normalized(), _historical(capex_prev_scope=PA, capex_cur_scope=PA))
    scope = m["input_scope"]["capex"]
    assert scope["current_period"]["concept"] == PA[1]
    assert scope["previous_period"]["concept"] == PA[1]


def test_both_periods_restricted_via_selected_capex_concept():
    # Production wiring: CapEx history is built from the concept the
    # normalizer selected, so its scope applies to every unlabelled period.
    m = _fcf_yoy(_normalized("PRODUCTIVE_ASSETS"), _historical())
    assert m["value"] == EXPECTED_VALUE
    scope = m["input_scope"]["capex"]
    assert scope["current_period"]["basis"] == "SELECTED_CAPEX_CONCEPT"
    assert scope["previous_period"]["basis"] == "SELECTED_CAPEX_CONCEPT"


def test_non_ok_fcf_yoy_gets_no_scope_metadata():
    hist = _historical(capex_cur_scope=PA)
    hist["cfo"]["quarterly"] = hist["cfo"]["quarterly"][1:]  # no prior-year quarter
    m = _fcf_yoy(_normalized(), hist)
    assert m["status"] != "OK"
    assert "input_scope" not in m
