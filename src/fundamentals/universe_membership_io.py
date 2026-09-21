"""
CSV read/write for data/reference/universe_membership.csv.

Why this module exists
-----------------------
src/fundamentals/universe_membership.py is deliberately a pure module: it
operates only on already-loaded row lists, no file I/O of its own (see its
own module docstring, which mirrors src/fundamentals/peer_classification.py's
raw-vs-managed boundary principle on this point). This module is where
universe_membership.csv rows actually get read from and written to disk --
it does no validation of its own; callers run
validate_universe_membership_row() / validate_universe_membership_data() on
rows before writing and/or after reading, exactly as they would on any
other row list this repo passes around.

Every universe_membership.csv column is a plain scalar -- no list-typed
columns, so no delimiter convention is needed here (same as
peer_classification_io.py).

Column order (2026-09-21 confirmed schema): identity columns first
(cik, ticker, universe_type), then the membership fact itself
(membership_status, membership_source, membership_version), then the
date/interval columns (effective_from, effective_to,
membership_available_date, reviewed_at), then free-text
(membership_reason, notes) -- mirrors peer_groups.csv's own column
grouping convention (identity -> classification -> dates -> free-text).
"""

from __future__ import annotations

import csv
from pathlib import Path

UNIVERSE_MEMBERSHIP_FIELDNAMES = [
    "cik",
    "ticker",
    "universe_type",
    "membership_status",
    "membership_source",
    "membership_version",
    "effective_from",
    "effective_to",
    "membership_available_date",
    "reviewed_at",
    "membership_reason",
    "notes",
]


def serialize_universe_membership_row(row: dict) -> dict:
    """universe_membership.csv-shaped dict -> flat dict of strings, ready
    for csv.DictWriter. Missing values become "" (never fabricated)."""
    out = {}
    for field in UNIVERSE_MEMBERSHIP_FIELDNAMES:
        value = row.get(field)
        out[field] = "" if value is None else str(value)
    return out


def deserialize_universe_membership_row(row: dict) -> dict:
    """Flat dict of strings (as read by csv.DictReader) -> a
    universe_membership.csv row shaped exactly as
    src.fundamentals.universe_membership's
    validate_universe_membership_row() / validate_universe_membership_data()
    / get_universe_membership_as_of() expect: "" becomes None."""
    return {
        field: (row.get(field) if row.get(field) else None)
        for field in UNIVERSE_MEMBERSHIP_FIELDNAMES
    }


def write_universe_membership_csv(rows: list[dict], path: Path) -> Path:
    """Write a list of universe_membership.csv-shaped rows to `path`.
    Overwrites any existing file at `path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=UNIVERSE_MEMBERSHIP_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(serialize_universe_membership_row(row))
    return path


def read_universe_membership_csv(path: Path) -> list[dict]:
    """Read universe_membership.csv from `path` back into row dicts, ready
    to pass straight into validate_universe_membership_data() /
    get_universe_membership_as_of()."""
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [deserialize_universe_membership_row(row) for row in reader]


__all__ = [
    "UNIVERSE_MEMBERSHIP_FIELDNAMES",
    "serialize_universe_membership_row",
    "deserialize_universe_membership_row",
    "write_universe_membership_csv",
    "read_universe_membership_csv",
]

