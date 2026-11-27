"""
Price data provider abstraction.

Follows the pattern of src/macro/provider.py: an ABC that pins down the
call shape, plus an in-memory synthetic implementation so every test in
this round runs with zero network access. No HTTP client, no API key
handling and no vendor SDK exist anywhere in this package; a real
provider (e.g. Massive Stocks Basic) is OUT OF SCOPE until a separately
approved PoC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Callable, Iterable, Optional

from src.price.schema import (
    SCHEMA_VERSION,
    Basis,
    NormalizedBar,
    PriceSeriesResponse,
    ProviderCoverage,
    Reason,
    SplitEvent,
    SplitEventsResponse,
    validate_series,
)


class ProviderError(RuntimeError):
    """Provider failure. Carries a reason code only -- never payload data."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class PriceDataProvider(ABC):
    """Interface any price source (synthetic or, later, real) must satisfy."""

    provider_id: str
    schema_version: str = SCHEMA_VERSION

    @abstractmethod
    def get_daily_bars(self, symbol: str, start: date, end: date,
                       basis: Basis) -> PriceSeriesResponse:
        """Daily bars for [start, end] on one basis. Raises ProviderError."""

    @abstractmethod
    def get_split_events(self, start: date, end: date,
                         symbols: Optional[Iterable[str]] = None
                         ) -> SplitEventsResponse:
        """
        Split-type events with execution_date in [start, end].

        Contract: an implementation MUST return the complete result for the
        requested range (all pages). If any page could not be fetched, the
        response MUST be marked is_partial=True -- an empty list is treated
        as "no event" evidence only when the response is complete.
        """

    @abstractmethod
    def coverage(self) -> ProviderCoverage:
        """Documented history coverage of this provider."""


class InMemoryPriceProvider(PriceDataProvider):
    """
    Synthetic provider for tests. Returns only the fixtures it was given.

    `clock` supplies `retrieved_at` (tz-aware UTC datetime); injected so
    tests are deterministic. `fail_bars` / `fail_splits` simulate provider
    failures. `partial_symbols` marks responses for those symbols as
    partial.
    """

    def __init__(
        self,
        *,
        bars: dict[tuple[str, Basis], list[NormalizedBar]],
        events: list[SplitEvent],
        coverage: ProviderCoverage,
        clock: Callable[[], datetime],
        provider_id: str = "in_memory_synthetic",
        fail_bars: bool = False,
        fail_splits: bool = False,
        partial_symbols: Iterable[str] = (),
    ) -> None:
        self.provider_id = provider_id
        self._bars = {k: tuple(v) for k, v in bars.items()}
        self._events = tuple(events)
        self._coverage = coverage
        self._clock = clock
        self._fail_bars = fail_bars
        self._fail_splits = fail_splits
        self._partial = frozenset(partial_symbols)

    def get_daily_bars(self, symbol: str, start: date, end: date,
                       basis: Basis) -> PriceSeriesResponse:
        if self._fail_bars:
            raise ProviderError(Reason.BARS_FETCH_FAILED)
        series = self._bars.get((symbol, basis), ())
        selected = tuple(b for b in series if start <= b.date <= end)
        validate_series(selected, as_of=end)
        return PriceSeriesResponse(
            symbol=symbol,
            basis=basis,
            requested_start=start,
            requested_end=end,
            actual_start=selected[0].date if selected else None,
            actual_end=selected[-1].date if selected else None,
            bars=selected,
            provider_id=self.provider_id,
            schema_version=self.schema_version,
            retrieved_at=self._clock(),
            is_partial=symbol in self._partial,
        )

    def get_split_events(self, start: date, end: date,
                         symbols: Optional[Iterable[str]] = None
                         ) -> SplitEventsResponse:
        if self._fail_splits:
            raise ProviderError(Reason.SPLIT_FETCH_FAILED)
        wanted = None if symbols is None else frozenset(symbols)
        selected = tuple(
            e for e in self._events
            if start <= e.execution_date <= end
            and (wanted is None or e.symbol in wanted)
        )
        return SplitEventsResponse(
            requested_start=start,
            requested_end=end,
            events=selected,
            provider_id=self.provider_id,
            schema_version=self.schema_version,
            retrieved_at=self._clock(),
            is_partial=False,
            symbols=None if wanted is None else tuple(sorted(wanted)),
        )

    def coverage(self) -> ProviderCoverage:
        return self._coverage
