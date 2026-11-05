"""Nobody answers their own permission card (plan Task 7a). Adversarial, one test per answer path.

The agent whose call is held, and the agent that proposed a grant, must never answer that card: not through the
CLI (whatever --surface/--answered-by it claims), not through the API (as the API shells the CLI), not through the
dashboard proxy, not through the gateway's /approval-answers, not through the ntfy listener or a batch submit, and
not by forging a gate card. Agents here are REAL subprocesses started with ORCHESTRA_AGENT_ID: a test's own
/proc/self/environ never changes with monkeypatch.setenv, and the check reads /proc.

A seatbelt, not a sandbox (same Unix user); the limits are in the plan. These tests pin the ordinary paths."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import answer_caller as AC  # noqa: E402
import gate_cards as GCD  # noqa: E402
import gate_policy as GP  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402
from gate_classify import Hit  # noqa: E402
from supervision_store import SupervisionStore  # noqa: E402

pytestmark = pytest.mark.skipif(not AC.PROC.available(), reason="the caller check reads /proc (Linux)")

DEPLOY = Hit("outside_world", "deploy", "netlify deploy --prod")
RES = GP.Resolved("watch_closely", (), 0, "rS", "agent", {"level": "role"}, True)
CLI = str(HERE / "approval.py")


def _runner_is_an_agent() -> bool:
    return bool(AC.of_process().agents)


needs_person_runner = pytest.mark.skipif(
    _runner_is_an_agent(),
    reason="this suite is running inside an agent's process tree, so a person's answer cannot be simulated "
           "from here (that is the point of the check)")


@pytest.fixture
def data(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setenv("GATE_CARDS_NOTIFY", "0")
    monkeypatch.setenv("APPROVAL_RESUME_BLOCK_REAL_SEAMS", "1")
    return tmp_path


@pytest.fixture
def ledger(data):
    s = ApprovalStore(db_path=str(data / "state" / "tasks.db"))
    s.migrate()
    return s


@pytest.fixture
def ss(ledger):
    s = SupervisionStore(db_path=ledger.db_path)
    s.migrate()
    return s


def gate_card(ss, ledger, agent="dev"):
    hid = ss.create_hold(agent=agent, tool="Bash", fingerprint="fp1", hits=[DEPLOY.to_dict()],
                         level="watch_closely", session_id="s1")
    rid = GCD.file_card(ledger, hid, agent=agent, hits=[DEPLOY], resolved=RES, role_names={"rS": "Supervised"})
    assert ss.transition_hold(hid, "open", ("filing",), card_id=rid)
    return hid, rid


def grant_card(ss, ledger, agent="dev"):
    gid, rid = GCD.propose_grant(ledger, ss, agent=agent, category="outside_world", narrow={"action": "deploy"},
                                 said="you can deploy", person="operator")
    return gid, rid


def _env(data, agent=None):
    env = {k: v for k, v in os.environ.items() if k != "ORCHESTRA_AGENT_ID"}
    env.update(APPROVAL_DB_PATH=str(data / "state" / "tasks.db"), ORCHESTRA_DIR=str(data), ORCH_DIR=str(data),
               APPROVAL_DDL_ARMED="all", GATE_CARDS_NOTIFY="0", APPROVAL_RESUME_BLOCK_REAL_SEAMS="1")
    env.pop("TMUX_PANE", None)
    if agent:
        env["ORCHESTRA_AGENT_ID"] = agent
    return env


def answer_argv(rid, n, *extra):
    return [sys.executable, CLI, "answer", "--id", rid, "--answer", "option", "--option-n", n,
            "--surface", "web", "--answered-by", "operator", *extra]


def run_as(data, agent, argv):
    return subprocess.run(argv, env=_env(data, agent), capture_output=True, text=True, timeout=60)


def out_json(proc):
    return json.loads(proc.stdout.strip().splitlines()[-1])


def assert_untouched(ss, ledger, rid, hid=None, gid=None):
    assert ledger.get(rid)["status"] == "pending"
    assert ss.grants("operator", states=("active",)) == []
    if hid:
        assert ss.hold(hid)["state"] == "open"
    if gid:
        assert ss.grant(gid)["state"] == "pending"


# ---- the CLI ----------------------------------------------------------------------------------------------------

def test_cli_the_held_agent_cannot_answer_its_own_card(ss, ledger, data):
    hid, rid = gate_card(ss, ledger)
    p = run_as(data, "dev", answer_argv(rid, "3"))
    assert p.returncode == 4, p.stderr
    assert out_json(p)["error"] == "agent_cannot_answer_gate_card" and out_json(p)["why"] == "own_card"
    assert "only a person can answer this permission card" in p.stderr
    assert_untouched(ss, ledger, rid, hid=hid)


def test_cli_a_later_generation_is_the_same_agent(ss, ledger, data):
    hid, rid = gate_card(ss, ledger, agent="dev-gen2")
    p = run_as(data, "dev-gen5", answer_argv(rid, "1"))
    assert p.returncode == 4 and out_json(p)["why"] == "own_card"
    assert_untouched(ss, ledger, rid, hid=hid)


def test_cli_stripping_the_variable_does_not_help(ss, ledger, data):
    hid, rid = gate_card(ss, ledger)
    inner = answer_argv(rid, "3")
    # an agent process (env dev) runs `env -u ORCHESTRA_AGENT_ID approval.py answer ...`, as a Bash tool would
    code = ("import subprocess, sys, os; e = {k: v for k, v in os.environ.items() if k != 'ORCHESTRA_AGENT_ID'}; "
            f"sys.exit(subprocess.call({inner!r}, env=e))")
    p = run_as(data, "dev", [sys.executable, "-c", code])
    assert p.returncode == 4, p.stderr
    assert_untouched(ss, ledger, rid, hid=hid)


def test_cli_the_proposer_cannot_confirm_its_own_grant(ss, ledger, data):
    gid, rid = grant_card(ss, ledger)
    p = run_as(data, "dev", answer_argv(rid, "1"))
    assert p.returncode == 4 and out_json(p)["why"] == "own_card"
    assert_untouched(ss, ledger, rid, gid=gid)


def test_cli_another_agent_cannot_answer_either(ss, ledger, data):
    hid, rid = gate_card(ss, ledger)
    p = run_as(data, "pm", answer_argv(rid, "1"))
    assert p.returncode == 4 and out_json(p)["why"] == "agent_not_handed"
    assert_untouched(ss, ledger, rid, hid=hid)


@needs_person_runner
def test_cli_a_person_still_answers(ss, ledger, data):
    hid, rid = gate_card(ss, ledger)
    p = run_as(data, None, answer_argv(rid, "3"))
    assert p.returncode == 0, p.stderr
    assert out_json(p)["applied"] is True
    assert ledger.get(rid)["status"] != "pending"
    assert [g["scope_ref"] for g in ss.grants("operator", states=("active",))] == ["dev"]


@needs_person_runner
def test_cli_ordinary_cards_are_unchanged(ledger, data):
    rid = ledger.create(from_agent="dev", question="Ship?", worker_kind="pane")
    p = run_as(data, "dev", [sys.executable, CLI, "answer", "--id", rid, "--answer", "approve"])
    assert p.returncode == 0 and out_json(p)["applied"] is True        # plan risk 8: out of scope, unchanged


def test_cli_a_forged_gate_card_is_refused(ledger, data):
    menu = {"question": "Run the tests?", "options": [{"n": "1", "label": "Confirm"}, {"n": "2", "label": "Don't"}],
            "bridge": {"kind": "gate_grant", "grant_id": "grt_x"}}
    p = run_as(data, "dev", [sys.executable, CLI, "request", "Run the tests?", "--from", "dev", "--worker-kind",
                             "pane", "--menu-json", json.dumps(menu)])
    assert p.returncode == 2 and "reserved for the safety gate" in p.stderr
    assert ledger.pending_to_notify() == []


# ---- the API hop (approval.py --peer, as the API passes it) and the dashboard proxy hop ---------------------------

class Listener:
    """A loopback server socket; `connect(agent, argv0)` starts a subprocess that connects and holds the line."""

    def __init__(self, tmp_path):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.tmp, self.procs, self.conns = tmp_path, [], []

    def connect(self, data, agent=None, script_name="client.py"):
        script = self.tmp / script_name
        script.write_text(textwrap.dedent(f"""
            import socket, time
            s = socket.create_connection(("127.0.0.1", {self.port}))
            time.sleep(60)
        """))
        p = subprocess.Popen([sys.executable, str(script)], env=_env(data, agent))
        self.procs.append(p)
        self.srv.settimeout(20)
        conn, addr = self.srv.accept()
        self.conns.append(conn)
        return f"{addr[0]}|{addr[1]}|{self.port}"

    def close(self):
        for p in self.procs:
            p.kill()
            p.wait()
        for c in self.conns:
            c.close()
        self.srv.close()


@pytest.fixture
def lines(tmp_path):
    made = []

    def make():
        sub = tmp_path / f"l{len(made)}"
        sub.mkdir()
        made.append(Listener(sub))
        return made[-1]
    yield make
    for x in made:
        x.close()


def test_api_hop_an_agent_peer_is_refused(ss, ledger, data, lines):
    hid, rid = gate_card(ss, ledger)
    peer = lines().connect(data, agent="dev")                 # `curl localhost:8888/api/...` from the agent
    p = run_as(data, None, answer_argv(rid, "3", "--peer", peer))
    assert p.returncode == 4 and out_json(p)["why"] == "own_card", p.stderr
    assert_untouched(ss, ledger, rid, hid=hid)


@needs_person_runner
def test_api_hop_a_person_peer_is_applied(ss, ledger, data, lines):
    hid, rid = gate_card(ss, ledger)
    peer = lines().connect(data, agent=None)                  # a browser on this machine
    p = run_as(data, None, answer_argv(rid, "1", "--peer", peer))
    assert p.returncode == 0 and out_json(p)["applied"] is True, p.stderr


def test_api_hop_a_missing_or_bad_peer_is_unknown(ss, ledger, data):
    hid, rid = gate_card(ss, ledger)
    for bad in ("unknown", "127.0.0.1|1|1"):                  # what the API sends with no socket; nothing there
        p = run_as(data, None, answer_argv(rid, "1", "--peer", bad))
        assert p.returncode == 4 and out_json(p)["why"] in ("caller_unknown",), (bad, p.stdout)
    assert_untouched(ss, ledger, rid, hid=hid)


def test_proxy_hop_the_proxys_client_is_followed(ss, ledger, data, lines):
    hid, rid = gate_card(ss, ledger)
    api, proxy = lines(), lines()
    proxy_conn = api.connect(data, agent=None, script_name="dashboard-proxy.js")   # `node dashboard-proxy.js`
    agent_conn = proxy.connect(data, agent="dev")                                   # the agent -> :8891
    p = run_as(data, None, answer_argv(rid, "3", "--peer", proxy_conn, "--forwarded-peer", agent_conn))
    assert p.returncode == 4 and out_json(p)["why"] == "own_card", p.stderr
    p = run_as(data, None, answer_argv(rid, "3", "--peer", proxy_conn))             # proxy sent no header
    assert p.returncode == 4 and out_json(p)["why"] == "caller_unknown"
    assert_untouched(ss, ledger, rid, hid=hid)


# ---- the gateway's /approval-answers ------------------------------------------------------------------------------

@pytest.fixture
def gateway_app(data, tmp_path, monkeypatch):
    import watch_gateway as G
    from aiohttp import web
    tok = tmp_path / "gw-token"
    tok.write_text("fleet-token-for-test")
    monkeypatch.setattr(G, "TOKEN_FILE", tok)
    app = web.Application(middlewares=[G.scope_middleware_factory()])
    app.router.add_post("/approval-answers", G.handle_answer)
    return app


async def _post_from_subprocess(data, agent, port, body):
    code = textwrap.dedent(f"""
        import json, urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:{port}/approval-answers", data=json.dumps({body!r}).encode(),
                                     headers={{"Authorization": "Bearer fleet-token-for-test",
                                              "Content-Type": "application/json"}})
        try:
            r = urllib.request.urlopen(req, timeout=30); print(r.status, r.read().decode())
        except urllib.error.HTTPError as e:
            print(e.code, e.read().decode())
    """)
    p = await asyncio.create_subprocess_exec(sys.executable, "-c", code, env=_env(data, agent),
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(p.communicate(), 60)
    status, _, rest = out.decode().strip().partition(" ")
    return int(status), json.loads(rest or "{}"), err.decode()


async def test_gateway_an_agent_with_the_fleet_token_is_refused(ss, ledger, data, gateway_app):
    from aiohttp.test_utils import TestServer
    hid, rid = gate_card(ss, ledger)
    server = TestServer(gateway_app, host="127.0.0.1")
    await server.start_server()
    try:
        st, body, err = await _post_from_subprocess(data, "dev", server.port,
                                                    {"id": rid, "answer": "option", "option_n": "3"})
        assert st == 403 and body["error"] == "agent_cannot_answer_gate_card" and body["why"] == "own_card", err
        st, body, _ = await _post_from_subprocess(data, "pm", server.port,
                                                  {"id": rid, "answer": "option", "option_n": "1"})
        assert st == 403 and body["why"] == "agent_not_handed"
        assert_untouched(ss, ledger, rid, hid=hid)
        if not _runner_is_an_agent():
            st, body, err = await _post_from_subprocess(data, None, server.port,
                                                        {"id": rid, "answer": "option", "option_n": "1"})
            assert st == 200 and body["applied"] is True, (body, err)
    finally:
        await server.close()


def test_gateway_a_request_with_no_socket_is_unknown(ss, ledger, data):
    import watch_gateway as G

    class Req:
        transport = None
        headers = {}
    hid, rid = gate_card(ss, ledger)
    res = G.apply_answer(ledger, rid, "option", option_n="1", caller=lambda: G._http_caller(Req()))
    assert res["status"] == 403 and res["why"] == "caller_unknown"
    assert_untouched(ss, ledger, rid, hid=hid)


# ---- the core, the store, the listener --------------------------------------------------------------------------

@pytest.mark.real_caller
def test_core_reads_its_own_process_chain(ss, ledger, data, monkeypatch):
    """A script that imports the core and calls it directly is still the agent that runs it."""
    import watch_gateway as G
    hid, rid = gate_card(ss, ledger)
    monkeypatch.setattr(AC, "self_caller", lambda: AC.Caller(("dev",), False, "process"))
    res = G.apply_answer(ledger, rid, "option", option_n="1", surface="web", answered_by="operator")
    assert res["status"] == 403 and res["why"] == "own_card"
    # a rule's hand-off can never name the owner
    res = G.apply_answer(ledger, rid, "option", option_n="1", handed_to="dev")
    assert res["why"] == "own_card"
    assert_untouched(ss, ledger, rid, hid=hid)


def test_core_an_unreadable_owner_still_refuses_the_author(ss, ledger, data, monkeypatch):
    import watch_gateway as G
    _, rid = gate_card(ss, ledger)
    import supervision_store
    monkeypatch.setattr(supervision_store.SupervisionStore, "hold", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    res = G.apply_answer(ledger, rid, "option", option_n="1", caller=AC.Caller(("dev",)))
    assert res["why"] == "own_card"


def test_store_refuses_a_gate_card_without_the_check(ss, ledger, data):
    hid, rid = gate_card(ss, ledger)
    assert ledger.record_answer(rid, "option", None, option_n="1", surface="web", answered_by="operator") is False
    assert ledger.record_batch_answer(rid, [{"part": 1, "ns": ["1"]}], surface="web") is False
    assert_untouched(ss, ledger, rid, hid=hid)
    other = ledger.create(from_agent="dev", question="Ship?", worker_kind="pane")
    assert ledger.record_answer(other, "approve", None) is True                # ordinary cards unchanged


def test_listener_tap_on_a_gate_card_is_not_applied(ss, ledger, data, monkeypatch):
    import approval_listener as L
    hid, rid = gate_card(ss, ledger)
    monkeypatch.setattr(L, "_write_cursor", lambda ts: None)
    fired = []
    monkeypatch.setattr(L, "fire_resume", lambda row, store: fired.append(row["id"]))
    L.handle_event({"event": "message", "message": json.dumps({"id": rid, "answer": "approve"})}, store=ledger)
    assert fired == []
    assert_untouched(ss, ledger, rid, hid=hid)


def test_an_answer_refused_once_can_still_be_given_by_the_person(ss, ledger, data):
    import watch_gateway as G
    hid, rid = gate_card(ss, ledger)
    assert G.apply_answer(ledger, rid, "option", option_n="1", caller=AC.Caller(("dev",)))["status"] == 403
    res = G.apply_answer(ledger, rid, "option", option_n="1", fire=False, caller=AC.PERSON)
    assert res["ok"] and res["applied"]
