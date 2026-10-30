"""
Filing-specific finance-lease evidence and the tier-2 debt metric.

Decision Log DEC-045 (2026-10-30), approving the consensus proposal
claude/2026-10-30-lease-scope-consensus-proposal.md:

* ``total_debt`` (DEC-036 / DEC-038) is unchanged. It measures borrowings as
  presented, including finance leases only where the issuer presents them
  inside borrowings.
* ``total_debt_including_finance_leases`` (this module) adds finance-lease
  liabilities that filing evidence places OUTSIDE ``total_debt``:

      tier2 = total_debt + finance-lease parts recorded as EXCLUDED

  It is computed for one filing (accession), one period end and one unit,
  and only from a reviewed evidence row for exactly that filing. Evidence is
  never carried forward to a later filing (D3). Equal amounts are never
  evidence of placement; an evidence row records placement explicitly.
* D1 -- a finance-lease liability of zero is used only when the row says
  VERIFIED_NONE with the NO_FINANCE_LEASE_D1 basis (a lease note without
  finance leases AND a debt schedule without a lease row that reconciles to
  the face debt lines).
* D2 -- EXCLUDED placement is accepted on the basis
  LEASE_NOTE_CAPTION_MAPPING_AND_DEBT_RECONCILIATION (the issuer's XBRL
  balance-sheet caption mapping points to a non-debt line AND the debt
  schedule reconciles to the face debt lines without a lease row).
* D4 -- when evidence places finance leases outside ``total_debt``,
  ``finance_lease_flag_for_total_debt()`` returns metadata to attach to
  ``total_debt``; its value and status never change.

Like company_type_registry.py, this module does no file I/O; see
finance_lease_evidence_registry_io.py.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from src.fundamentals.point_in_time import coerce_evaluation_date

POLICY_VERSIONS = frozenset({"LEASE_TIER2_V1"})

STATUS_PRESENT = "PRESENT"
STATUS_VERIFIED_NONE = "VERIFIED_NONE"
STATUS_UNKNOWN = "UNKNOWN"
FINANCE_LEASE_STATUSES = frozenset({STATUS_PRESENT, STATUS_VERIFIED_NONE, STATUS_UNKNOWN})

PLACEMENT_INCLUDED = "INCLUDED"
PLACEMENT_EXCLUDED = "EXCLUDED"
PLACEMENT_UNKNOWN = "UNKNOWN"
PLACEMENTS = frozenset({PLACEMENT_INCLUDED, PLACEMENT_EXCLUDED, PLACEMENT_UNKNOWN})

BASIS_CAPTION_MAPPING = "LEASE_NOTE_CAPTION_MAPPING_AND_DEBT_RECONCILIATION"
BASIS_DEBT_TABLE_ROW = "DEBT_TABLE_FINANCE_LEASE_ROW"
BASIS_NO_FINANCE_LEASE = "NO_FINANCE_LEASE_D1"
BASIS_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
EVIDENCE_BASES = frozenset({
    BASIS_CAPTION_MAPPING,
    BASIS_DEBT_TABLE_ROW,
    BASIS_NO_FINANCE_LEASE,
    BASIS_INSUFFICIENT,
})

LOOKUP_OK = "OK"
LOOKUP_NO_EVIDENCE = "NO_EVIDENCE"
LOOKUP_NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
LOOKUP_DUPLICATE_EVIDENCE = "DUPLICATE_EVIDENCE"
LOOKUP_INVALID_REFERENCE = "INVALID_REFERENCE"

TIER2_METRIC = "total_debt_including_finance_leases"

_REQUIRED = (
    "ticker",
    "cik",
    "accession",
    "form",
    "filing_date",
    "period_end",
    "unit",
    "finance_lease_status",
    "evidence_basis",
    "source_urls",
    "evidence_rationale",
    "evidence_available_date",
    "reviewed_at_utc",
    "policy_version",
    "decision_ref",
)
_AMOUNTS = (
    "finance_lease_liability_current",
    "finance_lease_liability_noncurrent",
    "finance_lease_liability_total",
)


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


def _parse_amount(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _cik_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    return text.lstrip("0") or None


def validate_finance_lease_evidence_row(row: dict) -> list[str]:
    """Return the names of failed checks (empty list = valid)."""
    failures: list[str] = []
    for field in _REQUIRED:
        if not row.get(field):
            failures.append(field)
    if _cik_key(row.get("cik")) is None and "cik" not in failures:
        failures.append("cik")
    for field in ("filing_date", "period_end", "evidence_available_date"):
        if row.get(field) and _parse_date(row.get(field)) is None:
            failures.append(field)
    reviewed = _parse_utc(row.get("reviewed_at_utc"))
    if row.get("reviewed_at_utc") and reviewed is None:
        failures.append("reviewed_at_utc")
    available = _parse_date(row.get("evidence_available_date"))
    if reviewed is not None and available is not None and available <= reviewed.date():
        # PIT: usable only from the calendar day after review (DEC-020 style).
        failures.append("evidence_available_date")
    if row.get("policy_version") and row.get("policy_version") not in POLICY_VERSIONS:
        failures.append("policy_version")
    if row.get("evidence_basis") and row.get("evidence_basis") not in EVIDENCE_BASES:
        failures.append("evidence_basis")

    status = row.get("finance_lease_status")
    if status and status not in FINANCE_LEASE_STATUSES:
        failures.append("finance_lease_status")
        return failures

    amounts = {f: row.get(f) for f in _AMOUNTS}
    placements = {f: row.get(f) for f in ("current_in_total_debt", "noncurrent_in_total_debt")}

    if status == STATUS_PRESENT:
        parsed = {f: _parse_amount(v) for f, v in amounts.items()}
        for f, v in parsed.items():
            if v is None:
                failures.append(f)
        if None not in parsed.values() and (
            parsed["finance_lease_liability_current"] + parsed["finance_lease_liability_noncurrent"]
            != parsed["finance_lease_liability_total"]
        ):
            failures.append("finance_lease_liability_total")
        for f, v in placements.items():
            if v not in PLACEMENTS:
                failures.append(f)
        for part, loc in (("current_in_total_debt", "current_location"),
                          ("noncurrent_in_total_debt", "noncurrent_location")):
            if placements[part] in (PLACEMENT_INCLUDED, PLACEMENT_EXCLUDED) and not row.get(loc):
                failures.append(loc)
        placed = set(placements.values())
        if PLACEMENT_EXCLUDED in placed and row.get("evidence_basis") != BASIS_CAPTION_MAPPING:
            failures.append("evidence_basis")
        if placed <= {PLACEMENT_INCLUDED} and row.get("evidence_basis") not in (
            BASIS_DEBT_TABLE_ROW, BASIS_CAPTION_MAPPING
        ):
            failures.append("evidence_basis")
    elif status == STATUS_VERIFIED_NONE:
        if row.get("evidence_basis") != BASIS_NO_FINANCE_LEASE:
            failures.append("evidence_basis")
        for f, v in {**amounts, **placements}.items():
            if v:
                failures.append(f)
    elif status == STATUS_UNKNOWN:
        if row.get("evidence_basis") != BASIS_INSUFFICIENT:
            failures.append("evidence_basis")
    return sorted(set(failures))


def validate_finance_lease_evidence_reference_data(rows: list[dict]) -> dict[str, Any]:
    """Row failures by index, plus duplicate (cik, accession, period_end, unit) keys."""
    row_failures = {}
    seen: dict[tuple, int] = {}
    duplicates = []
    for i, row in enumerate(rows):
        failed = validate_finance_lease_evidence_row(row)
        if failed:
            row_failures[i] = failed
        key = (_cik_key(row.get("cik")), row.get("accession"), row.get("period_end"), row.get("unit"))
        if key in seen:
            duplicates.append((seen[key], i))
        else:
            seen[key] = i
    return {"row_failures": row_failures, "duplicates": duplicates}


def get_finance_lease_evidence(
    cik: Any, accession: str, period_end: str, unit: str, evaluation_date: Any, rows: list[dict]
) -> dict:
    """PIT lookup of the evidence row for exactly one filing, period and unit."""
    eval_date = coerce_evaluation_date(evaluation_date)
    key = _cik_key(cik)
    matches = [
        row for row in rows
        if _cik_key(row.get("cik")) == key
        and row.get("accession") == accession
        and row.get("period_end") == period_end
        and row.get("unit") == unit
    ]
    if not matches:
        return {"lookup_status": LOOKUP_NO_EVIDENCE, "row": None}
    if len(matches) > 1:
        return {"lookup_status": LOOKUP_DUPLICATE_EVIDENCE, "row": None}
    row = matches[0]
    if validate_finance_lease_evidence_row(row):
        return {"lookup_status": LOOKUP_INVALID_REFERENCE, "row": row}
    if _parse_date(row.get("evidence_available_date")) > eval_date:
        return {"lookup_status": LOOKUP_NOT_YET_AVAILABLE, "row": row}
    return {"lookup_status": LOOKUP_OK, "row": row}


def _tier1_provenance(total_debt: dict) -> tuple[str | None, str | None, str | None]:
    components = total_debt.get("components") or {}
    cur = components.get("current_debt") or {}
    non = components.get("noncurrent_debt") or {}
    keys = {(c.get("accession"), c.get("period_end"), c.get("unit")) for c in (cur, non)}
    if len(keys) != 1:
        return None, None, None
    accession, period_end, unit = keys.pop()
    if unit != total_debt.get("unit"):
        return None, None, None
    return accession, period_end, unit


def compute_total_debt_including_finance_leases(
    total_debt: dict, cik: Any, evaluation_date: Any, evidence_rows: list[dict]
) -> dict:
    """Tier-2 debt for the filing behind ``total_debt``. Fail-closed: any
    missing, mismatched or unknown evidence gives INCOMPLETE."""
    result: dict[str, Any] = {
        "metric": TIER2_METRIC,
        "status": "INCOMPLETE",
        "value": None,
        "policy_version": "LEASE_TIER2_V1",
    }

    def _incomplete(code: str, why: str, **extra) -> dict:
        result.update({"reason_code": code, "reason": why})
        result.update(extra)
        return result

    if not isinstance(total_debt, dict) or total_debt.get("status") != "OK" or not isinstance(
        total_debt.get("value"), (int, float)
    ):
        return _incomplete("TIER1_INCOMPLETE", "total_debt is not OK; tier 2 is not computed.")
    accession, period_end, unit = _tier1_provenance(total_debt)
    if not (accession and period_end and unit):
        return _incomplete(
            "TIER1_PROVENANCE_AMBIGUOUS",
            "total_debt components do not share one accession, period end and unit.",
        )
    result.update({"accession": accession, "period_end": period_end, "unit": unit})

    lookup = get_finance_lease_evidence(cik, accession, period_end, unit, evaluation_date, evidence_rows)
    status = lookup["lookup_status"]
    if status != LOOKUP_OK:
        code = {
            LOOKUP_NO_EVIDENCE: "NO_FINANCE_LEASE_EVIDENCE",
            LOOKUP_NOT_YET_AVAILABLE: "EVIDENCE_NOT_YET_AVAILABLE",
            LOOKUP_DUPLICATE_EVIDENCE: "DUPLICATE_FINANCE_LEASE_EVIDENCE",
            LOOKUP_INVALID_REFERENCE: "INVALID_FINANCE_LEASE_EVIDENCE",
        }[status]
        return _incomplete(code, f"Finance-lease evidence lookup: {status} for {accession} / {period_end} / {unit}.")
    row = lookup["row"]
    evidence_ref = {
        "accession": row["accession"],
        "evidence_basis": row["evidence_basis"],
        "source_urls": row["source_urls"],
        "decision_ref": row["decision_ref"],
    }
    tier1 = total_debt["value"]
    fl_status = row["finance_lease_status"]
    if fl_status == STATUS_UNKNOWN:
        return _incomplete("FINANCE_LEASE_SCOPE_UNKNOWN", "Evidence records finance leases as UNKNOWN.",
                           evidence=evidence_ref)
    if fl_status == STATUS_VERIFIED_NONE:
        result.update({"status": "OK", "value": tier1, "finance_lease_added": 0,
                       "finance_lease_status": STATUS_VERIFIED_NONE, "evidence": evidence_ref})
        result.pop("reason_code", None)
        return result

    parts = (
        ("current", row["current_in_total_debt"], _parse_amount(row["finance_lease_liability_current"])),
        ("noncurrent", row["noncurrent_in_total_debt"], _parse_amount(row["finance_lease_liability_noncurrent"])),
    )
    if any(placement == PLACEMENT_UNKNOWN for _, placement, _ in parts):
        return _incomplete("FINANCE_LEASE_SCOPE_UNKNOWN",
                           "Placement of a finance-lease part relative to total_debt is UNKNOWN.",
                           evidence=evidence_ref)
    added = sum(amount for _, placement, amount in parts if placement == PLACEMENT_EXCLUDED)
    result.update({
        "status": "OK",
        "value": tier1 + added,
        "total_debt": tier1,
        "finance_lease_added": added,
        "finance_lease_status": STATUS_PRESENT,
        "finance_lease_parts": [
            {"part": name, "placement": placement, "amount": amount}
            for name, placement, amount in parts
        ],
        "evidence": evidence_ref,
    })
    return result


def finance_lease_flag_for_total_debt(tier2: dict) -> dict | None:
    """D4: metadata for ``total_debt`` when evidence places finance leases
    outside it. Returns None otherwise. Never changes total_debt's value."""
    if not isinstance(tier2, dict) or tier2.get("status") != "OK":
        return None
    outside = tier2.get("finance_lease_added") or 0
    if tier2.get("finance_lease_status") != STATUS_PRESENT or outside <= 0:
        return None
    return {
        "finance_leases_outside_total_debt": True,
        "finance_lease_liabilities_outside_total_debt": outside,
        "finance_lease_evidence_accession": (tier2.get("evidence") or {}).get("accession"),
        "note": (
            "Finance-lease liabilities presented outside borrowings are not in "
            "total_debt; see total_debt_including_finance_leases."
        ),
    }


__all__ = [
    "POLICY_VERSIONS",
    "FINANCE_LEASE_STATUSES",
    "PLACEMENTS",
    "EVIDENCE_BASES",
    "TIER2_METRIC",
    "validate_finance_lease_evidence_row",
    "validate_finance_lease_evidence_reference_data",
    "get_finance_lease_evidence",
    "compute_total_debt_including_finance_leases",
    "finance_lease_flag_for_total_debt",
]
