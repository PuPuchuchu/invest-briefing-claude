"""
CSV read/write for data/reference/metric_eligibility.csv (DEC-055).

Mirrors finance_lease_evidence_registry_io.py: file I/O only. Callers run
metric_eligibility_registry.assert_valid_metric_eligibility_reference_data()
on rows after reading (the orchestrator gate also validates on every call).
"""

from __future__ import annotations

import csv
from pathlib import Path

METRIC_ELIGIBILITY_FIELDNAMES = [
    "ticker",
    "cik",
    "metric",
    "outcome",
    "governance_status",
    "use_scope",
    "effective_from",
    "effective_to",
    "eligibility_available_date",
    "reviewed_at_utc",
    "policy_version",
    "decision_ref",
    "rationale",
]

DEFAULT_METRIC_ELIGIBILITY_CSV = Path("data/reference/metric_eligibility.csv")


def serialize_metric_eligibility_row(row: dict) -> dict:
    return {field: "" if row.get(field) is None else str(row.get(field)) for field in METRIC_ELIGIBILITY_FIELDNAMES}


def deserialize_metric_eligibility_row(row: dict) -> dict:
    """"" becomes None; nothing else is converted (the registry parses)."""
    return {field: (row.get(field) if row.get(field) else None) for field in METRIC_ELIGIBILITY_FIELDNAMES}


def write_metric_eligibility_csv(rows: list[dict], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=METRIC_ELIGIBILITY_FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(serialize_metric_eligibility_row(row))
    return path


def read_metric_eligibility_csv(path: Path = DEFAULT_METRIC_ELIGIBILITY_CSV) -> list[dict]:
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        return [deserialize_metric_eligibility_row(row) for row in csv.DictReader(f)]


__all__ = [
    "METRIC_ELIGIBILITY_FIELDNAMES",
    "DEFAULT_METRIC_ELIGIBILITY_CSV",
    "serialize_metric_eligibility_row",
    "deserialize_metric_eligibility_row",
    "write_metric_eligibility_csv",
    "read_metric_eligibility_csv",
]
