import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D
from urllib.parse import parse_qs, urlparse

import pytest

from src.price.calendar import NY_TZ
from src.price.provider import ProviderError
from src.price.schema import ActionType, Basis
from src.price_providers import massive as m

TS = datetime(2026, 11, 24, 5, 10, tzinfo=timezone.utc)
MARKER = "987654.321"


def ms(d):
    return int(datetime.combine(d, time(0, 0), tzinfo=NY_TZ).timestamp() * 1000)


def bar_row(d, c="100.25", v=1000):
    return {"t": ms(d), "o": float(c), "h": float(c), "l": float(c), "c": float(c), "v": v}


class FakeTransport:
    def __init__(self, routes):
        self.routes = routes          # list of (predicate, status, payload)
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        for pred, status, payload in self.routes:
            if pred(url):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                return status, body
        return 404, b""


def provider(routes, **kw):
    t = FakeTransport(routes)
    clock_t = [0.0]
    sleeps = []

    def mono():
        return clock_t[0]

    def sleep(s):
        sleeps.append(s)
        clock_t[0] += s
    p = m.MassivePriceProvider(as_of=date(2026, 11, 23), transport=t, clock=lambda: TS,
                               sleep=sleep, monotonic=mono, **kw)
    return p, t, sleeps


D1, D2 = date(2026, 11, 20), date(2026, 11, 23)


def test_bars_parse_decimal_date_basis_and_query():
    rows = [bar_row(D1, "100.25"), bar_row(D2, "101.5")]
    p, t, _ = provider([(lambda u: "/v2/aggs/" in u, 200, {"status": "OK", "results": rows})])
    r = p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert [b.date for b in r.bars] == [D1, D2]
    assert r.bars[0].close == D("100.25") and isinstance(r.bars[0].close, D)
    assert r.bars[0].basis is Basis.RAW and not r.is_partial
    q = parse_qs(urlparse(t.calls[0][0]).query)
    assert q["adjusted"] == ["false"] and q["sort"] == ["asc"]
    p.get_daily_bars("AAPL", D1, D2, Basis.SPLIT_ADJUSTED)
    assert parse_qs(urlparse(t.calls[1][0]).query)["adjusted"] == ["true"]


def test_bar_date_uses_eastern_time_across_dst():
    assert m.bar_session_date(ms(date(2026, 3, 9))) == date(2026, 3, 9)   # EDT
    assert m.bar_session_date(ms(date(2026, 11, 2))) == date(2026, 11, 2)  # EST


def test_pagination_complete_and_foreign_host_rejected():
    page2 = "https://api.massive.com/v2/aggs/next?cursor=abc"
    routes = [(lambda u: "cursor=abc" in u, 200, {"results": [bar_row(D2)]}),
              (lambda u: "/v2/aggs/ticker" in u, 200,
               {"results": [bar_row(D1)], "next_url": page2})]
    p, t, _ = provider(routes)
    r = p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert len(r.bars) == 2 and not r.is_partial
    bad = [(lambda u: True, 200, {"results": [], "next_url": "https://evil.example/x"})]
    p, _, _ = provider(bad)
    with pytest.raises(ProviderError) as e:
        p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert e.value.reason == m.MALFORMED_RESPONSE


def test_too_many_pages_marks_partial():
    loop = "https://api.massive.com/v2/aggs/loop"
    p, _, _ = provider([(lambda u: True, 200, {"results": [], "next_url": loop})])
    assert p.get_daily_bars("AAPL", D1, D2, Basis.RAW).is_partial


@pytest.mark.parametrize("status,reason", [(429, m.RATE_LIMITED), (401, m.AUTH_FAILED),
                                           (403, m.AUTH_FAILED), (500, m.HTTP_ERROR)])
def test_http_errors_carry_reason_only(status, reason):
    p, _, _ = provider([(lambda u: True, status, b"secret-body")])
    with pytest.raises(ProviderError) as e:
        p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert e.value.reason == reason and "secret" not in str(e.value)


def test_malformed_payloads():
    for payload in (b"not json", [1, 2], {"results": "x"},
                    {"results": [{"t": ms(D1), "o": 1, "h": 1, "l": 1, "c": 1, "v": 1.5}]},
                    {"results": [{"t": ms(D1), "o": -1, "h": 1, "l": -1, "c": 1, "v": 1}]}):
        p, _, _ = provider([(lambda u: True, 200, payload)])
        with pytest.raises(ProviderError) as e:
            p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
        assert e.value.reason.startswith(m.MALFORMED_RESPONSE)


def test_rate_limit_spacing():
    rows = {"results": [bar_row(D1)]}
    p, _, sleeps = provider([(lambda u: True, 200, rows)])
    for _ in range(3):
        p.get_daily_bars("AAPL", D1, D1, Basis.RAW)
    assert sleeps == [m.MIN_REQUEST_INTERVAL_SECONDS] * 2 and p.request_count == 3


def test_auth_modes(monkeypatch):
    rows = {"results": [bar_row(D1)]}
    p, t, _ = provider([(lambda u: True, 200, rows)])
    p.get_daily_bars("AAPL", D1, D1, Basis.RAW)
    assert "Authorization" not in t.calls[0][1]                  # PROXY: no credential
    monkeypatch.setenv("MASSIVE_API_KEY", "k-" + MARKER)
    p, t, _ = provider([(lambda u: True, 200, rows)], auth_mode="ENV")
    p.get_daily_bars("AAPL", D1, D1, Basis.RAW)
    assert t.calls[0][1]["Authorization"] == "Bearer k-" + MARKER
    assert "apiKey" not in t.calls[0][0] and MARKER not in t.calls[0][0]
    assert MARKER not in repr(p)
    monkeypatch.delenv("MASSIVE_API_KEY")
    p, _, _ = provider([(lambda u: True, 200, rows)], auth_mode="ENV")
    with pytest.raises(ProviderError) as e:
        p.get_daily_bars("AAPL", D1, D1, Basis.RAW)
    assert e.value.reason == m.KEY_MISSING


def test_split_events_parse_and_filter():
    rows = [{"ticker": "AAPL", "execution_date": "2026-06-03", "split_from": 1,
             "split_to": 4, "adjustment_type": "forward_split",
             "historical_adjustment_factor": 0.25},
            {"ticker": "ZZZ", "execution_date": "2026-06-04", "split_from": 10,
             "split_to": 1, "adjustment_type": "reverse_split"}]
    p, t, _ = provider([(lambda u: "/stocks/v1/splits" in u, 200, {"results": rows})])
    r = p.get_split_events(date(2026, 1, 1), D2, ["AAPL", "MSFT"])
    assert [e.symbol for e in r.events] == ["AAPL"]
    assert r.events[0].ratio == D(4) and r.events[0].action_type is ActionType.FORWARD_SPLIT
    assert r.symbols == ("AAPL", "MSFT") and not r.is_partial
    q = parse_qs(urlparse(t.calls[0][0]).query)
    assert q["execution_date.gte"] == ["2026-01-01"] and "ticker" not in q
    p.get_split_events(date(2026, 1, 1), D2, ["AAPL"])
    assert parse_qs(urlparse(t.calls[1][0]).query)["ticker"] == ["AAPL"]


def test_unknown_adjustment_type_fails_closed():
    rows = [{"ticker": "AAPL", "execution_date": "2026-06-03", "split_from": 1,
             "split_to": 2, "adjustment_type": "spin_off"}]
    p, _, _ = provider([(lambda u: True, 200, {"results": rows})])
    with pytest.raises(ProviderError) as e:
        p.get_split_events(date(2026, 1, 1), D2)
    assert e.value.reason == m.UNKNOWN_ADJUSTMENT_TYPE


def test_coverage_is_documented_two_years():
    p, _, _ = provider([])
    c = p.coverage()
    assert c.coverage_source == "DOCUMENTED"
    assert c.splits_history_start == date(2024, 11, 24)
    assert c.splits_history_end == date(2026, 11, 23)


def test_observed_decimal_places():
    from src.price.schema import NormalizedBar
    b = NormalizedBar(D1, D("1.5"), D("1.5"), D("1.25"), D("1.250"), 1, Basis.RAW, "x", TS)
    assert m.observed_decimal_places([b]) == 2
