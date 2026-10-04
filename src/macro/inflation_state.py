"""
Inflation State determination (Framework v2.1 Step 10B, 2026-10-04).

Implements, with no new interpretation added, the Inflation State /
Inflation Confidence rules frozen in
claude/2026-10-04-step10-market-regime-spec.md (v3) and this round's
sections 10-12.

Series (confirmed via live FRED documentation during Step 10A, not from
memory -- see claude/2026-10-04-step10a-connectivity-feasibility.md):

    Headline CPI = CPIAUCSL
    Core CPI     = CPILFESL
    Headline PCE = PCEPI
    Core PCE     = PCEPILFE

Per-series direction (this round's section 11, applied to each series'
YoY rate -- this round's section 10 confirms "기본 derived feature는
YoY inflation rate"):

    yoy = transforms.yoy_rate(series, periods_per_year=12)
    recent_3m_avg, prior_3m_avg = transforms.recent_prior_window_averages(
        [row["value"] for row in yoy], window=3
    )

    recent_3m_avg < prior_3m_avg  -> DISINFLATIONARY
    recent_3m_avg > prior_3m_avg  -> REACCELERATIONARY
    recent_3m_avg == prior_3m_avg -> UNCHANGED   (exact equality)
    insufficient data              -> UNVERIFIED

Quorum / consensus across the 4 series (this round's section 12, closed
in Step 10 v3):

    4/4 DISINFLATIONARY   -> DISINFLATING, HIGH
    3/4 DISINFLATIONARY   -> DISINFLATING, MEDIUM
    4/4 REACCELERATIONARY -> REACCELERATING, HIGH
    3/4 REACCELERATIONARY -> REACCELERATING, MEDIUM
    4/4 UNCHANGED         -> UNCHANGED, HIGH
    (every other valid-but-non-quorum combination) -> MIXED, MIXED
    any one series UNVERIFIED -> UNVERIFIED, UNVERIFIED

On the "any one series UNVERIFIED" rule: Step 10B's own text in this
round only spells out the quorum numerically ("3/4", "4/4") and
separately states "필수 데이터가 부족하면 -> UNVERIFIED". The frozen v3
spec's quorum table is defined over exactly 4 required series; it does
not describe evaluating a 3-of-4 majority from only the 3 series that
happen to have data when the 4th is itself data-insufficient. Per this
project's Hard Constraint #2 (missing/ambiguous data must resolve to
UNVERIFIED, never an implicit favorable fallback), this module treats ANY
one of the 4 required series failing to produce a direction (its own
UNVERIFIED) as making the full quorum itself unevaluable -> the whole
inflation_state is UNVERIFIED, not a partial quorum over the remaining 3.
This is flagged explicitly in the Step 10B report as an interpretation
applied to an edge case the v3 table itself does not spell out
numerically -- not silently invented, and not treated as equivalent to
an already-closed design decision.

3/4 vs 4/4 quorum is a deterministic consensus COUNT, not a claim of
statistical independence across the 4 series (v3's own explicit
disclaimer, repeated here) -- these 4 series share substantial economic
co-movement by construction (CPI and PCE both measure US consumer
inflation), so "3 of 4 agree" is a counting rule, nothing more.
"""

from __future__ import annotations

from src.macro.transforms import (
    DOWN,
    EQUAL,
    INVALID,
    UP,
    compare_exact,
    recent_prior_window_averages,
    yoy_rate,
)

SCHEMA_VERSION = "macro_inflation_state_v0.1"

YOY_PERIODS_PER_YEAR = 12
CONSENSUS_WINDOW = 3

HEADLINE_CPI_SERIES_ID = "CPIAUCSL"
CORE_CPI_SERIES_ID = "CPILFESL"
HEADLINE_PCE_SERIES_ID = "PCEPI"
CORE_PCE_SERIES_ID = "PCEPILFE"

REQUIRED_INFLATION_SERIES_IDS = (
    HEADLINE_CPI_SERIES_ID,
    CORE_CPI_SERIES_ID,
    HEADLINE_PCE_SERIES_ID,
    CORE_PCE_SERIES_ID,
)

DISINFLATIONARY = "DISINFLATIONARY"
REACCELERATIONARY = "REACCELERATIONARY"
DIRECTION_UNCHANGED = "UNCHANGED"
DIRECTION_UNVERIFIED = "UNVERIFIED"

INFLATION_DIRECTION_VALUES = frozenset(
    {DISINFLATIONARY, REACCELERATIONARY, DIRECTION_UNCHANGED, DIRECTION_UNVERIFIED}
)

INFLATION_DISINFLATING = "DISINFLATING"
INFLATION_REACCELERATING = "REACCELERATING"
INFLATION_MIXED = "MIXED"
INFLATION_UNCHANGED = "UNCHANGED"
INFLATION_UNVERIFIED = "UNVERIFIED"

INFLATION_STATES = frozenset(
    {
        INFLATION_DISINFLATING,
        INFLATION_REACCELERATING,
        INFLATION_MIXED,
        INFLATION_UNCHANGED,
        INFLATION_UNVERIFIED,
    }
)

INFLATION_CONFIDENCE_HIGH = "HIGH"
INFLATION_CONFIDENCE_MEDIUM = "MEDIUM"
INFLATION_CONFIDENCE_MIXED = "MIXED"
INFLATION_CONFIDENCE_UNVERIFIED = "UNVERIFIED"

INFLATION_CONFIDENCE_VALUES = frozenset(
    {
        INFLATION_CONFIDENCE_HIGH,
        INFLATION_CONFIDENCE_MEDIUM,
        INFLATION_CONFIDENCE_MIXED,
        INFLATION_CONFIDENCE_UNVERIFIED,
    }
)


# ============================================================
# PER-SERIES DIRECTION
# ============================================================

def compute_inflation_direction(series: list[dict]) -> str:
    """
    series: PIT-gated raw observations for ONE inflation series_id,
    sorted ascending by observation_date.

    Returns one of DISINFLATIONARY / REACCELERATIONARY / UNCHANGED /
    UNVERIFIED. UNVERIFIED covers "not enough raw levels to compute 12
    YoY points", "not enough YoY points to form two 3-month windows",
    AND (Hardening round, 2026-10-04) a NaN anywhere in either window
    making the two averages uncomparable -- all three are "insufficient
    or invalid evidence", never distinguished further here, and NaN is
    never allowed to fall through to DIRECTION_UNCHANGED (see
    transforms.py's module docstring on why that was a genuine bug).
    """
    yoy = yoy_rate(series, YOY_PERIODS_PER_YEAR)
    yoy_values = [row["value"] for row in yoy]

    averages = recent_prior_window_averages(yoy_values, CONSENSUS_WINDOW)
    if averages is None:
        return DIRECTION_UNVERIFIED

    recent_avg, prior_avg = averages
    comparison = compare_exact(recent_avg, prior_avg)

    if comparison == INVALID:
        return DIRECTION_UNVERIFIED
    if comparison == DOWN:
        return DISINFLATIONARY
    if comparison == UP:
        return REACCELERATIONARY
    return DIRECTION_UNCHANGED


# ============================================================
# QUORUM / CONSENSUS ACROSS THE 4 SERIES
# ============================================================

def determine_inflation_state(directions: dict[str, str]) -> tuple[str, str]:
    """
    directions: {series_id: direction} for exactly the 4
    REQUIRED_INFLATION_SERIES_IDS (caller-supplied -- this function does
    not compute directions itself, mirrors growth_state.determine_growth_state()'s
    pure-combination design).

    Returns (inflation_state, inflation_confidence) per the closed v3
    quorum table (see module docstring).

    Raises ValueError if any required series_id is missing from
    `directions` or carries an unrecognized direction value -- a
    structural/programming problem, not a data-quality case.
    """
    missing = [sid for sid in REQUIRED_INFLATION_SERIES_IDS if sid not in directions]
    if missing:
        raise ValueError(f"Missing direction(s) for required series: {missing!r}")

    values = [directions[sid] for sid in REQUIRED_INFLATION_SERIES_IDS]
    for value in values:
        if value not in INFLATION_DIRECTION_VALUES:
            raise ValueError(f"Unrecognized inflation direction: {value!r}")

    if any(value == DIRECTION_UNVERIFIED for value in values):
        return INFLATION_UNVERIFIED, INFLATION_CONFIDENCE_UNVERIFIED

    count_dis = values.count(DISINFLATIONARY)
    count_acc = values.count(REACCELERATIONARY)
    count_unchanged = values.count(DIRECTION_UNCHANGED)

    if count_dis == 4:
        return INFLATION_DISINFLATING, INFLATION_CONFIDENCE_HIGH
    if count_dis == 3:
        return INFLATION_DISINFLATING, INFLATION_CONFIDENCE_MEDIUM
    if count_acc == 4:
        return INFLATION_REACCELERATING, INFLATION_CONFIDENCE_HIGH
    if count_acc == 3:
        return INFLATION_REACCELERATING, INFLATION_CONFIDENCE_MEDIUM
    if count_unchanged == 4:
        return INFLATION_UNCHANGED, INFLATION_CONFIDENCE_HIGH

    return INFLATION_MIXED, INFLATION_CONFIDENCE_MIXED


__all__ = [
    "SCHEMA_VERSION",
    "YOY_PERIODS_PER_YEAR",
    "CONSENSUS_WINDOW",
    "HEADLINE_CPI_SERIES_ID",
    "CORE_CPI_SERIES_ID",
    "HEADLINE_PCE_SERIES_ID",
    "CORE_PCE_SERIES_ID",
    "REQUIRED_INFLATION_SERIES_IDS",
    "DISINFLATIONARY",
    "REACCELERATIONARY",
    "DIRECTION_UNCHANGED",
    "DIRECTION_UNVERIFIED",
    "INFLATION_DIRECTION_VALUES",
    "INFLATION_DISINFLATING",
    "INFLATION_REACCELERATING",
    "INFLATION_MIXED",
    "INFLATION_UNCHANGED",
    "INFLATION_UNVERIFIED",
    "INFLATION_STATES",
    "INFLATION_CONFIDENCE_HIGH",
    "INFLATION_CONFIDENCE_MEDIUM",
    "INFLATION_CONFIDENCE_MIXED",
    "INFLATION_CONFIDENCE_UNVERIFIED",
    "INFLATION_CONFIDENCE_VALUES",
    "compute_inflation_direction",
    "determine_inflation_state",
]
