"""Tests for cron_beat.py — the live fleet-beat cron entrypoint (soft_only first window).

Asserts by effect: soft_only defers EVERY hard_rotate (0 rotations, the binding
first-window invariant), SOFT agents get reversible author-triggers, the kill-switch
sentinel makes the beat a no-op, and state (locks/ledger/history) round-trips.
"""
import json

import pytest

from scripts.lineage_daemon import cron_beat
from scripts.lineage_daemon import beat as _beat
from scripts.lineage_daemon import fleet as _fleet


@pytest.fixture(autouse=True)
def _isolate_runtime_ebrake(tmp_path, monkeypatch):
    """Hermetic: point the non-synced runtime e-brake dir at a clean tmp so the
    dual-location kill_switch_engaged never picks up a live ~/runtime sentinel."""
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path / "rt-ebrake"))


def _agent(aid, pct, tier="T2"):
    return {"agent_id": aid, "tier_class": tier, "death": {},
            "ctx": {"status_bar_pct": pct}}


REG = {"agents": {a: {"generation": 1, "tier": "T2", "lineage_root": a}
                  for a in ("hot-hard", "soft-1", "soft-2", "calm")}}


def _fresh_state():
    return {"locks": {"rotations": []}, "ledger": {"holds": []},
            "history": {"retires": []}}


class _ArmedAny(frozenset):
    def __contains__(self, item):
        return True


def _held_state(canary="hot-hard", successor="hot-hard-g2"):
    st = _fresh_state()
    st["ledger"] = {"holds": [{"canary": canary, "successor": successor,
                               "status": "hold:successor-unconfirmed", "reason": "x",
                               "created_at": 0, "resolved": False}]}
    return st


_FAKE_COMPLETED = {"status": "completed", "promoted": True, "retired": True,
                   "budget_consumed": True, "recount": False, "steps": []}


# --- cron_beat wires the completion provider (INERT-UNTIL-ARMED) ----------------

def test_run_beat_threads_completion_provider_and_completes(tmp_path, monkeypatch):
    """A wired provider + an armed lineage with an open hold + soft_only False ->
    the completion branch fires (rotated>0), provider called with armed=True."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    calls = []

    def prov(canary, hold, *, armed, budget, now):
        calls.append((canary, armed))
        return {**_FAKE_COMPLETED, "canary": canary, "successor": hold["successor"]}

    r = cron_beat.run_beat(
        [_agent("hot-hard", 95)], REG, _held_state(), now=1000,
        orchestra_dir=str(tmp_path), dry=True, soft_only=False,
        armed_lineages=_ArmedAny(), completion_provider=prov)
    assert calls == [("hot-hard", True)]
    assert r["summary"]["rotated"] == 1


def test_run_beat_builds_default_completion_provider(tmp_path, monkeypatch):
    """With no override, run_beat BUILDS a real completion provider and threads it to
    plan_fleet (so the mechanism exists live; the arm gate keeps it inert)."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    captured = {}
    real = _fleet.plan_fleet

    def spy(*a, **k):
        captured["cp"] = k.get("completion_provider")
        return real(*a, **k)

    monkeypatch.setattr(_fleet, "plan_fleet", spy)
    cron_beat.run_beat([_agent("calm", 40)], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True)
    assert captured["cp"] is not None and callable(captured["cp"])


def test_run_beat_soft_only_keeps_completion_inert(tmp_path, monkeypatch):
    """soft_only=True: even a wired provider + an armed held lineage never fires
    (rotated=0) — the completion branch is arm-gated exactly like the spawn path."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    calls = []

    def prov(canary, hold, *, armed, budget, now):
        calls.append(1)
        return {**_FAKE_COMPLETED, "canary": canary, "successor": hold["successor"]}

    r = cron_beat.run_beat(
        [_agent("hot-hard", 95)], REG, _held_state(), now=1000,
        orchestra_dir=str(tmp_path), dry=True, soft_only=True,
        armed_lineages=_ArmedAny(), completion_provider=prov)
    assert calls == []
    assert r["summary"]["rotated"] == 0


# --- soft_only defers EVERY hard_rotate (the binding first-window invariant) ---

def test_soft_only_defers_all_hard_rotations(tmp_path, monkeypatch):
    # Tests the first-window invariant EXPLICITLY (soft_only=True) so it holds
    # regardless of the module SOFT_ONLY constant (now armed=False, the operator-directed).
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    agents = [_agent("hot-hard", 95), _agent("soft-1", 75), _agent("calm", 40)]
    # dry=True -> author_inject is a no-op recorder; no live msg_store writes.
    r = cron_beat.run_beat(agents, REG, _fresh_state(), now=1000,
                           orchestra_dir=str(tmp_path), dry=True, soft_only=True)
    by = {b["agent_id"]: b for b in r["beats"]}
    # HARD agent deferred, NOT rotated
    assert by["hot-hard"]["armed_status"] == _fleet.SKIP_SOFT_ONLY
    assert r["summary"]["rotated"] == 0                # THE invariant: 0 hard rotations
    assert r["summary"]["skipped_soft_only"] == 1


def test_soft_agents_get_reversible_author_triggers(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    # patch msg_store send so no real row is written; count trigger fires
    sent = []
    import scripts.lineage_daemon.cron_beat as cb

    agents = [_agent("soft-1", 72), _agent("soft-2", 74)]
    # run_beat builds author_inject internally (uses MessageStore); intercept it by
    # monkeypatching MessageStore.send at import site.
    import msg_store
    monkeypatch.setattr(msg_store.MessageStore, "send",
                        lambda self, **kw: sent.append(kw["to_agent"]) or "mid")
    r = cron_beat.run_beat(agents, REG, _fresh_state(), now=1000,
                           orchestra_dir=str(tmp_path))
    assert set(sent) == {"soft-1", "soft-2"}          # both SOFT triggers fired
    assert all(b["armed_status"] == _beat.SOFT_AUTHORING for b in r["beats"])
    assert r["summary"]["rotated"] == 0


# --- kill-switch sentinel -> whole beat is a no-op -----------------------------

def test_kill_switch_makes_beat_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    # authoritative brake = the non-synced runtime path (gm msg_b8f1c614)
    rt = tmp_path / "rt-ebrake"; rt.mkdir()
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(rt))
    (rt / "FLEET_BEAT_DISABLED").write_text("brake")
    r = cron_beat.run_beat([_agent("hot-hard", 99)], REG, _fresh_state(), now=1000,
                           orchestra_dir=str(tmp_path), dry=True)
    assert r.get("disabled") is True
    assert r["beats"] == []
    assert r["summary"]["rotated"] == 0


# --- state (locks/ledger/history) round-trips atomically ----------------------

def test_state_roundtrip(tmp_path):
    p = str(tmp_path / "fleet-beat-state.json")
    state = {"locks": {"rotations": [{"canary": "x"}]},
             "ledger": {"holds": [{"canary": "y", "resolved": False}]},
             "history": {"retires": [{"lineage_root": "z", "at": 5}]}}
    cron_beat.save_state(state, path=p)
    got = cron_beat.load_state(path=p)
    assert got["locks"]["rotations"] == [{"canary": "x"}]
    assert got["history"]["retires"] == [{"lineage_root": "z", "at": 5}]


def test_load_state_missing_is_fresh(tmp_path):
    got = cron_beat.load_state(path=str(tmp_path / "nope.json"))
    assert got == {"locks": {"rotations": []}, "ledger": {"holds": []},
                   "history": {"retires": []}}


# --- soft_only is hard-coded True (2nd the operator go required to change) -------------

def test_soft_only_constant_reflects_arm_state():
    # ARMED 2026-08-26 (the operator-directed, scoped to pm-skyline via ~/runtime/
    # self_retire_armed). SOFT_ONLY=False lets a hard_rotate reach the per-lineage
    # arm gate; a non-armed lineage still defers (SKIP_NOT_ARMED). Flip to True to
    # disarm globally. ARMED_TIERS stays T2-only (T0/T1 observe).
    assert cron_beat.SOFT_ONLY is False
    assert cron_beat.ARMED_TIERS == frozenset({"T2"})


# --- notify-only: first self_triggered skip fires ONE tg, once, fail-open ------

def _skip_beat(aid="s1", ctx=0.86):
    return [{"agent_id": aid, "armed_status": _fleet.SKIP_SELF_TRIGGERED, "ctx_pct": ctx}]


def test_first_skip_notify_fires_once(tmp_path, monkeypatch):
    rt = tmp_path / "rt"; rt.mkdir()
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(rt))
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a[0]))
    # first skip -> fires + drops sentinel
    assert cron_beat._first_skip_notify(_skip_beat(), now=1000) is True
    assert len(calls) == 1
    assert "tg-notify.sh" in calls[0][0]
    assert any("self-triggered" in str(x) for x in calls[0])
    assert (rt / cron_beat.FIRST_SKIP_NOTIFY_SENTINEL).exists()
    # second skip -> once-sentinel present -> does NOT re-fire
    assert cron_beat._first_skip_notify(_skip_beat(), now=1060) is False
    assert len(calls) == 1                              # still just one send


def test_first_skip_notify_noop_when_no_skip(tmp_path, monkeypatch):
    rt = tmp_path / "rt"; rt.mkdir()
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(rt))
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a))
    # a beat with no self_triggered skip -> nothing fires
    beats = [{"agent_id": "x", "armed_status": _beat.SOFT_AUTHORING, "ctx_pct": 0.82}]
    assert cron_beat._first_skip_notify(beats, now=1000) is False
    assert calls == []


def test_first_skip_notify_dry_run_no_send_no_sentinel(tmp_path, monkeypatch):
    rt = tmp_path / "rt"; rt.mkdir()
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(rt))
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a))
    assert cron_beat._first_skip_notify(_skip_beat(), now=1000, dry=True) is True
    assert calls == []                                  # dry: no send
    assert not (rt / cron_beat.FIRST_SKIP_NOTIFY_SENTINEL).exists()  # no sentinel


def test_first_skip_notify_fail_open_on_tg_error(tmp_path, monkeypatch):
    """A tg failure must be swallowed — the notify NEVER wedges the beat."""
    rt = tmp_path / "rt"; rt.mkdir()
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(rt))

    def boom(*a, **k):
        raise OSError("tg-notify blew up")
    monkeypatch.setattr("subprocess.run", boom)
    # must NOT raise
    assert cron_beat._first_skip_notify(_skip_beat(), now=1000) is False


# --- S3 confirm seam: real + injectable, NOT the old deceptive constant --------

def test_confirm_fn_default_is_real_s3_seam_not_stub(tmp_path, monkeypatch):
    """run_beat wires the REAL s3_confirm_seam (holds honestly), not the old
    hardcoded {'outcome':'held','reason':'hard-rotation-not-enabled-first-window'}.
    Capture the confirm_fn plan_fleet receives and assert its behavior + reason."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    captured = {}

    def fake_plan_fleet(*a, **kw):
        captured["confirm_fn"] = kw.get("confirm_fn")
        return {"beats": [], "locks": kw["locks"], "ledger": kw["ledger"],
                "history": kw["history"], "summary": {"total": 0, "actionable": 0,
                "armed_run": 0, "rotated": 0, "held": 0, "skipped_soft_only": 0,
                "skipped_self_triggered": 0}, "disabled": False,
                "advisory_sentinel": False}

    monkeypatch.setattr(_fleet, "plan_fleet", fake_plan_fleet)
    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True)
    cf = captured["confirm_fn"]
    out = cf("cand-g2", "cand-g3")
    assert out["outcome"] == "held"
    # HONEST reason from the real seam — the deceptive constant is GONE.
    assert out["reason"] == "s3-live-seams-unavailable"
    assert "hard-rotation-not-enabled" not in out["reason"]


def test_confirm_fn_injectable_override(tmp_path, monkeypatch):
    """A caller-supplied confirm_fn overrides the factory verbatim."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    captured = {}

    def fake_plan_fleet(*a, **kw):
        captured["confirm_fn"] = kw.get("confirm_fn")
        return {"beats": [], "locks": kw["locks"], "ledger": kw["ledger"],
                "history": kw["history"], "summary": {"total": 0, "actionable": 0,
                "armed_run": 0, "rotated": 0, "held": 0, "skipped_soft_only": 0,
                "skipped_self_triggered": 0}, "disabled": False,
                "advisory_sentinel": False}

    monkeypatch.setattr(_fleet, "plan_fleet", fake_plan_fleet)
    sentinel = lambda c, s: {"outcome": "confirmed", "via": "injected"}
    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True, confirm_fn=sentinel)
    assert captured["confirm_fn"] is sentinel


def test_confirm_fn_seams_provider_threaded(tmp_path, monkeypatch):
    """A live s3_seams_provider is threaded into the real seam and used."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    captured = {}

    def fake_plan_fleet(*a, **kw):
        captured["confirm_fn"] = kw.get("confirm_fn")
        return {"beats": [], "locks": kw["locks"], "ledger": kw["ledger"],
                "history": kw["history"], "summary": {"total": 0, "actionable": 0,
                "armed_run": 0, "rotated": 0, "held": 0, "skipped_soft_only": 0,
                "skipped_self_triggered": 0}, "disabled": False,
                "advisory_sentinel": False}

    monkeypatch.setattr(_fleet, "plan_fleet", fake_plan_fleet)
    seen = {}

    def provider(canary, successor):
        seen["called"] = (canary, successor)
        return None                              # None -> honest hold

    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True,
                       s3_seams_provider=provider)
    out = captured["confirm_fn"]("cand-g2", "cand-g3")
    assert seen["called"] == ("cand-g2", "cand-g3")
    assert out["outcome"] == "held"


# --- T2 auto-loop trio: grade_fn + approval_fn wired real + injectable ---------

class _Proc:
    def __init__(self, code, out="", err=""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


def _capture_plan_fleet(captured):
    def fake_plan_fleet(*a, **kw):
        captured.update(kw)
        return {"beats": [], "locks": kw["locks"], "ledger": kw["ledger"],
                "history": kw["history"], "summary": {"total": 0, "actionable": 0,
                "armed_run": 0, "rotated": 0, "held": 0, "skipped_soft_only": 0,
                "skipped_self_triggered": 0}, "disabled": False,
                "advisory_sentinel": False}
    return fake_plan_fleet


def _write_sessions(tmp_path, mapping):
    sdir = tmp_path / "state"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "agent-sessions.json").write_text(json.dumps(mapping))


def test_default_grade_fn_is_real_auto_grade_strict(tmp_path, monkeypatch):
    """run_beat's default grade_fn calls auto_grade_successor STRICT, resolving the
    predecessor sid from agent-sessions.json, and maps exit0->PASS / exit1->FAIL."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    _write_sessions(tmp_path, {"cand-g2": {"session_id": "sid-pred-abc"}})
    captured = {}
    monkeypatch.setattr(_fleet, "plan_fleet", _capture_plan_fleet(captured))

    seen_argv = {}

    def fake_runner(argv):
        seen_argv["argv"] = argv
        return _Proc(0)                          # graded PASS

    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True,
                       grade_runner=fake_runner)
    gf = captured["grade_fn"]
    res = gf("cand-g2", "cand-g3")
    assert res["disposition"] == "PASS"
    assert res["strict"] is True
    argv = seen_argv["argv"]
    assert "cand-g3" in argv                      # grading the SUCCESSOR
    assert "--strict" in argv                     # unattended => strict artifact
    assert "sid-pred-abc" in argv                 # predecessor sid resolved + passed

    # exit 1 -> FAIL disposition (the HOLD_GRADE trigger inside execute_rotation).
    def fail_runner(argv):
        return _Proc(1)
    # re-derive the wrapper with a FAIL runner
    captured2 = {}
    monkeypatch.setattr(_fleet, "plan_fleet", _capture_plan_fleet(captured2))
    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True, grade_runner=fail_runner)
    assert captured2["grade_fn"]("cand-g2", "cand-g3")["disposition"] == "FAIL"


def test_grade_fn_injectable_override(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    captured = {}
    monkeypatch.setattr(_fleet, "plan_fleet", _capture_plan_fleet(captured))
    sentinel = lambda c, s: {"disposition": "PASS", "via": "injected"}
    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True, grade_fn=sentinel)
    assert captured["grade_fn"] is sentinel


def test_default_approval_fn_falls_back_to_card_when_not_graduated(tmp_path, monkeypatch):
    """Default approval_fn is graduation-gated: with the S3 seam holding (first
    window) + no arming, it NEVER auto-approves — it delegates to the fallback card.
    We stub the fallback to a sentinel so no real the operator card is posted."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    _write_sessions(tmp_path, {"cand-g2": {"session_id": "sid-pred"}})
    captured = {}
    monkeypatch.setattr(_fleet, "plan_fleet", _capture_plan_fleet(captured))

    fired = {"card": 0}

    def fake_card(canary, successor):
        fired["card"] += 1
        return "deny"

    # patch the default the operator-card gate so the fallback is hermetic
    from scripts.lineage_daemon import execute as _ex
    monkeypatch.setattr(_ex, "default_approval_gate", fake_card)

    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True)
    af = captured["approval_fn"]
    decision = af("cand-g2", "cand-g3")           # not graduated => falls to card
    assert decision == "deny"                      # the card gate answered, no auto-approve
    assert fired["card"] == 1


def test_approval_fn_injectable_override(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    captured = {}
    monkeypatch.setattr(_fleet, "plan_fleet", _capture_plan_fleet(captured))
    sentinel = lambda c, s: "approve"
    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True, approval_fn=sentinel)
    assert captured["approval_fn"] is sentinel


def test_trio_threaded_into_plan_fleet(tmp_path, monkeypatch):
    """Both grade_fn AND approval_fn reach plan_fleet (forwarded verbatim to the
    beat as hard-path seams)."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    _write_sessions(tmp_path, {})
    captured = {}
    monkeypatch.setattr(_fleet, "plan_fleet", _capture_plan_fleet(captured))
    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True)
    assert captured.get("grade_fn") is not None
    assert captured.get("approval_fn") is not None


# --- per-lineage arm gate: armed_lineages threaded run_beat -> plan_fleet ------

def test_armed_lineages_threaded_into_plan_fleet(tmp_path, monkeypatch):
    """An explicit armed_lineages set reaches plan_fleet verbatim (the injectable
    per-lineage arm-gate source)."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    captured = {}
    monkeypatch.setattr(_fleet, "plan_fleet", _capture_plan_fleet(captured))
    armed = {"lineage-x", "lineage-y"}
    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True, armed_lineages=armed)
    assert captured["armed_lineages"] == armed


def test_armed_lineages_default_none_lets_plan_fleet_read_file(tmp_path, monkeypatch):
    """Default (no armed_lineages) forwards None -> plan_fleet reads the canonical
    self-retire allowlist itself (fail-closed when absent). run_beat does NOT open
    the gate on its own."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    captured = {}
    monkeypatch.setattr(_fleet, "plan_fleet", _capture_plan_fleet(captured))
    cron_beat.run_beat([], REG, _fresh_state(), now=1000,
                       orchestra_dir=str(tmp_path), dry=True)
    assert "armed_lineages" in captured                    # explicitly forwarded
    assert captured["armed_lineages"] is None              # default => plan_fleet reads the file


def test_soft_only_first_window_arm_gate_moot(tmp_path, monkeypatch):
    """End-to-end via the REAL plan_fleet: with soft_only=True (passed explicitly so
    the test is independent of the now-armed module constant), a HARD seat defers at
    SKIP_SOFT_ONLY BEFORE the arm gate regardless of armed_lineages."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    r = cron_beat.run_beat([_agent("hot-hard", 95)], REG, _fresh_state(), now=1000,
                           orchestra_dir=str(tmp_path), dry=True, soft_only=True,
                           armed_lineages={"hot-hard"})   # armed, but soft_only wins
    assert r["beats"][0]["armed_status"] == _fleet.SKIP_SOFT_ONLY
    assert r["summary"]["rotated"] == 0


def test_soft_only_false_hard_seat_reaches_arm_gate(tmp_path, monkeypatch):
    """Armed-path coverage (the state we are now in): with soft_only=False, a HARD
    seat is NO LONGER soft-deferred — an UNARMED lineage defers at the per-lineage
    arm gate (SKIP_NOT_ARMED) instead, and an ARMED one is evaluated. Either way a
    dry beat rotates 0. This is what the live arm relies on."""
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    # unarmed lineage -> defers at the arm gate, not the soft-only gate
    r = cron_beat.run_beat([_agent("hot-hard", 95)], REG, _fresh_state(), now=1000,
                           orchestra_dir=str(tmp_path), dry=True, soft_only=False,
                           armed_lineages=set())
    assert r["beats"][0]["armed_status"] == _fleet.SKIP_NOT_ARMED
    assert r["summary"]["rotated"] == 0
    assert r["summary"]["skipped_soft_only"] == 0        # NOT soft-deferred anymore


# --- dry=True is a TRUE dry-run: NO real author-trigger rows fired -------------

def test_dry_run_fires_no_real_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    sent = []
    import msg_store
    monkeypatch.setattr(msg_store.MessageStore, "send",
                        lambda self, **kw: sent.append(kw["to_agent"]) or "mid")
    r = cron_beat.run_beat([_agent("soft-1", 75)], REG, _fresh_state(), now=1000,
                           orchestra_dir=str(tmp_path), dry=True)
    assert sent == []                                  # NO real msg_store row in dry
    # the plan still computed the soft-authoring disposition (just didn't send)
    assert r["beats"][0]["armed_status"] == _beat.SOFT_AUTHORING


# --- soft-handoff author-trigger SEND (the interim per-seat MUTE was RETIRED,
# DEC-1788165818 — over-fire fixed at the root by recognition + freshness). ------

def test_soft_authoring_seat_author_inject_sends(tmp_path, monkeypatch):
    # a soft-authoring seat's author-trigger is sent (no mute denylist gating it).
    monkeypatch.setattr(cron_beat, "ORCHESTRA_DIR", str(tmp_path))
    sent = []
    import msg_store
    monkeypatch.setattr(msg_store.MessageStore, "send",
                        lambda self, **kw: sent.append(kw["to_agent"]) or "mid")
    r = cron_beat.run_beat([_agent("soft-1", 75)], REG, _fresh_state(), now=1000,
                           orchestra_dir=str(tmp_path))
    assert sent == ["soft-1"]                           # sends normally (no mute)
    assert r["beats"][0]["armed_status"] == _beat.SOFT_AUTHORING
