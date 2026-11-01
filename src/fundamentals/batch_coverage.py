"""
Single-run batch coverage and data-quality report (STEP 23-B1, Decision
Log DEC-056; design claude/2026-11-01-step23-coverage-monitoring-design.md).

Pure functions only: build_coverage() aggregates the in-memory batch records
and pipeline results that batch_runner.run_batch() already holds; nothing is
re-read from written JSON or Markdown, and nothing is written here.

Rules (owner-relayed STEP 23-B1 authorization):
- Execution status and data status are separate. Counts are primary.
- R1 "Data Acquisition Rate" and R2 "Runtime Non-Withheld OK Rate" are
  computed per normalized metric only, each with numerator, denominator,
  percentage and calculation_status; a zero denominator is NOT_COMPUTABLE.
  No thresholds or PASS/FAIL judgements.
- For a governance-WITHHELD metric the data axis uses the unmasked original
  status from stages["eligibility_gate"]["unmasked_originals"]. No metric
  value (masked or not) is ever copied into the report.
- Governance status is reported only for the six DEC-055-governed metrics;
  other metrics are labelled NOT_GOVERNED_BY_DEC055 (a report label, not a
  production status).
- Failure analysis uses structured fields only (reason_code, error_type,
  lookup_status, stage statuses). Free-text reason/error strings are never
  copied; a missing reason_code is counted as NO_REASON_CODE.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

COVERAGE_SCHEMA_VERSION = "fundamentals_coverage.v1"

# The 12 metrics sec_normalizer.normalize_company() returns.
NORMALIZED_METRICS = (
    "revenue",
    "net_income",
    "diluted_eps",
    "operating_income",
    "cfo",
    "capex",
    "cash",
    "current_debt",
    "noncurrent_debt",
    "total_debt",
    "shares_outstanding",
    "stockholders_equity",
)

# DEC-055 governance scope (metric_eligibility_registry.GOVERNED_METRICS).
GOVERNED_METRICS = (
    "operating_income",
    "cfo",
    "capex",
    "current_debt",
    "noncurrent_debt",
    "total_debt",
)

# sec_normalizer.normalize_company(): for company_type FINANCIAL these six
# branches are built with build_not_applicable_metric() / NOT_APPLICABLE
# total debt; every other metric is attempted for both company types. Used
# only to decide applicability for tickers whose pipeline run ended in ERROR.
NOT_APPLICABLE_BY_COMPANY_TYPE = {
    "FINANCIAL": frozenset(GOVERNED_METRICS),
    "NON_FINANCIAL": frozenset(),
}

KEY_ABSENT = "KEY_ABSENT"
NO_REASON_CODE = "NO_REASON_CODE"
NOT_GOVERNED = "NOT_GOVERNED_BY_DEC055"
GATE_NOT_APPLIED = "GATE_NOT_APPLIED"
NOT_COMPUTABLE = "NOT_COMPUTABLE"
COMPUTED = "COMPUTED"
APPLICABILITY_UNKNOWN = "APPLICABILITY_UNKNOWN"

EXEC_OK = "OK"
EXEC_ERROR = "ERROR"
EXEC_EXCLUDED_COMPANY_TYPE = "EXCLUDED_COMPANY_TYPE"
EXEC_EXCLUDED_NOT_IN_WATCHLIST = "EXCLUDED_NOT_IN_WATCHLIST"


def _inc(counter: dict, key: Any) -> None:
    counter[key] = counter.get(key, 0) + 1


def _ratio(numerator: int, denominator: int) -> dict:
    if denominator == 0:
        return {"numerator": numerator, "denominator": 0, "percentage": None, "calculation_status": NOT_COMPUTABLE}
    return {
        "numerator": numerator,
        "denominator": denominator,
        "percentage": round(100.0 * numerator / denominator, 1),
        "calculation_status": COMPUTED,
    }


def _stages(result: dict | None) -> dict:
    stages = (result or {}).get("stages") if isinstance(result, dict) else None
    return stages if isinstance(stages, dict) else {}


def _metrics(result: dict | None) -> dict | None:
    normalized = (_stages(result).get("normalization") or {}).get("normalized")
    metrics = normalized.get("metrics") if isinstance(normalized, dict) else None
    return metrics if isinstance(metrics, dict) else None


def _data_view(result: dict | None, metric: str) -> dict | None:
    """The metric's data-axis dict: the unmasked original if the gate withheld it."""
    metrics = _metrics(result)
    if metrics is None or metric not in metrics:
        return None
    current = metrics.get(metric)
    if isinstance(current, dict) and current.get("status") == "WITHHELD":
        gate = _stages(result).get("eligibility_gate") or {}
        originals = gate.get("unmasked_originals") or {}
        original = originals.get(metric)
        return original if isinstance(original, dict) else {"status": KEY_ABSENT}
    return current if isinstance(current, dict) else {"status": KEY_ABSENT}


def _governance_status(result: dict | None, metric: str) -> str:
    if metric not in GOVERNED_METRICS:
        return NOT_GOVERNED
    gate = _stages(result).get("eligibility_gate") or {}
    if gate.get("status") != "OK":
        return GATE_NOT_APPLIED
    if metric in (gate.get("withheld") or []):
        return "WITHHELD"
    if metric in (gate.get("not_yet_available") or []):
        return "NOT_YET_AVAILABLE"
    if metric in (gate.get("no_record") or []):
        return "NO_RECORD"
    return "ELIGIBLE"


def _use_scope(result: dict | None, metric: str) -> str | None:
    current = (_metrics(result) or {}).get(metric)
    if isinstance(current, dict):
        eligibility = current.get("eligibility")
        if isinstance(eligibility, dict) and eligibility.get("governance_status") == "ELIGIBLE":
            return eligibility.get("use_scope")
    return None


def _status_block(entries: dict) -> dict:
    """{name: {status: count}} for a dict of status-carrying outputs."""
    out: dict[str, dict] = {}
    for name, item in entries.items():
        block = out.setdefault(name, {"by_status": {}, "by_reason_code": {}})
        status = item.get("status") if isinstance(item, dict) else None
        _inc(block["by_status"], status if status is not None else KEY_ABSENT)
        if status not in ("OK", None):
            code = item.get("reason_code") if isinstance(item, dict) else None
            _inc(block["by_reason_code"], code or NO_REASON_CODE)
    return out


def _merge_blocks(target: dict, block: dict) -> None:
    for name, part in block.items():
        t = target.setdefault(name, {"by_status": {}, "by_reason_code": {}})
        for key in ("by_status", "by_reason_code"):
            for k, v in part[key].items():
                t[key][k] = t[key].get(k, 0) + v


def build_coverage(
    *,
    records: list[dict],
    results: dict[str, dict],
    evaluation_date: str,
    inputs: dict | None = None,
    requested_not_in_watchlist: list[str] | None = None,
    batch_schema_version: str | None = None,
) -> dict:
    """
    records: run_batch() records (one per batch-universe ticker considered).
    results: {ticker: pipeline result} for records with execution status OK.
    """
    inputs = dict(inputs or {})
    by_execution: dict[str, int] = {}
    company_type_lookup: dict[str, int] = {}
    membership_lookup: dict[str, int] = {}
    errors_by_type: dict[str, int] = {}
    raw_source: dict[str, int] = {}
    stage_status: dict[str, dict] = {}
    tickers_by_execution: dict[str, list[str]] = {}

    evaluated = []  # (ticker, company_type, result or None) for PIT members that passed Company Type
    for rec in records:
        status = rec.get("status")
        ticker = rec.get("ticker")
        _inc(by_execution, status)
        tickers_by_execution.setdefault(status, []).append(ticker)
        if status == EXEC_EXCLUDED_NOT_IN_WATCHLIST:
            _inc(membership_lookup, rec.get("watchlist_lookup_status") or NO_REASON_CODE)
            continue
        if "company_type_lookup_status" in rec:
            _inc(company_type_lookup, rec.get("company_type_lookup_status"))
        if status == EXEC_EXCLUDED_COMPANY_TYPE:
            continue
        if status == EXEC_ERROR:
            _inc(errors_by_type, rec.get("error_type") or NO_REASON_CODE)
            evaluated.append((ticker, rec.get("company_type"), None))
            continue
        if status == EXEC_OK:
            _inc(raw_source, rec.get("raw_source"))
            result = results.get(ticker)
            for stage_name, stage in _stages(result).items():
                st = stage.get("status") if isinstance(stage, dict) else None
                _inc(stage_status.setdefault(stage_name, {}), st)
            evaluated.append((ticker, rec.get("company_type"), result))

    normalized: dict[str, dict] = {}
    for metric in NORMALIZED_METRICS:
        data_counts: dict[str, int] = {}
        incomplete_codes: dict[str, int] = {}
        governance: dict[str, int] = {}
        scope: dict[str, int] = {}
        tickers: dict[str, list[str]] = {}
        r1_num = r1_den = r2_num = r2_den = 0
        r2_num_no_record = r2_num_not_yet_available = r2_num_gate_not_applied = 0
        unknown_applicability = 0
        for ticker, company_type, result in evaluated:
            if result is None:  # pipeline ERROR: not evaluated
                not_applicable = NOT_APPLICABLE_BY_COMPANY_TYPE.get(company_type)
                if not_applicable is None:
                    unknown_applicability += 1
                    continue
                if metric in not_applicable:
                    continue
                r2_den += 1
                _inc(data_counts, "NOT_EVALUATED_ERROR")
                tickers.setdefault("NOT_EVALUATED_ERROR", []).append(ticker)
                continue
            view = _data_view(result, metric)
            status = (view or {}).get("status") if view is not None else KEY_ABSENT
            _inc(data_counts, status)
            tickers.setdefault(status, []).append(ticker)
            if status == "INCOMPLETE":
                _inc(incomplete_codes, view.get("reason_code") or NO_REASON_CODE)
            gov = _governance_status(result, metric)
            if metric in GOVERNED_METRICS:
                _inc(governance, gov)
                sc = _use_scope(result, metric)
                if sc:
                    _inc(scope, sc)
            if status == "NOT_APPLICABLE":
                continue
            r1_den += 1
            r2_den += 1
            if status == "OK":
                r1_num += 1
                if gov != "WITHHELD":
                    r2_num += 1
                    if gov == "NO_RECORD":
                        r2_num_no_record += 1
                    elif gov == "NOT_YET_AVAILABLE":
                        r2_num_not_yet_available += 1
                    elif gov == GATE_NOT_APPLIED:
                        r2_num_gate_not_applied += 1
        r2 = _ratio(r2_num, r2_den)
        if unknown_applicability:
            r2 = {**r2, "percentage": None, "calculation_status": NOT_COMPUTABLE,
                  "not_computable_reason": f"{APPLICABILITY_UNKNOWN} for {unknown_applicability} ERROR ticker(s)"}
        r2["numerator_breakdown"] = {
            "governance_eligible": r2_num - r2_num_no_record - r2_num_not_yet_available - r2_num_gate_not_applied,
            "no_record": r2_num_no_record,
            "not_yet_available": r2_num_not_yet_available,
            "gate_not_applied": r2_num_gate_not_applied,
        }
        if metric not in GOVERNED_METRICS:
            r2["numerator_breakdown"] = {NOT_GOVERNED: r2_num}
        normalized[metric] = {
            "data_status_counts": data_counts,
            "incomplete_by_reason_code": incomplete_codes,
            "governance_status_counts": governance if metric in GOVERNED_METRICS else {NOT_GOVERNED: len([e for e in evaluated if e[2] is not None])},
            "use_scope_counts": scope,
            "tickers_by_data_status": tickers,
            "r1_data_acquisition_rate": _ratio(r1_num, r1_den),
            "r2_runtime_non_withheld_ok_rate": r2,
        }

    derived: dict = {}
    growth_components: dict = {}
    growth_aggregate: dict[str, int] = {}
    withheld_components: dict[str, int] = {}
    sufficiency: dict = {}
    debt_tier2: dict = {"by_status": {}, "by_reason_code": {}}
    for ticker, _ct, result in evaluated:
        if result is None:
            continue
        stages = _stages(result)
        dm = ((stages.get("derived_metrics") or {}).get("derived") or {}).get("derived_metrics")
        if isinstance(dm, dict):
            _merge_blocks(derived, _status_block(dm))
        growth = (stages.get("growth") or {}).get("growth")
        if isinstance(growth, dict):
            comps = growth.get("components")
            if isinstance(comps, dict):
                _merge_blocks(growth_components, _status_block(comps))
            agg = growth.get("growth")
            if isinstance(agg, dict):
                _inc(growth_aggregate, agg.get("status") or KEY_ABSENT)
                for comp in agg.get("withheld_components") or []:
                    _inc(withheld_components, comp)
        suff = (stages.get("data_sufficiency") or {}).get("sufficiency")
        if isinstance(suff, dict):
            for name, item in suff.items():
                block = sufficiency.setdefault(name, {"by_calculation_status": {}, "by_reason_code": {}})
                _inc(block["by_calculation_status"], (item or {}).get("calculation_status") or KEY_ABSENT)
                if (item or {}).get("calculation_status") != "OK":
                    _inc(block["by_reason_code"], (item or {}).get("reason_code") or NO_REASON_CODE)
        tier2 = (stages.get("debt_tiers") or {}).get("total_debt_including_finance_leases")
        if isinstance(tier2, dict):
            _inc(debt_tier2["by_status"], tier2.get("status") or KEY_ABSENT)
            if tier2.get("status") != "OK":
                _inc(debt_tier2["by_reason_code"], tier2.get("reason_code") or NO_REASON_CODE)

    member_count = sum(v for k, v in by_execution.items() if k != EXEC_EXCLUDED_NOT_IN_WATCHLIST)
    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "batch_schema_version": batch_schema_version,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_date": evaluation_date,
        "inputs": inputs,
        "universe": {
            "active_watchlist_rows_considered": len(records),
            "pit_watchlist_members": member_count,
            "requested_not_in_watchlist": sorted(requested_not_in_watchlist or []),
            "by_execution_status": by_execution,
            "tickers_by_execution_status": {k: sorted(v) for k, v in tickers_by_execution.items()},
            "watchlist_lookup_status_counts": membership_lookup,
            "company_type_lookup_status_counts": company_type_lookup,
            "raw_source_counts": raw_source,
            "errors_by_type": errors_by_type,
            "evaluated_tickers": len(evaluated),
        },
        "stage_status_counts": stage_status,
        "normalized_metrics": normalized,
        "derived_metrics": derived,
        "growth": {
            "components": growth_components,
            "aggregate_status_counts": growth_aggregate,
            "withheld_component_counts": withheld_components,
        },
        "data_sufficiency": sufficiency,
        "debt_tiers": {"total_debt_including_finance_leases": debt_tier2},
        "notes": [
            "Execution OK means the pipeline returned; it does not mean metric data is OK.",
            "R1/R2 apply to normalized metrics only. No thresholds are applied.",
            "WITHHELD metrics are counted on the data axis by their unmasked original status; values are never reported.",
            "NO_RECORD / NOT_YET_AVAILABLE / GATE_NOT_APPLIED in R2's numerator are not governance approvals.",
            "Exception stage is not captured for ERROR tickers; errors are counted by error_type only.",
        ],
    }


def coverage_headline(coverage: dict) -> dict:
    """Small additive block for summary.json."""
    universe = coverage["universe"]
    return {
        "coverage_file": "coverage.json",
        "schema_version": coverage["schema_version"],
        "pit_watchlist_members": universe["pit_watchlist_members"],
        "evaluated_tickers": universe["evaluated_tickers"],
        "by_execution_status": universe["by_execution_status"],
        "requested_not_in_watchlist": universe["requested_not_in_watchlist"],
    }


def _fmt_ratio(r: dict) -> str:
    if r["calculation_status"] != COMPUTED:
        return f"{r['numerator']}/{r['denominator']} (not computable)"
    return f"{r['numerator']}/{r['denominator']} ({r['percentage']:.1f}%)"


def _fmt_counts(counts: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: str(kv[0]))) or "—"


def render_coverage_markdown(coverage: dict) -> str:
    u = coverage["universe"]
    lines = [
        f"# Batch coverage — evaluation date {coverage['evaluation_date']}",
        "",
        "Counts are primary. Execution OK ≠ data OK. No thresholds are applied.",
        "",
        "## Universe",
        "",
        f"- ACTIVE WATCHLIST rows considered: {u['active_watchlist_rows_considered']}",
        f"- PIT WATCHLIST members on {coverage['evaluation_date']}: {u['pit_watchlist_members']}",
        f"- Evaluated (passed Company Type): {u['evaluated_tickers']}",
        f"- Execution status: {_fmt_counts(u['by_execution_status'])}",
        f"- Company Type lookup: {_fmt_counts(u['company_type_lookup_status_counts'])}",
        f"- WATCHLIST lookup (not members): {_fmt_counts(u['watchlist_lookup_status_counts'])}",
        f"- Errors by type: {_fmt_counts(u['errors_by_type'])}",
        f"- Requested but not in WATCHLIST: {', '.join(u['requested_not_in_watchlist']) or '—'}",
        f"- Inputs: {_fmt_counts({k: v for k, v in coverage['inputs'].items()})}",
        "",
        "## Normalized metrics",
        "",
        "R1 = Data Acquisition Rate (OK by original data status / executed and applicable). "
        "R2 = Runtime Non-Withheld OK Rate (OK and not WITHHELD / PIT members past Company Type, applicable; ERROR included in denominator).",
        "",
        "| Metric | Data status | INCOMPLETE reason codes | Governance | Use scope | R1 | R2 |",
        "|---|---|---|---|---|---|---|",
    ]
    for metric, m in coverage["normalized_metrics"].items():
        lines.append(
            f"| {metric} | {_fmt_counts(m['data_status_counts'])} | {_fmt_counts(m['incomplete_by_reason_code'])} | "
            f"{_fmt_counts(m['governance_status_counts'])} | {_fmt_counts(m['use_scope_counts'])} | "
            f"{_fmt_ratio(m['r1_data_acquisition_rate'])} | {_fmt_ratio(m['r2_runtime_non_withheld_ok_rate'])} |"
        )
    lines += ["", "## Derived metrics", "", "| Metric | Status | Non-OK reason codes |", "|---|---|---|"]
    for name, b in sorted(coverage["derived_metrics"].items()):
        lines.append(f"| {name} | {_fmt_counts(b['by_status'])} | {_fmt_counts(b['by_reason_code'])} |")
    g = coverage["growth"]
    lines += ["", "## Growth", "", "| Component | Status | Non-OK reason codes |", "|---|---|---|"]
    for name, b in sorted(g["components"].items()):
        lines.append(f"| {name} | {_fmt_counts(b['by_status'])} | {_fmt_counts(b['by_reason_code'])} |")
    lines += [
        "",
        f"Aggregate: {_fmt_counts(g['aggregate_status_counts'])}; withheld components: {_fmt_counts(g['withheld_component_counts'])}",
        "",
        "## Data sufficiency",
        "",
        "| Metric | Calculation status | Non-OK reason codes |",
        "|---|---|---|",
    ]
    for name, b in sorted(coverage["data_sufficiency"].items()):
        lines.append(f"| {name} | {_fmt_counts(b['by_calculation_status'])} | {_fmt_counts(b['by_reason_code'])} |")
    t2 = coverage["debt_tiers"]["total_debt_including_finance_leases"]
    lines += [
        "",
        "## Debt tier 2 (total_debt_including_finance_leases)",
        "",
        f"Status: {_fmt_counts(t2['by_status'])}; non-OK reason codes: {_fmt_counts(t2['by_reason_code'])}",
        "",
        "## Notes",
        "",
    ]
    lines += [f"- {n}" for n in coverage["notes"]]
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "COVERAGE_SCHEMA_VERSION",
    "NORMALIZED_METRICS",
    "GOVERNED_METRICS",
    "NOT_APPLICABLE_BY_COMPANY_TYPE",
    "KEY_ABSENT",
    "NO_REASON_CODE",
    "NOT_GOVERNED",
    "GATE_NOT_APPLIED",
    "NOT_COMPUTABLE",
    "COMPUTED",
    "EXEC_EXCLUDED_NOT_IN_WATCHLIST",
    "build_coverage",
    "coverage_headline",
    "render_coverage_markdown",
]
