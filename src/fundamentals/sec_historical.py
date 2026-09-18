import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

SCHEMA_VERSION = "sec_historical_v0.1"

RAW_DIR = Path("data/raw/sec")
PROCESSED_DIR = Path("data/processed/fundamentals")


# ============================================================
# CONCEPT MAP
# ============================================================

CONCEPT_MAP = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "Revenues"),
    ],
    "net_income": [
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
    ],
    "diluted_eps": [
        ("us-gaap", "EarningsPerShareDiluted"),
    ],
    "operating_income": [
        ("us-gaap", "OperatingIncomeLoss"),
    ],
    "cfo": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
    ],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("us-gaap", "PaymentsToAcquireOtherPropertyPlantAndEquipment"),
    ],
    "cash": [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
    ],
    "current_debt": [
        ("us-gaap", "DebtCurrent"),
        ("us-gaap", "LongTermDebtCurrent"),
        ("us-gaap", "ShortTermBorrowings"),
        ("us-gaap", "ShortTermDebt"),
        ("us-gaap", "CurrentDebt"),
    ],
    "noncurrent_debt": [
        ("us-gaap", "LongTermDebtNoncurrent"),
        # Fallback confirmed against real SEC data: Oracle
        # (CIK 0001341439) has zero observations under
        # LongTermDebtNoncurrent (404 from data.sec.gov) and
        # instead tags its noncurrent debt under LongTermNotesPayable
        # (86 real observations spanning 2009-2026, e.g. $122.3B as
        # of FY2026-05-31, filed 2026-06-22, form 10-K).
        ("us-gaap", "LongTermNotesPayable"),
    ],
    "shares_outstanding": [
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),
    ],
}


# ============================================================
# METRIC GROUPS
# ============================================================

DURATION_METRICS = [
    "revenue",
    "net_income",
    "diluted_eps",
    "operating_income",
    "cfo",
    "capex",
]

INSTANT_METRICS = [
    "cash",
    "current_debt",
    "noncurrent_debt",
    "shares_outstanding",
]


# EPS must NOT be reconstructed by subtracting YTD values.
# EPS is therefore direct-quarter-only in v0.1.
RECONSTRUCTABLE_QUARTERLY_METRICS = [
    "revenue",
    "net_income",
    "operating_income",
    "cfo",
    "capex",
]


# ============================================================
# BASIC HELPERS
# ============================================================

def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_string(value):
    if value is None:
        return None

    if isinstance(value, str):
        return value[:10]

    return str(value)[:10]


def _parse_date(value):
    value = _date_string(value)

    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _day_after(value):
    parsed = _parse_date(value)

    if parsed is None:
        return None

    return (parsed + timedelta(days=1)).isoformat()


def _duration_days(observation):
    start = _parse_date(observation.get("start"))
    end = _parse_date(observation.get("end"))

    if start is None or end is None:
        return None

    return (end - start).days + 1


def _is_duration(observation):
    return (
        bool(observation.get("start"))
        and bool(observation.get("end"))
    )


def _is_instant(observation):
    return (
        bool(observation.get("end"))
        and not bool(observation.get("start"))
    )


def _observation_sort_key(observation):
    return (
        _date_string(observation.get("filed")) or "",
        _date_string(observation.get("end")) or "",
        _date_string(observation.get("start")) or "",
        observation.get("accn") or "",
    )


# ============================================================
# RAW DATA HELPERS
# ============================================================

def validate_companyfacts(data):
    if not isinstance(data, dict):
        raise ValueError("Company Facts payload must be a dict.")

    required = ["entityName", "facts"]

    for key in required:
        if key not in data:
            raise ValueError(
                f"Company Facts payload missing required key: {key}"
            )

    if not isinstance(data["facts"], dict):
        raise ValueError("Company Facts 'facts' must be a dict.")

    return True


def get_concept_data(data, namespace, concept):
    validate_companyfacts(data)

    namespace_data = data.get("facts", {}).get(namespace, {})

    if not isinstance(namespace_data, dict):
        return None

    return namespace_data.get(concept)


def get_observations(concept_data):
    if not concept_data:
        return []

    units = concept_data.get("units", {})

    if not isinstance(units, dict):
        return []

    observations = []

    for unit, records in units.items():
        if not isinstance(records, list):
            continue

        for observation in records:
            if not isinstance(observation, dict):
                continue

            record = dict(observation)
            record["unit"] = unit

            observations.append(record)

    return observations


# ============================================================
# OBSERVATION FILTERING
# ============================================================

def _is_usable_duration_observation(observation):
    if not _is_duration(observation):
        return False

    if not _is_number(observation.get("val")):
        return False

    if not observation.get("end"):
        return False

    return True


def _is_usable_instant_observation(observation):
    if not _is_instant(observation):
        return False

    if not _is_number(observation.get("val")):
        return False

    if not observation.get("end"):
        return False

    return True


def _sort_observations(observations):
    return sorted(
        observations,
        key=_observation_sort_key,
        reverse=True,
    )


def _deduplicate_period_observations(observations, instant=False):
    """
    Keep the latest filed observation for the same economic period.

    Supports both:

        1. Raw SEC observations
           start / end

        2. Built historical records
           period_start / period_end

    Duration identity:
        period_start + period_end + fy + fp

    Instant identity:
        period_end
    """

    selected = {}

    for observation in observations:

        # ----------------------------------------------------
        # Determine period identity
        # ----------------------------------------------------

        if instant:
            period_end = (
                observation.get("end")
                if observation.get("end") is not None
                else observation.get("period_end")
            )

            identity = (
                _date_string(period_end),
            )

        else:
            period_start = (
                observation.get("start")
                if observation.get("start") is not None
                else observation.get("period_start")
            )

            period_end = (
                observation.get("end")
                if observation.get("end") is not None
                else observation.get("period_end")
            )

            identity = (
                _date_string(period_start),
                _date_string(period_end),
                observation.get("fy"),
                observation.get("fp"),
            )

        existing = selected.get(identity)

        if existing is None:
            selected[identity] = observation
            continue

        # ----------------------------------------------------
        # Latest filing wins
        # ----------------------------------------------------

        current_filed = (
            _date_string(
                observation.get("filed")
                if observation.get("filed") is not None
                else observation.get("filing_date")
            )
            or ""
        )

        existing_filed = (
            _date_string(
                existing.get("filed")
                if existing.get("filed") is not None
                else existing.get("filing_date")
            )
            or ""
        )

        if current_filed > existing_filed:
            selected[identity] = observation

        elif current_filed == existing_filed:

            current_accn = (
                observation.get("accn")
                if observation.get("accn") is not None
                else observation.get("accession")
                or ""
            )

            existing_accn = (
                existing.get("accn")
                if existing.get("accn") is not None
                else existing.get("accession")
                or ""
            )

            if current_accn > existing_accn:
                selected[identity] = observation

    return list(selected.values())


# ============================================================
# CONCEPT SELECTION
# ============================================================

def find_best_concept(
    data,
    metric_name,
    duration=False,
    instant=False,
):
    candidates = CONCEPT_MAP.get(
        metric_name,
        [],
    )

    for namespace, concept in candidates:

        concept_data = get_concept_data(
            data,
            namespace,
            concept,
        )

        observations = get_observations(
            concept_data
        )

        if duration:
            usable = [
                obs
                for obs in observations
                if _is_usable_duration_observation(obs)
            ]

        elif instant:
            usable = [
                obs
                for obs in observations
                if _is_usable_instant_observation(obs)
            ]

        else:
            usable = observations

        if usable:
            return {
                "namespace": namespace,
                "concept": concept,
                "concept_data": concept_data,
                "observations": usable,
            }

    return None


# ============================================================
# RECORD BUILDERS
# ============================================================

def build_record(
    observation,
    metric,
    period_type,
    quarter=None,
    derived=False,
    derivation=None,
    source_observations=None,
    period_start=None,
    period_end=None,
):
    record = {
        "period_type": period_type,
        "quarter": quarter,
        "fy": observation.get("fy"),
        "period_start": (
            _date_string(period_start)
            if period_start is not None
            else _date_string(observation.get("start"))
        ),
        "period_end": (
            _date_string(period_end)
            if period_end is not None
            else _date_string(observation.get("end"))
        ),
        "value": observation.get("val"),
        "unit": observation.get("unit"),
        "namespace": observation.get("_namespace"),
        "concept": observation.get("_concept"),
        "filing_date": _date_string(
            observation.get("filed")
        ),
        "form": observation.get("form"),
        "fp": observation.get("fp"),
        "frame": observation.get("frame"),
        "accession": observation.get("accn"),
        "derived": derived,
    }

    if derivation is not None:
        record["derivation"] = derivation

    if source_observations is not None:
        record["source_observations"] = source_observations

    return record


def _attach_concept_metadata(
    observations,
    namespace,
    concept,
):
    result = []

    for observation in observations:
        item = dict(observation)
        item["_namespace"] = namespace
        item["_concept"] = concept
        result.append(item)

    return result


# ============================================================
# ANNUAL EXTRACTION
# ============================================================

def extract_annual_history(
    data,
    metric_name,
):
    selected = find_best_concept(
        data,
        metric_name,
        duration=True,
    )

    if selected is None:
        return {
            "status": "MISSING",
            "metric": metric_name,
            "annual": [],
            "reason": "No usable annual concept found.",
        }

    observations = _attach_concept_metadata(
        selected["observations"],
        selected["namespace"],
        selected["concept"],
    )

    annual = []

    for observation in observations:

        if observation.get("form") != "10-K":
            continue

        if observation.get("fp") != "FY":
            continue

        days = _duration_days(
            observation
        )

        # Annual duration should normally be ~9-15 months.
        if days is None or days < 300:
            continue

        annual.append(
            build_record(
                observation=observation,
                metric=metric_name,
                period_type="annual",
            )
        )

    annual = _deduplicate_period_observations(
        annual,
        instant=False,
    )

    annual.sort(
        key=lambda x: (
            x.get("period_end") or "",
            x.get("filing_date") or "",
        )
    )

    if not annual:
        return {
            "status": "MISSING",
            "metric": metric_name,
            "annual": [],
            "reason": (
                "No usable 10-K FY annual observations found."
            ),
        }

    return {
        "status": "OK",
        "metric": metric_name,
        "namespace": selected["namespace"],
        "concept": selected["concept"],
        "annual": annual,
    }


# ============================================================
# INSTANT EXTRACTION
# ============================================================

def extract_instant_history(
    data,
    metric_name,
):
    selected = find_best_concept(
        data,
        metric_name,
        instant=True,
    )

    if selected is None:
        return {
            "status": "MISSING",
            "metric": metric_name,
            "instant": [],
            "reason": "No usable instant concept found.",
        }

    observations = _attach_concept_metadata(
        selected["observations"],
        selected["namespace"],
        selected["concept"],
    )

    observations = _deduplicate_period_observations(
        observations,
        instant=True,
    )

    records = []

    for observation in observations:
        records.append(
            build_record(
                observation=observation,
                metric=metric_name,
                period_type="instant",
            )
        )

    records.sort(
        key=lambda x: (
            x.get("period_end") or "",
            x.get("filing_date") or "",
        )
    )

    return {
        "status": "OK",
        "metric": metric_name,
        "namespace": selected["namespace"],
        "concept": selected["concept"],
        "instant": records,
    }


# ============================================================
# QUARTER CLASSIFICATION
# ============================================================

def _is_standalone_quarter(observation):
    days = _duration_days(
        observation
    )

    if days is None:
        return False

    # Normal fiscal quarter: roughly 70-120 days.
    return 70 <= days <= 120


def _is_ytd_observation(observation):
    days = _duration_days(
        observation
    )

    if days is None:
        return False

    # YTD periods are generally > 1 quarter and < 1 year.
    return 120 < days < 320


def _select_latest(records):
    if not records:
        return None

    return sorted(
        records,
        key=lambda x: (
            _date_string(x.get("filed")) or "",
            _date_string(x.get("end")) or "",
            x.get("accn") or "",
        ),
        reverse=True,
    )[0]


# ============================================================
# QUARTER SELECTION
# ============================================================

def _select_direct_quarter(
    observations,
    quarter,
):
    """
    Select a direct standalone quarterly observation.

    Q1 / Q2 / Q3:
        fp must match the requested quarter.

    Q4:
        SEC Company Facts may represent a standalone Q4
        observation with fp="FY" rather than fp="Q4".

        Therefore Q4 accepts:
            - fp="Q4"
            - fp="FY"

        as long as the observation itself is a standalone
        quarterly duration (70-120 days).

    This function never reconstructs a quarter.
    """

    candidates = []

    for observation in observations:

        if not _is_standalone_quarter(
            observation
        ):
            continue

        fp = observation.get("fp")

        if quarter in (
            "Q1",
            "Q2",
            "Q3",
        ):
            if fp != quarter:
                continue

        elif quarter == "Q4":
            if fp not in (
                "Q4",
                "FY",
            ):
                continue

        else:
            continue

        candidates.append(
            observation
        )

    return _select_latest(
        candidates
    )


def _select_ytd(
    observations,
    quarter,
):
    """
    Select cumulative YTD observation.

    Q1 is generally standalone and is not reconstructed.
    Q2 uses H1.
    Q3 uses 9M.
    """

    candidates = []

    for observation in observations:

        if not _is_ytd_observation(
            observation
        ):
            continue

        fp = observation.get("fp")

        if quarter == "Q2":

            if fp == "Q2":
                candidates.append(
                    observation
                )

        elif quarter == "Q3":

            if fp == "Q3":
                candidates.append(
                    observation
                )

    return _select_latest(
        candidates
    )


def _select_q1(observations):
    return _select_direct_quarter(
        observations,
        "Q1",
    )


def _select_q2(observations):
    return _select_direct_quarter(
        observations,
        "Q2",
    )


def _select_q3(observations):
    return _select_direct_quarter(
        observations,
        "Q3",
    )


def _select_fy(observations):
    candidates = []

    for observation in observations:

        if observation.get("form") != "10-K":
            continue

        if observation.get("fp") != "FY":
            continue

        days = _duration_days(
            observation
        )

        if days is None or days < 300:
            continue

        candidates.append(
            observation
        )

    return _select_latest(
        candidates
    )


# ============================================================
# SOURCE RECORD
# ============================================================

def _source_record(
    observation,
):
    return {
        "period_start": _date_string(
            observation.get("start")
        ),
        "period_end": _date_string(
            observation.get("end")
        ),
        "value": observation.get("val"),
        "unit": observation.get("unit"),
        "filing_date": _date_string(
            observation.get("filed")
        ),
        "form": observation.get("form"),
        "fy": observation.get("fy"),
        "fp": observation.get("fp"),
        "frame": observation.get("frame"),
        "accession": observation.get("accn"),
        "namespace": observation.get("_namespace"),
        "concept": observation.get("_concept"),
    }


# ============================================================
# QUARTER DERIVATION
# ============================================================

def _build_derived_quarter(
    metric_name,
    quarter,
    current_observation,
    subtract_observation,
    period_start,
    period_end,
):
    """
    Reconstruct a standalone quarter:

        current cumulative value
        -
        previous cumulative value

    Example:

        Q2 = H1 - Q1
        Q3 = 9M - H1
        Q4 = FY - 9M

    This function must NOT be used for EPS.
    """

    if current_observation is None:
        return None

    if subtract_observation is None:
        return None

    current_value = (
        current_observation.get("val")
    )

    subtract_value = (
        subtract_observation.get("val")
    )

    if not _is_number(
        current_value
    ):
        return None

    if not _is_number(
        subtract_value
    ):
        return None

    current_unit = (
        current_observation.get("unit")
    )

    subtract_unit = (
        subtract_observation.get("unit")
    )

    if current_unit != subtract_unit:
        return None

    value = (
        current_value
        - subtract_value
    )

    record = build_record(
        observation=current_observation,
        metric=metric_name,
        period_type="quarterly",
        quarter=quarter,
        derived=True,
        derivation=(
            f"{quarter} = "
            f"{current_observation.get('fp') or 'current'} "
            f"- "
            f"{subtract_observation.get('fp') or 'previous'}"
        ),
        source_observations=[
            _source_record(
                current_observation
            ),
            _source_record(
                subtract_observation
            ),
        ],
        period_start=period_start,
        period_end=period_end,
    )

    record["value"] = value

    return record


# ============================================================
# QUARTERLY EXTRACTION
# ============================================================

def extract_quarterly_history(
    data,
    metric_name,
):
    selected = find_best_concept(
        data,
        metric_name,
        duration=True,
    )

    if selected is None:

        if metric_name == "diluted_eps":
            return {
                "status": "MISSING",
                "metric": metric_name,
                "quarterly": [],
                "reason": (
                    "No usable diluted EPS concept found. "
                    "EPS reconstruction is disabled "
                    "in v0.1."
                ),
            }

        return {
            "status": "MISSING",
            "metric": metric_name,
            "quarterly": [],
            "reason": "No usable duration concept found.",
        }

    observations = _attach_concept_metadata(
        selected["observations"],
        selected["namespace"],
        selected["concept"],
    )

    # --------------------------------------------------------
    # EPS SPECIAL CASE
    # --------------------------------------------------------

    # EPS cannot be reconstructed by:
    #
    #   H1 EPS - Q1 EPS
    #
    # because EPS is a ratio/per-share metric,
    # not an additive cumulative flow.
    #
    # Therefore v0.1 accepts direct standalone
    # quarterly EPS only.

    if metric_name == "diluted_eps":

        quarters = []

        for quarter in (
            "Q1",
            "Q2",
            "Q3",
            "Q4",
        ):

            direct = _select_direct_quarter(
                observations,
                quarter,
            )

            if direct is None:
                continue

            quarters.append(
                build_record(
                    observation=direct,
                    metric=metric_name,
                    period_type="quarterly",
                    quarter=quarter,
                    derived=False,
                )
            )

        quarters.sort(
            key=lambda x: (
                x.get("period_end") or "",
                x.get("filing_date") or "",
            )
        )

        if not quarters:
            return {
                "status": "MISSING",
                "metric": metric_name,
                "quarterly": [],
                "reason": (
                    "No direct standalone quarterly EPS "
                    "observations were available. "
                    "EPS reconstruction is disabled "
                    "in v0.1."
                ),
            }

        return {
            "status": "OK",
            "metric": metric_name,
            "namespace": selected["namespace"],
            "concept": selected["concept"],
            "quarterly": quarters,
        }

    # --------------------------------------------------------
    # NORMAL DURATION METRICS
    # --------------------------------------------------------

    quarterly = []

    # --------------------------------------------------------
    # Q1
    # --------------------------------------------------------

    q1 = _select_q1(
        observations
    )

    if q1 is not None:

        quarterly.append(
            build_record(
                observation=q1,
                metric=metric_name,
                period_type="quarterly",
                quarter="Q1",
                derived=False,
            )
        )

    # --------------------------------------------------------
    # Q2
    # --------------------------------------------------------

    q2_direct = _select_q2(
        observations
    )

    if q2_direct is not None:

        quarterly.append(
            build_record(
                observation=q2_direct,
                metric=metric_name,
                period_type="quarterly",
                quarter="Q2",
                derived=False,
            )
        )

    elif metric_name in RECONSTRUCTABLE_QUARTERLY_METRICS:

        q2_ytd = _select_ytd(
            observations,
            "Q2",
        )

        if (
            q2_ytd is not None
            and q1 is not None
        ):

            period_start = _day_after(
                q1.get("end")
            )

            period_end = _date_string(
                q2_ytd.get("end")
            )

            derived_q2 = _build_derived_quarter(
                metric_name=metric_name,
                quarter="Q2",
                current_observation=q2_ytd,
                subtract_observation=q1,
                period_start=period_start,
                period_end=period_end,
            )

            if derived_q2 is not None:
                quarterly.append(
                    derived_q2
                )

    # --------------------------------------------------------
    # Q3
    # --------------------------------------------------------

    q3_direct = _select_q3(
        observations
    )

    if q3_direct is not None:

        quarterly.append(
            build_record(
                observation=q3_direct,
                metric=metric_name,
                period_type="quarterly",
                quarter="Q3",
                derived=False,
            )
        )

    elif metric_name in RECONSTRUCTABLE_QUARTERLY_METRICS:

        q3_ytd = _select_ytd(
            observations,
            "Q3",
        )

        # H1 observation is needed for Q3 reconstruction.
        q2_ytd = _select_ytd(
            observations,
            "Q2",
        )

        if (
            q3_ytd is not None
            and q2_ytd is not None
        ):

            period_start = _day_after(
                q2_ytd.get("end")
            )

            period_end = _date_string(
                q3_ytd.get("end")
            )

            derived_q3 = _build_derived_quarter(
                metric_name=metric_name,
                quarter="Q3",
                current_observation=q3_ytd,
                subtract_observation=q2_ytd,
                period_start=period_start,
                period_end=period_end,
            )

            if derived_q3 is not None:
                quarterly.append(
                    derived_q3
                )

    # --------------------------------------------------------
    # Q4
    # --------------------------------------------------------

    fy = _select_fy(
        observations
    )

    if (
        fy is not None
        and metric_name in RECONSTRUCTABLE_QUARTERLY_METRICS
    ):

        q3_ytd = _select_ytd(
            observations,
            "Q3",
        )

        if q3_ytd is not None:

            period_start = _day_after(
                q3_ytd.get("end")
            )

            period_end = _date_string(
                fy.get("end")
            )

            derived_q4 = _build_derived_quarter(
                metric_name=metric_name,
                quarter="Q4",
                current_observation=fy,
                subtract_observation=q3_ytd,
                period_start=period_start,
                period_end=period_end,
            )

            if derived_q4 is not None:
                quarterly.append(
                    derived_q4
                )

    # --------------------------------------------------------
    # Deduplicate quarterly records
    # --------------------------------------------------------

    unique = {}

    for record in quarterly:

        identity = (
            record.get("fy"),
            record.get("quarter"),
            record.get("period_end"),
        )

        existing = unique.get(
            identity
        )

        if existing is None:
            unique[identity] = record
            continue

        # Prefer direct SEC quarterly observation
        # over derived.
        existing_derived = existing.get(
            "derived",
            False,
        )

        current_derived = record.get(
            "derived",
            False,
        )

        if (
            existing_derived
            and not current_derived
        ):
            unique[identity] = record

    quarterly = list(
        unique.values()
    )

    quarterly.sort(
        key=lambda x: (
            x.get("period_end") or "",
            x.get("quarter") or "",
        )
    )

    if not quarterly:
        return {
            "status": "MISSING",
            "metric": metric_name,
            "quarterly": [],
            "reason": (
                "No usable quarterly observations found."
            ),
        }

    return {
        "status": "OK",
        "metric": metric_name,
        "namespace": selected["namespace"],
        "concept": selected["concept"],
        "quarterly": quarterly,
    }


# ============================================================
# FULL HISTORICAL EXTRACTION
# ============================================================

def extract_historical_data(
    ticker,
    data,
    company_type="NON_FINANCIAL",
):
    validate_companyfacts(
        data
    )

    history = {}

    for metric in DURATION_METRICS:

        annual = extract_annual_history(
            data,
            metric,
        )

        quarterly = extract_quarterly_history(
            data,
            metric,
        )

        history[metric] = {
            "annual": annual.get(
                "annual",
                [],
            ),
            "quarterly": quarterly.get(
                "quarterly",
                [],
            ),
            "status": (
                "OK"
                if (
                    annual.get("status")
                    == "OK"
                    or quarterly.get("status")
                    == "OK"
                )
                else "MISSING"
            ),
        }

        if annual.get(
            "namespace"
        ):
            history[metric][
                "namespace"
            ] = annual[
                "namespace"
            ]

        if annual.get(
            "concept"
        ):
            history[metric][
                "concept"
            ] = annual[
                "concept"
            ]

        if quarterly.get(
            "reason"
        ):
            history[metric][
                "quarterly_reason"
            ] = quarterly[
                "reason"
            ]

    for metric in INSTANT_METRICS:

        instant = extract_instant_history(
            data,
            metric,
        )

        history[metric] = {
            "instant": instant.get(
                "instant",
                [],
            ),
            "status": instant.get(
                "status"
            ),
        }

        if instant.get(
            "namespace"
        ):
            history[metric][
                "namespace"
            ] = instant[
                "namespace"
            ]

        if instant.get(
            "concept"
        ):
            history[metric][
                "concept"
            ] = instant[
                "concept"
            ]

        if instant.get(
            "reason"
        ):
            history[metric][
                "reason"
            ] = instant[
                "reason"
            ]

    return {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "entity_name": data.get(
            "entityName"
        ),
        "cik": data.get(
            "cik"
        ),
        "company_type": company_type,
        "source": {
            "provider": "SEC",
            "dataset": "Company Facts",
        },
        "extracted_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "history": history,
    }


# ============================================================
# FILE HELPERS
# ============================================================

def load_raw_companyfacts(
    ticker,
    raw_dir=RAW_DIR,
):
    path = (
        Path(raw_dir)
        / f"{ticker}_companyfacts.json"
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Raw Company Facts file not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    validate_companyfacts(
        data
    )

    return data


def save_historical_data(
    ticker,
    historical_data,
    processed_dir=PROCESSED_DIR,
):
    processed_dir = Path(
        processed_dir
    )

    processed_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = (
        processed_dir
        / f"{ticker}_historical.json"
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            historical_data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    return path


def extract_from_cache(
    ticker,
    company_type="NON_FINANCIAL",
    raw_dir=RAW_DIR,
    processed_dir=PROCESSED_DIR,
):
    raw_data = load_raw_companyfacts(
        ticker=ticker,
        raw_dir=raw_dir,
    )

    historical_data = extract_historical_data(
        ticker=ticker,
        data=raw_data,
        company_type=company_type,
    )

    validate_historical_data(
        historical_data
    )

    save_historical_data(
        ticker=ticker,
        historical_data=historical_data,
        processed_dir=processed_dir,
    )

    return historical_data


# ============================================================
# VALIDATION
# ============================================================

def validate_historical_data(
    data,
):
    if not isinstance(
        data,
        dict,
    ):
        raise ValueError(
            "Historical data must be a dict."
        )

    if data.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        raise ValueError(
            "Unexpected historical schema version."
        )

    required_top_level = [
        "ticker",
        "entity_name",
        "cik",
        "company_type",
        "source",
        "history",
    ]

    for key in required_top_level:

        if key not in data:
            raise ValueError(
                f"Missing required historical key: {key}"
            )

    if not isinstance(
        data["history"],
        dict,
    ):
        raise ValueError(
            "Historical 'history' must be a dict."
        )

    for metric in DURATION_METRICS:

        if metric not in data["history"]:
            raise ValueError(
                f"Missing duration metric history: {metric}"
            )

        metric_data = data[
            "history"
        ][metric]

        if not isinstance(
            metric_data,
            dict,
        ):
            raise ValueError(
                f"Invalid history object: {metric}"
            )

        annual = metric_data.get(
            "annual",
            [],
        )

        quarterly = metric_data.get(
            "quarterly",
            [],
        )

        if not isinstance(
            annual,
            list,
        ):
            raise ValueError(
                f"{metric}.annual must be a list."
            )

        if not isinstance(
            quarterly,
            list,
        ):
            raise ValueError(
                f"{metric}.quarterly must be a list."
            )

        for record in (
            annual + quarterly
        ):

            if not isinstance(
                record,
                dict,
            ):
                raise ValueError(
                    f"Invalid {metric} history record."
                )

            if record.get(
                "period_end"
            ) is None:
                raise ValueError(
                    f"{metric} record missing period_end."
                )

            if record.get(
                "value"
            ) is None:
                raise ValueError(
                    f"{metric} record missing value."
                )

            if record.get(
                "period_type"
            ) == "quarterly":

                if record.get(
                    "quarter"
                ) not in {
                    "Q1",
                    "Q2",
                    "Q3",
                    "Q4",
                }:
                    raise ValueError(
                        f"{metric} quarterly record "
                        f"has invalid quarter."
                    )

                if record.get(
                    "period_start"
                ) is None:
                    raise ValueError(
                        f"{metric} quarterly record "
                        f"missing period_start."
                    )

                # Derived quarterly records must contain provenance.
                if record.get(
                    "derived"
                ) is True:

                    if not record.get(
                        "source_observations"
                    ):
                        raise ValueError(
                            f"{metric} derived quarterly "
                            f"record missing source_observations."
                        )

    for metric in INSTANT_METRICS:

        if metric not in data["history"]:
            raise ValueError(
                f"Missing instant metric history: {metric}"
            )

        metric_data = data[
            "history"
        ][metric]

        if not isinstance(
            metric_data,
            dict,
        ):
            raise ValueError(
                f"Invalid instant history object: {metric}"
            )

        instant = metric_data.get(
            "instant",
            [],
        )

        if not isinstance(
            instant,
            list,
        ):
            raise ValueError(
                f"{metric}.instant must be a list."
            )

        for record in instant:

            if not isinstance(
                record,
                dict,
            ):
                raise ValueError(
                    f"Invalid {metric} instant record."
                )

            if record.get(
                "period_end"
            ) is None:
                raise ValueError(
                    f"{metric} instant record "
                    f"missing period_end."
                )

            if record.get(
                "value"
            ) is None:
                raise ValueError(
                    f"{metric} instant record "
                    f"missing value."
                )

    return True


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Extract SEC historical financial data."
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

    result = extract_from_cache(
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
