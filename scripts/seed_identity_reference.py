"""
Seed data/reference/{issuer_identity,security_identity,peer_groups}.csv
from live SEC data for the 21-stock watchlist.

Why this script exists
-----------------------
data/reference/*.csv currently ship as header-only schema stubs (see
src/sec/identity.py, src/fundamentals/peer_classification.py, and
src/sec/reference_io.py / src/fundamentals/peer_classification_io.py for
the modules that define and read/write this schema). This script performs
the one-time (then periodically re-run) bulk seed: for every ticker in the
project's 21-stock observation universe, fetch its real SEC Submissions
data, normalize it, and write it into the reference CSVs.

Watchlist source of truth: /projects/.../overview.md in Claude's memory
("Observation universe (21 stocks, 5 sectors -- as of 2026-09-07)"),
Information Technology(11) + Communication Services(3) + Financials(2) +
Healthcare(3) + Industrials/Defense(2) = 21. Reproduced here as a plain
list rather than re-derived, since this script has no access to that
memory store at run time.

CIK resolution deliberately does NOT hardcode CIK numbers for the 21
tickers -- SEC's bulk ticker->CIK file (fetch_company_tickers) is fetched
and used instead, via src.sec.identity.resolve_cik_for_ticker(). A ticker
this repo has previously looked up by hand is exactly the kind of value
that is easy to mistype once and then propagate silently forever; letting
the live bulk file be the single source of truth avoids that risk
entirely, at the cost of one extra HTTP call.

Network requirement
--------------------
This script needs outbound HTTPS to data.sec.gov and www.sec.gov. It is
NOT runnable from the Cowork cloud sandbox this project has been
developed in -- that sandbox's egress proxy explicitly denies
data.sec.gov at the policy layer (confirmed 2026-09-20 via the proxy's
own status endpoint: "gateway answered 403 to CONNECT (policy denial or
upstream failure)"). This mirrors every other real-SEC-data step in this
project (see e.g. tests/test_sec_fundamentals_pipeline.py,
tests/test_sec_history_real.py): it is meant to be run via a GitHub
Actions workflow_dispatch job with real network access
(.github/workflows/seed-identity-reference.yml), or locally by 명호. The
script itself has no test-suite dependency on live network -- its pure
logic (URL building, normalization, CSV serialization) is already covered
by tests/test_sec_identity.py, tests/test_sec_reference_io.py, and
tests/test_peer_classification_io.py using synthetic fixtures.

What this script deliberately does NOT do
-------------------------------------------
It seeds issuer_identity.csv and security_identity.csv fully (pure SEC
fact, no judgment involved), and peer_groups.csv with SIC_DERIVED rows
ONLY -- grouping companies that share the same SIC code into an
auto-generated candidate peer_group ("sic_<code>"). Per the 2026-09-19
Peer Classification design (see src/fundamentals/peer_classification.py's
module docstring): SIC is candidate generation only, and SIC_DERIVED rows
are explicitly NOT usable for Primary Peer Group comparison
(PRIMARY_COMPARISON_STATUSES = {"REVIEWED"} only). This script never
writes a REVIEWED row -- promoting a SIC-derived candidate group to
REVIEWED is a human/ChatGPT judgment call this script has no authority to
make, and this project's standing rule is never to assert an economic
classification without that judgment (parallels the "SEC taxonomy는
실제 데이터 근거 없이 수정하지 않는다" rule already applied elsewhere).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

from src.sec.fetcher import (
    fetch_and_cache_submissions,
    fetch_company_tickers,
    get_user_agent,
)
from src.sec.identity import (
    normalize_issuer_identity,
    validate_issuer_identity,
    validate_issuer_identity_provenance,
    normalize_security_identities,
    validate_security_identity,
    resolve_cik_for_ticker,
)
from src.sec.reference_io import (
    write_issuer_identity_csv,
    write_security_identity_csv,
)
from src.fundamentals.peer_classification import validate_reference_data
from src.fundamentals.peer_classification_io import write_peer_groups_csv


# =========================================================
# Configuration
# =========================================================

WATCHLIST_TICKERS = [
    # Information Technology (11)
    "MSFT", "AAPL", "NVDA", "AVGO", "INTC", "MU", "AMD", "PLTR", "ORCL", "SMCI", "CRWV",
    # Communication Services (3)
    "GOOGL", "META", "NFLX",
    # Financials (2)
    "BLK", "JPM",
    # Healthcare (3)
    "LLY", "UNH", "TEM",
    # Industrials/Defense (2)
    "LMT", "GE",
]

RAW_DIR = Path("data/raw/sec")
REFERENCE_DIR = Path("data/reference")


def main() -> None:
    user_agent = get_user_agent(default="invest-briefing-claude/0.1 chks7788@gmail.com")
    normalized_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = date.today().isoformat()

    print("=" * 60)
    print("Seeding identity reference data")
    print("=" * 60)

    # ---- Step 1: ticker -> CIK resolution (bulk file, not hardcoded) ----
    print(f"\nFetching SEC bulk ticker->CIK file for {len(WATCHLIST_TICKERS)} tickers...")
    company_tickers = fetch_company_tickers(user_agent=user_agent, verbose=False)

    ticker_to_cik: dict[str, str] = {}
    unresolved: list[str] = []
    for ticker in WATCHLIST_TICKERS:
        cik = resolve_cik_for_ticker(company_tickers, ticker)
        if cik is None:
            unresolved.append(ticker)
        else:
            ticker_to_cik[ticker] = cik

    if unresolved:
        raise RuntimeError(
            f"Could not resolve CIK for {len(unresolved)} watchlist ticker(s): "
            f"{unresolved}. Refusing to seed partial/guessed data -- check these "
            f"tickers manually against SEC's own bulk file before re-running."
        )

    print(f"[PASS] Resolved {len(ticker_to_cik)}/{len(WATCHLIST_TICKERS)} tickers to CIKs.")

    # ---- Step 2: fetch + cache raw Submissions per unique CIK ----
    unique_ciks = sorted(set(ticker_to_cik.values()))
    print(f"\nFetching SEC Submissions for {len(unique_ciks)} unique CIK(s)...")

    issuer_rows: list[dict] = []
    security_rows: list[dict] = []
    fetch_failures: list[tuple[str, str]] = []

    for cik in unique_ciks:
        try:
            raw, raw_path = fetch_and_cache_submissions(
                cik, raw_dir=RAW_DIR, user_agent=user_agent, verbose=False
            )
        except Exception as exc:  # noqa: BLE001 -- report and continue, don't abort the whole run
            fetch_failures.append((cik, str(exc)))
            print(f"  [FAIL] CIK {cik}: {exc}")
            continue

        issuer_row = normalize_issuer_identity(
            raw, source_raw_path=str(raw_path), normalized_at=normalized_at
        )
        issuer_failures = validate_issuer_identity(issuer_row) + validate_issuer_identity_provenance(issuer_row)
        if issuer_failures:
            raise RuntimeError(f"CIK {cik}: normalized issuer row failed validation: {issuer_failures}")
        issuer_rows.append(issuer_row)

        for sec_row in normalize_security_identities(raw):
            sec_failures = validate_security_identity(sec_row)
            if sec_failures:
                raise RuntimeError(f"CIK {cik}: normalized security row failed validation: {sec_failures}")
            security_rows.append(sec_row)

        print(f"  [PASS] CIK {cik}: {issuer_row['issuer_name']!r}, {len(normalize_security_identities(raw))} ticker(s)")

    if fetch_failures:
        print(f"\n[WARN] {len(fetch_failures)} CIK(s) failed to fetch -- continuing with the rest: {fetch_failures}")

    # ---- Step 3: write issuer_identity.csv / security_identity.csv ----
    write_issuer_identity_csv(issuer_rows, REFERENCE_DIR / "issuer_identity.csv")
    write_security_identity_csv(security_rows, REFERENCE_DIR / "security_identity.csv")
    print(f"\n[PASS] Wrote {len(issuer_rows)} issuer row(s), {len(security_rows)} security row(s).")

    # ---- Step 4: SIC_DERIVED peer_groups.csv candidate rows ----
    # Mechanical only -- group by SIC code, never a REVIEWED assertion.
    # See this module's docstring for why REVIEWED rows are out of scope.
    peer_rows: list[dict] = []
    for issuer_row in issuer_rows:
        sic = issuer_row.get("sic")
        if not sic:
            continue
        peer_rows.append(
            {
                "cik": issuer_row["cik"],
                "sector": None,
                "industry": issuer_row.get("sic_description"),
                "peer_group": f"sic_{sic}",
                "classification_source": "SIC_DERIVED",
                "classification_status": "SIC_DERIVED",
                "mapping_version": "v1",
                "effective_from": today,
                "classification_available_date": today,
                "effective_to": None,
                "reviewed_at": None,
                "review_reason": None,
                "notes": "Auto-generated SIC_DERIVED candidate -- not yet human-reviewed.",
            }
        )

    reference_failures = validate_reference_data(issuer_rows, peer_rows)
    if reference_failures:
        raise RuntimeError(f"Seeded peer_groups.csv failed validate_reference_data(): {reference_failures}")

    write_peer_groups_csv(peer_rows, REFERENCE_DIR / "peer_groups.csv")
    print(f"[PASS] Wrote {len(peer_rows)} SIC_DERIVED peer_groups.csv row(s) (0 REVIEWED -- human review still required).")

    print("\n" + "=" * 60)
    if fetch_failures or unresolved:
        print("[PARTIAL] Seeding completed with some failures -- see above.")
        sys.exit(1)
    print("[PASS] Identity reference seeding completed.")


if __name__ == "__main__":
    main()
