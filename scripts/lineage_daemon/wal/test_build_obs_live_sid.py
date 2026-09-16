"""RED (BG leg-(ii) (b) P0.6-live-sid — key the ctx read on the LIVE occupant sid).

P0.6 keys the detector read on ``blue.get("session_id")`` = the CANONICAL seat sid, which
may be a STALE seat sid (an OLD occupant), not the LIVE pane occupant. This violates the
blocker_surface_watchdog C1/R1 invariant: "the ctx% read is the LIVE OCCUPANT's used_pct,
never the stale seat sid ... live_sid != read_sid => a fresh occupant"
(scripts/blocker_surface_watchdog.py:23-28, 257-264).

FIX: resolve blue's LIVE pane-occupant sid (reusing blocker_surface_watchdog's named R1
mechanism — session-index's declaration-resolved sid in state/agent-sessions.json, i.e.
sid_resolver_from_sessions) and key the detector read on THAT, falling back to the canonical
sid only when no live sid is resolvable (no regression). The reader is INJECTABLE
(live_sid_fn) and wired in bg_supervise_fleet (which holds orchestra_dir).

Real-object (leg-(i) lesson): every case drives the REAL build_obs against REAL detector
files + a REAL BgStateStore; the live-sid reader is injected for hermeticity (the production
default reads the real state/agent-sessions.json). RED until build_obs keys the detector on
the resolved live sid and exposes it.
"""
import json
import os

from scripts.lineage_daemon.wal import bg_beat

ROOT = "second-brain-dev"
STALE_CANONICAL_SID = "stale-seat-sid-OLD"
LIVE_OCCUPANT_SID = "live-occupant-sid-NEW"


def _blue(gen=4, sid=STALE_CANONICAL_SID):
    b = {"generation": gen, "model": "claude-opus-4-8[1m]", "blue_generation_id": 41}
    if sid is not None:
        b["session_id"] = sid
    return b


def _agent(status_bar=None):
    ctx = {} if status_bar is None else {"status_bar_pct": status_bar}
    return {"agent_id": ROOT, "runtime": "claude", "ctx": ctx, "death": {}}


def _write_detector(detector_dir, sid, used_pct, ts):
    os.makedirs(detector_dir, exist_ok=True)
    with open(os.path.join(detector_dir, f"claude-ctx-{sid}.json"), "w") as fh:
        json.dump({"session_id": sid, "used_pct": used_pct, "timestamp": ts}, fh)


# ── the LIVE occupant sid wins over the stale canonical seat sid ───────────────

def test_ctx_keyed_on_live_sid_not_stale_canonical(tmp_path):
    det = str(tmp_path / "det")
    # a STALE canonical detector (an old occupant left it) reads 40%; the LIVE occupant
    # reads 83%. The C1/R1 invariant: we must read the LIVE occupant's 83%, not 40%.
    _write_detector(det, STALE_CANONICAL_SID, used_pct=40, ts=1000.0)
    _write_detector(det, LIVE_OCCUPANT_SID, used_pct=83, ts=1000.0)
    obs = bg_beat.build_obs(
        _agent(), _blue(), wal_dir=str(tmp_path / "wal"), now=1000.0, ctx_ttl_s=120.0,
        detector_dir=det, live_sid_fn=lambda root: LIVE_OCCUPANT_SID)
    assert obs["ctx_pct"] == 0.83, \
        "C1/R1: the ctx read must key on the LIVE occupant sid (83%), never the stale seat (40%)"
    assert obs["ctx_source"] == "adapter"
    assert obs["blue_sid_used"] == LIVE_OCCUPANT_SID
    assert obs["blue_sid_source"] == "live"


# ── mismatch is surfaced (observability, non-gating) ───────────────────────────

def test_live_vs_canonical_mismatch_flagged(tmp_path):
    det = str(tmp_path / "det")
    _write_detector(det, LIVE_OCCUPANT_SID, used_pct=83, ts=1000.0)
    obs = bg_beat.build_obs(
        _agent(), _blue(), wal_dir=str(tmp_path / "wal"), now=1000.0, ctx_ttl_s=120.0,
        detector_dir=det, live_sid_fn=lambda root: LIVE_OCCUPANT_SID)
    assert obs["blue_sid_live_mismatch"] is True, \
        "a live_sid != canonical seat sid must be surfaced (fresh-occupant signal)"


# ── fallback: no live sid resolvable -> canonical sid (no regression) ──────────

def test_falls_back_to_canonical_when_no_live_sid(tmp_path):
    det = str(tmp_path / "det")
    _write_detector(det, STALE_CANONICAL_SID, used_pct=71, ts=1000.0)
    obs = bg_beat.build_obs(
        _agent(), _blue(), wal_dir=str(tmp_path / "wal"), now=1000.0, ctx_ttl_s=120.0,
        detector_dir=det, live_sid_fn=lambda root: None)  # session-index has no live sid
    assert obs["ctx_pct"] == 0.71, "no live sid => fall back to the canonical sid's detector"
    assert obs["blue_sid_used"] == STALE_CANONICAL_SID
    assert obs["blue_sid_source"] == "canonical"
    assert obs["blue_sid_live_mismatch"] is False


def test_legacy_no_live_sid_fn_uses_canonical(tmp_path):
    # legacy callers pass no live_sid_fn => behavior identical to P0.6 (canonical sid).
    det = str(tmp_path / "det")
    _write_detector(det, STALE_CANONICAL_SID, used_pct=66, ts=1000.0)
    obs = bg_beat.build_obs(
        _agent(), _blue(), wal_dir=str(tmp_path / "wal"), now=1000.0, ctx_ttl_s=120.0,
        detector_dir=det)
    assert obs["ctx_pct"] == 0.66
    assert obs["blue_sid_source"] == "canonical"


# ── the production reader: state/agent-sessions.json (R1's named mechanism) ─────

def test_resolve_live_blue_sid_reads_agent_sessions(tmp_path):
    orch = str(tmp_path)
    os.makedirs(os.path.join(orch, "state"), exist_ok=True)
    with open(os.path.join(orch, "state", "agent-sessions.json"), "w") as fh:
        json.dump({ROOT: {"session_id": LIVE_OCCUPANT_SID, "status": "online"},
                   "other": {"session_id": "x"}}, fh)
    assert bg_beat._resolve_live_blue_sid(orch, ROOT) == LIVE_OCCUPANT_SID


def test_resolve_live_blue_sid_none_when_absent_or_null(tmp_path):
    orch = str(tmp_path)
    os.makedirs(os.path.join(orch, "state"), exist_ok=True)
    with open(os.path.join(orch, "state", "agent-sessions.json"), "w") as fh:
        json.dump({ROOT: {"session_id": None, "status": "online"}}, fh)
    assert bg_beat._resolve_live_blue_sid(orch, ROOT) is None          # null sid
    assert bg_beat._resolve_live_blue_sid(orch, "missing-agent") is None  # absent row
    # a totally absent file is also None (fail-soft, never raises into the beat)
    assert bg_beat._resolve_live_blue_sid(str(tmp_path / "nope"), ROOT) is None


def test_resolve_live_blue_sid_none_on_array_shaped_file(tmp_path):
    # hardening (leaf-2 reviewer nit): a JSON-ARRAY-shaped agent-sessions.json makes
    # `.get(root)` raise AttributeError; the helper must catch it (fail-soft None), not
    # only OSError/ValueError/TypeError, so even a DIRECT helper call never raises.
    orch = str(tmp_path)
    os.makedirs(os.path.join(orch, "state"), exist_ok=True)
    with open(os.path.join(orch, "state", "agent-sessions.json"), "w") as fh:
        json.dump(["not", "a", "dict"], fh)
    assert bg_beat._resolve_live_blue_sid(orch, ROOT) is None
