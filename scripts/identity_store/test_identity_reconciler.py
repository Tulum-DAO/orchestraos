"""RED-first — identity_reconciler v1 (DEC-1789507884583046, CONSENSUS_REACHED).

The 8 named tests from the DEC section 6, in order. Each seeds a tmp_path sandbox
DB (armed cutover) and drives ``identity_reconciler`` with dependency-injected
tmux/proc/registry functions — never the live fleet (see conftest.py hermeticity
guard). Test 7 (grep-gate) lives in
``scripts/lineage_daemon/wal/provider_agnostic_calibration_test.py`` — this module
was added to its ``CORE_FILES`` list rather than duplicating the pattern here.
"""
import json
import os

import pytest

from scripts.identity_store import identity_reconciler, orchestra_db


def _seed_seat(conn, root, *, status, tmux, gen=1, sid=None, runtime="claude",
               resume_command=None, machine="vps"):
    conn.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd) VALUES (?,?,?,?,?)",
                 (root, "T2", runtime, machine, "/home/testuser/repos/x"))
    cur = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model, spawned_at, "
        "resume_command) VALUES (?,?,?,?,?,?)",
        (root, gen, sid, "claude-opus-4-8[1m]", "2026-09-15T00:00:00Z", resume_command))
    gid = cur.lastrowid
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                 "VALUES (?,?,?,?)", (root, gid, tmux, status))
    doc = {"name": root, "generation": gen, "session_id": sid, "runtime": runtime,
           "model": "claude-opus-4-8[1m]", "status": status, "tier": "T2",
           "machine": machine, "cwd": "/home/testuser/repos/x", "tmux_session": tmux,
           "resume_command": resume_command}
    conn.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
                 "VALUES ('registry.json','agent',?,?,?)", (root, 0, json.dumps(doc)))
    conn.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
                 "VALUES ('agent-sessions.json','session',?,?,?)", (root, 0, json.dumps(doc)))
    conn.commit()
    return gid


@pytest.fixture
def od(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "wal").mkdir()
    (tmp_path / "logs").mkdir()
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
        port_listening_fn=lambda p: False,
    )


def _status(od, root):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    try:
        row = conn.execute("SELECT status FROM canonical WHERE root=?", (root,)).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. test_reconciler_hysteresis
# ---------------------------------------------------------------------------

def test_reconciler_hysteresis(od):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "deadseat", status="online", tmux="deadseat", sid="s1",
              resume_command="claude --resume s1")
    _seed_seat(conn, "recoverseat", status="parked", tmux="recoverseat", sid="s2")
    conn.close()

    fns = _fns(live_set=set(), child_set=set(), attached_set=set())  # nothing live
    fns_recover = dict(fns)
    fns_recover["has_session_fn"] = lambda t: t == "recoverseat"
    fns_recover["pane_has_child_fn"] = lambda t: t == "recoverseat"

    # PASS 1 (--cron / apply=True): dead once -> dead-1, NOT yet parked. Recovery is
    # immediate (1 pass) since it's a DIFFERENT direction (recover, not dead).
    rep1 = identity_reconciler.liveness_pass(str(od), apply=True, **fns_recover)
    assert rep1["dead1"] == ["deadseat"]
    assert rep1["confirmed_park"] == [] and rep1["confirmed_retire"] == []
    assert rep1["recovered"] == ["recoverseat"]
    assert _status(od, "deadseat") == "online"        # unchanged after 1 dead pass
    assert _status(od, "recoverseat") == "online"      # recovered in 1 pass

    hyst = identity_reconciler.load_hysteresis(str(od))
    assert hyst.get("deadseat") == 1

    # PASS 2: second consecutive dead pass -> confirmed (resume_command present -> park).
    rep2 = identity_reconciler.liveness_pass(str(od), apply=True, **fns_recover)
    assert rep2["confirmed_park"] == ["deadseat"]
    assert _status(od, "deadseat") == "parked"


def test_reconciler_hysteresis_dry_run_never_persists(od):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "deadseat", status="online", tmux="deadseat", sid="s1")
    conn.close()
    fns = _fns(live_set=set(), child_set=set(), attached_set=set())
    for _ in range(3):
        rep = identity_reconciler.liveness_pass(str(od), apply=False, **fns)
        assert rep["dead1"] == ["deadseat"]   # never advances past dead-1 in dry-run
    assert identity_reconciler.load_hysteresis(str(od)) == {}
    assert _status(od, "deadseat") == "online"   # zero writes


# ---------------------------------------------------------------------------
# 2. test_reconciler_skip_set
# ---------------------------------------------------------------------------

def test_reconciler_skip_set(od):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "attached", status="online", tmux="attached", sid="a1")
    _seed_seat(conn, "remote", status="online", tmux="remote", sid="r1", machine="kai-mac")
    _seed_seat(conn, "greenling", status="provisional", tmux="greenling", sid=None)
    _seed_seat(conn, "midfire", status="online", tmux="midfire", sid="m1")
    conn.close()
    (od / "state" / "wal" / "midfire.bg.json").write_text(
        json.dumps({"state": "SWAPPING", "root": "midfire"}))

    fns = _fns(live_set=set(), child_set=set(),
              attached_set={"attached"})  # every non-attached tmux reads dead

    rep = identity_reconciler.liveness_pass(str(od), apply=True, **fns)

    assert "attached" not in rep["dead1"] + rep["confirmed_park"] + rep["confirmed_retire"]
    assert "attached" in rep["attached_skipped"]
    assert "remote" in rep["nonlocal_skipped"]
    assert "greenling" in rep["skipped_provisional"]
    assert "midfire" in rep["skipped_midfire"]

    # none of the 4 skip-set seats were WRITTEN
    assert _status(od, "attached") == "online"
    assert _status(od, "remote") == "online"
    assert _status(od, "greenling") == "provisional"
    assert _status(od, "midfire") == "online"


# ---------------------------------------------------------------------------
# 3. test_reconciler_setsid_descendant
# ---------------------------------------------------------------------------

def test_reconciler_setsid_descendant():
    """A live descendant 2+ ppid-chain hops below the pane pid counts as live —
    the scan blind spot a ONE-LEVEL `pgrep -P <pane_pid>` cannot see beyond its
    single hop. full_tree_pane_has_child walks the WHOLE chain."""
    pane_pid = 100
    # 100 -> 250 (a wrapper/shell) -> 300 (the actual live agent, setsid'd)
    all_pids = {250: pane_pid, 300: 250, 9999: 1}   # 9999 unrelated to the pane
    has_child = identity_reconciler.full_tree_pane_has_child(
        "seat", pane_pids_fn=lambda t: [pane_pid], all_pids_fn=lambda: all_pids)
    assert has_child is True

    # No pid anywhere traces back to the pane pid -> correctly NOT live (no
    # false positive from an unrelated pid table).
    all_pids_dead = {9999: 1, 8888: 1}
    has_child_dead = identity_reconciler.full_tree_pane_has_child(
        "seat", pane_pids_fn=lambda t: [pane_pid], all_pids_fn=lambda: all_pids_dead)
    assert has_child_dead is False


# ---------------------------------------------------------------------------
# 4. test_reconciler_sid_resolve_writes_db_first
# ---------------------------------------------------------------------------

def test_reconciler_sid_resolve_writes_db_first(od):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "seatE", status="online", tmux="seatE", sid="old-sid", runtime="claude")
    _seed_seat(conn, "seatF", status="online", tmux="seatF", sid="old-sid-f", runtime="claude")
    conn.close()

    registered = []

    def fake_register(root):
        registered.append(root)
        c = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
        try:
            c.execute("UPDATE generations SET session_id=? WHERE root=?", ("new-sid", root))
            c.commit()
        finally:
            c.close()
        return "new-sid"

    alarms = []
    resolve_registry = {
        "claude": lambda root: "new-sid" if root == "seatE" else None,  # seatF ambiguous/unresolvable
    }
    rep = identity_reconciler.sid_resolve_pass(
        str(od), apply=True, live_roots={"seatE", "seatF"},
        resolve_registry=resolve_registry, register_sid_fn=fake_register,
        send_fn=lambda subj, body: alarms.append(body))

    assert registered == ["seatE"]
    assert {f["root"] for f in rep["fixed"]} == {"seatE"}
    assert rep["unresolved"] == ["seatF"]
    assert any("seatF" in a and "UNRESOLVABLE" in a.upper() or "AMBIGUOUS" in a.upper()
              for a in alarms)

    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    sids = {r["root"]: r["session_id"] for r in conn.execute(
        "SELECT root, session_id FROM generations")}
    conn.close()
    assert sids["seatE"] == "new-sid"        # DB-first write happened
    assert sids["seatF"] == "old-sid-f"      # unresolved -> untouched (fail-closed)


# ---------------------------------------------------------------------------
# gm GATE fix — never revert a canonical sid to a retired predecessor's sid, even
# when the predecessor's `<root>-genN` pane is deliberately kept alive/resumable
# (the rotated-root ambiguity: TWO live processes for one root, and a by-name
# provider resolver can match the predecessor's own boot declaration).
# ---------------------------------------------------------------------------

def test_reconciler_never_reverts_to_retired_predecessor(od):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    conn.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd) VALUES "
                 "('R','T2','claude','vps','/home/testuser/repos/x')")
    # gen1 — RETIRED predecessor, sid Y, but its pane is deliberately kept alive.
    g1 = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model, spawned_at, "
        "retired_at, resume_command) VALUES ('R',1,'sid-Y','claude-opus-4-8[1m]',"
        "'2026-09-14T00:00:00Z','2026-09-15T00:00:00Z','claude --resume sid-Y')").lastrowid
    # gen2 — the CANONICAL, promoted, sid X.
    g2 = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model, spawned_at) "
        "VALUES ('R',2,'sid-X','claude-opus-4-8[1m]','2026-09-15T00:00:00Z')").lastrowid
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                 "VALUES ('R', ?, 'R', 'online')", (g2,))
    conn.commit()
    conn.close()

    alarms = []
    # The BUGGY resolver behavior: a by-name scan matches the predecessor's own
    # boot declaration (its pane R-gen1 is alive) and returns sid-Y instead of X.
    resolve_registry = {"claude": lambda root: "sid-Y"}
    rep = identity_reconciler.sid_resolve_pass(
        str(od), apply=True, live_roots={"R"}, resolve_registry=resolve_registry,
        register_sid_fn=lambda root: pytest.fail("must NEVER write an ambiguous sid"),
        send_fn=lambda subj, body: alarms.append(body))

    assert rep["fixed"] == []
    assert rep["ambiguous_foreign_generation"] == ["R"]
    assert any("ambiguous" in a.lower() and "R" in a for a in alarms)
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    sid = conn.execute("SELECT session_id FROM generations WHERE id=?", (g2,)).fetchone()[0]
    conn.close()
    assert sid == "sid-X"   # untouched — never reverted to the retired predecessor

    # Second case: the predecessor's pane is DEAD (fully gone) — a retired
    # generation's sid is STILL never a valid target, regardless of liveness.
    # (foreign_generation_holder is a DB-fact check, not a liveness check, so
    # this is already covered by construction — assert explicitly for clarity.)
    assert identity_reconciler._foreign_generation_holder(str(od), "R", g2, "sid-Y") is True
    assert identity_reconciler._foreign_generation_holder(str(od), "R", g2, "sid-X") is False
    assert identity_reconciler._foreign_generation_holder(str(od), "R", g2, "sid-never-seen") is False


# ---------------------------------------------------------------------------
# gm follow-up (post-first-live-pass) — ALARM DEDUPE: the routine fail-closed
# sid-resolve notices must not re-page gm every */15 pass for an unchanged
# condition. Same (seat, reason, state) -> 1 alarm across passes; a state/reason
# CHANGE -> a fresh alarm; the same key going stale (>24h since last page) ->
# re-alarm.
# ---------------------------------------------------------------------------

def test_reconciler_alarm_dedupe(od):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "seatZ", status="online", tmux="seatZ", sid="stored-sid", runtime="claude")
    conn.close()

    clock = {"t": 1_000_000.0}

    def alarm_count(resolve_fn):
        alarms = []
        rep = identity_reconciler.sid_resolve_pass(
            str(od), apply=False, live_roots={"seatZ"},
            resolve_registry={"claude": resolve_fn}, register_sid_fn=lambda r: None,
            send_fn=lambda subj, body: alarms.append(body),
            now_fn=lambda: clock["t"])
        return alarms, rep

    always_unresolved = lambda root: None

    # PASS 1: first sighting -> 1 alarm.
    alarms1, rep1 = alarm_count(always_unresolved)
    assert len(alarms1) == 1
    assert rep1["alarms_suppressed"] == 0

    # PASS 2: same seat, same reason (unresolvable), 60s later -> SUPPRESSED.
    clock["t"] += 60
    alarms2, rep2 = alarm_count(always_unresolved)
    assert len(alarms2) == 0
    assert rep2["alarms_suppressed"] == 1

    # PASS 3: STATE CHANGE (now ambiguous instead of unresolvable — a different
    # reason-class/state key) -> a FRESH alarm, not suppressed. Seed a foreign
    # generation so the ambiguity gate has a genuine cross-check to trip.
    clock["t"] += 60
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    conn.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd) VALUES "
                 "('other','T2','claude','vps','/home/testuser/repos/x')")
    conn.execute("INSERT INTO generations (root, generation, session_id, model, spawned_at) "
                 "VALUES ('other',1,'foreign-sid','claude-opus-4-8[1m]','2026-09-15T00:00:00Z')")
    conn.commit()
    conn.close()

    def foreign_holder_always(root, gid, sid):
        return sid == "foreign-sid"

    def ambiguous_call():
        alarms = []
        rep = identity_reconciler.sid_resolve_pass(
            str(od), apply=False, live_roots={"seatZ"},
            resolve_registry={"claude": lambda root: "foreign-sid"},
            register_sid_fn=lambda r: None,
            foreign_holder_fn=foreign_holder_always,
            send_fn=lambda subj, body: alarms.append(body),
            now_fn=lambda: clock["t"])
        return alarms, rep

    alarms3, rep3 = ambiguous_call()
    assert len(alarms3) == 1, "a reason/state CHANGE must re-alarm, not suppress"
    assert rep3["alarms_suppressed"] == 0

    # PASS 4: SAME ambiguous state again, 60s later -> suppressed.
    clock["t"] += 60
    alarms4, rep4 = ambiguous_call()
    assert len(alarms4) == 0
    assert rep4["alarms_suppressed"] == 1

    # PASS 5: SAME key, but >24h since the LAST alarm (pass 3) -> re-alarm.
    clock["t"] += 24 * 3600 + 1
    alarms5, rep5 = ambiguous_call()
    assert len(alarms5) == 1, ">24h since last alarm must re-page"
    assert rep5["alarms_suppressed"] == 0


# ---------------------------------------------------------------------------
# 5. test_reconciler_cutover_off_loud
# ---------------------------------------------------------------------------

def test_reconciler_cutover_off_loud(od):
    os.remove(od / "state" / "identity-store-cutover.flag")   # cutover OFF
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "seatG", status="online", tmux="seatG", sid="g1")
    conn.close()

    alarms = []
    rep, rc = identity_reconciler.run_pass(
        str(od), apply=True, send_fn=lambda subj, body: alarms.append(body))

    assert rc != 0
    assert rep["gate_failed"] is True
    assert len(alarms) >= 1 and "cutover" in alarms[0].lower()
    # zero writes: seatG untouched
    assert _status(od, "seatG") == "online"
    assert identity_reconciler.load_hysteresis(str(od)) == {}


# ---------------------------------------------------------------------------
# 6. test_reconciler_order
# ---------------------------------------------------------------------------

def test_reconciler_order(od, monkeypatch):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "seatH", status="online", tmux="seatH", sid="h1")
    conn.close()

    calls = []
    real_sid = identity_reconciler.sid_resolve_pass

    def spy_sid(*a, **k):
        calls.append("sid")
        k.setdefault("resolve_registry", {})
        k.setdefault("register_sid_fn", lambda r: None)
        return real_sid(*a, **k)

    from scripts.identity_store import model_reconcile as mr
    real_model = mr.reconcile

    def spy_model(*a, **k):
        calls.append("model")
        return real_model(*a, **k)

    monkeypatch.setattr(identity_reconciler, "sid_resolve_pass", spy_sid)
    monkeypatch.setattr(identity_reconciler, "model_reconcile", mr)
    monkeypatch.setattr(mr, "reconcile", spy_model)

    fns = _fns(live_set={"seatH"}, child_set={"seatH"}, attached_set=set())
    identity_reconciler.run_pass(str(od), apply=False, skip_guards=True, **fns)

    assert calls == ["sid", "model"], f"model must run strictly AFTER sid: {calls}"


# ---------------------------------------------------------------------------
# 8. test_reconciler_guards_green_post_pass
# ---------------------------------------------------------------------------

def test_reconciler_guards_green_post_pass(od):
    conn = orchestra_db.get_connection(str(od / "state" / "orchestra-registry.db"))
    _seed_seat(conn, "seatI", status="online", tmux="seatI", sid="i1")
    conn.close()
    fns = _fns(live_set={"seatI"}, child_set={"seatI"}, attached_set=set())

    green_runner = lambda files: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    rep, rc = identity_reconciler.run_pass(
        str(od), apply=True, guard_runner=green_runner, **fns)
    assert rc == 0
    assert rep["guards"]["returncode"] == 0
    assert "red" not in rep["guards"]

    alarms = []
    red_runner = lambda files: type("R", (), {"returncode": 1, "stdout": "FAIL", "stderr": ""})()
    rep2, rc2 = identity_reconciler.run_pass(
        str(od), apply=True, guard_runner=red_runner,
        send_fn=lambda subj, body: alarms.append(body), **fns)
    assert rc2 == 0   # the PASS itself still completes; guard-red is a LOUD alarm, not a crash
    assert rep2["guards"]["red"] is True
    assert any("guard" in a.lower() for a in alarms)
    # NEVER auto-revert: seatI's post-pass state is untouched by the red guard result
    assert _status(od, "seatI") == "online"
