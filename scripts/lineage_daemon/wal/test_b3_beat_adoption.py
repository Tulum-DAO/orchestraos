"""B3 — bg_supervise_fleet ADOPTS the lossless-seam hooks + the MAX_CONCURRENT_GREENS fan-out cap
(v2.3 addendum, DEC-1789452018556720, anchor dea3f967, 3/3). RED-first.

Two pieces:
  1. HOOK ADOPTION: the production beat threads the provider-agnostic green_wake_fn (the #3/#4
     wake-green-to-ingest waker) into its per-seat BgArm, instead of leaving it None (=legacy
     verify-only, the arm-card-blocking adoption gap). green_ingested_seq_fn / blue_wal_event_count_fn
     are passed through too (None => BgArm's seams / real-reader fallback, unchanged).
  2. FAN-OUT CAP: a SOLO seat that would boot a NEW green is DEFERRED when in-flight greens
     (armed lineages in PREWARMING/READY/SWAPPING) >= cap (default 2, BG_MAX_CONCURRENT_GREENS
     override, core constant untouched). Fail-SAFE defer (reason=fanout:cap), never blocks a forced
     (supervised/death) drive. Provider-agnostic: counts by STATE, no runtime literal.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402


def _agent(root, ctx_pct=0.95, runtime="claude"):
    return {"agent_id": root, "runtime": runtime,
            "ctx": {"status_bar_pct": int(ctx_pct * 100), "model": "m"},
            "death": {"exhausted": False, "court": False, "state": None}}


def _blue(root):
    return {"blue_generation_id": 6, "generation": 6, "model": "m", "runtime": "claude"}


def _arm_flag(wal_dir, root):
    (wal_dir / f"{root}.bg_enabled").write_text("")


class _RecArm:
    """Recording arm: captures the ctor kwargs (so we can assert green_wake_fn was threaded)
    and records beat() calls; optionally flips its own bg_state on beat()."""
    def __init__(self, wal_dir, root, seams=None, cutover_active=None, forced=False,
                 green_wake_fn=None, green_ingested_seq_fn=None,
                 blue_wal_event_count_fn=None, flip_to=None, **kw):
        self.wal_dir = wal_dir
        self.root = root
        self.green_wake_fn = green_wake_fn
        self.beats = []
        self._flip_to = flip_to

    def beat(self, obs):
        self.beats.append(obs)
        if self._flip_to is not None:
            BgStateStore(self.wal_dir, self.root).write_state(self._flip_to, reason="test")


def _common(wal_dir, recorder, **over):
    d = dict(orchestra_dir="/nonexistent", wal_dir=str(wal_dir), now=1000.0,
             cutover_active=lambda: True,
             arm_ctor=lambda w, r, **kw: recorder(w, r, **kw),
             alarm_fn=lambda **k: None, blue_reader=_blue,
             seams_factory=lambda root, blue: object(),
             disarm_fn=lambda root: None)
    d.update(over)
    return d


# ---- 1. hook adoption --------------------------------------------------------

def test_beat_threads_live_green_wake_fn_when_none(tmp_path):
    """No green_wake_fn injected => the beat binds the LIVE provider-agnostic waker and threads a
    CALLABLE into arm_ctor (adoption). None at the beat does NOT mean legacy — BgArm's own
    green_wake_fn=None is the legacy sentinel; the beat's job is to supply the real waker."""
    _arm_flag(tmp_path, "a")
    made = []
    kw = _common(tmp_path, lambda w, r, **k: made.append(_RecArm(w, r, **k)) or made[-1])
    bg_beat.bg_supervise_fleet([_agent("a")], **kw)
    assert len(made) == 1
    assert callable(made[0].green_wake_fn)   # a real waker was threaded, not None


def test_beat_passes_injected_green_wake_fn_through(tmp_path):
    _arm_flag(tmp_path, "a")
    sentinel = lambda alias: True  # noqa: E731
    made = []
    kw = _common(tmp_path, lambda w, r, **k: made.append(_RecArm(w, r, **k)) or made[-1],
                 green_wake_fn=sentinel)
    bg_beat.bg_supervise_fleet([_agent("a")], **kw)
    assert made[0].green_wake_fn is sentinel


# ---- 2. fan-out cap ----------------------------------------------------------

def test_core_max_concurrent_greens_constant_is_2():
    assert bg_beat.MAX_CONCURRENT_GREENS == 2
    assert bg_beat._effective_max_concurrent_greens() == 2   # no env => core


def test_fanout_cap_defers_solo_seat_at_cap(tmp_path):
    """2 greens already in-flight (PREWARMING/READY) + 1 SOLO candidate, cap=2 => the SOLO seat is
    DEFERRED (arm.beat NOT called for it), reason=fanout:cap logged, and the in-flight seats still
    driven."""
    for r in ("a", "b", "c"):
        _arm_flag(tmp_path, r)
    states = {"a": "PREWARMING", "b": "READY", "c": "SOLO"}
    made = {}
    logs = []
    kw = _common(tmp_path, lambda w, r, **k: made.setdefault(r, _RecArm(w, r, **k)),
                 inflight_state_fn=lambda r: states[r], log_fn=logs.append)
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a"), _agent("b"), _agent("c")], **kw)
    assert summary["deferred_fanout"] == 1
    assert made["a"].beats and made["b"].beats          # in-flight seats driven
    assert "c" not in made                              # SOLO candidate never even constructed
    assert any("fanout:cap" in ln and "root=c" in ln for ln in logs)


def test_fanout_cap_env_override_raises_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("BG_MAX_CONCURRENT_GREENS", "3")
    assert bg_beat._effective_max_concurrent_greens() == 3
    for r in ("a", "b", "c"):
        _arm_flag(tmp_path, r)
    states = {"a": "PREWARMING", "b": "READY", "c": "SOLO"}
    made = {}
    kw = _common(tmp_path, lambda w, r, **k: made.setdefault(r, _RecArm(w, r, **k)),
                 inflight_state_fn=lambda r: states[r])
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a"), _agent("b"), _agent("c")], **kw)
    assert summary["deferred_fanout"] == 0
    assert made["c"].beats           # admitted because cap raised to 3


def test_fanout_cap_never_blocks_forced_seat(tmp_path):
    """A forced root (supervised / death-safety drive) is admitted even at cap — the cap NEVER
    blocks the safety path."""
    for r in ("a", "b", "c"):
        _arm_flag(tmp_path, r)
    states = {"a": "PREWARMING", "b": "READY", "c": "SOLO"}
    made = {}
    kw = _common(tmp_path, lambda w, r, **k: made.setdefault(r, _RecArm(w, r, **k)),
                 inflight_state_fn=lambda r: states[r], force_roots=["c"])
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a"), _agent("b"), _agent("c")], **kw)
    assert summary["deferred_fanout"] == 0
    assert made["c"].beats           # forced => admitted despite in-flight >= cap


def _run_beat_for_decision_line(tmp_path, logs):

    _arm_flag(tmp_path, "a")
    made = []
    kw = _common(tmp_path,
                 lambda w, r, **k: made.append(_RecArm(w, r, flip_to="PREWARMING", **k)) or made[-1],
                 log_fn=logs.append)
    bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)], **kw)


def test_beat_emits_decision_line_with_real_ctx_to_log(tmp_path):
    """Observability (gm-approved one-liner): each acted seat's decision + the REAL ctx value the
    beat read is emitted to logs/fleet-beat.log (via log_fn) — like the fan-out cap — so the
    autonomous beat's ctx decisions are visible in the canonical log, not only bg_state history.
    Proves-not-forced: the value carried is the obs ctx the beat itself read."""
    _arm_flag(tmp_path, "a")
    logs = []
    made = []
    kw = _common(tmp_path,
                 lambda w, r, **k: made.append(_RecArm(w, r, flip_to="PREWARMING", **k)) or made[-1],
                 log_fn=logs.append)
    bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)], **kw)
    dec = [ln for ln in logs if ln.startswith("decision") and "root=a" in ln]
    assert dec, f"no decision line emitted; logs={logs}"
    assert "ctx=" in dec[0]                       # carries the real ctx value
    assert "PREWARMING" in dec[0]                 # carries the resulting state


def test_within_beat_admission_counts_toward_cap(tmp_path):
    """3 SOLO seats, cap=2, 0 base in-flight: the first two admitted seats each boot a green
    (flip SOLO->PREWARMING); the third is DEFERRED because the two admitted this beat consume
    the cap (within-beat accounting, not just the base count)."""
    for r in ("a", "b", "c"):
        _arm_flag(tmp_path, r)
    made = {}
    # each admitted arm flips its own real bg_state to PREWARMING on beat() (a booted green)
    kw = _common(tmp_path,
                 lambda w, r, **k: made.setdefault(r, _RecArm(w, r, flip_to="PREWARMING", **k)))
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a"), _agent("b"), _agent("c")], **kw)
    admitted = [r for r in ("a", "b", "c") if r in made and made[r].beats]
    assert len(admitted) == 2          # exactly cap admitted
    assert summary["deferred_fanout"] == 1


def test_beat_decision_line_carries_decide_bg_reason(tmp_path):
    """gm queue (c) (msg_e7d7f734): the production fleet-beat decision line must also carry
    the decide_bg REASON token (decide=...), so a READY seat that is held by L3 guards
    (suppress:composer / suppress:attached) or that qualified (card:wait-swap) is legible
    from the canonical log, not only from the shadow jsonl."""
    logs = []
    _run_beat_for_decision_line(tmp_path, logs)
    dec = [ln for ln in logs if ln.startswith("decision") and "root=a" in ln]
    assert dec, f"no decision line emitted; logs={logs}"
    # ctx 0.95 -> decide_bg says ctx:swap; the READY-gate defers it to prewarm — the line must
    # show BOTH: what the ladder decided (decide=) and what the machine did (state/reason).
    assert "decide=ctx:swap" in dec[0], dec[0]
