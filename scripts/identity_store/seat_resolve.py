"""Which agent a name means: one resolver for every place a person or an agent names an agent.

Agents (any seat sending a message) must be EXACT: an agent's id, or one of its names or old names written
exactly (case aside). A typo between agents could land in the wrong inbox unnoticed, so nothing is guessed for
them; an unknown address comes straight back with the closest names, never queued silently.

People (a request typed or spoken to Arturo or the GM) get the forgiving path:
- an exact id, name or old name: that agent (alive, or dead, which offers to revive it);
- close to exactly one agent and nothing else is close: that agent, assumed and said out loud;
- close to several: a choice between them;
- nothing, and no agent ever had that name: offer to spawn one.

"Close" means: the same letters ignoring case, spaces and punctuation; the same words in any order; every word
given appearing in the agent's name or id ("demo" for "pm-demo"); or one typo (two in names of eight
letters or more) in a name with the same number of words. One close match that is clearly closer (same words, or one typo) beats looser ones.

`resolve()` is pure (tested on its own); `load_directory()` builds what it reads from the install.
"""
import json
import os
import re
import subprocess
import sys

if os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.identity_store.seat_names import (  # noqa: E402
    BUILTIN_IDS, all_names_ro, assistant_display_name, base_id, known_ids, name_key, read_only)

AGENT, PERSON = "agent", "person"
# words people put around a name ("the demo dev", "our builder agent") that are never part of one
_FILLER = {"the", "a", "an", "my", "our", "your", "agent", "agents", "seat"}


def _strip_filler(text):
    words = str(text or "").split()
    kept = [w for w in words if w.casefold().strip(".,!?") not in _FILLER]
    return " ".join(kept) if kept else str(text or "")


def _tokens(text):
    return [t for t in re.split(r"[^0-9a-z]+", str(text or "").casefold()) if t]


def _lev(a, b, cap):
    """Levenshtein distance, stopping once it exceeds cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _labels(seat):
    """(label, how) for every way the agent can be named: its id, its name, its full name ("Demo · dev"), its old
    names and old full names."""
    out = [(seat["id"], "id")]
    if seat.get("name") and seat["name"] != seat["id"]:
        out.append((seat["name"], "name"))
    if seat.get("full_name") and seat["full_name"] not in (seat["id"], seat.get("name")):
        out.append((seat["full_name"], "full"))
    out += [(a, "alias") for a in seat.get("aliases") or []]
    out += [(a, "alias") for a in seat.get("alias_full") or []]
    return out


def _closeness(query, label):
    """0 for no match, 2 for a strong match (same words any order, or one or two typos), 1 for a loose one
    (every word given appears in the label)."""
    qk, lk = name_key(query), name_key(label)
    if not qk or not lk:
        return 0
    qt, lt = _tokens(query), _tokens(label)
    if qt and sorted(qt) == sorted(lt):
        return 2
    # a typo, not a missing word: "pm-dmeo" is a typo of "pm-demo", "demo" is not one of "qa-demo"
    cap = 2 if len(lk) >= 8 else 1 if len(lk) >= 4 else 0
    if cap and len(qt) == len(lt) and _lev(qk, lk, cap) <= cap:
        return 2
    if qt and all(t in lt for t in qt) and any(len(t) >= 3 for t in qt):
        return 1
    return 0


def _seat_out(seat, via):
    return {"id": seat["id"], "name": seat.get("name") or seat["id"], "via": via, "alive": bool(seat.get("alive")),
            "resumable": bool(seat.get("resumable")), "full_name": seat.get("full_name") or seat.get("name") or seat["id"],
            "project": (seat.get("project") or {}).get("slug")}


def resolve(text, directory, caller=PERSON, context_project=None):
    """What `text` names, as {"kind": ..., ...}:

    deliver  {seat}            an exact match, alive
    dead     {seat}            an exact match, not running (a person is offered resume / a fresh successor;
                               an agent's message is held for it)
    guess    {seat}            people only: the one close agent (alive or not), to be said out loud
    choose   {options:[seat]}  people only: several close agents, or one name in several projects with no
                               project in context
    ambiguous {options:[seat]} agents only: one name in several projects (use the id or the full name)
    unknown  {did_you_mean}    no agent by that name or id, ever (a person is offered to spawn one; an agent's
                               send is refused with the closest names)

    `context_project` (a slug): the project being discussed; a name several projects share goes to that project's
    agent (said out loud as a guess).
    """
    q = str(text or "").strip()
    if q.startswith("@"):
        q = q[1:].strip()
    if not q:
        return {"kind": "unknown", "query": q, "did_you_mean": []}
    if caller == PERSON:
        q = _strip_filler(q)
    qb = base_id(q)                                     # a generation's id names its lineage
    # exact: the id as written, or a name/old name as written apart from case (people: any spacing/punctuation).
    # Names are unique per project, so a short name can match several agents: all are collected.
    exact = []
    for seat in directory:
        for label, how in _labels(seat):
            same = (qb == label) if how == "id" else (q.casefold() == label.casefold())
            if not same and caller == PERSON:
                same = name_key(qb if how == "id" else q) == name_key(label)
            if same:
                exact.append(_seat_out(seat, how))
                break
    if len(exact) == 1:
        return {"kind": "deliver" if exact[0]["alive"] else "dead", "query": q, "seat": exact[0]}
    if exact:
        exact.sort(key=lambda o: (not o["alive"], o["full_name"]))
        if caller == AGENT:
            return {"kind": "ambiguous", "query": q, "options": exact}
        here = [o for o in exact if context_project and o["project"] == context_project]
        if len(here) == 1:
            return {"kind": "guess", "query": q, "seat": here[0]}
        return {"kind": "choose", "query": q, "options": exact}
    scored = []
    for seat in directory:
        best, via = 0, None
        for label, how in _labels(seat):
            c = _closeness(q, label)
            if c > best:
                best, via = c, how
        if best:
            scored.append((best, seat, via))
    scored.sort(key=lambda t: (-t[0], not t[1].get("alive"), t[1]["id"]))
    if caller == AGENT or not scored:
        return {"kind": "unknown", "query": q, "did_you_mean": [_seat_out(s, v) for _, s, v in scored[:3]]}
    strong = [t for t in scored if t[0] == 2]
    pool = strong if len(strong) == 1 else scored
    if len(pool) > 1 and context_project:               # several close: the one in the project being discussed
        here = [t for t in pool if (t[1].get("project") or {}).get("slug") == context_project]
        if len(here) == 1:
            pool = here
    if len(pool) == 1:
        _, seat, via = pool[0]
        return {"kind": "guess", "query": q, "seat": _seat_out(seat, via)}
    return {"kind": "choose", "query": q, "options": [_seat_out(s, v) for _, s, v in pool[:6]]}


def _live_tmux_sessions():
    try:
        r = subprocess.run(["tmux", "list-sessions", "-F", "#S"], capture_output=True, text=True, timeout=3)
        return set(r.stdout.split()) if r.returncode == 0 else set()
    except (OSError, subprocess.SubprocessError):
        return set()


def _pane_has_claude(session):
    """Is Claude actually running in that pane: the fleet's one process-tree check (addressability.py), so a
    tmux session whose Claude has crashed counts as dead, not as an agent to deliver to."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "addressability.py")
    spec = importlib.util.spec_from_file_location("_addressability_for_resolve", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.pane_has_claude(session)


def load_directory(orchestra_dir, live_sessions=None, has_claude=None):
    """Every agent that exists or ever existed, with its names and whether it is running now: its tmux session
    is up AND Claude is running in it (a session left behind by a crashed Claude is dead). Read-only: never
    write-locks the identity DB."""
    sessions = {}
    for rel, pick in (("registry.json", lambda d: d.get("agents") or {}),
                      (os.path.join("state", "agent-sessions.json"), lambda d: d)):
        try:
            for k, v in (pick(json.loads(open(os.path.join(str(orchestra_dir), rel)).read())) or {}).items():
                if not k.startswith("_") and isinstance(v, dict):
                    rec = sessions.setdefault(base_id(v.get("lineage_root") or k), {})
                    rec.setdefault("tmux", v.get("tmux_session") or k)
                    rec["resumable"] = rec.get("resumable") or bool(v.get("resume_command") or v.get("session_id"))
        except (OSError, ValueError, AttributeError):
            pass
    try:
        conn = read_only(orchestra_dir)
        try:
            ids = known_ids(conn, orchestra_dir)
            for root, tmux in conn.execute("SELECT root, tmux_session FROM canonical"):
                sessions.setdefault(base_id(root), {})["tmux"] = tmux
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - no DB yet: the files alone
        ids = {base_id(k) for k in sessions}
    names = all_names_ro(orchestra_dir)
    from scripts.identity_store import seat_projects
    cards = seat_projects.project_cards(orchestra_dir)
    graph = seat_projects.seat_graph(orchestra_dir)
    label_of = lambda slug: (cards.get(slug) or {}).get("label") or slug  # noqa: E731
    live = _live_tmux_sessions() if live_sessions is None else set(live_sessions)
    has_claude = has_claude or _pane_has_claude

    def running(tmux):
        if tmux not in live:
            return False
        try:
            return bool(has_claude(tmux))
        except Exception:  # noqa: BLE001 - the check failed: trust the live session (never call a live agent dead on an error)
            return True
    out = []
    # the built-in mailboxes: always there, always reachable (the assistant is a service, not a pane)
    an = assistant_display_name()
    out.append({"id": "arturo", "name": an or "arturo", "aliases": [], "alive": True, "resumable": False, "builtin": True})
    out.append({"id": "operator", "name": "operator", "aliases": [], "alive": True, "resumable": False, "builtin": True})
    for i in sorted(ids):
        if i in BUILTIN_IDS:
            continue
        rec = names.get(i) or {}
        s = sessions.get(i) or {}
        proj = seat_projects.project_of(i, cards=cards, graph=graph)
        name = rec.get("name") or i
        out.append({"id": i, "name": name, "aliases": rec.get("aliases") or [],
                    "project": {"slug": proj["slug"], "label": proj["label"]} if proj else None,
                    "full_name": seat_projects.full_name(name, i, proj),
                    "alias_full": [f"{label_of(sc)} · {n}" for n, sc in rec.get("alias_rows") or [] if sc],
                    "alive": running(s.get("tmux") or i), "resumable": bool(s.get("resumable"))})
    return out


def main(argv=None):
    """`python3 -m scripts.identity_store.seat_resolve <text> [--agent]`: the resolution as JSON."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("text")
    ap.add_argument("--agent", action="store_true", help="resolve as an agent sender (exact only)")
    a = ap.parse_args(argv)
    from orchestra_cli.settings import data_dir
    print(json.dumps(resolve(a.text, load_directory(data_dir()), AGENT if a.agent else PERSON), indent=2))


if __name__ == "__main__":
    main()
