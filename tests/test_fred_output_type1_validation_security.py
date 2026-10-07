"""
Adversarial security tests for .github/scripts/fred_output_type1_validation.py
(Framework v2.1 Step 10J-QA-8, 2026-10-07).

PURPOSE: this file is the committed, repeatable form of the QA-7/QA-8
adversarial secret-redaction testing that was previously done only as an
ad-hoc, uncommitted script. It exists specifically to close the QA-7
finding that `_request()` had no catch-all exception handler, meaning a
generic (not HTTPError/SSLError/URLError/JSONDecodeError/socket.timeout)
exception type would propagate the real FRED_API_KEY unredacted if that
exception's own message happened to contain it.

Scope note: `.github/scripts/fred_output_type1_validation.py` is
validation infrastructure, not production code -- it is never imported
by anything under `src/`, and nothing here tests or touches
`src/macro/fred_provider.py`, `src/macro/pit.py`, `src/macro/schema.py`,
or `src/macro/engine.py` (all of which remain frozen). This file is
collected by the ordinary `pytest tests/` run so the redaction behavior
it checks cannot silently regress without a visible test failure, but it
never makes a real network call and never depends on a real
FRED_API_KEY being set -- it sets its own throwaway synthetic value
(FAKE_SECRET below) for the duration of each test via monkeypatch, and
every assertion is "the synthetic secret is absent", never anything
involving a real key.

Ten required paths (per the QA-8 approval's adversarial-test list):
    1. HTTPError (synthetic key echoed in the error body)
    2. URLError (synthetic key in e.reason)
    3. JSONDecodeError (synthetic key in the malformed response body)
    4. RuntimeError (generic exception type, synthetic key in message)
    5. ValueError (generic exception type, synthetic key in message)
    6. bare Exception (synthetic key in message)
    7. OSError with the synthetic key explicitly labeled "secret="
    8. a chained exception (raise ... from ...) with the synthetic key
       present in BOTH the outer and the inner (__cause__) exception
    9. the normal success path (must be entirely unaffected --
       regression check)
    10. the final JSON summary dump (RESULTS fed through json.dumps,
        the same call main() makes) must also never contain the
        synthetic secret
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import ssl
import sys
import urllib.error
from pathlib import Path

import pytest

FAKE_SECRET = "FAKE_FRED_SECRET_123456789"

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "scripts"
    / "fred_output_type1_validation.py"
)


def _load_module(monkeypatch):
    """Import the validation script fresh, with FRED_API_KEY set to the
    synthetic value for this test only (monkeypatch restores the real
    environment afterward). Loaded via importlib from its actual path
    rather than via a package import, since .github/scripts/ is not a
    package -- mirrors how this script is invoked by the workflow."""
    monkeypatch.setenv("FRED_API_KEY", FAKE_SECRET)
    spec = importlib.util.spec_from_file_location(
        "fred_output_type1_validation_under_test", _SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_secret(obj) -> bool:
    return FAKE_SECRET in json.dumps(obj, default=str)


class _FakeResponse:
    def __init__(self, data: bytes, status: int = 200):
        self._data = data
        self.status = status

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code: int, body: bytes):
        super().__init__("https://api.stlouisfed.org/fred/series/observations", code, "error", {}, None)
        self._body = body

    def read(self):
        return self._body


@pytest.fixture
def validation_module(monkeypatch):
    return _load_module(monkeypatch)


def test_1_http_error_key_in_body_is_redacted(validation_module, monkeypatch):
    err = _FakeHTTPError(400, f'{{"error_message":"Bad Request. api_key={FAKE_SECRET} invalid"}}'.encode())
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(err))
    result = validation_module._request({"series_id": "CPIAUCSL", "output_type": 1})
    assert not _has_secret(result)
    assert "REDACTED" in result["detail"]
    assert result["classification"] == "AUTH_FAILED"


def test_2_url_error_key_in_reason_is_redacted(validation_module, monkeypatch):
    err = urllib.error.URLError(f"connection refused key={FAKE_SECRET}")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(err))
    result = validation_module._request({"series_id": "CPIAUCSL", "output_type": 1})
    assert not _has_secret(result)
    assert "REDACTED" in result["detail"]


def test_3_json_decode_error_key_in_body_is_redacted(validation_module, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: _FakeResponse(f"not json key={FAKE_SECRET}".encode()),
    )
    result = validation_module._request({"series_id": "CPIAUCSL", "output_type": 1})
    assert not _has_secret(result)
    assert result["classification"] == "RESPONSE_PARSE_FAILED"


def test_4_runtime_error_generic_exception_is_redacted(validation_module, monkeypatch):
    err = RuntimeError(f"boom {FAKE_SECRET}")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(err))
    result = validation_module._request({"series_id": "CPIAUCSL", "output_type": 1})
    assert not _has_secret(result)
    assert result["classification"] == "UNEXPECTED_ERROR"
    assert "REDACTED" in result["detail"]


def test_5_value_error_generic_exception_is_redacted(validation_module, monkeypatch):
    err = ValueError(f"bad value {FAKE_SECRET}")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(err))
    result = validation_module._request({"series_id": "CPIAUCSL", "output_type": 1})
    assert not _has_secret(result)
    assert result["classification"] == "UNEXPECTED_ERROR"


def test_6_bare_exception_is_redacted(validation_module, monkeypatch):
    err = Exception(f"generic failure {FAKE_SECRET}")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(err))
    result = validation_module._request({"series_id": "CPIAUCSL", "output_type": 1})
    assert not _has_secret(result)
    assert result["classification"] == "UNEXPECTED_ERROR"


def test_7_secret_explicitly_labeled_in_message_is_redacted(validation_module, monkeypatch):
    err = OSError(f"os failure, secret={FAKE_SECRET}")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(err))
    result = validation_module._request({"series_id": "CPIAUCSL", "output_type": 1})
    assert not _has_secret(result)


def test_8_chained_exception_both_levels_redacted(validation_module, monkeypatch):
    def make_chained():
        try:
            try:
                raise ValueError(f"inner context leak {FAKE_SECRET}")
            except ValueError as inner:
                raise RuntimeError(f"outer with cause {FAKE_SECRET}") from inner
        except RuntimeError as outer:
            return outer

    chained = make_chained()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(chained))
    result = validation_module._request({"series_id": "CPIAUCSL", "output_type": 1})
    assert not _has_secret(result)
    assert result["classification"] == "UNEXPECTED_ERROR"
    # the redaction helper must also be directly clean against the raw
    # chained exception object (not just through _request's return value)
    direct = validation_module._redact_exception_text(chained)
    assert FAKE_SECRET not in direct


def test_9_normal_success_path_unaffected(validation_module, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: _FakeResponse(json.dumps({"observations": []}).encode()),
    )
    result = validation_module._request({"series_id": "CPIAUCSL", "output_type": 1})
    assert result["ok"] is True
    assert not _has_secret(result)
    assert FAKE_SECRET not in result["redacted_url"]


def test_10_final_json_summary_dump_never_contains_secret(validation_module, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: _FakeResponse(json.dumps({"observations": []}).encode()),
    )
    result = validation_module._request({"series_id": "CPIAUCSL", "output_type": 1})
    validation_module.RESULTS["adversarial_probe"] = result
    dumped = json.dumps(validation_module.RESULTS, indent=2, default=str)
    assert FAKE_SECRET not in dumped
