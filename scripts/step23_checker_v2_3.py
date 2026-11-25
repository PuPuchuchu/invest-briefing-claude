"""STEP 23 Checker v2.9 (read-only): DEC055 gate materialization + same-date batch pair comparison.

v2.12 (F5, SEC fy-label collision): expected_patch migration F5_FY_LABEL_COLLISION -- a recovered latest quarter may
move the four quarterly YoY components (revenue / operating income / EPS / FCF: status, value, stars, reason, reason
code, periods, source, EPS previous_value_basis), the Growth aggregate, warnings, limitations, their data-sufficiency
rows and the matching coverage counters. Not allowed: revenue_cagr_3y, the fcf_yoy input-scope block, any other
stage, summary.json, governed tickers. Ops add / remove / replace. A pair without this migration prints v2.11 output.

v2.9 (P2 Quality v0.2 stage; design r2-r5): expected_patch migration P2_QUALITY_V02_STAGE -- add-only, a grammar
generated from P2_LIST_MAXLEN, empty containers only at exact registered paths, summary.json entries (D7 then accepts
exactly the realized registered summary leaves), and governed tickers may carry entries only inside /stages/quality/.
New check group Q (emitted only when a run carries Quality output, so a pre-P2 pair prints exactly v2.8's lines):
Q0 P2 run has an evaluated non-FINANCIAL ticker; Q1 exact structure; Q2 no Quality stage ERROR; Q3 internal invariants
(weights, score, stars, status/reason table, E1); Q4 periods recompute incl. debt-date provenance (I9/I9-D) and strict
dates; Q5 Q-LEAK (dependency/provenance rules + the F1 amount scan as auxiliary); Q6 coverage Quality block recompute;
Q7 summary Quality fields = ticker files, joined by ticker. Every Q rule re-derives from the ticker inputs; production
Quality code is never imported.

v2.4 (STEP 24.3 A-0): optional profile field "allowed_added_paths" lets D2 accept an EXACT, pair-bound list of
newly added leaf paths (migration allowance). Without that field D2 behaves exactly as in v2.3.
v2.8 (STEP 24.6 Phase A, timestamp exclusions by exact path): run-varying timestamps are no longer recognized by leaf
NAME anywhere. Ticker JSON: only /stages/normalization/normalized/normalized_at is RUN_METADATA (D2, every mode);
coverage.json: only /generated_at_utc is excluded, now also in expected_patch mode's D9. The same key name anywhere
else is data.
v2.7 (STEP 24.6 D9 patchless gap): D9 runs in every D2 mode. Without expected_patch (allowance mode and the no-change
mode) it is the strict form: every coverage.json leaf outside the delegated stage blocks (/derived_metrics, /growth,
/data_sufficiency: recomputed by C1, compared by C3) must be identical BASE -> NEW (type-strict, additions and removals
included); only the exact run-varying path /generated_at_utc is excluded. expected_patch mode's D9 is unchanged.
v2.6 (STEP 24.6-N P0b): a governed ticker (withheld metrics) may carry expected_patch entries ONLY for the
component-scoped leaves GOVERNED_PATCH_SCOPE explicitly lists for that migration, and only when the component's
declared input metrics are not withheld in either run; Growth aggregate / warnings / limitations, derived metrics and
every other path stay forbidden on governed tickers (fail closed, input-independent of GROWTH_DEPS gaps).
v2.5 (STEP 24.4 P0): optional profile field "expected_patch" lets D2/C3 (and a new D9 for coverage.json) accept an
EXACT, pair-bound list of leaf changes {file, path, op, base, new} whose paths come from a per-migration_id leaf
allowlist; BASE and NEW must both equal the registered values. Without that field the output is exactly v2.4's.

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

VERSION = "checker_v2.12"
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
# Mirror of production growth.GROWTH_REASON_CODES (STEP 24.3 A-1; A-2 added NON_FINITE_RESULT; STEP 24.6-N added
# SAME_FILING_COMPARATIVE_UNAVAILABLE). A production test
# asserts equality. Constant only: no checker decision logic depends on the individual codes.
GROWTH_REASON_CODES = frozenset({"NON_NUMERIC_INPUT", "NON_FINITE_INPUT", "NONPOSITIVE_PRIOR_BASE", "NONPOSITIVE_CURRENT",
                                 "ZERO_PRIOR_BASE", "NEGATIVE_END_VALUE", "INVALID_PERIOD_PARAMETER", "NON_FINITE_RESULT",
                                 "SAME_FILING_COMPARATIVE_UNAVAILABLE"})
ALLOWANCE_VALUE_SETS = {"GROWTH_REASON_CODES": GROWTH_REASON_CODES}
# exact path grammar per value set: one Growth component's reason_code leaf; no wildcard / prefix forms exist
ALLOWANCE_PATH_RE = {"GROWTH_REASON_CODES": re.compile(
    r"^/stages/growth/growth/components/(revenue_yoy|operating_income_yoy|eps_yoy|fcf_yoy|revenue_cagr_3y)/reason_code$")}
ALLOWANCE_KEYS = {"migration_id", "binding", "value_set", "paths"}
ALLOWANCE_BINDING_KEYS = {"evaluation_date", "base_github_run_id", "base_github_sha", "new_github_run_id", "new_github_sha"}
ALLOWED_ADDITION = "ALLOWED_ADDITION"
# ---------------------------------------------------------------- v2.7 strict D9 (no expected_patch)
# The only run-varying coverage.json leaf: batch_coverage.build_coverage() writes "generated_at_utc" at the top level
# (datetime.now) and batch_runner writes the dict unchanged. Exact paths only: the same key name anywhere else is data.
COVERAGE_RUN_TS_PATHS = frozenset({"/generated_at_utc"})
# v2.8: the only run-varying ticker-JSON leaf: sec_normalizer writes normalized/normalized_at = datetime.now(). (The
# identity stage's normalized_at is the evaluation date, i.e. not run-varying, so it stays data.)
TICKER_RUN_TS_PATHS = frozenset({"/stages/normalization/normalized/normalized_at"})
# Blocks recomputed from the ticker files by C1 (every run) and compared across runs by C3 (governed tickers excluded,
# allowance / patch leaves applied). Matched by whole path segment, never by string prefix.
D9_DELEGATED_BLOCKS = ("/derived_metrics", "/growth", "/data_sufficiency")


def d9_delegated(path: str) -> bool:
    return any(path == d or path.startswith(d + "/") or path.startswith(d + "[") for d in D9_DELEGATED_BLOCKS)


def strict_coverage_changes(base_cov, new_cov) -> list[str]:
    """coverage.json leaf paths that differ BASE -> NEW (type-strict; added and removed leaves included), outside the
    delegated stage blocks and the exact run-varying paths."""
    cb, cn = flat(base_cov), flat(new_cov)
    return sorted(p for p in set(cb) | set(cn)
                  if p not in COVERAGE_RUN_TS_PATHS and not d9_delegated(p) and not same_leaf(flat_get(cb, p), flat_get(cn, p)))


# ---------------------------------------------------------------- v2.5 expected patch (D2 / C3 / D9)
# Leaf grammar = this checker's flat() paths: "/key" for object members, "[i]" for list items; a leaf is a scalar or an
# EMPTY container. Patch values are therefore scalars, or [] / {} at the few container paths below; a non-empty object
# or list can never be a patch value (no parent replacement). ABSENT is a tagged object, never JSON null.
ABSENT_TAG = {"$absent": True}
PATCH_KEYS = {"migration_id", "binding", "entries"}
PATCH_ENTRY_KEYS = {"file", "path", "op", "base", "new"}
PATCH_OPS = ("add", "remove", "replace")
COVERAGE_FILE = "coverage.json"
PATCH_TICKER_FILE_RE = re.compile(r"^([A-Z0-9][A-Z0-9.\-]{0,9})\.json$")
_SY = "(revenue_yoy|operating_income_yoy)"          # the two metrics calculate_standard_yoy() serves
PATCH_MIGRATIONS = {
    # STEP 24.4 P1: calculate_standard_yoy() previous < 0 -> INVALID / NONPOSITIVE_PRIOR_BASE (and its cascade)
    "STEP24_4_NEGATIVE_PRIOR_BASE": {
        "ticker": re.compile(
            r"^/stages/growth/growth/components/" + _SY + r"/(status|value|stars|reason|reason_code|current_period_end"
            r"|previous_period_end|source\[[01]\])$"
            r"|^/stages/growth/growth/growth/(status|score|stars|available_components|valid_components|coverage)$"
            r"|^/stages/growth/growth/warnings(\[[0-2]\])?$"
            r"|^/stages/growth/growth/limitations(/" + _SY + r")?$"
            r"|^/stages/data_sufficiency/sufficiency/" + _SY + r"/(calculation_status|detail)$"),
        "coverage": re.compile(
            r"^/growth/components/" + _SY + r"/by_status/(OK|INVALID)$"
            r"|^/growth/components/" + _SY + r"/by_reason_code(/NONPOSITIVE_PRIOR_BASE)?$"
            r"|^/growth/aggregate_status_counts/(OK|MISSING)$"
            r"|^/data_sufficiency/" + _SY + r"/by_calculation_status/(OK|INVALID)$"
            r"|^/data_sufficiency/" + _SY + r"/by_reason_code(/NO_REASON_CODE)?$"),
    },
    # STEP 24.6-N: eps_yoy prior-year value = same-filing comparative (per-share metrics only), fail-closed when it is
    # unavailable, plus the eps_yoy-only provenance leaf previous_value_basis. Period/source leaves are listed because an
    # OK -> INVALID (fail-closed) transition removes them; every registered leaf still needs its exact base/new value.
    "STEP24_6_N_SAME_FILING_COMPARATIVE": {
        "ticker": re.compile(
            r"^/stages/growth/growth/components/eps_yoy/(status|value|stars|reason|reason_code|previous_value_basis"
            r"|current_period_end|previous_period_end|source\[[01]\])$"
            r"|^/stages/growth/growth/growth/(status|score|stars|available_components|valid_components|coverage)$"
            r"|^/stages/growth/growth/warnings(\[[0-2]\])?$"
            r"|^/stages/growth/growth/limitations(/eps_yoy)?$"
            r"|^/stages/data_sufficiency/sufficiency/eps_yoy/(calculation_status|detail)$"),
        "coverage": re.compile(
            r"^/growth/components/eps_yoy/by_status/(OK|INVALID)$"
            r"|^/growth/components/eps_yoy/by_reason_code(/SAME_FILING_COMPARATIVE_UNAVAILABLE|/NONPOSITIVE_PRIOR_BASE)?$"
            r"|^/growth/aggregate_status_counts/(OK|MISSING)$"
            r"|^/data_sufficiency/eps_yoy/by_calculation_status/(OK|INVALID)$"
            r"|^/data_sufficiency/eps_yoy/by_reason_code(/NO_REASON_CODE)?$"),
    },
}
# v2.6 (STEP 24.6-N P0b): component-scoped expected_patch on governed tickers. Per migration: component -> the
# metrics it is computed from (explicit; a component absent here is never allowed, even if absent from GROWTH_DEPS).
# An entry on a governed ticker is accepted only if its path is one of the component's container leaves below, the
# path is in the migration grammar (checked separately), and none of the declared inputs is withheld in either run.
GOVERNED_PATCH_SCOPE = {
    "STEP24_6_N_SAME_FILING_COMPARATIVE": {"eps_yoy": ("diluted_eps",)},
}
GOVERNED_PATCH_CONTAINERS = ("/stages/growth/growth/components/", "/stages/data_sufficiency/sufficiency/")


def governed_entry_problem(migration_id, path, withheld) -> str | None:
    """None if a governed ticker may carry this expected_patch entry (v2.6), else the reason (fail closed).
    v2.9: a migration listed in GOVERNED_PATCH_STAGE allows exactly the leaves of its own new stage (P2: the Quality
    stage); what those leaves may contain on a governed ticker is checked independently by Q5 (Q-LEAK)."""
    stage_prefix = GOVERNED_PATCH_STAGE.get(migration_id) if isinstance(migration_id, str) else None
    if stage_prefix is not None:
        if path.startswith(stage_prefix): return None
        return f"governed ticker: only {stage_prefix} leaves may carry expected_patch entries for this migration"
    scope = GOVERNED_PATCH_SCOPE.get(migration_id) if isinstance(migration_id, str) else None
    if not scope:
        return "governed ticker (withheld metrics) cannot carry expected_patch entries for this migration"
    for container in GOVERNED_PATCH_CONTAINERS:
        if not path.startswith(container):
            continue
        rest = path[len(container):]
        comp, sep, leaf = rest.partition("/")
        if not sep or not leaf or comp not in scope:
            break
        if not re.fullmatch(r"[a-z_]+(\[[0-9]\])?", leaf):
            break
        hit = sorted(set(scope[comp]) & set(withheld))
        if hit:
            return f"governed ticker: {comp} depends on withheld metric(s) {hit}"
        return None
    return "governed ticker: only component-scoped leaves of an independent component may carry expected_patch entries"


# container paths (flat leaf only while empty): the only places [] / {} may appear as a patch value
PATCH_CONTAINER_LEAF = {"warnings": [], "by_reason_code": {}, "limitations": {}}
EXPECTED_PATCH = "EXPECTED_PATCH"
MAX_MEMBER = 512 * 1024 * 1024


# ---------------------------------------------------------------- v2.9 (P2): Quality v0.2 stage -- constants and grammar
# Every value below is the checker's own copy of the P2 design (v3 + v3.1, Checker design r2-r5). Production is never
# imported; where a value mirrors a production literal, the source is named.
Q_SCHEMA_VERSION = "quality_v0.2"
Q_STAGE_PREFIX = "/stages/quality/"
Q_METRICS = ("operating_margin", "net_margin", "fcf_margin", "fcf_conversion", "net_debt_to_fcf", "debt_to_cash",
             "profit_to_cash_consistency")
Q_INDEP = Q_METRICS[:6]
Q_INFO = {"profit_to_cash_consistency": "fcf_conversion"}
# per Quality metric: (gated normalized inputs, derived inputs) -- design v3 D1-D7
Q_INPUTS = {
    "operating_margin": (("operating_income", "revenue"), ("operating_margin",)),
    "net_margin": (("net_income", "revenue"), ("net_margin",)),
    "fcf_margin": (("cfo", "capex", "revenue"), ("fcf", "fcf_margin")),
    "fcf_conversion": (("net_income", "cfo", "capex"), ("fcf",)),
    "net_debt_to_fcf": (("total_debt", "cash", "cfo", "capex"), ("net_debt", "fcf")),
    "debt_to_cash": (("total_debt", "cash"), ("debt_to_cash",)),
    "profit_to_cash_consistency": (("net_income", "cfo", "capex", "revenue"), ("fcf_margin", "net_margin")),
}
Q_USES_FLOW = {m: m != "debt_to_cash" for m in Q_METRICS}
Q_BASE_WEIGHTS = {"operating_margin": 0.20, "net_margin": 0.10, "fcf_margin": 0.20, "fcf_conversion": 0.20,
                  "net_debt_to_fcf": 0.15, "debt_to_cash": 0.05}
Q_WEIGHTS = {m: Q_BASE_WEIGHTS[m] / sum(Q_BASE_WEIGHTS.values()) for m in Q_INDEP}
Q_DIMS = {"profitability": ("operating_margin", "net_margin", "fcf_margin"), "cash_conversion": ("fcf_conversion",),
          "leverage": ("net_debt_to_fcf", "debt_to_cash")}
Q_REASON_CODES = frozenset({
    "NONPOSITIVE_NET_INCOME", "NONPOSITIVE_FCF_WITH_NET_DEBT", "NONPOSITIVE_FCF_WITH_NONPOSITIVE_NET_DEBT",
    "FLOW_PERIOD_MISMATCH", "FLOW_UNIT_MISMATCH", "PERIOD_END_AFTER_EVALUATION_DATE", "NON_FINITE_INPUT",
    "NON_FINITE_RESULT", "INPUT_MISSING", "INPUT_INCOMPLETE", "INPUT_WITHHELD", "INPUT_INVALID",
    "UPSTREAM_REASON_UNSPECIFIED", "INSUFFICIENT_INDEPENDENT_COMPONENTS", "COMPANY_TYPE_FINANCIAL"})
Q_FLAG = "NONPOSITIVE_FCF_LEVERAGE_UNASSESSED"
Q_E1 = "NONPOSITIVE_FCF_WITH_NET_DEBT"
Q_NM_CODE = {"fcf_conversion": "NONPOSITIVE_NET_INCOME", "profit_to_cash_consistency": "NONPOSITIVE_NET_INCOME",
             "net_debt_to_fcf": "NONPOSITIVE_FCF_WITH_NONPOSITIVE_NET_DEBT"}
Q_LOCAL_INVALID = {m: frozenset({"NON_FINITE_INPUT", "PERIOD_END_AFTER_EVALUATION_DATE"}
                                | ({"FLOW_PERIOD_MISMATCH", "FLOW_UNIT_MISMATCH"} if Q_USES_FLOW[m] else set())
                                | ({"NON_FINITE_RESULT"} if m in ("fcf_conversion", "net_debt_to_fcf", "profit_to_cash_consistency") else set()))
                   for m in Q_METRICS}
# upstream status -> class; class precedence; class -> (component status, reason prefix)   (design v3.1 / r3 §6)
Q_UPSTREAM_ORDER = ("WITHHELD", "INVALID", "INCOMPLETE", "MISSING")
Q_UPSTREAM_MAP = {"WITHHELD": ("WITHHELD", "INPUT_WITHHELD"), "INVALID": ("INVALID", "INPUT_INVALID"),
                  "INCOMPLETE": ("MISSING", "INPUT_INCOMPLETE"), "MISSING": ("MISSING", "INPUT_MISSING")}
Q_COMPONENT_STATUSES = ("OK", "MISSING", "INVALID", "WITHHELD", "NOT_MEANINGFUL")
Q_FLOW_INPUTS = ("revenue", "operating_income", "net_income", "cfo", "capex")
Q_FLOW_SIG = ("period_start", "period_end", "fy", "fp", "period_kind", "period_class")
Q_LIQUIDITY_BASIS = "CASH_AND_CASH_EQUIVALENTS_ONLY"
Q_FLOW_BASIS = "ANNUAL_FY"
# company_type_registry.COMPANY_TYPE_VALUES = {"NON_FINANCIAL", "FINANCIAL"}; Quality is evaluated for the former only
Q_EVALUATED_COMPANY_TYPE = "NON_FINANCIAL"
# metric_eligibility_registry.GOVERNED_METRICS intersected with Quality's normalized inputs (Checker design r5 §2)
Q_GOVERNED_INPUTS = ("operating_income", "cfo", "capex", "total_debt")
Q_DEBT_COMPONENTS = ("current_debt", "noncurrent_debt")
# v2.11 (M2): values that were literals inside Checker logic, named so M2 can compare them with production
Q_MIN_RATED = 3                 # quality.QUALITY_V02_MIN_RATED
Q_MIN_RATED_PLUS_NM = 4         # quality.QUALITY_V02_MIN_RATED_PLUS_NOT_MEANINGFUL
Q_STAR_BANDS = ((4.5, 5), (3.5, 4), (2.5, 3), (1.5, 2))   # quality._stars_from_rating: `if rating >= c: return n`, in order
Q_STAR_FLOOR = 1                # its final `return`
Q_COMPANY_TYPES = frozenset({"NON_FINANCIAL", "FINANCIAL"})   # company_type_registry.COMPANY_TYPE_VALUES
Q_TOL = 1e-12          # Quality v0.2 records unrounded floats; production's own validator uses the same tolerance
Q_LIMITATIONS = ("liquidity_basis", "roic", "roe", "relative_evaluation", "historical_evaluation", "staleness_policy")
Q_KEYS_COMMON = frozenset({"metric", "status", "value", "rating", "stars", "weight", "reason_code", "upstream_reason_code",
                           "upstream_inputs", "role"})
Q_KEYS_EXTRA = {"profit_to_cash_consistency": frozenset({"duplicate_of"}),
                "net_debt_to_fcf": frozenset({"net_debt_sign", "liquidity_basis", "mixed_period_ratio", "cash_flow_gap_days",
                                              "debt_flow_gap_days", "balance_flow_gap_days"}),
                "debt_to_cash": frozenset({"liquidity_basis"})}
Q_UPSTREAM_KEYS = frozenset({"metric", "status", "reason_code"})
Q_ELEMENT_KEYS = frozenset({"status", "reason_code", "score", "stars", "scored_weight_fraction", "normalized_score_weights",
                            "rated_count", "not_meaningful_count", "independent_total", "assessed_dimensions",
                            "unavailable_components", "flags"})
Q_UNAVAILABLE_KEYS = frozenset({"metric", "status", "reason_code", "upstream_reason_code"})
Q_PERIOD_KEYS = frozenset({"flow_basis", "flow_inputs_consistent", "flow_period_start", "flow_period_end", "flow_age_days",
                           "flow_accession_consistent", "cash_as_of", "cash_age_days", "debt_as_of", "debt_age_days",
                           "balance_sheet_as_of", "balance_dates_consistent"})
Q_TOP_KEYS = frozenset({"schema_version", "ticker", "company_type", "evaluation_date", "element", "components", "periods",
                        "limitations"})
Q_SUMMARY_KEYS = ("_available", "schema_version", "status", "reason_code", "score", "stars", "scored_weight_fraction",
                  "rated_count", "not_meaningful_count", "leverage_assessment", "flags", "stage_status", "error_type")
Q_ERROR = "ERROR"
# Single source of list-length limits for the P2 grammar AND the pre-registration generator (r2 §4.1, r5 §3.5).
P2_LIST_MAXLEN = {"upstream_inputs": {m: len(Q_INPUTS[m][0]) + len(Q_INPUTS[m][1]) for m in Q_METRICS},
                  "unavailable_components": len(Q_INDEP), "flags": 1, "debt_dates": 2}


def _alt(xs) -> str:
    return "(" + "|".join(re.escape(x) for x in xs) + ")"


def _idx(n: int) -> str:
    assert 1 <= n <= 10
    return r"\[[0-%d]\]" % (n - 1)


def _p2_grammar() -> dict:
    q = r"/stages/quality/quality"
    tick = [r"/stages/quality/status",
            q + "/" + _alt(("schema_version", "ticker", "company_type", "evaluation_date")),
            q + "/element/" + _alt(("status", "reason_code", "score", "stars", "scored_weight_fraction", "normalized_score_weights",
                                    "rated_count", "not_meaningful_count", "independent_total")),
            q + "/element/normalized_score_weights/" + _alt(Q_INDEP),
            q + "/element/assessed_dimensions/" + _alt(Q_DIMS),
            q + "/element/unavailable_components(" + _idx(P2_LIST_MAXLEN["unavailable_components"]) + "/"
              + _alt(sorted(Q_UNAVAILABLE_KEYS)) + ")?",
            q + "/element/flags(" + _idx(P2_LIST_MAXLEN["flags"]) + ")?",
            q + "/periods/" + _alt(sorted(Q_PERIOD_KEYS - {"debt_as_of", "debt_age_days"})),
            q + "/periods/(debt_as_of|debt_age_days)(" + _idx(P2_LIST_MAXLEN["debt_dates"]) + ")?",
            q + "/limitations/" + _alt(Q_LIMITATIONS) + "/(status|reason)"]
    for m in Q_METRICS:
        keys = (Q_KEYS_COMMON | Q_KEYS_EXTRA.get(m, frozenset())) - {"upstream_inputs", "debt_flow_gap_days"}
        tick.append(q + "/components/" + m + "/" + _alt(sorted(keys)))
        tick.append(q + "/components/" + m + "/upstream_inputs(" + _idx(P2_LIST_MAXLEN["upstream_inputs"][m]) + "/"
                    + _alt(sorted(Q_UPSTREAM_KEYS)) + ")?")
    tick.append(q + "/components/net_debt_to_fcf/debt_flow_gap_days(" + _idx(P2_LIST_MAXLEN["debt_dates"]) + ")?")
    cov = [r"/stage_status_counts/quality/OK",
           r"/quality/element_status_counts/(OK|MISSING)",
           r"/quality/element_reason_code_counts(/INSUFFICIENT_INDEPENDENT_COMPONENTS)?",
           r"/quality/leverage_assessment_counts/(ASSESSED|PARTIALLY_ASSESSED|NOT_ASSESSED)",
           r"/quality/quality_ok_with_leverage_(not|partially)_assessed",
           r"/quality/flag_counts(/" + Q_FLAG + ")?",
           r"/quality/stage_error_count", r"/quality/stage_error_tickers"]
    for m in Q_METRICS:
        codes = set(Q_LOCAL_INVALID[m]) | ({Q_NM_CODE[m]} if m in Q_NM_CODE else set())
        codes |= {f"{prefix}:{x}" for _s, prefix in Q_UPSTREAM_MAP.values() for x in Q_INPUTS[m][0] + Q_INPUTS[m][1]}
        cov.append(r"/quality/components/" + m + "/by_status/" + _alt(Q_COMPONENT_STATUSES))
        cov.append(r"/quality/components/" + m + "/by_reason_code(/" + _alt(sorted(codes)) + ")?")
    summ = [r"/records\[\d+\]/stage_statuses/quality",
            r"/records\[\d+\]/quality/" + _alt([k for k in Q_SUMMARY_KEYS if k != "flags"]),
            r"/records\[\d+\]/quality/flags(" + _idx(P2_LIST_MAXLEN["flags"]) + ")?"]
    comp = lambda parts: re.compile("|".join(f"^{x}$" for x in parts))
    return {"ticker": comp(tick), "coverage": comp(cov), "summary": comp(summ)}


def _p2_containers() -> dict:
    """Exact (file kind, path) -> the only empty container that path may take (r2 §4.2). Summary paths differ only in
    the record index, which the registered entry pins exactly."""
    q = "/stages/quality/quality"
    ticker = {f"{q}/element/unavailable_components": [], f"{q}/element/flags": [],
              f"{q}/components/net_debt_to_fcf/debt_flow_gap_days": [], f"{q}/periods/debt_as_of": [],
              f"{q}/periods/debt_age_days": []}
    ticker.update({f"{q}/components/{m}/upstream_inputs": [] for m in Q_METRICS})
    coverage = {"/quality/element_reason_code_counts": {}, "/quality/flag_counts": {}, "/quality/stage_error_tickers": []}
    coverage.update({f"/quality/components/{m}/by_reason_code": {} for m in Q_METRICS})
    return {"ticker": ticker, "coverage": coverage, "summary": [(re.compile(r"^/records\[\d+\]/quality/flags$"), [])]}


P2_MIGRATION = "P2_QUALITY_V02_STAGE"
PATCH_MIGRATIONS[P2_MIGRATION] = dict(_p2_grammar(), ops=("add",), containers=_p2_containers())
# A governed ticker may carry P2 entries only inside the Quality stage; their content is checked independently by Q5.
GOVERNED_PATCH_STAGE = {P2_MIGRATION: Q_STAGE_PREFIX}

# v2.12 (F5): a filing whose SEC fy label collides with the same fiscal quarter one year earlier is relabelled fy + 1 in
# sec_history, so a dropped latest quarter can come back. Only the quarterly YoY components (and what is derived from
# them) may move; revenue_cagr_3y (annual) and the fcf_yoy input-scope block are deliberately outside the grammar
# (an F5 run that changes them fails closed and needs a new design). Governed tickers: no GOVERNED_PATCH_SCOPE entry,
# so any F5 entry on a governed ticker is rejected.
F5_MIGRATION = "F5_FY_LABEL_COLLISION"
_F5Q = "(revenue_yoy|operating_income_yoy|eps_yoy|fcf_yoy)"
_F5_STATUS = "(OK|INVALID|MISSING)"
_F5_GROWTH_CODES = "(NONPOSITIVE_PRIOR_BASE|NONPOSITIVE_CURRENT|NO_REASON_CODE|SAME_FILING_COMPARATIVE_UNAVAILABLE)"
_F5_SUFF_CODES = "(NO_REASON_CODE|CONCEPT_NOT_FOUND|INSUFFICIENT_HISTORY)"
PATCH_MIGRATIONS[F5_MIGRATION] = {
    "ticker": re.compile(
        r"^/stages/growth/growth/components/" + _F5Q + r"/(status|value|stars|reason|reason_code|current_period_end"
        r"|previous_period_end|source\[[01]\])$"
        r"|^/stages/growth/growth/components/eps_yoy/previous_value_basis$"
        r"|^/stages/growth/growth/growth/(status|score|stars|available_components|valid_components|coverage)$"
        r"|^/stages/growth/growth/warnings(\[[0-2]\])?$"
        r"|^/stages/growth/growth/limitations(/" + _F5Q + r")?$"
        r"|^/stages/data_sufficiency/sufficiency/" + _F5Q + r"/(calculation_status|reason_code|detail)$"),
    "coverage": re.compile(
        r"^/growth/components/" + _F5Q + r"/by_status/" + _F5_STATUS + r"$"
        r"|^/growth/components/" + _F5Q + r"/by_reason_code(/" + _F5_GROWTH_CODES + r")?$"
        r"|^/growth/aggregate_status_counts/(OK|MISSING)$"
        r"|^/data_sufficiency/" + _F5Q + r"/by_calculation_status/" + _F5_STATUS + r"$"
        r"|^/data_sufficiency/" + _F5Q + r"/by_reason_code(/" + _F5_SUFF_CODES + r")?$"),
}
SUMMARY_FILE = "summary.json"
SUMMARY_RUN_PATHS = re.compile(r"^/(generated_at_utc|reproducibility(/.*)?|records\[\d+\]/raw_input/(fetched_at_utc|loaded_at_utc|sha256|size_bytes))$")


def container_for(containers, kind, path):
    """The empty container registered for this exact path, or None."""
    if not containers: return None
    table = containers.get(kind)
    if isinstance(table, dict): return table.get(path)
    for rx, empty in table or ():
        if rx.fullmatch(path): return empty
    return None

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
_ABS = object()


def flat_get(fl, path):
    """Leaf value at a flat() path, or ABSENT_TAG when that leaf does not exist."""
    return fl[path] if path in fl else dict(ABSENT_TAG)


def unflatten(fl):
    """Inverse of flat(). Raises ValueError when the leaves do not form one consistent JSON document
    (e.g. a list with a missing index, or a path that is both a leaf and a container)."""
    root = _ABS
    def tokens(path): return [(k, int(i) if i else None) for k, i in re.findall(r"/([^/\[]+)|\[(\d+)\]", path)]
    def ensure(container, tok, nxt):
        k, i = tok
        if k:
            if not isinstance(container, dict): raise ValueError("object/list conflict")
            if k not in container: container[k] = _ABS if nxt is None else ({} if nxt[0] else [])
            return container[k]
        if not isinstance(container, list): raise ValueError("object/list conflict")
        while len(container) <= i: container.append(_ABS)
        if container[i] is _ABS and nxt is not None: container[i] = {} if nxt[0] else []
        return container[i]
    for path in sorted(fl):
        toks = tokens(path)
        if not toks: raise ValueError("root leaf")
        if root is _ABS: root = {} if toks[0][0] else []
        cur = root
        for j, tok in enumerate(toks[:-1]):
            cur = ensure(cur, tok, toks[j + 1])
            if not isinstance(cur, (dict, list)): raise ValueError("leaf/container conflict")
        k, i = toks[-1]
        v = copy.deepcopy(fl[path])
        if k:
            if not isinstance(cur, dict) or (k in cur and cur[k] is not _ABS): raise ValueError("leaf/container conflict")
            cur[k] = v
        else:
            if not isinstance(cur, list): raise ValueError("object/list conflict")
            while len(cur) <= i: cur.append(_ABS)
            if cur[i] is not _ABS: raise ValueError("leaf/container conflict")
            cur[i] = v
    def check(o):
        if o is _ABS: raise ValueError("list with a missing index")
        if isinstance(o, dict): [check(v) for v in o.values()]
        elif isinstance(o, list): [check(v) for v in o]
    check(root)
    rt = flat(root)
    if set(rt) != set(fl) or any(not same_leaf(rt[k], fl[k]) for k in fl):
        raise ValueError("leaves do not round-trip (a path is both a leaf and a container)")
    return root


def parent_and_key(path):
    m = re.match(r"^(.*)/([^/\[]+)$", path)
    return (m.group(1), m.group(2)) if m else (None, None)


# ---------------------------------------------------------------- per-run schema (T0)
GATE_LIST_KEYS = ("withheld", "scoped", "not_yet_available", "no_record")
RELABEL_STAGES = ("derived", "growth", "data_sufficiency")
# sec_normalizer.calculate_total_debt() is the only composite: it always returns exactly these two components
# (observed: 90/90 total_debt records in 5 batches; no other metric ever carries "components").
COMPOSITE_COMPONENTS = {"total_debt": ("current_debt", "noncurrent_debt")}
# v2.10 (F-7, production b5bcb27): the gate masks a composite whenever one of its components is WITHHELD. Such a mask
# carries eligibility.basis = COMPOSITE_WITHHELD_BASIS and withheld_components (the withheld parts, in part order) and
# no row references (no row decided it). M3 compares both constants with the production registry by static parse.
COMPOSITE_WITHHELD_BASIS = "COMPOSITE_COMPONENT_WITHHELD"
COMPOSITE_MASK_ROW_REFS = ("outcome", "decision_ref", "policy_version")
COMPOSITE_SOURCE = {"names": {"COMPOSITE_COMPONENTS": "COMPOSITE_GOVERNED_METRICS", "COMPOSITE_WITHHELD_BASIS": "COMPOSITE_WITHHELD_BASIS"},
                    "commit": "b5bcb27fc756f5e2f60f56002ca7c8594c384547"}
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


def composite_closure_problems(r) -> list[str]:
    """B8 (v2.10, F-7) for one ticker whose gate is applied and well formed. Metric names and rule ids only, never values.
    C1 a withheld component forces its composite into withheld; C2 a composite mask names exactly the withheld parts
       (in part order) and no row reference; C3 eligibility.basis appears only on a withheld composite and only with
       COMPOSITE_WITHHELD_BASIS; C4 no masked (withheld) record keeps nested components."""
    g, nm, p = gate(r), metrics(r), []
    W = set(_l(g.get("withheld")))
    for comp, parts in COMPOSITE_COMPONENTS.items():
        hit = [x for x in parts if x in W]
        if hit and comp not in W: p.append(f"C1 {comp} not withheld while component(s) {hit} are withheld")
        e = _d(nm.get(comp)).get("eligibility")
        if comp in W and isinstance(e, dict) and e.get("basis") == COMPOSITE_WITHHELD_BASIS:
            wc = e.get("withheld_components")
            if not (isinstance(wc, list) and wc and wc == hit):
                p.append(f"C2 {comp} withheld_components {wc if isinstance(wc, list) and all(isinstance(x, str) for x in wc) else type(wc).__name__} != withheld parts {hit}")
            refs = [k for k in COMPOSITE_MASK_ROW_REFS if k in e]
            if refs: p.append(f"C2 {comp} composite mask carries row reference(s) {refs}")
    for m, item in sorted(nm.items()):
        e = _d(item).get("eligibility") if isinstance(item, dict) else None
        if isinstance(e, dict) and "basis" in e:
            if m not in COMPOSITE_COMPONENTS or m not in W or e["basis"] != COMPOSITE_WITHHELD_BASIS:
                p.append(f"C3 {m} eligibility.basis not a composite mask basis on a withheld composite")
    for m in sorted(W):
        if isinstance(nm.get(m), dict) and "components" in nm[m]: p.append(f"C4 {m} masked record keeps components")
    return p


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
    return [x for t, r in run.results.items() for x in _ticker_targets(t, r)]


def _ticker_targets(t, r) -> list[dict]:
    out = []
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


def ticker_leak_scan(t, r):
    """F2 (v2.10) -> (fail, review, n_targets) or None when the ticker has no numeric original. Targets are this
    ticker's amounts from stages.eligibility_gate.unmasked_originals (the audit copy); exactly that subtree is left out
    of the scan, so a target is never matched against the record it was read from. Everything else in the ticker
    result is scanned with the F1/AUX rules (scan_json); the targets themselves are never written anywhere."""
    targets = _ticker_targets(t, r)
    if not targets: return None
    doc = copy.deepcopy(r)
    g = _d(_d(doc.get("stages")).get("eligibility_gate"))
    g.pop("unmasked_originals", None)
    f, rv = scan_json(doc, targets)
    return f, rv, len(targets)


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

# ---------------------------------------------------------------- v2.9 (P2): Quality v0.2 stage checks (Q0-Q7)
# Independent re-derivation from the ticker file only (gated normalized metrics + derived metrics); production's
# calculate_quality_v02 / batch_coverage / batch_runner are never imported or called. Every function returns
# paths, metric names and rule ids only -- never amounts.
def q_stage(r): return stages(r).get("quality")
def q_doc(r):
    s_ = q_stage(r)
    return s_.get("quality") if isinstance(s_, dict) else None
def _num(x) -> bool: return isinstance(x, (int, float)) and not isinstance(x, bool)
def _int(x) -> bool: return isinstance(x, int) and not isinstance(x, bool)
def q_band(x) -> int:
    """quality._stars_from_rating thresholds (Q_STAR_BANDS / Q_STAR_FLOOR; M2 compares them with production)."""
    for c, n in Q_STAR_BANDS:
        if x >= c: return n
    return Q_STAR_FLOOR
def q_upstream_class(status):
    if status == "OK": return None
    return status if status in ("MISSING", "INCOMPLETE", "WITHHELD", "INVALID") else "INVALID"
def q_days(later, earlier):
    if later is None or earlier is None: return None
    return (date.fromisoformat(later) - date.fromisoformat(earlier)).days
def run_has_quality(run) -> bool:
    return (any(isinstance(stages(r), dict) and "quality" in stages(r) for r in run.results.values())
            or "quality" in _d(run.coverage) or "quality" in _d(_d(run.coverage).get("stage_status_counts"))
            or any(isinstance(rec, dict) and "quality" in rec for rec in run.summary["records"]))


def q_structure_problems(t, r) -> list[str]:
    """Q1: exact key sets and value types of one ticker's Quality stage (r2 §1.4, r3 §1.3)."""
    p, s_ = [], q_stage(r)
    if not isinstance(s_, dict): return [f"{t}: stages.quality missing or not an object"]
    st = s_.get("status")
    if st == Q_ERROR:
        e = s_.get("error")
        if set(s_) != {"status", "quality", "error"} or s_.get("quality") is not None or not isinstance(e, dict) \
                or set(e) != {"type"} or not (isinstance(e.get("type"), str) and e["type"]):
            p.append(f"{t}: ERROR stage must be exactly {{status, quality: null, error: {{type}}}}")
        return p
    if st != "OK" or set(s_) != {"status", "quality"}:
        return [f"{t}: stage must be exactly {{status: OK, quality}} or the ERROR shape"]
    q = s_["quality"]
    if not isinstance(q, dict) or set(q) != Q_TOP_KEYS: return [f"{t}: quality keys must be exactly {sorted(Q_TOP_KEYS)}"]
    if q["schema_version"] != Q_SCHEMA_VERSION: p.append(f"{t}: schema_version")
    if q["ticker"] != t or _d(r).get("ticker") != t: p.append(f"{t}: ticker")
    if not isinstance(q["evaluation_date"], str) or q["evaluation_date"] != _d(r).get("evaluation_date"): p.append(f"{t}: evaluation_date")
    if not isinstance(q["company_type"], str) or q["company_type"] != _d(r).get("company_type"): p.append(f"{t}: company_type")
    lim = q["limitations"]
    if not isinstance(lim, dict) or set(lim) != set(Q_LIMITATIONS) or not all(
            isinstance(v, dict) and set(v) == {"status", "reason"} and all(isinstance(x, str) for x in v.values()) for v in lim.values()):
        p.append(f"{t}: limitations")
    e = q["element"]
    if not isinstance(e, dict) or set(e) != Q_ELEMENT_KEYS: return p + [f"{t}: element keys"]
    if e["status"] not in ("OK", "MISSING", "NOT_APPLICABLE"): p.append(f"{t}: element.status")
    if e["reason_code"] is not None and not isinstance(e["reason_code"], str): p.append(f"{t}: element.reason_code")
    for k in ("score", "scored_weight_fraction"):
        if e[k] is not None and not is_finite_number(e[k]): p.append(f"{t}: element.{k}")
    if e["stars"] is not None and not _int(e["stars"]): p.append(f"{t}: element.stars")
    for k in ("rated_count", "not_meaningful_count", "independent_total"):
        if not _int(e[k]): p.append(f"{t}: element.{k}")
    w = e["normalized_score_weights"]
    if w is not None and not (isinstance(w, dict) and w and set(w) <= set(Q_INDEP) and all(is_finite_number(v) for v in w.values())):
        p.append(f"{t}: element.normalized_score_weights")
    ad = e["assessed_dimensions"]
    if ad is not None and not (isinstance(ad, dict) and set(ad) == set(Q_DIMS)
                               and all(v in ("ASSESSED", "PARTIALLY_ASSESSED", "NOT_ASSESSED") for v in ad.values())):
        p.append(f"{t}: element.assessed_dimensions")
    ua = e["unavailable_components"]
    if not (isinstance(ua, list) and len(ua) <= P2_LIST_MAXLEN["unavailable_components"]
            and all(isinstance(x, dict) and set(x) == Q_UNAVAILABLE_KEYS for x in ua)):
        p.append(f"{t}: element.unavailable_components")
    fl = e["flags"]
    if not (isinstance(fl, list) and len(fl) <= P2_LIST_MAXLEN["flags"] and all(x == Q_FLAG for x in fl)):
        p.append(f"{t}: element.flags")
    comps, per = q["components"], q["periods"]
    if e["status"] == "NOT_APPLICABLE":
        if comps != {} or per is not None or ad is not None: p.append(f"{t}: NOT_APPLICABLE element with computed parts")
        return p
    if not isinstance(comps, dict) or set(comps) != set(Q_METRICS): return p + [f"{t}: components keys"]
    for m in Q_METRICS:
        c = comps[m]; lab = f"{t}: components.{m}"
        if not isinstance(c, dict) or set(c) != Q_KEYS_COMMON | Q_KEYS_EXTRA.get(m, frozenset()):
            p.append(f"{lab} keys"); continue
        if c["metric"] != m: p.append(f"{lab}.metric")
        if c["status"] not in Q_COMPONENT_STATUSES: p.append(f"{lab}.status")
        if c["role"] != ("INFORMATIONAL" if m in Q_INFO else "INDEPENDENT"): p.append(f"{lab}.role")
        if not _num(c["weight"]): p.append(f"{lab}.weight")
        for k in ("value", "rating"):
            if c[k] is not None and not is_finite_number(c[k]): p.append(f"{lab}.{k}")
        if c["stars"] is not None and not _int(c["stars"]): p.append(f"{lab}.stars")
        for k in ("reason_code", "upstream_reason_code"):
            if c[k] is not None and not isinstance(c[k], str): p.append(f"{lab}.{k}")
        ui = c["upstream_inputs"]
        if not (isinstance(ui, list) and len(ui) <= P2_LIST_MAXLEN["upstream_inputs"][m]
                and all(isinstance(x, dict) and set(x) == Q_UPSTREAM_KEYS and isinstance(x["metric"], str)
                        and (x["status"] is None or isinstance(x["status"], str))
                        and (x["reason_code"] is None or isinstance(x["reason_code"], str)) for x in ui)):
            p.append(f"{lab}.upstream_inputs")
        if m in Q_INFO and c["duplicate_of"] != Q_INFO[m]: p.append(f"{lab}.duplicate_of")
        if m in ("net_debt_to_fcf", "debt_to_cash") and c["liquidity_basis"] != Q_LIQUIDITY_BASIS: p.append(f"{lab}.liquidity_basis")
        if m == "net_debt_to_fcf":
            if c["mixed_period_ratio"] is not True: p.append(f"{lab}.mixed_period_ratio")
            if c["net_debt_sign"] not in (None, "ZERO", "NEGATIVE", "POSITIVE"): p.append(f"{lab}.net_debt_sign")
            for k in ("cash_flow_gap_days", "balance_flow_gap_days"):
                if c[k] is not None and not _int(c[k]): p.append(f"{lab}.{k}")
            g = c["debt_flow_gap_days"]
            if not (isinstance(g, list) and len(g) <= P2_LIST_MAXLEN["debt_dates"] and all(_int(x) for x in g)):
                p.append(f"{lab}.debt_flow_gap_days")
    if not isinstance(per, dict) or set(per) != Q_PERIOD_KEYS: return p + [f"{t}: periods keys"]
    if per["flow_basis"] != Q_FLOW_BASIS: p.append(f"{t}: periods.flow_basis")
    for k in ("debt_as_of", "debt_age_days"):
        v = per[k]
        if not (isinstance(v, list) and len(v) <= P2_LIST_MAXLEN["debt_dates"]): p.append(f"{t}: periods.{k}")
    return p


def _comp_inputs(r, m):
    """[(name, item, class)] of the component's direct inputs, normalized first then derived (production order)."""
    nm, dm = metrics(r), stage_items(r, "derived") or {}
    out = []
    for name in Q_INPUTS[m][0]: it = _d(nm.get(name)); out.append((name, it, q_upstream_class(it.get("status"))))
    for name in Q_INPUTS[m][1]: it = _d(dm.get(name)); out.append((name, it, q_upstream_class(it.get("status"))))
    return out


def q_e1_condition(r) -> bool:
    """r3 §6.1 E1: every direct input OK, derived fcf OK finite <= 0, derived net_debt OK finite > 0."""
    dm = stage_items(r, "derived") or {}
    fcf, nd = _d(dm.get("fcf")), _d(dm.get("net_debt"))
    return (all(c is None for _n, _i, c in _comp_inputs(r, "net_debt_to_fcf"))
            and fcf.get("status") == "OK" and is_finite_number(fcf.get("value")) and fcf["value"] <= 0
            and nd.get("status") == "OK" and is_finite_number(nd.get("value")) and nd["value"] > 0)


def q_component_problems(t, r, m, c) -> list[str]:
    """I1, I6, I7 and E1 for one component (r3 §6, r4 §4)."""
    p, lab = [], f"{t}.{m}"
    ins = _comp_inputs(r, m)
    bad = {n: (it, k) for n, it, k in ins if k is not None}
    # I1 weight
    if m in Q_INFO:
        if not (_num(c["weight"]) and c["weight"] == 0.0): p.append(f"{lab}: I1 informational weight must be 0")
    elif abs(c["weight"] - Q_WEIGHTS[m]) > Q_TOL: p.append(f"{lab}: I1 weight")
    # upstream_inputs = exactly the non-OK direct inputs, each copying the gated item's status / reason_code
    rec = c["upstream_inputs"]
    names = [x["metric"] for x in rec]
    if len(names) != len(set(names)) or set(names) != set(bad):
        p.append(f"{lab}: I7-upstream-set")
    else:
        for x in rec:
            it = bad[x["metric"]][0]
            if not (same_leaf(x["status"], it.get("status")) and same_leaf(x["reason_code"], it.get("reason_code"))):
                p.append(f"{lab}: I7-upstream-copy {x['metric']}")
    s, rc, urc = c["status"], c["reason_code"], c["upstream_reason_code"]
    if s != "OK" and any(c[k] is not None for k in ("value", "rating", "stars")):
        p.append(f"{lab}: I7 value/rating/stars must be null when status is {s}")
    if bad:
        top = min((k for _it, k in bad.values()), key=Q_UPSTREAM_ORDER.index)
        want_status, prefix = Q_UPSTREAM_MAP[top]
        if s != want_status: p.append(f"{lab}: I7-precedence status {s} (highest upstream class {top})")
        x = rc.split(":", 1)[1] if isinstance(rc, str) and rc.startswith(prefix + ":") else None
        if x is None or x not in bad or bad[x][1] != top:
            p.append(f"{lab}: I7-prefix reason_code must be {prefix}:<an input of class {top}>")
        else:
            want_urc = bad[x][0].get("reason_code") or "UPSTREAM_REASON_UNSPECIFIED"
            if not same_leaf(urc, want_urc): p.append(f"{lab}: I7 upstream_reason_code")
        return p
    if urc is not None: p.append(f"{lab}: I7 upstream_reason_code must be null without an upstream cause")
    if s in ("MISSING", "WITHHELD"):
        p.append(f"{lab}: I7 status {s} needs an upstream cause")
    elif s == "INVALID":
        if rc not in Q_LOCAL_INVALID[m]: p.append(f"{lab}: I7-local reason_code not allowed for {m}")
    elif s == "NOT_MEANINGFUL":
        if m not in Q_NM_CODE or rc != Q_NM_CODE[m]: p.append(f"{lab}: I7-NM reason_code")
    elif s == "OK":
        e1 = m == "net_debt_to_fcf" and q_e1_condition(r)
        if rc == Q_E1:
            if not e1: p.append(f"{lab}: E1-condition not met")
            if not (c["value"] is None and same_leaf(c["rating"], 1.0) and same_leaf(c["stars"], 1)):
                p.append(f"{lab}: E1-shape (value null, rating 1.0, stars 1)")
        elif rc is None:
            if e1: p.append(f"{lab}: E1-required (condition met but general OK rule used)")
            if not is_finite_number(c["value"]): p.append(f"{lab}: I7 OK value must be finite")
            if not (is_finite_number(c["rating"]) and 1.0 <= c["rating"] <= 5.0): p.append(f"{lab}: I6 rating range")
            elif not same_leaf(c["stars"], q_band(c["rating"])): p.append(f"{lab}: I5 component stars")
        else:
            p.append(f"{lab}: I7 OK reason_code must be null or {Q_E1}")
    return p


def q_invariant_problems(t, r) -> list[str]:
    """Q3: I1-I8, reason-code table and E1 (r2 §5, r3 §5-6). Assumes q_structure_problems() is empty."""
    q = q_doc(r)
    if q is None: return []
    e, comps = q["element"], q["components"]
    p = []
    if q["company_type"] == "FINANCIAL":
        if not (e["status"] == "NOT_APPLICABLE" and e["reason_code"] == "COMPANY_TYPE_FINANCIAL"
                and all(e[k] is None for k in ("score", "stars", "scored_weight_fraction", "normalized_score_weights"))
                and e["rated_count"] == 0 and e["not_meaningful_count"] == 0 and e["unavailable_components"] == [] and e["flags"] == []):
            p.append(f"{t}: I8 FINANCIAL element")
        return p
    if e["status"] == "NOT_APPLICABLE": return [f"{t}: I8 NOT_APPLICABLE for a non-FINANCIAL company"]
    for m in Q_METRICS: p += q_component_problems(t, r, m, comps[m])
    rated = [m for m in Q_INDEP if comps[m]["status"] == "OK"]
    nmc = [m for m in Q_INDEP if comps[m]["status"] == "NOT_MEANINGFUL"]
    if e["rated_count"] != len(rated) or e["not_meaningful_count"] != len(nmc) or e["independent_total"] != len(Q_INDEP):
        p.append(f"{t}: I8 counts")
    ok_cond = len(rated) >= Q_MIN_RATED and len(rated) + len(nmc) >= Q_MIN_RATED_PLUS_NM
    if ok_cond:
        if e["status"] != "OK" or e["reason_code"] is not None: p.append(f"{t}: I8 element must be OK")
        f, w, sc = e["scored_weight_fraction"], e["normalized_score_weights"], e["score"]
        if not (is_finite_number(f) and is_finite_number(sc) and isinstance(w, dict)):
            p.append(f"{t}: I8 OK element needs score, stars, fraction and weights")
        else:
            if abs(f - sum(Q_WEIGHTS[m] for m in rated)) > Q_TOL or not (0 < f <= 1 + Q_TOL): p.append(f"{t}: I2 scored_weight_fraction")
            if set(w) != set(rated): p.append(f"{t}: I3 normalized_score_weights keys")
            else:
                if any(abs(w[m] - Q_WEIGHTS[m] / f) > Q_TOL for m in rated): p.append(f"{t}: I3 normalized weight")
                if abs(sum(w.values()) - 1.0) > Q_TOL: p.append(f"{t}: I3 weights sum")
                if abs(sc - sum(comps[m]["rating"] * w[m] for m in rated)) > Q_TOL: p.append(f"{t}: I4 score")
            if not (1.0 - Q_TOL <= sc <= 5.0 + Q_TOL): p.append(f"{t}: I4 score range")   # weighted mean of floored ratings: float slack only
            elif not same_leaf(e["stars"], q_band(sc)): p.append(f"{t}: I5 element stars")
    else:
        if e["status"] != "MISSING" or e["reason_code"] != "INSUFFICIENT_INDEPENDENT_COMPONENTS": p.append(f"{t}: I8 element must be MISSING")
        if any(e[k] is not None for k in ("score", "stars", "scored_weight_fraction", "normalized_score_weights")):
            p.append(f"{t}: I8 MISSING element with score fields")
    def dim(members):
        n = sum(comps[m]["status"] in ("OK", "NOT_MEANINGFUL") for m in members)
        return "ASSESSED" if n == len(members) else ("PARTIALLY_ASSESSED" if n else "NOT_ASSESSED")
    if e["assessed_dimensions"] != {d: dim(ms) for d, ms in Q_DIMS.items()}: p.append(f"{t}: I8 assessed_dimensions")
    want_ua = {m: {"metric": m, "status": comps[m]["status"], "reason_code": comps[m]["reason_code"],
                   "upstream_reason_code": comps[m]["upstream_reason_code"]}
               for m in Q_INDEP if comps[m]["status"] not in ("OK", "NOT_MEANINGFUL")}
    got_ua = {x["metric"]: x for x in e["unavailable_components"] if isinstance(x.get("metric"), str)}
    if len(got_ua) != len(e["unavailable_components"]) or set(got_ua) != set(want_ua) \
            or any(flat(got_ua[m]) != flat(want_ua[m]) or any(not same_leaf(got_ua[m][k], want_ua[m][k]) for k in want_ua[m]) for m in want_ua):
        p.append(f"{t}: I8 unavailable_components")
    dm = stage_items(r, "derived") or {}
    fcf, nd = _d(dm.get("fcf")), _d(dm.get("net_debt"))
    flag = fcf.get("status") == "OK" and is_finite_number(fcf.get("value")) and fcf["value"] <= 0 and nd.get("status") != "OK"
    if e["flags"] != ([Q_FLAG] if flag else []): p.append(f"{t}: I8 flags")
    sign = None
    if nd.get("status") == "OK" and is_finite_number(nd.get("value")):
        sign = "ZERO" if nd["value"] == 0 else ("NEGATIVE" if nd["value"] < 0 else "POSITIVE")
    if comps["net_debt_to_fcf"]["net_debt_sign"] != sign: p.append(f"{t}: L6 net_debt_sign")
    return p


def q_debt_dates(td):
    """r5 §3.3: (dates, problems) of the total-debt observations actually summed. Rule ids in problems."""
    if td.get("status") != "OK": return [], []
    comps, val = td.get("components"), td.get("value")
    p = []
    if not (isinstance(comps, dict) and set(comps) == set(Q_DEBT_COMPONENTS)
            and all(isinstance(comps[k], dict) and comps[k].get("status") == "OK" for k in Q_DEBT_COMPONENTS)):
        return [], ["I9-D B-1/A-4 components must be exactly current_debt and noncurrent_debt, both OK"]
    for k in Q_DEBT_COMPONENTS:
        if not is_iso_date(comps[k].get("period_end")): p.append(f"I9-D date format components.{k}.period_end")
        if not is_finite_number(comps[k].get("value")): p.append(f"I9-D components.{k}.value")
    if not is_finite_number(val): p.append("I9-D total_debt value")
    if p: return [], p
    if "component_sum" in td:
        cs = td["component_sum"]
        if not isinstance(cs, dict) or cs.get("applied") is not True: return [], ["I9-D A-1 component_sum present but applied is not true"]
        rows = cs.get("components")
        roles = [x.get("role") for x in rows] if isinstance(rows, list) and all(isinstance(x, dict) for x in rows) else None
        if roles is None or len(rows) != 3 or sorted(roles) != sorted(["current_debt", "short_term_component", "noncurrent_debt"]):
            return [], ["I9-D A-2 component_sum.components must be the three roles current_debt, short_term_component, noncurrent_debt"]
        if not all(is_finite_number(x.get("value")) for x in rows): return [], ["I9-D A-3 component_sum values"]
        if not all(is_iso_date(x.get("period_end")) for x in rows): return [], ["I9-D date format component_sum period_end"]
        if sum(x["value"] for x in rows) != val: p.append("I9-D A-3 component_sum total != total_debt value")
        by_role = {x["role"]: x for x in rows}
        for k in Q_DEBT_COMPONENTS:
            if any(not same_leaf(by_role[k].get(f), comps[k].get(f)) for f in ("concept", "value", "period_end")):
                p.append(f"I9-D A-4 components.{k} does not match the summed {k}")
        dates = {x["period_end"] for x in rows}
        if len(dates) != 1: p.append("I9-D A-5 summed components must share one period_end")
    else:
        if comps["current_debt"]["value"] + comps["noncurrent_debt"]["value"] != val: p.append("I9-D B-2 component total != total_debt value")
        dates = {comps[k]["period_end"] for k in Q_DEBT_COMPONENTS}
    sa = td.get("scope_alignment")
    if sa is not None:
        if not isinstance(sa, dict) or not is_iso_date(sa.get("period_end")): p.append("I9-D C scope_alignment.period_end format")
        elif sa["period_end"] not in dates: p.append("I9-D C scope_alignment.period_end not among the summed dates")
    return (sorted(dates) if not p else []), p


def q_period_problems(t, r) -> list[str]:
    """Q4: I9 periods recompute + I9-D debt date provenance + date formats (r3 §4, r4 §3, r5 §3)."""
    q = q_doc(r)
    if q is None or q["company_type"] == "FINANCIAL": return []
    nm, ev = metrics(r), q["evaluation_date"]
    if not is_iso_date(ev): return [f"{t}: I9 evaluation_date format"]
    okf = [(n, _d(nm.get(n))) for n in Q_FLOW_INPUTS if _d(nm.get(n)).get("status") == "OK"]
    p = []
    for n, it in okf:
        if not is_iso_date(it.get("period_end")) or (it.get("period_start") is not None and not is_iso_date(it.get("period_start"))):
            p.append(f"{t}: I9 date format {n}")
    cash = _d(nm.get("cash"))
    cash_as_of = cash.get("period_end") if cash.get("status") == "OK" else None
    if cash_as_of is not None and not is_iso_date(cash_as_of): p.append(f"{t}: I9 date format cash")
    dates, dp = q_debt_dates(_d(nm.get("total_debt")))
    p += [f"{t}: {x}" for x in dp]
    if p: return p
    sigs = {tuple(it.get(k) for k in Q_FLOW_SIG) for _n, it in okf}
    units = {it.get("unit") for _n, it in okf}
    consistent = (len(sigs) <= 1 and len(units) <= 1) if okf else None
    fstart = fend = None
    if okf and consistent:
        fstart, fend = okf[0][1].get("period_start"), okf[0][1].get("period_end")
    acc = [it.get("accession") for _n, it in okf]
    if not okf or any(not (isinstance(a, str) and a) for a in acc): acc_ok = None     # r4 §3.4 / r5 §4: unknown -> null
    else: acc_ok = len(set(acc)) == 1
    bdc = (set(dates) == {cash_as_of}) if (cash_as_of is not None and dates) else None
    bsa = cash_as_of if bdc else None
    want = {"flow_basis": Q_FLOW_BASIS, "flow_inputs_consistent": consistent, "flow_period_start": fstart, "flow_period_end": fend,
            "flow_age_days": q_days(ev, fend), "flow_accession_consistent": acc_ok, "cash_as_of": cash_as_of,
            "cash_age_days": q_days(ev, cash_as_of), "debt_as_of": dates, "debt_age_days": [q_days(ev, d) for d in dates],
            "balance_sheet_as_of": bsa, "balance_dates_consistent": bdc}
    fw, fg = flat(want), flat(q["periods"])
    for k in sorted(set(fw) | set(fg)):
        if not same_leaf(flat_get(fg, k), flat_get(fw, k)): p.append(f"{t}: I9 periods{k}")
    nd = q["components"]["net_debt_to_fcf"]
    wnd = {"cash_flow_gap_days": q_days(cash_as_of, fend), "balance_flow_gap_days": q_days(bsa, fend) if bsa else None,
           "debt_flow_gap_days": [q_days(d, fend) for d in dates] if fend else []}
    for k, v in wnd.items():
        if flat(nd[k]) != flat(v) or any(not same_leaf(a, b) for a, b in zip(flat(nd[k]).values(), flat(v).values())):
            p.append(f"{t}: I9 net_debt_to_fcf.{k}")
    return p


def q_leak_problems(t, r, targets) -> tuple[list, list, list]:
    """Q5 (governed ticker): L0-L3d, L7 and the auxiliary amount scan (r2 §1, r4 §2, r5 §2). -> (fail, review, info)."""
    g, q, nm = gate(r), q_doc(r), metrics(r)
    W = [m for m in _l(g.get("withheld")) if isinstance(m, str)]
    dm = stage_items(r, "derived") or {}
    fail, review, info = [], [], []
    for gi in Q_GOVERNED_INPUTS:                                                      # L0 (bidirectional, G_Q only)
        it = _d(nm.get(gi))
        if (it.get("status") == "WITHHELD") != (gi in W): fail.append(f"{t}: L0 {gi} gate/status mismatch")
        if gi in W and it.get("value") is not None: fail.append(f"{t}: L0 {gi} withheld but valued")
    if set(W) & set(Q_DEBT_COMPONENTS) and "total_debt" not in W:
        fail.append(f"{t}: composite closure violated: total_debt not withheld while a debt component is withheld (F-7)")
    if q is None or q["company_type"] == "FINANCIAL": return fail, review, info
    comps, ws = q["components"], set(W) & set(Q_GOVERNED_INPUTS)
    tainted = set()
    for m in Q_METRICS:
        direct = set(Q_INPUTS[m][0]) & ws
        derived = {d for d in Q_INPUTS[m][1] if set(DERIVED_DEPS.get(d, ())) & set(W)}
        if not direct and not derived: continue
        tainted.add(m); c = comps[m]
        if c["status"] != "WITHHELD": fail.append(f"{t}.{m}: L1 tainted component not WITHHELD")
        if any(c[k] is not None for k in ("value", "rating", "stars")): fail.append(f"{t}.{m}: L2 tainted component carries value/rating/stars")
        for d in sorted(derived):
            if _d(dm.get(d)).get("status") != "WITHHELD": fail.append(f"{t}.{m}: L3a derived input {d} not WITHHELD")
        cand = {x for x in direct | derived if _d(nm.get(x) if x in Q_INPUTS[m][0] else dm.get(x)).get("status") == "WITHHELD"}
        if not cand: fail.append(f"{t}.{m}: L3d no withheld input among direct/derived sources")
        rc = c["reason_code"]
        x = rc.split(":", 1)[1] if isinstance(rc, str) and rc.startswith("INPUT_WITHHELD:") else None
        if x not in cand: fail.append(f"{t}.{m}: L3b reason_code does not name a withheld source")
        else:
            roots = sorted(direct | {w for d in derived for w in DERIVED_DEPS[d] if w in W})
            info.append(f"{t}.{m}: {x}={'DIRECT' if x in Q_INPUTS[m][0] else 'DERIVED'} roots={roots}")
    w_ = q["element"]["normalized_score_weights"]
    if isinstance(w_, dict) and set(w_) & tainted: fail.append(f"{t}: L7 tainted metric in normalized_score_weights")
    mine = [dict(x, names=set(x["names"]) | tainted) for x in targets if x["ticker"] == t]
    if mine:
        f_, r_ = scan_json(q, mine)
        fail += [f"{t}: AUX amount match at {loc}" for loc in f_]
        review += [f"{t}: AUX small-value match at {loc}" for loc in r_]
    return fail, review, info


def recompute_quality_coverage(run) -> tuple[dict, dict]:
    """Q6: (stage_status_counts['quality'], coverage['quality']) recomputed from the ticker files (r3 §2 table)."""
    ss: dict = {}
    qc = {"element_status_counts": {}, "element_reason_code_counts": {}, "components": {}, "leverage_assessment_counts": {},
          "quality_ok_with_leverage_not_assessed": 0, "quality_ok_with_leverage_partially_assessed": 0, "flag_counts": {},
          "stage_error_count": 0, "stage_error_tickers": []}
    for rec in run.summary["records"]:
        if rec.get("status") != "OK": continue
        t = rec["ticker"]; r = run.results[t]
        if "quality" not in stages(r): continue
        s_ = q_stage(r)
        st = s_.get("status") if isinstance(s_, dict) else None
        _inc(ss, "null" if st is None else st)               # production keys None, which JSON writes as "null"
        if st == Q_ERROR:
            qc["stage_error_count"] += 1; qc["stage_error_tickers"].append(t)
        q = q_doc(r)
        if not (isinstance(q, dict) and isinstance(q.get("element"), dict)): continue
        e = q["element"]
        _inc(qc["element_status_counts"], e.get("status") or KEY_ABSENT)
        if e.get("status") != "OK": _inc(qc["element_reason_code_counts"], e.get("reason_code") or NO_REASON_CODE)
        comps = q.get("components")
        if isinstance(comps, dict) and comps:
            for name, item in comps.items():
                b = qc["components"].setdefault(name, {"by_status": {}, "by_reason_code": {}})
                s2 = item.get("status") if isinstance(item, dict) else None
                _inc(b["by_status"], s2 if s2 is not None else KEY_ABSENT)
                if s2 not in ("OK", None): _inc(b["by_reason_code"], (item.get("reason_code") if isinstance(item, dict) else None) or NO_REASON_CODE)
        ad = e.get("assessed_dimensions")
        lev = ad.get("leverage") if isinstance(ad, dict) else None
        if lev is not None: _inc(qc["leverage_assessment_counts"], lev)
        if e.get("status") == "OK" and lev == "NOT_ASSESSED": qc["quality_ok_with_leverage_not_assessed"] += 1
        if e.get("status") == "OK" and lev == "PARTIALLY_ASSESSED": qc["quality_ok_with_leverage_partially_assessed"] += 1
        for f in _l(e.get("flags")): _inc(qc["flag_counts"], f)
    qc["stage_error_tickers"].sort()
    return ss, qc


def q_summary_expected(r) -> dict:
    """D10 (r3 §1.3): the 13-key summary Quality object an OK record must carry, from the ticker file."""
    s_ = q_stage(r)
    st = s_.get("status") if isinstance(s_, dict) else None
    q = q_doc(r)
    e = q.get("element") if isinstance(q, dict) else None
    avail = st == "OK" and isinstance(e, dict)
    out = {k: None for k in Q_SUMMARY_KEYS}
    out.update(_available=avail, flags=[], stage_status=st,
               error_type=_d(_d(s_).get("error")).get("type") if st == Q_ERROR else None)
    if avail:
        ad = e.get("assessed_dimensions")
        out.update(schema_version=q.get("schema_version"), status=e.get("status"), reason_code=e.get("reason_code"),
                   score=e.get("score"), stars=e.get("stars"), scored_weight_fraction=e.get("scored_weight_fraction"),
                   rated_count=e.get("rated_count"), not_meaningful_count=e.get("not_meaningful_count"),
                   leverage_assessment=ad.get("leverage") if isinstance(ad, dict) else None,
                   flags=list(e.get("flags") or []))
    return out


def q_summary_problems(run) -> list[str]:
    """Q7 (D10): summary.json Quality fields <-> ticker files, joined by ticker (never by index)."""
    p, seen = [], set()
    for rec in run.summary["records"]:
        t = rec.get("ticker")
        if t in seen: p.append(f"duplicate summary ticker {t}")
        seen.add(t)
        if rec.get("status") != "OK":
            if "quality" in rec or "quality" in _d(rec.get("stage_statuses")): p.append(f"{t}: Quality on a non-OK record")
            continue
        r = run.results.get(t)
        if r is None: p.append(f"{t}: ticker file missing"); continue
        if _d(r).get("ticker") != t: p.append(f"{t}: ticker mismatch")
        sq = rec.get("quality")
        if not isinstance(sq, dict) or set(sq) != set(Q_SUMMARY_KEYS):
            p.append(f"{t}: quality summary missing or keys != the 13 fixed keys"); continue
        want = q_summary_expected(r)
        for k in Q_SUMMARY_KEYS:
            a, b = flat(sq[k]) if isinstance(sq[k], (dict, list)) else {"": sq[k]}, flat(want[k]) if isinstance(want[k], (dict, list)) else {"": want[k]}
            if set(a) != set(b) or any(not same_leaf(a[x], b[x]) for x in a): p.append(f"{t}: quality summary differs: {k}")
        s_ = q_stage(r)
        if not same_leaf(_d(rec.get("stage_statuses")).get("quality"), s_.get("status") if isinstance(s_, dict) else None):
            p.append(f"{t}: stage_statuses.quality differs")
    return p


def q_valid_p0(r) -> bool:
    """Q0 (r4 §1 + r5 §1): one evaluated non-FINANCIAL Quality ticker."""
    s_ = q_stage(r)
    if not (isinstance(s_, dict) and s_.get("status") == "OK"): return False
    q = s_.get("quality")
    if not (isinstance(q, dict) and isinstance(q.get("element"), dict)): return False
    ct = q.get("company_type")
    if not (isinstance(ct, str) and ct == _d(r).get("company_type") and ct == Q_EVALUATED_COMPANY_TYPE): return False
    if q["element"].get("status") not in ("OK", "MISSING"): return False
    comps = q.get("components")
    t = _d(r).get("ticker")
    return isinstance(comps, dict) and set(comps) == set(Q_METRICS) and not [x for x in q_structure_problems(t, r) if ": components" in x]


def q_check_run(run, targets) -> list[tuple]:
    """All per-run Quality results as (layer, name, verdict, detail). Empty when the run has no Quality output."""
    out, lab = [], run.label
    if not run_has_quality(run):
        return [("INFO", f"Q {lab} has no Quality output (pre-P2 run)", "INFO", "Q1-Q7 not applicable to this run")]
    ok_t = sorted(run.results)
    struct = {t: q_structure_problems(t, run.results[t]) for t in ok_t}
    sp = [x for t in ok_t for x in struct[t]]
    out.append((lab, "Q1 Quality stage exact structure (every OK ticker)", "PASS" if not sp else "FAIL",
                f"{len(sp)} problem(s): {sp[:12]}" if sp else f"{len(ok_t)} tickers"))
    errs = sorted(t for t in ok_t if _d(q_stage(run.results[t])).get("status") == Q_ERROR)
    out.append((lab, "Q2 no Quality stage ERROR", "PASS" if not errs else "FAIL", f"ERROR tickers: {errs}" if errs else "0"))
    clean = [t for t in ok_t if not struct[t] and q_doc(run.results[t]) is not None]
    ip = [x for t in clean for x in q_invariant_problems(t, run.results[t])]
    out.append((lab, "Q3 Quality internal invariants (weights, score, stars, status/reason table, E1)", "PASS" if not ip else "FAIL",
                f"{len(ip)} problem(s): {ip[:12]}" if ip else f"{len(clean)} tickers"))
    pp = [x for t in clean for x in q_period_problems(t, run.results[t])]
    out.append((lab, "Q4 Quality periods = independent recompute (I9, I9-D debt date provenance, date formats)",
                "PASS" if not pp else "FAIL", f"{len(pp)} problem(s): {pp[:12]}" if pp else f"{len(clean)} tickers"))
    gov_t = sorted(t for t in ok_t if _l(gate(run.results[t]).get("withheld")))
    lf, lr, li = [], [], []
    for t in gov_t:
        if struct[t]: lf.append(f"{t}: not evaluated (structure, see Q1)"); continue
        f_, r_, i_ = q_leak_problems(t, run.results[t], targets); lf += f_; lr += r_; li += i_
    out.append((lab, "Q5 Q-LEAK: governed tickers' Quality built only from non-withheld sources (+ amount scan)",
                "FAIL" if lf else ("REVIEW" if lr else "PASS"),
                f"governed={gov_t}; fail={lf[:10]}; review={lr[:10]}; sources={li[:10]}"))
    ss, qc = recompute_quality_coverage(run)
    cov = _d(run.coverage)
    mism = []
    for name, got, want in (("stage_status_counts/quality", _d(cov.get("stage_status_counts")).get("quality"), ss or None),
                            ("quality", cov.get("quality"), qc)):
        a, b = flat(got) if isinstance(got, (dict, list)) else {"": got}, flat(want) if isinstance(want, (dict, list)) else {"": want}
        mism += [f"{name}{k}" for k in sorted(set(a) | set(b)) if not same_leaf(flat_get(a, k), flat_get(b, k))]
    zeros = [f"quality{k}" for k, v in flat(cov.get("quality")).items() if isinstance(cov.get("quality"), dict) and v == 0 and type(v) is int
             and not k.endswith(("_not_assessed", "_partially_assessed", "stage_error_count"))]
    mism += [f"{z} is a zero-count key" for z in zeros]
    out.append((lab, "Q6 coverage Quality block = independent recompute from ticker files", "PASS" if not mism else "FAIL",
                f"{len(mism)} differing leaves (paths only): {mism[:12]}" if mism else f"{len(flat(qc))} leaves"))
    dp = q_summary_problems(run)
    out.append((lab, "Q7 summary.json Quality fields = ticker files (joined by ticker)", "PASS" if not dp else "FAIL",
                f"{len(dp)} problem(s): {dp[:12]}" if dp else f"{len(run.summary['records'])} records"))
    return out


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
        v, d = composite_mapping_drift(self.repo_root)
        self.add("MAP", "M3 composite map/basis = production registry (static parse)", v, d)
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


    # ---------- Q: v2.9 Quality v0.2 stage (P2). Emits nothing when neither run carries Quality output (v2.8 output kept)
    def check_quality(self):
        if not (run_has_quality(self.new) or run_has_quality(self.base)): return
        pt = (self.profile or {}).get("expected_patch")
        if pt and pt.get("migration_id") == P2_MIGRATION:
            valid = sorted(t for t, r in self.new.results.items() if q_valid_p0(r))
            self.add("PROF", "Q0 P2 migration run has >= 1 evaluated non-FINANCIAL Quality ticker", "PASS" if valid else "FAIL",
                     f"{len(valid)} valid: {valid[:20]}" if valid else "P2 migration run has no evaluated non-FINANCIAL Quality ticker")
        for run in (self.new, self.base):
            for row in q_check_run(run, self.targets): self.add(*row)
        v, d = quality_parity(self.repo_root)     # M2 (v2.11): only where Q runs, so pairs without Quality keep v2.10 output
        self.add("MAP", "M2 Quality v0.2 constants = production (static parse)", v, d)

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
        # B8 (v2.10, F-7) composite closure, every ticker whose gate is applied and well formed; exclusions are counted
        for run in (self.new, self.base):
            bad, checked, masks, excluded = [], 0, 0, {}
            for t, r in sorted(run.results.items()):
                g = gate(r)
                why = ("gate SKIPPED" if g.get("status") == "SKIPPED" else "gate status not OK") if g.get("status") != "OK" \
                    else ("gate malformed (B0)" if gate_structure_problems(g) else None)
                if why: excluded[why] = excluded.get(why, 0) + 1; continue
                checked += 1
                masks += sum(_d(_d(metrics(r).get(c)).get("eligibility")).get("basis") == COMPOSITE_WITHHELD_BASIS for c in COMPOSITE_COMPONENTS)
                bad += [f"{t}: {x}" for x in composite_closure_problems(r)]
            ex = ", ".join(f"{k} {v}" for k, v in sorted(excluded.items())) or "none"
            v = "FAIL" if bad else ("PASS" if checked else "INFO")
            self.add(run.label, "B8 composite closure (debt component withheld => total_debt)", v,
                     "; ".join(bad[:10]) or f"{checked} ticker(s) checked, {masks} composite mask(s); excluded: {ex}")
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
        pt, _pe, realized, _pp = self.patch()
        base_view = self.base
        if pt and realized:
            class _P: pass
            base_view = _P(); base_view.summary = self.base.summary
            try:
                base_view.results = {t: self.patched_base(t) for t in self.base.results}
            except ValueError:
                base_view = None
        if base_view is None:
            self.add("CMP", "C3 stage coverage identical BASE vs NEW once governed tickers are excluded", "FAIL",
                     f"expected_patch {pt['migration_id']} leaves an inconsistent document when applied to BASE")
        elif accepted:
            class _B: pass
            base_view = _B(); base_view.summary = self.base.summary
            base_view.results = {t: copy.deepcopy(r) for t, r in self.base.results.items()}
            for t, p in accepted:
                parent, key = parent_and_key(p)
                get_path(base_view.results[t], parent)[key] = get_path(self.new.results[t], p)
        if base_view is not None:
            same = without(self.new) == without(base_view)
            note = ""
            if accepted:
                note = (f"; BASE compared with the {len(accepted)} accepted allowance addition(s) of "
                        f"{allow['migration_id']} applied (no other change tolerated)")
            elif pt:
                n_t = sum(1 for f, _ in realized if f not in (COVERAGE_FILE, SUMMARY_FILE))
                note = (f"; BASE compared with the {n_t} realized ticker leaf change(s) of expected_patch "
                        f"{pt['migration_id']} applied (no other change tolerated)")
            self.add("CMP", "C3 stage coverage identical BASE vs NEW once governed tickers are excluded", "PASS" if same else "FAIL",
                     f"governed excluded={sorted(gov_t)}" + note)
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

    # ---------- v2.7 strict D9 (allowance and no-change modes)
    def check_d9_strict(self, base, new):
        changed = strict_coverage_changes(base.coverage, new.coverage)
        self.add("CMP", "D9 coverage.json: no change outside the C1/C3-delegated stage blocks (same evaluation date)",
                 "PASS" if not changed else "FAIL",
                 f"{len(changed)} changed coverage paths: {changed[:12]}; delegated to C1/C3: {', '.join(D9_DELEGATED_BLOCKS)}; "
                 f"run-varying excluded: {', '.join(sorted(COVERAGE_RUN_TS_PATHS))}")

    # ---------- v2.5 expected patch (shared by C3, D2 and D9)
    def patch(self):
        """(patch, entries, realized, problems). Cached. realized = entries whose BASE leaf equals `base` AND whose NEW
        leaf equals `new` (type-strict; ABSENT = leaf does not exist). A ticker entry also needs the ticker in both runs;
        on a governed ticker (withheld metric in either run) only governed_entry_problem()-approved component leaves
        are allowed (v2.6). Every unrealized entry is a problem (FAIL)."""
        if hasattr(self, "_patch_cache"): return self._patch_cache
        pt = (self.profile or {}).get("expected_patch")
        entries, realized, problems = [], set(), []
        if pt:
            b, at = pt["binding"], self.profile["applies_to"]
            actual = {"evaluation_date": self.date}
            for run, side in ((self.base, "base"), (self.new, "new")):
                prov = run.repro.get("provenance") or {}
                actual[f"{side}_github_run_id"] = str(prov.get("github_run_id")); actual[f"{side}_github_sha"] = str(prov.get("github_sha"))
            declared = {"evaluation_date": at["evaluation_date"], "base_github_run_id": at["base"]["github_run_id"], "base_github_sha": at["base"]["github_sha"],
                        "new_github_run_id": at["new"]["github_run_id"], "new_github_sha": at["new"]["github_sha"]}
            unbound = sorted(k for k in ALLOWANCE_BINDING_KEYS if b[k] != actual[k] or b[k] != declared[k])
            if unbound: problems.append(f"expected_patch {pt['migration_id']} not bound to this BASE/NEW pair ({', '.join(unbound)})")
            else:
                entries = pt["entries"]
                flats = {}
                def fl(run, f):
                    key = (run.label, f)
                    if key not in flats:
                        doc = run.coverage if f == COVERAGE_FILE else (run.summary if f == SUMMARY_FILE else run.results.get(f[:-5]))
                        flats[key] = None if doc is None else flat(doc)
                    return flats[key]
                for e in entries:
                    f, pa = e["file"], e["path"]
                    if f not in (COVERAGE_FILE, SUMMARY_FILE):
                        t = f[:-5]
                        if t not in self.base.results or t not in self.new.results:
                            problems.append(f"{t}: not an OK ticker in both runs"); continue
                        withheld = set(gate(self.base.results[t]).get("withheld") or []) | set(gate(self.new.results[t]).get("withheld") or [])
                        if withheld:
                            why = governed_entry_problem(pt["migration_id"], pa, withheld)
                            if why is not None:
                                problems.append(f"{t}{pa}: {why}"); continue
                    fb, fn = fl(self.base, f), fl(self.new, f)
                    where = f"{f if f in (COVERAGE_FILE, SUMMARY_FILE) else f[:-5]}{pa}"
                    if fb is None or fn is None: problems.append(f"{where}: document missing"); continue
                    ok_b, ok_n = same_leaf(flat_get(fb, pa), e["base"]), same_leaf(flat_get(fn, pa), e["new"])
                    if not ok_b: problems.append(f"{where}: BASE leaf differs from expected_patch base")
                    if not ok_n: problems.append(f"{where}: NEW leaf differs from expected_patch new")
                    if ok_b and ok_n: realized.add((f, pa))
        self._patch_cache = (pt, entries, realized, problems)
        return self._patch_cache

    def patched_base(self, t):
        """BASE ticker document with the realized expected_patch leaves applied (C3)."""
        _pt, entries, realized, _p = self.patch()
        f = f"{t}.json"
        mine = [e for e in entries if e["file"] == f and (f, e["path"]) in realized]
        if not mine: return self.base.results[t]
        fl = flat(self.base.results[t])
        for e in mine:
            if is_absent(e["new"]): fl.pop(e["path"], None)
            else: fl[e["path"]] = copy.deepcopy(e["new"])
        return unflatten(fl)

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
            if path in TICKER_RUN_TS_PATHS:
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
        pt, p_entries, realized, patch_problems_ = self.patch()
        p_found = set()
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
            if realized:
                keep = []
                for p in c.get("DATA_CHANGE", []):
                    if (f"{t}.json", p) in realized: c.setdefault(EXPECTED_PATCH, []).append(p); p_found.add((f"{t}.json", p))
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
        if pt:
            tick = [(f, p) for f, p in ((e["file"], e["path"]) for e in p_entries) if f not in (COVERAGE_FILE, SUMMARY_FILE)]
            tprob = [x for x in patch_problems_ if not x.startswith(COVERAGE_FILE) and not x.startswith(SUMMARY_FILE)]
            unseen = sorted(f"{f[:-5]}{p}" for f, p in set(tick) & realized - p_found)
            if unseen: tprob.append(f"{len(unseen)} registered change(s) not observed as a ticker diff: {unseen[:12]}")
            label = f"D2 ticker JSON: no data change except expected_patch {pt['migration_id']} (same evaluation date)"
            detail = (f"{len(data_changes)} changed paths (paths only): {data_changes[:12]}; expected leaf changes {len(p_found)}/{len(tick)}"
                      + (f"; {'; '.join(tprob[:8])}" if tprob else ""))
            self.add("CMP", label, "PASS" if not data_changes and not tprob and len(p_found) == len(tick) else "FAIL", detail)
            # D9 coverage.json: every leaf difference (run timestamps aside) is a realized coverage entry
            cb, cn = flat(base.coverage), flat(new.coverage)
            diff_cov = {p for p in set(cb) | set(cn)
                        if not same_leaf(flat_get(cb, p), flat_get(cn, p)) and p not in COVERAGE_RUN_TS_PATHS}
            reg_cov = {p for f, p in ((e["file"], e["path"]) for e in p_entries) if f == COVERAGE_FILE}
            cprob = [x for x in patch_problems_ if x.startswith(COVERAGE_FILE) or x.startswith("expected_patch")]
            unreg = sorted(diff_cov - {p for f, p in realized if f == COVERAGE_FILE})
            missing_c = sorted(reg_cov - diff_cov)
            if missing_c: cprob.append(f"{len(missing_c)} registered coverage change(s) not observed: {missing_c[:12]}")
            self.add("CMP", f"D9 coverage.json: no change except expected_patch {pt['migration_id']}",
                     "PASS" if not unreg and not cprob else "FAIL",
                     f"{len(unreg)} unregistered coverage paths: {unreg[:12]}; expected coverage changes "
                     f"{len(reg_cov & diff_cov & {p for f, p in realized if f == COVERAGE_FILE})}/{len(reg_cov)}"
                     + (f"; {'; '.join(cprob[:8])}" if cprob else ""))
        elif allow:
            label = f"D2 ticker JSON: no data change except allowance {allow['migration_id']} (same evaluation date)"
            detail = (f"{len(data_changes)} changed paths (paths only): {data_changes[:12]}; allowed additions {len(found)}/{len(want)}"
                      + (f"; {'; '.join(allow_problems)}" if allow_problems else ""))
            self.add("CMP", label, "PASS" if not data_changes and not allow_problems else "FAIL", detail)
            self.check_d9_strict(base, new)
        else:
            self.add("CMP", "D2 ticker JSON: no data change (same evaluation date)", "PASS" if not data_changes else "FAIL",
                     f"{len(data_changes)} changed paths (paths only): {data_changes[:12]}")
            self.check_d9_strict(base, new)
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
        allowed_summary = SUMMARY_RUN_PATHS
        fa, fb = flat(base.summary), flat(new.summary)
        reg_s = [p for f, p in ((e["file"], e["path"]) for e in p_entries) if f == SUMMARY_FILE] if pt else []
        if not reg_s:
            bad = [p for p in sorted(set(fa) | set(fb)) if fa.get(p) != fb.get(p) and not allowed_summary.match(p)]
            self.add("CMP", "D7 summary.json differs only in run metadata / provenance / raw_input hashes", "PASS" if not bad else "FAIL", f"other changes: {bad[:10]}")
        else:
            # v2.9: summary.json leaves registered in expected_patch (exact, pair-bound, realized in BASE and NEW)
            real_s = {p for f, p in realized if f == SUMMARY_FILE}
            diff_s = {p for p in set(fa) | set(fb) if not same_leaf(flat_get(fa, p), flat_get(fb, p))}
            bad = sorted(p for p in diff_s if not allowed_summary.match(p) and p not in real_s)
            sprob = [x for x in patch_problems_ if x.startswith(SUMMARY_FILE)]
            unseen = sorted(set(reg_s) - diff_s)
            if unseen: sprob.append(f"{len(unseen)} registered summary change(s) not observed: {unseen[:12]}")
            self.add("CMP", f"D7 summary.json differs only in run metadata / provenance / raw_input hashes and expected_patch {pt['migration_id']}",
                     "PASS" if not bad and not sprob else "FAIL",
                     f"other changes: {bad[:10]}; expected summary changes {len(real_s & diff_s)}/{len(reg_s)}"
                     + (f"; {'; '.join(sprob[:8])}" if sprob else ""))
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
        # F2 (v2.10): inside each ticker result, outside the gate's audit copy (the only place the targets come from)
        for run in (self.new, self.base):
            fail, review, scanned, n_t = [], [], 0, 0
            for t, r in sorted(run.results.items()):
                res = ticker_leak_scan(t, r)
                if res is None: continue
                scanned += 1; n_t += res[2]
                fail += [f"{t}{p}" for p in res[0]]; review += [f"{t}{p}" for p in res[1]]
            v = "FAIL" if fail else ("REVIEW" if review else "PASS")
            self.add(run.label, "F2 no WITHHELD original inside ticker results (audit copy excluded)", v,
                     f"{scanned} ticker(s) with originals, {n_t} amount(s) scanned; leak locations={fail[:10]}; "
                     f"ambiguous small-value matches={review[:10]}" if scanned else "no WITHHELD originals: nothing to scan")

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
                         ("coverage (C)", self.check_coverage), ("quality (Q)", self.check_quality),
                         ("run comparison (D)", self.check_diff),
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
    if "expected_patch" in prof:
        if "allowed_added_paths" in prof:
            raise InputError(f"profile {arg}: allowed_added_paths and expected_patch cannot be combined (one migration per profile)")
        problems = patch_problems(prof["expected_patch"])
        if problems: raise InputError(f"profile {arg}: expected_patch invalid ({problems[0]})")
    return prof


def binding_problems(b) -> list[str]:
    if not isinstance(b, dict) or set(b) != ALLOWANCE_BINDING_KEYS: return [f"binding keys must be exactly {sorted(ALLOWANCE_BINDING_KEYS)}"]
    p = []
    if not is_iso_date(b["evaluation_date"]): p.append("binding.evaluation_date must be YYYY-MM-DD")
    for side in ("base", "new"):
        rid, sha = b[f"{side}_github_run_id"], b[f"{side}_github_sha"]
        if not (isinstance(rid, str) and rid.isdigit()): p.append(f"binding.{side}_github_run_id must be a digit string")
        if not (isinstance(sha, str) and SHA40.fullmatch(sha)): p.append(f"binding.{side}_github_sha must be a full 40-hex SHA")
    return p


def is_absent(v) -> bool:
    return isinstance(v, dict) and len(v) == 1 and v.get("$absent") is True and type(v["$absent"]) is bool


def same_leaf(a, b) -> bool:
    """Type-strict leaf equality (True != 1, 1 != 1.0); ABSENT only equals ABSENT."""
    if is_absent(a) or is_absent(b): return is_absent(a) and is_absent(b)
    return type(a) is type(b) and a == b


def _patch_value_problem(v, path, containers=None, kind=None) -> str | None:
    if is_absent(v): return None
    if containers is not None:
        # v2.9: per-migration exact table -- (migration, file kind, exact path, container type); the global
        # last-key-name table is not consulted
        want = container_for(containers, kind, path)
        if isinstance(v, (dict, list)):
            if want is not None and type(v) is type(want) and v == want: return None
            return "value must be a scalar (an empty container only at a registered container path of this migration)"
        if want is not None:
            return "a registered container path takes only its empty container or ABSENT"
        if isinstance(v, float) and not math.isfinite(v): return "non-finite number"
        if v is None or isinstance(v, (str, bool, int, float)): return None
        return "unsupported value type"
    last = None if path.endswith("]") else path.rsplit("/", 1)[-1]      # "[i]" = list item, never a container path
    if isinstance(v, (dict, list)):
        if last in PATCH_CONTAINER_LEAF and v == PATCH_CONTAINER_LEAF[last] and type(v) is type(PATCH_CONTAINER_LEAF[last]):
            return None
        return "value must be a scalar (only an empty container at a container path; no parent replacement)"
    if last in PATCH_CONTAINER_LEAF:
        return "a container path takes only its empty container or ABSENT"
    if isinstance(v, float) and not math.isfinite(v): return "non-finite number"
    if v is None or isinstance(v, (str, bool, int, float)): return None
    return "unsupported value type"


def _is_child(parent, child) -> bool:
    return child.startswith(parent) and len(child) > len(parent) and child[len(parent)] in "/["


def patch_problems(pt) -> list[str]:
    """Structural validation of a v2.5 expected_patch. Any deviation is an input error (fail closed)."""
    if not isinstance(pt, dict) or set(pt) != PATCH_KEYS: return [f"keys must be exactly {sorted(PATCH_KEYS)}"]
    mid = pt["migration_id"]
    if not isinstance(mid, str) or mid not in PATCH_MIGRATIONS:          # type first: [] / {} are unhashable
        return [f"migration_id must be one of the registered migrations {sorted(PATCH_MIGRATIONS)}"]
    p = binding_problems(pt["binding"])
    entries = pt["entries"]
    if not isinstance(entries, list) or not entries: return p + ["entries must be a non-empty list"]
    grammar, seen, by_file = PATCH_MIGRATIONS[mid], set(), {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or set(e) != PATCH_ENTRY_KEYS: p.append(f"entry {i}: keys must be exactly {sorted(PATCH_ENTRY_KEYS)}"); continue
        f, pa, op, b, n = e["file"], e["path"], e["op"], e["base"], e["new"]
        summary_ok = "summary" in grammar          # v2.9: only a migration with a summary grammar may patch summary.json
        if not isinstance(f, str) or not (f == COVERAGE_FILE or PATCH_TICKER_FILE_RE.fullmatch(f) or (summary_ok and f == SUMMARY_FILE)):
            p.append(f"entry {i}: file must be coverage.json or <TICKER>.json"
                     + (" or summary.json" if summary_ok else "")); continue
        kind = "coverage" if f == COVERAGE_FILE else ("summary" if f == SUMMARY_FILE else "ticker")
        if not isinstance(pa, str) or not grammar[kind].fullmatch(pa):
            p.append(f"entry {i}: {f}{pa!r} is not an allowed leaf of {mid}"); continue
        if kind == "summary" and SUMMARY_RUN_PATHS.fullmatch(pa):
            p.append(f"entry {i}: {f}{pa} is a D7 run-metadata path; it cannot also be registered"); continue
        ops = grammar.get("ops", PATCH_OPS)
        if not isinstance(op, str) or op not in ops: p.append(f"entry {i}: op must be one of {list(ops)}"); continue
        containers = grammar.get("containers")
        bad = _patch_value_problem(b, pa, containers, kind) or _patch_value_problem(n, pa, containers, kind)
        if bad: p.append(f"entry {i}: {f}{pa}: {bad}"); continue
        if op == "add" and not (is_absent(b) and not is_absent(n)): p.append(f"entry {i}: add needs base ABSENT and new present"); continue
        if op == "remove" and not (not is_absent(b) and is_absent(n)): p.append(f"entry {i}: remove needs base present and new ABSENT"); continue
        if op == "replace" and (is_absent(b) or is_absent(n) or same_leaf(b, n)):
            p.append(f"entry {i}: replace needs base and new present and different"); continue
        if (f, pa) in seen: p.append(f"entry {i}: duplicate {f}{pa}"); continue
        seen.add((f, pa)); by_file.setdefault(f, []).append(e)
    # parent/child overlap: forbidden, except the flat() emptiness pair -- a container leaf [] / {} exists exactly when the
    # container has no children, so "emptied" = container add + every child remove, "filled" = container remove + every child add
    for f, es in by_file.items():
        for c in es:
            kids = [k for k in es if _is_child(c["path"], k["path"])]
            if not kids: continue
            if "containers" in grammar:
                fkind = "coverage" if f == COVERAGE_FILE else ("summary" if f == SUMMARY_FILE else "ticker")
                is_container = container_for(grammar["containers"], fkind, c["path"]) is not None
            else:
                is_container = c["path"].rsplit("/", 1)[-1] in PATCH_CONTAINER_LEAF
            ok = is_container and ((c["op"] == "add" and all(k["op"] == "remove" for k in kids))
                                   or (c["op"] == "remove" and all(k["op"] == "add" for k in kids)))
            if not ok: p.append(f"{f}: overlapping parent/child entries under {c['path']}")
    return p


def allowance_problems(al) -> list[str]:
    """Structural validation of a v2.4 migration allowance. Any deviation is an input error (fail closed)."""
    if not isinstance(al, dict) or set(al) != ALLOWANCE_KEYS: return [f"keys must be exactly {sorted(ALLOWANCE_KEYS)}"]
    p = []
    if not isinstance(al["migration_id"], str) or not al["migration_id"].strip(): p.append("migration_id must be a non-empty string")
    p += binding_problems(al["binding"])
    value_set = al["value_set"]
    if not isinstance(value_set, str) or value_set not in ALLOWANCE_VALUE_SETS:   # type first: [] / {} are unhashable
        p.append(f"value_set must be one of the registered string value sets {sorted(ALLOWANCE_VALUE_SETS)}")
    paths = al["paths"]
    if not isinstance(paths, list) or not paths: p.append("paths must be a non-empty list")
    else:
        seen = set()
        rx = ALLOWANCE_PATH_RE.get(value_set) if isinstance(value_set, str) else None
        for e in paths:
            if not isinstance(e, dict) or set(e) != {"ticker", "path"}: p.append("each path entry must be exactly {ticker, path}"); continue
            tk, pa = e["ticker"], e["path"]
            if not (isinstance(tk, str) and TICKER_RE.fullmatch(tk)): p.append(f"bad ticker {tk!r}"); continue
            if not (isinstance(pa, str) and rx and rx.fullmatch(pa)): p.append(f"{tk}: path not an exact allowed leaf: {pa!r}"); continue
            if (tk, pa) in seen: p.append(f"duplicate entry {tk}{pa}")
            seen.add((tk, pa))
    return p


_STATIC_CALLS = {"frozenset": frozenset, "set": set, "tuple": tuple}


def _static_eval(node):
    """A literal, or frozenset/set/tuple applied to exactly one literal (no keywords). Anything else: ValueError."""
    if isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id in _STATIC_CALLS and len(node.args) == 1 and not node.keywords):
            raise ValueError("call is not frozenset/set/tuple of one literal")
        return _STATIC_CALLS[node.func.id](ast.literal_eval(node.args[0]))
    return ast.literal_eval(node)


def _names_in(target):
    if isinstance(target, ast.Name): return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)): return [n for e in target.elts for n in _names_in(e)]
    if isinstance(target, ast.Starred): return _names_in(target.value)
    return []


def static_value(path, name):
    """v2.11: value of the single module-level binding of `name`, by AST (no import, no execution). Module level
    only (function locals never count). Every top-level binding counts: Assign (each of several targets), AnnAssign,
    AugAssign, and names inside tuple/list unpacking -- more than one binding, or a binding that is not a plain
    `name = <value>` (unpacking, augmented), is unverifiable (ValueError), as is any value _static_eval rejects."""
    with open(path, encoding="utf-8") as fh: tree = ast.parse(fh.read(), filename=path)
    plain, other = [], 0
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == name: plain.append(n.value)
                elif name in _names_in(t): other += 1
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name:
            if n.value is None: other += 1
            else: plain.append(n.value)
        elif isinstance(n, ast.AugAssign) and name in _names_in(n.target):
            other += 1
    if other or len(plain) != 1: raise ValueError(f"{name}: {len(plain)} plain and {other} other top-level binding(s)")
    return _static_eval(plain[0])


def static_star_bands(path, func="_stars_from_rating"):
    """v2.11: ((threshold, stars), ...), floor) from the body of the single module-level `func(<arg>)`, which must be
    exactly: optional docstring, then `if <arg> >= <number>: return <int>` (no else) repeated, then `return <int>`.
    Any other shape is unverifiable (ValueError); nothing is guessed."""
    with open(path, encoding="utf-8") as fh: tree = ast.parse(fh.read(), filename=path)
    fs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func]
    if len(fs) != 1: raise ValueError(f"{func}: {len(fs)} module-level definitions")
    f = fs[0]; a = f.args
    if isinstance(f, ast.AsyncFunctionDef) or len(a.args) != 1 or a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg:
        raise ValueError(f"{func}: not a plain one-argument function")
    arg, body = a.args[0].arg, list(f.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    def num(n, ints=False):
        return isinstance(n, ast.Constant) and not isinstance(n.value, bool) and isinstance(n.value, int if ints else (int, float))
    def ret_int(n): return isinstance(n, ast.Return) and n.value is not None and num(n.value, ints=True)
    if not body or not ret_int(body[-1]): raise ValueError(f"{func}: does not end with `return <int>`")
    bands = []
    for st in body[:-1]:
        ok = (isinstance(st, ast.If) and not st.orelse and isinstance(st.test, ast.Compare)
              and isinstance(st.test.left, ast.Name) and st.test.left.id == arg
              and len(st.test.ops) == 1 and isinstance(st.test.ops[0], ast.GtE)
              and len(st.test.comparators) == 1 and num(st.test.comparators[0])
              and len(st.body) == 1 and ret_int(st.body[0]))
        if not ok: raise ValueError(f"{func}: statement at line {st.lineno} is not `if {arg} >= <number>: return <int>`")
        bands.append((st.test.comparators[0].value, st.body[0].value.value))
    return tuple(bands), body[-1].value.value


def static_dict(path, name):
    """Literal value of a top-level `name = {...}` assignment, by AST (no import, no execution)."""
    with open(path, encoding="utf-8") as fh: tree = ast.parse(fh.read(), filename=path)
    found = [n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    found += [n.value for n in tree.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name and n.value]
    if len(found) != 1: raise ValueError(f"{name}: {len(found)} top-level assignments")
    return ast.literal_eval(found[0])


# v2.6 (STEP 24.6-N P0b): GOVERNED_PATCH_SCOPE's component -> input declarations must equal production's own map.
GOVERNED_SCOPE_SOURCE = {"file": "src/fundamentals/orchestrator.py", "name": "_GROWTH_METRIC_CONCEPT_DEPENDENCIES"}


def governed_scope_parity_problems(path, scope=None) -> list[str]:
    """Static (AST, no import, no execution) parity of GOVERNED_PATCH_SCOPE with production's
    _GROWTH_METRIC_CONCEPT_DEPENDENCIES in `path`. [] = every scoped component exists in production with exactly the
    same input metrics. Anything unverifiable is a problem (fail closed)."""
    scope = GOVERNED_PATCH_SCOPE if scope is None else scope
    name = GOVERNED_SCOPE_SOURCE["name"]
    try:
        prod = static_dict(path, name)
    except (OSError, SyntaxError, ValueError, TypeError, UnicodeDecodeError) as e:
        return [f"{name} not statically verifiable ({type(e).__name__}: {e})"]
    if not isinstance(prod, dict) or not all(isinstance(k, str) and isinstance(v, (tuple, list)) for k, v in prod.items()):
        return [f"{name} is not a literal dict of component -> input tuples"]
    problems = []
    for mig, comps in sorted(scope.items()):
        for comp, inputs in sorted(comps.items()):
            if comp not in prod:
                problems.append(f"{mig}: component {comp!r} is not declared in production {name}")
            elif tuple(prod[comp]) != tuple(inputs):
                problems.append(f"{mig}: {comp!r} inputs {tuple(inputs)} != production {tuple(prod[comp])}")
    return problems


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


QUALITY_PARITY_SOURCES = {"quality": "src/fundamentals/quality.py", "batch_runner": "src/fundamentals/batch_runner.py",
                          "company_type": "src/fundamentals/company_type_registry.py", "eligibility": MAPPING_SOURCE["file"]}
_UNVERIFIED = object()


def quality_parity(repo_root):
    """M2 (v2.11): (verdict, detail). Quality v0.2 constants the Q checks copy, compared with production by static
    parse (no import). dict: keys and values; tuple: order; set: members; str: exact; int: type and value; booleans
    in _V02_INPUTS: type and value. PASS only when every item was read and equal; any drift or anything not
    statically verifiable is REVIEW (names the constant and the key)."""
    if not repo_root:
        return "REVIEW", "production sources not available (no --repo-root); Quality constants are the Checker's copies"
    probs, checked = [], []
    def path(src): return os.path.join(repo_root, *QUALITY_PARITY_SOURCES[src].split("/"))
    def get(src, name):
        try:
            return static_value(path(src), name)
        except (OSError, SyntaxError, ValueError, TypeError, UnicodeDecodeError) as e:
            probs.append(f"{QUALITY_PARITY_SOURCES[src]}:{name} not statically verifiable ({type(e).__name__})"); return _UNVERIFIED
    def same(label, prod, local, kind):
        if prod is _UNVERIFIED: return
        checked.append(label)
        if kind == "set":
            if not isinstance(prod, (set, frozenset)): probs.append(f"{label}: production is {type(prod).__name__}, not a set"); return
            if set(prod) != set(local): probs.append(f"{label}: production-only {sorted(set(prod) - set(local))}, checker-only {sorted(set(local) - set(prod))}")
        elif kind == "dict":
            if not isinstance(prod, dict): probs.append(f"{label}: production is {type(prod).__name__}, not a dict"); return
            for k in sorted(set(prod) | set(local), key=str):
                pv, lv = prod.get(k, "<absent>"), local.get(k, "<absent>")
                if type(pv) is not type(lv) or pv != lv: probs.append(f"{label}[{k!r}]: production {pv!r} vs checker {lv!r}")
        else:   # exact: same type and value (tuple order included; int is never float/bool)
            if type(prod) is not type(local) or prod != local: probs.append(f"{label}: production {prod!r} vs checker {local!r}")
    same("QUALITY_V02_SCHEMA_VERSION", get("quality", "QUALITY_V02_SCHEMA_VERSION"), Q_SCHEMA_VERSION, "exact")
    same("_QUALITY_V02_BASE_WEIGHTS", get("quality", "_QUALITY_V02_BASE_WEIGHTS"), Q_BASE_WEIGHTS, "dict")
    same("QUALITY_V02_INDEPENDENT_METRICS", get("quality", "QUALITY_V02_INDEPENDENT_METRICS"), Q_INDEP, "exact")
    same("QUALITY_V02_INFORMATIONAL_METRICS", get("quality", "QUALITY_V02_INFORMATIONAL_METRICS"), Q_INFO, "dict")
    same("QUALITY_V02_DIMENSIONS", get("quality", "QUALITY_V02_DIMENSIONS"), Q_DIMS, "dict")
    same("QUALITY_V02_MIN_RATED", get("quality", "QUALITY_V02_MIN_RATED"), Q_MIN_RATED, "exact")
    same("QUALITY_V02_MIN_RATED_PLUS_NOT_MEANINGFUL", get("quality", "QUALITY_V02_MIN_RATED_PLUS_NOT_MEANINGFUL"), Q_MIN_RATED_PLUS_NM, "exact")
    same("QUALITY_REASON_CODES", get("quality", "QUALITY_REASON_CODES"), Q_REASON_CODES, "set")
    same("QUALITY_V02_FLAGS", get("quality", "QUALITY_V02_FLAGS"), {Q_FLAG}, "set")
    same("LIQUIDITY_BASIS_CASH_ONLY", get("quality", "LIQUIDITY_BASIS_CASH_ONLY"), Q_LIQUIDITY_BASIS, "exact")
    same("FLOW_BASIS_ANNUAL_FY", get("quality", "FLOW_BASIS_ANNUAL_FY"), Q_FLOW_BASIS, "exact")
    same("_V02_COMPONENT_STATUSES", get("quality", "_V02_COMPONENT_STATUSES"), Q_COMPONENT_STATUSES, "set")
    same("_FLOW_INPUTS", get("quality", "_FLOW_INPUTS"), Q_FLOW_INPUTS, "exact")
    same("_FLOW_SIGNATURE_KEYS", get("quality", "_FLOW_SIGNATURE_KEYS"), Q_FLOW_SIG, "exact")
    vi = get("quality", "_V02_INPUTS")
    if vi is not _UNVERIFIED:
        if not (isinstance(vi, dict) and all(isinstance(v, tuple) and len(v) == 4 for v in vi.values())):
            probs.append("_V02_INPUTS: not a dict of 4-tuples")
        else:
            same("_V02_INPUTS keys (Q_METRICS, order)", tuple(vi), Q_METRICS, "exact")
            for m in Q_METRICS:
                if m not in vi: continue
                same(f"_V02_INPUTS[{m!r}][0] normalized inputs", vi[m][0], Q_INPUTS[m][0], "exact")
                same(f"_V02_INPUTS[{m!r}][1] derived inputs", vi[m][1], Q_INPUTS[m][1], "exact")
                same(f"_V02_INPUTS[{m!r}][2] uses flow", vi[m][2], Q_USES_FLOW[m], "exact")
    same("_UPSTREAM_PRECEDENCE", get("quality", "_UPSTREAM_PRECEDENCE"), Q_UPSTREAM_ORDER, "exact")
    same("_UPSTREAM_TO_V02", get("quality", "_UPSTREAM_TO_V02"), Q_UPSTREAM_MAP, "dict")
    try:
        bands, floor = static_star_bands(path("quality"))
        same("_stars_from_rating bands", bands, Q_STAR_BANDS, "exact")
        same("_stars_from_rating floor", floor, Q_STAR_FLOOR, "exact")
    except (OSError, SyntaxError, ValueError, TypeError, UnicodeDecodeError) as e:
        probs.append(f"{QUALITY_PARITY_SOURCES['quality']}:_stars_from_rating not statically verifiable ({type(e).__name__}: {e})")
    same("QUALITY_SUMMARY_KEYS", get("batch_runner", "QUALITY_SUMMARY_KEYS"), Q_SUMMARY_KEYS, "exact")
    same("COMPANY_TYPE_VALUES", get("company_type", "COMPANY_TYPE_VALUES"), Q_COMPANY_TYPES, "set")
    if Q_EVALUATED_COMPANY_TYPE not in Q_COMPANY_TYPES: probs.append(f"Q_EVALUATED_COMPANY_TYPE {Q_EVALUATED_COMPANY_TYPE!r} not a company type")
    gm = get("eligibility", "GOVERNED_METRICS")
    if gm is not _UNVERIFIED:
        if not (isinstance(gm, tuple) and all(isinstance(x, str) for x in gm)): probs.append("GOVERNED_METRICS: not a tuple of names")
        else:
            q_norm = {x for m in Q_METRICS for x in Q_INPUTS[m][0]}
            same("GOVERNED_METRICS x Quality inputs (Q_GOVERNED_INPUTS)", tuple(x for x in gm if x in q_norm), Q_GOVERNED_INPUTS, "exact")
    cg = get("eligibility", "COMPOSITE_GOVERNED_METRICS")
    if cg is not _UNVERIFIED:
        if not (isinstance(cg, dict) and isinstance(cg.get("total_debt"), (tuple, list))): probs.append("COMPOSITE_GOVERNED_METRICS['total_debt']: absent or not a sequence")
        else: same("COMPOSITE_GOVERNED_METRICS['total_debt'] (Q_DEBT_COMPONENTS)", tuple(cg["total_debt"]), Q_DEBT_COMPONENTS, "exact")
    if probs:
        return "REVIEW", f"{len(probs)} problem(s): " + "; ".join(probs)
    return "PASS", f"{len(checked)} comparison(s) equal across {', '.join(QUALITY_PARITY_SOURCES.values())}"


def composite_mapping_drift(repo_root):
    """M3 (v2.10): (verdict, detail). PASS only when production's COMPOSITE_GOVERNED_METRICS and
    COMPOSITE_WITHHELD_BASIS parse as literals of the expected types and equal the checker's copies (parts order
    included). Anything not statically verifiable is REVIEW, never PASS."""
    src = MAPPING_SOURCE["file"]
    if not repo_root:
        return "REVIEW", f"production registry not available (no --repo-root); composite map is the copy of {COMPOSITE_SOURCE['commit'][:7]}"
    path = os.path.join(repo_root, *src.split("/"))
    prod = {}
    for local_name, prod_name in COMPOSITE_SOURCE["names"].items():
        try:
            prod[local_name] = static_dict(path, prod_name)
        except (OSError, SyntaxError, ValueError, TypeError, UnicodeDecodeError) as e:
            return "REVIEW", f"{src}: {prod_name} not statically verifiable ({type(e).__name__}: {e})"
    pm, pb = prod["COMPOSITE_COMPONENTS"], prod["COMPOSITE_WITHHELD_BASIS"]
    if not (isinstance(pm, dict) and all(isinstance(k, str) and isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v) for k, v in pm.items())):
        return "REVIEW", f"{src}: COMPOSITE_GOVERNED_METRICS is not a dict of str -> sequence of str"
    if not isinstance(pb, str):
        return "REVIEW", f"{src}: COMPOSITE_WITHHELD_BASIS is not a string literal"
    diffs = []
    norm = {k: tuple(v) for k, v in pm.items()}
    for k in sorted(set(norm) | set(COMPOSITE_COMPONENTS)):
        if norm.get(k) != COMPOSITE_COMPONENTS.get(k): diffs.append(f"COMPOSITE_GOVERNED_METRICS[{k!r}]: production {norm.get(k)} vs checker {COMPOSITE_COMPONENTS.get(k)}")
    if pb != COMPOSITE_WITHHELD_BASIS: diffs.append(f"COMPOSITE_WITHHELD_BASIS: production {pb!r} vs checker {COMPOSITE_WITHHELD_BASIS!r}")
    if diffs:
        return "REVIEW", f"checker composite constants are stale vs {src} (copied at {COMPOSITE_SOURCE['commit'][:7]}): " + "; ".join(diffs)
    return "PASS", f"{', '.join(COMPOSITE_SOURCE['names'].values())} equal to {src}"


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
