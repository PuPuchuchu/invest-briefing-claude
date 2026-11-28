"""
Price stage runner (Claude scheduled task R2 / GitHub runner R1).

Fetches daily bars and split events from Massive for the given tickers,
runs the common price engine and prints ONE JSON document to stdout:
  {"run": {...}, "results": [<price_result_v0.1>, ...], "briefing": {ticker: [lines]}}

Nothing is written to disk; price series never appear in the output --
only engine facts (RSI, % distance to SMAs, PE status), statuses and
reason codes (capture class EPHEMERAL_ONLY).

Usage (repository root on PYTHONPATH):
  python scripts/run_price_briefing.py --tickers AAPL,MSFT,NVDA \
      [--auth proxy|env] [--price-precision 0.01] [--last-evaluated YYYY-MM-DD]
  python scripts/run_price_briefing.py --tickers AAPL --structure-check

--structure-check prints only structure facts for the PoC (per-stage error
reasons, response key names and JSON types, counts, date coverage, decimal
places, split-event counts, request count) -- no prices or volumes.

Price precision: required for the split-adjustment QC and accepted ONLY as an
explicit positive decimal (provider-documented or PoC-verified value). Until
then omit it: QC NOT_AVAILABLE -> momentum withheld. Observed decimal places
are reported by --structure-check as diagnostics only and are never used in
calculations.

Exit codes: 0 done (per-ticker statuses inside), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.price.adjustment_qc import check_adjustment_qc  # noqa: E402
from src.price.calendar import expected_latest_session, midnight_et_after_session  # noqa: E402
from src.price.corporate_actions import assess_corporate_actions  # noqa: E402
from src.price.freshness import assess_freshness  # noqa: E402
from src.price.interpret import interpret  # noqa: E402
from src.price.momentum import compute_momentum  # noqa: E402
from src.price.provider import ProviderError  # noqa: E402
from src.price.result import ProviderMetadata, StatusBlock, build_result  # noqa: E402
from src.price.schema import (  # noqa: E402
    AdjustedPriceStatus, Basis, Freshness, RawPriceStatus, ShareCountStatus,
)
from src.price.valuation import compute_pe_ttm  # noqa: E402
from src.price_providers.calendar_io import load_calendar  # noqa: E402
from src.price_providers.massive import (  # noqa: E402
    ADAPTER_SCHEMA_VERSION, MassivePriceProvider, observed_decimal_places,
)

CONFIG_VERSION = "price_runner_v0.1"
LOOKBACK_DAYS = 430          # >= 200 sessions + 40 completed weeks, inside 2y coverage
ADJUSTMENT_METHOD = "SPLIT_ONLY(massive adjusted=true)"


def _parse_args(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", required=True)
    p.add_argument("--auth", choices=["proxy", "env"], default="proxy")
    p.add_argument("--price-precision", default=None)
    p.add_argument("--last-evaluated", default=None)
    p.add_argument("--now-utc", default=None, help="ISO datetime (tests only)")
    p.add_argument("--structure-check", action="store_true")
    return p.parse_args(argv)


def _precision(arg):
    """Verified precision only; None -> QC NOT_AVAILABLE. Bad input raises."""
    if arg is None:
        return None
    d = Decimal(arg)                      # raises InvalidOperation (ArithmeticError)
    if not d.is_finite() or d <= 0:
        raise ValueError("PRICE_PRECISION")
    return d


def evaluate_ticker(provider, calendar, symbol, start, expected, now_utc,
                    splits_resp, splits_failed, precision, last_evaluated):
    limitations: list[str] = []
    raw = provider.get_daily_bars(symbol, start, expected, Basis.RAW)
    adj = provider.get_daily_bars(symbol, start, expected, Basis.SPLIT_ADJUSTED)
    if raw.is_partial or adj.is_partial:
        limitations.append("BARS_PARTIAL_RESPONSE")
    fr = assess_freshness(calendar=calendar, now_utc=now_utc,
                          latest_bar_date=raw.data_as_of,
                          availability_rule=midnight_et_after_session,
                          last_evaluated_session=last_evaluated)
    fresh = fr.status is Freshness.FRESH
    raw_fresh = fresh and not raw.is_partial
    adj_fresh = fresh and not adj.is_partial and adj.data_as_of == raw.data_as_of
    if fresh and adj.data_as_of != raw.data_as_of:
        limitations.append("ADJUSTED_LATEST_SESSION_MISSING")
    rp = ap = precision
    qc = check_adjustment_qc(symbol=symbol, raw=raw.bars, adjusted=adj.bars,
                             events=() if splits_resp is None else splits_resp.events,
                             raw_precision=rp, adjusted_precision=ap)
    ca = assess_corporate_actions(symbol=symbol, window_start=start, window_end=expected,
                                  response=splits_resp, fetch_failed=splits_failed,
                                  qc=qc, coverage=provider.coverage())
    adj_status = AdjustedPriceStatus.OK if adj_fresh else (
        AdjustedPriceStatus.MISSING if adj.data_as_of is None else AdjustedPriceStatus.STALE)
    raw_status = RawPriceStatus.OK if raw_fresh else (
        RawPriceStatus.MISSING if raw.data_as_of is None else RawPriceStatus.STALE)
    metrics = compute_momentum(adjusted_bars=adj.bars, adjusted_status=adj_status,
                               corporate_actions=ca, qc=qc, calendar=calendar,
                               as_of_session=expected)
    # PE_TTM: no validated four-quarter NI_TTM evidence source yet (P4 G6).
    metrics["PE_TTM"] = compute_pe_ttm(prices=[], shares=[], class_mapping_resolved=False,
                                       corporate_actions=ca, ni_ttm=None,
                                       price_session=expected)
    statuses = StatusBlock(ca.status, qc.status, ShareCountStatus.NOT_AVAILABLE,
                           raw_status, adj_status)
    meta = ProviderMetadata(provider.provider_id, ADAPTER_SCHEMA_VERSION,
                            ADJUSTMENT_METHOD, raw.data_as_of, raw.retrieved_at)
    return build_result(ticker=symbol,
                        evaluation_date=now_utc.astimezone(ZoneInfo("Asia/Seoul")).date(),
                        metrics=metrics,
                        statuses=statuses, freshness=fr, provider=meta,
                        config_version=CONFIG_VERSION, extra_limitations=limitations)


def _stage(fn):
    try:
        return fn(), None
    except ProviderError as exc:
        return None, exc.reason


def structure_check(provider, symbol, start, expected):
    """Per-stage structure facts (error reasons, key names and JSON types,
    counts, dates, decimal places) -- never price, volume or other values."""
    out = {"ticker": symbol}
    shape, err = _stage(lambda: provider.probe_shape(
        provider.bars_url(symbol, start, expected, Basis.RAW)))
    out["raw_page_shape"] = shape if err is None else {"error": err}
    shape, err = _stage(lambda: provider.probe_shape(
        provider.splits_url(start, expected, (symbol,))))
    out["splits_page_shape"] = shape if err is None else {"error": err}
    raw, e1 = _stage(lambda: provider.get_daily_bars(symbol, start, expected, Basis.RAW))
    adj, e2 = _stage(lambda: provider.get_daily_bars(symbol, start, expected,
                                                     Basis.SPLIT_ADJUSTED))
    sp, e3 = _stage(lambda: provider.get_split_events(start, expected, [symbol]))
    out["errors"] = {k: v for k, v in (("raw_bars", e1), ("adjusted_bars", e2),
                                       ("splits", e3)) if v}
    if raw is None or adj is None or sp is None:
        return out
    return out | {
        "raw_bars": len(raw.bars), "adjusted_bars": len(adj.bars),
        "raw_first": None if raw.actual_start is None else raw.actual_start.isoformat(),
        "raw_last": None if raw.actual_end is None else raw.actual_end.isoformat(),
        "dates_equal": [b.date for b in raw.bars] == [b.date for b in adj.bars],
        "raw_decimal_places": observed_decimal_places(raw.bars),
        "adjusted_decimal_places": observed_decimal_places(adj.bars),
        "partial": raw.is_partial or adj.is_partial or sp.is_partial,
        "split_events": len(sp.events),
        "split_types": sorted({e.action_type.value for e in sp.events}),
        "factor_present": sum(1 for e in sp.events
                              if e.historical_adjustment_factor is not None),
    }


def main(argv=None) -> int:
    args = _parse_args(argv)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        return 2
    try:
        precision = _precision(args.price_precision)
    except (ValueError, ArithmeticError):
        return 2
    now_utc = (datetime.fromisoformat(args.now_utc) if args.now_utc
               else datetime.now(timezone.utc))
    calendar = load_calendar()
    expected = expected_latest_session(calendar, now_utc, midnight_et_after_session)
    start = expected - timedelta(days=LOOKBACK_DAYS)
    last_eval = date.fromisoformat(args.last_evaluated) if args.last_evaluated else None
    provider = MassivePriceProvider(as_of=expected, auth_mode=args.auth.upper())
    coverage = provider.coverage()
    if start < coverage.bars_history_start:
        start = coverage.bars_history_start
    run = {"config_version": CONFIG_VERSION, "calendar_version": calendar.calendar_version,
           "expected_session": expected.isoformat(), "window_start": start.isoformat(),
           "tickers": tickers, "capture_class": "EPHEMERAL_ONLY"}

    if args.structure_check:
        out = []
        for t in tickers:
            try:
                out.append(structure_check(provider, t, start, expected))
            except ProviderError as exc:
                out.append({"ticker": t, "error": exc.reason})
        run["request_count"] = provider.request_count
        print(json.dumps({"run": run, "structure": out}, ensure_ascii=False, indent=1))
        return 0

    splits_resp, splits_failed = None, False
    try:
        splits_resp = provider.get_split_events(start, expected, tickers)
    except ProviderError as exc:
        splits_failed = True
        run["splits_error"] = exc.reason
    results, briefing = [], {}
    for t in tickers:
        try:
            r = evaluate_ticker(provider, calendar, t, start, expected, now_utc,
                                splits_resp, splits_failed, precision, last_eval)
            results.append(r)
            briefing[t] = interpret(r)
        except ProviderError as exc:
            results.append({"ticker": t, "error": exc.reason, "validation":
                            {"data_success": False}})
            briefing[t] = [f"[{t}] 가격 데이터 오류: {exc.reason}"]
    run["request_count"] = provider.request_count
    run["data_success_all"] = all(r.get("validation", {}).get("data_success")
                                  for r in results)
    print(json.dumps({"run": run, "results": results, "briefing": briefing},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
