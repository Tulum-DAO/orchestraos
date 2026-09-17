"""S3 item 3 — always-on PASSIVE surface-pickup cron (Universal Decision-Surface
Pipeline spec §2.1/§3.1-a, Finding-1).

THE arming blocker: the cron path must NEVER press a key on a live pane. Even
for a multi-part menu that needs hydration, run_cron_cycle must NOT call the real
active walk (menu_capture_walk) — it passes the no-op walk_fn and stores the
passive part-1 payload; active hydration is client-triggered only. Plus: kill-
switch, gm skip-list, disabled-by-default, --would-surface shadow (zero writes).
Hermetic: fake store + injected fleet, no tmux/network.
"""
import importlib, os, sys
sys.path.insert(0, os.path.dirname(__file__))
mb = importlib.import_module("menu_bridge")


class _FakeStore:
    def __init__(self): self.created = []; self.db_path = ":memory:"
    def create(self, **kw):
        self.created.append(kw); return f"apr_{len(self.created)}"


def _mp_cap():
    # a multi-part passive capture that WOULD trigger hydration (walk_complete false)
    return {"question": "Which surfaces?",
            "options": [{"n": "1", "label": "iOS", "checked": False}],
            "checkbox": True, "has_submit": True,
            "tabs": ["Surfaces", "Priority", "Submit"], "submit_tab_index": 2,
            "multipart": True, "part_count": 2, "part_index": 0,
            "walk_complete": False,
            "parts": [{"index": 0, "question": "Which surfaces?", "select": "multi",
                       "options": [{"n": "1", "label": "iOS", "checked": False}]}]}


def _fleet(*sessions):
    return {s: {"state": "working", "pending_menu": _mp_cap()} for s in sessions}


def _sp_cap():
    # a SINGLE-part menu: still bridges under §1.1 (only multipart && !walk_complete
    # is skipped). Used where a test needs a card that actually reaches the ledger.
    return {"question": "Pick one", "options": [{"n": "1", "label": "A"}],
            "has_submit": False, "selected_n": "1"}


def _fleet_sp(*sessions):
    return {s: {"state": "working", "pending_menu": _sp_cap()} for s in sessions}


# ---- THE zero-keypress arming blocker -------------------------------------
def test_cron_never_calls_active_walk_even_for_hydration(monkeypatch):
    # If the cron ever reached the real active walk it would press keys on the
    # live pane. Spy on _default_walk_fn: it must NEVER be invoked by the cron.
    called = []
    monkeypatch.setattr(mb, "_default_walk_fn",
                        lambda s: called.append(s) or {"walk_complete": True, "parts": []})
    store = _FakeStore()
    # ledger_pending_op_keys reads sqlite by db_path; stub it for the fake store
    monkeypatch.setattr(mb, "ledger_pending_op_keys", lambda store, db_path=None: set())
    monkeypatch.setattr(mb, "reconcile_orphans",
                        lambda *a, **k: [])
    res = mb.run_cron_cycle(store, _fleet("worker-1"), now=100.0)
    assert called == []                                   # ZERO key-press walks
    # §1.1 (spec b606468dc): the passive cron can't hydrate (no-op walk), so an
    # un-hydratable multipart is SKIPPED rather than surfaced as a broken single-part
    # card. The zero-keypress guarantee is even stronger: skipped BEFORE any walk.
    assert store.created == []
    assert not res["bridged"]


def test_noop_walk_fn_returns_none():
    assert mb._noop_walk_fn("any-session") is None


# ---- kill-switch ----------------------------------------------------------
def test_killswitch_hard_stops(tmp_path):
    ks = tmp_path / "SURFACE_PICKUP_DISABLED"; ks.write_text("")
    store = _FakeStore()
    res = mb.run_pickup_cron(store, _fleet("worker-1"), now=100.0,
                             cfg={"enabled": True, "skip_sessions": [], "skip_prefixes": []},
                             killswitch_path=str(ks))
    assert res["disabled"] == "killswitch" and res["armed"] is False
    assert store.created == []                             # wrote nothing


# ---- ships disabled -> shadow only ----------------------------------------
def test_disabled_config_shadows_only(tmp_path):
    store = _FakeStore()
    res = mb.run_pickup_cron(store, _fleet("worker-1"), now=100.0,
                             cfg={"enabled": False, "skip_sessions": [], "skip_prefixes": []},
                             killswitch_path=str(tmp_path / "nope"))
    assert res["armed"] is False and res["mode"] == "disabled"
    assert len(res["would_surface"]) == 1                  # reports, writes nothing
    assert store.created == []


def test_would_surface_shadow_writes_nothing(tmp_path):
    store = _FakeStore()
    res = mb.run_pickup_cron(store, _fleet("worker-1"), now=100.0,
                             cfg={"enabled": True, "skip_sessions": [], "skip_prefixes": []},
                             killswitch_path=str(tmp_path / "nope"), shadow=True)
    assert res["armed"] is False and res["mode"] == "shadow"
    assert len(res["would_surface"]) == 1
    assert store.created == []


# ---- gm skip-list ---------------------------------------------------------
def test_skip_list_excludes_flagged_session(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "ledger_pending_op_keys", lambda store, db_path=None: set())
    monkeypatch.setattr(mb, "reconcile_orphans", lambda *a, **k: [])
    store = _FakeStore()
    # single-part fleet so the non-flagged session actually bridges (§1.1 skips
    # multipart); this test is about the denylist, not multipart handling.
    res = mb.run_pickup_cron(store, _fleet_sp("e2e-menu-scratch", "worker-1"), now=100.0,
                             cfg={"enabled": True, "skip_sessions": ["e2e-menu-scratch"],
                                  "skip_prefixes": []},
                             killswitch_path=str(tmp_path / "nope"))
    assert res["armed"] is True
    bridged_sessions = [c["from_agent"] for c in store.created]
    assert "worker-1" in bridged_sessions
    assert "e2e-menu-scratch" not in bridged_sessions      # flagged session skipped


def test_skip_prefix_excludes(tmp_path):
    store = _FakeStore()
    res = mb.run_pickup_cron(store, _fleet("operator-mp-fieldtest"), now=100.0,
                             cfg={"enabled": False, "skip_sessions": [],
                                  "skip_prefixes": ["operator-"]},
                             killswitch_path=str(tmp_path / "nope"))
    assert res["would_surface"] == []                      # prefix-skipped in shadow too


# ---- config loader fail-safe ----------------------------------------------
def test_cfg_missing_file_disabled(tmp_path):
    cfg = mb._load_pickup_cfg(str(tmp_path / "nope.json"))
    assert cfg["enabled"] is False and cfg["skip_sessions"] == []


# ---- is_session_carded seam (perm-card lane, a prior decision Q3) ------------
def test_is_session_carded_true_when_op_key_present(monkeypatch):
    import importlib
    core = importlib.import_module("menu_bridge_core")
    ok = core.menu_op_key("worker-7", "Do you want to proceed?")
    monkeypatch.setattr(mb, "ledger_pending_op_keys", lambda store, db_path=None: {ok})
    assert mb.is_session_carded(_FakeStore(), "worker-7", "Do you want to proceed?") is True

def test_is_session_carded_false_when_absent(monkeypatch):
    monkeypatch.setattr(mb, "ledger_pending_op_keys", lambda store, db_path=None: set())
    assert mb.is_session_carded(_FakeStore(), "worker-7", "Do you want to proceed?") is False

def test_is_session_carded_distinct_per_question(monkeypatch):
    # exact (session,question): a card for question A must NOT suppress escalation
    # for a DIFFERENT question B in the same session (no cross-prompt false-suppress)
    import importlib
    core = importlib.import_module("menu_bridge_core")
    ok_a = core.menu_op_key("worker-7", "Question A?")
    monkeypatch.setattr(mb, "ledger_pending_op_keys", lambda store, db_path=None: {ok_a})
    assert mb.is_session_carded(_FakeStore(), "worker-7", "Question A?") is True
    assert mb.is_session_carded(_FakeStore(), "worker-7", "Question B?") is False
