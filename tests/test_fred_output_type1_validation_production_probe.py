"""
Offline regression tests for Round QA-7-CLOSE-2's new evidence-generation
and assertion logic in .github/scripts/fred_output_type1_validation.py
(_build_production_pit_evidence() and
section_m_production_output_type2_pit_selector_probe()).

PURPOSE: these tests are fixture-based regression evidence for the
EVIDENCE-BUILDER's own correctness -- grouping candidates by
observation_date, choosing an as_of value from the candidates' own
vintage dates, independently computing the expected selection, and
comparing it against the REAL, unmodified pit.select_latest_vintage_as_of()
/ get_point_in_time_series() (imported from src/macro/pit.py, never
reimplemented or mocked here). They are NOT live FRED evidence and must
never be described as such -- every fixture below is a synthetic,
hand-constructed, production-shaped observation list, not a captured or
live FRED response. Round QA-7-CLOSE-2's own report is the place that
states whether a genuine live run occurred; this file only protects the
offline logic from regressing.

Scope note: this file does not touch src/macro/fred_provider.py,
src/macro/pit.py, src/macro/schema.py, or src/macro/engine.py (all
remain frozen) -- it imports pit.py's public functions read-only, via
the validation script's own existing import, exactly as
test_fred_output_type1_validation_security.py already imports that
script for its own tests. No network call is made anywhere in this file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "scripts"
    / "fred_output_type1_validation.py"
)


def _load_module(monkeypatch):
    """Import the validation script fresh. FRED_API_KEY is set to a
    throwaway synthetic value purely so module import never fails if
    anything at import time happened to check for it (it currently does
    not -- see fred_provider.py's _get_api_key() docstring on checking
    only at request time) -- no test in this file makes a real request,
    so this value is never sent anywhere."""
    monkeypatch.setenv("FRED_API_KEY", "OFFLINE_TEST_ONLY_NOT_A_REAL_KEY")
    spec = importlib.util.spec_from_file_location(
        "fred_output_type1_validation_under_test_m", _SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_observation(
    series_id: str, observation_date: str, vintage_date: str, value: float
) -> dict:
    """A synthetic observation dict in exactly the shape
    FredMacroProvider.get_series() actually returns (per its own
    documented fields) -- series_id, observation_date, value,
    realtime_start, realtime_end, source, frequency, units, vintage_date.
    Hand-constructed for this test only; never claimed as a captured or
    live FRED response."""
    return {
        "series_id": series_id,
        "observation_date": observation_date,
        "value": value,
        "realtime_start": vintage_date,
        "realtime_end": None,
        "source": "FRED",
        "frequency": "Monthly",
        "units": "Thousands of Persons",
        "vintage_date": vintage_date,
    }


def test_no_multi_vintage_observation_is_inconclusive(monkeypatch):
    # Every observation_date has exactly one vintage -- there is nothing
    # to tie-break, so this must be INCONCLUSIVE, never a fabricated PASS.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-02-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert "two or more distinct" in result["reason"]


def test_two_vintage_candidates_selects_earlier_and_excludes_later(monkeypatch):
    # Exactly two vintages for one observation_date: as_of is chosen as
    # the earlier (second-newest-of-two) vintage, so the later vintage
    # must be excluded and the earlier one selected.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 99.5),  # revision
        _make_observation("PAYEMS", "2025-02-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["observation_date"] == "2025-01-01"
    assert result["as_of"] == "2025-02-07"
    assert result["eligible_vintage_dates"] == ["2025-02-07"]
    assert result["excluded_future_vintage_dates"] == ["2025-03-07"]
    assert result["expected_selected_vintage"] == "2025-02-07"
    assert result["actual_selected_vintage_direct"] == "2025-02-07"
    assert result["actual_selected_vintage_via_series"] == "2025-02-07"
    assert result["agrees_with_expected"] is True
    assert result["no_future_vintage_selected"] is True


def test_three_vintage_candidates_picks_closer_eligible_not_oldest(monkeypatch):
    # Three distinct vintages for one observation_date: as_of must land
    # on the second-newest, leaving the oldest ALSO eligible (but not
    # selected -- the latest eligible one must win) and the newest
    # excluded as a future vintage.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-06-01", "2025-07-03", 200.0),   # initial
        _make_observation("PAYEMS", "2025-06-01", "2025-08-01", 198.5),  # 1st revision
        _make_observation("PAYEMS", "2025-06-01", "2025-09-05", 197.0),  # 2nd revision (future)
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["as_of"] == "2025-08-01"
    assert result["eligible_vintage_dates"] == ["2025-07-03", "2025-08-01"]
    assert result["excluded_future_vintage_dates"] == ["2025-09-05"]
    # The latest ELIGIBLE vintage must win, not the oldest one.
    assert result["expected_selected_vintage"] == "2025-08-01"
    assert result["actual_selected_vintage_direct"] == "2025-08-01"
    assert result["actual_selected_vintage_via_series"] == "2025-08-01"
    assert result["agrees_with_expected"] is True
    assert result["no_future_vintage_selected"] is True


def test_picks_the_observation_date_with_the_most_distinct_vintages(monkeypatch):
    # Two observation_dates qualify (>=2 distinct vintages each); the
    # one with MORE distinct vintages must be chosen as the stronger
    # evidentiary case, not an arbitrary first match in iteration order.
    module = _load_module(monkeypatch)

    observations = [
        # 2025-03-01: only two vintages
        _make_observation("PAYEMS", "2025-03-01", "2025-04-04", 150.0),
        _make_observation("PAYEMS", "2025-03-01", "2025-05-02", 149.0),
        # 2025-04-01: three vintages -- the stronger case
        _make_observation("PAYEMS", "2025-04-01", "2025-05-02", 160.0),
        _make_observation("PAYEMS", "2025-04-01", "2025-06-06", 159.5),
        _make_observation("PAYEMS", "2025-04-01", "2025-07-03", 158.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["observation_date"] == "2025-04-01"
    assert result["multi_vintage_observation_dates_found"] == 2


def test_duplicate_rows_at_the_same_vintage_date_are_not_a_second_candidate(monkeypatch):
    # A repeated row at the SAME vintage_date (e.g. a duplicate fetch)
    # must not be mistaken for two distinct vintages.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),  # exact duplicate
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"


def test_rows_missing_vintage_date_are_excluded_from_grouping(monkeypatch):
    # A row with no vintage_date at all (e.g. a malformed/placeholder
    # record) must be dropped from candidate grouping, not treated as a
    # usable candidate with a None vintage.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        {
            "series_id": "PAYEMS",
            "observation_date": "2025-01-01",
            "value": 101.0,
            "vintage_date": None,
            "realtime_start": None,
        },
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert "two or more distinct" in result["reason"]


def test_section_wrapper_is_inconclusive_when_series_not_wide_window(monkeypatch):
    # The live wrapper must fail closed to INCONCLUSIVE -- without
    # attempting any network call -- for a series_id outside
    # WIDE_WINDOW_SERIES_IDS (e.g. an EXPLICIT_VINTAGE_DATES_SERIES_IDS
    # member), rather than silently probing the wrong fetch strategy.
    module = _load_module(monkeypatch)

    result = module.section_m_production_output_type2_pit_selector_probe(
        "T10Y2Y", "2025-01-01", "2025-12-31"
    )

    assert result["status"] == "INCONCLUSIVE"
    assert "WIDE_WINDOW_SERIES_IDS" in result["reason"]
    # Round QA-7-CLOSE-2-R2, Part D: an unsupported series_id is a
    # distinct situation from a missing FRED_API_KEY -- no request was
    # EVER attempted for this series regardless of credentials, so it
    # must carry its own stage code, never the misleading
    # "request_not_attempted_api_key_missing" (which previously implied
    # the only reason no request happened was an absent key).
    assert result["stage"] == "request_not_attempted_unsupported_series"


def test_section_wrapper_is_inconclusive_when_api_key_missing_never_claims_live(monkeypatch):
    # With FRED_API_KEY genuinely unset, the live wrapper must fail
    # closed to INCONCLUSIVE with production_path_exercised == False --
    # it must never claim the real output_type=2 request was made when
    # it demonstrably was not.
    module = _load_module(monkeypatch)
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    result = module.section_m_production_output_type2_pit_selector_probe(
        "PAYEMS", "2025-01-01", "2025-12-31"
    )

    assert result["status"] == "INCONCLUSIVE"
    assert result["production_path_exercised"] is False
    assert "FRED_API_KEY" in result["reason"]
    # Round QA-7-CLOSE-2-R2, Part D: this is the genuinely-supported-
    # series-but-no-key case, which must keep its own, distinct stage
    # code from the unsupported-series case above -- never collapsed
    # into one ambiguous label.
    assert result["stage"] == "request_not_attempted_api_key_missing"
    # The synthetic/missing-key message must never contain anything that
    # looks like a request URL or query string (nothing to redact here,
    # but this guards against a future change accidentally adding one
    # unredacted).
    assert "api_key=" not in str(result.get("detail", ""))


# ============================================================
# Round QA-7-CLOSE-2-R1 additions below: fail-closed error handling,
# date validation, candidate-uniqueness/value-consistency, and
# deterministic tie-break. All still offline/synthetic -- never live
# FRED evidence.
# ============================================================

def test_malformed_vintage_date_excluded_not_lexically_compared(monkeypatch):
    # Part B: a row with a non-None but unparseable vintage_date (e.g.
    # "not-a-date") must be excluded entirely, not compared lexically
    # against real ISO dates (which could otherwise let it "win" a
    # string-based max() the way Round AW's analogous bug did for
    # derived_metrics.py's period_end handling).
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
        {
            "series_id": "PAYEMS",
            "observation_date": "2025-01-01",
            "value": 999.0,
            "vintage_date": "not-a-date",
            "realtime_start": "not-a-date",
        },
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["excluded_invalid_date_row_count"] == 1
    # The malformed row's value (999.0) must never appear as a selected
    # or candidate value -- it was never a real candidate.
    assert 999.0 not in [c["value"] for c in result["candidate_sample"]]
    assert result["actual_selected_value_direct"] != 999.0


def test_malformed_observation_date_excluded_entirely(monkeypatch):
    # Part B: a row whose observation_date itself is unparseable must
    # also be excluded -- not grouped under a garbage key.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
        {
            "series_id": "PAYEMS",
            "observation_date": "2025-99-99",
            "value": 500.0,
            "vintage_date": "2025-04-01",
            "realtime_start": "2025-04-01",
        },
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["excluded_invalid_date_row_count"] == 1
    assert result["observation_dates_examined"] == 1


def test_all_dates_malformed_is_inconclusive_not_a_crash(monkeypatch):
    # Part B: if every row's dates are malformed, this must be a clean
    # INCONCLUSIVE, never an uncaught exception or a false PASS.
    module = _load_module(monkeypatch)

    observations = [
        {"series_id": "PAYEMS", "observation_date": "bad", "value": 1.0, "vintage_date": "bad"},
        {"series_id": "PAYEMS", "observation_date": "also-bad", "value": 2.0, "vintage_date": "also-bad"},
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert result["stage"] == "request_succeeded_no_usable_candidates"
    assert result["excluded_invalid_date_row_count"] == 2


def test_conflicting_duplicate_same_date_pair_is_inconclusive_not_false_pass(monkeypatch):
    # Part C, the reproduced gap: two rows share the EXACT SAME
    # (observation_date, vintage_date) pair but carry DIFFERENT values
    # (e.g. a data-integrity problem upstream), while a second, distinct
    # vintage_date also exists. Before this round's fix, the evidence
    # builder counted only DISTINCT vintage_date strings (len>=2) and
    # never checked whether rows sharing one vintage_date actually
    # agreed on value -- so this exact fixture previously returned a
    # confident "PASS", silently picking whichever conflicting row
    # pit.select_latest_vintage_as_of()'s own internal tie-break
    # happened to prefer. It must now be INCONCLUSIVE.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 999.0),  # CONFLICT: same pair, different value
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert result["stage"] == "request_succeeded_no_usable_candidates"
    assert result["conflicted_cell_count"] == 1


def test_exact_duplicate_same_date_pair_and_value_is_deduplicated_not_a_conflict(monkeypatch):
    # Part C: an EXACT duplicate (same pair, same value -- e.g. a
    # duplicate fetch/row) must be silently deduplicated to one
    # candidate, never flagged as a conflict and never counted twice.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),  # exact duplicate
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["conflicted_cell_count"] == 0
    assert result["expected_value"] == 100.0
    assert result["actual_selected_value_direct"] == 100.0
    assert result["values_reconcile"] is True


def test_nan_duplicates_at_same_date_pair_are_consistent_not_a_conflict(monkeypatch):
    # Part C: FRED's missing-value marker parses to float NaN in
    # production (_parse_numeric_cell()). Several NaN rows sharing one
    # (observation_date, vintage_date) pair must be treated as
    # consistent duplicates, never a false "conflict" (NaN != NaN under
    # plain equality).
    module = _load_module(monkeypatch)

    nan = float("nan")
    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    # The NaN duplicates must NOT be flagged as a conflict, and the
    # NaN-aware reconciliation (_values_reconcile()) must still report a
    # clean PASS -- both the expected and actual selected value are the
    # same missing-value marker, which is a consistent result, not an
    # unreconcilable one.
    assert result["conflicted_cell_count"] == 0
    assert result["status"] == "PASS"
    assert result["values_reconcile"] is True
    assert result["expected_value"] != result["expected_value"]  # NaN


def test_mixed_nan_and_finite_value_at_same_date_pair_is_inconclusive_not_false_pass(monkeypatch):
    # Round QA-7-CLOSE-2-R2, Part A: the previous implementation only
    # checked distinct_values among NON-NaN rows and separately checked
    # "all_nan" -- so a cell with ONE NaN row and ONE finite-value row
    # had distinct_values == {100.0} (len 1, not >1) and all_nan ==
    # False, so the "conflict" branch was never taken, and
    # usable_rows.append(rows[0]) silently picked whichever row (NaN or
    # 100.0) happened to be first in insertion order -- a false "PASS"
    # whose selected value depended on row order, never surfaced as a
    # disagreement. Demonstrated pre-fix (fed into a scratch copy of the
    # unmodified pre-R2 function body, outside this test file): the
    # NaN-first ordering returned status="PASS" with
    # actual_selected_value_direct == NaN, while the numeric-first
    # ordering returned status="PASS" with
    # actual_selected_value_direct == 100.0 -- same input data, two
    # different "confident" answers. This must now be INCONCLUSIVE,
    # deterministically, regardless of row order.
    module = _load_module(monkeypatch)
    nan = float("nan")

    observations_nan_first = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),  # MIXED: same pair, finite value
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]
    result_nan_first = module._build_production_pit_evidence(observations_nan_first, "PAYEMS")

    observations_finite_first = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),  # MIXED: same pair, NaN below
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]
    result_finite_first = module._build_production_pit_evidence(observations_finite_first, "PAYEMS")

    for result in (result_nan_first, result_finite_first):
        assert result["status"] == "INCONCLUSIVE"
        assert result["stage"] == "request_succeeded_no_usable_candidates"
        assert result["conflicted_cell_count"] == 1

    # Deterministic: both row orderings must agree with each other, not
    # just each individually avoid a crash.
    assert result_nan_first["conflicted_cell_count"] == result_finite_first["conflicted_cell_count"]


def test_all_nan_duplicates_still_preserved_as_consistent_not_a_conflict(monkeypatch):
    # Round QA-7-CLOSE-2-R2, Part A regression guard: the mixed-NaN fix
    # above must NOT regress the pre-existing, explicitly-tested
    # behavior that several rows that are ALL NaN at one
    # (observation_date, vintage_date) pair remain a CONSISTENT
    # duplicate (no finite value is ever claimed at that cell, so there
    # is nothing to disagree about) -- this is the same fixture as
    # test_nan_duplicates_at_same_date_pair_are_consistent_not_a_conflict
    # above, re-asserted here as a direct before/after pair with the
    # new mixed-NaN test immediately above it.
    module = _load_module(monkeypatch)
    nan = float("nan")

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["conflicted_cell_count"] == 0
    assert result["status"] == "PASS"


def test_deterministic_tie_break_prefers_most_recent_observation_date(monkeypatch):
    # Part D: two observation_dates tie on the maximum distinct-usable-
    # vintage count (2 each). The documented, deterministic rule is to
    # prefer the MOST RECENT observation_date -- never implicit
    # dict/insertion order. Fed in BOTH orders to prove the result is
    # order-independent.
    module = _load_module(monkeypatch)

    def _make_tied_observations(order):
        older = [
            _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
            _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
        ]
        newer = [
            _make_observation("PAYEMS", "2025-06-01", "2025-07-07", 200.0),
            _make_observation("PAYEMS", "2025-06-01", "2025-08-07", 201.0),
        ]
        return older + newer if order == "older_first" else newer + older

    result_a = module._build_production_pit_evidence(
        _make_tied_observations("older_first"), "PAYEMS"
    )
    result_b = module._build_production_pit_evidence(
        _make_tied_observations("newer_first"), "PAYEMS"
    )

    assert result_a["status"] == "PASS"
    assert result_b["status"] == "PASS"
    # Both orderings must pick the MOST RECENT observation_date
    # (2025-06-01), not whichever happened to be inserted first.
    assert result_a["observation_date"] == "2025-06-01"
    assert result_b["observation_date"] == "2025-06-01"


# ============================================================
# Round QA-7-CLOSE-2-R2 additions below: mixed-NaN conflict (Part A,
# see above, placed next to the existing NaN tests), strict
# "YYYY-MM-DD" date validation (Part C), standards-compliant JSON
# artifact serialization (Part B), and the corrected unsupported-series
# stage code (Part D, asserted inline on the existing tests above).
# All still offline/synthetic -- never live FRED evidence.
# ============================================================

def test_strict_date_parser_accepts_canonical_yyyy_mm_dd():
    # Part C: the one documented wire format must parse normally.
    module = _load_module_without_fixture_helper()
    assert module._parse_iso_date_for_evidence("2025-06-01") == module.date(2025, 6, 1)


def test_strict_date_parser_rejects_basic_format_no_separators():
    # Part C: "YYYYMMDD" is accepted by Python 3.11+'s
    # date.fromisoformat() (it is valid ISO 8601, just not the
    # "YYYY-MM-DD" representation this project documents as the wire
    # format) and parses to the same calendar date as "2025-06-01".
    # Silently accepting it would let two different observation_date/
    # vintage_date STRINGS collapse to one calendar date without that
    # ever being decided explicitly. It must be rejected (None), not
    # silently normalized.
    module = _load_module_without_fixture_helper()
    assert module._parse_iso_date_for_evidence("20250601") is None


def test_strict_date_parser_rejects_iso_week_date_form():
    # Part C: ISO week-date form ("YYYY-Www-D") is also accepted by
    # Python 3.11+'s date.fromisoformat() and can parse to the same
    # calendar date as a canonical "YYYY-MM-DD" string for a different-
    # looking input. Must be rejected, not silently normalized.
    module = _load_module_without_fixture_helper()
    assert module._parse_iso_date_for_evidence("2025-W01-1") is None


def test_strict_date_parser_still_rejects_ordinary_malformed_strings():
    # Part C regression guard: the round-trip check must not loosen
    # anything that was already correctly rejected pre-R2.
    module = _load_module_without_fixture_helper()
    assert module._parse_iso_date_for_evidence("not-a-date") is None
    assert module._parse_iso_date_for_evidence(None) is None
    assert module._parse_iso_date_for_evidence(12345) is None


def test_noncanonical_date_forms_excluded_from_evidence_building_not_a_false_pass(monkeypatch):
    # Part C, end-to-end: a row whose vintage_date is syntactically a
    # valid ISO 8601 date (per Python's fromisoformat) but NOT the
    # documented canonical "YYYY-MM-DD" form must be excluded from
    # grouping entirely, exactly like any other malformed date -- never
    # silently accepted as equal to some canonical-form date and never
    # producing an uncaught exception.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
        {
            "series_id": "PAYEMS",
            "observation_date": "2025-01-01",
            "value": 999.0,
            "vintage_date": "20250401",  # basic format, not canonical
            "realtime_start": "20250401",
        },
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["excluded_invalid_date_row_count"] == 1
    assert 999.0 not in [c["value"] for c in result["candidate_sample"]]


def _load_module_without_fixture_helper():
    """Thin alias so the Part C date-parser-only tests above (which take
    no `monkeypatch` fixture, since they never touch FRED_API_KEY or any
    env var) can still load the module. Reuses _load_module()'s own
    loading mechanics with a throwaway monkeypatch-like shim that is
    itself unnecessary here because _parse_iso_date_for_evidence() is a
    pure function -- imported directly via plain importlib, no env
    manipulation required."""
    spec = importlib.util.spec_from_file_location(
        "fred_output_type1_validation_under_test_m_dateonly", _SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_pit_evidence_artifact_is_strict_json_safe_with_nan_value(monkeypatch):
    # Part B: a row carrying FRED's production missing-value marker
    # (float NaN) can end up as "expected_value"/"actual_selected_value_*"
    # in the evidence dict (see
    # test_nan_duplicates_at_same_date_pair_are_consistent_not_a_conflict
    # above). json.dump()/json.dumps() accept NaN by default and emit
    # the literal, non-standard token "NaN" -- not valid JSON under a
    # strict parser. The sanitizer must convert it to JSON null before
    # the artifact is serialized, and the result must round-trip
    # through a strict parser (allow_nan=False) cleanly.
    module = _load_module(monkeypatch)
    nan = float("nan")

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]
    evidence = module._build_production_pit_evidence(observations, "PAYEMS")
    assert evidence["status"] == "PASS"  # sanity: NaN really is present below
    import math
    assert math.isnan(evidence["expected_value"])

    sanitized = module._json_sanitize(evidence)
    # Must serialize under the strictest setting without raising.
    serialized = module.json.dumps(sanitized, default=str, allow_nan=False)
    # And must round-trip through a standard, strict parser.
    reparsed = module.json.loads(serialized)
    assert reparsed["expected_value"] is None  # NaN -> null, never fabricated
    assert reparsed["status"] == "PASS"
    # The literal non-standard tokens must never appear in the output.
    assert "NaN" not in serialized
    assert "Infinity" not in serialized


def test_json_sanitize_preserves_ordinary_values_and_handles_infinity():
    # Part B: the sanitizer's recursion must leave ordinary values
    # (strings, finite numbers, booleans, None, nested dicts/lists)
    # completely unchanged, and must also neutralize +Infinity and
    # -Infinity (not just NaN) wherever they appear, including nested.
    module = _load_module_without_fixture_helper()

    payload = {
        "a_string": "hello",
        "a_bool": True,
        "a_none": None,
        "a_finite_float": 3.5,
        "an_int": 7,
        "a_nan": float("nan"),
        "pos_inf": float("inf"),
        "neg_inf": float("-inf"),
        "nested": {"inner_nan": float("nan"), "inner_list": [1, float("inf"), "x"]},
        "a_list": [float("nan"), 2, "y"],
    }

    sanitized = module._json_sanitize(payload)

    assert sanitized["a_string"] == "hello"
    assert sanitized["a_bool"] is True
    assert sanitized["a_none"] is None
    assert sanitized["a_finite_float"] == 3.5
    assert sanitized["an_int"] == 7
    assert sanitized["a_nan"] is None
    assert sanitized["pos_inf"] is None
    assert sanitized["neg_inf"] is None
    assert sanitized["nested"]["inner_nan"] is None
    assert sanitized["nested"]["inner_list"] == [1, None, "x"]
    assert sanitized["a_list"] == [None, 2, "y"]

    # Confirm the whole thing is strictly serializable afterward.
    serialized = module.json.dumps(sanitized, allow_nan=False)
    module.json.loads(serialized)  # must not raise


def test_evidence_artifact_write_path_block_is_strict_json_parseable(monkeypatch, tmp_path):
    # Part B, end-to-end on the EXACT write-path shape used inside
    # main() -- {"validation_timestamp": ..., "round": ..., "probe":
    # ..., "result": m_result} -- written to an isolated tmp_path file
    # (never into the repository) using the script's own
    # _json_sanitize() + json.dumps(..., allow_nan=False) exactly as
    # the fixed artifact-write block in main() does, without invoking
    # main() itself (which would attempt real network calls across its
    # other sections -- out of scope and explicitly disallowed here).
    # No network call is made anywhere in this test.
    module = _load_module(monkeypatch)
    nan = float("nan")

    m_result = {
        "status": "PASS",
        "stage": "request_succeeded_evidence_built",
        "series_id": "PAYEMS",
        "expected_value": nan,
        "actual_selected_value_direct": nan,
        "actual_selected_value_via_series": nan,
    }
    evidence_artifact = {
        "validation_timestamp": "2026-01-01T00:00:00Z",
        "round": "QA-7-CLOSE-2",
        "probe": "m_production_output_type2_pit_selector_probe",
        "result": m_result,
    }

    artifact_path = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH
    serialized = module.json.dumps(
        module._json_sanitize(evidence_artifact), indent=2, default=str, allow_nan=False
    )
    with open(artifact_path, "w", encoding="utf-8") as f:
        f.write(serialized)

    with open(artifact_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
    assert "NaN" not in raw_text
    assert "Infinity" not in raw_text
    parsed = module.json.loads(raw_text)
    assert parsed["result"]["expected_value"] is None
    assert parsed["result"]["status"] == "PASS"


# ============================================================
# Round QA-7-CLOSE-2-R3 additions below: atomic evidence-artifact
# write lifecycle (Part A) and infinity-aware value-reconciliation
# validity (Part B). All still offline/synthetic -- never live FRED
# evidence, and no network call is made anywhere in this file.
# ============================================================

def test_atomic_write_produces_strict_json_parseable_artifact(monkeypatch, tmp_path):
    # Part A, happy path: a valid payload is written via the atomic
    # helper and the resulting file (not a leftover temp file) is
    # present and parses as strict JSON.
    module = _load_module(monkeypatch)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH

    payload = {"round": "QA-7-CLOSE-2-R3", "result": {"status": "PASS", "value": float("nan")}}
    ok, detail = module._write_evidence_artifact_atomically(str(destination), payload)

    assert ok is True
    assert detail is None
    assert destination.exists()
    # No leftover temp files in the same directory.
    leftovers = [p for p in tmp_path.iterdir() if p != destination]
    assert leftovers == []
    with open(destination, "r", encoding="utf-8") as f:
        raw_text = f.read()
    assert "NaN" not in raw_text
    parsed = module.json.loads(raw_text)
    assert parsed["result"]["value"] is None  # NaN -> null, never fabricated
    assert parsed["result"]["status"] == "PASS"


def test_atomic_write_removes_stale_artifact_when_serialization_fails(monkeypatch, tmp_path):
    # Part A: a pre-existing artifact at the destination (simulating a
    # STALE result left over from a previous run of this script) must
    # not survive when THIS run's serialization fails -- it must never
    # be mistaken for this run's evidence. Serialization is forced to
    # fail via a genuinely unserializable payload (a circular
    # reference, which json.dumps() rejects with ValueError regardless
    # of default=str).
    module = _load_module(monkeypatch)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH

    stale_prior_run_content = '{"round": "QA-7-CLOSE-2", "result": {"status": "PASS"}}'
    destination.write_text(stale_prior_run_content, encoding="utf-8")
    assert destination.exists()

    circular: dict = {}
    circular["self"] = circular  # forces json.dumps() to raise ValueError

    ok, detail = module._write_evidence_artifact_atomically(str(destination), circular)

    assert ok is False
    assert detail is not None
    # The stale artifact must be gone -- never left for the workflow to
    # upload as if it were this run's result.
    assert not destination.exists()
    leftovers = list(tmp_path.iterdir())
    assert leftovers == []  # no stale file, no leftover temp file either


def test_atomic_write_leaves_no_partial_or_stale_artifact_on_replace_failure(monkeypatch, tmp_path):
    # Part A: a pre-existing (stale) artifact is present, serialization
    # succeeds, the temp file is written successfully, but the final
    # os.replace() publish step itself fails (simulated). The
    # destination must end up absent -- never the old stale file, and
    # never a partial file -- and the temp file must be cleaned up.
    module = _load_module(monkeypatch)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH
    destination.write_text('{"stale": true}', encoding="utf-8")

    def _boom_replace(src, dst):
        raise OSError("simulated replace failure, contains no secret")

    monkeypatch.setattr(module.os, "replace", _boom_replace)

    ok, detail = module._write_evidence_artifact_atomically(
        str(destination), {"round": "QA-7-CLOSE-2-R3", "result": {"status": "PASS"}}
    )

    assert ok is False
    assert detail is not None
    assert not destination.exists()
    # The temp file created before the simulated os.replace() failure
    # must be cleaned up -- no leftover files of any kind.
    leftovers = list(tmp_path.iterdir())
    assert leftovers == []


def test_atomic_write_error_detail_is_redacted(monkeypatch, tmp_path):
    # Part A: the error path must never surface an unredacted secret.
    # FRED_API_KEY is set to a synthetic value, and the simulated
    # failure's message is crafted to contain that exact value --
    # _redact_text() must have scrubbed it out of the returned detail.
    module = _load_module(monkeypatch)
    synthetic_key = "OFFLINE_TEST_ONLY_NOT_A_REAL_KEY"
    monkeypatch.setenv("FRED_API_KEY", synthetic_key)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH

    def _boom_replace(src, dst):
        raise OSError(f"simulated failure leaking api_key={synthetic_key} in message")

    monkeypatch.setattr(module.os, "replace", _boom_replace)

    ok, detail = module._write_evidence_artifact_atomically(
        str(destination), {"round": "QA-7-CLOSE-2-R3", "result": {"status": "PASS"}}
    )

    assert ok is False
    assert synthetic_key not in detail
    assert "REDACTED" in detail


def test_positive_infinity_in_selected_vintage_is_inconclusive_not_false_pass(monkeypatch):
    # Round QA-7-CLOSE-2-R3, Part B, the reproduced gap: a single
    # +Infinity value at the selected vintage. Run against the PRE-FIX
    # (R2) implementation first (see this round's own report for the
    # recorded actual pre-fix result: a confident "PASS" with
    # values_reconcile=True and expected_value=inf, because
    # inf == inf under ordinary float equality) -- this test asserts
    # the FIXED behavior: INCONCLUSIVE, with any_infinite_value=True
    # and an explicit reason, never a silent PASS.
    module = _load_module(monkeypatch)
    inf = float("inf")

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", inf),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert result["any_infinite_value"] is True
    assert "infinity" in result["reason"].lower()


def test_negative_infinity_in_selected_vintage_is_inconclusive_not_false_pass(monkeypatch):
    # Part B: the mirror case for -Infinity. Pre-fix (R2), this also
    # returned a confident "PASS" (same reasoning: -inf == -inf).
    module = _load_module(monkeypatch)
    ninf = float("-inf")

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", ninf),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert result["any_infinite_value"] is True


def test_infinite_and_finite_duplicate_at_same_date_pair_is_inconclusive(monkeypatch):
    # Part B: an infinite value and a finite value sharing the SAME
    # (observation_date, vintage_date) pair. This was already correctly
    # INCONCLUSIVE pre-fix too (distinct_values already treats
    # {inf, 100.0} as len==2, a conflicting duplicate under the
    # existing Part A/C logic -- inf is never NaN, so it was never
    # exempted from that check) -- asserted here as an explicit
    # regression guard so this round's Part B change is confirmed not
    # to have altered that pre-existing, already-correct behavior.
    module = _load_module(monkeypatch)
    inf = float("inf")

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", inf),
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),  # CONFLICT: inf vs finite
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert result["conflicted_cell_count"] == 1


def test_all_nan_duplicate_behavior_unchanged_by_infinity_fix(monkeypatch):
    # Part B regression guard: the existing, explicitly-tested all-NaN
    # consistent-duplicate behavior (production's missing-value
    # convention) must be completely unaffected by the new infinity
    # check -- NaN and Infinity are deliberately different
    # (_is_nan_value() vs _is_infinite_value()), so this must still
    # PASS with any_infinite_value=False.
    module = _load_module(monkeypatch)
    nan = float("nan")

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["conflicted_cell_count"] == 0
    assert result["any_infinite_value"] is False


def test_normal_finite_values_still_pass_after_infinity_fix(monkeypatch):
    # Part B regression guard: ordinary finite-value evidence must
    # continue to reach the same "PASS" verdict as before this round,
    # with any_infinite_value explicitly False.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["any_infinite_value"] is False


# ============================================================
# Round QA-7-CLOSE-2-R4 additions below: final exception-safety
# hardening of the atomic artifact writer (Part A, on top of R3) and
# malformed (non-float) "value" handling in the evidence builder
# (Part B). All still offline/synthetic -- never live FRED evidence,
# and no network call is made anywhere in this file.
# ============================================================

def test_atomic_write_non_oserror_failure_during_fsync_is_caught_and_cleaned_up(monkeypatch, tmp_path):
    # Round QA-7-CLOSE-2-R4, Part A (renamed and re-implemented in
    # Round QA-7-CLOSE-2-R6, Part A): a failure during the
    # write/flush/fsync step that is NOT an OSError (e.g. an
    # unexpected RuntimeError from some unforeseen cause) must still
    # be caught -- the pre-R4 implementation only caught
    # `except OSError`, so a non-OSError exception here would have
    # propagated straight out of this "never raises" helper. Must be
    # caught, cleaned up, and reported via the (ok, detail) return
    # contract, never raised.
    #
    # Round QA-7-CLOSE-2-R6, Part A: the original version of this test
    # exercised the non-OSError path by directly overwriting the
    # `write` attribute on the real file object returned by
    # os.fdopen() (`f.write = _boom_write`). That relies on CPython's
    # `_io.TextIOWrapper` (a C-extension type) permitting an instance
    # attribute to shadow a bound C method, which is not a documented
    # or guaranteed-stable behavior of that type across Python
    # versions/implementations. To avoid depending on that, this test
    # now instead monkeypatches `os.fsync()` itself (a plain Python
    # module-level name, safe to patch) to raise a non-OSError
    # exception. This still exercises the exact same code path inside
    # `_write_evidence_artifact_atomically()` -- the
    # `with f: f.write(...); f.flush(); os.fsync(f.fileno())` block
    # and its broad `except Exception` handler -- just at the fsync
    # sub-step rather than the write sub-step; the handler treats
    # failures anywhere in that block identically, so this remains a
    # faithful regression test for the "non-OSError failure inside the
    # write/flush/fsync step must be caught, not propagated" contract
    # this test's name and docstring describe.
    module = _load_module(monkeypatch)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH
    destination.write_text('{"stale": true}', encoding="utf-8")

    def _boom_fsync(fd):
        raise RuntimeError("simulated non-OSError fsync failure, contains no secret")

    monkeypatch.setattr(module.os, "fsync", _boom_fsync)

    ok, detail = module._write_evidence_artifact_atomically(
        str(destination), {"round": "QA-7-CLOSE-2-R4", "result": {"status": "PASS"}}
    )

    assert ok is False
    assert detail is not None
    assert "simulated non-OSError fsync failure" in detail
    # Stale artifact removed (step 1), no partial destination, no
    # leftover temp file.
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_atomic_write_fdopen_failure_closes_raw_fd_and_cleans_up_temp_file(monkeypatch, tmp_path):
    # Round QA-7-CLOSE-2-R4, Part A: if os.fdopen() itself raises before
    # ever wrapping the raw fd tempfile.mkstemp() returned, THIS
    # function -- not os.fdopen() -- owns that fd and must close it
    # directly, or it leaks. This test simulates an os.fdopen() failure
    # that does NOT itself close the fd (the realistic case: fdopen
    # raising for an unrelated reason, e.g. a bad encoding/mode, before
    # it has taken ownership) and checks for a leaked fd via
    # /proc/self/fd (Linux-specific; this sandbox runs Linux -- see
    # this round's own report if this environment ever changes and the
    # check needs to be skipped/reported as unverified instead of
    # forced to pass).
    module = _load_module(monkeypatch)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH

    def _boom_fdopen(fd, *args, **kwargs):
        # Deliberately does NOT close `fd` itself -- mirrors a real
        # os.fdopen() failure (e.g. bad encoding) that raises before
        # taking ownership, so the fd is still open and still this
        # function's responsibility.
        raise ValueError("simulated fdopen failure, contains no secret")

    monkeypatch.setattr(module.os, "fdopen", _boom_fdopen)

    proc_fd_dir = Path("/proc/self/fd")
    if not proc_fd_dir.is_dir():
        pytest.skip("/proc/self/fd not available on this platform -- fd-leak check unverified here")

    fd_count_before = len(list(proc_fd_dir.iterdir()))

    ok, detail = module._write_evidence_artifact_atomically(
        str(destination), {"round": "QA-7-CLOSE-2-R4", "result": {"status": "PASS"}}
    )

    fd_count_after = len(list(proc_fd_dir.iterdir()))

    assert ok is False
    assert detail is not None
    assert "simulated fdopen failure" in detail
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []  # temp file cleaned up
    # The raw fd from mkstemp() must have been closed by THIS function
    # (os.fdopen() never took ownership) -- no net increase in open fds.
    assert fd_count_after <= fd_count_before


def test_atomic_write_cleanup_failure_is_surfaced_not_hidden(monkeypatch, tmp_path):
    # Round QA-7-CLOSE-2-R4, Part A: when the primary write/replace
    # step fails AND the subsequent temp-file cleanup (os.remove) ALSO
    # fails, the function must still never raise, but it must not
    # silently swallow the cleanup failure either -- both the original
    # error and the cleanup failure must be visible in the returned
    # diagnostic.
    module = _load_module(monkeypatch)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH

    def _boom_replace(src, dst):
        raise OSError("simulated primary replace failure, contains no secret")

    real_remove = module.os.remove

    def _boom_remove(path):
        raise OSError("simulated secondary cleanup failure, contains no secret")

    monkeypatch.setattr(module.os, "replace", _boom_replace)
    monkeypatch.setattr(module.os, "remove", _boom_remove)

    try:
        ok, detail = module._write_evidence_artifact_atomically(
            str(destination), {"round": "QA-7-CLOSE-2-R4", "result": {"status": "PASS"}}
        )
    finally:
        # Best-effort real cleanup so this test doesn't itself leave a
        # leftover file in tmp_path for pytest's own teardown.
        for leftover in tmp_path.iterdir():
            try:
                real_remove(str(leftover))
            except OSError:
                pass

    assert ok is False
    assert detail is not None
    # Never raised -- we got here. Both failures must be visible.
    assert "simulated primary replace failure" in detail
    assert "simulated secondary cleanup failure" in detail


def test_cell_with_list_value_is_excluded_not_a_crash_or_false_pass(monkeypatch):
    # Round QA-7-CLOSE-2-R4, Part B, the reproduced gap: a "value" of
    # [] (a list) at the vintage_date that would otherwise be part of
    # the evidence. Pre-fix (R3), {row.get("value") for row in rows ...}
    # raised an uncaught TypeError ("unhashable type: 'list'") the
    # instant this row's value reached the set comprehension -- see
    # this round's own report for the actual pre-fix crash,
    # reproduced directly against an unmodified R3 scratch copy before
    # this fix was written. Must now be excluded (not crash, not a
    # false PASS from only the remaining good vintage).
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", []),  # malformed
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert result["malformed_value_cell_count"] == 1
    assert "malformed" in result["reason"].lower()


def test_cell_with_dict_value_is_excluded_not_a_crash_or_false_pass(monkeypatch):
    # Part B: the dict-value mirror of the list case above.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", {}),  # malformed
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert result["malformed_value_cell_count"] == 1


def test_malformed_value_mixed_with_finite_value_at_same_cell_is_excluded_entirely(monkeypatch):
    # Part B: a malformed value and a well-formed finite value sharing
    # the EXACT SAME (observation_date, vintage_date) pair must not be
    # silently resolved by ignoring the malformed row and using only
    # the finite one -- the whole cell is disqualified, exactly like a
    # genuine value conflict.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", []),  # malformed, SAME cell
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert result["malformed_value_cell_count"] == 1
    # The finite value (100.0) from the malformed cell must never
    # surface as a candidate or selected value.
    assert 100.0 not in [c["value"] for c in result.get("candidate_sample", [])]


def test_malformed_candidate_coexists_with_unrelated_valid_candidate_without_false_pass(monkeypatch):
    # Part B: a malformed value in one observation_date's data must not
    # prevent a WHOLLY UNRELATED, well-formed observation_date from
    # being correctly evaluated -- but it also must never be silently
    # ignored to manufacture a PASS out of only the good rows within
    # its OWN group. Here, 2025-04-01 has a malformed cell (excluded,
    # leaving fewer than 2 usable vintages for that group -- so
    # 2025-04-01 never becomes a candidate at all), while an unrelated,
    # fully well-formed 2025-01-01 group is both present and selected.
    #
    # Round QA-7-CLOSE-2-R6, Part B makes the following policy
    # EXPLICIT (re-confirmed by the assertions below, not changed):
    # this module's PASS status is scoped to the ONE selected
    # observation_date's candidate group -- it is never a guarantee
    # about the integrity or data quality of the FULL response window.
    # A malformed cell at a DIFFERENT, unrelated observation_date (as
    # here) is fully compatible with PASS and is reported via
    # `malformed_value_cell_count` on the very same PASS result (not
    # only on INCONCLUSIVE results) so a reader always sees it,
    # whatever the status. A malformed cell belonging to the SAME
    # group as the one actually selected is a different case, covered
    # by test_malformed_cell_within_selected_group_is_excluded_from_selection_not_silently_used
    # below -- there too, the malformed cell is never "used", just
    # never allowed to silently invalidate the unrelated/selected data
    # that surrounds it.
    module = _load_module(monkeypatch)

    observations = [
        # Unrelated, fully well-formed group -- must be selected and PASS.
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
        # A different observation_date with a malformed value at one
        # of its two vintages -- must be excluded (not silently
        # resolved by the other, well-formed vintage(s) alone turning
        # this group into its own 2-vintage candidate).
        _make_observation("PAYEMS", "2025-04-01", "2025-05-02", 160.0),
        _make_observation("PAYEMS", "2025-04-01", "2025-06-06", {}),  # malformed
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["observation_date"] == "2025-01-01"
    # malformed_value_cell_count is present (and nonzero) on this PASS
    # result, not just on INCONCLUSIVE results -- a malformed cell
    # elsewhere in the window is visible to the reader even when it
    # did not prevent this particular selection from passing.
    assert "malformed_value_cell_count" in result
    assert result["malformed_value_cell_count"] == 1
    # Only one multi-vintage candidate group should have qualified --
    # the malformed one never became a second candidate.
    assert result["multi_vintage_observation_dates_found"] == 1


def test_malformed_cell_within_selected_group_is_excluded_from_selection_not_silently_used(monkeypatch):
    # Round QA-7-CLOSE-2-R6, Part B, item 2: a malformed cell that
    # belongs to the SAME observation_date group as the one that ends
    # up selected must never be "used" by that selection -- it is
    # dropped at the per-cell filtering step, before `usable_rows` (and
    # therefore the `group` the real selector functions are called
    # against) is ever built, exactly like a cell at an unrelated
    # observation_date would be. This is distinct from -- and a
    # stronger guarantee than -- "the selected group can still PASS":
    # here the malformed vintage is itself a THIRD vintage inside the
    # very group that is selected, alongside two well-formed vintages
    # that remain sufficient (>= 2 usable vintages) to form a valid
    # candidate and PASS. The malformed cell's own value (999.0, chosen
    # to be obviously distinguishable) must never appear as a
    # candidate, as the expected or actual selected value, or
    # anywhere else in the result.
    module = _load_module(monkeypatch)

    observations = [
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
        # Third vintage at the SAME observation_date as the two above
        # -- malformed, must be excluded from this group's own
        # selection rather than silently coexisting as an unusable-but-
        # present candidate.
        _make_observation("PAYEMS", "2025-01-01", "2025-04-04", [999.0]),  # malformed
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "PASS"
    assert result["observation_date"] == "2025-01-01"
    assert result["malformed_value_cell_count"] == 1
    # The malformed vintage_date must never appear among the candidate
    # vintages considered for this group, and its value must never
    # surface anywhere in the evidence.
    assert "2025-04-04" not in result["candidate_vintage_dates"]
    assert all(c["vintage_date"] != "2025-04-04" for c in result["candidate_sample"])
    assert 999.0 not in [c["value"] for c in result["candidate_sample"]]
    assert result["expected_value"] != 999.0
    assert result["actual_selected_value_direct"] != 999.0
    assert result["actual_selected_value_via_series"] != 999.0


def test_malformed_value_row_order_does_not_change_result(monkeypatch):
    # Part B: feeding the exact same rows in two different orders must
    # produce the identical status, conflicted/malformed counts, and
    # selected example -- never an order-dependent result.
    module = _load_module(monkeypatch)

    def _rows(order):
        base = [
            _make_observation("PAYEMS", "2025-01-01", "2025-02-07", []),  # malformed
            _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
            _make_observation("PAYEMS", "2025-06-01", "2025-07-07", 200.0),
            _make_observation("PAYEMS", "2025-06-01", "2025-08-07", 201.0),
        ]
        return base if order == "forward" else list(reversed(base))

    result_forward = module._build_production_pit_evidence(_rows("forward"), "PAYEMS")
    result_reversed = module._build_production_pit_evidence(_rows("reversed"), "PAYEMS")

    for result in (result_forward, result_reversed):
        assert result["status"] == "PASS"
        assert result["observation_date"] == "2025-06-01"
        assert result["malformed_value_cell_count"] == 1
    assert result_forward["actual_selected_value_direct"] == result_reversed["actual_selected_value_direct"]


# ============================================================
# Round QA-7-CLOSE-2-R5 additions below: final exception-safety
# completion of the atomic artifact writer (Part A) and selector-
# exception-path diagnostic preservation (Part B). All still
# offline/synthetic -- never live FRED evidence, and no network call
# and no access to src/fundamentals/sec_history.py anywhere in this
# file.
# ============================================================

def test_destination_exists_check_general_exception_does_not_propagate(monkeypatch, tmp_path):
    # Round QA-7-CLOSE-2-R5, Part A #1: a non-OSError failure while
    # checking/removing a pre-existing destination (step 1) must be
    # caught and reported, never propagated out of this "never raises"
    # helper. Pre-R5, step 1 only caught `except OSError`.
    module = _load_module(monkeypatch)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH

    def _boom_exists(self):
        raise RuntimeError("simulated non-OSError exists() failure, contains no secret")

    monkeypatch.setattr(module.Path, "exists", _boom_exists)

    ok, detail = module._write_evidence_artifact_atomically(
        str(destination), {"round": "QA-7-CLOSE-2-R5", "result": {"status": "PASS"}}
    )

    assert ok is False
    assert detail is not None
    assert "simulated non-OSError exists() failure" in detail


def test_cleanup_exists_check_failure_is_reported_not_propagated(monkeypatch, tmp_path):
    # Round QA-7-CLOSE-2-R5, Part A #2: a failure in os.path.exists()
    # itself during temp-file cleanup (step 5) -- not just os.remove()
    # -- must be caught and surfaced, never propagated. The primary
    # failure (a simulated os.replace() error) is forced first so
    # cleanup is attempted at all; the detail must then state plainly
    # that the temp file's removal could NOT be confirmed, rather than
    # silently assuming success or crashing.
    module = _load_module(monkeypatch)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH

    def _boom_replace(src, dst):
        raise OSError("simulated replace failure for exists-check test, contains no secret")

    def _boom_path_exists(path):
        raise RuntimeError("simulated os.path.exists failure, contains no secret")

    monkeypatch.setattr(module.os, "replace", _boom_replace)
    monkeypatch.setattr(module.os.path, "exists", _boom_path_exists)

    ok, detail = module._write_evidence_artifact_atomically(
        str(destination), {"round": "QA-7-CLOSE-2-R5", "result": {"status": "PASS"}}
    )

    assert ok is False
    assert detail is not None
    assert "simulated replace failure for exists-check test" in detail
    assert "simulated os.path.exists failure" in detail
    assert "removal is NOT confirmed" in detail


def test_fdopen_close_failure_is_captured_separately_from_original_fdopen_error(monkeypatch, tmp_path):
    # Round QA-7-CLOSE-2-R5, Part A #3: when os.fdopen() fails AND the
    # subsequent os.close(tmp_fd) (this function's own ownership-
    # cleanup attempt) ALSO fails, that close failure must not be
    # silently dropped (pre-R5: `except OSError: pass`) -- it must be
    # captured and reported as its OWN diagnostic fragment, clearly
    # distinguished from the original os.fdopen() error, never merged
    # into or mistaken for it.
    #
    # Test Review Follow-up, Part A: the simulated os.close() failure
    # below means the real raw fd that tempfile.mkstemp() opened is
    # NEVER actually closed by the code under test (that is the whole
    # point of the simulation) -- so THIS TEST must close it itself,
    # on a best-effort basis, or it leaks a real file descriptor for
    # the rest of the test process's lifetime. The real os.close is
    # saved up front, the fd handed to the fake os.fdopen() is tracked,
    # and the real close happens in `finally` (after restoring the
    # real os.close, before any assertions) so a cleanup failure can
    # never mask the test's own pass/fail result below.
    module = _load_module(monkeypatch)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH

    real_os_close = module.os.close
    tracked_fds: list[int] = []

    def _boom_fdopen(fd, *args, **kwargs):
        tracked_fds.append(fd)
        raise ValueError("simulated fdopen failure, contains no secret")

    def _boom_close(fd):
        raise OSError("simulated close failure, contains no secret")

    monkeypatch.setattr(module.os, "fdopen", _boom_fdopen)
    monkeypatch.setattr(module.os, "close", _boom_close)

    try:
        ok, detail = module._write_evidence_artifact_atomically(
            str(destination), {"round": "QA-7-CLOSE-2-R5", "result": {"status": "PASS"}}
        )
    finally:
        # Restore the real os.close FIRST, then close the tracked fd
        # with it directly -- never through the still-faked module.os.close.
        monkeypatch.setattr(module.os, "close", real_os_close)
        for fd in tracked_fds:
            try:
                real_os_close(fd)
            except OSError:
                pass  # best-effort only; never let cleanup itself raise

    assert ok is False
    assert detail is not None
    # Both failures visible, and distinguishable from one another.
    assert "simulated fdopen failure" in detail
    assert "simulated close failure" in detail
    assert "also failed" in detail
    assert not destination.exists()


def test_non_oserror_removal_failure_during_cleanup_is_captured_distinctly(monkeypatch, tmp_path):
    # Test Review Follow-up, Part B: after a primary os.replace()
    # failure, the subsequent temp-file cleanup's os.remove() call
    # raises a RuntimeError (NOT an OSError). The helper must still
    # never propagate, the primary replace error and this secondary
    # removal error must both be visible and distinguishable, the
    # text must go through the same redaction path as every other
    # diagnostic here, and removal success must never be claimed when
    # it demonstrably did not happen.
    module = _load_module(monkeypatch)
    synthetic_key = "OFFLINE_TEST_ONLY_NOT_A_REAL_KEY"
    monkeypatch.setenv("FRED_API_KEY", synthetic_key)
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH

    def _boom_replace(src, dst):
        raise OSError(f"simulated primary replace failure, api_key={synthetic_key}, no real secret")

    real_os_remove = module.os.remove

    def _boom_remove(path):
        raise RuntimeError("simulated non-OSError removal failure, contains no secret")

    monkeypatch.setattr(module.os, "replace", _boom_replace)
    monkeypatch.setattr(module.os, "remove", _boom_remove)

    try:
        ok, detail = module._write_evidence_artifact_atomically(
            str(destination), {"round": "QA-7-CLOSE-2-R5", "result": {"status": "PASS"}}
        )
    finally:
        # The simulated os.remove() failure means the real temp file
        # tempfile.mkstemp() created is still sitting in tmp_path --
        # clean it up directly with the real os.remove, on a
        # best-effort basis, so this test leaves nothing behind.
        monkeypatch.setattr(module.os, "remove", real_os_remove)
        for leftover in tmp_path.iterdir():
            try:
                real_os_remove(str(leftover))
            except OSError:
                pass

    assert ok is False
    assert detail is not None
    # The helper never propagated the RuntimeError -- we got here.
    assert "simulated primary replace failure" in detail
    assert "simulated non-OSError removal failure" in detail
    # Distinguishable from one another, not merged into a single
    # ambiguous message.
    assert "temp file cleanup also failed" in detail
    # Existing redaction policy applied to both fragments.
    assert synthetic_key not in detail
    assert "REDACTED" in detail


def test_atomic_write_error_detail_redaction_failure_never_exposes_raw_text(monkeypatch, tmp_path):
    # Round QA-7-CLOSE-2-R5, Part A #4: if redaction itself were to
    # raise unexpectedly, the returned diagnostic must fall back to a
    # fixed, generic placeholder -- never to the raw, unredacted
    # exception text. This exercises _safe_redact_text() directly
    # (the function _write_evidence_artifact_atomically() now uses for
    # every diagnostic it returns) rather than trying to break the
    # production _redact_text() itself.
    module = _load_module(monkeypatch)

    def _boom_redact_text(text):
        raise RuntimeError("simulated redaction failure")

    monkeypatch.setattr(module, "_redact_text", _boom_redact_text)

    secret_bearing_text = "leaked_secret_value_should_never_appear"
    safe_result = module._safe_redact_text(secret_bearing_text)

    assert secret_bearing_text not in safe_result
    assert "redaction failed" in safe_result.lower()

    # And end-to-end: a real failure path in the writer, with
    # _redact_text() itself broken, must still never raise and must
    # never leak the raw secret-bearing message.
    destination = tmp_path / module.PRODUCTION_PIT_EVIDENCE_ARTIFACT_PATH

    def _boom_replace(src, dst):
        raise OSError(f"simulated failure containing {secret_bearing_text}")

    monkeypatch.setattr(module.os, "replace", _boom_replace)

    ok, detail = module._write_evidence_artifact_atomically(
        str(destination), {"round": "QA-7-CLOSE-2-R5", "result": {"status": "PASS"}}
    )

    assert ok is False
    assert detail is not None
    assert secret_bearing_text not in detail


def test_selector_exception_path_preserves_computed_diagnostic_counters(monkeypatch):
    # Round QA-7-CLOSE-2-R5, Part B: when the real selector call raises
    # (forcing stage="request_succeeded_evidence_build_error"), every
    # diagnostic counter that had ALREADY been computed earlier in the
    # function (date-validation exclusion, conflicting duplicates,
    # malformed-value cells, observation_dates_examined,
    # multi_vintage_observation_dates_found) must still be present and
    # correct in the returned dict -- not silently dropped just
    # because the exception happened downstream of where they were
    # computed. Built from a fixture containing one of each kind of
    # exclusion, so every counter is non-trivial (not just a
    # coincidental zero).
    module = _load_module(monkeypatch)

    def _boom_select(*args, **kwargs):
        raise RuntimeError("simulated selector failure, contains no secret")

    monkeypatch.setattr(module, "select_latest_vintage_as_of", _boom_select)

    observations = [
        # The genuine 2-vintage candidate (this is the group that will
        # be chosen and whose selector call is forced to raise).
        _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
        _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
        # A malformed-value cell at an unrelated observation_date --
        # excludes that one vintage, leaving only 1 usable vintage
        # there (never becomes a second candidate group).
        _make_observation("PAYEMS", "2025-04-01", "2025-05-02", 160.0),
        _make_observation("PAYEMS", "2025-04-01", "2025-06-06", {}),
        # A conflicting-duplicate cell at a third, unrelated
        # observation_date -- same effect, via a different exclusion.
        _make_observation("PAYEMS", "2025-07-01", "2025-08-01", 300.0),
        _make_observation("PAYEMS", "2025-07-01", "2025-08-01", 999.0),
        _make_observation("PAYEMS", "2025-07-01", "2025-09-01", 301.0),
        # An unparseable observation_date -- excluded at row level,
        # never grouped at all.
        {"series_id": "PAYEMS", "observation_date": "bad-date", "value": 1.0, "vintage_date": "2025-01-01"},
    ]

    result = module._build_production_pit_evidence(observations, "PAYEMS")

    assert result["status"] == "INCONCLUSIVE"
    assert result["stage"] == "request_succeeded_evidence_build_error"
    assert "simulated selector failure" in result["detail"]
    assert result["excluded_invalid_date_row_count"] == 1
    assert result["conflicted_cell_count"] == 1
    assert result["malformed_value_cell_count"] == 1
    assert result["observation_dates_examined"] == 3
    assert result["multi_vintage_observation_dates_found"] == 1


def test_selector_exception_path_counters_are_order_independent(monkeypatch):
    # Round QA-7-CLOSE-2-R5, Part C #7: feeding the exact same rows in
    # two different orders must preserve identical diagnostic counters
    # along the selector-exception path too, not just the ordinary
    # PASS path already covered elsewhere.
    module = _load_module(monkeypatch)

    def _boom_select(*args, **kwargs):
        raise RuntimeError("simulated selector failure, contains no secret")

    monkeypatch.setattr(module, "select_latest_vintage_as_of", _boom_select)

    def _rows(order):
        base = [
            _make_observation("PAYEMS", "2025-01-01", "2025-02-07", 100.0),
            _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
            _make_observation("PAYEMS", "2025-04-01", "2025-05-02", 160.0),
            _make_observation("PAYEMS", "2025-04-01", "2025-06-06", {}),
            _make_observation("PAYEMS", "2025-07-01", "2025-08-01", 300.0),
            _make_observation("PAYEMS", "2025-07-01", "2025-08-01", 999.0),
            _make_observation("PAYEMS", "2025-07-01", "2025-09-01", 301.0),
            {"series_id": "PAYEMS", "observation_date": "bad-date", "value": 1.0, "vintage_date": "2025-01-01"},
        ]
        return base if order == "forward" else list(reversed(base))

    result_forward = module._build_production_pit_evidence(_rows("forward"), "PAYEMS")
    result_reversed = module._build_production_pit_evidence(_rows("reversed"), "PAYEMS")

    for result in (result_forward, result_reversed):
        assert result["status"] == "INCONCLUSIVE"
        assert result["stage"] == "request_succeeded_evidence_build_error"
        assert result["excluded_invalid_date_row_count"] == 1
        assert result["conflicted_cell_count"] == 1
        assert result["malformed_value_cell_count"] == 1
        assert result["observation_dates_examined"] == 3
        assert result["multi_vintage_observation_dates_found"] == 1


def test_row_order_reversal_preserves_selected_example_with_nan_aware_comparison(monkeypatch):
    # Round QA-7-CLOSE-2-R5, Part C #7 (strengthened in the Test Review
    # Follow-up, Part D): on the ordinary (non-exception) PASS path,
    # reversing row order must not change ANY of: status,
    # observation_date, as_of, the selected vintage via BOTH the
    # direct selector call and the series-level call, or the selected
    # value via all three value fields (expected_value,
    # actual_selected_value_direct, actual_selected_value_via_series)
    # -- including when that value is NaN, where a plain `==`
    # comparison would incorrectly report inequality (NaN != NaN).
    # NaN comparisons are therefore handled separately (self-
    # inequality checks), never mixed into the ordinary `==`
    # comparisons used for everything else. Selection logic and
    # production PIT semantics are untouched by this test -- it only
    # observes _build_production_pit_evidence()'s existing behavior.
    module = _load_module(monkeypatch)
    nan = float("nan")

    def _rows(order):
        base = [
            _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),
            _make_observation("PAYEMS", "2025-01-01", "2025-02-07", nan),  # all-NaN duplicate: consistent
            _make_observation("PAYEMS", "2025-01-01", "2025-03-07", 101.0),
        ]
        return base if order == "forward" else list(reversed(base))

    result_forward = module._build_production_pit_evidence(_rows("forward"), "PAYEMS")
    result_reversed = module._build_production_pit_evidence(_rows("reversed"), "PAYEMS")

    for result in (result_forward, result_reversed):
        assert result["status"] == "PASS"
        assert result["observation_date"] == "2025-01-01"
        assert result["as_of"] == "2025-02-07"
        # Both selector paths (direct call and series-level call) must
        # agree on the same selected vintage.
        assert result["actual_selected_vintage_direct"] == "2025-02-07"
        assert result["actual_selected_vintage_via_series"] == "2025-02-07"
        # All three value fields must be the NaN missing-value marker --
        # checked with NaN-aware self-inequality, never plain `==`.
        assert result["expected_value"] != result["expected_value"]
        assert result["actual_selected_value_direct"] != result["actual_selected_value_direct"]
        assert result["actual_selected_value_via_series"] != result["actual_selected_value_via_series"]

    # Non-NaN fields: ordinary equality comparison between the two
    # orderings is fine and expected to hold exactly.
    for field in (
        "status",
        "observation_date",
        "as_of",
        "actual_selected_vintage_direct",
        "actual_selected_vintage_via_series",
        "agrees_with_expected",
        "values_reconcile",
        "no_future_vintage_selected",
        "any_infinite_value",
        "conflicted_cell_count",
        "malformed_value_cell_count",
        "excluded_invalid_date_row_count",
        "observation_dates_examined",
        "multi_vintage_observation_dates_found",
    ):
        assert result_forward[field] == result_reversed[field], f"field {field!r} differs by row order"

    # NaN fields: separated from the ordinary equality loop above --
    # result_forward[field] == result_reversed[field] would be False
    # for two NaNs under plain `==`, which is correct IEEE-754
    # behavior, not a bug -- so these are asserted via self-inequality
    # (both sides are independently confirmed to be NaN) rather than
    # cross-comparison.
    for field in ("expected_value", "actual_selected_value_direct", "actual_selected_value_via_series"):
        assert result_forward[field] != result_forward[field]
        assert result_reversed[field] != result_reversed[field]


def test_wrapper_fails_closed_when_evidence_builder_raises(monkeypatch):
    # Part A, defense-in-depth: even if _build_production_pit_evidence()
    # itself were to raise (simulated here via monkeypatch, since the
    # function is otherwise designed never to), the live wrapper must
    # still catch it and return a clean INCONCLUSIVE rather than letting
    # the exception propagate out of section_m_...().
    module = _load_module(monkeypatch)
    monkeypatch.setenv("FRED_API_KEY", "OFFLINE_TEST_ONLY_NOT_A_REAL_KEY")

    def _boom(observations, series_id):
        raise RuntimeError("simulated unexpected failure, contains no secret")

    monkeypatch.setattr(module, "_build_production_pit_evidence", _boom)

    class _FakeProvider:
        def get_series(self, series_id, *, start_date, end_date):
            return [
                {
                    "series_id": "PAYEMS",
                    "observation_date": "2025-01-01",
                    "value": 100.0,
                    "vintage_date": "2025-02-07",
                    "realtime_start": "2025-02-07",
                }
            ]

    monkeypatch.setattr(module, "FredMacroProvider", _FakeProvider)

    result = module.section_m_production_output_type2_pit_selector_probe(
        "PAYEMS", "2025-01-01", "2025-12-31"
    )

    assert result["status"] == "INCONCLUSIVE"
    assert result["stage"] == "request_succeeded_evidence_build_error"
    assert result["production_path_exercised"] is True
    assert "simulated unexpected failure" in result["detail"]


# ============================================================
# Round QA-7-CLOSE-2-R6 additions below: a test proxy replacing the
# unsafe `f.write = _boom_write` attribute-override approach (Part A),
# explicit PASS-scope assertions for the malformed-value counter
# policy (Part B, folded into the two malformed-candidate tests
# above), and coverage for the previously-unguarded
# `Path(destination_path)` construction at the top of
# _write_evidence_artifact_atomically() (Part C). Still
# offline/synthetic only -- no network call anywhere in this file.
# ============================================================

def test_atomic_write_non_path_like_destination_does_not_propagate(monkeypatch, tmp_path):
    # Round QA-7-CLOSE-2-R6, Part C: `Path(destination_path)` itself
    # used to sit outside every try/except in this function. A value
    # Path() cannot accept -- e.g. a plain int, which is neither str,
    # bytes, nor os.PathLike -- raises TypeError at that very first
    # line, before step 1 (stale-file removal) ever runs. This must
    # now be caught and reported via the ordinary (False, detail)
    # contract, never propagated out of this "never raises" helper.
    module = _load_module(monkeypatch)

    ok, detail = module._write_evidence_artifact_atomically(
        12345,  # not str/bytes/os.PathLike -- Path(12345) raises TypeError
        {"round": "QA-7-CLOSE-2-R6", "result": {"status": "PASS"}},
    )

    assert ok is False
    assert detail is not None
    # Nothing in tmp_path (the test's own isolated directory) should be
    # touched -- the function must fail before any filesystem step.
    assert list(tmp_path.iterdir()) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
