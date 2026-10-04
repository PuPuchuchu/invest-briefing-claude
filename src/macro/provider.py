"""
Macro Data Provider abstraction (Framework v2.1 Step 10B, 2026-10-04).

This module defines ONLY the shape a future live FRED/ALFRED provider
must satisfy -- it does NOT implement one. Per this round's absolute
constraints (and Step 10A's CONDITIONAL PASS gate -- see
claude/2026-10-04-step10a-connectivity-feasibility.md -- which left real
production FRED connectivity UNKNOWN), no HTTP client, no API key
handling, and no network call exists anywhere in this module or this
package.

The abstraction exists so that, in a future round (gated on the
connectivity smoke-test recommended in the Step 10A report), a real
`FredMacroProvider` can be dropped in without touching
src/macro/transforms.py, src/macro/*_state.py, src/macro/regime.py, or
src/macro/engine.py at all -- exactly the "Provider layer vs.
Transforms/State Engine/Regime Engine" separation the Step 10 v2/v3
spec lays out:

    Data Provider Interface -> FRED/ALFRED Provider -> Raw Macro Data
        -> Transforms -> State Engine -> Regime Engine

This module provides the first box and, for THIS round only, an
in-memory/synthetic implementation of it (`InMemoryMacroProvider`) so
every test in this round can run with zero network access.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

SCHEMA_VERSION = "macro_provider_v0.1"


class MacroDataProvider(ABC):
    """
    Minimal interface any macro data source (live or synthetic) must
    satisfy. A real FRED/ALFRED-backed implementation is explicitly OUT
    OF SCOPE this round (see module docstring) -- this class exists only
    to pin down the call shape the rest of src/macro/ is built against.
    """

    @abstractmethod
    def get_series(
        self,
        series_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        realtime_start: str | None = None,
        realtime_end: str | None = None,
    ) -> list[dict]:
        """
        Return raw macro observations (see src/macro/schema.py's
        RAW_OBSERVATION_FIELDS shape) for `series_id`. `start_date` /
        `end_date` bound the economic observation_date range requested;
        `realtime_start` / `realtime_end` -- when given -- bound the
        vintage/knowability range requested, mirroring the exact FRED API
        parameter names confirmed live in Step 10A
        (claude/2026-10-04-step10a-connectivity-feasibility.md section
        2-#5), so a future real provider's method signature needs no
        translation layer.

        Implementations decide for themselves how to interpret missing
        start_date/end_date/realtime_start/realtime_end (e.g. "return
        everything available") -- this interface does not mandate a
        default.
        """
        raise NotImplementedError


class InMemoryMacroProvider(MacroDataProvider):
    """
    Synthetic/fixture-backed MacroDataProvider for this round's tests.
    Holds a plain {series_id: [raw observation dict, ...]} mapping
    supplied at construction time and returns it verbatim (the
    start_date/end_date/realtime_start/realtime_end filters, if given,
    are applied as simple inclusive-range filters on observation_date /
    realtime_start respectively -- no PIT vintage-selection logic lives
    here; that is src/macro/pit.py's job, applied by the caller on the
    returned raw rows, exactly as it would be applied to a real
    provider's output).

    Never performs any network call, never fetches anything -- this is
    pure in-memory lookup over data the caller already supplied.
    """

    def __init__(self, series_by_id: dict[str, list[dict]]):
        self._series_by_id = series_by_id

    def get_series(
        self,
        series_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        realtime_start: str | None = None,
        realtime_end: str | None = None,
    ) -> list[dict]:
        rows = list(self._series_by_id.get(series_id, []))

        if start_date is not None:
            rows = [r for r in rows if r.get("observation_date", "") >= start_date]
        if end_date is not None:
            rows = [r for r in rows if r.get("observation_date", "") <= end_date]
        if realtime_start is not None:
            rows = [r for r in rows if (r.get("realtime_start") or "") >= realtime_start]
        if realtime_end is not None:
            rows = [r for r in rows if (r.get("realtime_start") or "") <= realtime_end]

        return rows


__all__ = [
    "SCHEMA_VERSION",
    "MacroDataProvider",
    "InMemoryMacroProvider",
]
