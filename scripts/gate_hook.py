"""gate_hook.py — the PreToolUse safety gate's logic (attention part 2a, plan §5.2 / §6).

hooks/gate-hook.py (the installed row's script) reads stdin, checks GATE_OFF with os/sys only, then calls run().
The logic lives here, imported rather than run as __main__, so its bytecode is cached (no per-call compile).

The row FAILS OPEN (PM amendment): no onFailure, the standard `[ -f ] && … || exit 0` form, timeout 600; a hook
that crashes, is killed or times out lets the call run. Inside the hook the per-level rule holds: under Watch
closely a gateway that is down or an error the hook catches denies the call. A deny is one JSON object on stdout
({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", ...}}); an allow prints
NOTHING (an explicit allow would also skip Claude's own permission prompts); the exit status is always 0.

Flow:
  gate.json missing/unreadable -> mode off; not a registry agent -> allow
  coverage heartbeat (gate-seen/<agent>.json, at most once a minute); mode off -> allow
  classify (gate_classify); no hits -> allow                                   (the ~95 % path)
  settings + grants from ONE read-only sqlite connection (gate.json snapshot when the DB can't be read)
  held_hits empty -> allow (Let them run ends here); observe -> allow, logged
  enforce: a one-time retry pass -> allow; an `open` hold for (agent, fingerprint) whose card is still live ->
  deny "still held" (a dead card: expired / retired / discarded / resolved elsewhere -> that hold is withdrawn
  and a fresh one filed); gateway /health down -> per level; else hold + card (a compensating pair) and, by
  default (wait 0), deny at once with "carry on"; with a wait > 0, poll the hold and card every second until
  answered, covered by a new grant, switched off, or out of time.
Every call with hits writes one redacted line to gate-log.jsonl: its decision (allow, observe, held, still_held,
retry_pass, fail_open, gateway_down, hold_failed, allowed_in_place, denied_in_place, released, withdrawn,
interrupted, deadline, gate_error) and would_hold per level.

Signals: SIGTERM/SIGINT -> deny, and a filing/waiting hold is withdrawn and its card retired. SIGALRM (armed at
start, re-armed at wait + 15 s) -> the per-level answer, so the hook always answers before the 600 s timeout.
Test switches (env): GATE_TEST_POLL_S, GATE_TEST_STALL_POLL, GATE_TEST_CRASH, GATE_TEST_FAIL_CARD.
"""
import os
import sys

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))


class _Interrupted(BaseException):
    """SIGTERM / SIGINT: the turn was interrupted."""


class _Deadline(BaseException):
    """SIGALRM: the absolute deadline."""


class _G:
    """Per-invocation state the signal and error paths need."""
    data_dir = None
    gate = {}
    person = "operator"
    agent = None
    session_id = None
    tool_use_id = None
    tool = ""
    hits = []
    held = []
    level = None
    rec = None
    t0 = 0.0
    answered = False
    hold_id = None
    card_id = None
    ss = None
    ledger = None


START_ALARM_S = 30
MAX_WAIT_S = 540
DEADLINE_PAD_S = 15
HEARTBEAT_TURNS = 5
STILL_HELD_DEAD = ("expired", "retired", "discarded", "resolved_elsewhere")


def _emit(reason=None):
    """Answer once: a deny prints the JSON, an allow prints nothing."""
    if _G.answered:
        return
    _G.answered = True
    if reason is None:
        return
    import json
    out = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}})
    try:
        sys.stdout.write(out + "\n")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


def _finish(decision, reason=None, **extra):
    """Log the call's one line (only calls with hits are logged) and answer."""
    if _G.rec is not None and not _G.answered:
        import time
        import gate_config as GCF
        rec = dict(_G.rec, decision=decision, **extra)
        if _G.hold_id:
            rec.setdefault("hold_id", _G.hold_id)
        if _G.card_id:
            rec.setdefault("card_id", _G.card_id)
        rec["ms"] = round((time.monotonic() - _G.t0) * 1000, 1)
        GCF.log_line(_G.data_dir, rec)
    _emit(reason)


# ---- wording (§5.2 "Messages Claude sees") -----------------------------------------------------------------------

def _action(hits) -> str:
    seen = []
    for h in hits or ():
        s = f"{h.action}: {h.detail}" if h.detail else h.action
        if s not in seen:
            seen.append(s)
    return "; ".join(seen) or "this action"


def _held_carry_on(card_id) -> str:
    p, h0 = _G.person, (_G.held[0] if _G.held else None)
    cat = h0.category if h0 else "<category>"
    act = h0.action if h0 else "<action>"
    return (f"Held for {p}'s approval (card {card_id}): {_action(_G.held)}. Don't wait: treat it as a to-do and "
            f"carry on with everything else you can do that doesn't depend on it. You'll get a message when it's "
            f"answered: if allowed, retry this exact action (it will pass once); if not, drop it. If {p} tells you "
            f"in chat you may do this without asking, record it with `orchestra grant propose --category {cat} "
            f"--only {act} --said \"<their words>\"`; it takes effect when they confirm the card.")


def _still_held(card_id) -> str:
    return (f"Still held on card {card_id} ({_action(_G.held)}). Carry on with other work; you'll get a message "
            f"when it's answered.")


def _denied_in_place(card_id, words) -> str:
    w = (words or "").strip()
    said = f" They said: {w.rstrip('.')}." if w else ""
    return (f"Not allowed on the permission card ({card_id}).{said} Drop this action. Ask in chat if you need it.")


def _withdrawn(card_id) -> str:
    return (f"The permission request for {_action(_G.held)} (card {card_id}) was withdrawn before it was "
            f"answered. Carry on with other work; if you still need it, try again later and a new card will be "
            f"filed.")


GATEWAY_DOWN = ("Held: OrchestraOS can't show a permission card right now (the server isn't answering), and you're "
                "set to Watch closely. Carry on with other work and try again in a few minutes, or ask in chat.")
GATE_ERROR = ("Held: the OrchestraOS safety gate hit an error checking this action, and you're set to Watch "
              "closely. Carry on with other work and try again in a few minutes, or ask in chat.")
INTERRUPTED = "Interrupted while this action was held for approval; it did not run."


# ---- helpers ---------------------------------------------------------------------------------------------------

def _now_iso():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json(path):
    import json
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _identify(registry):
    """(agent id, registry row) or (None, None) for a session that isn't a managed agent. The name is
    $ORCHESTRA_AGENT_ID, else this pane's tmux session; matched exactly, then -genN stripped, then by
    tmux_session."""
    import gate_config as GCF
    name = (os.environ.get("ORCHESTRA_AGENT_ID") or "").strip()
    if not name:
        pane = os.environ.get("TMUX_PANE")
        if not (os.environ.get("TMUX") and pane):
            return None, None
        try:
            import subprocess
            name = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#S"], capture_output=True,
                                  text=True, timeout=2).stdout.strip()
        except Exception:  # noqa: BLE001
            return None, None
    if not name:
        return None, None
    agents = registry.get("agents") if isinstance(registry.get("agents"), dict) else {}
    for key in (name, GCF.base_agent(name)):
        if isinstance(agents.get(key), dict):
            return key, agents[key]
    for key, row in agents.items():
        if isinstance(row, dict) and row.get("tmux_session") == name:
            return key, row
    return None, None


def _package_scripts(d):
    import json
    try:
        with open(os.path.join(d, "package.json")) as fh:
            s = json.load(fh).get("scripts")
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError, AttributeError):
        return None


def _local_bins(d):
    try:
        return frozenset(os.listdir(os.path.join(d, "node_modules", ".bin")))
    except OSError:
        return frozenset()


class _RO:
    """ONE read-only sqlite connection (mode=ro) for settings, grants, retry passes and open holds."""

    def __init__(self, db_path):
        import sqlite3
        from urllib.parse import quote
        # the ledger is WAL (a reader never waits on a writer); a locked or busy file falls back to the gate.json
        # snapshot within 0.5 s instead of stalling the call
        self.c = sqlite3.connect(f"file:{quote(db_path)}?mode=ro", uri=True, timeout=0.5)
        self.c.row_factory = sqlite3.Row

    def close(self):
        try:
            self.c.close()
        except Exception:  # noqa: BLE001
            pass

    def policy(self, person):
        c = self.c
        rows = {r["agent"]: dict(r) for r in c.execute("SELECT * FROM supervision_settings WHERE person=?",
                                                         [person])}
        roles = [dict(r) for r in c.execute("SELECT * FROM supervision_roles WHERE person=? AND deleted_at IS NULL",
                                            [person])]
        asg = {(r["target_kind"], r["target"]): (r["role_id"], r["source"]) for r in c.execute(
            "SELECT target_kind, target, role_id, source FROM role_assignments WHERE person=?", [person])}
        grants = [dict(r) for r in c.execute("SELECT * FROM standing_grants WHERE person=? AND state='active'",
                                             [person])]
        return rows, roles, asg, grants

    def retry_ready(self, agent, fp) -> bool:
        return self.c.execute("SELECT 1 FROM gate_holds WHERE agent=? AND fingerprint=? AND state='allowed' "
                              "AND retry_until>? AND consumed_at IS NULL LIMIT 1",
                              [agent, fp, _now_iso()]).fetchone() is not None

    def open_hold(self, agent, fp):
        """(hold row, its card's status or None) for the newest open hold, or (None, None)."""
        r = self.c.execute("SELECT * FROM gate_holds WHERE agent=? AND fingerprint=? AND state='open' "
                           "ORDER BY created_at DESC, id DESC LIMIT 1", [agent, fp]).fetchone()
        if not r:
            return None, None
        st = None
        if r["card_id"]:
            try:
                x = self.c.execute("SELECT status FROM approval_requests WHERE id=?", [r["card_id"]]).fetchone()
                st = x["status"] if x else None
            except Exception:  # noqa: BLE001
                st = None
        return dict(r), st


def _resolve(ctx_args):
    """(Resolved, grants, role_names, from_db) — the DB first, the gate.json snapshot when it can't be read."""
    import gate_config as GCF
    import gate_policy as GP
    agent, project = ctx_args["agent"], ctx_args["project"]
    ro = ctx_args.get("ro")
    if ro is not None:
        try:
            rows, roles, asg, grants = ro.policy(_G.person)
            res = GP.resolve(rows, roles, asg, agent, project)
            return res, grants, {r["id"]: r.get("name") for r in roles}, True
        except Exception:  # noqa: BLE001 — unreadable: the snapshot, no grants (the safe side)
            pass
    s = GCF.settings_for(_G.gate, agent)
    res = GP.Resolved(s["level"], tuple(s["high_risk"]), min(s["hold_wait_s"], MAX_WAIT_S), s["role"],
                      "agent" if s["role"] else None, {"level": "snapshot"}, False)
    return res, [], {}, False


def _gateway_up(url) -> bool:
    try:
        import http.client
        from urllib.parse import urlsplit
        u = urlsplit(url or "http://127.0.0.1:8890")
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = cls(u.hostname or "127.0.0.1", u.port, timeout=0.5)
        try:
            conn.request("GET", u.path.rstrip("/") + "/health")
            return 200 <= conn.getresponse().status < 300
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return False


def _retire(reason):
    """Retire the hold's card (by id, else by its op_key). Never raises."""
    try:
        import gate_cards as GCD
        rid = _G.card_id
        if not rid and _G.ledger is not None and _G.hold_id:
            for row in _G.ledger.find_by_op_key(GCD.op_key(_G.hold_id)) or []:
                rid = row["id"]
                break
        if rid and _G.ledger is not None:
            GCD.retire_card(_G.ledger, rid, reason)
    except Exception:  # noqa: BLE001
        pass


def _fallback(why):
    """No card can be shown (gateway down, the hold or card write failed): per level (§6)."""
    if _G.level == "watch_closely":
        _finish("gateway_down" if why == "gateway_down" else "hold_failed", GATEWAY_DOWN, why=why)
    else:
        _finish("fail_open", None, why=why)


# ---- holding ---------------------------------------------------------------------------------------------------

def _follow(h, wait_s, policy_args):
    """Act on a hold's current state."""
    st = (h or {}).get("state")
    _G.card_id = (h or {}).get("card_id") or _G.card_id
    if st == "open":
        return _finish("held", _held_carry_on(_G.card_id))
    if st == "waiting":
        return _wait(h, wait_s, policy_args)
    if st in ("allowed", "released"):
        return _finish("allowed_in_place" if st == "allowed" else "released", None)
    if st == "denied":
        words = None
        try:
            row = _G.ledger.get(_G.card_id) if _G.card_id else None
            words = (row or {}).get("answer_text")
        except Exception:  # noqa: BLE001
            pass
        return _finish("denied_in_place", _denied_in_place(_G.card_id, words))
    if st == "withdrawn":
        return _finish("withdrawn", _withdrawn(_G.card_id))
    raise RuntimeError(f"hold {(h or {}).get('id')} in unexpected state {st!r}")


def _sleep(s):
    import time
    end = time.monotonic() + s
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.2))


def _wait(h, wait_s, policy_args):
    """The in-place wait (hold_wait_s > 0): poll every second; heartbeat CAS and grant re-check every 5th turn."""
    import signal
    import time
    import gate_cards as GCD
    import gate_config as GCF
    import gate_policy as GP
    ss, hid = _G.ss, h["id"]
    signal.alarm(min(wait_s, MAX_WAIT_S) + DEADLINE_PAD_S)
    poll = float(os.environ.get("GATE_TEST_POLL_S") or 1.0)
    start, turn, errors = time.monotonic(), 0, 0
    while True:
        if os.environ.get("GATE_TEST_STALL_POLL") == "1":
            _sleep(3600)
        _sleep(poll)
        turn += 1
        try:
            if GCF.kill_switch(_G.data_dir) or GCF.read_gate(_G.data_dir).get("mode") == "off":
                if ss.transition_hold(hid, "released", ("waiting",), decision="gate_off"):
                    _retire("the safety gate was switched off")
                    return _finish("released", None, why="gate_off")
                return _follow(ss.hold(hid), wait_s, policy_args)
            cur = ss.hold(hid) or {}
            if cur.get("state") != "waiting":
                return _follow(cur, wait_s, policy_args)
            card = _G.ledger.get(_G.card_id) if _G.card_id else None
            dec = GCD.decision_of(card) if card and card.get("status") != "pending" else None
            if dec:
                to = "allowed" if dec.startswith("allow") else "denied"
                ss.transition_hold(hid, to, ("waiting",), decision=dec, notified_at=_now_iso())
                return _follow(ss.hold(hid), wait_s, policy_args)
            if turn % HEARTBEAT_TURNS == 0:
                if not ss.heartbeat(hid):
                    return _follow(ss.hold(hid), wait_s, policy_args)
                ro = None
                try:
                    ro = _RO(policy_args["db"])
                    res, grants, _names, from_db = _resolve(dict(policy_args, ro=ro))
                finally:
                    if ro is not None:
                        ro.close()
                if from_db and not GP.held_hits(_G.held, resolved=res, grants=grants, agent=_G.agent,
                                                session_id=_G.session_id, project=policy_args["project"]):
                    if ss.transition_hold(hid, "released", ("waiting",), decision="covered"):
                        _retire("a standing permission now covers it")
                        return _finish("released", None, why="covered")
                    return _follow(ss.hold(hid), wait_s, policy_args)
            if time.monotonic() - start >= wait_s:
                ss.transition_hold(hid, "open", ("waiting",))
                return _follow(ss.hold(hid), wait_s, policy_args)
            errors = 0
        except (_Interrupted, _Deadline):
            raise
        except Exception:  # noqa: BLE001 — a transient DB error: try again; ten in a row is a real failure
            errors += 1
            if errors >= 10:
                raise


def _hold(resolved, role_names, project, fp, policy_args):
    import supervision_store as SS
    import gate_cards as GCD
    from approval_schema import ApprovalStore
    db = policy_args["db"]
    wait_s = min(max(0, int(resolved.hold_wait_s or 0)), MAX_WAIT_S)
    try:
        _G.ss = SS.SupervisionStore(db_path=db)
        _G.ledger = ApprovalStore(db_path=db)
        hid = _G.ss.create_hold(agent=_G.agent, tool=_G.tool, fingerprint=fp, hits=[x.to_dict() for x in _G.held],
                                level=resolved.level, session_id=_G.session_id, tool_use_id=_G.tool_use_id)
        _G.hold_id = hid
        h = _G.ss.hold(hid)
        if h and h.get("state") == "filing":
            if os.environ.get("GATE_TEST_FAIL_CARD") == "1":
                raise RuntimeError("test: card filing failed")
            rid = GCD.file_card(_G.ledger, hid, agent=_G.agent, hits=_G.held, resolved=resolved,
                                role_names=role_names, project=project)
            _G.card_id = rid
            to = "waiting" if wait_s > 0 else "open"
            fields = {"card_id": rid}
            if to == "waiting":
                fields["hook_seen_at"] = _now_iso()
            _G.ss.transition_hold(hid, to, ("filing",), **fields)
            h = _G.ss.hold(hid)
    except (_Interrupted, _Deadline):
        raise
    except Exception as e:  # noqa: BLE001 — the compensating pair: withdraw, retire any card, then per level
        try:
            if _G.ss is not None and _G.hold_id:
                _G.ss.transition_hold(_G.hold_id, "withdrawn", ("filing",))
        except Exception:  # noqa: BLE001
            pass
        _retire("the card could not be filed")
        _G.card_id = None
        return _fallback(f"hold_failed: {type(e).__name__}")
    if h and h.get("state") == "filing":
        # another process holds the same tool call and is still filing: give it a moment, then follow
        import time
        for _ in range(30):
            time.sleep(0.1)
            h = _G.ss.hold(_G.hold_id)
            if h and h.get("state") != "filing":
                break
        else:
            return _fallback("hold_stuck_filing")
    return _follow(h, wait_s, policy_args)


# ---- the gate ----------------------------------------------------------------------------------------------------

def _on_term(signum, frame):
    raise _Interrupted()


def _on_alarm(signum, frame):
    raise _Deadline()


def _gate(raw, data_dir):
    import json
    import signal
    import time
    _G.t0 = time.monotonic()
    _G.data_dir = data_dir
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(START_ALARM_S)
    import gate_config as GCF
    try:
        data = json.loads(raw) if raw and raw.strip() else {}
    except ValueError:
        return _emit(None)
    if not isinstance(data, dict) or data.get("hook_event_name") not in (None, "", "PreToolUse"):
        return _emit(None)
    gate = GCF.read_gate(data_dir)
    _G.gate = gate
    mode = gate.get("mode", "off")
    p = gate.get("person")
    _G.person = p.strip() if isinstance(p, str) and p.strip() else "operator"
    registry = _load_json(os.path.join(data_dir, "registry.json"))
    agent, row = _identify(registry)
    if agent is None:
        return _emit(None)                                 # not a managed agent: never gated
    _G.agent = agent
    _G.session_id = data.get("session_id")
    _G.tool_use_id = data.get("tool_use_id")
    GCF.heartbeat(data_dir, agent, _G.session_id, mode)
    if mode == "off":
        return _emit(None)

    import gate_classify as GC
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    _G.tool = tool
    reg_cwd = row.get("cwd") if isinstance(row.get("cwd"), str) and row.get("cwd") else None
    cwd = reg_cwd or (data.get("cwd") if isinstance(data.get("cwd"), str) else None) or "/"
    scratch = ["/tmp"]
    if isinstance(data.get("scratchpad_dir"), str) and data["scratchpad_dir"]:
        scratch.append(data["scratchpad_dir"])
    loop = gate.get("loopback_hosts")
    loop = tuple(h for h in loop if isinstance(h, str)) if isinstance(loop, list) and loop else \
        ("127.0.0.1", "localhost", "::1")
    _cache = {}

    def scripts_loader(d):
        if d not in _cache:
            _cache[d] = _package_scripts(d)
        return _cache[d]

    ctx = GC.CallCtx(tool=tool, tool_input=tool_input, cwd=cwd,
                     own_dirs=(cwd, os.path.join(data_dir, "state"), os.path.join(data_dir, ".workspace")),
                     scratch_dirs=tuple(scratch), home=os.path.expanduser("~"), local_bins=_local_bins(cwd),
                     package_scripts=scripts_loader(cwd), loopback_hosts=loop, scripts_loader=scripts_loader)
    hits = GC.classify(ctx)
    if not hits:
        return _emit(None)                                 # the ~95 % path

    import gate_policy as GP
    _G.hits = hits
    _G.level = GCF.settings_for(gate, agent)["level"]       # until the DB answers (the crash rule needs a level)
    fp = GC.fingerprint(tool, tool_input)
    _G.rec = {"agent": agent, "session_id": _G.session_id, "tool_use_id": _G.tool_use_id, "tool": tool,
              "mode": mode, "hits": [[h.category, h.action] for h in hits], "detail": GP.action_text(hits),
              "certain": all(h.certain for h in hits), "fp": fp[:16]}

    project = GCF.project_key(reg_cwd, data_dir) if reg_cwd else None
    db = os.path.join(data_dir, "state", "tasks.db")
    policy_args = {"agent": agent, "project": project, "db": db}
    ro = None
    try:
        try:
            ro = _RO(db)
        except Exception:  # noqa: BLE001
            ro = None
        resolved, grants, role_names, from_db = _resolve(dict(policy_args, ro=ro))
        _G.level = resolved.level
        if os.environ.get("GATE_TEST_CRASH") == "1":
            raise RuntimeError("test: crash after classify")
        held = GP.held_hits(hits, resolved=resolved, grants=grants, agent=agent, session_id=_G.session_id,
                            project=project)
        _G.held = held
        _G.rec.update({"level": resolved.level, "role": resolved.role_id,
                       "level_source": (resolved.sources or {}).get("level"), "settings_from": "db" if from_db
                       else "snapshot", "held": [[h.category, h.action] for h in held],
                       "would_hold": GP.would_hold(hits, grants, agent=agent, session_id=_G.session_id,
                                                   project=project, resolved=resolved)})
        if not held:
            return _finish("allow")                        # under Let them run every call ends here
        if mode != "enforce":
            return _finish("observe")
        retry = existing = None
        card_status = None
        if ro is not None:
            try:
                retry = ro.retry_ready(agent, fp)
                existing, card_status = ro.open_hold(agent, fp)
            except Exception:  # noqa: BLE001 — no holds table readable: file a fresh one (or fall back)
                retry = existing = None
    finally:
        if ro is not None:
            ro.close()

    if retry:
        import supervision_store as SS
        try:
            hid = SS.SupervisionStore(db_path=db).take_retry(agent, fp)
        except (_Interrupted, _Deadline):
            raise
        except Exception:  # noqa: BLE001
            hid = None
        if hid:
            return _finish("retry_pass", None, hold_id=hid)
    if existing:
        if existing.get("card_id") and card_status not in STILL_HELD_DEAD and card_status is not None:
            _G.card_id = existing["card_id"]
            return _finish("still_held", _still_held(existing["card_id"]), hold_id=existing["id"])
        # its card expired, was retired or dismissed: nobody can answer it any more -> withdraw, file afresh
        import supervision_store as SS
        try:
            SS.SupervisionStore(db_path=db).transition_hold(existing["id"], "withdrawn", ("open",))
            _G.rec["replaced_hold"] = existing["id"]
        except (_Interrupted, _Deadline):
            raise
        except Exception:  # noqa: BLE001
            pass
    if not _gateway_up(gate.get("gateway_url")):
        return _fallback("gateway_down")
    return _hold(resolved, role_names, project, fp, policy_args)


def _cleanup_then(decision, reason, to_state, from_states, retire_reason, keep_allowed=False):
    """Signal/error paths: move a live hold (best effort, guarded by a hard 3 s alarm), then answer. keep_allowed:
    a hold this hook already moved to `allowed` in place (answered just before the signal) gets a one-time retry
    pass, so the answer is not lost although this call is denied."""
    import json
    import signal
    out = b""
    if reason is not None:
        out = (json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                  "permissionDecisionReason": reason}}) + "\n").encode()

    def hard(signum, frame):
        if not _G.answered:
            _G.answered = True
            try:
                os.write(1, out)
            except OSError:
                pass
        os._exit(0)

    try:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGALRM, hard)
        signal.alarm(3)
    except Exception:  # noqa: BLE001
        pass
    try:
        if _G.ss is not None and _G.hold_id and to_state:
            h = _G.ss.hold(_G.hold_id) or {}
            if h.get("state") in from_states:
                _G.card_id = h.get("card_id") or _G.card_id
                if _G.ss.transition_hold(_G.hold_id, to_state, tuple(from_states)) and retire_reason:
                    _retire(retire_reason)
            elif keep_allowed and not _G.answered and h.get("state") == "allowed" and not h.get("retry_until") \
                    and not h.get("consumed_at"):                 # not when this hook already answered allow
                import gate_cards as GCD
                from datetime import datetime, timezone
                until = (datetime.now(timezone.utc) + GCD.RETRY_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
                _G.ss.set_hold(_G.hold_id, retry_until=until)
    except BaseException:  # noqa: BLE001
        pass
    try:
        _finish(decision, reason)
    except BaseException:  # noqa: BLE001
        _emit(reason)
    try:
        signal.alarm(0)
    except Exception:  # noqa: BLE001
        pass


def run(raw: str, data_dir: str) -> int:
    """The gate for one PreToolUse call (the kill switch was already checked by the entry script). Always 0."""
    try:
        _gate(raw, data_dir)
    except _Interrupted:
        _cleanup_then("interrupted", INTERRUPTED, "withdrawn", ("filing", "waiting"),
                      "the agent's turn was interrupted", keep_allowed=True)
    except _Deadline:
        deny = _G.level == "watch_closely" and bool(_G.hits)
        if deny:
            reason = _held_carry_on(_G.card_id) if _G.card_id else GATE_ERROR
            _cleanup_then("deadline", reason, "open", ("waiting",), None)
        else:
            _cleanup_then("deadline", None, "withdrawn", ("filing", "waiting"), "the safety gate ran out of time")
    except Exception as e:  # noqa: BLE001 — §6: deny under Watch closely when there were hits, else allow
        deny = _G.level == "watch_closely" and bool(_G.hits)
        try:
            print(f"[gate-hook] error: {type(e).__name__}: {e}", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
        _cleanup_then("gate_error", GATE_ERROR if deny else None, "withdrawn", ("filing", "waiting"),
                      "the safety gate hit an error")
    finally:
        try:
            import signal
            signal.alarm(0)
        except Exception:  # noqa: BLE001
            pass
    if not _G.answered:
        _emit(None)
    return 0
