import json
from pathlib import Path

from src.sec.fetcher import fetch_and_cache_companyfacts, get_user_agent


# =========================================================
# Configuration
# =========================================================
# NOTE: SEC_USER_AGENT is resolved lazily inside main() via
# get_user_agent(), not at module import time. The previous version of
# this file called os.environ.get("SEC_USER_AGENT") and raised
# RuntimeError right here, at module scope -- which meant a plain
# `pytest -q` from a clean shell aborted collection for the ENTIRE test
# suite (not just this file) with "Interrupted: 1 error during
# collection, no tests collected", whenever the env var wasn't exported
# first. Verified directly in this environment before this fix. See
# src/sec/fetcher.get_user_agent() for the deferred-check rationale.


TICKERS = {
    "MSFT": "0000789019",
    "NVDA": "0001045810",
    "JPM": "0000019617",
    "LLY": "0000059478",
    "PLTR": "0001321655",
}


RAW_DIR = Path("data/raw/sec")
PROCESSED_DIR = Path("data/processed/fundamentals")


# =========================================================
# XBRL concept candidates
# =========================================================

METRIC_CANDIDATES = {

    "revenue": [
        # Regression fix (2026-09-18): this list previously put the
        # legacy "Revenues" tag ahead of the modern one. extract_metric()
        # below takes the FIRST candidate in this list that has ANY
        # observations -- it does not compare freshness across
        # candidates -- so ordering here is a correctness-critical
        # priority, not cosmetic.
        #
        # Confirmed against real SEC data (data.sec.gov) for Microsoft
        # (CIK 0000789019): "Revenues" exists (31 observations) but its
        # most recent observation is dated 2011-01-27 (10-Q, FY2011 Q2)
        # -- a dead/abandoned tag. "RevenueFromContractWithCustomer
        # ExcludingAssessedTax" exists with 149 observations, including
        # 10-Q data, through the current FY2026 10-K (filed 2026-07-29,
        # $331.839B). With the old ordering, extract_metric() silently
        # returned MSFT's ~15-year-stale 2011 revenue as if it were
        # current data.
        #
        # New order matches the already-validated priority used
        # elsewhere in this codebase (src/fundamentals/sec_historical.py
        # CONCEPT_MAP, tests/test_sec_companyfacts.py): modern tag
        # first, then the pre-ASC-606 legacy tag, then the older
        # generic tags as last-resort fallbacks (unverified against
        # real data for any current-universe ticker, kept only because
        # removing them was not data-justified either).
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
        "SalesRevenueGoodsNet",
        "Revenue",
    ],

    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],

    "diluted_eps": [
        "EarningsPerShareDiluted",
    ],

    "operating_income": [
        "OperatingIncomeLoss",
    ],

    "cfo": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],

    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ],

    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],

    "debt": [
        "LongTermDebtCurrent",
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "ShortTermBorrowings",
        "ShortTermDebt",
    ],

    "shares_outstanding": [
        "EntityCommonStockSharesOutstanding",
    ],
}


# =========================================================
# Raw SEC acquisition
# =========================================================
# fetch_json() / the HTTP + gzip/deflate handling previously duplicated
# here now lives in src/sec/fetcher.py (see module docstring there for
# why). This function keeps the same console output shape and the same
# required-key validation the original had; it just delegates the actual
# network call and raw-cache write to the shared module.

def download_companyfacts(
    ticker: str,
    cik: str,
    user_agent: str,
) -> dict:

    print()
    print("=" * 70)
    print(f"Downloading: {ticker}")
    print(f"CIK: {cik}")
    print("=" * 70)

    data, _raw_file = fetch_and_cache_companyfacts(
        ticker=ticker,
        cik=cik,
        raw_dir=RAW_DIR,
        user_agent=user_agent,
    )

    return data


# =========================================================
# XBRL concept search
# =========================================================

def find_concept(
    data: dict,
    concept: str
):

    facts = data.get(
        "facts",
        {}
    )

    # Prefer us-gaap first.
    namespace_order = [
        "us-gaap",
        "dei",
        "invest",
        "srt",
        "ecd",
        "ffd",
    ]

    for namespace in namespace_order:

        namespace_facts = facts.get(
            namespace,
            {}
        )

        if concept in namespace_facts:

            return (
                namespace,
                namespace_facts[concept]
            )

    return None, None


# =========================================================
# Observation helpers
# =========================================================

def get_observations(
    fact: dict
):

    units = fact.get(
        "units",
        {}
    )

    observations = []

    for unit, rows in units.items():

        for row in rows:

            if "val" not in row:
                continue

            item = dict(row)

            item["unit"] = unit

            observations.append(item)

    return observations


def sort_observations(
    observations: list
):

    return sorted(
        observations,
        key=lambda x: (
            x.get("filed", ""),
            x.get("end", ""),
            x.get("accn", ""),
        )
    )


# =========================================================
# Metric extraction
# =========================================================

def extract_metric(
    data: dict,
    metric: str
):

    candidates = METRIC_CANDIDATES[
        metric
    ]

    for concept in candidates:

        namespace, fact = find_concept(
            data,
            concept
        )

        if fact is None:
            continue

        observations = get_observations(
            fact
        )

        observations = sort_observations(
            observations
        )

        if not observations:
            continue

        # Keep the latest filed observation
        # only for this acquisition test.
        latest = observations[-1]

        return {
            "metric": metric,
            "namespace": namespace,
            "concept": concept,
            "value": latest.get("val"),
            "unit": latest.get("unit"),
            "period_start": latest.get("start"),
            "period_end": latest.get("end"),
            "fiscal_year": latest.get("fy"),
            "fiscal_period": latest.get("fp"),
            "form": latest.get("form"),
            "filed": latest.get("filed"),
            "frame": latest.get("frame"),
            "accn": latest.get("accn"),
        }

    return None


# =========================================================
# Fundamental extraction
# =========================================================

def extract_fundamentals(
    ticker: str,
    data: dict
) -> dict:

    print()
    print(
        f"--- Fundamental extraction: "
        f"{ticker} ---"
    )

    entity_name = data.get(
        "entityName"
    )

    extracted = {}
    missing = []

    for metric in METRIC_CANDIDATES:

        result = extract_metric(
            data,
            metric
        )

        if result is None:

            print(
                f"[WARN] {metric}: NOT FOUND"
            )

            missing.append(metric)

        else:

            extracted[metric] = result

            print(
                f"[PASS] {metric}: "
                f"value={result['value']} "
                f"unit={result['unit']} "
                f"concept={result['concept']} "
                f"period_end={result['period_end']} "
                f"form={result['form']} "
                f"filed={result['filed']}"
            )

    return {
        "ticker": ticker,
        "entity_name": entity_name,
        "source": "SEC Company Facts",
        "extracted_metrics": extracted,
        "missing_metrics": missing,
    }


# =========================================================
# Main pipeline
# =========================================================

def main():

    print(
        "=============================================="
    )
    print(
        "SEC Fundamental Pipeline Test"
    )
    print(
        "=============================================="
    )

    PROCESSED_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # Resolved here, not at import time -- see the Configuration note
    # above main() will fail fast with a clear error if SEC_USER_AGENT
    # is unset, but only once main() actually runs, not on collection.
    user_agent = get_user_agent()

    pipeline_results = {}

    api_success = 0
    extraction_success = 0

    for ticker, cik in TICKERS.items():

        try:

            data = download_companyfacts(
                ticker,
                cik,
                user_agent,
            )

            api_success += 1

            result = extract_fundamentals(
                ticker,
                data
            )

            pipeline_results[ticker] = result

            output_file = (
                PROCESSED_DIR /
                f"{ticker}_fundamentals.json"
            )

            with output_file.open(
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    result,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

            print(
                f"[PASS] Processed file written: "
                f"{output_file}"
            )

            extraction_success += 1

        except Exception as e:

            print(
                f"[FAIL] {ticker}: "
                f"{type(e).__name__}: {e}"
            )

    # -----------------------------------------------------
    # Summary
    # -----------------------------------------------------

    print()
    print(
        "=============================================="
    )
    print(
        "FINAL RESULT"
    )
    print(
        "=============================================="
    )

    print(
        f"API acquisition: "
        f"{api_success}/{len(TICKERS)}"
    )

    print(
        f"Fundamental extraction: "
        f"{extraction_success}/{len(TICKERS)}"
    )

    total_metrics = 0
    found_metrics = 0

    for ticker, result in pipeline_results.items():

        found = len(
            result["extracted_metrics"]
        )

        missing = len(
            result["missing_metrics"]
        )

        total = found + missing

        total_metrics += total
        found_metrics += found

        print(
            f"{ticker}: "
            f"{found}/{total} metrics found"
        )

        if missing:

            print(
                f"  Missing: "
                f"{', '.join(result['missing_metrics'])}"
            )

    print()
    print(
        f"Metric availability: "
        f"{found_metrics}/{total_metrics}"
    )

    print(
        "=============================================="
    )

    # API acquisition failure is a hard failure.
    if api_success != len(TICKERS):

        raise RuntimeError(
            "SEC API acquisition test failed."
        )

    # Extraction itself should not fail for all companies.
    if extraction_success != len(TICKERS):

        raise RuntimeError(
            "Fundamental extraction pipeline failed."
        )

    print(
        "[PASS] SEC Fundamental Pipeline completed."
    )


if __name__ == "__main__":
    main()
