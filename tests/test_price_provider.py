from datetime import date, datetime, timezone
from decimal import Decimal as D

import pytest

from src.price.provider import InMemoryPriceProvider, PriceDataProvider, ProviderError
from src.price.schema import (
    ActionType, Basis, NormalizedBar, ProviderCoverage, Reason, SCHEMA_VERSION,
    SplitEvent,
)

TS = datetime(2026, 11, 27, 5, 10, tzinfo=timezone.utc)
COV = ProviderCoverage("synthetic", SCHEMA_VERSION, date(2024, 11, 27),
                       date(2024, 11, 27), "SYNTHETIC")


def bar(day, close):
    c = D(close)
    return NormalizedBar(day, c, c, c, c, 1, Basis.RAW, "synthetic", TS)


def make(**kw):
    bars = {("AAA", Basis.RAW): [bar(date(2026, 11, d), "10") for d in (23, 24, 25)]}
    events = [SplitEvent("AAA", date(2026, 6, 1), D(1), D(2), ActionType.FORWARD_SPLIT),
              SplitEvent("BBB", date(2026, 7, 1), D(1), D(3), ActionType.FORWARD_SPLIT)]
    return InMemoryPriceProvider(bars=bars, events=events, coverage=COV,
                                 clock=lambda: TS, provider_id="synthetic", **kw)


def test_is_abc_implementation():
    assert isinstance(make(), PriceDataProvider)
    with pytest.raises(TypeError):
        PriceDataProvider()  # abstract


def test_bars_metadata():
    r = make().get_daily_bars("AAA", date(2026, 11, 20), date(2026, 11, 24), Basis.RAW)
    assert r.requested_start == date(2026, 11, 20)
    assert r.actual_start == date(2026, 11, 23) and r.actual_end == date(2026, 11, 24)
    assert r.data_as_of == date(2026, 11, 24)
    assert r.provider_id == "synthetic" and r.schema_version == SCHEMA_VERSION
    assert r.retrieved_at == TS and r.is_partial is False and len(r.bars) == 2


def test_empty_and_partial():
    p = make(partial_symbols=["AAA"])
    r = p.get_daily_bars("AAA", date(2026, 11, 23), date(2026, 11, 25), Basis.RAW)
    assert r.is_partial
    e = p.get_daily_bars("ZZZ", date(2026, 11, 23), date(2026, 11, 25), Basis.RAW)
    assert e.bars == () and e.actual_end is None


def test_split_filter():
    r = make().get_split_events(date(2026, 1, 1), date(2026, 12, 31), ["AAA"])
    assert [e.symbol for e in r.events] == ["AAA"]
    allr = make().get_split_events(date(2026, 1, 1), date(2026, 12, 31))
    assert len(allr.events) == 2


def test_failures_carry_reason_only():
    with pytest.raises(ProviderError) as e:
        make(fail_splits=True).get_split_events(date(2026, 1, 1), date(2026, 2, 1))
    assert e.value.reason == Reason.SPLIT_FETCH_FAILED
    with pytest.raises(ProviderError) as e:
        make(fail_bars=True).get_daily_bars("AAA", date(2026, 1, 1),
                                            date(2026, 2, 1), Basis.RAW)
    assert str(e.value) == Reason.BARS_FETCH_FAILED
