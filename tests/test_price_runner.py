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


def make_transport(fail_splits=False, status=200):
    def transport(url, headers):
        if status != 200:
            return status, b""
        if "/stocks/v1/splits" in url:
            if fail_splits:
                return 500, b""
            return 200, json.dumps({"results": []}).encode()
        g = re.search(r"/range/1/day/(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})", url)
        a, b = date.fromisoformat(g.group(1)), date.fromisoformat(g.group(2))
        rows = []
        for i, d in enumerate(sessions(a, b)):
            c = PRICE_MARK if d == b else f"{100 + (i % 7) * 0.5:.2f}"
            t = int(datetime.combine(d, time(0), tzinfo=NY_TZ).timestamp() * 1000)
            rows.append({"t": t, "o": c, "h": c, "l": c, "c": c, "v": 1000})
        body = json.dumps({"results": rows}).replace(f'"{PRICE_MARK}"', PRICE_MARK)
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


def test_auto_precision_flagged(patch_provider, capsys):
    patch_provider()
    _, out, doc = run(capsys, "--price-precision", "auto")
    assert "PRECISION_INFERRED" in out and PRICE_MARK not in out


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


def test_empty_tickers_exit_2():
    assert rpb.main(["--tickers", " , "]) == 2
