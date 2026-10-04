    for key in sorted(RESULTS.keys()):
        print(f"{key}: {RESULTS[key]}")

    print(f"MISSING_VALUE_MARKERS_OBSERVED: {sorted(MISSING_VALUE_MARKERS_OBSERVED) or 'NONE_OBSERVED'}")
    print("SECRET_EXPOSURE: NONE (key never printed; all logged URLs redacted)")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## Step 10I FRED Multi-Vintage Validation (v2)\n\n")
                f.write("| Layer | Result |\n|---|---|\n")
                for key in sorted(RESULTS.keys()):
                    f.write(f"| {key} | {RESULTS[key]} |\n")
                f.write(f"\nMissing-value markers observed: `{sorted(MISSING_VALUE_MARKERS_OBSERVED) or 'NONE_OBSERVED'}`\n\n")
                f.write("Secret exposure: NONE\n")
        except OSError:
            pass

    # Non-zero exit only if NEITHER core multi-vintage layer (CPI, PAYEMS)
    # was even reachable -- a FAIL verdict that both layers DID reach FRED
    # but found no multi-vintage columns is still a valid, informative
    # result, not a script error, so it does not itself fail the job.
    core = [RESULTS.get("LAYER1_OUTPUT_TYPE2_CPI"), RESULTS.get("LAYER2_OUTPUT_TYPE2_PAYEMS")]
    if all(v is None for v in core):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
