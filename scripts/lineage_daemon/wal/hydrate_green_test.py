"""RED-first tests for hydrate_green.py — the delta-hydrate seam.

Replays Blue's WAL DELTA (events with seq > last-hydrated) into Green so its state
is <= one beat stale at swap. Court-scrub gated (bytes-only on poisoned bodies).
Resumable + idempotent: the last-hydrated seq is PERSISTED in bg_state, so each
beat ships only the new delta and a re-entry after full hydrate ships nothing.
Delivery is DURABLE (injected; default msg_store), never tmux-only — a dropped
ephemeral inject would be silent context loss (violates lossless).
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import hydrate_green  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402


def _capture(sink):
    """A deliver_fn fake on the NEW 4-arg contract (root, alias, text,
    ingested_view). Appends (text, ingested_view) so tests can assert the
    structured artifact the seam builds for the msg_store row."""
    def deliver(root, alias, text, ingested_view=None, continue_capsule=None):
        sink.append((text, ingested_view))
    return deliver


def _seed(tmp_path, root, n, start_ts=1000):
    store = WalStore(str(tmp_path / f"{root}.db"))
    for i in range(n):
        store.append(ts=start_ts + i, lineage_root=root, generation=2, sid="b",
                     runtime="claude", kind="response", summary=f"e{i}",
                     source_path="x")
    return store


def test_first_hydrate_ships_full_delta_and_advances_seq(tmp_path):
    root = "bg-drill-victim"
    store = _seed(tmp_path, root, 5)
    delivered = []
    out = hydrate_green.hydrate_green(
        root, f"{root}-g-green", since_seq=None, wal_dir=str(tmp_path),
        store=store, deliver_fn=lambda root, alias, text, iv=None, cap=None: delivered.append(text))
    assert out["count"] == 5
    assert out["empty"] is False
    assert len(delivered) == 1 and "e4" in delivered[0]
    # last-hydrated seq persisted = store max
    assert BgStateStore(str(tmp_path), root).read_meta("last_hydrated_seq") == store.max_seq()


def test_second_hydrate_ships_only_new_delta(tmp_path):
    root = "bg-drill-victim"
    store = _seed(tmp_path, root, 3)
    delivered = []
    dfn = lambda r, a, t, iv=None, cap=None: delivered.append(t)  # noqa: E731
    hydrate_green.hydrate_green(root, "g", since_seq=None, wal_dir=str(tmp_path),
                                store=store, deliver_fn=dfn)
    # Blue does 2 more events, then hydrate again -> only the 2 new ship
    for i in range(2):
        store.append(ts=2000 + i, lineage_root=root, generation=2, sid="b",
                     runtime="claude", kind="response", summary=f"new{i}",
                     source_path="x")
    out = hydrate_green.hydrate_green(root, "g", since_seq=None, wal_dir=str(tmp_path),
                                      store=store, deliver_fn=dfn)
    assert out["count"] == 2
    assert "new1" in delivered[-1]
    assert "e0" not in delivered[-1]   # old events NOT re-shipped


def test_fully_hydrated_reentry_ships_nothing(tmp_path):
    """Idempotent: a re-entry with no new WAL events delivers nothing (no re-inject
    storm) and reports empty."""
    root = "bg-drill-victim"
    store = _seed(tmp_path, root, 4)
    delivered = []
    dfn = lambda r, a, t, iv=None, cap=None: delivered.append(t)  # noqa: E731
    hydrate_green.hydrate_green(root, "g", since_seq=None, wal_dir=str(tmp_path),
                                store=store, deliver_fn=dfn)
    n_after_first = len(delivered)
    out = hydrate_green.hydrate_green(root, "g", since_seq=None, wal_dir=str(tmp_path),
                                      store=store, deliver_fn=dfn)
    assert out["empty"] is True
    assert out["count"] == 0
    assert len(delivered) == n_after_first   # no second delivery


def test_explicit_since_seq_overrides_persisted(tmp_path):
    """A caller may pass an explicit since_seq (bg_arm passes since_seq=0 while
    PREWARMING); None => use the persisted last-hydrated seq."""
    root = "bg-drill-victim"
    store = _seed(tmp_path, root, 5)
    delivered = []
    out = hydrate_green.hydrate_green(
        root, "g", since_seq=2, wal_dir=str(tmp_path), store=store,
        deliver_fn=lambda r, a, t, iv=None, cap=None: delivered.append(t))
    assert out["count"] == 3    # seq 3,4,5 only (>2)


def test_court_scrub_gates_poison_bytes_only(tmp_path):
    """The scrub seam is threaded to render_digest — a poisoned body is handled
    bytes-only (never read into the delivered text). We assert the scrub fn is
    CALLED (the contagion firewall is wired), not the poison content."""
    root = "bg-drill-victim"
    store = _seed(tmp_path, root, 2)
    scrub_calls = []

    def scrub(text):
        scrub_calls.append(True)
        return text, False

    hydrate_green.hydrate_green(
        root, "g", since_seq=None, wal_dir=str(tmp_path), store=store,
        deliver_fn=lambda r, a, t, iv=None, cap=None: None, scrub=scrub)
    assert scrub_calls, "the court-scrub seam must be threaded into the digest"


def test_delivery_failure_does_not_advance_seq(tmp_path):
    """Durable-delivery discipline: if delivery RAISES, the last-hydrated seq is
    NOT advanced (so the next beat re-ships the delta — no silent loss)."""
    root = "bg-drill-victim"
    store = _seed(tmp_path, root, 3)

    def boom(root, alias, text, iv=None, cap=None):
        raise RuntimeError("msg_store down")

    import pytest
    with pytest.raises(RuntimeError):
        hydrate_green.hydrate_green(root, "g", since_seq=None, wal_dir=str(tmp_path),
                                    store=store, deliver_fn=boom)
    # seq NOT advanced -> next beat retries the same delta
    assert BgStateStore(str(tmp_path), root).read_meta("last_hydrated_seq") in (None, 0)


# ---- Sequence-A bar#4 producer: the structured ingested-view artifact ----------

def _seed_mixed(tmp_path, root, n=5, start_ts=1000):
    """WAL with alternating file_mod (body_ref) + response events, so working-set
    derivation (kind==file_mod, body_ref split on '#') has real material."""
    store = WalStore(str(tmp_path / f"{root}.db"))
    for i in range(n):
        kind = "file_mod" if i % 2 == 0 else "response"
        body = f"git:/cwd#path{i}.py" if kind == "file_mod" else None
        store.append(ts=start_ts + i, lineage_root=root, generation=2, sid="b",
                     runtime="claude", kind=kind, summary=f"e{i}",
                     body_ref=body, source_path="x")
    return store


def test_ingested_view_embedded_with_delta_events_and_scope(tmp_path):
    """gm bar#4: hydrate embeds a STRUCTURED ingested-view artifact in the SAME
    delivery (the msg_store row), carrying the delta's {seq,kind,summary,body_ref}
    events + a {since_seq,through_seq} scope — NOT a side file."""
    root = "bg-drill-victim"
    store = _seed_mixed(tmp_path, root, 5)
    cap = []
    out = hydrate_green.hydrate_green(root, "g", since_seq=None,
                                      wal_dir=str(tmp_path), store=store,
                                      deliver_fn=_capture(cap))
    assert out["delivered"] is True
    _text, iv = cap[-1]
    assert iv is not None, "ingested_view must be delivered alongside the text"
    assert iv["lineage_root"] == root
    assert iv["scope"] == {"since_seq": 0, "through_seq": store.max_seq()}
    # events cover the full delta (seq 1..5), each with the 4 required fields incl kind
    assert [e["seq"] for e in iv["events"]] == [1, 2, 3, 4, 5]
    for e in iv["events"]:
        assert set(e) >= {"seq", "kind", "summary", "body_ref"}
    # kind is preserved (probe._working_set filters kind==file_mod)
    kinds = {e["seq"]: e["kind"] for e in iv["events"]}
    assert kinds[1] == "file_mod" and kinds[2] == "response"
    # body_ref preserved for file_mod (the working-set source)
    body1 = next(e["body_ref"] for e in iv["events"] if e["seq"] == 1)
    assert body1 == "git:/cwd#path0.py"


def test_ingested_view_second_hydrate_carries_only_the_new_delta(tmp_path):
    """Each row's ingested_view scope is the delta THAT row shipped — the green
    unions rows by seq, so a row must not re-ship old events."""
    root = "bg-drill-victim"
    store = _seed_mixed(tmp_path, root, 3)
    cap = []
    dfn = _capture(cap)
    hydrate_green.hydrate_green(root, "g", since_seq=None, wal_dir=str(tmp_path),
                                store=store, deliver_fn=dfn)
    first_through = cap[-1][1]["scope"]["through_seq"]
    # Blue advances 2 events; second hydrate ships only the new delta
    for i in range(2):
        store.append(ts=5000 + i, lineage_root=root, generation=2, sid="b",
                     runtime="claude", kind="file_mod", summary=f"n{i}",
                     body_ref=f"git:/cwd#new{i}.py", source_path="x")
    hydrate_green.hydrate_green(root, "g", since_seq=None, wal_dir=str(tmp_path),
                                store=store, deliver_fn=dfn)
    iv2 = cap[-1][1]
    assert iv2["scope"] == {"since_seq": first_through,
                            "through_seq": store.max_seq()}
    assert [e["seq"] for e in iv2["events"]] == [4, 5]   # ONLY the new delta


def test_default_deliver_rides_the_msg_store_metadata_channel(tmp_path):
    """★ Load-bearing (no side file): the real _default_deliver puts the artifact
    on the lineage_hydrate msg_store ROW's metadata['ingested_view'], and a query
    for that row round-trips it — this is what proves the DELIVERY CHANNEL lossless
    (grading a side file would grade the WRITER, not the channel)."""
    import os, sqlite3
    from msg_store import MessageStore
    orch = str(tmp_path)
    os.makedirs(os.path.join(orch, "state"), exist_ok=True)
    wal_dir = os.path.join(orch, "state", "wal")
    os.makedirs(wal_dir, exist_ok=True)
    # bootstrap the messages table the live tasks.db already has (API-created in prod)
    _conn = sqlite3.connect(os.path.join(orch, "state", "tasks.db"))
    _conn.executescript(
        "CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT, "
        "task_id TEXT, parent_id TEXT, type TEXT, from_agent TEXT, to_agent TEXT, "
        "subject TEXT, body TEXT, priority TEXT DEFAULT 'medium', "
        "source TEXT DEFAULT 'system', status TEXT DEFAULT 'pending', "
        "retry_count INTEGER DEFAULT 0, max_retries INTEGER DEFAULT 5, "
        "metadata TEXT, created_at TEXT, attempted_at TEXT, delivered_at TEXT, "
        "acknowledged_at TEXT, archived_at TEXT, error TEXT, depends_on TEXT, "
        "gather_mode TEXT DEFAULT 'gather_all', tenant_id TEXT DEFAULT 'operator');"
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, subject TEXT, "
        "participants TEXT, task_id TEXT, created_at TEXT, updated_at TEXT, "
        "tenant_id TEXT DEFAULT 'operator', mode TEXT DEFAULT 'fire_and_forget', "
        "max_iterations INTEGER DEFAULT 1, iteration_count INTEGER DEFAULT 0, "
        "completion_condition TEXT, status TEXT DEFAULT 'open');")
    _conn.commit(); _conn.close()
    root = "bg-drill-victim"
    store = _seed_mixed(tmp_path / "state" / "wal", root, 4)
    green_alias = f"{root}-g2"
    hydrate_green.hydrate_green(root, green_alias, since_seq=None, wal_dir=wal_dir,
                                store=store, orchestra_dir=orch)
    db = os.path.join(orch, "state", "tasks.db")
    rows = MessageStore(db_path=db).query(to_agent=green_alias, type="lineage_hydrate")
    assert rows, "the lineage_hydrate row must land in the same tasks.db the green reads"
    import json
    md = rows[0]["metadata"]
    md = json.loads(md) if isinstance(md, str) else md
    iv = md["ingested_view"]
    assert iv["lineage_root"] == root
    assert [e["seq"] for e in iv["events"]] == [1, 2, 3, 4]


def test_first_hydrate_records_baseline_since_seq(tmp_path):
    """The green's hydrate baseline (first delivered since_seq) is recorded ONCE in
    bg_state — grade_probe's authoritative scope 'since' comes from here (0 for a
    fresh green, B for a mid-life green), NOT from a green-supplied value."""
    root = "bg-drill-victim"
    store = _seed_mixed(tmp_path, root, 3)
    st = BgStateStore(str(tmp_path), root)
    # simulate a MID-LIFE green: it already ingested through seq 1 at spawn
    st.write_meta("last_hydrated_seq", 1)
    hydrate_green.hydrate_green(root, "g", since_seq=None, wal_dir=str(tmp_path),
                                store=store, deliver_fn=lambda r, a, t, iv=None, cap=None: None)
    assert st.read_meta("first_hydrated_seq") == 1   # baseline = the mid-life since
    # a later hydrate must NOT overwrite the recorded baseline
    for i in range(2):
        store.append(ts=9000 + i, lineage_root=root, generation=2, sid="b",
                     runtime="claude", kind="response", summary=f"z{i}", source_path="x")
    hydrate_green.hydrate_green(root, "g", since_seq=None, wal_dir=str(tmp_path),
                                store=store, deliver_fn=lambda r, a, t, iv=None, cap=None: None)
    assert st.read_meta("first_hydrated_seq") == 1   # unchanged


# ---- gm companion condition: intended-drops (truncation/scrub) must NOT false-fail ----

def test_ingested_view_complete_even_when_digest_text_truncates(tmp_path):
    """gm companion condition (consensus ruling): a REAL BG with a large WAL that
    trips render_digest's 800k token-ceiling drops tail events FROM THE TEXT (marks
    truncated=true + dropped_spans, ref preserved). The ingested_view must NOT inherit
    that truncation — it is built from the raw WAL slice, so it carries EVERY in-scope
    event's {seq,kind,summary,body_ref}. Hence a faithful green reconstructs the full
    slice and grade PASSES (no false-fail); only a real channel loss fails."""
    from lineage_daemon.wal.digest import render_digest
    from lineage_daemon.wal.probe import grade_probe
    root = "bg-drill-victim"
    store = _seed_mixed(tmp_path, root, 12)   # 12 events, file_mods at even seqs
    cap = []
    # tiny ceiling forces render_digest to truncate the TEXT
    out = hydrate_green.hydrate_green(root, "g", since_seq=None,
                                      wal_dir=str(tmp_path), store=store,
                                      deliver_fn=_capture(cap), ceiling_tokens=40)
    # the digest TEXT truncated...
    d = render_digest(store, root, since_seq=0, ceiling_tokens=40)
    assert d["truncated"] is True and d["dropped_spans"], "precondition: text truncates"
    # ...but the ingested_view carries the COMPLETE in-scope slice
    iv = cap[-1][1]
    assert [e["seq"] for e in iv["events"]] == list(range(1, 13)), \
        "ingested_view must carry every in-scope seq despite text truncation"
    # a faithful green (produces from the complete artifact) passes the scope grade
    from lineage_daemon.wal.probe_answer import compute_probe_answer
    # green reconstructs from the artifact events (adapter shape == WalStore.events)
    class _A:
        def events(self, _=None): return iv["events"]
    ans = compute_probe_answer(_A(), root, k=5)
    scope = {"since_seq": 0, "through_seq": store.max_seq()}
    assert grade_probe(store, root, ans, k=5, scope=scope)["ok"] is True


def test_ingested_view_carries_body_ref_for_scrubbable_events(tmp_path):
    """Scrub excludes a contaminated BODY from the digest text but preserves the seq +
    body_ref (gm: 'still ship a body_ref the green can drill'). The ingested_view carries
    only structural fields (seq/kind/summary/body_ref) — never a resolved tool_result/
    file body (the actual contagion vector) — so a scrubbed event still contributes its
    working-set path + last-K entry, and grade does not false-fail."""
    root = "bg-drill-victim"
    store = _seed_mixed(tmp_path, root, 4)   # file_mod events carry body_ref
    cap = []
    # a scrub that trips on everything still must not strip the artifact's structural fields
    def scrub(text):
        return "<contaminated span excluded>", True
    hydrate_green.hydrate_green(root, "g", since_seq=None, wal_dir=str(tmp_path),
                                store=store, deliver_fn=_capture(cap), scrub=scrub)
    iv = cap[-1][1]
    # every file_mod event still carries its body_ref (working-set source preserved)
    fmods = [e for e in iv["events"] if e["kind"] == "file_mod"]
    assert fmods and all(e["body_ref"] and "#" in e["body_ref"] for e in fmods)
    assert [e["seq"] for e in iv["events"]] == [1, 2, 3, 4]   # no seq dropped


# ---- BG Layer-3: the working-state continue-capsule rides the hydrate delivery ----------

def _seed_directive(tmp_path, root):
    """A WAL with a salient OLD thread + a CURRENT directive (resolvable body_ref) + a file_mod,
    so the capsule has real material to anchor the objective on the directive, not the old thread."""
    store = WalStore(str(tmp_path / f"{root}.db"))
    for i in range(6):  # old salient thread
        store.append(ts=100 + i, lineage_root=root, generation=4, sid="b", runtime="claude",
                     kind="response", summary=f"orb status-glow decision {i}", source_path="x")
    store.append(ts=200, lineage_root=root, generation=4, sid="b", runtime="claude",
                 kind="prompt", summary="text", body_ref="t.jsonl:900", source_path="t.jsonl")
    store.append(ts=210, lineage_root=root, generation=4, sid="b", runtime="claude",
                 kind="file_mod", summary=" M projection.py",
                 body_ref="git:/home/testuser/agent-orchestra#scripts/lineage_daemon/wal/projection.py",
                 source_path="x")
    return store


def test_hydrate_prepends_continue_capsule_banner_to_body(tmp_path):
    """Layer-3: the delivered hydrate TEXT leads with a STATED continue-capsule banner (objective +
    next action), so the green resumes blue's CURRENT objective instead of inferring from the
    timeline below. The banner must precede the digest timeline."""
    root = "bg-drill-victim"
    store = _seed_directive(tmp_path, root)
    cap = []
    hydrate_green.hydrate_green(
        root, "g", since_seq=None, wal_dir=str(tmp_path), store=store, deliver_fn=_capture(cap),
        resolve_body=lambda ref: {"t.jsonl:900": "continue with AgentEvent stage 3"}.get(ref))
    text = cap[-1][0]
    assert "RESUME THIS" in text
    assert "stage 3" in text.lower()
    banner_idx = text.index("RESUME THIS")
    # the capsule banner precedes the decision timeline (which mentions the old orb thread)
    assert banner_idx < text.lower().index("orb")


def test_hydrate_carries_continue_capsule_in_metadata(tmp_path):
    """The structured capsule rides the SAME lineage_hydrate delivery as continue_capsule (a
    programmatic/auditable channel), alongside the bar#4 ingested_view — never a side file."""
    root = "bg-drill-victim"
    store = _seed_directive(tmp_path, root)
    seen = []
    def deliver(r, a, t, ingested_view=None, continue_capsule=None):
        seen.append(continue_capsule)
    hydrate_green.hydrate_green(
        root, "g", since_seq=None, wal_dir=str(tmp_path), store=store, deliver_fn=deliver,
        resolve_body=lambda ref: {"t.jsonl:900": "continue with AgentEvent stage 3"}.get(ref))
    capsule = seen[-1]
    assert capsule is not None
    assert "stage 3" in capsule["objective"].lower()
    assert capsule["source"] == "directive-thread"
    # cross-repo artifact resolved to its own repo root, not blue's cwd
    assert any(a["repo"] == "agent-orchestra" for a in capsule["artifact_refs"])


def test_hydrate_capsule_build_is_failsafe(tmp_path):
    """A capsule-build failure must NOT abort the hydrate beat (delivery is the critical path). If
    the capsule can't be built, the digest still ships (degraded to today's behavior)."""
    root = "bg-drill-victim"
    store = _seed_directive(tmp_path, root)
    cap = []
    def boom_resolver(ref):
        raise RuntimeError("resolver exploded")
    # must NOT raise; delivery still happens
    out = hydrate_green.hydrate_green(
        root, "g", since_seq=None, wal_dir=str(tmp_path), store=store, deliver_fn=_capture(cap),
        resolve_body=boom_resolver)
    assert out["delivered"] is True
    assert cap, "the hydrate must still deliver even if the capsule build fails"


def test_hydrate_capsule_does_not_regress_ingested_view(tmp_path):
    """bar#4 no-regression: the ingested_view is still delivered intact alongside the capsule."""
    root = "bg-drill-victim"
    store = _seed_directive(tmp_path, root)
    cap = []
    hydrate_green.hydrate_green(
        root, "g", since_seq=None, wal_dir=str(tmp_path), store=store, deliver_fn=_capture(cap),
        resolve_body=lambda ref: {"t.jsonl:900": "continue with stage 3"}.get(ref))
    _text, iv = cap[-1]
    assert iv is not None and iv["lineage_root"] == root
    assert iv["scope"]["through_seq"] == store.max_seq()
