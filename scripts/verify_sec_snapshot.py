"""
Offline SEC snapshot verifier (STEP 23 SEC PIT, C3).

Checks a downloaded fundamentals-batch-results artifact against the
sec-companyfacts-raw artifact of the same run:

    python scripts/verify_sec_snapshot.py RESULTS.zip RAW.zip

(an extracted directory is also accepted for either argument).

Order: inputs present and valid -> summary.json -> summary snapshot status
and expected hashes -> manifest file -> manifest structure -> manifest
SHA-256 vs summary -> snapshot_content_sha256 vs summary -> every listed raw
file present with matching size and SHA-256.

Prints PASS or FAIL with reasons. Exit code: 0 PASS, 1 FAIL, 2 usage error.
Raw files that the manifest does not list are reported but are not a
failure (they were not pipeline inputs).

Read-only: ZIP members are read in memory (never extracted); unsafe
members (absolute paths, '..', backslashes, drive letters, symbolic links,
duplicate names) fail the check. No network access, no repository code
other than src/fundamentals/sec_snapshot.py (standard library only).

Limitation: summary.json and the manifest ship in the same results
artifact, so the hash comparison is an internal-consistency check. A
consistent rewrite of the manifest, the raw files AND summary.json is not
detected; that needs an externally held hash or a signature.
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fundamentals import sec_snapshot as snap  # noqa: E402

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

SUMMARY_FILENAME = "summary.json"
# Only a snapshot that the run itself wrote, re-read and verified can pass.
REQUIRED_SNAPSHOT_STATUS = "OK"
REQUIRED_SELF_VERIFICATION_STATUS = "OK"
REQUIRED_MANIFEST_FILE_STATUS = "WRITTEN_AND_REREAD"

# Errors raised while reading ZIP data (corrupt archive, corrupt compressed
# stream, CRC failure, truncated data, unsupported compression). Converted to
# OSError so callers report them as FAIL; KeyboardInterrupt/SystemExit are
# never caught.
_ZIP_READ_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError, NotImplementedError,
                    ValueError)


class InputError(Exception):
    """Unusable input (missing, not a ZIP, unsafe member, ...)."""


class Archive:
    """Read-only view over a ZIP file or a directory, keyed by relative POSIX path."""

    def __init__(self, path: str, label: str):
        self.label = label
        p = Path(path)
        if not p.exists():
            raise InputError(f"{label} not found: {path}")
        self._zip = None
        self._dir = None
        if p.is_dir():
            self._dir = p
            self._names = None
            return
        if not zipfile.is_zipfile(p):
            raise InputError(f"{label} is not a valid ZIP file: {path}")
        try:
            zf = zipfile.ZipFile(p)
            infos = zf.infolist()
        except _ZIP_READ_ERRORS as exc:
            raise InputError(f"{label} could not be opened: {exc}") from exc
        names: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            name = info.filename
            if info.is_dir():
                continue
            errs = snap.check_relative_path(name)
            if errs:
                raise InputError(f"{label}: unsafe ZIP entry {name!r}: {errs}")
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and stat.S_ISLNK(mode):
                raise InputError(f"{label}: symbolic link ZIP entry {name!r}")
            if name in names:
                raise InputError(f"{label}: duplicate ZIP entry {name!r}")
            names[name] = info
        self._zip = zf
        self._names = names

    def read(self, rel: str) -> bytes | None:
        if self._dir is not None:
            return snap.directory_reader(self._dir)(rel)
        errs = snap.check_relative_path(rel)
        if errs:
            raise snap.SnapshotPathError(f"{rel!r}: {errs}")
        info = self._names.get(rel)
        if info is None:
            return None
        try:
            return self._zip.read(info)
        except _ZIP_READ_ERRORS as exc:
            raise OSError(f"{self.label}: cannot read {rel!r}: {type(exc).__name__}: {exc}") from exc

    def names(self) -> list[str]:
        if self._dir is not None:
            return sorted(p.relative_to(self._dir).as_posix() for p in self._dir.rglob("*") if p.is_file())
        return sorted(self._names)


def _is_sha256(v) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def verify(results_path: str, raw_path: str) -> dict:
    """Returns {"result": "PASS"|"FAIL", "reasons": [...], "info": [...], ...}."""
    reasons: list[str] = []
    info: list[str] = []
    out = {"result": "FAIL", "reasons": reasons, "info": info}
    try:
        results = Archive(results_path, "results artifact")
        raw = Archive(raw_path, "raw artifact")
    except InputError as exc:
        reasons.append(str(exc))
        return out

    try:
        summary_bytes = results.read(SUMMARY_FILENAME)
    except (OSError, snap.SnapshotPathError) as exc:
        reasons.append(f"summary.json unreadable: {exc}")
        return out
    if summary_bytes is None:
        reasons.append("summary.json missing from the results artifact")
        return out
    try:
        summary = json.loads(summary_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        reasons.append(f"summary.json is not valid JSON: {exc}")
        return out
    if not isinstance(summary, dict):
        reasons.append("summary.json is not a JSON object")
        return out
    repro = summary.get("reproducibility")
    if not isinstance(repro, dict):
        reasons.append("summary.json reproducibility is missing or not an object (pre-STEP-23 or damaged results)")
        return out
    sec = repro.get("sec_snapshot")
    if not isinstance(sec, dict):
        reasons.append("summary.json reproducibility.sec_snapshot is missing or not an object")
        return out
    expected_manifest = sec.get("manifest_sha256")
    expected_content = sec.get("snapshot_content_sha256")
    if not _is_sha256(expected_manifest) or not _is_sha256(expected_content):
        reasons.append("summary.json lacks a valid expected manifest_sha256 / snapshot_content_sha256")
        return out
    # R1: the recorded run-time state must be the one fully verified state;
    # a value that is merely present is not enough.
    out["summary_snapshot_status"] = sec.get("status")
    if "status" not in sec:
        reasons.append("summary.json sec_snapshot.status is missing")
    elif sec.get("status") != REQUIRED_SNAPSHOT_STATUS:
        reasons.append(f"summary.json sec_snapshot.status is {sec.get('status')!r}, not {REQUIRED_SNAPSHOT_STATUS!r}")
    sv = sec.get("self_verification")
    if not isinstance(sv, dict):
        reasons.append("summary.json sec_snapshot.self_verification is missing or not an object")
    else:
        out["summary_self_verification"] = sv.get("verification_status")
        if sv.get("verification_status") != REQUIRED_SELF_VERIFICATION_STATUS:
            reasons.append(f"run-time self-verification recorded in summary.json is "
                           f"{sv.get('verification_status')!r}, not {REQUIRED_SELF_VERIFICATION_STATUS!r}")
        if sv.get("manifest_file_status") != REQUIRED_MANIFEST_FILE_STATUS:
            reasons.append(f"run-time manifest_file_status is {sv.get('manifest_file_status')!r}, "
                           f"not {REQUIRED_MANIFEST_FILE_STATUS!r}")

    manifest_name = sec.get("manifest_file")
    if snap.check_relative_path(manifest_name) or manifest_name != snap.MANIFEST_FILENAME:
        reasons.append(f"unexpected manifest_file in summary.json: {manifest_name!r}")
        return out
    try:
        manifest_bytes = results.read(manifest_name)
    except (OSError, snap.SnapshotPathError) as exc:
        reasons.append(f"manifest unreadable: {exc}")
        return out
    if manifest_bytes is None:
        reasons.append(f"{manifest_name} missing from the results artifact")
        return out
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        reasons.append(f"manifest is not valid JSON: {exc}")
        return out
    if snap.sha256_bytes(manifest_bytes) != expected_manifest:
        reasons.append("manifest file SHA-256 does not match summary.json manifest_sha256")
    if not isinstance(manifest, dict):
        reasons.append("manifest is not a JSON object")
    elif sec.get("file_count") != manifest.get("file_count"):
        reasons.append("summary.json file_count does not match the manifest")

    res = snap.verify_manifest_with_reader(
        manifest, raw.read,
        expected_manifest_sha256=expected_manifest,
        expected_snapshot_content_sha256=expected_content,
    )
    out["verification"] = res
    if res["status"] != snap.VERIFY_OK:
        reasons.append(f"manifest verification {res['status']}: {res.get('reason')}")
        reasons.extend(res.get("validation_errors") or [])
        if res.get("missing_files"):
            reasons.append(f"raw files missing: {res['missing_files']}")
        if res.get("mismatched_files"):
            reasons.append(f"raw files with different size/SHA-256: {res['mismatched_files']}")

    files = manifest.get("files") if isinstance(manifest, dict) else None
    listed = {f.get("path") for f in files if isinstance(f, dict)} if isinstance(files, list) else set()
    unlisted = [n for n in raw.names() if n not in listed]
    if unlisted:
        info.append(f"raw files not listed in the manifest (not pipeline inputs, not a failure): {unlisted}")
    info.append("Limitation: hashes are compared with summary.json from the same artifact (internal consistency, not authentication).")
    if not reasons:
        out["result"] = "PASS"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", help="fundamentals-batch-results ZIP (or extracted directory)")
    parser.add_argument("raw", help="sec-companyfacts-raw ZIP (or extracted directory)")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    out = verify(args.results, args.raw)
    print(f"RESULT: {out['result']}")
    for r in out["reasons"]:
        print(f"  FAIL: {r}")
    for i in out["info"]:
        print(f"  info: {i}")
    v = out.get("verification") or {}
    if v:
        print(f"  manifest sha256: {v.get('actual_manifest_sha256')}")
        print(f"  content  sha256: {v.get('actual_snapshot_content_sha256')}")
    return EXIT_PASS if out["result"] == "PASS" else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
