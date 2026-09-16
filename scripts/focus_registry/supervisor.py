"""WS4 supervisor focus-backlog beat — DRY-RUN half (Decision 4).

For each active focus whose owner-PM lineage is idle and whose backlog has a
next actionable item, PROPOSE feeding that item to the owner. This build is
DRY-RUN ONLY: `propose_feeds` returns proposals (pure, no IO, drives no live
agent). Live feeding is a SEPARATE the operator-gated build (post gm+agy congruence +
trust window) — see docs/superpowers/specs/2026-08-14-agentic-loop-platform-build-plan.md WS4.

Consumes (does NOT re-derive) ob's landed scripts/mission_supervisor:
  - evaluator.evaluate -> COMPLETE/INCOMPLETE/UNKNOWN (verified artifact, not
    self-claim); an item's `completion` field carries that verdict.
  - eligibility / continuation are the daemon-side gates for the LIVE build; the
    dry-run beat only reads focus records + a per-focus backlog + an idle-owner set.
"""
from typing import List, Optional

ACTIVE = "active"
COMPLETE = "COMPLETE"
UNKNOWN = "UNKNOWN"
IDLE = "idle"  # agent-status state that means the owner has no in-flight work

_IMPORTANCE = {"H": 3, "M": 2, "L": 1}


def _rank_key(item: dict):
    imp = _IMPORTANCE.get(item.get("importance"), 0)
    rel = _IMPORTANCE.get(item.get("relevance"), 0)
    return (-imp, -rel)


def score_backlog(focus: dict, items: List[dict]) -> List[dict]:
    """Rank a focus's backlog items against its north-star (importance then
    relevance, stable on ties). `focus` is accepted for future north-star
    weighting; ranking today is item-intrinsic."""
    return sorted(items, key=_rank_key)


def next_item(focus: dict, items: List[dict]) -> Optional[dict]:
    """The next actionable item, or None.

    Walks ranked items: a COMPLETE item is done -> advance past it; an UNKNOWN
    item cannot be confirmed done -> STOP (never advance past unverified work,
    per evaluator's not-self-claim contract); a blocked item is skipped; the
    first unblocked None/INCOMPLETE item is the next thing to feed.
    """
    for it in score_backlog(focus, items):
        c = it.get("completion")
        if c == COMPLETE:
            continue
        if c == UNKNOWN:
            return None
        if it.get("blocked"):
            continue
        return it
    return None


def _bare(entity_id: str) -> str:
    return entity_id[len("agent:"):] if entity_id.startswith("agent:") else entity_id


def idle_owners_from_status(status_list: List[dict], owner_ids: set) -> set:
    """Which owner entity-ids are idle, per read-only agent-status.

    An owner is idle iff some status entry's `session` matches the owner's bare
    name (exact OR as a '-'-suffix of a prefixed tmux session name) AND its
    `state` == 'idle'. Any other state (working/thinking/stranded_input/unknown)
    is NOT idle — conservative: never feed an owner we can't confirm is free.

    NOTE: exact/suffix matching is a DRY-RUN heuristic only. The LIVE build MUST
    resolve focus-owner id <-> live session via resolve_delivery_target
    (scripts/message-router.py:212) — it walks the succeeded_by chain to the live
    head and handles the rotation-renamed-owner case this string-match would miss
    (ob-confirmed, Task 8). Reverse map: agent_for_session(session, meta).
    """
    idle: set = set()
    for oid in owner_ids:
        name = _bare(oid)
        for e in status_list:
            sess = e.get("session") or ""
            if (sess == name or sess.endswith("-" + name)) and e.get("state") == IDLE:
                idle.add(oid)
                break
    return idle


# Event types (from event_schema) whose being-the-latest-event means the agent is
# idle: it ended its turn / its session closed and hasn't started new work.
_IDLE_EVENT_TYPES = {"turn_ended", "session_end"}


def idle_owners_from_events(events: List[dict], owner_ids: set) -> set:
    """Which owners are idle per the HOOK-EVENT BUS (effects, not pane inference).

    For each owner, the LATEST event (by ts) decides: turn_ended/session_end -> idle;
    a later prompt_submit/tool_use means it started new work -> not idle. An owner with
    no events is NOT idle (conservative — no evidence of freeness, don't feed).

    This is the WS4 event-stream upgrade of idle_owners_from_status: the bus's
    turn_ended edge is a stamped effect, not a lagging pane scrape (Finding 0.5)."""
    latest = {}
    for e in events:
        agent = e.get("agent")
        if agent not in owner_ids:
            continue
        ts = e.get("ts", 0)
        if agent not in latest or ts >= latest[agent][0]:
            latest[agent] = (ts, e.get("type"))
    return {a for a, (_, typ) in latest.items() if typ in _IDLE_EVENT_TYPES}


def propose_feeds(store: dict, items_by_focus: dict, idle_owner_ids: set) -> List[dict]:
    """DRY-RUN beat: propose feeding each idle owned focus its next item.

    Returns a list of feed proposals (each marked live=False). Drives NO agent,
    writes nothing. `idle_owner_ids` is computed by the caller from process
    truth (detector), passed PURE in — the beat does no IO.
    """
    proposals: List[dict] = []
    for fid, ent in store.get("entities", {}).items():
        if ent.get("status") != ACTIVE:
            continue
        owner = ent.get("owner")
        if not owner:
            continue  # un-owned focus -> fallback routing, never auto-fed
        if owner not in idle_owner_ids:
            continue  # owner is in-flight -> don't interrupt
        nxt = next_item(ent, items_by_focus.get(fid, []))
        if nxt is None:
            continue
        proposals.append({
            "kind": "feed_proposal",
            "focus": fid,
            "owner": owner,
            "item": nxt,
            "live": False,  # DRY-RUN — a live feed is a separate the operator-gated build
        })
    return proposals


def dry_run(store: dict, status_list: List[dict], items_by_focus: dict) -> dict:
    """Compose the DRY-RUN beat report from a focus store + read-only status +
    a per-focus backlog. Pure given its inputs; drives nothing. The CLI supplies
    a live (read-only) status_list; backlog sourcing from tracked tasks/queue is
    the LIVE build's Task-8 integration (empty backlog -> 0 proposals, honestly)."""
    owner_ids = {e["owner"] for e in store.get("entities", {}).values()
                 if e.get("status") == ACTIVE and e.get("owner")}
    idle = idle_owners_from_status(status_list, owner_ids)
    proposals = propose_feeds(store, items_by_focus, idle)
    return {
        "cycle": "dry-run",
        "source": "agent-status",
        "drives_live_agent": False,
        "active_owned_focuses": len(owner_ids),
        "idle_owners": sorted(idle),
        "backlog_focuses": sorted(items_by_focus.keys()),
        "proposals": proposals,
    }


def dry_run_from_events(store: dict, events: List[dict], items_by_focus: dict) -> dict:
    """The WS4 beat consuming the HOOK-EVENT BUS (staging; drives nothing).

    Same shape as dry_run but idle owners come from the bus event stream
    (turn_ended/session_end as the latest event = idle) rather than pane inference —
    the effects-not-inference upgrade (Finding 0.5). The caller passes bus events
    (e.g. bus.read_events(stream_dir, days=<hot window>)). Live feeding stays a
    separate the operator-gated build (post gm+agy congruence + trust window)."""
    owner_ids = {e["owner"] for e in store.get("entities", {}).values()
                 if e.get("status") == ACTIVE and e.get("owner")}
    idle = idle_owners_from_events(events, owner_ids)
    proposals = propose_feeds(store, items_by_focus, idle)
    return {
        "cycle": "dry-run",
        "source": "event-stream",
        "drives_live_agent": False,
        "active_owned_focuses": len(owner_ids),
        "idle_owners": sorted(idle),
        "backlog_focuses": sorted(items_by_focus.keys()),
        "proposals": proposals,
    }
