"""BG sequence A (bar#4, RED) — the green-boot hook PRODUCES its probe answer from the
INGESTED view delivered through the REAL lineage_hydrate msg_store channel (not the WAL db,
not a side file), and the REAL verify seam grades it. Covers gm ruling DEC-1788609056:

  A1  faithful ingested artifact (delivered via msg_store) -> hook -> verify seam TRUE
  A1b a LOSSY delivered artifact (dropped/garbled event) -> verify FALSE (the real signal)
  A2  INERT: marker absent -> hook is a no-op, writes no probe.json
  A3  fail-safe: no lineage_hydrate row delivered -> hook returns non-zero, no crash, no probe.json
  A5  attribution: writes <green_alias>.probe.json

Self-contained: uses a FULL-scope artifact (fresh green: delta == full WAL) so it grades
without RAB's delivered-scope-aware grade_probe (the mid-life scope case wires in with that).
"""
import json
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import probe_answer, bg_beat, green_boot_probe  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402
from msg_store import MessageStore  # noqa: E402


def _seed_wal(wal_dir, root, n=6):
    """Blue's capture = the answer key (mirror probe_answer_test._seed)."""
    store = WalStore(os.path.join(wal_dir, f"{root}.db"))
    for i in range(n):
        kind = "file_mod" if i % 2 == 0 else "response"
        body = f"git:/cwd#path{i}.py" if kind == "file_mod" else None
        store.append(ts=1000 + i, lineage_root=root, generation=2, sid="b",
                     runtime="claude", kind=kind, summary=f"e{i}",
                     body_ref=body, source_path="x")
    return store


def _artifact_from_wal(store, root, *, since=0, through=None):
    """The structured ingested-view a FAITHFUL hydrate would deliver, from the WAL."""
    evs = [{"seq": r["seq"], "kind": r["kind"], "summary": r["summary"],
            "body_ref": r["body_ref"]} for r in store.events(root)]
    thr = through if through is not None else (evs[-1]["seq"] if evs else 0)
    return {"lineage_root": root, "scope": {"since_seq": since, "through_seq": thr},
            "events": evs}


def _ensure_msg_db(db):
    import sqlite3
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT,
          task_id TEXT, parent_id TEXT, type TEXT, from_agent TEXT, to_agent TEXT,
          subject TEXT, body TEXT, priority TEXT DEFAULT 'medium', source TEXT DEFAULT 'system',
          status TEXT DEFAULT 'pending', retry_count INTEGER DEFAULT 0, max_retries INTEGER DEFAULT 5,
          metadata TEXT, created_at TEXT, attempted_at TEXT, delivered_at TEXT,
          acknowledged_at TEXT, archived_at TEXT, error TEXT, depends_on TEXT,
          gather_mode TEXT DEFAULT 'gather_all', tenant_id TEXT DEFAULT 'operator');
        CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, subject TEXT,
          participants TEXT, task_id TEXT, created_at TEXT, updated_at TEXT,
          tenant_id TEXT DEFAULT 'operator');
    """)
    conn.close()


def _deliver_hydrate(orch, green_alias, artifact):
    """Deliver the artifact through the REAL lineage_hydrate msg_store channel (metadata),
    the same channel hydrate_green._default_deliver uses. The hook parses THIS row."""
    db = os.path.join(orch, "state", "tasks.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    _ensure_msg_db(db)
    MessageStore(db_path=db).send(
        from_agent="lineage-hydrate", to_agent=green_alias, type="lineage_hydrate",
        subject="hydrate", body="(digest text)", priority="high",
        metadata={"ingested_view": artifact})


def _mk_seams(root, orch, wal_dir):
    blue = {"generation": 1, "model": "claude-opus-4-8[1m]"}
    spawn_fn, verify_fn, hydrate_fn, reap_fn, produce_fn = bg_beat._live_effect_seams(
        root, str(orch), str(wal_dir), blue, live_fn=lambda r, g: True)  # answer_fn defaults to the file reader
    return bg_beat._Seams(
        register_provisional=lambda *a, **k: None, project_now=lambda *a, **k: True,
        swap=lambda *a, **k: None, spawn_fn=spawn_fn, verify_fn=verify_fn,
        hydrate_fn=hydrate_fn, reap_fn=reap_fn, produce_fn=produce_fn, timeout_s=10.0)


def _prep(tmp_path):
    root = "bg-drill-victim"
    green_alias = f"{root}-g2"
    (tmp_path / "state" / "agent-handoffs").mkdir(parents=True)
    wal_dir = tmp_path / "state" / "wal"; wal_dir.mkdir(parents=True)
    store = _seed_wal(str(wal_dir), root)
    return root, green_alias, wal_dir, store


# --------------------------------------------------------------------------
def test_a1_hook_produces_answer_from_delivered_artifact_verify_true(tmp_path):
    root, green_alias, wal_dir, store = _prep(tmp_path)
    seams = _mk_seams(root, tmp_path, wal_dir)
    # BEFORE: no probe.json -> verify False
    assert seams.verify(root, green_alias) is False
    # deliver the FAITHFUL ingested view through the real msg_store channel, run the hook
    _deliver_hydrate(str(tmp_path), green_alias, _artifact_from_wal(store, root))
    path = green_boot_probe.produce_probe_answer(str(tmp_path), root, green_alias)
    assert os.path.basename(path) == f"{green_alias}.probe.json"       # A5 attribution
    # AFTER: the green PRODUCED the answer from its ingested view -> verify True
    assert seams.verify(root, green_alias) is True


def test_a1b_lossy_delivered_artifact_verify_false(tmp_path):
    root, green_alias, wal_dir, store = _prep(tmp_path)
    seams = _mk_seams(root, tmp_path, wal_dir)
    art = _artifact_from_wal(store, root)
    art["events"][-1]["summary"] = "CORRUPTED"   # a lossy/garbled delivery of the last event
    _deliver_hydrate(str(tmp_path), green_alias, art)
    green_boot_probe.produce_probe_answer(str(tmp_path), root, green_alias)
    # divergence from Blue's WAL truth -> the real lossy-ingest signal -> verify False
    assert seams.verify(root, green_alias) is False


def _events_of(store, root):
    return [{"seq": r["seq"], "kind": r["kind"], "summary": r["summary"],
             "body_ref": r["body_ref"]} for r in store.events(root)]


def test_a6_cumulative_merge_across_beats_verify_true(tmp_path):
    """gm cumulative-delta requirement: over N PREWARMING beats the green's inbox
    accumulates MULTIPLE lineage_hydrate rows, each carrying only that beat's delta. The
    hook must MERGE ALL delivered rows into the cumulative ingested view (union by seq).
    Two deltas whose union == the full WAL -> verify TRUE."""
    root, green_alias, wal_dir, store = _prep(tmp_path)
    seams = _mk_seams(root, tmp_path, wal_dir)
    evs = _events_of(store, root)
    half = len(evs) // 2
    _deliver_hydrate(str(tmp_path), green_alias, {
        "lineage_root": root, "scope": {"since_seq": evs[0]["seq"], "through_seq": evs[half - 1]["seq"]},
        "events": evs[:half]})                                   # beat 1 delta
    _deliver_hydrate(str(tmp_path), green_alias, {
        "lineage_root": root, "scope": {"since_seq": evs[half]["seq"], "through_seq": evs[-1]["seq"]},
        "events": evs[half:]})                                   # beat 2 delta
    green_boot_probe.produce_probe_answer(str(tmp_path), root, green_alias)
    assert seams.verify(root, green_alias) is True               # cumulative union == full WAL


def test_a6b_dropped_in_scope_event_across_span_verify_false(tmp_path):
    """A dropped in-scope delta event across the multi-beat span -> the cumulative view
    is missing it -> divergence from Blue's WAL -> verify FALSE (real multi-beat loss)."""
    root, green_alias, wal_dir, store = _prep(tmp_path)
    seams = _mk_seams(root, tmp_path, wal_dir)
    evs = _events_of(store, root)
    half = len(evs) // 2
    _deliver_hydrate(str(tmp_path), green_alias, {
        "lineage_root": root, "scope": {"since_seq": evs[0]["seq"], "through_seq": evs[half - 1]["seq"]},
        "events": evs[:half]})
    dropped = evs[half:half + 1][0]["seq"]                       # drop the first event of beat 2
    _deliver_hydrate(str(tmp_path), green_alias, {
        "lineage_root": root, "scope": {"since_seq": evs[half]["seq"], "through_seq": evs[-1]["seq"]},
        "events": evs[half + 1:]})                               # beat 2 delta MINUS one in-scope event
    green_boot_probe.produce_probe_answer(str(tmp_path), root, green_alias)
    assert seams.verify(root, green_alias) is False, f"a dropped in-scope event (seq {dropped}) must fail"


def test_a2_inert_marker_absent_is_noop(tmp_path, monkeypatch):
    root, green_alias, wal_dir, store = _prep(tmp_path)
    monkeypatch.delenv("BG_GREEN_ROOT", raising=False)
    monkeypatch.delenv("BG_GREEN_ALIAS", raising=False)
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    assert green_boot_probe.main() == 0                     # no-op, exit 0
    assert not (tmp_path / "state" / "agent-handoffs" / f"{green_alias}.probe.json").exists()


def test_a3_failsafe_no_hydrate_row_returns_nonzero_no_probe(tmp_path, monkeypatch):
    root, green_alias, wal_dir, store = _prep(tmp_path)
    # marker SET but NO lineage_hydrate row delivered -> fail-safe: non-zero, no crash, no probe.json
    monkeypatch.setenv("BG_GREEN_ROOT", root)
    monkeypatch.setenv("BG_GREEN_ALIAS", green_alias)
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    assert green_boot_probe.main() == 1
    assert not (tmp_path / "state" / "agent-handoffs" / f"{green_alias}.probe.json").exists()


def test_a4_main_end_to_end_via_env(tmp_path, monkeypatch):
    root, green_alias, wal_dir, store = _prep(tmp_path)
    _deliver_hydrate(str(tmp_path), green_alias, _artifact_from_wal(store, root))
    monkeypatch.setenv("BG_GREEN_ROOT", root)
    monkeypatch.setenv("BG_GREEN_ALIAS", green_alias)
    monkeypatch.setenv("BG_GREEN_PROBE_K", "5")
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    assert green_boot_probe.main() == 0
    p = tmp_path / "state" / "agent-handoffs" / f"{green_alias}.probe.json"
    assert p.exists()
    ans = json.loads(p.read_text())
    assert "last_events" in ans and "working_set" in ans


# ---- gm mid-life multi-beat acceptance gate (gap-c: delivered delta is a strict SUBSET
#      of the full WAL; blue-authoritative scope from bg_state) --------------------------
from lineage_daemon.wal.probe import grade_probe as _grade_probe  # noqa: E402


def _midlife_prep(tmp_path):
    """WAL seq 1..4; B=2 is the spawn-handoff baseline (events 1,2 NOT re-shipped);
    the deltas are (2,4] = events seq 3,4, shipped across 2 beats."""
    root, green_alias = "bg-drill-victim", "bg-drill-victim-g2"
    (tmp_path / "state" / "agent-handoffs").mkdir(parents=True)
    wal_dir = tmp_path / "state" / "wal"; wal_dir.mkdir(parents=True)
    store = _seed_wal(str(wal_dir), root, n=4)     # seq 1..4, alternating file_mod/response
    B = 2
    evs = _events_of(store, root)                  # [{seq,kind,summary,body_ref} for 1..4]
    delta = [e for e in evs if e["seq"] > B]        # (B, 4] = seq 3,4
    return root, green_alias, wal_dir, store, B, delta


def test_midlife_faithful_passes_on_scope_but_full_wal_falsefails(tmp_path):
    """gm gap-c gate: a mid-life green whose delivered deltas (a strict SUBSET of the full
    WAL) are FAITHFUL passes the blue-authoritative scope grade — AND the same answer would
    FALSE-FAIL a full-WAL grade (scope=None), proving the scope fix is load-bearing."""
    root, green_alias, wal_dir, store, B, delta = _midlife_prep(tmp_path)
    thr = delta[-1]["seq"]
    # deliver the deltas across TWO beats (seq 3, then seq 4)
    _deliver_hydrate(str(tmp_path), green_alias, {
        "lineage_root": root, "scope": {"since_seq": B, "through_seq": delta[0]["seq"]},
        "events": [delta[0]]})
    _deliver_hydrate(str(tmp_path), green_alias, {
        "lineage_root": root, "scope": {"since_seq": delta[0]["seq"], "through_seq": thr},
        "events": [delta[1]]})
    path = green_boot_probe.produce_probe_answer(str(tmp_path), root, green_alias)
    answer = json.loads(open(path).read())
    scope = {"since_seq": B, "through_seq": thr}                 # blue-authoritative (bg_state)
    assert _grade_probe(store, root, answer, scope=scope)["ok"] is True     # scope grade PASSES
    assert _grade_probe(store, root, answer, scope=None)["ok"] is False     # full-WAL FALSE-FAILS (gap-c)


def test_midlife_whole_row_loss_fails(tmp_path):
    """A whole delivered ROW lost (a beat's delta never arrives) -> the green's union is
    missing those seqs -> blue-authoritative scope grade FAILS (channel loss caught)."""
    root, green_alias, wal_dir, store, B, delta = _midlife_prep(tmp_path)
    thr = delta[-1]["seq"]
    # only beat 1 arrives; beat 2 (seq 4) is a WHOLE-ROW loss
    _deliver_hydrate(str(tmp_path), green_alias, {
        "lineage_root": root, "scope": {"since_seq": B, "through_seq": delta[0]["seq"]},
        "events": [delta[0]]})
    path = green_boot_probe.produce_probe_answer(str(tmp_path), root, green_alias)
    answer = json.loads(open(path).read())
    assert _grade_probe(store, root, answer, scope={"since_seq": B, "through_seq": thr})["ok"] is False


def test_midlife_within_row_dropped_event_fails(tmp_path):
    """A within-row dropped/garbled event in a delivered delta -> scope grade FAILS."""
    root, green_alias, wal_dir, store, B, delta = _midlife_prep(tmp_path)
    thr = delta[-1]["seq"]
    _deliver_hydrate(str(tmp_path), green_alias, {
        "lineage_root": root, "scope": {"since_seq": B, "through_seq": delta[0]["seq"]},
        "events": [delta[0]]})
    garbled = dict(delta[1]); garbled["summary"] = "CORRUPTED"
    _deliver_hydrate(str(tmp_path), green_alias, {
        "lineage_root": root, "scope": {"since_seq": delta[0]["seq"], "through_seq": thr},
        "events": [garbled]})
    path = green_boot_probe.produce_probe_answer(str(tmp_path), root, green_alias)
    answer = json.loads(open(path).read())
    assert _grade_probe(store, root, answer, scope={"since_seq": B, "through_seq": thr})["ok"] is False
