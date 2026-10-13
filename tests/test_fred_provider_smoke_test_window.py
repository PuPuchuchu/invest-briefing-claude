"""
Offline, API-key-free tests for the 2026-10-12 T10Y2Y/VIXCLS scope-limiting
change in .github/scripts/fred_provider_live_smoke_test.py (Framework v2.1
Step 10 -- "T10Y2Y output_type=2 스모크 테스트 범위 제한 구현").

PURPOSE: the live smoke test script itself can only be fully exercised
against the real FRED API (workflow_dispatch, GitHub-hosted runner). This
file instead tests, entirely offline, the two pure/structural pieces of
this round's change that CAN be verified without any network access or
FRED_API_KEY:

    1. _compute_lookback_window() -- the 400-day window arithmetic.
    2. section_c_and_d_observations()'s call contract -- that the SAME
       as_of_date-derived window is passed consistently to all four of
       get_series()'s date-range keyword arguments for T10Y2Y/VIXCLS
       (EXPLICIT_VINTAGE_DATES_SERIES_IDS) ONLY, that CPIAUCSL/PAYEMS
       (WIDE_WINDOW_SERIES_IDS) remain unbounded/unchanged, and that a
       normal empty result, a non-empty success, and a raised provider
       exception are three distinct, never-confused outcomes.

Scope note (same discipline as test_fred_output_type1_validation_security.py):
.github/scripts/fred_provider_live_smoke_test.py is validation
infrastructure, not production code. Nothing here makes a real network
call, nothing here depends on FRED_API_KEY being set, and nothing here
modifies or re-tests src/macro/fred_provider.py, src/macro/pit.py,
src/macro/schema.py, or src/macro/engine.py (all frozen, imported
read-only exactly as the smoke test script itself imports them). The
smoke test script is loaded via importlib from its actual path (it lives
under .github/scripts/, which is not a Python package) -- the same
pattern test_fred_output_type1_validation_security.py already
established for the sibling validation script.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from src.macro.fred_provider import EXPLICIT_VINTAGE_DATES_SERIES_IDS, WIDE_WINDOW_SERIES_IDS

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "scripts"
    / "fred_provider_live_smoke_test.py"
)


def _load_module():
    """Import the smoke test script fresh from its real path. No
    FRED_API_KEY is set and none is needed -- this module never reads it
    at import time (see src/macro/fred_provider.py's _get_api_key(),
    which is only called when an actual HTTP request is about to be
    built, never at import)."""
    spec = importlib.util.spec_from_file_location(
        "fred_provider_live_smoke_test_under_test", _SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def smoke_module():
    return _load_module()


def _valid_row(series_id: str) -> dict:
    """One minimal RAW_OBSERVATION_FIELDS-shaped row (src/macro/schema.py)
    -- enough to pass validate_raw_observation() unchanged, used only to
    exercise the SUCCESS_NONEMPTY path below."""
    return {
        "series_id": series_id,
        "observation_date": "2026-09-01",
        "value": 1.0,
        "realtime_start": "2026-09-02",
        "realtime_end": None,
        "source": "FRED",
        "frequency": "DAILY",
        "units": "Percent",
        "vintage_date": "2026-09-02",
    }


class _RecordingFakeProvider:
    """Records every get_series() call's kwargs, keyed by series_id.
    Returns rows_by_series.get(series_id, []) -- i.e. a normal EMPTY
    result (never an exception) when the test does not pre-populate rows
    for that series_id. If raise_for is set to a series_id, raises that
    series_id's configured exception instead of returning anything --
    used to confirm a provider-level failure propagates rather than
    being silently swallowed."""

    def __init__(self, rows_by_series: dict | None = None, raise_for: dict | None = None):
        self.calls: dict[str, dict] = {}
        self._rows_by_series = rows_by_series or {}
        self._raise_for = raise_for or {}

    def get_series(self, series_id: str, **kwargs):
        self.calls[series_id] = kwargs
        if series_id in self._raise_for:
            raise self._raise_for[series_id]
        return self._rows_by_series.get(series_id, [])


# ============================================================
# 1. _compute_lookback_window() -- pure date arithmetic
# ============================================================


def test_lookback_window_is_exactly_400_days_before_as_of_date(smoke_module):
    as_of = date(2026, 10, 12)
    start, end = smoke_module._compute_lookback_window(as_of, 400)
    assert end == "2026-10-12"
    assert start == "2025-09-07"  # 2026-10-12 minus 400 days, computed independently
    assert (date.fromisoformat(end) - date.fromisoformat(start)).days == 400


def test_lookback_window_uses_the_module_constant_by_default(smoke_module):
    assert smoke_module.SMOKE_TEST_LOOKBACK_DAYS == 400


def test_lookback_window_is_deterministic_for_a_fixed_as_of_date(smoke_module):
    as_of = date(2026, 1, 1)
    first = smoke_module._compute_lookback_window(as_of, 400)
    second = smoke_module._compute_lookback_window(as_of, 400)
    assert first == second


# ============================================================
# 2. section_c_and_d_observations() -- call contract
# ============================================================


def test_windowed_series_receive_all_four_date_boundaries_consistently(smoke_module):
    window_start, window_end = "2025-09-07", "2026-10-12"
    provider = _RecordingFakeProvider()

    smoke_module.section_c_and_d_observations(provider, window_start, window_end)

    for series_id in EXPLICIT_VINTAGE_DATES_SERIES_IDS:
        call = provider.calls[series_id]
        assert call == {
            "start_date": window_start,
            "end_date": window_end,
            "realtime_start": window_start,
            "realtime_end": window_end,
        }, f"{series_id}: expected all four date boundaries to use the same window, got {call}"


def test_wide_window_series_remain_unbounded_and_unchanged(smoke_module):
    """CPIAUCSL/PAYEMS must NOT receive the 400-day window -- this round
    deliberately leaves them unchanged (see the module-level comment next
    to SMOKE_TEST_LOOKBACK_DAYS: Section F's PAYEMS
    revision-distinct-value check needs full history, and these two
    series do not exhibit the hang)."""
    provider = _RecordingFakeProvider()

    smoke_module.section_c_and_d_observations(provider, "2025-09-07", "2026-10-12")

    for series_id in WIDE_WINDOW_SERIES_IDS & set(smoke_module.SERIES_TO_CHECK):
        call = provider.calls[series_id]
        assert call == {}, f"{series_id}: expected no date-range kwargs, got {call}"


def test_empty_result_is_reported_distinctly_not_as_a_silent_pass(smoke_module, capsys):
    provider = _RecordingFakeProvider()  # no rows configured -> every series returns []

    out = smoke_module.section_c_and_d_observations(provider, "2025-09-07", "2026-10-12")

    assert out["T10Y2Y"] == []
    captured = capsys.readouterr().out
    assert "status=EMPTY_RESULT_NONPASS" in captured
    assert "row_count=0" in captured
    # the empty-result log line must never be mistaken for the
    # non-empty-success label
    assert "status=SUCCESS_NONEMPTY" not in captured


def test_nonempty_result_is_reported_distinctly_from_empty(smoke_module, capsys):
    rows_by_series = {sid: [_valid_row(sid)] for sid in smoke_module.SERIES_TO_CHECK}
    provider = _RecordingFakeProvider(rows_by_series=rows_by_series)

    out = smoke_module.section_c_and_d_observations(provider, "2025-09-07", "2026-10-12")

    assert len(out["T10Y2Y"]) == 1
    captured = capsys.readouterr().out
    assert "status=SUCCESS_NONEMPTY" in captured
    assert "row_count=1" in captured


def test_schema_invalid_row_still_raises_systemexit_unchanged(smoke_module):
    """Existing assertion (pre-dates this round) must survive unchanged:
    a structurally invalid row is still a hard failure, not merely logged."""
    bad_row = {"series_id": "T10Y2Y"}  # missing every other required field
    provider = _RecordingFakeProvider(rows_by_series={"T10Y2Y": [bad_row]})

    with pytest.raises(SystemExit):
        smoke_module.section_c_and_d_observations(provider, "2025-09-07", "2026-10-12")


def test_provider_exception_propagates_and_is_not_swallowed(smoke_module):
    """A provider-level failure (HTTP error, timeout, etc. -- modeled here
    as a generic RuntimeError standing in for FredHTTPError, since
    constructing a real FredHTTPError is exercised elsewhere) must
    propagate out of section_c_and_d_observations(), never be caught and
    silently turned into an empty result."""
    boom = RuntimeError("simulated provider failure")
    provider = _RecordingFakeProvider(raise_for={"T10Y2Y": boom})

    with pytest.raises(RuntimeError, match="simulated provider failure"):
        smoke_module.section_c_and_d_observations(provider, "2025-09-07", "2026-10-12")


def test_main_source_derives_window_from_date_today_via_the_shared_helper():
    """Static check (no execution of main(), which requires network
    access): main()'s source must derive its window from
    _compute_lookback_window(date.today(), SMOKE_TEST_LOOKBACK_DAYS)
    -- i.e. the same single helper already verified above, not a second,
    independently-written date computation that could drift from it."""
    main_source = _SCRIPT_PATH.read_text()
    assert "as_of_date = date.today()" in main_source
    assert "_compute_lookback_window(" in main_source
    assert "as_of_date, SMOKE_TEST_LOOKBACK_DAYS" in main_source
