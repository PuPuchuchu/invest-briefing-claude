from datetime import date, datetime, timezone
from decimal import Decimal as D

import pytest

from src.price.schema import (
    ActionType, Basis, NormalizedBar, PriceDataValidationError, Reason,
    SplitEvent, validate_series,
)

TS = datetime(2026, 11, 27, 5, 10, tzinfo=timezone.utc)


def bar(day, close="10.00", basis=Basis.RAW, **kw):
    c = D(close)
    args = dict(date=day, open=kw.get("open", c), high=kw.get("high", c),
                low=kw.get("low", c), close=c, volume=kw.get("volume", 100),
                basis=basis, provider_id="synthetic", retrieved_at=TS)
    args.update({k: v for k, v in kw.items() if k in ("currency",)})
    return NormalizedBar(**args)


def test_valid_bar():
    b = bar(date(2026, 11, 2), "10.00", high=D("11"), low=D("9"))
    assert b.close == D("10.00") and b.currency == "USD"


@pytest.mark.parametrize("field,value,reason", [
    ("close", None, Reason.MISSING_FIELD),
    ("close", 10.0, Reason.NON_DECIMAL_VALUE),
    ("close", D("NaN"), Reason.NON_DECIMAL_VALUE),
    ("close", D("-1"), Reason.NEGATIVE_VALUE),
    ("volume", -1, Reason.NEGATIVE_VALUE),
    ("volume", None, Reason.MISSING_FIELD),
])
def test_invalid_fields(field, value, reason):
    kwargs = dict(date=date(2026, 11, 2), open=D("10"), high=D("10"), low=D("10"),
                  close=D("10"), volume=1, basis=Basis.RAW, provider_id="s",
                  retrieved_at=TS)
    kwargs[field] = value
    with pytest.raises(PriceDataValidationError) as e:
        NormalizedBar(**kwargs)
    assert e.value.reason == reason


def test_bad_ohlc_relation():
    with pytest.raises(PriceDataValidationError) as e:
        bar(date(2026, 11, 2), "12", high=D("11"), low=D("9"))
    assert e.value.reason == Reason.INVALID_OHLC


def test_wrong_currency_and_naive_timestamp():
    with pytest.raises(PriceDataValidationError):
        bar(date(2026, 11, 2), currency="EUR")
    with pytest.raises(PriceDataValidationError):
        NormalizedBar(date=date(2026, 11, 2), open=D(1), high=D(1), low=D(1),
                      close=D(1), volume=1, basis=Basis.RAW, provider_id="s",
                      retrieved_at=datetime(2026, 11, 27))


def test_series_rules():
    d1, d2 = date(2026, 11, 2), date(2026, 11, 3)
    validate_series([bar(d1), bar(d2)], as_of=d2)
    with pytest.raises(PriceDataValidationError) as e:
        validate_series([bar(d1), bar(d2, basis=Basis.SPLIT_ADJUSTED)], as_of=d2)
    assert e.value.reason == Reason.BASIS_MIXED
    with pytest.raises(PriceDataValidationError) as e:
        validate_series([bar(d1), bar(d1)], as_of=d2)
    assert e.value.reason == Reason.DUPLICATE_DATE
    with pytest.raises(PriceDataValidationError) as e:
        validate_series([bar(d2), bar(d1)], as_of=d2)
    assert e.value.reason == Reason.DATES_NOT_ASCENDING
    with pytest.raises(PriceDataValidationError) as e:
        validate_series([bar(d1), bar(d2)], as_of=d1)
    assert e.value.reason == Reason.FUTURE_DATE


def test_error_messages_carry_no_values():
    marker = "987654.321"
    with pytest.raises(PriceDataValidationError) as e:
        bar(date(2026, 11, 2), marker, high=D("1"), low=D("1"))
    assert marker not in str(e.value) and marker not in repr(e.value)


def test_split_event_validation_and_ratio():
    e = SplitEvent("AAA", date(2026, 6, 1), D(1), D(4), ActionType.FORWARD_SPLIT)
    assert e.ratio == D(4)
    r = SplitEvent("AAA", date(2026, 6, 1), D(10), D(1), ActionType.REVERSE_SPLIT)
    assert r.ratio == D("0.1")
    for bad in (D(0), D(-2), None, 2):
        with pytest.raises(PriceDataValidationError) as ex:
            SplitEvent("AAA", date(2026, 6, 1), bad, D(2), ActionType.FORWARD_SPLIT)
        assert ex.value.reason == Reason.INVALID_SPLIT_RATIO
