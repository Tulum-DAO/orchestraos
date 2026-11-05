"""headless_adopt.py: a new Claude seat comes over to headless once idle, with its calls on; others are left alone."""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import headless_adopt as HA  # noqa: E402


class Pane:
    """A fake seat pane: a claude TUI that is busy for `busy` looks, then idle; /exit drops it to bash."""
    def __init__(self, busy=0):
        self.cmd, self.busy, self.typed = "claude", busy, []

    def __call__(self, *args):
        out = ""
        if args[0] == "display-message":
            out = self.cmd
        elif args[0] == "capture-pane":
            if self.busy > 0:
                self.busy -= 1
                out = "✻ Working (3s · esc to interrupt)\n❯ "
            else:
                out = "done\n❯ "
        elif args[0] == "send-keys":
            self.typed.append(args[-1])
            if args[-1] == "Enter" and self.typed[-2:] == ["/exit", "Enter"]:
                self.cmd = "bash"
        return types.SimpleNamespace(stdout=out, returncode=0)


def setup(monkeypatch, tmp_path, pane, runtime="claude", takeover_ok=True):
    (tmp_path / "state").mkdir()
    state = {"switched": None}
    monkeypatch.setattr(HA, "tmux", pane)
    monkeypatch.setattr(HA.time, "sleep", lambda s: None)
    clock = [1000.0]
    def now():
        clock[0] += 3
        return clock[0]
    monkeypatch.setattr(HA.time, "time", now)
    monkeypatch.setattr(HA.HS, "data_dir", lambda orch_dir=None: tmp_path)
    monkeypatch.setattr(HA.HS, "is_headless", lambda seat, **k: False)
    monkeypatch.setattr(HA.HS, "set_switch", lambda seat, on, runtime, by="": state.update(switched=(seat, on, by)))
    monkeypatch.setattr(HA.HS, "status", lambda seat: {"ok": True, "state": "idle", "session_id": "s1"})
    monkeypatch.setattr(HA.SS, "registry_row", lambda seat, orch: {"runtime": runtime, "session_id": "s1"})
    monkeypatch.setattr(HA.SS, "takeover", lambda seat: (takeover_ok, "line" if takeover_ok else "no pane"))
    return state


def calls(tmp_path):
    p = tmp_path / "state" / "agent-calls.json"
    return json.loads(p.read_text())["seats"] if p.exists() else {}


def test_waits_for_idle_then_switches_exits_takes_over_and_turns_calls_on(monkeypatch, tmp_path):
    pane = Pane(busy=3)
    st = setup(monkeypatch, tmp_path, pane)
    assert HA.adopt("new-seat", 600, "new-agent") == 0
    assert st["switched"] == ("new-seat", True, "new-agent")
    assert pane.typed[:2] == ["/exit", "Enter"] and pane.cmd == "bash"
    assert calls(tmp_path)["new-seat"] == {"on": True, "ack_after_s": 3}


def test_a_busy_seat_is_never_switched_before_the_timeout(monkeypatch, tmp_path):
    pane = Pane(busy=10**6)
    st = setup(monkeypatch, tmp_path, pane)
    assert HA.adopt("busy", 60, "new-agent") == 3
    assert st["switched"] is None and pane.typed == [] and calls(tmp_path) == {}


def test_other_runtimes_stay_on_tmux(monkeypatch, tmp_path):
    pane = Pane()
    st = setup(monkeypatch, tmp_path, pane, runtime="codex")
    assert HA.adopt("cx", 60, "new-agent") == 0
    assert st["switched"] is None and pane.typed == []


def test_a_failed_takeover_leaves_the_switch_on_and_calls_off(monkeypatch, tmp_path):
    pane = Pane()
    st = setup(monkeypatch, tmp_path, pane, takeover_ok=False)
    assert HA.adopt("s", 60, "new-agent") == 5
    assert st["switched"][1] is True and calls(tmp_path) == {}


def test_existing_options_are_kept(monkeypatch, tmp_path):
    pane = Pane()
    setup(monkeypatch, tmp_path, pane)
    (tmp_path / "state" / "agent-calls.json").write_text(json.dumps({"seats": {"s": {"ack_after_s": 5}, "o": {"on": True}}}))
    assert HA.adopt("s", 60, "new-agent") == 0
    assert calls(tmp_path) == {"s": {"on": True, "ack_after_s": 5}, "o": {"on": True}}


def test_the_install_default_off_leaves_a_new_seat_s_calls_off(monkeypatch, tmp_path):
    pane = Pane()
    setup(monkeypatch, tmp_path, pane)
    (tmp_path / "state" / "agent-calls.json").write_text(json.dumps({"default": {"on": False}, "seats": {}}))
    assert HA.adopt("new-seat", 60, "new-agent") == 0
    doc = json.loads((tmp_path / "state" / "agent-calls.json").read_text())
    assert doc["seats"]["new-seat"] == {"on": False, "ack_after_s": 3}
    assert doc["default"] == {"on": False}
