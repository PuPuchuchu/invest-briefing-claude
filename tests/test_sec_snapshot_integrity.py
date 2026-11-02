"""
STEP 23 SEC PIT, C1-C5: SEC snapshot integrity.

C1 verify_manifest() fails closed on every malformed or adversarial input
   (structured FAIL, never an exception, never OK).
C2 run_batch() verifies the snapshot at the end of the run (manifest file
   written, re-read and compared with the in-memory expected hash; listed
   raw files compared byte for byte); a failure is a batch-level
   operational FAIL and CLI exit 3, and never changes ticker results.
C3 scripts/verify_sec_snapshot.py checks downloaded results/raw ZIPs
   offline, read-only, rejecting unsafe ZIP members.

Every failing case asserts the specific detection (status plus the field
or message that names the cause), not merely that something raised.
No test here needs the protected SEC history module: C2 uses a fake
pipeline that reports the SEC cutoff the way the orchestrator does.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.fundamentals import sec_snapshot as snap
from src.fundamentals.batch_runner import run_batch

REPO = Path(__file__).resolve().parents[1]
CIK = "0000999003"


# ------------------------------------------------------------------ helpers

def _write_raw(raw: Path, name="AAA_companyfacts.json", payload=None) -> Path:
    raw.mkdir(parents=True, exist_ok=True)
    f = raw / name
    f.write_text(json.dumps(payload or {"cik": 999003, "facts": {}}), encoding="utf-8")
    return f


def _manifest_for(raw: Path, names=("AAA_companyfacts.json",), tickers=("AAA",)) -> dict:
    entries = []
    for name, ticker in zip(names, tickers):
        _, e = snap.load_input_file(raw / name, raw)
        e.update(ticker=ticker, cik=CIK, source="CACHE")
        entries.append(e)
    return snap.build_manifest(entries)


@pytest.fixture
def good(tmp_path):
    raw = tmp_path / "raw"
    _write_raw(raw)
    _write_raw(raw, "BBB_companyfacts.json", {"cik": 999004, "facts": {"x": 1}})
    m = _manifest_for(raw, ("AAA_companyfacts.json", "BBB_companyfacts.json"), ("AAA", "BBB"))
    assert snap.verify_manifest(m, raw)["status"] == snap.VERIFY_OK
    return raw, m


def _fails(m, raw, *, expect_error=None, **kw):
    res = snap.verify_manifest(m, raw, **kw)
    assert res["status"] == snap.VERIFY_FAIL, res
    if expect_error is not None:
        blob = json.dumps(res)
        assert expect_error in blob, (expect_error, res)
    return res


# ------------------------------------------------------------------ C1: structure

@pytest.mark.parametrize("field", sorted(snap.MANIFEST_KEYS))
def test_c1_missing_manifest_field(good, field):
    raw, m = good
    m = copy.deepcopy(m)
    del m[field]
    _fails(m, raw, expect_error="missing manifest fields")


def test_c1_wrong_manifest_version(good):
    raw, m = good
    _fails({**m, "manifest_version": "SEC_SNAPSHOT_MANIFEST_V0"}, raw, expect_error="unsupported manifest_version")


def test_c1_invalid_status(good):
    raw, m = good
    _fails({**m, "status": "WHATEVER"}, raw, expect_error="invalid status")


def test_c1_status_inconsistent_with_files(good):
    raw, m = good
    _fails({**m, "status": "NO_SEC_INPUTS"}, raw, expect_error="NO_SEC_INPUTS requires no files")


def test_c1_files_not_a_list(good):
    raw, m = good
    _fails({**m, "files": {"a": 1}, "file_count": 0}, raw, expect_error="files is not a list")


def test_c1_file_count_mismatch(good):
    raw, m = good
    _fails({**m, "file_count": 5}, raw, expect_error="file_count 5 does not match 2")


def test_c1_duplicate_path(good):
    raw, m = good
    m = copy.deepcopy(m)
    m["files"].append(copy.deepcopy(m["files"][0]))
    m["file_count"] = 3
    _fails(m, raw, expect_error="duplicate path")


@pytest.mark.parametrize("bad_path, error", [
    ("../outside.json", "'..' segments"),
    ("sub/../../outside.json", "'..' segments"),
    ("/etc/passwd", "absolute path"),
    ("C:/x.json", "drive-qualified"),
    ("C:x.json", "drive-qualified"),
    ("dir\\x.json", "backslash"),
    ("./AAA_companyfacts.json", "'..' segments"),
    ("", "non-empty string"),
])
def test_c1_unsafe_paths(good, bad_path, error):
    raw, m = good
    m = copy.deepcopy(m)
    m["files"][0]["path"] = bad_path
    m["files"].sort(key=lambda f: f["path"])
    _fails(m, raw, expect_error=error)


def test_c1_dotdot_escape_is_refused_even_with_matching_hash(tmp_path, good):
    raw, m = good
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": 1}', encoding="utf-8")
    m = copy.deepcopy(m)
    data = outside.read_bytes()
    m["files"][0].update(path="../outside.json", size_bytes=len(data), sha256=snap.sha256_bytes(data))
    m["files"].sort(key=lambda f: f["path"])
    _fails(m, raw, expect_error="'..' segments")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink support")
def test_c1_symlink_to_outside_is_refused(tmp_path, good):
    raw, m = good
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": 1}', encoding="utf-8")
    try:
        (raw / "LINK_companyfacts.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted here")
    data = outside.read_bytes()
    m = copy.deepcopy(m)
    m["files"].append({"path": "LINK_companyfacts.json", "ticker": "LINK", "cik": CIK, "source": "CACHE",
                       "size_bytes": len(data), "sha256": snap.sha256_bytes(data)})
    m["files"].sort(key=lambda f: f["path"])
    m["file_count"] = 3
    _fails(m, raw, expect_error="symbolic link")


@pytest.mark.parametrize("field, value, error", [
    ("ticker", "", "invalid ticker"),
    ("ticker", 123, "invalid ticker"),
    ("cik", "999003", "invalid cik"),
    ("cik", 999003, "invalid cik"),
    ("source", "DOWNLOADED", "invalid source"),
    ("sha256", "XYZ", "invalid sha256"),
    ("sha256", "A" * 64, "invalid sha256"),
    ("size_bytes", -1, "invalid size_bytes"),
    ("size_bytes", "10", "invalid size_bytes"),
    ("size_bytes", True, "invalid size_bytes"),
])
def test_c1_invalid_file_fields(good, field, value, error):
    raw, m = good
    m = copy.deepcopy(m)
    m["files"][0][field] = value
    _fails(m, raw, expect_error=error)


@pytest.mark.parametrize("field", sorted(snap.FILE_KEYS))
def test_c1_missing_file_field_is_fail_not_exception(good, field):
    raw, m = good
    m = copy.deepcopy(m)
    del m["files"][0][field]
    _fails(m, raw, expect_error="missing fields")


def test_c1_unexpected_input_types_never_raise(good):
    raw, _ = good
    for bad in (None, [], "manifest", 42, {"files": None}):
        assert snap.verify_manifest(bad, raw)["status"] == snap.VERIFY_FAIL


# ------------------------------------------------------------------ C1: content

def test_c1_missing_file(good):
    raw, m = good
    (raw / "BBB_companyfacts.json").unlink()
    res = _fails(m, raw)
    assert res["missing_files"] == ["BBB_companyfacts.json"]


def test_c1_size_mismatch(good):
    raw, m = good
    m = copy.deepcopy(m)
    m["files"][0]["size_bytes"] += 1
    res = _fails(m, raw)
    assert res["mismatched_files"] == ["AAA_companyfacts.json"]


def test_c1_one_byte_change(good):
    raw, m = good
    f = raw / "AAA_companyfacts.json"
    b = bytearray(f.read_bytes())
    b[-2] = ord("1") if b[-2] != ord("1") else ord("2")
    f.write_bytes(bytes(b))
    res = _fails(m, raw)
    assert res["mismatched_files"] == ["AAA_companyfacts.json"]


def test_c1_metadata_tamper_detected_by_expected_manifest_hash(good):
    raw, m = good
    expected = snap.manifest_sha256(m)
    tampered = copy.deepcopy(m)
    tampered["files"][0]["ticker"] = "ZZZ"
    tampered["files"][0]["cik"] = "0000000001"
    # Without an expected hash the files still match (documented limitation) ...
    assert snap.verify_manifest(tampered, raw)["status"] == snap.VERIFY_OK
    # ... with the independently held expected hash it is detected.
    res = _fails(tampered, raw, expect_error="manifest_sha256 does not match", expected_manifest_sha256=expected)
    assert res["expected_manifest_sha256"] == expected != res["actual_manifest_sha256"]


def test_c1_content_hash_mismatch(good):
    raw, m = good
    _fails(m, raw, expect_error="snapshot_content_sha256 does not match",
           expected_snapshot_content_sha256="0" * 64)


def test_c1_raw_dir_missing(tmp_path, good):
    _, m = good
    res = _fails(m, tmp_path / "nope")
    assert res["missing_files"] == ["AAA_companyfacts.json", "BBB_companyfacts.json"]


def test_c1_no_inputs_and_incomplete_are_never_ok(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    assert snap.verify_manifest(snap.build_manifest([]), raw)["status"] == snap.VERIFY_NO_SEC_INPUTS
    inc = snap.build_manifest([], missing_stored_inputs=["AAA"])
    assert snap.verify_manifest(inc, raw)["status"] == snap.VERIFY_NOT_VERIFIABLE


def test_c1_read_error_is_reported_not_skipped(good):
    raw, m = good

    def reader(path):
        raise PermissionError("denied")

    res = snap.verify_manifest_with_reader(m, reader)
    assert res["status"] == snap.VERIFY_FAIL
    assert any("PermissionError" in e for e in res["validation_errors"])


# ------------------------------------------------------------------ C2: run-time self-verification

CT_ROWS = [{
    "ticker": "AAA", "cik": CIK, "company_type": "NON_FINANCIAL", "effective_from": "2026-09-20",
    "effective_to": None, "classification_available_date": "2026-09-20",
    "mapping_source": "SEC_10K_BUSINESS_MODEL_EVIDENCE", "mapping_rationale": "t",
    "reviewed_at_utc": "2026-10-26T00:00:00Z", "decision_ref": "t",
}]
MEMBERS = [{"cik": CIK, "ticker": "AAA", "universe_type": "WATCHLIST", "membership_status": "ACTIVE",
            "membership_source": "LEGACY_SEED", "membership_version": "1", "effective_from": "2026-09-21",
            "effective_to": None, "membership_available_date": "2026-09-21"}]
RUN_AT = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)


def _fake_pipeline(after_load=None):
    def pipeline(**kwargs):
        if after_load:
            after_load()
        return {"stages": {
            "pit_gating": {"status": "OK", "evaluation_date": "2026-11-02",
                           "sec_filed_cutoff_date": "2026-11-01", "sec_pit_policy_version": "SEC_PIT_NEXT_KST_DAY_V1"},
            "normalization": {"normalized": {"metrics": {"revenue": {"status": "OK", "value": 1}}}},
        }}
    return pipeline


def _batch(tmp_path, pipeline, out="out", monkeypatch=None):
    return run_batch(
        evaluation_date="2026-11-02", company_type_rows=copy.deepcopy(CT_ROWS), membership_rows=copy.deepcopy(MEMBERS),
        pipeline_fn=pipeline, fetch_fn=lambda t, c: {"cik": 999003, "facts": {}}, raw_dir=tmp_path / "raw",
        output_dir=tmp_path / out, run_started_at=RUN_AT,
    )


def _sv(summary):
    return summary["reproducibility"]["sec_snapshot"]["self_verification"]


def _codes(summary):
    return [r["code"] for r in summary["operational_review"]["reasons"]]


def test_c2_normal_run_self_verifies_ok(tmp_path):
    s = _batch(tmp_path, _fake_pipeline())
    sv = _sv(s)
    assert sv["verification_status"] == "OK" and sv["manifest_file_status"] == "WRITTEN_AND_REREAD"
    assert sv["expected_manifest_sha256"] == sv["actual_manifest_sha256"] == s["reproducibility"]["sec_snapshot"]["manifest_sha256"]
    assert sv["expected_snapshot_content_sha256"] == sv["actual_snapshot_content_sha256"]
    assert sv["missing_files"] == sv["mismatched_files"] == sv["validation_errors"] == []
    assert s["operational_review"]["status"] == "PASS"
    stored = (tmp_path / "out" / snap.MANIFEST_FILENAME).read_bytes()
    assert snap.sha256_bytes(stored) == sv["expected_manifest_sha256"]


def test_c2_raw_file_changed_during_run_fails(tmp_path):
    def tamper():
        f = tmp_path / "raw" / "AAA_companyfacts.json"
        f.write_bytes(f.read_bytes() + b" ")
    s = _batch(tmp_path, _fake_pipeline(after_load=tamper))
    sv = _sv(s)
    assert sv["verification_status"] == "FAIL" and sv["mismatched_files"] == ["AAA_companyfacts.json"]
    assert s["operational_review"]["status"] == "FAIL" and "SNAPSHOT_VERIFICATION_FAILED" in _codes(s)


def test_c2_raw_file_deleted_during_run_fails(tmp_path):
    s = _batch(tmp_path, _fake_pipeline(after_load=lambda: (tmp_path / "raw" / "AAA_companyfacts.json").unlink()))
    sv = _sv(s)
    assert sv["verification_status"] == "FAIL" and sv["missing_files"] == ["AAA_companyfacts.json"]
    assert s["operational_review"]["status"] == "FAIL"


def test_c2_manifest_file_tampered_on_write_fails(tmp_path, monkeypatch):
    real = snap.write_manifest

    def tampering_write(manifest, output_dir):
        path = real(manifest, output_dir)
        m = json.loads(path.read_bytes())
        m["files"][0]["ticker"] = "ZZZ"
        path.write_bytes(snap.canonical_json_bytes(m))
        return path

    monkeypatch.setattr(snap, "write_manifest", tampering_write)
    s = _batch(tmp_path, _fake_pipeline())
    sv = _sv(s)
    assert sv["verification_status"] == "FAIL"
    assert "stored manifest file bytes do not match the expected manifest_sha256" in sv["validation_errors"]
    assert sv["actual_manifest_sha256"] != sv["expected_manifest_sha256"]
    assert s["operational_review"]["status"] == "FAIL"


def test_c2_integrity_failure_does_not_change_ticker_results(tmp_path):
    ok = _batch(tmp_path / "a", _fake_pipeline())
    bad = _batch(tmp_path / "b", _fake_pipeline(
        after_load=lambda: (tmp_path / "b" / "raw" / "AAA_companyfacts.json").write_bytes(b"{}")))
    strip = lambda recs: [{k: v for k, v in r.items() if k != "raw_input"} for r in recs]  # noqa: E731
    assert strip(ok["records"]) == strip(bad["records"])
    for d in ("a", "b"):
        assert (tmp_path / d / "out" / "AAA.json").exists()
    assert json.loads((tmp_path / "a" / "out" / "AAA.json").read_text()) == json.loads((tmp_path / "b" / "out" / "AAA.json").read_text())
    assert ok["operational_review"]["status"] == "PASS" and bad["operational_review"]["status"] == "FAIL"


def test_c2_no_raw_dir_is_not_verifiable_not_pass(tmp_path):
    s = run_batch(evaluation_date="2026-11-02", company_type_rows=copy.deepcopy(CT_ROWS),
                  membership_rows=copy.deepcopy(MEMBERS), pipeline_fn=_fake_pipeline(),
                  fetch_fn=lambda t, c: {"cik": 999003, "facts": {}}, raw_dir=None, output_dir=tmp_path / "out",
                  run_started_at=RUN_AT)
    assert _sv(s)["verification_status"] == "NOT_VERIFIABLE"
    assert s["operational_review"]["status"] == "REVIEW" and "SNAPSHOT_INCOMPLETE" in _codes(s)


def _cli():
    path = REPO / "scripts" / "run_fundamentals_batch.py"
    spec = importlib.util.spec_from_file_location("run_fundamentals_batch_c2", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("review, errors, code", [
    ("PASS", 0, 0), ("REVIEW", 0, 0), ("PASS", 1, 1), ("FAIL", 0, 3), ("FAIL", 1, 3),
])
def test_c2_cli_exit_codes(monkeypatch, review, errors, code):
    cli = _cli()
    monkeypatch.setattr(cli, "load_reference_rows", lambda: ([], []))
    monkeypatch.setattr(cli, "read_finance_lease_evidence_csv", lambda: [])
    monkeypatch.setattr(cli, "read_metric_eligibility_csv", lambda: [])
    monkeypatch.setattr(cli, "assert_valid_metric_eligibility_reference_data", lambda rows: None)
    monkeypatch.setattr(cli, "run_batch", lambda **k: {
        "evaluation_date": "2026-11-01", "ticker_count": 1, "records": [],
        "status_counts": {"ERROR": errors} if errors else {"OK": 1},
        "operational_review": {"status": review, "reasons": []}})
    assert cli.main(["--no-fetch", "--evaluation-date", "2026-11-01"]) == code
    assert (cli.EXIT_OK, cli.EXIT_TICKER_ERROR, cli.EXIT_FUTURE_DATE_REFUSED, cli.EXIT_OPERATIONAL_FAIL) == (0, 1, 2, 3)


# ------------------------------------------------------------------ C3: offline verifier

def _verifier():
    path = REPO / "scripts" / "verify_sec_snapshot.py"
    spec = importlib.util.spec_from_file_location("verify_sec_snapshot_c3", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _zip_dir(src: Path, dest: Path, *, extra: list[tuple] = (), skip=()) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        for p in sorted(src.iterdir()):
            if p.is_file() and p.name not in skip:
                zf.write(p, p.name)
        for name, data in extra:
            zf.writestr(name, data)
    return dest


@pytest.fixture
def artifacts(tmp_path):
    s = _batch(tmp_path, _fake_pipeline())
    assert s["operational_review"]["status"] == "PASS"
    results = _zip_dir(tmp_path / "out", tmp_path / "results.zip")
    raw = _zip_dir(tmp_path / "raw", tmp_path / "raw.zip")
    return tmp_path, results, raw


def _rezip_results(tmp_path, name, mutate):
    out = tmp_path / "out_mut"
    if out.exists():
        for p in out.iterdir():
            p.unlink()
    out.mkdir(exist_ok=True)
    for p in (tmp_path / "out").iterdir():
        (out / p.name).write_bytes(p.read_bytes())
    mutate(out)
    return _zip_dir(out, tmp_path / name)


def _c3(results, raw):
    v = _verifier()
    return v.verify(str(results), str(raw)), v


def test_c3_pass(artifacts, capsys):
    tmp, results, raw = artifacts
    out, v = _c3(results, raw)
    assert out["result"] == "PASS", out["reasons"]
    assert v.main([str(results), str(raw)]) == 0
    assert "RESULT: PASS" in capsys.readouterr().out


def test_c3_directories_are_accepted(artifacts):
    tmp, _, _ = artifacts
    out, _ = _c3(tmp / "out", tmp / "raw")
    assert out["result"] == "PASS", out["reasons"]


@pytest.mark.parametrize("which", ["results", "raw"])
def test_c3_missing_zip(artifacts, which):
    tmp, results, raw = artifacts
    out, v = _c3(tmp / "nope.zip" if which == "results" else results, raw if which == "results" else tmp / "nope.zip")
    assert out["result"] == "FAIL" and "not found" in out["reasons"][0]
    assert v.main([str(tmp / "nope.zip"), str(raw)]) == 1


def test_c3_not_a_zip(artifacts):
    tmp, results, _ = artifacts
    junk = tmp / "junk.zip"
    junk.write_bytes(b"not a zip")
    out, _ = _c3(results, junk)
    assert out["result"] == "FAIL" and "not a valid ZIP" in out["reasons"][0]


def test_c3_summary_missing(artifacts):
    tmp, _, raw = artifacts
    out, _ = _c3(_rezip_results(tmp, "r.zip", lambda d: (d / "summary.json").unlink()), raw)
    assert out["result"] == "FAIL" and "summary.json missing" in out["reasons"][0]


def test_c3_summary_corrupt(artifacts):
    tmp, _, raw = artifacts
    out, _ = _c3(_rezip_results(tmp, "r.zip", lambda d: (d / "summary.json").write_text("{not json")), raw)
    assert out["result"] == "FAIL" and "not valid JSON" in out["reasons"][0]


def test_c3_manifest_missing(artifacts):
    tmp, _, raw = artifacts
    out, _ = _c3(_rezip_results(tmp, "r.zip", lambda d: (d / snap.MANIFEST_FILENAME).unlink()), raw)
    assert out["result"] == "FAIL" and "missing from the results artifact" in out["reasons"][0]


def test_c3_expected_hash_missing_or_tampered_in_summary(artifacts):
    tmp, _, raw = artifacts

    def drop(d):
        s = json.loads((d / "summary.json").read_text())
        del s["reproducibility"]["sec_snapshot"]["manifest_sha256"]
        (d / "summary.json").write_text(json.dumps(s))

    out, _ = _c3(_rezip_results(tmp, "r1.zip", drop), raw)
    assert out["result"] == "FAIL" and "lacks a valid expected" in out["reasons"][0]

    def alter(d):
        s = json.loads((d / "summary.json").read_text())
        s["reproducibility"]["sec_snapshot"]["manifest_sha256"] = "0" * 64
        (d / "summary.json").write_text(json.dumps(s))

    out, _ = _c3(_rezip_results(tmp, "r2.zip", alter), raw)
    assert out["result"] == "FAIL"
    assert "manifest file SHA-256 does not match summary.json manifest_sha256" in out["reasons"]


def test_c3_manifest_tampered(artifacts):
    tmp, _, raw = artifacts

    def alter(d):
        m = json.loads((d / snap.MANIFEST_FILENAME).read_bytes())
        m["files"][0]["cik"] = "0000000001"
        (d / snap.MANIFEST_FILENAME).write_bytes(snap.canonical_json_bytes(m))

    out, _ = _c3(_rezip_results(tmp, "r.zip", alter), raw)
    assert out["result"] == "FAIL"
    assert "manifest file SHA-256 does not match summary.json manifest_sha256" in out["reasons"]


def test_c3_raw_file_missing(artifacts):
    tmp, results, _ = artifacts
    raw2 = _zip_dir(tmp / "raw", tmp / "raw2.zip", skip=("AAA_companyfacts.json",))
    out, _ = _c3(results, raw2)
    assert out["result"] == "FAIL" and any("raw files missing" in r for r in out["reasons"])


def test_c3_raw_file_one_byte_changed(artifacts):
    tmp, results, _ = artifacts
    data = bytearray((tmp / "raw" / "AAA_companyfacts.json").read_bytes())
    data[0] = ord("[") if data[0] != ord("[") else ord("{")
    raw2 = _zip_dir(tmp / "raw", tmp / "raw2.zip", skip=("AAA_companyfacts.json",),
                    extra=[("AAA_companyfacts.json", bytes(data))])
    out, _ = _c3(results, raw2)
    assert out["result"] == "FAIL" and any("different size/SHA-256" in r for r in out["reasons"])


def test_c3_extra_unlisted_raw_file_is_info_not_failure(artifacts):
    tmp, results, _ = artifacts
    raw2 = _zip_dir(tmp / "raw", tmp / "raw2.zip", extra=[("ZZZ_companyfacts.json", b"{}")])
    out, _ = _c3(results, raw2)
    assert out["result"] == "PASS"
    assert any("ZZZ_companyfacts.json" in i for i in out["info"])


@pytest.mark.parametrize("name", ["../evil.json", "/abs/evil.json", "sub/../../evil.json", "C:/evil.json", "a\\b.json"])
def test_c3_zip_slip_entries_fail(artifacts, name):
    tmp, results, _ = artifacts
    raw2 = _zip_dir(tmp / "raw", tmp / "raw2.zip", extra=[(name, b"{}")])
    out, _ = _c3(results, raw2)
    assert out["result"] == "FAIL" and "unsafe ZIP entry" in out["reasons"][0]


def test_c3_duplicate_zip_entry_fails(artifacts):
    tmp, results, _ = artifacts
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw2 = _zip_dir(tmp / "raw", tmp / "raw2.zip", extra=[("AAA_companyfacts.json", b"{}")])
    out, _ = _c3(results, raw2)
    assert out["result"] == "FAIL" and "duplicate ZIP entry" in out["reasons"][0]


def test_c3_symlink_zip_entry_fails(artifacts):
    tmp, results, _ = artifacts
    dest = tmp / "raw_link.zip"
    with zipfile.ZipFile(dest, "w") as zf:
        for p in sorted((tmp / "raw").iterdir()):
            zf.write(p, p.name)
        info = zipfile.ZipInfo("LINK_companyfacts.json")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    out, _ = _c3(results, dest)
    assert out["result"] == "FAIL" and "symbolic link ZIP entry" in out["reasons"][0]


def test_c3_is_read_only(artifacts):
    tmp, results, raw = artifacts
    before = {p: p.read_bytes() for p in (results, raw)}
    listing = sorted(os.listdir(tmp))
    _c3(results, raw)
    assert {p: p.read_bytes() for p in (results, raw)} == before
    assert sorted(os.listdir(tmp)) == listing  # nothing extracted


def test_c3_limitation_consistent_rewrite_is_not_detected(artifacts):
    """Documented limitation: if raw file, manifest AND summary hashes are all
    rewritten consistently, the internal-consistency check passes. Detecting
    that needs an externally held hash or a signature."""
    tmp, _, _ = artifacts
    new_raw = tmp / "raw_forged"
    new_raw.mkdir()
    (new_raw / "AAA_companyfacts.json").write_text('{"forged": true}', encoding="utf-8")
    m = _manifest_for(new_raw)
    m["files"][0]["source"] = "FETCHED"

    def forge(d):
        (d / snap.MANIFEST_FILENAME).write_bytes(snap.canonical_json_bytes(m))
        s = json.loads((d / "summary.json").read_text())
        s["reproducibility"]["sec_snapshot"]["manifest_sha256"] = snap.manifest_sha256(m)
        s["reproducibility"]["sec_snapshot"]["snapshot_content_sha256"] = snap.snapshot_content_sha256(m)
        (d / "summary.json").write_text(json.dumps(s))

    out, v = _c3(_rezip_results(tmp, "forged.zip", forge), _zip_dir(new_raw, tmp / "forged_raw.zip"))
    assert out["result"] == "PASS"  # limitation, by design
    assert "internal-consistency" in (v.__doc__ or "") and "internal-consistency" in (snap.__doc__ or "")


def test_c3_summary_recording_failed_self_verification_fails(artifacts):
    tmp, _, raw = artifacts

    def mark(d):
        s = json.loads((d / "summary.json").read_text())
        s["reproducibility"]["sec_snapshot"]["self_verification"]["verification_status"] = "FAIL"
        (d / "summary.json").write_text(json.dumps(s))

    out, _ = _c3(_rezip_results(tmp, "r.zip", mark), raw)
    assert out["result"] == "FAIL" and any("self-verification" in r for r in out["reasons"])


def test_c3_usage_error_exit_code():
    assert _verifier().main([]) == 2
