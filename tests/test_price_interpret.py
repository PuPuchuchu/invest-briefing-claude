from src.price.interpret import interpret


def result(facts, fresh="FRESH", ok=True):
    return {"ticker": "AAA", "provider": {"data_as_of": "2026-11-23"},
            "freshness": {"status": fresh}, "facts": facts,
            "validation": {"data_success": ok}}


def fact(metric, value, unit, status="OK", interp="MEANINGFUL", state=None):
    return {"metric": metric, "calc_status": status, "interpretability": interp,
            "value": value, "unit": unit, "state": state}


def test_rsi_bands_are_reference_only_and_deterministic():
    facts = [fact("RSI_14D", "25.0", "INDEX_0_100"),
             fact("RSI_14D", "75.0", "INDEX_0_100"),
             fact("RSI_14D", "50.0", "INDEX_0_100", state="WARMUP")]
    a = interpret(result(facts))
    assert a == interpret(result(facts))
    assert "과매도 참고 구간" in a[1] and "과매수 참고 구간" in a[2]
    assert "중립 구간" in a[3] and "워밍업" in a[3]


def test_status_and_freshness_lines():
    facts = [fact("PE_TTM", None, "RATIO", status="MISSING", interp=None),
             fact("PE_TTM", None, "RATIO", interp="NOT_MEANINGFUL"),
             fact("PRICE_VS_SMA200_PCT", "-6.10", "PERCENT")]
    lines = interpret(result(facts, fresh="STALE", ok=False))
    assert any("STALE" in l for l in lines)
    assert "계산 안 함 (MISSING)" in lines[2]
    assert "의미 없음" in lines[3]
    assert "-6.10% (아래)" in lines[4]
    assert lines[-1].startswith("검증: 정상 결과 아님")
