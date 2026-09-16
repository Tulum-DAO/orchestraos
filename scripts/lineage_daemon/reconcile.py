"""Reconciler invariants -- R2 name->live-head guarantee (WS1 addendum).

R2-for-gm is a RECONCILER guarantee: external injectors that target by NAME
(Arturo gm-inject `-t "gm"`, `VOICE_BRAIN_SESSION="gm"`) are correct-by-
construction ONLY if the canonical session name always maps to exactly ONE live
head. This module holds the pure invariant checks the reconciler enforces:

  * exactly-one-live-head per canonical name,
  * detect a canonical name pointing at a dead/retired session,
  * compute the live head a stale name should repin to (heal).

It also covers the stray `jarvis-gm` name-collision: two live terminal sessions
sharing the `gm` lineage root == a multiple-live-heads violation.

Pure over (meta, sessions) -- no tmux, no live services, no writes. Succession
semantics mirror message-router.resolve_delivery_target (explicit pointers only;
NEVER a name-stem heuristic -- the DEC-1786280521 hazard).
"""


def _successor(entry):
    """Explicit successor pointer, normalizing the two field names in the data
    (`succeeded_by` canonical, `superseded_by` legacy synonym)."""
    e = entry or {}
    return e.get("succeeded_by") or e.get("superseded_by")


def _session_of(agent_id, meta):
    e = meta.get(agent_id) or {}
    return e.get("tmux_session", agent_id)


def canonical_name(agent_id, meta):
    """The durable address a lineage answers to: its lineage_root when set, else
    the agent_id itself (a lineage of one)."""
    e = meta.get(agent_id) or {}
    return e.get("lineage_root") or agent_id


def live_heads_by_name(meta, sessions):
    """Map canonical name -> sorted list of LIVE TERMINAL head sessions.

    A terminal head is a member with no explicit successor (the tip of the
    chain). Healthy == exactly one live terminal head per canonical name.
    """
    heads: dict = {}
    for aid, e in meta.items():
        if _successor(e):
            continue  # not a terminal head
        sess = _session_of(aid, meta)
        if sess not in sessions:
            continue  # not live
        heads.setdefault(canonical_name(aid, meta), set()).add(sess)
    return {name: sorted(s) for name, s in heads.items()}


def single_live_head_violations(meta, sessions):
    """Canonical names that do NOT have exactly one live head.

    Returns a list of {name, kind, live_heads}:
      * kind='no-live-head'        -> zero live terminal heads (dead lineage)
      * kind='multiple-live-heads' -> >1 (ambiguous; e.g. gm + jarvis-gm)
    A name with exactly one live head is healthy and omitted.
    """
    by_name = live_heads_by_name(meta, sessions)
    # roots that exist in meta but have zero live terminal heads must still be
    # reported, so seed from every known canonical name.
    all_names = {canonical_name(a, meta) for a in meta}
    violations = []
    for name in sorted(all_names):
        live = by_name.get(name, [])
        if len(live) == 1:
            continue
        violations.append({
            "name": name,
            "kind": "no-live-head" if not live else "multiple-live-heads",
            "live_heads": live,
        })
    return violations


def name_points_at_dead_session(name, meta, sessions):
    """True iff the canonical name's OWN registered session is not live but a
    live successor exists -- a name pinned to a dead/retired predecessor that
    the reconciler must repin."""
    own = _session_of(name, meta)
    if own in sessions:
        return False
    return bool(heal_target(name, meta, sessions))


def heal_target(name, meta, sessions):
    """The single live head session `name` should repin to, or None.

    Used to heal a canonical name pointing at a dead session. Returns None when
    there is no live head (nothing to heal to) or when the head is ambiguous
    (>1 live head -- a multiple-live-heads violation the caller must resolve
    before repinning, never silently pick one)."""
    root = canonical_name(name, meta)
    live = live_heads_by_name(meta, sessions).get(root, [])
    return live[0] if len(live) == 1 else None
