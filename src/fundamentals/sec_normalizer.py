import json
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

RAW_DIR = Path("data/raw/sec")
PROCESSED_DIR = Path("data/processed/fundamentals")

SCHEMA_VERSION = "sec_fundamentals_v0.2"


# ============================================================
# BASIC VALIDATION
# ============================================================

def validate_companyfacts(data: dict) -> None:
    required_keys = [
        "cik",
        "entityName",
        "facts",
    ]

    for key in required_keys:
        if key not in data:
            raise ValueError(f"Missing required key: {key}")

    if not isinstance(data["facts"], dict):
        raise ValueError("facts is not a dictionary")


# ============================================================
# SEC FACT HELPERS
# ============================================================

def get_all_concepts(data: dict) -> list:
    validate_companyfacts(data)

    concepts = []

    for namespace, namespace_data in data["facts"].items():
        if not isinstance(namespace_data, dict):
            continue

        for concept_name, concept_data in namespace_data.items():
            concepts.append({
                "namespace": namespace,
                "concept": concept_name,
                "data": concept_data,
            })

    return concepts


def get_observations(concept_data: dict) -> list:
    if not isinstance(concept_data, dict):
        return []

    units = concept_data.get("units", {})

    if not isinstance(units, dict):
        return []

    observations = []

    for unit, values in units.items():
        if not isinstance(values, list):
            continue

        for obs in values:
            if not isinstance(obs, dict):
                continue

            row = dict(obs)
            row["unit"] = unit
            observations.append(row)

    return observations


# ============================================================
# OBSERVATION CLASSIFICATION
# ============================================================

def is_instant_observation(obs: dict) -> bool:
    return "end" in obs and "start" not in obs


def is_duration_observation(obs: dict) -> bool:
    return "start" in obs and "end" in obs


def is_annual_observation(obs: dict) -> bool:
    if obs.get("form") != "10-K":
        return False

    if obs.get("fp") != "FY":
        return False

    return is_duration_observation(obs)


# ============================================================
# BASIC VALUE / DATE HELPERS
# ============================================================

def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _date_string(value):
    if value is None:
        return None

    if isinstance(value, str):
        return value[:10]

    return str(value)[:10]


def _observation_sort_key(
    observation: dict,
    concept_priority: int = 0,
):
    return (
        _date_string(observation.get("end")) or "",
        _date_string(observation.get("filed")) or "",
        -concept_priority,
        observation.get("accn") or "",
    )


# ============================================================
# OBSERVATION SELECTION
# ============================================================

def sort_observations(observations: list) -> list:
    return sorted(
        observations,
        key=lambda x: (
            _date_string(x.get("filed")) or "",
            _date_string(x.get("end")) or "",
            _date_string(x.get("start")) or "",
            x.get("accn") or "",
        ),
        reverse=True,
    )


def sort_instant_observations(observations: list) -> list:
    return sorted(
        observations,
        key=lambda x: (
            _date_string(x.get("end")) or "",
            _date_string(x.get("filed")) or "",
            x.get("accn") or "",
        ),
        reverse=True,
    )


def select_latest_annual_observation(concept_data: dict):
    observations = get_observations(concept_data)

    annual = [
        obs
        for obs in observations
        if is_annual_observation(obs)
    ]

    if not annual:
        return None

    return sort_observations(annual)[0]


def select_latest_instant_observation(concept_data: dict):
    observations = get_observations(concept_data)

    instant = [
        obs
        for obs in observations
        if is_instant_observation(obs)
    ]

    if not instant:
        return None

    return sort_instant_observations(instant)[0]


# ============================================================
# CONCEPT LOOKUP
# ============================================================

def find_concept(
    data: dict,
    namespace: str,
    concept_name: str,
):
    facts = data.get("facts", {})

    namespace_data = facts.get(namespace)

    if not isinstance(namespace_data, dict):
        return None

    return namespace_data.get(concept_name)


# ============================================================
# PRIORITY-BASED ANNUAL CONCEPT SELECTION
# ============================================================

def find_best_annual_concept(
    data: dict,
    concept_candidates: list,
    namespaces=("us-gaap",),
):
    candidates = []

    for concept_priority, concept_name in enumerate(
        concept_candidates
    ):
        for namespace in namespaces:
            concept_data = find_concept(
                data,
                namespace,
                concept_name,
            )

            if concept_data is None:
                continue

            observation = select_latest_annual_observation(
                concept_data
            )

            if observation is None:
                continue

            candidates.append({
                "namespace": namespace,
                "concept": concept_name,
                "observation": observation,
                "concept_priority": concept_priority,
            })

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            _date_string(
                item["observation"].get("filed")
            ) or "",
            _date_string(
                item["observation"].get("end")
            ) or "",
            -item["concept_priority"],
            item["observation"].get("accn") or "",
        ),
        reverse=True,
    )

    return candidates[0]


# ============================================================
# PRIORITY-BASED INSTANT CONCEPT SELECTION
# ============================================================

def find_best_instant_concept(
    data: dict,
    concept_candidates: list,
    namespaces=("us-gaap", "dei"),
):
    """
    Select the best instant observation across all
    candidate concepts and namespaces.

    Selection priority:
        1. Latest economic period end
        2. Latest filing date
        3. Candidate concept priority
        4. Accession number

    All candidate concepts are evaluated before selection.
    """

    candidates = []

    for concept_priority, concept_name in enumerate(
        concept_candidates
    ):
        for namespace in namespaces:
            concept_data = find_concept(
                data,
                namespace,
                concept_name,
            )

            if concept_data is None:
                continue

            observation = select_latest_instant_observation(
                concept_data
            )

            if observation is None:
                continue

            candidates.append({
                "namespace": namespace,
                "concept": concept_name,
                "observation": observation,
                "concept_priority": concept_priority,
            })

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            _date_string(
                item["observation"].get("end")
            ) or "",
            _date_string(
                item["observation"].get("filed")
            ) or "",
            -item["concept_priority"],
            item["observation"].get("accn") or "",
        ),
        reverse=True,
    )

    return candidates[0]


# ============================================================
# METRIC SEMANTICS
# ============================================================

def get_metric_semantics(
    metric_name: str,
    concept: str,
):
    """
    Return semantic metadata for metrics whose accounting
    meaning can be distinguished from the selected SEC concept.
    """

    if metric_name != "stockholders_equity":
        return {}

    if concept == "StockholdersEquity":
        return {
            "equity_scope": "STOCKHOLDERS_EQUITY",
            "includes_noncontrolling_interest": False,
            "semantic_status": "CLASSIFIED",
        }

    if (
        concept
        == "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
    ):
        return {
            "equity_scope": "TOTAL_EQUITY",
            "includes_noncontrolling_interest": True,
            "semantic_status": "CLASSIFIED",
        }

    return {
        "equity_scope": "UNKNOWN",
        "includes_noncontrolling_interest": None,
        "semantic_status": "UNCLASSIFIED",
    }


# ============================================================
# RESULT BUILDERS
# ============================================================

def build_missing_metric(
    metric_name: str,
    reason: str = None,
):
    result = {
        "metric": metric_name,
        "status": "MISSING",
        "value": None,
    }

    if reason:
        result["reason"] = reason

    return result


def build_not_applicable_metric(
    metric_name: str,
    reason: str,
):
    return {
        "metric": metric_name,
        "status": "NOT_APPLICABLE",
        "value": None,
        "reason": reason,
    }


def build_metric_result(
    metric_name: str,
    namespace: str,
    concept: str,
    observation: dict,
):
    result = {
        "metric": metric_name,
        "status": "OK",
        "namespace": namespace,
        "concept": concept,
        "unit": observation.get("unit"),
        "value": observation.get("val"),
        "period_start": observation.get("start"),
        "period_end": observation.get("end"),
        "filing_date": observation.get("filed"),
        "form": observation.get("form"),
        "fy": observation.get("fy"),
        "fp": observation.get("fp"),
        "frame": observation.get("frame"),
        "accession": observation.get("accn"),
    }

    result.update(
        get_metric_semantics(
            metric_name,
            concept,
        )
    )

    return result


def build_instant_result(
    metric_name: str,
    namespace: str,
    concept: str,
    observation: dict,
):
    result = {
        "metric": metric_name,
        "status": "OK",
        "namespace": namespace,
        "concept": concept,
        "unit": observation.get("unit"),
        "value": observation.get("val"),
        "period_end": observation.get("end"),
        "filing_date": observation.get("filed"),
        "form": observation.get("form"),
        "fy": observation.get("fy"),
        "fp": observation.get("fp"),
        "frame": observation.get("frame"),
        "accession": observation.get("accn"),
    }

    result.update(
        get_metric_semantics(
            metric_name,
            concept,
        )
    )

    return result


# ============================================================
# METRIC EXTRACTION
# ============================================================

def extract_metric(
    data: dict,
    metric_name: str,
    concept_candidates: list,
    namespaces=("us-gaap",),
):
    result = find_best_annual_concept(
        data,
        concept_candidates,
        namespaces,
    )

    if result is None:
        return build_missing_metric(metric_name)

    return build_metric_result(
        metric_name,
        result["namespace"],
        result["concept"],
        result["observation"],
    )


def extract_instant_metric(
    data: dict,
    metric_name: str,
    concept_candidates: list,
    namespaces=("us-gaap", "dei"),
):
    result = find_best_instant_concept(
        data,
        concept_candidates,
        namespaces,
    )

    if result is None:
        return build_missing_metric(metric_name)

    return build_instant_result(
        metric_name,
        result["namespace"],
        result["concept"],
        result["observation"],
    )


# ============================================================
# TOTAL DEBT
# ============================================================

def calculate_total_debt(
    current_debt: dict,
    noncurrent_debt: dict,
    company_type: str,
):
    if company_type == "FINANCIAL":
        return {
            "metric": "total_debt",
            "status": "NOT_APPLICABLE",
            "value": None,
            "reason": (
                "Generic EV debt calculation is not "
                "appropriate for financial companies."
            ),
            "components": {
                "current_debt": current_debt,
                "noncurrent_debt": noncurrent_debt,
            },
        }

    result = {
        "metric": "total_debt",
        "status": "INCOMPLETE",
        "value": None,
        "components": {
            "current_debt": current_debt,
            "noncurrent_debt": noncurrent_debt,
        },
    }

    current_value = current_debt.get("value")
    noncurrent_value = noncurrent_debt.get("value")

    if (
        current_debt.get("status") == "OK"
        and noncurrent_debt.get("status") == "OK"
        and _is_number(current_value)
        and _is_number(noncurrent_value)
    ):
        result["value"] = current_value + noncurrent_value
        result["status"] = "OK"

    return result


# ============================================================
# COMPANY NORMALIZATION
# ============================================================

def normalize_company(
    ticker: str,
    data: dict,
    company_type: str,
):
    validate_companyfacts(data)

    revenue = extract_metric(
        data,
        "revenue",
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ],
    )

    net_income = extract_metric(
        data,
        "net_income",
        [
            "NetIncomeLoss",
            "ProfitLoss",
        ],
    )

    diluted_eps = extract_metric(
        data,
        "diluted_eps",
        [
            "EarningsPerShareDiluted",
        ],
    )

    if company_type == "FINANCIAL":
        operating_income = build_not_applicable_metric(
            "operating_income",
            (
                "Generic operating income framework "
                "is not used for financial companies."
            ),
        )
    else:
        operating_income = extract_metric(
            data,
            "operating_income",
            [
                "OperatingIncomeLoss",
            ],
        )

    if company_type == "FINANCIAL":
        cfo = build_not_applicable_metric(
            "cfo",
            (
                "Generic CFO/FCF framework "
                "is not used for financial companies."
            ),
        )
    else:
        cfo = extract_metric(
            data,
            "cfo",
            [
                "NetCashProvidedByUsedInOperatingActivities",
            ],
        )

    if company_type == "FINANCIAL":
        capex = build_not_applicable_metric(
            "capex",
            (
                "Generic CapEx/FCF framework "
                "is not used for financial companies."
            ),
        )
    else:
        capex = extract_metric(
            data,
            "capex",
            [
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "PaymentsToAcquireOtherPropertyPlantAndEquipment",
            ],
        )

    cash = extract_instant_metric(
        data,
        "cash",
        [
            "CashAndCashEquivalentsAtCarryingValue",
        ],
    )

    if company_type == "FINANCIAL":
        current_debt = build_not_applicable_metric(
            "current_debt",
            (
                "Generic industrial debt framework "
                "is not used for financial companies."
            ),
        )
    else:
        current_debt = extract_instant_metric(
            data,
            "current_debt",
            [
                "LongTermDebtCurrent",
                "ShortTermBorrowings",
                "ShortTermDebt",
                "CurrentDebt",
            ],
        )

    if company_type == "FINANCIAL":
        noncurrent_debt = build_not_applicable_metric(
            "noncurrent_debt",
            (
                "Generic industrial debt framework "
                "is not used for financial companies."
            ),
        )
    else:
        noncurrent_debt = extract_instant_metric(
            data,
            "noncurrent_debt",
            [
                "LongTermDebtNoncurrent",
                # Fallback migrated from src/fundamentals/sec_historical.py's
                # CONCEPT_MAP (2026-09-28, Framework v2.1 Architecture v5,
                # implementation step 1 -- "ORCL fallback migration").
                # Confirmed against real SEC data (2026-09-18 investigation):
                # Oracle (CIK 0001341439) has zero observations under
                # LongTermDebtNoncurrent (404 from data.sec.gov) and instead
                # tags its noncurrent debt under LongTermNotesPayable (86 real
                # observations spanning 2009-2026, e.g. $122.3B as of
                # FY2026-05-31, filed 2026-06-22, form 10-K). Before this
                # migration, this candidate list -- the one actually used by
                # the production path (point_in_time.py -> sec_normalizer.
                # find_concept / normalize_company) -- did not have this
                # fallback; only the non-production sec_historical.py did.
                "LongTermNotesPayable",
            ],
        )

    shares = extract_instant_metric(
        data,
        "shares_outstanding",
        [
            "EntityCommonStockSharesOutstanding",
            "CommonStockSharesOutstanding",
        ],
        namespaces=(
            "dei",
            "us-gaap",
        ),
    )

    # ========================================================
    # STOCKHOLDERS' EQUITY
    # ========================================================

    stockholders_equity = extract_instant_metric(
        data,
        "stockholders_equity",
        [
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ],
        namespaces=(
            "us-gaap",
        ),
    )

    total_debt = calculate_total_debt(
        current_debt,
        noncurrent_debt,
        company_type,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "entity_name": data.get("entityName"),
        "cik": data.get("cik"),
        "company_type": company_type,
        "source": {
            "provider": "SEC",
            "dataset": "Company Facts",
        },
        "normalized_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "metrics": {
            "revenue": revenue,
            "net_income": net_income,
            "diluted_eps": diluted_eps,
            "operating_income": operating_income,
            "cfo": cfo,
            "capex": capex,
            "cash": cash,
            "current_debt": current_debt,
            "noncurrent_debt": noncurrent_debt,
            "total_debt": total_debt,
            "shares_outstanding": shares,
            "stockholders_equity": stockholders_equity,
        },
    }


# ============================================================
# NORMALIZED DATA VALIDATION
# ============================================================

def validate_normalized_data(normalized: dict) -> list:
    required_top_level = [
        "schema_version",
        "ticker",
        "entity_name",
        "cik",
        "company_type",
        "metrics",
    ]

    for key in required_top_level:
        if key not in normalized:
            raise ValueError(f"Missing normalized key: {key}")

    company_type = normalized["company_type"]

    if company_type == "NON_FINANCIAL":
        required_metrics = [
            "revenue",
            "net_income",
            "diluted_eps",
            "operating_income",
            "cfo",
            "capex",
            "cash",
            "shares_outstanding",
        ]
    elif company_type == "FINANCIAL":
        required_metrics = [
            "revenue",
            "net_income",
            "diluted_eps",
            "cash",
            "shares_outstanding",
        ]
    else:
        raise ValueError(
            f"Unknown company_type: {company_type}"
        )

    failures = []

    for metric in required_metrics:
        metric_data = normalized["metrics"].get(metric)

        if not isinstance(metric_data, dict):
            failures.append(metric)
            continue

        if metric_data.get("status") != "OK":
            failures.append(metric)

    total_debt = normalized["metrics"].get("total_debt")

    if not isinstance(total_debt, dict):
        failures.append("total_debt")
    elif company_type == "NON_FINANCIAL":
        if total_debt.get("status") != "OK":
            failures.append("total_debt")
    elif company_type == "FINANCIAL":
        if total_debt.get("status") != "NOT_APPLICABLE":
            failures.append("total_debt")

    stockholders_equity = normalized["metrics"].get(
        "stockholders_equity"
    )

    if isinstance(stockholders_equity, dict):
        if stockholders_equity.get("status") == "OK":
            if (
                "equity_scope" not in stockholders_equity
                or "includes_noncontrolling_interest"
                not in stockholders_equity
                or "semantic_status" not in stockholders_equity
            ):
                failures.append("stockholders_equity_semantics")

    return failures


# ============================================================
# FILE HELPERS
# ============================================================

def load_raw_companyfacts(
    ticker: str,
    raw_dir: Path = RAW_DIR,
) -> dict:
    path = raw_dir / f"{ticker}_companyfacts.json"

    if not path.exists():
        raise FileNotFoundError(
            f"Raw SEC file not found: {path}"
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_normalized_data(
    ticker: str,
    normalized: dict,
    processed_dir: Path = PROCESSED_DIR,
) -> Path:
    processed_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = processed_dir / f"{ticker}_fundamentals.json"

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            normalized,
            f,
            ensure_ascii=False,
            indent=2,
        )

    return path


# ============================================================
# NORMALIZE FROM CACHE
# ============================================================

def normalize_from_cache(
    ticker: str,
    company_type: str,
    raw_dir: Path = RAW_DIR,
    processed_dir: Path = PROCESSED_DIR,
):
    data = load_raw_companyfacts(
        ticker,
        raw_dir,
    )

    normalized = normalize_company(
        ticker,
        data,
        company_type,
    )

    failures = validate_normalized_data(
        normalized
    )

    output_path = save_normalized_data(
        ticker,
        normalized,
        processed_dir,
    )

    return {
        "ticker": ticker,
        "output_path": str(output_path),
        "failures": failures,
        "normalized": normalized,
    }


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Normalize SEC Company Facts "
            "into the Stock Master "
            "fundamental schema."
        )
    )

    parser.add_argument(
        "--ticker",
        required=True,
        help="Ticker symbol, e.g. NVDA",
    )

    parser.add_argument(
        "--company-type",
        default="NON_FINANCIAL",
        choices=[
            "NON_FINANCIAL",
            "FINANCIAL",
        ],
    )

    args = parser.parse_args()

    result = normalize_from_cache(
        ticker=args.ticker,
        company_type=args.company_type,
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )
