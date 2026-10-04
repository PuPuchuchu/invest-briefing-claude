"""
Step 10G -- FRED / ALFRED live connectivity smoke test (GitHub-hosted
runner only).

PURPOSE: answer exactly one question -- can this execution environment
reach api.stlouisfed.org, authenticate with a real FRED_API_KEY, and
retrieve a current observation, a multi-series set, a real-time-period
query, and a vintage-dates query. Nothing here is a FRED provider
module, touches src/macro/*, writes any dataset/CSV, computes any macro
state/regime, or runs on a schedule (the workflow that calls this script
is workflow_dispatch-only). This script is intentionally NOT imported by
anything under src/ and lives outside the package so it is obviously
disconnected from production code.

SECURITY: FRED_API_KEY is read from the environment (injected by the
calling workflow from secrets.FRED_API_KEY) and is NEVER printed, logged,
or included in any URL that is itself printed. Every URL this script
prints has its api_key query-string value replaced with "REDACTED"
before printing. This script never dumps os.environ, never uses a
debug/trace mode that would echo the key, and never shells out to curl
(so there is no risk of the key appearing in an echoed shell command
line) -- all HTTP calls use Python's standard library (urllib), run
directly in-process.

Only the standard library is used (urllib, json, os, re, sys, datetime,
ssl, socket) -- no new dependency is installed for this smoke test, per
Step 10G's "don't install unnecessary dependencies" scope limit.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Series IDs confirmed from this repository's own current implementation
# this round (src/macro/*.py), not from memory -- see the Step 10G
# report's "Series Retrieval Matrix" section for the exact source line
# each one was read from.
SERIES_INFLATION = ["CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE"]
SERIES_POLICY = ["DFEDTARU", "DFEDTARL"]
SERIES_CURVE = "T10Y2Y"
SERIES_RISK = "VIXCLS"
SERIES_GROWTH = ["PAYEMS", "UNRATE"]

LAYER3_SERIES = [SERIES_CURVE, SERIES_RISK, "CPIAUCSL"]
LAYER4_SERIES = [
    (SERIES_CURVE, "daily"),
    (SERIES_RISK, "daily"),
    ("CPIAUCSL", "monthly"),
    (SERIES_POLICY[0], "daily (7-day step function)"),
]
LAYER5_SERIES = "CPIAUCSL"
LAYER5_REALTIME_START = "2015-06-01"
LAYER5_REALTIME_END = "2015-06-01"
LAYER6_SERIES = "CPIAUCSL"
LAYER6_VINTAGE_DATE = "2015-06-15"

RESULTS: dict[str, str] = {}
MISSING_VALUE_MARKERS_OBSERVED: set[str] = set()
FAILURE_CODES: list[str] = []


def _redact(url: str) -> str:
    """Replace the api_key query-string value with REDACTED before this
    URL is ever printed anywhere. Applied unconditionally to every URL
    this script logs -- never relies on remembering to redact at each
    call site."""
    return re.sub(r"(api_key=)[^&]+", r"\1REDACTED", url)


def _build_url(params: dict) -> str:
    api_key = os.environ.get("FRED_API_KEY", "")
    query = dict(params)
    query["api_key"] = api_key
    query["file_type"] = "json"
    return f"{FRED_API_BASE}?{urllib.parse.urlencode(query)}"


def _request(url: str, timeout: int = 20):
    """Returns (status_code, parsed_json_or_None, error_classification_or_None, detail).
    Never raises -- every failure mode is caught and classified. Never
    includes the raw URL (with api_key) in any returned detail string;
    callers must use _redact(url) if they want to log the URL at all."""
    req = urllib.request.Request(url, headers={"User-Agent": "invest-briefing-claude-step10g-smoke-test/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        if status == 429:
            return status, None, "RATE_LIMITED", body[:300]
        if status == 400:
            # FRED returns 400 for bad api_key, bad series_id, and bad
            # parameters alike -- disambiguate by message content.
            lowered = body.lower()
            if "api_key" in lowered or "api key" in lowered:
                return status, None, "AUTH_FAILED", body[:300]
            if "series does not exist" in lowered or "series_id" in lowered and "not" in lowered:
                return status, None, "SERIES_NOT_FOUND", body[:300]
            return status, None, "INVALID_PARAMETER", body[:300]
        return status, None, "HTTP_FAILURE", body[:300]
    except ssl.SSLError as e:
        return None, None, "TLS_FAILURE", str(e)[:300]
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, socket.gaierror):
            return None, None, "DNS_FAILURE", str(reason)[:300]
        return None, None, "NETWORK_FAILURE", str(reason)[:300]
    except socket.timeout:
        return None, None, "NETWORK_FAILURE", "socket timeout"
    except Exception as e:  # pragma: no cover -- genuinely unexpected
        return None, None, "UNKNOWN", f"{type(e).__name__}: {e}"[:300]

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        return status, None, "RESPONSE_PARSE_FAILED", str(e)[:300]

    return status, parsed, None, None


def _scan_missing_values(parsed: dict) -> None:
    for row in parsed.get("observations", []):
        v = row.get("value")
        if v is not None and not re.match(r"^-?\d+(\.\d+)?$", str(v)):
            MISSING_VALUE_MARKERS_OBSERVED.add(str(v))


def layer1_network() -> None:
    """Real HTTPS request (not ping) against the FRED API host, with NO
    api_key, purely to confirm DNS/TLS/HTTP-level reachability -- Layer 2
    tests authentication separately with the real key."""
    url = f"{FRED_API_BASE}?series_id={SERIES_CURVE}&file_type=json"
    req = urllib.request.Request(url, headers={"User-Agent": "invest-briefing-claude-step10g-smoke-test/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code  # a real HTTP error response IS a successful network layer -- host answered
    except ssl.SSLError as e:
        RESULTS["LAYER1_NETWORK"] = "FAIL"
        FAILURE_CODES.append("TLS_FAILURE")
        print(f"LAYER1_NETWORK: FAIL (TLS_FAILURE) detail={str(e)[:200]}")
        return
    except urllib.error.URLError as e:
        reason = e.reason
        code = "DNS_FAILURE" if isinstance(reason, socket.gaierror) else "NETWORK_FAILURE"
        RESULTS["LAYER1_NETWORK"] = "FAIL"
        FAILURE_CODES.append(code)
        print(f"LAYER1_NETWORK: FAIL ({code}) detail={str(reason)[:200]}")
        return
    except Exception as e:
        RESULTS["LAYER1_NETWORK"] = "FAIL"
        FAILURE_CODES.append("UNKNOWN")
        print(f"LAYER1_NETWORK: FAIL (UNKNOWN) detail={type(e).__name__}: {e}")
        return

    # Any real HTTP status code (200, 400, etc.) means DNS resolved, TLS
    # completed, and the FRED host answered over HTTP -- that is exactly
    # what Layer 1 is checking.
    RESULTS["LAYER1_NETWORK"] = "PASS"
    print(f"LAYER1_NETWORK: PASS (received HTTP {status} from {_redact(url)})")


def layer2_authentication() -> None:
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        RESULTS["LAYER2_AUTH"] = "NOT_CONFIGURED"
        print("LAYER2_AUTH: NOT_CONFIGURED (FRED_API_KEY not present in environment)")
        return

    url = _build_url({"series_id": SERIES_CURVE})
    status, parsed, err, detail = _request(url)
    if err == "AUTH_FAILED":
        RESULTS["LAYER2_AUTH"] = "FAIL"
        FAILURE_CODES.append("AUTH_FAILED")
        print(f"LAYER2_AUTH: FAIL (AUTH_FAILED) http_status={status}")
        return
    if err is not None:
        RESULTS["LAYER2_AUTH"] = "FAIL"
        FAILURE_CODES.append(err)
        print(f"LAYER2_AUTH: FAIL ({err}) http_status={status}")
        return
    if status == 200 and parsed is not None and "observations" in parsed:
        RESULTS["LAYER2_AUTH"] = "PASS"
        print(f"LAYER2_AUTH: PASS (HTTP 200, observations array present, count={len(parsed['observations'])})")
        _scan_missing_values(parsed)
        return

    RESULTS["LAYER2_AUTH"] = "FAIL"
    FAILURE_CODES.append("RESPONSE_PARSE_FAILED")
    print(f"LAYER2_AUTH: FAIL (RESPONSE_PARSE_FAILED) http_status={status}")


def layer3_current_observation() -> None:
    if RESULTS.get("LAYER2_AUTH") != "PASS":
        RESULTS["LAYER3_CURRENT_OBS"] = "FAIL"
        print("LAYER3_CURRENT_OBS: FAIL (skipped -- Layer 2 authentication did not PASS)")
        return

    all_ok = True
    for sid in LAYER3_SERIES:
        url = _build_url({"series_id": sid})
        status, parsed, err, detail = _request(url)
        if err is not None or status != 200 or parsed is None:
            all_ok = False
            FAILURE_CODES.append(err or "HTTP_FAILURE")
            print(f"  {sid}: FAIL (http_status={status}, error={err})")
            continue

        obs = parsed.get("observations", [])
        if not obs:
            all_ok = False
            print(f"  {sid}: FAIL (HTTP 200 but no observations in payload)")
            continue

        latest = obs[-1]
        date_ok = bool(re.match(r"^\d{4}-\d{2}-\d{2}$", str(latest.get("date", ""))))
        value_raw = str(latest.get("value", ""))
        numeric_ok = bool(re.match(r"^-?\d+(\.\d+)?$", value_raw))
        if not numeric_ok:
            MISSING_VALUE_MARKERS_OBSERVED.add(value_raw)
        _scan_missing_values(parsed)

        print(
            f"  {sid}: PASS (HTTP 200, {len(obs)} observations, "
            f"latest date={latest.get('date')!r} date_parseable={date_ok}, "
            f"latest value={'<non-numeric marker>' if not numeric_ok else value_raw!r} numeric_parseable={numeric_ok})"
        )
        if not date_ok:
            all_ok = False

    RESULTS["LAYER3_CURRENT_OBS"] = "PASS" if all_ok else "FAIL"
    print(f"LAYER3_CURRENT_OBS: {RESULTS['LAYER3_CURRENT_OBS']}")


def layer4_multi_series() -> None:
    if RESULTS.get("LAYER2_AUTH") != "PASS":
        RESULTS["LAYER4_MULTISERIES"] = "FAIL"
        print("LAYER4_MULTISERIES: FAIL (skipped -- Layer 2 authentication did not PASS)")
        return

    all_ok = True
    for sid, freq in LAYER4_SERIES:
        url = _build_url({"series_id": sid})
        status, parsed, err, detail = _request(url)
        ok = err is None and status == 200 and parsed is not None and parsed.get("observations")
        if ok:
            _scan_missing_values(parsed)
        all_ok = all_ok and bool(ok)
        print(f"  {sid} ({freq}): {'PASS' if ok else 'FAIL'} (http_status={status}, error={err})")

    RESULTS["LAYER4_MULTISERIES"] = "PASS" if all_ok else "FAIL"
    print(f"LAYER4_MULTISERIES: {RESULTS['LAYER4_MULTISERIES']}")


def layer5_realtime_query() -> None:
    if RESULTS.get("LAYER2_AUTH") != "PASS":
        RESULTS["LAYER5_REALTIME"] = "UNKNOWN"
        print("LAYER5_REALTIME: UNKNOWN (skipped -- Layer 2 authentication did not PASS)")
        return

    url = _build_url(
        {
            "series_id": LAYER5_SERIES,
            "realtime_start": LAYER5_REALTIME_START,
            "realtime_end": LAYER5_REALTIME_END,
        }
    )
    status, parsed, err, detail = _request(url)
    if err is not None:
        RESULTS["LAYER5_REALTIME"] = "FAIL"
        FAILURE_CODES.append("PIT_QUERY_FAILED")
        print(f"LAYER5_REALTIME: FAIL (PIT_QUERY_FAILED, underlying={err}) http_status={status} detail={detail}")
        return
    if status == 200 and parsed is not None and "observations" in parsed:
        _scan_missing_values(parsed)
        echoed_start = parsed.get("realtime_start")
        echoed_end = parsed.get("realtime_end")
        print(
            f"LAYER5_REALTIME: PASS (HTTP 200, requested realtime_start={LAYER5_REALTIME_START} "
            f"realtime_end={LAYER5_REALTIME_END}, response echoed realtime_start={echoed_start} "
            f"realtime_end={echoed_end}, observation count={len(parsed['observations'])})"
        )
        RESULTS["LAYER5_REALTIME"] = "PASS"
        return

    RESULTS["LAYER5_REALTIME"] = "FAIL"
    FAILURE_CODES.append("PIT_QUERY_FAILED")
    print(f"LAYER5_REALTIME: FAIL (PIT_QUERY_FAILED) http_status={status}")


def layer6_vintage_query() -> None:
    if RESULTS.get("LAYER2_AUTH") != "PASS":
        RESULTS["LAYER6_VINTAGE"] = "UNKNOWN"
        print("LAYER6_VINTAGE: UNKNOWN (skipped -- Layer 2 authentication did not PASS)")
        return

    url = _build_url({"series_id": LAYER6_SERIES, "vintage_dates": LAYER6_VINTAGE_DATE})
    status, parsed, err, detail = _request(url)
    if err is not None:
        RESULTS["LAYER6_VINTAGE"] = "FAIL"
        FAILURE_CODES.append("VINTAGE_QUERY_FAILED")
        print(f"LAYER6_VINTAGE: FAIL (VINTAGE_QUERY_FAILED, underlying={err}) http_status={status} detail={detail}")
        return
    if status == 200 and parsed is not None and "observations" in parsed:
        _scan_missing_values(parsed)
        print(
            f"LAYER6_VINTAGE: PASS (HTTP 200, requested vintage_dates={LAYER6_VINTAGE_DATE}, "
            f"observation count={len(parsed['observations'])})"
        )
        RESULTS["LAYER6_VINTAGE"] = "PASS"
        return

    RESULTS["LAYER6_VINTAGE"] = "FAIL"
    FAILURE_CODES.append("VINTAGE_QUERY_FAILED")
    print(f"LAYER6_VINTAGE: FAIL (VINTAGE_QUERY_FAILED) http_status={status}")


def main() -> int:
    print("=== Step 10G FRED Connectivity Smoke Test ===")
    print("(FRED_API_KEY is never printed. Every logged URL has its api_key value redacted.)")
    print(f"FRED_API_KEY: {'CONFIGURED' if os.environ.get('FRED_API_KEY') else 'NOT_CONFIGURED'}")
    print()

    layer1_network()
    layer2_authentication()
    layer3_current_observation()
    layer4_multi_series()
    layer5_realtime_query()
    layer6_vintage_query()

    print()
    print("=== SUMMARY ===")
    for key in [
        "LAYER1_NETWORK",
        "LAYER2_AUTH",
        "LAYER3_CURRENT_OBS",
        "LAYER4_MULTISERIES",
        "LAYER5_REALTIME",
        "LAYER6_VINTAGE",
    ]:
        print(f"{key}: {RESULTS.get(key, 'UNKNOWN')}")

    print(f"MISSING_VALUE_MARKERS_OBSERVED: {sorted(MISSING_VALUE_MARKERS_OBSERVED) or 'NONE_OBSERVED'}")
    print(f"FAILURE_CODES: {FAILURE_CODES or 'NONE'}")
    print("SECRET_EXPOSURE: NONE (key never printed; all logged URLs redacted)")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## Step 10G FRED Connectivity Smoke Test\n\n")
                f.write("| Layer | Result |\n|---|---|\n")
                for key in [
                    "LAYER1_NETWORK",
                    "LAYER2_AUTH",
                    "LAYER3_CURRENT_OBS",
                    "LAYER4_MULTISERIES",
                    "LAYER5_REALTIME",
                    "LAYER6_VINTAGE",
                ]:
                    f.write(f"| {key} | {RESULTS.get(key, 'UNKNOWN')} |\n")
                f.write(f"\nMissing-value markers observed: `{sorted(MISSING_VALUE_MARKERS_OBSERVED) or 'NONE_OBSERVED'}`\n\n")
                f.write(f"Failure codes: `{FAILURE_CODES or 'NONE'}`\n\n")
                f.write("Secret exposure: NONE\n")
        except OSError:
            pass

    # Non-zero exit if network or auth hard-failed (the layers everything
    # else depends on); downstream UNKNOWN/FAIL states are still reported
    # above either way and do not themselves fail the job, since partial
    # results are still useful smoke-test information.
    if RESULTS.get("LAYER1_NETWORK") != "PASS" or RESULTS.get("LAYER2_AUTH") not in ("PASS",):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
