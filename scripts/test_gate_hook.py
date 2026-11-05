"""hooks/gate-hook.py, run as a subprocess the way Claude Code runs it (attention part 2a, Task 5).

Every test uses a scratch data dir (registry.json, an armed state/tasks.db, state/attention/gate.json) and a fake
gateway (a stdlib HTTP server on a free port serving /health) whose URL goes in gate.json.
"""
import json
import os
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HOOK = REPO / "hooks" / "gate-hook.py"
sys.path.insert(0, str(HERE))
import gate_cards as GCD  # noqa: E402
import gate_config as GCF  # noqa: E402
import supervision_store as SSM  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402
from supervision_store import SupervisionStore  # noqa: E402

PERSON = "operator"


def BASH(cmd, tool_use="t1", session="s1"):
    return {"hook_event_name": "PreToolUse", "session_id": session, "tool_use_id": tool_use, "cwd": "/x",
            "tool_name": "Bash", "tool_input": {"command": cmd}}


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *a):
        pass


class _Proc:
    def __init__(self, p, t0):
        self.p, self.t0 = p, t0

    def terminate(self):
        self.p.terminate()

    def result(self, timeout=10):
        out, _err = self.p.communicate(timeout=timeout)
        assert self.p.returncode == 0, _err
        out = out.strip()
        return json.loads(out) if out else None


class Box:
    def __init__(self, tmp_path, monkeypatch):
        self.data = tmp_path / "data"
        (self.data / "state" / "attention").mkdir(parents=True)
        self.proj = self.data / "projects" / "proj"
        self.proj.mkdir(parents=True)
        (self.data / "registry.json").write_text(json.dumps({"agents": {
            "dev": {"cwd": str(self.proj), "tmux_session": "dev", "runtime": "claude"}}}))
        monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
        monkeypatch.setenv("ORCHESTRA_DIR", str(self.data))
        monkeypatch.setenv("GATE_CARDS_NOTIFY", "0")
        self.db = self.data / "state" / "tasks.db"
        self.ledger = ApprovalStore(db_path=str(self.db))
        self.ledger.migrate()
        self.ss = SupervisionStore(db_path=self.db)
        self.ss.migrate()
        self.project = SSM.project_key(str(self.proj), str(self.data))
        self.ss.seed(PERSON, [self.project])
        self.sent = []
        monkeypatch.setattr(GCD, "_default_send", self.sent.append)
        self.extra = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        GCF.write_gate(self.data, mode="off", person=PERSON, gateway_url=f"http://127.0.0.1:{self.port}",
                       loopback_hosts=["127.0.0.1", "localhost", "::1"],
                       default={"level": "let_them_run", "high_risk": [], "hold_wait_s": 0}, agents={}, gen=1)

    # ---- files ----
    def p(self, rel):
        return self.data / rel

    def touch(self, rel):
        self.p(rel).parent.mkdir(parents=True, exist_ok=True)
        self.p(rel).touch()

    def rm(self, rel):
        self.p(rel).unlink()

    def corrupt(self, rel):
        self.p(rel).write_text("{not json")

    def mode(self, m):
        GCF.write_gate(self.data, mode=m)

    def snapshot(self, agent, **s):
        g = GCF.read_gate_raw(self.data)
        agents = dict(g.get("agents") or {})
        agents[agent] = dict({"level": "let_them_run", "high_risk": [], "hold_wait_s": 0, "role": None}, **s)
        GCF.write_gate(self.data, agents=agents)

    # ---- settings ----
    def role_id(self, name):
        return next(r["id"] for r in self.ss.roles(PERSON) if r["name"] == name)

    def role(self, agent, name):
        self.ss.assign(PERSON, "agent", agent, self.role_id(name), by=PERSON)

    def set_default(self, **kw):
        self.ss.set_default(PERSON, by=PERSON, **kw)

    def seed_defaults(self):
        self.ss.seed(PERSON, [self.project])          # idempotent: Let them run, Hands-off projects, no agent role

    def agent_wait(self, agent, s):
        self.ss.set_agent(PERSON, agent, by=PERSON, hold_wait_s=s)

    def agent_override(self, agent, **kw):
        self.ss.set_agent(PERSON, agent, by=PERSON, **kw)

    def grant(self, *, scope, ref, category, action=None):
        return self.ss.create_grant(PERSON, scope=scope, scope_ref=ref, category=category, source="settings",
                                    by=PERSON, narrow={"action": action} if action else None)

    def stop_gateway(self):
        self.server.shutdown()
        self.server.server_close()

    def env_extra(self, **kw):
        self.extra.update(kw)

    # ---- reads ----
    def cards(self):
        c = sqlite3.connect(self.db)
        try:
            return [r[0] for r in c.execute("SELECT id FROM approval_requests WHERE origin='gate' ORDER BY rowid")]
        finally:
            c.close()

    def only_card(self):
        cs = self.cards()
        assert len(cs) == 1, cs
        return cs[0]

    def wait_for_card(self, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            cs = self.cards()
            if cs:
                rid = cs[-1]
                h = self.ss.hold_by_card(rid)
                if h and h["state"] in ("waiting", "open"):
                    return rid
            time.sleep(0.05)
        raise AssertionError("no card filed")

    def hold_by_card(self, rid):
        return self.ss.hold_by_card(rid)

    def answer(self, rid, n, text=None):
        import watch_gateway as WG
        res = WG.apply_answer(self.ledger, rid, "option", option_n=n, answer_text=text, fire=True,
                              answered_by="p", surface="web")
        assert res["ok"] and res["applied"], res
        return res

    def messages(self, agent):
        return [m for m in self.sent if m["to_agent"] == agent]

    def log(self):
        return GCF.read_log(self.data, limit=0)

    def env(self, agent="dev", tmux=None):
        e = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "ORCHESTRA_AGENT_ID")}
        e.update({"ORCHESTRA_DIR": str(self.data), "APPROVAL_DDL_ARMED": "all", "GATE_CARDS_NOTIFY": "0"})
        if agent:
            e["ORCHESTRA_AGENT_ID"] = agent
        if tmux:
            e["TMUX"], e["TMUX_PANE"] = tmux, "%0"
        e.update(self.extra)
        return e


@pytest.fixture
def box(tmp_path, monkeypatch):
    b = Box(tmp_path, monkeypatch)
    yield b
    try:
        b.server.shutdown()
        b.server.server_close()
    except Exception:  # noqa: BLE001
        pass


def start(inp, env, argv_prefix=()):
    t0 = time.monotonic()
    p = subprocess.Popen([sys.executable, *argv_prefix, str(HOOK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env)
    p.stdin.write(json.dumps(inp))
    p.stdin.close()
    p.stdin = None
    return _Proc(p, t0)


def run(inp, env, timeout=10):
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(inp), capture_output=True, text=True, env=env,
                       timeout=timeout)
    ms = (time.monotonic() - t0) * 1000
    assert r.returncode == 0, r.stderr
    out = r.stdout.strip()
    return (json.loads(out) if out else None), ms


def decision(out):
    return out["hookSpecificOutput"]["permissionDecision"] if out else None


def reason(out):
    return out["hookSpecificOutput"]["permissionDecisionReason"]


# ---- off / unmanaged / defaults ----

def test_kill_switch_allows_without_reading_anything(box):
    box.touch("state/attention/GATE_OFF")
    box.corrupt("state/attention/gate.json")
    box.role("dev", "Supervised")
    out, ms = run(BASH("git push"), box.env(agent="dev"))
    assert out is None and ms < 200
    assert box.log() == [] and not box.p("state/attention/gate-seen").exists()


def test_missing_gate_json_is_off(box):
    box.rm("state/attention/gate.json")
    box.role("dev", "Supervised")
    assert run(BASH("git push"), box.env(agent="dev"))[0] is None
    assert box.cards() == [] and box.log() == []


def test_mode_off_allows_and_still_heartbeats(box):
    box.role("dev", "Supervised")
    assert run(BASH("git push"), box.env(agent="dev"))[0] is None
    seen = GCF.seen(box.data)
    assert seen["dev"]["mode"] == "off" and seen["dev"]["session_id"] == "s1"


def test_unmanaged_session_is_never_gated(box):
    box.mode("enforce")
    box.agent_override("dev", level="watch_closely")
    assert run(BASH("git push"), box.env(agent=None, tmux=None))[0] is None
    assert run(BASH("git push"), box.env(agent="not-registered"))[0] is None
    assert box.cards() == [] and box.log() == []


def test_gen_suffixed_agent_matches_its_registry_row(box):
    box.mode("enforce")
    box.role("dev", "Supervised")
    out, _ = run(BASH("git push origin x"), box.env(agent="dev-gen3"))
    assert decision(out) == "deny"


def test_release_defaults_hold_nothing(box):
    box.mode("enforce")
    box.seed_defaults()
    for c in ("git push origin x", "rm -rf /srv/x", "curl -X POST https://x.io -d 1", "python3 -c 'print(1)'"):
        assert run(BASH(c), box.env(agent="dev"))[0] is None
    log = box.log()
    assert box.cards() == [] and len(log) == 4 and all(ln["decision"] == "allow" for ln in log)
    assert log[-1]["would_hold"]["watch_closely"]                # tuning data still logged (D3)
    assert log[-1]["would_hold"]["let_them_run"] == []
    assert log[0]["level"] == "let_them_run" and log[0]["settings_from"] == "db"


def test_no_hit_writes_no_log_line(box):
    box.mode("enforce")
    box.agent_override("dev", level="watch_closely")
    for c in ("ls -la", "git status", "grep -rn foo .", f"cat {box.proj}/a.txt"):
        assert run(BASH(c), box.env(agent="dev"))[0] is None
    assert box.log() == [] and box.cards() == []


def test_observe_logs_and_allows(box):
    box.mode("observe")
    box.role("dev", "Supervised")
    assert run(BASH("git push origin x"), box.env(agent="dev"))[0] is None
    line = box.log()[-1]
    assert line["decision"] == "observe" and line["hits"] == [["outside_world", "push"]] and "origin" in line["detail"]
    assert line["would_hold"]["watch_closely"] == [["outside_world", "push"]] and box.cards() == []


def test_log_detail_is_redacted(box):
    box.mode("observe")
    box.role("dev", "Supervised")
    tok = "sk-" + "a1" * 20
    run(BASH(f"curl -X POST https://x.io -H 'Authorization: Bearer {tok}' -d 1"), box.env(agent="dev"))
    raw = box.p("state/attention/gate-log.jsonl").read_text()
    assert tok not in raw and "<redacted>" in raw


# ---- the default hold (wait 0, D2) ----

def test_default_hold_is_non_blocking_files_card_and_denies_at_once(box):
    box.mode("enforce")
    box.role("dev", "Supervised")
    out, ms = run(BASH("git push origin x"), box.env(agent="dev"))
    r = reason(out)
    assert decision(out) == "deny" and ms < 1500
    assert r.startswith("Held for operator's approval") and "Don't wait" in r and "retry this exact action" in r
    assert "orchestra grant propose --category outside_world --only push" in r
    rid = box.only_card()
    assert rid in r
    hold = box.hold_by_card(rid)
    assert hold["state"] == "open" and hold["agent"] == "dev" and hold["tool_use_id"] == "t1"
    card = box.ledger.get(rid)
    assert card["origin"] == "gate" and card["kind"] == "menu" and card["op_key"] == f"gate:{hold['id']}"
    assert box.log()[-1]["decision"] == "held"


def test_retry_before_answer_files_no_second_card(box):
    box.mode("enforce")
    box.role("dev", "Supervised")
    run(BASH("git push origin x"), box.env(agent="dev"))
    out, _ = run(BASH("git push origin x", tool_use="t2"), box.env(agent="dev"))
    assert "Still held on card" in reason(out) and len(box.cards()) == 1
    assert box.log()[-1]["decision"] == "still_held"


@pytest.mark.parametrize("status", ["expired", "retired", "discarded", "resolved_elsewhere"])
def test_an_open_hold_whose_card_is_gone_is_withdrawn_and_filed_afresh(box, status):
    box.mode("enforce")
    box.role("dev", "Supervised")
    run(BASH("git push origin x"), box.env(agent="dev"))
    old = box.only_card()
    old_hold = box.hold_by_card(old)
    c = sqlite3.connect(box.db)
    c.execute("UPDATE approval_requests SET status=? WHERE id=?", [status, old])
    c.commit()
    c.close()
    out, _ = run(BASH("git push origin x", tool_use="t2"), box.env(agent="dev"))
    assert reason(out).startswith("Held for") and "Still held" not in reason(out)
    cards = box.cards()
    assert len(cards) == 2 and cards[-1] in reason(out)
    assert box.ss.hold(old_hold["id"])["state"] == "withdrawn"
    assert box.hold_by_card(cards[-1])["state"] == "open"
    assert box.log()[-1]["replaced_hold"] == old_hold["id"]


def test_after_allow_the_retry_passes_exactly_once(box):
    box.mode("enforce")
    box.role("dev", "Supervised")
    run(BASH("git push"), box.env(agent="dev"))
    rid = box.only_card()
    box.answer(rid, "1")
    assert box.messages("dev")[-1]["type"] == "gate_answer"
    assert run(BASH("git push", tool_use="t2"), box.env(agent="dev"))[0] is None
    assert box.log()[-1]["decision"] == "retry_pass"
    out, _ = run(BASH("git push", tool_use="t3"), box.env(agent="dev"))       # a new hold, held again
    assert decision(out) == "deny" and len(box.cards()) == 2


def test_same_tool_use_twice_reuses_one_hold(box):
    box.mode("enforce")
    box.role("dev", "Supervised")
    a = start(BASH("git push"), box.env(agent="dev"))
    b = start(BASH("git push"), box.env(agent="dev"))
    oa, ob = a.result(), b.result()
    assert decision(oa) == "deny" and decision(ob) == "deny"
    assert len(box.cards()) == 1
    c = sqlite3.connect(box.db)
    assert c.execute("SELECT COUNT(*) FROM gate_holds").fetchone()[0] == 1
    c.close()


# ---- the in-place wait (wait > 0) ----

def _waiting(box, wait=30, cmd="git push origin x"):
    box.mode("enforce")
    box.role("dev", "Supervised")
    box.agent_wait("dev", wait)
    box.env_extra(GATE_TEST_POLL_S="0.2")
    p = start(BASH(cmd), box.env(agent="dev"))
    return p, box.wait_for_card()


def test_in_place_wait_allow_once(box):
    p, rid = _waiting(box)
    assert box.hold_by_card(rid)["state"] == "waiting"
    box.answer(rid, "1")
    assert p.result(timeout=5) is None and box.hold_by_card(rid)["decision"] == "allow_once"
    assert box.messages("dev") == []                          # answered while waiting: no message


def test_in_place_wait_deny_carries_words(box):
    p, rid = _waiting(box)
    box.answer(rid, "5", text="not today")
    out = p.result(timeout=5)
    assert decision(out) == "deny" and "not today" in reason(out) and rid in reason(out)
    assert box.messages("dev") == []


def test_in_place_wait_answer_without_the_lane_is_followed(box):
    """The card answered but the lane hasn't run (fire=False): the hook reads the card and CASes the hold."""
    import watch_gateway as WG
    p, rid = _waiting(box)
    res = WG.apply_answer(box.ledger, rid, "option", option_n="1", fire=False, answered_by="p", surface="web")
    assert res["ok"]
    assert p.result(timeout=5) is None
    h = box.hold_by_card(rid)
    assert h["state"] == "allowed" and h["decision"] == "allow_once" and h["notified_at"]


def test_in_place_wait_runs_out_then_carry_on(box):
    box.mode("enforce")
    box.role("dev", "Supervised")
    box.agent_wait("dev", 2)
    out, ms = run(BASH("git push"), box.env(agent="dev"))
    assert reason(out).startswith("Held for") and 1800 < ms < 6000
    assert box.hold_by_card(box.only_card())["state"] == "open"


def test_new_grant_releases_a_waiting_hold(box):
    p, rid = _waiting(box, cmd="git push")
    box.grant(scope="agent", ref="dev", category="outside_world", action="push")
    assert p.result(timeout=8) is None and box.hold_by_card(rid)["state"] == "released"
    assert box.ledger.get(rid)["status"] in ("retired", "resolved_elsewhere")


def test_kill_switch_during_wait_releases(box):
    p, rid = _waiting(box)
    box.touch("state/attention/GATE_OFF")
    assert p.result(timeout=5) is None
    h = box.hold_by_card(rid)
    assert h["state"] == "released" and h["decision"] == "gate_off"
    assert box.ledger.get(rid)["status"] in ("retired", "resolved_elsewhere")


def test_mode_off_during_wait_releases(box):
    p, rid = _waiting(box)
    box.mode("off")
    assert p.result(timeout=5) is None and box.hold_by_card(rid)["state"] == "released"


def test_sigterm_denies_withdraws_and_retires_card(box):
    p, rid = _waiting(box)
    p.terminate()
    out = p.result(timeout=5)
    assert decision(out) == "deny"                                            # review 2
    assert box.hold_by_card(rid)["state"] == "withdrawn"
    assert box.ledger.get(rid)["status"] in ("retired", "resolved_elsewhere")


def test_swept_hold_turns_into_deny_not_allow(box):
    p, rid = _waiting(box)
    box.ss.sweep_stale(now_offset_s=120, stale_s=30)                          # simulate a missed heartbeat
    out = p.result(timeout=8)
    assert decision(out) == "deny" and "withdrawn" in reason(out)


def test_alarm_deadline_answers_before_timeout(box):
    box.mode("enforce")
    box.role("dev", "Supervised")
    box.agent_wait("dev", 2)
    box.env_extra(GATE_TEST_STALL_POLL="1")
    out, ms = run(BASH("git push"), box.env(agent="dev"), timeout=30)
    assert decision(out) == "deny" and ms < 20_000
    assert box.hold_by_card(box.only_card())["state"] == "open"                 # the card stays; carry on
    assert box.log()[-1]["decision"] == "deadline"


# ---- fail-safe per level (§6) ----

@pytest.mark.parametrize("level,expect_deny", [("watch_closely", True), ("check_in", False), ("let_them_run", False)])
def test_gateway_down_fails_safe_per_level(box, level, expect_deny):
    box.mode("enforce")
    box.agent_override("dev", level=level, high_risk=["outside_world"])
    box.stop_gateway()
    out, ms = run(BASH("git push"), box.env(agent="dev"))
    assert (decision(out) == "deny") == expect_deny
    assert ms < 1500 and box.cards() == []
    if expect_deny:
        assert "server isn't answering" in reason(out)
    if level == "check_in":
        assert box.log()[-1]["decision"] == "fail_open"


@pytest.mark.parametrize("level,expect_deny", [("watch_closely", True), ("check_in", False), ("let_them_run", False)])
def test_crash_fails_safe_per_level(box, level, expect_deny):
    box.mode("enforce")
    box.agent_override("dev", level=level, high_risk=["outside_world"])
    box.env_extra(GATE_TEST_CRASH="1")
    out, _ = run(BASH("git push"), box.env(agent="dev"))
    assert (decision(out) == "deny") == expect_deny
    assert box.log()[-1]["decision"] == "gate_error"
    box.env_extra(GATE_TEST_CRASH="0")
    assert run(BASH("ls"), dict(box.env(agent="dev"), GATE_TEST_CRASH="1"))[0] is None   # no hits: never crashes


def test_observe_never_denies_even_when_the_gate_crashes(box):
    box.mode("observe")
    box.agent_override("dev", level="watch_closely", high_risk=["outside_world"])
    box.env_extra(GATE_TEST_CRASH="1")
    out, _ = run(BASH("git push"), box.env(agent="dev"))
    assert decision(out) != "deny"
    box.env_extra(GATE_TEST_CRASH="0")


@pytest.mark.parametrize("level,expect_deny", [("watch_closely", True), ("check_in", False)])
def test_card_failure_withdraws_hold_and_fails_safe_per_level(box, level, expect_deny):
    box.mode("enforce")
    box.agent_override("dev", level=level, high_risk=["outside_world"])
    box.env_extra(GATE_TEST_FAIL_CARD="1")
    out, _ = run(BASH("git push"), box.env(agent="dev"))
    assert (decision(out) == "deny") == expect_deny
    c = sqlite3.connect(box.db)
    states = [r[0] for r in c.execute("SELECT state FROM gate_holds")]
    c.close()
    assert states == ["withdrawn"] and box.cards() == []


def test_unreadable_db_falls_back_to_the_snapshot(box):
    box.mode("observe")
    box.snapshot("dev", level="watch_closely")
    os.chmod(box.db, 0)
    try:
        assert run(BASH("git push"), box.env(agent="dev"))[0] is None
    finally:
        os.chmod(box.db, stat.S_IRUSR | stat.S_IWUSR)
    line = box.log()[-1]
    assert line["settings_from"] == "snapshot" and line["level"] == "watch_closely" and line["decision"] == "observe"


def test_snapshot_watch_closely_with_no_db_denies_when_the_hold_cannot_be_written(box):
    box.mode("enforce")
    box.snapshot("dev", level="watch_closely")
    os.chmod(box.db, 0)
    try:
        out, _ = run(BASH("git push"), box.env(agent="dev"))
    finally:
        os.chmod(box.db, stat.S_IRUSR | stat.S_IWUSR)
    assert decision(out) == "deny"


# ---- classification inputs ----

def test_package_scripts_of_another_folder_are_read(box):
    box.mode("enforce")
    box.agent_override("dev", level="watch_closely")
    web = box.proj / "web"
    web.mkdir()
    (web / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))
    assert run(BASH("cd web && npm test"), box.env(agent="dev"))[0] is None
    assert box.log() == []
    (web / "package.json").write_text(json.dumps({"scripts": {"test": "netlify deploy --prod"}}))
    out, _ = run(BASH("cd web && npm test"), box.env(agent="dev"))
    assert decision(out) == "deny" and box.log()[-1]["hits"] == [["outside_world", "deploy"]]


def test_scratchpad_dir_is_scratch(box):
    box.mode("enforce")
    box.agent_override("dev", level="watch_closely")
    inp = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_use_id": "t1", "tool_name": "Write",
           "tool_input": {"file_path": "/var/scratch/x/a.txt"}, "scratchpad_dir": "/var/scratch/x"}
    assert run(inp, box.env(agent="dev"))[0] is None
    inp["tool_input"]["file_path"] = "/var/other/a.txt"
    assert decision(run(inp, box.env(agent="dev"))[0]) == "deny"


def test_hot_path_imports(box):
    """A no-hit call never imports sqlite3, the stores or the cards; a held call imports them lazily."""
    box.mode("enforce")
    box.agent_override("dev", level="watch_closely")
    r = subprocess.run([sys.executable, "-X", "importtime", str(HOOK)], input=json.dumps(BASH("ls")),
                       capture_output=True, text=True, env=box.env(agent="dev"))
    mods = {ln.split("|")[-1].strip() for ln in r.stderr.splitlines() if ln.startswith("import time:")}
    assert "gate_classify" in mods and "gate_config" in mods and "gate_hook" in mods
    for m in ("sqlite3", "approval_schema", "supervision_store", "gate_cards", "approval_config", "urllib.request",
              "gate_policy", "tempfile", "http.client"):
        assert m not in mods, m


# ---- heartbeat, latency ----

def test_heartbeat_written_once_a_minute(box):
    box.mode("enforce")
    run(BASH("ls"), box.env(agent="dev"))
    f = box.p("state/attention/gate-seen/dev.json")
    first = json.loads(f.read_text())
    assert first["mode"] == "enforce" and first["session_id"] == "s1" and first["v"] == 1
    m0 = f.stat().st_mtime_ns
    time.sleep(0.05)
    run(BASH("ls", tool_use="t2"), box.env(agent="dev"))
    assert f.stat().st_mtime_ns == m0                                   # within the minute: untouched
    run(BASH("ls", tool_use="t3", session="s2"), box.env(agent="dev"))  # a new session: rewritten
    assert json.loads(f.read_text())["session_id"] == "s2"
    old = time.time() - 120
    os.utime(f, (old, old))
    run(BASH("ls", tool_use="t4", session="s2"), box.env(agent="dev"))
    assert f.stat().st_mtime > old + 60


def test_no_hit_latency_p95(box):
    from gate_fixture_harvest import FAKE_CWD, expand
    box.mode("observe")                                   # a stray hit is logged, never held
    box.agent_override("dev", level="watch_closely")
    (box.data / "registry.json").write_text(json.dumps({"agents": {"dev": {"cwd": FAKE_CWD, "runtime": "claude"}}}))
    calls = [json.loads(ln) for ln in (HERE / "fixtures" / "gate" / "calls.jsonl").read_text().splitlines() if ln]
    no_hit = [c for c in calls if not c.get("expect")][:60]
    assert len(no_hit) >= 30
    ms = []
    for i, c in enumerate(no_hit):
        inp = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_use_id": f"n{i}", "tool_name": c["tool"],
               "tool_input": expand(c["input"])}
        before = len(box.log())
        t = run(inp, box.env(agent="dev"))[1]
        if len(box.log()) == before:                      # it really took the no-hit path here
            ms.append(t)
    assert len(ms) >= 30
    ms.sort()
    assert ms[int(len(ms) * 0.95)] < 120                    # CI headroom; the box target is 45 ms (R7b)


# ---- gate_config ----

def test_gate_config_read_write(tmp_path):
    assert GCF.read_gate(tmp_path) == {"mode": "off"} and GCF.read_gate_raw(tmp_path) is None
    w = GCF.write_gate(tmp_path, mode="enforce", person="op")
    p = Path(GCF.paths(tmp_path)["gate"])
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and w["v"] == 1 and w["updated_at"].endswith("Z")
    GCF.write_gate(tmp_path, gen=3)
    g = GCF.read_gate(tmp_path)
    assert g["mode"] == "enforce" and g["person"] == "op" and g["gen"] == 3              # merged
    with pytest.raises(ValueError):
        GCF.write_gate(tmp_path, mode="loud")
    p.write_text(json.dumps({"mode": "loud"}))
    assert GCF.read_gate(tmp_path) == {"mode": "off"}
    p.write_text("[]")
    assert GCF.read_gate(tmp_path) == {"mode": "off"}
    assert not GCF.kill_switch(tmp_path)
    Path(GCF.paths(tmp_path)["kill"]).touch()
    assert GCF.kill_switch(tmp_path)


def test_gate_config_settings_for():
    g = {"default": {"level": "check_in", "high_risk": ["install"], "hold_wait_s": 60},
         "agents": {"dev": {"level": "watch_closely", "high_risk": [], "hold_wait_s": 0, "role": "rol_1"}}}
    assert GCF.settings_for(g, "dev-gen2") == {"level": "watch_closely", "high_risk": [], "hold_wait_s": 0,
                                               "role": "rol_1"}
    assert GCF.settings_for(g, "other")["level"] == "check_in"
    assert GCF.settings_for({}, "x") == {"level": "let_them_run", "high_risk": [], "hold_wait_s": 0, "role": None}
    assert GCF.settings_for({"agents": {"x": {"level": "bogus"}}}, "x")["level"] == "let_them_run"


def test_gate_config_log_rotates_and_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(GCF, "LOG_MAX_BYTES", 300)
    for i in range(10):
        GCF.log_line(tmp_path, {"agent": "dev" if i % 2 else "ops-gen2", "i": i, "detail": "x" * 40})
    p = GCF.paths(tmp_path)
    assert Path(p["log_old"]).exists() and Path(p["log"]).stat().st_size < 600
    assert [r["i"] for r in GCF.read_log(tmp_path, limit=2)] == [8, 9]
    assert all(r["agent"] == "ops-gen2" for r in GCF.read_log(tmp_path, limit=0, agent="ops"))


def test_gate_config_project_key_matches_the_store(tmp_path):
    data = tmp_path / "d"
    (data / "projects" / "a" / "sub").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    for cwd in (data / "projects" / "a" / "sub", data / "projects" / "a", data / "projects",
                tmp_path / "elsewhere", "/nonexistent/x"):
        assert GCF.project_key(str(cwd), str(data)) == SSM.project_key(str(cwd), str(data))
    assert GCF.project_key("", str(data)) is None


def test_gate_config_is_stdlib_only():
    src = (HERE / "gate_config.py").read_text()
    import ast
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    assert mods <= {"__future__", "json", "os", "re", "tempfile", "time", "gate_classify"}, mods
    top = {n.names[0].name for n in tree.body if isinstance(n, ast.Import)}
    assert "tempfile" not in top                     # imported only when gate.json is written (hot path)


# ---- gate_latency.py (the R7b tool) ----

def test_gate_latency_refuses_the_live_data_dir(tmp_path):
    import gate_latency as GL
    live = os.path.expanduser("~/.orchestra")
    assert GL.refuse_live(live) and GL.refuse_live(os.path.join(live, "state"))
    assert GL.refuse_live(str(tmp_path)) is None
    assert GL.main(["--data-dir", live]) == 2


def test_interrupt_after_an_in_place_allow_leaves_a_retry_pass(box, monkeypatch, capsys):
    """review r1: SIGTERM landing after the hook moved its waiting hold to `allowed` (before it answered) denies this
    call, but the answer is not lost: the hold gets a one-time retry pass."""
    import signal
    import gate_hook as GHK
    hit = {"category": "outside_world", "action": "push", "detail": "git push", "certain": True, "folder": None,
           "recipient": None}
    hid = box.ss.create_hold(agent="dev", tool="Bash", fingerprint="fp-race", hits=[hit], level="watch_closely",
                             session_id="s1", tool_use_id="t-race", state="waiting")
    assert box.ss.transition_hold(hid, "allowed", ("waiting",), decision="allow_once", notified_at="2026-01-01T00:00:00Z")
    for k, v in {"answered": False, "ss": box.ss, "hold_id": hid, "rec": None, "ledger": None, "card_id": None,
                 "held": []}.items():
        monkeypatch.setattr(GHK._G, k, v)
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)}
    try:
        GHK._cleanup_then("interrupted", GHK.INTERRUPTED, "withdrawn", ("filing", "waiting"), "interrupted",
                          keep_allowed=True)
    finally:
        signal.alarm(0)
        for s, h in saved.items():
            signal.signal(s, h)
    assert decision(json.loads(capsys.readouterr().out.strip().splitlines()[-1])) == "deny"
    assert box.ss.hold(hid)["state"] == "allowed" and box.ss.hold(hid)["retry_until"]
    assert box.ss.take_retry("dev", "fp-race") == hid


def test_interrupt_after_this_hook_already_answered_allow_leaves_no_extra_pass(box, monkeypatch, capsys):
    """review r2: a signal landing after the allow was emitted must not mint a second pass."""
    import signal
    import gate_hook as GHK
    hit = {"category": "outside_world", "action": "push", "detail": "git push", "certain": True, "folder": None,
           "recipient": None}
    hid = box.ss.create_hold(agent="dev", tool="Bash", fingerprint="fp-race2", hits=[hit], level="watch_closely",
                             session_id="s1", tool_use_id="t-race2", state="waiting")
    assert box.ss.transition_hold(hid, "allowed", ("waiting",), decision="allow_once", notified_at="2026-01-01T00:00:00Z")
    for k, v in {"answered": True, "ss": box.ss, "hold_id": hid, "rec": None, "ledger": None, "card_id": None,
                 "held": []}.items():
        monkeypatch.setattr(GHK._G, k, v)
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)}
    try:
        GHK._cleanup_then("interrupted", GHK.INTERRUPTED, "withdrawn", ("filing", "waiting"), "interrupted",
                          keep_allowed=True)
    finally:
        signal.alarm(0)
        for s, h in saved.items():
            signal.signal(s, h)
    assert capsys.readouterr().out == ""                       # already answered: nothing more is printed
    assert not box.ss.hold(hid)["retry_until"] and box.ss.take_retry("dev", "fp-race2") is None
