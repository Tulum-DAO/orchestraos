"""scripts/away.py: the "While you were away" card's data (RFC 0002, gm msg_8a661e3c conditions)."""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import away as A  # noqa: E402

T0 = 1_760_000_000.0          # an arbitrary "now - a while"


def no_secrets(_b):
    return None


def _iso(t):
    return A.iso(t)


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    (d / "state" / "voice-calls").mkdir(parents=True)
    con = sqlite3.connect(d / "state" / "tasks.db")
    con.executescript("""
      CREATE TABLE approval_requests (id TEXT, from_agent TEXT, question TEXT, answer TEXT, status TEXT,
        created_at TEXT, answered_at TEXT, answer_surface TEXT, answered_by TEXT, answer_device TEXT);
      CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, assigned_to TEXT, completed_at TEXT);
      CREATE TABLE messages (id TEXT, from_agent TEXT, to_agent TEXT, subject TEXT, created_at TEXT);
    """)
    con.commit(); con.close()
    return d


def _answer(d, rid, at, *, by="operator", surface="phone", device=None, agent="gm", answer="yes"):
    con = sqlite3.connect(d / "state" / "tasks.db")
    con.execute("INSERT INTO approval_requests VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, agent, "ship it?", answer, "answered", _iso(at - 60), _iso(at), surface, by, device))
    con.commit(); con.close()


def _call(d, cid, started, *, ended=None, status="ended", users=("make the card start from my action",),
          summary="Agreed to spec it.", label=None, page="voice"):
    j = {"call_id": cid, "started_at": started, "status": status, "page": page, "summary": summary,
         "turns": [{"role": "user", "text": u, "ts": started + 1} for u in users]}
    if ended is not None:
        j["ended_at"] = ended
    if label:
        j["device_label"] = label
    p = d / "state" / "voice-calls" / f"{cid}.json"
    p.write_text(json.dumps(j))
    os.utime(p, (ended or started, ended or started))


def _ledger(d, at, target="gm", said="please fix the card", device=None, surface="phone"):
    with open(d / "state" / "operator-actions.jsonl", "a") as f:
        f.write(json.dumps({"kind": "message", "at": _iso(at), "target": target, "said": said,
                            "device": device, "surface": surface}) + "\n")


# ---- anchor ----------------------------------------------------------------------------------------

def test_no_action_at_all_is_a_null_anchor_not_an_error(data):
    out = A.build(data, [], [], scan=no_secrets)
    assert out["anchor"] is None and out["reason"] == "no_action_yet" and out["moved"] == []


@pytest.mark.parametrize("newest", ["answer", "call", "message"])
def test_each_source_wins_when_it_is_newest(data, newest):
    times = {"answer": T0, "call": T0, "message": T0}
    times[newest] = T0 + 100
    _answer(data, "apr_1", times["answer"])
    _call(data, "vc_1", times["call"] - 50, ended=times["call"])
    _ledger(data, times["message"])
    assert A.pick_anchor(data)["kind"] == newest


def test_a_tie_breaks_deterministically_answer_first(data):
    _answer(data, "apr_1", T0)
    _call(data, "vc_1", T0 - 10, ended=T0)
    _ledger(data, T0)
    assert A.pick_anchor(data)["kind"] == "answer"


def test_agent_cli_and_unstamped_answers_are_not_the_operator(data):
    _answer(data, "apr_self", T0 + 50, by="dev-seat")
    _answer(data, "apr_cli", T0 + 40, surface="agent_cli")
    _answer(data, "apr_null", T0 + 30, by=None)                # pre-R7d rows: cannot be taken as the operator
    _answer(data, "apr_me", T0)
    assert A.pick_anchor(data)["ref"] == "apr_me"


def test_a_call_with_no_user_words_is_not_an_anchor(data):
    _call(data, "vc_silent", T0, ended=T0 + 5, users=())
    assert A.pick_anchor(data) is None


# ---- device naming: never a guess -------------------------------------------------------------------

def test_answer_device_stamp_names_the_device(data):
    _answer(data, "apr_1", T0, device="d_9f:iPad")
    a = A.pick_anchor(data)
    assert a["device"] == "iPad" and a["device_source"] == "answer_device"


def test_legacy_fleet_token_answer_names_no_device(data):
    _answer(data, "apr_1", T0, device=None)
    a = A.pick_anchor(data)
    assert a["device"] is None and a["device_source"] == "none"


def test_a_call_without_a_label_states_the_channel_only(data):
    _call(data, "vc_1", T0, ended=T0 + 30, page="phone")
    a = A.pick_anchor(data)
    assert a["device"] is None and a["device_source"] == "channel" and a["surface"] == "phone"


def test_a_call_with_a_label_names_it(data):
    _call(data, "vc_1", T0, ended=T0 + 30, label="iPad")
    a = A.pick_anchor(data)
    assert a["device"] == "iPad" and a["device_source"] == "label"


# ---- outcome ---------------------------------------------------------------------------------------

def test_voice_outcome_comes_from_the_journal(data):
    _call(data, "vc_1", T0, ended=T0 + 30, status="ended", summary="Agreed to spec it.")
    out = A.build(data, [], [], scan=no_secrets)
    assert out["anchor"]["outcome"]["state"] == "done"
    assert out["anchor"]["outcome"]["summary"] == "Agreed to spec it."


def test_a_live_call_is_working(data):
    _call(data, "vc_1", T0, status="live")
    assert A.build(data, [], [], scan=no_secrets)["anchor"]["outcome"]["state"] == "working"


def _anchor_msg(data):
    _ledger(data, T0, target="gm")
    return A.pick_anchor(data)


def test_outcome_waiting_on_you_when_the_target_asked_after(data):
    a = _anchor_msg(data)
    cards = [{"id": "apr_new", "from_agent": "gm", "question": "which one?", "created_at": _iso(T0 + 10)}]
    assert A.outcome(a, [{"id": "gm", "state": "idle"}], cards)["state"] == "waiting_on_you"


def test_outcome_working_done_unknown(data):
    a = _anchor_msg(data)
    assert A.outcome(a, [{"id": "gm", "state": "working"}], [])["state"] == "working"
    assert A.outcome(a, [{"id": "gm", "state": "idle", "last_used_ts": T0 + 5}], [])["state"] == "done"
    assert A.outcome(a, [{"id": "gm", "state": "idle", "last_used_ts": T0 - 5}], [])["state"] == "unknown"
    assert A.outcome(a, [], [])["state"] == "unknown"           # the agent is gone


# ---- moved since -------------------------------------------------------------------------------------

def test_moved_lists_only_events_after_the_anchor(data):
    con = sqlite3.connect(data / "state" / "tasks.db")
    con.execute("INSERT INTO tasks VALUES ('t1','old task','done','dev','2025-10-09 08:00:00')")
    con.execute("INSERT INTO tasks VALUES ('t2','new task','done','dev',?)",
                (A._dt.datetime.fromtimestamp(T0 + 20, A._dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),))
    con.execute("INSERT INTO messages VALUES ('m1','gm','operator','status update',?)", (_iso(T0 + 30),))
    con.execute("INSERT INTO messages VALUES ('m0','gm','operator','before',?)", (_iso(T0 - 30),))
    con.commit(); con.close()
    cards = [{"id": "apr_a", "from_agent": "dev", "question": "q", "created_at": _iso(T0 + 40)},
             {"id": "apr_b", "from_agent": "dev", "question": "older", "created_at": _iso(T0 - 40)}]
    moved = A.moved_since(data, T0, cards)
    assert [m["kind"] for m in moved] == ["card", "message", "task_done"]


def test_down_now_is_registered_agent_seats_and_never_counted_as_moved(data):
    (data / "registry.json").write_text(json.dumps({"agents": {"svc": {"runtime": "service"},
                                                                "svc2": {"runtime": "service"}}}))
    agents = [{"id": "seat", "state": "crashed"}, {"id": "seat2", "state": "stopped"},
              {"id": "svc", "state": "stopped"},                 # a registered service seat: never down
              {"id": "svc2", "state": "crashed"},                # ... even when the scan says crashed
              {"id": "proxy", "state": "stopped", "unregistered": True}, {"id": "w", "state": "working"}]
    assert [d["agent"] for d in A.down_now(data, agents)] == ["seat", "seat2"]
    assert A.moved_since(data, T0, []) == []           # no crash time: not news since the anchor
    _ledger(data, T0)
    out = A.build(data, agents, [], scan=no_secrets)
    assert out["moved_count"] == 0 and [d["agent"] for d in out["down_now"]] == ["seat", "seat2"]


def test_rotations_come_from_the_identity_db(data):
    con = sqlite3.connect(data / "state" / "orchestra-registry.db")
    con.execute("CREATE TABLE generations (root TEXT, generation INT, promoted_at TEXT)")
    con.execute("INSERT INTO generations VALUES ('gm', 104, ?)", (_iso(T0 + 5),))
    con.commit(); con.close()
    assert [m["text"] for m in A.moved_since(data, T0, [])] == ["gm moved to generation 104"]


# ---- summary cache -----------------------------------------------------------------------------------

def test_summary_is_cached_per_anchor_and_rewritten_when_the_count_changes(data):
    _ledger(data, T0)
    calls = []
    first = A.build(data, [], [], scan=no_secrets, write_brain=lambda aid, m: calls.append(aid))
    assert first["summary"]["source"] == "rules" and len(calls) == 1
    A.store_summary(data, first["summary"]["anchor_id"], 0, "A brain wrote this.", "brain", scan=no_secrets)
    again = A.build(data, [], [], scan=no_secrets, write_brain=lambda aid, m: calls.append(aid))
    assert again["summary"] == {**again["summary"], "text": "A brain wrote this.", "source": "brain"}
    assert len(calls) == 1                                       # a hit asks no brain
    cards = [{"id": "apr_x", "from_agent": "dev", "question": "q", "created_at": _iso(T0 + 9)}]
    changed = A.build(data, [], cards, scan=no_secrets, write_brain=lambda aid, m: calls.append(aid))
    assert changed["summary"]["source"] == "rules" and len(calls) == 2


def test_a_new_anchor_is_a_new_cache_entry(data):
    _ledger(data, T0)
    a1 = A.build(data, [], [], scan=no_secrets)["summary"]["anchor_id"]
    _ledger(data, T0 + 60, said="second ask")
    a2 = A.build(data, [], [], scan=no_secrets)["summary"]["anchor_id"]
    assert a1 != a2


def test_text_that_looks_like_a_secret_is_never_stored_or_served(data):
    _ledger(data, T0, said="token sk-live-THISLOOKSSECRET")
    scan = lambda b: "secret" if b"sk-live" in b else None
    out = A.build(data, [], [], scan=scan)
    assert out["anchor"]["said"] is None
    assert A.store_summary(data, "a_x", 0, "has sk-live-123", "brain", scan=scan) is False
    assert not (data / "state" / "away-summary" / "a_x.json").exists()


def test_a_broken_scanner_lets_nothing_through(data):
    def boom(_b):
        raise RuntimeError("scanner down")
    assert A._safe("anything", boom) is None


# ---- screen anchor + presence ------------------------------------------------------------------------

def test_screen_anchor_without_any_signal_is_null_with_a_reason(data):
    out = A.build(data, [], [], mode="screen", scan=no_secrets)
    assert out["anchor"] is None and out["reason"] == "no_presence_yet"


def test_screen_anchor_uses_presence_and_surface_never_last_seen(data):
    (data / "state" / "devices").mkdir()
    (data / "state" / "devices" / "d1.json").write_text(json.dumps({"last_seen": _iso(T0 + 999)}))
    (data / "state" / "arturo").mkdir()
    (data / "state" / "arturo" / "active-surface.json").write_text(json.dumps({"devices": {
        "ios": {"route": "/agents", "updated_at": T0 + 10},
        "watch": {"route": None, "updated_at": T0 + 500}}}))      # null route = backgrounded: not on screen
    A.record_presence(data, "web:c1", label="Chrome on macOS", surface="web", now=T0 + 5)
    a = A.pick_anchor(data, "screen")
    assert a["ref"] == "surface:ios" and a["at"] == T0 + 10


def test_presence_is_debounced_and_stores_only_label_surface_time(data):
    assert A.record_presence(data, "dev:d1", label="iPhone", surface="phone", now=T0) is True
    assert A.record_presence(data, "dev:d1", label="iPhone", surface="phone", now=T0 + 30) is False
    assert A.record_presence(data, "dev:d1", label="iPhone", surface="phone", now=T0 + 61) is True
    row = json.loads((data / "state" / "away-presence.json").read_text())["dev:d1"]
    assert set(row) == {"label", "surface", "foreground_at"}


# ---- operator-actions ledger (gm msg_71cd9caa) -------------------------------------------------------

def test_ledger_row_is_cut_scanned_and_0600(data):
    long = "please " * 60
    row = A.record_message(data, "gm", long, device="d_1:iPhone", surface="phone", scan=no_secrets, now=T0)
    assert len(row["said"]) <= A.SAID_MAX and row["device"] == "d_1:iPhone"
    p = data / "state" / "operator-actions.jsonl"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert A.pick_anchor(data)["kind"] == "message"


def test_ledger_stores_a_flagged_excerpt_as_null_never_in_part(data):
    scan = lambda b: "secret" if b"ghp_" in b else None
    row = A.record_message(data, "gm", "use ghp_abcdefabcdef please", scan=scan, now=T0)
    assert row["said"] is None
    assert "ghp_" not in (data / "state" / "operator-actions.jsonl").read_text()


def test_ledger_rotates_at_the_cap_keeping_one(data, monkeypatch):
    monkeypatch.setattr(A, "LEDGER_MAX_BYTES", 200)
    for i in range(10):
        A.record_message(data, "gm", f"message {i} " + "x" * 40, scan=no_secrets, now=T0 + i)
    p = data / "state" / "operator-actions.jsonl"
    assert p.stat().st_size < 400 and (data / "state" / "operator-actions.jsonl.1").exists()
    assert not (data / "state" / "operator-actions.jsonl.1.1").exists()
    assert A.pick_anchor(data)["said"].startswith("message 9")


def test_no_other_shipped_file_reads_the_ledger():
    import subprocess
    root = HERE.parent
    out = subprocess.run(["git", "grep", "-l", "operator-actions.jsonl"], cwd=root, capture_output=True, text=True).stdout
    readers = {f for f in out.split() if not f.rsplit("/", 1)[-1].startswith("test_") and not f.startswith("docs/")}
    assert readers == {"scripts/away.py"}, readers


def test_a_journal_summary_the_scanner_flags_is_not_served(data):
    _call(data, "vc_1", T0, ended=T0 + 30, summary="the key is sk-live-ABC")
    scan = lambda b: "secret" if b"sk-live" in b else None
    out = A.build(data, [], [], scan=scan)
    assert out["anchor"]["outcome"]["state"] == "done" and out["anchor"]["outcome"]["summary"] is None


def test_a_bare_device_id_stamp_names_no_device(data):
    """_answer_device stamps a device that has no label as its bare id. The card names no device for it."""
    _ledger(data, T0, device="d_7", surface=None)
    a = A.pick_anchor(data)
    assert a["device"] is None and a["device_source"] == "none"


def test_an_answered_card_whose_agent_still_waits_is_working_not_waiting_on_you(data):
    _answer(data, "apr_1", T0, agent="gm")
    a = A.pick_anchor(data)
    assert A.outcome(a, [{"id": "gm", "state": "waiting"}], [])["state"] == "working"
    newer = [{"id": "apr_2", "from_agent": "gm", "question": "next?", "created_at": _iso(T0 + 5)}]
    assert A.outcome(a, [{"id": "gm", "state": "waiting"}], newer)["state"] == "waiting_on_you"
    assert A.outcome(a, [{"id": "gm", "state": "idle", "last_used_ts": T0 + 9}], [])["state"] == "done"
