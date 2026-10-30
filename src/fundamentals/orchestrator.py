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

2026-09-29 update -- GROWTH and DATA_SUFFICIENCY are now wired
-----------------------------------------------------------------------
Per the Framework v2.1 Step 5B production-path verification round
(2026-09-29), this orchestrator now also runs
src.fundamentals.growth.calculate_growth() (a new GROWTH stage) and
src.fundamentals.data_sufficiency.classify_growth_data_sufficiency() (a
new DATA_SUFFICIENCY stage) as two more real stages in the same one
execution path, using the SAME PIT-gated companyfacts and the SAME
already-selected concepts every earlier stage uses (see
_pit_history_for_selected_concept() / _growth_historical_slice() /
_GROWTH_HISTORY_CONCEPTS / _GROWTH_METRIC_CONCEPT_DEPENDENCIES below).
Neither calculate_growth()'s own formulas nor
classify_growth_data_sufficiency()'s own classification rules were
changed to wire this in -- only plumbing (concept selection -> PIT
history -> shape adaptation -> already-existing function call),
matching this module's "thin" philosophy throughout. This does NOT
mean quality.py, Metric Applicability, Runtime Modifier, Relative
Evaluation, or Factor aggregation are wired -- see below, still
current.

IMPORTANT -- this GROWTH -> DATA_SUFFICIENCY placement is NOT the final
Framework v5 Production Execution Order (ChatGPT confirmation,
2026-09-29 approval of Step 5)
-----------------------------------------------------------------------
The current stage order in this function
(... -> derived_metrics -> GROWTH -> DATA_SUFFICIENCY -> peer_group ->
...) exists ONLY as Step 5B integration/verification wiring -- proof
that concept_found genuinely reaches
classify_growth_data_sufficiency() through a real execution path, per
the 2026-09-29 production-path verification round. It is explicitly
NOT confirmed, documented, or to be treated anywhere else in this
codebase as the final economic execution order.

The confirmed final Framework v5 order is:

    Primary Peer Group -> Metric Profile -> Base Applicability ->
    Runtime Modifier -> Data Sufficiency Preflight -> Metric
    Calculation -> Relative Evaluation

Two consequences that follow directly from that confirmed order and do
NOT hold in this file's current (temporary) wiring:
  1. If Applicability resolves to NOT_APPLICABLE for a metric, that
     metric's calculation must never even be attempted -- Applicability
     gates Calculation, not the other way around. This orchestrator's
     GROWTH stage currently runs unconditionally (Metric Applicability
     isn't wired here at all yet -- see the metric_applicability stage
     below, still NOT_WIRED), so this gating does not yet exist here.
  2. Data Sufficiency belongs BEFORE Metric Calculation in the final
     order (a "Preflight"), not after it as a post-hoc relabeling of
     calculate_growth()'s own output the way it runs today.
  3. quality.py Note: this round's constraint list also directs this
     Step 5 round not to add wiring beyond what verification required;
     quality.py accordingly remains completely unwired here.

Re-sequencing GROWTH/DATA_SUFFICIENCY into their correct position
relative to Metric Applicability / Runtime Modifier / Relative
Evaluation is explicitly deferred to Step 6, when Metric Applicability
+ Data Sufficiency + Metric Calculation get their real, final
production connection -- at that point the current temporary wiring
here gets relocated, not re-invented (the underlying calls --
_pit_history_for_selected_concept(), _growth_historical_slice(),
calculate_growth(), classify_growth_data_sufficiency() -- stay the
same; only WHEN they run, relative to Applicability, changes). Nothing
in Step 5 should be read as declaring this stage order final, and
nothing in Step 6 should assume this file's current stage order is a
constraint to preserve.

Still explicitly NOT wired (Framework v2.1 final approval,
constraint (1) -- "Step 3 완료를 10C activation으로 간주하지 않는다")
-----------------------------------------------------------------------
Metric Profile / Base Applicability / Runtime Modifier / Final Runtime
Applicability, Relative Evaluation (percentile scoring against real
peer raw values), Factor aggregation, and quality.py (Quality Factor
calculation) are all deliberately left OUT of this orchestrator's
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

STEP 14 update (2026-10-26) -- Company Type is now a validated,
registry-backed lookup, not a bare caller-supplied value
--------------------------------------------------------------------
Per the user's explicit Production Implementation Authorization
("STEP 14 -- Company Type Source of Truth & Fail-Closed Validation"),
this function now runs a new COMPANY_TYPE_VALIDATION stage FIRST
(before SEC_FETCH, so an unapproved/unresolved cik never triggers a
live SEC request) that resolves this cik's approved Company Type as
of evaluation_date via company_type_registry.get_company_type_as_of()
against the new company_type_rows parameter (required, no default),
and raises ValueError on any non-OK lookup_status or on a caller-
value/registry-value mismatch. See company_type_registry.py's own
module docstring for the full rationale, and the function docstring
below for the exact contract.

STEP 14 Finalization Review update (2026-10-26) -- normalized_data_
validation is now wired, as an INFORMATIONAL stage
--------------------------------------------------------------------
sec_normalizer.validate_normalized_data() is now connected as a new
stage immediately after NORMALIZATION. It is deliberately NOT a
pipeline-blocking gate: it reports whatever failures list the
function returns, but does not raise merely because that list is
non-empty (the function's own existing hard-raise behavior for
genuine structural defects -- a missing top-level key, or an
unrecognized company_type -- is unaffected and still propagates).
See that stage's own inline comment for the full contract analysis;
in short, a non-empty failures list from this function represents a
legitimate, already-meaningful "concept not found/not OK" data state
(the same state GROWTH/DATA_SUFFICIENCY below classify on purpose),
not a structural error, so this orchestrator does not block on it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.fundamentals.company_type_registry import (
    LOOKUP_OK as COMPANY_TYPE_LOOKUP_OK,
    get_company_type_as_of,
)
from src.fundamentals.data_sufficiency import classify_growth_data_sufficiency
from src.fundamentals.derived_metrics import calculate_derived_metrics
from src.fundamentals.finance_lease_evidence_registry import (
    resolve_lease_scope_mismatch_with_evidence,
)
from src.fundamentals.growth import calculate_growth
from src.fundamentals.peer_classification import get_peer_mapping_as_of
from src.fundamentals.point_in_time import (
    coerce_evaluation_date,
    gate_companyfacts_as_of,
    get_point_in_time_history,
)
from src.fundamentals.sec_normalizer import (
    normalize_company,
    validate_companyfacts,
    validate_normalized_data,
)
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
# src.fundamentals.growth.calculate_growth()'s `historical` parameter
# reads exactly these 5 normalize_company() metric keys (see growth.py's
# calculate_growth(): historical.get("revenue"/"operating_income"/
# "diluted_eps"/"cfo"/"capex")). Added 2026-09-29 (production-path
# verification round, Step 5B wiring) -- a naming/wiring table, not an
# economic decision, exactly like _YOY_HISTORY_METRICS above. Overlaps
# with _YOY_HISTORY_METRICS on revenue/operating_income/cfo/capex
# (both derived_metrics.py and growth.py independently need those same
# 4 concepts' PIT history) and additionally needs diluted_eps, which
# derived_metrics.py's YoY kwargs never required.
_GROWTH_HISTORY_CONCEPTS = [
    "revenue",
    "operating_income",
    "diluted_eps",
    "cfo",
    "capex",
]

# Which of growth.py's 5 GROWTH_METRICS depends on which
# _GROWTH_HISTORY_CONCEPTS concept(s), for concept_found propagation
# into data_sufficiency.classify_growth_data_sufficiency() (2026-09-29).
# fcf_yoy depends on BOTH cfo and capex (calculate_growth() builds FCF
# history as cfo + capex before computing YoY on it -- see growth.py's
# _build_fcf_history()); a concept-coverage gap in EITHER one is a
# concept-coverage gap for fcf_yoy too, so both must have been found for
# fcf_yoy's concept_found to be True. This mapping is plumbing (which
# already-selected concept feeds which already-defined growth
# component) -- it does not decide which metrics exist or how they are
# computed; that is growth.py's own GROWTH_METRICS/GROWTH_WEIGHTS.
_GROWTH_METRIC_CONCEPT_DEPENDENCIES = {
    "revenue_yoy": ("revenue",),
    "operating_income_yoy": ("operating_income",),
    "eps_yoy": ("diluted_eps",),
    "fcf_yoy": ("cfo", "capex"),
    "revenue_cagr_3y": ("revenue",),
}

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


def _pit_history_for_selected_concept(
    gated_data: dict,
    evaluation_date: date,
    metric_result: dict | None,
) -> dict | None:
    """
    Like _pit_annual_history_for_selected_concept() above, but returns
    the FULL point_in_time.get_point_in_time_history() result (annual +
    quarterly + standalone_quarters), not just the annual slice.

    Added 2026-09-29 (production-path verification round, Step 5B
    wiring) because src.fundamentals.growth.calculate_growth() needs
    BOTH series: revenue_cagr_3y compares two annual observations, but
    revenue_yoy / operating_income_yoy / eps_yoy / fcf_yoy all compare
    consecutive-year QUARTERS (see growth.py's
    _select_latest_quarter_pair(), called with period="quarterly") --
    something the existing annual-only helper never fetched, because the
    derived_metrics stage above only ever needed the annual series.

    Returns None under the exact same two conditions
    _pit_annual_history_for_selected_concept() already documents:
    metric_result is not an OK sec_normalizer.normalize_company() result
    (no concept was ever resolved for this metric at all), or the
    resolved concept genuinely is not present in gated_data. This
    None-vs-a-real-(possibly-empty)-history-dict distinction is exactly
    the "concept found or not" signal
    data_sufficiency.classify_component_sufficiency()'s concept_found
    parameter was added (2026-09-29) to consume -- see that module's
    docstring for why growth.py's own MISSING status cannot make this
    distinction by itself. Deliberately NOT collapsed into an empty
    history the way _pit_annual_history_for_selected_concept()'s callers
    already do for the annual-only derived_metrics stage.
    """
    if not isinstance(metric_result, dict):
        return None

    if metric_result.get("status") != "OK":
        return None

    namespace = metric_result.get("namespace")
    concept = metric_result.get("concept")

    if not namespace or not concept:
        return None

    # Idempotent re-gate -- see _pit_annual_history_for_selected_concept()
    # above for why calling get_point_in_time_history() again here on
    # already-gated data is harmless.
    return get_point_in_time_history(
        gated_data,
        namespace,
        concept,
        evaluation_date,
    )


def _growth_historical_slice(history: dict | None) -> dict[str, list[dict]]:
    """
    Shape-adapt one concept's full PIT history dict into the
    {"annual": [...], "quarterly": [...]} shape
    src.fundamentals.growth.calculate_growth()'s `historical[concept]`
    expects (added 2026-09-29, Step 5B wiring).

    Pure shape adaptation, no new selection/reconstruction logic (see
    module docstring's "shape adaptation" section and
    _history_records_for_yoy() above for the established precedent this
    follows):

      - "annual": sec_history.py's raw annual records use SEC's own
        "end"/"val" keys -- renamed via the SAME
        _history_records_for_yoy() helper the derived_metrics stage
        already uses above, unchanged.
      - "quarterly": growth.py's YoY components compare CONSECUTIVE-YEAR
        QUARTERS, which is exactly what sec_history.py's already-tested
        reconstruct_standalone_quarters() already computes (Q1=Q1,
        Q2=H1-Q1, Q3=9M-H1, Q4=FY-9M) and get_point_in_time_history()
        already exposes as history["standalone_quarters"] -- NOT the
        same as history["quarterly"] (that field groups raw 10-Q
        observations by cumulative-duration TYPE -- q1/ytd_6m/ytd_9m/
        unknown -- it is not a flat per-quarter series and growth.py
        does not consume it). Standalone-quarter records already use
        growth.py's own {"period_end", "value", "fy", "quarter"} field
        names natively (see sec_history._make_reconstructed_quarter()),
        so -- unlike the annual records -- no key renaming is needed or
        applied here.
    """
    if history is None:
        return {"annual": [], "quarterly": []}

    return {
        "annual": _history_records_for_yoy(history.get("annual") or []),
        "quarterly": list(history.get("standalone_quarters") or []),
    }


# ============================================================
# ORCHESTRATOR
# ============================================================

def run_fundamentals_pipeline(
    *,
    ticker: str,
    cik: str,
    evaluation_date: Any,
    company_type: str,
    company_type_rows: list[dict],
    raw_companyfacts: dict | None = None,
    raw_submissions: dict | None = None,
    peer_rows: list[dict] | None = None,
    membership_rows: list[dict] | None = None,
    sec_user_agent: str | None = None,
    finance_lease_evidence_rows: list[dict] | None = None,
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
        through to sec_normalizer.normalize_company(). As of the STEP 14
        Company Type Source of Truth round (2026-10-26), this
        caller-supplied value is no longer trusted on its own -- it is
        checked against the approved company_type_rows registry for
        this cik as of evaluation_date (see the new COMPANY_TYPE_
        VALIDATION stage below) and must agree with the registry's
        value, or this function raises ValueError. The parameter itself
        is kept (not replaced by a company_type_rows-only lookup) so
        every existing caller's explicit company_type stays a visible,
        checked assertion rather than a silent implicit default.
    company_type_rows : already-loaded data/reference/company_type.csv
        rows (see company_type_registry_io.read_company_type_csv),
        REQUIRED (no default) as of the STEP 14 round. Used by the new
        COMPANY_TYPE_VALIDATION stage to resolve this cik's approved
        Company Type as of evaluation_date via
        company_type_registry.get_company_type_as_of(); any non-OK
        lookup_status (unregistered cik, not-yet-available, structurally
        invalid, or overlapping reference data) fails closed with
        ValueError -- never a guessed/defaulted Company Type. This is
        intentionally required rather than optional-with-SKIPPED (unlike
        peer_rows/membership_rows below), because validating
        company_type is no longer optional per STEP 14's authorization:
        every caller must now go through the approved registry.
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
    finance_lease_evidence_rows : optional, already-loaded
        data/reference/finance_lease_evidence.csv rows. When given, a
        total_debt that is INCOMPLETE with LEASE_SCOPE_MISMATCH may be
        resolved from same-filing evidence (DEC-049). Omitted: unchanged.

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
                "company_type_validation": {"status": ..., "lookup": {...}},
                "sec_fetch": {"status": ..., ...},
                "raw_validation": {"status": ...},
                "identity": {"status": ...},
                "pit_gating": {"status": ...},
                "normalization": {"status": ..., "normalized": {...} },
                "normalized_data_validation": {"status": ..., "failures": [...]},
                "annual_history": {"status": ..., "history": {metric: [...]}},
                "derived_metrics": {"status": ..., "derived": {...}},
                "growth": {"status": ..., "growth": {...}},
                "data_sufficiency": {"status": ..., "sufficiency": {metric: {...}}},
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
    # STAGE: COMPANY_TYPE_VALIDATION (STEP 14, 2026-10-26)
    #
    # Runs FIRST, before any SEC request, per STEP 14 Section 4's
    # instruction that Company Type validation should happen before
    # pipeline side effects where possible. company_type is no longer
    # trusted as a bare caller-supplied value -- it must agree with the
    # approved data/reference/company_type.csv registry's value for
    # this cik as of evaluation_date (company_type_registry.
    # get_company_type_as_of()). Any non-OK lookup_status (unregistered
    # cik -- e.g. BLK/UNH/JPM remain unregistered by design -- not-yet-
    # available, structurally invalid, or overlapping reference data)
    # fails closed with ValueError, matching this function's existing
    # fail-loud convention for shape/input defects (see RAW_VALIDATION
    # above). A caller-value/registry-value mismatch also raises
    # ValueError -- the caller-supplied company_type may only be used to
    # VERIFY agreement with the approved source, never to override it.
    # --------------------------------------------------------

    company_type_lookup = get_company_type_as_of(cik, eval_date, company_type_rows)

    if company_type_lookup["lookup_status"] != COMPANY_TYPE_LOOKUP_OK:
        raise ValueError(
            f"Company Type validation failed for cik={cik!r} as of "
            f"{eval_date.isoformat()}: lookup_status="
            f"{company_type_lookup['lookup_status']!r}. "
            f"{company_type_lookup['reason']}"
        )

    approved_company_type = company_type_lookup["mapping"]["company_type"]
    if company_type != approved_company_type:
        raise ValueError(
            f"company_type mismatch for cik={cik!r}: caller supplied "
            f"{company_type!r}, but the approved Company Type registry "
            f"(data/reference/company_type.csv) says {approved_company_type!r} "
            f"as of {eval_date.isoformat()}. The caller-supplied value may "
            f"only be used to verify agreement with the approved source, "
            f"never to override it."
        )

    stages["company_type_validation"] = {
        "status": STAGE_OK,
        "lookup": company_type_lookup,
    }

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
    # DEC-049: when finance-lease evidence rows are supplied, a total_debt
    # that is INCOMPLETE only because of LEASE_SCOPE_MISMATCH may be
    # resolved from filing-specific evidence (same accession, same-filing
    # combined-debt reconciliation, no carry-forward). Without rows, or
    # without a matching row, total_debt is unchanged. Runs before derived
    # metrics so net_debt / debt_to_cash use the same total_debt.
    if finance_lease_evidence_rows is not None:
        metrics = normalized.get("metrics")
        if isinstance(metrics, dict) and isinstance(metrics.get("total_debt"), dict):
            metrics["total_debt"] = resolve_lease_scope_mismatch_with_evidence(
                metrics["total_debt"], cik, eval_date, finance_lease_evidence_rows, gated_companyfacts
            )
    stages["normalization"] = {
        "status": STAGE_OK,
        "normalized": normalized,
    }

    # --------------------------------------------------------
    # STAGE: NORMALIZED_DATA_VALIDATION (STEP 14 Finalization Review,
    # 2026-10-26)
    #
    # Connects sec_normalizer.validate_normalized_data() WITHOUT
    # redefining its rules and WITHOUT adding a new raise this stage
    # didn't already have a basis for. validate_normalized_data() itself
    # already draws its own two-tier distinction: it RAISES ValueError
    # directly (unchanged, still happens here, propagates exactly as
    # before) for a structural/schema defect (a missing top-level key, or
    # an unrecognized company_type) -- that is unaffected by this stage
    # and was already true before this round. Separately, it RETURNS a
    # list of per-metric completeness gaps (e.g. "net_income", "cfo") for
    # metrics that are structurally fine but weren't found/weren't OK --
    # this is NOT the same as a structural defect: normalize_company()'s
    # own convention (see sec_normalizer.py, build_missing_metric /
    # build_not_applicable_metric) already treats an unfound concept as a
    # legitimate, informative MISSING status, not an error -- and this
    # orchestrator's own GROWTH/DATA_SUFFICIENCY stages (below) exist
    # specifically to classify exactly this situation (CONCEPT_NOT_FOUND
    # vs INSUFFICIENT_HISTORY) as meaningful output, not something to
    # hide behind a raised exception.
    #
    # So this stage reports validate_normalized_data()'s returned
    # failures list transparently -- exactly like the metric_
    # applicability / relative_evaluation / factor "NOT_WIRED" stages
    # below report their own status without blocking the pipeline -- and
    # deliberately does NOT raise merely because that list is non-empty.
    # Doing so would silently reclassify an existing, already-meaningful
    # "legitimate data gap" outcome as a hard pipeline failure, which
    # would also make it structurally impossible to ever reach the
    # GROWTH/DATA_SUFFICIENCY Case A-E tests (added 2026-09-29) for any
    # fixture that doesn't happen to populate all 9 of
    # validate_normalized_data()'s required concepts -- including the
    # existing ORCL_FIXTURE those tests are built on, which deliberately
    # only ever defines 2 of the 9 (Revenue, LongTermNotesPayable) to
    # prove CONCEPT_NOT_FOUND handling works at the real orchestrator
    # level. Raising here would not "connect" that test design, it would
    # erase its ability to run at all -- a material, un-approved change
    # to already-documented test behavior, not a narrow integration.
    # See the STEP 14 Finalization Review report, section D, for the
    # full contract analysis this decision is based on.
    # --------------------------------------------------------

    normalized_data_failures = validate_normalized_data(normalized)
    stages["normalized_data_validation"] = {
        "status": STAGE_OK,
        "failures": normalized_data_failures,
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
    # STAGE: GROWTH (2026-09-29, production-path verification round --
    # Step 5B Data Sufficiency wiring)
    #
    # Invents no new economic/selection logic -- see
    # _pit_history_for_selected_concept() / _growth_historical_slice()
    # above. Reuses the SAME PIT-gated companyfacts and the SAME
    # already-selected concepts (normalized["metrics"]) as every other
    # stage above; calculate_growth() itself is untouched.
    #
    # NOT the final Framework v5 execution order -- see the module
    # docstring's "IMPORTANT -- this GROWTH -> DATA_SUFFICIENCY
    # placement is NOT the final Framework v5 Production Execution
    # Order" section (ChatGPT's 2026-09-29 Step 5 approval). Runs
    # unconditionally here because Metric Applicability isn't wired
    # into this orchestrator at all yet (see the metric_applicability
    # stage below, still NOT_WIRED) -- the final order gates
    # Calculation on Applicability (NOT_APPLICABLE must never even
    # attempt calculation) and runs Data Sufficiency as a Preflight
    # BEFORE Calculation, not after it as a relabeling of
    # calculate_growth()'s own output the way it runs today. Step 6
    # relocates this wiring into that correct position; it does not
    # re-invent it.
    # --------------------------------------------------------

    growth_historical: dict[str, dict[str, list[dict]]] = {}
    concept_found_by_concept: dict[str, bool] = {}

    for concept_key in _GROWTH_HISTORY_CONCEPTS:
        metric_result = normalized["metrics"].get(concept_key)
        concept_history = _pit_history_for_selected_concept(
            gated_companyfacts,
            eval_date,
            metric_result,
        )
        concept_found_by_concept[concept_key] = concept_history is not None
        growth_historical[concept_key] = _growth_historical_slice(concept_history)

    growth_result = calculate_growth(normalized, derived, historical=growth_historical)
    stages["growth"] = {
        "status": STAGE_OK,
        "growth": growth_result,
    }

    # --------------------------------------------------------
    # STAGE: DATA_SUFFICIENCY (2026-09-29, production-path verification
    # round -- Step 5B). Pure relabeling of the GROWTH stage's own
    # already-computed OK/MISSING/INVALID output (see
    # data_sufficiency.py's module docstring) -- classify_growth_data_
    # sufficiency() is called exactly as-is, unmodified this round. The
    # only new wiring is concept_found_by_metric, threaded from the
    # concept-level lookups just above via
    # _GROWTH_METRIC_CONCEPT_DEPENDENCIES, so a metric whose underlying
    # SEC concept was never found at all is never mislabeled
    # INSUFFICIENT_HISTORY (see data_sufficiency.py's CONCEPT_NOT_FOUND).
    # --------------------------------------------------------

    concept_found_by_growth_metric = {
        growth_metric: all(
            concept_found_by_concept.get(concept_key, False)
            for concept_key in concept_keys
        )
        for growth_metric, concept_keys in _GROWTH_METRIC_CONCEPT_DEPENDENCIES.items()
    }

    sufficiency = classify_growth_data_sufficiency(
        growth_result,
        concept_found_by_metric=concept_found_by_growth_metric,
    )
    stages["data_sufficiency"] = {
        "status": STAGE_OK,
        "sufficiency": sufficiency,
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
            "Final Runtime Applicability reference data "
            "(metric_profiles.csv, peer_group_metric_profile.csv, "
            "metric_applicability.csv, metric_applicability_modifiers.csv) "
            "is only header-only reference scaffolding so far -- the real "
            "economic policy rows (Framework v2.1 implementation step 4's "
            "actual content) do not exist yet. Note: Data Sufficiency "
            "(2026-09-29) is now wired separately -- see the "
            "'data_sufficiency' stage above; it is deliberately NOT part "
            "of this NOT_WIRED bucket any more, since Data Sufficiency "
            "and Applicability are two different questions (see "
            "src.fundamentals.data_sufficiency's module docstring)."
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
