"""boundary-delivery-core (s96, DEC-1786921182 CONSENSUS) — event-driven §9.6
turn-boundary delivery of held rows. Pure `run_boundary_delivery` over injected
seams: no tmux, no network, no real msg_store.

Binding conditions proven here (gm + agy):
- SHARED store.claim() CAS is the once-only gate: claim-fail (router won) → drop
  from bundle; inject-ok → deliver(); inject-fail → fail() so the backstop retries.
- F11 at fire-time: a session that transitioned into a menu/permission pane →
  ABORT inject, release the claim for backstop bridging (zero keystrokes).
- resolve_live_head before inject; bundle all of S's held rows created_at ASC into
  ONE verified inject.
- ships disabled → --shadow (WOULD-deliver, zero injects/claims) → arm.
"""
import importlib, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
bd = importlib.import_module("scripts.lineage_daemon.boundary_delivery")


class _FakeStore:
    """Minimal msg_store: pending held rows + CAS claim/deliver/fail."""
    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.delivered, self.failed = [], []
    def held_pending_for(self, session):
        return sorted(
            [dict(r) for r in self.rows.values()
             if r["to_agent"] == session and r["status"] == "pending"
             and (r.get("type") == "held_message")],
            key=lambda r: r["created_at"])
    def claim(self, mid):
        r = self.rows.get(mid)
        if r and r["status"] == "pending":
            r["status"] = "processing"; return True
        return False
    def deliver(self, mid):
        self.rows[mid]["status"] = "delivered"; self.delivered.append(mid)
    def fail(self, mid, error=None):
        self.rows[mid]["status"] = "pending"; self.failed.append((mid, error))
    def stamp_latency(self, mid, created_at, fired_at):
        self.rows[mid].setdefault("_latency", {})
        self.rows[mid]["_latency"] = {"created_at": created_at, "fired_at": fired_at}


def _row(mid, session, body, created_at, type="held_message"):
    return {"id": mid, "to_agent": session, "from_agent": "operator", "type": type,
            "body": body, "status": "pending", "created_at": created_at,
            "metadata": {"held_for_boundary": True, "deliver_raw": True}}


def _base(**over):
    """Default injected seams: idle pane (F11 clear), inject OK, head resolves."""
    d = dict(
        store=None,
        f11_ok=lambda s: True,            # True = safe to inject (no menu/perm)
        resolve_head=lambda s: s,         # live head resolves to itself
        inject=lambda s, text: (True, {}),  # (ok, info)
        armed=True,
        now_fired="2026-08-16T21:00:05",  # boundary-fire timestamp (injectable)
    )
    d.update(over)
    return d


# ---- happy path: bundle + deliver + once-only -------------------------------
def test_bundles_held_rows_created_at_asc_one_inject():
    store = _FakeStore([_row("m2", "S", "second", "2026-08-16T21:00:02"),
                        _row("m1", "S", "first", "2026-08-16T21:00:01")])
    injects = []
    args = _base(store=store, inject=lambda s, t: injects.append((s, t)) or (True, {}))
    res = bd.run_boundary_delivery("S", **args)
    assert len(injects) == 1                       # ONE bundled inject
    s, text = injects[0]
    assert s == "S" and text.index("first") < text.index("second")  # created_at asc
    assert store.delivered == ["m1", "m2"]         # both stamped delivered
    assert res["delivered"] == ["m1", "m2"] and res["armed"] is True


def test_single_held_row_delivers_raw_body():
    store = _FakeStore([_row("m1", "S", "reindex the crm", "2026-08-16T21:00:01")])
    injects = []
    args = _base(store=store, inject=lambda s, t: injects.append(t) or (True, {}))
    bd.run_boundary_delivery("S", **args)
    assert injects == ["reindex the crm"]          # RAW, no [MSG] envelope


# ---- once-only via shared CAS ----------------------------------------------
def test_claim_fail_drops_from_bundle_router_won_race():
    store = _FakeStore([_row("m1", "S", "a", "2026-08-16T21:00:01"),
                        _row("m2", "S", "b", "2026-08-16T21:00:02")])
    store.rows["m1"]["status"] = "delivered"       # router already delivered m1
    injects = []
    args = _base(store=store, inject=lambda s, t: injects.append(t) or (True, {}))
    bd.run_boundary_delivery("S", **args)
    assert injects == ["b"]                          # only the still-claimable m2
    assert store.delivered == ["m2"] and "m1" not in store.delivered


def test_no_held_rows_no_inject():
    store = _FakeStore([])
    injects = []
    args = _base(store=store, inject=lambda s, t: injects.append(t) or (True, {}))
    res = bd.run_boundary_delivery("S", **args)
    assert injects == [] and res["delivered"] == []


# ---- F11 at fire-time (load-bearing) ---------------------------------------
def test_f11_menu_pane_aborts_inject_and_releases_claim():
    store = _FakeStore([_row("m1", "S", "a", "2026-08-16T21:00:01")])
    injects = []
    args = _base(store=store, f11_ok=lambda s: False,      # pane became a menu/perm
                 inject=lambda s, t: injects.append(t) or (True, {}))
    res = bd.run_boundary_delivery("S", **args)
    assert injects == []                            # ZERO keystrokes into a menu pane
    assert store.rows["m1"]["status"] == "pending"  # claim released for the backstop
    assert res["reason"] == "f11_blocked" and res["delivered"] == []


# ---- inject failure -> fail() so backstop retries --------------------------
def test_inject_failure_fails_rows_for_backstop_retry():
    store = _FakeStore([_row("m1", "S", "a", "2026-08-16T21:00:01")])
    args = _base(store=store, inject=lambda s, t: (False, {"reason": "unverified"}))
    res = bd.run_boundary_delivery("S", **args)
    assert store.failed and store.failed[0][0] == "m1"     # fail() called
    assert store.rows["m1"]["status"] == "pending"          # back to pending (retry)
    assert res["delivered"] == []


# ---- resolve_live_head -----------------------------------------------------
def test_resolves_live_head_before_inject():
    store = _FakeStore([_row("m1", "gm", "a", "2026-08-16T21:00:01")])
    seen = []
    args = _base(store=store, resolve_head=lambda s: "gm-gen10",
                 inject=lambda s, t: seen.append(s) or (True, {}))
    bd.run_boundary_delivery("gm", **args)
    assert seen == ["gm-gen10"]                     # injected to the RESOLVED head
    # no live head -> hold (don't inject a bare session name)


def test_no_live_head_holds_no_inject():
    store = _FakeStore([_row("m1", "gm", "a", "2026-08-16T21:00:01")])
    injects = []
    args = _base(store=store, resolve_head=lambda s: None,
                 inject=lambda s, t: injects.append(t) or (True, {}))
    res = bd.run_boundary_delivery("gm", **args)
    assert injects == [] and res["reason"] == "no_live_head"
    assert store.rows["m1"]["status"] == "pending"  # stays for the backstop


# ---- shadow mode: zero writes ----------------------------------------------
def test_shadow_mode_would_deliver_zero_injects_zero_claims():
    store = _FakeStore([_row("m1", "S", "a", "2026-08-16T21:00:01"),
                        _row("m2", "S", "b", "2026-08-16T21:00:02")])
    injects = []
    args = _base(store=store, armed=False,
                 inject=lambda s, t: injects.append(t) or (True, {}))
    res = bd.run_boundary_delivery("S", **args)
    assert injects == []                            # zero injects
    assert store.delivered == [] and store.failed == []   # zero claims/writes
    assert res["armed"] is False and res["would_deliver"] == ["m1", "m2"]
    assert all(store.rows[m]["status"] == "pending" for m in ("m1", "m2"))


# ---- per-delivery latency stamp (v2 addition, §9.6 B6) ---------------------
def test_delivered_rows_get_latency_stamp():
    store = _FakeStore([_row("m1", "S", "a", "2026-08-16T21:00:01")])
    args = _base(store=store, inject=lambda s, t: (True, {}),
                 now_fired="2026-08-16T21:03:00")
    res = bd.run_boundary_delivery("S", **args)
    assert res["delivered"] == ["m1"]
    lat = store.rows["m1"]["_latency"]
    assert lat["created_at"] == "2026-08-16T21:00:01"
    assert lat["fired_at"] == "2026-08-16T21:03:00"       # boundary-fire time

def test_shadow_and_f11_do_not_stamp_latency():
    # only a REAL delivery stamps latency (not shadow, not an F11-aborted fire)
    store = _FakeStore([_row("m1", "S", "a", "2026-08-16T21:00:01")])
    bd.run_boundary_delivery("S", **_base(store=store, armed=False,
                                          now_fired="t"))
    assert "_latency" not in store.rows["m1"]
    store2 = _FakeStore([_row("m2", "S", "b", "2026-08-16T21:00:01")])
    bd.run_boundary_delivery("S", **_base(store=store2, f11_ok=lambda s: False,
                                          now_fired="t"))
    assert "_latency" not in store2.rows["m2"]


# ---- real MessageStore.held_pending_for seam -------------------------------
def test_held_pending_for_real_store(tmp_path):
    import importlib, sqlite3
    ms = importlib.import_module("msg_store")
    db = str(tmp_path / "m.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT,
          parent_id TEXT, type TEXT, from_agent TEXT, to_agent TEXT, subject TEXT,
          body TEXT, priority TEXT DEFAULT 'medium', source TEXT DEFAULT 'system',
          status TEXT DEFAULT 'pending', retry_count INTEGER DEFAULT 0,
          max_retries INTEGER DEFAULT 5, metadata TEXT, created_at TEXT,
          attempted_at TEXT, delivered_at TEXT, acknowledged_at TEXT, archived_at TEXT,
          error TEXT, depends_on TEXT, gather_mode TEXT DEFAULT 'gather_all',
          tenant_id TEXT DEFAULT 'operator');
        CREATE TABLE conversations (id TEXT PRIMARY KEY, subject TEXT, participants TEXT,
          task_id TEXT, created_at TEXT, updated_at TEXT, tenant_id TEXT DEFAULT 'operator');
    """)
    conn.close()
    s = ms.MessageStore(db_path=db)
    # two held rows to S (asc), one to another agent, one non-held, one delivered
    s.send(from_agent="operator", to_agent="S", type="held_message", body="first",
           metadata={"held_for_boundary": True}, msg_id="h1")
    s.send(from_agent="operator", to_agent="S", type="held_message", body="second",
           metadata={"held_for_boundary": True}, msg_id="h2")
    s.send(from_agent="operator", to_agent="OTHER", type="held_message", body="x",
           metadata={"held_for_boundary": True}, msg_id="h3")
    s.send(from_agent="pm", to_agent="S", type="reply", body="normal", msg_id="n1")
    got = s.held_pending_for("S")
    ids = [r["id"] for r in got]
    assert ids == ["h1", "h2"]                      # only S's held rows, created_at asc
    # claimed row drops out (shared CAS with the router)
    s.claim("h1")
    assert [r["id"] for r in s.held_pending_for("S")] == ["h2"]


# ---- P0 pre-arm fix: _real_* seam wiring vs the REAL APIs -------------------
# mock-hid-API-contract class (bridge_one state=str twin, 360fc8236): the _base
# seam fake above returns a BARE STRING head, but the real
# lineage_resolve.resolve_live_head returns (session|None, reason). Also the
# cron context never had scripts/ on sys.path, so BOTH inner imports
# (lineage_resolve, agent-status) raised ModuleNotFoundError and silently
# fail-closed (no_live_head / f11_blocked) — armed lane could never deliver.

def test_real_resolve_head_unpacks_live_tuple(monkeypatch):
    import lineage_resolve as lr
    monkeypatch.setattr(lr, "resolve_live_head", lambda s: ("gm", "direct-live"))
    assert bd._real_resolve_head("gm") == "gm"


def test_real_resolve_head_none_and_empty_session_fail_closed(monkeypatch):
    import lineage_resolve as lr
    monkeypatch.setattr(lr, "resolve_live_head", lambda s: (None, "no-live-head"))
    assert bd._real_resolve_head("ghost-agent") is None
    monkeypatch.setattr(lr, "resolve_live_head", lambda s: ("", "odd-empty"))
    assert bd._real_resolve_head("x") is None


def test_real_resolve_head_resolver_error_fail_closed(monkeypatch):
    import lineage_resolve as lr
    def boom(s):
        raise RuntimeError("resolver down")
    monkeypatch.setattr(lr, "resolve_live_head", boom)
    assert bd._real_resolve_head("x") is None


def test_REAL_resolver_tuple_contract_no_mock():
    # NO MOCK — pins the real resolver's (session|None, reason) tuple contract
    # with hermetic fixtures, so a future resolver shape change breaks THIS test,
    # not the arm.
    import lineage_resolve as lr
    live = lr.resolve_live_head("gm", sessions={"gm"}, meta={})
    if live[0] is None and "registry" in str(live[1]).lower() or live[0] is None and "unknown" in str(live[1]).lower():
        import pytest
        pytest.skip(f"needs a registered 'gm' seat in the data dir (bare CI runner has none): {live[1]}")
    assert isinstance(live, tuple) and len(live) == 2
    assert live[0] == "gm" and isinstance(live[1], str) and live[1]
    dead = lr.resolve_live_head("no-such-agent-p0drill", sessions=set(), meta={})
    assert isinstance(dead, tuple) and len(dead) == 2
    assert dead[0] is None and isinstance(dead[1], str) and dead[1]


def test_cron_context_seam_imports_by_effect():
    # the audit's second same-class defect: in the EXACT cron script context
    # (no pytest path magic) scripts/ was absent from sys.path, so BOTH inner
    # imports raised ModuleNotFoundError and silently fail-closed
    # (no_live_head / f11_blocked). Subprocess = honest by-effect pin.
    import subprocess, textwrap
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    probe = textwrap.dedent('''
        import importlib.util, importlib
        spec = importlib.util.spec_from_file_location(
            "__probe_bd__", "scripts/lineage_daemon/boundary_delivery.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        importlib.import_module("lineage_resolve")
        importlib.import_module("agent-status")
        print("IMPORTS_OK")
    ''')
    r = subprocess.run([sys.executable, "-c", probe],
                       capture_output=True, text=True, cwd=root)
    assert "IMPORTS_OK" in r.stdout, f"stderr: {r.stderr[-400:]}"


# ---- §9.6-B.5 triage preamble (DEC-1787032722, Piece 3/4) --------------------
# One-line receiver-side standard preamble prefixed to the bundled inject when
# the caller passes it (real consumer: BOUNDARY_PREAMBLE_ENABLED=1, default OFF
# — gm's flag; OFF preserves the proven deliver_raw contract byte-for-byte).

def test_preamble_off_by_default_raw_contract_preserved():
    st = _FakeStore([_row("m1", "s", "hello raw", "2026-08-16T21:00:01")])
    sent = {}
    kw = _base(store=st, inject=lambda s, t: (sent.update(text=t) or True, {}))
    r = bd.run_boundary_delivery("s", **kw)
    assert r["reason"] == "delivered"
    assert sent["text"] == "hello raw"          # byte-identical, no preamble


def test_preamble_prefixes_bundle_when_supplied():
    st = _FakeStore([_row("m1", "s", "one", "2026-08-16T21:00:01"),
                     _row("m2", "s", "two", "2026-08-16T21:00:02")])
    sent = {}
    kw = _base(store=st, inject=lambda s, t: (sent.update(text=t) or True, {}))
    r = bd.run_boundary_delivery("s", preamble=bd.TRIAGE_PREAMBLE, **kw)
    assert r["reason"] == "delivered"
    body = sent["text"]
    assert body.startswith("[routed @turn-end]")
    assert "ACT-NOW only if" in body and "DECLINE with a reason" in body
    # preamble is ONE line, then the raw bundle in order
    lines = body.split("\n")
    assert lines[0] == bd.TRIAGE_PREAMBLE
    assert lines[1:] == ["one", "two"]


def test_preamble_not_applied_in_shadow_or_empty():
    st = _FakeStore([])
    kw = _base(store=st)
    r = bd.run_boundary_delivery("s", preamble=bd.TRIAGE_PREAMBLE, **kw)
    assert r["reason"] == "no_held_rows"


def test_env_flag_resolves_preamble():
    # real-consumer seam: flag OFF -> None; ON -> the spec-verbatim line
    assert bd.preamble_from_env({}) is None
    assert bd.preamble_from_env({"BOUNDARY_PREAMBLE_ENABLED": ""}) is None
    assert bd.preamble_from_env({"BOUNDARY_PREAMBLE_ENABLED": "0"}) is None
    assert bd.preamble_from_env({"BOUNDARY_PREAMBLE_ENABLED": "1"}) == bd.TRIAGE_PREAMBLE


# ---- #12 claim-3 (msg_66d1f98d): diagnostic reasons must survive empty results

def test_empty_result_keeps_diagnostic_reasons():
    # no_live_head is a diagnostic state — it must be REPORTABLE, not collapsed
    st = _FakeStore([_row("m1", "s", "b", "2026-08-16T21:00:01")])
    kw = _base(store=st, resolve_head=lambda s: None)
    r = bd.run_boundary_delivery("s", **kw)
    assert r["reason"] == "no_live_head"
    assert bd.reportable_result(r) is True          # diagnostic: report even w/o deliveries

def test_no_held_rows_not_reportable():
    st = _FakeStore([])
    r = bd.run_boundary_delivery("s", **_base(store=st))
    assert r["reason"] == "no_held_rows"
    assert bd.reportable_result(r) is False         # routine emptiness: stays quiet

def test_f11_blocked_reportable():
    st = _FakeStore([_row("m1", "s", "b", "2026-08-16T21:00:01")])
    r = bd.run_boundary_delivery("s", **_base(store=st, f11_ok=lambda s: False))
    assert bd.reportable_result(r) is True
