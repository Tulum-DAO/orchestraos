"""live_steps: Arturo's current step for the dashboard's working line, throttled per seat, never raw args."""
import json

from services.arturo import live_steps as ls


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Timer:
    """A timer the test fires by hand."""
    made = []

    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args, self.cancelled = delay, fn, args, False
        _Timer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn(*self.args)


def _store(tmp_path, clock):
    _Timer.made = []
    return ls.LiveSteps(path=tmp_path / "steps.json", clock=clock, timer=_Timer)


def _seat(tmp_path, seat="arturo"):
    return json.loads((tmp_path / "steps.json").read_text())["seats"].get(seat)


def test_a_step_is_written_at_once_with_its_time(tmp_path):
    clock = _Clock()
    s = _store(tmp_path, clock)
    s.step("arturo", "Reading a file")
    assert _seat(tmp_path) == {"text": "Reading a file", "ts": 1000.0}


def test_at_most_one_write_per_half_second_and_the_latest_step_wins(tmp_path):
    clock = _Clock()
    s = _store(tmp_path, clock)
    s.step("arturo", "one")
    clock.t += 0.1
    s.step("arturo", "two")
    clock.t += 0.1
    s.step("arturo", "three")
    assert _seat(tmp_path)["text"] == "one"           # still inside the window
    assert len(_Timer.made) == 1 and abs(_Timer.made[0].delay - 0.4) < 1e-9
    clock.t += 0.3
    _Timer.made[0].fire()
    assert _seat(tmp_path)["text"] == "three"
    clock.t += 0.6
    s.step("arturo", "four")                          # a new window: written at once
    assert _seat(tmp_path)["text"] == "four"


def test_seats_are_throttled_separately(tmp_path):
    clock = _Clock()
    s = _store(tmp_path, clock)
    s.step("arturo", "a")
    s.step("gm", "b")
    assert _seat(tmp_path)["text"] == "a" and _seat(tmp_path, "gm")["text"] == "b"


def test_clear_ends_the_line_at_once_and_drops_a_waiting_step(tmp_path):
    clock = _Clock()
    s = _store(tmp_path, clock)
    s.step("arturo", "one")
    clock.t += 0.1
    s.step("arturo", "two")
    s.clear("arturo")
    assert _seat(tmp_path)["text"] == ""
    assert _Timer.made[0].cancelled
    _Timer.made[0].fire()
    assert _seat(tmp_path)["text"] == ""


def test_clear_with_nothing_showing_writes_nothing(tmp_path):
    s = _store(tmp_path, _Clock())
    s.clear("arturo")
    assert not (tmp_path / "steps.json").exists()


def test_the_line_is_a_phrase_never_the_tool_arguments():
    assert ls.step_text("read_file", {"path": "/home/x/.env.secrets"}) == "Reading a file"
    assert ls.step_text("run_command", {"command": "cat ~/.ssh/id_ed25519"}) == "Running a command"
    assert ls.step_text("research", {"query": "my bank password"}) == "Researching"
    assert ls.step_text("inject_message", {"agent": "dev-client-a", "text": "secret plan"}) == "Messaging dev-client-a"
    # an "agent" that is not an agent id is not shown
    assert ls.step_text("inject_message", {"agent": "rm -rf / && echo hi"}) == "Messaging an agent"
    assert ls.step_text("some_new_tool", {"x": 1}) == "Some new tool"
    assert len(ls.step_text("x" * 500)) <= ls.MAX_TEXT


def test_a_write_failure_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    s = ls.LiveSteps(path=blocker / "steps.json", clock=_Clock(), timer=_Timer)
    s.step("arturo", "x")                              # parent is a file: the write fails quietly


def test_every_tool_run_records_arturos_step_and_a_nested_one_does_not(monkeypatch):
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("arturo_proxy_steps", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seen = []
    monkeypatch.setattr(mod._live_steps, "step", lambda seat, name, args=None: seen.append((seat, name, args)))
    monkeypatch.setattr(mod, "_execute_tool_impl", lambda name, args, user_turns=None: "ok")
    assert mod.execute_tool("read_file", {"path": "/x"}) == "ok"
    assert seen == [("arturo", "read_file", {"path": "/x"})]
    tok = mod._NESTED_DISPATCH.set(True)
    try:
        mod.execute_tool("read_file", {"path": "/y"})
    finally:
        mod._NESTED_DISPATCH.reset(tok)
    assert len(seen) == 1
