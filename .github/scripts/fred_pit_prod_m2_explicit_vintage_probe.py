"""
FRED-PIT-PROD-M2 -- isolated, additive LIVE validation of the production
output_type=2 "EXPLICIT_VINTAGE_DATES_SERIES_IDS" fetch strategy
(T10Y2Y, VIXCLS), via the real, unmodified
src.macro.fred_provider.FredMacroProvider.get_series() and
src.macro.pit.select_latest_vintage_as_of()/get_point_in_time_series().

BACKGROUND / WHY THIS IS A SEPARATE FILE, NOT A CHANGE TO
fred_output_type1_validation.py:

  claude/2026-10-24-fred-pit-prod-m2-explicit-vintage-live-probe.md found
  that fred_output_type1_validation.py's existing
  section_m_production_output_type2_pit_selector_probe() hard-gates on
  WIDE_WINDOW_SERIES_IDS membership and main() calls it with the literal
  "PAYEMS" -- there is no way to reach the EXPLICIT_VINTAGE_DATES_SERIES_IDS
  path (T10Y2Y, VIXCLS) through that script/workflow as they stand.
  claude/2026-10-24-fred-pit-prod-m2-enablement-design.md compared two
  designs (a gate-widening change to the existing probe, vs. a dedicated
  new probe) and recommended the dedicated new probe, specifically
  because widening the existing gate without ALSO fixing its hardcoded
  `fetch_strategy` evidence string would make the evidence self-report
  the wrong fetch strategy -- a correctness bug, not a style concern.
  claude/2026-10-24-fred-pit-prod-m2-implementation.md (this round)
  extends that choice one step further: rather than adding a second
  function to fred_output_type1_validation.py and a mode flag to its
  workflow, this is a wholly separate script + separate workflow, so
  that file and fred-output-type1-validation.yml are left with ZERO
  bytes changed -- the strongest possible guarantee that their already-
  audited Section A-M behavior (QA-3 through QA-8, QA-7-CLOSE-2 through
  QA-7-CLOSE-6) is unaffected.

SCOPE / SAFETY (mirrors fred_output_type1_validation.py's own
discipline):
  - does NOT modify, import for mutation, or touch
    src/macro/fred_provider.py, src/macro/pit.py, src/macro/schema.py,
    src/macro/engine.py, fred_output_type1_validation.py, or
    fred-output-type1-validation.yml.
  - DOES import fred_output_type1_validation.py as a plain sibling
    module, READ-ONLY, to reuse its already-audited helpers
    (_build_production_pit_evidence(), _write_evidence_artifact_atomically(),
    _redact_text(), _redact_exception_text(), _log(), _print_header(),
    _shift_months()) rather than reimplementing any of them. This is the
    same "import read-only, never reimplement" discipline that script
    itself uses for src/macro/pit.py and src/macro/fred_provider.py.
  - DOES import src/macro/fred_provider.py's real, unmodified
    FredMacroProvider.get_series() and the EXPLICIT_VINTAGE_DATES_SERIES_IDS
    constant, exactly as fred_output_type1_validation.py's Section M
    already does for WIDE_WINDOW_SERIES_IDS.
  - writes no CSV, no macro-state, no regime, no briefing output.
  - runs on workflow_dispatch only (fred-pit-prod-m2.yml), never
    scheduled, never triggered by push/pull_request.
  - series selection is explicit and required (FRED_PIT_M2_SERIES_ID
    environment variable, set by the workflow from its `series_id`
    workflow_dispatch input): an absent value runs nothing (never a
    silently-guessed default series), and a value outside
    EXPLICIT_VINTAGE_DATES_SERIES_IDS is rejected explicitly, before any
    network call -- never silently redirected to the WIDE_WINDOW
    strategy instead.

SECURITY: FRED_API_KEY is read only from the environment (same
discipline as fred_output_type1_validation.py). No API key, credential,
or unredacted exception text is ever included in anything this script
prints or writes -- every error detail is passed through the reused
_redact_text()/_redact_exception_text() helpers first.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# Read-only reuse of fred_output_type1_validation.py's already-audited
# helpers. This is a plain sibling import (both files live in
# .github/scripts/) -- it does not execute that script's main(), which
# only runs under `if __name__ == "__main__":` in that file. Importing
# it also performs its own repo-root sys.path insertion as a side
# effect, which is what makes `src.macro...` importable below without
# duplicating that logic here.
import fred_output_type1_validation as base_validation  # noqa: E402

from src.macro.fred_provider import (  # noqa: E402
    EXPLICIT_VINTAGE_DATES_SERIES_IDS,
    FredAPIKeyMissing,
    FredHTTPError,
    FredMacroProvider,
    FredProviderError,
)

RESULTS: dict = {}

M2_EVIDENCE_ARTIFACT_PATH = "fred_pit_prod_m2_explicit_vintage_evidence.json"

# Mirrors fred_output_type1_validation.py's own WIDE_WINDOW_SERIES_IDS
# naming convention at line ~147 of that file -- intentionally does
# NOT redefine SERIES_TO_CHECK/DAILY_SERIES/MONTHLY_SERIES here, since
# this probe is scoped to exactly one explicit-vintage-dates series per
# run, selected by the caller.


def section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
    series_id: str, start_date: str, end_date: str
) -> dict:
    """
    FRED-PIT-PROD-M2. LIVE-ONLY wrapper around the REAL, unmodified
    src.macro.fred_provider.FredMacroProvider.get_series() (the actual
    production output_type=2 EXPLICIT_VINTAGE_DATES_SERIES_IDS fetch +
    crosstab-parsing path, via get_series()'s own
    _fetch_explicit_vintage_dates()), then hands the real result to
    fred_output_type1_validation.py's existing, unmodified
    _build_production_pit_evidence() -- deliberately NOT reimplemented
    here, exactly mirroring that file's own
    section_m_production_output_type2_pit_selector_probe() in every
    respect except which series set is accepted and which
    `fetch_strategy` string is reported.

    Fails closed to {"status": "INCONCLUSIVE", "reason": ..., "stage":
    ...} -- never raises -- on an unsupported series_id, when
    FRED_API_KEY is unavailable, on any classified FRED HTTP/response-
    shape error, on any other exception raised by the fetch itself, OR
    on any unexpected exception raised by _build_production_pit_evidence()
    (same defense-in-depth as Section M). Every error detail is passed
    through the reused _redact_text()/_redact_exception_text() before
    being stored; no API key, credential, or unredacted exception text
    is ever included in the returned dict.

    "stage" vocabulary (mirrors Section M's, plus one new value):
      - "request_not_attempted_unsupported_series" (series_id is not in
        EXPLICIT_VINTAGE_DATES_SERIES_IDS -- this probe never silently
        redirects a WIDE_WINDOW_SERIES_IDS series, e.g. PAYEMS, to this
        path; that series is covered by Section M in
        fred_output_type1_validation.py instead)
      - "request_not_attempted_api_key_missing"
      - "request_failed"
      - "request_succeeded_no_usable_candidates"
      - "request_succeeded_evidence_build_error"
      - "request_succeeded_evidence_built"
    "production_path_exercised" is True only once the real fetch
    actually returned data; it is False whenever the fetch itself never
    completed.
    """
    if series_id not in EXPLICIT_VINTAGE_DATES_SERIES_IDS:
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_not_attempted_unsupported_series",
            "reason": (
                f"{series_id!r} is not in EXPLICIT_VINTAGE_DATES_SERIES_IDS "
                f"{sorted(EXPLICIT_VINTAGE_DATES_SERIES_IDS)}; this probe only "
                "targets the production output_type=2 explicit-vintage-dates "
                "path. A WIDE_WINDOW_SERIES_IDS series (e.g. PAYEMS, CPIAUCSL) "
                "is covered by Section M in fred_output_type1_validation.py "
                "instead -- this probe never silently redirects to that "
                "strategy."
            ),
            "series_id": series_id,
            "production_path_exercised": False,
        }

    provider = FredMacroProvider()
    try:
        observations = provider.get_series(series_id, start_date=start_date, end_date=end_date)
    except FredAPIKeyMissing as e:
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_not_attempted_api_key_missing",
            "reason": (
                "FRED_API_KEY is not set in this execution environment; the "
                "real production output_type=2 (explicit-vintage-dates) "
                "request was never made."
            ),
            "series_id": series_id,
            "detail": base_validation._redact_text(str(e)),
            "production_path_exercised": False,
        }
    except FredHTTPError as e:
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_failed",
            "reason": f"Production fetch failed: classification={e.classification} status={e.status}",
            "series_id": series_id,
            "detail": (base_validation._redact_text(e.detail) or "")[:300],
            "production_path_exercised": False,
        }
    except FredProviderError as e:
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_failed",
            "reason": "Production fetch raised a FredProviderError.",
            "series_id": series_id,
            "detail": base_validation._redact_text(str(e)),
            "production_path_exercised": False,
        }
    except Exception as e:  # noqa: BLE001 -- fail closed, never crash main()
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_failed",
            "reason": "Production fetch raised an unexpected exception.",
            "series_id": series_id,
            "detail": base_validation._redact_exception_text(e)[:300],
            "production_path_exercised": False,
        }

    try:
        evidence = base_validation._build_production_pit_evidence(observations, series_id)
    except Exception as e:  # noqa: BLE001 -- fail closed, never crash main()
        return {
            "status": "INCONCLUSIVE",
            "stage": "request_succeeded_evidence_build_error",
            "reason": "_build_production_pit_evidence() raised an unexpected exception.",
            "series_id": series_id,
            "detail": base_validation._redact_exception_text(e)[:300],
            "production_path_exercised": True,
            "total_raw_records_returned": len(observations) if isinstance(observations, list) else None,
        }

    evidence["production_path_exercised"] = True
    evidence["request_params"] = {
        "series_id": series_id,
        "start_date": start_date,
        "end_date": end_date,
        "fetch_strategy": (
            "output_type=2 (EXPLICIT_VINTAGE_DATES_SERIES_IDS path, via the "
            "real, unmodified FredMacroProvider.get_series())"
        ),
    }
    evidence["total_raw_records_returned"] = len(observations)
    return evidence


def main() -> int:
    series_id = os.environ.get("FRED_PIT_M2_SERIES_ID", "").strip()

    if not series_id:
        base_validation._print_header(
            "SECTION M2 -- live production output_type=2 explicit-vintage-dates "
            "PIT selector probe (not requested this run)"
        )
        base_validation._log(
            "  FRED_PIT_M2_SERIES_ID is not set -- this script never guesses a "
            "default series. Nothing was fetched."
        )
        RESULTS["m2_production_output_type2_explicit_vintage_pit_selector_probe"] = {
            "status": "INCONCLUSIVE",
            "stage": "not_requested_this_run",
            "reason": "FRED_PIT_M2_SERIES_ID was not set for this run.",
            "production_path_exercised": False,
        }
        base_validation._print_header("SUMMARY (sanitized -- no API key, no unredacted URL, anywhere above)")
        base_validation._log(json.dumps(RESULTS, indent=2, default=str))
        return 0

    if series_id not in EXPLICIT_VINTAGE_DATES_SERIES_IDS:
        base_validation._print_header(
            "SECTION M2 -- live production output_type=2 explicit-vintage-dates "
            "PIT selector probe (rejected input)"
        )
        base_validation._log(
            f"  FRED_PIT_M2_SERIES_ID={series_id!r} is not one of "
            f"{sorted(EXPLICIT_VINTAGE_DATES_SERIES_IDS)} -- rejecting explicitly "
            "rather than silently falling back to a default or another strategy."
        )
        RESULTS["m2_production_output_type2_explicit_vintage_pit_selector_probe"] = {
            "status": "INCONCLUSIVE",
            "stage": "rejected_invalid_series_input",
            "reason": f"{series_id!r} is not in EXPLICIT_VINTAGE_DATES_SERIES_IDS.",
            "series_id": series_id,
            "production_path_exercised": False,
        }
        base_validation._print_header("SUMMARY (sanitized -- no API key, no unredacted URL, anywhere above)")
        base_validation._log(json.dumps(RESULTS, indent=2, default=str))
        return 0

    today = date.today()
    as_of_today = today.isoformat()
    # Same ~13-month window Section I/Section M already use, reused for
    # consistency, not independently re-derived.
    window_start = base_validation._shift_months(today, -13).isoformat()

    base_validation._print_header(
        f"SECTION M2 -- live production output_type=2 explicit-vintage-dates "
        f"PIT selector probe ({series_id})"
    )
    m2_result = section_m2_production_output_type2_explicit_vintage_pit_selector_probe(
        series_id, window_start, as_of_today
    )
    base_validation._log(json.dumps(m2_result, indent=2, default=str))
    RESULTS["m2_production_output_type2_explicit_vintage_pit_selector_probe"] = m2_result

    evidence_artifact = {
        "validation_timestamp": datetime.utcnow().isoformat() + "Z",
        "round": "FRED-PIT-PROD-M2",
        "probe": "m2_production_output_type2_explicit_vintage_pit_selector_probe",
        "result": m2_result,
    }
    artifact_written, artifact_error_detail = base_validation._write_evidence_artifact_atomically(
        M2_EVIDENCE_ARTIFACT_PATH, evidence_artifact
    )
    if artifact_written:
        base_validation._log(f"  evidence artifact written: {M2_EVIDENCE_ARTIFACT_PATH}")
    else:
        base_validation._log(
            f"  WARNING: could not write evidence artifact file: {artifact_error_detail}"
        )

    base_validation._print_header("SUMMARY (sanitized -- no API key, no unredacted URL, anywhere above)")
    base_validation._log(json.dumps(RESULTS, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
