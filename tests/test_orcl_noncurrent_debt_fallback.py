"""
Regression test for the ORCL `noncurrent_debt` fallback migration
(Framework v2.1 Architecture v5, implementation step 1 -- "ORCL fallback
migration", 2026-09-28).

Background
----------
The verified fix -- Oracle (CIK 0001341439) tags its noncurrent debt
under `LongTermNotesPayable` rather than the generic
`LongTermDebtNoncurrent` concept most other issuers use -- was
originally discovered and applied only to
`src/fundamentals/sec_historical.py`'s `CONCEPT_MAP`, a module with
ZERO production callers (confirmed via repo-wide grep during the
Framework v2.1 Repository Implementation Audit). The actual production
path (`src/fundamentals/point_in_time.py` -> `sec_normalizer.find_concept`
/ `sec_normalizer.normalize_company`) never had this fallback, so a
production run for ORCL would have silently reported `noncurrent_debt`
(and therefore `total_debt`) as MISSING.

This test exercises the real production candidate list inside
`sec_normalizer.normalize_company()` (edited 2026-09-28 to add the
`LongTermNotesPayable` fallback) against a fixture built from REAL SEC
XBRL data, independently re-fetched live on 2026-09-28 via
https://data.sec.gov/api/xbrl/companyconcept/CIK0001341439/us-gaap/LongTermNotesPayable.json
and .../LongTermDebtNoncurrent.json -- re-confirming, with fresh data,
both halves of the original 2026-09-18 finding:

    - LongTermDebtNoncurrent: NoSuchKey (Oracle has never reported
      this concept).
    - LongTermNotesPayable: 86 real observations, most recently
      end=2026-05-31, val=122342000000 (~$122.3B), filed=2026-06-22,
      form=10-K, accn=0001193125-26-277521.

No CSV/production data files are touched by this test. It is pure
regression coverage for the code edit in
src/fundamentals/sec_normalizer.py's normalize_company().
"""

from __future__ import annotations

import copy

from src.fundamentals.sec_normalizer import (
    extract_instant_metric,
    find_best_instant_concept,
    normalize_company,
)


# ============================================================
# FIXTURE
# ============================================================
#
# Minimal but REAL SEC Company Facts shape for Oracle Corporation
# (CIK 0001341439). Only the us-gaap.LongTermNotesPayable concept is
# populated -- deliberately mirroring live production data, where
# Oracle's companyfacts payload has NO LongTermDebtNoncurrent key at
# all (confirmed live 2026-09-28: data.sec.gov returns NoSuchKey for
# that concept for this CIK). A handful of real observations are kept
# (rather than the full 86) to exercise the "latest period end, then
# latest filed date" selection logic in
# sec_normalizer.find_best_instant_concept() across more than one
# fiscal year, without bloating the fixture.

ORCL_CIK = "0001341439"

ORCL_COMPANYFACTS_FIXTURE = {
    "cik": 1341439,
    "entityName": "Oracle Corporation",
    "facts": {
        "us-gaap": {
            "LongTermNotesPayable": {
                "label": "Notes Payable, Noncurrent",
                "description": (
                    "Carrying value as of the balance sheet date of "
                    "notes payable (with maturities initially due "
                    "after one year or beyond the operating cycle if "
                    "longer), excluding current portion."
                ),
                "units": {
                    "USD": [
                        {
                            "end": "2024-05-31",
                            "val": 76264000000,
                            "accn": "0000950170-24-075605",
                            "fy": 2024,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2024-06-20",
                        },
                        {
                            "end": "2024-05-31",
                            "val": 76264000000,
                            "accn": "0000950170-25-087926",
                            "fy": 2025,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2025-06-18",
                            "frame": "CY2024Q2I",
                        },
                        {
                            "end": "2025-05-31",
                            "val": 85297000000,
                            "accn": "0000950170-25-087926",
                            "fy": 2025,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2025-06-18",
                        },
                        {
                            "end": "2025-05-31",
                            "val": 85297000000,
                            "accn": "0001193125-26-277521",
                            "fy": 2026,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2026-06-22",
                            "frame": "CY2025Q2I",
                        },
                        {
                            "end": "2026-05-31",
                            "val": 122342000000,
                            "accn": "0001193125-26-277521",
                            "fy": 2026,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2026-06-22",
                            "frame": "CY2026Q2I",
                        },
                    ]
                },
            },
            # No LongTermDebtNoncurrent key -- matches real production
            # data for this CIK (live-confirmed 2026-09-28: NoSuchKey).
        }
    },
}

# The exact candidate list used by the production path inside
# normalize_company() as of this migration (see sec_normalizer.py,
# normalize_company(), the non-FINANCIAL noncurrent_debt branch).
PRODUCTION_NONCURRENT_DEBT_CANDIDATES = [
    "LongTermDebtNoncurrent",
    "LongTermNotesPayable",
]

# The candidate list as it existed BEFORE this migration -- kept here
# only to positively demonstrate the bug this migration fixes, not as
# something any production code still uses.
PRE_MIGRATION_NONCURRENT_DEBT_CANDIDATES = [
    "LongTermDebtNoncurrent",
]


# ============================================================
# TESTS
# ============================================================

def test_orcl_fixture_has_no_longtermdebtnoncurrent_concept():
    """Sanity check on the fixture itself: it must faithfully mirror
    live production data, where Oracle has zero observations under
    LongTermDebtNoncurrent (confirmed live 2026-09-28)."""
    assert "LongTermDebtNoncurrent" not in (
        ORCL_COMPANYFACTS_FIXTURE["facts"]["us-gaap"]
    )
    assert "LongTermNotesPayable" in (
        ORCL_COMPANYFACTS_FIXTURE["facts"]["us-gaap"]
    )


def test_pre_migration_candidate_list_reports_orcl_noncurrent_debt_missing():
    """Documents the bug: before this migration, the production
    candidate list for noncurrent_debt was just
    ["LongTermDebtNoncurrent"], which resolves to nothing for ORCL."""
    data = copy.deepcopy(ORCL_COMPANYFACTS_FIXTURE)

    result = extract_instant_metric(
        data,
        "noncurrent_debt",
        PRE_MIGRATION_NONCURRENT_DEBT_CANDIDATES,
    )

    assert result["status"] == "MISSING"
    assert result["value"] is None


def test_production_candidate_list_resolves_orcl_noncurrent_debt_via_fallback():
    """The fix: the current production candidate list (as migrated
    into sec_normalizer.normalize_company()) must resolve ORCL's
    noncurrent_debt via the LongTermNotesPayable fallback, selecting
    the latest (period end, then filed date) observation."""
    data = copy.deepcopy(ORCL_COMPANYFACTS_FIXTURE)

    result = extract_instant_metric(
        data,
        "noncurrent_debt",
        PRODUCTION_NONCURRENT_DEBT_CANDIDATES,
    )

    assert result["status"] == "OK"
    assert result["concept"] == "LongTermNotesPayable"
    assert result["namespace"] == "us-gaap"
    # Latest real observation as of the 2026-09-28 live re-verification:
    # FY2026 10-K, period end 2026-05-31, filed 2026-06-22.
    assert result["value"] == 122342000000
    assert result["period_end"] == "2026-05-31"
    assert result["filing_date"] == "2026-06-22"
    assert result["form"] == "10-K"
    assert result["accession"] == "0001193125-26-277521"


def test_find_best_instant_concept_selects_latest_period_over_duplicate_filings():
    """The fixture deliberately includes the same (end=2025-05-31,
    val=85297000000) observation refiled under two different
    accessions (the FY2025 10-K itself, and again as a comparative
    prior-year column in the FY2026 10-K) to confirm selection isn't
    accidentally keyed on accession alone -- the true latest period
    (2026-05-31) must always win over any 2025-05-31 duplicate."""
    data = copy.deepcopy(ORCL_COMPANYFACTS_FIXTURE)

    best = find_best_instant_concept(
        data,
        PRODUCTION_NONCURRENT_DEBT_CANDIDATES,
        namespaces=("us-gaap", "dei"),
    )

    assert best is not None
    assert best["observation"]["end"] == "2026-05-31"
    assert best["observation"]["val"] == 122342000000


def test_normalize_company_resolves_orcl_noncurrent_debt_end_to_end():
    """End-to-end production-path check: normalize_company() -- the
    function this migration actually edited -- must surface ORCL's
    noncurrent_debt as OK, not MISSING, for a NON_FINANCIAL company
    type (Oracle is software/cloud, not a financial company)."""
    data = copy.deepcopy(ORCL_COMPANYFACTS_FIXTURE)

    normalized = normalize_company("ORCL", data, "NON_FINANCIAL")

    noncurrent_debt = normalized["metrics"]["noncurrent_debt"]

    assert noncurrent_debt["status"] == "OK"
    assert noncurrent_debt["concept"] == "LongTermNotesPayable"
    assert noncurrent_debt["value"] == 122342000000

    # total_debt is expected to stay INCOMPLETE in this fixture --
    # current_debt concepts (LongTermDebtCurrent / ShortTermBorrowings
    # / etc.) are deliberately out of scope for this fixture, since
    # this test's sole purpose is the noncurrent_debt fallback. A
    # missing current_debt must not be misreported as OK.
    assert normalized["metrics"]["current_debt"]["status"] == "MISSING"
    assert normalized["metrics"]["total_debt"]["status"] == "INCOMPLETE"
