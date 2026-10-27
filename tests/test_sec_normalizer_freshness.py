"""
STEP 15 (2026-10-27): freshness check and candidate additions in
sec_normalizer.normalize_company().

All fixtures are synthetic. They reproduce the shapes found in real SEC
companyfacts during the step15-batch-runner CI run (a concept abandoned
years ago, a tag change, one-quarter-old balance-sheet values) without
hard-coding any real issuer's values.
"""

from __future__ import annotations

from src.fundamentals.derived_metrics import calculate_derived_metrics
from src.fundamentals.sec_normalizer import (
    FRESHNESS_TOLERANCE_DAYS,
    annual_freshness_anchor,
    instant_freshness_anchor,
    normalize_company,
    validate_normalized_data,
)


def _annual(val, end, start=None, filed=None, unit="USD"):
    year = int(end[:4])
    return {
        "start": start or f"{year - 1}{end[4:]}",
        "end": end,
        "val": val,
        "form": "10-K",
        "fp": "FY",
        "fy": year,
        "filed": filed or f"{year + 1}-02-01",
        "accn": f"0000000000-{str(year + 1)[2:]}-000001",
        "unit": unit,
    }


def _instant(val, end, form="10-Q", filed=None):
    return {
        "end": end,
        "val": val,
        "form": form,
        "fp": "Q2",
        "fy": int(end[:4]),
        "filed": filed or end[:8] + "28",
        "accn": f"0000000000-{end[2:4]}-000002",
    }


def _concept(obs_list, unit="USD"):
    return {"units": {unit: list(obs_list)}}


def _facts(us_gaap: dict, dei: dict | None = None) -> dict:
    return {
        "cik": 1234567,
        "entityName": "Synthetic Co",
        "facts": {"us-gaap": us_gaap, "dei": dei or {}},
    }


def _current_base():
    """A clean issuer: every metric current for FY2025 / Q2 2026."""
    fy = "2025-12-31"
    bs = "2026-06-30"
    return {
        "Revenues": _concept([_annual(100e9, fy)]),
        "NetIncomeLoss": _concept([_annual(10e9, fy)]),
        "EarningsPerShareDiluted": _concept([_annual(2.5, fy)], unit="USD/shares"),
        "OperatingIncomeLoss": _concept([_annual(15e9, fy)]),
        "NetCashProvidedByUsedInOperatingActivities": _concept([_annual(20e9, fy)]),
        "PaymentsToAcquirePropertyPlantAndEquipment": _concept([_annual(5e9, fy)]),
        "Assets": _concept([_instant(300e9, bs)]),
        "CashAndCashEquivalentsAtCarryingValue": _concept([_instant(30e9, bs)]),
        "LongTermDebtCurrent": _concept([_instant(2e9, bs)]),
        "LongTermDebtNoncurrent": _concept([_instant(40e9, bs)]),
        "StockholdersEquity": _concept([_instant(150e9, bs)]),
    }


def _shares_dei(end="2026-07-20"):
    return {
        "EntityCommonStockSharesOutstanding": _concept(
            [_instant(4e9, end, form="10-Q")], unit="shares"
        )
    }


def _norm(us_gaap, dei=None):
    return normalize_company("SYN", _facts(us_gaap, dei or _shares_dei()), "NON_FINANCIAL")


# ------------------------------------------------------------------
# Normal case: nothing changes except a CURRENT annotation
# ------------------------------------------------------------------

def test_clean_issuer_all_current_and_values_unchanged():
    n = _norm(_current_base())
    m = n["metrics"]
    for name in ("revenue", "operating_income", "cfo", "capex", "cash",
                 "current_debt", "noncurrent_debt", "stockholders_equity",
                 "shares_outstanding"):
        assert m[name]["status"] == "OK", name
        assert m[name]["freshness"] == "CURRENT", name
    assert m["capex"]["value"] == 5e9
    assert m["total_debt"]["status"] == "OK"
    assert m["total_debt"]["value"] == 42e9
    assert n["freshness_anchors"] == {
        "annual": "2025-12-31",
        "instant": "2026-06-30",
        "tolerance_days": FRESHNESS_TOLERANCE_DAYS,
    }


def test_later_cover_page_share_count_is_not_stale():
    n = _norm(_current_base(), _shares_dei("2026-08-15"))
    assert n["metrics"]["shares_outstanding"]["status"] == "OK"


# ------------------------------------------------------------------
# Abandoned concept (GE operating income FY2012 shape)
# ------------------------------------------------------------------

def test_abandoned_annual_concept_is_stale_not_ok():
    g = _current_base()
    g["OperatingIncomeLoss"] = _concept([_annual(22e9, "2012-12-31")])
    m = _norm(g)["metrics"]["operating_income"]
    assert m["status"] == "INCOMPLETE"
    assert m["freshness"] == "STALE"
    assert m["value"] is None
    assert m["stale_observation"]["period_end"] == "2012-12-31"
    assert m["stale_observation"]["value"] == 22e9
    assert "older than" in m["reason"]


def test_stale_input_blocks_derived_metric_instead_of_zero():
    g = _current_base()
    g["PaymentsToAcquirePropertyPlantAndEquipment"] = _concept([_annual(1e9, "2012-12-31")])
    n = _norm(g)
    assert n["metrics"]["capex"]["freshness"] == "STALE"
    derived = calculate_derived_metrics(n)
    fcf = derived["derived_metrics"]["fcf"]
    assert fcf["status"] != "OK"
    assert fcf["value"] is None


def test_stale_metric_is_reported_by_normalized_validation():
    g = _current_base()
    g["NetCashProvidedByUsedInOperatingActivities"] = _concept([_annual(1e9, "2019-12-31")])
    assert "cfo" in validate_normalized_data(_norm(g))


# ------------------------------------------------------------------
# Tag change (LMT / NVDA capex shape)
# ------------------------------------------------------------------

def test_capex_tag_change_selects_current_productive_assets_with_label():
    g = _current_base()
    g["PaymentsToAcquirePropertyPlantAndEquipment"] = _concept([_annual(1e9, "2012-12-31")])
    g["PaymentsToAcquireProductiveAssets"] = _concept([_annual(6e9, "2025-12-31")])
    m = _norm(g)["metrics"]["capex"]
    assert m["status"] == "OK"
    assert m["concept"] == "PaymentsToAcquireProductiveAssets"
    assert m["value"] == 6e9
    assert m["concept_scope"] == "PRODUCTIVE_ASSETS"


def test_primary_capex_concept_keeps_priority_when_both_current():
    g = _current_base()
    g["PaymentsToAcquireProductiveAssets"] = _concept([_annual(6e9, "2025-12-31")])
    m = _norm(g)["metrics"]["capex"]
    assert m["concept"] == "PaymentsToAcquirePropertyPlantAndEquipment"
    assert m["value"] == 5e9


# ------------------------------------------------------------------
# Balance-sheet staleness (MU 2013 / SMCI mixed years / PLTR 2021)
# ------------------------------------------------------------------

def test_old_debt_values_are_stale_and_total_debt_not_computed():
    g = _current_base()
    g["LongTermDebtCurrent"] = _concept([_instant(0.3e9, "2013-05-30", form="10-K")])
    g["LongTermDebtNoncurrent"] = _concept([_instant(3e9, "2013-05-30", form="10-K")])
    m = _norm(g)["metrics"]
    assert m["current_debt"]["freshness"] == "STALE"
    assert m["noncurrent_debt"]["freshness"] == "STALE"
    assert m["total_debt"]["status"] == "INCOMPLETE"
    assert m["total_debt"]["value"] is None


def test_debt_components_from_different_old_years_are_not_combined():
    g = _current_base()
    g["LongTermDebtCurrent"] = _concept([_instant(0.2e9, "2023-06-30", form="10-K")])
    g["LongTermDebtNoncurrent"] = _concept([_instant(0.1e9, "2024-03-31")])
    m = _norm(g)["metrics"]
    assert m["current_debt"]["status"] == "INCOMPLETE"
    assert m["noncurrent_debt"]["status"] == "INCOMPLETE"
    assert m["total_debt"]["status"] == "INCOMPLETE"


def test_one_quarter_old_component_is_stale():
    g = _current_base()
    g["LongTermDebtNoncurrent"] = _concept([_instant(40e9, "2026-03-31")])
    m = _norm(g)["metrics"]
    assert m["noncurrent_debt"]["freshness"] == "STALE"
    assert m["total_debt"]["status"] == "INCOMPLETE"


def test_current_debtcurrent_tag_replaces_stale_current_portion():
    # INTC shape: LongTermDebtCurrent only at fiscal year end,
    # DebtCurrent at the latest balance-sheet date.
    g = _current_base()
    g["LongTermDebtCurrent"] = _concept([_instant(2.5e9, "2025-12-31", form="10-K")])
    g["DebtCurrent"] = _concept([_instant(2.0e9, "2026-06-30")])
    m = _norm(g)["metrics"]
    assert m["current_debt"]["concept"] == "DebtCurrent"
    assert m["current_debt"]["value"] == 2.0e9
    assert m["total_debt"]["value"] == 42e9


def test_debt_and_lease_obligations_used_only_when_current():
    g = _current_base()
    del g["LongTermDebtNoncurrent"]
    g["LongTermDebtAndCapitalLeaseObligations"] = _concept([_instant(17e9, "2026-06-30")])
    m = _norm(g)["metrics"]["noncurrent_debt"]
    assert m["concept"] == "LongTermDebtAndCapitalLeaseObligations"
    assert m["concept_scope"] == "DEBT_AND_LEASE_OBLIGATIONS"


# ------------------------------------------------------------------
# Tolerance boundary, missing anchor, unit ambiguity, PIT
# ------------------------------------------------------------------

def test_tolerance_boundary():
    g = _current_base()
    # Assets 2026-06-30; 45 days earlier = 2026-05-16 (current), 46 = 2026-05-15 (stale)
    g["CashAndCashEquivalentsAtCarryingValue"] = _concept([_instant(1e9, "2026-05-16")])
    assert _norm(g)["metrics"]["cash"]["freshness"] == "CURRENT"
    g["CashAndCashEquivalentsAtCarryingValue"] = _concept([_instant(1e9, "2026-05-15")])
    assert _norm(g)["metrics"]["cash"]["freshness"] == "STALE"


def test_missing_assets_anchor_leaves_instant_values_unverified_not_stale():
    g = _current_base()
    del g["Assets"]
    m = _norm(g)["metrics"]["cash"]
    assert m["status"] == "OK"
    assert m["freshness"] == "UNVERIFIED_NO_ANCHOR"


def test_missing_value_stays_missing_not_stale():
    g = _current_base()
    del g["OperatingIncomeLoss"]
    m = _norm(g)["metrics"]["operating_income"]
    assert m["status"] == "MISSING"
    assert "freshness" not in m


def test_unit_ambiguity_result_passes_through_unchanged():
    g = _current_base()
    g["Revenues"] = {
        "units": {
            "USD": [_annual(100e9, "2025-12-31")],
            "EUR": [_annual(90e9, "2025-12-31", unit="EUR")],
        }
    }
    m = _norm(g)["metrics"]["revenue"]
    assert m["status"] == "INCOMPLETE"
    assert "freshness" not in m


def test_anchor_uses_only_supplied_pit_snapshot():
    """The anchor comes from the document passed in. If the PIT gate has
    removed a later filing, an older value is current for that snapshot."""
    g = _current_base()
    g["OperatingIncomeLoss"] = _concept([_annual(12e9, "2024-12-31")])
    for name in ("Revenues", "NetIncomeLoss", "EarningsPerShareDiluted",
                 "NetCashProvidedByUsedInOperatingActivities",
                 "PaymentsToAcquirePropertyPlantAndEquipment"):
        unit = "USD/shares" if name == "EarningsPerShareDiluted" else "USD"
        g[name] = _concept([_annual(1.0, "2024-12-31")], unit=unit)
    data = _facts(g, _shares_dei())
    assert annual_freshness_anchor(data).isoformat() == "2024-12-31"
    m = normalize_company("SYN", data, "NON_FINANCIAL")["metrics"]["operating_income"]
    assert m["status"] == "OK"
    assert m["freshness"] == "CURRENT"


def test_instant_anchor_is_latest_assets_date():
    g = _current_base()
    g["Assets"] = _concept([_instant(1, "2025-12-31", form="10-K"), _instant(1, "2026-06-30")])
    assert instant_freshness_anchor(_facts(g)).isoformat() == "2026-06-30"
