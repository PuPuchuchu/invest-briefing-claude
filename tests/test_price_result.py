import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from src.price.adjustment_qc import check_adjustment_qc
from src.price.calendar import TradingCalendar, midnight_et_after_session
from src.price.corporate_actions import assess_corporate_actions
from src.price.freshness import assess_freshness
from src.price.interpret import interpret
from src.price.momentum import compute_momentum
from src.price.result import ALLOWED_FACTS, ProviderMetadata, StatusBlock, build_result
from src.price.schema import (
    AdjustedPriceStatus, Basis, CalcStatus, CaptureClass, MetricResult,
    NormalizedBar, PriceDataValidationError, ProviderCoverage, RawPriceStatus,
    Reason, SCHEMA_VERSION, ShareCountStatus, SplitEventsResponse,
)
from src.price.valuation import (
    NI_CONCEPT_COMMON, SCOPE_CONSOLIDATED_NO_DIMENSION, ClassPriceInput,
    ClassSharesInput, NiTtmInput, compute_pe_ttm,
)

MARKER = "987654.321"          # synthetic marker price -- must never leak
TS = datetime(2026, 11, 24, 5, 10, tzinfo=timezone.utc)
CAL = TradingCalendar(holidays=frozenset())
COV = ProviderCoverage("synthetic", SCHEMA_VERSION, date(2024, 11, 1),
                       date(2024, 11, 1), "SYNTHETIC")


def weekdays_until(end, n):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def marker_bars(basis, days):
    out = []
    for i, d in enumerate(days):
        c = D(MARKER) + D(i % 5)
        out.append(NormalizedBar(d, c, c, c, c, 7, basis, "synthetic", TS))
    return out


def pipeline():
    days = weekdays_until(date(2026, 11, 23), 260)
    raw = marker_bars(Basis.RAW, days)
    adj = marker_bars(Basis.SPLIT_ADJUSTED, days)
    qc = check_adjustment_qc(raw=raw, adjusted=adj, events=[],
                             raw_precision=D("0.001"), adjusted_precision=D("0.001"))
    ca = assess_corporate_actions(symbol="AAA", window_start=days[0],
                                  window_end=days[-1],
                                  response=SplitEventsResponse(days[0], days[-1], (),
                                                               "synthetic", SCHEMA_VERSION,
                                                               TS, False),
                                  fetch_failed=False,
                                  qc=qc, coverage=COV)
    metrics = compute_momentum(adjusted_bars=adj, adjusted_status=AdjustedPriceStatus.OK,
                               corporate_actions=ca, qc=qc, calendar=CAL,
                               as_of_session=days[-1])
    d0 = days[-1]
    metrics["PE_TTM"] = compute_pe_ttm(
        prices=[ClassPriceInput("A", raw[-1].close, RawPriceStatus.OK, d0)],
        shares=[ClassSharesInput("A", D(1000), ShareCountStatus.OK,
                                 date(2026, 10, 20), date(2026, 10, 30), d0)],
        class_mapping_resolved=True, corporate_actions=ca,
        ni_ttm=NiTtmInput(D(123456), (NI_CONCEPT_COMMON,), "USD", date(2025, 10, 1),
                          date(2026, 9, 30), date(2026, 10, 30),
                          SCOPE_CONSOLIDATED_NO_DIMENSION, "COMMON_STOCKHOLDERS"),
        price_session=d0)
    fr = assess_freshness(calendar=CAL, now_utc=TS, latest_bar_date=d0,
                          availability_rule=midnight_et_after_session)
    statuses = StatusBlock(ca.status, qc.status, ShareCountStatus.OK,
                           RawPriceStatus.OK, AdjustedPriceStatus.OK)
    meta = ProviderMetadata("synthetic", SCHEMA_VERSION,
                            "SPLIT_ONLY(provider adjusted=true)", d0, TS)
    return build_result(ticker="AAA", evaluation_date=date(2026, 11, 24),
                        metrics=metrics, statuses=statuses, freshness=fr,
                        provider=meta, config_version="cfg-test")


def test_result_shape_and_capture_class():
    r = pipeline()
    assert r["capture_class"] == CaptureClass.EPHEMERAL_ONLY.value
    assert set(r) >= {"facts", "status", "freshness", "limitations", "validation",
                      "reason_codes", "provider", "calculation_version",
                      "config_version"}
    assert {f["metric"] for f in r["facts"]} == set(ALLOWED_FACTS)
    assert r["validation"]["data_success"] is True
    json.dumps(r)  # serialisable


def test_no_ohlcv_or_price_level_fields():
    text = json.dumps(pipeline())
    for forbidden in ('"open"', '"high"', '"low"', '"close"', '"volume"', '"bars"',
                      "SMA_50D\"", "SMA_200D\"", '"raw_close"'):
        assert forbidden not in text


def test_disallowed_fact_rejected():
    with pytest.raises(ValueError):
        build_result(ticker="AAA", evaluation_date=date(2026, 11, 24),
                     metrics={"CLOSE": MetricResult("CLOSE", CalcStatus.OK, D(1))},
                     statuses=StatusBlock(*([None] * 5)), freshness=None,
                     provider=None, config_version="x")


def test_marker_does_not_leak_anywhere(caplog, capsys):
    caplog.set_level("DEBUG")
    r = pipeline()
    lines = interpret(r)
    with pytest.raises(PriceDataValidationError) as e:
        NormalizedBar(date(2026, 11, 23), D(MARKER), D("1"), D("1"), D(MARKER), 1,
                      Basis.RAW, "synthetic", TS)
    out = capsys.readouterr()
    haystacks = [json.dumps(r), "\n".join(lines), caplog.text, out.out, out.err,
                 str(e.value), repr(e.value)]
    for h in haystacks:
        assert MARKER not in h
        assert "987654" not in h


SRC = Path(__file__).resolve().parents[1] / "src" / "price"
FORBIDDEN = [r"\bopen\(", r"\.write\(", r"\bprint\(", r"^\s*(import|from)\s+(requests|"
             r"urllib|http|socket|pickle|shelve|sqlite3|logging|tempfile|os)\b"]


def test_ephemeral_only_no_io_network_or_logging_in_package():
    files = sorted(SRC.glob("*.py"))
    assert len(files) == 11
    for f in files:
        src = f.read_text(encoding="utf-8")
        for pat in FORBIDDEN:
            assert not re.search(pat, src, re.M), (f.name, pat)
