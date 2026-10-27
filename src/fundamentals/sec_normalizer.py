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
            result["value"] = current_value + noncurrent_value
            result["status"] = "OK"
            result["unit"] = current_unit

    return result


# ============================================================
# COMPANY NORMALIZATION
# ============================================================

def normalize_company(
    ticker: str,
    data: dict,
    company_type: str,
):
    validate_companyfacts(data)

    revenue = extract_metric(
        data,
        "revenue",
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ],
    )

    net_income = extract_metric(
        data,
        "net_income",
        [
            "NetIncomeLoss",
            "ProfitLoss",
        ],
    )

    diluted_eps = extract_metric(
        data,
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
        operating_income = extract_metric(
            data,
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
        cfo = extract_metric(
            data,
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
        capex = extract_metric(
            data,
            "capex",
            [
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "PaymentsToAcquireOtherPropertyPlantAndEquipment",
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
            [
                "LongTermDebtCurrent",
                "ShortTermBorrowings",
                "ShortTermDebt",
                "CurrentDebt",
            ],
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
            [
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
            ],
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

    total_debt = calculate_total_debt(
        current_debt,
        noncurrent_debt,
        company_type,
    )

    return {
        "schema_version": SCHEMA_VERSION,
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
