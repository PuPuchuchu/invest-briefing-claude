"""
Unit tests for src/fundamentals/universe_membership_io.py.

No network -- exercises the CSV read/write round trip for
universe_membership.csv against synthetic universe_membership.py-shaped
rows and a temp directory (tmp_path).
"""

from src.fundamentals.universe_membership import validate_universe_membership_row
from src.fundamentals.universe_membership_io import (
    UNIVERSE_MEMBERSHIP_FIELDNAMES,
    serialize_universe_membership_row,
    deserialize_universe_membership_row,
    write_universe_membership_csv,
    read_universe_membership_csv,
)


def _active_row(**overrides):
    row = {
        "cik": "0001730168",
        "ticker": "AVGO",
        "universe_type": "WATCHLIST",
        "membership_status": "ACTIVE",
        "membership_source": "LEGACY_SEED",
        "membership_version": "1",
        "effective_from": "2026-09-21",
        "effective_to": None,
        "membership_available_date": "2026-09-21",
        "reviewed_at": None,
        "membership_reason": None,
        "notes": None,
    }
    row.update(overrides)
    return row


def test_serialize_universe_membership_row_all_strings():
    serialized = serialize_universe_membership_row(_active_row())
    assert all(isinstance(v, str) for v in serialized.values())
    assert serialized["cik"] == "0001730168"
    assert serialized["effective_to"] == ""
    assert serialized["notes"] == ""


def test_deserialize_universe_membership_row_empty_string_becomes_none():
    flat = {field: "" for field in UNIVERSE_MEMBERSHIP_FIELDNAMES}
    row = deserialize_universe_membership_row(flat)
    assert all(value is None for value in row.values())


def test_write_then_read_universe_membership_csv_round_trips(tmp_path):
    rows = [
        _active_row(),
        _active_row(
            universe_type="CORE",
            membership_source="MANUAL_REVIEW",
            reviewed_at="2026-09-21",
            membership_reason="Framework review: promoted candidate",
        ),
    ]
    path = tmp_path / "universe_membership.csv"
    write_universe_membership_csv(rows, path)
    read_back = read_universe_membership_csv(path)
    assert read_back == rows


def test_round_tripped_rows_still_pass_validate_universe_membership_row(tmp_path):
    rows = [_active_row()]
    path = tmp_path / "universe_membership.csv"
    write_universe_membership_csv(rows, path)
    read_back = read_universe_membership_csv(path)
    assert validate_universe_membership_row(read_back[0]) == []


def test_write_universe_membership_csv_header_matches_fieldnames(tmp_path):
    path = tmp_path / "universe_membership.csv"
    write_universe_membership_csv([], path)
    header_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert header_line.split(",") == UNIVERSE_MEMBERSHIP_FIELDNAMES


def test_read_universe_membership_csv_header_only_file_returns_empty_list(tmp_path):
    path = tmp_path / "universe_membership.csv"
    path.write_text(",".join(UNIVERSE_MEMBERSHIP_FIELDNAMES) + "\n", encoding="utf-8")
    assert read_universe_membership_csv(path) == []


def test_write_universe_membership_csv_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "universe_membership.csv"
    write_universe_membership_csv([], path)
    assert path.exists()


def test_avgo_dual_membership_fixture_round_trips_and_validates(tmp_path):
    # AVGO gets both a WATCHLIST+ACTIVE and a CORE+ACTIVE row (2026-09-21
    # confirmed fixture) -- confirm the I/O layer round-trips both rows
    # for the same CIK cleanly and each still validates independently.
    watchlist_row = _active_row(
        universe_type="WATCHLIST",
        membership_source="LEGACY_SEED",
        membership_version="1",
    )
    core_row = _active_row(
        universe_type="CORE",
        membership_source="MANUAL_REVIEW",
        membership_version="1",
        reviewed_at="2026-09-21",
        membership_reason="Framework review: promoted candidate",
    )
    path = tmp_path / "universe_membership.csv"
    write_universe_membership_csv([watchlist_row, core_row], path)
    read_back = read_universe_membership_csv(path)
    assert read_back == [watchlist_row, core_row]
    for row in read_back:
        assert validate_universe_membership_row(row) == []

