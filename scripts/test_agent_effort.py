"""Effort (how hard a model thinks), per agent: the levels each runtime has, where an agent's chosen
level is kept, how a spawn reads it, and a running Claude seat's live `/effort <level>`.

What the installed CLI does (claude 2.1.295, read from its own code; not run against a live pane):
- `/effort <level>` sets the session's effort and answers "Set effort level to <level> (saved as
  your default for new sessions): ..." (low..xhigh) or "(this session only)" (max). The save writes
  `modelSettings.<model>.effortLevel` into the user settings file every new session reads, so the
  switch snapshots and puts that key back, like `/model`'s "model" key.
- On a seat with history it first asks "Change effort level?" with "1. Yes, switch to <level>" /
  "2. No, go back", the same dialog `/model` shows.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.agent_effort as AE

ROOT = Path(__file__).resolve().parent.parent


# --- levels -------------------------------------------------------------------------------------

def test_each_runtime_has_its_own_levels_and_only_claude_and_codex_have_any():
    assert AE.LEVELS["claude"] == ("low", "medium", "high", "xhigh", "max")
    assert AE.LEVELS["codex"] == ("low", "medium", "high", "xhigh")
    assert "gemini" not in AE.LEVELS and "grok" not in AE.LEVELS
    assert AE.valid("claude", "max") and not AE.valid("codex", "max")
    assert not AE.valid("gemini", "high") and not AE.valid(None, "high") and not AE.valid("claude", "turbo")
    assert not AE.valid("claude", "High"), "ids only: the CLI takes the lowercase id"


# --- the per-agent store ------------------------------------------------------------------------

def test_an_agents_effort_is_kept_by_its_lineage_root_so_a_rotation_keeps_it(tmp_path):
    f = tmp_path / "state" / "agent-efforts.json"
    assert AE.get(f, "dev") is None
    AE.put(f, "dev", "high")
    assert AE.get(f, "dev") == "high"
    assert AE.root_of("dev-g7") == "dev" and AE.root_of("dev") == "dev"
    assert AE.root_of("dev-g7", {"agents": {"dev-g7": {"lineage_root": "dev"}}}) == "dev"
    assert AE.root_of("x-gate", {}) == "x-gate", "only a -g<number> suffix is a generation"
    assert json.loads(f.read_text()) == {"efforts": {"dev": "high"}}
    AE.put(f, "dev", None)
    assert AE.get(f, "dev") is None and json.loads(f.read_text()) == {"efforts": {}}


def test_a_damaged_store_reads_as_nothing_and_a_bad_level_is_never_written(tmp_path):
    f = tmp_path / "agent-efforts.json"
    f.write_text("{not json")
    assert AE.get(f, "dev") is None
    with pytest.raises(ValueError):
        AE.put(f, "dev", "turbo")
    f.write_text(json.dumps({"efforts": {"dev": "rm -rf"}}))
    assert AE.get(f, "dev") is None, "a stored value is checked on read too: it goes on a command line"


def _cli(tmp_path, *args, env=None):
    e = dict(os.environ, AGENT_EFFORT_FILE=str(tmp_path / "agent-efforts.json"))
    e.pop("AGENT_EFFORT", None)
    e.update(env or {})
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "agent_effort.py"), *args],
                          capture_output=True, text=True, env=e, timeout=30)


def test_spawn_resolves_the_stored_level_for_the_runtime(tmp_path):
    AE.put(tmp_path / "agent-efforts.json", "dev", "max")
    assert _cli(tmp_path, "resolve", "dev-g3", "--runtime", "claude").stdout.strip() == "max"
    # A level the runtime does not have (max on codex) is not passed: the CLI would refuse to start.
    assert _cli(tmp_path, "resolve", "dev", "--runtime", "codex").stdout.strip() == ""
    assert _cli(tmp_path, "resolve", "dev", "--runtime", "gemini").stdout.strip() == ""
    assert _cli(tmp_path, "resolve", "other", "--runtime", "claude").stdout.strip() == ""


def test_an_explicit_AGENT_EFFORT_wins_and_is_kept_for_the_agent(tmp_path):
    r = _cli(tmp_path, "resolve", "fresh", "--runtime", "codex", env={"AGENT_EFFORT": "xhigh"})
    assert r.returncode == 0 and r.stdout.strip() == "xhigh"
    assert AE.get(tmp_path / "agent-efforts.json", "fresh") == "xhigh", "its next rotation keeps it"
    r = _cli(tmp_path, "resolve", "fresh", "--runtime", "codex", env={"AGENT_EFFORT": "max"})
    assert r.stdout.strip() == "xhigh", "an override the runtime cannot take is ignored, never passed"


# --- Claude, live -------------------------------------------------------------------------------

def _stdout_line(text):
    return json.dumps({"type": "user", "timestamp": "2026-11-03T10:00:00.000Z",
                       "message": {"content": f"<local-command-stdout>{text}</local-command-stdout>"}}) + "\n"


SET_HIGH = "Set effort level to high (saved as your default for new sessions): Comprehensive implementation"


def test_the_clis_answer_is_read_from_the_transcript(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text(_stdout_line("Set model to Opus 5.5"))
    off = t.stat().st_size
    assert AE.scan_effort_result(t, off) is None
    with open(t, "a") as f:
        f.write(_stdout_line(SET_HIGH))
    assert AE.scan_effort_result(t, off) == ("set", "high")
    assert AE.scan_effort_result(t, 0) == ("set", "high"), "a /model line is not an answer to /effort"
    t.write_text(_stdout_line("Set effort level to max (this session only): Maximum capability"))
    assert AE.scan_effort_result(t, 0) == ("set", "max")
    t.write_text(_stdout_line("Effort 'max' exceeds the cap for opus set by your settings or organization; "
                              "set to 'high' instead (saved as your default for new sessions): x"))
    assert AE.scan_effort_result(t, 0) == ("set", "high")
    t.write_text(_stdout_line("Not applied: CLAUDE_CODE_EFFORT_LEVEL=low overrides effort this session"))
    assert AE.scan_effort_result(t, 0)[0] == "refused"
    t.write_text(_stdout_line("Kept effort level as medium"))
    assert AE.scan_effort_result(t, 0)[0] == "refused"


SETTINGS = {"theme": "dark", "model": "opus", "modelSettings": {"claude-opus-5-5": {"effortLevel": "medium"}}}


class FakeClaude:
    """A Claude pane: `/effort <level>` asks first when `asks` (a seat with history), then saves the
    level into the user settings (as the CLI does) and prints its line into the transcript."""

    def __init__(self, tmp_path, asks=False, busy=False, offers=None, answer=None):
        self.settings = tmp_path / "claude" / "settings.json"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps(SETTINGS, indent=2))
        self.jsonl = tmp_path / "s.jsonl"
        self.jsonl.write_text(_stdout_line("Set effort level to low (saved as your default for new sessions): x"))
        self.asks, self.busy, self.offers, self.answer = asks, busy, offers, answer
        self.screen, self.sent, self.keyed, self.level = "❯ \n", [], [], None

    def send(self, text):
        self.sent.append(text)
        if self.busy:
            return False, {"reason": "busy", "state": "working"}
        self.level = text.split()[-1]
        if self.asks:
            offered = self.offers or self.level
            self.screen = (" Change effort level?\n This conversation is cached for the current effort level.\n"
                           f" ❯ 1. Yes, switch to {offered}\n   2. No, go back\n")
        else:
            self._apply()
        return True, {}

    def _apply(self):
        data = json.loads(self.settings.read_text())
        data["modelSettings"] = {"claude-opus-5-5": {"effortLevel": self.level}}
        self.settings.write_text(json.dumps(data))
        with open(self.jsonl, "a") as f:
            f.write(_stdout_line(self.answer or f"Set effort level to {self.level} (saved as your default for new sessions): x"))
        self.screen = "❯ \n"

    def keys(self, k):
        self.keyed.append(k)
        if k == "1":
            self._apply()
        else:
            with open(self.jsonl, "a") as f:
                f.write(_stdout_line("Kept effort level as low"))
            self.screen = "❯ \n"

    def capture(self):
        return self.screen

    def run(self, effort="high", jsonl="default", timeout_s=5, settle_s=0, recheck_s=0, sleep=lambda _s: None):
        return AE.switch_effort_live(
            effort=effort, settings_path=self.settings, send=self.send, capture=self.capture, keys=self.keys,
            find_jsonl=lambda: self.jsonl if jsonl == "default" else jsonl,
            timeout_s=timeout_s, settle_s=settle_s, recheck_s=recheck_s, sleep=sleep)


def test_a_live_effort_switch_types_effort_and_puts_the_hosts_setting_back(tmp_path):
    c = FakeClaude(tmp_path)
    status, body = c.run("high")
    assert status == 200 and body == {"ok": True, "effort": "high", "switching": "switched"}
    assert c.sent == ["/effort high"]
    assert json.loads(c.settings.read_text()) == SETTINGS, "the host default for new sessions is untouched"
    assert not (c.settings.parent / AE.JOURNAL_NAME).exists()


def test_the_confirm_dialog_is_answered_yes_only_for_the_level_asked_for(tmp_path):
    c = FakeClaude(tmp_path, asks=True)
    assert c.run("xhigh")[0] == 200 and c.keyed == ["1"]
    c2 = FakeClaude(tmp_path / "b", asks=True, offers="low")
    status, body = c2.run("xhigh")
    assert status == 502 and c2.keyed == ["2"]
    assert json.loads(c2.settings.read_text()) == SETTINGS


def test_a_busy_seat_is_409_and_nothing_is_typed_or_changed(tmp_path):
    c = FakeClaude(tmp_path, busy=True)
    status, body = c.run("high")
    assert status == 409 and body["error"] == "busy"
    assert json.loads(c.settings.read_text()) == SETTINGS


def test_no_transcript_means_nothing_is_typed(tmp_path):
    c = FakeClaude(tmp_path)
    status, body = c.run("high", jsonl=None)
    assert status == 502 and body["error"] == "unconfirmable" and c.sent == []


def test_a_refusal_or_another_level_is_never_a_success(tmp_path):
    c = FakeClaude(tmp_path, answer="Not applied: CLAUDE_CODE_EFFORT_LEVEL=low overrides effort this session")
    status, body = c.run("high")
    assert status == 502 and body["error"] == "refused"
    c2 = FakeClaude(tmp_path / "b", answer="Effort 'max' exceeds the cap; set to 'high' instead: x")
    status, body = c2.run("max")
    assert status == 502 and body["error"] == "a different effort was set" and body["detail"] == "high"
    assert json.loads(c2.settings.read_text()) == SETTINGS


def test_a_level_outside_the_table_is_never_typed(tmp_path):
    c = FakeClaude(tmp_path)
    assert c.run("high; rm -rf /")[0] == 400 and c.sent == []


def test_a_switch_that_died_mid_way_is_restored_by_the_next_one(tmp_path):
    c = FakeClaude(tmp_path)
    before = c.settings.read_text()
    AE._write_journal(c.settings, AE.read_keys(c.settings))
    data = json.loads(before)
    data["modelSettings"] = {"claude-opus-5-5": {"effortLevel": "low"}}
    c.settings.write_text(json.dumps(data))                       # a save that was never put back
    assert c.run("high")[0] == 200
    assert json.loads(c.settings.read_text()) == SETTINGS


def test_an_absent_key_stays_absent(tmp_path):
    c = FakeClaude(tmp_path)
    c.settings.write_text(json.dumps({"theme": "dark"}))
    assert c.run("high")[0] == 200
    assert json.loads(c.settings.read_text()) == {"theme": "dark"}


def test_a_recovered_claude_seat_resumes_on_its_effort_too():
    """Static guard: both Claude resume lines in agent-recovery.sh carry --effort from resume_effort
    (agent_effort.py resolve), as spawn-agent.sh does."""
    src = (ROOT / "scripts" / "agent-recovery.sh").read_text()
    assert 'agent_effort.py" resolve "$1" --runtime claude' in src
    assert src.count('RESUME_EFFORT=$(resume_effort "$agent_id")') == 2
    assert src.count('MODEL_FLAG="$MODEL_FLAG --effort $RESUME_EFFORT"') == 2



# --- review: timing, the late save, a dialog left open, a journal from either switch -------------

def test_a_late_save_after_the_restore_is_caught_by_the_recheck(tmp_path):
    c = FakeClaude(tmp_path)
    late = []

    def sleep(s):
        if s == 0.02 and not late:                 # the recheck wait: the CLI's own save lands now
            late.append(s)
            data = json.loads(c.settings.read_text())
            data["modelSettings"] = {"claude-opus-5-5": {"effortLevel": "high"}}
            c.settings.write_text(json.dumps(data))
    assert c.run("high", settle_s=0.01, recheck_s=0.02, sleep=sleep)[0] == 200
    assert late and json.loads(c.settings.read_text()) == SETTINGS


def test_max_is_never_saved_so_its_switch_skips_the_settle_and_the_recheck(tmp_path):
    c = FakeClaude(tmp_path)
    slept = []
    assert c.run("max", settle_s=3, recheck_s=5, sleep=slept.append)[0] == 200
    assert 3 not in slept and 5 not in slept
    c2 = FakeClaude(tmp_path / "b")
    slept = []
    assert c2.run("high", settle_s=3, recheck_s=5, sleep=slept.append)[0] == 200
    assert 3 in slept and 5 in slept


def test_a_dialog_that_opens_after_the_wait_is_answered_no_on_the_way_out(tmp_path):
    c = FakeClaude(tmp_path, asks=True)
    shows = {"n": 0}
    real = c.capture

    def capture():
        shows["n"] += 1
        # Not up on the look before the send, nor on the wait's only look: it opens just after.
        return "❯ \n" if shows["n"] <= 2 else real()
    c.capture = capture
    status, body = c.run("high", timeout_s=0)
    assert status == 502 and body["error"] == "unconfirmed"
    assert c.keyed == ["2"], "never left asking: No, go back"
    assert json.loads(c.settings.read_text()) == SETTINGS


def _effort_journal_left(settings):
    AE._write_journal(settings, AE.read_keys(settings))
    data = json.loads(settings.read_text())
    data["modelSettings"] = {"claude-opus-5-5": {"effortLevel": "low"}}
    settings.write_text(json.dumps(data))


def test_a_model_switch_also_puts_back_an_effort_switch_that_died(tmp_path):
    import scripts.agent_model as AM
    c = FakeClaude(tmp_path)
    _effort_journal_left(c.settings)
    status, _ = AM.switch_live(model="claude-sonnet-5-5", settings_path=c.settings, send=c.send,
                               find_jsonl=lambda: None, sleep=lambda _s: None)
    assert status == 502 and c.sent == []
    assert json.loads(c.settings.read_text()) == SETTINGS
    assert not (c.settings.parent / AE.JOURNAL_NAME).exists()



def test_forget_takes_out_the_lineage_roots_level_under_the_writers_lock(tmp_path):
    f = tmp_path / "agent-efforts.json"
    AE.put(f, "dev", "high")
    AE.put(f, "other", "low")
    r = _cli(tmp_path, "forget", "dev-g4")
    assert r.returncode == 0, r.stderr
    assert json.loads(f.read_text()) == {"efforts": {"other": "low"}}
    assert (tmp_path / "agent-efforts.json.lock").exists()
