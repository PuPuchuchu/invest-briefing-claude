"""
CSV read/write for the four Metric Applicability reference files
(Framework v2.1 Step 4, 2026-09-28): data/reference/metric_profiles.csv,
data/reference/peer_group_metric_profile.csv,
data/reference/metric_applicability.csv,
data/reference/metric_applicability_modifiers.csv.

Why this module exists
-----------------------
src/fundamentals/metric_applicability.py is deliberately a pure module: it
operates only on already-loaded row lists, no file I/O of its own (see its
own module docstring, and the established convention in
peer_classification.py / peer_classification_io.py). This module is where
these four files' rows actually get read from and written to disk -- it
does no validation of its own; callers run the matching validate_*()
function from metric_applicability.py on rows before writing and/or after
reading.

All four files are combined into one I/O module rather than four separate
ones, mirroring src/sec/reference_io.py's precedent of combining
issuer_identity.csv + security_identity.csv (two files that only make
sense together) into a single module -- these four reference files are
even more tightly coupled than that pair.

Every column across all four files is a plain scalar -- no list-typed
columns, so no delimiter convention is needed here (unlike
issuer_identity.csv's sec_tickers/sec_exchanges).
"""

from __future__ import annotations

import csv
from pathlib import Path

METRIC_PROFILES_FIELDNAMES = [
    "profile_id",
    "description",
    "default_applicability",
]

PEER_GROUP_METRIC_PROFILE_FIELDNAMES = [
    "peer_group",
    "profile_id",
    "effective_from",
    "effective_to",
    "classification_available_date",
    "mapping_source",
    "mapping_version",
    "reviewed_at",
    "mapping_rationale",
]

METRIC_APPLICABILITY_FIELDNAMES = [
    "profile_id",
    "metric_name",
    "applicability_status",
    "resolver_id",
    "policy_owner",
    "policy_version",
]

METRIC_APPLICABILITY_MODIFIERS_FIELDNAMES = [
    "profile_id",
    "metric_name",
    "modifier_id",
    "trigger",
    "result",
    "reason_code",
    "policy_owner",
    "rationale",
    "effective_from",
    "effective_to",
    "policy_version",
]


def _serialize_row(row: dict, fieldnames: list[str]) -> dict:
    out = {}
    for field in fieldnames:
        value = row.get(field)
        out[field] = "" if value is None else str(value)
    return out


def _deserialize_row(row: dict, fieldnames: list[str]) -> dict:
    return {field: (row.get(field) if row.get(field) else None) for field in fieldnames}


def _write_csv(rows: list[dict], path: Path, fieldnames: list[str]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(_serialize_row(row, fieldnames))
    return path


def _read_csv(path: Path, fieldnames: list[str]) -> list[dict]:
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [_deserialize_row(row, fieldnames) for row in reader]


# ============================================================
# metric_profiles.csv
# ============================================================

def serialize_metric_profile_row(row: dict) -> dict:
    return _serialize_row(row, METRIC_PROFILES_FIELDNAMES)


def deserialize_metric_profile_row(row: dict) -> dict:
    return _deserialize_row(row, METRIC_PROFILES_FIELDNAMES)


def write_metric_profiles_csv(rows: list[dict], path: Path) -> Path:
    return _write_csv(rows, path, METRIC_PROFILES_FIELDNAMES)


def read_metric_profiles_csv(path: Path) -> list[dict]:
    return _read_csv(path, METRIC_PROFILES_FIELDNAMES)


# ============================================================
# peer_group_metric_profile.csv
# ============================================================

def serialize_peer_group_metric_profile_row(row: dict) -> dict:
    return _serialize_row(row, PEER_GROUP_METRIC_PROFILE_FIELDNAMES)


def deserialize_peer_group_metric_profile_row(row: dict) -> dict:
    return _deserialize_row(row, PEER_GROUP_METRIC_PROFILE_FIELDNAMES)


def write_peer_group_metric_profile_csv(rows: list[dict], path: Path) -> Path:
    return _write_csv(rows, path, PEER_GROUP_METRIC_PROFILE_FIELDNAMES)


def read_peer_group_metric_profile_csv(path: Path) -> list[dict]:
    return _read_csv(path, PEER_GROUP_METRIC_PROFILE_FIELDNAMES)


# ============================================================
# metric_applicability.csv
# ============================================================

def serialize_metric_applicability_row(row: dict) -> dict:
    return _serialize_row(row, METRIC_APPLICABILITY_FIELDNAMES)


def deserialize_metric_applicability_row(row: dict) -> dict:
    return _deserialize_row(row, METRIC_APPLICABILITY_FIELDNAMES)


def write_metric_applicability_csv(rows: list[dict], path: Path) -> Path:
    return _write_csv(rows, path, METRIC_APPLICABILITY_FIELDNAMES)


def read_metric_applicability_csv(path: Path) -> list[dict]:
    return _read_csv(path, METRIC_APPLICABILITY_FIELDNAMES)


# ============================================================
# metric_applicability_modifiers.csv
# ============================================================

def serialize_metric_applicability_modifier_row(row: dict) -> dict:
    return _serialize_row(row, METRIC_APPLICABILITY_MODIFIERS_FIELDNAMES)


def deserialize_metric_applicability_modifier_row(row: dict) -> dict:
    return _deserialize_row(row, METRIC_APPLICABILITY_MODIFIERS_FIELDNAMES)


def write_metric_applicability_modifiers_csv(rows: list[dict], path: Path) -> Path:
    return _write_csv(rows, path, METRIC_APPLICABILITY_MODIFIERS_FIELDNAMES)


def read_metric_applicability_modifiers_csv(path: Path) -> list[dict]:
    return _read_csv(path, METRIC_APPLICABILITY_MODIFIERS_FIELDNAMES)


__all__ = [
    "METRIC_PROFILES_FIELDNAMES",
    "PEER_GROUP_METRIC_PROFILE_FIELDNAMES",
    "METRIC_APPLICABILITY_FIELDNAMES",
    "METRIC_APPLICABILITY_MODIFIERS_FIELDNAMES",
    "serialize_metric_profile_row",
    "deserialize_metric_profile_row",
    "write_metric_profiles_csv",
    "read_metric_profiles_csv",
    "serialize_peer_group_metric_profile_row",
    "deserialize_peer_group_metric_profile_row",
    "write_peer_group_metric_profile_csv",
    "read_peer_group_metric_profile_csv",
    "serialize_metric_applicability_row",
    "deserialize_metric_applicability_row",
    "write_metric_applicability_csv",
    "read_metric_applicability_csv",
    "serialize_metric_applicability_modifier_row",
    "deserialize_metric_applicability_modifier_row",
    "write_metric_applicability_modifiers_csv",
    "read_metric_applicability_modifiers_csv",
]
