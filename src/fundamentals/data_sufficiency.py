"""
Data Sufficiency classification layer (Framework v2.1 Step 5B, 2026-09-28
ChatGPT confirmation round).

Why this module exists
-----------------------
Framework v2.1's factor-weight pipeline separates two DIFFERENT questions
that are easy to conflate:

    1. Applicability (src/fundamentals/metric_applicability.py, Step 4/5A)
       -- "does this metric make economic sense for this company's
       profile?" Answered by APPLICABLE / NOT_APPLICABLE / CONDITIONAL /
       UNVERIFIED, entirely from reference-data policy. A metric can be
       fully APPLICABLE and still be uncomputable today.

    2. Data Sufficiency (THIS module, Step 5B) -- "given that a metric IS
       applicable, do the PIT-gated observations needed to actually
       calculate it exist yet?" A newly-listed or recently-spun-off
       company (the SNDK case) is the canonical example: revenue_yoy is
       fully APPLICABLE for it, today's revenue observation exists, but
       there is no PIT-gated comparable observation from a year ago
       because the company didn't exist as a public filer back then. That
       is a Data Sufficiency gap, never an Applicability question, and
       must never be recoded as NOT_APPLICABLE or UNVERIFIED (2026-09-28
       confirmation, section 5).

This module does NOT reimplement observation selection or PIT gating. The
2026-09-28 confirmation is explicit: "growth.py의 기존 비교 관측치 선택
로직을 중복해서 새 계산 로직으로 복제하지 마세요." src/fundamentals/growth.py's
calculate_growth() already performs the sufficiency check internally --
via _select_latest_quarter_pair() / _select_three_year_revenue_pair(),
themselves built on src/fundamentals/point_in_time.py's PIT-gated
get_point_in_time_history() -- and already returns status="MISSING" with a
free-text reason exactly when a required historical comparison observation
does not exist. By inspection (2026-09-28 investigation round), every
single MISSING result growth.py produces for the 5 GROWTH_METRICS IS an
insufficient-history case -- there is no other cause of MISSING in that
module for these metrics (a present-but-unusable value, e.g. a zero or
negative denominator, is INVALID instead, never MISSING).

So this module's entire job is to RELABEL growth.py's already-correct,
already-tested OK / MISSING / INVALID determination into the Step 5B
structured vocabulary (calculation_status + reason_code), never to
recompute it. It reads calculate_growth()'s output; it does not call
calculate_growth() itself (callers own that -- matching this codebase's
established "callers own orchestration/I/O, this module owns
classification" convention, identical to metric_applicability.py's own
"callers own I/O" convention).

Explicitly out of scope this round (per the 2026-09-28 confirmation)
-----------------------------------------------------------------------
    - quality.py is untouched and not wrapped by this module. Its own
      per-metric MISSING means "the current-period derived value itself is
      missing or non-numeric" -- a single-period data-completeness issue,
      not a multi-period historical-comparison issue. QUALITY_METRICS are
      single-period ratios with no YoY/CAGR structure, so
      "INSUFFICIENT_HISTORY" is not a meaningful concept for them in this
      round (2026-09-28 confirmation, section 7: "두 로직을 합치거나 서로의
      상태를 덮어쓰지 마세요"). Extending Data Sufficiency semantics to
      quality.py, if ever needed, is a separate future design round.
    - growth.py, quality.py, derived_metrics.py, sec_history.py,
      point_in_time.py, orchestrator.py are not modified by this module or
      this implementation round.
    - This module is not called from anywhere yet -- exactly like
      metric_applicability.py's resolve_metric_applicability() /
      evaluate_modifiers(), it is built and tested but unwired. Wiring
      Data Sufficiency into the production pipeline is a later step in the
      confirmed 9-step implementation order; completing this module must
      never be mistaken for Production (10C) activation.

Status vocabulary (deliberately separate from metric_applicability.py's
APPLICABILITY_STATUSES -- these are two different questions and must never
share one vocabulary or be compared to each other):
    calculation_status: OK / MISSING / INVALID
    reason_code: INSUFFICIENT_HISTORY (only ever paired with MISSING when
        the underlying concept IS known to exist), CONCEPT_NOT_FOUND
        (added 2026-09-29, production-path verification round -- only
        ever paired with MISSING when the caller explicitly passed
        concept_found=False; see classify_component_sufficiency()'s
        docstring), or None (OK and INVALID never carry a reason_code --
        INVALID's own explanation lives in "detail", carried through
        unchanged from growth.py's own "reason" field, never promoted to
        a reason_code of its own, per the "no new economic policy" / "no
        new reason codes beyond what's needed" constraint).

2026-09-29 addition -- CONCEPT_NOT_FOUND
-----------------------------------------
The 2026-09-28 concept_found parameter originally left the
"concept never found at all" case with reason_code=None, distinguished
from INSUFFICIENT_HISTORY only via the free-text "detail" field. The
2026-09-29 production-path verification round's explicit requirement
("핵심 요구사항은 concept_not_found와 insufficient_history를 명확하게
구분하는 것") is better served by a second, equally structured
reason_code on the SAME axis a caller already filters on, rather than
asking every caller to also parse "detail" text to tell the two MISSING
causes apart. This is the one new reason_code this round adds -- not a
new calculation_status, not a new status vocabulary, and not a policy
change to any Applicability/Modifier reference data. It reuses the
already-established REASON_CODES allow-list convention (mirrors
metric_applicability.py's RESOLVER_IDS / POLICY_VERSIONS precedent):
growing this set is a deliberate, reviewed decision, never something a
caller can do by just inventing a new string.
"""

from __future__ import annotations

from typing import Any

from src.fundamentals.growth import GROWTH_METRICS

DATA_SUFFICIENCY_STATUSES = frozenset({"OK", "MISSING", "INVALID"})

# 2026-09-28 confirmation added INSUFFICIENT_HISTORY; 2026-09-29
# production-path verification round added CONCEPT_NOT_FOUND (see module
# docstring). Growing this set further is a policy decision, not
# something a caller can do by just inventing a new string (mirrors
# metric_applicability.py's RESOLVER_IDS / POLICY_VERSIONS precedent).
REASON_CODES = frozenset({"INSUFFICIENT_HISTORY", "CONCEPT_NOT_FOUND"})

# 2026-09-28 confirmation: INSUFFICIENT_HISTORY is a meaningful concept
# only for the 5 GROWTH_METRICS (year-over-year / multi-year comparisons
# that structurally require a second, older PIT-gated observation).
# QUALITY_METRICS are deliberately NOT included -- see module docstring,
# "Explicitly out of scope this round".
HISTORY_DEPENDENT_METRICS = frozenset(GROWTH_METRICS)


def classify_component_sufficiency(
    component: dict[str, Any],
    *,
    concept_found: bool | None = None,
) -> dict[str, Any]:
    """
    Translate one already-computed growth.py component result (as found at
    calculate_growth()["components"][metric_name], produced internally by
    _calculate_history_yoy() / _calculate_revenue_cagr()) into the Step 5B
    Data Sufficiency vocabulary.

    Performs NO new observation-selection or PIT-gating logic of its own --
    see module docstring. This is a pure, read-only relabeling of a result
    growth.py already computed.

    Mapping (2026-09-28 confirmation, refined 2026-09-29 -- see
    concept_found below):
        status="OK"      -> calculation_status="OK",      reason_code=None
        status="MISSING" -> calculation_status="MISSING", reason_code="INSUFFICIENT_HISTORY"
                             (unless concept_found is False -- see below,
                             in which case reason_code="CONCEPT_NOT_FOUND")
        status="INVALID" -> calculation_status="INVALID", reason_code=None

    concept_found (2026-09-29 verification round finding): growth.py's
    MISSING status, by itself, does NOT distinguish two structurally
    different situations that both end up as an empty quarterly/annual
    records list by the time growth.py sees them:
        (a) the underlying SEC concept genuinely exists for this company,
            but not enough PIT-gated history has accumulated yet (the
            SNDK spin-off case -- this IS "insufficient history");
        (b) the underlying SEC concept was never found for this company
            at all (point_in_time.get_point_in_time_history() returned
            None) -- a concept-coverage / normalization-selection gap,
            NOT a temporal-depth gap. Relabeling this as
            INSUFFICIENT_HISTORY would misrepresent a data-availability
            problem as a "just wait for more history" problem.
        growth.py itself cannot make this distinction (its
        _valid_history_records()/_extract_history_records() never
        inspect anything upstream of the records list it was handed) and
        this module does not duplicate its logic to derive it either
        (see module docstring). Instead, a caller that already knows the
        answer -- because IT called
        point_in_time.get_point_in_time_history() and observed None vs a
        non-None (possibly empty) structure for this metric's concept(s)
        -- may pass that knowledge in explicitly:
            concept_found=True/None (default): unchanged behavior, MISSING
                -> INSUFFICIENT_HISTORY (correct whenever the concept is
                known to exist, and is also this function's honest
                default when the caller doesn't have -- or doesn't pass --
                that information, matching the 2026-09-28 confirmation's
                literal MISSING -> INSUFFICIENT_HISTORY mapping).
            concept_found=False: MISSING is classified with
                reason_code="CONCEPT_NOT_FOUND" instead (never
                INSUFFICIENT_HISTORY) -- added 2026-09-29 (previously
                reason_code=None; a caller could only tell the two
                MISSING causes apart by parsing "detail" text, which the
                2026-09-29 production-path verification round flagged as
                an inadequate way to satisfy "clearly distinguish
                concept_not_found from insufficient_history" -- see
                module docstring). "detail" still carries the
                human-readable explanation. Still
                calculation_status="MISSING" (this module invents no new
                calculation_status, and adds exactly the one new
                reason_code documented in REASON_CODES -- not an
                open-ended vocabulary).
        This parameter changes no OTHER default behavior; every
        2026-09-28 acceptance test (including the SNDK integration test)
        exercises the unchanged default path (concept_found
        unspecified/True).

    Returns:
        {
            "metric": metric_name,
            "calculation_status": one of DATA_SUFFICIENCY_STATUSES,
            "reason_code": one of REASON_CODES, or None,
            "detail": growth.py's own free-text reason (MISSING/INVALID),
                or an explanatory string for the concept_found=False case,
                or None for OK,
        }

    Raises:
        ValueError if `component` is not a dict, or if component["metric"]
        is not one of HISTORY_DEPENDENT_METRICS -- this function is
        deliberately scoped to the 5 GROWTH_METRICS only (see
        HISTORY_DEPENDENT_METRICS docstring above), never a generic
        classifier for arbitrary metric results.
    """
    if not isinstance(component, dict):
        raise ValueError("component must be a dict.")

    metric_name = component.get("metric")

    if metric_name not in HISTORY_DEPENDENT_METRICS:
        raise ValueError(
            "classify_component_sufficiency() is scoped to the 5 "
            "history-dependent GROWTH_METRICS only "
            f"(see HISTORY_DEPENDENT_METRICS); got metric={metric_name!r}."
        )

    status = component.get("status")

    if status == "OK":
        return {
            "metric": metric_name,
            "calculation_status": "OK",
            "reason_code": None,
            "detail": None,
        }

    if status == "MISSING":
        if concept_found is False:
            return {
                "metric": metric_name,
                "calculation_status": "MISSING",
                "reason_code": "CONCEPT_NOT_FOUND",
                "detail": (
                    "Underlying SEC concept was not found for this company "
                    "at all (point_in_time.get_point_in_time_history() "
                    "returned None) -- a concept-coverage gap, not an "
                    "insufficient-history gap. Not classified as "
                    "INSUFFICIENT_HISTORY."
                ),
            }
        return {
            "metric": metric_name,
            "calculation_status": "MISSING",
            "reason_code": "INSUFFICIENT_HISTORY",
            "detail": component.get("reason"),
        }

    if status == "INVALID":
        return {
            "metric": metric_name,
            "calculation_status": "INVALID",
            "reason_code": None,
            "detail": component.get("reason"),
        }

    # Defensive fallback only -- growth.py's own documented contract never
    # produces any status outside OK/MISSING/INVALID for these metrics.
    # Failing closed to INVALID (never to OK, never inventing a new
    # reason_code) rather than silently passing an unrecognized status
    # through.
    return {
        "metric": metric_name,
        "calculation_status": "INVALID",
        "reason_code": None,
        "detail": f"Unrecognized growth component status: {status!r}.",
    }


def classify_growth_data_sufficiency(
    growth_result: dict[str, Any],
    *,
    concept_found_by_metric: dict[str, bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Apply classify_component_sufficiency() to every HISTORY_DEPENDENT_METRICS
    component inside one calculate_growth() result.

    growth_result: the full, unmodified return value of
    src.fundamentals.growth.calculate_growth(). This function only reads
    growth_result["components"]; it never calls calculate_growth() itself
    (callers own that -- see module docstring).

    concept_found_by_metric (2026-09-29 verification round, optional):
    {metric_name: bool}, passed straight through as
    classify_component_sufficiency()'s concept_found for that metric (see
    its docstring for the concept-coverage-gap-vs-history-gap
    distinction). A metric absent from this dict, or the whole dict
    omitted, uses that function's default (unchanged MISSING ->
    INSUFFICIENT_HISTORY behavior). The caller decides per metric --
    e.g. for fcf_yoy, which depends on BOTH cfo and capex concepts, the
    caller's own definition of "found" for that composite metric (this
    module does not define one).

    Returns:
        {metric_name: classify_component_sufficiency(...) result, ...}
        for each of the 5 GROWTH_METRICS found in growth_result["components"].
    """
    if not isinstance(growth_result, dict):
        raise ValueError("growth_result must be a dict.")

    components = growth_result.get("components")

    if not isinstance(components, dict):
        raise ValueError("growth_result['components'] must be a dict.")

    concept_found_by_metric = concept_found_by_metric or {}

    return {
        metric_name: classify_component_sufficiency(
            component,
            concept_found=concept_found_by_metric.get(metric_name),
        )
        for metric_name, component in components.items()
        if metric_name in HISTORY_DEPENDENT_METRICS
    }


__all__ = [
    "DATA_SUFFICIENCY_STATUSES",
    "REASON_CODES",
    "HISTORY_DEPENDENT_METRICS",
    "classify_component_sufficiency",
    "classify_growth_data_sufficiency",
]
