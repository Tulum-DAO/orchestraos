"""RED-first — canonical.status truth reconcile (Identity Layer v1 item (a), gm-specified).

INVARIANT under test: canonical.status=='online' <=> the row's tmux_session exists AND
its pane pid has >=1 live child process (by effect, NEVER by name). Otherwise 'parked'.

'online' is only ever written at insert/promote; nothing sets a seat offline (the 228
phantom-canonical class). This reconciler is the missing offline writer:
  online && !live  -> 'parked'
  parked && live   -> 'online'   (the ios-watch-dev-gen16 class)
A seat with an attached tmux CLIENT is LOGGED and SKIPPED (never flip a seat a human is on).
Rows/generations/sids untouched; resume_command preserved. Flat follows via project_now,
so both the typed canonical.status AND the source_record doc status must flip (M1-clean).
"""
import json
import os
from pathlib import Path

import pytest

from scripts.identity_store import orchestra_db, status_reconcile


def _seed_seat(conn, root, *, status, tmux, gen=1, sid=None, runtime="claude"):
    """Seed one canonical seat: lineage + non-retired generation + canonical row +
    a source_records registry.json agent doc whose status matches the canonical row."""
    conn.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd) VALUES (?,?,?,?,?)",
                 (root, "T2", runtime, "vps", "/home/testuser/repos/x"))
    cur = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model, spawned_at) "
        "VALUES (?,?,?,?,?)", (root, gen, sid, "claude-opus-4-8[1m]", "2026-09-15T00:00:00Z"))
    gid = cur.lastrowid
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) VALUES (?,?,?,?)",
                 (root, gid, tmux, status))
    doc = {"name": root, "generation": gen, "session_id": sid, "runtime": runtime,
           "model": "claude-opus-4-8[1m]", "status": status, "tier": "T2",
           "machine": "vps", "cwd": "/home/testuser/repos/x", "tmux_session": tmux,
           "resume_command": (f"claude --resume {sid} --dangerously-skip-permissions" if sid else None)}
    conn.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
                 "VALUES ('registry.json','agent',?,?,?)", (root, 0, json.dumps(doc)))
    conn.commit()


@pytest.fixture
def od(tmp_path, monkeypatch):
    """A sandbox orchestra dir with an armed cutover flag so the sanctioned writer runs."""
    (tmp_path / "state").mkdir()
    db = tmp_path / "state" / "orchestra-registry.db"
    orchestra_db.init_db(str(db))
    (tmp_path / "state" / "identity-store-cutover.flag").write_text("armed\n")
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    return tmp_path


def _fns(live_set, child_set, attached_set):
    return dict(
        has_session_fn=lambda t: t in live_set,
        pane_has_child_fn=lambda t: t in child_set,
        list_clients_fn=lambda t: "client x" if t in attached_set else "",
    )


def test_dry_run_reports_exactly_the_two_flips_and_skips_attached(od):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "seatA", status="online", tmux="seatA", sid="aaaa")   # online+live -> keep
    _seed_seat(conn, "seatB", status="online", tmux="seatB", sid="bbbb")   # online+dead -> park
    _seed_seat(conn, "seatC", status="parked", tmux="seatC", sid="cccc")   # parked+live -> online
    _seed_seat(conn, "seatD", status="online", tmux="seatD", sid="dddd")   # online+live+ATTACHED -> skip
    conn.close()
    live = {"seatA", "seatC", "seatD"}
    rep = status_reconcile.reconcile(str(od), apply=False,
                                     **_fns(live, live, {"seatD"}))
    assert set(rep["to_parked"]) == {"seatB"}
    assert set(rep["to_online"]) == {"seatC"}
    assert set(rep["attached_skipped"]) == {"seatD"}
    assert rep["applied"] is False
    # dry-run wrote NOTHING
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    st = {r["root"]: r["status"] for r in conn.execute("SELECT root,status FROM canonical")}
    conn.close()
    assert st == {"seatA": "online", "seatB": "online", "seatC": "parked", "seatD": "online"}


def test_apply_flips_both_and_leaves_attached_and_updates_doc(od):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "seatA", status="online", tmux="seatA", sid="aaaa")
    _seed_seat(conn, "seatB", status="online", tmux="seatB", sid="bbbb")
    _seed_seat(conn, "seatC", status="parked", tmux="seatC", sid="cccc")
    _seed_seat(conn, "seatD", status="online", tmux="seatD", sid="dddd")
    conn.close()
    live = {"seatA", "seatC", "seatD"}
    rep = status_reconcile.reconcile(str(od), apply=True, **_fns(live, live, {"seatD"}))
    assert rep["applied"] is True
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    st = {r["root"]: r["status"] for r in conn.execute("SELECT root,status FROM canonical")}
    # source_record doc status must flip too (M1-clean / roster follows)
    docs = {r["key"]: json.loads(r["payload_json"])["status"]
            for r in conn.execute("SELECT key,payload_json FROM source_records WHERE kind='agent'")}
    conn.close()
    assert st == {"seatA": "online", "seatB": "parked", "seatC": "online", "seatD": "online"}
    assert docs["seatB"] == "parked" and docs["seatC"] == "online"
    assert docs["seatA"] == "online" and docs["seatD"] == "online"  # untouched


def test_service_liveness_uses_declared_probe_not_tmux():
    """A service with a port probe is live iff the port LISTENs — NOT tmux (item-b addendum)."""
    sr = status_reconcile
    # a known service with a port probe
    assert "proxy" in sr.SERVICE_PROBES and "port" in sr.SERVICE_PROBES["proxy"]
    live, reason = sr.service_is_live("proxy", port_listening_fn=lambda p: True,
                                      pane_has_child_fn=lambda t: False)
    assert live is True and reason.startswith("service:port")
    live, reason = sr.service_is_live("proxy", port_listening_fn=lambda p: False,
                                      pane_has_child_fn=lambda t: True)
    assert live is False
    # unknown service -> no probe -> not live, LOUD reason (never guessed online)
    live, reason = sr.service_is_live("mystery-svc", port_listening_fn=lambda p: True,
                                      pane_has_child_fn=lambda t: True)
    assert live is False and reason == "service:no-probe"


def test_reconcile_reonlines_a_parked_service_whose_port_is_live(od):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "proxy", status="parked", tmux="proxy", sid=None, runtime="service")
    conn.close()
    rep = status_reconcile.reconcile(
        str(od), apply=False,
        has_session_fn=lambda t: False, pane_has_child_fn=lambda t: False,
        list_clients_fn=lambda t: "", port_listening_fn=lambda p: True)  # port 8091 up
    assert set(rep["to_online"]) == {"proxy"}
    assert rep["service_no_probe"] == []


def test_live_requires_both_session_and_a_child_process(od):
    """A husk pane (session exists, but pane pid has NO child = agent exited) is NOT live."""
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "husk", status="online", tmux="husk", sid="hhhh")
    conn.close()
    # session exists but no child process -> not live -> park
    rep = status_reconcile.reconcile(str(od), apply=False,
                                     has_session_fn=lambda t: True,
                                     pane_has_child_fn=lambda t: False,
                                     list_clients_fn=lambda t: "")
    assert set(rep["to_parked"]) == {"husk"}
