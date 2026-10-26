"""Background jobs: the tray's data, results posted home to their thread, sandboxed helpers."""
import json
import os

from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture
from services.arturo import helpers as H
from services.arturo.jobs import JobBoard
from services.arturo.thread_store import ThreadStore


def _now_spawn(fn):
    fn()


def test_a_job_reports_steps_and_ends_once(tmp_path):
    ended = []
    b = JobBoard(tmp_path / "jobs.json", on_done=ended.append, spawn=_now_spawn)

    def work(step):
        step({"phase": "start", "call_id": "t1", "name": "WebSearch", "args_summary": '{"q": "x"}'})
        step({"phase": "done", "call_id": "t1", "name": "WebSearch", "ok": True, "summary": "3 results"})
        step({"phase": "done", "call_id": "t2", "name": "WebFetch", "ok": False, "summary": "404"})
        return "the answer", True
    job = b.start("th", "research", "Reading a link", work)
    (done,) = ended
    assert done["id"] == job["id"] and done["status"] == "done" and done["result"] == "the answer"
    assert [(s["name"], s["ok"]) for s in done["steps"]] == [("WebSearch", True), ("WebFetch", False)]
    assert b.for_thread("th")[0]["status"] == "done" and b.for_thread("other") == []
    # persisted: a reload still lists what finished
    assert JobBoard(tmp_path / "jobs.json").for_thread("th")[0]["result"] == "the answer"


def test_a_job_running_at_restart_is_lost_not_running(tmp_path):
    p = tmp_path / "jobs.json"
    p.write_text(json.dumps([{"id": "job_1", "thread": "th", "kind": "research", "title": "x", "status": "running",
                              "started": 1.0, "ended": None, "steps": [], "result": None, "error": None}]))
    (j,) = JobBoard(p, clock=lambda: 5.0).for_thread("th")
    assert j["status"] == "lost" and "restarted" in j["error"]


def test_cancel_stops_it_and_posts_nothing(tmp_path):
    ended, stopped, held = [], [], []
    b = JobBoard(None, on_done=ended.append, spawn=held.append)
    job = b.start("th", "research", "x", lambda step: ("late answer", True), cancel=lambda: stopped.append(1))
    assert not b.cancel("other-thread", job["id"])
    assert b.cancel("th", job["id"]) and stopped == [1]
    held[0]()
    assert ended[0]["status"] == "cancelled" and ended[0]["result"] is None


def test_a_thread_has_a_cap_on_running_jobs(tmp_path):
    b = JobBoard(None, spawn=lambda fn: None)
    for _ in range(4):
        assert b.start("th", "research", "x", lambda s: ("", True))
    assert b.start("th", "research", "x", lambda s: ("", True)) is None
    assert b.start("th2", "research", "x", lambda s: ("", True))


def test_the_stream_parser_turns_cli_events_into_steps():
    lines = [json.dumps(x) for x in [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "WebFetch",
                                                         "input": {"url": "https://example.com"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a",
                                                    "content": "Example Domain page"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "It's a placeholder page."}]]
    steps = []
    text, err = H.parse_stream(lines, on_step=steps.append)
    assert text == "It's a placeholder page." and not err
    assert steps[0]["phase"] == "start" and steps[0]["name"] == "WebFetch" and "example.com" in steps[0]["args_summary"]
    assert steps[1] == {"phase": "done", "call_id": "a", "name": "WebFetch", "ok": True, "summary": "Example Domain page"}


def test_the_helpers_get_only_their_own_tools(tmp_path):
    r = H.research_run("what is it", ["https://example.com"])
    i = r.argv.index("--tools")
    assert r.argv[i + 1] == "WebSearch,WebFetch" and "Read" not in " ".join(r.argv)
    assert "https://example.com" in r.prompt
    up = tmp_path / "uploads"
    up.mkdir()
    (up / "a.png").write_bytes(b"x")
    (tmp_path / "secret.txt").write_text("no")
    lk = H.look_run(str(up / "a.png"), "what's this?", up)
    j = lk.argv.index("--tools")
    assert lk.argv[j + 1] == "Read" and f"Read(/{up.resolve()}/**)" in lk.argv and lk.cwd == str(up.resolve())
    assert "WebFetch" not in " ".join(lk.argv)
    assert H.look_path_ok(str(up / "a.png"), up)
    assert not H.look_path_ok(str(tmp_path / "secret.txt"), up)
    assert not H.look_path_ok(str(up / ".." / "secret.txt"), up)
    os.symlink(tmp_path / "secret.txt", up / "link.png")
    assert not H.look_path_ok(str(up / "link.png"), up)


def test_a_helper_run_reports_failure_and_cancel(tmp_path):
    class Proc:
        def __init__(self, lines):
            self.stdout = iter(lines)

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass
    ok_lines = [json.dumps({"type": "result", "subtype": "success", "result": "found it"})]
    run = H.HelperRun(["x"], "q", 5, cwd=str(tmp_path), popen=lambda *a, **k: Proc(ok_lines))
    assert run.run() == ("found it", True)
    bad = H.HelperRun(["x"], "q", 5, cwd=str(tmp_path),
                      popen=lambda *a, **k: Proc([json.dumps({"type": "result", "subtype": "error_max_turns"})]))
    assert bad.run()[1] is False
    c = H.HelperRun(["x"], "q", 5, cwd=str(tmp_path), popen=lambda *a, **k: Proc(ok_lines))
    c.cancel()
    assert c.run() == ("cancelled", False)


def _as_dashboard(P, conv):
    """A dashboard turn on `conv`: the fleet caller, the conversation the tool call belongs to."""
    t = P._TEAM_TURN.set(P._begin_team_turn(conv, None, "fleet", "hi"))
    c = P._CALLER_CONV.set(conv)
    return lambda: (P._CALLER_CONV.reset(c), P._TEAM_TURN.reset(t))


def test_research_in_a_thread_is_a_job_whose_result_comes_home(P, monkeypatch):
    held = []
    P._JOBS = P._jobs_mod.JobBoard(None, on_done=P._post_job_result, spawn=held.append)

    class Run:
        def run(self, on_step=None):
            on_step({"phase": "start", "call_id": "a", "name": "WebFetch", "args_summary": "{}"})
            return "GPT-Live launched in October.", True

        def cancel(self):
            pass
    monkeypatch.setattr(P._helpers, "research_run", lambda q, urls=None: Run())
    done = _as_dashboard(P, "web_th1")
    try:
        out = P._execute_tool_impl("research", {"question": "what's this link?", "urls": ["https://x.example/a"]})
    finally:
        done()
    assert "Research started (job job_" in out
    held[0]()                                               # the job runs, then posts home
    turns = P._THREADS.get_thread("web_th1")["turns"]
    assert turns[-1]["meta"]["kind"] == "job_result" and turns[-1]["meta"]["status"] == "done"
    assert "GPT-Live launched in October." in turns[-1]["content"]
    assert "UNTRUSTED" not in turns[-1]["content"]          # the label is for the brain, not the screen
    hist = P._THREADS.history("web_th1")[-1]["content"]
    assert hist.startswith("(Background job result") and "UNTRUSTED" not in hist


def test_look_at_image_refuses_anything_outside_uploads(P, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "UPLOADS_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir()
    done = _as_dashboard(P, "web_th3")
    try:
        assert "only look at images" in P._execute_tool_impl("look_at_image", {"path": "/etc/passwd"})
    finally:
        done()


def test_the_gm_gets_a_digest_not_the_transcript(P, monkeypatch):
    turns = [{"role": "user", "text": "spin up a research agent on competitors"},
             {"role": "arturo", "text": "On it."}]
    monkeypatch.setenv("ARTURO_GM_DIGEST", "1")
    monkeypatch.setattr(P, "brain", type("B", (), {"complete": staticmethod(lambda **k: P._brain.make_response(
        "FYI, handled by Arturo: competitor research.\nStarted a research job. Nothing left open.", None, "stop"))}))
    summary, transcript = P._gm_call_note(turns, "Voice call: 2 turns")
    assert transcript is None and summary.startswith("FYI, handled by Arturo:") and "\n" in summary
    monkeypatch.setenv("ARTURO_GM_INJECT_FULL", "1")
    summary, transcript = P._gm_call_note(turns, "Voice call: 2 turns")
    assert "spin up a research agent" in transcript


def test_deep_think_runs_arturos_brain_on_the_thread_and_answers_as_its_turn(P, monkeypatch):
    held = []
    P._JOBS = P._jobs_mod.JobBoard(None, on_done=P._post_job_result, spawn=held.append)
    P._THREADS.record_turn("web_d1", "we shipped the voice bridge today", "Nice, it's live.", principal="fleet")
    seen = {}

    def brain_reply(messages, conversation_id):
        # inside the run: the deep turn, the thread as history, read-only tools, and tool steps reported
        turn = P._TEAM_TURN.get()
        seen.update(deep=P._is_deep(turn), conv=conversation_id,
                    offered={t["function"]["name"] for t in P._bound_tools()},
                    history=[m["content"] for m in messages])
        monkeypatch.setattr(P, "_execute_tool_impl", lambda name, args, user_turns=None:
                            P._non_fleet_refusal(name, P._TEAM_TURN.get()) or f"{name} ok")
        seen["spawn"] = P.execute_tool("run_command", {"command": "ls"})
        seen["list"] = P.execute_tool("list_agents", {})
        return "The bridge is live and nothing is blocking it.", ["list_agents"], []
    monkeypatch.setattr(P, "_brain_reply", brain_reply)
    done = _as_dashboard(P, "web_d1")
    try:
        out = P._execute_tool_impl("ask_gm", {"request": "is anything blocking the voice launch?"})
    finally:
        done()
    assert "job job_" in out and "GM" not in out
    held[0]()
    assert seen["deep"] and seen["conv"] == "web_d1"
    assert "run_command" not in seen["offered"] and "async_task" not in seen["offered"]
    assert {"list_agents", "spawn_agent", "kill_agent", "inject_message", "agent_message"} <= seen["offered"]
    assert "we shipped the voice bridge today" in seen["history"]
    assert seen["spawn"].startswith("NOT RUN") and seen["list"] == "list_agents ok"
    last = P._THREADS.get_thread("web_d1")["turns"][-1]
    assert last["content"] == "The bridge is live and nothing is blocking it."         # Arturo's own words
    assert last["meta"]["job_kind"] == "deep_think"
    assert [(st["name"], st["ok"]) for st in last["meta"]["steps"]] == [("run_command", False), ("list_agents", True)]


def test_a_deep_run_never_holds_outside_content_and_a_way_out(P):
    t = P._deep_turn("web_d2", "fleet")
    assert P._non_fleet_refusal("read_file", t) is None                 # private data alone: fine
    assert P._non_fleet_refusal("spawn_agent", t) is None               # a way out alone: fine
    assert P._non_fleet_refusal("research", t) is None                  # the first web read (its query is the way out)
    for way_out in ("research", "spawn_agent", "kill_agent", "inject_message", "agent_message"):
        assert "outside content" in P._non_fleet_refusal(way_out, t)    # now holding outside content
    assert P._non_fleet_refusal("read_file", t) is None                 # reading stays open: nothing can leave
    assert P._non_fleet_refusal("deep_think", t).startswith("NOT RUN") and P._non_fleet_refusal("ask_gm", t).startswith("NOT RUN")
    t2 = P._deep_turn("web_d2b", "fleet")
    P._non_fleet_refusal("look_at_image", t2)
    assert "outside content" in P._non_fleet_refusal("research", t2)


def test_outside_content_in_the_threads_recent_history_taints_a_new_run(P, monkeypatch):
    P._THREADS.record_turn("web_t5", "read this page", "Reading it.", principal="fleet")
    assert not P._THREADS.untrusted_in_last("web_t5", 40)
    tok = P._CALLER_CONV.set("web_t5")
    try:
        monkeypatch.setattr(P, "_execute_tool_impl", lambda name, args, user_turns=None: "ok")
        P.execute_tool("research", {"question": "q"})              # any turn reading outside content marks it
    finally:
        P._CALLER_CONV.reset(tok)
    assert P._THREADS.untrusted_in_last("web_t5", 40)
    for i in range(30):
        P._THREADS.record_turn("web_t5", f"q{i}", f"a{i}", principal="fleet")
    assert not P._THREADS.untrusted_in_last("web_t5", 40)          # scrolled out of the window a run is given
    assert P._THREADS.untrusted_in_last("web_t5", 80)


def test_secrets_stay_host_side(P):
    done = _as_dashboard(P, "web_t6")                                  # even the dashboard's own turn
    try:
        for path in ("~/.orchestra/.env.secrets", "/home/x/.orchestra/state/gateway-token", "~/.ssh/id_ed25519",
                     "/srv/app/client_secret.json", "/home/x/.claude/.credentials.json"):
            assert P._execute_tool_impl("read_file", {"path": path}).startswith("Refused: that file holds secrets")
    finally:
        done()
    assert not P._secret_path("/home/x/orchestraos/services/arturo/tokenizer.py")


def test_the_budget_and_cancel_stop_the_tools(P, monkeypatch):
    import threading
    import time as _t
    monkeypatch.setattr(P, "_execute_tool_impl", lambda name, args, user_turns=None: "ran")
    steps = []
    stop = threading.Event()
    run = {"step": steps.append, "deadline": _t.time() + 60, "max_steps": 2, "steps": 0, "cancelled": stop}
    tok = P._JOB_RUN.set(run)
    try:
        assert P.execute_tool("list_agents", {}) == "ran"
        assert P.execute_tool("list_agents", {}) == "ran"
        assert P.execute_tool("list_agents", {}).startswith("STOP")              # step budget
        run["max_steps"] = 10
        stop.set()
        assert P.execute_tool("list_agents", {}).startswith("STOP")              # cancelled
    finally:
        P._JOB_RUN.reset(tok)
    assert [s["phase"] for s in steps] == ["start", "done", "start", "done"]


def test_a_deep_run_orchestrates_until_it_reads_outside_content(P):  # noqa: D103
    t = P._deep_turn("web_d3", "fleet")
    assert P._non_fleet_refusal("spawn_agent", t) is None
    assert P._non_fleet_refusal("inject_message", t) is None
    assert P._non_fleet_refusal("research", t) is None                  # a web page now in the run
    for action in ("spawn_agent", "kill_agent", "inject_message", "agent_message"):
        assert "outside content" in P._non_fleet_refusal(action, t)
    t2 = P._deep_turn("web_d4", "fleet")
    P._non_fleet_refusal("look_at_image", t2)
    assert "outside content" in P._non_fleet_refusal("kill_agent", t2)
    assert P._non_fleet_refusal("run_command", P._deep_turn("web_d5", "fleet")).startswith("NOT RUN")


def test_a_readers_result_reaches_the_brain_as_its_structured_digest(tmp_path):
    from services.arturo import helpers as h
    from services.arturo.thread_store import brain_content
    text = ('Full write-up with every detail.\n\n```json\n{"summary": "Grade D", "facts": ["quote button 404"], '
            '"links": ["https://acme.example/"]}\n```')
    shown, digest = h.split_digest(text)
    assert shown == "Full write-up with every detail." and "```" not in shown
    assert "Summary: Grade D" in digest and "- quote button 404" in digest and "https://acme.example/" in digest
    meta = {"kind": "job_result", "title": "Grading the site", "digest": digest}
    read = brain_content("assistant", shown, "", meta)
    assert "Grade D" in read and "Full write-up" not in read            # the brain reads the digest, not the page


def test_a_reader_with_no_json_block_still_gives_a_short_digest():
    from services.arturo import helpers as h
    shown, digest = h.split_digest("x" * 5000)
    assert len(shown) == 5000 and digest.startswith("Summary (no structured result") and len(digest) < 700


def test_background_work_in_a_conversation_is_a_drawer_job(P, monkeypatch):
    started = []
    monkeypatch.setattr(P, "_current_thread", lambda: "web_tasks")
    monkeypatch.setattr(P._JOBS, "start", lambda thread, kind, title, work, cancel=None:
                        started.append((thread, kind, title, work)) or {"id": "job_x"})
    done = _as_dashboard(P, "web_tasks")
    try:
        out = P._execute_tool_impl("async_task", {"tool_name": "list_agents", "tool_args": {}, "summary": "Checking the fleet"})
    finally:
        done()
    assert "job_x" in out and started[0][:3] == ("web_tasks", "task", "Checking the fleet")
    steps = []
    monkeypatch.setattr(P, "execute_tool", lambda name, args, user_turns=None: "Agents (3 registered, 3 running)")
    text, ok = started[0][3](steps.append)
    assert ok and text.startswith("Agents") and [s["phase"] for s in steps] == ["start", "done"]
