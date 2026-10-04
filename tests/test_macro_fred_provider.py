"""
Unit tests for src/macro/fred_provider.py (Framework v2.1 Step 10J,
2026-10-04).

OFFLINE ONLY: every test in this file mocks urllib.request.urlopen via
monkeypatch -- no real network call is ever made in this module. A live
FRED API test is explicitly NOT part of this file (Step 10J section 16's
requirement: pytest's default collection must never depend on
FRED_API_KEY being present; the full suite must not fail in an
environment with no key).

Fixtures (section 13 of this round's instructions):
    Fixture A -- CPI multi-vintage crosstab
    Fixture B -- PAYEMS revision with REAL differing values across
                 vintages (the case that actually exercises value
                 selection, not just date selection)
    Fixture C -- missing marker "."
    Fixture D -- malformed vintage column (must raise, not silently
                 mis-parse)
    Fixture E -- metadata response (frequency/units/etc.)

PIT integration tests (section 14): Cases 1-5, including the
most-important Case 5 (PAYEMS-style differing values per vintage must
be selected correctly, not just dates). No-look-ahead regression test
(section 15) is also included.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

import pytest

from src.macro.fred_provider import (
    EXPLICIT_VINTAGE_DATES_SERIES_IDS,
    WIDE_WINDOW_SERIES_IDS,
    FredAPIKeyMissing,
    FredHTTPError,
    FredMacroProvider,
    FredMalformedVintageColumnError,
    FredProviderError,
    FredResponseShapeError,
    _extract_vintage_cells,
    _parse_crosstab_response,
    _parse_numeric_cell,
    default_raw_response_path,
    save_raw_fred_response,
)
from src.macro.pit import get_point_in_time_series, select_latest_vintage_as_of
from src.macro.schema import validate_raw_observation


# ============================================================
# Fixtures (raw FRED-shaped payloads)
# ============================================================


def fixture_a_cpi_multi_vintage() -> dict:
    """Fixture A -- CPI multi-vintage crosstab: one observation_date with
    three published vintage columns (same style as Step 10I's real
    CPIAUCSL response)."""
    return {
        "observations": [
            {
                "date": "2015-01-01",
                "CPIAUCSL_20150226": "234.677",
                "CPIAUCSL_20150324": "234.677",
                "CPIAUCSL_20150417": "234.750",
            }
        ]
    }


def fixture_b_payems_revision() -> dict:
    """Fixture B -- PAYEMS revision: SAME observation_date, three
    vintages with REAL DIFFERING values (not just different dates) --
    mirrors a real BLS initial-estimate-then-revision sequence."""
    return {
        "observations": [
            {
                "date": "2015-01-01",
                "PAYEMS_20150206": "140793",
                "PAYEMS_20150306": "140831",
                "PAYEMS_20150410": "140849",
            }
        ]
    }


def fixture_c_missing_marker() -> dict:
    """Fixture C -- missing marker "." for one vintage column cell."""
    return {
        "observations": [
            {
                "date": "2015-01-01",
                "CPIAUCSL_20150226": ".",
                "CPIAUCSL_20150324": "234.677",
            }
        ]
    }


def fixture_d_malformed_vintage_column() -> dict:
    """Fixture D -- a column that has this series' vintage-column prefix
    but a non-date suffix (malformed). The provider must raise, not
    silently skip or mis-parse."""
    return {
        "observations": [
            {
                "date": "2015-01-01",
                "CPIAUCSL_NOTADATE": "234.677",
            }
        ]
    }


def fixture_e_metadata() -> dict:
    """Fixture E -- fred/series metadata response."""
    return {
        "seriess": [
            {
                "id": "CPIAUCSL",
                "frequency": "Monthly",
                "frequency_short": "M",
                "units": "Index 1982-1984=100",
                "units_short": "Index 1982-1984=100",
                "seasonal_adjustment": "Seasonally Adjusted",
                "observation_start": "1947-01-01",
                "observation_end": "2026-09-01",
                "last_updated": "2026-10-01T07:42:01-05:00",
            }
        ]
    }


def fixture_vintagedates(dates: list[str]) -> dict:
    return {"vintage_dates": dates}


# ============================================================
# Mock urllib.request.urlopen plumbing
# ============================================================


class _FakeHTTPResponse:
    def __init__(self, status: int, body: dict | str):
        self.status = status
        self._body = body if isinstance(body, (bytes,)) else json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_urlopen_router(monkeypatch, router):
    """router: callable(url: str) -> dict | HTTPError-to-raise. Installs
    a fake urlopen that dispatches on the request URL."""

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        result = router(url)
        if isinstance(result, Exception):
            raise result
        return _FakeHTTPResponse(200, result)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


# ============================================================
# Unit: numeric cell parsing / missing-value policy
# ============================================================


def test_parse_numeric_cell_valid_number():
    assert _parse_numeric_cell("234.677") == 234.677


def test_parse_numeric_cell_missing_marker_is_nan():
    import math

    assert math.isnan(_parse_numeric_cell("."))


def test_parse_numeric_cell_none_is_nan():
    import math

    assert math.isnan(_parse_numeric_cell(None))


def test_parse_numeric_cell_never_returns_zero_for_missing():
    assert _parse_numeric_cell(".") != 0.0


# ============================================================
# Unit: crosstab parsing (Fixtures A/B/C/D)
# ============================================================


def test_crosstab_parses_fixture_a_cpi_multi_vintage():
    records = _parse_crosstab_response(fixture_a_cpi_multi_vintage()["observations"], "CPIAUCSL")
    assert len(records) == 3
    vintage_dates = {r["vintage_date"] for r in records}
    assert vintage_dates == {"2015-02-26", "2015-03-24", "2015-04-17"}
    for r in records:
        assert r["observation_date"] == "2015-01-01"
        assert r["realtime_start"] == r["vintage_date"]
        assert r["series_id"] == "CPIAUCSL"


def test_crosstab_parses_fixture_b_payems_revision_with_distinct_values():
    records = _parse_crosstab_response(fixture_b_payems_revision()["observations"], "PAYEMS")
    values_by_vintage = {r["vintage_date"]: r["value"] for r in records}
    assert values_by_vintage == {
        "2015-02-06": 140793.0,
        "2015-03-06": 140831.0,
        "2015-04-10": 140849.0,
    }


def test_crosstab_fixture_c_missing_marker_becomes_nan_not_dropped():
    records = _parse_crosstab_response(fixture_c_missing_marker()["observations"], "CPIAUCSL")
    # Both cells present as records -- the missing one is NOT dropped.
    assert len(records) == 2
    import math

    by_vintage = {r["vintage_date"]: r["value"] for r in records}
    assert math.isnan(by_vintage["2015-02-26"])
    assert by_vintage["2015-03-24"] == 234.677


def test_crosstab_fixture_d_malformed_column_raises():
    with pytest.raises(FredMalformedVintageColumnError):
        _parse_crosstab_response(fixture_d_malformed_vintage_column()["observations"], "CPIAUCSL")


def test_extract_vintage_cells_ignores_date_key():
    cells = _extract_vintage_cells({"date": "2015-01-01", "CPIAUCSL_20150226": "1.0"}, "CPIAUCSL")
    assert "date" not in cells
    assert cells == {"2015-02-26": "1.0"}


# ============================================================
# Provider-level: get_series() against mocked HTTP, each row schema-valid
# ============================================================


def test_get_series_wide_window_series_produces_schema_valid_rows(monkeypatch):
    def router(url):
        if "fred/series/observations" in url:
            return fixture_a_cpi_multi_vintage()
        if "fred/series?" in url or "fred/series&" in url:
            return fixture_e_metadata()
        raise AssertionError(f"unexpected URL in test: {url}")

    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")
    _install_urlopen_router(monkeypatch, router)

    provider = FredMacroProvider()
    rows = provider.get_series("CPIAUCSL")

    assert len(rows) == 3
    for row in rows:
        assert validate_raw_observation(row) == []
        assert row["source"] == "FRED"
        assert row["frequency"] == "MONTHLY"
        assert row["units"] == "Index 1982-1984=100"


def test_get_series_filters_by_observation_date_range(monkeypatch):
    payload = {
        "observations": [
            {"date": "2015-01-01", "CPIAUCSL_20150226": "1.0"},
            {"date": "2015-06-01", "CPIAUCSL_20150626": "2.0"},
        ]
    }

    def router(url):
        if "fred/series/observations" in url:
            return payload
        return fixture_e_metadata()

    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")
    _install_urlopen_router(monkeypatch, router)

    provider = FredMacroProvider()
    rows = provider.get_series("CPIAUCSL", start_date="2015-03-01", end_date="2015-12-31")
    assert [r["observation_date"] for r in rows] == ["2015-06-01"]


def test_get_series_explicit_vintage_dates_series_uses_vintagedates_endpoint(monkeypatch):
    calls = []

    def router(url):
        calls.append(url)
        if "fred/series/vintagedates" in url:
            return fixture_vintagedates(["2015-02-26", "2015-03-24"])
        if "fred/series/observations" in url:
            assert "vintage_dates=" in url
            return {
                "observations": [
                    {"date": "2015-01-02", "T10Y2Y_20150226": "1.50", "T10Y2Y_20150324": "1.52"}
                ]
            }
        return fixture_e_metadata()

    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")
    _install_urlopen_router(monkeypatch, router)

    provider = FredMacroProvider()
    rows = provider.get_series("T10Y2Y")

    assert any("fred/series/vintagedates" in u for u in calls)
    assert len(rows) == 2
    for row in rows:
        assert validate_raw_observation(row) == []


def test_get_series_unclassified_series_id_raises_explicitly(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")

    def router(url):  # pragma: no cover -- should never be called
        raise AssertionError("no HTTP call should happen for an unclassified series_id")

    _install_urlopen_router(monkeypatch, router)
    provider = FredMacroProvider()
    with pytest.raises(FredProviderError):
        provider.get_series("SOME_UNCLASSIFIED_SERIES")


def test_wide_window_and_explicit_sets_are_disjoint_and_cover_required_series():
    assert WIDE_WINDOW_SERIES_IDS.isdisjoint(EXPLICIT_VINTAGE_DATES_SERIES_IDS)
    required = {
        "PAYEMS",
        "UNRATE",
        "CPIAUCSL",
        "CPILFESL",
        "PCEPI",
        "PCEPILFE",
        "DFEDTARU",
        "DFEDTARL",
        "T10Y2Y",
        "VIXCLS",
    }
    assert required == WIDE_WINDOW_SERIES_IDS | EXPLICIT_VINTAGE_DATES_SERIES_IDS


# ============================================================
# API key handling
# ============================================================


def test_missing_api_key_raises_without_network_call(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    def router(url):  # pragma: no cover
        raise AssertionError("no HTTP call should happen without an API key")

    _install_urlopen_router(monkeypatch, router)
    provider = FredMacroProvider()
    with pytest.raises(FredAPIKeyMissing):
        provider.get_series("CPIAUCSL")


def test_api_key_never_appears_in_raised_exception_message(monkeypatch):
    # The real request URL DOES carry the key (FRED requires it) -- that
    # is expected and correct. What must never happen is the key leaking
    # into a raised exception's message, which is what redaction (used
    # only for logging/exceptions, never for the live request itself)
    # exists to prevent.
    monkeypatch.setenv("FRED_API_KEY", "super-secret-value-12345")

    def router(url):
        raise HTTPError(url, 400, "bad request", {}, None)

    _install_urlopen_router(monkeypatch, router)
    provider = FredMacroProvider()
    with pytest.raises(FredHTTPError) as exc_info:
        provider.get_series("CPIAUCSL")
    assert "super-secret-value-12345" not in str(exc_info.value)
    assert "super-secret-value-12345" not in (exc_info.value.redacted_url or "")


def test_http_error_carries_redacted_url_for_diagnosis(monkeypatch):
    """2026-10-05 QA finding: the original Step 10J cut computed a
    redacted URL in _FredHttpClient.request_json() but never attached it
    to the raised exception (confirmed dead-code via pyflakes), so a
    failure could not be traced back to which endpoint/series_id caused
    it. This locks in the fix: every FredHTTPError must carry a
    `redacted_url` that identifies the failing request but never the raw
    api_key value."""
    monkeypatch.setenv("FRED_API_KEY", "super-secret-value-12345")

    def router(url):
        raise HTTPError(url, 400, "bad request", {}, None)

    _install_urlopen_router(monkeypatch, router)
    provider = FredMacroProvider()
    with pytest.raises(FredHTTPError) as exc_info:
        provider.get_series("CPIAUCSL")
    assert exc_info.value.redacted_url is not None
    # get_series_metadata() runs first, so the failing call here is the
    # fred/series metadata endpoint -- either endpoint identifies the
    # failing request, which is the point of this test.
    assert "fred/series" in exc_info.value.redacted_url
    assert "api_key=REDACTED" in exc_info.value.redacted_url
    assert "super-secret-value-12345" not in exc_info.value.redacted_url


# ============================================================
# HTTP error classification (reused vocabulary from Step 10G/10I)
# ============================================================


def test_http_429_classified_rate_limited(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")

    def router(url):
        return HTTPError(url, 429, "too many requests", {}, None)

    _install_urlopen_router(monkeypatch, router)
    provider = FredMacroProvider()
    with pytest.raises(FredHTTPError) as exc_info:
        provider.get_series("CPIAUCSL")
    assert exc_info.value.classification == "RATE_LIMITED"


def test_http_400_with_series_not_found_body(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")

    class _Err(HTTPError):
        def read(self):
            return b"The series does not exist."

    def router(url):
        return _Err(url, 400, "bad request", {}, None)

    _install_urlopen_router(monkeypatch, router)
    provider = FredMacroProvider()
    with pytest.raises(FredHTTPError) as exc_info:
        provider.get_series("CPIAUCSL")
    assert exc_info.value.classification == "SERIES_NOT_FOUND"


def test_missing_observations_key_is_response_shape_error_not_http_error(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")

    def router(url):
        if "fred/series/observations" in url:
            return {}  # no "observations" key at all
        return fixture_e_metadata()

    _install_urlopen_router(monkeypatch, router)
    provider = FredMacroProvider()
    with pytest.raises(FredResponseShapeError):
        provider.get_series("CPIAUCSL")


# ============================================================
# Metadata (Fixture E)
# ============================================================


def test_get_series_metadata_fixture_e(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")

    def router(url):
        assert "fred/series?" in url
        return fixture_e_metadata()

    _install_urlopen_router(monkeypatch, router)
    provider = FredMacroProvider()
    meta = provider.get_series_metadata("CPIAUCSL")
    assert meta["frequency_short"] == "M"
    assert meta["units_short"] == "Index 1982-1984=100"
    assert meta["seasonal_adjustment"] == "Seasonally Adjusted"


def test_get_series_metadata_empty_seriess_raises_shape_error(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")

    def router(url):
        return {"seriess": []}

    _install_urlopen_router(monkeypatch, router)
    provider = FredMacroProvider()
    with pytest.raises(FredResponseShapeError):
        provider.get_series_metadata("CPIAUCSL")


# ============================================================
# Raw response preservation (never writes api_key to disk)
# ============================================================


def test_save_raw_fred_response_excludes_api_key(tmp_path: Path):
    path = tmp_path / "fred" / "CPIAUCSL" / "output_type2_full.json"
    save_raw_fred_response(
        path,
        series_id="CPIAUCSL",
        query={"series_id": "CPIAUCSL", "api_key": "SHOULD_NEVER_BE_WRITTEN", "output_type": 2},
        fred_response={"observations": []},
        fetched_at="2026-10-04T00:00:00Z",
    )
    written = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in written["query"]
    assert "SHOULD_NEVER_BE_WRITTEN" not in path.read_text(encoding="utf-8")
    assert written["query"]["series_id"] == "CPIAUCSL"


def test_default_raw_response_path_layout(tmp_path: Path):
    path = default_raw_response_path(tmp_path, "PAYEMS", "output_type2_full")
    assert path == tmp_path / "fred" / "PAYEMS" / "output_type2_full.json"


def test_get_series_with_raw_dir_writes_raw_files(monkeypatch, tmp_path: Path):
    def router(url):
        if "fred/series/observations" in url:
            return fixture_a_cpi_multi_vintage()
        return fixture_e_metadata()

    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")
    _install_urlopen_router(monkeypatch, router)

    provider = FredMacroProvider(raw_dir=tmp_path)
    provider.get_series("CPIAUCSL")

    raw_file = tmp_path / "fred" / "CPIAUCSL" / "output_type2_full.json"
    assert raw_file.exists()
    content = raw_file.read_text(encoding="utf-8")
    assert "api_key" not in json.loads(content)["query"]


# ============================================================
# PIT INTEGRATION TESTS (section 14) -- Provider output fed directly
# into the UNCHANGED src/macro/pit.py
# ============================================================


def _payems_pit_fixture_records():
    """The Fixture B PAYEMS revision sequence, parsed into raw-observation
    rows exactly as FredMacroProvider.get_series() would produce them."""
    records = _parse_crosstab_response(fixture_b_payems_revision()["observations"], "PAYEMS")
    return [
        {
            "series_id": r["series_id"],
            "observation_date": r["observation_date"],
            "value": r["value"],
            "realtime_start": r["realtime_start"],
            "realtime_end": None,
            "source": "FRED",
            "frequency": "M",
            "units": "Thousands of Persons",
            "vintage_date": r["vintage_date"],
        }
        for r in records
    ]


def test_pit_case1_before_first_vintage_selects_none():
    rows = _payems_pit_fixture_records()
    selected = select_latest_vintage_as_of(rows, date(2015, 2, 5))  # before 2015-02-06
    assert selected is None


def test_pit_case2_exact_vintage_selects_that_vintage():
    rows = _payems_pit_fixture_records()
    selected = select_latest_vintage_as_of(rows, date(2015, 3, 6))
    assert selected is not None
    assert selected["vintage_date"] == "2015-03-06"
    assert selected["value"] == 140831.0


def test_pit_case3_between_vintages_selects_previous_available():
    rows = _payems_pit_fixture_records()
    # Between 2015-03-06 and 2015-04-10 -- must select the EARLIER one.
    selected = select_latest_vintage_as_of(rows, date(2015, 4, 9))
    assert selected is not None
    assert selected["vintage_date"] == "2015-03-06"
    assert selected["value"] == 140831.0


def test_pit_case4_after_last_vintage_selects_latest():
    rows = _payems_pit_fixture_records()
    selected = select_latest_vintage_as_of(rows, date(2015, 12, 31))
    assert selected is not None
    assert selected["vintage_date"] == "2015-04-10"
    assert selected["value"] == 140849.0


def test_pit_case5_value_matches_selected_vintage_not_just_date():
    """The most important case (section 14): value selection, not just
    date selection. Using distinct REAL-style revision values
    (140793 -> 140831 -> 140849), confirm each as_of selects both the
    correct vintage_date AND the correct, DIFFERENT value."""
    rows = _payems_pit_fixture_records()
    values_by_as_of = {
        date(2015, 2, 10): 140793.0,
        date(2015, 3, 10): 140831.0,
        date(2015, 4, 15): 140849.0,
    }
    for as_of, expected_value in values_by_as_of.items():
        selected = select_latest_vintage_as_of(rows, as_of)
        assert selected is not None
        assert selected["value"] == expected_value


def test_pit_integration_via_get_point_in_time_series_end_to_end():
    rows = _payems_pit_fixture_records()
    series = get_point_in_time_series(rows, "PAYEMS", "2015-03-10")
    assert len(series) == 1
    assert series[0]["value"] == 140831.0


# ============================================================
# NO-LOOK-AHEAD REGRESSION TEST (section 15)
# ============================================================


def test_no_look_ahead_regression_future_vintage_never_selected():
    """as_of=2021-01-12 with a vintage dated 2021-01-13 present must
    NEVER select that future vintage."""
    rows = [
        {
            "series_id": "CPIAUCSL",
            "observation_date": "2020-12-01",
            "value": 100.0,
            "realtime_start": "2021-01-13",
            "realtime_end": None,
            "source": "FRED",
            "frequency": "M",
            "units": "Index",
            "vintage_date": "2021-01-13",
        }
    ]
    selected = select_latest_vintage_as_of(rows, date(2021, 1, 12))
    assert selected is None  # the only candidate is not yet knowable


def test_no_look_ahead_regression_payems_fixture_future_vintage_excluded():
    """Same regression, using the Fixture B PAYEMS sequence: an as_of
    strictly before the LAST vintage's knowability date must never
    return that last vintage's value."""
    rows = _payems_pit_fixture_records()
    selected = select_latest_vintage_as_of(rows, date(2015, 4, 9))  # one day before 2015-04-10
    assert selected is not None
    assert selected["vintage_date"] != "2015-04-10"
    assert selected["value"] != 140849.0


# ============================================================
# No-network-dependency-in-default-suite guard (section 16)
# ============================================================


def test_no_live_network_calls_in_this_module_by_default(monkeypatch):
    """Guard: with urllib.request.urlopen left completely UNPATCHED (no
    router installed) and no FRED_API_KEY set, calling get_series() must
    raise before any socket is ever touched -- confirming the default
    (un-mocked) pytest run path never reaches the network, which is
    exactly what section 16 requires of the default test suite."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    provider = FredMacroProvider()
    with pytest.raises(FredAPIKeyMissing):
        provider.get_series("CPIAUCSL")
