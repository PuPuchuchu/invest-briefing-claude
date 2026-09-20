"""
CSV read/write for data/reference/peer_groups.csv.

Why this module exists
-----------------------
src/fundamentals/peer_classification.py is deliberately a pure module: it
operates only on already-loaded row lists, no file I/O of its own (see its
own module docstring). This module is where peer_groups.csv rows actually
get read from and written to disk -- it does no validation of its own;
callers run validate_peer_mapping_row() / validate_reference_data() on
rows before writing and/or after reading, exactly as they would on any
other row list this repo passes around.

Unlike data/reference/issuer_identity.csv (see src/sec/reference_io.py),
every peer_groups.csv column is a plain scalar -- no list-typed columns,
so no delimiter convention is needed here.

2026-09-20 schema revision: added next_review_due (see
src/fundamentals/peer_classification.py's module docstring for why this
is a deliberately separate concept from effective_to) and reordered the
date columns to match the confirmed column order. This module does not
validate rows -- see that reminder in the module docstring above -- so
this reorder needed no logic change here, only the fieldnames list.

Deliberately NOT added here: sec_sic / sec_sic_description. Those stay
Single Source of Truth in data/reference/issuer_identity.csv (joined by
cik) -- duplicating them into peer_groups.csv would give the raw SIC two
places to drift out of sync, which is exactly the kind of raw-vs-managed
boundary confusion this module's design exists to prevent.
"""

from __future__ import annotations

import csv
from pathlib import Path

PEER_GROUPS_FIELDNAMES = [
    "cik",
    "sector",
    "industry",
    "peer_group",
    "classification_source",
    "classification_status",
    "mapping_version",
    "effective_from",
    "effective_to",
    "classification_available_date",
    "reviewed_at",
    "next_review_due",
    "review_reason",
    "notes",
]


def serialize_peer_group_row(row: dict) -> dict:
    """peer_groups.csv-shaped dict -> flat dict of strings, ready for
    csv.DictWriter. Missing values become "" (never fabricated)."""
    out = {}
    for field in PEER_GROUPS_FIELDNAMES:
        value = row.get(field)
        out[field] = "" if value is None else str(value)
    return out


def deserialize_peer_group_row(row: dict) -> dict:
    """Flat dict of strings (as read by csv.DictReader) -> a
    peer_groups.csv row shaped exactly as
    src.fundamentals.peer_classification's validate_peer_mapping_row() /
    get_peer_mapping_as_of() expect: "" becomes None."""
    return {field: (row.get(field) if row.get(field) else None) for field in PEER_GROUPS_FIELDNAMES}


def write_peer_groups_csv(rows: list[dict], path: Path) -> Path:
    """Write a list of peer_groups.csv-shaped rows to `path`. Overwrites
    any existing file at `path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PEER_GROUPS_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(serialize_peer_group_row(row))
    return path


def read_peer_groups_csv(path: Path) -> list[dict]:
    """Read peer_groups.csv from `path` back into row dicts, ready to pass
    straight into validate_reference_data() / get_peer_mapping_as_of()."""
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [deserialize_peer_group_row(row) for row in reader]


__all__ = [
    "PEER_GROUPS_FIELDNAMES",
    "serialize_peer_group_row",
    "deserialize_peer_group_row",
    "write_peer_groups_csv",
    "read_peer_groups_csv",
]
