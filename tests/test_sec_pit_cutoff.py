"""
STEP 23 SEC PIT (2026-11-02): SEC companyfacts are gated to
sec_filed_cutoff_date = evaluation_date - 1 calendar day, computed once in
orchestrator.run_fundamentals_pipeline() and passed to all three SEC slicing
calls; non-SEC axes keep evaluation_date. Batch-level: future evaluation
dates refused before any SEC request, SEC snapshot manifest, provenance and
an operational review that never changes ticker results.

Tests that need the real SEC history path (which reaches the protected
module) are marked ``needs_real_history`` and are skipped when that module
is replaced by a guard stub (local guarded runs); CI runs them. All other
tests replace point_in_time history with a deterministic fake.
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.fundamentals import orchestrator as orch
from src.fundamentals import sec_snapshot
from src.fundamentals.batch_runner import (
    FutureEvaluationDateError,
    run_batch,
    run_timing_status,
)

KST = ZoneInfo("Asia/Seoul")
CIK = "0000999002"
EVAL = "2026-11-02"
CUTOFF = "2026-11-01"

_stub = getattr(sys.modules.get("src.fundamentals.sec_history"), "__guard_stub__", False)
needs_real_history = pytest.mark.skipif(_stub, reason="protected module stubbed (local guarded run); runs in CI")


# ------------------------------------------------------------------ fixtures

def _dur(val, start, end, accn, filed, fy, fp="FY", form="10-K"):
    out = {"start": start, "end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form}
    if filed is not None:
        out["filed"] = filed
    return out


def _inst(val, end, accn, filed, fy, fp, form):
    return {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}


A_OLD = "0000999002-24-000001"   # FY2023 10-K, filed long ago
A_PREV = "0000999002-26-000001"  # FY2024 10-K, filed on the cutoff day (EVAL - 1)
A_SAME = "0000999002-26-000002"  # FY2025 10-K, filed ON the evaluation date
A_NOFILED = "0000999002-26-000003"


def _annual(v23, v24, v25):
    return {"units": {"USD": [
        _dur(v23, "2023-01-01", "2023-12-31", A_OLD, "2024-02-01", 2023),
        _dur(v24, "2024-01-01", "2024-12-31", A_PREV, CUTOFF, 2024),
        _dur(v25, "2025-01-01", "2025-12-31", A_SAME, EVAL, 2025),
    ]}}


def _instants(v23, v24, v25):
    return {"units": {"USD": [
        _inst(v23, "2023-12-31", A_OLD, "2024-02-01", 2023, "FY", "10-K"),
        _inst(v24, "2024-12-31", A_PREV, CUTOFF, 2024, "FY", "10-K"),
        _inst(v25, "2025-12-31", A_SAME, EVAL, 2025, "FY", "10-K"),
    ]}}


def _fixture():
    revenues = _annual(1000, 1100, 1300)
    # An observation with no "filed" at all must never be usable.
    revenues["units"]["USD"].append(_dur(9999, "2026-01-01", "2026-06-30", A_NOFILED, None, 2026, "Q2", "10-Q"))
    return {
        "cik": 999002,
        "entityName": "Cutoff Test Co",
        "facts": {"us-gaap": {
            "Revenues": revenues,
            "NetIncomeLoss": _annual(100, 110, 130),
            "NetCashProvidedByUsedInOperatingActivities": _annual(200, 220, 260),
            "PaymentsToAcquirePropertyPlantAndEquipment": _annual(50, 55, 65),
            "Assets": _instants(5000, 5500, 6000),
            "CashAndCashEquivalentsAtCarryingValue": _instants(300, 330, 390),
            "LongTermDebtCurrent": _instants(10, 11, 13),
            "LongTermDebtNoncurrent": _instants(90, 99, 117),
        }},
    }


CT_ROWS = [{
    "ticker": "CUT", "cik": CIK, "company_type": "NON_FINANCIAL", "effective_from": "2026-09-20",
    "effective_to": None, "classification_available_date": "2026-09-20",
    "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE", "mapping_rationale": "test fixture",
    "reviewed_at_utc": "2026-10-26T00:00:00Z", "decision_ref": "TEST",
}]
MEMBERS = [{"cik": CIK, "ticker": "CUT", "universe_type": "WATCHLIST", "membership_status": "ACTIVE",
            "membership_source": "LEGACY_SEED", "membership_version": "1", "effective_from": "2026-09-21",
            "effective_to": None, "membership_available_date": "2026-09-21"}]
RUN_AT = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)  # 15:00 KST on EVAL


class Spy:
    """Records the date given to every SEC slicing call. History is replaced
    by a deterministic fake unless real=True (CI only)."""

    def __init__(self, monkeypatch, real_history=False):
        self.gate_dates, self.history_dates, self.gated = [], [], []
        real_gate = orch.gate_companyfacts_as_of
        real_hist = orch.get_point_in_time_history

        def gate(data, d):
            self.gate_dates.append(d)
            out = real_gate(data, d)
            self.gated.append(out)
            return out

        def hist(data, ns, concept, d):
            self.history_dates.append(d)
            if real_history:
                return real_hist(data, ns, concept, d)
            return None

        monkeypatch.setattr(orch, "gate_companyfacts_as_of", gate)
        monkeypatch.setattr(orch, "get_point_in_time_history", hist)


def _run(evaluation_date=EVAL, raw=None, **kw):
    return orch.run_fundamentals_pipeline(
        ticker="CUT", cik=CIK, evaluation_date=evaluation_date, company_type="NON_FINANCIAL",
        company_type_rows=copy.deepcopy(CT_ROWS), raw_companyfacts=copy.deepcopy(raw or _fixture()), **kw,
    )


def _gated_accessions(gated, concept="Revenues"):
    return {o["accn"] for o in gated["facts"]["us-gaap"][concept]["units"]["USD"]}


def _metric(result, name):
    return result["stages"]["normalization"]["normalized"]["metrics"][name]


# ------------------------------------------------------------------ T1-T4 cutoff semantics

def test_t1_filed_on_evaluation_date_is_excluded(monkeypatch):
    spy = Spy(monkeypatch)
    res = _run()
    assert A_SAME not in _gated_accessions(spy.gated[0])
    assert _metric(res, "revenue")["period_end"] == "2024-12-31"  # not the FY2025 value filed on EVAL
    assert res["stages"]["pit_gating"]["sec_filed_cutoff_date"] == CUTOFF
    assert res["stages"]["pit_gating"]["evaluation_date"] == EVAL  # meaning unchanged


def test_t2_filed_on_previous_day_is_included(monkeypatch):
    spy = Spy(monkeypatch)
    res = _run()
    assert A_PREV in _gated_accessions(spy.gated[0])
    assert _metric(res, "revenue")["value"] == 1100


def test_t3_older_filing_is_included_and_next_day_admits_the_same_day_filing(monkeypatch):
    spy = Spy(monkeypatch)
    _run()
    assert A_OLD in _gated_accessions(spy.gated[0])
    res = _run("2026-11-03")
    assert A_SAME in _gated_accessions(spy.gated[-1])
    assert _metric(res, "revenue")["value"] == 1300


def test_t4_missing_filed_fails_closed(monkeypatch):
    spy = Spy(monkeypatch)
    _run("2030-01-01")
    assert A_NOFILED not in _gated_accessions(spy.gated[0])


# ------------------------------------------------------------------ T5-T8 path consistency

def test_t5_same_cutoff_reaches_all_three_sec_slicing_calls(monkeypatch):
    spy = Spy(monkeypatch)
    res = _run()
    expected = date(2026, 11, 1)
    assert spy.gate_dates == [expected]
    assert len(spy.history_dates) >= 2  # annual history + growth history both ran
    assert set(spy.history_dates) == {expected}
    assert res["sec_filed_cutoff_date"] == CUTOFF
    assert res["sec_pit_policy_version"] == orch.SEC_PIT_POLICY_VERSION == "SEC_PIT_NEXT_KST_DAY_V1"
    assert res["schema_version"] == "orchestrator_v0.2"


@needs_real_history
def test_t6_real_history_including_quarters_never_exceeds_cutoff(monkeypatch):
    spy = Spy(monkeypatch, real_history=True)
    raw = _fixture()
    # Quarterly observations, one filed on EVAL (must not enter quarter reconstruction).
    raw["facts"]["us-gaap"]["Revenues"]["units"]["USD"] += [
        _dur(300, "2026-01-01", "2026-03-31", "0000999002-26-000010", "2026-04-30", 2026, "Q1", "10-Q"),
        _dur(650, "2026-01-01", "2026-06-30", "0000999002-26-000011", "2026-07-30", 2026, "Q2", "10-Q"),
        _dur(1000, "2026-01-01", "2026-09-30", "0000999002-26-000012", EVAL, 2026, "Q3", "10-Q"),
    ]
    res = _run(raw=raw)
    assert spy.history_dates and set(spy.history_dates) == {date(2026, 11, 1)}

    def filed_dates(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "filed" and isinstance(v, str):
                    yield v
                else:
                    yield from filed_dates(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from filed_dates(v)

    seen = list(filed_dates(res["stages"]["annual_history"])) + list(filed_dates(res["stages"]["growth"]))
    assert all(d <= CUTOFF for d in seen)
    assert "0000999002-26-000012" not in json.dumps(res["stages"]["growth"])


def test_t7_non_sec_axes_keep_evaluation_date(monkeypatch):
    Spy(monkeypatch)
    seen = {}

    def wrap(name):
        real = getattr(orch, name)

        def inner(*args, **kwargs):
            seen.setdefault(name, []).append(args)
            return real(*args, **kwargs)
        monkeypatch.setattr(orch, name, inner)

    for name in ("get_company_type_as_of", "apply_metric_eligibility_gate", "get_universe_membership_as_of",
                 "get_peer_mapping_as_of"):
        wrap(name)
    _run(metric_eligibility_rows=[], membership_rows=copy.deepcopy(MEMBERS), peer_rows=[])
    ev = date(2026, 11, 2)
    assert seen["get_company_type_as_of"][0][1] == ev
    assert seen["apply_metric_eligibility_gate"][0][2] == ev
    assert {a[2] for a in seen["get_universe_membership_as_of"]} == {ev}
    assert seen["get_peer_mapping_as_of"][0][1] == ev


@needs_real_history
def test_t8_pit_self_validation_still_detects_cutoff_mismatch():
    from src.fundamentals import point_in_time as pit
    raw = _fixture()
    hist = pit.get_point_in_time_history(raw, "us-gaap", "Revenues", date(2026, 11, 1))
    assert pit.validate_point_in_time_history(hist, date(2026, 11, 1)) == []
    assert "evaluation_date_mismatch" in pit.validate_point_in_time_history(hist, date(2026, 11, 2))


# ------------------------------------------------------------------ T9-T12 determinism, manifest, helper

def _strip_volatile(result):
    r = copy.deepcopy(result)
    r["stages"]["normalization"]["normalized"].pop("normalized_at", None)
    return r


def test_t9_same_inputs_same_pipeline_result(monkeypatch):
    Spy(monkeypatch)
    assert _strip_volatile(_run()) == _strip_volatile(_run())


def test_t10_one_byte_change_changes_hashes_and_is_detected(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    f = raw / "CUT_companyfacts.json"
    f.write_text(json.dumps({"a": 1}), encoding="utf-8")
    _, entry = sec_snapshot.load_input_file(f, raw)
    entry.update(ticker="CUT", cik=CIK, source="CACHE")
    m1 = sec_snapshot.build_manifest([entry])
    assert sec_snapshot.verify_manifest(m1, raw)["status"] == sec_snapshot.VERIFY_OK
    f.write_text(json.dumps({"a": 2}), encoding="utf-8")
    _, entry2 = sec_snapshot.load_input_file(f, raw)
    entry2.update(ticker="CUT", cik=CIK, source="CACHE")
    m2 = sec_snapshot.build_manifest([entry2])
    assert sec_snapshot.snapshot_content_sha256(m1) != sec_snapshot.snapshot_content_sha256(m2)
    assert sec_snapshot.manifest_sha256(m1) != sec_snapshot.manifest_sha256(m2)
    v = sec_snapshot.verify_manifest(m1, raw)
    assert v["status"] == sec_snapshot.VERIFY_FAIL and v["mismatched_files"] == ["CUT_companyfacts.json"]


@pytest.mark.parametrize("ev, expected", [
    ("2026-03-08", "2026-03-07"),  # US DST starts 2026-03-08
    ("2026-03-09", "2026-03-08"),
    ("2026-11-01", "2026-10-31"),  # US DST ends 2026-11-01
    ("2026-11-02", "2026-11-01"),
    ("2026-11-09", "2026-11-08"),  # Monday -> Sunday (Friday filings already included)
    ("2026-01-20", "2026-01-19"),  # day after a US federal holiday (MLK Day)
    ("2027-01-01", "2026-12-31"),  # year boundary
    ("2028-03-01", "2028-02-29"),  # leap day
])
def test_t11_cutoff_is_one_calendar_day_earlier(ev, expected):
    assert orch.sec_filed_cutoff_for(ev).isoformat() == expected
    assert orch.sec_filed_cutoff_for(date.fromisoformat(ev)).isoformat() == expected


@pytest.mark.parametrize("bad", [None, "", "2026-13-01", "not-a-date"])
def test_t12_cutoff_helper_fails_closed(bad):
    with pytest.raises(ValueError):
        orch.sec_filed_cutoff_for(bad)


# ------------------------------------------------------------------ T13 TEM-like replica

def test_t13_quarterly_filing_on_evaluation_date_waits_one_day(monkeypatch):
    """Replica of the 2026-11-02 TEM case: a 10-Q filed on the evaluation
    date must not move instant metrics until the next evaluation date."""
    Spy(monkeypatch)
    raw = _fixture()
    q2 = ("0000999002-26-000020", "2026-07-30")
    q3 = ("0000999002-26-000021", EVAL)
    for concept, (v2, v3) in {"Assets": (6100, 6200), "CashAndCashEquivalentsAtCarryingValue": (599, 590)}.items():
        raw["facts"]["us-gaap"][concept]["units"]["USD"] += [
            _inst(v2, "2026-06-30", q2[0], q2[1], 2026, "Q2", "10-Q"),
            _inst(v3, "2026-09-30", q3[0], q3[1], 2026, "Q3", "10-Q"),
        ]
    on_eval = _metric(_run(raw=raw), "cash")
    next_day = _metric(_run("2026-11-03", raw=raw), "cash")
    assert (on_eval["period_end"], on_eval["value"]) == ("2026-06-30", 599)
    assert (next_day["period_end"], next_day["value"]) == ("2026-09-30", 590)


# ------------------------------------------------------------------ T14 metadata

def _cli():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_fundamentals_batch.py"
    spec = importlib.util.spec_from_file_location("run_fundamentals_batch_pit", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_t14_provenance_and_run_timing():
    cli = _cli()
    meta = cli.collect_run_metadata(env={}, head_sha=None)
    assert meta["github_sha"] is None and meta["commit_sha_consistency"] == "UNKNOWN"
    assert "COMMIT_SHA_UNVERIFIED" in meta["operational_flags"]
    env = {"GITHUB_SHA": "a" * 40, "GITHUB_WORKFLOW_SHA": "b" * 40, "GITHUB_RUN_ID": "1",
           "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/x.yml@refs/heads/main",
           "GITHUB_EVENT_NAME": "workflow_dispatch"}
    assert cli.collect_run_metadata(env=env, head_sha="a" * 40)["commit_sha_consistency"] == "MATCH"
    mism = cli.collect_run_metadata(env=env, head_sha="c" * 40)
    assert mism["commit_sha_consistency"] == "MISMATCH" and "COMMIT_SHA_MISMATCH" in mism["operational_flags"]
    assert mism["github_workflow_sha"] == "b" * 40  # recorded separately, never used as the code SHA
    assert mism["python_version"]
    ev = date(2026, 11, 2)
    at = lambda h, m: datetime(2026, 11, 2, h, m, tzinfo=KST)  # noqa: E731
    assert run_timing_status(ev, at(12, 59)) == "EARLY_RUN"
    assert run_timing_status(ev, at(13, 0)) == "RECOMMENDED_WINDOW"
    assert run_timing_status(date(2026, 11, 1), at(9, 0)) == "HISTORICAL"


# ------------------------------------------------------------------ batch helpers

def _batch(tmp_path, *, evaluation_date=EVAL, run_started_at=RUN_AT, raw_dir=None, out="out", fetch=None, **kw):
    return run_batch(
        evaluation_date=evaluation_date, company_type_rows=copy.deepcopy(CT_ROWS),
        membership_rows=copy.deepcopy(MEMBERS), pipeline_fn=orch.run_fundamentals_pipeline,
        fetch_fn=fetch or (lambda t, c: _fixture()), raw_dir=raw_dir or (tmp_path / "raw"),
        output_dir=tmp_path / out, run_started_at=run_started_at, **kw,
    )


VOLATILE_RECORD = ("raw_input",)
VOLATILE_SUMMARY = ("generated_at_utc", "reproducibility", "operational_review")


def _analysis_payload(summary, out_dir):
    s = {k: v for k, v in summary.items() if k not in VOLATILE_SUMMARY}
    s["records"] = [{k: v for k, v in r.items() if k not in VOLATILE_RECORD + ("raw_source",)} for r in s["records"]]
    per_ticker = {}
    for p in sorted(Path(out_dir).glob("*.json")):
        if p.name in ("summary.json", "coverage.json", sec_snapshot.MANIFEST_FILENAME):
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        d["stages"]["normalization"]["normalized"].pop("normalized_at", None)
        per_ticker[p.name] = d
    cov = json.loads((Path(out_dir) / "coverage.json").read_text(encoding="utf-8"))
    cov.pop("generated_at_utc", None)
    cov["universe"].pop("raw_source_counts", None)  # acquisition (FETCHED/CACHE), not analysis
    return s, per_ticker, cov


# ------------------------------------------------------------------ batch tests

def test_future_evaluation_date_refused_before_any_sec_request(tmp_path, monkeypatch):
    Spy(monkeypatch)
    calls = []
    with pytest.raises(FutureEvaluationDateError):
        _batch(tmp_path, evaluation_date="2026-11-03",
               run_started_at=datetime(2026, 11, 2, 14, 59, tzinfo=timezone.utc),  # 23:59 KST 11-02
               fetch=lambda t, c: calls.append(t) or _fixture())
    assert calls == []
    assert not (tmp_path / "raw" / "CUT_companyfacts.json").exists()
    # 15:00 UTC is 00:00 KST on 11-03: no longer future.
    s = _batch(tmp_path, evaluation_date="2026-11-03", run_started_at=datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc))
    assert s["records"][0]["status"] == "OK"


def test_cli_refuses_future_date_before_loading_or_fetching(monkeypatch):
    cli = _cli()
    touched = []
    monkeypatch.setattr(cli, "load_reference_rows", lambda: touched.append("ref") or ([], []))
    monkeypatch.setattr(cli, "run_batch", lambda **k: touched.append("batch"))
    future = (datetime.now(KST).date() + timedelta(days=2)).isoformat()
    assert cli.main(["--no-fetch", "--evaluation-date", future]) == 2
    assert touched == []


def test_fetched_input_is_reloaded_from_the_stored_file(tmp_path, monkeypatch):
    Spy(monkeypatch)
    seen = []

    def pipeline(**kwargs):
        seen.append(kwargs["raw_companyfacts"])
        return orch.run_fundamentals_pipeline(**kwargs)

    fetched = _fixture()
    s = run_batch(evaluation_date=EVAL, company_type_rows=copy.deepcopy(CT_ROWS), membership_rows=copy.deepcopy(MEMBERS),
                  pipeline_fn=pipeline, fetch_fn=lambda t, c: fetched, raw_dir=tmp_path / "raw",
                  output_dir=tmp_path / "out", run_started_at=RUN_AT)
    stored = tmp_path / "raw" / "CUT_companyfacts.json"
    assert seen[0] is not fetched
    assert seen[0] == json.loads(stored.read_bytes())
    rec = s["records"][0]
    assert rec["raw_source"] == "FETCHED" and rec["raw_input"]["fetched_at_utc"]
    assert rec["raw_input"]["sha256"] == sec_snapshot.sha256_bytes(stored.read_bytes())
    manifest = json.loads((tmp_path / "out" / sec_snapshot.MANIFEST_FILENAME).read_bytes())
    assert manifest["files"] == [{"path": "CUT_companyfacts.json", "ticker": "CUT", "cik": CIK, "source": "FETCHED",
                                  "size_bytes": stored.stat().st_size, "sha256": rec["raw_input"]["sha256"]}]
    assert (tmp_path / "out" / sec_snapshot.MANIFEST_FILENAME).read_bytes() == sec_snapshot.canonical_json_bytes(manifest)
    assert s["reproducibility"]["sec_snapshot"]["manifest_sha256"] == sec_snapshot.manifest_sha256(manifest)


def test_cached_input_has_no_invented_fetch_time_and_unused_files_are_not_in_manifest(tmp_path, monkeypatch):
    Spy(monkeypatch)
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "CUT_companyfacts.json").write_text(json.dumps(_fixture()), encoding="utf-8")
    (raw / "OTHER_companyfacts.json").write_text("{}", encoding="utf-8")  # present but not an input
    s = _batch(tmp_path, raw_dir=raw, fetch=lambda t, c: pytest.fail("cache must be used"))
    rec = s["records"][0]
    assert rec["raw_source"] == "CACHE" and rec["raw_input"]["fetched_at_utc"] is None
    manifest = json.loads((tmp_path / "out" / sec_snapshot.MANIFEST_FILENAME).read_bytes())
    assert [f["path"] for f in manifest["files"]] == ["CUT_companyfacts.json"]


def test_manifest_verification_fails_closed(tmp_path, monkeypatch):
    Spy(monkeypatch)
    s = _batch(tmp_path)
    manifest = json.loads((tmp_path / "out" / sec_snapshot.MANIFEST_FILENAME).read_bytes())
    assert sec_snapshot.verify_manifest(manifest, tmp_path / "raw")["status"] == "OK"
    empty = tmp_path / "artifact_empty"
    empty.mkdir()
    v = sec_snapshot.verify_manifest(manifest, empty)
    assert v["status"] == "FAIL" and v["missing_files"] == ["CUT_companyfacts.json"]
    none = sec_snapshot.build_manifest([])
    assert none["status"] == "NO_SEC_INPUTS"
    assert sec_snapshot.verify_manifest(none, empty)["status"] == "NO_SEC_INPUTS"  # never OK
    assert sec_snapshot.verify_manifest({"manifest_version": "X"}, empty)["status"] == "FAIL"
    assert s["reproducibility"]["sec_snapshot"]["status"] == "OK"


def test_batch_reproducibility_metadata(tmp_path, monkeypatch):
    Spy(monkeypatch)
    meta = {"github_sha": "a" * 40, "checkout_head_sha": "c" * 40, "commit_sha_consistency": "MISMATCH",
            "operational_flags": ["COMMIT_SHA_MISMATCH"]}
    s = _batch(tmp_path, run_metadata=meta)
    rep = s["reproducibility"]
    assert rep["sec_filed_cutoff_date"] == CUTOFF and rep["sec_pit_policy_version"] == "SEC_PIT_NEXT_KST_DAY_V1"
    assert rep["sec_cutoff_consistency"]["status"] == "MATCH"
    assert rep["governance_policy_version"] == "NO_METRIC_ELIGIBILITY_GATE"
    assert rep["run_timing_status"] == "RECOMMENDED_WINDOW"
    assert rep["provenance"]["checkout_head_sha"] == "c" * 40
    assert "operational_flags" not in rep["provenance"]
    assert s["operational_review"]["status"] == "REVIEW"
    assert [r["code"] for r in s["operational_review"]["reasons"]] == ["COMMIT_SHA_MISMATCH"]
    assert s["schema_version"] == "fundamentals_batch.v2"


# ------------------------------------------------------------------ adversarial A1-A4

def test_a1_wrong_date_on_one_slicing_path_is_detected(monkeypatch):
    """Simulated regression: growth history receives evaluation_date while
    the other calls receive the cutoff. The path-consistency check used by
    T5 must flag it."""
    spy = Spy(monkeypatch)
    real = orch._pit_history_for_selected_concept

    def buggy(gated, _cutoff, metric_result):
        return real(gated, date(2026, 11, 2), metric_result)

    monkeypatch.setattr(orch, "_pit_history_for_selected_concept", buggy)
    _run()

    def uniform_cutoff():
        assert set(spy.gate_dates) | set(spy.history_dates) == {date(2026, 11, 1)}

    with pytest.raises(AssertionError):
        uniform_cutoff()


def test_a2_same_snapshot_same_code_same_policy_same_analysis(tmp_path, monkeypatch):
    Spy(monkeypatch)
    s1 = _batch(tmp_path, out="out1")
    raw = tmp_path / "raw"  # second run reads the stored snapshot (CACHE)
    s2 = _batch(tmp_path, raw_dir=raw, out="out2", fetch=lambda t, c: pytest.fail("must replay stored file"),
                run_started_at=RUN_AT + timedelta(hours=1))
    assert _analysis_payload(s1, tmp_path / "out1") == _analysis_payload(s2, tmp_path / "out2")
    a, b = s1["reproducibility"]["sec_snapshot"], s2["reproducibility"]["sec_snapshot"]
    assert a["snapshot_content_sha256"] == b["snapshot_content_sha256"]  # same bytes
    assert a["manifest_sha256"] != b["manifest_sha256"]  # source differs: FETCHED vs CACHE
    # Volatile fields are checked separately, not ignored.
    assert s1["reproducibility"]["run_started_at_utc"] != s2["reproducibility"]["run_started_at_utc"]


def _evidence_row(accession, period_end, filing_date):
    from src.fundamentals.finance_lease_evidence_registry_io import read_finance_lease_evidence_csv
    base = next(r for r in read_finance_lease_evidence_csv(
        Path(__file__).resolve().parents[1] / "data/reference/finance_lease_evidence.csv") if r["ticker"] == "AAPL")
    row = dict(base)
    row.update(ticker="CUT", cik=CIK, accession=accession, period_end=period_end, filing_date=filing_date,
               evidence_available_date="2026-10-31", reviewed_at_utc="2026-10-30T00:00:00Z")
    return row


def test_a3_finance_lease_evidence_for_a_same_day_filing_is_not_reached(tmp_path, monkeypatch):
    Spy(monkeypatch)
    row = _evidence_row(A_SAME, "2025-12-31", EVAL)
    s = _batch(tmp_path, finance_lease_evidence_rows=[row])
    t2 = s["records"][0]["total_debt_including_finance_leases"]
    assert t2["accession"] == A_PREV  # tier 1 comes from the cutoff-gated filing
    assert t2["reason_code"] == "NO_FINANCE_LEASE_EVIDENCE" and t2["value"] is None
    # Next day the same row IS reachable: the gating, not the row, blocked it.
    s2 = _batch(tmp_path, evaluation_date="2026-11-03", out="out2", raw_dir=tmp_path / "raw2",
                run_started_at=datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc), finance_lease_evidence_rows=[row])
    t2b = s2["records"][0]["total_debt_including_finance_leases"]
    assert t2b["accession"] == A_SAME and t2b.get("reason_code") != "NO_FINANCE_LEASE_EVIDENCE"


def test_a4_operational_review_never_changes_ticker_results(tmp_path, monkeypatch):
    Spy(monkeypatch)
    early = _batch(tmp_path, out="early", run_started_at=datetime(2026, 11, 2, 12, 59, tzinfo=KST))
    late = _batch(tmp_path, out="late", raw_dir=tmp_path / "raw", run_started_at=datetime(2026, 11, 2, 13, 1, tzinfo=KST))
    assert early["operational_review"]["status"] == "REVIEW"
    assert "EARLY_RUN" in [r["code"] for r in early["operational_review"]["reasons"]]
    assert late["reproducibility"]["run_timing_status"] == "RECOMMENDED_WINDOW"
    assert _analysis_payload(early, tmp_path / "early") == _analysis_payload(late, tmp_path / "late")
    assert early["records"][0]["status"] == "OK"
