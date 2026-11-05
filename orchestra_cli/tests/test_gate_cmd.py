"""`orchestra grant propose` and `orchestra gate mode|status|replay|retire-open` (attention part 2a, plan §7.3 / Task 9).
Every test runs on a tmp data dir with its own ledger; nothing here reaches a live install."""
import io
import json
import subprocess
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestra_cli import __main__ as M

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import gate_cards as GCD  # noqa: E402
import gate_config as GCF  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402
from gate_classify import Hit  # noqa: E402
from supervision_store import SupervisionStore  # noqa: E402

BASE = "9246bfe"            # main before this change (the commit this branch lands on)
FIXTURES = REPO / "scripts" / "fixtures" / "gate" / "calls.jsonl"
DEPLOY = Hit("outside_world", "deploy", "netlify deploy --prod").to_dict()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    (d / "state").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))                     # no ~/runtime sentinel can arm anything
    monkeypatch.setenv("ORCHESTRA_DIR", str(d))
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "no-orchestra.toml"))
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    monkeypatch.setenv("GATE_CARDS_NOTIFY", "0")
    monkeypatch.setenv("WATCH_GATEWAY_URL", "http://127.0.0.1:9")   # closed: the gateway is down
    monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(tmp_path / "no-token"))
    monkeypatch.delenv("ORCHESTRA_AGENT_ID", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (d / "registry.json").write_text(json.dumps({"agents": {
        "dev": {"cwd": str(proj), "runtime": "claude", "session_id": "s-dev"},
        "pm": {"cwd": str(proj), "runtime": "claude", "session_id": "s-pm"},
        "cx": {"cwd": str(proj), "runtime": "codex"}}}))
    return d


@pytest.fixture
def ledger(data_dir):
    s = ApprovalStore(db_path=str(data_dir / "state" / "tasks.db"))
    s.migrate()
    return s


@pytest.fixture
def ss(data_dir, ledger):
    s = SupervisionStore(db_path=data_dir / "state" / "tasks.db")
    s.migrate()
    return s


@pytest.fixture
def seeded(ss, data_dir, tmp_path):
    """The release defaults: person default Let them run, starters, the project on Hands-off, no agent assigned."""
    import supervision_store as SS
    ss.seed("operator", [SS.project_key(str(tmp_path / "proj"), str(data_dir))])
    return ss


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = M.main(list(argv))
        except SystemExit as e:             # argparse usage errors
            rc = e.code
    return SimpleNamespace(returncode=rc, stdout=out.getvalue(), stderr=err.getvalue())


@pytest.fixture
def cli(data_dir):
    return run_cli


def ledger_cards(data_dir):
    import sqlite3
    db = data_dir / "state" / "tasks.db"
    if not db.exists():
        return []
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT id FROM approval_requests WHERE origin='gate'").fetchall()
    finally:
        c.close()


# ---- grant propose ------------------------------------------------------------------------------------------------

def test_propose_files_pending_grant_and_card(cli, ss, ledger, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "dev")
    r = cli("grant", "propose", "--category", "outside_world", "--only", "deploy",
            "--said", "you can deploy without asking")
    assert r.returncode == 0 and r.stdout.strip().startswith("apr_"), r.stderr
    g = ss.grants("operator", states=("pending",))[0]
    assert (g["scope"], g["scope_ref"], g["narrow_action"], g["proposed_by"]) == ("agent", "dev", "deploy", "dev")
    assert g["source"] == "chat" and g["state"] == "pending"
    card = ledger.get(r.stdout.strip())
    menu = json.loads(card["menu"]) if isinstance(card["menu"], str) else card["menu"]
    assert card["origin"] == "gate" and card["kind"] == "menu"
    assert menu["bridge"] == {"kind": "gate_grant", "grant_id": g["id"], "scope": "agent", "permission": False}
    assert [o["label"] for o in menu["options"]] == ["Confirm", "Don't"]
    assert "you can deploy without asking" in card["summary"]


def test_propose_scopes(cli, ss, monkeypatch, data_dir, tmp_path):
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "dev-gen3")
    assert cli("grant", "propose", "--category", "install", "--for", "all").returncode == 0
    assert cli("grant", "propose", "--category", "install", "--for", "project").returncode == 0
    assert cli("grant", "propose", "--category", "install", "--for", "session").returncode == 0
    got = {(g["scope"], g["scope_ref"]) for g in ss.grants("operator", states=("pending",))}
    import supervision_store as SS
    assert got == {("fleet", None), ("project", SS.project_key(str(tmp_path / "proj"), str(data_dir))),
                   ("session", "s-dev")}
    r = cli("grant", "propose", "--category", "install", "--for", "role")      # dev has no role
    assert r.returncode == 2 and "no role" in r.stderr


def test_propose_unknown_action_is_usage_error(cli, ss, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "dev")
    assert cli("grant", "propose", "--category", "outside_world", "--only", "teleport").returncode == 2
    assert cli("grant", "propose", "--category", "telepathy").returncode == 2
    assert ss.grants("operator", states=("pending",)) == []


def test_propose_without_identity_is_usage_error(cli, ss):
    r = cli("grant", "propose", "--category", "install")
    assert r.returncode == 2 and "who are you" in r.stderr


def test_propose_unarmed_exits_3(cli, data_dir, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "")
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "dev")
    r = cli("grant", "propose", "--category", "outside_world", "--only", "deploy")
    assert r.returncode == 3 and "not armed" in r.stderr
    assert ledger_cards(data_dir) == []


def test_propose_busy_exits_1(cli, ss, monkeypatch):
    import supervision_store as SS
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "dev")

    def busy(*a, **k):
        raise SS.SupervisionBusy("the ledger is busy, run it again")
    monkeypatch.setattr(SS.SupervisionStore, "create_grant", busy)
    assert cli("grant", "propose", "--category", "install").returncode == 1


# ---- gate mode ----------------------------------------------------------------------------------------------------

def test_gate_mode_writes_gate_json(cli, data_dir):
    r = cli("gate", "mode", "observe")
    assert r.returncode == 0 and "gate.json" in r.stdout
    assert GCF.read_gate(data_dir)["mode"] == "observe"
    GCF.write_gate(data_dir, person="operator", gen=4)
    assert cli("gate", "mode", "enforce").returncode == 0
    g = GCF.read_gate(data_dir)
    assert (g["mode"], g["person"], g["gen"]) == ("enforce", "operator", 4)        # merged, nothing else lost
    assert cli("gate", "mode", "loud").returncode == 2


def _gateway(status=200, body=None):
    got = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_PUT(self):
            n = int(self.headers.get("Content-Length") or 0)
            got.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(n))))
            raw = json.dumps(body if body is not None else {"ok": True, "mode": got[-1][2]["mode"]}).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def test_gate_mode_goes_through_the_gateway_when_up(cli, data_dir, monkeypatch, tmp_path):
    srv, got = _gateway()
    try:
        (tmp_path / "tok").write_text("bearer-1\n")
        monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(tmp_path / "tok"))
        monkeypatch.setenv("WATCH_GATEWAY_URL", f"http://127.0.0.1:{srv.server_port}")
        r = cli("gate", "mode", "off")
        assert r.returncode == 0 and "through the gateway" in r.stdout
        assert got == [("/gate/mode", "Bearer bearer-1", {"mode": "off"})]
        assert GCF.read_gate_raw(data_dir) is None                    # the gateway owns the write
    finally:
        srv.shutdown()


def test_gate_mode_gateway_refusal_is_an_error(cli, data_dir, monkeypatch):
    srv, _got = _gateway(status=403, body={"ok": False, "error": "not_allowed"})
    try:
        monkeypatch.setenv("WATCH_GATEWAY_URL", f"http://127.0.0.1:{srv.server_port}")
        assert cli("gate", "mode", "enforce").returncode == 1
        assert GCF.read_gate_raw(data_dir) is None
    finally:
        srv.shutdown()


# ---- gate status --------------------------------------------------------------------------------------------------

def test_gate_status_prints_coverage(cli, data_dir):
    import time
    GCF.write_gate(data_dir, mode="enforce")
    GCF.heartbeat(data_dir, "dev", "s-dev", "enforce", now=time.time())
    GCF.log_line(data_dir, {"agent": "dev", "decision": "allow", "hits": [["outside_world", "deploy"]],
                            "detail": "netlify deploy --prod"})
    r = cli("gate", "status")
    assert r.returncode == 0
    assert "mode:        enforce" in r.stdout
    lines = {ln.split()[0]: ln.split()[1] for ln in r.stdout.splitlines() if ln.startswith("  ") and
             len(ln.split()) > 1 and ln.split()[0] in ("dev", "pm", "cx")}
    assert lines == {"dev": "protected", "pm": "not_seen", "cx": "not_supported"}
    assert "netlify deploy --prod" in r.stdout
    j = json.loads(cli("gate", "status", "--json").stdout)
    assert j["kill_switch"] is False and j["coverage"]["dev"]["status"] == "protected" and len(j["log"]) == 1
    (data_dir / "state" / "attention" / "GATE_OFF").touch()
    assert json.loads(cli("gate", "status", "--json").stdout)["kill_switch"] is True


def test_gate_status_stale_session_is_not_seen(cli, data_dir):
    import time
    GCF.write_gate(data_dir, mode="observe")
    GCF.heartbeat(data_dir, "dev", "an-older-session", "observe", now=time.time())
    GCF.heartbeat(data_dir, "pm", "s-pm", "observe", now=time.time())
    cov = json.loads(cli("gate", "status", "--json").stdout)["coverage"]
    assert (cov["dev"]["status"], cov["pm"]["status"]) == ("not_seen", "observing")


# ---- gate replay --------------------------------------------------------------------------------------------------

def _db_bytes(data_dir):
    p = data_dir / "state" / "tasks.db"
    return p.read_bytes() if p.exists() else None


def test_gate_replay_counts_holds_without_filing_anything(cli, data_dir, seeded):
    before = _db_bytes(data_dir)
    r = cli("gate", "replay", "--fixtures", str(FIXTURES), "--as-agent", "dev")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert set(out) == {"calls", "with_hits", "held", "by_level"}
    assert out["held"] == 0                                                   # with seeded defaults
    assert out["calls"] == len([ln for ln in FIXTURES.read_text().splitlines() if ln.strip()])
    assert 0 < out["with_hits"] <= out["calls"]
    assert out["by_level"]["let_them_run"] == 0
    assert out["by_level"]["watch_closely"] == out["with_hits"]
    assert ledger_cards(data_dir) == []                                       # replay never files a card
    assert seeded.open_holds(states=("filing", "waiting", "open")) == []
    assert not (data_dir / "state" / "attention" / "gate-log.jsonl").exists()   # no log line
    assert _db_bytes(data_dir) == before                                      # read-only


def test_gate_replay_level_override_and_grants(cli, data_dir, seeded):
    r = json.loads(cli("gate", "replay", "--fixtures", str(FIXTURES), "--as-agent", "dev",
                       "--level", "watch_closely").stdout)
    assert r["held"] == r["with_hits"] > 0
    # an agent-level allow for every category clears every certain hit (uncertain ones are destructive/unrecognised)
    for cat in ("outside_world", "destructive", "outside_folder", "install"):
        seeded.create_grant("operator", scope="agent", scope_ref="dev", category=cat, source="settings", by="t")
    r2 = json.loads(cli("gate", "replay", "--fixtures", str(FIXTURES), "--as-agent", "dev",
                        "--level", "watch_closely").stdout)
    assert r2["held"] == 0


def test_gate_replay_all_agents(cli, data_dir, seeded):
    r = cli("gate", "replay", "--fixtures", str(FIXTURES), "--all-agents")
    out = json.loads(r.stdout)
    assert r.returncode == 0 and set(out["agents"]) == {"dev", "pm", "cx"} and out["held"] == 0


def test_gate_replay_without_a_db_is_let_them_run(cli, data_dir):
    r = cli("gate", "replay", "--fixtures", str(FIXTURES), "--as-agent", "dev")
    assert r.returncode == 0 and json.loads(r.stdout)["held"] == 0
    assert not (data_dir / "state" / "tasks.db").exists()                     # mode=ro never creates it


def test_gate_replay_needs_an_agent(cli, data_dir):
    assert cli("gate", "replay", "--fixtures", str(FIXTURES)).returncode == 2


# ---- gate retire-open ---------------------------------------------------------------------------------------------

def test_gate_retire_open(cli, data_dir, ss, ledger):
    import gate_policy as GP
    res = GP.Resolved("watch_closely", (), 0, None, None, {}, False)
    holds = {}
    for st in ("filing", "waiting", "open", "allowed"):
        hid = ss.create_hold(agent="dev", tool="Bash", fingerprint=f"fp-{st}", hits=[DEPLOY], level="watch_closely")
        rid = GCD.file_card(ledger, hid, agent="dev", hits=[Hit.from_dict(DEPLOY)], resolved=res, notify=False)
        ss.set_hold(hid, state=st, card_id=rid)
        holds[st] = (hid, rid)
    gid, grid = GCD.propose_grant(ledger, ss, agent="dev", category="install", said="go ahead", person="operator",
                                  notify=False)
    r = cli("gate", "retire-open")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    # filing/waiting/open withdrawn with their cards; the allowed hold's still-pending card is retired as an orphan
    assert out == {"holds_withdrawn": 3, "cards_retired": 4, "grant_cards_retired": 1, "grants_declined": 1}
    for st in ("filing", "waiting", "open"):
        assert ss.hold(holds[st][0])["state"] == "withdrawn"
    assert ss.hold(holds["allowed"][0])["state"] == "allowed"
    assert ss.grant(gid)["state"] == "declined"
    assert all(ledger.get(rid)["status"] != "pending" for _h, rid in holds.values())
    assert ledger.get(grid)["status"] != "pending"
    assert json.loads(cli("gate", "retire-open").stdout) == {"holds_withdrawn": 0, "cards_retired": 0,
                                                             "grant_cards_retired": 0, "grants_declined": 0}


def test_gate_retire_open_unarmed_exits_3(cli, data_dir, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "")
    assert cli("gate", "retire-open").returncode == 3


# ---- no prompt changes (the operator's constraint) --------------------------------------------------------------------

def test_prompts_untouched():
    if subprocess.run(["git", "cat-file", "-e", f"{BASE}^{{commit}}"], cwd=REPO, capture_output=True).returncode:
        pytest.skip(f"no git history for {BASE} here (shallow checkout)")
    r = subprocess.run(["git", "diff", "--quiet", BASE, "--", "prompts/"], cwd=REPO)
    assert r.returncode == 0
