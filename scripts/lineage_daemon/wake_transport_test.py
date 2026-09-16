"""wake_transport tests — hermetic, pure over injected seams (no tmux/gateway/DB).

Transport side of gm wake-on-delivery (DEC-1786828407). Consumes PB's frozen
wake.py contract (should_wake/build_wake_digest/confirm_turn_started) — the
REAL functions, never mocked; the seams faked here are IO only (inject_fn,
artifact_fn, filesystem via tmp_path).
"""
import json
import os

import pytest

from scripts.focus_registry import wake
from scripts.lineage_daemon import wake_transport as wt


# --- fixtures -----------------------------------------------------------------

def _msg(i, priority="high", **kw):
    m = {"id": f"msg_{i}", "from_agent": f"agent-{i}", "subject": f"done {i}",
         "priority": priority, "acknowledged_at": None, "archived_at": None}
    m.update(kw)
    return m


IDLE_GM = {"status": "idle", "last_event_type": "turn_ended"}
# raw ANSI composer: prompt with no typed text -> verdict 'ok'
OK_COMPOSER = ["\u276f "]
COLD = {"last_wake_ts": None, "woken_ids": []}


class FakeInject:
    """Records inject calls; scripted (ok, info) results."""
    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])

    def __call__(self, text, submit):
        self.calls.append({"text": text, "submit": submit})
        if self.results:
            return self.results.pop(0)
        return (True, {})


# --- cooldown persistence -----------------------------------------------------

class TestCooldownPersistence:
    def test_missing_file_is_cold_start_dict(self, tmp_path):
        got = wt.load_cooldown(str(tmp_path / "nope.json"))
        assert got == {"last_wake_ts": None, "woken_ids": []}

    def test_corrupt_file_is_none_fail_closed(self, tmp_path):
        p = tmp_path / "cool.json"
        p.write_text("{not json")
        # None (non-dict) -> should_wake fail-closes with bad_cooldown_state:
        # an unreadable cooldown could mask a just-fired wake (storm risk).
        assert wt.load_cooldown(str(p)) is None

    def test_save_load_roundtrip_atomic(self, tmp_path):
        p = str(tmp_path / "cool.json")
        state = {"last_wake_ts": 1000.0, "woken_ids": ["msg_1", "msg_2"]}
        wt.save_cooldown(p, state)
        assert wt.load_cooldown(p) == state
        assert not os.path.exists(p + ".tmp")   # atomic write-temp+rename

    def test_woken_ids_bounded(self, tmp_path):
        p = str(tmp_path / "cool.json")
        state = {"last_wake_ts": 1.0,
                 "woken_ids": [f"m{i}" for i in range(wt.WOKEN_IDS_CAP + 50)]}
        wt.save_cooldown(p, state)
        got = wt.load_cooldown(p)
        assert len(got["woken_ids"]) == wt.WOKEN_IDS_CAP
        assert got["woken_ids"][-1] == f"m{wt.WOKEN_IDS_CAP + 49}"   # keeps newest

    def test_corrupt_cooldown_blocks_wake_end_to_end(self):
        # integration with the REAL contract: non-dict cooldown -> no wake, no inject
        inj = FakeInject()
        res = wt.wake_step([_msg(1)], IDLE_GM, OK_COMPOSER, None,
                           now=1000.0, mode="dry", inject_fn=inj)
        assert res["wake"] is False
        assert res["reason"] == "bad_cooldown_state"
        assert inj.calls == []


# --- wake_step: dry mode ------------------------------------------------------

class TestWakeStepDry:
    def test_no_wake_is_passthrough(self):
        inj = FakeInject()
        res = wt.wake_step([], IDLE_GM, OK_COMPOSER, dict(COLD),
                           now=1000.0, mode="dry", inject_fn=inj)
        assert res["wake"] is False and res["reason"] == "no_actionable"
        assert inj.calls == []
        assert res["cooldown"] == COLD          # unchanged

    def test_dry_injects_digest_without_submit(self):
        inj = FakeInject()
        res = wt.wake_step([_msg(1), _msg(2, priority="critical")], IDLE_GM,
                           OK_COMPOSER, dict(COLD), now=1000.0, mode="dry",
                           inject_fn=inj)
        assert res["wake"] is True
        assert len(inj.calls) == 1
        assert inj.calls[0]["submit"] is False          # NEVER Enter in dry
        assert "[wake]" in inj.calls[0]["text"]
        assert "agent-2" in inj.calls[0]["text"]        # critical item present
        assert res["would_submit"] is True              # the 'would submit' log fact
        assert res["submitted"] is False

    def test_dry_marks_woken_and_stamps_cooldown_on_inject_ok(self):
        inj = FakeInject()
        res = wt.wake_step([_msg(1)], IDLE_GM, OK_COMPOSER, dict(COLD),
                           now=1000.0, mode="dry", inject_fn=inj)
        assert res["cooldown"]["last_wake_ts"] == 1000.0
        assert "msg_1" in res["cooldown"]["woken_ids"]

    def test_dry_inject_refused_leaves_items_unwoken(self):
        # gateway-style refusal (busy) -> items NOT marked; next beat retries
        inj = FakeInject(results=[(False, {"reason": "busy"})])
        res = wt.wake_step([_msg(1)], IDLE_GM, OK_COMPOSER, dict(COLD),
                           now=1000.0, mode="dry", inject_fn=inj)
        assert res["wake"] is True and res["injected"] is False
        assert res["cooldown"]["woken_ids"] == []
        assert res["cooldown"]["last_wake_ts"] is None

    def test_woken_ids_suppress_rewake_next_beat(self):
        inj = FakeInject()
        first = wt.wake_step([_msg(1)], IDLE_GM, OK_COMPOSER, dict(COLD),
                             now=1000.0, mode="dry", inject_fn=inj)
        inj2 = FakeInject()
        second = wt.wake_step([_msg(1)], IDLE_GM, OK_COMPOSER,
                              first["cooldown"], now=2000.0, mode="dry",
                              inject_fn=inj2)
        assert second["wake"] is False
        assert second["reason"] == "no_actionable"      # already woken-on
        assert inj2.calls == []


# --- wake_step: armed mode ----------------------------------------------------

class TestWakeStepArmed:
    def test_armed_submits_and_confirms_first_try(self):
        inj = FakeInject()
        before = {"status": "idle", "last_event_type": "turn_ended"}
        after = {"status": "working", "last_event_type": "turn_ended"}
        res = wt.wake_step([_msg(1)], IDLE_GM, OK_COMPOSER, dict(COLD),
                           now=1000.0, mode="armed", inject_fn=inj,
                           gm_state_fn=lambda: after, artifact_fn=lambda r: None)
        assert inj.calls[0]["submit"] is True
        assert res["submitted"] is True
        assert res["confirmed"] is True
        assert res["wake_failed"] is False
        assert "msg_1" in res["cooldown"]["woken_ids"]

    def test_armed_retries_once_then_confirms(self):
        inj = FakeInject()
        states = [dict(IDLE_GM), {"status": "working", "last_event_type": "turn_ended"}]
        res = wt.wake_step([_msg(1)], IDLE_GM, OK_COMPOSER, dict(COLD),
                           now=1000.0, mode="armed", inject_fn=inj,
                           gm_state_fn=lambda: states.pop(0),
                           artifact_fn=lambda r: None)
        assert len(inj.calls) == 2                       # retry ONCE
        assert res["confirmed"] is True
        assert res["wake_failed"] is False

    def test_armed_wake_failed_durable_artifact_no_spin(self):
        inj = FakeInject()
        artifacts = []
        res = wt.wake_step([_msg(1)], IDLE_GM, OK_COMPOSER, dict(COLD),
                           now=1000.0, mode="armed", inject_fn=inj,
                           gm_state_fn=lambda: dict(IDLE_GM),   # never starts a turn
                           artifact_fn=artifacts.append)
        assert len(inj.calls) == 2                       # exactly one retry
        assert res["confirmed"] is False
        assert res["wake_failed"] is True
        assert len(artifacts) == 1                       # ONE durable artifact
        # items still marked woken -> the next beat cannot spin on the same batch
        assert "msg_1" in res["cooldown"]["woken_ids"]

    def test_armed_without_confirm_seams_fails_open_no_wake(self):
        # armed mode REQUIRES gm_state_fn + artifact_fn; missing -> refuse to inject
        inj = FakeInject()
        res = wt.wake_step([_msg(1)], IDLE_GM, OK_COMPOSER, dict(COLD),
                           now=1000.0, mode="armed", inject_fn=inj)
        assert res["wake"] is False
        assert inj.calls == []


# --- fail-open ----------------------------------------------------------------

class TestFailOpen:
    def test_inject_exception_never_raises(self):
        def boom(text, submit):
            raise RuntimeError("gateway down")
        res = wt.wake_step([_msg(1)], IDLE_GM, OK_COMPOSER, dict(COLD),
                           now=1000.0, mode="dry", inject_fn=boom)
        assert res["injected"] is False
        assert res["cooldown"]["woken_ids"] == []        # not marked; retry later

    def test_typed_composer_aborts_before_inject(self):
        inj = FakeInject()
        res = wt.wake_step([_msg(1)], IDLE_GM, ["\u276f fix the bug now"],
                           dict(COLD), now=1000.0, mode="dry", inject_fn=inj)
        assert res["wake"] is False
        assert res["reason"] == "human_composing"
        assert inj.calls == []


# --- gm_state gathering (pure parts) ------------------------------------------

class TestGmState:
    def test_last_gm_event_type_from_stream(self):
        events = [
            {"event_id": "e1", "pane": "gm", "type": "prompt_submit"},
            {"event_id": "e2", "pane": "other", "type": "turn_ended"},
            {"event_id": "e3", "pane": "gm", "type": "turn_ended"},
        ]
        resolve = lambda pane, sid, cwd: "gm" if pane == "gm" else "other"
        assert wt.last_gm_event_type(events, resolve_fn=resolve) == "turn_ended"

    def test_last_gm_event_type_none_when_absent(self):
        assert wt.last_gm_event_type([], resolve_fn=lambda *a: None) is None

    def test_build_gm_state_maps_agent_status_state(self):
        st = wt.build_gm_state({"state": "idle"}, last_event_type="turn_ended")
        assert st == {"status": "idle", "last_event_type": "turn_ended"}


# --- composer capture slicing -------------------------------------------------

class TestComposerLines:
    PANE = "\n".join([
        "some agent output",
        "─" * 24,
        "\u276f\xa0type here",
        "─" * 24,
        "  status bar 42% something",
    ])

    def test_slices_composer_row_to_box_bottom(self):
        lines = wt.composer_lines(self.PANE)
        assert lines is not None
        assert "\u276f" in lines[0]
        # never includes the status bar (would false-'typed' every wake)
        assert not any("status bar" in l for l in lines)

    def test_no_chrome_is_none_unreadable(self):
        assert wt.composer_lines("just output\nno prompt anywhere") is None
        assert wt.composer_lines("") is None


# --- run_wake orchestration + e-brake -----------------------------------------

class TestRunWake:
    def _seams(self, tmp_path, inject=None):
        return dict(
            cooldown_path=str(tmp_path / "wake-cooldown.json"),
            inbox_fn=lambda: [_msg(1)],
            gm_state_fn=lambda: dict(IDLE_GM),
            composer_fn=lambda: list(OK_COMPOSER),
            inject_fn=inject or FakeInject(),
            artifact_fn=lambda r: None,
        )

    def test_ebrake_sentinel_is_instant_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path))
        (tmp_path / wt.WAKE_DISABLED_BASENAME).write_text("stop")
        inj = FakeInject()
        res = wt.run_wake("dry", now=1000.0, **self._seams(tmp_path, inject=inj))
        assert res["disabled"] is True
        assert inj.calls == []
        assert not os.path.exists(str(tmp_path / "wake-cooldown.json"))

    def test_dry_wake_persists_cooldown(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path / "rt"))
        seams = self._seams(tmp_path)
        res = wt.run_wake("dry", now=1000.0, **seams)
        assert res["disabled"] is False and res["wake"] is True
        persisted = wt.load_cooldown(seams["cooldown_path"])
        assert "msg_1" in persisted["woken_ids"]
        assert persisted["last_wake_ts"] == 1000.0

    def test_corrupt_cooldown_no_wake_and_never_clobbered(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path / "rt"))
        seams = self._seams(tmp_path)
        with open(seams["cooldown_path"], "w") as fh:
            fh.write("{corrupt")
        inj = seams["inject_fn"]
        res = wt.run_wake("dry", now=1000.0, **seams)
        assert res["wake"] is False and res["reason"] == "bad_cooldown_state"
        assert inj.calls == []
        # the corrupt file is preserved for a human, never silently overwritten
        assert open(seams["cooldown_path"]).read() == "{corrupt"

    def test_gather_exception_fails_open(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path / "rt"))
        seams = self._seams(tmp_path)
        seams["inbox_fn"] = lambda: (_ for _ in ()).throw(RuntimeError("db locked"))
        res = wt.run_wake("dry", now=1000.0, **seams)
        assert res["wake"] is False
        assert res["disabled"] is False


class TestWakeModeFlag:
    def test_default_is_none_inert(self):
        from scripts.lineage_daemon import bus_beat
        assert bus_beat.wake_mode([]) is None
        assert bus_beat.wake_mode(["--dry-run"]) is None

    def test_flags_select_mode(self):
        from scripts.lineage_daemon import bus_beat
        assert bus_beat.wake_mode(["--wake-dry"]) == "dry"
        assert bus_beat.wake_mode(["--wake-armed"]) == "armed"
