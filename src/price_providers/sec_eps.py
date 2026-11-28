"""
Read diluted-EPS facts and cover-page shares from an already cached SEC
CompanyFacts JSON file (written by scripts/run_fundamentals_batch.py into
its --raw-dir as "{TICKER}_companyfacts.json").

No network access, no writes, no logging, never touches the protected SEC
history module. Only us-gaap:EarningsPerShareDiluted (unit USD/shares,
forms 10-Q / 10-K and their amendments) and dei:EntityCommonStockShares-
Outstanding are read. Values are parsed as Decimal; malformed values fail
the whole ticker (EPS_SOURCE_MALFORMED). EPS reported under any unit other
than USD/shares fails closed (EPS_UNIT_MISMATCH).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from src.price.schema import Reason
from src.price.valuation import EPS_CONCEPT_DILUTED, EPS_UNIT, EpsFact, SharesFact

FORMS = frozenset({"10-Q", "10-K", "10-Q/A", "10-K/A"})
CACHE_FILENAME = "{ticker}_companyfacts.json"


class EpsSourceError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _dec(v: object) -> Decimal:
    if isinstance(v, bool) or not isinstance(v, (int, Decimal)):
        raise EpsSourceError(Reason.EPS_SOURCE_MALFORMED)
    d = Decimal(v)
    if not d.is_finite():
        raise EpsSourceError(Reason.EPS_SOURCE_MALFORMED)
    return d


def _date(v: object) -> date:
    if not isinstance(v, str):
        raise EpsSourceError(Reason.EPS_SOURCE_MALFORMED)
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise EpsSourceError(Reason.EPS_SOURCE_MALFORMED) from None


def _opt_int(v: object) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def parse_companyfacts(cf: object) -> tuple[list[EpsFact], list[SharesFact]]:
    if not isinstance(cf, dict) or not isinstance(cf.get("facts"), dict):
        raise EpsSourceError(Reason.EPS_SOURCE_MALFORMED)
    gaap = cf["facts"].get("us-gaap", {})
    concept = gaap.get(EPS_CONCEPT_DILUTED) if isinstance(gaap, dict) else None
    eps: list[EpsFact] = []
    if concept is not None:
        units = concept.get("units") if isinstance(concept, dict) else None
        if not isinstance(units, dict):
            raise EpsSourceError(Reason.EPS_SOURCE_MALFORMED)
        for unit, rows in units.items():
            if not isinstance(rows, list):
                raise EpsSourceError(Reason.EPS_SOURCE_MALFORMED)
            rows = [r for r in rows if isinstance(r, dict) and r.get("form") in FORMS]
            if rows and unit != EPS_UNIT:
                raise EpsSourceError(Reason.EPS_UNIT_MISMATCH)
            for r in rows:
                if "start" not in r:
                    continue                      # instants are not EPS periods
                accn = r.get("accn")
                if not isinstance(accn, str) or not accn:
                    raise EpsSourceError(Reason.EPS_SOURCE_MALFORMED)
                eps.append(EpsFact(start=_date(r["start"]), end=_date(r.get("end")),
                                   value=_dec(r.get("val")), filed=_date(r.get("filed")),
                                   accession=accn, form=r["form"], unit=unit,
                                   concept=EPS_CONCEPT_DILUTED, fy=_opt_int(r.get("fy")),
                                   fp=r.get("fp") if isinstance(r.get("fp"), str) else None))
    shares: list[SharesFact] = []
    dei = cf["facts"].get("dei", {})
    sh = dei.get("EntityCommonStockSharesOutstanding") if isinstance(dei, dict) else None
    if isinstance(sh, dict) and isinstance(sh.get("units"), dict):
        rows = sh["units"].get("shares", [])
        by_key: dict[tuple[date, date], set] = {}
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or r.get("form") not in FORMS:
                continue
            try:
                k = (_date(r.get("end")), _date(r.get("filed")))
                by_key.setdefault(k, set()).add(_dec(r.get("val")))
            except EpsSourceError:
                continue                          # cover shares are optional evidence
        for (as_of, filed), vals in by_key.items():
            if len(vals) == 1:                    # multi-class (ambiguous) dates skipped
                shares.append(SharesFact(as_of, next(iter(vals)), filed))
    return eps, shares


def load_eps_source(raw_dir: Path, ticker: str) -> tuple[list[EpsFact], list[SharesFact]]:
    path = Path(raw_dir) / CACHE_FILENAME.format(ticker=ticker)
    if not path.is_file():
        raise EpsSourceError(Reason.EPS_SOURCE_MISSING)
    try:
        with path.open(encoding="utf-8") as f:
            cf = json.load(f, parse_float=Decimal)
    except (OSError, ValueError):
        raise EpsSourceError(Reason.EPS_SOURCE_MALFORMED) from None
    return parse_companyfacts(cf)
