"""
Offline regression tests for FRED-PIT-PROD-M2
(.github/scripts/fred_pit_prod_m2_explicit_vintage_probe.py).

PURPOSE: these tests cover the NEW, isolated M2 probe script only.
They are NOT live FRED evidence and must never be described as such --
every fixture below is a synthetic, hand-constructed, production-shaped
response (crosstab observations + vintagedates), not a captured or live
FRED response. No test in this file makes a real network call; every
test that reaches FredMacroProvider.get_series() monkeypatches
urllib.request.urlopen with a synthetic router, the same technique
tests/test_macro_fred_provider.py already uses for the
EXPLICIT_VINTAGE_DATES_SERIES_IDS path, and several tests additionally
install a hard network guard (monkeypatching socket.create_connection
to raise) to PROVE no real network call occurs on a rejection path.

Scope note: this file does not touch, modify, or import for mutation
src/macro/fred_provider.py, src/macro/pit.py, src/macro/schema.py,
src/macro/engine.py, .github/scripts/fred_output_type1_validation.py,
or .github/workflows/*.yml (all remain frozen). It imports
fred_pit_prod_m2_explicit_vintage_probe.py fresh via
importlib.util.spec_from_file_location (mirroring
tests/test_fred_output_type1_validation_production_probe.py's own
_load_module() convention), which in turn performs its own read-only
sibling import of fred_output_type1_validation.py -- this file never
reimplements that import.

Nine categories covered (per the FRED-PIT-PROD-M2 implementation task's
enumerated test requirements):
  1. Section M preservation (the base Section M wrapper's own gate
     behavior for a non-WIDE_WINDOW series is unchanged by M2's
     existence/import).
  2. M2 series acceptance for T10Y2Y and VIXCLS.
  3. Pre-network rejection of an unsupported series (hard network
     guard -- proves zero network activity).
  4. Non-execution of Section M / other base_validation entrypoints
     when M2's main() runs.
  5. Default-behavior preservation when FRED_PIT_M2_SERIES_ID is absent
     (hard network guard).
  6. fetch_strategy string accuracy (EXPLICIT_VINTAGE_DATES_SERIES_IDS,
     never WIDE_WINDOW).
  7. Independent-expectation PIT comparison via a genuine multi-vintage
     synthetic T10Y2Y crosstab fixture -- not a no-candidate fixture.
  8. Future-vintage-exclusion invariant.
  9. Honest error/no-candidate/malformed-response reporting (missing
     API key, classified HTTP error, single-vintage no-candidate case).
"""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path
from urllib.error import HTTPError

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "scripts"
    / "fred_pit_prod_m2_explicit_vintage_probe.py"
)


def _load_module(monkeypatch):
    """Import the M2 probe script fresh. FRED_API_KEY is set to a
    throwaway synthetic value purely so module import never fails if
    anything at import time happened to check for it -- no test in
    this file makes a real request, so this value is never sent
    anywhere. Mirrors
    test_fred_output_type1_validation_production_probe.py's own
    _load_module()."""
    monkeypatch.setenv("FRED_API_KEY", "OFFLINE_TEST_ONLY_NOT_A_REAL_KEY")
    spec = importlib.util.spec_from_file_location(
        "fred_pit_prod_m2_explicit_vintage_probe_under_test", _SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_no_network_guard(monkeypatch):
    """Raises if ANYTHING in the test attempts a real TCP connection --
    used on every rejection-path test to prove the rejection genuinely
    happens before any network call, not merely before a successful
    one."""

    def _guard(*args, **kwargs):
        raise AssertionError(
            "socket.create_connection was called -- a rejection/absent-input "
            "path must never reach the network."
        )

    monkeypatch.setattr(socket, "create_connection", _guard)


class _FakeHTTPResponse:
    def __init__(self, body, status: int = 200) -> None:
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_urlopen_router(monkeypatch, router):
    """router: callable(url: str) -> dict | Exception-to-raise. Mirrors
    tests/test_macro_fred_provider.py's _install_urlopen_router()."""

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        result = router(url)
        if isinstance(result, Exception):
            raise result
        return _FakeHTTPResponse(result)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def _two_vintage_t10y2y_router(calls: list):
    """Genuine multi-vintage synthetic T10Y2Y crosstab fixture -- TWO
    distinct vintage dates for one observation_date, extending the
    T10Y2Y_20150226/T10Y2Y_20150324 pattern from
    tests/test_macro_fred_provider.py's own
    test_get_series_explicit_vintage_dates_series_uses_vintagedates_endpoint
    (not a no-candidate, single-vintage fixture -- this one can and
    must reach "status": "PASS")."""

    def router(url):
        calls.append(url)
        if "fred/series/vintagedates" in url:
            return {"vintage_dates": ["2015-02-26", "2015-03-24"]}
        if "fred/series/observations" in url:
            assert "vintage_dates=" in url
            return {
                "observations": [
                    {
                        "date": "2015-01-02",
                        "T10Y2Y_20150226": "1.50",
                        "T10Y2Y_20150324": "1.52",
                    }
                ]
            }
        return {
            "seriess": [
                {
                    "id": "T10Y2Y",
                    "frequency": "Daily",
                    "frequency_short": "D",
                    "units": "Percent",
                    "units_short": "Percent",
                    "seasonal_adjustment": "Not Seasonally Adjusted",
                    "observation_start": "1976-06-01",
                    "observation_end": "2026-10-23",
                    "last_updated": "2026-10-23T15:17:03-05:00",
                }
            ]
        }

    return router


def _three_vintage_t10y2y_router(calls: list):
    """Three distinct vintages for one observation_date -- mirrors
    test_three_vintage_candidates_picks_closer_eligible_not_oldest in
    tests/test_fred_output_type1_validation_production_probe.py, adapted
    to the T10Y2Y explicit-vintage crosstab shape: the oldest vintage
    must be eligible-but-not-selected, the middle vintage must be
    selected (as_of), and the newest must be excluded as "future"."""

    def router(url):
        calls.append(url)
        if "fred/series/vintagedates" in url:
            return {"vintage_dates": ["2015-07-03", "2015-08-01", "2015-09-05"]}
        if "fred/series/observations" in url:
            return {
                "observations": [
                    {
                        "date": "2015-06-01",
                        "T10Y2Y_20150703": "1.40",
                        "T10Y2Y_20150801": "1.38",
                        "T10Y2Y_20150905": "1.36",
                    }
                ]
            }
        return {
            "seriess": [
                {
                    "id": "T10Y2Y",
                    "frequency": "Daily",
                    "frequency_short": "D",
                    "units": "Percent",
                    "units_short": "Percent",
                    "seasonal_adjustment": "Not Seasonally Adjusted",
                    "observation_start": "1976-06-01",
                    "observation_end": "2026-10-23",
                    "last_updated": "2026-10-23T15:17:03-05:00",
                }
            ]
        }

    return router


def _single_vintage_vixcls_router(calls: list):
    """Exactly one vintage per observation_date -- no usable multi-
    vintage candidate at all. Used for the honest no-candidate
    reporting test (category 9) -- must be INCONCLUSIVE /
    "request_succeeded_no_usable_candidates", never a false PASS."""

    def router(url):
        calls.append(url)
        if "fred/series/vintagedates" in url:
            return {"vintage_dates": ["2026-01-05"]}
        if "fred/series/observations" in url:
            return {
                "observations": [
                    {"date": "2026-01-02", "VIXCLS_20260105": "14.20"},
                ]
            }
        return {
            "seriess": [
                {
                    "id": "VIXCLS",
                    "frequency": "Daily, Close",
                    "frequency_short": "D",
                    "units": "Index",
                    "units_short": "Index",
                    "seasonal_adjustment": "Not Seasonally Adjusted",
                    "observation_start": "1990-01-02",
                    "observation_end": "2026-10-09",
                    "last_updated": "2026-10-10T15:17:03-05:00",
                }
            ]
        }

    return router


# ============================================================
# Category 1 -- Section M preservation: the base script's own Section M
# wrapper's gate behavior for a non-WIDE_WINDOW series is unaffected by
# M2's existence or import.
# ============================================================


def test_section_m_unsupported_series_gate_unchanged_after_m2_import(monkeypatch):
    module = _load_module(monkeypatch)

    result = module.base_validation.section_m_production_output_type2_pit_selector_probe(
        "T10Y2Y", "2025-01-01", "2025-12-31"
    )

    assert result["status"] == "INCONCLUSIVE"
    assert result["stage"] == "request_not_attempted_unsupported_series"
    assert "WIDE_WINDOW_SERIES_IDS" in result["reason"]


def test_m2_module_does_not_mutate_base_validation_attributes(monkeypatch):
    # Importing the M2 script must not monkeypatch, wrap, or replace
    # any attribute on the base_validation module it imports read-only.
    # The function object itself must be the real one from that file,
    # not a replacement -- confirmed indirectly by its docstring (which
    # M2 never rewrites) and by its hard gate constant name still
    # appearing in its own source-derived __doc__.
    module = _load_module(monkeypatch)

    base_doc = module.base_validation.section_m_production_output_type2_pit_selector_probe.__doc__ or ""
    assert "WIDE_WINDOW_SERIES_IDS" in base_doc
    assert module.base_validation.WIDE_WINDOW_SERIES_IDS == frozenset(
        {
            "PAYEMS",
            "UNRATE",
            "CPIAUCSL",
            "CPILFESL",
            "PCEPI",
            "PCEPILFE",
            "DFEDTARU",
            "DFEDTARL",
        }
    )


# ============================================================
# Category 2 -- M2 series acceptance for T10Y2Y and VIXCLS.
# ============================================================


def test_m2_accepts_t10y2y_and_exercises_production_path(monkeypatch):
    module = _load_module(monkeypatch)
    calls: list = []
    _install_urlopen_router(monkeypatch, _two_vintage_t10y2y_router(calls))

    result = module.section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        "T10Y2Y", "2014-01-01", "2015-12-31"
    )

    assert result["production_path_exercised"] is True
    assert any("fred/series/vintagedates" in u for u in calls)
    assert result["status"] == "PASS"


def test_m2_accepts_vixcls_and_exercises_production_path(monkeypatch):
    module = _load_module(monkeypatch)
    calls: list = []

    def router(url):
        calls.append(url)
        if "fred/series/vintagedates" in url:
            return {"vintage_dates": ["2026-01-05", "2026-02-02"]}
        if "fred/series/observations" in url:
            return {
                "observations": [
                    {
                        "date": "2026-01-02",
                        "VIXCLS_20260105": "14.20",
                        "VIXCLS_20260202": "14.35",
                    }
                ]
            }
        return {"seriess": [{"id": "VIXCLS", "frequency": "Daily, Close"}]}

    _install_urlopen_router(monkeypatch, router)

    result = module.section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        "VIXCLS", "2026-01-01", "2026-12-31"
    )

    assert result["production_path_exercised"] is True
    assert result["status"] == "PASS"


# ============================================================
# Category 3 -- pre-network rejection of an unsupported series, proven
# via a hard network guard.
# ============================================================


def test_m2_rejects_unsupported_series_before_any_network_call(monkeypatch):
    module = _load_module(monkeypatch)
    _install_no_network_guard(monkeypatch)

    result = module.section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        "PAYEMS", "2025-01-01", "2025-12-31"
    )

    assert result["status"] == "INCONCLUSIVE"
    assert result["stage"] == "request_not_attempted_unsupported_series"
    assert result["production_path_exercised"] is False
    assert "EXPLICIT_VINTAGE_DATES_SERIES_IDS" in result["reason"]


def test_m2_rejects_wide_window_series_unrate_before_any_network_call(monkeypatch):
    module = _load_module(monkeypatch)
    _install_no_network_guard(monkeypatch)

    result = module.section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        "UNRATE", "2025-01-01", "2025-12-31"
    )

    assert result["status"] == "INCONCLUSIVE"
    assert result["stage"] == "request_not_attempted_unsupported_series"
    assert result["production_path_exercised"] is False


# ============================================================
# Category 4 -- non-execution of Section M / other base_validation
# entrypoints when M2's main() runs.
# ============================================================


def test_m2_main_never_invokes_section_m_or_other_sections(monkeypatch, tmp_path):
    module = _load_module(monkeypatch)
    calls: list = []
    _install_urlopen_router(monkeypatch, _two_vintage_t10y2y_router(calls))
    monkeypatch.setenv("FRED_PIT_M2_SERIES_ID", "T10Y2Y")
    # Redirect the evidence-artifact path to tmp_path, mirroring
    # test_fred_output_type1_validation_production_probe.py's own
    # test_main_actually_wires_probe_output_to_approved_sections_and_leaves_others_unaffected
    # convention -- this test must never write into the real repository
    # working tree.
    monkeypatch.setattr(module, "M2_EVIDENCE_ARTIFACT_PATH", str(tmp_path / "m2_evidence.json"))

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("Section M (or another base_validation section) was invoked by M2's main().")

    # Every other section-style entrypoint in the base module must stay
    # untouched by M2's main() -- not merely unmodified on disk, but
    # genuinely never called at runtime.
    for name in dir(module.base_validation):
        if name.startswith("section_") and name != "section_m2_production_output_type2_explicit_vintage_pit_selector_probe":
            if callable(getattr(module.base_validation, name)):
                monkeypatch.setattr(module.base_validation, name, _fail_if_called)

    exit_code = module.main()

    assert exit_code == 0


# ============================================================
# Category 5 -- default-behavior preservation when
# FRED_PIT_M2_SERIES_ID is absent, proven via a hard network guard.
# ============================================================


def test_m2_main_does_nothing_when_series_id_env_var_absent(monkeypatch):
    module = _load_module(monkeypatch)
    monkeypatch.delenv("FRED_PIT_M2_SERIES_ID", raising=False)
    _install_no_network_guard(monkeypatch)

    exit_code = module.main()

    assert exit_code == 0
    recorded = module.RESULTS["m2_production_output_type2_explicit_vintage_pit_selector_probe"]
    assert recorded["stage"] == "not_requested_this_run"
    assert recorded["production_path_exercised"] is False


def test_m2_main_rejects_invalid_series_id_env_var_before_any_network_call(monkeypatch):
    module = _load_module(monkeypatch)
    monkeypatch.setenv("FRED_PIT_M2_SERIES_ID", "PAYEMS")
    _install_no_network_guard(monkeypatch)

    exit_code = module.main()

    assert exit_code == 0
    recorded = module.RESULTS["m2_production_output_type2_explicit_vintage_pit_selector_probe"]
    assert recorded["stage"] == "rejected_invalid_series_input"
    assert recorded["production_path_exercised"] is False


# ============================================================
# Category 6 -- fetch_strategy string accuracy.
# ============================================================


def test_m2_fetch_strategy_string_names_explicit_vintage_dates_not_wide_window(monkeypatch):
    module = _load_module(monkeypatch)
    calls: list = []
    _install_urlopen_router(monkeypatch, _two_vintage_t10y2y_router(calls))

    result = module.section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        "T10Y2Y", "2014-01-01", "2015-12-31"
    )

    fetch_strategy = result["request_params"]["fetch_strategy"]
    assert "EXPLICIT_VINTAGE_DATES_SERIES_IDS" in fetch_strategy
    assert "WIDE_WINDOW" not in fetch_strategy


# ============================================================
# Category 7 -- independent-expectation PIT comparison via a genuine
# multi-vintage synthetic T10Y2Y crosstab fixture (not a no-candidate
# fixture).
# ============================================================


def test_m2_two_vintage_t10y2y_agrees_with_independent_expectation(monkeypatch):
    module = _load_module(monkeypatch)
    calls: list = []
    _install_urlopen_router(monkeypatch, _two_vintage_t10y2y_router(calls))

    result = module.section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        "T10Y2Y", "2015-01-01", "2015-12-31"
    )

    assert result["status"] == "PASS"
    assert result["stage"] == "request_succeeded_evidence_built"
    assert result["observation_date"] == "2015-01-02"
    assert result["as_of"] == "2015-02-26"
    assert result["eligible_vintage_dates"] == ["2015-02-26"]
    assert result["excluded_future_vintage_dates"] == ["2015-03-24"]
    assert result["expected_selected_vintage"] == "2015-02-26"
    assert result["actual_selected_vintage_direct"] == "2015-02-26"
    assert result["actual_selected_vintage_via_series"] == "2015-02-26"
    assert result["agrees_with_expected"] is True
    assert result["no_future_vintage_selected"] is True


# ============================================================
# Category 8 -- future-vintage-exclusion invariant.
# ============================================================


def test_m2_three_vintage_t10y2y_excludes_future_vintage_and_picks_closer_eligible(monkeypatch):
    module = _load_module(monkeypatch)
    calls: list = []
    _install_urlopen_router(monkeypatch, _three_vintage_t10y2y_router(calls))

    result = module.section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        "T10Y2Y", "2015-01-01", "2015-12-31"
    )

    assert result["status"] == "PASS"
    assert result["as_of"] == "2015-08-01"
    assert result["eligible_vintage_dates"] == ["2015-07-03", "2015-08-01"]
    assert result["excluded_future_vintage_dates"] == ["2015-09-05"]
    assert result["expected_selected_vintage"] == "2015-08-01"
    assert result["no_future_vintage_selected"] is True
    # The invariant this category exists to prove: the excluded future
    # vintage must never equal the selected one.
    assert result["actual_selected_vintage_direct"] not in result["excluded_future_vintage_dates"]
    assert result["actual_selected_vintage_via_series"] not in result["excluded_future_vintage_dates"]


# ============================================================
# Category 9 -- honest error/no-candidate/malformed-response reporting.
# ============================================================


def test_m2_reports_missing_api_key_honestly_never_claims_live(monkeypatch):
    module = _load_module(monkeypatch)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    _install_no_network_guard(monkeypatch)

    result = module.section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        "T10Y2Y", "2025-01-01", "2025-12-31"
    )

    assert result["status"] == "INCONCLUSIVE"
    assert result["stage"] == "request_not_attempted_api_key_missing"
    assert result["production_path_exercised"] is False
    assert "api_key=" not in str(result.get("detail", ""))


def test_m2_reports_classified_http_error_honestly(monkeypatch):
    module = _load_module(monkeypatch)

    def router(url):
        if "fred/series/vintagedates" in url:
            return HTTPError(url, 400, "Bad Request", {}, None)
        return {"seriess": [{"id": "T10Y2Y"}]}

    _install_urlopen_router(monkeypatch, router)

    result = module.section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        "T10Y2Y", "2025-01-01", "2025-12-31"
    )

    assert result["status"] == "INCONCLUSIVE"
    assert result["stage"] == "request_failed"
    assert result["production_path_exercised"] is False


def test_m2_reports_no_usable_candidates_honestly_not_a_false_pass(monkeypatch):
    module = _load_module(monkeypatch)
    calls: list = []
    _install_urlopen_router(monkeypatch, _single_vintage_vixcls_router(calls))

    result = module.section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        "VIXCLS", "2026-01-01", "2026-12-31"
    )

    assert result["status"] == "INCONCLUSIVE"
    assert result["stage"] == "request_succeeded_no_usable_candidates"
    assert result["production_path_exercised"] is True
