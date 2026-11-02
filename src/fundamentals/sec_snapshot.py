"""
SEC input snapshot manifest (STEP 23 SEC PIT, 2026-11-02).

A batch's SEC snapshot is defined as the companyfacts JSON files that were
stored in --raw-dir AND actually loaded as pipeline input -- not the SEC
HTTP response bytes (the fetcher parses and discards those) and not every
file that happens to sit in the raw directory.

The manifest records, per input file: its path relative to the raw
directory (POSIX), ticker, CIK, how it was obtained (CACHE / FETCHED),
its size and the SHA-256 of its bytes. Two hashes are derived:

- snapshot_content_sha256: over (path, size_bytes, sha256) only. Identifies
  the input bytes; identical for a live run and a later run over the same
  stored files, whatever their source.
- manifest_sha256: over the whole manifest (including ticker, CIK and
  source).

Hashes identify data; they do not preserve it. The files themselves are
preserved separately (the sec-companyfacts-raw workflow artifact).

Standard library only, no network, no repository code imports.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

MANIFEST_VERSION = "SEC_SNAPSHOT_MANIFEST_V1"
MANIFEST_FILENAME = "sec_snapshot_manifest.json"

SOURCE_CACHE = "CACHE"
SOURCE_FETCHED = "FETCHED"

SNAPSHOT_OK = "OK"
SNAPSHOT_NO_SEC_INPUTS = "NO_SEC_INPUTS"
SNAPSHOT_INCOMPLETE = "INCOMPLETE"  # an input was used without a stored file

VERIFY_OK = "OK"
VERIFY_FAIL = "FAIL"
VERIFY_NO_SEC_INPUTS = "NO_SEC_INPUTS"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(obj: Any) -> bytes:
    """Deterministic serialization used for every manifest hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def relative_posix(path: Path, raw_dir: Path) -> str:
    return Path(path).resolve().relative_to(Path(raw_dir).resolve()).as_posix()


def load_input_file(path: Path, raw_dir: Path) -> tuple[dict, dict]:
    """Read the stored input ONCE: hash those bytes and parse those same
    bytes, so the manifest entry and the pipeline input cannot diverge.
    Returns (parsed_json, file_entry_without_source)."""
    path = Path(path)
    data = path.read_bytes()
    parsed = json.loads(data.decode("utf-8"))
    entry = {
        "path": relative_posix(path, raw_dir),
        "size_bytes": len(data),
        "sha256": sha256_bytes(data),
    }
    return parsed, entry


def build_manifest(entries: Iterable[dict], *, missing_stored_inputs: Iterable[str] = ()) -> dict:
    """entries: dicts with path, ticker, cik, source, size_bytes, sha256."""
    files = sorted(
        (
            {
                "path": e["path"],
                "ticker": e["ticker"],
                "cik": e["cik"],
                "source": e["source"],
                "size_bytes": e["size_bytes"],
                "sha256": e["sha256"],
            }
            for e in entries
        ),
        key=lambda e: e["path"],
    )
    paths = [f["path"] for f in files]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate input path in SEC snapshot manifest.")
    missing = sorted(set(missing_stored_inputs))
    if missing:
        status = SNAPSHOT_INCOMPLETE
    elif not files:
        status = SNAPSHOT_NO_SEC_INPUTS
    else:
        status = SNAPSHOT_OK
    return {
        "manifest_version": MANIFEST_VERSION,
        "status": status,
        "file_count": len(files),
        "files": files,
        "inputs_without_stored_file": missing,
    }


def snapshot_content_sha256(manifest: dict) -> str:
    content = [
        {"path": f["path"], "size_bytes": f["size_bytes"], "sha256": f["sha256"]}
        for f in sorted(manifest.get("files") or [], key=lambda f: f["path"])
    ]
    return sha256_bytes(canonical_json_bytes(content))


def manifest_sha256(manifest: dict) -> str:
    return sha256_bytes(canonical_json_bytes(manifest))


def write_manifest(manifest: dict, output_dir: Path) -> Path:
    path = Path(output_dir) / MANIFEST_FILENAME
    path.write_bytes(canonical_json_bytes(manifest))
    return path


def verify_manifest(manifest: dict, raw_dir: Path) -> dict:
    """Check a raw directory (e.g. the downloaded sec-companyfacts-raw
    artifact) against a manifest. Fail closed: a missing file, a size or
    hash difference, or an unusable manifest is FAIL. A manifest with no
    inputs is NO_SEC_INPUTS, never OK."""
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != MANIFEST_VERSION:
        return {"status": VERIFY_FAIL, "reason": "unrecognized manifest", "missing": [], "mismatched": []}
    files = manifest.get("files")
    if not isinstance(files, list):
        return {"status": VERIFY_FAIL, "reason": "manifest has no file list", "missing": [], "mismatched": []}
    if manifest.get("status") == SNAPSHOT_INCOMPLETE:
        return {"status": VERIFY_FAIL, "reason": "manifest marks inputs without a stored file",
                "missing": list(manifest.get("inputs_without_stored_file") or []), "mismatched": []}
    if not files:
        return {"status": VERIFY_NO_SEC_INPUTS, "reason": "no SEC inputs recorded", "missing": [], "mismatched": []}
    missing, mismatched = [], []
    base = Path(raw_dir)
    for f in files:
        p = base / f["path"]
        if not p.is_file():
            missing.append(f["path"])
            continue
        data = p.read_bytes()
        if len(data) != f.get("size_bytes") or sha256_bytes(data) != f.get("sha256"):
            mismatched.append(f["path"])
    status = VERIFY_OK if not missing and not mismatched else VERIFY_FAIL
    return {"status": status, "reason": None if status == VERIFY_OK else "missing or changed files",
            "missing": missing, "mismatched": mismatched}


__all__ = [
    "MANIFEST_VERSION",
    "MANIFEST_FILENAME",
    "SOURCE_CACHE",
    "SOURCE_FETCHED",
    "SNAPSHOT_OK",
    "SNAPSHOT_NO_SEC_INPUTS",
    "SNAPSHOT_INCOMPLETE",
    "VERIFY_OK",
    "VERIFY_FAIL",
    "VERIFY_NO_SEC_INPUTS",
    "build_manifest",
    "canonical_json_bytes",
    "load_input_file",
    "manifest_sha256",
    "snapshot_content_sha256",
    "verify_manifest",
    "write_manifest",
]
