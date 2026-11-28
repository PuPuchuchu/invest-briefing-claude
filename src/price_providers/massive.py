"""
Massive (formerly Polygon.io) Stocks Basic provider for src/price.

Documented basis (read 2026-11-26/27, see P3 v4 design docs):
  * Custom bars  GET /v2/aggs/ticker/{t}/range/1/day/{from}/{to}
      adjusted=true -> split-adjusted; adjusted=false -> raw. `t` is the
      Unix ms start of the aggregate window (ET-based). Bars only on days
      with trades.
  * Splits       GET /stocks/v1/splits  (execution_date = first
      post-split session; split_from / split_to; adjustment_type in
      forward_split / reverse_split / stock_dividend)
  * Auth         `Authorization: Bearer <key>` (or apiKey query; NOT used)
  * Basic plan   end-of-day, ~2 years history, 5 requests / minute

UNVERIFIED until the live PoC: exact price precision, whether "2 years" is
rolling, daily call cap, whether bar resultsCount is per page (assumed:
equals len(results); a mismatch fails closed and shows in --structure-check).

Auth modes:
  PROXY  -- send no credential; an egress proxy (Claude cloud environment
            "network secret") injects the header for api.massive.com.
  ENV    -- read the key from an environment variable at request time
            (GitHub Actions secret). The key is never stored on the object,
            never logged and never included in exception messages.

Redirects are never followed (any 3xx -> ProviderError(REDIRECT_REFUSED)),
so a credential can never be forwarded to another origin; the ENV-mode
Authorization header is additionally marked "unredirected".

Response contract per page (docs read 2026-11-28; anything else fails
closed, so an API error or inconsistent page can never look like
"no split events"):
  * bars   -- status OK or DELAYED; `resultsCount` ("total number of
              results for this request", documented integer field) is
              REQUIRED and must equal len(results); `results` may be
              omitted only together with resultsCount == 0.
  * splits -- status OK (documented enum); `results` is mandatory (the
              endpoint documents no count field).
  * Any count field present (resultsCount, count, queryCount) must be a
    non-negative integer (never bool/str); resultsCount and count, if
    both present, must agree. Every row must be an object.
Confirmed by the same docs: splits filters execution_date.gte/.lte,
ticker; limit max 5000; JSON pagination via next_url.

The provider respects the documented rate limit with a minimum interval
between requests and never retries to circumvent limits (Individuals
ToS 6.1(o)): HTTP 429 raises ProviderError(RATE_LIMITED).

No disk writes, no logging, no payload/URL in errors.
"""

from __future__ import annotations

import json
import os
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Iterable, Optional, Protocol

from src.price.calendar import NY_TZ
from src.price.provider import PriceDataProvider, ProviderError
from src.price.schema import (
    ActionType,
    Basis,
    NormalizedBar,
    PriceDataValidationError,
    PriceSeriesResponse,
    ProviderCoverage,
    SCHEMA_VERSION,
    SplitEvent,
    SplitEventsResponse,
    validate_series,
)

BASE_URL = "https://api.massive.com"
PROVIDER_ID = "massive_stocks_basic"
ADAPTER_SCHEMA_VERSION = "massive_adapter_v0.1"
MIN_REQUEST_INTERVAL_SECONDS = 12.5          # 5 requests/minute, with margin
MAX_PAGES = 20
DOCUMENTED_HISTORY_YEARS = 2

RATE_LIMITED = "RATE_LIMITED"
AUTH_FAILED = "AUTH_FAILED"
HTTP_ERROR = "HTTP_ERROR"
NETWORK_ERROR = "NETWORK_ERROR"
MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
TOO_MANY_PAGES = "TOO_MANY_PAGES"
KEY_MISSING = "KEY_MISSING"
UNKNOWN_ADJUSTMENT_TYPE = "UNKNOWN_ADJUSTMENT_TYPE"
REDIRECT_REFUSED = "REDIRECT_REFUSED"
BAR_STATUSES = ("OK", "DELAYED")
SPLIT_STATUSES = ("OK",)
_COUNT_FIELDS = ("resultsCount", "count", "queryCount")

_ACTION = {
    "forward_split": ActionType.FORWARD_SPLIT,
    "reverse_split": ActionType.REVERSE_SPLIT,
    "stock_dividend": ActionType.STOCK_DIVIDEND,
}


class Transport(Protocol):
    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]: ...


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the 3xx surfaces as HTTPError to the caller."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects())


def urllib_transport(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        if k.lower() == "authorization":
            req.add_unredirected_header(k, v)
        else:
            req.add_header(k, v)
    try:
        with _OPENER.open(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError(NETWORK_ERROR) from None


def _to_decimal(v: object) -> Decimal:
    if isinstance(v, bool):
        raise ProviderError(MALFORMED_RESPONSE)
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    raise ProviderError(MALFORMED_RESPONSE)


def _to_volume(v: object) -> int:
    d = _to_decimal(v)
    if d != d.to_integral_value():
        raise ProviderError(MALFORMED_RESPONSE)
    return int(d)


def bar_session_date(t_ms: object) -> date:
    """Unix ms (window start, ET-based) -> US/Eastern calendar date."""
    ms = _to_decimal(t_ms)
    ts = datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)
    return ts.astimezone(NY_TZ).date()


class MassivePriceProvider(PriceDataProvider):
    provider_id = PROVIDER_ID
    schema_version = ADAPTER_SCHEMA_VERSION

    def __init__(self, *, as_of: date, auth_mode: str = "PROXY",
                 key_env_var: str = "MASSIVE_API_KEY",
                 transport: Transport = urllib_transport,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 sleep: Callable[[float], None] = _time.sleep,
                 monotonic: Callable[[], float] = _time.monotonic,
                 min_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
                 base_url: str = BASE_URL) -> None:
        if auth_mode not in ("PROXY", "ENV"):
            raise ValueError("AUTH_MODE")
        self._as_of = as_of
        self._auth_mode = auth_mode
        self._key_env_var = key_env_var
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._monotonic = monotonic
        self._min_interval = min_interval
        self._base = base_url.rstrip("/")
        self._last_request: Optional[float] = None
        self.request_count = 0

    def __repr__(self) -> str:  # never expose configuration secrets
        return f"MassivePriceProvider(auth_mode={self._auth_mode})"

    # ----------------------------------------------------------------- http
    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._auth_mode == "ENV":
            key = os.environ.get(self._key_env_var, "")
            if not key:
                raise ProviderError(KEY_MISSING)
            h["Authorization"] = "Bearer " + key
        return h

    def _get_json(self, url: str) -> dict:
        if self._last_request is not None:
            wait = self._min_interval - (self._monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        try:
            status, body = self._transport(url, self._headers())
        finally:
            self._last_request = self._monotonic()
            self.request_count += 1
        if 300 <= status < 400:
            raise ProviderError(REDIRECT_REFUSED)
        if status == 429:
            raise ProviderError(RATE_LIMITED)
        if status in (401, 403):
            raise ProviderError(AUTH_FAILED)
        if status != 200:
            raise ProviderError(HTTP_ERROR)
        try:
            data = json.loads(body, parse_float=Decimal)
        except (ValueError, UnicodeDecodeError):
            raise ProviderError(MALFORMED_RESPONSE) from None
        if not isinstance(data, dict):
            raise ProviderError(MALFORMED_RESPONSE)
        return data

    @staticmethod
    def _page_rows(data: dict, statuses: tuple[str, ...],
                   allow_omitted_results: bool,
                   require_results_count: bool = False) -> list:
        if data.get("status") not in statuses:
            raise ProviderError(MALFORMED_RESPONSE + ":STATUS")
        counts = {}
        for k in _COUNT_FIELDS:
            if k in data:
                v = data[k]
                if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                    raise ProviderError(MALFORMED_RESPONSE + ":COUNT_TYPE")
                counts[k] = v
        if ("resultsCount" in counts and "count" in counts
                and counts["resultsCount"] != counts["count"]):
            raise ProviderError(MALFORMED_RESPONSE + ":COUNT_CONFLICT")
        if "results" in data:
            res = data["results"]
        elif allow_omitted_results and counts.get("resultsCount") == 0:
            res = []                           # explicit, verified empty page
        else:
            raise ProviderError(MALFORMED_RESPONSE + ":RESULTS_MISSING")
        if not isinstance(res, list):
            raise ProviderError(MALFORMED_RESPONSE + ":RESULTS_TYPE")
        if not all(isinstance(r, dict) for r in res):
            raise ProviderError(MALFORMED_RESPONSE + ":ROW_TYPE")
        for k in ("resultsCount", "count"):
            if k in counts and counts[k] != len(res):
                raise ProviderError(MALFORMED_RESPONSE + ":COUNT_MISMATCH")
        if require_results_count and "resultsCount" not in counts:
            raise ProviderError(MALFORMED_RESPONSE + ":RESULTS_COUNT_MISSING")
        return res

    def _paged(self, first_url: str, *, statuses: tuple[str, ...],
               allow_omitted_results: bool,
               require_results_count: bool = False) -> tuple[list[dict], bool]:
        """All `results` across pages. Returns (rows, complete)."""
        rows: list[dict] = []
        url: Optional[str] = first_url
        pages = 0
        while url:
            if pages >= MAX_PAGES:
                return rows, False
            data = self._get_json(url)
            pages += 1
            rows.extend(self._page_rows(data, statuses, allow_omitted_results,
                                        require_results_count))
            nxt = data.get("next_url")
            if nxt is not None and not isinstance(nxt, str):
                raise ProviderError(MALFORMED_RESPONSE)
            if nxt and not nxt.startswith(self._base + "/"):
                raise ProviderError(MALFORMED_RESPONSE)  # never follow foreign hosts
            url = nxt or None
        return rows, True

    # ----------------------------------------------------------- interface
    def get_daily_bars(self, symbol: str, start: date, end: date,
                       basis: Basis) -> PriceSeriesResponse:
        adjusted = "true" if basis is Basis.SPLIT_ADJUSTED else "false"
        url = (f"{self._base}/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/1/day/"
               f"{start.isoformat()}/{end.isoformat()}"
               f"?adjusted={adjusted}&sort=asc&limit=50000")
        rows, complete = self._paged(url, statuses=BAR_STATUSES,
                                     allow_omitted_results=True,
                                     require_results_count=True)
        retrieved = self._clock()
        bars = []
        try:
            for r in rows:
                d = bar_session_date(r.get("t"))
                if d < start or d > end:
                    continue
                bars.append(NormalizedBar(
                    date=d, open=_to_decimal(r.get("o")), high=_to_decimal(r.get("h")),
                    low=_to_decimal(r.get("l")), close=_to_decimal(r.get("c")),
                    volume=_to_volume(r.get("v")), basis=basis,
                    provider_id=self.provider_id, retrieved_at=retrieved))
            bars_t = tuple(bars)
            validate_series(bars_t, as_of=end)
        except PriceDataValidationError as exc:
            raise ProviderError(MALFORMED_RESPONSE + ":" + exc.reason) from None
        return PriceSeriesResponse(
            symbol=symbol, basis=basis, requested_start=start, requested_end=end,
            actual_start=bars_t[0].date if bars_t else None,
            actual_end=bars_t[-1].date if bars_t else None,
            bars=bars_t, provider_id=self.provider_id,
            schema_version=self.schema_version, retrieved_at=retrieved,
            is_partial=not complete)

    def get_split_events(self, start: date, end: date,
                         symbols: Optional[Iterable[str]] = None
                         ) -> SplitEventsResponse:
        wanted = None if symbols is None else tuple(sorted(set(symbols)))
        params = [("execution_date.gte", start.isoformat()),
                  ("execution_date.lte", end.isoformat()),
                  ("limit", "5000")]
        if wanted is not None and len(wanted) == 1:
            params.append(("ticker", wanted[0]))
        url = f"{self._base}/stocks/v1/splits?" + urllib.parse.urlencode(params)
        rows, complete = self._paged(url, statuses=SPLIT_STATUSES,
                                     allow_omitted_results=False)
        events = []
        try:
            for r in rows:
                sym = r.get("ticker")
                if not isinstance(sym, str):
                    raise ProviderError(MALFORMED_RESPONSE)
                if wanted is not None and sym not in wanted:
                    continue
                kind = _ACTION.get(r.get("adjustment_type"))
                if kind is None:
                    raise ProviderError(UNKNOWN_ADJUSTMENT_TYPE)
                ex = date.fromisoformat(str(r.get("execution_date")))
                if ex < start or ex > end:
                    continue
                haf = r.get("historical_adjustment_factor")
                events.append(SplitEvent(
                    symbol=sym, execution_date=ex,
                    split_from=_to_decimal(r.get("split_from")),
                    split_to=_to_decimal(r.get("split_to")), action_type=kind,
                    historical_adjustment_factor=None if haf is None else _to_decimal(haf)))
        except (PriceDataValidationError, ValueError):
            raise ProviderError(MALFORMED_RESPONSE) from None
        return SplitEventsResponse(
            requested_start=start, requested_end=end, events=tuple(events),
            provider_id=self.provider_id, schema_version=self.schema_version,
            retrieved_at=self._clock(), is_partial=not complete, symbols=wanted)

    def coverage(self) -> ProviderCoverage:
        """Documented Basic coverage: ~2 years back from as_of (+1 day margin)."""
        try:
            start = self._as_of.replace(year=self._as_of.year - DOCUMENTED_HISTORY_YEARS)
        except ValueError:  # Feb 29
            start = self._as_of - timedelta(days=365 * DOCUMENTED_HISTORY_YEARS)
        start += timedelta(days=1)
        return ProviderCoverage(provider_id=self.provider_id,
                                schema_version=SCHEMA_VERSION,
                                bars_history_start=start, splits_history_start=start,
                                coverage_source="DOCUMENTED",
                                splits_history_end=self._as_of)


def observed_decimal_places(bars: Iterable[NormalizedBar]) -> Optional[int]:
    """Max decimal places seen in OHLC values (structure check only)."""
    best: Optional[int] = None
    for b in bars:
        for v in (b.open, b.high, b.low, b.close):
            exp = v.normalize().as_tuple().exponent
            places = -exp if isinstance(exp, int) and exp < 0 else 0
            best = places if best is None else max(best, places)
    return best
