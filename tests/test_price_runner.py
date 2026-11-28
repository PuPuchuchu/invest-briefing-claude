import importlib.util
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from src.price.calendar import NY_TZ
from src.price_providers import massive as m
from src.price_providers.calendar_io import load_calendar

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rpb", ROOT / "scripts" / "run_price_briefing.py")
rpb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rpb)

CAL = load_calendar()
PRICE_MARK = "4321.987"          # sentinel close value, must never reach stdout


def sessions(a, b):
    d = a
    while d <= b:
        if d.weekday() < 5 and d not in CAL.holidays:
            yield d
        d += timedelta(days=1)


def make_transport(fail_splits=False, status=200, splits_payload=None, drop_adj_last=False,
                   raw_split_day=None, raw_split_mult=2):
    def transport(url, headers):
        if status != 200:
            return status, b""
        if "/stocks/v1/splits" in url:
            if fail_splits:
                return 500, b""
            if splits_payload is not None:
                return 200, json.dumps(splits_payload).encode()
            return 200, json.dumps({"status": "OK", "results": []}).encode()
        g = re.search(r"/range/1/day/(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})", url)
        a, b = date.fromisoformat(g.group(1)), date.fromisoformat(g.group(2))
        rows = []
        for i, d in enumerate(sessions(a, b)):
            if drop_adj_last and d == b and "adjusted=true" in url:
                continue
            c = PRICE_MARK if d == b else f"{100 + (i % 7) * 0.5:.2f}"
            if raw_split_day and d < raw_split_day and "adjusted=false" in url \
                    and "/ticker/AAPL/" in url:
                c = f"{float(c) * raw_split_mult:.2f}"
            t = int(datetime.combine(d, time(0), tzinfo=NY_TZ).timestamp() * 1000)
            rows.append({"t": t, "o": c, "h": c, "l": c, "c": c, "v": 1000})
        body = json.dumps({"status": "OK", "resultsCount": len(rows), "results": rows}).replace(f'"{PRICE_MARK}"', PRICE_MARK)
        body = re.sub(r'"(\d+\.\d+)"', r"\1", body)
        return 200, body.encode()
    return transport


@pytest.fixture
def patch_provider(monkeypatch):
    def apply(**kw):
        real = m.MassivePriceProvider

        def factory(**args):
            return real(transport=make_transport(**kw), sleep=lambda s: None, **args)
        monkeypatch.setattr(rpb, "MassivePriceProvider", factory)
    return apply


NOW = "2026-11-24T05:10:00+00:00"     # Tue 14:10 KST -> expected session Mon 11-23


def run(capsys, *extra):
    rc = rpb.main(["--tickers", "aapl,MSFT", "--now-utc", NOW, *extra])
    out = capsys.readouterr().out
    return rc, out, json.loads(out)


def test_full_run_outputs_facts_only(patch_provider, capsys):
    patch_provider()
    rc, out, doc = run(capsys, "--price-precision", "0.01")
    assert rc == 0
    assert doc["run"]["expected_session"] == "2026-11-23"
    assert doc["run"]["capture_class"] == "EPHEMERAL_ONLY"
    assert doc["run"]["tickers"] == ["AAPL", "MSFT"]
    assert set(doc["briefing"]) == {"AAPL", "MSFT"}
    assert PRICE_MARK not in out
    for key in ('"o"', '"h"', '"l"', '"c"', '"close"', '"open"', '"volume"'):
        assert key not in out
    r = doc["results"][0]
    assert r["validation"]["data_success"] is True
    assert "RSI_14D" in json.dumps(r)
    assert r["evaluation_date"] == "2026-11-24"


def test_missing_precision_withholds_momentum(patch_provider, capsys):
    patch_provider()
    rc, out, doc = run(capsys)
    assert rc == 0 and PRICE_MARK not in out
    assert "NOT_AVAILABLE" in json.dumps(doc["results"][0])


def momentum_statuses(result):
    # momentum metrics only (valuation facts PE_TTM / PE_FY / EPS_* excluded)
    return {f["metric"]: f["calc_status"] for f in result["facts"]
            if f["metric"] == "RSI_14D" or f["metric"].startswith("PRICE_VS_")}


@pytest.mark.parametrize("bad", ["auto", "0", "-0.01", "NaN", "Infinity", "x"])
def test_precision_must_be_explicit_positive(bad, patch_provider, capsys):
    patch_provider()
    assert rpb.main(["--tickers", "AAPL", "--now-utc", NOW, "--price-precision", bad]) == 2


def test_unverified_precision_never_yields_momentum(patch_provider, capsys):
    patch_provider()
    _, out, doc = run(capsys)
    for r in doc["results"]:
        assert set(momentum_statuses(r).values()) <= {"MISSING", "INVALID"}
        assert "PRECISION_INFERRED" not in out


def test_split_api_error_blocks_momentum(patch_provider, capsys):
    patch_provider(splits_payload={"status": "ERROR"})
    _, out, doc = run(capsys, "--price-precision", "0.01")
    assert doc["run"]["splits_error"] == "MALFORMED_RESPONSE:STATUS"
    assert "NO_EVENT_VERIFIED" not in out
    for r in doc["results"]:
        assert set(momentum_statuses(r).values()) <= {"MISSING", "INVALID"}


def test_split_count_mismatch_blocks_momentum(patch_provider, capsys):
    patch_provider(splits_payload={"status": "OK", "results": [], "resultsCount": 1})
    _, out, doc = run(capsys, "--price-precision", "0.01")
    assert doc["run"]["splits_error"] == "MALFORMED_RESPONSE:COUNT_MISMATCH"
    assert "NO_EVENT_VERIFIED" not in out
    for r in doc["results"]:
        assert set(momentum_statuses(r).values()) <= {"MISSING", "INVALID"}


def test_verified_empty_splits_allow_momentum(patch_provider, capsys):
    patch_provider()
    _, out, doc = run(capsys, "--price-precision", "0.01")
    assert "NO_EVENT_VERIFIED" in out
    assert "OK" in momentum_statuses(doc["results"][0]).values()


def test_adjusted_latest_missing_blocks_momentum(patch_provider, capsys):
    patch_provider(drop_adj_last=True)
    _, out, doc = run(capsys, "--price-precision", "0.01")
    r = doc["results"][0]
    assert set(momentum_statuses(r).values()) <= {"MISSING", "INVALID"}
    assert "ADJUSTED_LATEST_SESSION_MISSING" in out


def test_splits_failure_recorded(patch_provider, capsys):
    patch_provider(fail_splits=True)
    _, out, doc = run(capsys, "--price-precision", "0.01")
    assert doc["run"]["splits_error"] == m.HTTP_ERROR and PRICE_MARK not in out


def test_provider_error_per_ticker(patch_provider, capsys):
    patch_provider(status=401)
    rc, out, doc = run(capsys)
    assert rc == 0 and doc["run"]["data_success_all"] is False
    assert all(r["error"] == m.AUTH_FAILED for r in doc["results"])


def test_structure_check_has_no_prices(patch_provider, capsys):
    patch_provider()
    rc, out, doc = run(capsys, "--structure-check")
    s = doc["structure"][0]
    assert rc == 0 and s["dates_equal"] is True and s["raw_decimal_places"] == 3
    assert s["raw_last"] == "2026-11-23" and PRICE_MARK not in out
    assert s["errors"] == {}
    assert s["raw_page_shape"]["first_row"]["c"] == "float"
    assert s["raw_page_shape"]["counts"]["resultsCount"] == s["raw_bars"]
    assert s["splits_page_shape"]["top_level"]["results"] == "list"


def test_structure_check_reports_failing_stage(patch_provider, monkeypatch, capsys):
    real = make_transport()

    def bad_close(url, headers):
        st, body = real(url, headers)
        if b"adjusted=true" in url.encode():
            body = body.replace(b'"c": ', b'"c": "x", "c_": ')
        return st, body
    real_cls = m.MassivePriceProvider
    monkeypatch.setattr(rpb, "MassivePriceProvider",
                        lambda **a: real_cls(transport=bad_close,
                                             sleep=lambda s: None, **a))
    rc, out, doc = run(capsys, "--structure-check")
    s = doc["structure"][0]
    assert rc == 0 and s["errors"] == {"adjusted_bars": "MALFORMED_RESPONSE:CLOSE_TYPE"}
    assert "raw_bars" not in s and PRICE_MARK not in out


def test_empty_tickers_exit_2():
    assert rpb.main(["--tickers", " , "]) == 2


def test_adjusted_partial_does_not_mark_raw_stale(patch_provider, monkeypatch, capsys):
    from src.price.schema import Basis
    real = m.MassivePriceProvider.get_daily_bars

    def partial_adjusted(self, symbol, start, end, basis):
        r = real(self, symbol, start, end, basis)
        if basis is Basis.SPLIT_ADJUSTED:
            import dataclasses
            r = dataclasses.replace(r, is_partial=True)
        return r
    patch_provider()
    monkeypatch.setattr(m.MassivePriceProvider, "get_daily_bars", partial_adjusted)
    _, out, doc = run(capsys, "--price-precision", "0.01")
    st = doc["results"][0]["status"]
    assert st["raw_price"] == "OK" and st["adjusted_price"] != "OK"
    assert set(momentum_statuses(doc["results"][0]).values()) <= {"MISSING", "INVALID"}
    assert "BARS_PARTIAL_RESPONSE" in out


def test_bars_missing_results_count_is_provider_error(patch_provider, monkeypatch, capsys):
    import re as _re
    real = make_transport()

    def no_count(url, headers):
        st, body = real(url, headers)
        return st, _re.sub(rb'"resultsCount": \d+, ', b"", body)
    real_cls = m.MassivePriceProvider
    monkeypatch.setattr(rpb, "MassivePriceProvider",
                        lambda **a: real_cls(transport=no_count, sleep=lambda s: None, **a))
    rc, out, doc = run(capsys, "--price-precision", "0.01")
    assert rc == 0 and doc["run"]["data_success_all"] is False
    assert all(r["error"] == "MALFORMED_RESPONSE:RESULTS_COUNT_MISSING" for r in doc["results"])


SPLIT_DAY = date(2026, 6, 3)


def split_payload(ticker="AAPL", frm=1, to=2):
    return {"status": "OK", "results": [{
        "ticker": ticker, "execution_date": SPLIT_DAY.isoformat(), "split_from": frm,
        "split_to": to, "adjustment_type": "forward_split"}]}


def _patch(monkeypatch, transport):
    real_cls = m.MassivePriceProvider
    monkeypatch.setattr(rpb, "MassivePriceProvider",
                        lambda **a: real_cls(transport=transport, sleep=lambda s: None, **a))


def test_r3_row2_split_unlocks_momentum_but_not_valuation(monkeypatch, capsys):
    _patch(monkeypatch, make_transport(splits_payload=split_payload(),
                                       raw_split_day=SPLIT_DAY))
    rc, out, doc = run(capsys, "--price-precision", "0.01")
    aapl, msft = doc["results"]
    assert aapl["status"]["corporate_action"] == "PENDING_SEC"
    assert set(momentum_statuses(aapl).values()) == {"OK"}
    pe = [f for f in aapl["facts"] if f["metric"] == "PE_TTM"][0]
    assert pe["calc_status"] != "OK"
    assert "SPLIT_IN_WINDOW_PRICE_VERIFIED_SEC_PENDING" in json.dumps(aapl)
    assert msft["status"]["corporate_action"] == "NO_EVENT_VERIFIED"
    assert PRICE_MARK not in out


def test_raw_jump_without_event_is_candidate(monkeypatch, capsys):
    _patch(monkeypatch, make_transport(raw_split_day=SPLIT_DAY))
    rc, out, doc = run(capsys, "--price-precision", "0.01")
    aapl = doc["results"][0]
    assert aapl["status"]["corporate_action"] == "CANDIDATE"
    assert "OK" not in momentum_statuses(aapl).values()


def test_r3_row2_event_without_raw_jump_stays_blocked(monkeypatch, capsys):
    # provider reports a split, adjusted series consistent, but raw prices never jumped
    _patch(monkeypatch, make_transport(splits_payload=split_payload()))
    rc, out, doc = run(capsys, "--price-precision", "0.01")
    aapl = doc["results"][0]
    assert aapl["status"]["corporate_action"] != "PENDING_SEC"
    assert "OK" not in momentum_statuses(aapl).values()


# ------------------------------------------------------------------ G6 EPS path
def _eps_row(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "accn": f"A-{end}", "fy": 2026,
            "fp": "Q", "form": form, "filed": filed}


GOOD_CF = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": [
    _eps_row("2025-01-01", "2025-03-31", 0.9, "2025-04-30"),
    _eps_row("2025-04-01", "2025-06-30", 1.0, "2025-07-30"),
    _eps_row("2025-07-01", "2025-09-30", 1.0, "2025-10-30"),
    _eps_row("2025-10-01", "2025-12-31", 1.1, "2026-02-10", "10-K"),
    _eps_row("2025-01-01", "2025-12-31", 4.0, "2026-02-10", "10-K"),
    _eps_row("2026-01-01", "2026-03-31", 1.2, "2026-04-30"),
    _eps_row("2026-04-01", "2026-06-30", 1.3, "2026-07-30"),
    _eps_row("2026-07-01", "2026-09-30", 1.4, "2026-10-30")]}}}}}


def test_eps_path_per_ticker_isolation(monkeypatch, capsys, tmp_path):
    (tmp_path / "AAPL_companyfacts.json").write_text(json.dumps(GOOD_CF), encoding="utf-8")
    (tmp_path / "MSFT_companyfacts.json").write_text("{broken", encoding="utf-8")
    _patch(monkeypatch, make_transport())
    rc, out, doc = run(capsys, "--price-precision", "0.01", "--sec-raw-dir", str(tmp_path))
    aapl, msft = doc["results"]
    facts = {f["metric"]: f for f in aapl["facts"]}
    assert facts["EPS_TTM"]["value"] == "5.00" and facts["EPS_FY"]["value"] == "4.00"
    assert facts["PE_TTM"]["calc_status"] == "OK" and facts["PE_FY"]["calc_status"] == "OK"
    assert facts["PE_TTM"]["evidence"]["method"] == "DIRECT_4Q"
    assert len(facts["PE_TTM"]["evidence"]["periods"]) == 4
    m = {f["metric"]: f for f in msft["facts"]}
    assert m["PE_TTM"]["calc_status"] == "INVALID"
    assert m["PE_TTM"]["reasons"] == ["EPS_SOURCE_MALFORMED"]
    assert set(momentum_statuses(msft).values()) == {"OK"}        # rest of the ticker intact
    assert PRICE_MARK not in out


def test_eps_path_without_sec_dir_is_missing(monkeypatch, capsys):
    _patch(monkeypatch, make_transport())
    rc, out, doc = run(capsys, "--price-precision", "0.01")
    f = {x["metric"]: x for x in doc["results"][0]["facts"]}
    assert f["PE_TTM"]["calc_status"] == "MISSING"
    assert f["PE_TTM"]["reasons"] == ["EPS_SOURCE_MISSING"]


def test_eps_path_blocked_when_split_fetch_failed(monkeypatch, capsys, tmp_path):
    (tmp_path / "AAPL_companyfacts.json").write_text(json.dumps(GOOD_CF), encoding="utf-8")
    _patch(monkeypatch, make_transport(fail_splits=True))
    rc, out, doc = run(capsys, "--price-precision", "0.01", "--sec-raw-dir", str(tmp_path))
    f = {x["metric"]: x for x in doc["results"][0]["facts"]}
    assert f["PE_TTM"]["reasons"] == ["EPS_SPLIT_BASIS_UNVERIFIED"]
