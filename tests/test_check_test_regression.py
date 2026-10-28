"""Tests for .github/scripts/check_test_regression.py (full-suite regression gate)."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "check_test_regression.py"
BASELINE_FILE = ROOT / ".github" / "ci" / "full_suite_baseline.json"

_spec = importlib.util.spec_from_file_location("check_test_regression", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

MARKER = "protected_marker_module"


def _xml(cases):
    """cases: list of (classname, name, status, message)."""
    body = []
    for cls, name, status, msg in cases:
        inner = ""
        if status == "failure":
            inner = f'<failure message="{msg}">x</failure>'
        elif status == "error":
            inner = f'<error message="{msg}">x</error>'
        elif status == "skipped":
            inner = '<skipped message="skip"/>'
        body.append(f'<testcase classname="{cls}" name="{name}">{inner}</testcase>')
    return '<?xml version="1.0"?><testsuites><testsuite name="pytest">' + "".join(body) + "</testsuite></testsuites>"


BASE_CASES = [
    ("tests.test_a", "test_ok1", "passed", ""),
    ("tests.test_a", "test_ok2", "passed", ""),
    ("tests.test_a", "test_skip", "skipped", ""),
    ("tests.test_env", "test_known[AAPL]", "failure", "SEC raw file not found"),
    (f"tests.{MARKER}", "p1", "failure", "hidden"),
    (f"tests.{MARKER}", "p2", "passed", ""),
    (f"tests.{MARKER}", "p3", "passed", ""),
]


def _baseline():
    return {
        "baseline_commit": "base",
        "known_failures": ["tests.test_env::test_known[AAPL]"],
        "protected": {"classname_markers": [MARKER], "tests": 3, "failed": 1},
        "tests": {
            "tests.test_a::test_ok1": "passed",
            "tests.test_a::test_ok2": "passed",
            "tests.test_a::test_skip": "skipped",
            "tests.test_env::test_known[AAPL]": "failure",
        },
    }


def _run(tmp_path, cases, exit_code=1, baseline=None):
    report = tmp_path / "r.xml"
    report.write_text(_xml(cases), encoding="utf-8")
    base = tmp_path / "b.json"
    base.write_text(json.dumps(baseline or _baseline()), encoding="utf-8")
    out = tmp_path / "out.json"
    rc = gate.main(["--report", str(report), "--baseline", str(base),
                    "--pytest-exit-code", str(exit_code), "--json-out", str(out)])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def _replace(cases, cls, name, status, msg=""):
    return [(c, n, status, msg) if (c, n) == (cls, name) else (c, n, s, m) for c, n, s, m in cases]


def test_identical_to_baseline_passes_and_lists_known_failure(tmp_path):
    rc, res = _run(tmp_path, BASE_CASES)
    assert rc == gate.EXIT_PASS and res["verdict"] == "PASS"
    assert res["known_failures_still_failing"] == ["tests.test_env::test_known[AAPL]"]
    assert res["new_failures"] == []


def test_new_failure_is_regression(tmp_path):
    cases = _replace(BASE_CASES, "tests.test_a", "test_ok1", "failure", "boom")
    rc, res = _run(tmp_path, cases)
    assert rc == gate.EXIT_REGRESSION
    assert res["new_failures"] == [{"id": "tests.test_a::test_ok1", "message": "boom"}]


def test_new_error_is_regression(tmp_path):
    cases = _replace(BASE_CASES, "tests.test_a", "test_ok2", "error", "collection")
    rc, res = _run(tmp_path, cases)
    assert rc == gate.EXIT_REGRESSION


def test_added_failing_test_is_regression(tmp_path):
    rc, res = _run(tmp_path, BASE_CASES + [("tests.test_new", "test_x", "failure", "f")])
    assert rc == gate.EXIT_REGRESSION
    assert res["added_tests"] == {"tests.test_new::test_x": "failure"}


def test_added_passing_test_passes(tmp_path):
    rc, res = _run(tmp_path, BASE_CASES + [("tests.test_new", "test_x", "passed", "")])
    assert rc == gate.EXIT_PASS
    assert res["added_tests"] == {"tests.test_new::test_x": "passed"}


def test_missing_baseline_test_is_regression(tmp_path):
    cases = [c for c in BASE_CASES if c[1] != "test_ok2"]
    rc, res = _run(tmp_path, cases)
    assert rc == gate.EXIT_REGRESSION
    assert res["missing_baseline_tests"] == ["tests.test_a::test_ok2"]


def test_missing_known_failure_is_regression(tmp_path):
    # A known failure that disappears is a missing test, not a fix.
    cases = [c for c in BASE_CASES if c[0] != "tests.test_env"]
    rc, res = _run(tmp_path, cases)
    assert rc == gate.EXIT_REGRESSION
    assert res["missing_baseline_tests"] == ["tests.test_env::test_known[AAPL]"]


def test_passed_to_skipped_is_regression(tmp_path):
    cases = _replace(BASE_CASES, "tests.test_a", "test_ok1", "skipped")
    rc, res = _run(tmp_path, cases)
    assert rc == gate.EXIT_REGRESSION
    assert res["newly_skipped"] == ["tests.test_a::test_ok1"]


def test_known_failure_now_passing_is_reported_not_failing(tmp_path):
    cases = _replace(BASE_CASES, "tests.test_env", "test_known[AAPL]", "passed")
    rc, res = _run(tmp_path, cases)
    assert rc == gate.EXIT_PASS
    assert res["known_failures_now_passing"] == ["tests.test_env::test_known[AAPL]"]


def test_protected_failure_count_increase_is_regression(tmp_path):
    cases = _replace(BASE_CASES, f"tests.{MARKER}", "p2", "failure", "x")
    rc, res = _run(tmp_path, cases)
    assert rc == gate.EXIT_REGRESSION
    assert res["protected"]["problems"]


def test_protected_count_drop_is_regression(tmp_path):
    cases = [c for c in BASE_CASES if c[1] != "p3"]
    rc, res = _run(tmp_path, cases)
    assert rc == gate.EXIT_REGRESSION


def test_protected_tests_are_never_listed_by_name(tmp_path):
    cases = _replace(BASE_CASES, f"tests.{MARKER}", "p2", "failure", "secret")
    rc, res = _run(tmp_path, cases)
    listed = json.dumps({k: v for k, v in res.items() if k != "protected"})
    assert MARKER not in listed
    assert "secret" not in json.dumps(res) and "hidden" not in json.dumps(res)
    assert set(res["protected"]["current"]) == {"tests", "failed", "passed", "skipped"}


@pytest.mark.parametrize("code", [2, 3, 4, 5])
def test_bad_pytest_exit_code_is_unverifiable(tmp_path, code):
    rc, res = _run(tmp_path, BASE_CASES, exit_code=code)
    assert rc == gate.EXIT_UNVERIFIABLE and res["verdict"] == "UNVERIFIABLE"


def test_empty_report_is_unverifiable(tmp_path):
    rc, res = _run(tmp_path, [], exit_code=1)
    assert rc == gate.EXIT_UNVERIFIABLE


def test_missing_report_is_unverifiable(tmp_path):
    base = tmp_path / "b.json"
    base.write_text(json.dumps(_baseline()), encoding="utf-8")
    out = tmp_path / "o.json"
    rc = gate.main(["--report", str(tmp_path / "nope.xml"), "--baseline", str(base),
                    "--json-out", str(out)])
    assert rc == gate.EXIT_UNVERIFIABLE
    assert json.loads(out.read_text())["verdict"] == "UNVERIFIABLE"


def test_malformed_report_is_unverifiable(tmp_path):
    report = tmp_path / "r.xml"
    report.write_text("<testsuites><testsuite>", encoding="utf-8")
    base = tmp_path / "b.json"
    base.write_text(json.dumps(_baseline()), encoding="utf-8")
    assert gate.main(["--report", str(report), "--baseline", str(base)]) == gate.EXIT_UNVERIFIABLE


def test_summary_states_known_failures_are_not_passing(tmp_path):
    _, res = _run(tmp_path, BASE_CASES)
    text = gate.render_summary(res)
    assert "PASS" in text and "not counted as passing" in text
    assert "tests.test_env::test_known[AAPL]" in text


def test_committed_baseline_is_consistent():
    b = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    known = set(b["known_failures"])
    assert known, "baseline must list its known failures explicitly"
    assert all(b["tests"].get(t) in ("failure", "error") for t in known)
    failing = {t for t, s in b["tests"].items() if s in ("failure", "error")}
    assert failing == known
    assert b["protected"]["tests"] >= b["protected"]["failed"] >= 0
    markers = b["protected"]["classname_markers"]
    assert not any(any(m in t for m in markers) for t in b["tests"])
