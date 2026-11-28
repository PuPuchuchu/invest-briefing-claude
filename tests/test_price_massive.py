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
    p, t, _ = provider([(lambda u: "/v2/aggs/" in u, 200, {"status": "OK", "resultsCount": 2, "results": rows})])
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
    routes = [(lambda u: "cursor=abc" in u, 200, {"status": "OK", "resultsCount": 1, "results": [bar_row(D2)]}),
              (lambda u: "/v2/aggs/ticker" in u, 200,
               {"status": "OK", "resultsCount": 1, "results": [bar_row(D1)], "next_url": page2})]
    p, t, _ = provider(routes)
    r = p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert len(r.bars) == 2 and not r.is_partial
    bad = [(lambda u: True, 200, {"status": "OK", "resultsCount": 0, "results": [], "next_url": "https://evil.example/x"})]
    p, _, _ = provider(bad)
    with pytest.raises(ProviderError) as e:
        p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert e.value.reason == m.MALFORMED_RESPONSE + ":NEXT_URL_HOST"


def test_too_many_pages_marks_partial():
    loop = "https://api.massive.com/v2/aggs/loop"
    p, _, _ = provider([(lambda u: True, 200, {"status": "OK", "resultsCount": 0, "results": [], "next_url": loop})])
    assert p.get_daily_bars("AAPL", D1, D2, Basis.RAW).is_partial


@pytest.mark.parametrize("status,reason", [(429, m.RATE_LIMITED), (401, m.AUTH_FAILED),
                                           (403, m.AUTH_FAILED), (500, m.HTTP_ERROR)])
def test_http_errors_carry_reason_only(status, reason):
    p, _, _ = provider([(lambda u: True, status, b"secret-body")])
    with pytest.raises(ProviderError) as e:
        p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert e.value.reason == reason and "secret" not in str(e.value)


def test_malformed_payloads():
    for payload in (b"not json", [1, 2], {"status": "OK", "results": "x"},
                    {"status": "OK", "results": [bar_row(D1), "junk"]},
                    {"status": "OK", "results": [{"t": ms(D1), "o": 1, "h": 1, "l": 1, "c": 1, "v": 1.5}]},
                    {"status": "OK", "results": [{"t": ms(D1), "o": -1, "h": 1, "l": -1, "c": 1, "v": 1}]}):
        p, _, _ = provider([(lambda u: True, 200, payload)])
        with pytest.raises(ProviderError) as e:
            p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
        assert e.value.reason.startswith(m.MALFORMED_RESPONSE)


def test_rate_limit_spacing():
    rows = {"status": "OK", "resultsCount": 1, "results": [bar_row(D1)]}
    p, _, sleeps = provider([(lambda u: True, 200, rows)])
    for _ in range(3):
        p.get_daily_bars("AAPL", D1, D1, Basis.RAW)
    assert sleeps == [m.MIN_REQUEST_INTERVAL_SECONDS] * 2 and p.request_count == 3


def test_auth_modes(monkeypatch):
    rows = {"status": "OK", "resultsCount": 1, "results": [bar_row(D1)]}
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
    p, t, _ = provider([(lambda u: "/stocks/v1/splits" in u, 200, {"status": "OK", "results": rows})])
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
    p, _, _ = provider([(lambda u: True, 200, {"status": "OK", "results": rows})])
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


# ---------------------------------------------------------------- B2: fail closed
@pytest.mark.parametrize("payload,reason", [
    ({"status": "ERROR", "error": "x"}, "MALFORMED_RESPONSE:STATUS"),
    ({"status": "NOT_AUTHORIZED"}, "MALFORMED_RESPONSE:STATUS"),
    ({"results": []}, "MALFORMED_RESPONSE:STATUS"),                 # no status
    ({"status": "OK"}, "MALFORMED_RESPONSE:RESULTS_MISSING"),
    ({"status": "OK", "count": 3}, "MALFORMED_RESPONSE:RESULTS_MISSING"),
    ({"status": "OK", "count": False}, "MALFORMED_RESPONSE:COUNT_TYPE"),
    ({"status": "OK", "resultsCount": 0}, "MALFORMED_RESPONSE:RESULTS_MISSING"),
    ({"status": "DELAYED", "results": []}, "MALFORMED_RESPONSE:STATUS"),
    ({"status": "OK", "results": None}, "MALFORMED_RESPONSE:RESULTS_TYPE"),
    ({"status": "OK", "results": [None]}, "MALFORMED_RESPONSE:ROW_TYPE"),
])
def test_split_api_errors_never_look_like_empty(payload, reason):
    p, _, _ = provider([(lambda u: True, 200, payload)])
    with pytest.raises(ProviderError) as e:
        p.get_split_events(date(2026, 1, 1), D2, ["AAPL"])
    assert e.value.reason == reason


def test_explicit_empty_results_are_verified_empty():
    p, _, _ = provider([(lambda u: True, 200, {"status": "OK", "results": []})])
    r = p.get_split_events(date(2026, 1, 1), D2, ["AAPL"])
    assert r.events == () and not r.is_partial
    for payload in ({"status": "DELAYED", "resultsCount": 0},
                    {"status": "OK", "resultsCount": 0, "results": []}):
        p, _, _ = provider([(lambda u: True, 200, payload)])
        assert p.get_daily_bars("AAPL", D1, D2, Basis.RAW).bars == ()


SPLIT_ROW = {"ticker": "AAPL", "execution_date": "2026-06-03", "split_from": 1,
             "split_to": 4, "adjustment_type": "forward_split"}


@pytest.mark.parametrize("payload,reason", [
    ({"status": "OK", "results": [], "resultsCount": 2}, "COUNT_MISMATCH"),
    ({"status": "OK", "results": [SPLIT_ROW], "resultsCount": 3}, "COUNT_MISMATCH"),
    ({"status": "OK", "results": [SPLIT_ROW], "count": 0}, "COUNT_MISMATCH"),
    ({"status": "OK", "results": [], "resultsCount": 0, "count": 1}, "COUNT_CONFLICT"),
    ({"status": "OK", "results": [], "resultsCount": True}, "COUNT_TYPE"),
    ({"status": "OK", "results": [], "resultsCount": "0"}, "COUNT_TYPE"),
    ({"status": "OK", "results": [], "resultsCount": -1}, "COUNT_TYPE"),
    ({"status": "OK", "results": [], "queryCount": 1.5}, "COUNT_TYPE"),
])
def test_inconsistent_counts_fail_closed(payload, reason):
    for call in ("splits", "bars"):
        p, _, _ = provider([(lambda u: True, 200, payload)])
        with pytest.raises(ProviderError) as e:
            if call == "splits":
                p.get_split_events(date(2026, 1, 1), D2, ["AAPL"])
            else:
                p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
        assert e.value.reason == "MALFORMED_RESPONSE:" + reason


def test_consistent_counts_accepted():
    p, _, _ = provider([(lambda u: True, 200, {"status": "OK", "results": [bar_row(D1)],
                                               "resultsCount": 1, "queryCount": 1})])
    assert len(p.get_daily_bars("AAPL", D1, D2, Basis.RAW).bars) == 1


def test_count_mismatch_on_second_page_fails_whole_response():
    nxt = "https://api.massive.com/stocks/v1/splits?cursor=2"
    routes = [(lambda u: "cursor=2" in u, 200,
               {"status": "OK", "results": [], "resultsCount": 1}),
              (lambda u: True, 200, {"status": "OK", "results": [SPLIT_ROW],
                                     "resultsCount": 1, "next_url": nxt})]
    p, _, _ = provider(routes)
    with pytest.raises(ProviderError) as e:
        p.get_split_events(date(2026, 1, 1), D2, ["AAPL"])
    assert e.value.reason == "MALFORMED_RESPONSE:COUNT_MISMATCH"


def test_error_on_second_page_fails_whole_response():
    nxt = "https://api.massive.com/stocks/v1/splits?cursor=2"
    routes = [(lambda u: "cursor=2" in u, 200, {"status": "ERROR"}),
              (lambda u: True, 200, {"status": "OK", "results": [], "next_url": nxt})]
    p, _, _ = provider(routes)
    with pytest.raises(ProviderError):
        p.get_split_events(date(2026, 1, 1), D2, ["AAPL"])


# ---------------------------------------------------------------- B1: redirects
@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_status_refused(code):
    p, _, _ = provider([(lambda u: True, code, b"")])
    with pytest.raises(ProviderError) as e:
        p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert e.value.reason == m.REDIRECT_REFUSED


@pytest.mark.parametrize("code", [301, 302, 307, 308])
def test_real_transport_never_forwards_key_on_redirect(code):
    """Local servers on two ports (= two origins): A redirects to B."""
    import http.server
    import threading

    seen = []

    class B(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"OK","results":[]}')

        def log_message(self, *a):
            pass

    sb = http.server.HTTPServer(("127.0.0.1", 0), B)

    class A(B):
        def do_GET(self):
            self.send_response(code)
            self.send_header("Location", f"http://127.0.0.1:{sb.server_port}/x")
            self.end_headers()

    sa = http.server.HTTPServer(("127.0.0.1", 0), A)
    for s in (sa, sb):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        status, body = m.urllib_transport(f"http://127.0.0.1:{sa.server_port}/v",
                                          {"Authorization": "Bearer k-" + MARKER})
    finally:
        sa.shutdown(); sb.shutdown()
    assert status == code and body == b""
    assert seen == []                                # B never contacted


# ------------------------------------------- B2 final: bars resultsCount required
@pytest.mark.parametrize("payload,reason", [
    ({"status": "OK", "results": []}, "RESULTS_COUNT_MISSING"),
    ({"status": "OK", "results": [bar_row(D1)]}, "RESULTS_COUNT_MISSING"),
    ({"status": "OK", "resultsCount": 1, "results": []}, "COUNT_MISMATCH"),
    ({"status": "OK", "resultsCount": 0, "results": [bar_row(D1)]}, "COUNT_MISMATCH"),
    ({"status": "OK", "resultsCount": True, "results": [bar_row(D1)]}, "COUNT_TYPE"),
    ({"status": "OK", "resultsCount": "1", "results": [bar_row(D1)]}, "COUNT_TYPE"),
    ({"status": "OK", "resultsCount": -1, "results": []}, "COUNT_TYPE"),
    ({"status": "OK"}, "RESULTS_MISSING"),
])
def test_bars_results_count_contract(payload, reason):
    p, _, _ = provider([(lambda u: True, 200, payload)])
    with pytest.raises(ProviderError) as e:
        p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert e.value.reason == "MALFORMED_RESPONSE:" + reason


@pytest.mark.parametrize("payload,n", [
    ({"status": "OK", "resultsCount": 0, "results": []}, 0),
    ({"status": "OK", "resultsCount": 0}, 0),
    ({"status": "OK", "resultsCount": 1, "results": [bar_row(D1)]}, 1),
])
def test_bars_results_count_accepted(payload, n):
    p, _, _ = provider([(lambda u: True, 200, payload)])
    assert len(p.get_daily_bars("AAPL", D1, D2, Basis.RAW).bars) == n


def test_bars_second_page_count_mismatch_fails_whole_response():
    page2 = "https://api.massive.com/v2/aggs/next?cursor=abc"
    routes = [(lambda u: "cursor=abc" in u, 200,
               {"status": "OK", "resultsCount": 2, "results": [bar_row(D2)]}),
              (lambda u: True, 200, {"status": "OK", "resultsCount": 1,
                                     "results": [bar_row(D1)], "next_url": page2})]
    p, _, _ = provider(routes)
    with pytest.raises(ProviderError) as e:
        p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert e.value.reason == "MALFORMED_RESPONSE:COUNT_MISMATCH"


def test_bars_second_page_missing_count_fails_whole_response():
    page2 = "https://api.massive.com/v2/aggs/next?cursor=abc"
    routes = [(lambda u: "cursor=abc" in u, 200, {"status": "OK", "results": [bar_row(D2)]}),
              (lambda u: True, 200, {"status": "OK", "resultsCount": 1,
                                     "results": [bar_row(D1)], "next_url": page2})]
    p, _, _ = provider(routes)
    with pytest.raises(ProviderError) as e:
        p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert e.value.reason == "MALFORMED_RESPONSE:RESULTS_COUNT_MISSING"


def test_splits_do_not_require_count_field():
    p, _, _ = provider([(lambda u: True, 200, {"status": "OK", "results": [SPLIT_ROW]})])
    assert len(p.get_split_events(date(2026, 1, 1), D2, ["AAPL"]).events) == 1


# ------------------------------------------- PoC diagnostics: specific reasons
def _bar(**over):
    r = bar_row(D1)
    r.update(over)
    return {"status": "OK", "resultsCount": 1, "results": [r]}


@pytest.mark.parametrize("payload,reason", [
    (_bar(v=-1), "VOLUME_VALUE"),
    (_bar(v=True), "VOLUME_TYPE"),
    (_bar(v=None), "VOLUME_MISSING"),
    (_bar(v="1000"), "VOLUME_TYPE"),
    (_bar(c=None), "CLOSE_MISSING"),
    (_bar(o="1"), "OPEN_TYPE"),
    (_bar(t=None), "TIMESTAMP_MISSING"),
    (b"<html>", "JSON_DECODE"),
    ([1], "JSON_TYPE"),
    ({"status": "OK", "resultsCount": 0, "results": [], "next_url": 5}, "NEXT_URL_TYPE"),
])
def test_bar_malformed_reasons_are_specific(payload, reason):
    p, _, _ = provider([(lambda u: True, 200, payload)])
    with pytest.raises(ProviderError) as e:
        p.get_daily_bars("AAPL", D1, D2, Basis.RAW)
    assert e.value.reason == "MALFORMED_RESPONSE:" + reason


@pytest.mark.parametrize("over,reason", [
    ({"ticker": None}, "SPLIT_TICKER"),
    ({"execution_date": "x"}, "SPLIT_FIELD"),
    ({"split_to": "4"}, "SPLIT_TO_TYPE"),
])
def test_split_malformed_reasons_are_specific(over, reason):
    row = dict(SPLIT_ROW, **over)
    p, _, _ = provider([(lambda u: True, 200, {"status": "OK", "results": [row]})])
    with pytest.raises(ProviderError) as e:
        p.get_split_events(date(2026, 1, 1), D2)
    assert e.value.reason == "MALFORMED_RESPONSE:" + reason


def test_describe_page_has_types_not_values():
    body = {"status": "OK", "resultsCount": 1, "queryCount": 1, "request_id": "r1",
            "next_url": "https://api.massive.com/v2/x?cursor=SECRETCURSOR",
            "results": [{"t": ms(D1), "o": D(MARKER), "c": D(MARKER), "v": 77, "otc": False}]}
    d = m.describe_page(body)
    assert d["top_level"]["results"] == "list" and d["top_level"]["resultsCount"] == "int"
    assert d["first_row"] == {"c": "float", "o": "float", "otc": "bool", "t": "int", "v": "int"}
    assert d["counts"] == {"resultsCount": 1, "queryCount": 1} and d["results_len"] == 1
    assert d["next_url_host"] == "api.massive.com" and d["status"] == "OK"
    txt = json.dumps(d)
    assert MARKER not in txt and "SECRETCURSOR" not in txt and "r1" not in txt


@pytest.mark.parametrize("v,expected", [(1000.5, 1000), (1001.5, 1002), (7.0526e7, 70526000),
                                        (123.4, 123), (0, 0)])
def test_fractional_volume_rounded_half_even(v, expected):
    p, _, _ = provider([(lambda u: True, 200, _bar(v=v))])
    assert p.get_daily_bars("AAPL", D1, D2, Basis.RAW).bars[0].volume == expected
