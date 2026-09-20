"""
Unit tests for src/fundamentals/peer_classification_io.py.

No network -- exercises the CSV read/write round trip for
peer_groups.csv against synthetic peer_classification.py-shaped rows and
a temp directory (tmp_path).
"""

from src.fundamentals.peer_classification import validate_peer_mapping_row
from src.fundamentals.peer_classification_io import (
    PEER_GROUPS_FIELDNAMES,
    serialize_peer_group_row,
    deserialize_peer_group_row,
    write_peer_groups_csv,
    read_peer_groups_csv,
)


def _reviewed_row(**overrides):
    row = {
        "cik": "0000320193",
        "sector": "Information Technology",
        "industry": "Technology Hardware",
        "peer_group": "hardware_devices",
        "classification_source": "MANUAL",
        "classification_status": "REVIEWED",
        "mapping_version": "v1",
        "effective_from": "2025-01-01",
        "classification_available_date": "2025-01-05",
        "effective_to": None,
        "reviewed_at": "2025-01-05",
        "review_reason": "Initial peer group assignment",
        "notes": None,
    }
    row.update(overrides)
    return row


def test_serialize_peer_group_row_all_strings():
    serialized = serialize_peer_group_row(_reviewed_row())
    assert all(isinstance(v, str) for v in serialized.values())
    assert serialized["cik"] == "0000320193"
    assert serialized["effective_to"] == ""
    assert serialized["notes"] == ""


def test_deserialize_peer_group_row_empty_string_becomes_none():
    flat = {field: "" for field in PEER_GROUPS_FIELDNAMES}
    row = deserialize_peer_group_row(flat)
    assert all(value is None for value in row.values())


def test_write_then_read_peer_groups_csv_round_trips(tmp_path):
    rows = [
        _reviewed_row(),
        _reviewed_row(
            cik="0001375365",
            classification_source="SIC_DERIVED",
            classification_status="SIC_DERIVED",
            mapping_version="v1",
            reviewed_at=None,
            review_reason=None,
        ),
    ]
    path = tmp_path / "peer_groups.csv"
    write_peer_groups_csv(rows, path)
    read_back = read_peer_groups_csv(path)
    assert read_back == rows


def test_round_tripped_rows_still_pass_validate_peer_mapping_row(tmp_path):
    rows = [_reviewed_row()]
    path = tmp_path / "peer_groups.csv"
    write_peer_groups_csv(rows, path)
    read_back = read_peer_groups_csv(path)
    assert validate_peer_mapping_row(read_back[0]) == []


def test_write_peer_groups_csv_header_matches_fieldnames(tmp_path):
    path = tmp_path / "peer_groups.csv"
    write_peer_groups_csv([], path)
    header_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert header_line.split(",") == PEER_GROUPS_FIELDNAMES


def test_read_peer_groups_csv_header_only_file_returns_empty_list(tmp_path):
    path = tmp_path / "peer_groups.csv"
    path.write_text(",".join(PEER_GROUPS_FIELDNAMES) + "\n", encoding="utf-8")
    assert read_peer_groups_csv(path) == []


def test_write_peer_groups_csv_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "peer_groups.csv"
    write_peer_groups_csv([], path)
    assert path.exists()
