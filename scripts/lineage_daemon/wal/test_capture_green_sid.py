"""RED (BG leg-(ii) P2.7, Design 2 — green-sid capture).

The green's real claude session_id is never captured at spawn today
(spawn_green.py:120-127 records pane_pid, not sid), so canonical ends
session_id=null after a swap (the M3 gap). Design 2 fixes this with a
deterministic green SessionStart hook that writes state/wal/<alias>.sid from the
hook's stdin payload — NO log parsing, marker-gated on BG_GREEN_ALIAS exactly like
green_boot_probe (a normal session never has the marker → INERT no-op).

Each test drives the REAL capture_green_sid.main() (not a mock of it) with a real
stdin JSON payload + a real temp orchestra dir, and asserts the file effect — the
leg-(i) lesson: the decisive test must exercise the live code path.

RED until scripts/lineage_daemon/wal/capture_green_sid.py exists.
"""
import io
import json
import os

import pytest

from scripts.lineage_daemon.wal import capture_green_sid


def _feed_stdin(monkeypatch, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def _sid_path(orch, alias):
    return os.path.join(orch, "state", "wal", f"{alias}.sid")


def test_captures_sid_to_wal_file_when_marker_set(tmp_path, monkeypatch):
    orch = str(tmp_path)
    os.makedirs(os.path.join(orch, "state", "wal"))
    monkeypatch.setenv("ORCHESTRA_DIR", orch)
    monkeypatch.setenv("BG_GREEN_ALIAS", "second-brain-dev-g5")
    _feed_stdin(monkeypatch, {"hook_event_name": "SessionStart",
                              "session_id": "abc123-real-green-sid"})

    rc = capture_green_sid.main()

    assert rc == 0
    p = _sid_path(orch, "second-brain-dev-g5")
    assert os.path.exists(p), "green-sid capture did not write state/wal/<alias>.sid"
    assert open(p).read().strip() == "abc123-real-green-sid"


def test_inert_no_marker_writes_nothing(tmp_path, monkeypatch):
    orch = str(tmp_path)
    os.makedirs(os.path.join(orch, "state", "wal"))
    monkeypatch.setenv("ORCHESTRA_DIR", orch)
    monkeypatch.delenv("BG_GREEN_ALIAS", raising=False)
    _feed_stdin(monkeypatch, {"hook_event_name": "SessionStart",
                              "session_id": "should-not-be-written"})

    rc = capture_green_sid.main()

    assert rc == 0  # INERT: a normal (non-BG) session is unaffected
    # nothing under state/wal was created for a sid
    assert not any(f.endswith(".sid") for f in os.listdir(os.path.join(orch, "state", "wal")))


def test_failsafe_on_empty_stdin_never_crashes(tmp_path, monkeypatch):
    orch = str(tmp_path)
    os.makedirs(os.path.join(orch, "state", "wal"))
    monkeypatch.setenv("ORCHESTRA_DIR", orch)
    monkeypatch.setenv("BG_GREEN_ALIAS", "second-brain-dev-g5")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # no JSON at all

    rc = capture_green_sid.main()

    assert rc == 0  # hook contract: always exit 0, never abort the green boot
    assert not os.path.exists(_sid_path(orch, "second-brain-dev-g5"))


def test_missing_session_id_writes_nothing(tmp_path, monkeypatch):
    orch = str(tmp_path)
    os.makedirs(os.path.join(orch, "state", "wal"))
    monkeypatch.setenv("ORCHESTRA_DIR", orch)
    monkeypatch.setenv("BG_GREEN_ALIAS", "second-brain-dev-g5")
    _feed_stdin(monkeypatch, {"hook_event_name": "SessionStart"})  # no session_id

    rc = capture_green_sid.main()

    assert rc == 0
    assert not os.path.exists(_sid_path(orch, "second-brain-dev-g5"))


# ---- (a) register_green_session: the ACTIVE green-side #1 for a runtime with no claude
#         SessionStart-stdin hook (gemini) — resolve the green's live cid by effect and
#         write it DB-first + flat (via update_session) + .sid, BEFORE spawn returns. -----

def test_register_green_session_resolves_writes_db_and_sid(tmp_path):
    orch = str(tmp_path)
    os.makedirs(os.path.join(orch, "state", "wal"))
    alias = "demo-gemini-pred-g2"
    cid = "206f75be-cbcd-44c6-8c86-8f87ef3c7eff"
    calls = {"polls": 0, "update": None, "project": 0}

    def resolve_cid(seat):
        calls["polls"] += 1
        return cid if calls["polls"] >= 2 else None   # resolves on the 2nd poll (booting)

    def update_session(agent_id, fields, full_record=None):
        calls["update"] = (agent_id, fields, full_record)
        return True

    def project():
        calls["project"] += 1

    out = capture_green_sid.register_green_session(
        orch, alias, resolve_cid_fn=resolve_cid, update_session_fn=update_session,
        project_fn=project, poll_attempts=5, sleep_fn=lambda: None)

    assert out == cid
    # DB-first: update_session got the green alias + the live cid
    assert calls["update"][0] == alias
    assert calls["update"][1].get("session_id") == cid
    assert calls["project"] == 1  # flat projection refreshed
    # .sid written for build_obs/M3/(b) to read
    p = _sid_path(orch, alias)
    assert os.path.exists(p) and open(p).read().strip() == cid


def test_register_green_session_unresolvable_is_bounded_none(tmp_path):
    """A green that never becomes resolvable within the poll budget => None, no DB write,
    no .sid, and it does NOT hang (bounded). spawn_green then records the breadcrumb."""
    orch = str(tmp_path)
    os.makedirs(os.path.join(orch, "state", "wal"))
    alias = "demo-gemini-pred-g2"
    updated = []

    out = capture_green_sid.register_green_session(
        orch, alias, resolve_cid_fn=lambda s: None,
        update_session_fn=lambda *a, **k: updated.append(a) or True,
        project_fn=lambda: None, poll_attempts=3, sleep_fn=lambda: None)

    assert out is None
    assert updated == []
    assert not os.path.exists(_sid_path(orch, alias))
