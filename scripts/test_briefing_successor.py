"""Fresh successors: a dead agent comes back as its next generation at once, with one line on what it was last
doing in its first prompt; the full briefing builds and arrives in the background; the successor's hold hook
holds replies until the briefing has loaded, and after a timeout answers saying the context is missing."""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import briefing as br  # noqa: E402
from scripts import fresh_successor as fs  # noqa: E402
from scripts.identity_store import orchestra_db as odb  # noqa: E402

sys.path.insert(0, str(ROOT / "hooks"))
import briefing_hold as hold  # noqa: E402

AG = "zz-dev-demo"
SID = "11111111-2222-3333-4444-555555555555"


def _turn(kind, ts, text, meta=False):
    content = text if kind == "user" else [{"type": "text", "text": text}, {"type": "tool_use", "name": "Bash"}]
    d = {"type": kind, "timestamp": ts, "message": {"role": kind, "content": content}}
    if meta:
        d["isMeta"] = True
    return json.dumps(d)


@pytest.fixture
def install(tmp_path, monkeypatch):
    data, home = tmp_path / "data", tmp_path / "home"
    (data / "state").mkdir(parents=True)
    cwd = "/work/demo.project"
    (data / "registry.json").write_text(json.dumps({"agents": {AG: {"cwd": cwd, "runtime": "claude", "session_id": SID,
                                                                    "system_prompt": "prompts/zz-dev-demo.md", "tmux_session": AG}}}))
    (data / "state" / "agent-sessions.json").write_text(json.dumps({AG: {"tmux_session": AG, "session_id": SID}}))
    proj = home / ".claude" / "projects" / "-work-demo-project"
    proj.mkdir(parents=True)
    (proj / f"{SID}.jsonl").write_text("\n".join([
        _turn("user", "2026-10-20T10:00:00Z", "Please fix the login form validation"),
        _turn("assistant", "2026-10-20T10:01:00Z", "Looking at the form now."),
        _turn("user", "2026-10-20T10:05:00Z", "tool result", meta=True),
        json.dumps({"type": "user", "timestamp": "2026-10-20T10:06:00Z", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}),
        _turn("user", "2026-10-20T10:10:00Z", "Also add a test for the empty email case"),
        _turn("assistant", "2026-10-20T10:12:00Z", "Added the empty-email test; the validation fix is pushed to the branch, review pending."),
        _turn("user", "2026-10-20T11:00:00Z", "This session is being continued from a previous conversation that ran out of context."),
        json.dumps({"type": "last-prompt", "sessionId": SID}),
    ]) + "\n")
    db = sqlite3.connect(str(data / "state" / "tasks.db"))
    db.execute("CREATE TABLE messages (from_agent TEXT, to_agent TEXT, subject TEXT, created_at TEXT)")
    db.execute("INSERT INTO messages VALUES ('pm-demo', ?, 'review the validation fix', '2026-10-20T10:20:00+00:00')", (AG,))
    db.commit(); db.close()
    (data / "memory" / AG).mkdir(parents=True)
    (data / "memory" / AG / "MEMORY.md").write_text("# Memory\n- the form lives in src/login.tsx\n")
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    return data, home


def test_last_line_reads_the_end_of_the_last_session_with_no_model(install):
    data, home = install
    line = br.last_line(AG, data, home)
    assert line.startswith("When your predecessor last worked (Oct 20, 10:12 UTC), it was handling: \"Also add a test")
    assert "validation fix is pushed" in line and "continued from a previous" not in line


def test_last_line_falls_back_to_its_last_message_then_its_task(install):
    data, home = install
    assert "review the validation fix" in br.last_line(AG, data, tmp_home := home / "nowhere")
    (data / "state" / "tasks.db").unlink()
    (data / "state" / f"{AG}.json").write_text(json.dumps({"task": "build the login page"}))
    assert br.last_line(AG, data, tmp_home) == 'Your predecessor was started for: "build the login page"'


def test_build_has_the_digest_exchanges_messages_and_memory(install):
    data, home = install
    seen = {}
    def runner(cmd, excerpt):
        seen["cmd"], seen["excerpt"] = cmd, excerpt
        return "- fixed login validation\n- empty-email test added, review pending"
    md = br.build(AG, data, home, runner=runner).read_text()
    assert "## Summary\n- fixed login validation" in md
    assert "## Its last exchanges" in md and "## Recent messages" in md and "src/login.tsx" in md
    assert "--setting-sources" in seen["cmd"] and "" in seen["cmd"] and "--tools" in seen["cmd"]   # no hooks, no tools
    assert "Please fix the login form" in seen["excerpt"]
    assert br.is_fresh(AG, data, home)


def test_build_without_a_model_still_writes_a_briefing(install):
    data, home = install
    md = br.build(AG, data, home, runner=lambda c, e: None).read_text()
    assert "## Summary" not in md and "## Its last exchanges" in md
    assert json.loads((data / "state" / "briefings" / f"{AG}.json").read_text())["digest"] is False


def test_precompute_picks_recently_stopped_agents_without_a_fresh_briefing_and_needs_its_flag(install, monkeypatch):
    data, home = install
    d = [{"id": AG, "name": AG, "alive": False}, {"id": "gm-demo", "name": "gm-demo", "alive": True},
         {"id": "arturo", "builtin": True, "alive": True}]
    assert br.precompute_due(data, d, home) == [AG]
    br.build(AG, data, home, runner=lambda c, e: None)
    assert br.precompute_due(data, d, home) == []                    # its briefing covers its last session
    monkeypatch.setattr(br, "precompute_due", lambda *a, **k: [AG])
    assert br.precompute_pass(data) == []                            # off without the flag
    (data / "state" / br.PRECOMPUTE_FLAG).write_text("")
    monkeypatch.setattr(br, "build", lambda root, od=None: None)
    assert br.precompute_pass(data) == [AG]


# ---- the hold hook ----

class Clock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def sleep(self, s):
        self.t += s


def _pending(data):
    b = data / "state" / "briefings"; b.mkdir(parents=True, exist_ok=True)
    (b / f"{AG}.pending").write_text(json.dumps({"started_at": time.time() - 5}))
    return b


def test_hold_attaches_a_ready_briefing_once(install):
    data, home = install
    _pending(data)
    br.build(AG, data, home, runner=lambda c, e: "- did things")
    ctx = hold.run(AG, str(data), prompt="what were you doing?")
    assert ctx.startswith("BRIEFING FROM YOUR PREDECESSOR") and "- did things" in ctx
    assert hold.run(AG, str(data), prompt="and now?") is None        # loaded: nothing more, no wait


def test_hold_waits_for_the_briefing_then_attaches_it(install):
    data, home = install
    _pending(data)
    clock = Clock()
    calls = {"n": 0}
    def sleep(s):
        clock.sleep(s)
        calls["n"] += 1
        if calls["n"] == 8:                                          # the briefing lands two seconds in
            br.build(AG, data, home, runner=lambda c, e: "- arrived")
    ctx = hold.run(AG, str(data), hold_s=40, sleep=sleep, clock=clock, prompt="hi")
    assert "- arrived" in ctx and clock.t == pytest.approx(2.0)


def test_hold_times_out_says_the_context_is_missing_and_never_waits_again(install):
    data, home = install
    _pending(data)
    clock = Clock()
    ctx = hold.run(AG, str(data), hold_s=3, sleep=clock.sleep, clock=clock, prompt="hi")
    assert "has not arrived yet (waited 3 seconds)" in ctx and "say plainly" in ctx and clock.t >= 3
    t0 = clock.t
    ctx = hold.run(AG, str(data), hold_s=3, sleep=clock.sleep, clock=clock, prompt="next")
    assert clock.t == t0 and "still hasn't arrived" in ctx           # later prompts check without waiting


def test_the_boot_prompt_is_never_held(install):
    data, home = install
    _pending(data)
    clock = Clock()
    assert hold.run(AG, str(data), hold_s=40, sleep=clock.sleep, clock=clock, prompt=f"You are {AG}. Read /tmp/x.md and follow") is None
    assert clock.t == 0


def test_without_a_waiting_successor_nothing_is_held(install):
    data, home = install
    clock = Clock()
    assert hold.run(AG, str(data), hold_s=40, sleep=clock.sleep, clock=clock, prompt="hi") is None and clock.t == 0


def test_the_hook_never_fails_a_prompt(monkeypatch, capsys):
    monkeypatch.setenv("BRIEFING_HOLD_ROOT", AG)
    monkeypatch.setenv("ORCHESTRA_DIR", "/nonexistent/dir")
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("not json"))
    monkeypatch.setattr(hold, "HOLD_S", 0)
    assert hold.main() == 0


def test_the_hold_hook_rides_only_a_fresh_successors_own_settings(tmp_path, monkeypatch):
    from scripts import spawn_permission_rules as spr
    monkeypatch.delenv("BRIEFING_HOLD_ROOT", raising=False)
    monkeypatch.delenv("BG_GREEN_ROOT", raising=False)
    s = json.loads(Path(spr.write_settings("x", "/work", orchestra_dir=str(tmp_path), out_dir=str(tmp_path))).read_text())
    assert "hooks" not in s
    monkeypatch.setenv("BRIEFING_HOLD_ROOT", AG)
    s = json.loads(Path(spr.write_settings(AG, "/work", orchestra_dir=str(tmp_path), out_dir=str(tmp_path))).read_text())
    h = s["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert "briefing_hold.py" in h["command"] and f"BRIEFING_HOLD_ROOT={AG}" in h["command"] and h["timeout"] == 60
    monkeypatch.setenv("BRIEFING_HOLD_ROOT", "x; rm -rf /")
    s = json.loads(Path(spr.write_settings(AG, "/work", orchestra_dir=str(tmp_path), out_dir=str(tmp_path))).read_text())
    assert "hooks" not in s                                          # a malformed id never reaches a command


# ---- the fresh start ----

def _identity(data, gen_retired=False):
    db = str(data / "state" / "orchestra-registry.db")
    odb.init_db(db)
    c = odb.get_connection(db)
    odb.adopt_identity(c, {"root": AG, "generation": 1, "model": "claude-opus", "tier": "T2", "runtime": "claude"})
    c.close()
    (data / "state" / "identity-store-cutover.flag").write_text("")
    return db


def test_fresh_start_retires_the_old_generation_so_the_spawn_mints_the_next_one(install):
    data, home = install
    db = _identity(data)
    assert fs.retire_current_generation(data, AG) is True
    assert fs.retire_current_generation(data, AG) is False                 # already retired
    from scripts.identity_store import identity_writer
    assert identity_writer.respawn_retired_canonical(str(data), AG, "claude-opus") == 2   # the existing respawn path
    c = sqlite3.connect(db)
    gen = c.execute("SELECT g.generation FROM canonical c JOIN generations g ON g.id = c.generation_id WHERE c.root = ?", (AG,)).fetchone()[0]
    assert gen == 2 and c.execute("SELECT tmux_session FROM canonical WHERE root = ?", (AG,)).fetchone()[0] == AG
    c.close()


def test_start_returns_as_soon_as_the_session_exists_with_a_short_first_prompt(install, monkeypatch):
    data, home = install
    _identity(data)
    monkeypatch.setattr(br, "transcript_path", lambda root, od=None, home_=None, **k: br.transcript_path.__wrapped__(root, od, home) if hasattr(br.transcript_path, "__wrapped__") else None)
    spawned, delivered, ups = {}, {}, iter([False, False, True])
    r = fs.start(AG, orchestra_dir=data, directory=[{"id": AG, "name": "Builder", "alive": False}],
                 spawn=lambda root, env: spawned.update(root=root, env=env),
                 deliver=lambda root, od: delivered.update(root=root), session_up=lambda t: next(ups), wait_s=5)
    assert r["session_up"] is True and r["id"] == AG and r["name"] == "Builder"
    env = spawned["env"]
    assert env["BRIEFING_HOLD_ROOT"] == AG and env["REINCARNATION"] == "true" and env["AGENT_RUNTIME"] == "claude"
    prompt = Path(env["FRESH_INIT_PROMPT_FILE"]).read_text()
    assert prompt.startswith(f"You are Builder ({AG}), a fresh start of this agent: its next generation")
    assert "briefing on what your predecessor did is being prepared" in prompt and "inbox --agent" in prompt
    assert delivered["root"] == AG
    assert json.loads((data / "state" / "briefings" / f"{AG}.pending").read_text())["started_at"] > 0


def test_start_refuses_a_running_agent_an_unknown_one_and_the_built_ins(install):
    data, home = install
    with pytest.raises(fs.NotStartable, match="already running"):
        fs.start(AG, orchestra_dir=data, directory=[{"id": AG, "name": AG, "alive": True}])
    with pytest.raises(fs.NotStartable, match="no agent"):
        fs.start("ghost", orchestra_dir=data, directory=[])
    with pytest.raises(fs.NotStartable, match="not an agent"):
        fs.start("arturo", orchestra_dir=data, directory=[{"id": "arturo", "builtin": True, "alive": True}])


def test_start_clears_a_previous_successors_markers(install):
    data, home = install
    b = data / "state" / "briefings"; b.mkdir(parents=True)
    (b / f"{AG}.loaded").write_text("old"); (b / f"{AG}.timedout").write_text("old")
    fs.start(AG, orchestra_dir=data, directory=[{"id": AG, "name": AG, "alive": False}],
             spawn=lambda r, e: None, deliver=lambda r, od: None, session_up=lambda t: True)
    assert not (b / f"{AG}.loaded").exists() and not (b / f"{AG}.timedout").exists()


def test_boot_prompts_are_not_the_agents_work(install):
    data, home = install
    tp = br.transcript_path(AG, data, home)
    with open(tp, "a") as f:
        f.write(_turn("user", "2026-10-21T09:00:00Z", f"You are {AG}. Read /tmp/agent-init-{AG}.md and follow all instructions in it.") + "\n")
        f.write(_turn("assistant", "2026-10-21T09:00:30Z", "Read my instructions; waiting for work.") + "\n")
    line = br.last_line(AG, data, home)
    assert "agent-init" not in line and 'it was handling: "Also add a test' in line


def test_the_briefing_uses_the_predecessors_session_pinned_at_start_not_the_successors(install, tmp_path):
    data, home = install
    pred = br.transcript_path(AG, data, home)
    fs.start(AG, orchestra_dir=data, directory=[{"id": AG, "name": AG, "alive": False}],
             spawn=lambda r, e: None, deliver=lambda r, od: None, session_up=lambda t: True, home=home)
    reg = json.loads((data / "registry.json").read_text())
    reg["agents"][AG]["session_id"] = "99999999-0000-0000-0000-000000000000"     # the successor's own session now
    (data / "registry.json").write_text(json.dumps(reg))
    assert br.transcript_path(AG, data, home) == pred
    assert "Also add a test for the empty email" in br.build(AG, data, home, runner=lambda c, e: None).read_text()


def test_an_older_briefing_from_before_the_predecessors_latest_work_is_not_ready(install):
    data, home = install
    br.build(AG, data, home, runner=lambda c, e: "- old news")         # built before the latest work
    time.sleep(0.05)
    tp = br.transcript_path(AG, data, home)
    with open(tp, "a") as f:
        f.write(_turn("assistant", "2026-10-22T09:00:00Z", "Added apple.") + "\n")
    fs.start(AG, orchestra_dir=data, directory=[{"id": AG, "name": AG, "alive": False}],
             spawn=lambda r, e: None, deliver=lambda r, od: None, session_up=lambda t: True, home=home)
    clock = Clock()
    ctx = hold.run(AG, str(data), hold_s=2, sleep=clock.sleep, clock=clock, prompt="hi")
    assert "has not arrived yet" in ctx and "old news" not in ctx        # the stale one is never attached
    br.build(AG, data, home, runner=lambda c, e: "- added apple")
    ctx = hold.run(AG, str(data), hold_s=2, sleep=clock.sleep, clock=clock, prompt="hi again")
    assert "- added apple" in ctx


def test_the_briefing_shows_what_the_predecessor_did_not_only_what_it_said(install):
    data, home = install
    tp = br.transcript_path(AG, data, home)
    with open(tp, "a") as f:
        f.write(json.dumps({"type": "assistant", "timestamp": "2026-10-22T09:00:00Z", "message": {"content": [
            {"type": "text", "text": "Adding it now."},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/work/demo.project/fruits.txt"}}]}}) + "\n")
    turns = br.text_turns(tp)
    assert turns[-1][2] == "Adding it now. [ran Edit /work/demo.project/fruits.txt]"
