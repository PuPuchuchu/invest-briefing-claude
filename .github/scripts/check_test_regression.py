"""Full-suite regression gate (DEC-025 merge criterion, automated).

Compares a pytest JUnit XML report against a committed baseline and fails
when the candidate introduces a regression. It does NOT require every test to
pass: the baseline's known failures are allowed to keep failing, but they are
reported, never counted as passed.

The gate fails (exit 1) when any of these holds:
  * a non-protected test fails or errors and is not a baseline known failure;
  * a non-protected test that exists in the baseline is missing from the report;
  * a baseline known failure still fails but with a different failure
    signature (status, exception type, or normalized message) -- i.e. the
    same test now fails for a different reason;
  * a non-protected test that passed or failed in the baseline is now skipped;
  * the protected-file test count drops below the baseline count, or its
    failure+error count rises above the baseline count (counts only -- the
    protected tests' names and causes are never inspected).

It fails closed (exit 2) when the baseline has no failure signatures (schema
version below 2), when the report is missing or unparseable, contains
no test cases, or pytest exited with a code other than 0 (all passed) or 1
(some tests failed).

Reported but not failing:
  * known failures that now pass (baseline can be tightened in a reviewed PR);
  * new tests (all statuses listed; a new failing test is a regression).

Usage:
  python .github/scripts/check_test_regression.py \
      --report pytest-report.xml --baseline .github/ci/full_suite_baseline.json \
      [--pytest-exit-code N] [--summary-out PATH] [--json-out PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET

EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_UNVERIFIABLE = 2

ALLOWED_PYTEST_EXIT_CODES = {0, 1}
MIN_BASELINE_SCHEMA = 2

_ABS_WORKSPACE = re.compile(r"(?:/home/runner/work|/github/workspace|/tmp)/[^\s:'\"]*?/(?=(?:data|tests|src|\.github)/)")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
_LOC_TAIL = re.compile(r":\d+: (\S+)\s*$")


def make_test_id(classname: str, name: str) -> str:
    return f"{classname}::{name}"


def _status(testcase: ET.Element) -> str:
    for tag, status in (("error", "error"), ("failure", "failure"), ("skipped", "skipped")):
        if testcase.find(tag) is not None:
            return status
    return "passed"


def _first_line(testcase: ET.Element) -> str:
    for tag in ("error", "failure"):
        el = testcase.find(tag)
        if el is not None:
            msg = el.get("message") or (el.text or "")
            return msg.strip().splitlines()[0][:300] if msg.strip() else ""
    return ""


def normalize_message(msg: str) -> str:
    """Make a failure message comparable across runs.

    Collapses whitespace, strips CI workspace path prefixes (so a relative
    repo path remains) and memory addresses. Everything else, including
    numbers and file names, is kept: a different value is a different cause.
    """
    msg = _ABS_WORKSPACE.sub("", msg or "")
    msg = _ADDRESS.sub("0x?", msg)
    return " ".join(msg.split())[:300]


def failure_signature(testcase: ET.Element) -> dict | None:
    """Return {'status', 'exception', 'message'} for a failing test, else None.

    'exception' comes from the last line of the traceback text
    ("path:LINE: ExcType"); the line number is deliberately dropped so that an
    unrelated edit that shifts lines is not reported as a cause change.
    """
    for tag in ("error", "failure"):
        el = testcase.find(tag)
        if el is None:
            continue
        text_lines = [ln for ln in (el.text or "").strip().splitlines() if ln.strip()]
        exc = ""
        if text_lines:
            m = _LOC_TAIL.search(text_lines[-1])
            if m:
                exc = m.group(1)
        raw = el.get("message") or (text_lines[0] if text_lines else "")
        first = raw.strip().splitlines()[0] if raw.strip() else ""
        return {"status": tag, "exception": exc, "message": normalize_message(first)}
    return None


def is_protected(classname: str, markers: list[str]) -> bool:
    return any(m in classname for m in markers)


def parse_report(path: str, markers: list[str]) -> dict:
    """Return {'tests': {id: status}, 'messages': {id: msg}, 'protected': {...}}."""
    root = ET.parse(path).getroot()
    tests: dict[str, str] = {}
    messages: dict[str, str] = {}
    signatures: dict[str, dict] = {}
    protected = {"tests": 0, "failed": 0, "passed": 0, "skipped": 0}
    duplicates: list[str] = []
    for tc in root.iter("testcase"):
        classname = tc.get("classname", "") or ""
        status = _status(tc)
        if is_protected(classname, markers):
            protected["tests"] += 1
            if status in ("failure", "error"):
                protected["failed"] += 1
            elif status == "skipped":
                protected["skipped"] += 1
            else:
                protected["passed"] += 1
            continue
        tid = make_test_id(classname, tc.get("name", "") or "")
        if tid in tests:
            duplicates.append(tid)
        tests[tid] = status
        if status in ("failure", "error"):
            messages[tid] = _first_line(tc)
            signatures[tid] = failure_signature(tc)
    return {"tests": tests, "messages": messages, "signatures": signatures,
            "protected": protected, "duplicates": duplicates}


def evaluate(report: dict, baseline: dict, pytest_exit_code: int | None = None) -> dict:
    problems: list[str] = []
    if pytest_exit_code is not None and pytest_exit_code not in ALLOWED_PYTEST_EXIT_CODES:
        problems.append(f"pytest exit code {pytest_exit_code} (only 0 or 1 can be evaluated)")
    total = len(report["tests"]) + report["protected"]["tests"]
    if total == 0:
        problems.append("report contains no test cases")
    if report["duplicates"]:
        problems.append(f"{len(report['duplicates'])} duplicate test id(s) in report")

    base_tests: dict[str, str] = baseline["tests"]
    known_sigs = baseline.get("known_failures")
    if baseline.get("schema_version", 1) < MIN_BASELINE_SCHEMA or not isinstance(known_sigs, dict):
        problems.append("baseline has no failure signatures (schema_version < 2); "
                        "known-failure causes cannot be compared")
        known_sigs = {t: None for t in (known_sigs or [])}
    known = set(known_sigs)
    cur = report["tests"]

    failing = {t for t, s in cur.items() if s in ("failure", "error")}
    new_failures = sorted(failing - known)
    known_still_failing = sorted(failing & known)
    fixed = sorted(t for t in known if cur.get(t) == "passed")
    cause_changed = []
    for t in known_still_failing:
        expected = known_sigs.get(t)
        actual = report.get("signatures", {}).get(t)
        if expected is not None and actual != expected:
            cause_changed.append({"id": t, "baseline": expected, "current": actual})
    missing = sorted(set(base_tests) - set(cur))
    newly_skipped = sorted(
        t for t, s in base_tests.items()
        if s in ("passed", "failure", "error") and cur.get(t) == "skipped"
    )
    added = sorted(set(cur) - set(base_tests))

    bp = baseline["protected"]
    cp = report["protected"]
    protected_problems = []
    if cp["tests"] < bp["tests"]:
        protected_problems.append(
            f"protected test count fell from {bp['tests']} to {cp['tests']}"
        )
    if cp["failed"] > bp["failed"]:
        protected_problems.append(
            f"protected failure count rose from {bp['failed']} to {cp['failed']}"
        )

    if problems:
        verdict = "UNVERIFIABLE"
    elif new_failures or cause_changed or missing or newly_skipped or protected_problems:
        verdict = "REGRESSION"
    else:
        verdict = "PASS"

    return {
        "verdict": verdict,
        "baseline_commit": baseline.get("baseline_commit"),
        "unverifiable_reasons": problems,
        "new_failures": [{"id": t, "message": report["messages"].get(t, "")} for t in new_failures],
        "known_failures_cause_changed": cause_changed,
        "missing_baseline_tests": missing,
        "newly_skipped": newly_skipped,
        "protected": {"baseline": bp, "current": cp, "problems": protected_problems},
        "known_failures_still_failing": known_still_failing,
        "known_failures_now_passing": fixed,
        "added_tests": {t: cur[t] for t in added},
        "counts": {
            "non_protected_tests": len(cur),
            "non_protected_failing": len(failing),
            "protected_tests": cp["tests"],
        },
    }


def render_summary(result: dict) -> str:
    v = result["verdict"]
    lines = [f"## Full-suite regression gate: **{v}**", ""]
    lines.append(f"Baseline commit: `{result['baseline_commit']}`")
    lines.append("")
    lines.append(
        "Known failures are compared by test id and by failure signature "
        "(status, exception type, normalized message). Protected tests are compared "
        "by counts only; their causes are not compared."
    )
    lines.append("")
    lines.append(
        "This gate passes when there is no regression against the baseline. "
        "It does not mean every test passed: known failures are listed below and are "
        "not counted as passing."
    )
    lines.append("")
    if result["unverifiable_reasons"]:
        lines.append("### Unverifiable (fail closed)")
        lines += [f"- {r}" for r in result["unverifiable_reasons"]]
        lines.append("")
    c = result["counts"]
    p = result["protected"]
    lines += [
        "| Item | Count |",
        "|---|---|",
        f"| Non-protected tests | {c['non_protected_tests']} |",
        f"| New failures | {len(result['new_failures'])} |",
        f"| Known failures with changed cause | {len(result['known_failures_cause_changed'])} |",
        f"| Baseline tests missing | {len(result['missing_baseline_tests'])} |",
        f"| Ran in baseline → skipped | {len(result['newly_skipped'])} |",
        f"| Known failures still failing | {len(result['known_failures_still_failing'])} |",
        f"| Known failures now passing | {len(result['known_failures_now_passing'])} |",
        f"| Added tests | {len(result['added_tests'])} |",
        f"| Protected tests (counts only) | {p['current']['tests']} run, "
        f"{p['current']['failed']} failed (baseline {p['baseline']['tests']} / {p['baseline']['failed']}) |",
        "",
    ]
    sections = [
        ("New failures", [f"`{x['id']}` — {x['message']}" for x in result["new_failures"]]),
        ("Known failures failing for a different reason",
         [f"`{x['id']}` — baseline {x['baseline']} → now {x['current']}"
          for x in result["known_failures_cause_changed"]]),
        ("Baseline tests missing", [f"`{t}`" for t in result["missing_baseline_tests"]]),
        ("Ran in baseline, now skipped", [f"`{t}`" for t in result["newly_skipped"]]),
        ("Protected-count problems", p["problems"]),
        ("Known failures still failing", [f"`{t}`" for t in result["known_failures_still_failing"]]),
        ("Known failures now passing (baseline can be tightened)",
         [f"`{t}`" for t in result["known_failures_now_passing"]]),
    ]
    for title, items in sections:
        if items:
            lines.append(f"### {title}")
            lines += [f"- {i}" for i in items]
            lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--pytest-exit-code", type=int, default=None)
    ap.add_argument("--summary-out", default=None)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    with open(args.baseline, encoding="utf-8") as f:
        baseline = json.load(f)
    markers = baseline["protected"]["classname_markers"]
    try:
        report = parse_report(args.report, markers)
    except (OSError, ET.ParseError) as exc:
        result = {
            "verdict": "UNVERIFIABLE",
            "baseline_commit": baseline.get("baseline_commit"),
            "unverifiable_reasons": [f"cannot read report: {exc}"],
        }
        print(json.dumps(result, indent=2))
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        if args.summary_out:
            with open(args.summary_out, "a", encoding="utf-8") as f:
                f.write(f"## Full-suite regression gate: **UNVERIFIABLE**\n\n- {result['unverifiable_reasons'][0]}\n")
        return EXIT_UNVERIFIABLE

    result = evaluate(report, baseline, args.pytest_exit_code)
    summary = render_summary(result)
    print(summary)
    if args.summary_out:
        with open(args.summary_out, "a", encoding="utf-8") as f:
            f.write(summary)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=True)
    return {"PASS": EXIT_PASS, "REGRESSION": EXIT_REGRESSION}.get(result["verdict"], EXIT_UNVERIFIABLE)


if __name__ == "__main__":
    sys.exit(main())
