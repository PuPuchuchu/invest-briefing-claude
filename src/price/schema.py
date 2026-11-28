"""
Data shapes, status vocabularies and reason codes for the price engine.

Design source: claude/2026-11-27-p3-v7-and-price-engine-design-v1.md
(sections 2.2-2.5 and 3.2).

Rules enforced here (GPT implementation instruction, section 3.2):
  * one series never mixes bases (RAW vs SPLIT_ADJUSTED);
  * no duplicate trading dates, dates strictly ascending;
  * no bar dated after the caller-supplied `as_of` date;
  * OHLC relationship is sane and nothing is negative;
  * missing values are never replaced by 0 or by the previous value --
    a bar with a missing field is simply invalid;
  * money and ratios are `decimal.Decimal`.

Error messages raised from this module NEVER contain price or volume
values -- only field names and reason codes -- so that a validation
failure cannot leak provider data into logs or tracebacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

SCHEMA_VERSION = "price_schema_v0.1"
CALCULATION_VERSION = "price_engine_v0.1"

CURRENCY_USD = "USD"
PRICE_UNIT = "USD_PER_SHARE"


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

class Basis(str, Enum):
    RAW = "RAW"
    SPLIT_ADJUSTED = "SPLIT_ADJUSTED"


class ActionType(str, Enum):
    FORWARD_SPLIT = "FORWARD_SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"
    STOCK_DIVIDEND = "STOCK_DIVIDEND"


class CorporateActionStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    PENDING_SEC = "PENDING_SEC"
    NO_EVENT_VERIFIED = "NO_EVENT_VERIFIED"
    CANDIDATE = "CANDIDATE"
    UNVERIFIED = "UNVERIFIED"


class PriceAdjustmentQC(str, Enum):
    CONSISTENT = "CONSISTENT"
    INCONSISTENT = "INCONSISTENT"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class ShareCountStatus(str, Enum):
    OK = "OK"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    STALE = "STALE"
    PIT_VIOLATION = "PIT_VIOLATION"
    BASIS_AMBIGUOUS = "BASIS_AMBIGUOUS"
    CLASS_UNRESOLVED = "CLASS_UNRESOLVED"


class RawPriceStatus(str, Enum):
    OK = "OK"
    MISSING = "MISSING"
    STALE = "STALE"
    BAR_MISSING = "BAR_MISSING"


class AdjustedPriceStatus(str, Enum):
    OK = "OK"
    MISSING = "MISSING"
    STALE = "STALE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


class Freshness(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    NO_NEW_SESSION = "NO_NEW_SESSION"


class CalcStatus(str, Enum):
    OK = "OK"
    INVALID = "INVALID"
    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Interpretability(str, Enum):
    MEANINGFUL = "MEANINGFUL"
    NOT_MEANINGFUL = "NOT_MEANINGFUL"


class CaptureClass(str, Enum):
    AS_CAPTURED_PIT = "AS_CAPTURED_PIT"
    SCORE_ONLY_CAPTURE = "SCORE_ONLY_CAPTURE"
    EPHEMERAL_ONLY = "EPHEMERAL_ONLY"
    NON_PIT_HISTORICAL = "NON_PIT_HISTORICAL"


class RsiState(str, Enum):
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    WARMUP = "WARMUP"
    STABLE = "STABLE"


class Reason:
    """Reason codes. Plain strings so results stay JSON-serialisable."""

    # series / bar validation
    BASIS_MIXED = "BASIS_MIXED"
    DUPLICATE_DATE = "DUPLICATE_DATE"
    DATES_NOT_ASCENDING = "DATES_NOT_ASCENDING"
    FUTURE_DATE = "FUTURE_DATE"
    INVALID_OHLC = "INVALID_OHLC"
    NEGATIVE_VALUE = "NEGATIVE_VALUE"
    MISSING_FIELD = "MISSING_FIELD"
    NON_DECIMAL_VALUE = "NON_DECIMAL_VALUE"
    WRONG_CURRENCY = "WRONG_CURRENCY"
    # split events
    INVALID_SPLIT_RATIO = "INVALID_SPLIT_RATIO"
    # provider
    PROVIDER_ERROR = "PROVIDER_ERROR"
    SPLIT_FETCH_FAILED = "SPLIT_FETCH_FAILED"
    BARS_FETCH_FAILED = "BARS_FETCH_FAILED"
    PARTIAL_RESPONSE = "PARTIAL_RESPONSE"
    # freshness / calendar
    NO_NEW_SESSION = "NO_NEW_SESSION"
    PRICE_STALE = "PRICE_STALE"
    PRICE_UNAVAILABLE = "PRICE_UNAVAILABLE"
    BAR_AFTER_EXPECTED_SESSION = "BAR_AFTER_EXPECTED_SESSION"
    # adjustment QC
    QC_PRECISION_UNKNOWN = "QC_PRECISION_UNKNOWN"
    QC_DATE_MISMATCH = "QC_DATE_MISMATCH"
    QC_BASIS_MISMATCH = "QC_BASIS_MISMATCH"
    QC_NONPOSITIVE_DENOMINATOR = "QC_NONPOSITIVE_DENOMINATOR"
    QC_OUT_OF_INTERVAL = "QC_OUT_OF_INTERVAL"
    QC_NO_OVERLAP = "QC_NO_OVERLAP"
    SYMBOL_REQUIRED = "SYMBOL_REQUIRED"
    FACTOR_CROSSCHECK_UNVERIFIED = "FACTOR_CROSSCHECK_UNVERIFIED"
    # corporate actions (R3)
    SPLIT_EVENT_MISSING = "SPLIT_EVENT_MISSING"
    COVERAGE_UNVERIFIED = "COVERAGE_UNVERIFIED"
    UNSUPPORTED_SCENARIO = "UNSUPPORTED_SCENARIO"
    SPLIT_PENDING_SEC = "SPLIT_PENDING_SEC"
    SPLIT_RAW_JUMP_UNVERIFIED = "SPLIT_RAW_JUMP_UNVERIFIED"
    SHARE_COUNT_NOT_AVAILABLE = "SHARE_COUNT_NOT_AVAILABLE"
    # momentum
    RSI_WARMUP = "RSI_WARMUP"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    CORPORATE_ACTION_BLOCKS_MOMENTUM = "CORPORATE_ACTION_BLOCKS_MOMENTUM"
    ADJUSTED_PRICE_NOT_OK = "ADJUSTED_PRICE_NOT_OK"
    QC_BLOCKS_MOMENTUM = "QC_BLOCKS_MOMENTUM"
    WEEK_FINAL_BAR_MISSING = "WEEK_FINAL_BAR_MISSING"
    ZERO_REFERENCE = "ZERO_REFERENCE"
    LATEST_SESSION_BAR_MISSING = "LATEST_SESSION_BAR_MISSING"
    # valuation
    CLASS_MAPPING_MISSING = "CLASS_MAPPING_MISSING"
    CLASS_SET_MISMATCH = "CLASS_SET_MISMATCH"
    RAW_PRICE_NOT_OK = "RAW_PRICE_NOT_OK"
    SHARE_COUNT_NOT_OK = "SHARE_COUNT_NOT_OK"
    SPLIT_BASIS_UNKNOWN = "SPLIT_BASIS_UNKNOWN"
    SPLIT_BASIS_MISMATCH = "SPLIT_BASIS_MISMATCH"
    SHARES_PIT_VIOLATION = "SHARES_PIT_VIOLATION"
    CORPORATE_ACTION_BLOCKS_VALUATION = "CORPORATE_ACTION_BLOCKS_VALUATION"
    # EPS path (G6): price / directly reported diluted EPS
    EPS_SOURCE_MISSING = "EPS_SOURCE_MISSING"
    EPS_SOURCE_MALFORMED = "EPS_SOURCE_MALFORMED"
    EPS_NOT_REPORTED = "EPS_NOT_REPORTED"
    Q4_EPS_NOT_REPORTED = "Q4_EPS_NOT_REPORTED"
    EPS_PERIOD_DISCONTINUITY = "EPS_PERIOD_DISCONTINUITY"
    EPS_PERIOD_OVERLAP = "EPS_PERIOD_OVERLAP"
    EPS_PERIOD_LENGTH_INVALID = "EPS_PERIOD_LENGTH_INVALID"
    EPS_VALUE_CONFLICT = "EPS_VALUE_CONFLICT"
    EPS_UNIT_MISMATCH = "EPS_UNIT_MISMATCH"
    EPS_CONCEPT_MISMATCH = "EPS_CONCEPT_MISMATCH"
    EPS_SPLIT_BASIS_UNRESTATED = "EPS_SPLIT_BASIS_UNRESTATED"
    EPS_SPLIT_BASIS_UNVERIFIED = "EPS_SPLIT_BASIS_UNVERIFIED"
    EPS_SPLIT_BASIS_MISMATCH = "EPS_SPLIT_BASIS_MISMATCH"
    EPS_NONPOSITIVE_TTM = "EPS_NONPOSITIVE_TTM"
    EPS_NONPOSITIVE_FY = "EPS_NONPOSITIVE_FY"
    EPS_ROUNDED_TO_CENTS = "EPS_ROUNDED_TO_CENTS"
    EPS_TTM_FROM_FY_DIRECT = "EPS_TTM_FROM_FY_DIRECT"
    SEC_SPLIT_EVIDENCE_UNAVAILABLE = "SEC_SPLIT_EVIDENCE_UNAVAILABLE"
    NI_TTM_MISSING = "NI_TTM_MISSING"
    NI_TTM_PIT_VIOLATION = "NI_TTM_PIT_VIOLATION"
    NI_TTM_CONCEPT_MIXED = "NI_TTM_CONCEPT_MIXED"
    NI_TTM_CONCEPT_NOT_ALLOWED = "NI_TTM_CONCEPT_NOT_ALLOWED"
    NI_TTM_CURRENCY = "NI_TTM_CURRENCY"
    NI_TTM_SCOPE_UNCLEAR = "NI_TTM_SCOPE_UNCLEAR"
    NI_TTM_ATTRIBUTION_UNCLEAR = "NI_TTM_ATTRIBUTION_UNCLEAR"
    NI_TTM_PERIOD_INVALID = "NI_TTM_PERIOD_INVALID"
    NI_TTM_NONPOSITIVE = "NI_TTM_NONPOSITIVE"
    NI_TTM_UNVALIDATED = "NI_TTM_UNVALIDATED"
    NI_TTM_QUARTERS_INCOMPLETE = "NI_TTM_QUARTERS_INCOMPLETE"
    NI_TTM_QUARTER_DUPLICATE = "NI_TTM_QUARTER_DUPLICATE"
    NI_TTM_QUARTERS_NOT_CONSECUTIVE = "NI_TTM_QUARTERS_NOT_CONSECUTIVE"
    NI_TTM_QUARTER_LENGTH = "NI_TTM_QUARTER_LENGTH"
    NI_TTM_PERIOD_GAP = "NI_TTM_PERIOD_GAP"
    NI_TTM_PERIOD_OVERLAP = "NI_TTM_PERIOD_OVERLAP"
    NI_TTM_SUM_MISMATCH = "NI_TTM_SUM_MISMATCH"
    NI_TTM_PERIOD_ANOMALY = "NI_TTM_PERIOD_ANOMALY"
    NI_TTM_PROVENANCE_MISSING = "NI_TTM_PROVENANCE_MISSING"
    NI_TTM_ENTITY_MISMATCH = "NI_TTM_ENTITY_MISMATCH"
    # result
    FACT_NOT_ALLOWED = "FACT_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# Validation error (value-free messages)
# ---------------------------------------------------------------------------

class PriceDataValidationError(ValueError):
    """Raised for malformed price data. Message = reason code + field only."""

    def __init__(self, reason: str, field_name: str = "") -> None:
        self.reason = reason
        self.field_name = field_name
        super().__init__(f"{reason}:{field_name}" if field_name else reason)


def _require_decimal(value: object, field_name: str) -> Decimal:
    if value is None:
        raise PriceDataValidationError(Reason.MISSING_FIELD, field_name)
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise PriceDataValidationError(Reason.NON_DECIMAL_VALUE, field_name)
    if not value.is_finite():
        raise PriceDataValidationError(Reason.NON_DECIMAL_VALUE, field_name)
    return value


# ---------------------------------------------------------------------------
# NormalizedBar
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizedBar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    basis: Basis
    provider_id: str
    retrieved_at: datetime
    currency: str = CURRENCY_USD
    price_unit: str = PRICE_UNIT

    def __post_init__(self) -> None:
        if not isinstance(self.date, date) or isinstance(self.date, datetime):
            raise PriceDataValidationError(Reason.MISSING_FIELD, "date")
        for name in ("open", "high", "low", "close"):
            v = _require_decimal(getattr(self, name), name)
            if v < 0:
                raise PriceDataValidationError(Reason.NEGATIVE_VALUE, name)
        if self.volume is None:
            raise PriceDataValidationError(Reason.MISSING_FIELD, "volume")
        if isinstance(self.volume, bool) or not isinstance(self.volume, int):
            raise PriceDataValidationError(Reason.NON_DECIMAL_VALUE, "volume")
        if self.volume < 0:
            raise PriceDataValidationError(Reason.NEGATIVE_VALUE, "volume")
        if not isinstance(self.basis, Basis):
            raise PriceDataValidationError(Reason.MISSING_FIELD, "basis")
        if self.currency != CURRENCY_USD:
            raise PriceDataValidationError(Reason.WRONG_CURRENCY, "currency")
        if not (
            self.low <= self.high
            and self.low <= self.open <= self.high
            and self.low <= self.close <= self.high
        ):
            raise PriceDataValidationError(Reason.INVALID_OHLC, "ohlc")
        if not self.provider_id:
            raise PriceDataValidationError(Reason.MISSING_FIELD, "provider_id")
        if not isinstance(self.retrieved_at, datetime) or self.retrieved_at.tzinfo is None:
            raise PriceDataValidationError(Reason.MISSING_FIELD, "retrieved_at")


def validate_series(bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
                    *, as_of: date) -> None:
    """Validate one price series. Raises PriceDataValidationError."""
    if not bars:
        return
    basis = bars[0].basis
    prev: Optional[date] = None
    seen: set[date] = set()
    for bar in bars:
        if bar.basis is not basis:
            raise PriceDataValidationError(Reason.BASIS_MIXED, "basis")
        if bar.date in seen:
            raise PriceDataValidationError(Reason.DUPLICATE_DATE, "date")
        if prev is not None and bar.date < prev:
            raise PriceDataValidationError(Reason.DATES_NOT_ASCENDING, "date")
        if bar.date > as_of:
            raise PriceDataValidationError(Reason.FUTURE_DATE, "date")
        seen.add(bar.date)
        prev = bar.date


# ---------------------------------------------------------------------------
# SplitEvent
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SplitEvent:
    """
    One split-type corporate action.

    `execution_date` follows the provider definition: the first session
    that trades on the post-split basis.

    `historical_adjustment_factor` is carried as-is. Its direction and
    reference basis are UNVERIFIED (to be confirmed in the approved PoC);
    this engine never uses it as an input to a decision -- it is only
    reported, and any cross-check against it is marked UNVERIFIED.
    """

    symbol: str
    execution_date: date
    split_from: Decimal
    split_to: Decimal
    action_type: ActionType
    historical_adjustment_factor: Optional[Decimal] = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise PriceDataValidationError(Reason.MISSING_FIELD, "symbol")
        if not isinstance(self.execution_date, date):
            raise PriceDataValidationError(Reason.MISSING_FIELD, "execution_date")
        for name in ("split_from", "split_to"):
            v = getattr(self, name)
            if v is None or isinstance(v, bool) or not isinstance(v, Decimal) \
                    or not v.is_finite() or v <= 0:
                raise PriceDataValidationError(Reason.INVALID_SPLIT_RATIO, name)
        if not isinstance(self.action_type, ActionType):
            raise PriceDataValidationError(Reason.MISSING_FIELD, "action_type")

    @property
    def ratio(self) -> Decimal:
        """New shares per old share (split_to / split_from)."""
        return self.split_to / self.split_from


# ---------------------------------------------------------------------------
# Metric result (shared by momentum / valuation)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetricResult:
    """
    One computed metric. `value` is set only when calc_status is OK and
    interpretability is MEANINGFUL. `state` carries extra lifecycle info
    (e.g. RSI WARMUP / STABLE).
    """

    name: str
    calc_status: CalcStatus
    value: Optional[Decimal] = None
    interpretability: Optional[Interpretability] = None
    reasons: tuple[str, ...] = ()
    state: Optional[str] = None
    # provenance for SEC-derived metrics (EPS path): method, periods,
    # accession numbers, filing dates. Never price levels.
    evidence: Optional[dict] = None


# ---------------------------------------------------------------------------
# Provider responses / coverage
# ---------------------------------------------------------------------------

COVERAGE_SOURCE_DOCUMENTED = "DOCUMENTED"


@dataclass(frozen=True)
class ProviderCoverage:
    """
    Documented history coverage. Corporate-action "no event" evidence is
    accepted only when coverage_source == DOCUMENTED and BOTH splits bounds
    are known (inclusive). For a rolling window (e.g. "2 years") the caller
    computes both bounds for the evaluation date.
    """

    provider_id: str
    schema_version: str
    bars_history_start: Optional[date]
    splits_history_start: Optional[date]
    coverage_source: str  # "DOCUMENTED" or anything else (= not usable)
    splits_history_end: Optional[date] = None


@dataclass(frozen=True)
class PriceSeriesResponse:
    symbol: str
    basis: Basis
    requested_start: date
    requested_end: date
    actual_start: Optional[date]
    actual_end: Optional[date]
    bars: tuple[NormalizedBar, ...]
    provider_id: str
    schema_version: str
    retrieved_at: datetime
    is_partial: bool

    @property
    def data_as_of(self) -> Optional[date]:
        return self.actual_end


@dataclass(frozen=True)
class SplitEventsResponse:
    requested_start: date
    requested_end: date
    events: tuple[SplitEvent, ...]
    provider_id: str
    schema_version: str
    retrieved_at: datetime
    is_partial: bool
    symbols: Optional[tuple[str, ...]] = field(default=None)
