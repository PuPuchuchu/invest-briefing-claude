"""Build the stockholders'-equity test fixtures from real SEC companyfacts.

The fixtures let tests/test_stockholders_equity_coverage.py run offline in CI
on real SEC data. Each fixture is a slice of one issuer's companyfacts JSON
that keeps only the us-gaap concepts the stockholders'-equity extraction reads:

  * StockholdersEquity
  * StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest
  * Assets  (the instant freshness anchor; see sec_normalizer)

Each concept is copied whole (label, description, every unit and fact), and
the top-level cik/entityName are kept. Nothing is edited or filtered inside a
concept.

The build fails unless normalize_company() returns an identical
stockholders_equity result on the full file and on the slice. That result is
written as the pinned expectation, and PROVENANCE.json records the source
artifact and the sha256 of every full raw file and slice.

Usage (run from the repository root):

  python scripts/build_sec_equity_fixtures.py \
      --raw-dir <dir with TICKER_companyfacts.json> \
      --source-artifact sec-companyfacts-raw \
      --source-zip-sha256 <sha256 of the downloaded artifact zip> \
      --source-created-utc 2026-10-28T12:57Z \
      [--source-run-id <GitHub Actions run id>] \
      [--out-dir tests/fixtures/sec_equity]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

TICKERS = [
    "MSFT", "AAPL", "NVDA", "AVGO", "INTC", "MU",
    "AMD", "PLTR", "ORCL", "SMCI", "CRWV",
]

KEPT_CONCEPTS = [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "Assets",
]

TOP_LEVEL_KEYS = ["cik", "entityName"]

SLICE_SUFFIX = "_companyfacts_equity_slice.json"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_slice(data: dict) -> dict:
    us_gaap = (data.get("facts") or {}).get("us-gaap") or {}
    out = {k: data[k] for k in TOP_LEVEL_KEYS if k in data}
    out["facts"] = {"us-gaap": {c: us_gaap[c] for c in KEPT_CONCEPTS if c in us_gaap}}
    return out


def dump(obj) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--source-artifact", required=True)
    ap.add_argument("--source-zip-sha256", required=True)
    ap.add_argument("--source-created-utc", required=True)
    ap.add_argument("--source-run-id", default=None)
    ap.add_argument("--out-dir", default="tests/fixtures/sec_equity")
    args = ap.parse_args(argv)

    from src.fundamentals.sec_normalizer import normalize_company

    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    provenance = {
        "description": (
            "Slices of real SEC companyfacts used by "
            "tests/test_stockholders_equity_coverage.py. Built by "
            "scripts/build_sec_equity_fixtures.py; do not edit by hand."
        ),
        "source": {
            "artifact_name": args.source_artifact,
            "artifact_zip_sha256": args.source_zip_sha256,
            "artifact_created_utc": args.source_created_utc,
            "github_actions_run_id": args.source_run_id,
            "origin": "SEC EDGAR XBRL companyfacts API, fetched by the Fundamentals Batch workflow",
        },
        "kept_concepts": KEPT_CONCEPTS,
        "kept_top_level_keys": TOP_LEVEL_KEYS,
        "equivalence_check": (
            "normalize_company(...)['metrics']['stockholders_equity'] is identical "
            "on the full file and on the slice, for every ticker (checked at build time)"
        ),
        "tickers": {},
    }
    expected = {}

    for t in TICKERS:
        full_path = raw_dir / f"{t}_companyfacts.json"
        full_bytes = full_path.read_bytes()
        full = json.loads(full_bytes)
        sl = make_slice(full)

        a = normalize_company(ticker=t, data=full, company_type="NON_FINANCIAL")["metrics"]["stockholders_equity"]
        b = normalize_company(ticker=t, data=sl, company_type="NON_FINANCIAL")["metrics"]["stockholders_equity"]
        if a != b:
            print(f"{t}: slice result differs from full-file result", file=sys.stderr)
            return 1

        slice_bytes = dump(sl)
        (out_dir / f"{t}{SLICE_SUFFIX}").write_bytes(slice_bytes)
        provenance["tickers"][t] = {
            "full_file": full_path.name,
            "full_file_bytes": len(full_bytes),
            "full_file_sha256": sha256_bytes(full_bytes),
            "slice_file": f"{t}{SLICE_SUFFIX}",
            "slice_sha256": sha256_bytes(slice_bytes),
            "concepts_present": [c for c in KEPT_CONCEPTS if c in sl["facts"]["us-gaap"]],
        }
        expected[t] = a

    (out_dir / "PROVENANCE.json").write_bytes(dump(provenance))
    (out_dir / "expected_stockholders_equity.json").write_bytes(dump(expected))
    print(f"wrote {len(TICKERS)} slices to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
