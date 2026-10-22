"""Codex live model switch (scripts/agent_model_codex.py).

The stand-in pane draws the two pickers exactly as codex 0.153.4 drew them in the fenced probe
(2026-10-22), and on the effort digit does what codex did: rewrites `model` and
`model_reasoning_effort` in config.toml and logs thread_settings_applied in the rollout.
Every file is under tmp_path.
"""
import json

import pytest

import scripts.agent_model as AM
import scripts.agent_model_codex as C
import scripts.watch_gateway as G
from scripts.test_agent_model import _Req, _body, gw  # noqa: F401  (fixture)
from scripts.test_gateway_scopes import _run

MODELS_SCREEN = """\
› Reply with exactly: ok two
• ok two
  Select Model and Effort
  Access legacy models by running codex -m <model_name> or in your config.toml
  1. gpt-6-astra (default)   Frontier intelligence for the most demanding work.
  2. gpt-5.6-sol             Older generation workhorse model.
  3. gpt-5.6-terra           Older balanced model for straightforward work.
› 4. gpt-5.6-luna (current)  Older fast and efficient model.
  Press enter to confirm or esc to go back
"""

EFFORT_SCREEN = """\
• ok two
  Select Reasoning Level for {model}
  1. Low               Fast responses with lighter reasoning
› 2. Medium (default)  Balances speed and reasoning depth for everyday tasks
  3. High              Greater reasoning depth for complex problems
  4. Extra high        Extra high reasoning depth for complex problems
  5. More reasoning…   Max and Ultra consume usage limits faster
  Press enter to confirm or esc to go back
"""

CONFIG = '''model = "gpt-5.6-terra"
model_context_window = 872000
model_reasoning_effort = "medium"

[projects."/home/someone/repo"]
trust_level = "trusted"
model = "not-top-level"
'''


class FakeCodex:
    def __init__(self, tmp_path, picker=MODELS_SCREEN, busy=False, applies=None, opens=True):
        self.config = tmp_path / "codex" / "config.toml"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(CONFIG)
        self.rollout = tmp_path / "rollout.jsonl"
        self.rollout.write_text(json.dumps({"type": "turn_context", "payload": {"model": "gpt-5.6-luna", "effort": "low"}}) + "\n"
                                + json.dumps({"type": "event_msg", "payload": {"type": "thread_settings_applied",
                                              "thread_settings": {"model": "gpt-5.6-terra"}}}) + "\n")
        self.picker, self.busy, self.applies, self.opens = picker, busy, applies, opens
        self.screen, self.sent, self.keyed, self.chosen = "› Ask Codex to do anything\n", [], [], None
        self.lock = tmp_path / "claude" / "settings.json"

    def send(self, text):
        self.sent.append(text)
        if self.busy:
            return False, {"reason": "busy", "state": "working"}
        if self.opens:
            self.screen = self.picker
        return True, {}

    def keys(self, k):
        self.keyed.append(k)
        if k == "Escape":
            self.screen = "› Ask Codex to do anything\n"
            return
        if "Select Model and Effort" in self.screen:
            row = {m.group(1): m.group(2) for m in map(C._ROW.match, self.screen.splitlines()) if m}
            self.chosen = row[k]
            self.screen = EFFORT_SCREEN.format(model=self.chosen)
        elif "Select Reasoning Level" in self.screen:
            effort = {"1": "low", "2": "medium", "3": "high", "4": "xhigh"}[k]
            text = self.config.read_text()
            text = text.replace('model = "gpt-5.6-terra"', f'model = "{self.chosen}"', 1)
            text = text.replace('model_reasoning_effort = "medium"', f'model_reasoning_effort = "{effort}"', 1)
            self.config.write_text(text)
            with open(self.rollout, "a") as f:
                f.write(json.dumps({"type": "event_msg", "payload": {"type": "thread_settings_applied",
                        "thread_settings": {"model": self.applies or self.chosen}}}) + "\n")
            self.screen = f"• Model changed to {self.chosen} {effort}\n› Ask Codex to do anything\n"

    def capture(self):
        return self.screen

    def run(self, model="gpt-5.6-terra", rollout="default"):
        return C.switch_live(model=model, config_path=self.config, lock_path=self.lock, send=self.send,
                             keys=self.keys, capture=self.capture,
                             rollout=self.rollout if rollout == "default" else rollout,
                             timeout_s=0, screen_timeout_s=0, sleep=lambda _s: None)


def test_the_picker_is_driven_by_the_rows_on_screen_and_the_default_comes_back(tmp_path):
    c = FakeCodex(tmp_path)
    status, body = c.run("gpt-5.6-terra")
    assert status == 200 and body == {"ok": True, "model": "gpt-5.6-terra", "switching": "switched", "effort": "low"}
    assert c.sent == ["/model"]                 # never "/model <id>": codex sends that to the model
    assert c.keyed == ["3", "1"]                 # terra's row, then the agent's current effort (low)
    assert c.config.read_text() == CONFIG


def test_the_row_number_is_read_not_assumed(tmp_path):
    c = FakeCodex(tmp_path, picker=MODELS_SCREEN.replace("3. gpt-5.6-terra", "9. gpt-5.6-terra"))
    status, _ = c.run("gpt-5.6-terra")
    assert status == 200 and c.keyed[0] == "9"


def test_a_model_the_picker_does_not_list_is_refused_and_the_picker_closed(tmp_path):
    c = FakeCodex(tmp_path)
    status, body = c.run("gpt-5.6-nova")
    assert status == 422 and body["error"] == "not in this agent's model picker"
    assert "gpt-5.6-terra" in body["offered"]
    assert c.keyed == ["Escape"] and c.config.read_text() == CONFIG     # one: a second primes "edit previous"


def test_a_picker_that_never_opens_types_no_digit(tmp_path):
    c = FakeCodex(tmp_path, opens=False)
    status, body = c.run()
    assert status == 502 and body["error"] == "the model picker did not open"
    assert [k for k in c.keyed if k != "Escape"] == []


def test_only_a_thread_settings_line_after_the_send_confirms(tmp_path):
    """The rollout already holds a thread_settings_applied for terra (the old one): it must
    not count. Here codex logs another model, so the switch is not confirmed as terra."""
    c = FakeCodex(tmp_path, applies="gpt-5.6-sol")
    status, body = c.run("gpt-5.6-terra")
    assert status == 502 and body == {"ok": False, "error": "a different model was confirmed", "detail": "gpt-5.6-sol"}
    assert c.config.read_text() == CONFIG


def test_no_rollout_means_nothing_is_typed(tmp_path):
    c = FakeCodex(tmp_path)
    status, body = c.run(rollout=None)
    assert status == 502 and body["error"] == "unconfirmable" and c.sent == [] and c.keyed == []


def test_a_busy_codex_is_409(tmp_path):
    c = FakeCodex(tmp_path, busy=True)
    status, body = c.run()
    assert status == 409 and c.keyed == [] and c.config.read_text() == CONFIG


@pytest.mark.parametrize("current,digit", [("medium", "2"), ("high", "3"), ("xhigh", "4"), (None, "2")])
def test_effort_follows_the_agent_or_the_default_row(current, digit):
    rows = C.effort_rows(EFFORT_SCREEN.format(model="gpt-5.6-terra"), "gpt-5.6-terra")
    assert C.pick_effort(rows, current)[0] == digit


def test_an_effort_picker_for_another_model_is_not_ours():
    assert C.effort_rows(EFFORT_SCREEN.format(model="gpt-5.6-sol"), "gpt-5.6-terra") is None


def test_only_the_two_top_level_keys_are_put_back(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(CONFIG)
    before = C.read_keys(p)
    assert before == {"model": 'model = "gpt-5.6-terra"\n', "model_reasoning_effort": 'model_reasoning_effort = "medium"\n'}
    later = CONFIG.replace('model = "gpt-5.6-terra"', 'model = "gpt-5.6-luna"', 1).replace('"medium"', '"low"', 1)
    later += '\n[projects."/tmp/new"]\ntrust_level = "trusted"\n'          # someone else's new line stays
    p.write_text(later)
    assert C.write_keys(p, before)
    assert p.read_text() == CONFIG + '\n[projects."/tmp/new"]\ntrust_level = "trusted"\n'
    assert 'model = "not-top-level"' in p.read_text()


def test_a_key_that_was_absent_goes_away_and_one_that_vanished_comes_back(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('model = "x"\n[t]\na = 1\n')
    assert C.write_keys(p, {"model_reasoning_effort": 'model_reasoning_effort = "low"\n'})
    assert p.read_text() == 'model_reasoning_effort = "low"\n[t]\na = 1\n'


def test_a_switch_that_died_mid_way_is_restored_by_the_next_one(tmp_path):
    c = FakeCodex(tmp_path)
    before = C.read_keys(c.config)
    c.config.write_text(CONFIG.replace('"gpt-5.6-terra"', '"gpt-6-astra"', 1))
    (c.config.parent / C.JOURNAL_NAME).write_text(json.dumps(before))
    status, _ = c.run("gpt-5.6-sol")
    assert status == 200 and c.config.read_text() == CONFIG
    assert not (c.config.parent / C.JOURNAL_NAME).exists()


def test_the_real_picker_rows_parse():
    assert C.model_rows(MODELS_SCREEN) == {"gpt-6-astra": "1", "gpt-5.6-sol": "2", "gpt-5.6-terra": "3", "gpt-5.6-luna": "4"}
    assert C.model_rows("› Ask Codex to do anything") is None


def test_the_gateway_drives_a_codex_seat_through_the_picker_path(gw, monkeypatch):  # noqa: F811
    monkeypatch.setattr(G, "_seat_for_session", lambda s: ("cx", {}, "codex", "gpt-5.6-luna"))
    monkeypatch.setattr(AM, "load_catalog", lambda rt, p, st: [{"id": "gpt-5.6-luna", "title": "L"},
                                                              {"id": "gpt-5.6-terra", "title": "T"}])
    monkeypatch.setattr(G, "_seat_codex_rollout", lambda s, m=None: None)
    called = []
    monkeypatch.setattr(C, "switch_live", lambda **kw: called.append(kw["model"]) or
                        (200, {"ok": True, "model": kw["model"], "switching": "switched", "effort": "low"}))
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": "gpt-5.6-terra"}, actor="app:ios")))
    assert r.status == 200 and _body(r)["switching"] == "switched" and called == ["gpt-5.6-terra"]
    assert gw["live"] == [] and gw["stored"] == [("cx", {"model": "gpt-5.6-terra"})]


def test_an_open_effort_picker_gets_one_escape_per_menu(tmp_path, monkeypatch):
    """Failing on the effort list leaves two menus open: Escape goes back to the model list,
    a second Escape closes it, and no third one lands on the composer."""
    c = FakeCodex(tmp_path)
    real = c.keys

    def keys(k):
        if k == "Escape" and "Select Reasoning Level" in c.screen:
            c.keyed.append(k)
            c.screen = MODELS_SCREEN                 # back one menu
            return
        real(k)
    c.keys = keys

    def no_row(_rows, _current):
        raise LookupError("no row")
    monkeypatch.setattr(C, "pick_effort", no_row)
    status, body = c.run("gpt-5.6-terra")
    assert status == 502 and body["error"] == "switch failed"
    assert c.keyed == ["3", "Escape", "Escape"] and "Select" not in c.screen
    assert c.config.read_text() == CONFIG
