"""STEP 23 Checker v2.3: synthetic adversarial tests (stdlib unittest; synthetic fixtures only).

The checker is loaded from scripts/ by file path (like the verify_sec_snapshot tests). No production module
is imported; the production registry is only read as text by the M1 / mapping-sync checks.
"""
import contextlib, copy, hashlib, importlib.util, io, json, os, sys, tempfile, unittest, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("step23_checker_v2_3", REPO / "scripts" / "step23_checker_v2_3.py")
ck = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ck
_spec.loader.exec_module(ck)

DATE, CUT = "2026-11-03", "2026-11-02"
SHA_N, SHA_B = "a" * 40, "b" * 40
W = ["operating_income", "cfo", "capex"]
SECRET = {"cfo": 5_123_000_000, "capex": -1_234_000_000, "operating_income": 7_777_000_000}
RUN_N, RUN_B = "222", "111"
PROFILE = {
    "profile_version": 1, "name": "SYNTH",
    "applies_to": {"evaluation_date": DATE,
                   "base": {"github_run_id": RUN_B, "github_sha": SHA_B, "governance_policy_version": ck.POLICY_V1},
                   "new": {"github_run_id": RUN_N, "github_sha": SHA_N, "governance_policy_version": ck.POLICY_V2}},
    "gate": {"GE": {"withheld": W, "scoped": ["total_debt"], "not_yet_available": [], "no_record": []}},
    "relabelled": {"GE": {"derived": ["fcf", "fcf_margin", "cfo_yoy", "capex_yoy", "operating_margin", "operating_income_yoy"],
                          "growth": ["fcf_yoy", "operating_income_yoy"], "data_sufficiency": ["fcf_yoy", "operating_income_yoy"]}},
    "materialization": {"GE": {"derived": {"cfo_yoy": [False, None], "fcf": [True, "MISSING"]}}},
    "derived_totals": {"cfo_yoy": [2, 3], "operating_income_yoy": [1, 2]},
    "derived_status_new": {"cfo_yoy": {"OK": 2, "WITHHELD": 1}},
    "non_governance_gaps": {"LLY": {"derived": {"operating_income_yoy": "ABSENT"},
                                    "data_sufficiency": {"operating_income_yoy": "MISSING/CONCEPT_NOT_FOUND"}}},
    "diff_classes": {"GE": ["RUN_METADATA", "GOVERNANCE_MARKER"], "AAPL": ["RUN_METADATA", "FRAME_METADATA_ONLY"], "*": ["RUN_METADATA"]},
    "raw_changed": ["AAPL"],
    "post_cutoff_cases": [{"ticker": "AAPL", "accession": "0000000001-26-000099", "form": "10-K", "metric": "revenue"}],
}


def metric(name, value, accn="0000000001-26-000010", frame=None, **kw):
    m = {"metric": name, "status": "OK", "namespace": "us-gaap", "concept": name.title(), "unit": "USD", "value": value,
         "period_start": "2025-01-01", "period_end": "2025-12-31", "filing_date": "2026-02-01", "form": "10-K",
         "fy": 2025, "fp": "FY", "accession": accn, "frame": frame}
    m.update(kw); return m


def total_debt_composite(status="OK"):
    """Production shape (sec_normalizer.calculate_total_debt): parent without SEC identity, exactly two components."""
    return {"metric": "total_debt", "status": status, "value": 20_000 if status == "OK" else None, "unit": "USD",
            "components": {"current_debt": metric("current_debt", 5_000), "noncurrent_debt": metric("noncurrent_debt", 15_000)}}


def v1_marker(inputs, orig="MISSING"):
    return {"status": "WITHHELD", "value": None, "withheld_inputs": inputs, "original_status": orig, "reason": "dep"}


def v2_marker(inputs, mat, orig, stage="derived", name=None):
    if stage == "data_sufficiency":
        return {"metric": name, "calculation_status": "WITHHELD", "reason_code": "GOVERNANCE_WITHHELD", "withheld_inputs": inputs,
                "materialized": mat, "original_status": orig, "set_by": "DEC055_GATE", "detail": "dep"}
    return {"status": "WITHHELD", "value": None, "reason_code": "GOVERNANCE_WITHHELD", "withheld_inputs": inputs,
            "materialized": mat, "original_status": orig, "set_by": "DEC055_GATE", "reason": "dep"}


def ok(v=0.1): return {"status": "OK", "value": v}


def ticker(t, policy, frame="CY2025"):
    v2 = policy == "V2"
    metrics = {"revenue": metric("revenue", 100_000 + len(t), frame=frame if t == "AAPL" else None),
               "cash": metric("cash", 50_000), "total_debt": total_debt_composite(),
               "cfo": metric("cfo", 30_000), "capex": metric("capex", -5_000), "operating_income": metric("operating_income", 40_000)}
    gate = {"status": "OK", "withheld": [], "scoped": [], "not_yet_available": [], "no_record": ["cfo"],
            "unmasked_originals": {}, "relabelled": {"derived": [], "growth": [], "data_sufficiency": []}}
    derived = {"fcf": ok(), "fcf_margin": ok(), "cfo_yoy": ok(), "capex_yoy": ok(), "operating_margin": ok(),
               "operating_income_yoy": ok(), "net_debt": ok(), "debt_to_cash": ok()}
    comps = {"fcf_yoy": ok(), "operating_income_yoy": ok()}
    suff = {"fcf_yoy": {"metric": "fcf_yoy", "calculation_status": "OK"}, "operating_income_yoy": {"metric": "operating_income_yoy", "calculation_status": "OK"}}
    agg = {"status": "OK"}
    if t == "LLY":
        derived.pop("operating_income_yoy")
        comps["operating_income_yoy"] = {"status": "MISSING", "value": None}
        suff["operating_income_yoy"] = {"metric": "operating_income_yoy", "calculation_status": "MISSING", "reason_code": "CONCEPT_NOT_FOUND"}
    if t == "GE":
        uo = {m: metric(m, SECRET[m]) for m in W}
        for m in W: metrics[m] = {"metric": m, "status": "WITHHELD", "value": None}
        metrics["total_debt"]["eligibility"] = {"governance_status": "ELIGIBLE", "use_scope": "CONSOLIDATED_REPORTED"}
        gate = {"status": "OK", "withheld": list(W), "scoped": ["total_debt"], "not_yet_available": [], "no_record": [],
                "unmasked_originals": uo}
        derived["net_debt"] = {"status": "MISSING", "value": None}
        derived["debt_to_cash"] = {"status": "MISSING", "value": None}
        for n in ("cfo_yoy", "capex_yoy", "operating_income_yoy"): derived.pop(n)
        if v2:
            derived.update(fcf=v2_marker(["cfo", "capex"], True, "MISSING"), fcf_margin=v2_marker(["cfo", "capex"], True, "MISSING"),
                           operating_margin=v2_marker(["operating_income"], True, "MISSING"),
                           cfo_yoy=v2_marker(["cfo"], False, None), capex_yoy=v2_marker(["capex"], False, None),
                           operating_income_yoy=v2_marker(["operating_income"], False, None))
            comps = {"fcf_yoy": v2_marker(["cfo", "capex"], True, "MISSING"), "operating_income_yoy": v2_marker(["operating_income"], True, "MISSING")}
            suff = {"fcf_yoy": v2_marker(["cfo", "capex"], True, "MISSING", "data_sufficiency", "fcf_yoy"),
                    "operating_income_yoy": v2_marker(["operating_income"], True, "MISSING", "data_sufficiency", "operating_income_yoy")}
            gate["relabelled"] = copy.deepcopy(PROFILE["relabelled"]["GE"])
        else:
            derived.update(fcf=v1_marker(["cfo", "capex"]), fcf_margin=v1_marker(["cfo", "capex"]), operating_margin=v1_marker(["operating_income"]))
            comps = {"fcf_yoy": v1_marker(["cfo", "capex"]), "operating_income_yoy": v1_marker(["operating_income"])}
            suff = {n: {"metric": n, "calculation_status": "WITHHELD", "reason_code": "GOVERNANCE_WITHHELD", "detail": "d"} for n in comps}
            gate["relabelled"] = {"derived": ["fcf", "fcf_margin", "operating_margin"], "growth": ["fcf_yoy", "operating_income_yoy"],
                                  "data_sufficiency": ["fcf_yoy", "operating_income_yoy"]}
        agg = {"status": "OK", "withheld_components": ["fcf_yoy", "operating_income_yoy"]}
    return {"schema_version": "orchestrator_v0.2", "ticker": t, "evaluation_date": DATE, "sec_filed_cutoff_date": CUT,
            "stages": {"pit_gating": {"status": "OK", "sec_filed_cutoff_date": CUT},
                       "normalization": {"normalized": {"metrics": metrics, "normalized_at": f"2026-11-03T1{policy[-1]}:00:00Z"}},
                       "eligibility_gate": gate,
                       "derived_metrics": {"status": "OK", "derived": {"derived_metrics": derived}},
                       "growth": {"status": "OK", "growth": {"components": comps, "growth": agg}},
                       "data_sufficiency": {"status": "OK", "sufficiency": suff}}}


def raw_file(t, policy):
    facts = [{"accn": "0000000001-26-000010", "filed": "2026-02-01", "form": "10-K", "val": 1, "end": "2025-12-31"}]
    if t == "AAPL" and policy == "V2":
        facts.append({"accn": "0000000001-26-000099", "filed": "2026-11-03", "form": "10-K", "val": 2, "end": "2026-09-30"})
    return json.dumps({"cik": 1, "facts": {"us-gaap": {"Revenue": {"units": {"USD": facts}}}}}).encode()


def canon(o): return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class Pair:
    """Builds BASE (V1) and NEW (V2) runs as {name: bytes}; tests mutate the dicts, then finish()."""

    def __init__(self):
        self.tickers = ["AAPL", "GE", "LLY"]
        self.res = {"NEW": {t: ticker(t, "V2", frame=None) for t in self.tickers}, "BASE": {t: ticker(t, "V1") for t in self.tickers}}
        self.raw = {lab: {f"{t}_companyfacts.json": raw_file(t, "V2" if lab == "NEW" else "V1") for t in self.tickers} for lab in ("NEW", "BASE")}
        self.cov_mut = {"NEW": None, "BASE": None}
        self.summary_mut = {"NEW": None, "BASE": None}
        self.md_extra = {"NEW": "", "BASE": ""}
        self.policy = {"NEW": ck.POLICY_V2, "BASE": ck.POLICY_V1}
        self.manifest_mut = {"NEW": None, "BASE": None}

    def files(self, lab):
        res, raw = self.res[lab], self.raw[lab]
        entries = [{"path": p, "ticker": p.split("_")[0], "cik": "0000000001", "source": "FETCHED", "size_bytes": len(b),
                    "sha256": hashlib.sha256(b).hexdigest()} for p, b in sorted(raw.items())]
        records_entries = [dict(e) for e in entries]
        if self.manifest_mut[lab]: self.manifest_mut[lab](entries)
        man = {"manifest_version": "SEC_SNAPSHOT_MANIFEST_V1", "status": "OK", "file_count": len(entries), "files": entries,
               "inputs_without_stored_file": []}
        mb = canon(man)
        content = hashlib.sha256(canon([{k: e.get(k) for k in ("path", "size_bytes", "sha256")} for e in entries if isinstance(e, dict)])).hexdigest()
        msha = hashlib.sha256(mb).hexdigest()
        sha = SHA_N if lab == "NEW" else SHA_B
        records = [{"ticker": t, "cik": "0000000001", "status": "OK", "company_type": "NON_FINANCIAL", "raw_source": "FETCHED",
                    "raw_input": {"path": f"{t}_companyfacts.json", "sha256": records_entries[i]["sha256"], "size_bytes": records_entries[i]["size_bytes"],
                                  "fetched_at_utc": lab, "loaded_at_utc": lab}} for i, t in enumerate(self.tickers)]
        summary = {"schema_version": "fundamentals_batch.v2", "evaluation_date": DATE, "generated_at_utc": lab, "records": records,
                   "operational_review": {"status": "PASS", "reasons": []},
                   "reproducibility": {"sec_filed_cutoff_date": CUT, "sec_cutoff_consistency": {"status": "MATCH", "mismatched_tickers": []},
                                       "governance_policy_version": self.policy[lab],
                                       "provenance": {"github_run_id": RUN_N if lab == "NEW" else RUN_B, "github_sha": sha, "checkout_head_sha": sha, "github_workflow_sha": sha, "commit_sha_consistency": "MATCH"},
                                       "sec_snapshot": {"status": "OK", "manifest_sha256": msha, "snapshot_content_sha256": content,
                                                        "self_verification": {"verification_status": "OK", "manifest_file_status": "WRITTEN_AND_REREAD",
                                                                              "expected_manifest_sha256": msha, "actual_manifest_sha256": msha,
                                                                              "expected_snapshot_content_sha256": content, "actual_snapshot_content_sha256": content,
                                                                              "missing_files": [], "mismatched_files": [], "validation_errors": []}}}}
        if self.summary_mut[lab]: self.summary_mut[lab](summary)

        class R: pass
        r = R(); r.summary = summary; r.results = res
        cov = {"evaluation_date": DATE, "normalized_metrics": {}}
        for m in ("revenue", "cash", "cfo"):
            r1n, r1d, r2n, r2d, s = ck.recompute_normalized(r, m)
            cov["normalized_metrics"][m] = {"r1_data_acquisition_rate": {"numerator": r1n, "denominator": r1d},
                                            "r2_runtime_non_withheld_ok_rate": {"numerator": r2n, "denominator": r2d, "calculation_status": s}}
        cov.update(ck.recompute_stage_coverage(r))
        if self.cov_mut[lab]: self.cov_mut[lab](cov)
        out = {f"{t}.json": json.dumps(v).encode() for t, v in res.items()}
        out.update({"summary.json": json.dumps(summary).encode(), "coverage.json": json.dumps(cov).encode(),
                    "summary.md": b"# summary\n", "coverage.md": ("# coverage\n" + self.md_extra[lab]).encode(),
                    "sec_snapshot_manifest.json": mb})
        return out


def write_dir(d, files):
    os.makedirs(d, exist_ok=True)
    for n, b in files.items():
        with open(os.path.join(d, n), "wb") as fh: fh.write(b)


def run_pair(p: Pair, profile=PROFILE, zip_new_members=None, extra=(), shas=True):
    with tempfile.TemporaryDirectory() as tmp:
        dirs = {}
        for lab in ("NEW", "BASE"):
            write_dir(os.path.join(tmp, lab), p.files(lab)); write_dir(os.path.join(tmp, lab + "_raw"), p.raw[lab])
        new_arg = os.path.join(tmp, "NEW")
        if zip_new_members is not None:
            new_arg = os.path.join(tmp, "new.zip")
            with zipfile.ZipFile(new_arg, "w") as z:
                for n, b in zip_new_members(p.files("NEW")): z.writestr(n, b)
        prof = "none"
        if profile is not None:
            prof = os.path.join(tmp, "profile.json")
            with open(prof, "w") as fh: json.dump(profile, fh)
        args = ["--new", new_arg, "--new-raw", os.path.join(tmp, "NEW_raw"), "--base", os.path.join(tmp, "BASE"),
                "--base-raw", os.path.join(tmp, "BASE_raw"), "--expect-date", DATE,
                *(["--new-sha", SHA_N, "--base-sha", SHA_B] if shas else []), "--profile", prof, *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): code = ck.main(args)
        return code, buf.getvalue()


VERDICT_RE = __import__("re").compile(r"^\[(PASS|FAIL|REVIEW|INFO)\s*\]\s+(\S+)\s+(\S+)")
def verdict(out, check_id, layer=None):
    for line in out.splitlines():
        m = VERDICT_RE.match(line)
        if m and m.group(3) == check_id and (layer is None or m.group(2) == layer):
            return m.group(1)
    return None


class Baseline(unittest.TestCase):
    def test_clean_pair_passes(self):
        code, out = run_pair(Pair())
        self.assertEqual(code, 0, out)
        self.assertIn("OVERALL PASS", out)
        for s in map(str, SECRET.values()): self.assertNotIn(s, out)

    def test_zip_input_equivalent(self):
        code, out = run_pair(Pair(), zip_new_members=lambda f: sorted(f.items()))
        self.assertEqual(code, 0, out)


class Frame(unittest.TestCase):
    def _frame(self, fn):
        p = Pair(); fn(p.res["NEW"]["AAPL"]["stages"]["normalization"]["normalized"]["metrics"]["revenue"]); return run_pair(p)

    def test01_frame_to_null_is_metadata_only(self):
        code, out = self._frame(lambda m: m.update(frame=None))
        self.assertEqual(code, 0, out)
        self.assertIn("FRAME_METADATA_ONLY", out); self.assertEqual(verdict(out, "D2"), "PASS")

    def test02_frame_field_removed_is_metadata_only(self):
        code, out = self._frame(lambda m: m.pop("frame"))
        self.assertEqual(code, 0, out); self.assertIn("(field removed)", out)

    def test03_frame_and_value(self):
        code, out = self._frame(lambda m: m.update(frame=None, value=m["value"] + 1))
        self.assertEqual(code, 1); self.assertEqual(verdict(out, "D2"), "FAIL"); self.assertEqual(verdict(out, "D3"), "FAIL")

    def test04_frame_and_accession(self):
        code, out = self._frame(lambda m: m.update(frame=None, accession="0000000001-26-000011"))
        self.assertEqual(code, 1); self.assertEqual(verdict(out, "D3"), "FAIL")

    def test05_frame_and_period_end(self):
        code, out = self._frame(lambda m: m.update(frame=None, period_end="2025-12-30"))
        self.assertEqual(code, 1); self.assertEqual(verdict(out, "D3"), "FAIL")

    def test05b_frame_kept_in_comparison_for_unmasked_originals(self):
        p = Pair(); p.res["NEW"]["GE"]["stages"]["eligibility_gate"]["unmasked_originals"]["cfo"]["value"] += 1
        code, out = run_pair(p)
        self.assertEqual(verdict(out, "D2"), "FAIL")
        for s in map(str, SECRET.values()): self.assertNotIn(s, out)


class RunMeta(unittest.TestCase):
    def test06_normalized_at_only_is_not_data(self):
        p = Pair(); p.res["NEW"]["LLY"]["stages"]["normalization"]["normalized"]["normalized_at"] = "2099-01-01T00:00:00Z"
        code, out = run_pair(p)
        self.assertEqual(code, 0, out); self.assertEqual(verdict(out, "D2"), "PASS")

    def test06b_other_value_change_is_data(self):
        p = Pair(); p.res["NEW"]["LLY"]["stages"]["derived_metrics"]["derived"]["derived_metrics"]["fcf"]["value"] = 0.2
        code, out = run_pair(p); self.assertEqual(verdict(out, "D2"), "FAIL")


class Materialization(unittest.TestCase):
    def _ge(self, p, stage="derived"):
        r = p.res["NEW"]["GE"]["stages"]
        return {"derived": r["derived_metrics"]["derived"]["derived_metrics"], "growth": r["growth"]["growth"]["components"],
                "data_sufficiency": r["data_sufficiency"]["sufficiency"]}[stage]

    def test07_not_materialized_but_original_status(self):
        p = Pair(); self._ge(p)["cfo_yoy"]["original_status"] = "MISSING"
        code, out = run_pair(p); self.assertEqual(code, 1); self.assertEqual(verdict(out, "B2-B4"), "FAIL")

    def test07c_marker_rule_without_base(self):
        mp = ck.Checker._marker_problems
        self.assertEqual(mp("derived", "cfo_yoy", v2_marker(["cfo"], False, None), ["cfo"]), [])
        self.assertTrue(mp("derived", "cfo_yoy", v2_marker(["cfo"], False, "MISSING"), ["cfo"]))
        self.assertTrue(mp("derived", "fcf", v2_marker(["cfo", "capex"], True, None), ["cfo", "capex"]))
        m = v2_marker(["cfo"], True, "MISSING"); del m["original_status"]
        self.assertTrue(mp("growth", "fcf_yoy", m, ["cfo"]))
        self.assertTrue(mp("derived", "cfo_yoy", dict(v2_marker(["cfo"], False, None), materialized="false"), ["cfo"]))
        self.assertTrue(mp("derived", "cfo_yoy", dict(v2_marker(["cfo"], False, None), value=0), ["cfo"]))

    def test08_materialized_but_original_status_missing(self):
        p = Pair(); del self._ge(p)["fcf"]["original_status"]
        code, out = run_pair(p); self.assertEqual(verdict(out, "B2-B4"), "FAIL")

    def test08b_sufficiency_materialized_without_original(self):
        p = Pair(); self._ge(p, "data_sufficiency")["fcf_yoy"]["original_status"] = None
        code, out = run_pair(p); self.assertEqual(verdict(out, "B2-B4"), "FAIL")

    def test09_withheld_inputs_mismatch(self):
        p = Pair(); self._ge(p)["fcf"]["withheld_inputs"] = ["cfo"]
        code, out = run_pair(p); self.assertEqual(verdict(out, "B2-B4"), "FAIL")

    def test09b_withheld_inputs_order_must_follow_map(self):
        p = Pair(); self._ge(p, "growth")["fcf_yoy"]["withheld_inputs"] = ["capex", "cfo"]
        code, out = run_pair(p); self.assertEqual(verdict(out, "B2-B4"), "FAIL")

    def test09c_dependent_dropped(self):
        p = Pair(); del self._ge(p)["capex_yoy"]
        p.res["NEW"]["GE"]["stages"]["eligibility_gate"]["relabelled"]["derived"].remove("capex_yoy")
        code, out = run_pair(p); self.assertEqual(verdict(out, "B2-B4"), "FAIL")

    def test09d_materialized_flag_contradicts_base(self):
        p = Pair(); m = self._ge(p)["cfo_yoy"]; m["materialized"] = True; m["original_status"] = "OK"
        code, out = run_pair(p); self.assertEqual(verdict(out, "B2-B4"), "FAIL")

    def test10_lly_missing_relabelled_as_withheld(self):
        p = Pair()
        p.res["NEW"]["LLY"]["stages"]["data_sufficiency"]["sufficiency"]["operating_income_yoy"] = v2_marker(["operating_income"], True, "MISSING", "data_sufficiency", "operating_income_yoy")
        code, out = run_pair(p)
        self.assertEqual(code, 1); self.assertEqual(verdict(out, "B5"), "FAIL"); self.assertEqual(verdict(out, "B6"), "FAIL")

    def test10b_lly_absent_derived_materialized(self):
        p = Pair()
        p.res["NEW"]["LLY"]["stages"]["derived_metrics"]["derived"]["derived_metrics"]["operating_income_yoy"] = v2_marker(["operating_income"], False, None)
        code, out = run_pair(p); self.assertEqual(verdict(out, "B5"), "FAIL")

    def test10c_net_debt_governance_contamination(self):
        p = Pair(); self._ge(p)["net_debt"] = v2_marker(["total_debt"], True, "MISSING")
        code, out = run_pair(p); self.assertEqual(code, 1)
        self.assertIn("FAIL", verdict(out, "B2-B4") + verdict(out, "B7"))


class Coverage(unittest.TestCase):
    def test11_coverage_count_tampered(self):
        p = Pair(); p.cov_mut["NEW"] = lambda c: c["derived_metrics"]["cfo_yoy"]["by_status"].update(OK=99)
        code, out = run_pair(p); self.assertEqual(verdict(out, "C1", "NEW"), "FAIL")

    def test11b_sufficiency_reason_tampered(self):
        p = Pair()
        def mut(c):
            b = c["data_sufficiency"]["operating_income_yoy"]["by_reason_code"]
            b["INSUFFICIENT_HISTORY"] = b.pop("CONCEPT_NOT_FOUND")
        p.cov_mut["NEW"] = mut
        code, out = run_pair(p); self.assertEqual(verdict(out, "C1", "NEW"), "FAIL")

    def test11c_profile_totals_mismatch(self):
        prof = copy.deepcopy(PROFILE); prof["derived_totals"]["cfo_yoy"] = [2, 4]
        code, out = run_pair(Pair(), profile=prof); self.assertEqual(verdict(out, "C4"), "FAIL")


class PIT(unittest.TestCase):
    def test12_post_cutoff_filing_in_results(self):
        p = Pair(); p.res["NEW"]["AAPL"]["stages"]["normalization"]["normalized"]["metrics"]["revenue"].update(
            accession="0000000001-26-000099", filing_date="2026-11-03")
        code, out = run_pair(p)
        self.assertEqual(code, 1)
        for cid in ("E1", "E2", "E3"): self.assertEqual(verdict(out, cid), "FAIL", cid)

    def test12b_case_not_exercised_without_post_cutoff_raw(self):
        p = Pair(); p.raw["NEW"]["AAPL_companyfacts.json"] = raw_file("AAPL", "V1")
        code, out = run_pair(p); self.assertEqual(verdict(out, "E3"), "FAIL")

    def test12c_wrong_cutoff(self):
        p = Pair(); p.res["NEW"]["LLY"]["sec_filed_cutoff_date"] = DATE
        code, out = run_pair(p); self.assertEqual(verdict(out, "A1", "NEW"), "FAIL")


class Inputs(unittest.TestCase):
    def _zip(self, members):
        code, out = run_pair(Pair(), zip_new_members=members); return code, out

    def test13_path_traversal(self):
        code, out = self._zip(lambda f: list(f.items()) + [("../evil.json", b"{}")])
        self.assertEqual(code, 3); self.assertIn("unsafe member", out)

    def test13b_duplicate_member(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            code, out = self._zip(lambda f: list(f.items()) + [("GE.json", b"{}")])
        self.assertEqual(code, 3); self.assertIn("duplicate", out)

    def test13c_absolute_backslash_drive(self):
        for bad in ("/abs.json", "a\\b.json", "C:x.json"):
            code, out = self._zip(lambda f, bad=bad: list(f.items()) + [(bad, b"{}")])
            self.assertEqual(code, 3, bad)

    def test13d_symlink_member(self):
        def members(f):
            return list(f.items())
        with tempfile.TemporaryDirectory() as tmp:
            zp = os.path.join(tmp, "s.zip")
            with zipfile.ZipFile(zp, "w") as z:
                for n, b in Pair().files("NEW").items(): z.writestr(n, b)
                zi = zipfile.ZipInfo("link.json"); zi.external_attr = (0o120777 << 16); z.writestr(zi, "GE.json")
            with self.assertRaises(ck.InputError): ck.Archive(zp, "summary.json")

    def test13e_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            zp = os.path.join(tmp, "c.zip")
            with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_STORED) as z: z.writestr("summary.json", b"{" * 200)
            data = bytearray(open(zp, "rb").read()); i = data.find(b"{{{{"); data[i:i + 4] = b"XXXX"
            open(zp, "wb").write(bytes(data))
            with self.assertRaises(ck.InputError): ck.Archive(zp, "summary.json")
            open(zp, "wb").write(b"not a zip")
            with self.assertRaises(ck.InputError): ck.Archive(zp, "summary.json")

    def test13f_symlink_in_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dir(tmp, Pair().files("NEW")); os.symlink("/etc/passwd", os.path.join(tmp, "x.json"))
            with self.assertRaises(ck.InputError): ck.Archive(tmp, "summary.json")


class Leak(unittest.TestCase):
    def test14_withheld_original_in_coverage_md(self):
        p = Pair(); p.md_extra["NEW"] = f"| cfo | {SECRET['cfo']:,} |\n"
        code, out = run_pair(p)
        self.assertEqual(code, 1); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL")
        self.assertNotIn(f"{SECRET['cfo']:,}", out); self.assertNotIn(str(SECRET["cfo"]), out)

    def test14b_withheld_original_in_summary_json(self):
        p = Pair(); p.summary_mut["NEW"] = lambda s: s.update(debug=SECRET["capex"])
        code, out = run_pair(p); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL")

    def test14c_json_report_has_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            jp = os.path.join(tmp, "r.json"); code, out = run_pair(Pair(), extra=("--json-out", jp))
            blob = open(jp).read()
            for s in map(str, SECRET.values()): self.assertNotIn(s, blob)


class Integrity(unittest.TestCase):
    def test_short_sha_is_review(self):
        with tempfile.TemporaryDirectory():
            p = Pair(); code, out = run_pair(p, extra=("--new-sha", SHA_N[:7]))
            self.assertEqual(verdict(out, "A4", "NEW"), "REVIEW"); self.assertEqual(code, 2)

    def test_wrong_sha_fails(self):
        code, out = run_pair(Pair(), extra=("--new-sha", "c" * 40)); self.assertEqual(verdict(out, "A4", "NEW"), "FAIL")

    def test_raw_tamper_fails(self):
        p = Pair(); files = p.files("NEW")
        p.raw["NEW"]["LLY_companyfacts.json"] += b" "
        orig = p.files
        p.files = lambda lab: files if lab == "NEW" else orig(lab)
        code, out = run_pair(p); self.assertEqual(verdict(out, "A7", "NEW"), "FAIL")

    def test_policy_version_wrong(self):
        p = Pair(); p.summary_mut["NEW"] = lambda s: s["reproducibility"].update(governance_policy_version=ck.POLICY_V1)
        code, out = run_pair(p)
        self.assertEqual(code, 1)
        self.assertEqual(verdict(out, "P0"), "FAIL")               # policy is bound to the run by the profile
        self.assertEqual(verdict(out, "B2-B4", "NEW"), "FAIL")     # V2-shaped output fails the V1 rule it claims

    def test_operational_fail(self):
        p = Pair(); p.summary_mut["NEW"] = lambda s: s["operational_review"].update(status="FAIL")
        code, out = run_pair(p); self.assertEqual(verdict(out, "A3", "NEW"), "FAIL")


def ge(p, lab="NEW", stage="derived"):
    r = p.res[lab]["GE"]["stages"]
    return {"derived": r["derived_metrics"]["derived"]["derived_metrics"], "growth": r["growth"]["growth"]["components"],
            "data_sufficiency": r["data_sufficiency"]["sufficiency"]}[stage]


class ProfileBinding(unittest.TestCase):
    def _prof(self, fn):
        prof = copy.deepcopy(PROFILE); fn(prof); return prof

    def test_p0_wrong_run_id_fails(self):
        code, out = run_pair(Pair(), profile=self._prof(lambda p: p["applies_to"]["new"].update(github_run_id="999")))
        self.assertEqual(code, 1); self.assertEqual(verdict(out, "P0"), "FAIL")

    def test_p0_wrong_sha_fails(self):
        code, out = run_pair(Pair(), profile=self._prof(lambda p: p["applies_to"]["base"].update(github_sha="c" * 40)), shas=False)
        self.assertEqual(verdict(out, "P0"), "FAIL"); self.assertEqual(verdict(out, "A4", "BASE"), "FAIL")

    def test_p0_wrong_date_fails(self):
        code, out = run_pair(Pair(), profile=self._prof(lambda p: p["applies_to"].update(evaluation_date="2026-11-04")))
        self.assertEqual(verdict(out, "P0"), "FAIL")

    def test_p0_wrong_policy_fails(self):
        code, out = run_pair(Pair(), profile=self._prof(lambda p: p["applies_to"]["base"].update(governance_policy_version=ck.POLICY_V2)))
        self.assertEqual(verdict(out, "P0"), "FAIL")

    def test_p0_cli_sha_contradicts_profile(self):
        code, out = run_pair(Pair(), extra=("--new-sha", "d" * 40))
        self.assertEqual(verdict(out, "P0"), "FAIL")

    def test_profile_sha_used_when_cli_sha_absent(self):
        code, out = run_pair(Pair(), shas=False)
        self.assertEqual(code, 0, out); self.assertEqual(verdict(out, "A4", "NEW"), "PASS")

    def test_invalid_profile_is_input_error(self):
        for bad in (lambda p: p["applies_to"]["new"].update(github_sha="abc1234"), lambda p: p.pop("applies_to"),
                    lambda p: p.update(profile_version=2), lambda p: p["applies_to"]["new"].update(governance_policy_version="X")):
            code, out = run_pair(Pair(), profile=self._prof(bad))
            self.assertEqual(code, 3, out)

    def test_profile_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ck.main(["--new", "x", "--base", "y", "--expect-date", DATE])


class ProfileNone(unittest.TestCase):
    def test_none_clean_pair_passes_without_profile_checks(self):
        code, out = run_pair(Pair(), profile=None)
        self.assertEqual(code, 0, out)
        self.assertNotIn("] PROF ", out)
        for cid in ("B0", "B2-B4", "B5", "B7", "C1", "C3", "D2", "D3", "E1", "E2", "F1", "M1"):
            self.assertEqual(verdict(out, cid), "PASS", cid)

    def test_none_still_catches_governance_and_security(self):
        p = Pair(); ge(p)["cfo_yoy"]["original_status"] = "MISSING"
        p.res["NEW"]["LLY"]["stages"]["derived_metrics"]["derived"]["derived_metrics"]["fcf"] = v2_marker(["cfo"], True, "OK")
        p.md_extra["NEW"] = f"{SECRET['cfo']}\n"
        code, out = run_pair(p, profile=None)
        self.assertEqual(code, 1)
        for cid in ("B2-B4", "B5", "F1"): self.assertEqual(verdict(out, cid), "FAIL", cid)

    def test_none_without_sha_or_raw_is_not_pass(self):
        p = Pair()
        with tempfile.TemporaryDirectory() as tmp:
            for lab in ("NEW", "BASE"): write_dir(os.path.join(tmp, lab), p.files(lab))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = ck.main(["--new", os.path.join(tmp, "NEW"), "--base", os.path.join(tmp, "BASE"), "--expect-date", DATE, "--profile", "none"])
        self.assertEqual(code, 2, buf.getvalue())


class MappingDrift(unittest.TestCase):
    def _registry(self, tmp, derived=None, growth=None, raw=None):
        d = os.path.join(tmp, "src", "fundamentals"); os.makedirs(d)
        text = raw if raw is not None else (f"DERIVED_DEPENDENCIES = {derived if derived is not None else dict(ck.DERIVED_DEPS)!r}\n"
                                            f"GROWTH_DEPENDENCIES = {growth if growth is not None else dict(ck.GROWTH_DEPS)!r}\n")
        with open(os.path.join(d, "metric_eligibility_registry.py"), "w") as fh: fh.write(text)
        return tmp

    def test_m1_identical_maps_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run_pair(Pair(), extra=("--repo-root", self._registry(tmp)))
        self.assertEqual(code, 0, out); self.assertEqual(verdict(out, "M1"), "PASS")

    def test_m1_stale_map_is_review_and_names_entry_and_file(self):
        derived = dict(ck.DERIVED_DEPS); derived["new_metric_yoy"] = ("cfo",)
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run_pair(Pair(), extra=("--repo-root", self._registry(tmp, derived=derived)))
        self.assertEqual(code, 2, out); self.assertEqual(verdict(out, "M1"), "REVIEW")
        self.assertIn("new_metric_yoy", out); self.assertIn("metric_eligibility_registry.py", out)

    def test_m1_changed_inputs_is_review(self):
        growth = dict(ck.GROWTH_DEPS); growth["fcf_yoy"] = ("cfo",)
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run_pair(Pair(), extra=("--repo-root", self._registry(tmp, growth=growth)))
        self.assertEqual(verdict(out, "M1"), "REVIEW"); self.assertIn("fcf_yoy", out)

    def test_m1_unverifiable_is_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run_pair(Pair(), extra=("--repo-root", tmp))                       # file missing
            self.assertEqual(verdict(out, "M1"), "REVIEW")
        with tempfile.TemporaryDirectory() as tmp:
            reg = self._registry(tmp, raw="DERIVED_DEPENDENCIES = build()\nGROWTH_DEPENDENCIES = {}\n")
            code, out = run_pair(Pair(), extra=("--repo-root", reg))                       # not a literal
            self.assertEqual(verdict(out, "M1"), "REVIEW")

    def test_checker_maps_match_production_registry(self):
        """Repository guard: a production map change must update the checker in the same PR."""
        path = REPO / "src" / "fundamentals" / "metric_eligibility_registry.py"
        prod_d = ck.static_dict(str(path), "DERIVED_DEPENDENCIES"); prod_g = ck.static_dict(str(path), "GROWTH_DEPENDENCIES")
        self.assertEqual({k: tuple(v) for k, v in prod_d.items()}, ck.DERIVED_DEPS)
        self.assertEqual({k: tuple(v) for k, v in prod_g.items()}, ck.GROWTH_DEPS)


class SchemaSeparation(unittest.TestCase):
    def test_absent_vs_null_original_status(self):
        mp = ck.Checker._marker_problems
        m = v2_marker(["cfo"], False, None)
        self.assertEqual(mp("derived", "cfo_yoy", m, ["cfo"]), [])                       # null: allowed
        del m["original_status"]
        self.assertIn("original_status key absent", mp("derived", "cfo_yoy", m, ["cfo"]))  # absent: not allowed

    def test_new_v2_marker_missing_field_not_excused_by_base_v1(self):
        p = Pair(); del ge(p, "NEW", "data_sufficiency")["fcf_yoy"]["original_status"]
        self.assertNotIn("original_status", ge(p, "BASE", "data_sufficiency")["fcf_yoy"])
        code, out = run_pair(p)
        self.assertEqual(verdict(out, "B2-B4", "NEW"), "FAIL"); self.assertEqual(verdict(out, "B2-B4", "BASE"), "PASS")

    def test_base_v1_rule_rejects_unproduced_relabel(self):
        p = Pair(); p.res["BASE"]["GE"]["stages"]["eligibility_gate"]["relabelled"]["derived"].append("cfo_yoy")
        code, out = run_pair(p); self.assertEqual(verdict(out, "B2-B4", "BASE"), "FAIL")

    def test_base_v1_rule_rejects_v2_marker(self):
        p = Pair(); ge(p, "BASE")["fcf"]["set_by"] = "DEC055_GATE"
        code, out = run_pair(p); self.assertEqual(verdict(out, "B2-B4", "BASE"), "FAIL")

    def test_base_v1_withheld_inputs_checked(self):
        p = Pair(); ge(p, "BASE", "growth")["fcf_yoy"]["withheld_inputs"] = ["cfo"]
        code, out = run_pair(p); self.assertEqual(verdict(out, "B2-B4", "BASE"), "FAIL")

    def _v2_pair(self):
        p = Pair(); p.policy["BASE"] = ck.POLICY_V2
        p.res["BASE"] = {t: ticker(t, "V2") for t in p.tickers}
        p.res["BASE"]["AAPL"] = ticker("AAPL", "V2", frame=None)
        for t in p.tickers: p.res["BASE"][t]["stages"]["normalization"]["normalized"]["normalized_at"] = "2026-11-03T10:00:00Z"
        return p

    def test_v2_vs_v2_markers_carry_over(self):
        code, out = run_pair(self._v2_pair(), profile=None)
        self.assertEqual(verdict(out, "B2-B4", "NEW"), "PASS", out); self.assertEqual(verdict(out, "B2-B4", "BASE"), "PASS")

    def test_v2_vs_v2_flip_detected(self):
        p = self._v2_pair(); m = ge(p)["cfo_yoy"]; m["materialized"] = True; m["original_status"] = "OK"
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "B2-B4", "NEW"), "FAIL")

    def test_b4_absent_at_base_without_profile(self):
        """capex_yoy did not exist at BASE: NEW must say materialized=false even with no profile expectations."""
        p = Pair(); m = ge(p)["capex_yoy"]; m["materialized"] = True; m["original_status"] = "OK"
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "B2-B4", "NEW"), "FAIL"); self.assertIn("BASE implies False/None", out)

    def test_unknown_policy_is_review(self):
        p = Pair(); p.policy["NEW"] = "DEC055_GATE_V9_SOMETHING"
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "A2", "NEW"), "REVIEW"); self.assertEqual(verdict(out, "B2-B4", "NEW"), "REVIEW")



class RequiredStages(unittest.TestCase):
    STAGES = {"derived": "derived_metrics", "growth": "growth", "data_sufficiency": "data_sufficiency"}

    def test_each_stage_removed_fails_with_and_without_profile(self):
        for label, key in self.STAGES.items():
            for prof in (PROFILE, None):
                for ticker_ in ("GE", "LLY"):
                    with self.subTest(stage=key, profile=bool(prof), ticker=ticker_):
                        p = Pair(); del p.res["NEW"][ticker_]["stages"][key]
                        code, out = run_pair(p, profile=prof)
                        self.assertEqual(code, 1, out)
                        self.assertEqual(verdict(out, "S1", "NEW"), "FAIL")
                        self.assertIn(f"{ticker_}.{key}", out)
                        if ticker_ == "GE":
                            self.assertEqual(verdict(out, "B2-B4", "NEW"), "FAIL")
                            self.assertIn("stage output missing (required)", out)

    def test_stage_removed_in_base_v1_fails(self):
        p = Pair(); del p.res["BASE"]["GE"]["stages"]["growth"]
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "S1", "BASE"), "FAIL"); self.assertEqual(verdict(out, "B2-B4", "BASE"), "FAIL")

    def test_base_v1_missing_sufficiency_reported_by_rule(self):
        p = Pair(); del p.res["BASE"]["GE"]["stages"]["data_sufficiency"]
        code, out = run_pair(p, profile=None)
        line = [l for l in out.splitlines() if "] BASE B2-B4" in l][0]
        self.assertIn("FAIL", line); self.assertIn("data_sufficiency: stage output missing (required)", line)

    def test_stage_status_not_ok_or_container_missing_fails(self):
        p = Pair(); p.res["NEW"]["AAPL"]["stages"]["data_sufficiency"]["status"] = "ERROR"
        p.res["NEW"]["LLY"]["stages"]["growth"]["growth"].pop("growth")
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "S1", "NEW"), "FAIL")
        self.assertIn("AAPL.data_sufficiency", out); self.assertIn("LLY.growth", out)


class RawInputManifest(unittest.TestCase):
    def test_manifest_drops_a_ticker(self):
        p = Pair(); p.manifest_mut["NEW"] = lambda es: es.pop(0)
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "A8", "NEW"), "FAIL"); self.assertIn("missing from manifest", out)

    def test_summary_raw_input_removed_only(self):
        p = Pair(); p.summary_mut["NEW"] = lambda s: s["records"][1].pop("raw_input")
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "A8", "NEW"), "FAIL"); self.assertIn("OK without raw_input", out)

    def test_manifest_duplicate_ticker(self):
        def dup(es):
            e = dict(es[0]); e["path"] = "AAPL_copy.json"; es.append(e)
        p = Pair(); p.manifest_mut["NEW"] = dup
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "A8", "NEW"), "FAIL"); self.assertIn("duplicate ticker in manifest", out)

    def test_summary_manifest_ticker_mismatch(self):
        p = Pair(); p.manifest_mut["NEW"] = lambda es: es[2].update(ticker="ZZZ")
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "A8", "NEW"), "FAIL")
        self.assertIn("without a summary raw_input", out); self.assertIn("missing from manifest", out)

    def test_cik_or_source_mismatch(self):
        p = Pair(); p.manifest_mut["NEW"] = lambda es: es[0].update(source="CACHE")
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "A8", "NEW"), "FAIL")

    def test_raw_input_incomplete(self):
        for field, bad in (("sha256", None), ("size_bytes", "10"), ("path", None)):
            with self.subTest(field=field):
                p = Pair(); p.summary_mut["NEW"] = lambda s, f=field, b=bad: s["records"][0]["raw_input"].update({f: b})
                code, out = run_pair(p, profile=None)
                self.assertEqual(verdict(out, "A8", "NEW"), "FAIL"); self.assertIn("AAPL: raw_input incomplete", out)

    def _with_extra_records(self, p, recs):
        def add(s): s["records"].extend(copy.deepcopy(recs))
        p.summary_mut["NEW"] = add; p.summary_mut["BASE"] = add

    def test_allowed_exceptions_pass(self):
        """ERROR before the input was loaded and EXCLUDED records carry no raw_input and no manifest entry."""
        p = Pair()
        self._with_extra_records(p, [{"ticker": "ERRX", "cik": "0000000009", "status": "ERROR", "company_type": "NON_FINANCIAL", "error_type": "HTTPError"},
                                     {"ticker": "EXCL", "cik": "0000000008", "status": "EXCLUDED_COMPANY_TYPE"},
                                     {"ticker": "NOWL", "cik": "0000000007", "status": "EXCLUDED_NOT_IN_WATCHLIST"}])
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "A8", "NEW"), "PASS", out); self.assertEqual(code, 0, out)
        self.assertIn("ERROR without raw_input (allowed)=1", out)

    def test_error_after_load_must_be_in_manifest(self):
        p = Pair()
        self._with_extra_records(p, [{"ticker": "ERRL", "cik": "0000000009", "status": "ERROR", "company_type": "NON_FINANCIAL", "raw_source": "FETCHED",
                                      "raw_input": {"path": "ERRL_companyfacts.json", "sha256": "0" * 64, "size_bytes": 10}}])
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "A8", "NEW"), "FAIL"); self.assertIn("ERRL", out)

    def test_excluded_with_raw_input_fails(self):
        p = Pair()
        self._with_extra_records(p, [{"ticker": "EXCL", "cik": "0000000008", "status": "EXCLUDED_COMPANY_TYPE",
                                      "raw_input": {"path": "EXCL_companyfacts.json", "sha256": "0" * 64, "size_bytes": 1}}])
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "A8", "NEW"), "FAIL")


class LeakBoundaries(unittest.TestCase):
    VALUES = (999, 1000, -999, -1000, 0, 3.25, -0.5)

    def _pair(self, v):
        p = Pair()
        for lab in ("NEW", "BASE"):
            p.res[lab]["GE"]["stages"]["eligibility_gate"]["unmasked_originals"]["cfo"]["value"] = v
        return p

    def test_clean_pair_passes_for_every_boundary_value(self):
        for v in self.VALUES:
            with self.subTest(v=v):
                code, out = run_pair(self._pair(v), profile=None)
                self.assertEqual(verdict(out, "F1", "NEW"), "PASS", out)

    def test_value_context_leak_fails_for_every_magnitude(self):
        for v in self.VALUES:
            with self.subTest(v=v):
                p = self._pair(v)
                p.summary_mut["NEW"] = lambda s, v=v: [r.setdefault("metrics", {}).update(cfo={"value": v}) for r in s["records"] if r["ticker"] == "GE"]
                code, out = run_pair(p, profile=None)
                self.assertEqual(verdict(out, "F1", "NEW"), "FAIL", v)
                self.assertIn("/metrics/cfo/value", out)

    def test_dependent_name_counts_as_context(self):
        p = self._pair(3.25)
        p.summary_mut["NEW"] = lambda s: [r.setdefault("metrics", {}).update(fcf_yoy={"value": 3.25}) for r in s["records"] if r["ticker"] == "GE"]
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL")

    def test_same_small_number_in_another_ticker_record_is_not_a_leak_but_is_reviewed(self):
        p = self._pair(3.25)
        p.summary_mut["NEW"] = lambda s: [r.setdefault("metrics", {}).update(cfo={"value": 3.25}) for r in s["records"] if r["ticker"] == "LLY"]
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "REVIEW")

    def test_uncontexted_matches(self):
        expect = {999: "REVIEW", 1000: "FAIL", -999: "REVIEW", -1000: "FAIL", 0: "PASS", 3.25: "REVIEW", -0.5: "REVIEW"}
        for v, want in expect.items():
            with self.subTest(v=v):
                p = self._pair(v); p.cov_mut["NEW"] = lambda c, v=v: c.update(unrelated_count=v)
                code, out = run_pair(p, profile=None)
                self.assertEqual(verdict(out, "F1", "NEW"), want, out)

    def test_rounding_slack_only_from_1000(self):
        """|v| >= 1000 tolerates +-0.5 (rounded display); below 1000 the match is exact."""
        for v, tok, want in ((1000, 1000.4, "FAIL"), (-1000, -999.6, "FAIL"), (999, 999.4, "PASS"), (-999, -998.6, "PASS")):
            with self.subTest(v=v, tok=tok):
                p = self._pair(v); p.cov_mut["NEW"] = lambda c, tok=tok: c.update(unrelated=tok)
                code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), want, out)

    def test_small_value_must_match_exactly(self):
        p = self._pair(3.25); p.cov_mut["NEW"] = lambda c: c.update(unrelated=3.0, other=3.5)
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "PASS")

    def test_markdown_small_value_on_named_line_is_review(self):
        p = self._pair(3.25); p.md_extra["NEW"] = "| cfo | 3.25 |\n"
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "REVIEW")
        p = self._pair(3.25); p.md_extra["NEW"] = "| revenue | 3.25 |\n"
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "PASS")

    def test_nested_original_stale_observation_is_a_target(self):
        p = Pair(); hidden = 4_444_000_000
        for lab in ("NEW", "BASE"):
            p.res[lab]["GE"]["stages"]["eligibility_gate"]["unmasked_originals"]["operating_income"] = {
                "metric": "operating_income", "status": "INCOMPLETE", "value": None, "stale_observation": {"value": hidden, "fy": 2024}}
        self.assertEqual(verdict(run_pair(p, profile=None)[1], "F1", "NEW"), "PASS")
        p.md_extra["NEW"] = f"{hidden:,}\n"
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "F1", "NEW"), "FAIL"); self.assertNotIn(f"{hidden:,}", out); self.assertNotIn(str(hidden), out)

    def test_fy_is_not_a_target(self):
        targets = ck.withheld_targets(type("R", (), {"results": {"GE": ticker("GE", "V2")}})())
        self.assertEqual(sorted(t["value"] for t in targets), sorted(float(v) for v in SECRET.values()))

    def test_values_never_printed(self):
        for v in (999, -999, 3.25):
            p = self._pair(v)
            p.summary_mut["NEW"] = lambda s, v=v: [r.setdefault("metrics", {}).update(cfo={"value": v}) for r in s["records"] if r["ticker"] == "GE"]
            p.cov_mut["NEW"] = lambda c, v=v: c.update(unrelated_count=v)
            code, out = run_pair(p, profile=None)
            self.assertNotIn(str(v), out)

    def test_report_self_check(self):
        t = [{"ticker": "GE", "metric": "cfo", "value": 3.25, "names": {"cfo"}}]
        self.assertTrue(ck.report_leaks("x value: 3.25", t))
        self.assertTrue(ck.report_leaks('"value": 3.25', t))
        self.assertFalse(ck.report_leaks("count 3 and 25 paths", t))
        self.assertTrue(ck.report_leaks("total 5,123,000,000", [{"value": 5_123_000_000.0}]))



class LeakNumericStrings(unittest.TestCase):
    """Round 3: small originals written as JSON strings."""

    def _pair(self, v):
        p = Pair()
        for lab in ("NEW", "BASE"):
            p.res[lab]["GE"]["stages"]["eligibility_gate"]["unmasked_originals"]["cfo"]["value"] = v
        return p

    def _put(self, p, ticker_, metric_, key, val):
        p.summary_mut["NEW"] = lambda s: [r.setdefault("metrics", {}).update({metric_: {key: val}}) for r in s["records"] if r["ticker"] == ticker_]

    def test_clean_pair_with_37_passes(self):
        """Guard for the tests below: the target collides with nothing in the fixture."""
        code, out = run_pair(self._pair(37), profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "PASS", out)

    def test_value_string_same_ticker_same_metric_fails(self):
        p = self._pair(37); self._put(p, "GE", "cfo", "value", "37")
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL"); self.assertIn("/metrics/cfo/value#str", out)

    def test_zero_string_in_context_fails(self):
        p = self._pair(0); self._put(p, "GE", "cfo", "value", "0")
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL")

    def test_amount_string_on_dependent_path_fails(self):
        p = self._pair(37); self._put(p, "GE", "fcf_yoy", "amount", "37")
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL")

    def test_value_string_in_other_ticker_record_keeps_review(self):
        p = self._pair(37); self._put(p, "LLY", "cfo", "value", "37")
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "REVIEW")

    def test_value_string_on_unrelated_path_is_review(self):
        p = self._pair(37); p.cov_mut["NEW"] = lambda c: c.update(misc={"value": "37"})
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "REVIEW")

    def test_zero_string_without_context_is_not_flagged(self):
        p = self._pair(0); p.cov_mut["NEW"] = lambda c: c.update(misc={"value": "0"})
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "PASS")

    def test_numeric_and_string_exact_match_equivalence(self):
        # 37 collides with nothing in the fixture (3 would: ticker counts, the "03" of the date)
        for val, want in ((37, "FAIL"), ("37", "FAIL"), ("37.00", "FAIL"), (" 37 ", "FAIL"), ("37.5", "PASS"), (37.0000001, "PASS"),
                          ("about 37 USD", "REVIEW")):
            with self.subTest(val=val):
                p = self._pair(37); self._put(p, "GE", "cfo", "value", val)
                code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), want, out)

    def test_decimal_string(self):
        p = self._pair(-0.5); self._put(p, "GE", "cfo", "value", "-0.5")
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL")
        self.assertNotIn("-0.5", out)

    def test_large_string_tolerance_unchanged(self):
        p = self._pair(5_123_000_000); self._put(p, "LLY", "x", "note", "5,123,000,000.4")
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL")

    def test_report_self_check_quoted_value(self):
        t = [{"ticker": "GE", "metric": "cfo", "value": 3.0, "names": {"cfo"}}]
        self.assertTrue(ck.report_leaks('"value": "3"', t)); self.assertTrue(ck.report_leaks("amount='3'", t))
        self.assertFalse(ck.report_leaks('"count": "3"', t))


class RawInputIndependentValidity(unittest.TestCase):
    """Round 3: each side validated on its own, so identical wrong values on both sides cannot pass."""

    def _both(self, rec_fn, man_fn):
        p = Pair()
        p.summary_mut["NEW"] = lambda s: rec_fn(s["records"][0])
        p.manifest_mut["NEW"] = lambda es: man_fn(es[0])
        return run_pair(p, profile=None)

    def _assert_fail(self, out, text):
        self.assertEqual(verdict(out, "A8", "NEW"), "FAIL", out); self.assertIn(text, out)

    def test_cik_missing_both_sides(self):
        code, out = self._both(lambda r: r.pop("cik"), lambda e: e.update(cik=None)); self._assert_fail(out, "invalid cik")

    def test_cik_same_but_not_10_digits(self):
        for bad in ("12345", "000032019X", 320193, "00003201930"):
            with self.subTest(cik=bad):
                code, out = self._both(lambda r, b=bad: r.update(cik=b), lambda e, b=bad: e.update(cik=b)); self._assert_fail(out, "invalid cik")

    def test_source_missing_both_sides(self):
        code, out = self._both(lambda r: r.pop("raw_source"), lambda e: e.update(source=None)); self._assert_fail(out, "invalid source")

    def test_source_same_but_unknown(self):
        code, out = self._both(lambda r: r.update(raw_source="LIVE"), lambda e: e.update(source="LIVE")); self._assert_fail(out, "invalid source")

    def test_sha_format(self):
        for bad in (lambda h: h.upper(), lambda h: h[:63], lambda h: h + "0", lambda h: "g" * 64):
            with self.subTest():
                p = Pair(); h = {}
                p.manifest_mut["NEW"] = lambda es, b=bad: [es[0].update(sha256=b(es[0]["sha256"])), h.update(v=es[0]["sha256"])]
                p.summary_mut["NEW"] = lambda s, b=bad: s["records"][0]["raw_input"].update(sha256=b(s["records"][0]["raw_input"]["sha256"]))
                code, out = run_pair(p, profile=None); self._assert_fail(out, "invalid sha256")

    def test_size_bool_or_negative(self):
        for bad in (True, -1, 1.5, "10"):
            with self.subTest(size=bad):
                code, out = self._both(lambda r, b=bad: r["raw_input"].update(size_bytes=b), lambda e, b=bad: e.update(size_bytes=b))
                self._assert_fail(out, "invalid size_bytes")

    def test_unsafe_path_both_sides(self):
        code, out = self._both(lambda r: r["raw_input"].update(path="../AAPL_companyfacts.json"),
                               lambda e: e.update(path="../AAPL_companyfacts.json"))
        self._assert_fail(out, "path empty, '.' or '..' segment"); self.assertEqual(verdict(out, "A6", "NEW"), "FAIL")

    def test_cache_and_fetched_each_pass(self):
        for src in ("CACHE", "FETCHED"):
            with self.subTest(source=src):
                p = Pair()
                for lab in ("NEW", "BASE"):
                    p.summary_mut[lab] = lambda s, v=src: [r.update(raw_source=v) for r in s["records"]]
                    p.manifest_mut[lab] = lambda es, v=src: [e.update(source=v) for e in es]
                code, out = run_pair(p, profile=None)
                self.assertEqual(verdict(out, "A8", "NEW"), "PASS", out); self.assertEqual(code, 0, out)

    def test_manifest_only_invalid_is_named_as_such(self):
        """Only the manifest is wrong: A6 (structure) fails and A8 names the manifest entry, not just a mismatch."""
        code, out = self._both(lambda r: None, lambda e: e.update(cik="12345"))
        self._assert_fail(out, "manifest 'AAPL': invalid (invalid cik")
        self.assertEqual(verdict(out, "A6", "NEW"), "FAIL")

    def test_mismatch_still_fails(self):
        code, out = self._both(lambda r: r.update(cik="0000000002"), lambda e: None)
        self._assert_fail(out, "summary raw_input/cik/raw_source != manifest entry")


class ManifestStructure(unittest.TestCase):
    """Round 3: independent mirror of sec_snapshot.validate_manifest_structure + completeness rule."""

    def _man(self, **kw):
        e = {"path": "AAPL_companyfacts.json", "ticker": "AAPL", "cik": "0000320193", "source": "FETCHED", "size_bytes": 10, "sha256": "a" * 64}
        m = {"manifest_version": "SEC_SNAPSHOT_MANIFEST_V1", "status": "OK", "file_count": 1, "files": [e], "inputs_without_stored_file": []}
        m.update(kw); return m

    def test_valid_manifest(self):
        self.assertEqual(ck.manifest_structure_problems(self._man()), [])

    def test_subdirectory_path_allowed_like_production(self):
        m = self._man(); m["files"][0]["path"] = "raw/AAPL_companyfacts.json"
        self.assertEqual(ck.manifest_structure_problems(m), [])

    def test_structural_violations(self):
        cases = {
            "unexpected manifest fields": lambda m: m.update(extra=1),
            "missing manifest fields": lambda m: m.pop("status"),
            "unsupported manifest_version": lambda m: m.update(manifest_version="V2"),
            "file_count": lambda m: m.update(file_count=True),
            "unexpected fields": lambda m: m["files"][0].update(note="x"),
            "missing fields": lambda m: m["files"][0].pop("source"),
            "invalid ticker": lambda m: m["files"][0].update(ticker="aapl"),
            "not sorted by path": lambda m: m.update(files=[dict(m["files"][0], path="B.json", ticker="B"), dict(m["files"][0], path="A.json", ticker="A")], file_count=2),
            "duplicate path": lambda m: m.update(files=[m["files"][0], dict(m["files"][0], ticker="AAPX")], file_count=2),
            "inputs_without_stored_file is not a list of strings": lambda m: m.update(inputs_without_stored_file=[1]),
        }
        for text, fn in cases.items():
            with self.subTest(text):
                m = self._man(); fn(m)
                self.assertTrue(any(text in e for e in ck.manifest_structure_problems(m)), ck.manifest_structure_problems(m))

    def test_incomplete_or_empty_snapshot_rejected(self):
        for kw in ({"status": "INCOMPLETE", "inputs_without_stored_file": ["AAPL"]}, {"status": "NO_SEC_INPUTS", "files": [], "file_count": 0},
                   {"inputs_without_stored_file": ["AAPL"]}):
            with self.subTest(kw=kw):
                self.assertTrue(any("not complete" in e for e in ck.manifest_structure_problems(self._man(**kw))))

    def test_incomplete_snapshot_end_to_end_fails(self):
        p = Pair()
        p.summary_mut["NEW"] = lambda s: s["records"][0].update(raw_input={"path": None, "sha256": None, "size_bytes": None})
        p.manifest_mut["NEW"] = lambda es: es.pop(0)
        code, out = run_pair(p, profile=None)
        self.assertEqual(code, 1); self.assertEqual(verdict(out, "A8", "NEW"), "FAIL")



class LeakTickerScope(unittest.TestCase):
    """Round 4: the outermost record's ticker fixes the scope; a nested "ticker" cannot re-scope a leak."""

    def _pair(self, v):
        p = Pair()
        for lab in ("NEW", "BASE"):
            p.res[lab]["GE"]["stages"]["eligibility_gate"]["unmasked_originals"]["cfo"]["value"] = v
        return p

    def _nested(self, p, outer, inner, val):
        p.summary_mut["NEW"] = lambda s: [r.setdefault("metrics", {}).update(cfo={"ticker": inner, "value": val})
                                          for r in s["records"] if r["ticker"] == outer]

    def test_nested_lly_inside_ge_record_zero_fails(self):
        p = self._pair(0); self._nested(p, "GE", "LLY", "0")
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL", out)

    def test_nested_lly_inside_ge_record_37_fails(self):
        for val in ("37", 37):
            with self.subTest(val=val):
                p = self._pair(37); self._nested(p, "GE", "LLY", val)
                code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL", out)

    def test_nested_ge_inside_ge_record_fails(self):
        p = self._pair(37); self._nested(p, "GE", "GE", "37")
        code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "FAIL")

    def test_independent_lly_record_keeps_review(self):
        for inner in (None, "GE"):          # a nested "GE" inside the LLY record cannot pull it into GE scope either
            with self.subTest(inner=inner):
                p = self._pair(37)
                if inner is None:
                    p.summary_mut["NEW"] = lambda s: [r.setdefault("metrics", {}).update(cfo={"value": "37"}) for r in s["records"] if r["ticker"] == "LLY"]
                else:
                    self._nested(p, "LLY", inner, "37")
                code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "REVIEW", out)

    def test_first_ticker_below_an_unscoped_root_sets_scope(self):
        for tk, want in (("GE", "FAIL"), ("LLY", "REVIEW")):
            with self.subTest(tk=tk):
                p = self._pair(37); p.cov_mut["NEW"] = lambda c, tk=tk: c.update(misc={"ticker": tk, "cfo": {"value": "37", "ticker": "GE" if tk == "LLY" else "LLY"}})
                code, out = run_pair(p, profile=None); self.assertEqual(verdict(out, "F1", "NEW"), want, out)

    def test_scope_unit(self):
        leaves = ck._json_leaves({"records": [{"ticker": "GE", "m": {"ticker": "LLY", "x": {"value": 1}}}, {"ticker": "LLY", "value": 2}]})
        scope = {x: tk for _, _, x, tk, _ in leaves}
        self.assertEqual(scope[1.0], "GE"); self.assertEqual(scope[2.0], "LLY")

    def test_clean_inputs_without_nested_ticker_regression(self):
        code, out = run_pair(self._pair(37), profile=None); self.assertEqual(verdict(out, "F1", "NEW"), "PASS", out)
        code, out = run_pair(Pair()); self.assertEqual(code, 0, out)


class RawSourceWithoutInput(unittest.TestCase):
    """Round 4: batch_runner writes raw_input before raw_source, and never fetches EXCLUDED tickers."""

    def _extra(self, p, rec):
        def add(s): s["records"].append(copy.deepcopy(rec))
        p.summary_mut["NEW"] = add; p.summary_mut["BASE"] = add

    def _run(self, rec):
        p = Pair(); self._extra(p, rec); return run_pair(p, profile=None)

    def test_error_without_input_or_source_passes(self):
        for rec in ({"ticker": "ERRX", "cik": "0000000009", "status": "ERROR", "company_type": "NON_FINANCIAL"},
                    {"ticker": "ERRX", "cik": "0000000009", "status": "ERROR", "company_type": "NON_FINANCIAL", "raw_source": None}):
            with self.subTest(rec=rec):
                code, out = self._run(rec); self.assertEqual(verdict(out, "A8", "NEW"), "PASS", out); self.assertEqual(code, 0, out)

    def test_error_with_source_but_no_input_fails(self):
        for src in ("FETCHED", "CACHE"):
            with self.subTest(src=src):
                code, out = self._run({"ticker": "AAPX", "cik": "0000000009", "status": "ERROR", "company_type": "NON_FINANCIAL", "raw_source": src})
                self.assertEqual(code, 1); self.assertEqual(verdict(out, "A8", "NEW"), "FAIL")
                self.assertIn(f"ERROR has raw_source '{src}' but no raw_input", out)

    def test_excluded_with_source_fails(self):
        for status, src in (("EXCLUDED_COMPANY_TYPE", "FETCHED"), ("EXCLUDED_COMPANY_TYPE", "CACHE"),
                            ("EXCLUDED_NOT_IN_WATCHLIST", "FETCHED"), ("EXCLUDED_NOT_IN_WATCHLIST", "CACHE")):
            with self.subTest(status=status, src=src):
                code, out = self._run({"ticker": "EXCL", "cik": "0000000008", "status": status, "raw_source": src})
                self.assertEqual(verdict(out, "A8", "NEW"), "FAIL"); self.assertIn("but has raw_source", out)

    def test_excluded_without_source_passes(self):
        for status in ("EXCLUDED_COMPANY_TYPE", "EXCLUDED_NOT_IN_WATCHLIST"):
            with self.subTest(status=status):
                code, out = self._run({"ticker": "EXCL", "cik": "0000000008", "status": status})
                self.assertEqual(verdict(out, "A8", "NEW"), "PASS", out)

    def test_error_after_load_with_manifest_passes(self):
        p = Pair(); body = raw_file("ERRL", "V1")
        for lab in ("NEW", "BASE"): p.raw[lab]["ERRL_companyfacts.json"] = body
        self._extra(p, {"ticker": "ERRL", "cik": "0000000001", "status": "ERROR", "company_type": "NON_FINANCIAL", "raw_source": "FETCHED",
                        "raw_input": {"path": "ERRL_companyfacts.json", "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}})
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "A8", "NEW"), "PASS", out); self.assertEqual(code, 0, out)

    def test_error_after_load_without_manifest_fails(self):
        code, out = self._run({"ticker": "ERRL", "cik": "0000000001", "status": "ERROR", "company_type": "NON_FINANCIAL", "raw_source": "FETCHED",
                               "raw_input": {"path": "ERRL_companyfacts.json", "sha256": "0" * 64, "size_bytes": 10}})
        self.assertEqual(verdict(out, "A8", "NEW"), "FAIL"); self.assertIn("missing from manifest", out)



class MalformedTypesNeverCrash(unittest.TestCase):
    """Final hardening: malformed JSON types produce a FAIL report, never a traceback (and never hash a bad value)."""

    def _assert_fail_report(self, code, out):
        self.assertEqual(code, 1, out)
        self.assertIn("OVERALL FAIL", out); self.assertNotIn("Traceback", out)

    def test_manifest_ticker_non_string(self):
        for label, bad in (("list", ["AAPL"]), ("dict", {"t": "AAPL"}), ("null", None), ("int", 7), ("bool", True), ("float", 1.5)):
            with self.subTest(ticker=label):
                p = Pair(); p.manifest_mut["NEW"] = lambda es, b=bad: es[0].update(ticker=copy.deepcopy(b))
                code, out = run_pair(p, profile=None)
                self._assert_fail_report(code, out)
                self.assertEqual(verdict(out, "A6", "NEW"), "FAIL"); self.assertEqual(verdict(out, "A8", "NEW"), "FAIL")
                self.assertIn("non-string ticker", out); self.assertNotIn(" X0 ", out)

    def test_string_ticker_normal_and_duplicate(self):
        code, out = run_pair(Pair(), profile=None); self.assertEqual(code, 0, out)
        def dup(es):
            e = dict(es[0]); e["path"] = "AAPL_dup.json"; es.append(e); es.sort(key=lambda x: x["path"])
        p = Pair(); p.manifest_mut["NEW"] = dup
        code, out = run_pair(p, profile=None)
        self._assert_fail_report(code, out); self.assertIn("duplicate ticker in manifest: ['AAPL']", out)

    def test_other_manifest_fields_unhashable(self):
        for field in ("path", "source", "cik", "sha256", "size_bytes"):
            for bad in (["x"], {"x": 1}):
                with self.subTest(field=field, bad=type(bad).__name__):
                    p = Pair(); p.manifest_mut["NEW"] = lambda es, f=field, b=bad: es[0].update({f: copy.deepcopy(b)})
                    code, out = run_pair(p, profile=None)
                    self._assert_fail_report(code, out); self.assertEqual(verdict(out, "A6", "NEW"), "FAIL")
                    self.assertNotIn(" X0 ", out)          # handled by the owning checks, not by group isolation

    def test_manifest_entry_not_an_object(self):
        for bad in (["AAPL"], "AAPL", None, 3):
            with self.subTest(entry=type(bad).__name__):
                p = Pair(); p.manifest_mut["NEW"] = lambda es, b=bad: es.__setitem__(0, copy.deepcopy(b))
                code, out = run_pair(p, profile=None)
                self._assert_fail_report(code, out); self.assertEqual(verdict(out, "A6", "NEW"), "FAIL")

    def test_summary_raw_source_unhashable(self):
        for bad in (["FETCHED"], {"s": "FETCHED"}):
            with self.subTest(bad=type(bad).__name__):
                p = Pair(); p.summary_mut["NEW"] = lambda s, b=bad: s["records"][0].update(raw_source=copy.deepcopy(b))
                code, out = run_pair(p, profile=None)
                self._assert_fail_report(code, out); self.assertEqual(verdict(out, "A8", "NEW"), "FAIL")

    def test_gate_and_stage_values_malformed(self):
        cases = {
            "withheld item list": lambda p: ge_gate(p)["withheld"].append(["cfo"]),
            "scoped not a list": lambda p: ge_gate(p).update(scoped=3),
            "relabelled item dict": lambda p: ge_gate(p)["relabelled"]["derived"].append({"x": 1}),
            "unmasked_originals not an object": lambda p: ge_gate(p).update(unmasked_originals=[1]),
            "derived status list": lambda p: ge(p)["fcf"].update(status=["WITHHELD"]),
            "sufficiency status dict": lambda p: ge(p, stage="data_sufficiency")["fcf_yoy"].update(calculation_status={"s": 1}),
            "withheld_components int": lambda p: p.res["NEW"]["GE"]["stages"]["growth"]["growth"]["growth"].update(withheld_components=5),
            "stages is a list": lambda p: p.res["NEW"]["LLY"].update(stages=[1]),
            "pit_gating is a list": lambda p: p.res["NEW"]["LLY"]["stages"].update(pit_gating=[1]),
        }
        for label, fn in cases.items():
            with self.subTest(case=label):
                p = Pair(); fn(p)
                code, out = run_pair(p, profile=None); self._assert_fail_report(code, out)

    def test_unhashable_status_counted_not_crashed(self):
        p = Pair(); ge(p)["fcf"].update(status=["WITHHELD"])
        code, out = run_pair(p, profile=None)
        self.assertIsNotNone(verdict(out, "C1", "NEW")); self.assertNotIn("X0 coverage", out)

    def test_gate_malformed_is_named_by_b0(self):
        p = Pair(); ge_gate(p)["withheld"].append(["cfo"])
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "B0", "NEW"), "FAIL"); self.assertIn("gate structure malformed (withheld is a list with non-string items", out)

    def test_check_group_isolation(self):
        """An unexpected exception in one group is FAIL for that group only; the others still report."""
        orig = ck.Checker.check_diff
        def boom(self): raise KeyError(SECRET["cfo"])
        ck.Checker.check_diff = boom
        try:
            code, out = run_pair(Pair(), profile=None)
        finally:
            ck.Checker.check_diff = orig
        self._assert_fail_report(code, out)
        self.assertIn("X0 run comparison (D) checks aborted on malformed input", out); self.assertIn("KeyError in boom", out)
        for cid in ("A1", "B2-B4", "C1", "E1", "F1"): self.assertEqual(verdict(out, cid), "PASS", cid)
        self.assertNotIn(str(SECRET["cfo"]), out)              # exception text (here: a WITHHELD value) is never printed

    def test_targets_collected_before_any_group(self):
        orig = ck.Checker.check_integrity
        seen = {}
        def probe(self): seen["targets"] = list(self.targets); raise RuntimeError("x")
        ck.Checker.check_integrity = probe
        try:
            run_pair(Pair(), profile=None)
        finally:
            ck.Checker.check_integrity = orig
        self.assertEqual(sorted(t["value"] for t in seen["targets"]) , sorted(float(v) for v in list(SECRET.values()) * 2))

    def test_main_last_line_of_defence(self):
        orig = ck.Checker.run
        def boom(self): raise ValueError(SECRET["capex"])
        ck.Checker.run = boom
        try:
            code, out = run_pair(Pair(), profile=None)
        finally:
            ck.Checker.run = orig
        self.assertEqual(code, 1); self.assertIn("[FAIL] checker could not complete: ValueError", out)
        self.assertNotIn(str(SECRET["capex"]), out); self.assertNotIn("Traceback", out)


def ge_gate(p, lab="NEW"):
    return p.res[lab]["GE"]["stages"]["eligibility_gate"]



def stg(p, lab, t="AAPL"):
    return p.res[lab][t]["stages"]


class SymmetricSchema(unittest.TestCase):
    """Round 5: identical damage in BASE and NEW must still FAIL, in the owning per-run check (T0/B0/B5), not X0."""

    def _sym(self, fn, labs=("NEW", "BASE")):
        p = Pair()
        for lab in labs: fn(p, lab)
        return run_pair(p, profile=None)

    def _owner_fail(self, out, code, check, labs=("NEW", "BASE"), text=None):
        self.assertEqual(code, 1, out); self.assertIn("OVERALL FAIL", out); self.assertNotIn(" X0 ", out)
        for lab in labs: self.assertEqual(verdict(out, check, lab), "FAIL", f"{check}[{lab}]\n{out}")
        if text: self.assertIn(text, out)

    def test_gpt_case_a_status_list_both_runs(self):
        """GPT repro: fcf.status = ["MALFORMED_STATUS"] in both runs, coverage consistent with it -> was PASS."""
        code, out = self._sym(lambda p, lab: stg(p, lab)["derived_metrics"]["derived"]["derived_metrics"]["fcf"].update(status=["MALFORMED_STATUS"]))
        self._owner_fail(out, code, "T0", text="derived_metrics.fcf.status is list, not a string")

    def test_containers_not_objects(self):
        """normalized.metrics has no other owner than T0 (S1 covers only the three computed stages)."""
        for label, fn in (("normalized.metrics", lambda p, lab: stg(p, lab)["normalization"]["normalized"].update(metrics=[{"metric": "cash"}])),
                          ("eligibility_gate.unmasked_originals.cfo", lambda p, lab: stg(p, lab, "GE")["eligibility_gate"]["unmasked_originals"].update(cfo=["x"]))):
            with self.subTest(container=label):
                code, out = self._sym(fn)
                self._owner_fail(out, code, "T0", text=label)

    def test_derived_item_not_object(self):
        code, out = self._sym(lambda p, lab: stg(p, lab)["derived_metrics"]["derived"]["derived_metrics"].update(fcf=["x"]))
        self._owner_fail(out, code, "T0", text="derived_metrics.fcf is list, not an object")

    def test_derived_status_list_or_dict(self):
        for bad in (["OK"], {"s": "OK"}):
            with self.subTest(bad=type(bad).__name__):
                code, out = self._sym(lambda p, lab, b=bad: stg(p, lab)["derived_metrics"]["derived"]["derived_metrics"]["fcf"].update(status=copy.deepcopy(b)))
                self._owner_fail(out, code, "T0")

    def test_normalized_status_list_or_dict(self):
        for bad in (["OK"], {"s": "OK"}):
            with self.subTest(bad=type(bad).__name__):
                code, out = self._sym(lambda p, lab, b=bad: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update(status=copy.deepcopy(b)))
                self._owner_fail(out, code, "T0", text="normalized.metrics.revenue.status is")

    def test_growth_component_not_object(self):
        code, out = self._sym(lambda p, lab: stg(p, lab)["growth"]["growth"]["components"].update(fcf_yoy=["x"]))
        self._owner_fail(out, code, "T0", text="growth.components.fcf_yoy is list")

    def test_growth_aggregate_status_not_string(self):
        for bad in (1, ["OK"], None):
            with self.subTest(bad=repr(bad)):
                code, out = self._sym(lambda p, lab, b=bad: stg(p, lab)["growth"]["growth"]["growth"].update(status=copy.deepcopy(b)))
                self._owner_fail(out, code, "T0", text="growth aggregate status is")

    def test_withheld_components_dict_or_int(self):
        """GPT Case B: {"bad": 1} was silently treated as an empty list -> PASS."""
        for t in ("AAPL", "GE"):
            for bad in ({"bad": 1}, 5):
                with self.subTest(ticker=t, bad=repr(bad)):
                    code, out = self._sym(lambda p, lab, b=bad, t=t: stg(p, lab, t)["growth"]["growth"]["growth"].update(withheld_components=copy.deepcopy(b)))
                    self._owner_fail(out, code, "T0", text="withheld_components is")
                    if t == "GE":                       # the dependent rule says it cannot evaluate, rather than passing
                        line = [l for l in out.splitlines() if "] NEW  B2-B4 GE" in l][0]
                        self.assertIn("FAIL", line); self.assertIn("growth.withheld_components malformed (see T0)", line)

    def test_sufficiency_item_not_object(self):
        code, out = self._sym(lambda p, lab: stg(p, lab)["data_sufficiency"]["sufficiency"].update(fcf_yoy=["x"]))
        self._owner_fail(out, code, "T0", text="data_sufficiency.sufficiency.fcf_yoy is list")

    def test_calculation_status_dict_or_list(self):
        for bad in ({"s": "OK"}, ["OK"]):
            with self.subTest(bad=type(bad).__name__):
                code, out = self._sym(lambda p, lab, b=bad: stg(p, lab)["data_sufficiency"]["sufficiency"]["fcf_yoy"].update(calculation_status=copy.deepcopy(b)))
                self._owner_fail(out, code, "T0", text="calculation_status is")

    def test_frame_bad_types_new_only(self):
        """GPT Case C (frame = []) plus non-empty containers, which a flattened scan would miss."""
        for bad in ([], ["CY2025"], {}, {"a": 1}, 2025, True):
            with self.subTest(bad=repr(bad)):
                code, out = self._sym(lambda p, lab, b=bad: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update(frame=copy.deepcopy(b)), labs=("NEW",))
                self._owner_fail(out, code, "T0", labs=("NEW",), text="/frame is")
                self.assertEqual(verdict(out, "T0", "BASE"), "PASS")

    def test_frame_bad_type_both_runs(self):
        code, out = self._sym(lambda p, lab: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update(frame=["CY2025"]))
        self._owner_fail(out, code, "T0")

    def test_frame_string_or_null_change_still_allowed(self):
        for val in ("CY2024", None):
            with self.subTest(val=val):
                code, out = self._sym(lambda p, lab, v=val: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update(frame=v), labs=("NEW",))
                self.assertEqual(code, 0, out); self.assertEqual(verdict(out, "T0", "NEW"), "PASS"); self.assertIn("FRAME_METADATA_ONLY", out)

    def test_identity_field_types_both_runs(self):
        for field, bad in (("filing_date", []), ("accession", {"a": 1}), ("period_end", 20251231), ("fy", "2025"), ("value", "100"), ("unit", True)):
            with self.subTest(field=field):
                code, out = self._sym(lambda p, lab, f=field, b=bad: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update({f: copy.deepcopy(b)}))
                self._owner_fail(out, code, "T0", text=f"normalized.metrics.revenue.{field} is")

    def test_ok_observation_without_source_identity(self):
        """An OK value with no filing date could never be PIT-checked (E1 only compares strings)."""
        for field in ("filing_date", "accession", "period_end", "value"):
            with self.subTest(field=field):
                code, out = self._sym(lambda p, lab, f=field: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update({f: None}))
                self._owner_fail(out, code, "T0", text="status OK without valid source identity")

    def test_non_ok_observation_may_lack_identity(self):
        def fn(p, lab):
            m = stg(p, lab)["normalization"]["normalized"]["metrics"]["cash"]
            m.update(status="MISSING", value=None, filing_date=None, accession=None, period_end=None)
        code, out = self._sym(fn)
        self.assertEqual(verdict(out, "T0", "NEW"), "PASS"); self.assertEqual(verdict(out, "T0", "BASE"), "PASS")

    def test_composite_components_checked(self):
        def comp(p, lab, bad):
            m = stg(p, lab)["normalization"]["normalized"]["metrics"]["total_debt"]
            m["components"] = {"current_debt": dict(metric("current_debt", 5_000)), "noncurrent_debt": bad}
            for k in ("filing_date", "accession", "period_end"): m.pop(k, None)
        code, out = self._sym(lambda p, lab: comp(p, lab, dict(metric("noncurrent_debt", 15_000))))
        self.assertEqual(verdict(out, "T0", "NEW"), "PASS", out)          # composite without top-level identity is valid
        code, out = self._sym(lambda p, lab: comp(p, lab, ["x"]))
        self._owner_fail(out, code, "T0", text="components.noncurrent_debt is list")
        code, out = self._sym(lambda p, lab: comp(p, lab, dict(metric("noncurrent_debt", 15_000), filing_date=None)))
        self._owner_fail(out, code, "T0", text="components.noncurrent_debt: status OK without valid source identity")

    def test_non_governed_withheld_components_both_runs(self):
        """Well-typed but impossible: a ticker with no withheld input lists withheld growth components."""
        code, out = self._sym(lambda p, lab: stg(p, lab)["growth"]["growth"]["growth"].update(withheld_components=["fcf_yoy"]))
        self._owner_fail(out, code, "B5", text="AAPL: growth.withheld_components non-empty")

    def test_gate_malformed_both_runs_named_not_isolated(self):
        code, out = self._sym(lambda p, lab: stg(p, lab, "GE")["eligibility_gate"]["withheld"].append(["cfo"]))
        self._owner_fail(out, code, "B0", text="gate structure malformed (withheld is a list with non-string items")
        self.assertIn("not evaluated: gate fields malformed (see B0)", out)

    def test_t0_is_independent_of_comparison(self):
        """Same damage on both sides: D2 sees no change and C1 agrees with itself; only T0 can fail it."""
        code, out = self._sym(lambda p, lab: stg(p, lab)["derived_metrics"]["derived"]["derived_metrics"]["fcf"].update(status=["X"]))
        self.assertEqual(verdict(out, "D2"), "PASS"); self.assertEqual(verdict(out, "C1", "NEW"), "PASS")
        self.assertEqual(verdict(out, "T0", "NEW"), "FAIL")



class PitIdentityAndGateClosure(unittest.TestCase):
    """Round 6: PIT source identity is validated semantically, composite components need a status, and an applied
    gate has its exact production structure. Symmetric damage must FAIL in the owning check (T0 / B0), not X0."""

    def _sym(self, fn, labs=("NEW", "BASE")):
        p = Pair()
        for lab in labs: fn(p, lab)
        return run_pair(p, profile=None)

    def _owner_fail(self, out, code, check, labs=("NEW", "BASE"), text=None):
        self.assertEqual(code, 1, out); self.assertIn("OVERALL FAIL", out); self.assertNotIn(" X0 ", out)
        for lab in labs: self.assertEqual(verdict(out, check, lab), "FAIL", f"{check}[{lab}]\n{out}")
        if text: self.assertIn(text, out)

    def _rev(self, p, lab): return stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"]

    # ---- PIT identity: normalized OK observation
    def test_gpt_f1_filing_date_and_accession(self):
        for upd in ({"filing_date": "0000"}, {"accession": "bad"}, {"filing_date": "0000", "accession": "bad"}):
            with self.subTest(upd=upd):
                code, out = self._sym(lambda p, lab, u=upd: self._rev(p, lab).update(u))
                self._owner_fail(out, code, "T0", text="status OK without valid source identity")

    def test_invalid_dates_semantic_not_lexicographic(self):
        """'2025-13-01', '2025-02-30', '20251231', '2025-1-01' would all survive a string comparison."""
        for field in ("filing_date", "period_end", "period_start"):
            for bad in ("2025-13-01", "2025-02-30", "20251231", "2025-1-01", "2025-12-31T00:00", "0000"):
                with self.subTest(field=field, bad=bad):
                    code, out = self._sym(lambda p, lab, f=field, b=bad: self._rev(p, lab).update({f: b}))
                    self._owner_fail(out, code, "T0", text=field)

    def test_accession_format(self):
        for bad in ("bad", "0000000001-26-00001", "000000001-26-000010", "0000000001_26_000010", "0000000001-26-0000100", " 0000000001-26-000010"):
            with self.subTest(bad=bad):
                code, out = self._sym(lambda p, lab, b=bad: self._rev(p, lab).update(accession=b))
                self._owner_fail(out, code, "T0", text="accession")

    def test_value_bool_nan_infinity(self):
        for bad in (True, float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=repr(bad)):
                code, out = self._sym(lambda p, lab, b=bad: self._rev(p, lab).update(value=b))
                self._owner_fail(out, code, "T0")

    def test_valid_identity_passes(self):
        def fn(p, lab):
            self._rev(p, lab).update(filing_date="2024-02-29", period_end="2023-12-31", period_start="2023-01-01",
                                     accession="0000320193-24-000123", value=-1.5)
        code, out = self._sym(fn)
        self.assertEqual(verdict(out, "T0", "NEW"), "PASS", out); self.assertEqual(verdict(out, "T0", "BASE"), "PASS")

    def test_null_period_start_allowed_for_instant(self):
        code, out = self._sym(lambda p, lab: self._rev(p, lab).update(period_start=None))
        self.assertEqual(verdict(out, "T0", "NEW"), "PASS", out)

    def test_non_ok_statuses_still_allowed(self):
        for status in ("MISSING", "NOT_APPLICABLE", "INCOMPLETE"):
            with self.subTest(status=status):
                def fn(p, lab, st_=status):
                    stg(p, lab)["normalization"]["normalized"]["metrics"]["cash"].update(
                        status=st_, value=None, filing_date=None, accession=None, period_end=None, period_start=None, frame=None)
                code, out = self._sym(fn)
                self.assertEqual(verdict(out, "T0", "NEW"), "PASS", out); self.assertEqual(verdict(out, "T0", "BASE"), "PASS")
        code, out = run_pair(Pair(), profile=None)          # GE's masked WITHHELD metrics (value null) are valid
        self.assertEqual(verdict(out, "T0", "NEW"), "PASS", out)

    def test_identity_keys_validated_anywhere(self):
        """E1/E2 read every filing_date / accession in the result, so T0 checks them wherever they occur."""
        def fn(p, lab):
            stg(p, lab).setdefault("debt_tiers", {})["total_debt_including_finance_leases"] = {"status": "INCOMPLETE", "accession": "bad", "period_end": "2025-99-99"}
        code, out = self._sym(fn)
        self._owner_fail(out, code, "T0", text="debt_tiers/total_debt_including_finance_leases/accession is not an SEC accession")

    def test_identity_dates_validated_anywhere_alone(self):
        """A malformed date on its own, outside normalized metrics and history (where only the walk sees it)."""
        for where, fn in (("debt_tiers", lambda p, lab: stg(p, lab).__setitem__("debt_tiers", {"tier2": {"status": "INCOMPLETE", "period_end": "2025-99-99"}})),
                          ("derived", lambda p, lab: stg(p, lab)["derived_metrics"]["derived"]["derived_metrics"]["fcf"].update(filing_date="0000"))):
            with self.subTest(where=where):
                code, out = self._sym(fn)
                self._owner_fail(out, code, "T0", text="is not an ISO date (YYYY-MM-DD) or null")

    def test_unmasked_original_identity_checked(self):
        code, out = self._sym(lambda p, lab: stg(p, lab, "GE")["eligibility_gate"]["unmasked_originals"]["cfo"].update(filing_date="0000"))
        self._owner_fail(out, code, "T0", text="eligibility_gate.unmasked_originals.cfo: status OK without valid source identity")
        self.assertNotIn(str(SECRET["cfo"]), out)

    # ---- annual history rows
    def _hist_rows(self, p, lab):
        rows = [{"accn": "0000000001-25-000010", "end": "2024-12-31", "filed": "2025-02-01", "form": "10-K", "fp": "FY",
                 "fy": 2024, "start": "2024-01-01", "val": 90_000, "_unit": "USD"}]
        stg(p, lab)["annual_history"] = {"status": "OK", "history": {"revenue": rows}}
        return rows

    def test_history_valid_rows_pass(self):
        code, out = self._sym(lambda p, lab: self._hist_rows(p, lab))
        self.assertEqual(verdict(out, "T0", "NEW"), "PASS", out)

    def test_history_invalid_identity(self):
        for field, bad in (("filed", "2025-02-30"), ("filed", None), ("accn", "bad"), ("accn", None), ("end", "2024-12-32"),
                           ("start", "x"), ("val", float("nan")), ("val", True)):
            with self.subTest(field=field, bad=repr(bad)):
                code, out = self._sym(lambda p, lab, f=field, b=bad: self._hist_rows(p, lab)[0].update({f: b}))
                self._owner_fail(out, code, "T0", text="annual_history.revenue[0]")

    def test_history_required_fields_absent(self):
        for field in ("filed", "accn"):
            with self.subTest(field=field):
                code, out = self._sym(lambda p, lab, f=field: self._hist_rows(p, lab)[0].pop(f))
                self._owner_fail(out, code, "T0", text=f"invalid source identity ['{field}']")

    # ---- composite components
    def _composite(self, p, lab, comps, status="OK"):
        m = stg(p, lab)["normalization"]["normalized"]["metrics"]["total_debt"]
        for k in ("filing_date", "accession", "period_end", "period_start", "form", "concept", "namespace", "fy", "fp", "frame"): m.pop(k, None)
        m["status"] = status; m["components"] = comps

    def test_gpt_f2_component_status_missing(self):
        comp = {"value": 100, "filing_date": "2026-02-01", "accession": "0000000001-26-000010", "period_end": "2025-12-31"}
        code, out = self._sym(lambda p, lab: self._composite(p, lab, {"component_1": dict(comp)}))
        self._owner_fail(out, code, "T0", text="components.component_1.status is absent, not a string")

    def test_component_status_null_dict_list(self):
        for bad in (None, {"s": "OK"}, ["OK"]):
            with self.subTest(bad=repr(bad)):
                code, out = self._sym(lambda p, lab, b=bad: self._composite(p, lab, {"c": dict(metric("current_debt", 5), status=copy.deepcopy(b))}))
                self._owner_fail(out, code, "T0", text="components.c.status is")

    def test_component_ok_without_identity(self):
        for field in ("filing_date", "accession", "period_end", "value"):
            with self.subTest(field=field):
                code, out = self._sym(lambda p, lab, f=field: self._composite(p, lab, {"c": dict(metric("current_debt", 5), **{f: None})}))
                self._owner_fail(out, code, "T0", text="components.c: status OK without valid source identity")

    def test_valid_composite_without_parent_identity_passes(self):
        def fn(p, lab):
            self._composite(p, lab, {"current_debt": dict(metric("current_debt", 5_000)),
                                     "noncurrent_debt": dict(metric("noncurrent_debt", 15_000), accession="0000000002-26-000011")})
        code, out = self._sym(fn)
        self.assertEqual(verdict(out, "T0", "NEW"), "PASS", out); self.assertEqual(verdict(out, "T0", "BASE"), "PASS")

    def test_ok_composite_with_no_components(self):
        code, out = self._sym(lambda p, lab: self._composite(p, lab, {}))
        self._owner_fail(out, code, "T0", text="total_debt.components keys [] != ['current_debt', 'noncurrent_debt']")
        # calculate_total_debt() builds both components for every status, so an empty set is never valid
        code, out = self._sym(lambda p, lab: self._composite(p, lab, {}, status="INCOMPLETE"))
        self._owner_fail(out, code, "T0", text="components keys [] !=")

    # ---- governance gate exact structure
    def test_gpt_f3_each_gate_field_removed(self):
        for field in ("withheld", "scoped", "not_yet_available", "no_record", "unmasked_originals", "relabelled"):
            for t in ("AAPL", "GE"):
                with self.subTest(field=field, ticker=t):
                    code, out = self._sym(lambda p, lab, f=field, t=t: stg(p, lab, t)["eligibility_gate"].pop(f))
                    self._owner_fail(out, code, "B0", text=f"{t}: gate structure malformed ({field} is absent")

    def test_gate_fields_null(self):
        for field in ("withheld", "no_record", "unmasked_originals", "relabelled"):
            with self.subTest(field=field):
                code, out = self._sym(lambda p, lab, f=field: stg(p, lab)["eligibility_gate"].update({f: None}))
                self._owner_fail(out, code, "B0", text=f"{field} is NoneType")

    def test_relabelled_stage_missing_or_extra_or_nonstring(self):
        cases = {"stage missing": lambda r: r.pop("growth"), "extra stage": lambda r: r.update(other=[]),
                 "non-string item": lambda r: r["derived"].append(3), "stage not a list": lambda r: r.update(derived="fcf")}
        for label, fn in cases.items():
            with self.subTest(case=label):
                code, out = self._sym(lambda p, lab, f=fn: f(stg(p, lab)["eligibility_gate"]["relabelled"]))
                self._owner_fail(out, code, "B0", text="relabelled")

    def test_empty_lists_and_objects_are_valid(self):
        code, out = run_pair(Pair(), profile=None)          # AAPL/LLY: empty lists, {} originals, empty relabelled lists
        self.assertEqual(code, 0, out); self.assertEqual(verdict(out, "B0", "NEW"), "PASS")

    def test_gate_skipped_rule(self):
        """SKIPPED is the production record when no eligibility rows were supplied: allowed only under that policy."""
        skipped = {"status": "SKIPPED", "reason": "No metric_eligibility_rows provided."}
        code, out = self._sym(lambda p, lab: stg(p, lab)["eligibility_gate"].clear() or stg(p, lab)["eligibility_gate"].update(skipped))
        self._owner_fail(out, code, "B0", text="AAPL: gate status 'SKIPPED' under policy")
        p = Pair()
        for lab in ("NEW", "BASE"):
            p.policy[lab] = ck.POLICY_NOT_APPLIED
            for t in p.tickers:
                stg(p, lab, t)["eligibility_gate"] = dict(skipped)
                for k in ("cfo", "capex", "operating_income"):
                    if stg(p, lab, t)["normalization"]["normalized"]["metrics"].get(k, {}).get("status") == "WITHHELD":
                        stg(p, lab, t)["normalization"]["normalized"]["metrics"][k] = metric(k, 1_000)
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "B0", "NEW"), "PASS", out); self.assertIn("3 SKIPPED under NO_METRIC_ELIGIBILITY_GATE", out)
        self.assertNotIn(" X0 ", out)



class Round61CompositeSkippedFinite(unittest.TestCase):
    """Round 6.1: exact total_debt composite contract, exact SKIPPED gate record, overflow-safe numbers.
    Every damage case is run NEW only, BASE only and symmetric; the owner (T0/B0) must FAIL directly, no X0."""
    MODES = (("NEW",), ("BASE",), ("NEW", "BASE"))

    def _run(self, fn, labs):
        p = Pair()
        for lab in labs: fn(p, lab)
        return run_pair(p, profile=None)

    def _all_modes_fail(self, fn, check, text=None):
        for labs in self.MODES:
            with self.subTest(mode="+".join(labs)):
                code, out = self._run(fn, labs)
                self.assertEqual(code, 1, out); self.assertIn("OVERALL FAIL", out); self.assertNotIn(" X0 ", out)
                for lab in labs: self.assertEqual(verdict(out, check, lab), "FAIL", f"{check}[{lab}]\n{out}")
                for lab in {"NEW", "BASE"} - set(labs): self.assertEqual(verdict(out, check, lab), "PASS", f"{check}[{lab}] untouched")
                if text: self.assertIn(text, out)

    def _td(self, p, lab, t="AAPL"): return stg(p, lab, t)["normalization"]["normalized"]["metrics"]["total_debt"]

    # ---- A. composite completeness
    def test_component_deleted(self):
        for comp in ("current_debt", "noncurrent_debt"):
            with self.subTest(component=comp):
                self._all_modes_fail(lambda p, lab, c=comp: self._td(p, lab)["components"].pop(c), "T0", text="total_debt.components keys")

    def test_components_key_deleted_even_with_parent_identity(self):
        def fn(p, lab):
            m = self._td(p, lab); m.pop("components")
            m.update(filing_date="2026-02-01", accession="0000000001-26-000010", period_end="2025-12-31", value=20_000)
        self._all_modes_fail(fn, "T0", text="total_debt: components missing")

    def test_components_empty_or_extra(self):
        self._all_modes_fail(lambda p, lab: self._td(p, lab).update(components={}), "T0", text="components keys [] !=")
        self._all_modes_fail(lambda p, lab: self._td(p, lab)["components"].update(lease=metric("lease", 1)), "T0", text="components keys ['current_debt', 'lease', 'noncurrent_debt']")

    def test_components_wrong_type(self):
        for bad in ([], None, "current_debt,noncurrent_debt", 3):
            with self.subTest(bad=repr(bad)):
                self._all_modes_fail(lambda p, lab, b=bad: self._td(p, lab).update(components=copy.deepcopy(b)), "T0", text="total_debt.components is")

    def test_components_on_other_metric(self):
        """Claude: an OK value with "components" used to skip its own identity check."""
        def fn(p, lab):
            stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"] = {"metric": "revenue", "status": "OK", "value": 1,
                                                                               "components": {"x": metric("x", 1)}}
        self._all_modes_fail(fn, "T0", text="components are not allowed on 'revenue'")

    def test_ok_composite_value_must_be_finite(self):
        for bad in (None, True, float("nan")):
            with self.subTest(bad=repr(bad)):
                self._all_modes_fail(lambda p, lab, b=bad: self._td(p, lab).update(value=b), "T0", text="status OK composite without a finite value")

    def test_component_status_and_identity_rules_kept(self):
        self._all_modes_fail(lambda p, lab: self._td(p, lab)["components"]["current_debt"].pop("status"), "T0", text="current_debt.status is absent")
        self._all_modes_fail(lambda p, lab: self._td(p, lab)["components"]["current_debt"].update(accession="bad"), "T0",
                             text="current_debt: status OK without valid source identity")

    def test_valid_composites_pass(self):
        def incomplete(p, lab):
            m = self._td(p, lab); m.update(status="INCOMPLETE", value=None)
            m["components"]["noncurrent_debt"] = {"metric": "noncurrent_debt", "status": "MISSING", "value": None}
        def missing_both(p, lab):
            m = self._td(p, lab); m.update(status="INCOMPLETE", value=None)
            m["components"]["current_debt"] = {"metric": "current_debt", "status": "INCOMPLETE", "value": None}
            m["components"]["noncurrent_debt"] = {"metric": "noncurrent_debt", "status": "MISSING", "value": None}
        for label, fn in (("both OK", lambda p, lab: None), ("one MISSING", incomplete), ("INCOMPLETE+MISSING", missing_both)):
            with self.subTest(case=label):
                code, out = self._run(fn, ("NEW", "BASE"))
                self.assertEqual(verdict(out, "T0", "NEW"), "PASS", out); self.assertEqual(verdict(out, "T0", "BASE"), "PASS")

    def test_gate_masked_total_debt_is_not_a_composite(self):
        """apply_metric_eligibility_gate() replaces a WITHHELD metric with a mask that has no components; the
        original composite moves to unmasked_originals and is checked there."""
        def withhold(p, lab, original):
            g = stg(p, lab, "GE")["eligibility_gate"]; m = stg(p, lab, "GE")["normalization"]["normalized"]["metrics"]
            g["scoped"] = []; g["withheld"].append("total_debt"); g["unmasked_originals"]["total_debt"] = original
            m["total_debt"] = {"status": "WITHHELD", "value": None, "unit": "USD", "reason": "Governance eligibility WITHHELD.",
                               "eligibility": {"governance_status": "WITHHELD"}, "original_status": "OK"}
        code, out = self._run(lambda p, lab: withhold(p, lab, total_debt_composite()), ("NEW", "BASE"))
        self.assertEqual(verdict(out, "T0", "NEW"), "PASS", out); self.assertEqual(verdict(out, "T0", "BASE"), "PASS")
        bad = total_debt_composite(); bad["components"].pop("noncurrent_debt")
        code, out = self._run(lambda p, lab: withhold(p, lab, copy.deepcopy(bad)), ("NEW", "BASE"))
        self.assertEqual(verdict(out, "T0", "NEW"), "FAIL", out); self.assertIn("unmasked_originals.total_debt.components keys", out)
        self.assertNotIn(" X0 ", out)
        # a WITHHELD record without the gate's eligibility signature is not a mask
        self._all_modes_fail(lambda p, lab: self._td(p, lab).__setitem__("total_debt", None) or self._td(p, lab).clear() or
                             self._td(p, lab).update(status="WITHHELD", value=None), "T0", text="total_debt: components missing")

    # ---- B. SKIPPED gate record
    def _skipped_pair(self, labs, record):
        p = Pair()
        for lab in ("NEW", "BASE"):
            p.policy[lab] = ck.POLICY_NOT_APPLIED
            for t in p.tickers:
                stg(p, lab, t)["eligibility_gate"] = copy.deepcopy(record if (lab in labs and t == "AAPL") else
                                                                   {"status": "SKIPPED", "reason": "No metric_eligibility_rows provided."})
                for k in ("cfo", "capex", "operating_income"):
                    if stg(p, lab, t)["normalization"]["normalized"]["metrics"].get(k, {}).get("status") == "WITHHELD":
                        stg(p, lab, t)["normalization"]["normalized"]["metrics"][k] = metric(k, 1_000)
        return run_pair(p, profile=None)

    def test_skipped_reason_required(self):
        for label, rec in (("reason deleted", {"status": "SKIPPED"}), ("reason null", {"status": "SKIPPED", "reason": None}),
                           ("reason number", {"status": "SKIPPED", "reason": 3}), ("reason object", {"status": "SKIPPED", "reason": {"x": 1}}),
                           ("reason empty", {"status": "SKIPPED", "reason": "  "}), ("extra key", {"status": "SKIPPED", "reason": "x", "withheld": []})):
            for labs in self.MODES:
                with self.subTest(case=label, mode="+".join(labs)):
                    code, out = self._skipped_pair(labs, rec)
                    self.assertNotIn(" X0 ", out)
                    for lab in labs: self.assertEqual(verdict(out, "B0", lab), "FAIL", out)
                    self.assertIn("AAPL: SKIPPED gate malformed", out)

    def test_skipped_valid_reason_passes(self):
        code, out = self._skipped_pair((), {})
        self.assertEqual(verdict(out, "B0", "NEW"), "PASS", out); self.assertEqual(verdict(out, "B0", "BASE"), "PASS")
        self.assertIn("3 SKIPPED under NO_METRIC_ELIGIBILITY_GATE", out)

    def test_skipped_under_applied_policy_still_fails(self):
        self._all_modes_fail(lambda p, lab: stg(p, lab)["eligibility_gate"].clear() or
                             stg(p, lab)["eligibility_gate"].update(status="SKIPPED", reason="No metric_eligibility_rows provided."),
                             "B0", text="AAPL: gate status 'SKIPPED' under policy")

    # ---- C. finite numbers without OverflowError (round 7: production contract = float()-representable and finite)
    def test_is_finite_number_unit(self):
        f = ck.is_finite_number
        for v in (0, 7, -3, 10**308, -(10**308), 1.5, -0.0, 1e308):
            with self.subTest(v=repr(v)[:20]): self.assertTrue(f(v))
        for v in (True, False, float("nan"), float("inf"), float("-inf"), 10**309, -(10**309), 10**400, "1", None, [1], {"v": 1}):
            with self.subTest(v=repr(v)[:20]): self.assertFalse(f(v))      # never raises

    def test_huge_int_never_crashes(self):
        """10**400 is beyond what production can use: T0 FAILs it directly; no OverflowError anywhere (T0, leak scan,
        self-check), no X0, no abort."""
        cases = {"revenue": lambda p, lab: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update(value=10**400),
                 "history val": lambda p, lab: stg(p, lab).update(annual_history={"status": "OK", "history": {"revenue": [
                     {"accn": "0000000001-25-000010", "end": "2024-12-31", "filed": "2025-02-01", "val": 10**400}]}}),
                 "GE original (leak target)": lambda p, lab: stg(p, lab, "GE")["eligibility_gate"]["unmasked_originals"]["cfo"].update(value=10**400)}
        for label, fn in cases.items():
            for labs in self.MODES:
                with self.subTest(case=label, mode="+".join(labs)):
                    code, out = self._run(fn, labs)
                    self.assertNotIn(" X0 ", out); self.assertNotIn("could not complete", out); self.assertNotIn("Traceback", out)
                    self.assertEqual(code, 1, out)
                    for lab in labs: self.assertEqual(verdict(out, "T0", lab), "FAIL", out)

    def test_huge_int_leak_still_detected_exactly(self):
        p = Pair()
        for lab in ("NEW", "BASE"): stg(p, lab, "GE")["eligibility_gate"]["unmasked_originals"]["cfo"]["value"] = 10**400
        p.summary_mut["NEW"] = lambda s: s.update(debug=10**400)
        code, out = run_pair(p, profile=None)
        self.assertEqual(verdict(out, "F1", "NEW"), "FAIL", out); self.assertNotIn(" X0 ", out)
        self.assertNotIn(str(10**400), out)

    def test_nan_inf_bool_value_still_fail(self):
        for bad in (float("nan"), float("inf"), float("-inf"), True, False):
            with self.subTest(bad=repr(bad)):
                self._all_modes_fail(lambda p, lab, b=bad: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update(value=b),
                                     "T0", text="status OK without valid source identity")



class Round7ExtremeInteger(unittest.TestCase):
    """Round 7: is_finite_number() follows production derived_metrics._is_number() (finite float()), never raises,
    and an extreme integer in an artifact is a direct T0 FAIL (no X0, no traceback)."""
    MODES = (("NEW",), ("BASE",), ("NEW", "BASE"))

    def test_unit_cases(self):
        f = ck.is_finite_number
        accepted = {"positive int": 12_345, "negative int": -12_345, "float": 3.25, "negative float": -0.5, "zero": 0, "zero float": 0.0,
                    "large finite float-representable int": 10**308}
        rejected = {"True": True, "False": False, "NaN": float("nan"), "+Infinity": float("inf"), "-Infinity": float("-inf"),
                    "10**309": 10**309, "-(10**309)": -(10**309), "string": "100", "null": None}
        for name, v in accepted.items():
            with self.subTest(accepted=name): self.assertIs(f(v), True)
        for name, v in rejected.items():
            with self.subTest(rejected=name): self.assertIs(f(v), False)

    def _run(self, fn, labs, zip_new=False):
        p = Pair()
        for lab in labs: fn(p, lab)
        return run_pair(p, profile=None, zip_new_members=(lambda f: sorted(f.items())) if zip_new else None)

    def _direct_t0_fail(self, code, out, labs):
        self.assertEqual(code, 1, out); self.assertIn("OVERALL FAIL", out)
        self.assertNotIn(" X0 ", out); self.assertNotIn("Traceback", out); self.assertNotIn("could not complete", out)
        for lab in labs: self.assertEqual(verdict(out, "T0", lab), "FAIL", f"T0[{lab}]\n{out}")
        for lab in {"NEW", "BASE"} - set(labs): self.assertEqual(verdict(out, "T0", lab), "PASS")
        self.assertIn("status OK without valid source identity ['value']", out)

    def test_extreme_integers_in_a_checker_run(self):
        for v in (10**309, -(10**309)):
            for labs in self.MODES:
                with self.subTest(v="10**309" if v > 0 else "-(10**309)", mode="+".join(labs)):
                    code, out = self._run(lambda p, lab, v=v: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update(value=v), labs)
                    self._direct_t0_fail(code, out, labs)
                    self.assertNotIn(str(abs(v))[:40], out)          # the value itself is never printed

    def test_extreme_integer_serialized_in_json_artifact(self):
        """The fixture writes real JSON files (and a real ZIP for NEW): the 310-digit literal is parsed back as an int."""
        v = 10**309
        literal = json.dumps({"value": v})
        self.assertIn("1" + "0" * 309, literal); self.assertEqual(json.loads(literal)["value"], v)
        for zip_new in (False, True):
            with self.subTest(input="zip" if zip_new else "directory"):
                code, out = self._run(lambda p, lab: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update(value=v),
                                      ("NEW", "BASE"), zip_new=zip_new)
                self._direct_t0_fail(code, out, ("NEW", "BASE"))

    def test_large_finite_float_representable_integer_passes(self):
        code, out = self._run(lambda p, lab: stg(p, lab)["normalization"]["normalized"]["metrics"]["revenue"].update(value=10**308), ("NEW", "BASE"))
        self.assertEqual(verdict(out, "T0", "NEW"), "PASS", out); self.assertNotIn(" X0 ", out)

    def test_extreme_integer_in_history_and_composite(self):
        def hist(p, lab):
            stg(p, lab)["annual_history"] = {"status": "OK", "history": {"revenue": [
                {"accn": "0000000001-25-000010", "end": "2024-12-31", "filed": "2025-02-01", "val": -(10**309)}]}}
        def comp(p, lab):
            stg(p, lab)["normalization"]["normalized"]["metrics"]["total_debt"]["value"] = 10**309
        for label, fn, text in (("history val", hist, "invalid source identity ['val']"),
                                ("composite value", comp, "status OK composite without a finite value")):
            with self.subTest(case=label):
                code, out = self._run(fn, ("NEW", "BASE"))
                self.assertEqual(code, 1); self.assertNotIn(" X0 ", out); self.assertIn(text, out)
                self.assertEqual(verdict(out, "T0", "NEW"), "FAIL"); self.assertEqual(verdict(out, "T0", "BASE"), "FAIL")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------- v2.4: D2 migration allowance (STEP 24.3 A-0)
INV_P = "Previous value must be greater than zero for conventional YoY calculation."
MIG = [("AAPL", "fcf_yoy", "NONPOSITIVE_PRIOR_BASE"), ("AAPL", "operating_income_yoy", "ZERO_PRIOR_BASE"), ("LLY", "fcf_yoy", "NONPOSITIVE_CURRENT")]


def mig_profile(entries=MIG, **binding_override):
    prof = copy.deepcopy(PROFILE)
    b = {"evaluation_date": DATE, "base_github_run_id": RUN_B, "base_github_sha": SHA_B, "new_github_run_id": RUN_N, "new_github_sha": SHA_N}
    b.update(binding_override)
    prof["allowed_added_paths"] = {"migration_id": "TEST_A1_REASON_CODE", "binding": b, "value_set": "GROWTH_REASON_CODES",
                                   "paths": [{"ticker": t, "path": f"/stages/growth/growth/components/{m}/reason_code"} for t, m, _ in entries]}
    prof["diff_classes"] = {"GE": ["RUN_METADATA", "GOVERNANCE_MARKER"], "AAPL": ["RUN_METADATA", "FRAME_METADATA_ONLY", "ALLOWED_ADDITION"],
                            "LLY": ["RUN_METADATA", "ALLOWED_ADDITION"], "*": ["RUN_METADATA"]}
    return prof


def mig_pair(entries=MIG, add=True):
    """INVALID components in BASE and NEW; NEW additionally carries reason_code (the A-1 change)."""
    p = Pair()
    for t, m, code in MIG:
        for lab in ("BASE", "NEW"):
            p.res[lab][t]["stages"]["growth"]["growth"]["components"][m] = {"status": "INVALID", "metric": m, "value": None, "reason": INV_P}
    if add:
        for t, m, code in entries:
            p.res["NEW"][t]["stages"]["growth"]["growth"]["components"][m]["reason_code"] = code
    return p


def comp(p, lab, t, m): return p.res[lab][t]["stages"]["growth"]["growth"]["components"][m]


class MigrationAllowanceV24(unittest.TestCase):
    def test_version_is_v24(self):
        self.assertEqual(ck.VERSION, "checker_v2.4")

    def test_exact_expected_additions_pass(self):
        code, out = run_pair(mig_pair(), profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "PASS", out)
        self.assertIn("allowed additions 3/3", out)
        self.assertEqual(code, 0, out)

    def test_unexpected_addition_fails(self):
        p = mig_pair(); comp(p, "NEW", "LLY", "fcf_yoy")["extra_flag"] = "X"
        code, out = run_pair(p, profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "FAIL"); self.assertEqual(code, 1)

    def test_unexpected_reason_code_on_unlisted_component_fails(self):
        p = mig_pair(); comp(p, "BASE", "LLY", "operating_income_yoy").update(status="INVALID")
        comp(p, "NEW", "LLY", "operating_income_yoy").update(status="INVALID", reason_code="NONPOSITIVE_PRIOR_BASE")
        code, out = run_pair(p, profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "FAIL")

    def test_expected_path_missing_fails(self):
        p = mig_pair(); comp(p, "NEW", "LLY", "fcf_yoy").pop("reason_code")
        code, out = run_pair(p, profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "FAIL"); self.assertIn("expected addition(s) not found", out)

    def test_field_deletion_fails(self):
        p = mig_pair(); comp(p, "NEW", "AAPL", "fcf_yoy").pop("reason")
        code, out = run_pair(p, profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "FAIL")

    def test_existing_value_change_fails(self):
        p = mig_pair(); comp(p, "NEW", "AAPL", "fcf_yoy")["reason"] = "Reworded message."
        code, out = run_pair(p, profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "FAIL")

    def test_reason_code_value_change_is_not_an_addition(self):
        p = mig_pair(); comp(p, "BASE", "AAPL", "fcf_yoy")["reason_code"] = "NONPOSITIVE_CURRENT"   # present in BASE already
        code, out = run_pair(p, profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "FAIL")

    def test_unregistered_reason_code_fails(self):
        p = mig_pair(); comp(p, "NEW", "LLY", "fcf_yoy")["reason_code"] = "SOMETHING_NEW"
        code, out = run_pair(p, profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "FAIL")

    def test_non_string_reason_code_fails(self):
        p = mig_pair(); comp(p, "NEW", "LLY", "fcf_yoy")["reason_code"] = ["NONPOSITIVE_CURRENT"]
        code, out = run_pair(p, profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "FAIL")

    def test_addition_on_non_invalid_component_fails(self):
        p = mig_pair()
        for lab in ("BASE", "NEW"): comp(p, lab, "LLY", "fcf_yoy").update(status="OK", value=0.1)
        code, out = run_pair(p, profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "FAIL")

    def test_profile_not_bound_to_pair_fails(self):
        for k, v in (("new_github_run_id", "999"), ("base_github_sha", "c" * 40), ("evaluation_date", "2026-11-04")):
            with self.subTest(k=k):
                code, out = run_pair(mig_pair(), profile=mig_profile(**{k: v}))
                self.assertEqual(verdict(out, "D2"), "FAIL"); self.assertIn("not bound to this BASE/NEW pair", out)

    def test_no_profile_keeps_v23_d2(self):
        code, out = run_pair(mig_pair(), profile=None)
        self.assertEqual(verdict(out, "D2"), "FAIL"); self.assertIn("D2 ticker JSON: no data change (same evaluation date)", out)

    def test_profile_without_allowance_keeps_v23_d2(self):
        prof = mig_profile(); prof.pop("allowed_added_paths")
        code, out = run_pair(mig_pair(), profile=prof)
        self.assertEqual(verdict(out, "D2"), "FAIL"); self.assertNotIn("ALLOWED_ADDITION", out.split("D5")[0])

    def test_clean_pair_with_allowance_but_no_additions_fails(self):
        code, out = run_pair(mig_pair(add=False), profile=mig_profile())
        self.assertEqual(verdict(out, "D2"), "FAIL"); self.assertIn("expected addition(s) not found", out)

    def test_malformed_allowance_is_input_error(self):
        bad_paths = ["/stages/growth/growth/components/*/reason_code", "/stages/growth/growth/components/fcf_yoy",
                     "/stages/growth/growth/components/fcf_yoy/reason", "/stages/growth/growth/components/fcf_yoy/reason_code/x",
                     "/stages/derived_metrics/derived/derived_metrics/fcf/reason_code", "/stages/growth/growth/components/ebitda_yoy/reason_code"]
        for pa in bad_paths:
            with self.subTest(path=pa):
                prof = mig_profile(); prof["allowed_added_paths"]["paths"][0]["path"] = pa
                code, out = run_pair(mig_pair(), profile=prof)
                self.assertEqual(code, 3, out)
        mutations = [lambda a: a.pop("binding"), lambda a: a.update(value_set="ANY"), lambda a: a.update(paths=[]),
                     lambda a: a["paths"].append(dict(a["paths"][0])), lambda a: a.update(extra=1), lambda a: a.update(migration_id=""),
                     lambda a: a["binding"].update(new_github_sha="abc"), lambda a: a["paths"].__setitem__(0, {"ticker": "AAPL"}),
                     lambda a: a["paths"].__setitem__(0, {"ticker": "aapl", "path": "/stages/growth/growth/components/fcf_yoy/reason_code"})]
        for i, mut in enumerate(mutations):
            with self.subTest(mutation=i):
                prof = mig_profile(); mut(prof["allowed_added_paths"])
                code, out = run_pair(mig_pair(), profile=prof)
                self.assertEqual(code, 3, out)

    def test_allowance_values_are_the_registered_set(self):
        self.assertEqual(ck.ALLOWANCE_VALUE_SETS["GROWTH_REASON_CODES"], ck.GROWTH_REASON_CODES)
        self.assertEqual(len(ck.GROWTH_REASON_CODES), 7)

    def test_c3_accepts_only_the_allowed_coverage_shift(self):
        code, out = run_pair(mig_pair(), profile=mig_profile())
        self.assertEqual(verdict(out, "C3"), "PASS", out); self.assertIn("3 accepted allowance addition(s)", out)
        p = mig_pair(); comp(p, "NEW", "AAPL", "fcf_yoy")["status"] = "MISSING"          # extra coverage shift
        code, out = run_pair(p, profile=mig_profile())
        self.assertEqual(verdict(out, "C3"), "FAIL"); self.assertEqual(verdict(out, "D2"), "FAIL")

    def test_without_allowance_c3_still_fails_on_reason_code_shift(self):
        prof = mig_profile(); prof.pop("allowed_added_paths")
        code, out = run_pair(mig_pair(), profile=prof)
        self.assertEqual(verdict(out, "C3"), "FAIL")

    def test_empty_container_reason_code_fails_without_crash(self):
        for bad in ([], {}, None, 7, True):
            with self.subTest(value=bad):
                p = mig_pair(); comp(p, "NEW", "LLY", "fcf_yoy")["reason_code"] = bad
                code, out = run_pair(p, profile=mig_profile())
                self.assertEqual(verdict(out, "D2"), "FAIL", out); self.assertNotIn(" X0 ", out)
