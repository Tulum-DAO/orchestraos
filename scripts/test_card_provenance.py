"""Every card is stamped, when it is created and before it is stored, with the agent it came from and
that agent's provider (card_provenance.py): every approval_requests kind, questionnaires (behind their
DDL arm), and the permission-prompt state (the gateway's instance ledger, approval_notify's push state).
Values come from the registry, a caller's own value always wins, and a source that is not a
registered agent keeps its own name with no provider."""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_schema  # noqa: E402
import card_provenance  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402

REGISTRY = {"agents": {
    "pm-demo": {"runtime": "claude", "session_id": "sid-pm-demo-2", "generation": 2,
                "tmux_session": "pm-demo-pane", "status": "online"},
    "dev-demo": {"runtime": "codex", "session_id": "sid-dev-demo", "generation": 1, "status": "online"},
    # a retired predecessor still naming the reused pane: the live record must win
    "pm-demo-g1": {"runtime": "gemini", "session_id": "sid-old", "generation": 1,
                   "tmux_session": "pm-demo-pane", "status": "retired"},
}}
IDENT = ("seat_id", "provider", "provider_session_id", "generation")


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "registry.json").write_text(json.dumps(REGISTRY))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(home):
    s = ApprovalStore(db_path=str(home / "state" / "tasks.db"))
    s.migrate()
    return s


def _ident(store, rid):
    row = store.get(rid)
    return tuple(row[k] for k in IDENT)


# ── the resolver ──

def test_resolves_by_agent_id_and_by_tmux_session_preferring_the_live_record(home):
    assert card_provenance.source_identity("dev-demo") == {
        "seat_id": "dev-demo", "provider": "codex", "provider_session_id": "sid-dev-demo", "generation": 1}
    assert card_provenance.source_identity("pm-demo-pane")["seat_id"] == "pm-demo"
    assert card_provenance.source_identity("pm-demo-pane")["provider"] == "claude"


def test_a_source_that_is_not_an_agent_keeps_its_name_and_has_no_provider(home):
    assert card_provenance.source_identity("lineage-daemon") == {
        "seat_id": "lineage-daemon", "provider": None, "provider_session_id": None, "generation": None}


def test_a_value_the_caller_gave_is_never_overwritten(home):
    out = card_provenance.fill("pm-demo", {"seat_id": "seat-it-is-about", "provider": None,
                                           "provider_session_id": None, "generation": None})
    assert out["seat_id"] == "seat-it-is-about" and out["provider"] == "claude"


def test_a_missing_registry_never_blocks_a_card(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))          # no registry.json here
    assert card_provenance.fill("pm-demo", {})["seat_id"] == "pm-demo"


# ── every approval_requests kind, through its real writer ──

def test_agent_filed_decision(store, monkeypatch, capsys):
    import approval
    monkeypatch.setattr(approval, "_store", lambda: store)
    monkeypatch.setattr(approval, "notify", lambda rid, store=None: None)
    assert approval.main(["request", "Ship it?", "--from", "pm-demo", "--worker-kind", "pane"]) == 0
    rid = capsys.readouterr().out.strip().splitlines()[-1]
    assert _ident(store, rid) == ("pm-demo", "claude", "sid-pm-demo-2", 2)


def test_completion(store, monkeypatch, capsys):
    import approval
    monkeypatch.setattr(approval, "_store", lambda: store)
    monkeypatch.setattr(approval, "notify", lambda rid, store=None: None)
    assert approval.main(["complete", "Landing page live", "--from", "dev-demo"]) == 0
    rid = capsys.readouterr().out.strip().splitlines()[-1]
    assert store.get(rid)["kind"] == "completion"
    assert _ident(store, rid) == ("dev-demo", "codex", "sid-dev-demo", 1)


def test_human_task(store, monkeypatch, capsys):
    import approval
    monkeypatch.setattr(approval, "_store", lambda: store)
    monkeypatch.setattr(approval, "notify", lambda rid, store=None: None)
    assert approval.main(["human-task", "--from", "pm-demo", "--task", "Sign the form", "--op-key", "ht-1"]) == 0
    rid = capsys.readouterr().out.strip().splitlines()[-1]
    assert store.get(rid)["kind"] == "human_task"
    assert _ident(store, rid)[:2] == ("pm-demo", "claude")


def test_red_alert_card_keeps_the_seat_it_names_and_gets_no_provider(store, monkeypatch, capsys):
    # red_alert_watch files as red-alert-builder and passes --seat-id <the seat the alert is about>
    import approval
    monkeypatch.setattr(approval, "_store", lambda: store)
    monkeypatch.setattr(approval, "notify", lambda rid, store=None: None)
    assert approval.main(["request", "RED ALERT: repair it?", "--from", "red-alert-builder",
                          "--worker-kind", "node", "--seat-id", "dev-demo", "--feature", "RED ALERT"]) == 0
    rid = capsys.readouterr().out.strip().splitlines()[-1]
    assert _ident(store, rid) == ("dev-demo", None, None, None)


def test_bridged_terminal_menu_resolves_its_pane_to_the_agent(store):
    import menu_bridge as mb
    cap = {"question": "Proceed?", "options": [{"n": "1", "label": "Yes"}, {"n": "2", "label": "No"}],
           "multipart": False, "walk_complete": True}
    rid = mb.bridge_one(store, "pm-demo-pane", cap, 0, 100, "working",
                        walk_fn=lambda s: None, reread_fn=lambda s: cap)
    assert rid
    row = store.get(rid)
    assert row["origin"] == "menu_bridge" and row["from_agent"] == "pm-demo-pane"
    assert _ident(store, rid) == ("pm-demo", "claude", "sid-pm-demo-2", 2)


def test_backfill(store, home):
    import approval_backfill
    pend = home / "pending"; pend.mkdir()
    (pend / "fs_1.json").write_text(json.dumps({"id": "fs_1", "agent_id": "dev-demo", "title": "Old card"}))
    out = approval_backfill.backfill_fs(store=store, pending_dir=str(pend))
    [rid] = out["ids"]
    assert store.get(rid)["origin"] == "backfill_fs"
    assert _ident(store, rid)[:2] == ("dev-demo", "codex")


def test_stamped_before_the_row_is_written(store, monkeypatch):
    # the identity is in the INSERT itself, not patched on afterwards
    seen = []
    real = ApprovalStore._conn

    def spy(self):
        c = real(self)
        c.set_trace_callback(lambda sql: seen.append(sql))
        return c
    monkeypatch.setattr(ApprovalStore, "_conn", spy)
    store.create(from_agent="pm-demo", question="q", worker_kind="pane")
    inserts = [s for s in seen if s.lstrip().upper().startswith("INSERT INTO APPROVAL_REQUESTS")]
    assert inserts and "'claude'" in inserts[0] and "'pm-demo'" in inserts[0]
    assert not any(s.lstrip().upper().startswith("UPDATE APPROVAL_REQUESTS SET SEAT_ID") for s in seen)


# ── questionnaires (their own tables, behind the DDL arm) ──

def _qrow(home, qid):
    c = sqlite3.connect(str(home / "state" / "tasks.db"))
    c.row_factory = sqlite3.Row
    return dict(c.execute("SELECT * FROM questionnaires WHERE id=?", [qid]).fetchone())


def test_questionnaire_unarmed_adds_nothing_and_writes_as_before(home, monkeypatch):
    from questionnaire_schema import QuestionnaireStore
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    monkeypatch.setenv("HOME", str(home))                       # no ~/runtime sentinel
    q = QuestionnaireStore(db_path=str(home / "state" / "tasks.db")); q.migrate()
    qid = q.create("pm-demo", "Intake", [{"prompt": "Name?", "kind": "free_text"}])
    assert "provider" not in _qrow(home, qid)


def test_questionnaire_armed_stamps_the_source_agent_in_the_insert(home, monkeypatch):
    from questionnaire_schema import QuestionnaireStore, PROVENANCE_MIGRATION
    monkeypatch.setenv("APPROVAL_DDL_ARMED", PROVENANCE_MIGRATION)
    q = QuestionnaireStore(db_path=str(home / "state" / "tasks.db")); q.migrate()
    qid = q.create("pm-demo", "Intake", [{"prompt": "Name?", "kind": "free_text"}])
    row = _qrow(home, qid)
    assert (row["seat_id"], row["provider"], row["provider_session_id"], row["generation"]) == \
        ("pm-demo", "claude", "sid-pm-demo-2", 2)


# ── permission prompts (state files, not rows) ──

def test_instance_ledger_stamps_the_agent_when_a_prompt_is_first_seen_and_on_a_new_instance(home):
    import watch_gateway as G
    led = {}
    ident = lambda: card_provenance.source_identity("pm-demo-pane")   # noqa: E731
    G._instance_step(led, "pm-demo-pane", "d1", present=True, now=0, identity=ident)
    e = led["pm-demo-pane|d1"]
    assert (e["seat_id"], e["provider"], e["generation"]) == ("pm-demo", "claude", 2)
    # a later sighting of the same instance reads nothing new
    calls = []
    G._instance_step(led, "pm-demo-pane", "d1", present=True, now=1, identity=lambda: calls.append(1) or {})
    assert calls == []
    # gone, then back: a NEW instance, stamped afresh
    G._instance_step(led, "pm-demo-pane", "d1", present=False, now=2, answered_signal=False)
    G._instance_step(led, "pm-demo-pane", "d1", present=False, now=10)
    G._instance_step(led, "pm-demo-pane", "d1", present=True, now=11, identity=lambda: {"provider": "codex"})
    assert led["pm-demo-pane|d1"]["instance_n"] == 2 and led["pm-demo-pane|d1"]["provider"] == "codex"


def test_permission_push_state_stamps_the_agent_on_a_new_prompt(home, tmp_path):
    import approval_notify as N
    path = str(tmp_path / "perm-notify-state.json")
    present = {"menu:pm-demo-pane:abc:1": {"agent": "pm-demo-pane", "question": "Run rm?"}}
    N.permission_backstop(now=1000.0, state_path=path, scan=lambda: present, send=lambda *a: None)
    st = json.load(open(path))["menu:pm-demo-pane:abc:1"]
    assert (st["seat_id"], st["provider"], st["provider_session_id"]) == ("pm-demo", "claude", "sid-pm-demo-2")
    assert st["first_seen"] == 1000.0
