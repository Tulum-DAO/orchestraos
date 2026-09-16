"""`wal probe` — the mechanical shadow-verify grader (spec §3.3(3)).

Replaces the LLM-authored canary/readback comprehension exam of the old design.
Green (the pre-warmed successor) must return the seq+summary of the last K WAL
events and the current working-set paths; `grade_probe` checks the answer
against the WAL ITSELF. The WAL is the answer key — there is NO LLM grader, so
the check is deterministic and un-gameable (Green cannot talk its way past it).

This proves WAL-RECALL, not judgment (spec risk-1 is explicit about that).
"""


def _working_set_from_events(events):
    """Distinct working-set paths from file_mod events (body_ref = 'git:<cwd>#<path>').
    The green's producer derives its working-set the SAME way over the delivered
    events, so grader + producer stay byte-identical (SR2 single source)."""
    paths = []
    for r in events:
        if r["kind"] == "file_mod" and r["body_ref"] and "#" in r["body_ref"]:
            p = r["body_ref"].split("#", 1)[1]
            if p and p not in paths:
                paths.append(p)
    return paths


def _working_set(store, lineage_root):
    """Distinct working-set paths from file_mod events (full WAL)."""
    return _working_set_from_events(store.events(lineage_root))


def probe_challenge(store, lineage_root, k=5):
    """The challenge handed to Green: report the last K events + working set."""
    return {
        "lineage_root": lineage_root,
        "k": k,
        "require_working_set": True,
        "instructions": (f"Report the seq+summary of the last {k} WAL events "
                         f"and the current working-set paths."),
    }


def grade_probe(store, lineage_root, answer, k=5, scope=None):
    """Grade Green's answer against the WAL truth. Deterministic; WAL = answer key.

    ``scope`` (bar#4 gap-c fix): when given as ``{since_seq, through_seq}``, grade
    only the DELIVERED slice ``since_seq < seq <= through_seq`` — not the full WAL.
    A mid-life green only receives the delta after its hydrate baseline, so grading
    the full WAL would FALSE-FAIL a perfect green (the last-K WAL events span the
    baseline it never re-received). The scope is BLUE-AUTHORITATIVE (bg_state:
    since=first_hydrated_seq, through=last_hydrated_seq), NOT the union of the rows
    the green actually received — a lost row must shrink neither, or a whole-row
    loss would silently narrow the graded slice and re-hide the loss (Potemkin).
    ``scope=None`` => full-WAL grading (unchanged; backward compatible)."""
    events = store.events(lineage_root)
    if scope is not None:
        since = scope.get("since_seq") or 0
        through = scope.get("through_seq")
        events = [r for r in events
                  if r["seq"] > since and (through is None or r["seq"] <= through)]
    expected_events = [{"seq": r["seq"], "summary": r["summary"]}
                       for r in events[-k:]]
    expected_ws = _working_set_from_events(events)

    answer = answer or {}
    got_events = answer.get("last_events") or []
    got_ws = answer.get("working_set") or []

    # last-events must match EXACTLY in order (seq + summary), no more, no less
    norm_got = [{"seq": e.get("seq"), "summary": e.get("summary")}
                for e in got_events]
    last_events_ok = norm_got == expected_events
    # working-set = set equality (order-independent)
    working_set_ok = set(got_ws) == set(expected_ws)

    return {
        "ok": bool(last_events_ok and working_set_ok),
        "last_events_ok": last_events_ok,
        "working_set_ok": working_set_ok,
        "expected_last_events": expected_events,
        "expected_working_set": expected_ws,
    }
