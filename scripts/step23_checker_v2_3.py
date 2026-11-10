"""STEP 23 Checker v2.4 (read-only): DEC055 gate materialization + same-date batch pair comparison.

v2.4 (STEP 24.3 A-0): optional profile field "allowed_added_paths" lets D2 accept an EXACT, pair-bound list of
newly added leaf paths (migration allowance). Without that field D2 behaves exactly as in v2.3.

usage:
  python3 -I scripts/step23_checker_v2_3.py --new NEW --base BASE --expect-date YYYY-MM-DD --profile (FILE.json|none)
        [--new-raw RAW] [--base-raw RAW] [--new-sha SHA40] [--base-sha SHA40] [--repo-root DIR] [--json-out FILE]

  NEW / BASE        : fundamentals-batch-results ZIP or extracted directory (same evaluation date)
  --new-raw/base-raw: sec-companyfacts-raw ZIP or directory of the same run (optional; without it the
                      raw-file and post-cutoff-accession checks are REVIEW)
  --profile         : REQUIRED, explicit. A JSON profile binds run-specific expectations to one BASE/NEW pair
                      (run ids, full SHAs, evaluation date); applying it to other runs FAILs (P0).
                      "none" disables only run-specific numbers and per-ticker expectations; every structural,
                      governance, coverage, comparison, PIT, integrity and leak check still runs.
  --repo-root       : repository root used to compare this checker's dependency maps with the production
                      registry by static parsing (default: the checkout that contains this script). The
                      production module is never imported. Unverifiable or different maps -> REVIEW (M1).

Verdicts per check: PASS / REVIEW / FAIL (INFO is informational).
Exit: 0 PASS, 1 FAIL, 2 REVIEW, 3 input error (unsafe/corrupt/missing input or invalid profile; nothing judged).

Guarantees:
  * standard library only; imports no repository code; no network; never writes into its inputs;
    ZIPs are read member-by-member in memory (no extraction).
  * never prints a governance-WITHHELD original value: diff output lists paths only (frame labels are
    printed because they are period labels, not amounts), and the final report text is itself scanned
    for WITHHELD original values before it is printed.
  * independent of scripts/verify_sec_snapshot.py: hashes are re-derived here as a second, separate
    implementation; that verifier is neither replaced nor called.
"""
from __future__ import annotations

import argparse, ast, copy, hashlib, json, math, os, re, stat, sys, traceback, zipfile
from datetime import date, timedelta

VERSION = "checker_v2.4"
# ---------------------------------------------------------------- policy constants
# Copied from the production registry below; M1 compares them with the checkout by static parsing.
MAPPING_SOURCE = {"file": "src/fundamentals/metric_eligibility_registry.py",
                  "names": {"DERIVED_DEPS": "DERIVED_DEPENDENCIES", "GROWTH_DEPS": "GROWTH_DEPENDENCIES"},
                  "commit": "a1a86d70a9745eb0be1299587b6151d85bf1116d",
                  "file_sha256_at_commit": "a1178eba0af6fb3d8d5ed72d10a9cb44945e12d8d1609f34ca129c5008ea335b"}
DERIVED_DEPS = {"fcf": ("cfo", "capex"), "fcf_margin": ("cfo", "capex"), "cfo_yoy": ("cfo",), "capex_yoy": ("capex",),
                "operating_margin": ("operating_income",), "operating_income_yoy": ("operating_income",),
                "net_debt": ("total_debt",), "debt_to_cash": ("total_debt",)}
GROWTH_DEPS = {"fcf_yoy": ("cfo", "capex"), "operating_income_yoy": ("operating_income",)}
STAGE_DEPS = {"derived": DERIVED_DEPS, "growth": GROWTH_DEPS, "data_sufficiency": GROWTH_DEPS}
STATUS_KEY = {"derived": "status", "growth": "status", "data_sufficiency": "calculation_status"}
GATE_SET_BY = "DEC055_GATE"
GOV_REASON = "GOVERNANCE_WITHHELD"
POLICY_V1 = "DEC055_GATE_V1_RELABEL_PRODUCED_DEPENDENTS"
POLICY_V2 = "DEC055_GATE_V2_RELABEL_ALL_DEPENDENTS"
KNOWN_POLICIES = (POLICY_V1, POLICY_V2)  # V1: relabel produced dependents only; V2: list every dependent
NO_REASON_CODE, KEY_ABSENT = "NO_REASON_CODE", "KEY_ABSENT"
NOT_APPLICABLE_BY_TYPE = {"FINANCIAL": {"operating_income", "cfo", "capex", "current_debt", "noncurrent_debt", "total_debt"},
                          "NON_FINANCIAL": set()}
# run-to-run classification
RUN_TS_KEYS = {"normalized_at", "fetched_at_utc", "loaded_at_utc", "generated_at_utc", "run_started_at_utc", "run_started_at_kst"}
FRAME_IDENTITY = ("value", "period_start", "period_end", "filing_date", "form", "accession", "concept", "unit", "namespace",
                  "status", "fy", "fp")
# keys of a gate marker that may change when a V1 marker becomes a V2 marker (status/value may not)
MARKER_MUTABLE = {"materialized", "reason_code", "set_by", "withheld_inputs", "reason", "detail", "original_status"}
LARGE_ABS = 1000          # |original| >= this: any numeric token anywhere is a leak (rounded displays: +-0.5)
VALUE_KEYS = {"value", "val", "amount", "original_value"}   # keys that carry an amount (fy etc. never do)
ACCN_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
# SEC snapshot manifest contract, re-implemented from src/fundamentals/sec_snapshot.py (a1a86d7) without importing it
MANIFEST_VERSION = "SEC_SNAPSHOT_MANIFEST_V1"
MANIFEST_KEYS = {"manifest_version", "status", "file_count", "files", "inputs_without_stored_file"}
MANIFEST_FILE_KEYS = {"path", "ticker", "cik", "source", "size_bytes", "sha256"}
SNAPSHOT_STATUSES = {"OK", "NO_SEC_INPUTS", "INCOMPLETE"}
RAW_SOURCES = {"CACHE", "FETCHED"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CIK_RE = re.compile(r"^[0-9]{10}$")
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
SHA40 = re.compile(r"[0-9a-f]{40}")
# ---------------------------------------------------------------- v2.4 migration allowance (D2)
# Mirror of production growth.GROWTH_REASON_CODES (STEP 24.3 A-1). The A-1 PR adds a test asserting equality.
GROWTH_REASON_CODES = frozenset({"NON_NUMERIC_INPUT", "NON_FINITE_INPUT", "NONPOSITIVE_PRIOR_BASE", "NONPOSITIVE_CURRENT",
                                 "ZERO_PRIOR_BASE", "NEGATIVE_END_VALUE", "INVALID_PERIOD_PARAMETER"})
ALLOWANCE_VALUE_SETS = {"GROWTH_REASON_CODES": GROWTH_REASON_CODES}
# exact path grammar per value set: one Growth component's reason_code leaf; no wildcard / prefix forms exist
ALLOWANCE_PATH_RE = {"GROWTH_REASON_CODES": re.compile(
    r"^/stages/growth/growth/components/(revenue_yoy|operating_income_yoy|eps_yoy|fcf_yoy|revenue_cagr_3y)/reason_code$")}
ALLOWANCE_KEYS = {"migration_id", "binding", "value_set", "paths"}
ALLOWANCE_BINDING_KEYS = {"evaluation_date", "base_github_run_id", "base_github_sha", "new_github_run_id", "new_github_sha"}
ALLOWED_ADDITION = "ALLOWED_ADDITION"
MAX_MEMBER = 512 * 1024 * 1024

class InputError(Exception):
    pass


# ---------------------------------------------------------------- safe input
def member_problems(name: str) -> list[str]:
    p = []
    if not name or "\x00" in name: p.append("empty/NUL name")
    if name.startswith("/"): p.append("absolute path")
    if "\\" in name: p.append("backslash")
    if re.match(r"^[A-Za-z]:", name): p.append("drive path")
    segs = name.rstrip("/").split("/")
    if any(s in ("..", ".") for s in segs): p.append("dot segment")
    if any(s == "" for s in segs): p.append("empty segment")
    return p


class Archive:
    """Read-only view over a ZIP or a directory. Fail-closed on anything unusual."""

    def __init__(self, path: str, anchor: str | None):
        self.path, self.files, self.zip_sha256 = path, {}, None
        if os.path.isdir(path):
            self._dir(path)
        elif os.path.isfile(path):
            self._zip(path)
        else:
            raise InputError(f"{path}: not found")
        self.prefix = ""
        if anchor:
            hits = [n for n in self.files if n == anchor or n.endswith("/" + anchor)]
            if len(hits) != 1: raise InputError(f"{path}: expected exactly one {anchor}, found {len(hits)}")
            self.prefix = hits[0][: -len(anchor)]

    def _zip(self, path):
        try:
            raw = open(path, "rb").read()
            self.zip_sha256 = hashlib.sha256(raw).hexdigest()
            zf = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as e:
            raise InputError(f"{path}: unreadable zip ({type(e).__name__})")
        seen, folded = set(), set()
        with zf:
            for info in zf.infolist():
                n = info.filename
                prob = member_problems(n)
                if prob: raise InputError(f"{path}: unsafe member {n!r}: {', '.join(prob)}")
                if stat.S_ISLNK(info.external_attr >> 16): raise InputError(f"{path}: symlink member {n!r}")
                if info.flag_bits & 0x1: raise InputError(f"{path}: encrypted member {n!r}")
                if n in seen or n.casefold() in folded: raise InputError(f"{path}: duplicate member {n!r}")
                seen.add(n); folded.add(n.casefold())
                if n.endswith("/"): continue
                if info.file_size > MAX_MEMBER: raise InputError(f"{path}: member too large {n!r}")
                try:
                    self.files[n] = zf.read(info)
                except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, NotImplementedError, RuntimeError, ValueError) as e:
                    raise InputError(f"{path}: corrupt member {n!r} ({type(e).__name__})")

    def _dir(self, path):
        for root, dirs, files in os.walk(path, followlinks=False):
            for d in dirs:
                if os.path.islink(os.path.join(root, d)): raise InputError(f"{path}: symlinked directory {d!r}")
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, path).replace(os.sep, "/")
                if os.path.islink(full): raise InputError(f"{path}: symlink {rel!r}")
                if member_problems(rel): raise InputError(f"{path}: unsafe path {rel!r}")
                if not stat.S_ISREG(os.lstat(full).st_mode): continue
                fd = os.open(full, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as fh:
                    self.files[rel] = fh.read()

    def has(self, name): return (self.prefix + name) in self.files
    def bytes(self, name):
        if not self.has(name): raise InputError(f"{self.path}: missing {name}")
        return self.files[self.prefix + name]
    def json(self, name):
        try:
            return json.loads(self.bytes(name).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise InputError(f"{self.path}: {name} is not valid JSON ({type(e).__name__})")
    def text(self, name):
        try:
            return self.bytes(name).decode("utf-8")
        except UnicodeDecodeError:
            raise InputError(f"{self.path}: {name} is not UTF-8")


class Run:
    def __init__(self, arc: Archive, raw: Archive | None, label: str):
        self.arc, self.raw, self.label = arc, raw, label
        self.summary = arc.json("summary.json")
        self.coverage = arc.json("coverage.json")
        self.summary_md, self.coverage_md = arc.text("summary.md"), arc.text("coverage.md")
        if not isinstance(self.summary, dict) or not isinstance(self.summary.get("records"), list):
            raise InputError(f"{label}: summary.json lacks records")
        if not isinstance(self.coverage, dict): raise InputError(f"{label}: coverage.json is not an object")
        self.results = {}
        for r in self.summary["records"]:
            if not isinstance(r, dict) or not isinstance(r.get("ticker"), str): raise InputError(f"{label}: malformed record")
            if r.get("status") == "OK":
                t = r["ticker"]
                if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", t): raise InputError(f"{label}: odd ticker {t!r}")
                res = arc.json(f"{t}.json")
                if not isinstance(res, dict): raise InputError(f"{label}: {t}.json is not an object")
                self.results[t] = res
        self.manifest = arc.json("sec_snapshot_manifest.json") if arc.has("sec_snapshot_manifest.json") else None

    @property
    def repro(self): return self.summary.get("reproducibility") or {}


# ---------------------------------------------------------------- accessors
def _d(x): return x if isinstance(x, dict) else {}       # malformed JSON never reaches .get() on a non-dict
def _l(x): return x if isinstance(x, list) else []
def stages(r): return _d(_d(r).get("stages"))
def metrics(r): return _d(_d(_d(stages(r).get("normalization")).get("normalized")).get("metrics"))
def gate(r): return _d(stages(r).get("eligibility_gate"))
def stage_items(r, stage):
    s = stages(r)
    if stage == "derived": c = _d(_d(s.get("derived_metrics")).get("derived")).get("derived_metrics")
    elif stage == "growth": c = _d(_d(s.get("growth")).get("growth")).get("components")
    else: c = _d(s.get("data_sufficiency")).get("sufficiency")
    return c if isinstance(c, dict) else None
def growth_agg(r): return _d(_d(stages(r).get("growth")).get("growth")).get("growth")
def data_view(r, m):
    cur = metrics(r).get(m)
    if isinstance(cur, dict) and cur.get("status") == "WITHHELD":
        o = _d(gate(r).get("unmasked_originals")).get(m)
        return o if isinstance(o, dict) else {"status": KEY_ABSENT}
    return cur if isinstance(cur, dict) else {"status": KEY_ABSENT}
def gov(r, m):
    g = gate(r)
    if g.get("status") != "OK": return "GATE_NOT_APPLIED"
    for k, lab in (("withheld", "WITHHELD"), ("not_yet_available", "NOT_YET_AVAILABLE"), ("no_record", "NO_RECORD")):
        if m in _l(g.get(k)): return lab
    return "ELIGIBLE"
def is_marker(item): return isinstance(item, dict) and item.get("set_by") == GATE_SET_BY


def flat(o, p=""):
    out = {}
    if isinstance(o, dict):
        if not o: out[p] = {}
        for k, v in o.items(): out.update(flat(v, f"{p}/{k}"))
    elif isinstance(o, list):
        if not o: out[p] = []
        for i, v in enumerate(o): out.update(flat(v, f"{p}[{i}]"))
    else:
        out[p] = o
    return out
def get_path(o, path):
    for tok in re.findall(r"/([^/\[]+)|\[(\d+)\]", path):
        k, i = tok
        if k:
            if not isinstance(o, dict) or k not in o: return None
            o = o[k]
        else:
            i = int(i)
            if not isinstance(o, list) or i >= len(o): return None
            o = o[i]
    return o
def parent_and_key(path):
    m = re.match(r"^(.*)/([^/\[]+)$", path)
    return (m.group(1), m.group(2)) if m else (None, None)


# ---------------------------------------------------------------- per-run schema (T0)
GATE_LIST_KEYS = ("withheld", "scoped", "not_yet_available", "no_record")
RELABEL_STAGES = ("derived", "growth", "data_sufficiency")
# sec_normalizer.calculate_total_debt() is the only composite: it always returns exactly these two components
# (observed: 90/90 total_debt records in 5 batches; no other metric ever carries "components").
COMPOSITE_COMPONENTS = {"total_debt": ("current_debt", "noncurrent_debt")}
GATE_SKIPPED_KEYS = {"status", "reason"}         # orchestrator: {"status": SKIPPED, "reason": "..."}
POLICY_NOT_APPLIED = "NO_METRIC_ELIGIBILITY_GATE"     # batch_runner: the gate did not run (eligibility rows not supplied)
SEC_ACCESSION_RE = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
# Every key E1/E2 read anywhere in a ticker result: dates must be real ISO dates and accessions SEC-formatted,
# or null (observed: 25,000+ such values in 5 batches, all valid). "end"/"start" are generic words, so they are
# only date-checked inside annual history rows.
DATE_KEYS = ("filing_date", "filed", "period_end", "period_start")
ACCESSION_KEYS = ("accession", "accn")


def is_iso_date(x) -> bool:
    """A real calendar date written exactly as YYYY-MM-DD (round trip; never a lexicographic stand-in)."""
    if not isinstance(x, str) or len(x) != 10:
        return False
    try:
        return date.fromisoformat(x).isoformat() == x
    except ValueError:
        return False


def is_finite_number(x) -> bool:
    """Same contract as production derived_metrics._is_number(): an int/float (never bool) whose float() is finite.
    Production can only use a value that converts to a finite float, so an int beyond the float range (10**309)
    is not a valid amount. Never raises: an OverflowError (or any conversion error) means "not a valid number"."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(float(x))
    except (OverflowError, ValueError):
        return False


_STR = (str, type(None))
_NUM = (int, float, type(None))
# Source-identity fields of a normalized metric / unmasked original (when present). Every observed artifact has
# strings (unit sometimes null), int fy and numeric-or-null value; null is accepted for "not available".
IDENTITY_FIELD_TYPES = {"accession": _STR, "concept": _STR, "filing_date": _STR, "form": _STR, "fp": _STR,
                        "namespace": _STR, "period_end": _STR, "period_start": _STR, "unit": _STR,
                        "fy": (int, type(None)), "value": _NUM}
HISTORY_FIELD_TYPES = {"filed": _STR, "form": _STR, "fp": _STR, "fy": (int, type(None))}
# An OK observation must carry its source identity, or PIT (E1/E2) could not verify it. Observed in every artifact:
# all 907 non-composite OK metrics and all 135 OK components have them; composites (with "components") are
# checked through their components.
OK_IDENTITY_REQUIRED = ("filing_date", "accession", "period_end")


def _type_ok(v, allowed) -> bool:
    return isinstance(v, allowed) and not isinstance(v, bool)


def gate_structure_problems(g) -> list[str]:
    """Exact structure of an applied gate (status OK): apply_metric_eligibility_gate() returns the four lists and
    unmasked_originals (an object, {} when nothing is withheld) and the orchestrator adds relabelled with exactly
    derived / growth / data_sufficiency. A missing or null field is never read as an empty one."""
    p = []
    for k in GATE_LIST_KEYS:
        v = g.get(k, "<absent>")
        if not (isinstance(v, list) and all(isinstance(m, str) for m in v)):
            p.append(f"{k} is {'absent' if k not in g else type(v).__name__ if not isinstance(v, list) else 'a list with non-string items'}, not a list of metric names")
    uo = g.get("unmasked_originals", "<absent>")
    if not isinstance(uo, dict): p.append(f"unmasked_originals is {'absent' if 'unmasked_originals' not in g else type(uo).__name__}, not an object")
    rel = g.get("relabelled", "<absent>")
    if not isinstance(rel, dict):
        p.append(f"relabelled is {'absent' if 'relabelled' not in g else type(rel).__name__}, not an object")
    else:
        if set(rel) != set(RELABEL_STAGES): p.append(f"relabelled stages {sorted(rel)} != {list(RELABEL_STAGES)}")
        for k, v in rel.items():
            if not (isinstance(v, list) and all(isinstance(x, str) for x in v)): p.append(f"relabelled.{k} is not a list of metric names")
    return p


def gate_wellformed(r) -> bool:
    """The gate fields B2-B7 rely on have the production structure (validated and reported by B0)."""
    g = gate(r)
    return g.get("status") == "OK" and not gate_structure_problems(g)


def schema_problems(r) -> list[str]:
    """T0: structural types of one result, validated on its own (never by comparison with the other run).
    Contract (production a1a86d7 + every observed artifact): normalized / derived / growth / sufficiency
    containers are objects of objects; every item carries a string status (calculation_status for
    sufficiency); the growth aggregate is an object with a string status and, if present, a list of strings
    in withheld_components; every "frame" is a string or null (sec_normalizer/sec_historical copy the SEC
    observation's frame, which is a string or absent). Status VALUES are not restricted here.
    Returns paths and type names only."""
    p, st = [], stages(r)
    def tn(x): return type(x).__name__
    def items(label, container, key, fields=None):
        if not isinstance(container, dict): p.append(f"{label} is {tn(container)}, not an object"); return
        for name, item in container.items():
            if not isinstance(item, dict): p.append(f"{label}.{name} is {tn(item)}, not an object"); continue
            if not isinstance(item.get(key), str): p.append(f"{label}.{name}.{key} is {tn(item.get(key)) if key in item else 'absent'}, not a string")
            if fields: observation(f"{label}.{name}", item, fields, name)
    def observation(label, item, fields, metric_name):
        for f, allowed in fields.items():
            if f in item and not _type_ok(item[f], allowed): p.append(f"{label}.{f} is {tn(item[f])}")
        expected = COMPOSITE_COMPONENTS.get(metric_name)
        gate_mask = (item.get("status") == "WITHHELD" and isinstance(item.get("eligibility"), dict)
                     and item["eligibility"].get("governance_status") == "WITHHELD")
        if expected is None and "components" in item:
            # only calculate_total_debt() builds components; elsewhere they would exempt an OK value from identity checks
            p.append(f"{label}: components are not allowed on {metric_name!r} (only on {sorted(COMPOSITE_COMPONENTS)})"); return
        if expected is not None and "components" not in item and not gate_mask:
            # the gate's WITHHELD mask replaces the metric (its original, with components, is in unmasked_originals)
            p.append(f"{label}: components missing (calculate_total_debt() always builds {list(expected)})"); return
        if "components" in item:
            comps = item["components"]
            if not isinstance(comps, dict): p.append(f"{label}.components is {tn(comps)}, not an object"); return
            if set(comps) != set(expected):
                p.append(f"{label}.components keys {sorted(comps)} != {sorted(expected)}")
            if item.get("status") == "OK" and not is_finite_number(item.get("value")):
                p.append(f"{label}: status OK composite without a finite value")
            for cn, cv in comps.items():
                if not isinstance(cv, dict): p.append(f"{label}.components.{cn} is {tn(cv)}, not an object"); continue
                if not isinstance(cv.get("status"), str):
                    p.append(f"{label}.components.{cn}.status is {tn(cv.get('status')) if 'status' in cv else 'absent'}, not a string")
                observation(f"{label}.components.{cn}", cv, fields, cn)
        elif item.get("status") == "OK":
            # build_metric_result()/build_instant_result() copy filed/accn/end/start/val of one SEC observation
            bad = [f for f in ("filing_date", "period_end") if not is_iso_date(item.get(f))]
            if item.get("period_start") is not None and not is_iso_date(item.get("period_start")): bad.append("period_start")
            if not (isinstance(item.get("accession"), str) and SEC_ACCESSION_RE.match(item["accession"])): bad.append("accession")
            if not is_finite_number(item.get("value")): bad.append("value")
            if bad: p.append(f"{label}: status OK without valid source identity {bad}")
    norm = _d(st.get("normalization")).get("normalized")
    items("normalized.metrics", _d(norm).get("metrics") if isinstance(norm, dict) else norm, "status", IDENTITY_FIELD_TYPES)
    uo = gate(r).get("unmasked_originals")
    if isinstance(uo, dict): items("eligibility_gate.unmasked_originals", uo, "status", IDENTITY_FIELD_TYPES)
    hist = _d(st.get("annual_history")).get("history")
    if isinstance(hist, dict):
        for m, rows in hist.items():
            if not isinstance(rows, list): p.append(f"annual_history.{m} is {tn(rows)}, not a list"); continue
            for i, row in enumerate(rows):
                if not isinstance(row, dict): p.append(f"annual_history.{m}[{i}] is {tn(row)}, not an object"); continue
                for f, allowed in HISTORY_FIELD_TYPES.items():
                    if f in row and not _type_ok(row[f], allowed): p.append(f"annual_history.{m}[{i}].{f} is {tn(row[f])}")
                # point_in_time history rows are SEC observations: filed + accn always, end/start/val when present
                bad = [] if is_iso_date(row.get("filed")) else ["filed"]
                if not (isinstance(row.get("accn"), str) and SEC_ACCESSION_RE.match(row["accn"])): bad.append("accn")
                bad += [f for f in ("end", "start") if f in row and row[f] is not None and not is_iso_date(row[f])]
                if "val" in row and not is_finite_number(row["val"]): bad.append("val")
                if bad: p.append(f"annual_history.{m}[{i}]: invalid source identity {bad}")
    dm = st.get("derived_metrics")
    if isinstance(dm, dict) and "derived" in dm:
        items("derived_metrics", _d(dm.get("derived")).get("derived_metrics") if isinstance(dm.get("derived"), dict) else dm.get("derived"), "status")
    g = st.get("growth")
    if isinstance(g, dict) and "growth" in g:
        gg = g.get("growth")
        if not isinstance(gg, dict): p.append(f"growth.growth is {tn(gg)}, not an object")
        else:
            items("growth.components", gg.get("components"), "status")
            agg = gg.get("growth")
            if not isinstance(agg, dict): p.append(f"growth aggregate is {tn(agg)}, not an object")
            else:
                if not isinstance(agg.get("status"), str): p.append(f"growth aggregate status is {tn(agg.get('status')) if 'status' in agg else 'absent'}, not a string")
                if "withheld_components" in agg and not (isinstance(agg["withheld_components"], list) and all(isinstance(x, str) for x in agg["withheld_components"])):
                    p.append(f"growth aggregate withheld_components is {tn(agg['withheld_components'])}, not a list of strings")
    ds = st.get("data_sufficiency")
    if isinstance(ds, dict) and "sufficiency" in ds:
        items("data_sufficiency.sufficiency", ds.get("sufficiency"), "calculation_status")
    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "frame" and not (v is None or isinstance(v, str)):
                    p.append(f"{path}/frame is {tn(v)}, not a string or null")
                elif k in DATE_KEYS and v is not None and not is_iso_date(v):
                    p.append(f"{path}/{k} is not an ISO date (YYYY-MM-DD) or null")
                elif k in ACCESSION_KEYS and v is not None and not (isinstance(v, str) and SEC_ACCESSION_RE.match(v)):
                    p.append(f"{path}/{k} is not an SEC accession (##########-##-######) or null")
                walk(v, f"{path}/{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o): walk(v, f"{path}[{i}]")
    walk(r)
    return p


# ---------------------------------------------------------------- recomputation (mirrors batch_coverage a1a86d7)
def _inc(d, k):
    try:
        hash(k)
    except TypeError:                      # a list/dict status is malformed: count it under its type, never crash
        k = f"<{type(k).__name__}>"
    d[k] = d.get(k, 0) + 1
def recompute_stage_coverage(run: Run) -> dict:
    derived, comps, agg, wcomp, suff = {}, {}, {}, {}, {}
    def block(target, entries):
        for name, item in entries.items():
            b = target.setdefault(name, {"by_status": {}, "by_reason_code": {}})
            s = item.get("status") if isinstance(item, dict) else None
            _inc(b["by_status"], s if s is not None else KEY_ABSENT)
            if s not in ("OK", None):
                _inc(b["by_reason_code"], (item.get("reason_code") if isinstance(item, dict) else None) or NO_REASON_CODE)
    for rec in run.summary["records"]:
        if rec.get("status") != "OK": continue
        r = run.results[rec["ticker"]]
        d = stage_items(r, "derived")
        if d is not None: block(derived, d)
        g = (stages(r).get("growth") or {}).get("growth")
        if isinstance(g, dict):
            if isinstance(g.get("components"), dict): block(comps, g["components"])
            a = g.get("growth")
            if isinstance(a, dict):
                _inc(agg, a.get("status") or KEY_ABSENT)
                for c in _l(a.get("withheld_components")): _inc(wcomp, c)
        s = stage_items(r, "data_sufficiency")
        if s is not None:
            for name, item in s.items():
                b = suff.setdefault(name, {"by_calculation_status": {}, "by_reason_code": {}})
                cs = _d(item).get("calculation_status")
                _inc(b["by_calculation_status"], cs or KEY_ABSENT)
                if cs != "OK": _inc(b["by_reason_code"], _d(item).get("reason_code") or NO_REASON_CODE)
    return {"derived_metrics": derived, "growth": {"components": comps, "aggregate_status_counts": agg,
            "withheld_component_counts": wcomp}, "data_sufficiency": suff}

def recompute_normalized(run: Run, metric: str):
    r1n = r1d = r2n = r2d = 0; unknown = 0
    for rec in run.summary["records"]:
        status = rec.get("status")
        if status in ("EXCLUDED_NOT_IN_WATCHLIST", "EXCLUDED_COMPANY_TYPE"): continue
        if status == "ERROR":
            na = NOT_APPLICABLE_BY_TYPE.get(rec.get("company_type"))
            if na is None: unknown += 1; continue
            if metric not in na: r2d += 1
            continue
        if status != "OK": continue
        r = run.results[rec["ticker"]]
        ds = data_view(r, metric).get("status")
        if ds == "NOT_APPLICABLE": continue
        r1d += 1; r2d += 1
        if ds == "OK":
            r1n += 1
            if gov(r, metric) != "WITHHELD": r2n += 1
    return (r1n, r1d, r2n, r2d, "NOT_COMPUTABLE" if (unknown or r2d == 0) else "COMPUTED")


# ---------------------------------------------------------------- leak scan
NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")
def withheld_targets(run: Run) -> list[dict]:
    """Every amount inside every unmasked original, nested ones included (e.g. stale_observation.value).
    Magnitude is not filtered: small values and 0 are targets too."""
    out = []
    for t, r in run.results.items():
        for m, o in _d(gate(r).get("unmasked_originals")).items():
            names = {m} | {n for deps in (DERIVED_DEPS, GROWTH_DEPS) for n, d in deps.items() if m in d}
            for p, v in flat(o).items():
                if parent_and_key(p)[1] in VALUE_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append({"ticker": t, "metric": m, "value": v if isinstance(v, int) else float(v), "names": names})
    return out


def same_amount(x, t) -> bool:
    """x, t: int or float (ints are kept exact, so 10**400 never goes through float())."""
    try:
        if abs(t) >= LARGE_ABS: return abs(x - t) <= 0.5
        return abs(x - t) <= 1e-9 * max(1.0, abs(t))   # small values: exact (no rounding slack)
    except OverflowError:                              # e.g. a float token against an int beyond float range
        return x == t


def _json_leaves(o, path="", ticker=None, out=None):
    """(path, key, number, ticker context, kind) for every number. kind: "num" (JSON number), "numstr" (a string
    that is exactly one number, e.g. "3" or "-1,234.5"), "embedded" (a number inside other text)."""
    out = [] if out is None else out
    if isinstance(o, dict):
        # The outermost record that names a ticker fixes the scope; a nested "ticker" field cannot re-scope
        # the amounts below it (e.g. {"ticker": "GE", "metrics": {"cfo": {"ticker": "LLY", "value": ...}}} stays GE).
        tk = ticker if ticker is not None else (o.get("ticker") if isinstance(o.get("ticker"), str) else None)
        for k, v in o.items(): _json_leaves(v, f"{path}/{k}", tk, out)
    elif isinstance(o, list):
        for i, v in enumerate(o): _json_leaves(v, f"{path}[{i}]", ticker, out)
    elif isinstance(o, (int, float)) and not isinstance(o, bool):
        out.append((path, parent_and_key(path)[1], o if isinstance(o, int) else float(o), ticker, "num"))
    elif isinstance(o, str):
        toks = NUM_RE.findall(o)
        kind = "numstr" if len(toks) == 1 and o.strip() == toks[0] else "embedded"
        for tok in toks: out.append((path + "#str", parent_and_key(path)[1], float(tok.replace(",", "")), ticker, kind))
    return out


def scan_json(obj, targets):
    """-> (fail_locations, review_locations); locations only, never values.
    FAIL  : a large original anywhere; a small original (0 included) written as an amount -- a JSON number or a
            string that is exactly that number -- under a value-like key on a path naming that metric or a
            dependent, and not inside another ticker's record.
    REVIEW: a non-zero small original matching anywhere else (ambiguous with counts)."""
    fail, review = set(), set()
    for path, key, x, tk, kind in _json_leaves(obj):
        segs = set(re.split(r"[/\[\]#]", path))
        for t in targets:
            if not same_amount(x, t["value"]): continue
            if abs(t["value"]) >= LARGE_ABS:
                fail.add(path)
            elif key in VALUE_KEYS and kind != "embedded" and tk in (None, t["ticker"]) and segs & t["names"]:
                fail.add(path)
            elif t["value"] != 0:
                review.add(path)
    return sorted(fail), sorted(review)


def scan_text(txt, targets):
    """Markdown/plain text: a large original anywhere -> FAIL; a small original (0 included) on a line that names
    that metric or a dependent -> REVIEW (line context only)."""
    fail, review = set(), set()
    for ln, line in enumerate(txt.splitlines(), 1):
        nums = [float(m.replace(",", "")) for m in NUM_RE.findall(line)]
        for t in targets:
            if not any(same_amount(x, t["value"]) for x in nums): continue
            if abs(t["value"]) >= LARGE_ABS:
                fail.add(f"line {ln}")
            elif any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", line) for n in t["names"]):
                review.add(f"line {ln}")
    return sorted(fail), sorted(review)


VALUE_ASSIGN_RE = re.compile(r"""\b(?:value|val|amount|original_value)['"]?\s*[:=]\s*['"]?(-?\d[\d,]*(?:\.\d+)?)""")
def report_leaks(text, targets) -> bool:
    """Self-check of the checker's own output: large originals anywhere, small ones written as an amount."""
    nums = [float(m.replace(",", "")) for m in NUM_RE.findall(text)]
    assigned = [float(m.replace(",", "")) for m in VALUE_ASSIGN_RE.findall(text)]
    return any(same_amount(x, t["value"]) for t in targets for x in (nums if abs(t["value"]) >= LARGE_ABS else assigned))


def safe_relative_posix(path) -> list[str]:
    """Same rules as sec_snapshot.check_relative_path: plain relative POSIX path inside the snapshot root."""
    if not isinstance(path, str) or not path: return ["path must be a non-empty string"]
    e = []
    if "\x00" in path: e.append("NUL")
    if "\\" in path: e.append("backslash")
    if path.startswith("/"): e.append("absolute path")
    if re.match(r"^[A-Za-z]:", path): e.append("drive-qualified path")
    if any(seg in ("", ".", "..") for seg in path.split("/")): e.append("empty, '.' or '..' segment")
    return e


def raw_entry_problems(path, ticker, cik, source, size, sha) -> list[str]:
    """Independent validity of one raw-input description (manifest entry or summary record)."""
    p = [f"path {x}" for x in safe_relative_posix(path)]
    if not isinstance(ticker, str) or not TICKER_RE.match(ticker): p.append("invalid ticker")
    if not isinstance(cik, str) or not CIK_RE.match(cik): p.append("invalid cik (10-digit string)")
    if not (isinstance(source, str) and source in RAW_SOURCES): p.append("invalid source (CACHE/FETCHED)")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0: p.append("invalid size_bytes")
    if not isinstance(sha, str) or not SHA256_RE.match(sha): p.append("invalid sha256 (64 lowercase hex)")
    return p


def manifest_structure_problems(man) -> list[str]:
    """Mirror of sec_snapshot.validate_manifest_structure (a1a86d7), plus this checker's completeness rule:
    only a complete stored snapshot (status OK, files present, no input without a stored file) is accepted."""
    if not isinstance(man, dict): return ["manifest is not an object"]
    e = []
    keys = set(man)
    if MANIFEST_KEYS - keys: e.append(f"missing manifest fields: {sorted(MANIFEST_KEYS - keys)}")
    if keys - MANIFEST_KEYS: e.append(f"unexpected manifest fields: {sorted(keys - MANIFEST_KEYS)}")
    if man.get("manifest_version") != MANIFEST_VERSION: e.append(f"unsupported manifest_version {man.get('manifest_version')!r}")
    status = man.get("status")
    if status not in SNAPSHOT_STATUSES: e.append(f"invalid status {status!r}")
    files = man.get("files")
    if not isinstance(files, list): e.append("files is not a list"); files = []
    cnt = man.get("file_count")
    if not isinstance(cnt, int) or isinstance(cnt, bool) or cnt != len(files): e.append(f"file_count {cnt!r} != {len(files)} entries")
    without = man.get("inputs_without_stored_file")
    if not isinstance(without, list) or not all(isinstance(x, str) for x in without):
        e.append("inputs_without_stored_file is not a list of strings"); without = []
    if status != "OK" or not files or without:
        e.append(f"snapshot is not complete (status={status!r}, files={len(files)}, inputs_without_stored_file={len(without)}): "
                 "this checker accepts only a complete stored snapshot")
    seen = set()
    for i, f in enumerate(files):
        if not isinstance(f, dict): e.append(f"files[{i}] is not an object"); continue
        fk = set(f)
        if MANIFEST_FILE_KEYS - fk: e.append(f"files[{i}] missing fields {sorted(MANIFEST_FILE_KEYS - fk)}")
        if fk - MANIFEST_FILE_KEYS: e.append(f"files[{i}] unexpected fields {sorted(fk - MANIFEST_FILE_KEYS)}")
        e += [f"files[{i}] {x}" for x in raw_entry_problems(f.get("path"), f.get("ticker"), f.get("cik"), f.get("source"),
                                                            f.get("size_bytes"), f.get("sha256"))]
        if isinstance(f.get("path"), str):
            if f["path"] in seen: e.append(f"files[{i}] duplicate path")
            seen.add(f["path"])
    paths = [f.get("path") for f in files if isinstance(f, dict)]
    if all(isinstance(x, str) for x in paths) and paths != sorted(paths): e.append("files are not sorted by path")
    return e


# ---------------------------------------------------------------- checker
class Checker:
    def __init__(self, new: Run, base: Run, expect_date: str, new_sha, base_sha, profile: dict | None, repo_root=None):
        self.new, self.base, self.date, self.profile, self.repo_root = new, base, expect_date, profile, repo_root
        self.shas = {"NEW": new_sha, "BASE": base_sha}
        self.res: list[tuple[str, str, str, str]] = []
        self.diff_report: dict = {}

    def add(self, layer, name, verdict, detail=""): self.res.append((layer, name, verdict, detail))

    # ---------- A: run integrity
    def check_integrity(self):
        try:
            cutoff = (date.fromisoformat(self.date) - timedelta(days=1)).isoformat()
        except ValueError:
            raise InputError(f"--expect-date {self.date!r} is not YYYY-MM-DD")
        self.cutoff = cutoff
        for run in (self.new, self.base):
            L, s, rp = run.label, run.summary, run.repro
            per = {t: (r.get("evaluation_date"), r.get("sec_filed_cutoff_date"), _d(stages(r).get("pit_gating")).get("sec_filed_cutoff_date"))
                   for t, r in run.results.items()}
            bad = sorted(t for t, v in per.items() if v != (self.date, cutoff, cutoff))
            ok = (s.get("evaluation_date") == self.date and run.coverage.get("evaluation_date") == self.date
                  and rp.get("sec_filed_cutoff_date") == cutoff and (rp.get("sec_cutoff_consistency") or {}).get("status") == "MATCH"
                  and not (rp.get("sec_cutoff_consistency") or {}).get("mismatched_tickers") and not bad and run.results)
            self.add(L, "A1 evaluation date / SEC cutoff (summary + every ticker)", "PASS" if ok else "FAIL",
                     f"eval={s.get('evaluation_date')} cutoff={rp.get('sec_filed_cutoff_date')} expected {cutoff}; "
                     f"consistency={(rp.get('sec_cutoff_consistency') or {}).get('status')}; {len(per)} tickers recomputed, bad={bad}")
            gp = rp.get("governance_policy_version")
            v = "PASS" if gp in KNOWN_POLICIES else "REVIEW"      # profile binding of the policy is checked in P0
            self.add(L, "A2 schema + known governance_policy_version", v if s.get("schema_version") == "fundamentals_batch.v2" else "FAIL",
                     f"schema={s.get('schema_version')} policy={gp}" + ("" if v == "PASS" else " (unknown policy: no rule to check against)"))
            orv = (s.get("operational_review") or {})
            self.add(L, "A3 operational_review", "PASS" if orv.get("status") == "PASS" else "FAIL",
                     f"status={orv.get('status')} reasons={orv.get('reasons')}")
            self._check_sha(run)
            self._check_snapshot(run)

    def _check_sha(self, run):
        prov = run.repro.get("provenance") or {}
        vals = {k: prov.get(k) for k in ("github_sha", "checkout_head_sha", "github_workflow_sha")}
        consistent = len(set(vals.values())) == 1 and all(isinstance(x, str) and SHA40.fullmatch(x) for x in vals.values()) \
            and prov.get("commit_sha_consistency") in (None, "MATCH")
        given = (self.shas.get(run.label) or "").strip().lower()
        source = "--sha"
        if not given and self.profile:
            given, source = self.profile["applies_to"]["new" if run.label == "NEW" else "base"]["github_sha"], "profile"
        recorded = vals["github_sha"] or ""
        if not consistent: v = "FAIL"
        elif not given: v = "REVIEW"
        elif SHA40.fullmatch(given): v = "PASS" if given == recorded else "FAIL"
        elif re.fullmatch(r"[0-9a-f]{7,39}", given): v = "REVIEW" if recorded.startswith(given) else "FAIL"
        else: v = "FAIL"
        self.add(run.label, "A4 provenance SHAs (github/checkout/workflow) = expected full SHA", v,
                 f"recorded={recorded} consistency={prov.get('commit_sha_consistency')} expected={given or '(none)'} ({source})"
                 + (" (short SHA: not conclusive)" if given and not SHA40.fullmatch(given) else ""))

    def _check_snapshot(self, run):
        L, snap = run.label, (run.repro.get("sec_snapshot") or {})
        sv = snap.get("self_verification") if isinstance(snap.get("self_verification"), dict) else {}
        ok = (snap.get("status") == "OK" and sv.get("verification_status") == "OK" and sv.get("manifest_file_status") == "WRITTEN_AND_REREAD"
              and sv.get("expected_manifest_sha256") == sv.get("actual_manifest_sha256") == snap.get("manifest_sha256")
              and sv.get("expected_snapshot_content_sha256") == sv.get("actual_snapshot_content_sha256") == snap.get("snapshot_content_sha256")
              and not sv.get("missing_files") and not sv.get("mismatched_files") and not sv.get("validation_errors"))
        self.add(L, "A5 snapshot status + run-time self-verification", "PASS" if ok else "FAIL",
                 f"status={snap.get('status')} self={sv.get('verification_status')}/{sv.get('manifest_file_status')}")
        problems = []
        man = run.manifest
        if man is None:
            self.add(L, "A6 manifest file hashes (independent recompute)", "FAIL", "sec_snapshot_manifest.json missing"); return
        mbytes = run.arc.bytes("sec_snapshot_manifest.json")
        if hashlib.sha256(mbytes).hexdigest() != snap.get("manifest_sha256"): problems.append("manifest file sha256 != summary")
        canon = json.dumps(man, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        if canon != mbytes: problems.append("manifest file is not canonical JSON")
        problems += manifest_structure_problems(man)
        files = [f for f in (man.get("files") if isinstance(man, dict) and isinstance(man.get("files"), list) else []) if isinstance(f, dict)]
        content = [{"path": f.get("path"), "size_bytes": f.get("size_bytes"), "sha256": f.get("sha256")}
                   for f in sorted(files, key=lambda f: str(f.get("path")))]
        csha = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        if csha != snap.get("snapshot_content_sha256"): problems.append("snapshot_content_sha256 recompute mismatch")
        paths = [f.get("path") for f in files]
        self.add(L, "A6 manifest file hashes (independent recompute)", "PASS" if not problems else "FAIL",
                 "; ".join(problems) or f"{len(files)} entries; manifest {snap.get('manifest_sha256')}")
        self._check_raw_inputs(run, files)
        if run.raw is None:
            self.add(L, "A7 raw files match manifest (size + sha256)", "REVIEW", "raw input not supplied"); return
        bad = []
        for f in files:
            p = f.get("path")
            if not isinstance(p, str) or safe_relative_posix(p) or not run.raw.has(p): bad.append(f"missing/unsafe {p}"); continue
            b = run.raw.bytes(p)
            if len(b) != f.get("size_bytes") or hashlib.sha256(b).hexdigest() != f.get("sha256"): bad.append(f"mismatch {p}")
        str_paths = {x for x in paths if isinstance(x, str)}
        extra = sorted(n for n in run.raw.files if n not in str_paths)
        self.add(L, "A7 raw files match manifest (size + sha256)", "FAIL" if bad else "PASS",
                 "; ".join(bad) or f"{len(files)} files match" + (f"; extra raw files not in manifest (ignored): {extra}" if extra else ""))

    def _check_raw_inputs(self, run, files):
        """A8: summary.raw_input <-> manifest, 1:1, per the batch_runner contract (a1a86d7):
        - OK      : companyfacts are loaded before the pipeline runs, so raw_input (non-null path/sha256/size)
                    and a manifest entry are REQUIRED;
        - ERROR   : raw_input present iff the failure happened after the input was loaded (then it must be in
                    the manifest); an ERROR with neither raw_input nor raw_source (fetch failed) is the allowed
                    exception; raw_source without raw_input is impossible (raw_input is written first) -> FAIL;
        - EXCLUDED_NOT_IN_WATCHLIST / EXCLUDED_COMPANY_TYPE: never fetched -> no raw_input, no raw_source,
                    no manifest entry."""
        p, expected = [], {}
        tickers = [r.get("ticker") for r in run.summary["records"]]
        if len(tickers) != len(set(tickers)): p.append("duplicate ticker in summary records")
        for rec in run.summary["records"]:
            t, st_, ri = rec.get("ticker"), rec.get("status"), rec.get("raw_input")
            src = rec.get("raw_source")          # None and absent are the same: "no source recorded"
            if st_ in ("EXCLUDED_NOT_IN_WATCHLIST", "EXCLUDED_COMPANY_TYPE"):
                if ri is not None: p.append(f"{t}: {st_} but has raw_input")
                if src is not None: p.append(f"{t}: {st_} but has raw_source (excluded tickers are never fetched)")
                continue
            if st_ not in ("OK", "ERROR"): p.append(f"{t}: unknown record status {st_!r}"); continue
            if ri is None:
                if st_ == "OK": p.append(f"{t}: OK without raw_input")
                # batch_runner records raw_input BEFORE raw_source, so a recorded source implies a recorded input
                elif src is not None: p.append(f"{t}: ERROR has raw_source {src!r} but no raw_input")
                continue
            if not isinstance(ri, dict): p.append(f"{t}: raw_input incomplete (not an object)"); continue
            # validated on its own: two identical wrong values (e.g. cik None on both sides) must not pass
            bad = raw_entry_problems(ri.get("path"), t, rec.get("cik"), rec.get("raw_source"), ri.get("size_bytes"), ri.get("sha256"))
            if bad: p.append(f"{t}: raw_input incomplete/invalid ({'; '.join(bad)})")
            expected[t] = (ri.get("path"), ri.get("sha256"), ri.get("size_bytes"), rec.get("cik"), rec.get("raw_source"))
        for f in files:
            bad = raw_entry_problems(f.get("path"), f.get("ticker"), f.get("cik"), f.get("source"), f.get("size_bytes"), f.get("sha256"))
            if bad: p.append(f"manifest {f.get('ticker')!r}: invalid ({'; '.join(bad)})")
        # only string tickers can be keys or set members; anything else is recorded as FAIL, never hashed
        odd = [type(f.get("ticker")).__name__ for f in files if not isinstance(f.get("ticker"), str)]
        if odd: p.append(f"manifest entries with a non-string ticker: {len(odd)} ({', '.join(sorted(set(odd)))})")
        mt = [f.get("ticker") for f in files if isinstance(f.get("ticker"), str)]
        if len(mt) != len(set(mt)): p.append(f"duplicate ticker in manifest: {sorted({x for x in mt if mt.count(x) > 1})}")
        got = {f["ticker"]: (f.get("path"), f.get("sha256"), f.get("size_bytes"), f.get("cik"), f.get("source"))
               for f in files if isinstance(f.get("ticker"), str)}
        missing, extra = sorted(set(expected) - set(got)), sorted(set(got) - set(expected), key=str)
        if missing: p.append(f"raw_input tickers missing from manifest: {missing}")
        if extra: p.append(f"manifest tickers without a summary raw_input: {extra}")
        for t in sorted(set(expected) & set(got)):
            if expected[t] != got[t]: p.append(f"{t}: summary raw_input/cik/raw_source != manifest entry")
        n_ok = sum(r.get("status") == "OK" for r in run.summary["records"])
        n_err_no_raw = sum(r.get("status") == "ERROR" and r.get("raw_input") is None and r.get("raw_source") is None
                           for r in run.summary["records"])
        self.add(run.label, "A8 summary.raw_input <-> manifest 1:1 (OK requires raw input)", "PASS" if not p else "FAIL",
                 "; ".join(p[:10]) or f"{len(expected)} raw inputs = {len(got)} manifest entries; OK={n_ok}; ERROR without raw_input (allowed)={n_err_no_raw}")

    def check_stages(self):
        """S1: orchestrator a1a86d7 sets derived_metrics / growth / data_sufficiency unconditionally (status OK)
        for every result it returns, and a batch OK ticker is a returned result -> all three are required."""
        for run in (self.new, self.base):
            bad = []
            for t, r in sorted(run.results.items()):
                s_ = stages(r)
                if (s_.get("derived_metrics") or {}).get("status") != "OK" or stage_items(r, "derived") is None: bad.append(f"{t}.derived_metrics")
                g = (s_.get("growth") or {})
                if g.get("status") != "OK" or stage_items(r, "growth") is None or not isinstance(growth_agg(r), dict): bad.append(f"{t}.growth")
                if (s_.get("data_sufficiency") or {}).get("status") != "OK" or stage_items(r, "data_sufficiency") is None: bad.append(f"{t}.data_sufficiency")
            self.add(run.label, "S1 required stages present (derived_metrics, growth, data_sufficiency; every OK ticker)",
                     "PASS" if not bad else "FAIL", f"missing/malformed: {bad[:12]}" if bad else f"{len(run.results)} tickers x 3 stages")

    def check_schema(self):
        """T0: every OK ticker of each run is type-checked on its own, so identical damage in BASE and NEW
        cannot pass by agreeing with itself. Missing stages are S1's; malformed gate fields are B0's."""
        for run in (self.new, self.base):
            bad = []
            for t, r in sorted(run.results.items()):
                bad += [f"{t}: {x}" for x in schema_problems(r)]
            self.add(run.label, "T0 per-run schema: item objects, string statuses, identity-field and frame types", "PASS" if not bad else "FAIL",
                     f"{len(bad)} problem(s): {bad[:12]}" if bad else f"{len(run.results)} tickers")

    # ---------- P/M: profile binding and dependency-map currency
    def check_profile(self):
        v, d = mapping_drift(self.repo_root)
        self.add("MAP", "M1 checker dependency maps = production registry (static parse)", v, d)
        if self.profile is None:
            self.add("INFO", "P0 profile", "INFO", "--profile none: run-specific numbers and per-ticker expectations disabled; generic checks run")
            return
        at, bad = self.profile["applies_to"], []
        if at["evaluation_date"] != self.date: bad.append(f"profile date {at['evaluation_date']} != --expect-date {self.date}")
        for run, side in ((self.new, "new"), (self.base, "base")):
            prov, want = run.repro.get("provenance") or {}, at[side]
            for k in ("github_run_id", "github_sha"):
                if str(prov.get(k)) != want[k]: bad.append(f"{run.label} {k}={prov.get(k)} but profile expects {want[k]}")
            if run.repro.get("governance_policy_version") != want["governance_policy_version"]:
                bad.append(f"{run.label} policy={run.repro.get('governance_policy_version')} but profile expects {want['governance_policy_version']}")
            given = (self.shas.get(run.label) or "").strip().lower()
            if given and not want["github_sha"].startswith(given): bad.append(f"--{side}-sha {given} contradicts profile {want['github_sha']}")
        self.add("PROF", f"P0 profile {self.profile.get('name')} applies to these runs (run id, full SHA, date, policy)",
                 "PASS" if not bad else "FAIL", "; ".join(bad) or f"BASE {at['base']['github_run_id']} / NEW {at['new']['github_run_id']}")

    # ---------- B: governance (both runs, each by its own policy)
    def check_governance(self):
        prof = self.profile or {}
        for run in (self.new, self.base):
            # B0 gate internal consistency, every ticker
            bad, skipped = [], 0
            policy = run.repro.get("governance_policy_version")
            for t, r in sorted(run.results.items()):
                g = gate(r)
                if g.get("status") == "SKIPPED" and policy == POLICY_NOT_APPLIED:
                    # explicit rule: no gate expected when the run did not apply one; the record itself is exact
                    if set(g) != GATE_SKIPPED_KEYS or not (isinstance(g.get("reason"), str) and g["reason"].strip()):
                        bad.append(f"{t}: SKIPPED gate malformed (needs exactly {sorted(GATE_SKIPPED_KEYS)}, reason a non-empty string)")
                    else:
                        skipped += 1
                    continue
                if g.get("status") != "OK":
                    bad.append(f"{t}: gate status {g.get('status')!r} under policy {policy!r}"); continue
                sp = gate_structure_problems(g)
                if sp: bad.append(f"{t}: gate structure malformed ({'; '.join(sp)})"); continue
                lists = {k: g[k] for k in GATE_LIST_KEYS}
                flat_l = [m for v in lists.values() for m in v]
                if len(flat_l) != len(set(flat_l)): bad.append(f"{t}: a metric is in more than one gate list")
                if sorted(g["unmasked_originals"]) != sorted(lists["withheld"]): bad.append(f"{t}: unmasked_originals keys != withheld")
            self.add(run.label, "B0 gate exact structure, lists disjoint, unmasked_originals = withheld (every ticker)", "PASS" if not bad else "FAIL",
                     "; ".join(bad[:10]) or f"{len(run.results)} tickers" + (f" ({skipped} SKIPPED under {POLICY_NOT_APPLIED})" if skipped else ""))
        # B1 exact gate sets (profile)
        for t, exp in (prof.get("gate") or {}).items():
            g = gate(self.new.results.get(t))
            got = {k: sorted(g.get(k) or []) for k in exp}
            ok = g.get("status") == "OK" and all(got[k] == sorted(v) for k, v in exp.items())
            self.add("PROF", f"B1 {t} gate sets exact (withheld/scoped/nya/no_record)", "PASS" if ok else "FAIL", f"status={g.get('status')} {got}")
        # B2-B4 dependents, by each run's policy
        for run in (self.new, self.base):
            policy = run.repro.get("governance_policy_version")
            for t in sorted(t for t, r in run.results.items() if gate(r).get("withheld")):
                if not gate_wellformed(run.results[t]):
                    self.add(run.label, f"B2-B4 {t} dependents", "FAIL", "not evaluated: gate fields malformed (see B0)"); continue
                if policy == POLICY_V2:
                    self._v2_dependents(run, t, prof if run is self.new else {})
                elif policy == POLICY_V1:
                    self._v1_dependents(run, t)
                else:
                    self.add(run.label, f"B2-B4 {t} dependents", "REVIEW", f"unknown governance policy {policy!r}: no rule to check against")
        new, base = self.new, self.base
        gov_tickers = sorted(t for t, r in new.results.items() if gate(r).get("withheld") and gate_wellformed(r))
        # B5 no governance artefacts outside governed tickers (both runs)
        for run in (new, base):
            gt = {t for t, r in run.results.items() if gate(r).get("withheld")}
            stray = []
            for t, r in run.results.items():
                if t in gt: continue
                if any(_d(gate(r).get("relabelled")).get(s_) for s_ in STAGE_DEPS): stray.append(f"{t}: relabelled non-empty")
                if _d(growth_agg(r)).get("withheld_components"): stray.append(f"{t}: growth.withheld_components non-empty")
                for stage in STAGE_DEPS:
                    for n, item in (stage_items(r, stage) or {}).items():
                        if isinstance(item, dict) and (is_marker(item) or item.get(STATUS_KEY[stage]) == "WITHHELD" or item.get("reason_code") == GOV_REASON):
                            stray.append(f"{t}.{stage}.{n}")
            self.add(run.label, "B5 no WITHHELD/governance marker outside governed tickers", "PASS" if not stray else "FAIL",
                     f"governed={sorted(gt)}; stray={stray[:20]}")
        # B6 non-governance gaps keep their own status/reason (LLY / CRWV)
        gaps = prof.get("non_governance_gaps") or {}
        bad, seen = [], []
        for t, spec in gaps.items():
            r = new.results.get(t)
            for stage, ms in spec.items():
                items = stage_items(r, stage) or {}
                for m, want in ms.items():
                    item = items.get(m)
                    if want == "ABSENT":
                        got = "ABSENT" if m not in items else str((item or {}).get(STATUS_KEY[stage]))
                    else:
                        st_ = (item or {}).get(STATUS_KEY[stage]) if isinstance(item, dict) else "ABSENT"
                        got = st_ if "/" not in want else f"{st_}/{(item or {}).get('reason_code') if isinstance(item, dict) else None}"
                    seen.append(f"{t}.{stage}.{m}={got}")
                    if got != want: bad.append(f"{t}.{stage}.{m}: {got} expected {want}")
        if gaps: self.add("PROF", "B6 non-governance gaps keep their own status (not GOVERNANCE_WITHHELD)", "PASS" if not bad else "FAIL",
                          "; ".join(bad) or "; ".join(seen))
        # B7 GE masking / originals / debt / controls (carried over from v2.2 C5/C6/C8)
        for t in gov_tickers:
            r = new.results[t]; g = gate(r); W = g.get("withheld") or []; nm = metrics(r); uo = _d(g.get("unmasked_originals"))
            p = []
            if sorted(uo) != sorted(W): p.append(f"unmasked_originals keys {sorted(uo)} != withheld")
            for m in W:
                if _d(nm.get(m)).get("status") != "WITHHELD" or _d(nm.get(m)).get("value") is not None: p.append(f"{m} not masked")
            for m in g.get("scoped") or []:
                e = _d(_d(nm.get(m)).get("eligibility"))
                if e.get("governance_status") != "ELIGIBLE" or _d(nm.get(m)).get("status") == "WITHHELD": p.append(f"{m} scoped but not ELIGIBLE/unmasked")
            dm = stage_items(r, "derived") or {}
            for c in ("net_debt", "debt_to_cash"):
                if "total_debt" in W: continue
                if c not in dm: p.append(f"{c} absent")
                elif _d(dm[c]).get("status") == "WITHHELD" or _d(dm[c]).get("reason_code") == GOV_REASON or is_marker(dm[c]):
                    p.append(f"{c} carries governance WITHHELD")
            self.add("NEW", f"B7 {t} normalized masked, originals kept, scoped debt ELIGIBLE, net_debt/debt_to_cash not governance",
                     "PASS" if not p else "FAIL", "; ".join(p) or ", ".join(f"{c}={_d(dm.get(c)).get('status')}" for c in ("net_debt", "debt_to_cash")))


    def _v1_dependents(self, run, t):
        """V1 (DEC055_GATE_V1_RELABEL_PRODUCED_DEPENDENTS): only dependents the stage produced are relabelled."""
        r = run.results[t]; W = list(gate(r).get("withheld") or []); rel = gate(r).get("relabelled") or {}
        problems, notes = [], []
        for stage, deps in STAGE_DEPS.items():
            items = stage_items(r, stage)
            if items is None: problems.append(f"{stage}: stage output missing (required)"); continue
            expected = [n for n, d in deps.items() if set(d) & set(W) and n in items]
            got = list(rel.get(stage) or [])
            if sorted(got) != sorted(expected): problems.append(f"{stage}: relabelled {sorted(got)} expected (produced) {sorted(expected)}")
            for n in expected:
                item, inputs = items[n], [d for d in deps[n] if d in W]
                if not isinstance(item, dict): problems.append(f"{stage}.{n}: not an object"); continue
                if item.get(STATUS_KEY[stage]) != "WITHHELD": problems.append(f"{stage}.{n}: {STATUS_KEY[stage]}={item.get(STATUS_KEY[stage])}")
                if item.get("set_by") is not None: problems.append(f"{stage}.{n}: V2 marker field set_by in a V1 output")
                if stage == "data_sufficiency":
                    if item.get("reason_code") != GOV_REASON: problems.append(f"{stage}.{n}: reason_code={item.get('reason_code')}")
                else:
                    if "value" not in item or item.get("value") is not None: problems.append(f"{stage}.{n}: value not null")
                    if item.get("withheld_inputs") != inputs: problems.append(f"{stage}.{n}: withheld_inputs={item.get('withheld_inputs')} expected {inputs}")
                notes.append(f"{stage}.{n}")
            for n, item in (items or {}).items():
                if n not in expected and isinstance(item, dict) and (is_marker(item) or item.get(STATUS_KEY[stage]) == "WITHHELD"):
                    problems.append(f"{stage}.{n}: WITHHELD/marker outside the produced dependency set")
        ag = growth_agg(r)
        if not isinstance(ag, dict): problems.append("growth.growth aggregate missing (required)")
        elif not isinstance(ag.get("withheld_components") or [], list) or not all(isinstance(x, str) for x in ag.get("withheld_components") or []):
            problems.append("growth.withheld_components malformed (see T0)")
        elif sorted(ag.get("withheld_components") or []) != sorted(rel.get("growth") or []):
            problems.append(f"growth.withheld_components {ag.get('withheld_components')} != relabelled growth")
        self.add(run.label, f"B2-B4 {t} dependents (V1 rule: produced only)", "PASS" if not problems else "FAIL",
                 "; ".join(problems) or "relabelled " + ", ".join(notes))

    def _v2_dependents(self, run, t, prof):
        """V2 (DEC055_GATE_V2_RELABEL_ALL_DEPENDENTS): every dependency-map entry is listed; marker structure is
        validated on its own (never relaxed by what BASE lacked), then cross-checked against BASE when given."""
        r = run.results[t]; rb = self.base.results.get(t) if run is self.new else None
        W = list(gate(r).get("withheld") or []); rel = gate(r).get("relabelled") or {}
        problems, notes = [], []
        for stage, deps in STAGE_DEPS.items():
            items = stage_items(r, stage)
            if items is None: problems.append(f"{stage}: stage output missing (required)"); continue
            expected = [n for n, d in deps.items() if set(d) & set(W)]
            got = list(rel.get(stage) or [])
            if sorted(got) != sorted(expected): problems.append(f"{stage}: relabelled {sorted(got)} expected {sorted(expected)}")
            pexp = ((prof.get("relabelled") or {}).get(t) or {}).get(stage)
            if pexp is not None and sorted(got) != sorted(pexp): problems.append(f"{stage}: relabelled {sorted(got)} != profile {sorted(pexp)}")
            base_items = stage_items(rb, stage) if rb else None
            for n in expected:
                item = (items or {}).get(n)
                p = self._marker_problems(stage, n, item, [d for d in deps[n] if d in W])
                problems += [f"{stage}.{n}: {x}" for x in p]
                if p or not isinstance(item, dict): continue
                if base_items is not None:
                    ok_b4, want = self._b4_against_base(stage, base_items.get(n), item)
                    if want == "V1_NO_ORIGINAL":
                        notes.append(f"{stage}.{n}: BASE V1 marker has no original_status -> only materialized compared (NEW structure checked separately)")
                    if not ok_b4:
                        problems.append(f"{stage}.{n}: materialized/original_status {item.get('materialized')}/{item.get('original_status')} but BASE implies {want}")
                pm = (((prof.get("materialization") or {}).get(t) or {}).get(stage) or {}).get(n)
                if pm is not None and [item.get("materialized"), item.get("original_status")] != list(pm):
                    problems.append(f"{stage}.{n}: materialized/original_status {item.get('materialized')}/{item.get('original_status')} != profile {pm}")
                notes.append(f"{stage}.{n}={item.get('materialized')}/{item.get('original_status')}")
            for n, item in (items or {}).items():
                if n not in expected and (is_marker(item) or (isinstance(item, dict) and item.get(STATUS_KEY[stage]) == "WITHHELD")):
                    problems.append(f"{stage}.{n}: WITHHELD/marker outside the dependency set")
        ag = growth_agg(r)
        gexp = sorted(n for n, d in GROWTH_DEPS.items() if set(d) & set(W))
        if not isinstance(ag, dict): problems.append("growth.growth aggregate missing (required)")
        elif not isinstance(ag.get("withheld_components") or [], list) or not all(isinstance(x, str) for x in ag.get("withheld_components") or []):
            problems.append("growth.withheld_components malformed (see T0)")
        elif sorted(ag.get("withheld_components") or []) != gexp:
            problems.append(f"growth.withheld_components {ag.get('withheld_components')} expected {gexp}")
        self.add(run.label, f"B2-B4 {t} dependents (V2 rule: all listed; marker structure; vs BASE)", "PASS" if not problems else "FAIL",
                 "; ".join(problems) or "; ".join(notes))

    @staticmethod
    def _b4_against_base(stage, prev, item):
        """What the same-date BASE output implies for (materialized, original_status) of a NEW V2 marker."""
        mat, orig = item.get("materialized"), item.get("original_status")
        if is_marker(prev):                                   # BASE is V2 too: markers must carry over unchanged
            want = (prev.get("materialized"), prev.get("original_status"))
            return (mat, orig) == want, f"{want[0]}/{want[1]}"
        if isinstance(prev, dict) and prev.get(STATUS_KEY[stage]) == "WITHHELD":   # V1 marker: the stage had produced it
            if "original_status" not in prev:
                return mat is True, "V1_NO_ORIGINAL"
            return (mat, orig) == (True, prev["original_status"]), f"True/{prev['original_status']}"
        if isinstance(prev, dict):                            # ungated stage output
            return (mat, orig) == (True, prev.get(STATUS_KEY[stage])), f"True/{prev.get(STATUS_KEY[stage])}"
        return (mat, orig) == (False, None), "False/None"    # not produced at BASE

    @staticmethod
    def _marker_problems(stage, name, item, inputs):
        if not isinstance(item, dict): return ["missing (V2 must list every dependent)"]
        p = []
        if item.get(STATUS_KEY[stage]) != "WITHHELD": p.append(f"{STATUS_KEY[stage]}={item.get(STATUS_KEY[stage])}")
        if stage != "data_sufficiency" and ("value" not in item or item.get("value") is not None): p.append("value not null")
        if item.get("reason_code") != GOV_REASON: p.append(f"reason_code={item.get('reason_code')}")
        if item.get("set_by") != GATE_SET_BY: p.append(f"set_by={item.get('set_by')}")
        if item.get("withheld_inputs") != inputs: p.append(f"withheld_inputs={item.get('withheld_inputs')} expected {inputs}")
        mat, orig = item.get("materialized"), item.get("original_status")
        if "original_status" not in item: p.append("original_status key absent")
        if not isinstance(mat, bool): p.append(f"materialized={mat!r} not bool")
        elif mat is False and orig is not None: p.append("materialized=false but original_status set")
        elif mat is True and not isinstance(orig, str): p.append("materialized=true but original_status missing")
        return p

    # ---------- C: coverage
    def check_coverage(self):
        for run in (self.new, self.base):
            mism = []
            nmc = run.coverage.get("normalized_metrics") or {}
            for m, cm in sorted(nmc.items()):
                got = recompute_normalized(run, m)
                c1, c2 = cm.get("r1_data_acquisition_rate") or {}, cm.get("r2_runtime_non_withheld_ok_rate") or {}
                if (c1.get("numerator"), c1.get("denominator"), c2.get("numerator"), c2.get("denominator"), c2.get("calculation_status")) != got:
                    mism.append(f"normalized.{m}")
            rc = recompute_stage_coverage(run)
            for sec in ("derived_metrics", "growth", "data_sufficiency"):
                if rc[sec] != run.coverage.get(sec):
                    a, b = flat(rc[sec]), flat(run.coverage.get(sec) or {})
                    mism += [f"{sec}{k}: coverage {b.get(k)} vs tickers {a.get(k)}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)][:10]
            self.add(run.label, "C1 coverage = independent recompute from ticker files (R1/R2, derived, growth, sufficiency)",
                     "PASS" if not mism and nmc else "FAIL", "; ".join(mism[:15]) or f"{len(nmc)} normalized + stage blocks match")
        n_norm, b_norm = self.new.coverage.get("normalized_metrics"), self.base.coverage.get("normalized_metrics")
        self.add("CMP", "C2 normalized R1/R2 unchanged BASE -> NEW", "PASS" if n_norm == b_norm else "FAIL",
                 "identical" if n_norm == b_norm else "normalized_metrics differ")
        # C3: every stage-coverage change is accounted for by governed tickers only
        gov_t = {t for t, r in self.new.results.items() if gate(r).get("withheld")} | {t for t, r in self.base.results.items() if gate(r).get("withheld")}
        def without(run):
            class _R: pass
            v = _R(); v.summary = {"records": [r for r in run.summary["records"] if r.get("ticker") not in gov_t]}; v.results = run.results
            return recompute_stage_coverage(v)
        allow, _w, accepted, _p = self.allowance()
        base_view = self.base
        if accepted:
            class _B: pass
            base_view = _B(); base_view.summary = self.base.summary
            base_view.results = {t: copy.deepcopy(r) for t, r in self.base.results.items()}
            for t, p in accepted:
                parent, key = parent_and_key(p)
                get_path(base_view.results[t], parent)[key] = get_path(self.new.results[t], p)
        same = without(self.new) == without(base_view)
        self.add("CMP", "C3 stage coverage identical BASE vs NEW once governed tickers are excluded", "PASS" if same else "FAIL",
                 f"governed excluded={sorted(gov_t)}" + (f"; BASE compared with the {len(accepted)} accepted allowance addition(s) of "
                                                         f"{allow['migration_id']} applied (no other change tolerated)" if accepted else ""))
        prof = self.profile or {}
        p = []
        tot = lambda run, m: sum(((recompute_stage_coverage(run)["derived_metrics"].get(m) or {}).get("by_status") or {}).values())
        for m, (b, n) in (prof.get("derived_totals") or {}).items():
            if (tot(self.base, m), tot(self.new, m)) != (b, n): p.append(f"{m}: {tot(self.base, m)}->{tot(self.new, m)} expected {b}->{n}")
        for m, want in (prof.get("derived_status_new") or {}).items():
            got = (recompute_stage_coverage(self.new)["derived_metrics"].get(m) or {}).get("by_status")
            if got != want: p.append(f"{m}: {got} expected {want}")
        if prof.get("derived_totals") or prof.get("derived_status_new"):
            self.add("PROF", "C4 derived totals / status mix vs profile", "PASS" if not p else "FAIL",
                     "; ".join(p) or ", ".join(f"{m} {b}->{n}" for m, (b, n) in prof["derived_totals"].items()))

    # ---------- v2.4 migration allowance (shared by C3 and D2)
    def allowance(self):
        """(allowance, wanted {(ticker, path)}, accepted {(ticker, path)}, problems). Cached.
        accepted = wanted entries that are genuine additions: absent in BASE, present in NEW, value a registered string,
        and the owning component is INVALID in both runs. Anything else stays a data change."""
        if hasattr(self, "_allow_cache"): return self._allow_cache
        allow = (self.profile or {}).get("allowed_added_paths")
        want, accepted, problems = set(), set(), []
        if allow:
            b, at = allow["binding"], self.profile["applies_to"]
            actual = {"evaluation_date": self.date}
            for run, side in ((self.base, "base"), (self.new, "new")):
                prov = run.repro.get("provenance") or {}
                actual[f"{side}_github_run_id"] = str(prov.get("github_run_id")); actual[f"{side}_github_sha"] = str(prov.get("github_sha"))
            declared = {"evaluation_date": at["evaluation_date"], "base_github_run_id": at["base"]["github_run_id"], "base_github_sha": at["base"]["github_sha"],
                        "new_github_run_id": at["new"]["github_run_id"], "new_github_sha": at["new"]["github_sha"]}
            unbound = sorted(k for k in ALLOWANCE_BINDING_KEYS if b[k] != actual[k] or b[k] != declared[k])
            if unbound: problems.append(f"allowance {allow['migration_id']} not bound to this BASE/NEW pair ({', '.join(unbound)})")
            else:
                want = {(e["ticker"], e["path"]) for e in allow["paths"]}
                values = ALLOWANCE_VALUE_SETS[allow["value_set"]]
                for t, p in want:
                    rb, rn = self.base.results.get(t), self.new.results.get(t)
                    if rb is None or rn is None: continue
                    parent = parent_and_key(p)[0]
                    fa, fb = flat(rb), flat(rn)
                    if (p not in fa and p in fb and isinstance(fb[p], str) and fb[p] in values
                            and _d(get_path(rb, parent)).get("status") == "INVALID" and _d(get_path(rn, parent)).get("status") == "INVALID"):
                        accepted.add((t, p))
        self._allow_cache = (allow, want, accepted, problems)
        return self._allow_cache

    # ---------- D: run-to-run diff
    def classify(self, t, a, b, governed: bool):
        """a = BASE doc, b = NEW doc. Returns {class: [paths]}."""
        fa, fb = flat(a), flat(b)
        out: dict[str, list] = {}
        gov_containers = {"/stages/derived_metrics/derived/derived_metrics": DERIVED_DEPS, "/stages/growth/growth/components": GROWTH_DEPS,
                          "/stages/data_sufficiency/sufficiency": GROWTH_DEPS}
        for path in sorted(set(fa) | set(fb)):
            if fa.get(path, "<<absent>>") == fb.get(path, "<<absent>>") and (path in fa) == (path in fb): continue
            parent, key = parent_and_key(path)
            cls = "DATA_CHANGE"
            if key in RUN_TS_KEYS:
                cls = "RUN_METADATA"
            elif key == "frame":
                pa, pb = get_path(a, parent), get_path(b, parent)
                same_id = isinstance(pa, dict) and isinstance(pb, dict) and all(pa.get(k) == pb.get(k) for k in FRAME_IDENTITY) \
                    and set(pa) - {"frame"} == set(pb) - {"frame"}
                cls = "FRAME_METADATA_ONLY" if same_id else "FRAME_WITH_IDENTITY_CHANGE"
            elif governed:
                if path.startswith("/stages/eligibility_gate/relabelled") or path.startswith("/stages/growth/growth/growth/withheld_components"):
                    cls = "GOVERNANCE_MARKER"
                else:
                    for c, deps in gov_containers.items():
                        m = re.match(re.escape(c) + r"/([^/\[]+)(/.*)?$", path)
                        if not m or m.group(1) not in deps: continue
                        item_b = get_path(b, f"{c}/{m.group(1)}")
                        item_a = get_path(a, f"{c}/{m.group(1)}")
                        sub = (m.group(2) or "").lstrip("/")
                        top = re.split(r"[/\[]", sub)[0] if sub else ""
                        if is_marker(item_b) and (item_a is None or top in MARKER_MUTABLE):
                            cls = "GOVERNANCE_MARKER"
                        break
            out.setdefault(cls, []).append(path)
        return out

    def check_diff(self):
        new, base = self.new, self.base
        nt, bt = sorted(new.results), sorted(base.results)
        ns = {r["ticker"]: r.get("status") for r in new.summary["records"]}; bs = {r["ticker"]: r.get("status") for r in base.summary["records"]}
        self.add("CMP", "D1 same tickers and execution statuses", "PASS" if (nt == bt and ns == bs) else "FAIL",
                 f"{len(nt)} OK tickers; added={sorted(set(nt)-set(bt))} removed={sorted(set(bt)-set(nt))}")
        prof_cls = (self.profile or {}).get("diff_classes") or {}
        data_changes, frame_bad, unexpected, summary_rows = [], [], [], {}
        # v2.4 migration allowance: exact, pair-bound, additions only (absent in BASE, present in NEW), registered values only
        allow, want, accepted, allow_problems = self.allowance()
        allow_problems = list(allow_problems); found = set()
        for t in sorted(set(nt) & set(bt)):
            governed = bool(gate(new.results[t]).get("withheld") or gate(base.results[t]).get("withheld"))
            c = self.classify(t, base.results[t], new.results[t], governed)
            if accepted:
                keep = []
                for p in c.get("DATA_CHANGE", []):
                    if (t, p) in accepted: c.setdefault(ALLOWED_ADDITION, []).append(p); found.add((t, p))
                    else: keep.append(p)
                if keep: c["DATA_CHANGE"] = keep
                else: c.pop("DATA_CHANGE", None)
            summary_rows[t] = {k: len(v) for k, v in c.items()}
            self.diff_report[t] = {k: v for k, v in c.items()}
            data_changes += [f"{t}{p}" for p in c.get("DATA_CHANGE", [])]
            frame_bad += [f"{t}{p}" for p in c.get("FRAME_WITH_IDENTITY_CHANGE", [])]
            if prof_cls:
                allowed = set(prof_cls.get(t, prof_cls.get("*", [])))
                got = set(c)
                if got != allowed and not (got < allowed and "RUN_METADATA" in got):
                    unexpected.append(f"{t}: {sorted(got)} expected {sorted(allowed)}")
        if want:
            missing = sorted(f"{t}{p}" for t, p in want - found)
            if missing: allow_problems.append(f"{len(missing)} expected addition(s) not found exactly: {missing[:12]}")
        if allow:
            label = f"D2 ticker JSON: no data change except allowance {allow['migration_id']} (same evaluation date)"
            detail = (f"{len(data_changes)} changed paths (paths only): {data_changes[:12]}; allowed additions {len(found)}/{len(want)}"
                      + (f"; {'; '.join(allow_problems)}" if allow_problems else ""))
            self.add("CMP", label, "PASS" if not data_changes and not allow_problems else "FAIL", detail)
        else:
            self.add("CMP", "D2 ticker JSON: no data change (same evaluation date)", "PASS" if not data_changes else "FAIL",
                     f"{len(data_changes)} changed paths (paths only): {data_changes[:12]}")
        self.add("CMP", "D3 frame drift only where source identity is unchanged", "PASS" if not frame_bad else "FAIL",
                 f"frame+identity changes: {frame_bad[:12]}")
        frames = []
        for t, c in self.diff_report.items():
            for p in c.get("FRAME_METADATA_ONLY", []):
                a, b = get_path(base.results[t], p), get_path(new.results[t], p)
                frames.append(f"{t}{p}: {a!r}->{b!r}" if p in flat(new.results[t]) else f"{t}{p}: {a!r}->(field removed)")
        self.add("INFO", "D4 frame metadata drift (METADATA_ONLY)", "INFO", f"{len(frames)}: {frames[:12]}")
        self.add("INFO", "D5 change classes per ticker", "INFO", json.dumps(summary_rows, sort_keys=True))
        if prof_cls:
            self.add("PROF", "D6 change classes per ticker vs profile", "PASS" if not unexpected else "REVIEW", "; ".join(unexpected) or "as expected")
        # D7 summary.json
        allowed_summary = re.compile(r"^/(generated_at_utc|reproducibility(/.*)?|records\[\d+\]/raw_input/(fetched_at_utc|loaded_at_utc|sha256|size_bytes))$")
        fa, fb = flat(base.summary), flat(new.summary)
        bad = [p for p in sorted(set(fa) | set(fb)) if fa.get(p) != fb.get(p) and not allowed_summary.match(p)]
        self.add("CMP", "D7 summary.json differs only in run metadata / provenance / raw_input hashes", "PASS" if not bad else "FAIL", f"other changes: {bad[:10]}")
        # D8 source snapshot drift
        def man_map(run): return {f["ticker"]: (f.get("sha256"), f.get("size_bytes")) for f in _l(_d(run.manifest).get("files"))
                                  if isinstance(f, dict) and isinstance(f.get("ticker"), str)}
        mn, mb = man_map(new), man_map(base)
        changed = sorted(t for t in set(mn) | set(mb) if mn.get(t) != mb.get(t))
        detail = ", ".join(f"{t} {(mb.get(t) or (None, None))[1]}->{(mn.get(t) or (None, None))[1]} bytes" for t in changed)
        exp = (self.profile or {}).get("raw_changed")
        v = "INFO" if exp is None else ("PASS" if changed == sorted(exp) else "REVIEW")
        self.add("PROF" if exp is not None else "INFO", "D8 source snapshot (raw file) changes", v,
                 f"SOURCE_SNAPSHOT_CHANGED: {detail or 'none'}; recorded separately from normalized values (see D2)")

    # ---------- E: PIT
    def check_pit(self):
        new, cutoff = self.new, self.cutoff
        late = []
        for t, r in new.results.items():
            for p, v in flat(r).items():
                k = parent_and_key(p)[1]
                if k in ("filing_date", "filed") and isinstance(v, str) and v > cutoff: late.append(f"{t}{p}")
        self.add("PIT", "E1 no filing date after cutoff anywhere in NEW ticker results", "PASS" if not late else "FAIL",
                 f"cutoff {cutoff}; late paths={late[:10]}")
        if new.raw is None:
            self.add("PIT", "E2 post-cutoff accessions in raw never reach results", "REVIEW", "raw input not supplied"); return
        post, leaked = {}, []
        for f in _l(_d(new.manifest).get("files")):
            if not isinstance(f, dict): continue                 # malformed entries are FAIL in A6/A8
            t, p = f.get("ticker"), f.get("path")
            if not isinstance(t, str) or not isinstance(p, str) or t not in new.results or not new.raw.has(p): continue
            try:
                facts = json.loads(new.raw.bytes(p).decode("utf-8")).get("facts") or {}
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                raise InputError(f"raw {p}: not valid companyfacts JSON")
            acc = {}
            for ns in facts.values():
                for tag in (ns or {}).values():
                    for unit in ((tag or {}).get("units") or {}).values():
                        for x in unit or []:
                            if isinstance(x, dict) and isinstance(x.get("filed"), str) and x["filed"] > cutoff and x.get("accn"):
                                acc[x["accn"]] = (x.get("form"), x["filed"])
            post[t] = acc
            used = set(ACCN_RE.findall(json.dumps(new.results[t])))
            leaked += [f"{t}:{a}" for a in acc if a in used]
        n_post = sum(len(v) for v in post.values())
        self.add("PIT", "E2 post-cutoff accessions in raw never reach results", "PASS" if not leaked else "FAIL",
                 f"{n_post} post-cutoff accession(s) in raw: " + ", ".join(f"{t}:{a}({fm},{fd})" for t, d in sorted(post.items()) for a, (fm, fd) in d.items())
                 + f"; leaked={leaked}")
        for case in (self.profile or {}).get("post_cutoff_cases") or []:
            t, a = case["ticker"], case["accession"]
            info = (post.get(t) or {}).get(a)
            p = []
            if not info: p.append("accession not found in raw as a post-cutoff filing (case not exercised)")
            elif info[0] != case.get("form"): p.append(f"form {info[0]} != {case.get('form')}")
            if a in set(ACCN_RE.findall(json.dumps(new.results.get(t)))): p.append("accession present in results")
            m = case.get("metric")
            if m:
                nv, bv = metrics(new.results.get(t)).get(m) or {}, metrics(self.base.results.get(t)).get(m) or {}
                keys = ("status", "period_end", "filing_date", "accession", "value")
                if any(nv.get(k) != bv.get(k) for k in keys): p.append(f"{m} differs from BASE")
                if not (isinstance(nv.get("filing_date"), str) and nv["filing_date"] <= cutoff): p.append(f"{m} filing_date not <= cutoff")
                extra = f"; {m} period_end={nv.get('period_end')} filing_date={nv.get('filing_date')} accession={nv.get('accession')}"
            else:
                extra = ""
            self.add("PROF", f"E3 {t} {a} ({case.get('form')}) excluded", "PASS" if not p else "FAIL",
                     ("; ".join(p) or f"in raw filed {info[1]}, absent from results") + extra)

    # ---------- F: leakage
    def check_leak(self):
        for run in (self.new, self.base):
            targets = withheld_targets(run)
            if not targets:
                n_w = sum(len(gate(r).get("withheld") or []) for r in run.results.values())
                self.add(run.label, "F1 no WITHHELD original in coverage/summary (json+md)", "PASS",
                         "no WITHHELD originals" if not n_w else f"{n_w} withheld metric(s) carry no numeric original: nothing to leak")
                continue
            fail, review = [], []
            for name, obj in (("coverage.json", run.coverage), ("summary.json", run.summary)):
                f, r = scan_json(obj, targets); fail += [name + p for p in f]; review += [name + p for p in r]
            for name, txt in (("coverage.md", run.coverage_md), ("summary.md", run.summary_md)):
                f, r = scan_text(txt, targets); fail += [f"{name} {p}" for p in f]; review += [f"{name} {p}" for p in r]
            small = sum(abs(t["value"]) < LARGE_ABS for t in targets)
            v = "FAIL" if fail else ("REVIEW" if review else "PASS")
            self.add(run.label, "F1 no WITHHELD original in coverage/summary (json+md)", v,
                     f"{len(targets)} original amount(s) scanned, {small} small (exact match + context rules); "
                     f"leak locations={fail[:10]}; ambiguous small-value matches={review[:10]}")

    def run(self):
        """Every check group runs in isolation: an unexpected exception on malformed input becomes a FAIL for that
        group (exception type and code location only, never values) and the remaining groups still run.
        The WITHHELD targets are collected first, so the output self-check always has them."""
        try:
            self.cutoff = (date.fromisoformat(self.date) - timedelta(days=1)).isoformat()
        except (TypeError, ValueError):
            raise InputError(f"--expect-date {self.date!r} is not YYYY-MM-DD")
        self.targets = withheld_targets(self.new) + withheld_targets(self.base)
        for name, fn in (("integrity (A)", self.check_integrity), ("profile/mapping (P0/M1)", self.check_profile),
                         ("stages (S1)", self.check_stages), ("schema (T0)", self.check_schema),
                         ("governance (B)", self.check_governance),
                         ("coverage (C)", self.check_coverage), ("run comparison (D)", self.check_diff),
                         ("PIT (E)", self.check_pit), ("leak (F)", self.check_leak)):
            try:
                fn()
            except InputError:
                raise
            except Exception as e:              # noqa: BLE001 -- fail closed, never a traceback
                where = traceback.extract_tb(e.__traceback__)[-1]
                self.add("ERR", f"X0 {name} checks aborted on malformed input", "FAIL",
                         f"{type(e).__name__} in {where.name} (line {where.lineno}); other check groups still ran")
        return self.res


def load_profile(arg):
    """Explicit profile: a JSON file bound to one BASE/NEW pair, or "none". Malformed -> input error."""
    if arg == "none": return None
    try:
        with open(arg, encoding="utf-8") as fh: prof = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InputError(f"profile {arg}: {type(e).__name__}")
    if not isinstance(prof, dict) or prof.get("profile_version") != 1:
        raise InputError(f"profile {arg}: profile_version must be 1")
    at = prof.get("applies_to")
    ok = isinstance(at, dict) and isinstance(at.get("evaluation_date"), str)
    for side in ("base", "new"):
        s = at.get(side) if isinstance(at, dict) else None
        ok = ok and isinstance(s, dict) and isinstance(s.get("github_run_id"), str) and s["github_run_id"].isdigit() \
            and isinstance(s.get("github_sha"), str) and bool(SHA40.fullmatch(s["github_sha"])) \
            and s.get("governance_policy_version") in KNOWN_POLICIES
    if not ok:
        raise InputError(f"profile {arg}: applies_to needs evaluation_date and base/new github_run_id, full github_sha, known policy")
    if "allowed_added_paths" in prof:
        problems = allowance_problems(prof["allowed_added_paths"])
        if problems: raise InputError(f"profile {arg}: allowed_added_paths invalid ({problems[0]})")
    return prof


def allowance_problems(al) -> list[str]:
    """Structural validation of a v2.4 migration allowance. Any deviation is an input error (fail closed)."""
    if not isinstance(al, dict) or set(al) != ALLOWANCE_KEYS: return [f"keys must be exactly {sorted(ALLOWANCE_KEYS)}"]
    p = []
    if not isinstance(al["migration_id"], str) or not al["migration_id"].strip(): p.append("migration_id must be a non-empty string")
    b = al["binding"]
    if not isinstance(b, dict) or set(b) != ALLOWANCE_BINDING_KEYS: p.append(f"binding keys must be exactly {sorted(ALLOWANCE_BINDING_KEYS)}")
    else:
        if not is_iso_date(b["evaluation_date"]): p.append("binding.evaluation_date must be YYYY-MM-DD")
        for side in ("base", "new"):
            rid, sha = b[f"{side}_github_run_id"], b[f"{side}_github_sha"]
            if not (isinstance(rid, str) and rid.isdigit()): p.append(f"binding.{side}_github_run_id must be a digit string")
            if not (isinstance(sha, str) and SHA40.fullmatch(sha)): p.append(f"binding.{side}_github_sha must be a full 40-hex SHA")
    if al["value_set"] not in ALLOWANCE_VALUE_SETS: p.append(f"value_set must be one of {sorted(ALLOWANCE_VALUE_SETS)}")
    paths = al["paths"]
    if not isinstance(paths, list) or not paths: p.append("paths must be a non-empty list")
    else:
        seen = set()
        rx = ALLOWANCE_PATH_RE.get(al["value_set"]) if isinstance(al["value_set"], str) else None
        for e in paths:
            if not isinstance(e, dict) or set(e) != {"ticker", "path"}: p.append("each path entry must be exactly {ticker, path}"); continue
            tk, pa = e["ticker"], e["path"]
            if not (isinstance(tk, str) and TICKER_RE.fullmatch(tk)): p.append(f"bad ticker {tk!r}"); continue
            if not (isinstance(pa, str) and rx and rx.fullmatch(pa)): p.append(f"{tk}: path not an exact allowed leaf: {pa!r}"); continue
            if (tk, pa) in seen: p.append(f"duplicate entry {tk}{pa}")
            seen.add((tk, pa))
    return p


def static_dict(path, name):
    """Literal value of a top-level `name = {...}` assignment, by AST (no import, no execution)."""
    with open(path, encoding="utf-8") as fh: tree = ast.parse(fh.read(), filename=path)
    found = [n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    found += [n.value for n in tree.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name and n.value]
    if len(found) != 1: raise ValueError(f"{name}: {len(found)} top-level assignments")
    return ast.literal_eval(found[0])


def mapping_drift(repo_root):
    """(verdict, detail). PASS only when both production maps parse and equal the checker's copies."""
    if not repo_root:
        return "REVIEW", f"production registry not available (no --repo-root); maps are the copy of {MAPPING_SOURCE['commit'][:7]}"
    path = os.path.join(repo_root, *MAPPING_SOURCE["file"].split("/"))
    diffs = []
    for local_name, prod_name in MAPPING_SOURCE["names"].items():
        local = globals()[local_name]
        try:
            prod = static_dict(path, prod_name)
        except (OSError, SyntaxError, ValueError, UnicodeDecodeError) as e:
            return "REVIEW", f"{MAPPING_SOURCE['file']}: {prod_name} not verifiable ({type(e).__name__}: {e})"
        if not isinstance(prod, dict):
            return "REVIEW", f"{MAPPING_SOURCE['file']}: {prod_name} is not a dict literal"
        norm = {k: tuple(v) if isinstance(v, (list, tuple)) else v for k, v in prod.items()}
        for k in sorted(set(norm) | set(local)):
            if norm.get(k) != local.get(k):
                diffs.append(f"{prod_name}[{k!r}]: production {norm.get(k)} vs checker {local.get(k)}")
    if diffs:
        return "REVIEW", f"checker maps are stale vs {MAPPING_SOURCE['file']} (copied at {MAPPING_SOURCE['commit'][:7]}); update {', '.join(MAPPING_SOURCE['names'])}: " + "; ".join(diffs)
    return "PASS", f"{', '.join(MAPPING_SOURCE['names'].values())} equal to {MAPPING_SOURCE['file']} in {repo_root}"


def default_repo_root():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return here if os.path.isfile(os.path.join(here, *MAPPING_SOURCE["file"].split("/"))) else None


def render(res, header):
    w = max(len(r[1]) for r in res)
    lines = [header] + [f"[{v:6}] {layer:4} {n.ljust(w)}  {d}" for layer, n, v, d in res]
    nf = sum(r[2] == "FAIL" for r in res); nr = sum(r[2] == "REVIEW" for r in res)
    overall = "FAIL" if nf else ("REVIEW" if nr else "PASS")
    lines.append(f"\nOVERALL {overall}: PASS {sum(r[2] == 'PASS' for r in res)} / FAIL {nf} / REVIEW {nr} / INFO {sum(r[2] == 'INFO' for r in res)}")
    return "\n".join(lines), overall


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--new", required=True); ap.add_argument("--base", required=True)
    ap.add_argument("--new-raw"); ap.add_argument("--base-raw")
    ap.add_argument("--expect-date", required=True)
    ap.add_argument("--new-sha"); ap.add_argument("--base-sha")
    ap.add_argument("--profile", required=True, help="profile JSON bound to this BASE/NEW pair, or 'none'")
    ap.add_argument("--repo-root", help="checkout holding the production registry (default: this script's checkout)")
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)
    try:
        prof = load_profile(a.profile)
        new = Run(Archive(a.new, "summary.json"), Archive(a.new_raw, None) if a.new_raw else None, "NEW")
        base = Run(Archive(a.base, "summary.json"), Archive(a.base_raw, None) if a.base_raw else None, "BASE")
        ck = Checker(new, base, a.expect_date, a.new_sha, a.base_sha, prof, a.repo_root or default_repo_root())
        res = ck.run()
    except InputError as e:
        print(f"[INPUT ERROR] {e}"); return 3
    except Exception as e:                       # noqa: BLE001 -- last line of defence: FAIL, never a traceback
        where = traceback.extract_tb(e.__traceback__)[-1]
        print(f"[FAIL] checker could not complete: {type(e).__name__} in {where.name} (line {where.lineno})")
        print("\nOVERALL FAIL: checker aborted"); return 1
    header = (f"{VERSION}  profile={(prof or {}).get('name', 'none')}  expect-date={a.expect_date}\n"
              f"NEW  input sha256={new.arc.zip_sha256 or 'directory'} raw={new.raw.zip_sha256 if new.raw else None}\n"
              f"BASE input sha256={base.arc.zip_sha256 or 'directory'} raw={base.raw.zip_sha256 if base.raw else None}")
    text, overall = render(res, header)
    # self-check: the report itself must not contain a WITHHELD original value
    if report_leaks(text, ck.targets):
        print("[FAIL] report self-check: output would contain a WITHHELD original value; report suppressed"); return 1
    print(text)
    if a.json_out:
        rep = {"version": VERSION, "overall": overall, "results": [dict(zip(("layer", "check", "verdict", "detail"), r)) for r in res],
               "diff_paths": ck.diff_report}
        blob = json.dumps(rep, indent=1, sort_keys=True)
        if report_leaks(blob, ck.targets):
            print("[FAIL] json report self-check failed; not written"); return 1
        with open(a.json_out, "w", encoding="utf-8") as fh: fh.write(blob)
    return {"PASS": 0, "FAIL": 1, "REVIEW": 2}[overall]


if __name__ == "__main__":
    sys.exit(main())
