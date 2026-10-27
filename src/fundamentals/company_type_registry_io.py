"""
CSV read/write for data/reference/company_type.csv.

Why this module exists
-----------------------
src/fundamentals/company_type_registry.py is deliberately a pure
module: it operates only on already-loaded row lists, no file I/O of
its own (mirrors peer_classification.py / peer_classification_io.py's
split exactly). This module is where company_type.csv rows actually
get read from and written to disk -- it does no validation of its
own; callers run validate_company_type_row() / validate_company_type_
reference_data() on rows before writing and/or after reading.
"""

from __future__ import annotations

import csv
from pathlib import Path

COMPANY_TYPE_FIELDNAMES = [
    "ticker",
    "cik",
    "company_type",
    "effective_from",
    "effective_to",
    "classification_available_date",
    "mapping_source",
    "mapping_rationale",
    "reviewed_at_utc",
    "decision_ref",
]


def serialize_company_type_row(row: dict) -> dict:
    """company_type.csv-shaped dict -> flat dict of strings, ready for
    csv.DictWriter. Missing values become "" (never fabricated)."""
    out = {}
    for field in COMPANY_TYPE_FIELDNAMES:
        value = row.get(field)
        out[field] = "" if value is None else str(value)
    return out


def deserialize_company_type_row(row: dict) -> dict:
    """Flat dict of strings (as read by csv.DictReader) -> a
    company_type.csv row shaped exactly as
    src.fundamentals.company_type_registry's validate_company_type_row() /
    get_company_type_as_of() expect: "" becomes None."""
    return {
        field: (row.get(field) if row.get(field) else None)
        for field in COMPANY_TYPE_FIELDNAMES
    }


def write_company_type_csv(rows: list[dict], path: Path) -> Path:
    """Write a list of company_type.csv-shaped rows to `path`.
    Overwrites any existing file at `path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COMPANY_TYPE_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(serialize_company_type_row(row))
    return path


def read_company_type_csv(path: Path) -> list[dict]:
    """Read company_type.csv from `path` back into row dicts, ready to
    pass straight into validate_company_type_reference_data() /
    get_company_type_as_of()."""
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [deserialize_company_type_row(row) for row in reader]


__all__ = [
    "COMPANY_TYPE_FIELDNAMES",
    "serialize_company_type_row",
    "deserialize_company_type_row",
    "write_company_type_csv",
    "read_company_type_csv",
]
