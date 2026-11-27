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
    ClassPriceInput, ClassSharesInput, NiTtmInput, compute_pe_ttm, validate_ni_ttm,
)

D0 = date(2026, 11, 25)
CA_OK = CorporateActionAssessment(CorporateActionStatus.NO_EVENT_VERIFIED, (), 0, True, "16")
PRICE = ClassPriceInput("A", D("50"), RawPriceStatus.OK, D0)
SHARES = ClassSharesInput("A", D("1000"), ShareCountStatus.OK,
                          date(2026, 10, 20), date(2026, 10, 30), D0)
NI = NiTtmInput(D("2500"), (NI_CONCEPT_COMMON,), "USD", date(2025, 10, 1),
                date(2026, 9, 30), date(2026, 10, 30), SCOPE_CONSOLIDATED_NO_DIMENSION,
                "COMMON_STOCKHOLDERS")


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
        r = pe(ni=replace(NI, value=D(v)))
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
        Reason.NI_TTM_CONCEPT_NOT_ALLOWED: replace(NI, concepts=("ProfitLoss",)),
        Reason.NI_TTM_ATTRIBUTION_UNCLEAR: replace(NI, attribution="PARENT"),
        Reason.NI_TTM_CURRENCY: replace(NI, currency="EUR"),
        Reason.NI_TTM_SCOPE_UNCLEAR: replace(NI, consolidation_scope=None),
        Reason.NI_TTM_PERIOD_INVALID: replace(NI, period_start=date(2026, 4, 1)),
    }
    for reason, ni in cases.items():
        r = pe(ni=ni)
        assert r.calc_status is CalcStatus.INVALID and reason in r.reasons, reason


def test_parent_concept_with_parent_attribution_ok():
    ni = replace(NI, concepts=(NI_CONCEPT_PARENT,), attribution="PARENT")
    assert pe(ni=ni).calc_status is CalcStatus.OK


def test_53_week_period_accepted():
    ni = replace(NI, period_start=date(2025, 9, 24))   # 372 days
    assert pe(ni=ni).calc_status is CalcStatus.OK


def test_ni_period_must_end_before_price_session():
    ni = replace(NI, period_start=date(2025, 11, 26), period_end=D0)
    r = pe(ni=ni)
    assert r.calc_status is CalcStatus.INVALID
    assert Reason.NI_TTM_PERIOD_INVALID in r.reasons
