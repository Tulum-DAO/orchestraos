"""gate_policy.py — supervision policy for the PreToolUse safety gate (attention part 2a, plan §4).

Pure functions over plain rows, stdlib + gate_classify only, cheap to import: the hook imports it on every
classified call.

  resolve(...)        the resolution chain (D1), field by field, most specific wins:
                        agent override row > the agent's role > the project's default role (only when the agent
                        has no role of its own) > the person's default > built-in Let them run.
  winner(...)         the precedence contract for standing grants: the highest tier that reaches the call wins;
                        inside it any deny beats every allow.
  held_hits(...)      which hits the gate holds at the resolved level, after standing permissions.
  would_hold(...)     the same answer for all three levels (tuning data the hook logs).
  card_text(...)      the gate card's question / summary / why-line.
  covers_item(...)    for the attention rules' safety gate: a gate card whose held hits are all allowed now.
  resolve_all(...)    every registry agent resolved (the gateway's push decision and gate.json snapshot).

Rows are the raw SupervisionStore rows: high_risk may be a JSON string, a list or NULL.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace

from gate_classify import Hit, inside

LEVELS = ("watch_closely", "check_in", "let_them_run")
LEVEL_LABELS = {"watch_closely": "Watch closely", "check_in": "Check in", "let_them_run": "Let them run"}
SCOPE_RANK = {"session": 6, "agent": 5, "role": 4, "project": 3, "project_role": 2, "fleet": 1}
BUILTIN = {"level": "let_them_run", "high_risk": [], "hold_wait_s": 0}
FIELDS = ("level", "high_risk", "hold_wait_s")

_GEN_SUFFIX = re.compile(r"-gen\d+$")          # notify_policy.level_for / supervision_store.base_agent

CATEGORY_LABELS = {"outside_world": "Outside-world action", "destructive": "Destructive action",
                   "outside_folder": "Outside its folder", "install": "Install"}
CATEGORY_VERBS = {"outside_world": "reach the outside world", "destructive": "make destructive changes",
                  "outside_folder": "work outside its folder", "install": "install software"}
VERBS = {
    "deploy": "deploy", "push": "push code", "publish": "publish", "message": "send a message",
    "http_write": "send data to a website", "remote_shell": "run a command on another machine",
    "spend": "use a paid API", "connector_write": "change something in a connected app",
    "delete_files": "delete files", "force_push": "force-push", "drop_data": "delete data",
    "kill_process": "stop a program", "system_settings": "change system settings",
    "unrecognised": "run a command the gate doesn't recognise",
    "edit_outside": "edit files outside its folder", "secrets": "touch secrets or keys",  # pragma: allowlist secret
    "npm": "install npm packages", "pip": "install Python packages", "system": "install system packages",
    "script_pipe": "run an install script from the internet", "other": "install software",
}


def base_agent(agent: str) -> str:
    """The agent id with its -genN suffix stripped."""
    return _GEN_SUFFIX.sub("", agent or "")


def verb(action: str | None, category: str | None = None) -> str:
    """The plain verb for an action ("deploy", "push code", …); the category's verb when there is no action."""
    if action and action in VERBS:
        return VERBS[action]
    if category in CATEGORY_VERBS:
        return CATEGORY_VERBS[category]
    return action or "do this"


def _registry_agents(registry) -> dict:
    if not isinstance(registry, dict):
        return {}
    agents = registry.get("agents")
    return agents if isinstance(agents, dict) else registry


def project_of(agent: str, registry: dict, data_dir: str) -> str | None:
    """The project key = realpath of the project folder (review 17): for a cwd under <data>/projects/<name>/…, that
    folder; any other cwd -> its own realpath. Full paths, so two folders sharing a basename never merge; the UI label
    is the basename. Delegates to supervision_store.project_key so the seeded keys match. None when the agent (exact
    or -genN stripped) is not in the registry or has no cwd."""
    agents = _registry_agents(registry)
    row = agents.get(agent)
    if not isinstance(row, dict):
        row = agents.get(base_agent(agent))
    cwd = row.get("cwd") if isinstance(row, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return None
    from supervision_store import project_key  # lazy: keeps this module cheap for the hook
    return project_key(cwd, data_dir)


@dataclass(frozen=True)
class Resolved:
    level: str
    high_risk: tuple
    hold_wait_s: int
    role_id: str | None
    role_source: str | None            # 'agent' | 'project' | None
    sources: dict                      # field -> 'agent' | 'role' | 'project_role' | 'default' | 'builtin'
    explicit: bool                     # True when any winning field came from a person-made value (§9)


def _hr(v):
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return tuple(v) if isinstance(v, (list, tuple)) else None


def _field(row: dict, f: str):
    v = row.get(f)
    if f == "high_risk":
        return _hr(v)
    return v


def _role_person_made(role: dict) -> bool:
    """A role a person edited (touched=1) or created (a stored row whose starter is NULL). A dict without a
    `starter` key is treated as a seeded template (never person-made by itself)."""
    if role.get("touched") in (1, True):
        return True
    return "starter" in role and role.get("starter") is None


def _roles_by_id(roles) -> dict:
    if isinstance(roles, dict):
        return roles
    return {r["id"]: r for r in roles or () if isinstance(r, dict) and r.get("id")}


def resolve(person_rows: dict, roles, assignments: dict, agent: str, project: str | None) -> Resolved:
    """The settings chain (D1). person_rows: {agent: row} (agent '' = the person's default); roles: {id: row} or a
    list of rows; assignments: {(kind, target): (role_id, source)}."""
    person_rows = person_rows or {}
    roles = _roles_by_id(roles)
    assignments = assignments or {}
    a = base_agent(agent)
    tiers = []                                    # (source name, row, person_made)
    own = person_rows.get(a)
    if isinstance(own, dict):
        tiers.append(("agent", own, own.get("source") == "person"))
    role_id = role_source = None
    asg = assignments.get(("agent", a))
    role = roles.get(asg[0]) if asg else None
    if role is not None and not role.get("deleted_at"):
        role_id, role_source = role["id"], "agent"
        tiers.append(("role", role, asg[1] == "person" or _role_person_made(role)))
    elif project:
        pasg = assignments.get(("project", project))
        prole = roles.get(pasg[0]) if pasg else None
        if prole is not None and not prole.get("deleted_at"):
            role_id, role_source = prole["id"], "project"
            tiers.append(("project_role", prole, pasg[1] == "person" or _role_person_made(prole)))
    default = person_rows.get("")
    if isinstance(default, dict):
        tiers.append(("default", default, default.get("source") == "person"))
    out, sources, explicit = {}, {}, False
    for f in FIELDS:
        for name, row, made in tiers:
            v = _field(row, f)
            if v is not None:
                out[f], sources[f] = v, name
                explicit = explicit or made
                break
        else:
            out[f], sources[f] = BUILTIN[f], "builtin"
    level = out["level"] if out["level"] in LEVELS else "let_them_run"
    try:
        wait = int(out["hold_wait_s"])
    except (TypeError, ValueError):
        wait = 0
    return Resolved(level, tuple(out["high_risk"] or ()), wait, role_id, role_source, sources, explicit)


def resolve_all(store, person: str, registry: dict, data_dir: str) -> dict:
    """{agent: Resolved} for every agent in the registry (one read of each table)."""
    rows, roles, asg = store.person_rows(person), store.roles(person), store.assignments(person)
    return {aid: resolve(rows, roles, asg, aid, project_of(aid, registry, data_dir))
            for aid in _registry_agents(registry)}


# ---- grants ---------------------------------------------------------------------------------------------------

def grant_matches(g: dict, hit: Hit) -> bool:
    """Same category; and each narrowing that is set matches: action ==; hit.folder is inside g's folder by
    components (a hit with no folder never matches a folder-narrowed grant); recipient == (case-insensitive)."""
    if g.get("category") != hit.category:
        return False
    if g.get("narrow_action") and g["narrow_action"] != hit.action:
        return False
    if g.get("narrow_folder"):
        if not hit.folder or not inside(hit.folder, g["narrow_folder"]):
            return False
    if g.get("narrow_recipient"):
        if not hit.recipient or hit.recipient.strip().lower() != str(g["narrow_recipient"]).strip().lower():
            return False
    return True


def tier_of(g: dict, *, agent, session_id, project, resolved: Resolved) -> int | None:
    """None when the grant doesn't reach this call; else its tier. A role grant is tier 4 when its role is the
    agent's own role, tier 2 when it is the project default role and the agent has none, else None."""
    scope, ref = g.get("scope"), g.get("scope_ref")
    if scope == "session":
        return SCOPE_RANK["session"] if session_id and ref == session_id else None
    if scope == "agent":
        return SCOPE_RANK["agent"] if ref and base_agent(ref) == base_agent(agent) else None
    if scope == "role":
        if resolved is None or not ref or ref != resolved.role_id:
            return None
        if resolved.role_source == "agent":
            return SCOPE_RANK["role"]
        if resolved.role_source == "project":
            return SCOPE_RANK["project_role"]
        return None
    if scope == "project":
        return SCOPE_RANK["project"] if project and ref == project else None
    if scope == "fleet":
        return SCOPE_RANK["fleet"]
    return None


def winner(grants: list, hit: Hit, *, agent, session_id=None, project=None, resolved: Resolved = None) -> dict | None:
    """The precedence contract (review 7):
       1. keep the grants that are active, reach this call (tier_of) and match the hit;
       2. keep only those at the highest tier present: most specific wins;
       3. within that tier, any deny wins over every allow, however narrow the allow is;
       4. else the newest allow (for display only; all allows mean the same).
    None = no grant."""
    best, top = [], None
    for g in grants or ():
        if g.get("state") != "active" or not grant_matches(g, hit):
            continue
        t = tier_of(g, agent=agent, session_id=session_id, project=project, resolved=resolved)
        if t is None:
            continue
        if top is None or t > top:
            best, top = [g], t
        elif t == top:
            best.append(g)
    if not best:
        return None
    denies = [g for g in best if g.get("effect") == "deny"]
    if denies:
        return denies[0]
    return max(best, key=lambda g: (str(g.get("created_at") or ""), str(g.get("id") or "")))


def _level_holds(hits, level: str, high_risk) -> list:
    if level == "watch_closely":
        return list(hits)
    if level == "check_in":
        hr = set(high_risk or ())
        return [h for h in hits if h.certain and h.category in hr]
    return []


def held_hits(hits, *, resolved: Resolved, grants, agent, session_id=None, project=None) -> list:
    """Hits the gate holds:
       let_them_run -> none; check_in -> certain hits whose category is in high_risk; watch_closely -> every hit
       (an uncertain one counts as destructive); then drop any hit whose winner is an allow grant. A deny winner
       keeps it held."""
    out = []
    for h in _level_holds(list(hits or ()), resolved.level, resolved.high_risk):
        w = winner(grants, h, agent=agent, session_id=session_id, project=project, resolved=resolved)
        if w is not None and w.get("effect") == "allow":
            continue
        out.append(h)
    return out


def would_hold(hits, grants, *, agent, session_id=None, project=None, resolved: Resolved = None) -> dict:
    """{level: [[category, action], …]} for all three levels: the tuning data logged on every classified call.
    Check in uses the resolved high_risk (none when no resolution is given)."""
    base = resolved or Resolved("let_them_run", (), 0, None, None, {}, False)
    return {lvl: [[h.category, h.action] for h in held_hits(hits, resolved=replace(base, level=lvl), grants=grants,
                                                              agent=agent, session_id=session_id, project=project)]
            for lvl in LEVELS[::-1]}


def all_covered(hits, *, resolved: Resolved, grants, agent, session_id=None, project=None) -> bool:
    """True when every hit has an allow winner now (and there is at least one hit)."""
    hits = list(hits or ())
    if not hits:
        return False
    for h in hits:
        w = winner(grants, h, agent=agent, session_id=session_id, project=project, resolved=resolved)
        if w is None or w.get("effect") != "allow":
            return False
    return True


# ---- card wording ----------------------------------------------------------------------------------------------

def action_text(hits) -> str:
    """The held command(s) as one short line: the distinct details, in order."""
    seen = []
    for h in hits or ():
        d = (h.detail or h.action or "").strip()
        if d and d not in seen:
            seen.append(d)
    return "; ".join(seen)


def why_line(agent: str, resolved: Resolved, *, role_names=None, project=None) -> str:
    src = (resolved.sources or {}).get("level")
    lvl = LEVEL_LABELS.get(resolved.level, resolved.level)
    role = (role_names or {}).get(resolved.role_id) or resolved.role_id or "its role"
    if src == "agent":
        why = f"Held because you set {agent} to {lvl}"
    elif src == "role":
        why = f"Held because {agent}'s role is {role}"
    elif src == "project_role":
        name = os.path.basename(project.rstrip("/")) if project else "this project"
        why = f"Held because {name}'s default role is {role}"
    else:
        why = f"Held because your default is {lvl}"
    if resolved.level == "check_in" and resolved.high_risk:
        cats = " and ".join(CATEGORY_LABELS.get(c, c).lower() for c in resolved.high_risk)
        why += f" (you marked {cats} high-risk)"
    return why


def card_text(agent: str, hits, resolved: Resolved, role_names=None, project=None) -> dict:
    """{question, summary, why} for the gate card (§5.1); names the role if any."""
    hits = list(hits or ())
    verbs = []
    for h in hits:
        v = verb(h.action, h.category)
        if v not in verbs:
            verbs.append(v)
    what = action_text(hits)
    question = f"{agent} wants to {' and '.join(verbs) or 'act'}" + (f": {what}" if what else "")
    kinds = []
    for h in hits:
        k = f"{CATEGORY_LABELS.get(h.category, h.category)} · {h.action}"
        if k not in kinds:
            kinds.append(k)
    why = why_line(agent, resolved, role_names=role_names, project=project)
    tail = (f"{agent} is carrying on with other work." if resolved.hold_wait_s <= 0
            else f"{agent} is waiting up to {max(1, round(resolved.hold_wait_s / 60))} min for you.")
    summary = f"{'; '.join(kinds)}. {why}. {tail}" if kinds else f"{why}. {tail}"
    return {"question": question, "summary": summary, "why": why}


# ---- the attention rules' safety gate --------------------------------------------------------------------------

def _menu(row):
    m = (row or {}).get("menu")
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            return {}
    return m if isinstance(m, dict) else {}


def hits_of(raw_hits) -> list:
    if isinstance(raw_hits, str):
        try:
            raw_hits = json.loads(raw_hits)
        except ValueError:
            return []
    out = []
    for d in raw_hits or ():
        if isinstance(d, dict) and d.get("category") and d.get("action"):
            out.append(Hit.from_dict(d))
    return out


def _load_registry(data_dir):
    try:
        with open(os.path.join(data_dir, "registry.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return {}


def hold_covered(store, hold: dict, *, person: str, registry=None, data_dir=None, grants=None) -> bool:
    """True when every held hit of `hold` has an allow winner now, for that hold's agent/session/project."""
    hits = hits_of(hold.get("hits"))
    if not hits:
        return False
    agent = hold.get("agent") or ""
    data_dir = data_dir or os.environ.get("ORCHESTRA_DIR")
    if registry is None and data_dir:
        registry = _load_registry(data_dir)
    project = project_of(agent, registry or {}, data_dir) if data_dir else None
    resolved = resolve(store.person_rows(person), store.roles(person), store.assignments(person), agent, project)
    if grants is None:
        grants = store.grants(person, states=("active",))
    return all_covered(hits, resolved=resolved, grants=grants, agent=agent, session_id=hold.get("session_id"),
                       project=project)


def covers_item(item: dict, raw: dict | None, store, *, person: str = "operator", registry=None,
                data_dir=None) -> bool:
    """For part 1's safety gate: True only for a gate card whose hold's hits are ALL covered by an allow winner now.
    Every other card (high risk, hard to undo, a headless or terminal permission prompt) -> False. Never raises."""
    try:
        row = raw if isinstance(raw, dict) else (item or {}).get("card")
        if not isinstance(row, dict) or row.get("origin") not in (None, "gate"):
            return False
        bridge = _menu(row).get("bridge")
        if not isinstance(bridge, dict) or bridge.get("kind") != "gate":
            return False
        hold = store.hold(bridge.get("hold_id")) if bridge.get("hold_id") else None
        if hold is None and row.get("id"):
            hold = store.hold_by_card(row["id"])
        if hold is None:
            return False
        return hold_covered(store, hold, person=person, registry=registry, data_dir=data_dir)
    except Exception:  # noqa: BLE001 — unreadable: never covered (the safe side)
        return False
