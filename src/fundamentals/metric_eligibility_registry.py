"""
Runtime metric eligibility gate (Decision Log DEC-055, design approved by
the owner 2026-11-01; decisions D1-D5 as recommended).

Why this module exists
----------------------
Per-metric governance decisions (B-1.C-R1 Outcomes, DEC-036 / DEC-054) were
recorded only in documents ("runtime enforcement: NOT_ENFORCED"). Derived
metrics read input *values*, not statuses (derived_metrics.calculate_fcf()
receives cfo["value"]), so a governance label alone would not stop FCF
from being computed from a withheld CFO. This module turns the decisions
into data (data/reference/metric_eligibility.csv) and masks withheld
metrics before any downstream stage reads them.

Decisions implemented
---------------------
D1  A metric with no applicable row passes through unchanged and is only
    counted as NO_RECORD (reviews are exception-triggered, not universal).
D2  A WITHHELD metric is masked: status "WITHHELD", value None. The
    original metric dict is kept in the gate's audit record.
D3  Rows are PIT-dated: a row applies only when effective_from <=
    evaluation_date < effective_to and eligibility_available_date <=
    evaluation_date (next-calendar-day KST convention, DEC-020).
D4  Derived / growth / data-sufficiency outputs that depend on a withheld
    metric are relabelled WITHHELD (never MISSING / CONCEPT_NOT_FOUND).
D5  An ELIGIBLE row with a use_scope only attaches metadata; the value is
    never changed.

Callers own file I/O (metric_eligibility_registry_io.py); this module owns
validation, lookup and masking. Invalid reference data raises ValueError
(fail closed): a malformed governance table must never silently become
"no record".
"""

from __future__ import annotations

import copy
from datetime import date, datetime
from typing import Any

from src.fundamentals.point_in_time import coerce_evaluation_date

STATUS_ELIGIBLE = "ELIGIBLE"
STATUS_WITHHELD = "WITHHELD"
GOVERNANCE_STATUSES = frozenset({STATUS_ELIGIBLE, STATUS_WITHHELD})

LOOKUP_ELIGIBLE = "ELIGIBLE"
LOOKUP_WITHHELD = "WITHHELD"
LOOKUP_NO_RECORD = "NO_RECORD"
LOOKUP_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"

# The metrics a row may govern: the six company_type-governed extraction
# branches (B-1.C scope). Extending the list is a separate decision.
GOVERNED_METRICS = (
    "operating_income",
    "cfo",
    "capex",
    "current_debt",
    "noncurrent_debt",
    "total_debt",
)

USE_SCOPES = frozenset({"CONSOLIDATED_REPORTED"})

# B-1.C-R1: Outcome 1 is eligible; Outcomes 2, 3 and 4 are withheld.
OUTCOME_STATUS = {"1": STATUS_ELIGIBLE, "2": STATUS_WITHHELD, "3": STATUS_WITHHELD, "4": STATUS_WITHHELD}

# D4: which outputs inherit a withheld input.
DERIVED_DEPENDENCIES = {
    "fcf": ("cfo", "capex"),
    "fcf_margin": ("cfo", "capex"),
    "cfo_yoy": ("cfo",),
    "capex_yoy": ("capex",),
    "operating_margin": ("operating_income",),
    "operating_income_yoy": ("operating_income",),
    "net_debt": ("total_debt",),
    "debt_to_cash": ("total_debt",),
}
GROWTH_DEPENDENCIES = {
    "fcf_yoy": ("cfo", "capex"),
    "operating_income_yoy": ("operating_income",),
}

SUFFICIENCY_REASON_WITHHELD = "GOVERNANCE_WITHHELD"
# Reason code carried by every WITHHELD dependent marker (derived / growth).
DEPENDENT_REASON_WITHHELD = "GOVERNANCE_WITHHELD"


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _cik_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    return text.lstrip("0") or None


def validate_metric_eligibility_row(row: dict) -> list[str]:
    """Field-level failures for one row (empty list = valid)."""
    failures = []
    if not isinstance(row, dict):
        return ["row"]
    if not row.get("ticker"):
        failures.append("ticker")
    if _cik_key(row.get("cik")) is None:
        failures.append("cik")
    if row.get("metric") not in GOVERNED_METRICS:
        failures.append("metric")
    outcome = str(row.get("outcome")) if row.get("outcome") is not None else None
    status = row.get("governance_status")
    if outcome not in OUTCOME_STATUS:
        failures.append("outcome")
    if status not in GOVERNANCE_STATUSES:
        failures.append("governance_status")
    elif outcome in OUTCOME_STATUS and OUTCOME_STATUS[outcome] != status:
        failures.append("outcome_status_mismatch")
    scope = row.get("use_scope")
    if scope is not None and scope not in USE_SCOPES:
        failures.append("use_scope")
    if scope is not None and status == STATUS_WITHHELD:
        failures.append("use_scope_on_withheld")
    start = _parse_date(row.get("effective_from"))
    if start is None:
        failures.append("effective_from")
    end_raw = row.get("effective_to")
    end = _parse_date(end_raw) if end_raw is not None else None
    if end_raw is not None and end is None:
        failures.append("effective_to")
    if start is not None and end is not None and end <= start:
        failures.append("effective_interval")
    if _parse_date(row.get("eligibility_available_date")) is None:
        failures.append("eligibility_available_date")
    if _parse_utc(row.get("reviewed_at_utc")) is None:
        failures.append("reviewed_at_utc")
    for field in ("policy_version", "decision_ref", "rationale"):
        if not row.get(field):
            failures.append(field)
    return failures


def _interval(row: dict) -> tuple[date, date]:
    start = _parse_date(row.get("effective_from"))
    end = _parse_date(row.get("effective_to")) if row.get("effective_to") is not None else date.max
    return start, end


def validate_metric_eligibility_reference_data(rows: list[dict]) -> dict[str, Any]:
    """Row failures by index, plus overlapping intervals for the same (cik, metric)."""
    row_failures = {}
    for i, row in enumerate(rows):
        failed = validate_metric_eligibility_row(row)
        if failed:
            row_failures[i] = failed
    overlaps = []
    valid = [(i, row) for i, row in enumerate(rows) if i not in row_failures]
    for a in range(len(valid)):
        for b in range(a + 1, len(valid)):
            ia, ra = valid[a]
            ib, rb = valid[b]
            if (_cik_key(ra.get("cik")), ra.get("metric")) != (_cik_key(rb.get("cik")), rb.get("metric")):
                continue
            sa, ea = _interval(ra)
            sb, eb = _interval(rb)
            if sa < eb and sb < ea:
                overlaps.append((ia, ib))
    return {"row_failures": row_failures, "overlaps": overlaps}


def assert_valid_metric_eligibility_reference_data(rows: list[dict]) -> None:
    report = validate_metric_eligibility_reference_data(rows)
    if report["row_failures"] or report["overlaps"]:
        raise ValueError(f"Invalid metric eligibility reference data: {report}")


def get_metric_eligibility_as_of(cik: Any, metric: str, evaluation_date: Any, rows: list[dict]) -> dict:
    """PIT lookup for one (cik, metric). Rows must already be validated."""
    eval_date = coerce_evaluation_date(evaluation_date)
    key = _cik_key(cik)
    active = []
    pending = []
    for row in rows:
        if _cik_key(row.get("cik")) != key or row.get("metric") != metric:
            continue
        start, end = _interval(row)
        if not (start <= eval_date < end):
            continue
        if _parse_date(row.get("eligibility_available_date")) > eval_date:
            pending.append(row)
        else:
            active.append(row)
    if active:
        row = active[0]
        status = LOOKUP_WITHHELD if row["governance_status"] == STATUS_WITHHELD else LOOKUP_ELIGIBLE
        return {"lookup_status": status, "row": row}
    if pending:
        return {"lookup_status": LOOKUP_NOT_YET_AVAILABLE, "row": pending[0]}
    return {"lookup_status": LOOKUP_NO_RECORD, "row": None}


def _governance_ref(row: dict) -> dict:
    return {
        "outcome": str(row.get("outcome")),
        "decision_ref": row.get("decision_ref"),
        "policy_version": row.get("policy_version"),
    }


def apply_metric_eligibility_gate(normalized: dict, cik: Any, evaluation_date: Any, rows: list[dict]) -> dict:
    """
    Mask WITHHELD metrics in normalized["metrics"] in place (D2) and tag
    scoped ELIGIBLE metrics (D5). Returns the gate record for
    stages["eligibility_gate"], including the unmasked originals (audit).
    """
    assert_valid_metric_eligibility_reference_data(rows)
    metrics = normalized.get("metrics") if isinstance(normalized, dict) else None
    if not isinstance(metrics, dict):
        raise ValueError("normalized['metrics'] must be a dict.")

    withheld, scoped, no_record, pending = [], [], [], []
    originals: dict[str, Any] = {}
    for metric in GOVERNED_METRICS:
        lookup = get_metric_eligibility_as_of(cik, metric, evaluation_date, rows)
        status = lookup["lookup_status"]
        row = lookup["row"]
        if status == LOOKUP_WITHHELD:
            original = metrics.get(metric)
            originals[metric] = copy.deepcopy(original)
            unit = original.get("unit") if isinstance(original, dict) else None
            metrics[metric] = {
                "status": STATUS_WITHHELD,
                "value": None,
                "unit": unit,
                "reason": "Governance eligibility WITHHELD (metric eligibility gate).",
                "eligibility": {"governance_status": STATUS_WITHHELD, **_governance_ref(row)},
                "original_status": original.get("status") if isinstance(original, dict) else None,
            }
            withheld.append(metric)
        elif status == LOOKUP_ELIGIBLE:
            if row.get("use_scope") is not None and isinstance(metrics.get(metric), dict):
                metrics[metric]["eligibility"] = {
                    "governance_status": STATUS_ELIGIBLE,
                    "use_scope": row["use_scope"],
                    **_governance_ref(row),
                }
                scoped.append(metric)
        elif status == LOOKUP_NOT_YET_AVAILABLE:
            pending.append(metric)
        else:
            no_record.append(metric)

    return {
        "status": "OK",
        "withheld": withheld,
        "scoped": scoped,
        "not_yet_available": pending,
        "no_record": no_record,
        "unmasked_originals": originals,
    }


def _withheld_marker(previous: Any, inputs: list[str], *, materialized: bool = True) -> dict:
    """WITHHELD marker for a dependent output. ``materialized`` is a display
    field, not a status: True when the stage had produced the output (now
    masked), False when it was never produced because its governance-
    withheld input was masked upstream (e.g. no history is extracted for a
    masked metric, so cfo_yoy is never computed). Governance WITHHELD takes
    precedence over "not produced": the output is listed either way, so a
    consumer can see why it has no value."""
    return {
        "status": STATUS_WITHHELD,
        "value": None,
        "reason_code": DEPENDENT_REASON_WITHHELD,
        "withheld_inputs": inputs,
        "materialized": materialized,
        "original_status": previous.get("status") if isinstance(previous, dict) else None,
        "reason": "Depends on governance-withheld input(s): " + ", ".join(inputs) + "."
        + ("" if materialized else " Not produced: the input was withheld upstream."),
    }


def relabel_withheld_dependents(
    withheld: list[str],
    derived: dict | None,
    growth: dict | None,
    sufficiency: dict | None,
) -> dict:
    """D4: relabel outputs that depend on withheld metrics. In place; returns what changed.

    STEP 23 (2026-11-03): every dependency-map entry with a withheld input is
    listed as WITHHELD, including outputs the stage did not produce
    (materialized=False). Only entries of the dependency maps are added, only
    into a stage that ran (its container dict exists), and always with value
    None. R1/R2 (normalized metrics) are not affected."""
    withheld_set = set(withheld)
    changed: dict[str, list[str]] = {"derived": [], "growth": [], "data_sufficiency": []}
    if not withheld_set:
        return changed

    derived_metrics = derived.get("derived_metrics") if isinstance(derived, dict) else None
    if isinstance(derived_metrics, dict):
        for name, deps in DERIVED_DEPENDENCIES.items():
            inputs = [d for d in deps if d in withheld_set]
            if inputs:
                present = name in derived_metrics
                derived_metrics[name] = _withheld_marker(derived_metrics.get(name), inputs, materialized=present)
                changed["derived"].append(name)

    components = growth.get("components") if isinstance(growth, dict) else None
    growth_inputs: dict[str, list[str]] = {}
    if isinstance(components, dict):
        for name, deps in GROWTH_DEPENDENCIES.items():
            inputs = [d for d in deps if d in withheld_set]
            if inputs:
                present = name in components
                components[name] = _withheld_marker(components.get(name), inputs, materialized=present)
                growth_inputs[name] = inputs
                changed["growth"].append(name)
        if growth_inputs and isinstance(growth.get("growth"), dict):
            growth["growth"]["withheld_components"] = sorted(growth_inputs)

    if isinstance(sufficiency, dict):
        for name, deps in GROWTH_DEPENDENCIES.items():
            inputs = [d for d in deps if d in withheld_set]
            if inputs:
                present = name in sufficiency
                sufficiency[name] = {
                    "metric": name,
                    "calculation_status": STATUS_WITHHELD,
                    "reason_code": SUFFICIENCY_REASON_WITHHELD,
                    "materialized": present,
                    "detail": "Depends on governance-withheld input(s): " + ", ".join(inputs) + ".",
                }
                changed["data_sufficiency"].append(name)
    return changed


__all__ = [
    "STATUS_ELIGIBLE",
    "STATUS_WITHHELD",
    "LOOKUP_ELIGIBLE",
    "LOOKUP_WITHHELD",
    "LOOKUP_NO_RECORD",
    "LOOKUP_NOT_YET_AVAILABLE",
    "GOVERNED_METRICS",
    "USE_SCOPES",
    "DEPENDENT_REASON_WITHHELD",
    "DERIVED_DEPENDENCIES",
    "GROWTH_DEPENDENCIES",
    "SUFFICIENCY_REASON_WITHHELD",
    "validate_metric_eligibility_row",
    "validate_metric_eligibility_reference_data",
    "assert_valid_metric_eligibility_reference_data",
    "get_metric_eligibility_as_of",
    "apply_metric_eligibility_gate",
    "relabel_withheld_dependents",
]
