"""
Point-in-time gating layer for SEC XBRL observations.

Why this module exists
-----------------------
Framework v2.0 (finalized 2026-09-13, see project design docs) locks in
one non-negotiable discipline: a metric's value "as of" some evaluation
date must only ever be built from filings that were PUBLICLY AVAILABLE
by that date. Concretely: an observation is usable only if

    observation["filed"] <= evaluation_date

Never gate on the observation's economic period (`start`/`end`) alone --
a company can file a 10-K for fiscal year 2024 as late as several months
into 2025, and SEC filings are sometimes restated. Using period_end
instead of filed_date as the availability cutoff is exactly how
look-ahead bias sneaks into a "historical" backtest.

This module does NOT do concept selection (which XBRL tag represents
"revenue") -- that is src/fundamentals/sec_normalizer.py's job. It does
NOT do annual/quarterly reconstruction (Q2 = H1 - Q1, etc.) -- that is
src/fundamentals/sec_history.py's job. This module is a thin, pure
composition layer that sits between them: look up one already-identified
concept's raw observations (via sec_normalizer.find_concept), gate them
to only those filed on or before the evaluation date, and hand the
gated, still-fully-provenanced list to sec_history.py for reconstruction.

Nothing here flattens or drops SEC provenance fields (filed/start/end/
accn) -- gating only removes whole observations that were not yet public,
it never rewrites the ones that remain.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from src.fundamentals.sec_normalizer import find_concept, get_all_concepts, validate_companyfacts
from src.fundamentals.sec_history import extract_concept_history, validate_history

SCHEMA_VERSION = "point_in_time_v0.1"


# ============================================================
# DATE HELPERS
# ============================================================

def _parse_date(value: Any) -> date | None:
    """Parse an ISO 'YYYY-MM-DD' string (or a date/datetime already) into
    a date. Returns None for anything unparseable -- callers treat that
    as "no filing date on record", which fails the gate closed (excluded),
    never open."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    return None


def coerce_evaluation_date(value: Any) -> date:
    """Normalize the caller-supplied evaluation date into a date object.
    Raises ValueError rather than silently falling back to today's date
    or an unbounded gate -- an unparseable evaluation date is a caller
    bug, not something to paper over, since it directly controls
    look-ahead-bias protection."""
    parsed = _parse_date(value)

    if parsed is None:
        raise ValueError(
            f"evaluation_date could not be parsed as an ISO date: {value!r}"
        )

    return parsed


# ============================================================
# OBSERVATION-LEVEL GATING
# ============================================================

def is_observation_available_as_of(
    observation: dict,
    evaluation_date: date,
) -> bool:
    """
    True only if this observation's filing date is on or before
    evaluation_date. An observation with a missing or unparseable
    'filed' field is treated as NOT available -- the gate fails closed,
    never open, since a missing filed date means we cannot prove the
    data was public by evaluation_date.
    """
    filed = _parse_date(observation.get("filed"))

    if filed is None:
        return False

    return filed <= evaluation_date


def filter_observations_as_of(
    observations: list[dict],
    evaluation_date: date,
) -> list[dict]:
    """Keep only observations available as of evaluation_date. Order and
    every field on the surviving observations are preserved unchanged."""
    return [
        observation
        for observation in observations
        if is_observation_available_as_of(observation, evaluation_date)
    ]


# ============================================================
# CONCEPT-BLOCK-LEVEL GATING
# ============================================================

def as_of_concept_data(
    concept_data: dict,
    evaluation_date: date,
) -> dict:
    """
    Given a raw SEC Company Facts concept block
    (`{"label": ..., "units": {"USD": [...], ...}}`), return a new block
    of the same shape whose per-unit observation lists have been gated
    to evaluation_date. The input is never mutated. Non-list `units`
    entries and non-dict observations are dropped defensively (mirrors
    sec_history.extract_concept_history's own defensive handling), not
    raised on, since malformed SEC data should degrade to "no usable
    observations" rather than crash a point-in-time build.
    """
    if not isinstance(concept_data, dict):
        raise ValueError("concept_data must be a dictionary")

    units = concept_data.get("units")

    if not isinstance(units, dict):
        raise ValueError("concept_data.units must be a dictionary")

    gated_units: dict[str, list[dict]] = {}

    for unit_name, unit_observations in units.items():
        if not isinstance(unit_observations, list):
            continue

        valid_observations = [
            observation
            for observation in unit_observations
            if isinstance(observation, dict)
        ]

        gated_units[unit_name] = filter_observations_as_of(
            valid_observations,
            evaluation_date,
        )

    result = dict(concept_data)
    result["units"] = gated_units

    return result


# ============================================================
# WHOLE-DOCUMENT-LEVEL GATING
# ============================================================

def gate_companyfacts_as_of(
    data: dict,
    evaluation_date: Any,
) -> dict:
    """
    Gate an ENTIRE raw SEC Company Facts document (every namespace and
    concept, not just one already-identified concept) to what was
    publicly available as of evaluation_date.

    Added 2026-09-28 for Framework v2.1 Architecture v5, implementation
    step 3 ("Thin Production Orchestrator"). Why this exists: this
    module's other gating functions (is_observation_available_as_of /
    filter_observations_as_of / as_of_concept_data) all operate on ONE
    already-identified concept block. But sec_normalizer.py's own
    multi-candidate concept SELECTION logic (find_best_instant_concept /
    find_best_annual_concept -- e.g. the candidate list inside
    normalize_company()'s noncurrent_debt branch) reads directly from
    `data["facts"]`, unfiltered, and picks the "best" (latest period end,
    then latest filed date) observation among several candidate
    concepts. If normalize_company() were called on raw, ungated data,
    it could legitimately select an observation that was not yet public
    as of evaluation_date -- a look-ahead-bias violation exactly as
    serious as the one point_in_time.py's other functions already exist
    to prevent for a single concept.

    This function closes that gap WITHOUT introducing any new economic
    or selection logic of its own: it is pure composition of two
    already-validated, already-tested primitives --
    sec_normalizer.get_all_concepts() (enumerate every namespace/concept
    pair present) and this module's own as_of_concept_data() (gate one
    concept block) -- applied to every concept in the document. The
    candidate-selection logic itself (which concept wins, e.g.
    LongTermDebtNoncurrent vs LongTermNotesPayable) is left entirely to
    sec_normalizer.py, unmodified; this function only ensures that by
    the time that selection logic runs, every observation it can
    possibly see was genuinely public as of evaluation_date.

    Returns a new dict of the same top-level shape as `data` (cik /
    entityName / other top-level keys preserved unchanged, `facts`
    replaced with a gated copy). `data` itself is never mutated. A
    concept with zero observations surviving the gate is kept in the
    output as an empty-units concept block (mirrors as_of_concept_data's
    own "nothing was public yet" semantics -- not an error, not
    silently dropped, since sec_normalizer.find_concept() must still be
    able to find the concept key and correctly conclude "no usable
    observation" rather than "concept does not exist at all").
    """
    validate_companyfacts(data)

    evaluation_date = coerce_evaluation_date(evaluation_date)

    gated_facts: dict[str, dict[str, dict]] = {}

    for entry in get_all_concepts(data):
        namespace = entry["namespace"]
        concept_name = entry["concept"]
        concept_data = entry["data"]

        if not isinstance(concept_data, dict):
            # Malformed concept block -- preserved as-is, exactly as
            # get_all_concepts() itself does no shape enforcement here;
            # as_of_concept_data() would raise on this, and a single
            # malformed concept among hundreds must not abort gating the
            # rest of the document.
            gated_facts.setdefault(namespace, {})[concept_name] = concept_data
            continue

        gated_facts.setdefault(namespace, {})[concept_name] = as_of_concept_data(
            concept_data,
            evaluation_date,
        )

    result = dict(data)
    result["facts"] = gated_facts

    return result


# ============================================================
# END-TO-END: LOOKUP -> GATE -> RECONSTRUCT
# ============================================================

def get_point_in_time_history(
    data: dict,
    namespace: str,
    concept_name: str,
    evaluation_date: Any,
) -> dict | None:
    """
    Look up one already-identified XBRL concept
    (namespace + concept_name -- the caller decides which concept
    represents the metric, e.g. via sec_normalizer's concept-priority
    helpers; this function does not choose among candidates), gate its
    observations to what was publicly available as of evaluation_date,
    and reconstruct annual/quarterly/standalone-quarter history from the
    gated set via sec_history.extract_concept_history().

    Returns None if the concept does not exist in `data` at all (matches
    sec_normalizer.find_concept's own None-on-missing behavior). Returns
    an empty-but-valid history structure if the concept exists but every
    observation was filed after evaluation_date (nothing was public yet
    -- a real, expected state for e.g. a newly-listed company evaluated
    shortly after IPO, not an error).
    """
    evaluation_date = coerce_evaluation_date(evaluation_date)

    concept_data = find_concept(data, namespace, concept_name)

    if concept_data is None:
        return None

    gated_concept_data = as_of_concept_data(concept_data, evaluation_date)

    history = extract_concept_history(gated_concept_data)
    history["evaluation_date"] = evaluation_date.isoformat()
    history["namespace"] = namespace
    history["concept"] = concept_name

    # This is the one function in the whole SEC data pipeline whose
    # entire job is preventing look-ahead bias (see module docstring),
    # so its own output is self-validated here rather than left to be
    # checked only if a caller happens to remember to. See
    # validate_point_in_time_history() below for what this actually
    # checks -- notably, it independently re-verifies filed <=
    # evaluation_date on every underlying observation, not just that
    # the shape of the result looks right.
    validation_failures = validate_point_in_time_history(
        history,
        evaluation_date,
    )

    if validation_failures:
        raise ValueError(
            f"get_point_in_time_history() produced invalid output for "
            f"{namespace}:{concept_name} as of "
            f"{evaluation_date.isoformat()}: {validation_failures}"
        )

    return history


# ============================================================
# VALIDATION
# ============================================================

def _all_observation_filed_dates(record: dict) -> list[Any]:
    """
    Collect every 'filed' value referenced by one annual/quarterly/
    standalone-quarter record -- including, for a derived standalone
    quarter, the filed dates buried inside its "source" sub-observations
    (see sec_history._make_reconstructed_quarter) -- so
    validate_point_in_time_history() below can check ALL of them, not
    just the top-level one. A derived quarter (e.g. Q2 = H1 - Q1) is
    only genuinely point-in-time-safe if BOTH the current and the
    previous cumulative observation it was built from were filed on or
    before evaluation_date.
    """
    dates = [record.get("filed")]

    source = record.get("source")

    if isinstance(source, dict):
        for key in ("current", "previous"):
            sub = source.get(key)

            if isinstance(sub, dict):
                dates.append(sub.get("filed"))

    return dates


def validate_point_in_time_history(
    result: dict | None,
    evaluation_date: Any,
) -> list[str]:
    """
    Validate the output of get_point_in_time_history().

    A None result (the concept did not exist in the source data at all)
    is a valid, expected outcome -- callers must check for None
    themselves before calling this; passing None here is reported as a
    failure, not silently accepted.

    Beyond the base structural checks already performed by
    sec_history.validate_history() (schema_version/annual/quarterly/
    standalone_quarters), this adds the two things that are specific to
    -- and the entire reason for -- this module:

        1. the three point-in-time provenance fields this layer adds
           (evaluation_date/namespace/concept) are present, and
           evaluation_date on the result matches what the caller
           actually asked for;
        2. every single observation actually used to build annual,
           quarterly AND standalone_quarters (including the raw
           observations buried inside a derived quarter's own "source"
           block) was genuinely filed on or before evaluation_date --
           i.e. the look-ahead-bias gate this whole module exists to
           enforce was not violated by a bug upstream in
           sec_normalizer.find_concept or
           sec_history.extract_concept_history. This is checked
           independently here rather than trusted, because it is the
           single most consequential invariant in this codebase.

    Returns:
        [] when valid
        list[str] of failure identifiers otherwise
    """
    if not isinstance(result, dict):
        return ["root"]

    failures = list(validate_history(result))

    for key in ("evaluation_date", "namespace", "concept"):
        if key not in result:
            failures.append(key)

    cutoff = coerce_evaluation_date(evaluation_date)

    result_cutoff = _parse_date(result.get("evaluation_date"))

    if result_cutoff is None:
        failures.append("evaluation_date")
    elif result_cutoff != cutoff:
        failures.append("evaluation_date_mismatch")

    def _check_records(label: str, records: Any) -> None:
        if not isinstance(records, list):
            return

        for record in records:
            if not isinstance(record, dict):
                continue

            for filed_value in _all_observation_filed_dates(record):
                filed = _parse_date(filed_value)

                if filed is None or filed > cutoff:
                    failures.append(f"{label}.look_ahead_bias")
                    return

    # "annual" and "standalone_quarters" are flat lists of records.
    _check_records("annual", result.get("annual"))
    _check_records("standalone_quarters", result.get("standalone_quarters"))

    # "quarterly" (see sec_history.extract_quarterly_history) is NOT a
    # flat list -- it is a dict of raw-observation buckets (q1/ytd_6m/
    # ytd_9m/unknown), already checked structurally above via
    # validate_history(). Each bucket still needs the same look-ahead
    # check as everything else.
    quarterly = result.get("quarterly")

    if isinstance(quarterly, dict):
        for bucket_name, bucket_records in quarterly.items():
            _check_records(f"quarterly.{bucket_name}", bucket_records)

    return failures
