"""
CSV read/write for data/reference/finance_lease_evidence.csv.

Mirrors company_type_registry_io.py: file I/O only, no validation of its
own. Callers run finance_lease_evidence_registry.validate_finance_lease_
evidence_reference_data() on rows after reading and before writing.
"""

from __future__ import annotations

import csv
from pathlib import Path

FINANCE_LEASE_EVIDENCE_FIELDNAMES = [
    "ticker",
    "cik",
    "accession",
    "form",
    "filing_date",
    "period_end",
    "unit",
    "finance_lease_status",
    "finance_lease_amount_status",
    "finance_lease_liability_current",
    "finance_lease_liability_noncurrent",
    "finance_lease_liability_total",
    "current_location",
    "noncurrent_location",
    "current_in_total_debt",
    "noncurrent_in_total_debt",
    "tag_scope_capability",
    "evidence_basis",
    "source_urls",
    "evidence_rationale",
    "evidence_available_date",
    "reviewed_at_utc",
    "policy_version",
    "decision_ref",
]

DEFAULT_FINANCE_LEASE_EVIDENCE_CSV = Path("data/reference/finance_lease_evidence.csv")


def serialize_finance_lease_evidence_row(row: dict) -> dict:
    out = {}
    for field in FINANCE_LEASE_EVIDENCE_FIELDNAMES:
        value = row.get(field)
        out[field] = "" if value is None else str(value)
    return out


def deserialize_finance_lease_evidence_row(row: dict) -> dict:
    """"" becomes None; nothing else is converted (the registry parses)."""
    return {
        field: (row.get(field) if row.get(field) else None)
        for field in FINANCE_LEASE_EVIDENCE_FIELDNAMES
    }


def write_finance_lease_evidence_csv(rows: list[dict], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FINANCE_LEASE_EVIDENCE_FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(serialize_finance_lease_evidence_row(row))
    return path


def read_finance_lease_evidence_csv(path: Path = DEFAULT_FINANCE_LEASE_EVIDENCE_CSV) -> list[dict]:
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [deserialize_finance_lease_evidence_row(row) for row in reader]


__all__ = [
    "FINANCE_LEASE_EVIDENCE_FIELDNAMES",
    "DEFAULT_FINANCE_LEASE_EVIDENCE_CSV",
    "serialize_finance_lease_evidence_row",
    "deserialize_finance_lease_evidence_row",
    "write_finance_lease_evidence_csv",
    "read_finance_lease_evidence_csv",
]
