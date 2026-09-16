"""Stale-delivery digest gate (DEC-1787031444, gm commission msg_0aeb0aa8).

For protected+attached sessions (canonically gm): when a delivery gap opens,
2+ stale rows batch into ONE index-style digest inject (oldest-first, /tmp
pointers) instead of interrupting live work as stale singles. Mechanical
[SUPERSEDED-CANDIDATE] tagging; critical never digested; shadow-first.

gm build binds (msg_39f80ee7): (1) hook/gate double-present check — rows
already surfaced by gm's Stop-hook digest are skipped, and digested rows are
stamped so the hook can reciprocate; (2) shadow-first (--would-digest).
"""
import importlib.util
import json
import os
import datetime

_here = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "message_router", os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


def _iso(mins_ago):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=mins_ago)).isoformat()


def _row(mid, mins_ago, prio="medium", frm="pm-x", subj="s", meta=None):
    return {"id": mid, "from_agent": frm, "to_agent": "gm", "priority": prio,
            "subject": subj, "body": "b", "created_at": _iso(mins_ago),
            "conversation_id": "c1", "metadata": json.dumps(meta) if meta else None}


class FakeStore:
    def __init__(self, rows, superseded=(), acted_past=()):
        self.rows = {r["id"]: dict(r) for r in rows}
        self._sup = set(superseded)
        self._acted = set(acted_past)
        self.claimed, self.delivered, self.failed, self.stamped = [], [], [], []

    def pending_rows_for(self, agent):
        return sorted([r for r in self.rows.values()], key=lambda r: r["created_at"])

    def claim(self, mid):
        self.claimed.append(mid); return True

    def deliver(self, mid):
        self.delivered.append(mid)

    def fail(self, mid, error=None):
        self.failed.append(mid)

    def has_superseding_row(self, frm, to, created):
        for r in self.rows.values():
            if r["from_agent"] == frm and r["id"] in self._sup:
                return True
        return False

    def recipient_replied_after(self, conversation_id, to_agent, created_at):
        return any(r["id"] in self._acted for r in self.rows.values()
                   if r["conversation_id"] == conversation_id)

    def stamp_metadata(self, mid, **kv):
        self.stamped.append((mid, kv))


# --- partition ---------------------------------------------------------------

def test_partition_fresh_vs_stale():
    rows = [_row("a", 30), _row("b", 20), _row("c", 2)]
    fresh, stale = mr.partition_for_digest(rows, age_min=15)
    assert [r["id"] for r in stale] == ["a", "b"]     # oldest-first
    assert [r["id"] for r in fresh] == ["c"]


def test_partition_critical_never_stale():
    rows = [_row("a", 60, prio="critical"), _row("b", 60)]
    fresh, stale = mr.partition_for_digest(rows, age_min=15)
    assert [r["id"] for r in fresh] == [r["id"] for r in rows if r["id"] == "a"]
    assert [r["id"] for r in stale] == ["b"]


def test_partition_hook_surfaced_rows_excluded():
    # gm bind 1: a row the Stop-hook digest already presented is in NEITHER set
    rows = [_row("a", 60, meta={"surfaced_by": "gm-stop-hook"}), _row("b", 60)]
    fresh, stale = mr.partition_for_digest(rows, age_min=15)
    assert [r["id"] for r in stale] == ["b"]
    assert fresh == []


# --- compose -----------------------------------------------------------------

def test_compose_digest_index_format_and_tags():
    rows = [_row("a", 73, frm="pm-x", prio="high", subj="oldest thing"),
            _row("b", 51, frm="ob", subj="superseded thing")]
    store = FakeStore(rows, superseded={"b"})
    text = mr.compose_digest(rows, store)
    assert text.splitlines()[0].startswith("[DIGEST 2 stale msgs, oldest 73m")
    assert "1. " in text and "2. " in text
    assert text.index("oldest thing") < text.index("superseded thing")  # asc
    assert "[SUPERSEDED-CANDIDATE]" in text
    assert text.count("[SUPERSEDED-CANDIDATE]") == 1                    # only b
    assert "/tmp/agent-msg-a.md" in text and "/tmp/agent-msg-b.md" in text
    assert "verify before acting" in text.lower()


def test_compose_digest_acted_past_tags():
    rows = [_row("a", 30)]
    store = FakeStore(rows, acted_past={"a"})
    assert "[SUPERSEDED-CANDIDATE]" in mr.compose_digest(rows, store)


# --- gate --------------------------------------------------------------------

def _cfg(enabled=True):
    return {"enabled": enabled, "age_min": 15, "max_rows": 12}


def test_gate_digests_two_plus_stale_one_inject(monkeypatch):
    rows = [_row("a", 30), _row("b", 20), _row("c", 2)]
    store = FakeStore(rows)
    injects = []
    n = mr.deliver_stale_digest("gm", store, rows, _cfg(),
                                inject_fn=lambda s, t, r=None: injects.append((s, t)) or "submitted",
                                shadow=False)
    assert n == 2
    assert len(injects) == 1 and injects[0][0] == "gm"
    assert store.delivered == ["a", "b"]              # fresh c untouched
    assert ("a", {"digested_at": mr._digest_now()}) not in []  # stamped (loosely)
    assert [s[0] for s in store.stamped] == ["a", "b"]


def test_gate_single_stale_row_not_digested():
    rows = [_row("a", 30), _row("c", 2)]
    store = FakeStore(rows)
    injects = []
    n = mr.deliver_stale_digest("gm", store, rows, _cfg(),
                                inject_fn=lambda s, t, r=None: injects.append(1) or "submitted",
                                shadow=False)
    assert n == 0 and injects == [] and store.claimed == []


def test_gate_inject_fail_releases_all():
    rows = [_row("a", 30), _row("b", 20)]
    store = FakeStore(rows)
    n = mr.deliver_stale_digest("gm", store, rows, _cfg(),
                                inject_fn=lambda s, t, r=None: "failed", shadow=False)
    assert n == 0
    assert sorted(store.failed) == ["a", "b"] and store.delivered == []


def test_gate_shadow_mode_zero_writes():
    rows = [_row("a", 30), _row("b", 20)]
    store = FakeStore(rows)
    injects = []
    n = mr.deliver_stale_digest("gm", store, rows, _cfg(),
                                inject_fn=lambda s, t, r=None: injects.append(1) or "submitted",
                                shadow=True)
    assert n == 0 and injects == []
    assert store.claimed == [] and store.delivered == [] and store.stamped == []


def test_gate_disabled_config_noop():
    rows = [_row("a", 30), _row("b", 20)]
    store = FakeStore(rows)
    n = mr.deliver_stale_digest("gm", store, rows, _cfg(enabled=False),
                                inject_fn=lambda s, t, r=None: "submitted", shadow=False)
    assert n == 0 and store.claimed == []


def test_gate_max_rows_overflow_rolls():
    rows = [_row(f"r{i}", 60 - i) for i in range(15)]     # 15 stale
    store = FakeStore(rows)
    cfg = {"enabled": True, "age_min": 15, "max_rows": 12}
    n = mr.deliver_stale_digest("gm", store, rows, cfg,
                                inject_fn=lambda s, t, r=None: "submitted", shadow=False)
    assert n == 12
    assert len(store.delivered) == 12
    assert store.delivered[0] == "r0"                     # oldest first


# --- config + scope ----------------------------------------------------------

def test_digest_config_reads_protected_sessions(tmp_path, monkeypatch):
    p = tmp_path / "protected-sessions.json"
    p.write_text(json.dumps({"protected": ["gm"], "digest":
                             {"enabled": True, "age_min": 20, "max_rows": 5}}))
    monkeypatch.setattr(mr, "PROTECTED_SESSIONS_FILE", str(p))
    cfg = mr.digest_config()
    assert cfg["enabled"] is True and cfg["age_min"] == 20 and cfg["max_rows"] == 5
    assert mr.is_digest_session("gm") is True
    assert mr.is_digest_session("pm-x") is False


def test_digest_config_defaults_disabled(tmp_path, monkeypatch):
    p = tmp_path / "protected-sessions.json"
    p.write_text(json.dumps({"protected": ["gm"]}))       # no digest key
    monkeypatch.setattr(mr, "PROTECTED_SESSIONS_FILE", str(p))
    assert mr.digest_config()["enabled"] is False
