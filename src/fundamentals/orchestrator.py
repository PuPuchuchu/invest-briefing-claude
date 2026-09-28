"""
Thin Production Orchestrator (Framework v2.1 Architecture v5,
implementation step 3, 2026-09-28).

What this module is
--------------------
The execution layer that actually connects the modules that, until now,
were only ever exercised individually by their own unit tests: SEC raw
data -> point-in-time gating -> canonical concept normalization ->
annual/quarterly reconstruction -> derived metrics -- as ONE real
execution path, parameterized by (cik, evaluation_date) from the start
so the exact same path can later be re-run across many evaluation_dates
for Historical Validation.

Per ChatGPT's final Framework v2.1 approval message (2026-09-28), this
is "the most important next verification point": confirming that SEC
raw -> canonical normalizer -> PIT -> derived metrics genuinely connect
as one execution path, something module-level unit tests alone cannot
show.

What "thin" means here, concretely
-----------------------------------
This module invents NO new economic, selection, or validation logic.
Every actual decision (which XBRL concept wins among candidates, how a
standalone quarter is reconstructed, how FCF/margins/YoY are computed,
which point-in-time gate a row must pass) is made by a function that
already existed and was already independently tested before this file
was written:

    src.sec.fetcher                    (SEC_FETCH)
    src.fundamentals.sec_normalizer    (RAW_VALIDATION, CONCEPT_SELECTION)
    src.sec.identity                   (IDENTITY, optional)
    src.fundamentals.point_in_time     (PIT_GATING -- including the new
                                         gate_companyfacts_as_of(), added
                                         in this same round, which is
                                         itself pure composition of
                                         already-existing primitives; see
                                         its own docstring)
    src.fundamentals.sec_history       (ANNUAL/QUARTERLY RECONSTRUCTION
                                         -- invoked internally by
                                         point_in_time.get_point_in_time_history)
    src.fundamentals.derived_metrics   (DERIVED_METRICS)
    src.fundamentals.peer_classification (PEER_GROUP, optional)
    src.fundamentals.universe_membership (UNIVERSE_MEMBERSHIP, optional)

This module's only job is sequencing those calls in the right order,
threading (cik, evaluation_date) through all of them consistently, and
doing the minimal SHAPE ADAPTATION needed where two already-existing
modules' data contracts don't line up on their own (see
_history_records_for_yoy() below -- sec_history.py's reconstructed
annual records use SEC's own "end"/"val" keys, while
derived_metrics.py's `*_history` parameters expect "period_end"/
"value"; renaming those keys is plumbing, not a new economic decision).

Explicitly NOT wired in this round (Framework v2.1 final approval,
constraint (1) -- "Step 3 완료를 10C activation으로 간주하지 않는다")
-----------------------------------------------------------------------
Metric Profile / Base Applicability / Runtime Modifier / Final Runtime
Applicability / Data Sufficiency Preflight, Relative Evaluation
(percentile scoring against real peer raw values), and Factor
aggregation are all deliberately left OUT of this orchestrator's
execution path -- not stubbed, not faked, not approximated. Reasons:

    - The Metric Applicability reference data (metric_profiles.csv,
      peer_group_metric_profile.csv, metric_applicability.csv,
      metric_applicability_modifiers.csv) does not exist yet -- it is
      this same implementation plan's step 4.
    - percentile_engine.score_metric_percentile() requires a REAL,
      already-computed raw value for every comparison peer, for the
      SAME metric, as of the SAME evaluation_date -- i.e. this same
      orchestrator run for every other CIK in the peer group. Building
      that multi-company batch runner is a distinct, larger piece of
      work than "wire one company's own pipeline together" and is
      reserved for a later step once Metric Applicability (steps 4-5)
      exists to correctly filter what a peer's "valid" value even means.
      Wiring Factor scoring now, on top of an incomplete Applicability
      layer, would silently violate the fail-closed UNVERIFIED->HOLD
      design finalized in Architecture v5 (a metric that SHOULD be
      HELD could instead be renormalized away) -- exactly the failure
      mode Architecture v5 was written to prevent.

The `stages` dict this module returns marks each of these as
NOT_WIRED with an explicit reason, rather than omitting them, so a
caller (or a future reader of this code) never has to guess whether
they were forgotten or deliberately deferred.

Point-in-time gating: WHY the whole companyfacts document is gated
before concept selection runs, not after
--------------------------------------------------------------------
sec_normalizer.normalize_company() picks the "best" observation among
several CANDIDATE concepts (e.g. noncurrent_debt: LongTermDebtNoncurrent
vs LongTermNotesPayable) by ranking on (latest period end, latest filed
date, candidate priority) -- see find_best_instant_concept()'s
docstring. That ranking function reads `data["facts"]` directly and has
no evaluation_date parameter at all; it is NOT point-in-time-aware by
itself. If it were run on raw, ungated data, it could legitimately
select an observation that was not yet public as of evaluation_date --
a look-ahead-bias violation. So this orchestrator gates the ENTIRE
companyfacts document FIRST (point_in_time.gate_companyfacts_as_of()),
and only then calls normalize_company() on the gated snapshot --
normalize_company()'s own already-approved selection logic then runs
correctly and safely, unmodified, because every observation it can
possibly see was already filtered to filed <= evaluation_date.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.fundamentals.derived_metrics import calculate_derived_metrics
from src.fundamentals.peer_classification import get_peer_mapping_as_of
from src.fundamentals.point_in_time import (
    coerce_evaluation_date,
    gate_companyfacts_as_of,
    get_point_in_time_history,
)
from src.fundamentals.sec_normalizer import normalize_company, validate_companyfacts
from src.fundamentals.universe_membership import get_universe_membership_as_of
from src.sec.fetcher import fetch_companyfacts, get_user_agent
from src.sec.identity import normalize_issuer_identity

SCHEMA_VERSION = "orchestrator_v0.1"

# normalize_company()'s NON_FINANCIAL current-metrics that
# derived_metrics.calculate_derived_metrics() knows how to compute a
# historical YoY series for (its `*_history` keyword parameters). Kept
# here, not invented per-call, so the mapping between "which normalized
# metric" and "which derived_metrics `*_history` kwarg it feeds" is
# declared once, in one place -- this is a naming/wiring table, not an
# economic decision (the underlying calculate_yoy formula, and which
# metrics HAVE a YoY component at all, are calculate_derived_metrics()'s
# and metric_specs.py's decisions, already made elsewhere).
_YOY_HISTORY_METRICS = {
    "revenue": "revenue_history",
    "net_income": "net_income_history",
    "operating_income": "operating_income_history",
    "cfo": "cfo_history",
    "capex": "capex_history",
}

STAGE_NOT_WIRED = "NOT_WIRED"
STAGE_SKIPPED = "SKIPPED"
STAGE_OK = "OK"
STAGE_PROVIDED = "PROVIDED"
STAGE_FETCHED_LIVE = "FETCHED_LIVE"


# ============================================================
# SHAPE ADAPTATION (plumbing, not economic logic -- see module
# docstring)
# ============================================================

def _history_records_for_yoy(annual_records: list[dict]) -> list[dict]:
    """
    Rename sec_history.py's raw-observation-shaped annual records
    ("end" / "val", i.e. SEC's own field names, preserved verbatim by
    sec_history.extract_annual_history) into the {"period_end", "value"}
    shape derived_metrics._calculate_history_yoy() expects. Pure key
    renaming -- every value is passed through unchanged, no new
    computation.
    """
    records = []

    for record in annual_records:
        if not isinstance(record, dict):
            continue

        records.append(
            {
                "period_end": record.get("end"),
                "value": record.get("val"),
                # Original SEC provenance kept alongside under its own
                # keys too, for callers that want to inspect it --
                # calculate_derived_metrics() only reads period_end/value.
                "filed": record.get("filed"),
                "form": record.get("form"),
                "fy": record.get("fy"),
                "accn": record.get("accn"),
            }
        )

    return records


def _pit_annual_history_for_selected_concept(
    gated_data: dict,
    evaluation_date: date,
    metric_result: dict | None,
) -> list[dict] | None:
    """
    Given the (already PIT-gated) companyfacts document and one
    normalize_company() metric result, re-fetch that SAME winning
    concept's PIT-gated annual history via
    point_in_time.get_point_in_time_history() -- this is what actually
    supplies the multi-year series calculate_derived_metrics() needs for
    YoY, since normalize_company() itself only returns the single
    latest selected value, not a series.

    Reuses whichever concept normalize_company() already selected
    (metric_result["namespace"] / metric_result["concept"]) -- this
    function does not re-decide which concept to use; that decision was
    already made by sec_normalizer's own already-approved candidate
    selection logic.

    Returns None if the metric itself was not OK (nothing was selected,
    so there is no concept to re-fetch history for), or if the concept
    genuinely has no annual observations at all.
    """
    if not isinstance(metric_result, dict):
        return None

    if metric_result.get("status") != "OK":
        return None

    namespace = metric_result.get("namespace")
    concept = metric_result.get("concept")

    if not namespace or not concept:
        return None

    # gated_data is already PIT-gated (see gate_companyfacts_as_of()
    # above in the caller), so calling get_point_in_time_history() again
    # here gates a second time -- harmless (gating an already-gated set
    # to the same evaluation_date is idempotent: every observation that
    # remains already satisfies filed <= evaluation_date, so the second
    # pass removes nothing further) and keeps this call symmetric with
    # every other point_in_time.py entry point in this codebase, which
    # all expect to receive raw, ungated data and do their own gating.
    history = get_point_in_time_history(
        gated_data,
        namespace,
        concept,
        evaluation_date,
    )

    if history is None:
        return None

    return history.get("annual") or []


# ============================================================
# ORCHESTRATOR
# ============================================================

def run_fundamentals_pipeline(
    *,
    ticker: str,
    cik: str,
    evaluation_date: Any,
    company_type: str,
    raw_companyfacts: dict | None = None,
    raw_submissions: dict | None = None,
    peer_rows: list[dict] | None = None,
    membership_rows: list[dict] | None = None,
    sec_user_agent: str | None = None,
) -> dict:
    """
    Run the Thin Production Orchestrator's wired execution path for one
    company as of one evaluation_date.

    (cik, evaluation_date) are first-class, required parameters (not
    optional/defaulted) specifically so this same function can be
    called again, unchanged, for a different evaluation_date once
    Historical Validation is built -- per Framework v2.1 Architecture v4
    approval, item 5.

    Parameters
    ----------
    ticker, cik : the target company. cik is the authoritative key used
        for every downstream lookup (see src/sec/identity.py's module
        docstring on why CIK, never ticker, is the identity key).
    evaluation_date : the point-in-time cutoff. Coerced via
        point_in_time.coerce_evaluation_date() -- raises ValueError on
        an unparseable value, matching every other point-in-time entry
        point in this codebase (fail closed, never silently unbounded).
    company_type : "NON_FINANCIAL" or "FINANCIAL", passed straight
        through to sec_normalizer.normalize_company().
    raw_companyfacts : an already-fetched SEC Company Facts dict for
        this CIK. If omitted, this function fetches it live via
        src.sec.fetcher.fetch_companyfacts() -- this is a genuine SEC
        Fetch stage, not a stub, for callers running in an environment
        with SEC EDGAR network access. Passing raw_companyfacts
        explicitly (e.g. from an already-cached file, or from data
        obtained through another channel) is the supported path for
        environments without direct SEC network access, and is also
        what makes this function fully unit-testable without any
        network dependency.
    raw_submissions : an already-fetched SEC Submissions dict for this
        CIK, for the optional Identity stage. If omitted, the Identity
        stage is SKIPPED (never silently treated as OK) -- this
        orchestrator does not fetch Submissions data itself, since
        Identity resolution is not required to reach Normalization /
        PIT / Derived Metrics, this round's actual verification target.
    peer_rows : already-loaded data/reference/peer_groups.csv rows (see
        peer_classification_io.read_peer_groups_csv), for the optional
        Peer Group stage. Callers own I/O for this, per this module's
        own established convention (peer_classification.py's module
        docstring: "This module does NOT ... read or write files").
        SKIPPED if omitted.
    membership_rows : already-loaded data/reference/universe_membership.csv
        rows, for the optional Universe Membership stage (checked for
        both WATCHLIST and CORE). SKIPPED if omitted.
    sec_user_agent : User-Agent string for a live SEC fetch. If omitted,
        read from the SEC_USER_AGENT environment variable (see
        src.sec.fetcher.get_user_agent) -- only consulted when
        raw_companyfacts was not provided.

    Returns
    -------
    A dict:
        {
            "schema_version": SCHEMA_VERSION,
            "ticker": ticker,
            "cik": cik,
            "company_type": company_type,
            "evaluation_date": "<ISO date>",
            "stages": {
                "sec_fetch": {"status": ..., ...},
                "raw_validation": {"status": ...},
                "identity": {"status": ...},
                "pit_gating": {"status": ...},
                "normalization": {"status": ..., "normalized": {...} },
                "annual_history": {"status": ..., "history": {metric: [...]}},
                "derived_metrics": {"status": ..., "derived": {...}},
                "peer_group": {"status": ...},
                "universe_membership": {"status": ..., "watchlist": ..., "core": ...},
                "metric_applicability": {"status": "NOT_WIRED", "reason": "..."},
                "relative_evaluation": {"status": "NOT_WIRED", "reason": "..."},
                "factor": {"status": "NOT_WIRED", "reason": "..."},
            },
        }

    Raises ValueError / TypeError on structurally malformed input
    (unparseable evaluation_date, malformed companyfacts document) --
    matching this codebase's established convention that a shape/input
    defect fails loudly rather than being absorbed into a status field
    (see e.g. sec_normalizer.validate_companyfacts,
    point_in_time.get_point_in_time_history's self-validation).
    """
    eval_date = coerce_evaluation_date(evaluation_date)
    stages: dict[str, Any] = {}

    # --------------------------------------------------------
    # STAGE: SEC_FETCH
    # --------------------------------------------------------

    if raw_companyfacts is not None:
        stages["sec_fetch"] = {"status": STAGE_PROVIDED}
    else:
        user_agent = get_user_agent(default=sec_user_agent)
        raw_companyfacts = fetch_companyfacts(cik, user_agent=user_agent)
        stages["sec_fetch"] = {"status": STAGE_FETCHED_LIVE}

    # --------------------------------------------------------
    # STAGE: RAW_VALIDATION
    #
    # Raises on structural defects -- see docstring above. Not caught
    # here: a malformed companyfacts document is a shape/input error,
    # not a data-availability outcome, matching this codebase's
    # established fail-loud convention for that distinction.
    # --------------------------------------------------------

    validate_companyfacts(raw_companyfacts)
    stages["raw_validation"] = {"status": STAGE_OK}

    # --------------------------------------------------------
    # STAGE: IDENTITY (optional)
    # --------------------------------------------------------

    if raw_submissions is not None:
        issuer_identity = normalize_issuer_identity(
            raw_submissions,
            source_raw_path="<caller-provided raw_submissions>",
            normalized_at=eval_date.isoformat(),
        )
        stages["identity"] = {
            "status": STAGE_OK,
            "issuer_identity": issuer_identity,
        }
    else:
        stages["identity"] = {
            "status": STAGE_SKIPPED,
            "reason": (
                "No raw_submissions provided -- Identity resolution is "
                "not required to reach Normalization / PIT / Derived "
                "Metrics, this round's actual verification target."
            ),
        }

    # --------------------------------------------------------
    # STAGE: PIT_GATING
    #
    # Gate the WHOLE document before concept selection runs -- see
    # module docstring for why this must happen in this order.
    # --------------------------------------------------------

    gated_companyfacts = gate_companyfacts_as_of(raw_companyfacts, eval_date)
    stages["pit_gating"] = {
        "status": STAGE_OK,
        "evaluation_date": eval_date.isoformat(),
    }

    # --------------------------------------------------------
    # STAGE: NORMALIZATION (concept selection, on the PIT-gated
    # snapshot)
    # --------------------------------------------------------

    normalized = normalize_company(ticker, gated_companyfacts, company_type)
    stages["normalization"] = {
        "status": STAGE_OK,
        "normalized": normalized,
    }

    # --------------------------------------------------------
    # STAGE: ANNUAL/QUARTER RECONSTRUCTION (for the metrics
    # derived_metrics.py can compute a YoY series for)
    # --------------------------------------------------------

    annual_history: dict[str, list[dict]] = {}

    for metric_name in _YOY_HISTORY_METRICS:
        metric_result = normalized["metrics"].get(metric_name)
        annual_records = _pit_annual_history_for_selected_concept(
            gated_companyfacts,
            eval_date,
            metric_result,
        )
        if annual_records:
            annual_history[metric_name] = annual_records

    stages["annual_history"] = {
        "status": STAGE_OK,
        "history": annual_history,
    }

    # --------------------------------------------------------
    # STAGE: DERIVED_METRICS
    # --------------------------------------------------------

    yoy_kwargs = {
        kwarg_name: _history_records_for_yoy(annual_history[metric_name])
        for metric_name, kwarg_name in _YOY_HISTORY_METRICS.items()
        if metric_name in annual_history
    }

    derived = calculate_derived_metrics(normalized, **yoy_kwargs)
    stages["derived_metrics"] = {
        "status": STAGE_OK,
        "derived": derived,
    }

    # --------------------------------------------------------
    # STAGE: PEER_GROUP (optional)
    # --------------------------------------------------------

    if peer_rows is not None:
        peer_lookup = get_peer_mapping_as_of(cik, eval_date, peer_rows)
        stages["peer_group"] = {
            "status": STAGE_OK,
            "lookup": peer_lookup,
        }
    else:
        stages["peer_group"] = {
            "status": STAGE_SKIPPED,
            "reason": "No peer_rows provided.",
        }

    # --------------------------------------------------------
    # STAGE: UNIVERSE_MEMBERSHIP (optional)
    # --------------------------------------------------------

    if membership_rows is not None:
        stages["universe_membership"] = {
            "status": STAGE_OK,
            "watchlist": get_universe_membership_as_of(
                cik, "WATCHLIST", eval_date, membership_rows
            ),
            "core": get_universe_membership_as_of(
                cik, "CORE", eval_date, membership_rows
            ),
        }
    else:
        stages["universe_membership"] = {
            "status": STAGE_SKIPPED,
            "reason": "No membership_rows provided.",
        }

    # --------------------------------------------------------
    # STAGES DELIBERATELY NOT WIRED THIS ROUND -- see module docstring.
    # --------------------------------------------------------

    stages["metric_applicability"] = {
        "status": STAGE_NOT_WIRED,
        "reason": (
            "Metric Profile / Base Applicability / Runtime Modifier / "
            "Final Runtime Applicability / Data Sufficiency Preflight "
            "reference data (metric_profiles.csv, "
            "peer_group_metric_profile.csv, metric_applicability.csv, "
            "metric_applicability_modifiers.csv) does not exist yet -- "
            "Framework v2.1 implementation step 4."
        ),
    }
    stages["relative_evaluation"] = {
        "status": STAGE_NOT_WIRED,
        "reason": (
            "Requires real raw values for every comparison peer as of "
            "the same evaluation_date (a multi-company batch run of "
            "this same pipeline) -- not built this round."
        ),
    }
    stages["factor"] = {
        "status": STAGE_NOT_WIRED,
        "reason": (
            "Requires relative_evaluation plus the Metric Applicability "
            "layer's UNVERIFIED -> FACTOR_STATUS_HOLD handling "
            "(Framework v2.1 Architecture v5) to be wired first. Per "
            "Framework v2.1 final approval constraint (1), completing "
            "this Thin Orchestrator must not be mistaken for 10C "
            "Production activation -- this orchestrator deliberately "
            "does not compute a Factor or Composite score."
        ),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "cik": cik,
        "company_type": company_type,
        "evaluation_date": eval_date.isoformat(),
        "stages": stages,
    }


__all__ = [
    "SCHEMA_VERSION",
    "STAGE_NOT_WIRED",
    "STAGE_SKIPPED",
    "STAGE_OK",
    "STAGE_PROVIDED",
    "STAGE_FETCHED_LIVE",
    "run_fundamentals_pipeline",
]
