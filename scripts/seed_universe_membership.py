"""
One-time seed script for data/reference/universe_membership.csv.

Why this script exists
-----------------------
Per the 2026-09-21 ChatGPT design round (see
src/fundamentals/universe_membership.py's module docstring), the
21-ticker Watchlist Universe had no canonical, versioned membership
record at all -- it existed only as a hard-coded Python list in
scripts/seed_identity_reference.py's WATCHLIST_TICKERS. This script
performs the one-time migration of that list into
data/reference/universe_membership.csv, plus AVGO's already-confirmed
Core Universe membership (see core_universe_candidates.csv's AVGO row:
inclusion_status=PROMOTED, added_date=2026-09-20).

Unlike scripts/seed_identity_reference.py, this script needs NO network
access at all -- every ticker in WATCHLIST_TICKERS already has a
resolved CIK in data/reference/security_identity.csv /
issuer_identity.csv (seeded earlier by that script), so this script only
reads those two files and writes universe_membership.csv. It is safe to
run from the Cowork cloud sandbox.

Migration date discipline (2026-09-21 instruction: do not backdate)
---------------------------------------------------------------------
The hard-coded WATCHLIST_TICKERS list carries NO reliable evidence of
when each ticker actually became part of 명호's observation universe --
only that it is part of it today. So every LEGACY_SEED row's
effective_from AND membership_available_date are set to the date this
script is actually run (SEED_DATE below), never backdated to a guessed
historical date.

AVGO's CORE+ACTIVE row is different: a real, evidenced decision date
already exists (core_universe_candidates.csv's added_date=2026-09-20,
the date the Framework's Core-promotion review was recorded), so its
effective_from uses that real date. Its membership_available_date is
still SEED_DATE -- the date this ledger itself came into existence and
became queryable -- per ChatGPT's 2026-09-21 confirmation that
effective_from and membership_available_date are never treated as the
same date:

    evaluation_date = 2026-09-20 (the promotion decision date itself)
        -> get_universe_membership_as_of(...) reports NOT_YET_AVAILABLE
           (correct: the ledger did not exist yet on that date)
    evaluation_date = 2026-09-21 (SEED_DATE, when the ledger was created)
        -> reports OK

This is the intended look-ahead-bias-prevention behavior, not a bug.

AVGO's WATCHLIST+ACTIVE row is unaffected by the above -- it is seeded
exactly like the other 20 legacy tickers (LEGACY_SEED, effective_from =
membership_available_date = SEED_DATE), since AVGO's Watchlist
membership itself has the same "no historical evidence" property as
every other legacy ticker. AVGO is the only ticker that receives BOTH a
WATCHLIST row and a CORE row (the dual-membership fixture confirmed by
ChatGPT's 2026-09-21 message and covered by
tests/test_universe_membership.py's
test_avgo_dual_membership_watchlist_and_core_both_lookup_ok_independently).
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.fundamentals.universe_membership import validate_universe_membership_data
from src.fundamentals.universe_membership_io import write_universe_membership_csv
from src.sec.reference_io import read_issuer_identity_csv, read_security_identity_csv

# Same 21 tickers as scripts/seed_identity_reference.py's
# WATCHLIST_TICKERS -- reproduced here rather than imported, since that
# module is a standalone entry-point script, not a library this repo
# imports from (consistent with this repo's convention of not importing
# across scripts/*.py files).
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

# The date this script is actually run -- see module docstring for why
# this is never backdated. Fixed as a literal (not date.today()) so a
# re-run of this exact script always reproduces the same seed file;
# update it by hand if this script is genuinely re-run on a later date
# for a fresh migration.
SEED_DATE = "2026-09-21"

# Real, evidenced Core-promotion decision date for AVGO -- see
# data/reference/core_universe_candidates.csv's AVGO row (added_date).
AVGO_CORE_EFFECTIVE_FROM = "2026-09-20"

REFERENCE_DIR = Path("data/reference")


def main() -> None:
    print("=" * 60)
    print("Seeding data/reference/universe_membership.csv")
    print("=" * 60)

    issuer_rows = read_issuer_identity_csv(REFERENCE_DIR / "issuer_identity.csv")
    security_rows = read_security_identity_csv(REFERENCE_DIR / "security_identity.csv")

    ticker_to_cik: dict[str, str] = {}
    for row in security_rows:
        ticker = row.get("ticker")
        cik = row.get("cik")
        if ticker in WATCHLIST_TICKERS and ticker not in ticker_to_cik and cik:
            ticker_to_cik[ticker] = cik

    missing = [t for t in WATCHLIST_TICKERS if t not in ticker_to_cik]
    if missing:
        raise RuntimeError(
            f"Refusing to seed: {len(missing)} watchlist ticker(s) have no "
            f"resolved CIK in security_identity.csv yet: {missing}. Run "
            f"scripts/seed_identity_reference.py first."
        )

    print(f"[PASS] Resolved {len(ticker_to_cik)}/{len(WATCHLIST_TICKERS)} tickers via security_identity.csv.")

    membership_rows: list[dict] = []

    # ---- 21 legacy WATCHLIST rows ----
    for ticker in WATCHLIST_TICKERS:
        membership_rows.append({
            "cik": ticker_to_cik[ticker],
            "ticker": ticker,
            "universe_type": "WATCHLIST",
            "membership_status": "ACTIVE",
            "membership_source": "LEGACY_SEED",
            "membership_version": "1",
            "effective_from": SEED_DATE,
            "effective_to": None,
            "membership_available_date": SEED_DATE,
            "reviewed_at": None,
            "membership_reason": (
                "Migrated from scripts/seed_identity_reference.py's hard-coded "
                "21-stock WATCHLIST_TICKERS list -- no reliable historical "
                "effective date exists, so effective_from is set to this "
                "migration's run date, not backdated."
            ),
            "notes": None,
        })

    # ---- AVGO CORE row (real promotion decision date) ----
    membership_rows.append({
        "cik": ticker_to_cik["AVGO"],
        "ticker": "AVGO",
        "universe_type": "CORE",
        "membership_status": "ACTIVE",
        "membership_source": "MANUAL_REVIEW",
        "membership_version": "1",
        "effective_from": AVGO_CORE_EFFECTIVE_FROM,
        "effective_to": None,
        "membership_available_date": SEED_DATE,
        "reviewed_at": AVGO_CORE_EFFECTIVE_FROM,
        "membership_reason": (
            "Core Universe promotion per core_universe_candidates.csv "
            "(business_mix_status=DIVERSIFIED, business_model_status="
            "CONDITIONAL, production_eligibility=ELIGIBLE, scoped to its own "
            "Diversified Semiconductor & Infrastructure peer group)."
        ),
        "notes": None,
    })

    failures = validate_universe_membership_data(issuer_rows, security_rows, membership_rows)
    if failures:
        for failure in failures:
            print(f"  [FAIL] {failure}")
        raise RuntimeError(
            f"Refusing to write: {len(failures)} validation failure(s) in the "
            f"seed rows -- see above."
        )

    print(f"[PASS] {len(membership_rows)} membership row(s) pass validate_universe_membership_data().")

    path = write_universe_membership_csv(membership_rows, REFERENCE_DIR / "universe_membership.csv")
    print(f"\n[PASS] Wrote {len(membership_rows)} row(s) to {path}.")
    print(f"       WATCHLIST rows: {sum(1 for r in membership_rows if r['universe_type'] == 'WATCHLIST')}")
    print(f"       CORE rows:      {sum(1 for r in membership_rows if r['universe_type'] == 'CORE')}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"\n[ABORT] {exc}", file=sys.stderr)
        sys.exit(1)

