"""`orchestra grant` / `orchestra gate`: the safety gate from the command line (attention part 2a, plan §7.3).

    orchestra grant propose --category C [--only ACTION] [--folder P] [--recipient R]
                            [--for session|me|role|project|all] [--said "what the person said in chat"]
        A pending grant (source chat, proposed by you) and a Confirm / Don't card. It does nothing until the
        card is answered Confirm. Prints the card id. Who you are: $ORCHESTRA_AGENT_ID, else the tmux session.
    orchestra gate mode off|observe|enforce
        Through the gateway (PUT /gate/mode) when it is up, else gate.json directly.
    orchestra gate status [--json]
        Mode, kill switch, per-agent coverage (gate-seen/) and the last 20 gate-log lines.
    orchestra gate replay --fixtures F (--as-agent A | --all-agents) [--level L]
        Classify every fixture call and resolve it against the DB as that agent (read-only: no card, no hold,
        no log line). Prints {"calls", "with_hits", "held", "by_level"}; the release check wants held == 0.
    orchestra gate retire-open
        Rollback: every filing / waiting / open hold -> withdrawn (CAS) and its card retired; every pending
        grant confirm card retired and its grant declined. Prints the counts.

Exit codes: 0 done; 1 busy or failed (run it again); 2 usage (unknown category, action, level or agent);
3 the supervision store is not armed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

from . import settings as S

FOR_SCOPES = {"session": "session", "me": "agent", "role": "role", "project": "project", "all": "fleet"}
LEVELS = ("watch_closely", "check_in", "let_them_run")
MODES = ("off", "observe", "enforce")
NOT_GATED_RUNTIMES = ("codex", "grok")
SEEN_FRESH_S = 24 * 3600
LOG_LINES = 20
HTTP_TIMEOUT_S = 5.0
OPEN_HOLD_STATES = ("filing", "waiting", "open")


class GateCmdError(Exception):
    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


# ---- parser -------------------------------------------------------------------------------------------------------

def add_parsers(sub) -> None:
    g = sub.add_parser("grant", help="propose a standing permission the person gave you in chat (needs their Confirm)")
    gs = g.add_subparsers(dest="grant_cmd", required=True)
    gp = gs.add_parser("propose", help="file a pending grant and a Confirm / Don't card; prints the card id")
    gp.add_argument("--category", required=True, help="outside_world | destructive | outside_folder | install")
    gp.add_argument("--only", dest="action", help="narrow to one action of the category, e.g. deploy")
    gp.add_argument("--folder", help="narrow to a folder (and everything inside it)")
    gp.add_argument("--recipient", help="narrow to one recipient (a remote, host or address)")
    gp.add_argument("--for", dest="for_", choices=list(FOR_SCOPES), default="me",
                    help="who it covers: this session, me (default), my role, my project, or all agents")
    gp.add_argument("--said", help="the person's words in chat, quoted on the card")

    t = sub.add_parser("gate", help="the safety gate: mode, status, replay (release check), retire-open (rollback)")
    ts = t.add_subparsers(dest="gate_cmd", required=True)
    tm = ts.add_parser("mode", help="set the gate mode (through the gateway when it is up, else gate.json)")
    tm.add_argument("mode", choices=list(MODES))
    st = ts.add_parser("status", help="mode, kill switch, per-agent coverage and the last 20 log lines")
    st.add_argument("--json", action="store_true")
    rp = ts.add_parser("replay", help="classify + resolve fixture calls as an agent, read-only; prints held counts")
    rp.add_argument("--fixtures", required=True, help="a calls.jsonl (placeholders are expanded)")
    who = rp.add_mutually_exclusive_group(required=True)
    who.add_argument("--as-agent", dest="as_agent", help="the agent to resolve as")
    who.add_argument("--all-agents", dest="all_agents", action="store_true", help="every registered agent")
    rp.add_argument("--level", choices=list(LEVELS), help="pretend the agent is at this level")
    ts.add_parser("retire-open", help="rollback: withdraw every open hold, retire gate cards, decline pending grants")


# ---- shared plumbing ----------------------------------------------------------------------------------------------

def _data_dir() -> Path:
    d = S.data_dir()
    # The scripts below resolve the data dir lazily from ORCHESTRA_DIR; make them agree with this command.
    os.environ.setdefault("ORCHESTRA_DIR", str(d))
    return d


def _scripts():
    p = str(S.repo_root_from_env() / "scripts")
    if p not in sys.path:
        sys.path.insert(0, p)


def _db(data_dir: Path) -> Path:
    return data_dir / "state" / "tasks.db"


def _registry(data_dir: Path) -> dict:
    try:
        reg = json.loads((data_dir / "registry.json").read_text())
    except (OSError, ValueError):
        return {}
    return reg if isinstance(reg, dict) else {}


def _agents(registry: dict) -> dict:
    a = registry.get("agents")
    return a if isinstance(a, dict) else {}


def _person(data_dir: Path) -> str:
    """gate.json's person, else the install's operator id, else 'operator' (as the gateway and hook read it)."""
    _scripts()
    import gate_config as GCF
    p = GCF.read_gate_raw(data_dir) or {}
    if isinstance(p.get("person"), str) and p["person"].strip():
        return p["person"].strip()
    try:
        import notify_policy
        return notify_policy.install_user_and_zone()[0] or "operator"
    except Exception:  # noqa: BLE001
        return "operator"


class _RO:
    """Read-only policy reads (sqlite mode=ro). A missing DB or missing tables read as empty (= Let them run)."""

    def __init__(self, db_path: Path):
        self.c = None
        if db_path.exists():
            try:
                self.c = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True, timeout=5)
                self.c.row_factory = sqlite3.Row
            except sqlite3.Error:
                self.c = None

    def _q(self, sql, args=()):
        if self.c is None:
            return []
        try:
            return [dict(r) for r in self.c.execute(sql, list(args))]
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return []
            raise

    def policy(self, person):
        rows = {r["agent"]: r for r in self._q("SELECT * FROM supervision_settings WHERE person=?", [person])}
        roles = self._q("SELECT * FROM supervision_roles WHERE person=? AND deleted_at IS NULL", [person])
        asg = {(r["target_kind"], r["target"]): (r["role_id"], r["source"]) for r in self._q(
            "SELECT target_kind, target, role_id, source FROM role_assignments WHERE person=?", [person])}
        grants = self._q("SELECT * FROM standing_grants WHERE person=? AND state='active'", [person])
        return rows, roles, asg, grants

    def open_hold_count(self) -> int:
        r = self._q("SELECT count(*) AS n FROM gate_holds WHERE state IN ('filing','waiting','open')")
        return int(r[0]["n"]) if r else 0

    def close(self):
        if self.c is not None:
            self.c.close()


# ---- orchestra grant propose ---------------------------------------------------------------------------------------

def _agent_id() -> str:
    from .react_cmd import agent_id
    return agent_id()


def propose(ns) -> str:
    data_dir = _data_dir()
    _scripts()
    import gate_classify as GC
    import gate_policy as GP
    import supervision_store as SS
    if ns.category not in SS.CATEGORIES:
        raise GateCmdError(f"unknown category {ns.category!r} (one of {', '.join(SS.CATEGORIES)})", 2)
    if ns.action and ns.action not in GC.ACTIONS.get(ns.category, {}):
        raise GateCmdError(f"unknown action {ns.action!r} for {ns.category} "
                           f"(one of {', '.join(GC.ACTIONS.get(ns.category, {}))})", 2)
    agent = _agent_id()
    if not agent:
        raise GateCmdError("who are you? ORCHESTRA_AGENT_ID is not set and this is not a tmux pane", 2)
    narrow = {}
    if ns.action:
        narrow["action"] = ns.action
    if ns.folder:
        narrow["folder"] = os.path.realpath(os.path.expanduser(ns.folder))
    if ns.recipient:
        narrow["recipient"] = ns.recipient.strip()

    ss = SS.SupervisionStore(db_path=_db(data_dir))
    try:
        ss.migrate()
    except SS.SupervisionBusy as e:
        raise GateCmdError(str(e), 1) from e
    if not ss.armed:
        raise GateCmdError(f"the supervision store is not armed ({SS.MIGRATION_ID}); nothing filed", 3)

    person = _person(data_dir)
    registry = _registry(data_dir)
    row = _agents(registry).get(agent) or _agents(registry).get(GP.base_agent(agent)) or {}
    scope = FOR_SCOPES[ns.for_]
    scope_ref = None
    if scope == "session":
        scope_ref = (row.get("session_id") if isinstance(row, dict) else None) or \
            os.environ.get("CLAUDE_SESSION_ID", "").strip() or None
        if not scope_ref:
            raise GateCmdError("--for session: no session id for you in the registry", 2)
    elif scope == "agent":
        scope_ref = agent
    elif scope in ("role", "project"):
        project = GP.project_of(agent, registry, str(data_dir))
        if scope == "project":
            scope_ref = project
            if not scope_ref:
                raise GateCmdError("--for project: you have no project folder in the registry", 2)
        else:
            ro = _RO(_db(data_dir))
            try:
                rows, roles, asg, _g = ro.policy(person)
            finally:
                ro.close()
            scope_ref = GP.resolve(rows, roles, asg, agent, project).role_id
            if not scope_ref:
                raise GateCmdError("--for role: you have no role", 2)

    import gate_cards as GCD
    from approval_schema import ApprovalStore
    try:
        _gid, rid = GCD.propose_grant(ApprovalStore(db_path=str(_db(data_dir))), ss, agent=agent,
                                      category=ns.category, narrow=narrow or None, scope=scope, scope_ref=scope_ref,
                                      said=ns.said, person=person)
    except SS.SupervisionNotArmed as e:
        raise GateCmdError(str(e), 3) from e
    except SS.SupervisionBusy as e:
        raise GateCmdError(str(e), 1) from e
    except SS.Invalid as e:
        raise GateCmdError(f"invalid grant: {e}", 2) from e
    return rid


def cmd_grant(ns) -> int:
    try:
        print(propose(ns))
        return 0
    except GateCmdError as e:
        print(f"orchestra grant: {e}", file=sys.stderr)
        return e.code


# ---- orchestra gate mode ------------------------------------------------------------------------------------------

def _gateway_url(data_dir: Path) -> str:
    env = os.environ.get("WATCH_GATEWAY_URL", "").strip()
    if env:
        return env.rstrip("/")
    _scripts()
    import gate_config as GCF
    g = GCF.read_gate_raw(data_dir) or {}
    if isinstance(g.get("gateway_url"), str) and g["gateway_url"].strip():
        return g["gateway_url"].strip().rstrip("/")
    st = S.load_settings()
    return f"http://{S._connectable(st.gateway_host)}:{st.gateway_port}"


def _gateway_token() -> str:
    env = os.environ.get("WATCH_GATEWAY_TOKEN_FILE", "").strip()
    try:
        p = Path(env) if env else S.load_settings().token_file
        return Path(p).read_text().strip()
    except (OSError, AttributeError, TypeError):
        return ""


def set_mode(mode: str) -> str:
    """'gateway' when the gateway took it, 'file' when gate.json was written directly."""
    if mode not in MODES:
        raise GateCmdError(f"unknown mode {mode!r}", 2)
    data_dir = _data_dir()
    url = _gateway_url(data_dir) + "/gate/mode"
    tok = _gateway_token()
    req = urllib.request.Request(url, data=json.dumps({"mode": mode}).encode(), method="PUT",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {tok}"} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            doc = json.loads(resp.read() or b"{}")
        if doc.get("ok") and doc.get("mode") == mode:
            return "gateway"
        raise GateCmdError(f"the gateway did not set the mode: {doc}", 1)
    except urllib.error.HTTPError as e:
        if e.code != 404:                       # 404: a gateway without the route; the file is the truth then
            raise GateCmdError(f"the gateway refused: HTTP {e.code}", 1) from e
    except (urllib.error.URLError, OSError, ValueError):
        pass                                    # the gateway is down: write the file
    _scripts()
    import gate_config as GCF
    try:
        GCF.write_gate(data_dir, mode=mode)
    except OSError as e:
        raise GateCmdError(f"could not write gate.json: {e}", 1) from e
    return "file"


# ---- orchestra gate status ----------------------------------------------------------------------------------------

def coverage(data_dir: Path, registry: dict, seen: dict, now: float | None = None) -> dict:
    """{agent: {status, mode, seen_at, session_id}}; status protected | observing | off | not_seen | not_supported."""
    now = time.time() if now is None else now
    out = {}
    for aid, row in sorted(_agents(registry).items()):
        row = row if isinstance(row, dict) else {}
        s = seen.get(aid)
        if row.get("runtime") in NOT_GATED_RUNTIMES:
            status = "not_supported"
        elif not isinstance(s, dict):
            status = "not_seen"
        else:
            fresh = isinstance(s.get("ts"), (int, float)) and now - s["ts"] <= SEEN_FRESH_S
            same = not row.get("session_id") or s.get("session_id") == row.get("session_id")
            if not (fresh and same):
                status = "not_seen"
            else:
                status = {"enforce": "protected", "observe": "observing"}.get(s.get("mode"), "off")
        out[aid] = {"status": status, "mode": (s or {}).get("mode"), "seen_at": (s or {}).get("ts"),
                    "session_id": (s or {}).get("session_id")}
    return out


def status() -> dict:
    data_dir = _data_dir()
    _scripts()
    import gate_config as GCF
    gate = GCF.read_gate(data_dir)
    ro = _RO(_db(data_dir))
    try:
        open_holds = ro.open_hold_count()
    finally:
        ro.close()
    return {"mode": gate.get("mode", "off"), "kill_switch": GCF.kill_switch(data_dir), "gen": gate.get("gen"),
            "updated_at": gate.get("updated_at"), "open_holds": open_holds,
            "coverage": coverage(data_dir, _registry(data_dir), GCF.seen(data_dir)),
            "log": GCF.read_log(data_dir, limit=LOG_LINES)}


def render_status(s: dict) -> str:
    out = [f"mode:        {s['mode']}" + ("   (GATE_OFF present: every call is allowed)" if s["kill_switch"] else ""),
           f"kill switch: {'ON' if s['kill_switch'] else 'off'}",
           f"open holds:  {s['open_holds']}", "", "coverage:"]
    if not s["coverage"]:
        out.append("  (no registered agents)")
    for aid, c in s["coverage"].items():
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(c["seen_at"])) if c["seen_at"] else "-"
        out.append(f"  {aid:24} {c['status']:14} last seen {when}")
    out += ["", f"last {LOG_LINES} log lines:"]
    if not s["log"]:
        out.append("  (none)")
    for r in s["log"]:
        hits = ",".join(f"{h[0]}/{h[1]}" for h in r.get("hits") or [] if isinstance(h, list) and len(h) == 2)
        out.append(f"  {r.get('ts', '?')} {str(r.get('agent', '?')):20} {str(r.get('decision', '?')):8} "
                   f"{hits} {r.get('detail') or ''}".rstrip())
    return "\n".join(out)


# ---- orchestra gate replay ----------------------------------------------------------------------------------------

def _fake_realpath(mapping):
    import posixpath
    import gate_classify as GC

    def real(p):
        p = posixpath.normpath(p)
        for src, dst in (mapping or {}).items():
            if GC.inside(p, src):
                return posixpath.normpath(dst + p[len(src):])
        return p
    return real


def load_fixtures(path) -> list:
    _scripts()
    import gate_fixture_harvest as GH
    out = []
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(GH.expand(json.loads(line)))
        except ValueError as e:
            raise GateCmdError(f"{path}:{n}: not JSON ({e})", 2) from e
    return out


def _classify_fixture(c) -> list:
    """The fixture's call exactly as the classifier tests build it (fake home/cwd, its own bins and scripts)."""
    import gate_classify as GC
    bins = frozenset(c.get("local_bins") or [])
    saved = GC._bin_exists
    GC._bin_exists = lambda cwd, name: name in bins
    try:
        ctx = GC.CallCtx(tool=c.get("tool") or "", tool_input=c.get("input") if isinstance(c.get("input"), dict)
                         else {}, cwd=c.get("cwd") or "/home/agent/proj",
                         own_dirs=tuple(c.get("own_dirs") or ()), scratch_dirs=("/tmp",), home="/home/agent",
                         local_bins=bins, package_scripts=c.get("package_scripts"),
                         realpath=_fake_realpath(c.get("realpath")))
        return GC.classify(ctx)
    finally:
        GC._bin_exists = saved


def replay_one(classified: list, agent: str, *, policy, registry: dict, data_dir: Path, level=None) -> dict:
    import gate_policy as GP
    rows, roles, asg, grants = policy
    project = GP.project_of(agent, registry, str(data_dir))
    resolved = GP.resolve(rows, roles, asg, agent, project)
    if level:
        resolved = replace(resolved, level=level)
    calls = with_hits = held = 0
    by_level = {lvl: 0 for lvl in LEVELS}
    for hits in classified:
        calls += 1
        if not hits:
            continue
        with_hits += 1
        if GP.held_hits(hits, resolved=resolved, grants=grants, agent=agent, project=project):
            held += 1
        for lvl, h in GP.would_hold(hits, grants, agent=agent, project=project, resolved=resolved).items():
            if h:
                by_level[lvl] += 1
    return {"calls": calls, "with_hits": with_hits, "held": held, "by_level": by_level}


def replay(fixtures, *, as_agent=None, all_agents=False, level=None) -> dict:
    data_dir = _data_dir()
    _scripts()
    if level is not None and level not in LEVELS:
        raise GateCmdError(f"unknown level {level!r}", 2)
    classified = [_classify_fixture(c) for c in load_fixtures(fixtures)]
    registry = _registry(data_dir)
    ro = _RO(_db(data_dir))
    try:
        policy = ro.policy(_person(data_dir))
    finally:
        ro.close()
    if not all_agents:
        if not as_agent:
            raise GateCmdError("which agent? --as-agent A or --all-agents", 2)
        return replay_one(classified, as_agent, policy=policy, registry=registry, data_dir=data_dir, level=level)
    agents = {a: replay_one(classified, a, policy=policy, registry=registry, data_dir=data_dir, level=level)
              for a in sorted(_agents(registry))}
    return {"agents": agents, "held": sum(r["held"] for r in agents.values())}


# ---- orchestra gate retire-open -----------------------------------------------------------------------------------

def retire_open() -> dict:
    data_dir = _data_dir()
    _scripts()
    import gate_cards as GCD
    import supervision_store as SS
    from approval_schema import ApprovalStore
    ss = SS.SupervisionStore(db_path=_db(data_dir))
    ss.migrate()
    if not ss.armed:
        raise GateCmdError(f"the supervision store is not armed ({SS.MIGRATION_ID}); nothing to retire", 3)
    ledger = ApprovalStore(db_path=str(_db(data_dir)))
    out = {"holds_withdrawn": 0, "cards_retired": 0, "grant_cards_retired": 0, "grants_declined": 0}
    person = _person(data_dir)
    for h in ss.open_holds(states=OPEN_HOLD_STATES):
        if ss.transition_hold(h["id"], "withdrawn", (h["state"],)):
            out["holds_withdrawn"] += 1
            if h.get("card_id") and GCD.retire_card(ledger, h["card_id"], "gate retired (rollback)", by="cli"):
                out["cards_retired"] += 1
    c = ledger._conn()
    try:
        pending = [dict(r) for r in c.execute(
            "SELECT * FROM approval_requests WHERE origin=? AND status='pending' ORDER BY created_at, id",
            [GCD.ORIGIN]).fetchall()]
    finally:
        c.close()
    for row in pending:
        b = GCD.bridge_of(row) or {}
        if b.get("kind") == GCD.GRANT_BRIDGE_KIND:
            if GCD.retire_card(ledger, row["id"], "gate retired (rollback)", by="cli"):
                out["grant_cards_retired"] += 1
            gid = b.get("grant_id")
            g = ss.grant(gid) if gid else None
            if g and g.get("state") == "pending":
                try:
                    ss.decline_grant(gid, by="cli")
                    out["grants_declined"] += 1
                except (SS.Conflict, SS.NotFound):
                    pass
        elif b.get("kind") == GCD.BRIDGE_KIND:
            # a gate card whose hold already ended (or was never written): nobody can answer it usefully
            if GCD.retire_card(ledger, row["id"], "gate retired (rollback)", by="cli"):
                out["cards_retired"] += 1
    return out


# ---- dispatch -----------------------------------------------------------------------------------------------------

def cmd_gate(ns) -> int:
    try:
        if ns.gate_cmd == "mode":
            via = set_mode(ns.mode)
            print(f"gate mode {ns.mode} ({'through the gateway' if via == 'gateway' else 'gate.json written'})")
        elif ns.gate_cmd == "status":
            s = status()
            print(json.dumps(s, indent=1, default=str) if ns.json else render_status(s))
        elif ns.gate_cmd == "replay":
            print(json.dumps(replay(ns.fixtures, as_agent=ns.as_agent, all_agents=ns.all_agents, level=ns.level),
                             sort_keys=True))
        elif ns.gate_cmd == "retire-open":
            print(json.dumps(retire_open(), sort_keys=True))
        return 0
    except GateCmdError as e:
        print(f"orchestra gate {ns.gate_cmd}: {e}", file=sys.stderr)
        return e.code
    except Exception as e:  # noqa: BLE001 — busy and friends: exit 1, run it again
        if type(e).__name__ == "SupervisionBusy":
            print(f"orchestra gate {ns.gate_cmd}: {e}", file=sys.stderr)
            return 1
        if type(e).__name__ == "SupervisionNotArmed":
            print(f"orchestra gate {ns.gate_cmd}: {e}", file=sys.stderr)
            return 3
        raise
