import json
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

RAW_DIR = Path("data/raw/sec")
PROCESSED_DIR = Path("data/processed/fundamentals")

SCHEMA_VERSION = "sec_fundamentals_v0.2"


# ============================================================
# BASIC VALIDATION
# ============================================================

def validate_companyfacts(data: dict) -> None:
    required_keys = [
        "cik",
        "entityName",
        "facts",
    ]

    for key in required_keys:
        if key not in data:
            raise ValueError(f"Missing required key: {key}")

    if not isinstance(data["facts"], dict):
        raise ValueError("facts is not a dictionary")


# ============================================================
# SEC FACT HELPERS
# ============================================================

def get_all_concepts(data: dict) -> list:
    validate_companyfacts(data)

    concepts = []

    for namespace, namespace_data in data["facts"].items():
        if not isinstance(namespace_data, dict):
            continue

        for concept_name, concept_data in namespace_data.items():
            concepts.append({
                "namespace": namespace,
                "concept": concept_name,
                "data": concept_data,
            })

    return concepts


def get_observations(concept_data: dict) -> list:
    if not isinstance(concept_data, dict):
        return []

    units = concept_data.get("units", {})

    if not isinstance(units, dict):
        return []

    observations = []

    for unit, values in units.items():
        if not isinstance(values, list):
            continue

        for obs in values:
            if not isinstance(obs, dict):
                continue

            row = dict(obs)
            row["unit"] = unit
            observations.append(row)

    return observations


# ============================================================
# OBSERVATION CLASSIFICATION
# ============================================================

def is_instant_observation(obs: dict) -> bool:
    return "end" in obs and "start" not in obs


def is_duration_observation(obs: dict) -> bool:
    return "start" in obs and "end" in obs


def is_annual_observation(obs: dict) -> bool:
    if obs.get("form") != "10-K":
        return False

    if obs.get("fp") != "FY":
        return False

    return is_duration_observation(obs)


# ============================================================
# BASIC VALUE / DATE HELPERS
# ============================================================

def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _date_string(value):
    if value is None:
        return None

    if isinstance(value, str):
        return value[:10]

    return str(value)[:10]


def _is_valid_date_string(value) -> bool:
    # Used by the filing-recency-aware unit ambiguity policy
    # (Round AU-R3) to decide whether a "filed" date can actually be
    # trusted to prove an observation is older than another one.
    # A missing or malformed date must never be silently treated as
    # "oldest" or "newest" -- it must be treated as unprovable.
    date_str = _date_string(value)

    if not date_str or len(date_str) != 10:
        return False

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False

    return True


def _observation_sort_key(
    observation: dict,
    concept_priority: int = 0,
):
    return (
        _date_string(observation.get("end")) or "",
        _date_string(observation.get("filed")) or "",
        -concept_priority,
        observation.get("accn") or "",
    )


# ============================================================
# OBSERVATION SELECTION
# ============================================================

def sort_observations(observations: list) -> list:
    return sorted(
        observations,
        key=lambda x: (
            _date_string(x.get("filed")) or "",
            _date_string(x.get("end")) or "",
            _date_string(x.get("start")) or "",
            x.get("accn") or "",
        ),
        reverse=True,
    )


def sort_instant_observations(observations: list) -> list:
    return sorted(
        observations,
        key=lambda x: (
            _date_string(x.get("end")) or "",
            _date_string(x.get("filed")) or "",
            x.get("accn") or "",
        ),
        reverse=True,
    )


def _group_key_for_annual(obs: dict):
    # Economic reporting period for a duration/annual observation.
    # Deliberately uses the actual start/end dates (not fy/fp labels,
    # which are self-reported and do not always correspond 1:1 with
    # the real period -- see Round AS/AT) and deliberately ignores
    # filed/accn, so that a same-period restatement under a
    # different accession number is still grouped with the original.
    return (obs.get("start"), obs.get("end"))


def _group_key_for_instant(obs: dict):
    # Point-in-time reporting context for an instant observation.
    return (obs.get("end"),)


def _select_latest_with_ambiguity(
    observations: list,
    sort_fn,
    group_key_fn,
):
    """
    Select the latest observation from an already-classified
    (annual or instant) observation list, using the existing
    date-based sort_fn to determine "latest" exactly as before (this
    preserves the existing filed/AS_OF-based point-in-time selection
    rule unchanged).

    Before returning, check whether the reporting context the
    selected observation belongs to (same economic period: start+end
    for annual/duration observations, end alone for instant
    observations) contains more than one distinct unit of measure.
    Restated/resubmitted filings for the same period are included in
    this check (they share the same start/end even if filed/accn
    differ). If more than one unit competes for the same reporting
    context, the choice of unit is ambiguous: this function refuses
    to pick one arbitrarily (no date-based tie-break, no reliance on
    JSON dict/list order) and reports the ambiguity instead of
    silently returning a value for one of the candidate units.

    Returns (observation_or_None, ambiguity_or_None). ambiguity is a
    dict with "units" (sorted list of the competing unit strings)
    and "context" (the grouping key) when ambiguous; otherwise None.
    A different period using a different unit does NOT trigger this
    (grouping is strictly per reporting context) -- that is a
    separate, not-yet-validated cross-period comparability question
    left for a future round.

    Filing-recency-aware policy (Round AU-R3, following Round AU-R2's
    findings): within one reporting context, a unit used only by an
    OLDER, superseded filing must not by itself block a later filing
    that reports the very same period unambiguously in a single
    unit. Only the most-recently-FILED cohort within this context
    (same "filed" date; different accessions on that same date are
    one cohort) is checked for unit competition. A peer whose "filed"
    date is missing or not a valid date can never be proven older
    than that cohort, so if its unit isn't already part of the
    cohort, the result stays ambiguous rather than assuming it is
    stale.

    Returns a THIRD value (Round AU-R5): `peers`, the full list of
    this concept's own raw observations sharing `top`'s reporting
    context (start+end for annual, end for instant) -- regardless of
    whether this call resolved cleanly or was ambiguous. This is used
    by find_best_annual_concept()/find_best_instant_concept() to pool
    a concept's own current-period observations together with every
    OTHER concept candidate's own current-period observations before
    any within-concept-vs-cross-concept ambiguity decision is made,
    so that a concept's internal unit conflict can still be resolved
    by a more-recently-filed, single-unit observation reported under
    a DIFFERENT concept for the exact same period (Round AU-R5) --
    without this, filing recency and unit information from an
    internally-ambiguous concept would be discarded before
    cross-concept comparison ever saw it. The two public
    "_with_ambiguity" wrappers below discard this third value; only
    the find_best_* orchestration functions use it.
    """

    if not observations:
        return None, None, []

    sorted_obs = sort_fn(observations)
    top = sorted_obs[0]
    top_key = group_key_fn(top)

    peers = [
        obs
        for obs in observations
        if group_key_fn(obs) == top_key
    ]

    all_units = sorted({
        obs.get("unit")
        for obs in peers
    })

    if len(all_units) <= 1:
        # No unit diversity at all in this reporting context --
        # nothing to resolve, regardless of filing dates.
        return top, None, peers

    dated_peers = [
        obs for obs in peers
        if _is_valid_date_string(obs.get("filed"))
    ]
    undated_peers = [
        obs for obs in peers
        if not _is_valid_date_string(obs.get("filed"))
    ]

    if dated_peers:
        latest_filed = max(
            _date_string(obs.get("filed"))
            for obs in dated_peers
        )
        latest_cohort = [
            obs for obs in dated_peers
            if _date_string(obs.get("filed")) == latest_filed
        ]
    else:
        latest_cohort = []

    cohort_units = {
        obs.get("unit")
        for obs in latest_cohort
    }

    # A peer with no provable filing date whose unit is not already
    # covered by the latest cohort cannot be ruled out as a
    # competing, equally-current observation.
    unresolved_undated = [
        obs for obs in undated_peers
        if obs.get("unit") not in cohort_units
    ]

    if len(cohort_units) > 1 or unresolved_undated:
        return None, {
            "units": all_units,
            "context": top_key,
        }, peers

    # Exactly one unit in the latest-filed cohort, and no undated
    # peer competes with it -- the older, differently-unit'd
    # observation(s) are superseded and do not block selection. The
    # existing sort_fn already ensured `top` is the best-ranked
    # observation inside this (now-confirmed single-unit) cohort, so
    # existing accession/priority tie-break behavior is unchanged.
    return top, None, peers


def select_latest_annual_observation(concept_data: dict):
    observations = get_observations(concept_data)

    annual = [
        obs
        for obs in observations
        if is_annual_observation(obs)
    ]

    if not annual:
        return None

    observation, _ambiguity, _peers = _select_latest_with_ambiguity(
        annual,
        sort_observations,
        _group_key_for_annual,
    )

    return observation


def select_latest_annual_observation_with_ambiguity(
    concept_data: dict,
):
    """
    Same selection as select_latest_annual_observation(), but also
    surfaces unit-ambiguity diagnostics instead of silently
    discarding them when the latest reporting context contains more
    than one distinct unit. See _select_latest_with_ambiguity().

    Returns (observation_or_None, ambiguity_or_None). This public,
    single-concept-only wrapper deliberately discards
    _select_latest_with_ambiguity()'s third ("peers") return value --
    only the find_best_* orchestration functions below need it for
    cross-concept pooling (Round AU-R5). This function's own
    behavior and signature are unchanged from Round AU-R3/AU-R4.
    """

    observation, ambiguity, _peers = _annual_observation_peers(
        concept_data
    )

    return observation, ambiguity


def _annual_observation_peers(concept_data: dict):
    """
    Internal helper (Round AU-R5): identical selection to
    select_latest_annual_observation_with_ambiguity(), but also
    returns this concept's own raw "peers" list (every observation
    sharing this concept's own latest reporting context -- see
    _select_latest_with_ambiguity()) so that find_best_annual_concept()
    can pool it together with every OTHER concept candidate's own
    peers before deciding within-concept vs. cross-concept ambiguity.

    Returns (observation_or_None, ambiguity_or_None, peers_list).
    """

    observations = get_observations(concept_data)

    annual = [
        obs
        for obs in observations
        if is_annual_observation(obs)
    ]

    if not annual:
        return None, None, []

    return _select_latest_with_ambiguity(
        annual,
        sort_observations,
        _group_key_for_annual,
    )


def select_latest_instant_observation(concept_data: dict):
    observations = get_observations(concept_data)

    instant = [
        obs
        for obs in observations
        if is_instant_observation(obs)
    ]

    if not instant:
        return None

    observation, _ambiguity, _peers = _select_latest_with_ambiguity(
        instant,
        sort_instant_observations,
        _group_key_for_instant,
    )

    return observation


def select_latest_instant_observation_with_ambiguity(
    concept_data: dict,
):
    """
    Same selection as select_latest_instant_observation(), but also
    surfaces unit-ambiguity diagnostics instead of silently
    discarding them when the latest reporting context contains more
    than one distinct unit. See _select_latest_with_ambiguity().

    Returns (observation_or_None, ambiguity_or_None). See the
    docstring on select_latest_annual_observation_with_ambiguity()
    for why the third ("peers") value is discarded here.
    """

    observation, ambiguity, _peers = _instant_observation_peers(
        concept_data
    )

    return observation, ambiguity


def _instant_observation_peers(concept_data: dict):
    """
    Internal helper (Round AU-R5): instant-observation analog of
    _annual_observation_peers(). See that function's docstring.

    Returns (observation_or_None, ambiguity_or_None, peers_list).
    """

    observations = get_observations(concept_data)

    instant = [
        obs
        for obs in observations
        if is_instant_observation(obs)
    ]

    if not instant:
        return None, None, []

    return _select_latest_with_ambiguity(
        instant,
        sort_instant_observations,
        _group_key_for_instant,
    )


# ============================================================
# CONCEPT LOOKUP
# ============================================================

def find_concept(
    data: dict,
    namespace: str,
    concept_name: str,
):
    facts = data.get("facts", {})

    namespace_data = facts.get(namespace)

    if not isinstance(namespace_data, dict):
        return None

    return namespace_data.get(concept_name)


def _resolve_cross_concept_unit_conflicts(
    tagged_peers: list,
    ambiguous_candidates: list,
    group_key_fn,
):
    """
    Unified within-concept AND cross-concept unit conflict
    resolution, filing-recency-aware (Round AU-R3), extended in
    Round AU-R5 to arbitrate the two together instead of separately.

    `tagged_peers` is the FLAT POOL of every concept candidate's own
    raw current-period observations (each concept candidate
    contributes its own "peers" -- see _select_latest_with_ambiguity()
    -- regardless of whether that concept's own observations were, in
    isolation, internally ambiguous or not). This is deliberately
    NOT a list of already-resolved per-concept picks: Round AU-R5
    found that resolving each concept's internal ambiguity BEFORE
    comparing across concepts let a concept's own stale internal
    conflict permanently block a different, more-recently-filed,
    single-unit concept candidate for the exact same reporting
    period -- because the two were partitioned into disjoint
    "candidates" / "ambiguous_candidates" pools before this function
    ever saw them, and the final cross-period comparison only
    compares by reporting-period end date, which is identical for
    same-period entries and so could not break the tie correctly.

    Pooling every concept's raw peers (not just its already-reduced
    pick) and applying ONE latest-filed-cohort check per reporting
    context -- across however many different concepts contributed
    observations to that context -- means a given context now
    produces exactly one outcome (either a single resolved winner or
    one ambiguous group), so a context can never appear in both the
    resolved and the ambiguous pools at once. No filing-date or unit
    information is lost in the process: every concept's own peers
    (including an internally-conflicting concept's competing units)
    enter this same pool and this same cohort check.

    Within a context with only one distinct unit across ALL pooled
    entries, every entry is kept as a resolved candidate (ties among
    them -- e.g. several same-unit restatements from one concept, or
    agreeing values from two concepts -- are broken later by the
    existing filed/end/concept_priority/accn sort in
    find_best_annual_concept()/find_best_instant_concept(), unchanged
    from Round AU-R1/AU-R3). Within a context with more than one
    distinct unit, only the single most-recently-FILED cohort (same
    "filed" date string; different accessions on that date are one
    cohort) is examined: if that cohort itself has more than one
    unit, or if any peer whose filing date is missing/invalid reports
    a unit not already in that cohort (so it cannot be proven
    superseded), the context stays ambiguous -- otherwise the older,
    differently-unit'd peers (from any concept) are superseded and
    excluded.

    Mutates `ambiguous_candidates` in place (appends a new ambiguous
    group when found) and returns the resolved-candidates list --
    only genuinely-resolved entries (the latest-filed, single-unit
    cohort for each conflicting context, or every entry for a
    non-conflicting context) are kept.
    """

    context_groups = {}

    for item in tagged_peers:
        key = group_key_fn(item["observation"])
        context_groups.setdefault(key, []).append(item)

    resolved_candidates = []

    for key, group in context_groups.items():
        units_in_group = sorted({
            item["observation"].get("unit")
            for item in group
        })

        if len(units_in_group) <= 1:
            resolved_candidates.extend(group)
            continue

        dated_items = [
            item for item in group
            if _is_valid_date_string(
                item["observation"].get("filed")
            )
        ]
        undated_items = [
            item for item in group
            if not _is_valid_date_string(
                item["observation"].get("filed")
            )
        ]

        if dated_items:
            latest_filed = max(
                _date_string(item["observation"].get("filed"))
                for item in dated_items
            )
            latest_cohort = [
                item for item in dated_items
                if _date_string(
                    item["observation"].get("filed")
                ) == latest_filed
            ]
        else:
            # No peer in this context has a provable "filed" date at
            # all -- latest_filed is unknowable, not "oldest" or
            # "newest" (Round AU-R6). This is recorded as None below
            # rather than silently defaulting to "" or omitting the
            # key, so callers can tell "unprovable" apart from a
            # genuine empty-string date.
            latest_filed = None
            latest_cohort = []

        cohort_units = {
            item["observation"].get("unit")
            for item in latest_cohort
        }

        unresolved_undated = [
            item for item in undated_items
            if item["observation"].get("unit")
            not in cohort_units
        ]

        if len(cohort_units) > 1 or unresolved_undated:
            ambiguous_candidates.append({
                "namespace": group[0]["namespace"],
                "concept": group[0]["concept"],
                "concept_priority": min(
                    item["concept_priority"]
                    for item in group
                ),
                "context": key,
                "units": units_in_group,
                # Round AU-R6: the latest-filed-cohort's filing date
                # (or None when no peer in this context has a
                # provable "filed" date at all). This lets annual
                # arbitration compare an ambiguous context against a
                # resolved candidate using the SAME filed-first
                # ordering the resolved candidates themselves use,
                # instead of falling back to period-end-only
                # comparison in every case.
                "latest_filed": latest_filed,
            })
        else:
            # Only the latest-filed, single-unit cohort stands; the
            # older-unit entries for this same context are
            # superseded and excluded so they cannot win the final
            # concept-priority/accession tie-break below.
            resolved_candidates.extend(latest_cohort)

    return resolved_candidates


# ============================================================
# PRIORITY-BASED ANNUAL CONCEPT SELECTION
# ============================================================

def find_best_annual_concept(
    data: dict,
    concept_candidates: list,
    namespaces=("us-gaap",),
):
    # Round AU-R5: pool every concept candidate's own current-period
    # peer observations FIRST (each concept's own "latest reporting
    # context" is still chosen exactly as before, via
    # _annual_observation_peers() -> _select_latest_with_ambiguity(),
    # using the existing filed-date-primary sort_observations() rule
    # -- this per-concept step is unchanged). Only AFTER pooling do
    # we decide, per reporting context and across however many
    # concepts contributed to it, whether a single unit resolves it
    # or it stays ambiguous. See _resolve_cross_concept_unit_conflicts().
    tagged_peers = []

    for concept_priority, concept_name in enumerate(
        concept_candidates
    ):
        for namespace in namespaces:
            concept_data = find_concept(
                data,
                namespace,
                concept_name,
            )

            if concept_data is None:
                continue

            _observation, _ambiguity, peers = (
                _annual_observation_peers(concept_data)
            )

            for peer in peers:
                tagged_peers.append({
                    "namespace": namespace,
                    "concept": concept_name,
                    "observation": peer,
                    "concept_priority": concept_priority,
                })

    ambiguous_candidates = []

    candidates = _resolve_cross_concept_unit_conflicts(
        tagged_peers,
        ambiguous_candidates,
        _group_key_for_annual,
    )

    best_candidate = None

    if candidates:
        candidates.sort(
            key=lambda item: (
                _date_string(
                    item["observation"].get("filed")
                ) or "",
                _date_string(
                    item["observation"].get("end")
                ) or "",
                -item["concept_priority"],
                item["observation"].get("accn") or "",
            ),
            reverse=True,
        )
        best_candidate = candidates[0]

    # Round AU-R7: Round AU-R6 fixed the comparison between a SINGLE
    # pre-selected "best_ambiguous" context and the resolved
    # candidate, but the step that picked that single context in the
    # first place still sorted ALL ambiguous contexts by
    # reporting-period end date only (see the old
    # "ambiguous_candidates.sort(key=... _date_string(item["context"]
    # [-1]) ...)" this replaces). That end-first pre-selection could
    # discard a genuinely more-current (by filing date) ambiguous
    # context in favor of a merely newer-ending one, before the
    # AU-R6 filed-first comparison ever got a chance to consider it.
    #
    # Fix: split the ambiguous pool into two kinds up front --
    # "dated" (at least one peer in the latest-filed cohort has a
    # provable "filed" date, i.e. "latest_filed" is not None) and
    # "undated" (no peer does) -- and rank each kind using the SAME
    # axis consistently:
    #   - Dated ambiguous contexts are ranked against EACH OTHER, and
    #     against the resolved candidate, using the identical
    #     (filed, end, -priority) axis the resolved pool itself is
    #     ranked by. This is a direct extension of Round AU-R6's
    #     single-pair rule: a dated ambiguous context is exactly as
    #     comparable on the filing timeline as a resolved one.
    #   - Undated ambiguous contexts are ranked against each other by
    #     (end, -priority) only, since there is no filed date to
    #     compare -- consistent with Round AU-R6's conservative,
    #     fail-closed fallback (never invent a date).
    #
    # Mixed-provability resolution rule (Round AU-R7 Part D #4; this
    # specific pairing was not decided by any prior round and is
    # resolved here explicitly rather than silently): once the single
    # best "dated-comparable" entry is known (the resolved candidate
    # or the best dated-ambiguous context, whichever ranks higher on
    # the (filed, end, -priority) axis), the best UNDATED ambiguous
    # context is compared against THAT entry using end-date only --
    # i.e. Round AU-R6's existing "undated ambiguous vs resolved"
    # fallback is generalized to "undated ambiguous vs whichever
    # dated-comparable entry currently leads," rather than inventing
    # a new rule for "undated ambiguous vs dated ambiguous"
    # specifically. This is the narrowest extension of the only
    # already-tested undated-comparison rule: it never asks what an
    # undated context's filing date might be, on either side of the
    # comparison, and it never lets a dated entry's filed date alone
    # (ignoring end) decide against an undated context -- exactly as
    # before.
    dated_ambiguous = [
        item for item in ambiguous_candidates
        if item["latest_filed"] is not None
    ]
    undated_ambiguous = [
        item for item in ambiguous_candidates
        if item["latest_filed"] is None
    ]

    best_dated_ambiguous = None

    if dated_ambiguous:
        dated_ambiguous.sort(
            key=lambda item: (
                item["latest_filed"],
                _date_string(item["context"][-1]) or "",
                -item["concept_priority"],
            ),
            reverse=True,
        )
        best_dated_ambiguous = dated_ambiguous[0]

    best_undated_ambiguous = None

    if undated_ambiguous:
        undated_ambiguous.sort(
            key=lambda item: (
                _date_string(item["context"][-1]) or "",
                -item["concept_priority"],
            ),
            reverse=True,
        )
        best_undated_ambiguous = undated_ambiguous[0]

    # Step 1: decide the leading "dated-comparable" entry -- the
    # resolved candidate and the best dated-ambiguous context both
    # live on the same (filed, end, -priority) axis. On an exact tie,
    # the ambiguous context wins (preserves Round AU-R6's ">="
    # tie-break direction).
    leading = None

    if best_candidate is not None:
        leading = {
            "kind": "resolved",
            "value": best_candidate,
            "key": (
                _date_string(
                    best_candidate["observation"].get("filed")
                ) or "",
                _date_string(
                    best_candidate["observation"].get("end")
                ) or "",
                -best_candidate["concept_priority"],
            ),
        }

    if best_dated_ambiguous is not None:
        dated_key = (
            best_dated_ambiguous["latest_filed"],
            _date_string(
                best_dated_ambiguous["context"][-1]
            ) or "",
            -best_dated_ambiguous["concept_priority"],
        )
        if leading is None or dated_key >= leading["key"]:
            leading = {
                "kind": "ambiguous",
                "value": best_dated_ambiguous,
                "key": dated_key,
            }

    # Step 2: an undated ambiguous context cannot be placed on the
    # filing timeline, so it is compared only by its directly
    # observed reporting-period end date against whichever entry
    # currently leads -- never against that entry's filed date.
    winner = leading

    if best_undated_ambiguous is not None:
        undated_end = (
            _date_string(best_undated_ambiguous["context"][-1]) or ""
        )

        if winner is None:
            winner = {"kind": "ambiguous", "value": best_undated_ambiguous}
        else:
            if winner["kind"] == "resolved":
                leading_end = (
                    _date_string(
                        winner["value"]["observation"].get("end")
                    ) or ""
                )
            else:
                leading_end = (
                    _date_string(
                        winner["value"]["context"][-1]
                    ) or ""
                )

            if undated_end >= leading_end:
                winner = {
                    "kind": "ambiguous",
                    "value": best_undated_ambiguous,
                }

    if winner is None:
        return None

    if winner["kind"] == "ambiguous":
        amb = winner["value"]
        return {
            "ambiguous": True,
            "namespace": amb["namespace"],
            "concept": amb["concept"],
            "units": amb["units"],
        }

    return winner["value"]


# ============================================================
# PRIORITY-BASED INSTANT CONCEPT SELECTION
# ============================================================

def find_best_instant_concept(
    data: dict,
    concept_candidates: list,
    namespaces=("us-gaap", "dei"),
):
    """
    Select the best instant observation across all
    candidate concepts and namespaces.

    Selection priority:
        1. Latest economic period end
        2. Latest filing date
        3. Candidate concept priority
        4. Accession number

    All candidate concepts are evaluated before selection.
    """

    # Round AU-R5: pool every concept candidate's own current-point-
    # in-time peer observations first, exactly as
    # find_best_annual_concept() now does -- see that function's
    # comment and _resolve_cross_concept_unit_conflicts() for why.
    # Instant context is grouped by point-in-time ("end") rather than
    # start+end; each concept's own context is still chosen via the
    # existing sort_instant_observations() rule, unchanged.
    tagged_peers = []

    for concept_priority, concept_name in enumerate(
        concept_candidates
    ):
        for namespace in namespaces:
            concept_data = find_concept(
                data,
                namespace,
                concept_name,
            )

            if concept_data is None:
                continue

            _observation, _ambiguity, peers = (
                _instant_observation_peers(concept_data)
            )

            for peer in peers:
                tagged_peers.append({
                    "namespace": namespace,
                    "concept": concept_name,
                    "observation": peer,
                    "concept_priority": concept_priority,
                })

    ambiguous_candidates = []

    candidates = _resolve_cross_concept_unit_conflicts(
        tagged_peers,
        ambiguous_candidates,
        _group_key_for_instant,
    )

    best_candidate = None

    if candidates:
        candidates.sort(
            key=lambda item: (
                _date_string(
                    item["observation"].get("end")
                ) or "",
                _date_string(
                    item["observation"].get("filed")
                ) or "",
                -item["concept_priority"],
                item["observation"].get("accn") or "",
            ),
            reverse=True,
        )
        best_candidate = candidates[0]

    best_ambiguous = None

    if ambiguous_candidates:
        ambiguous_candidates.sort(
            key=lambda item: (
                _date_string(item["context"][-1]) or "",
                -item["concept_priority"],
            ),
            reverse=True,
        )
        best_ambiguous = ambiguous_candidates[0]

    if best_ambiguous is not None:
        resolved_end = (
            _date_string(
                best_candidate["observation"].get("end")
            ) or ""
        ) if best_candidate else ""

        ambiguous_end = (
            _date_string(best_ambiguous["context"][-1]) or ""
        )

        if ambiguous_end >= resolved_end:
            return {
                "ambiguous": True,
                "namespace": best_ambiguous["namespace"],
                "concept": best_ambiguous["concept"],
                "units": best_ambiguous["units"],
            }

    return best_candidate


# ============================================================
# METRIC SEMANTICS
# ============================================================

def get_metric_semantics(
    metric_name: str,
    concept: str,
):
    """
    Return semantic metadata for metrics whose accounting
    meaning can be distinguished from the selected SEC concept.
    """

    # STEP 15 (2026-10-27): concepts added as lower-priority candidates
    # whose scope is broader than the metric's primary concept. They are
    # labelled so consumers can tell them apart; the scope difference is
    # recorded, not corrected.
    if metric_name == "capex" and concept == "PaymentsToAcquireProductiveAssets":
        return {
            "concept_scope": "PRODUCTIVE_ASSETS",
            "semantic_note": (
                "Payments to acquire productive assets; may include "
                "intangible assets or software in addition to PP&E."
            ),
        }

    if (
        metric_name == "noncurrent_debt"
        and concept == "LongTermDebtAndCapitalLeaseObligations"
    ):
        return {
            "concept_scope": "DEBT_AND_LEASE_OBLIGATIONS",
            "semantic_note": (
                "Noncurrent long-term debt including lease "
                "obligations."
            ),
        }

    if metric_name == "current_debt" and concept == "NotesPayableCurrent":
        return {
            "concept_scope": "CURRENT_PORTION_OF_LONG_TERM_NOTES",
            "scope_coverage": "PARTIAL",
            "semantic_note": (
                "Current portion of long-term notes payable only; not a "
                "complete measure of current debt."
            ),
        }

    if metric_name != "stockholders_equity":
        return {}

    if concept == "StockholdersEquity":
        return {
            "equity_scope": "STOCKHOLDERS_EQUITY",
            "includes_noncontrolling_interest": False,
            "semantic_status": "CLASSIFIED",
        }

    if (
        concept
        == "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
    ):
        return {
            "equity_scope": "TOTAL_EQUITY",
            "includes_noncontrolling_interest": True,
            "semantic_status": "CLASSIFIED",
        }

    return {
        "equity_scope": "UNKNOWN",
        "includes_noncontrolling_interest": None,
        "semantic_status": "UNCLASSIFIED",
    }


# ============================================================
# RESULT BUILDERS
# ============================================================

def build_missing_metric(
    metric_name: str,
    reason: str = None,
):
    result = {
        "metric": metric_name,
        "status": "MISSING",
        "value": None,
    }

    if reason:
        result["reason"] = reason

    return result


def build_not_applicable_metric(
    metric_name: str,
    reason: str,
):
    return {
        "metric": metric_name,
        "status": "NOT_APPLICABLE",
        "value": None,
        "reason": reason,
    }


def build_incomplete_metric(
    metric_name: str,
    reason: str,
    units_observed: list = None,
):
    """
    Status for a metric whose source value(s) could not be safely
    resolved even though raw observations exist -- currently used
    for unit-selection ambiguity (Round AU). Reuses the existing
    "INCOMPLETE" status already used by calculate_total_debt(),
    rather than introducing a new status value, per the Round AU
    design constraint. Deliberately distinct from "MISSING" (no
    source value exists at all): here a value exists but selecting
    one unit over another without an approved tie-break rule would
    be an unjustified guess.
    """

    result = {
        "metric": metric_name,
        "status": "INCOMPLETE",
        "value": None,
        "reason": reason,
    }

    if units_observed is not None:
        result["units_observed"] = units_observed

    return result


def build_metric_result(
    metric_name: str,
    namespace: str,
    concept: str,
    observation: dict,
):
    result = {
        "metric": metric_name,
        "status": "OK",
        "namespace": namespace,
        "concept": concept,
        "unit": observation.get("unit"),
        "value": observation.get("val"),
        "period_start": observation.get("start"),
        "period_end": observation.get("end"),
        "filing_date": observation.get("filed"),
        "form": observation.get("form"),
        "fy": observation.get("fy"),
        "fp": observation.get("fp"),
        "frame": observation.get("frame"),
        "accession": observation.get("accn"),
    }

    result.update(
        get_metric_semantics(
            metric_name,
            concept,
        )
    )

    return result


def build_instant_result(
    metric_name: str,
    namespace: str,
    concept: str,
    observation: dict,
):
    result = {
        "metric": metric_name,
        "status": "OK",
        "namespace": namespace,
        "concept": concept,
        "unit": observation.get("unit"),
        "value": observation.get("val"),
        "period_end": observation.get("end"),
        "filing_date": observation.get("filed"),
        "form": observation.get("form"),
        "fy": observation.get("fy"),
        "fp": observation.get("fp"),
        "frame": observation.get("frame"),
        "accession": observation.get("accn"),
    }

    result.update(
        get_metric_semantics(
            metric_name,
            concept,
        )
    )

    return result


# ============================================================
# METRIC EXTRACTION
# ============================================================

def extract_metric(
    data: dict,
    metric_name: str,
    concept_candidates: list,
    namespaces=("us-gaap",),
):
    result = find_best_annual_concept(
        data,
        concept_candidates,
        namespaces,
    )

    if result is None:
        return build_missing_metric(metric_name)

    if result.get("ambiguous"):
        return build_incomplete_metric(
            metric_name,
            (
                "Multiple units of measure ("
                + ", ".join(
                    str(u) for u in result.get("units") or []
                )
                + f") were reported for {metric_name}'s most "
                "recent annual reporting period and no approved "
                "reporting-currency anchor resolves the ambiguity; "
                "refusing to select a unit by date or dict order."
            ),
            result.get("units"),
        )

    return build_metric_result(
        metric_name,
        result["namespace"],
        result["concept"],
        result["observation"],
    )


def extract_instant_metric(
    data: dict,
    metric_name: str,
    concept_candidates: list,
    namespaces=("us-gaap", "dei"),
):
    result = find_best_instant_concept(
        data,
        concept_candidates,
        namespaces,
    )

    if result is None:
        return build_missing_metric(metric_name)

    if result.get("ambiguous"):
        return build_incomplete_metric(
            metric_name,
            (
                "Multiple units of measure ("
                + ", ".join(
                    str(u) for u in result.get("units") or []
                )
                + f") were reported for {metric_name}'s most "
                "recent point-in-time reporting context and no "
                "approved reporting-currency anchor resolves the "
                "ambiguity; refusing to select a unit by date or "
                "dict order."
            ),
            result.get("units"),
        )

    return build_instant_result(
        metric_name,
        result["namespace"],
        result["concept"],
        result["observation"],
    )


# ============================================================
# TOTAL DEBT
# ============================================================

def calculate_total_debt(
    current_debt: dict,
    noncurrent_debt: dict,
    company_type: str,
):
    if company_type == "FINANCIAL":
        return {
            "metric": "total_debt",
            "status": "NOT_APPLICABLE",
            "value": None,
            "reason": (
                "Generic EV debt calculation is not "
                "appropriate for financial companies."
            ),
            "components": {
                "current_debt": current_debt,
                "noncurrent_debt": noncurrent_debt,
            },
        }

    result = {
        "metric": "total_debt",
        "status": "INCOMPLETE",
        "value": None,
        "components": {
            "current_debt": current_debt,
            "noncurrent_debt": noncurrent_debt,
        },
    }

    current_value = current_debt.get("value")
    noncurrent_value = noncurrent_debt.get("value")

    if (
        current_debt.get("status") == "OK"
        and noncurrent_debt.get("status") == "OK"
        and _is_number(current_value)
        and _is_number(noncurrent_value)
    ):
        current_unit = current_debt.get("unit")
        noncurrent_unit = noncurrent_debt.get("unit")

        if not current_unit or not noncurrent_unit:
            # Values exist but at least one input is missing unit
            # metadata -- do not assume compatibility (Round AU
            # design constraint A-4: absent unit metadata is never
            # treated as "compatible by default").
            result["reason"] = (
                "Unit metadata is missing for current_debt and/or "
                "noncurrent_debt; refusing to sum without a known "
                "unit of measure for both inputs."
            )
        elif current_unit != noncurrent_unit:
            result["reason"] = (
                f"current_debt unit ({current_unit!r}) does not "
                f"match noncurrent_debt unit ({noncurrent_unit!r}); "
                "refusing to sum values reported in different "
                "units without an approved FX conversion policy."
            )
        else:
            # STEP 15: scope checks. Only applied when the components
            # carry scope labels (normalize_company() adds them), so
            # callers passing unlabelled components are unaffected.
            current_lease = current_debt.get("lease_scope")
            noncurrent_lease = noncurrent_debt.get("lease_scope")
            partial = [
                name
                for name, component in (
                    ("current_debt", current_debt),
                    ("noncurrent_debt", noncurrent_debt),
                )
                if component.get("scope_coverage") == "PARTIAL"
            ]
            if current_lease and noncurrent_lease and current_lease != noncurrent_lease:
                result["reason_code"] = "LEASE_SCOPE_MISMATCH"
                result["reason"] = (
                    f"current_debt ({current_debt.get('concept')}) is "
                    f"{current_lease} but noncurrent_debt "
                    f"({noncurrent_debt.get('concept')}) is "
                    f"{noncurrent_lease}; refusing to report their sum as a "
                    "complete total debt."
                )
                result["unqualified_sum"] = current_value + noncurrent_value
            elif current_debt.get("uncovered_components"):
                result["reason_code"] = "CURRENT_COMPONENT_NOT_COVERED"
                result["reason"] = (
                    f"current_debt ({current_debt.get('concept')}) does not "
                    "cover same-date facts "
                    + ", ".join(
                        c.get("concept", "?")
                        for c in current_debt.get("uncovered_components")
                    )
                    + "; total debt completeness is not verified."
                )
                result["unqualified_sum"] = current_value + noncurrent_value
            elif partial:
                result["reason_code"] = "PARTIAL_COMPONENT_SCOPE"
                result["reason"] = (
                    "Component(s) " + ", ".join(partial) + " cover only part "
                    "of the debt they stand in for; refusing to report the "
                    "sum as a complete total debt."
                )
                result["unqualified_sum"] = current_value + noncurrent_value
            else:
                result["value"] = current_value + noncurrent_value
                result["status"] = "OK"
                result["unit"] = current_unit
                if current_lease and current_lease == noncurrent_lease:
                    result["lease_scope"] = current_lease

    return result


# ============================================================
# FRESHNESS CHECK (STEP 15, 2026-10-27)
#
# Candidate selection picks the latest observation of each candidate
# concept. When an issuer stopped using a concept years ago, that
# concept's last value (e.g. FY2012) was returned as "OK" even though
# it is not the issuer's current period. This check compares the
# selected period_end with the issuer's own latest reporting date in
# the same (already PIT-gated) companyfacts document:
#
#   - annual (duration) metrics: latest 10-K FY period end across all
#     us-gaap concepts;
#   - instant (balance-sheet) metrics: latest instant period end of
#     us-gaap:Assets.
#
# A selected value older than its anchor by more than
# FRESHNESS_TOLERANCE_DAYS is downgraded to the existing INCOMPLETE
# status with freshness="STALE" and value=None; the stale observation
# is kept under "stale_observation" for diagnostics. The check is
# one-sided (later dates, e.g. dei cover-page share counts, pass).
# Freshness says nothing about a concept's accounting meaning; that is
# a separate question.
# ============================================================

FRESHNESS_TOLERANCE_DAYS = 45

_FRESHNESS_ANNUAL = "annual"
_FRESHNESS_INSTANT = "instant"


def _parse_iso_date(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def annual_freshness_anchor(data: dict):
    """Latest 10-K FY period end across all us-gaap concepts."""
    best = None
    for concept_data in (data.get("facts", {}).get("us-gaap") or {}).values():
        for obs in get_observations(concept_data):
            if not is_annual_observation(obs):
                continue
            end = _parse_iso_date(obs.get("end"))
            if end is not None and (best is None or end > best):
                best = end
    return best


def instant_freshness_anchor(data: dict):
    """Latest instant period end of us-gaap:Assets (the balance-sheet
    date), or None when Assets is not reported."""
    concept_data = (data.get("facts", {}).get("us-gaap") or {}).get("Assets")
    if concept_data is None:
        return None
    best = None
    for obs in get_observations(concept_data):
        if not is_instant_observation(obs):
            continue
        end = _parse_iso_date(obs.get("end"))
        if end is not None and (best is None or end > best):
            best = end
    return best


_DIAGNOSTIC_KEYS = (
    "namespace",
    "concept",
    "value",
    "unit",
    "period_start",
    "period_end",
    "filing_date",
    "form",
    "accession",
)


def _unverified_freshness(result: dict, freshness: str, why: str, anchor) -> dict:
    metric_name = result.get("metric")
    out = build_incomplete_metric(
        metric_name,
        (
            f"Freshness of the selected {metric_name} observation "
            f"({result.get('concept')}) cannot be verified: {why}. "
            "Refusing to return an unverified value as current."
        ),
    )
    out["freshness"] = freshness
    out["reason_code"] = "FRESHNESS_UNVERIFIABLE"
    if anchor is not None:
        out["freshness_anchor"] = anchor.isoformat()
    out["unverified_observation"] = {
        key: result.get(key) for key in _DIAGNOSTIC_KEYS if key in result
    }
    return out


def apply_freshness_check(result: dict, anchor, kind: str) -> dict:
    """Return `result` annotated with freshness, or an INCOMPLETE/STALE
    replacement when its period_end predates `anchor` by more than
    FRESHNESS_TOLERANCE_DAYS. Non-OK results are returned unchanged."""
    if not isinstance(result, dict) or result.get("status") != "OK":
        return result

    # Freshness that cannot be verified fails closed: the value is not
    # returned as OK, so consumers that only check status/value (e.g.
    # derived_metrics) never treat it as usable.
    if anchor is None:
        return _unverified_freshness(
            result,
            "UNVERIFIED_NO_ANCHOR",
            "the issuer's reference date could not be determined "
            "(no 10-K FY observation / no us-gaap:Assets instant)",
            None,
        )

    period_end = _parse_iso_date(result.get("period_end"))
    if period_end is None:
        return _unverified_freshness(
            result,
            "UNVERIFIED_NO_PERIOD_END",
            "the selected observation has no parseable period end",
            anchor,
        )

    lag_days = (anchor - period_end).days
    if lag_days <= FRESHNESS_TOLERANCE_DAYS:
        annotated = dict(result)
        annotated["freshness"] = "CURRENT"
        annotated["freshness_anchor"] = anchor.isoformat()
        return annotated

    metric_name = result.get("metric")
    anchor_label = (
        "latest 10-K fiscal-year end"
        if kind == _FRESHNESS_ANNUAL
        else "latest balance-sheet date (us-gaap:Assets)"
    )
    stale = build_incomplete_metric(
        metric_name,
        (
            f"Selected {metric_name} observation "
            f"({result.get('concept')}, period ending "
            f"{period_end.isoformat()}) is {lag_days} days older than "
            f"the issuer's {anchor_label} {anchor.isoformat()}; no "
            "candidate concept reports a current-period value. "
            "Refusing to return a stale value as current."
        ),
    )
    stale["freshness"] = "STALE"
    stale["reason_code"] = "STALE_PERIOD"
    stale["freshness_anchor"] = anchor.isoformat()
    stale["stale_observation"] = {
        key: result.get(key) for key in _DIAGNOSTIC_KEYS if key in result
    }
    return stale


# ============================================================
# ANNUAL DURATION CHECK (STEP 15, 2026-10-27)
#
# is_annual_observation() accepts any 10-K / fp=FY duration fact, so a
# 3-month Q4 value tagged in a 10-K is also "annual", and the shared
# selection prefers it over the full-year value that ends on the same
# date. For normalize_company()'s annual metrics only, 10-K/FY duration
# facts are therefore filtered by length before selection.
#
# Day-count rule: duration_days = (end - start) in calendar days, using
# the SEC fact's inclusive start and end dates as given. A calendar
# year (Jan 1 - Dec 31) is 364 (365 in a leap year); a 52-week fiscal
# year is 363 and a 53-week year 370. Accepted range: 350..380
# inclusive. Shared selection helpers are not changed.
# ============================================================

ANNUAL_MIN_DAYS = 350
ANNUAL_MAX_DAYS = 380


def annual_duration_days(obs: dict):
    """(end - start) in days, or None when either date is unparseable."""
    start = _parse_iso_date(obs.get("start"))
    end = _parse_iso_date(obs.get("end"))
    if start is None or end is None:
        return None
    return (end - start).days


def _is_full_year(obs: dict) -> bool:
    days = annual_duration_days(obs)
    return days is not None and ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS


def annual_duration_view(data: dict) -> dict:
    """Copy of `data` in which 10-K / fp=FY duration facts whose length
    is outside ANNUAL_MIN_DAYS..ANNUAL_MAX_DAYS (or unparseable) are
    removed. All other facts (instants, 10-Q facts) are kept as-is."""
    facts = data.get("facts") or {}
    view_facts = {}
    for namespace, concepts in facts.items():
        if not isinstance(concepts, dict):
            view_facts[namespace] = concepts
            continue
        view_concepts = {}
        for concept_name, concept_data in concepts.items():
            units = concept_data.get("units") if isinstance(concept_data, dict) else None
            if not isinstance(units, dict):
                view_concepts[concept_name] = concept_data
                continue
            view_units = {
                unit: [
                    obs
                    for obs in (observations or [])
                    if not (isinstance(obs, dict) and is_annual_observation(obs))
                    or _is_full_year(obs)
                ]
                for unit, observations in units.items()
            }
            view_concept = dict(concept_data)
            view_concept["units"] = view_units
            view_concepts[concept_name] = view_concept
        view_facts[namespace] = view_concepts
    view = dict(data)
    view["facts"] = view_facts
    return view


def extract_full_year_metric(
    data: dict,
    annual_view: dict,
    metric_name: str,
    concept_candidates: list,
):
    """extract_metric() on the full-year view. If nothing qualifies but
    the unfiltered data would have produced a value, return INCOMPLETE
    with the out-of-range observation kept for diagnostics, so a
    non-annual value is never reported as annual and the absence is not
    mistaken for MISSING."""
    result = extract_metric(annual_view, metric_name, concept_candidates)
    if result.get("status") != "MISSING":
        return result

    unfiltered = extract_metric(data, metric_name, concept_candidates)
    if unfiltered.get("status") != "OK":
        return result

    probe = {"start": unfiltered.get("period_start"), "end": unfiltered.get("period_end")}
    days = annual_duration_days(probe)
    if days is None:
        code = "ANNUAL_DURATION_UNVERIFIABLE"
        why = "its start/end dates cannot be parsed"
    else:
        code = "ANNUAL_DURATION_OUT_OF_RANGE"
        why = (
            f"it spans {days} days, outside the accepted "
            f"{ANNUAL_MIN_DAYS}-{ANNUAL_MAX_DAYS} day annual range"
        )
    out = build_incomplete_metric(
        metric_name,
        (
            f"The only 10-K/FY {metric_name} observation found "
            f"({unfiltered.get('concept')}, {unfiltered.get('period_start')} "
            f"to {unfiltered.get('period_end')}) is not a full-year value: "
            f"{why}."
        ),
    )
    out["reason_code"] = code
    out["duration_days"] = days
    out["rejected_observation"] = {
        key: unfiltered.get(key) for key in _DIAGNOSTIC_KEYS if key in unfiltered
    }
    return out


# ============================================================
# DEBT SCOPE (STEP 15, 2026-10-27)
#
# Capital/finance lease scope per concept, from the US-GAAP element
# definitions carried in companyfacts (operating lease liabilities are a
# different concept and are never part of these debt components):
#   DebtCurrent: "short-term debt and current maturity of long-term debt
#     and capital lease obligations" -> CAPITAL_FINANCE_LEASE_INCLUDED
#   LongTermDebtCurrent / LongTermDebtNoncurrent: "Excludes capital lease
#     obligations" -> CAPITAL_FINANCE_LEASE_EXCLUDED
#   LongTermDebtAndCapitalLeaseObligations: "long-term debt and lease
#     obligation, classified as noncurrent" -> CAPITAL_FINANCE_LEASE_INCLUDED
#   ShortTermBorrowings / ShortTermDebt / notes payable: borrowing concepts
#     whose definitions do not include lease obligations
#     -> CAPITAL_FINANCE_LEASE_EXCLUDED (basis: BORROWING_CONCEPT)
#
# Rules (no tolerance, no inference from equal values):
#   1. If the default selection pairs components of different lease scope,
#      look for a pair of the SAME scope reported at the same date and unit
#      (CAPITAL_FINANCE_LEASE_INCLUDED first, then EXCLUDED). If found, use
#      it and record the replacement; otherwise calculate_total_debt()
#      reports LEASE_SCOPE_MISMATCH.
#   2. A component that covers only part of the debt it stands in for
#      (NotesPayableCurrent) blocks a complete total (PARTIAL).
#   3. If a same-date fact shows a current-debt component that the selected
#      concept does not cover (e.g. CommercialPaper alongside
#      LongTermDebtCurrent), total debt is not reported as complete.
# ============================================================

FINANCE_LEASE_INCLUDED = "CAPITAL_FINANCE_LEASE_INCLUDED"
FINANCE_LEASE_EXCLUDED = "CAPITAL_FINANCE_LEASE_EXCLUDED"

_DEBT_LEASE_SCOPE = {
    "DebtCurrent": (FINANCE_LEASE_INCLUDED, "ELEMENT_DEFINITION"),
    "LongTermDebtCurrent": (FINANCE_LEASE_EXCLUDED, "ELEMENT_DEFINITION"),
    "ShortTermBorrowings": (FINANCE_LEASE_EXCLUDED, "BORROWING_CONCEPT"),
    "ShortTermDebt": (FINANCE_LEASE_EXCLUDED, "BORROWING_CONCEPT"),
    "NotesPayableCurrent": (FINANCE_LEASE_EXCLUDED, "BORROWING_CONCEPT"),
    "LongTermDebtNoncurrent": (FINANCE_LEASE_EXCLUDED, "ELEMENT_DEFINITION"),
    "LongTermNotesPayable": (FINANCE_LEASE_EXCLUDED, "BORROWING_CONCEPT"),
    "LongTermDebtAndCapitalLeaseObligations": (FINANCE_LEASE_INCLUDED, "ELEMENT_DEFINITION"),
}

# Same-date facts that the selected current-debt concept does not cover.
_NOT_COVERED_BY = {
    "LongTermDebtCurrent": ("CommercialPaper", "ShortTermBorrowings", "ShortTermDebt"),
    "ShortTermBorrowings": ("LongTermDebtCurrent",),
    "ShortTermDebt": ("LongTermDebtCurrent",),
}

CURRENT_DEBT_CANDIDATES = [
    # STEP 15 (2026-10-27, B partially approved): DebtCurrent is the
    # aggregate current-debt concept and takes priority over its
    # components. In the 18-issuer comparison over five past PIT dates it
    # changed 5 of 90 values (GE x3, AVGO x2), each matching the issuer's
    # reported totals or component sums. Only one concept is selected, so
    # ShortTermBorrowings is never added on top.
    "DebtCurrent",
    "LongTermDebtCurrent",
    "ShortTermBorrowings",
    "ShortTermDebt",
    "CurrentDebt",
    # Lowest priority; partial scope (labelled), never treated as
    # complete current debt (ORCL).
    "NotesPayableCurrent",
]

NONCURRENT_DEBT_CANDIDATES = [
    "LongTermDebtNoncurrent",
    # Fallback migrated from src/fundamentals/sec_historical.py's
    # CONCEPT_MAP (2026-09-28, Framework v2.1 Architecture v5,
    # implementation step 1 -- "ORCL fallback migration").
    # Confirmed against real SEC data (2026-09-18 investigation):
    # Oracle (CIK 0001341439) has zero observations under
    # LongTermDebtNoncurrent (404 from data.sec.gov) and instead
    # tags its noncurrent debt under LongTermNotesPayable (86 real
    # observations spanning 2009-2026, e.g. $122.3B as of
    # FY2026-05-31, filed 2026-06-22, form 10-K). Before this
    # migration, this candidate list -- the one actually used by
    # the production path (point_in_time.py -> sec_normalizer.
    # find_concept / normalize_company) -- did not have this
    # fallback; only the non-production sec_historical.py did.
    "LongTermNotesPayable",
    # STEP 15 (2026-10-27): noncurrent debt tag at the latest
    # balance-sheet date for GE and MU. Cross-checked: with
    # DebtCurrent it sums to each issuer's reported combined
    # total (GE DebtLongtermAndShorttermCombinedAmount, MU
    # DebtAndCapitalLeaseObligations). Includes capital/finance
    # lease obligations, labelled by get_metric_semantics().
    "LongTermDebtAndCapitalLeaseObligations",
]


def _instant_observation_at(data: dict, concept: str, end, unit):
    """Latest-filed instant observation of `concept` at `end` in `unit`."""
    concept_data = (data.get("facts", {}).get("us-gaap") or {}).get(concept)
    if not isinstance(concept_data, dict):
        return None
    matches = [
        obs
        for obs in get_observations(concept_data)
        if is_instant_observation(obs)
        and obs.get("end") == end
        and obs.get("unit") == unit
        and _is_number(obs.get("val"))
    ]
    if not matches:
        return None
    return max(matches, key=lambda o: (_date_string(o.get("filed")) or "", o.get("accn") or ""))


def annotate_debt_scope(metric: dict, data: dict = None) -> dict:
    """Add lease_scope (and its basis) to an OK debt component."""
    if not isinstance(metric, dict) or metric.get("status") != "OK":
        return metric
    entry = _DEBT_LEASE_SCOPE.get(metric.get("concept"))
    if entry is None:
        return metric
    annotated = dict(metric)
    annotated["lease_scope"], annotated["lease_scope_basis"] = entry
    return annotated


def _scope_of(metric):
    return metric.get("lease_scope") if isinstance(metric, dict) else None


def align_debt_scope(current_debt: dict, noncurrent_debt: dict, data: dict):
    """Rule 1: replace a mixed-scope pair with a same-date, same-unit pair
    of matching lease scope, if the filing reports one. Returns the
    (possibly replaced) pair."""
    if not (
        isinstance(current_debt, dict)
        and isinstance(noncurrent_debt, dict)
        and current_debt.get("status") == "OK"
        and noncurrent_debt.get("status") == "OK"
    ):
        return current_debt, noncurrent_debt
    cur_scope, non_scope = _scope_of(current_debt), _scope_of(noncurrent_debt)
    if not cur_scope or not non_scope or cur_scope == non_scope:
        return current_debt, noncurrent_debt
    end = current_debt.get("period_end")
    unit = current_debt.get("unit")
    if end != noncurrent_debt.get("period_end") or unit != noncurrent_debt.get("unit"):
        return current_debt, noncurrent_debt

    for scope in (FINANCE_LEASE_INCLUDED, FINANCE_LEASE_EXCLUDED):
        picked = {}
        for role, candidates in (
            ("current_debt", CURRENT_DEBT_CANDIDATES),
            ("noncurrent_debt", NONCURRENT_DEBT_CANDIDATES),
        ):
            for concept in candidates:
                entry = _DEBT_LEASE_SCOPE.get(concept)
                if not entry or entry[0] != scope:
                    continue
                if get_metric_semantics(role, concept).get("scope_coverage") == "PARTIAL":
                    continue
                obs = _instant_observation_at(data, concept, end, unit)
                if obs is not None:
                    picked[role] = (concept, obs)
                    break
        if len(picked) == 2:
            replaced = []
            out = {}
            for role, original in (("current_debt", current_debt), ("noncurrent_debt", noncurrent_debt)):
                concept, obs = picked[role]
                result = build_instant_result(role, "us-gaap", concept, obs)
                for key in ("freshness", "freshness_anchor"):
                    if key in original:
                        result[key] = original[key]
                result = annotate_debt_scope(result)
                if concept != original.get("concept"):
                    result["scope_alignment"] = {
                        "replaced_concept": original.get("concept"),
                        "replaced_value": original.get("value"),
                        "reason": (
                            "Default selection mixed lease scopes; a same-date "
                            f"pair reported as {scope} was used instead."
                        ),
                    }
                    replaced.append(role)
                    # Replacing the aggregate DebtCurrent with a narrower
                    # concept must not silently drop what the aggregate
                    # held beyond it.
                    if (
                        original.get("concept") == "DebtCurrent"
                        and original.get("value") != result.get("value")
                    ):
                        result["uncovered_components"] = [{
                            "concept": "DebtCurrent",
                            "value": original.get("value"),
                            "accession": original.get("accession"),
                            "note": (
                                "Aggregate current debt differs from the "
                                "scope-aligned component; the difference is "
                                "not covered."
                            ),
                        }]
                out[role] = result
            return out["current_debt"], out["noncurrent_debt"]
    return current_debt, noncurrent_debt


def detect_uncovered_current_components(current_debt: dict, data: dict) -> dict:
    """Rule 3: record same-date current-debt facts the selected concept
    does not cover (no summing, no estimate)."""
    if not isinstance(current_debt, dict) or current_debt.get("status") != "OK":
        return current_debt
    others = _NOT_COVERED_BY.get(current_debt.get("concept"))
    if not others:
        return current_debt
    found = []
    for concept in others:
        obs = _instant_observation_at(
            data, concept, current_debt.get("period_end"), current_debt.get("unit")
        )
        if obs is not None and obs.get("val") not in (0, 0.0):
            found.append({"concept": concept, "value": obs.get("val"), "accession": obs.get("accn")})
    if not found:
        return current_debt
    annotated = dict(current_debt)
    annotated["uncovered_components"] = list(current_debt.get("uncovered_components") or []) + found
    return annotated


# ============================================================
# COMPANY NORMALIZATION
# ============================================================

def normalize_company(
    ticker: str,
    data: dict,
    company_type: str,
):
    validate_companyfacts(data)

    # STEP 15: annual metrics are selected from full-year facts only.
    annual_view = annual_duration_view(data)

    revenue = extract_full_year_metric(
        data,
        annual_view,
        "revenue",
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ],
    )

    net_income = extract_full_year_metric(
        data,
        annual_view,
        "net_income",
        [
            "NetIncomeLoss",
            "ProfitLoss",
        ],
    )

    diluted_eps = extract_full_year_metric(
        data,
        annual_view,
        "diluted_eps",
        [
            "EarningsPerShareDiluted",
        ],
    )

    if company_type == "FINANCIAL":
        operating_income = build_not_applicable_metric(
            "operating_income",
            (
                "Generic operating income framework "
                "is not used for financial companies."
            ),
        )
    else:
        operating_income = extract_full_year_metric(
            data,
            annual_view,
            "operating_income",
            [
                "OperatingIncomeLoss",
            ],
        )

    if company_type == "FINANCIAL":
        cfo = build_not_applicable_metric(
            "cfo",
            (
                "Generic CFO/FCF framework "
                "is not used for financial companies."
            ),
        )
    else:
        cfo = extract_full_year_metric(
            data,
            annual_view,
            "cfo",
            [
                "NetCashProvidedByUsedInOperatingActivities",
            ],
        )

    if company_type == "FINANCIAL":
        capex = build_not_applicable_metric(
            "capex",
            (
                "Generic CapEx/FCF framework "
                "is not used for financial companies."
            ),
        )
    else:
        capex = extract_full_year_metric(
            data,
            annual_view,
            "capex",
            [
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "PaymentsToAcquireOtherPropertyPlantAndEquipment",
                # STEP 15 (2026-10-27): current capex tag for GE, LMT and
                # NVDA in their latest 10-Ks (raw companyfacts, CI run on
                # step15-batch-runner); broader scope, labelled by
                # get_metric_semantics(). Lowest priority.
                "PaymentsToAcquireProductiveAssets",
            ],
        )

    cash = extract_instant_metric(
        data,
        "cash",
        [
            "CashAndCashEquivalentsAtCarryingValue",
        ],
    )

    if company_type == "FINANCIAL":
        current_debt = build_not_applicable_metric(
            "current_debt",
            (
                "Generic industrial debt framework "
                "is not used for financial companies."
            ),
        )
    else:
        current_debt = extract_instant_metric(
            data,
            "current_debt",
            CURRENT_DEBT_CANDIDATES,
        )

    if company_type == "FINANCIAL":
        noncurrent_debt = build_not_applicable_metric(
            "noncurrent_debt",
            (
                "Generic industrial debt framework "
                "is not used for financial companies."
            ),
        )
    else:
        noncurrent_debt = extract_instant_metric(
            data,
            "noncurrent_debt",
            NONCURRENT_DEBT_CANDIDATES,
        )

    shares = extract_instant_metric(
        data,
        "shares_outstanding",
        [
            "EntityCommonStockSharesOutstanding",
            "CommonStockSharesOutstanding",
        ],
        namespaces=(
            "dei",
            "us-gaap",
        ),
    )

    # ========================================================
    # STOCKHOLDERS' EQUITY
    # ========================================================

    stockholders_equity = extract_instant_metric(
        data,
        "stockholders_equity",
        [
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ],
        namespaces=(
            "us-gaap",
        ),
    )

    # Freshness check (STEP 15): downgrade stale selections before any
    # derived value (total_debt) is computed from them.
    annual_anchor = annual_freshness_anchor(annual_view)
    instant_anchor = instant_freshness_anchor(data)
    instant_anchor_source = "US_GAAP_ASSETS" if instant_anchor else None
    if instant_anchor is None:
        # Fallback when us-gaap:Assets is not reported: the latest
        # period end among the selected us-gaap balance-sheet metrics
        # themselves (cover-page share counts excluded, since their
        # date follows the balance sheet). An outlier older than the
        # others is still caught; with no selected balance-sheet value
        # at all, freshness stays unverifiable and fails closed.
        candidate_ends = [
            _parse_iso_date(m.get("period_end"))
            for m in (cash, current_debt, noncurrent_debt, stockholders_equity)
            if isinstance(m, dict)
            and m.get("status") == "OK"
            and m.get("namespace") == "us-gaap"
        ]
        candidate_ends = [e for e in candidate_ends if e is not None]
        if candidate_ends:
            instant_anchor = max(candidate_ends)
            instant_anchor_source = "SELECTED_BALANCE_SHEET_METRICS"

    revenue = apply_freshness_check(revenue, annual_anchor, _FRESHNESS_ANNUAL)
    net_income = apply_freshness_check(net_income, annual_anchor, _FRESHNESS_ANNUAL)
    diluted_eps = apply_freshness_check(diluted_eps, annual_anchor, _FRESHNESS_ANNUAL)
    operating_income = apply_freshness_check(
        operating_income, annual_anchor, _FRESHNESS_ANNUAL
    )
    cfo = apply_freshness_check(cfo, annual_anchor, _FRESHNESS_ANNUAL)
    capex = apply_freshness_check(capex, annual_anchor, _FRESHNESS_ANNUAL)
    cash = apply_freshness_check(cash, instant_anchor, _FRESHNESS_INSTANT)
    current_debt = apply_freshness_check(current_debt, instant_anchor, _FRESHNESS_INSTANT)
    noncurrent_debt = apply_freshness_check(
        noncurrent_debt, instant_anchor, _FRESHNESS_INSTANT
    )
    shares = apply_freshness_check(shares, instant_anchor, _FRESHNESS_INSTANT)
    stockholders_equity = apply_freshness_check(
        stockholders_equity, instant_anchor, _FRESHNESS_INSTANT
    )

    # STEP 15: debt lease-scope labels (used by calculate_total_debt()).
    current_debt = annotate_debt_scope(current_debt)
    noncurrent_debt = annotate_debt_scope(noncurrent_debt)
    current_debt, noncurrent_debt = align_debt_scope(current_debt, noncurrent_debt, data)
    current_debt = detect_uncovered_current_components(current_debt, data)

    # STEP 15: period labels. period_kind is DURATION or INSTANT;
    # period_class distinguishes the annual (10-K FY) duration metrics.
    # Named to avoid the existing "period_type" field used by history
    # records in growth.py.
    def _label(metric, kind, period_class=None):
        if not isinstance(metric, dict) or metric.get("status") == "NOT_APPLICABLE":
            return metric
        labelled = dict(metric)
        labelled["period_kind"] = kind
        if period_class:
            labelled["period_class"] = period_class
        return labelled

    revenue = _label(revenue, "DURATION", "ANNUAL_FY")
    net_income = _label(net_income, "DURATION", "ANNUAL_FY")
    diluted_eps = _label(diluted_eps, "DURATION", "ANNUAL_FY")
    operating_income = _label(operating_income, "DURATION", "ANNUAL_FY")
    cfo = _label(cfo, "DURATION", "ANNUAL_FY")
    capex = _label(capex, "DURATION", "ANNUAL_FY")
    cash = _label(cash, "INSTANT")
    current_debt = _label(current_debt, "INSTANT")
    noncurrent_debt = _label(noncurrent_debt, "INSTANT")
    shares = _label(shares, "INSTANT")
    stockholders_equity = _label(stockholders_equity, "INSTANT")

    total_debt = calculate_total_debt(
        current_debt,
        noncurrent_debt,
        company_type,
    )
    total_debt = _label(total_debt, "INSTANT")

    # STEP 15: scope restrictions that downstream derived metrics do not
    # carry themselves. A consumer must treat the listed derived metrics
    # as not eligible for general peer comparison / scoring.
    scope_flags = []
    if isinstance(capex, dict) and capex.get("status") == "OK" and capex.get("concept_scope"):
        scope_flags.append({
            "metric": "capex",
            "concept": capex.get("concept"),
            "concept_scope": capex.get("concept_scope"),
            "affected_derived_metrics": ["fcf", "fcf_margin"],
            "scoring_eligibility": "RESTRICTED",
            "reason": (
                "CapEx is broader than PP&E-only CapEx; FCF built on it is "
                "not directly comparable with PP&E-based FCF."
            ),
        })
    for name, component in (("current_debt", current_debt), ("noncurrent_debt", noncurrent_debt)):
        if isinstance(component, dict) and component.get("status") == "OK" and component.get("scope_coverage") == "PARTIAL":
            scope_flags.append({
                "metric": name,
                "concept": component.get("concept"),
                "concept_scope": component.get("concept_scope"),
                "affected_derived_metrics": ["total_debt", "net_debt", "debt_to_cash"],
                "scoring_eligibility": "RESTRICTED",
                "reason": "Component covers only part of the debt it stands in for.",
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "scope_flags": scope_flags,
        "annual_duration_range_days": [ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS],
        "freshness_anchors": {
            "annual": annual_anchor.isoformat() if annual_anchor else None,
            "instant": instant_anchor.isoformat() if instant_anchor else None,
            "instant_source": instant_anchor_source,
            "tolerance_days": FRESHNESS_TOLERANCE_DAYS,
        },
        "ticker": ticker,
        "entity_name": data.get("entityName"),
        "cik": data.get("cik"),
        "company_type": company_type,
        "source": {
            "provider": "SEC",
            "dataset": "Company Facts",
        },
        "normalized_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "metrics": {
            "revenue": revenue,
            "net_income": net_income,
            "diluted_eps": diluted_eps,
            "operating_income": operating_income,
            "cfo": cfo,
            "capex": capex,
            "cash": cash,
            "current_debt": current_debt,
            "noncurrent_debt": noncurrent_debt,
            "total_debt": total_debt,
            "shares_outstanding": shares,
            "stockholders_equity": stockholders_equity,
        },
    }


# ============================================================
# NORMALIZED DATA VALIDATION
# ============================================================

def validate_normalized_data(normalized: dict) -> list:
    required_top_level = [
        "schema_version",
        "ticker",
        "entity_name",
        "cik",
        "company_type",
        "metrics",
    ]

    for key in required_top_level:
        if key not in normalized:
            raise ValueError(f"Missing normalized key: {key}")

    company_type = normalized["company_type"]

    if company_type == "NON_FINANCIAL":
        required_metrics = [
            "revenue",
            "net_income",
            "diluted_eps",
            "operating_income",
            "cfo",
            "capex",
            "cash",
            "shares_outstanding",
        ]
    elif company_type == "FINANCIAL":
        required_metrics = [
            "revenue",
            "net_income",
            "diluted_eps",
            "cash",
            "shares_outstanding",
        ]
    else:
        raise ValueError(
            f"Unknown company_type: {company_type}"
        )

    failures = []

    for metric in required_metrics:
        metric_data = normalized["metrics"].get(metric)

        if not isinstance(metric_data, dict):
            failures.append(metric)
            continue

        if metric_data.get("status") != "OK":
            failures.append(metric)

    total_debt = normalized["metrics"].get("total_debt")

    if not isinstance(total_debt, dict):
        failures.append("total_debt")
    elif company_type == "NON_FINANCIAL":
        if total_debt.get("status") != "OK":
            failures.append("total_debt")
    elif company_type == "FINANCIAL":
        if total_debt.get("status") != "NOT_APPLICABLE":
            failures.append("total_debt")

    stockholders_equity = normalized["metrics"].get(
        "stockholders_equity"
    )

    if isinstance(stockholders_equity, dict):
        if stockholders_equity.get("status") == "OK":
            if (
                "equity_scope" not in stockholders_equity
                or "includes_noncontrolling_interest"
                not in stockholders_equity
                or "semantic_status" not in stockholders_equity
            ):
                failures.append("stockholders_equity_semantics")

    return failures


# ============================================================
# FILE HELPERS
# ============================================================

def load_raw_companyfacts(
    ticker: str,
    raw_dir: Path = RAW_DIR,
) -> dict:
    path = raw_dir / f"{ticker}_companyfacts.json"

    if not path.exists():
        raise FileNotFoundError(
            f"Raw SEC file not found: {path}"
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_normalized_data(
    ticker: str,
    normalized: dict,
    processed_dir: Path = PROCESSED_DIR,
) -> Path:
    processed_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = processed_dir / f"{ticker}_fundamentals.json"

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            normalized,
            f,
            ensure_ascii=False,
            indent=2,
        )

    return path


# ============================================================
# NORMALIZE FROM CACHE
# ============================================================

def normalize_from_cache(
    ticker: str,
    company_type: str,
    raw_dir: Path = RAW_DIR,
    processed_dir: Path = PROCESSED_DIR,
):
    data = load_raw_companyfacts(
        ticker,
        raw_dir,
    )

    normalized = normalize_company(
        ticker,
        data,
        company_type,
    )

    failures = validate_normalized_data(
        normalized
    )

    output_path = save_normalized_data(
        ticker,
        normalized,
        processed_dir,
    )

    return {
        "ticker": ticker,
        "output_path": str(output_path),
        "failures": failures,
        "normalized": normalized,
    }


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Normalize SEC Company Facts "
            "into the Stock Master "
            "fundamental schema."
        )
    )

    parser.add_argument(
        "--ticker",
        required=True,
        help="Ticker symbol, e.g. NVDA",
    )

    parser.add_argument(
        "--company-type",
        default="NON_FINANCIAL",
        choices=[
            "NON_FINANCIAL",
            "FINANCIAL",
        ],
    )

    args = parser.parse_args()

    result = normalize_from_cache(
        ticker=args.ticker,
        company_type=args.company_type,
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )
