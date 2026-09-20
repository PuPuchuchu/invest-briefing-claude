"""
Offline integration test: Raw SEC Submissions JSON -> Identity Normalization
-> Issuer/Security Record -> Peer Mapping Point-in-Time Lookup.

Required by the 2026-09-19 design review's final 6-point round, point 6:
an end-to-end proof that src/sec/identity.py's output is exactly what
src/fundamentals/peer_classification.py consumes, using a FIXED, hand-built
fixture -- no live network calls, no dependency on data.sec.gov being
reachable or unchanged.

This is a Pipeline Integration Test in this repo's taxonomy (mirrors the
intent of tests/test_sec_fundamentals_pipeline.py and
tests/test_point_in_time.py's own history-reconstruction tests), scoped
narrowly to the identity -> peer-classification seam, not the whole
Framework v2.0 pipeline.
"""

from src.sec.identity import (
    normalize_issuer_identity,
    validate_issuer_identity,
    validate_issuer_identity_provenance,
    normalize_security_identities,
    validate_security_identity,
    resolve_identity_status,
    IDENTITY_MATCH,
)
from src.fundamentals.peer_classification import (
    validate_reference_data,
    get_peer_mapping_as_of,
    build_comparison_peers,
    LOOKUP_OK,
    LOOKUP_NOT_YET_AVAILABLE,
    PRIMARY_COMPARISON_STATUSES,
)


# ============================================================
# Fixed fixture: raw SEC Submissions responses for 3 issuers
# (shapes match real, live-confirmed 2026-09-19 SEC API responses --
# GOOGL's multi-ticker array is the real Alphabet CIK 0001652044 shape)
# ============================================================

RAW_SUBMISSIONS_FIXTURE = {
    "0000320193": {  # Apple
        "cik": "0000320193",
        "name": "Apple Inc.",
        "sic": "3571",
        "sicDescription": "Electronic Computers",
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
    },
    "0001652044": {  # Alphabet -- multi-ticker CIK
        "cik": "0001652044",
        "name": "Alphabet Inc.",
        "sic": "7370",
        "sicDescription": "Services-Computer Programming, Data Processing, Etc.",
        "tickers": ["GOOGL", "GOOG", "GOOGM", "GOOGN"],
        "exchanges": ["Nasdaq", "Nasdaq", "Nasdaq", "Nasdaq"],
    },
    "0001375365": {  # Super Micro -- SIC-neighbor of Apple (both 3571)
        "cik": "0001375365",
        "name": "Super Micro Computer, Inc.",
        "sic": "3571",
        "sicDescription": "Electronic Computers",
        "tickers": ["SMCI"],
        "exchanges": ["Nasdaq"],
    },
}


def _raw_path(cik: str) -> str:
    return f"data/raw/sec/{cik}_submissions.json"


def test_full_pipeline_raw_to_identity_to_peer_lookup():
    normalized_at = "2026-09-19T00:00:00Z"

    # ---- Stage 1: Raw -> Issuer Identity (normalization) ----
    issuer_rows = []
    security_rows_by_cik = {}
    for cik, raw in RAW_SUBMISSIONS_FIXTURE.items():
        issuer_row = normalize_issuer_identity(
            raw, source_raw_path=_raw_path(cik), normalized_at=normalized_at
        )
        assert validate_issuer_identity(issuer_row) == []
        assert validate_issuer_identity_provenance(issuer_row) == []
        issuer_rows.append(issuer_row)

        security_rows = normalize_security_identities(raw)
        for sec_row in security_rows:
            assert validate_security_identity(sec_row) == []
        security_rows_by_cik[cik] = security_rows

    # Multi-ticker CIK produced exactly the tickers SEC reported, all
    # pointing back at the same issuer -- the core property this whole
    # module exists to preserve.
    assert [r["ticker"] for r in security_rows_by_cik["0001652044"]] == [
        "GOOGL", "GOOG", "GOOGM", "GOOGN",
    ]
    assert all(r["cik"] == "0001652044" for r in security_rows_by_cik["0001652044"])

    # ---- Stage 2: Identity resolution against a claimed mapping ----
    # A security_identity.csv row claims GOOG -> CIK 0001652044; cross-check
    # against the freshly normalized issuer record for that CIK.
    fresh_googl_issuer = next(r for r in issuer_rows if r["cik"] == "0001652044")
    status, reason = resolve_identity_status(
        claimed_cik="0001652044",
        claimed_ticker="GOOG",
        fresh_issuer_record=fresh_googl_issuer,
    )
    assert status == IDENTITY_MATCH

    # ---- Stage 3: Peer Mapping reference data (managed classification,
    # built on top of -- but never equal to -- the SIC pulled in Stage 1) ----
    # Apple and Super Micro share SIC 3571, which makes them SIC-derived
    # peer CANDIDATES; only Apple has actually been human-REVIEWED into the
    # "hardware_devices" Primary Peer Group as of this fixture.
    peer_rows = [
        {
            "cik": "0000320193",
            "mapping_version": "v1",
            "classification_status": "REVIEWED",
            "classification_source": "MANUAL",
            "peer_group": "hardware_devices",
            "effective_from": "2020-01-01",
            "effective_to": None,
            "classification_available_date": "2020-02-01",
        },
        {
            "cik": "0001375365",
            "mapping_version": "v1",
            "classification_status": "SIC_DERIVED",
            "classification_source": "SIC_DERIVED",
            "peer_group": "hardware_devices",
            "effective_from": "2020-01-01",
            "effective_to": None,
            "classification_available_date": "2020-01-01",
        },
        {
            "cik": "0001652044",
            "mapping_version": "v1",
            "classification_status": "REVIEWED",
            "classification_source": "MANUAL",
            "peer_group": "internet_services",
            "effective_from": "2020-01-01",
            "effective_to": None,
            "classification_available_date": "2020-02-01",
        },
    ]

    reference_failures = validate_reference_data(issuer_rows, peer_rows)
    assert reference_failures == []

    # ---- Stage 4: Point-in-time Primary peer lookup ----
    # As of 2025-06-01, Apple's REVIEWED mapping is usable for Primary
    # comparison; Super Micro's SIC_DERIVED mapping is NOT (candidate-only).
    apple_lookup = get_peer_mapping_as_of("0000320193", "2025-06-01", peer_rows)
    assert apple_lookup["lookup_status"] == LOOKUP_OK
    assert apple_lookup["mapping"]["peer_group"] == "hardware_devices"

    smci_lookup = get_peer_mapping_as_of("0001375365", "2025-06-01", peer_rows)
    assert smci_lookup["lookup_status"] != LOOKUP_OK  # SIC_DERIVED, not usable at PRIMARY tier

    # Retroactive-use prevention: evaluating Apple as of a date BEFORE its
    # mapping became available must fail closed.
    early_lookup = get_peer_mapping_as_of("0000320193", "2020-01-15", peer_rows)
    assert early_lookup["lookup_status"] == LOOKUP_NOT_YET_AVAILABLE

    # ---- Stage 5: Comparison set assembly (self-exclusion) ----
    hardware_devices_members = ["0000320193", "0001375365"]  # includes target
    comparison = build_comparison_peers("0000320193", hardware_devices_members)
    assert "0000320193" not in comparison["comparison_peers"]
    assert comparison["comparison_peers"] == ["0001375365"]
    assert comparison["peer_adequacy_status"] == "INSUFFICIENT_PEERS"  # 1 peer only


def test_full_pipeline_detects_a_planted_reference_data_integrity_problem():
    # Two overlapping mapping rows for the same CIK is exactly the kind of
    # reference-data corruption validate_reference_data() exists to catch
    # BEFORE it ever reaches get_peer_mapping_as_of() as an
    # OVERLAPPING_MAPPING surprise.
    issuer_rows = [
        normalize_issuer_identity(
            RAW_SUBMISSIONS_FIXTURE["0000320193"],
            source_raw_path=_raw_path("0000320193"),
            normalized_at="2026-09-19T00:00:00Z",
        )
    ]
    peer_rows = [
        {
            "cik": "0000320193",
            "mapping_version": "v1",
            "classification_status": "REVIEWED",
            "classification_source": "MANUAL",
            "peer_group": "hardware_devices",
            "effective_from": "2020-01-01",
            "effective_to": None,
            "classification_available_date": "2020-02-01",
        },
        {
            "cik": "0000320193",
            "mapping_version": "v2",
            "classification_status": "REVIEWED",
            "classification_source": "MANUAL",
            "peer_group": "consumer_electronics",
            "effective_from": "2022-06-01",  # overlaps v1's open-ended range
            "effective_to": None,
            "classification_available_date": "2022-06-15",
        },
    ]

    failures = validate_reference_data(issuer_rows, peer_rows)
    assert any("overlaps_peer_rows" in f for f in failures)

    lookup = get_peer_mapping_as_of("0000320193", "2025-01-01", peer_rows)
    assert lookup["lookup_status"] == "OVERLAPPING_MAPPING"