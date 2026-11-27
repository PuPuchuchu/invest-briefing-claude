from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from src.price.corporate_actions import CorporateActionAssessment
from src.price.schema import (
    CalcStatus, CorporateActionStatus, Interpretability, RawPriceStatus, Reason,
    ShareCountStatus,
)
from src.price.valuation import (
    NI_CONCEPT_COMMON, NI_CONCEPT_PARENT, SCOPE_CONSOLIDATED_NO_DIMENSION,
    ClassPriceInput, ClassSharesInput, NiTtmInput, TtmQuarter, compute_pe_ttm,
    validate_ni_ttm,
)

D0 = date(2026, 11, 25)
CA_OK = CorporateActionAssessment(CorporateActionStatus.NO_EVENT_VERIFIED, (), 0, True, "16")
PRICE = ClassPriceInput("A", D("50"), RawPriceStatus.OK, D0)
SHARES = ClassSharesInput("A", D("1000"), ShareCountStatus.OK,
                          date(2026, 10, 20), date(2026, 10, 30), D0)
def q(fy, fq, start, end, value="625", concept=NI_CONCEPT_COMMON, currency="USD",
      scope=SCOPE_CONSOLIDATED_NO_DIMENSION, filed=None):
    return TtmQuarter(fy, fq, start, end, D(value), concept, currency, scope,
                      filed or date(2026, 10, 30), accession=f"0000-{fy}-{fq}",
                      entity_id="CIK0000001")


# Calendar-year issuer: FY2025 Q4 + FY2026 Q1-Q3 (Oct 2025 - Sep 2026)
QUARTERS = (
    q(2025, 4, date(2025, 10, 1), date(2025, 12, 31), filed=date(2026, 2, 10)),
    q(2026, 1, date(2026, 1, 1), date(2026, 3, 31), filed=date(2026, 4, 30)),
    q(2026, 2, date(2026, 4, 1), date(2026, 6, 30), filed=date(2026, 7, 30)),
    q(2026, 3, date(2026, 7, 1), date(2026, 9, 30), filed=date(2026, 10, 30)),
)
NI = NiTtmInput(D("2500"), (NI_CONCEPT_COMMON,), "USD", date(2025, 10, 1),
                date(2026, 9, 30), date(2026, 10, 30), SCOPE_CONSOLIDATED_NO_DIMENSION,
                "COMMON_STOCKHOLDERS", QUARTERS, entity_id="CIK0000001")


def pe(prices=(PRICE,), shares=(SHARES,), ni=NI, ca=CA_OK, mapped=True):
    return compute_pe_ttm(prices=list(prices), shares=list(shares),
                          class_mapping_resolved=mapped, corporate_actions=ca,
                          ni_ttm=ni, price_session=D0)


def test_basic_pe():
    r = pe()
    assert r.calc_status is CalcStatus.OK and r.value == D(20)  # 50*1000/2500


def test_multi_class_sum():
    p2 = ClassPriceInput("B", D("40"), RawPriceStatus.OK, D0)
    s2 = replace(SHARES, class_id="B", shares=D("500"))
    assert pe([PRICE, p2], [SHARES, s2]).value == D(70000) / D(2500)


def test_class_unresolved_and_mismatch():
    assert pe(mapped=False).calc_status is CalcStatus.MISSING
    assert pe(mapped=False).reasons == (Reason.CLASS_MAPPING_MISSING,)
    other = replace(SHARES, class_id="B")
    assert pe(shares=[other]).reasons == (Reason.CLASS_SET_MISMATCH,)


def test_nonpositive_ni_not_meaningful_no_negative_pe():
    for v in ("0", "-100"):
        qs = QUARTERS[:3] + (replace(QUARTERS[3], value=D(v) - D(1875)),)
        r = pe(ni=replace(NI, value=D(v), quarters=qs))
        assert r.calc_status is CalcStatus.OK and r.value is None
        assert r.interpretability is Interpretability.NOT_MEANINGFUL


def test_shares_missing_or_not_ok():
    r = pe(shares=[replace(SHARES, status=ShareCountStatus.NOT_AVAILABLE, shares=None)])
    assert r.calc_status is CalcStatus.MISSING
    assert r.reasons == (Reason.SHARE_COUNT_NOT_AVAILABLE,)
    for st in (ShareCountStatus.STALE, ShareCountStatus.BASIS_AMBIGUOUS,
               ShareCountStatus.PIT_VIOLATION, ShareCountStatus.CLASS_UNRESOLVED):
        assert pe(shares=[replace(SHARES, status=st)]).calc_status is CalcStatus.INVALID


def test_split_basis_unknown_and_mismatch():
    assert pe(shares=[replace(SHARES, basis_session=None)]).reasons == \
        (Reason.SPLIT_BASIS_UNKNOWN,)
    assert pe(shares=[replace(SHARES, basis_session=date(2026, 11, 24))]).reasons == \
        (Reason.SPLIT_BASIS_MISMATCH,)


def test_shares_pit_violation():
    for bad in (replace(SHARES, shares_filed=D0),                       # filed == D
                replace(SHARES, shares_as_of=date(2026, 11, 26)),       # as_of > D
                replace(SHARES, shares_as_of=date(2026, 11, 1))):       # as_of > filed
        assert pe(shares=[bad]).reasons == (Reason.SHARES_PIT_VIOLATION,)


def test_corporate_action_blocks_valuation():
    for st in (CorporateActionStatus.PENDING_SEC, CorporateActionStatus.UNVERIFIED,
               CorporateActionStatus.CANDIDATE):
        ca = CorporateActionAssessment(st, (Reason.UNSUPPORTED_SCENARIO,), 1, False, None)
        r = pe(ca=ca)
        assert r.calc_status is CalcStatus.INVALID
        assert Reason.CORPORATE_ACTION_BLOCKS_VALUATION in r.reasons


def test_raw_price_not_ok():
    for p in (replace(PRICE, status=RawPriceStatus.STALE),
              replace(PRICE, raw_close=None),
              replace(PRICE, session=date(2026, 11, 24))):
        assert pe(prices=[p]).reasons == (Reason.RAW_PRICE_NOT_OK,)


def test_ni_validation_rules():
    assert validate_ni_ttm(None, D0) == [Reason.NI_TTM_MISSING]
    assert pe(ni=None).calc_status is CalcStatus.MISSING
    cases = {
        Reason.NI_TTM_PIT_VIOLATION: replace(NI, latest_filed=D0),
        Reason.NI_TTM_CONCEPT_MIXED: replace(NI, concepts=(NI_CONCEPT_COMMON,
                                                           NI_CONCEPT_PARENT)),
        Reason.NI_TTM_CONCEPT_NOT_ALLOWED: replace(
            NI, concepts=("ProfitLoss",),
            quarters=tuple(replace(x, concept="ProfitLoss") for x in QUARTERS)),
        Reason.NI_TTM_ATTRIBUTION_UNCLEAR: replace(NI, attribution="PARENT"),
        Reason.NI_TTM_CURRENCY: replace(NI, currency="EUR"),
        Reason.NI_TTM_SCOPE_UNCLEAR: replace(NI, consolidation_scope=None),
        Reason.NI_TTM_PERIOD_INVALID: replace(NI, period_start=date(2026, 4, 1)),
    }
    for reason, ni in cases.items():
        r = pe(ni=ni)
        assert r.calc_status is CalcStatus.INVALID and reason in r.reasons, reason


def test_parent_concept_with_parent_attribution_ok():
    ni = replace(NI, concepts=(NI_CONCEPT_PARENT,), attribution="PARENT",
                 quarters=tuple(replace(x, concept=NI_CONCEPT_PARENT) for x in QUARTERS))
    assert pe(ni=ni).calc_status is CalcStatus.OK


# ---- four-quarter validated-input contract (BLOCKER-3) ----

def test_four_consecutive_quarters_ok():
    r = pe()
    assert r.calc_status is CalcStatus.OK and r.reasons == ()


def test_no_quarter_evidence_is_not_used():
    r = pe(ni=replace(NI, quarters=()))
    assert r.calc_status is CalcStatus.MISSING
    assert r.reasons == (Reason.NI_TTM_UNVALIDATED,)


def _reasons(qs, **kw):
    return pe(ni=replace(NI, quarters=qs, **kw))


def test_missing_quarter():
    r = _reasons(QUARTERS[1:], value=D("1875"), period_start=date(2026, 1, 1))
    assert r.calc_status is CalcStatus.INVALID
    assert Reason.NI_TTM_QUARTERS_INCOMPLETE in r.reasons


def test_duplicate_quarter():
    qs = (QUARTERS[0], QUARTERS[1], QUARTERS[1], QUARTERS[3])
    r = _reasons(qs)
    assert Reason.NI_TTM_QUARTER_DUPLICATE in r.reasons


def test_non_consecutive_fiscal_sequence():
    qs = QUARTERS[:3] + (q(2026, 4, date(2026, 7, 1), date(2026, 9, 30)),)
    r = _reasons(qs)
    assert Reason.NI_TTM_QUARTERS_NOT_CONSECUTIVE in r.reasons


def test_period_gap():
    qs = QUARTERS[:2] + (replace(QUARTERS[2], period_start=date(2026, 4, 3)),) + QUARTERS[3:]
    assert Reason.NI_TTM_PERIOD_GAP in _reasons(qs).reasons


def test_period_overlap_including_zero_day_step():
    qs = QUARTERS[:2] + (replace(QUARTERS[2], period_start=date(2026, 3, 31)),) + QUARTERS[3:]
    assert Reason.NI_TTM_PERIOD_OVERLAP in _reasons(qs).reasons


def test_quarter_length_out_of_range():
    qs = QUARTERS[:3] + (replace(QUARTERS[3], period_end=date(2026, 11, 20)),)
    r = _reasons(qs, period_end=date(2026, 11, 20))
    assert Reason.NI_TTM_QUARTER_LENGTH in r.reasons


def test_53_week_fiscal_year_accepted():
    # 52/53-week issuer: 13,13,13,14-week quarters = 371 days, contiguous
    s = date(2025, 9, 28)
    lens = (91, 91, 91, 98)
    qs, cur = [], s
    for i, n in enumerate(lens):
        end = cur.fromordinal(cur.toordinal() + n - 1)
        qs.append(q(2026, i + 1, cur, end, filed=date(2026, 10, 30)))
        cur = end.fromordinal(end.toordinal() + 1)
    ni = replace(NI, quarters=tuple(qs), period_start=qs[0].period_start,
                 period_end=qs[-1].period_end)
    r = pe(ni=ni)
    assert r.calc_status is CalcStatus.OK and r.reasons == ()


def test_period_length_ok_but_not_four_quarters():
    # Two 'half-year' pieces spanning 365 days: length fine, contract fails.
    qs = (q(2026, 1, date(2025, 10, 1), date(2026, 3, 31), value="1250"),
          q(2026, 2, date(2026, 4, 1), date(2026, 9, 30), value="1250"))
    r = _reasons(qs)
    assert r.calc_status is CalcStatus.INVALID
    assert Reason.NI_TTM_QUARTERS_INCOMPLETE in r.reasons
    assert Reason.NI_TTM_QUARTER_LENGTH in r.reasons


def test_quarter_pit_violation_with_valid_period():
    qs = QUARTERS[:3] + (replace(QUARTERS[3], filed=D0),)
    assert Reason.NI_TTM_PIT_VIOLATION in _reasons(qs).reasons


def test_quarter_concept_currency_scope_mixing():
    for field, value, reason in (
            ("concept", NI_CONCEPT_PARENT, Reason.NI_TTM_CONCEPT_MIXED),
            ("currency", "EUR", Reason.NI_TTM_CURRENCY),
            ("consolidation_scope", "SEGMENT", Reason.NI_TTM_SCOPE_UNCLEAR)):
        qs = QUARTERS[:3] + (replace(QUARTERS[3], **{field: value}),)
        assert reason in _reasons(qs).reasons, field


def test_sum_mismatch():
    assert Reason.NI_TTM_SUM_MISMATCH in pe(ni=replace(NI, value=D("2501"))).reasons


def test_period_length_anomaly_is_warning_not_block():
    # Four valid contiguous quarters of 105 days = 420 days (outside 350-380)
    qs, cur = [], date(2025, 8, 3)
    for i in range(4):
        end = cur.fromordinal(cur.toordinal() + 104)
        qs.append(q(2026, i + 1, cur, end, filed=date(2026, 10, 30)))
        cur = end.fromordinal(end.toordinal() + 1)
    ni = replace(NI, quarters=tuple(qs), period_start=qs[0].period_start,
                 period_end=qs[-1].period_end)
    r = pe(ni=ni)
    assert r.calc_status is CalcStatus.OK and r.value is not None
    assert r.reasons == (Reason.NI_TTM_PERIOD_ANOMALY,)


def test_ni_period_must_end_before_price_session():
    ni = replace(NI, period_end=D0)
    r = pe(ni=ni)
    assert r.calc_status is CalcStatus.INVALID
    assert Reason.NI_TTM_PERIOD_INVALID in r.reasons


def test_one_missing_day_is_a_gap():
    # previous end 2026-03-31, next start 2026-04-02 -> step 2 days (gap)
    qs = QUARTERS[:2] + (replace(QUARTERS[2], period_start=date(2026, 4, 2)),) + QUARTERS[3:]
    assert Reason.NI_TTM_PERIOD_GAP in _reasons(qs).reasons



def test_quarter_provenance_required():
    qs = QUARTERS[:3] + (replace(QUARTERS[3], accession=""),)
    assert Reason.NI_TTM_PROVENANCE_MISSING in _reasons(qs).reasons
    qs = QUARTERS[:3] + (replace(QUARTERS[3], entity_id=""),)
    assert Reason.NI_TTM_PROVENANCE_MISSING in _reasons(qs).reasons


def test_quarters_from_other_entity_rejected():
    qs = QUARTERS[:3] + (replace(QUARTERS[3], entity_id="CIK0000002"),)
    r = _reasons(qs)
    assert r.calc_status is CalcStatus.INVALID
    assert Reason.NI_TTM_ENTITY_MISMATCH in r.reasons
    assert Reason.NI_TTM_ENTITY_MISMATCH in pe(ni=replace(NI, entity_id="")).reasons
