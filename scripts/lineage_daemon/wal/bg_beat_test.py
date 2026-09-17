"""RED-first tests for bg_beat.bg_supervise_fleet — the Gate-3.5 live driver that
wires bg_arm.beat() into the fleet beat (locked contract DEC-1788415854).

The driver is the ONLY thing that makes an armed lineage actually rotate; it runs
AFTER plan_fleet on the SAME collected fleet, and it MUST NOT be able to regress
the live in-band rotation. These tests pin the 7 load-bearing invariants:

  1. FAIL-ISOLATION FIREWALL — a raising arm can NEVER propagate out of the pass;
     per-lineage exception => continue (skip only that seat), swallow-to-alarm.
  2. MUTUAL EXCLUSION — an armed seat is excluded from the old plan_fleet path
     (cron_beat computes exclude = DEFAULT_EXCLUDE | armed).
  3. INERT-by-effect — unarmed fleet = one is_armed stat/agent, then a pure no-op
     (no seams_factory / arm / bg_state write when no seat is armed).
  4. bg_enabled ⊆ cutover-active — BgArm.beat raises ArmRefused when armed while
     cutover inactive; the firewall turns it into a real alarm, never a crash.
  5. GLOBAL BG_DISABLED kill-switch — one file disarms the whole fleet.
  6. DB WRITE-TRUTH — blue_generation_id + green resolved from the canonical DB
     each beat; absent canonical => SwapPreconditionError, fail-closed (alarmed).
  7. ANTI-ORPHAN (ob's consensus catch) — inv1+inv2 together can orphan a
     persistently-throwing armed seat; N consecutive bg-failures => disarm-to-
     legacy (drop bg_enabled) + page, never silent indefinite orphaning.
"""
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402
from lineage_daemon.wal.bg_state import is_armed  # noqa: E402
from lineage_daemon.wal.bg_arm import ArmRefused  # noqa: E402
from identity_store.identity_writer import SwapPreconditionError  # noqa: E402


# ---- fakes ------------------------------------------------------------------

def _agent(root, ctx_pct=0.0, runtime="claude", death_state=None):
    """A collected-fleet-shaped agent dict (collect.py shape: ctx.status_bar_pct
    is a PERCENT int; death is a dict)."""
    return {
        "agent_id": root,
        "runtime": runtime,
        "ctx": {"status_bar_pct": int(ctx_pct * 100), "model": "claude-opus-4-8[1m]"},
        "death": {"exhausted": bool(death_state), "court": False,
                  "state": death_state},
    }


class _RecordingArm:
    """arm_ctor stand-in: records beat() calls; optionally raises."""
    instances = []

    def __init__(self, wal_dir, root, seams=None, cutover_active=None, raises=None,
                 forced=False, **kwargs):
        # **kwargs absorbs the B3-threaded hooks (green_wake_fn / green_ingested_seq_fn /
        # blue_wal_event_count_fn) so these invariant tests stay unchanged; green_wake_fn is
        # recorded for the B3 adoption assertions in test_b3_beat_adoption.py.
        self.root = root
        self.beats = []
        self._raises = raises
        self.forced = forced
        self.green_wake_fn = kwargs.get("green_wake_fn")
        _RecordingArm.instances.append(self)

    def beat(self, obs):
        self.beats.append(obs)
        if self._raises is not None:
            raise self._raises


def _arm_ctor_factory(raises=None):
    def ctor(wal_dir, root, seams=None, cutover_active=None, forced=False, **kwargs):
        return _RecordingArm(wal_dir, root, seams=seams,
                             cutover_active=cutover_active, raises=raises,
                             forced=forced, **kwargs)
    return ctor


class _Alarms:
    def __init__(self):
        self.calls = []

    def __call__(self, *, kind, root, detail, consecutive, page):
        self.calls.append({"kind": kind, "root": root, "detail": detail,
                           "consecutive": consecutive, "page": page})


def _blue(root):
    return {"blue_generation_id": 6, "generation": 6, "model": "claude-opus-4-8[1m]"}


def _arm_flag(wal_dir, root):
    (wal_dir / f"{root}.bg_enabled").write_text("")


def _common(wal_dir, **over):
    """Default injected kwargs for bg_supervise_fleet (all seams faked)."""
    d = dict(orchestra_dir="/nonexistent", wal_dir=str(wal_dir), now=1000.0,
             cutover_active=lambda: True, arm_ctor=_arm_ctor_factory(),
             alarm_fn=_Alarms(), blue_reader=_blue,
             seams_factory=lambda root, blue: object(),
             disarm_fn=lambda root: (wal_dir / f"{root}.bg_enabled").unlink())
    d.update(over)
    return d


def setup_function():
    _RecordingArm.instances = []


# ---- inv 3: INERT-by-effect --------------------------------------------------

def test_unarmed_fleet_is_pure_noop(tmp_path):
    """No armed seat => no arm ever constructed, no seams built, summary armed=0."""
    seams_built = []
    kw = _common(tmp_path, arm_ctor=_arm_ctor_factory(),
                 seams_factory=lambda root, blue: seams_built.append(root))
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a"), _agent("b", ctx_pct=0.95)], **kw)
    assert summary["armed"] == 0
    assert summary["acted"] == 0
    assert _RecordingArm.instances == []   # arm never constructed
    assert seams_built == []                # seams never built for unarmed seats


# ---- inv 5: global BG_DISABLED kill-switch -----------------------------------

def test_global_bg_disabled_disarms_whole_fleet(tmp_path):
    _arm_flag(tmp_path, "a")
    (tmp_path / "BG_DISABLED").write_text("")   # one-tap fleet halt
    summary = bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)],
                                         **_common(tmp_path))
    assert summary["armed"] == 0
    assert _RecordingArm.instances == []


# ---- inv 1: fail-isolation firewall -----------------------------------------

def test_raising_arm_never_propagates_and_is_alarmed(tmp_path):
    _arm_flag(tmp_path, "a")
    alarms = _Alarms()
    kw = _common(tmp_path, arm_ctor=_arm_ctor_factory(raises=RuntimeError("boom")),
                 alarm_fn=alarms)
    # MUST NOT raise — the firewall swallows to an alarm.
    summary = bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)], **kw)
    assert summary["failed"] == 1
    assert len(alarms.calls) == 1
    assert alarms.calls[0]["kind"] == "bg-failure"


def test_one_raising_seat_does_not_skip_the_others(tmp_path):
    _arm_flag(tmp_path, "a")
    _arm_flag(tmp_path, "b")

    def ctor(wal_dir, root, seams=None, cutover_active=None, forced=False, **kwargs):
        return _RecordingArm(wal_dir, root, raises=RuntimeError("boom")
                             if root == "a" else None, forced=forced, **kwargs)

    kw = _common(tmp_path, arm_ctor=ctor, alarm_fn=_Alarms())
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a", ctx_pct=0.95), _agent("b", ctx_pct=0.72)], **kw)
    # a failed but b was still supervised (per-lineage continue).
    assert summary["failed"] == 1
    assert summary["acted"] == 1
    acted_roots = {arm.root for arm in _RecordingArm.instances if arm.beats}
    assert "b" in acted_roots


# ---- inv 4: bg_enabled ⊆ cutover-active (ArmRefused) --------------------------

def test_armed_without_cutover_is_alarmed_not_crashed(tmp_path):
    _arm_flag(tmp_path, "a")
    alarms = _Alarms()
    kw = _common(tmp_path, arm_ctor=_arm_ctor_factory(
        raises=ArmRefused("cutover inactive")), alarm_fn=alarms)
    summary = bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)], **kw)
    assert summary["failed"] == 1
    assert alarms.calls[0]["kind"] == "bg-failure"


# ---- inv 6: DB write-truth, fail-closed on absent canonical ------------------

def test_absent_canonical_fails_closed_and_alarms(tmp_path):
    _arm_flag(tmp_path, "a")
    alarms = _Alarms()

    def raising_reader(root):
        raise SwapPreconditionError(f"no canonical for {root}")

    kw = _common(tmp_path, blue_reader=raising_reader, alarm_fn=alarms)
    summary = bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)], **kw)
    assert summary["failed"] == 1
    assert len(alarms.calls) == 1


def test_build_obs_maps_percent_to_fraction_and_green_is_blue_plus_one():
    obs = bg_beat.build_obs(_agent("a", ctx_pct=0.82), _blue("a"))
    assert obs["ctx_pct"] == pytest.approx(0.82)
    assert obs["blue_generation_id"] == 6
    assert obs["green"]["generation"] == 7      # blue.generation + 1 (write-truth)
    assert obs["ceiling_calibrated"] is True    # fresh in-range ctx = calibrated (data-driven)


def test_build_obs_calibrated_data_driven_any_runtime():
    """v2 (the operator #1, gm RULING msg_02d19242): calibration is DATA-DRIVEN, not by runtime
    name. A non-default runtime with a fresh in-range ctx IS calibrated (this replaces the
    v1 rule 'calibrated = runtime==claude' that gm rejected). Absent ctx fails closed for
    EVERY runtime."""
    obs = bg_beat.build_obs(_agent("a", ctx_pct=0.82, runtime="gemini"), _blue("a"))
    assert obs["ceiling_calibrated"] is True     # fresh in-range ctx -> calibrated
    obs_none = bg_beat.build_obs(
        {"agent_id": "a", "runtime": "gemini", "ctx": {}, "death": {}}, _blue("a"))
    assert obs_none["ceiling_calibrated"] is False  # absent ctx -> fail-closed, any runtime


# ---- inv 7: anti-orphan (disarm-to-legacy or page after N failures) ----------

def test_persistently_throwing_armed_seat_is_disarmed_after_n_beats(tmp_path):
    _arm_flag(tmp_path, "a")
    alarms = _Alarms()
    disarmed = []
    kw = _common(tmp_path, arm_ctor=_arm_ctor_factory(raises=RuntimeError("boom")),
                 alarm_fn=alarms, orphan_after=3,
                 disarm_fn=lambda root: disarmed.append(root))
    # beats 1 and 2: still armed, plain bg-failure alarms, no disarm.
    for beat in (1, 2):
        s = bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)], **kw)
        assert s["disarmed"] == 0, f"disarmed too early at beat {beat}"
        assert disarmed == []
    assert [c["kind"] for c in alarms.calls] == ["bg-failure", "bg-failure"]
    # beat 3: N consecutive => disarm-to-legacy + page (never silent orphan).
    s = bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)], **kw)
    assert s["disarmed"] == 1
    assert disarmed == ["a"]
    assert alarms.calls[-1]["kind"] == "orphan-disarm"
    assert alarms.calls[-1]["page"] is True


def test_default_disarm_fn_removes_the_bg_enabled_flag(tmp_path):
    _arm_flag(tmp_path, "a")
    assert is_armed(str(tmp_path), "a") is True
    bg_beat._default_disarm_fn(str(tmp_path))("a")
    assert is_armed(str(tmp_path), "a") is False   # fell back to the legacy path


def test_success_resets_the_failure_counter(tmp_path):
    _arm_flag(tmp_path, "a")
    # two failures, then a success => counter reset (no lingering toward disarm).
    fail_kw = _common(tmp_path, arm_ctor=_arm_ctor_factory(raises=RuntimeError("x")),
                      orphan_after=3)
    bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)], **fail_kw)
    bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)], **fail_kw)
    assert bg_beat._read_failures(str(tmp_path), "a") == 2
    ok_kw = _common(tmp_path)   # arm that does not raise
    bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.95)], **ok_kw)
    assert bg_beat._read_failures(str(tmp_path), "a") == 0


# ---- stale flag for a non-fleet seat = invisible, never a false alarm --------

def test_stale_flag_for_non_fleet_seat_raises_no_false_alarm(tmp_path):
    """armed_roots is fleet-scoped (one is_armed stat per COLLECTED agent, per the
    spec's 'same already-collected fleet'). A leftover bg_enabled flag for a seat
    that is not in the fleet this beat is simply not supervised — and MUST NOT
    generate a spurious failure/orphan/alarm."""
    _arm_flag(tmp_path, "ghost")               # armed but not in the fleet
    alarms = _Alarms()
    kw = _common(tmp_path, alarm_fn=alarms)
    summary = bg_beat.bg_supervise_fleet([_agent("other")], **kw)
    assert summary["armed"] == 0               # ghost not statted (not collected)
    assert summary["acted"] == 0
    assert summary["failed"] == 0              # no false orphan march
    assert alarms.calls == []
    assert _RecordingArm.instances == []


# ---- leg (i) supervised drive: force_roots / --manual-override bypass ---------
# The supervised hand-drive (bg_live_beat.py) must fire ONE named root WITH the
# global BG_DISABLED kill-switch STILL PRESENT (so the */15 cron_beat and the
# telemetryd beat — which never pass force_roots — stay strict-no-ops and there is
# no second driver to race). force_roots is an explicit per-beat allowlist: a root
# it names is armed for THIS beat only, bypassing BOTH BG_DISABLED and the
# per-lineage bg_enabled flag; it is NEVER persisted and NEVER touches disk state.

def test_force_roots_fires_with_global_bg_disabled_present(tmp_path):
    """The whole point of the bypass: BG_DISABLED present but the named root fires."""
    _arm_flag(tmp_path, "a")
    (tmp_path / "BG_DISABLED").write_text("")          # halt STAYS present
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a", ctx_pct=0.95)], **_common(tmp_path, force_roots={"a"}))
    assert summary["armed"] == 1
    assert len(_RecordingArm.instances) == 1
    assert _RecordingArm.instances[0].root == "a"


def test_force_roots_does_not_need_the_per_lineage_flag(tmp_path):
    """force_roots bypasses is_armed entirely — no bg_enabled flag required."""
    # NO _arm_flag(a); NO BG_DISABLED. is_armed(a) is False, force_roots overrides.
    assert is_armed(str(tmp_path), "a") is False
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a", ctx_pct=0.95)], **_common(tmp_path, force_roots={"a"}))
    assert summary["armed"] == 1
    assert [i.root for i in _RecordingArm.instances] == ["a"]


def test_force_roots_is_scoped_only_to_named_roots(tmp_path):
    """A forced beat must select ONLY the named root — never the rest of the fleet,
    even armed siblings (BG_DISABLED present keeps them inert; the bypass is surgical)."""
    _arm_flag(tmp_path, "a")
    _arm_flag(tmp_path, "b")
    (tmp_path / "BG_DISABLED").write_text("")
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a", ctx_pct=0.95), _agent("b", ctx_pct=0.95)],
        **_common(tmp_path, force_roots={"a"}))
    assert summary["armed"] == 1
    assert [i.root for i in _RecordingArm.instances] == ["a"]   # b never constructed


def test_force_roots_default_preserves_the_bg_disabled_halt(tmp_path):
    """Regression: the cron/telemetryd path passes NO force_roots — BG_DISABLED must
    still disarm the whole fleet (byte-identical to today's kill-switch behavior)."""
    _arm_flag(tmp_path, "a")
    (tmp_path / "BG_DISABLED").write_text("")
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a", ctx_pct=0.95)], **_common(tmp_path))   # force_roots absent
    assert summary["armed"] == 0
    assert _RecordingArm.instances == []


def test_force_roots_for_absent_seat_is_invisible_no_false_alarm(tmp_path):
    """A forced root not in the collected fleet this beat is unobservable — selected
    but not acted on, no arm, no failure, no alarm (mirrors the ghost-flag rule)."""
    alarms = _Alarms()
    kw = _common(tmp_path, alarm_fn=alarms, force_roots={"ghost"})
    summary = bg_beat.bg_supervise_fleet([_agent("other")], **kw)
    assert summary["acted"] == 0
    assert summary["failed"] == 0
    assert alarms.calls == []
    assert _RecordingArm.instances == []


def test_force_roots_bypasses_the_REAL_BgArm_is_armed_guard(tmp_path):
    """DECISIVE seam (not a fake arm): the REAL BgArm.beat re-checks is_armed
    (bg_arm.py:57-58) and no-ops with BG_DISABLED present. force_roots must ALSO
    make the real arm act, or the supervised hand-drive selects the seat but the
    arm silently no-ops (the fake-only gap the mocked-arm tests hid)."""
    from lineage_daemon.wal.bg_arm import BgArm
    from lineage_daemon.wal.bg_state import BgStateStore
    (tmp_path / "BG_DISABLED").write_text("")          # global halt STAYS present
    calls = []

    class _FakeSeams:
        def register_provisional(self, *a, **k): calls.append("register")
        def project_now(self, *a, **k): calls.append("project")
        def spawn(self, *a, **k): calls.append("spawn")
        def verify(self, *a, **k): return False
        def hydrate(self, *a, **k): calls.append("hydrate")
        def produce(self, *a, **k): calls.append("produce")

    kw = _common(tmp_path, arm_ctor=BgArm,             # the REAL arm
                 seams_factory=lambda root, blue: _FakeSeams(),
                 cutover_active=lambda: True)
    # GOAL-FINDING #5: the real BgArm refuses to spawn an un-hydratable green, so an
    # armed seat must have a non-empty WAL. Seed one event (a real armed seat has capture).
    from lineage_daemon.wal.store import WalStore
    _ws = WalStore(str(tmp_path / "a.db"))
    _ws.append(ts=1000.0, lineage_root="a", generation=6, sid="sid-a", runtime="claude",
               kind="response", summary="seed", source_path="s")
    _ws.close()
    summary = bg_beat.bg_supervise_fleet(
        [_agent("a", ctx_pct=0.72)], force_roots={"a"}, **kw)
    # SOLO + prewarm-ctx + forced => the real arm registers/spawns and advances.
    assert "spawn" in calls, "real BgArm no-op'd despite force_roots (is_armed guard)"
    assert BgStateStore(str(tmp_path), "a").read()["state"] == "PREWARMING"
    assert summary["acted"] == 1


def test_forced_beat_still_raises_ArmRefused_when_cutover_inactive(tmp_path):
    """force bypasses ONLY the is_armed guard, NEVER the cutover gate (bg_arm.py:73):
    a forced beat with the store cutover inactive must STILL raise ArmRefused (a swap
    is impossible pre-cutover — never fire a doomed swap even under --manual-override)."""
    from lineage_daemon.wal.bg_arm import BgArm
    (tmp_path / "BG_DISABLED").write_text("")          # halt present
    calls = []

    class _FakeSeams:
        def register_provisional(self, *a, **k): calls.append("register")
        def project_now(self, *a, **k): calls.append("project")
        def spawn(self, *a, **k): calls.append("spawn")
        def verify(self, *a, **k): return False
        def hydrate(self, *a, **k): calls.append("hydrate")
        def produce(self, *a, **k): calls.append("produce")

    arm = BgArm(str(tmp_path), "a", seams=_FakeSeams(), blue_wal_event_count_fn=lambda: 1,
                cutover_active=lambda: False, forced=True)
    obs = {"root": "a", "runtime": "claude", "ctx_pct": 0.72, "death": "",
           "ceiling_calibrated": True, "blue_generation_id": 6,
           "green": {"generation": 7, "model": "m"}}
    with pytest.raises(ArmRefused):
        arm.beat(obs)
    assert calls == []                                 # no spawn on a refused arm


def test_real_BgArm_still_inert_with_bg_disabled_and_no_force(tmp_path):
    """Regression: the REAL arm must STILL no-op with BG_DISABLED present when NOT
    forced (the kill-switch keeps the autonomous path inert)."""
    from lineage_daemon.wal.bg_arm import BgArm
    from lineage_daemon.wal.bg_state import BgStateStore
    _arm_flag(tmp_path, "a")
    (tmp_path / "BG_DISABLED").write_text("")
    calls = []

    class _FakeSeams:
        def register_provisional(self, *a, **k): calls.append("register")
        def project_now(self, *a, **k): calls.append("project")
        def spawn(self, *a, **k): calls.append("spawn")
        def verify(self, *a, **k): return False
        def hydrate(self, *a, **k): calls.append("hydrate")
        def produce(self, *a, **k): calls.append("produce")

    kw = _common(tmp_path, arm_ctor=BgArm,
                 seams_factory=lambda root, blue: _FakeSeams(),
                 cutover_active=lambda: True)
    # no force_roots => armed_roots selects nothing (BG_DISABLED) => arm never built.
    summary = bg_beat.bg_supervise_fleet([_agent("a", ctx_pct=0.72)], **kw)
    assert calls == []
    assert summary["armed"] == 0


def test_armed_roots_force_roots_unit(tmp_path):
    """Unit on the selection primitive: force_roots ORs onto the is_armed set,
    stays fleet-scoped, and does not duplicate an already-armed root."""
    _arm_flag(tmp_path, "armed")
    (tmp_path / "BG_DISABLED").write_text("")
    agents = [_agent("armed"), _agent("forced"), _agent("cold")]
    # is_armed is False for all three (BG_DISABLED present); force picks only "forced".
    assert bg_beat.armed_roots(agents, str(tmp_path)) == []
    assert bg_beat.armed_roots(agents, str(tmp_path), force_roots={"forced"}) == ["forced"]
    # no dup when a forced root is also is_armed (remove the halt so "armed" is armed).
    (tmp_path / "BG_DISABLED").unlink()
    got = bg_beat.armed_roots(agents, str(tmp_path), force_roots={"armed"})
    assert got == ["armed"]


def test_armed_roots_never_arms_experimental_runtimes_unless_opted_in(tmp_path, monkeypatch):
    """Operator ruling 2026-09-17: gemini/codex stay out of the async blue-green lane unless
    [rotation] experimental_runtimes opts in; the supervised force_roots hand-drive still wins."""
    monkeypatch.delenv("ORCHESTRA_ROTATION_EXPERIMENTAL_RUNTIMES", raising=False)
    _arm_flag(tmp_path, "gem"); _arm_flag(tmp_path, "cla")
    gem = dict(_agent("gem"), runtime="gemini"); cla = dict(_agent("cla"), runtime="claude")
    assert bg_beat.armed_roots([gem, cla], str(tmp_path)) == ["cla"]
    assert bg_beat.armed_roots([gem, cla], str(tmp_path), force_roots={"gem"}) == ["gem", "cla"]
    monkeypatch.setenv("ORCHESTRA_ROTATION_EXPERIMENTAL_RUNTIMES", "gemini")
    assert bg_beat.armed_roots([gem, cla], str(tmp_path)) == ["gem", "cla"]
