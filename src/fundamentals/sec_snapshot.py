"""
SEC input snapshot manifest (STEP 23 SEC PIT, 2026-11-02).

A batch's SEC snapshot is defined as the companyfacts JSON files that were
stored in --raw-dir AND actually loaded as pipeline input -- not the SEC
HTTP response bytes (the fetcher parses and discards those) and not every
file that happens to sit in the raw directory.

The manifest records, per input file: its path relative to the raw
directory (POSIX), ticker, CIK, how it was obtained (CACHE / FETCHED),
its size and the SHA-256 of its bytes. Two hashes are derived, both over
the canonical serialization (canonical_json_bytes):

- snapshot_content_sha256: over (path, size_bytes, sha256) only. Identifies
  the input bytes; identical for a live run and a later run over the same
  stored files, whatever their source.
- manifest_sha256: over the whole manifest (including ticker, CIK and
  source). The manifest file is written as exactly these canonical bytes,
  so the file's own SHA-256 equals manifest_sha256.

Integrity checks are split in two and never self-referential (the manifest
does not contain its own hash):

A. the manifest matches an EXPECTED manifest hash supplied from outside it
   (the value computed in memory during the run, or summary.json's record);
B. every file the manifest lists matches the bytes actually present.

Limitation: when the expected hash comes from summary.json shipped in the
same results artifact, A is an internal-consistency check. Someone who
rewrites the manifest, the raw files AND summary.json consistently is not
detected; that would need an externally held hash or a signature.

Hashes identify data; they do not preserve it. The files themselves are
preserved separately (the sec-companyfacts-raw workflow artifact).

Standard library only, no network, no repository code imports.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

MANIFEST_VERSION = "SEC_SNAPSHOT_MANIFEST_V1"
MANIFEST_FILENAME = "sec_snapshot_manifest.json"

SOURCE_CACHE = "CACHE"
SOURCE_FETCHED = "FETCHED"
SOURCES = frozenset({SOURCE_CACHE, SOURCE_FETCHED})

SNAPSHOT_OK = "OK"
SNAPSHOT_NO_SEC_INPUTS = "NO_SEC_INPUTS"
SNAPSHOT_INCOMPLETE = "INCOMPLETE"  # an input was used without a stored file
SNAPSHOT_STATUSES = frozenset({SNAPSHOT_OK, SNAPSHOT_NO_SEC_INPUTS, SNAPSHOT_INCOMPLETE})

VERIFY_OK = "OK"
VERIFY_FAIL = "FAIL"
VERIFY_NO_SEC_INPUTS = "NO_SEC_INPUTS"
# The run used inputs without a stored file: nothing can be verified. Never OK.
VERIFY_NOT_VERIFIABLE = "NOT_VERIFIABLE"

MANIFEST_KEYS = frozenset({"manifest_version", "status", "file_count", "files", "inputs_without_stored_file"})
FILE_KEYS = frozenset({"path", "ticker", "cik", "source", "size_bytes", "sha256"})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CIK_RE = re.compile(r"^[0-9]{10}$")
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# A reader returns a file's bytes for a manifest path, or None if absent.
# It may raise SnapshotPathError for a path it refuses to resolve.
Reader = Callable[[str], "bytes | None"]


class SnapshotPathError(ValueError):
    """A manifest path that is unsafe or resolves outside the snapshot root."""


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


# ------------------------------------------------------------------ validation

def check_relative_path(path: Any) -> list[str]:
    """Errors for a manifest/zip path that is not a plain relative POSIX
    path inside the snapshot root. Empty list = acceptable."""
    if not isinstance(path, str) or not path:
        return ["path must be a non-empty string"]
    errors = []
    if "\x00" in path:
        errors.append("path contains NUL")
    if "\\" in path:
        errors.append("path contains a backslash")
    if path.startswith("/") or PurePosixPath(path).is_absolute():
        errors.append("absolute path")
    if _DRIVE_RE.match(path):
        errors.append("drive-qualified path")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        errors.append("path has empty, '.' or '..' segments")
    return errors


def validate_manifest_structure(manifest: Any) -> list[str]:
    """Structural validation only (no file access). Never raises."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest is not an object"]
    keys = set(manifest)
    missing = sorted(MANIFEST_KEYS - keys)
    extra = sorted(keys - MANIFEST_KEYS)
    if missing:
        errors.append(f"missing manifest fields: {missing}")
    if extra:
        errors.append(f"unexpected manifest fields: {extra}")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        errors.append(f"unsupported manifest_version: {manifest.get('manifest_version')!r}")
    status = manifest.get("status")
    if status not in SNAPSHOT_STATUSES:
        errors.append(f"invalid status: {status!r}")
    files = manifest.get("files")
    if not isinstance(files, list):
        errors.append("files is not a list")
        files = []
    count = manifest.get("file_count")
    if not isinstance(count, int) or isinstance(count, bool) or count != len(files):
        errors.append(f"file_count {count!r} does not match {len(files)} file entries")
    without = manifest.get("inputs_without_stored_file")
    if not isinstance(without, list) or not all(isinstance(t, str) for t in without):
        errors.append("inputs_without_stored_file is not a list of strings")
        without = []
    if status == SNAPSHOT_OK and (not files or without):
        errors.append("status OK requires files and no inputs without a stored file")
    if status == SNAPSHOT_NO_SEC_INPUTS and (files or without):
        errors.append("status NO_SEC_INPUTS requires no files")
    if status == SNAPSHOT_INCOMPLETE and not without:
        errors.append("status INCOMPLETE requires inputs_without_stored_file")

    seen: set[str] = set()
    for i, f in enumerate(files):
        where = f"files[{i}]"
        if not isinstance(f, dict):
            errors.append(f"{where} is not an object")
            continue
        fkeys = set(f)
        if FILE_KEYS - fkeys:
            errors.append(f"{where} missing fields: {sorted(FILE_KEYS - fkeys)}")
        if fkeys - FILE_KEYS:
            errors.append(f"{where} unexpected fields: {sorted(fkeys - FILE_KEYS)}")
        path = f.get("path")
        for e in check_relative_path(path):
            errors.append(f"{where} {e}")
        if isinstance(path, str):
            if path in seen:
                errors.append(f"{where} duplicate path {path!r}")
            seen.add(path)
        if not isinstance(f.get("ticker"), str) or not _TICKER_RE.match(f.get("ticker") or ""):
            errors.append(f"{where} invalid ticker")
        if not isinstance(f.get("cik"), str) or not _CIK_RE.match(f.get("cik") or ""):
            errors.append(f"{where} invalid cik (10 digits)")
        if f.get("source") not in SOURCES:
            errors.append(f"{where} invalid source")
        size = f.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            errors.append(f"{where} invalid size_bytes")
        if not isinstance(f.get("sha256"), str) or not _SHA256_RE.match(f.get("sha256") or ""):
            errors.append(f"{where} invalid sha256")
    if files and [f.get("path") for f in files if isinstance(f, dict)] != sorted(
        (f.get("path") for f in files if isinstance(f, dict) and isinstance(f.get("path"), str))
    ):
        errors.append("files are not sorted by path")
    return errors


def directory_reader(raw_dir: Path) -> Reader:
    """Reader over a directory. Refuses unsafe paths and anything that
    resolves outside raw_dir (including through a symbolic link)."""
    root = Path(raw_dir).resolve()

    def read(path: str) -> bytes | None:
        errs = check_relative_path(path)
        if errs:
            raise SnapshotPathError(f"{path!r}: {errs}")
        candidate = Path(raw_dir) / path
        # Any symlink on the way (file or intermediate directory) is refused.
        probe = Path(raw_dir)
        for part in PurePosixPath(path).parts:
            probe = probe / part
            if probe.is_symlink():
                raise SnapshotPathError(f"{path!r}: symbolic link inside the snapshot")
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise SnapshotPathError(f"{path!r}: resolves outside the snapshot root")
        if not resolved.is_file():
            return None
        return resolved.read_bytes()

    return read


def verify_manifest_with_reader(
    manifest: Any,
    reader: Reader,
    *,
    expected_manifest_sha256: str | None = None,
    expected_snapshot_content_sha256: str | None = None,
) -> dict:
    """Fail-closed verification. Never raises: any error becomes FAIL with
    the cause in validation_errors (file-access errors are reported, never
    skipped)."""
    result: dict[str, Any] = {
        "status": VERIFY_FAIL,
        "reason": None,
        "validation_errors": [],
        "missing_files": [],
        "mismatched_files": [],
        "expected_manifest_sha256": expected_manifest_sha256,
        "actual_manifest_sha256": None,
        "expected_snapshot_content_sha256": expected_snapshot_content_sha256,
        "actual_snapshot_content_sha256": None,
    }
    try:
        errors = validate_manifest_structure(manifest)
        if isinstance(manifest, dict):
            try:
                result["actual_manifest_sha256"] = manifest_sha256(manifest)
            except (TypeError, ValueError) as exc:
                errors.append(f"manifest not serializable: {exc}")
        if errors:
            result["validation_errors"] = errors
            result["reason"] = "invalid manifest"
            return result
        result["actual_snapshot_content_sha256"] = snapshot_content_sha256(manifest)
        # A. manifest vs an externally supplied expected hash
        if expected_manifest_sha256 is not None and expected_manifest_sha256 != result["actual_manifest_sha256"]:
            errors.append("manifest_sha256 does not match the expected value")
        if (expected_snapshot_content_sha256 is not None
                and expected_snapshot_content_sha256 != result["actual_snapshot_content_sha256"]):
            errors.append("snapshot_content_sha256 does not match the expected value")
        if manifest["status"] == SNAPSHOT_INCOMPLETE:
            result["validation_errors"] = errors
            result["status"] = VERIFY_NOT_VERIFIABLE if not errors else VERIFY_FAIL
            result["reason"] = "inputs were used without a stored file"
            return result
        if manifest["status"] == SNAPSHOT_NO_SEC_INPUTS:
            result["validation_errors"] = errors
            result["status"] = VERIFY_NO_SEC_INPUTS if not errors else VERIFY_FAIL
            result["reason"] = "no SEC inputs recorded"
            return result
        # B. listed files vs actual bytes
        for f in manifest["files"]:
            try:
                data = reader(f["path"])
            except SnapshotPathError as exc:
                errors.append(str(exc))
                continue
            except OSError as exc:
                errors.append(f"{f['path']!r}: read error {type(exc).__name__}: {exc}")
                continue
            if data is None:
                result["missing_files"].append(f["path"])
            elif len(data) != f["size_bytes"] or sha256_bytes(data) != f["sha256"]:
                result["mismatched_files"].append(f["path"])
        result["validation_errors"] = errors
        if errors or result["missing_files"] or result["mismatched_files"]:
            result["reason"] = "integrity check failed"
            return result
        result["status"] = VERIFY_OK
        return result
    except Exception as exc:  # defensive: never propagate, never pass
        result["status"] = VERIFY_FAIL
        result["reason"] = "verification error"
        result["validation_errors"] = list(result["validation_errors"]) + [f"{type(exc).__name__}: {exc}"]
        return result


def verify_manifest(
    manifest: Any,
    raw_dir: Path,
    *,
    expected_manifest_sha256: str | None = None,
    expected_snapshot_content_sha256: str | None = None,
) -> dict:
    """Check a raw directory (e.g. the extracted sec-companyfacts-raw
    artifact or the run's --raw-dir) against a manifest. Fail closed."""
    if not Path(raw_dir).is_dir():
        res = verify_manifest_with_reader(manifest, lambda p: None,
                                          expected_manifest_sha256=expected_manifest_sha256,
                                          expected_snapshot_content_sha256=expected_snapshot_content_sha256)
        if res["status"] == VERIFY_OK:  # unreachable with files present, kept fail-closed
            res["status"] = VERIFY_FAIL
        res["validation_errors"] = list(res["validation_errors"]) + [f"raw directory not found: {os.fspath(raw_dir)}"]
        if res["status"] not in (VERIFY_NO_SEC_INPUTS, VERIFY_NOT_VERIFIABLE):
            res["status"] = VERIFY_FAIL
        return res
    return verify_manifest_with_reader(
        manifest,
        directory_reader(raw_dir),
        expected_manifest_sha256=expected_manifest_sha256,
        expected_snapshot_content_sha256=expected_snapshot_content_sha256,
    )


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
    "VERIFY_NOT_VERIFIABLE",
    "SnapshotPathError",
    "build_manifest",
    "canonical_json_bytes",
    "check_relative_path",
    "directory_reader",
    "load_input_file",
    "manifest_sha256",
    "snapshot_content_sha256",
    "validate_manifest_structure",
    "verify_manifest",
    "verify_manifest_with_reader",
    "write_manifest",
]
