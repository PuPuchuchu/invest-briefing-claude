from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


# ============================================================
# CONFIGURATION
# ============================================================

SCHEMA_VERSION = "sec_history_v0.1"

BASE_DIR = Path(__file__).resolve().parents[2]

RAW_DIR = BASE_DIR / "data" / "raw" / "sec"
PROCESSED_DIR = BASE_DIR / "data" / "processed" / "fundamentals"


# Approximate duration ranges.
#
# These ranges are intentionally broad because fiscal calendars
# differ between companies and some companies use 52/53-week
# fiscal calendars.
DURATION_RANGES = {
    "q1": (70, 110),
    "ytd_6m": (150, 210),
    "ytd_9m": (240, 300),
    "annual": (300, 400),
}


# Concepts whose values are NOT an additive cumulative flow -- diluted
# EPS is net income / weighted-average diluted shares, and subtracting
# one cumulative EPS figure from another does not yield the correct
# standalone-quarter EPS whenever the weighted-average diluted share
# count differs between the two periods (buybacks, issuances,
# convertible-dilution changes). This is a standard GAAP EPS pitfall,
# not something specific to this codebase.
#
# A concept named here is restricted, in
# reconstruct_standalone_quarters() below, to DIRECT standalone-quarter
# observations only -- never Q2 = H1 - Q1 / Q3 = 9M - H1 / Q4 = FY - 9M
# subtraction. If no direct observation exists for a given quarter,
# that quarter is simply absent from the result (the same "absent =
# not available" semantics every other gap in this module already
# uses) -- never fabricated from YTD data.
#
# Added 2026-10-07 to close a confirmed production defect: see
# claude/2026-10-07-sec-companyfacts-audit.md section E.1 (EPS was
# being reconstructed via generic cumulative subtraction in this
# module, unlike the non-production sec_historical.py reference
# implementation, which already excludes EPS from its equivalent
# RECONSTRUCTABLE_QUARTERLY_METRICS list).
NON_ADDITIVE_PER_SHARE_CONCEPTS = frozenset(
    {
        "EarningsPerShareDiluted",
    }
)


# ============================================================
# BASIC HELPERS
# ============================================================

def _parse_date(value: Any) -> date | None:
    """
    Parse an ISO date string into datetime.date.

    Returns None for invalid or missing values.
    """
    if not isinstance(value, str):
        return None

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()
    except ValueError:
        return None


def _duration_days(observation: dict) -> int | None:
    """
    Return inclusive duration in days.

    Example:

        2025-01-01 -> 2025-03-31
        = 90 days
    """
    start = _parse_date(
        observation.get("start")
    )

    end = _parse_date(
        observation.get("end")
    )

    if start is None or end is None:
        return None

    if end < start:
        return None

    return (end - start).days + 1


def _get_observation_value(
    observation: dict,
) -> Any:
    """
    Return the numeric value stored in an observation.

    SEC Company Facts uses:

        "val"

    Synthetic test fixtures may use:

        "value"

    Support both representations.

    SEC's "val" takes precedence when both are present.
    """
    if "val" in observation:
        return observation.get("val")

    return observation.get("value")


def _is_numeric(value: Any) -> bool:
    """
    Return True for finite int/float values.

    bool is intentionally excluded because bool is a subclass
    of int in Python.
    """
    if isinstance(value, bool):
        return False

    if not isinstance(value, (int, float)):
        return False

    return value == value and value not in (
        float("inf"),
        float("-inf"),
    )


def _filing_sort_key(
    observation: dict,
) -> tuple:
    """
    Sort observations by information availability.

    Earlier filing date means the information became publicly
    available earlier, so "filed" (ascending) remains the primary
    key -- this preserves the original, unchanged intent of
    preferring the earliest public disclosure of a given economic
    period (e.g. preferring an originally-filed figure over a later
    10-K/A restatement of that same period).

    Secondary key -- "end" (2026-10-03 Level 2 real-data validation
    finding, confirmed against real AVGO/AMD/MSFT/ORCL SEC Company
    Facts data; see module docstring reference below):

    SEC Company Facts' "fy"/"fp" fields describe the FILING's own
    fiscal focus (dei:DocumentFiscalYearFocus /
    DocumentFiscalPeriodFocus), not the individual fact's own
    economic period. A single filing routinely reports both its own
    current-period value AND one or more prior-year/prior-period
    comparative values for the same concept, and SEC tags ALL of
    them with that filing's own identical (fy, fp, filed, accn) --
    only "start"/"end" (and, when present, "frame") actually
    distinguish which economic period a given observation covers.

    Concretely (real AMD Revenue, 10-Q filed 2026-08-05, fy=2026,
    fp=Q2, accn identical on both rows):

        start=2025-03-30 end=2025-06-28 val=7,685,000,000  (comparative)
        start=2026-03-29 end=2026-06-27 val=11,536,000,000 (current)

    A prior-year/prior-period comparative observation's "end" date
    is, by construction, always earlier than the filing's own
    current-period "end" date for the identical (fy, fp, filed)
    label (a comparative covers an earlier calendar window than the
    period the filing itself is reporting on). So once the primary
    "earliest filed wins" key has resolved genuinely distinct
    filings/restatements, ties on "filed" are broken by preferring
    the LATEST "end" -- not the earliest -- so a filing's own current
    period is chosen over an embedded same-tag comparative figure.
    This is scoped to same-filed ties only; it is not a blanket
    "always pick the latest end across everything" rule, and it does
    not assume any calendar-year alignment (it compares two "end"
    dates that already belong to the same (fy, fp, filed) group, so
    it works unchanged for non-calendar fiscal-year issuers such as
    AVGO and ORCL).

    Tertiary keys ("start", "accn") remain for determinism when
    "filed" and "end" are both tied; "accn" previously read a
    nonexistent "accession" key (real SEC Company Facts observations
    use "accn"), which made that tertiary key a silent no-op on real
    data -- fixed here as part of the same finding.
    """
    filed = _parse_date(
        observation.get("filed")
    )

    end = _parse_date(
        observation.get("end")
    )

    start = _parse_date(
        observation.get("start")
    )

    end_ordinal = (
        end.toordinal()
        if end is not None
        else None
    )

    return (
        filed or date.max,
        # Negated so that, within a "filed" tie, sorting ascending
        # (as sort_by_filing_date() does) prefers the LATEST "end"
        # instead of the earliest. An observation with no parsable
        # "end" is deprioritized (sorts after any real end date).
        -end_ordinal
        if end_ordinal is not None
        else float("inf"),
        start or date.max,
        str(
            observation.get(
                "accn",
                "",
            )
        ),
    )


def _unambiguous_first(
    sorted_observations: list[dict],
) -> dict | None:
    """
    Return the preferred observation from an already
    filing-sorted (see _filing_sort_key / sort_by_filing_date)
    candidate list, or None if the top choice is genuinely
    ambiguous.

    "Genuinely ambiguous" means the top two candidates share both
    the same "filed" date AND the same "end" date (the two keys
    _filing_sort_key() uses to disambiguate same-tag duplicates)
    but report different values -- i.e. the data itself does not
    let this function tell current-period from comparative apart.
    Per Framework v2.1 Architecture v5 constraint #2 (missing /
    ambiguous data must resolve to UNVERIFIED, never a default
    favorable fallback), such a case is reported as "no selection"
    (None) rather than silently guessing based on array order.

    This situation has not been observed in real AVGO / AMD / MSFT /
    ORCL Company Facts data (2026-10-03 Level 2 validation) -- it is
    a defensive guard for data this function has not yet seen, not
    a response to an observed real-data case.
    """
    if not sorted_observations:
        return None

    if len(sorted_observations) == 1:
        return sorted_observations[0]

    first = sorted_observations[0]
    second = sorted_observations[1]

    first_filed = _parse_date(first.get("filed"))
    second_filed = _parse_date(second.get("filed"))

    first_end = _parse_date(first.get("end"))
    second_end = _parse_date(second.get("end"))

    same_filed_end = (
        first_filed == second_filed
        and first_end == second_end
    )

    if (
        same_filed_end
        and _get_observation_value(first)
        != _get_observation_value(second)
    ):
        return None

    return first


# ============================================================
# OBSERVATION CLASSIFICATION
# ============================================================

def is_duration_observation(
    observation: dict,
) -> bool:
    """
    Return True if observation contains a valid duration.
    """
    if not isinstance(observation, dict):
        return False

    start = _parse_date(
        observation.get("start")
    )

    end = _parse_date(
        observation.get("end")
    )

    if start is None or end is None:
        return False

    return end >= start


def is_annual_observation(
    observation: dict,
) -> bool:
    """
    Return True for annual 10-K / FY observations.

    SEC Company Facts annual observations are normally represented
    by:

        form = 10-K
        fp   = FY
    """
    if not is_duration_observation(
        observation
    ):
        return False

    if observation.get("form") != "10-K":
        return False

    if observation.get("fp") != "FY":
        return False

    duration = _duration_days(
        observation
    )

    if duration is None:
        return False

    min_days, max_days = DURATION_RANGES[
        "annual"
    ]

    return (
        min_days
        <= duration
        <= max_days
    )


def is_quarterly_form_observation(
    observation: dict,
) -> bool:
    """
    Return True for 10-Q duration observations.
    """
    return (
        is_duration_observation(
            observation
        )
        and observation.get("form")
        == "10-Q"
    )


def classify_duration_observation(
    observation: dict,
) -> str:
    """
    Classify a duration observation.

    Possible results:

        annual
        q1
        ytd_6m
        ytd_9m
        unknown
    """
    if not is_duration_observation(
        observation
    ):
        return "unknown"

    # --------------------------------------------------------
    # Annual
    # --------------------------------------------------------

    if is_annual_observation(
        observation
    ):
        return "annual"

    # --------------------------------------------------------
    # Quarterly 10-Q
    # --------------------------------------------------------

    if observation.get("form") != "10-Q":
        return "unknown"

    duration = _duration_days(
        observation
    )

    if duration is None:
        return "unknown"

    q1_min, q1_max = DURATION_RANGES[
        "q1"
    ]

    h1_min, h1_max = DURATION_RANGES[
        "ytd_6m"
    ]

    ytd9_min, ytd9_max = DURATION_RANGES[
        "ytd_9m"
    ]

    if q1_min <= duration <= q1_max:
        return "q1"

    if h1_min <= duration <= h1_max:
        return "ytd_6m"

    if ytd9_min <= duration <= ytd9_max:
        return "ytd_9m"

    return "unknown"


# ============================================================
# FILTERING
# ============================================================

def filter_valid_duration_observations(
    observations: list[dict],
) -> list[dict]:
    """
    Keep observations that:

    1. are dictionaries
    2. contain valid start/end dates
    3. have end >= start
    4. contain a numeric observation value

    Both SEC "val" and synthetic-test "value"
    representations are supported.
    """
    result = []

    for observation in observations:
        if not isinstance(
            observation,
            dict,
        ):
            continue

        if not is_duration_observation(
            observation
        ):
            continue

        value = _get_observation_value(
            observation
        )

        if not _is_numeric(value):
            continue

        result.append(observation)

    return result


# ============================================================
# SORTING
# ============================================================

def sort_by_filing_date(
    observations: list[dict],
    newest_first: bool = False,
) -> list[dict]:
    """
    Sort observations by filing date.

    Default:
        oldest filing first

    newest_first=True:
        newest filing first
    """
    return sorted(
        observations,
        key=_filing_sort_key,
        reverse=newest_first,
    )


# ============================================================
# ANNUAL HISTORY
# ============================================================

def extract_annual_history(
    observations: list[dict],
) -> list[dict]:
    """
    Extract annual 10-K / FY observations.

    One observation is retained per fiscal year.

    If multiple observations exist for the same FY,
    the earliest filing is preferred because it represents
    the earliest point at which the information became
    publicly available; within the same filing, the observation
    matching that filing's own current (not comparative) annual
    period is preferred (see _filing_sort_key()'s docstring). A
    fiscal year is omitted entirely -- rather than guessed -- if its
    candidates are genuinely indistinguishable (see
    _unambiguous_first()).
    """
    valid = filter_valid_duration_observations(
        observations
    )

    annual = [
        observation
        for observation in valid
        if is_annual_observation(
            observation
        )
    ]

    grouped: dict[Any, list[dict]] = defaultdict(
        list
    )

    for observation in annual:
        fy = observation.get("fy")

        if fy is None:
            continue

        grouped[fy].append(
            observation
        )

    result = []

    for fy, records in grouped.items():
        records = sort_by_filing_date(
            records
        )

        selected = _unambiguous_first(
            records
        )

        if selected is None:
            continue

        result.append(
            selected.copy()
        )

    result.sort(
        key=lambda observation: (
            observation.get(
                "fy",
                0,
            ),
            observation.get(
                "end",
                "",
            ),
        )
    )

    return result


# ============================================================
# QUARTERLY HISTORY
# ============================================================

def extract_quarterly_history(
    observations: list[dict],
) -> dict[str, list[dict]]:
    """
    Classify 10-Q duration observations into:

        q1
        ytd_6m
        ytd_9m
        unknown

    Annual observations are intentionally excluded.

    The original observations are preserved.
    """
    valid = filter_valid_duration_observations(
        observations
    )

    result = {
        "q1": [],
        "ytd_6m": [],
        "ytd_9m": [],
        "unknown": [],
    }

    for observation in valid:
        # ----------------------------------------------------
        # Quarterly history must contain only 10-Q records.
        # ----------------------------------------------------

        if not is_quarterly_form_observation(
            observation
        ):
            continue

        classification = classify_duration_observation(
            observation
        )

        if classification not in result:
            continue

        result[
            classification
        ].append(
            observation
        )

    for key in result:
        result[key] = sort_by_filing_date(
            result[key]
        )

    return result


# ============================================================
# FISCAL YEAR HELPERS
# ============================================================

def _same_fiscal_year(
    observation_a: dict,
    observation_b: dict,
) -> bool:
    """
    Return True if two observations belong to the same
    fiscal year.

    Prefer SEC "fy" when available.

    Fall back to calendar year of end date when fy
    is unavailable.
    """
    fy_a = observation_a.get("fy")
    fy_b = observation_b.get("fy")

    if (
        fy_a is not None
        and fy_b is not None
    ):
        return fy_a == fy_b

    end_a = _parse_date(
        observation_a.get("end")
    )

    end_b = _parse_date(
        observation_b.get("end")
    )

    if end_a is None or end_b is None:
        return False

    return end_a.year == end_b.year


# ============================================================
# DIRECT-ONLY RECONSTRUCTION (non-additive / per-share concepts)
# ============================================================

def _is_direct_standalone_quarter_observation(
    observation: dict,
) -> bool:
    """
    True for an observation that is itself already a non-cumulative,
    ~3-month economic period -- regardless of "form" (a direct Q4
    value is often disclosed inside the 10-K itself, not a separate
    10-Q, e.g. SEC's own "selected quarterly financial data"
    footnote), mirroring the duration-only test already used by the
    (non-production) reference implementation's
    _is_standalone_quarter() in sec_historical.py.
    """
    duration = _duration_days(
        observation
    )

    if duration is None:
        return False

    min_days, max_days = DURATION_RANGES[
        "q1"
    ]

    return (
        min_days
        <= duration
        <= max_days
    )


def _select_direct_quarter_observation(
    candidates: list[dict],
    fy: Any,
    quarter: str,
) -> dict | None:
    """
    Select one direct standalone-quarter observation for one (fy,
    quarter) pair.

    Q1 / Q2 / Q3 require an exact "fp" match.

    Q4 accepts fp="Q4" OR fp="FY" -- SEC Company Facts commonly
    represents a standalone Q4 observation (e.g. inside a 10-K's own
    "selected quarterly data") tagged with fp="FY" rather than fp="Q4",
    the same convention already documented and already relied on by
    the reference implementation (sec_historical.py's
    _select_direct_quarter()).

    Uses the same filing-date / same-tag-comparative tie-break already
    used everywhere else in this module (sort_by_filing_date +
    _unambiguous_first), so a same-filed current-vs-comparative
    duplicate is resolved identically here to every other extraction
    path in this file.
    """
    matching = []

    for observation in candidates:

        if observation.get("fy") != fy:
            continue

        fp = observation.get("fp")

        if quarter in ("Q1", "Q2", "Q3"):
            if fp != quarter:
                continue

        elif quarter == "Q4":
            if fp not in ("Q4", "FY"):
                continue

        else:
            continue

        matching.append(
            observation
        )

    sorted_matching = sort_by_filing_date(
        matching
    )

    return _unambiguous_first(
        sorted_matching
    )


def _build_direct_quarter_record(
    quarter: str,
    observation: dict,
) -> dict:
    """
    Build a standalone-quarter record from a genuinely direct
    (non-cumulative) observation -- same output shape as the Q1 branch
    of _make_reconstructed_quarter() below (quarter/value/
    period_start/period_end/filed/form/fy/accession/reconstructed/
    source), so downstream consumers (growth.py, validate_history())
    see an identical schema regardless of which path produced the
    record.
    """
    return {
        "quarter": quarter,
        "value": _get_observation_value(
            observation
        ),
        "period_start": observation.get(
            "start"
        ),
        "period_end": observation.get(
            "end"
        ),
        "filed": observation.get(
            "filed"
        ),
        "form": observation.get(
            "form"
        ),
        "fy": observation.get(
            "fy"
        ),
        "accession": observation.get(
            "accession"
        ),
        "reconstructed": False,
        "source": {
            "current": observation.copy(),
            "previous": None,
        },
    }


def _reconstruct_direct_only_quarters(
    observations: list[dict],
) -> list[dict]:
    """
    Build standalone-quarter history for a non-additive (per-share)
    concept -- see NON_ADDITIVE_PER_SHARE_CONCEPTS.

    Unlike reconstruct_standalone_quarters()'s generic path below,
    this NEVER subtracts a cumulative/YTD observation from another to
    derive a missing quarter. A quarter with no genuinely direct
    (~3-month, correctly fp-matched) observation is simply absent from
    the result -- never fabricated.
    """
    valid = filter_valid_duration_observations(
        observations
    )

    direct_candidates = [
        observation
        for observation in valid
        if _is_direct_standalone_quarter_observation(
            observation
        )
    ]

    fiscal_years = {
        observation.get("fy")
        for observation in direct_candidates
        if observation.get("fy") is not None
    }

    result = []

    for fy in sorted(
        fiscal_years
    ):

        for quarter in (
            "Q1",
            "Q2",
            "Q3",
            "Q4",
        ):

            selected = _select_direct_quarter_observation(
                direct_candidates,
                fy,
                quarter,
            )

            if selected is None:
                continue

            value = _get_observation_value(
                selected
            )

            if not _is_numeric(
                value
            ):
                continue

            result.append(
                _build_direct_quarter_record(
                    quarter,
                    selected,
                )
            )

    result.sort(
        key=lambda observation: (
            observation.get(
                "period_end",
                "",
            ),
            observation.get(
                "quarter",
                "",
            ),
        )
    )

    return result


# ============================================================
# QUARTER RECONSTRUCTION
# ============================================================

def _make_reconstructed_quarter(
    *,
    quarter: str,
    current_observation: dict,
    previous_observation: dict | None,
) -> dict | None:
    """
    Reconstruct a standalone quarter.

    Logic:

        Q1 = Q1

        Q2 = H1 - Q1

        Q3 = 9M - H1

        Q4 = FY - 9M

    Provenance is preserved.
    """
    current_value = _get_observation_value(
        current_observation
    )

    if not _is_numeric(
        current_value
    ):
        return None

    # --------------------------------------------------------
    # Q1 is already standalone.
    # --------------------------------------------------------

    if quarter == "Q1":
        return {
            "quarter": "Q1",
            "value": current_value,
            "period_start": current_observation.get(
                "start"
            ),
            "period_end": current_observation.get(
                "end"
            ),
            "filed": current_observation.get(
                "filed"
            ),
            "form": current_observation.get(
                "form"
            ),
            "fy": current_observation.get(
                "fy"
            ),
            "accession": current_observation.get(
                "accession"
            ),
            "reconstructed": False,
            "source": {
                "current": current_observation.copy(),
                "previous": None,
            },
        }

    # --------------------------------------------------------
    # Q2 / Q3 / Q4 require a previous cumulative period.
    # --------------------------------------------------------

    if previous_observation is None:
        return None

    previous_value = _get_observation_value(
        previous_observation
    )

    if not _is_numeric(
        previous_value
    ):
        return None

    value = (
        current_value
        - previous_value
    )

    previous_end = _parse_date(
        previous_observation.get(
            "end"
        )
    )

    current_end = _parse_date(
        current_observation.get(
            "end"
        )
    )

    if (
        previous_end is None
        or current_end is None
    ):
        return None

    # --------------------------------------------------------
    # Standalone quarter starts the day after the previous
    # cumulative period ended.
    # --------------------------------------------------------

    period_start = (
        previous_end
        + timedelta(days=1)
    )

    return {
        "quarter": quarter,
        "value": value,
        "period_start": period_start.isoformat(),
        "period_end": current_observation.get(
            "end"
        ),
        "filed": current_observation.get(
            "filed"
        ),
        "form": current_observation.get(
            "form"
        ),
        "fy": current_observation.get(
            "fy"
        ),
        "accession": current_observation.get(
            "accession"
        ),
        "reconstructed": True,
        "source": {
            "current": current_observation.copy(),
            "previous": previous_observation.copy(),
        },
    }


def _select_one_observation(
    observations: list[dict],
    fy: Any,
) -> dict | None:
    """
    Select one observation for a fiscal year.

    Earliest filing is preferred; within the same filing, the
    observation whose "end" date actually matches the current
    (not comparative) period for that filing is preferred (see
    _filing_sort_key()'s docstring for the real-data finding this
    implements). Returns None -- rather than guessing -- if the
    top two filing-sorted candidates are genuinely indistinguishable
    (see _unambiguous_first()).
    """
    candidates = [
        observation
        for observation in observations
        if observation.get("fy") == fy
    ]

    if not candidates:
        return None

    candidates = sort_by_filing_date(
        candidates
    )

    return _unambiguous_first(
        candidates
    )


def reconstruct_standalone_quarters(
    observations: list[dict],
    concept_name: str | None = None,
) -> list[dict]:
    """
    Reconstruct standalone quarterly values.

    Logic:

        Q1 = Q1

        Q2 = H1 - Q1

        Q3 = 9M - H1

        Q4 = FY - 9M

    Q4 filing provenance uses the FY / 10-K filing date.

    concept_name (optional, added 2026-10-07): when it names a
    non-additive per-share concept (NON_ADDITIVE_PER_SHARE_CONCEPTS,
    currently just diluted EPS), this function instead delegates to
    _reconstruct_direct_only_quarters() above -- direct observations
    only, never the Q2=H1-Q1 / Q3=9M-H1 / Q4=FY-9M subtraction this
    function otherwise performs. Omitting concept_name (the default)
    preserves this function's prior behavior exactly, which remains
    correct for every additive flow metric (revenue, net_income,
    operating_income, cfo, capex). See
    claude/2026-10-07-sec-companyfacts-audit.md section E.1 for why:
    a per-share ratio is not an additive cumulative flow, so
    subtracting cumulative EPS figures does not yield the correct
    standalone-quarter EPS whenever the weighted-average diluted share
    count differs between the two periods (buybacks, issuances,
    convertible-dilution changes).
    """
    if concept_name in NON_ADDITIVE_PER_SHARE_CONCEPTS:
        return _reconstruct_direct_only_quarters(
            observations
        )

    valid = filter_valid_duration_observations(
        observations
    )

    # --------------------------------------------------------
    # Separate annual and quarterly observations.
    # --------------------------------------------------------

    annual = [
        observation
        for observation in valid
        if is_annual_observation(
            observation
        )
    ]

    quarterly = [
        observation
        for observation in valid
        if is_quarterly_form_observation(
            observation
        )
    ]

    classified = extract_quarterly_history(
        quarterly
    )

    # --------------------------------------------------------
    # Build fiscal-year universe.
    # --------------------------------------------------------

    fiscal_years = set()

    for observation in annual:
        if observation.get("fy") is not None:
            fiscal_years.add(
                observation.get("fy")
            )

    for records in classified.values():
        for observation in records:
            if observation.get("fy") is not None:
                fiscal_years.add(
                    observation.get("fy")
                )

    result = []

    # --------------------------------------------------------
    # Process each fiscal year independently.
    # --------------------------------------------------------

    for fy in sorted(fiscal_years):

        q1 = _select_one_observation(
            classified["q1"],
            fy,
        )

        h1 = _select_one_observation(
            classified["ytd_6m"],
            fy,
        )

        ytd9 = _select_one_observation(
            classified["ytd_9m"],
            fy,
        )

        fy_observation = _select_one_observation(
            annual,
            fy,
        )

        # ----------------------------------------------------
        # Q1
        # ----------------------------------------------------

        if q1 is not None:

            reconstructed_q1 = (
                _make_reconstructed_quarter(
                    quarter="Q1",
                    current_observation=q1,
                    previous_observation=None,
                )
            )

            if reconstructed_q1 is not None:
                result.append(
                    reconstructed_q1
                )

        # ----------------------------------------------------
        # Q2 = H1 - Q1
        # ----------------------------------------------------

        if (
            h1 is not None
            and q1 is not None
            and _same_fiscal_year(
                h1,
                q1,
            )
        ):

            reconstructed_q2 = (
                _make_reconstructed_quarter(
                    quarter="Q2",
                    current_observation=h1,
                    previous_observation=q1,
                )
            )

            if reconstructed_q2 is not None:
                result.append(
                    reconstructed_q2
                )

        # ----------------------------------------------------
        # Q3 = 9M - H1
        # ----------------------------------------------------

        if (
            ytd9 is not None
            and h1 is not None
            and _same_fiscal_year(
                ytd9,
                h1,
            )
        ):

            reconstructed_q3 = (
                _make_reconstructed_quarter(
                    quarter="Q3",
                    current_observation=ytd9,
                    previous_observation=h1,
                )
            )

            if reconstructed_q3 is not None:
                result.append(
                    reconstructed_q3
                )

        # ----------------------------------------------------
        # Q4 = FY - 9M
        #
        # Q4 becomes publicly available with the annual filing.
        # ----------------------------------------------------

        if (
            fy_observation is not None
            and ytd9 is not None
            and _same_fiscal_year(
                fy_observation,
                ytd9,
            )
        ):

            reconstructed_q4 = (
                _make_reconstructed_quarter(
                    quarter="Q4",
                    current_observation=fy_observation,
                    previous_observation=ytd9,
                )
            )

            if reconstructed_q4 is not None:
                result.append(
                    reconstructed_q4
                )

    # --------------------------------------------------------
    # Chronological order.
    # --------------------------------------------------------

    result.sort(
        key=lambda observation: (
            observation.get(
                "period_end",
                "",
            ),
            observation.get(
                "quarter",
                "",
            ),
        )
    )

    return result


# ============================================================
# CONCEPT HISTORY
# ============================================================

def extract_concept_history(
    concept_data: dict,
    concept_name: str | None = None,
) -> dict:
    """
    Extract annual, quarterly and standalone-quarter history
    from one SEC Company Facts concept.

    Expected structure:

        {
            "label": "...",
            "description": "...",
            "units": {
                "USD": [...],
                ...
            }
        }

    v0.1 intentionally preserves the original SEC unit
    on each observation as "_unit".

    Cross-unit normalization is NOT performed here.

    concept_name (optional, added 2026-10-07): the XBRL concept this
    data belongs to (e.g. "EarningsPerShareDiluted"), passed straight
    through to reconstruct_standalone_quarters() below. This function
    itself does no concept-specific branching -- it only threads the
    identity through so that function can restrict non-additive
    per-share concepts to direct-observation-only reconstruction (see
    NON_ADDITIVE_PER_SHARE_CONCEPTS). Omitting it preserves this
    function's prior behavior exactly.
    """
    if not isinstance(
        concept_data,
        dict,
    ):
        raise ValueError(
            "concept_data must be a dictionary"
        )

    units = concept_data.get(
        "units"
    )

    if not isinstance(
        units,
        dict,
    ):
        raise ValueError(
            "concept_data.units must be a dictionary"
        )

    observations = []

    for unit_name, unit_observations in units.items():

        if not isinstance(
            unit_observations,
            list,
        ):
            continue

        for observation in unit_observations:

            if not isinstance(
                observation,
                dict,
            ):
                continue

            copied = observation.copy()

            # Preserve SEC unit explicitly.
            copied["_unit"] = unit_name

            observations.append(
                copied
            )

    annual = extract_annual_history(
        observations
    )

    quarterly = extract_quarterly_history(
        observations
    )

    standalone_quarters = (
        reconstruct_standalone_quarters(
            observations,
            concept_name=concept_name,
        )
    )

    result = {
        "schema_version": SCHEMA_VERSION,
        "label": concept_data.get(
            "label"
        ),
        "description": concept_data.get(
            "description"
        ),
        "annual": annual,
        "quarterly": quarterly,
        "standalone_quarters": standalone_quarters,
    }

    # --------------------------------------------------------
    # Self-validation (2026-09-19): validate_history() below already
    # existed and was already exercised by tests/test_sec_history.py
    # and tests/test_sec_history_real.py, but was never actually called
    # from within extract_concept_history() itself -- so a real bug
    # that produced malformed output would have gone uncaught outside
    # of the specific fixtures the test suite happens to cover. A
    # validate_history() failure here means this function's OWN output
    # doesn't match its own documented schema, which is a programming
    # defect -- so it fails loudly rather than silently returning
    # malformed data.
    validation_failures = validate_history(result)

    if validation_failures:
        raise ValueError(
            f"extract_concept_history() produced invalid output: "
            f"{validation_failures}"
        )

    return result


# ============================================================
# VALIDATION
# ============================================================

def validate_history(
    history: dict,
) -> list[str]:
    """
    Validate extracted history structure.

    Returns a list of validation failure keys.

    Empty list means validation passed.
    """
    if not isinstance(
        history,
        dict,
    ):
        return ["history"]

    failures = []

    required_keys = [
        "schema_version",
        "annual",
        "quarterly",
        "standalone_quarters",
    ]

    for key in required_keys:
        if key not in history:
            failures.append(
                key
            )

    if failures:
        return failures

    # --------------------------------------------------------
    # Schema version
    # --------------------------------------------------------

    if history.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        failures.append(
            "schema_version"
        )

    # --------------------------------------------------------
    # Annual
    # --------------------------------------------------------

    annual = history.get(
        "annual"
    )

    if not isinstance(
        annual,
        list,
    ):
        failures.append(
            "annual"
        )

    # --------------------------------------------------------
    # Quarterly
    # --------------------------------------------------------

    quarterly = history.get(
        "quarterly"
    )

    if not isinstance(
        quarterly,
        dict,
    ):
        failures.append(
            "quarterly"
        )
    else:
        required_quarterly_keys = [
            "q1",
            "ytd_6m",
            "ytd_9m",
            "unknown",
        ]

        for key in required_quarterly_keys:

            if key not in quarterly:
                failures.append(
                    f"quarterly.{key}"
                )

            elif not isinstance(
                quarterly[key],
                list,
            ):
                failures.append(
                    f"quarterly.{key}"
                )

    # --------------------------------------------------------
    # Standalone quarters
    # --------------------------------------------------------

    standalone = history.get(
        "standalone_quarters"
    )

    if not isinstance(
        standalone,
        list,
    ):
        failures.append(
            "standalone_quarters"
        )

    # --------------------------------------------------------
    # Validate annual records.
    # --------------------------------------------------------

    if isinstance(
        annual,
        list,
    ):

        for index, observation in enumerate(
            annual
        ):

            if not isinstance(
                observation,
                dict,
            ):
                failures.append(
                    f"annual[{index}]"
                )
                continue

            value = _get_observation_value(
                observation
            )

            if not _is_numeric(
                value
            ):
                failures.append(
                    f"annual[{index}].value"
                )

            if not is_duration_observation(
                observation
            ):
                failures.append(
                    f"annual[{index}].duration"
                )

    # --------------------------------------------------------
    # Validate quarterly records.
    # --------------------------------------------------------

    if isinstance(
        quarterly,
        dict,
    ):

        for key, records in quarterly.items():

            if not isinstance(
                records,
                list,
            ):
                continue

            for index, observation in enumerate(
                records
            ):

                if not isinstance(
                    observation,
                    dict,
                ):
                    failures.append(
                        f"quarterly.{key}[{index}]"
                    )
                    continue

                value = _get_observation_value(
                    observation
                )

                if not _is_numeric(
                    value
                ):
                    failures.append(
                        f"quarterly.{key}[{index}].value"
                    )

                if not is_duration_observation(
                    observation
                ):
                    failures.append(
                        f"quarterly.{key}[{index}].duration"
                    )

    # --------------------------------------------------------
    # Validate standalone quarters.
    # --------------------------------------------------------

    if isinstance(
        standalone,
        list,
    ):

        for index, quarter in enumerate(
            standalone
        ):

            if not isinstance(
                quarter,
                dict,
            ):
                failures.append(
                    f"standalone_quarters[{index}]"
                )
                continue

            if quarter.get(
                "quarter"
            ) not in {
                "Q1",
                "Q2",
                "Q3",
                "Q4",
            }:
                failures.append(
                    f"standalone_quarters[{index}].quarter"
                )

            if not _is_numeric(
                quarter.get("value")
            ):
                failures.append(
                    f"standalone_quarters[{index}].value"
                )

            if _parse_date(
                quarter.get(
                    "period_start"
                )
            ) is None:
                failures.append(
                    f"standalone_quarters[{index}].period_start"
                )

            if _parse_date(
                quarter.get(
                    "period_end"
                )
            ) is None:
                failures.append(
                    f"standalone_quarters[{index}].period_end"
                )

    return failures


# ============================================================
# PUBLIC EXPORTS
# ============================================================

__all__ = [
    "SCHEMA_VERSION",
    "DURATION_RANGES",
    "NON_ADDITIVE_PER_SHARE_CONCEPTS",
    "is_duration_observation",
    "is_annual_observation",
    "is_quarterly_form_observation",
    "classify_duration_observation",
    "filter_valid_duration_observations",
    "sort_by_filing_date",
    "extract_annual_history",
    "extract_quarterly_history",
    "reconstruct_standalone_quarters",
    "extract_concept_history",
    "validate_history",
]
    "validate_history",
]
