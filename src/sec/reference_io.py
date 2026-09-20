"""
CSV read/write for the Identity reference tables
(data/reference/issuer_identity.csv, data/reference/security_identity.csv).

Why this module exists
-----------------------
src/sec/identity.py is deliberately a pure module: zero API calls, zero
file I/O (see its own docstring). Something still has to own turning its
normalize_issuer_identity() / normalize_security_identities() output into
the actual reference CSV files data/reference/*.csv describe, and back.
That's this module's only job -- it does no normalization or validation
of its own; callers run identity.py's validate_* functions on rows before
writing and/or after reading, exactly as they would on any other row list.

CSV serialization decision (2026-09-20)
-----------------------------------------
issuer_identity.csv has two list-typed columns -- sec_tickers,
sec_exchanges -- because normalize_issuer_identity() can return several
tickers for one CIK (see identity.py's module docstring: a single CIK can
legitimately carry multiple tickers). CSV has no native list type, so one
serialization convention had to be picked. This module uses PIPE-DELIMITED
strings (e.g. "GOOGL|GOOG|GOOGM|GOOGN"), not JSON-encoded strings, because:

    - Ticker symbols and exchange names never legitimately contain "|",
      so no escaping is ever needed.
    - A JSON string embedded inside a CSV field requires its own quoting
      discipline on top of CSV's own quoting (commas inside the JSON,
      quotes inside quotes) -- avoidable complexity for data this simple.
    - It's trivially readable by a human opening the file directly on
      GitHub's mobile web view, which is how this repo's changes get
      manually applied.

This was a lower-stakes implementation-layer decision than the peer
classification design rounds required for peer_classification.py --
CIK/ticker/exchange strings are exactly what they are per SEC's own
submissions API, not an economic judgment -- but it is documented here
explicitly rather than left implicit, so a future reader (or a
ChatGPT-authored design doc) can see the reasoning and override it if
needed. security_identity.csv and peer_groups.csv have no list-typed
columns and need no such convention (see
src/fundamentals/peer_classification_io.py for the latter).
"""

from __future__ import annotations

import csv
from pathlib import Path

LIST_DELIMITER = "|"

ISSUER_IDENTITY_FIELDNAMES = [
    "cik",
    "issuer_name",
    "sic",
    "sic_description",
    "sec_tickers",
    "sec_exchanges",
    "source_raw_path",
    "normalized_at",
]

SECURITY_IDENTITY_FIELDNAMES = [
    "security_id",
    "ticker",
    "cik",
    "exchange",
    "identity_status",
    "identity_status_reason",
    "verified_at",
]

_ISSUER_LIST_FIELDS = ("sec_tickers", "sec_exchanges")
_ISSUER_SCALAR_FIELDS = (
    "cik",
    "issuer_name",
    "sic",
    "sic_description",
    "source_raw_path",
    "normalized_at",
)


# ============================================================
# issuer_identity.csv
# ============================================================

def serialize_issuer_identity_row(row: dict) -> dict:
    """normalize_issuer_identity()-shaped dict -> flat dict of strings,
    ready for csv.DictWriter. sec_tickers/sec_exchanges lists become
    pipe-delimited strings; missing scalars become "" (never fabricated)."""
    out = {}
    for field in _ISSUER_SCALAR_FIELDS:
        value = row.get(field)
        out[field] = "" if value is None else str(value)
    for field in _ISSUER_LIST_FIELDS:
        value = row.get(field)
        out[field] = LIST_DELIMITER.join(value) if isinstance(value, list) else ""
    return out


def deserialize_issuer_identity_row(row: dict) -> dict:
    """Flat dict of strings (as read by csv.DictReader) -> the same shape
    normalize_issuer_identity() returns: "" becomes None for scalars, and
    an empty/missing list field becomes []."""
    out = {}
    for field in _ISSUER_SCALAR_FIELDS:
        value = row.get(field)
        out[field] = value if value else None
    for field in _ISSUER_LIST_FIELDS:
        value = row.get(field) or ""
        out[field] = value.split(LIST_DELIMITER) if value else []
    return out


def write_issuer_identity_csv(rows: list[dict], path: Path) -> Path:
    """Write a list of normalize_issuer_identity()-shaped rows to `path`
    as issuer_identity.csv. Overwrites any existing file at `path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ISSUER_IDENTITY_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(serialize_issuer_identity_row(row))
    return path


def read_issuer_identity_csv(path: Path) -> list[dict]:
    """Read issuer_identity.csv from `path` back into
    normalize_issuer_identity()-shaped rows. Returns [] if the file has a
    header but no data rows (the schema-only stub this repo ships with)."""
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [deserialize_issuer_identity_row(row) for row in reader]


# ============================================================
# security_identity.csv (no list-typed columns -- straight pass-through)
# ============================================================

def serialize_security_identity_row(row: dict) -> dict:
    """normalize_security_identities()-shaped dict -> flat dict of
    strings. All fields are scalars; missing values become ""."""
    out = {}
    for field in SECURITY_IDENTITY_FIELDNAMES:
        value = row.get(field)
        out[field] = "" if value is None else str(value)
    return out


def deserialize_security_identity_row(row: dict) -> dict:
    """Flat dict of strings -> the same shape normalize_security_identities()
    returns: "" becomes None."""
    return {field: (row.get(field) if row.get(field) else None) for field in SECURITY_IDENTITY_FIELDNAMES}


def write_security_identity_csv(rows: list[dict], path: Path) -> Path:
    """Write a list of normalize_security_identities()-shaped rows to
    `path` as security_identity.csv. Overwrites any existing file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SECURITY_IDENTITY_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(serialize_security_identity_row(row))
    return path


def read_security_identity_csv(path: Path) -> list[dict]:
    """Read security_identity.csv from `path` back into
    normalize_security_identities()-shaped rows."""
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [deserialize_security_identity_row(row) for row in reader]


__all__ = [
    "LIST_DELIMITER",
    "ISSUER_IDENTITY_FIELDNAMES",
    "SECURITY_IDENTITY_FIELDNAMES",
    "serialize_issuer_identity_row",
    "deserialize_issuer_identity_row",
    "write_issuer_identity_csv",
    "read_issuer_identity_csv",
    "serialize_security_identity_row",
    "deserialize_security_identity_row",
    "write_security_identity_csv",
    "read_security_identity_csv",
]

