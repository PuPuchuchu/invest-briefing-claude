"""
Deterministic, rule-based Korean sentences from a result dict
(R1 fallback path). No AI call, no randomness, no price levels.

RSI 30/70 are REFERENCE bands only (P4 design), never buy/sell signals.
"""

from __future__ import annotations

from decimal import Decimal

RSI_LOW_REF = Decimal(30)
RSI_HIGH_REF = Decimal(70)

_LABELS = {
    "RSI_14D": "RSI(14)",
    "PRICE_VS_SMA50_PCT": "50일선 대비",
    "PRICE_VS_SMA200_PCT": "200일선 대비",
    "PRICE_VS_SMA40W_PCT": "40주선 대비",
    "PE_TTM": "PER(TTM, 직접공시 4분기 EPS)",
    "PE_FY": "PER(최근 회계연도 EPS)",
    "EPS_TTM": "희석 EPS(TTM)",
    "EPS_FY": "희석 EPS(회계연도)",
}


def _fact_sentence(fact: dict) -> str:
    label = _LABELS.get(fact["metric"], fact["metric"])
    if fact["calc_status"] != "OK":
        return f"{label}: 계산 안 함 ({fact['calc_status']})"
    if fact["interpretability"] == "NOT_MEANINGFUL" or fact["value"] is None:
        return f"{label}: 의미 없음"
    value = Decimal(fact["value"])
    if fact["metric"] == "RSI_14D":
        band = ("과매도 참고 구간" if value < RSI_LOW_REF
                else "과매수 참고 구간" if value > RSI_HIGH_REF
                else "중립 구간")
        suffix = " (워밍업: 초기값 영향 남음)" if fact.get("state") == "WARMUP" else ""
        return f"{label} {fact['value']} — {band}{suffix}"
    if fact["unit"] == "PERCENT":
        side = "위" if value > 0 else "아래" if value < 0 else "같음"
        return f"{label} {fact['value']}% ({side})"
    return f"{label} {fact['value']}"


def interpret(result: dict) -> list[str]:
    lines = [f"[{result['ticker']}] 데이터 기준일 {result['provider']['data_as_of']}"]
    fresh = result["freshness"]["status"]
    if fresh != "FRESH":
        lines.append(f"주의: 데이터 신선도 {fresh} — 최신 거래일 데이터가 아님")
    lines.extend(_fact_sentence(f) for f in result["facts"])
    if not result["validation"]["data_success"]:
        lines.append("검증: 정상 결과 아님 (부분 또는 실패)")
    return lines
