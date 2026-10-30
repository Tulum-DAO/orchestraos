"""seat_supervisor.py against a fake `claude` that speaks stream-json (init, deltas, the replay echo,
result, a held control request, an interrupt reply, an exit before the echo). The supervisor runs
as a real subprocess with a temp data dir; nothing touches tmux, a real seat or a real CLI."""
import importlib.util
import json
import os
import types
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import headless_switch as HS  # noqa: E402

spec = importlib.util.spec_from_file_location("seat_supervisor_t", HERE / "seat_supervisor.py")
SS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SS)

SID = "00000000-0000-4000-8000-000000000001"

FAKE = r'''#!__PY__
"""A stand-in for `claude -p --input-format stream-json --output-format stream-json`."""
import json, os, sys, uuid
rec = os.environ["FAKE_RECORD"]
state = os.environ["FAKE_STATE"]
args = sys.argv[1:]
sid = args[args.index("--resume") + 1]
with open(os.path.join(state, "start-%d.json" % os.getpid()), "w") as fh:
    json.dump({"argv": args, "env": {k: os.environ.get(k) for k in
               ("TMUX_PANE", "ORCHESTRA_DIR", "ORCH_DIR", "CLAUDECODE", "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN",
                "AQD_TMUX_SESSION")},
               "cwd": os.getcwd()}, fh)
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
def record(line):
    with open(rec, "a") as fh:
        fh.write(json.dumps({"pid": os.getpid(), "line": json.loads(line)}) + "\n")
if "-p" not in args:
    # The TUI (Dev): it runs until "/exit" is typed into its pane (the fake tmux writes the keys to a
    # file) or the test asks it to leave on its own.
    import time as _t
    keys = os.path.join(state, "tmux-keys")
    while True:
        if os.path.exists(os.path.join(state, "tui-exit")):
            sys.exit(0)
        if os.path.exists(keys) and "/exit" in open(keys).read():
            sys.exit(0)
        _t.sleep(0.05)
out({"type": "system", "subtype": "hook_started", "hook_name": "SessionStart:resume"})
pending = {}
hanging = []
ECHO_OURS = os.environ.get("FAKE_ECHO_UUID") == "1"
cur = {"uuid": None}
def echo_uuid():
    return (cur["uuid"] if ECHO_OURS and cur["uuid"] else str(uuid.uuid4()))
def turn(text, extra="", echo=True):
    out({"type": "system", "subtype": "init", "session_id": sid, "cwd": os.getcwd()})
    if echo:
        out({"type": "user", "message": {"role": "user", "content": text}, "session_id": sid,
             "uuid": echo_uuid(), "isReplay": True})
    for piece in ("Hel", "lo"):
        out({"type": "stream_event", "event": {"type": "content_block_delta",
             "delta": {"type": "text_delta", "text": piece}}, "session_id": sid})
    out({"type": "assistant", "parent_tool_use_id": None, "message": {
        "content": [{"type": "text", "text": "Hello" + extra}],
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 1990,
                  "cache_read_input_tokens": 48000, "output_tokens": 0}}})
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": sid,
         "modelUsage": {"sample-small": {"inputTokens": 50, "contextWindow": 200000},
                        "sample-model": {"inputTokens": 10, "cacheReadInputTokens": 48000,
                                         "contextWindow": 100000}}})
for line in sys.stdin:
    if not line.strip():
        continue
    record(line)
    o = json.loads(line)
    if o.get("type") == "control_request" and o["request"].get("subtype") == "set_model":
        if os.environ.get("FAKE_SET_MODEL") == "ok":
            out({"type": "control_response", "response": {"subtype": "success", "request_id": o["request_id"]}})
        else:
            out({"type": "control_response", "response": {"subtype": "error", "request_id": o["request_id"],
                 "error": "Unsupported control request subtype: set_model"}})
        continue
    if o.get("type") == "control_request" and o["request"].get("subtype") == "interrupt":
        out({"type": "control_response", "response": {"subtype": "success", "request_id": o["request_id"]}})
        if hanging:
            hanging.clear()
            out({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": sid})
        continue
    if o.get("type") == "control_response":
        rid = o["response"]["request_id"]
        text = pending.pop(rid)
        pick = o["response"]["response"]["updatedInput"]["answers"]
        turn(text, extra=" you picked " + json.dumps(pick), echo=False)
        continue
    text = o["message"]["content"]
    cur["uuid"] = o.get("uuid")
    if "DIE-BEFORE-ECHO" in text and not os.path.exists(os.path.join(state, "died")):
        open(os.path.join(state, "died"), "w").close()
        sys.exit(1)
    if "DIE-AFTER-ECHO" in text and not os.path.exists(os.path.join(state, "died-after")):
        open(os.path.join(state, "died-after"), "w").close()
        out({"type": "system", "subtype": "init", "session_id": sid})
        out({"type": "user", "message": {"role": "user", "content": text}, "session_id": sid,
             "uuid": str(uuid.uuid4()), "isReplay": True})
        sys.stdout.flush()
        os._exit(1)
    if "HANG" in text:
        out({"type": "system", "subtype": "init", "session_id": sid, "model": "sample-model"})
        out({"type": "user", "message": {"role": "user", "content": text}, "session_id": sid,
             "uuid": str(uuid.uuid4()), "isReplay": True})
        out({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
            {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "sleep 999"}}]}})
        hanging.append(text)
        continue
    if "ASK" in text:
        out({"type": "system", "subtype": "init", "session_id": sid})
        out({"type": "user", "message": {"role": "user", "content": text}, "session_id": sid,
             "uuid": str(uuid.uuid4()), "isReplay": True})
        rid = str(uuid.uuid4())
        pending[rid] = text
        out({"type": "control_request", "request_id": rid, "request": {
            "subtype": "can_use_tool", "tool_name": "AskUserQuestion",
            "input": {"questions": [{"question": "Which size?", "header": "Size",
                      "options": [{"label": "Small"}, {"label": "Large"}], "multiSelect": False}]}}})
        continue
    turn(text)
'''


@pytest.fixture
def rig():
    # A short temp root: a unix socket path is limited to ~107 bytes.
    root = Path(tempfile.mkdtemp(prefix="hs-", dir="/tmp"))
    orch = root / "d"
    (orch / "state").mkdir(parents=True)
    work = root / "w"
    work.mkdir()
    (orch / "registry.json").write_text(json.dumps({"agents": {"qa-demo": {
        "runtime": "claude", "cwd": str(work), "session_id": SID, "model": "sample-model",
        "tmux_session": "qa-demo"}}}))
    fake = root / "claude"
    fake.write_text(FAKE.replace("__PY__", sys.executable))
    fake.chmod(0o755)
    state = root / "s"
    state.mkdir()
    rec = root / "stdin.jsonl"
    env = {**os.environ, "ORCHESTRA_DIR": str(orch), "ORCH_DIR": str(orch), "TMUX_PANE": "%99",
           "HEADLESS_CARDS": "0", "HEADLESS_CARDS_NOTIFY": "0",
           "CLAUDECODE": "1", "FAKE_RECORD": str(rec), "FAKE_STATE": str(state)}
    procs = []

    def start():
        p = subprocess.Popen([sys.executable, str(HERE / "seat_supervisor.py"), "run", "qa-demo",
                              "--claude-bin", str(fake), "--no-settings"],
                             env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        procs.append(p)
        wait_for(lambda: _status(orch) is not None, what="supervisor socket")
        return p

    r = type("Rig", (), {})()
    r.orch, r.work, r.state, r.rec, r.start, r.env, r.fake = orch, work, state, rec, start, env, fake
    r.writes = lambda: [json.loads(x) for x in rec.read_text().splitlines()] if rec.exists() else []
    yield r
    for p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
    shutil.rmtree(root, ignore_errors=True)


def _status(orch):
    try:
        return HS.status("qa-demo", orch_dir=orch)
    except HS.SupervisorDown:
        return None


def wait_for(cond, timeout=15.0, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def msg_state(r, mid):
    return HS.message_state("qa-demo", mid, orch_dir=r.orch).get("state")


def log_records(r):
    return [json.loads(x) for x in (HS.seat_dir("qa-demo", r.orch) / "events.jsonl").read_text().splitlines()]


def test_message_goes_queued_delivered_answered_on_one_warm_process(rig):
    rig.start()
    out = HS.send("qa-demo", "Sample one", "m1", orch_dir=rig.orch)
    assert out["ok"] and out["duplicate"] is False and out["state"] in ("queued", "delivered", "answered")
    wait_for(lambda: msg_state(rig, "m1") == "answered", what="m1 answered")
    pid1 = HS.status("qa-demo", orch_dir=rig.orch)["pid"]
    HS.send("qa-demo", "Sample two", "m2", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "m2") == "answered", what="m2 answered")
    st = HS.status("qa-demo", orch_dir=rig.orch)
    assert st["pid"] == pid1 and st["restarts"] == 0          # warm: one process for both turns
    assert st["state"] == "idle" and st["session_id"] == SID
    recs = log_records(rig)
    states = [x["state"] for x in recs if x["kind"] == "msg" and x["id"] == "m1"]
    assert states == ["accepted", "queued", "delivered", "answered"]
    assert any(x["kind"] == "in" and x["obj"]["message"]["content"] == "Sample one" for x in recs)
    assert any(x["kind"] == "out" and x["ev"].get("type") == "result" for x in recs)
    assert all("wall" in x and "t" in x for x in recs)


def test_same_id_is_written_once(rig):
    rig.start()
    HS.send("qa-demo", "Example once", "dup-1", orch_dir=rig.orch)
    again = HS.send("qa-demo", "Example once", "dup-1", orch_dir=rig.orch)
    assert again["ok"] and again["duplicate"] is True
    wait_for(lambda: msg_state(rig, "dup-1") == "answered", what="answered")
    HS.send("qa-demo", "Example once", "dup-1", orch_dir=rig.orch)
    texts = [w["line"]["message"]["content"] for w in rig.writes() if w["line"].get("type") == "user"]
    assert texts.count("Example once") == 1


def test_control_request_is_held_until_answered(rig):
    rig.start()
    HS.send("qa-demo", "ASK me a size", "q1", orch_dir=rig.orch)
    wait_for(lambda: HS.status("qa-demo", orch_dir=rig.orch)["state"] == "waiting", what="waiting")
    pend = HS.request("qa-demo", {"cmd": "pending"}, orch_dir=rig.orch)["pending"]
    assert len(pend) == 1 and pend[0]["request"]["tool_name"] == "AskUserQuestion"
    time.sleep(1.0)
    # Nothing answers on the agent's behalf.
    assert not [w for w in rig.writes() if w["line"].get("type") == "control_response"]
    assert msg_state(rig, "q1") == "delivered"
    # A message while a question waits is refused, not written.
    refused = HS.send("qa-demo", "Sample meanwhile", "q2", orch_dir=rig.orch)
    assert refused == {"ok": False, "reason": "waiting", "pending": 1}
    q = pend[0]["request"]["input"]
    resp = {"behavior": "allow", "updatedInput": {**q, "answers": {"Which size?": "Large"}}}
    ok = HS.request("qa-demo", {"cmd": "answer", "request_id": pend[0]["request_id"],
                                "response": resp}, orch_dir=rig.orch)
    assert ok["ok"]
    wait_for(lambda: msg_state(rig, "q1") == "answered", what="q1 answered")
    st = HS.status("qa-demo", orch_dir=rig.orch)
    assert st["held"] == 0 and st["state"] == "idle"
    sent = [w["line"] for w in rig.writes() if w["line"].get("type") == "control_response"]
    assert len(sent) == 1 and sent[0]["response"]["response"]["updatedInput"]["answers"] == {"Which size?": "Large"}
    bad = HS.request("qa-demo", {"cmd": "answer", "request_id": "nope", "response": {}}, orch_dir=rig.orch)
    assert bad == {"ok": False, "reason": "no-such-request"}


def test_exit_before_echo_is_unknown_then_resent_once_on_restart(rig):
    rig.start()
    HS.send("qa-demo", "Sample DIE-BEFORE-ECHO", "r1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "r1") == "answered", timeout=20, what="resent and answered")
    st = HS.status("qa-demo", orch_dir=rig.orch)
    assert st["restarts"] == 1 and st["generation"] == 2
    states = [x["state"] for x in log_records(rig) if x["kind"] == "msg" and x["id"] == "r1"]
    assert states == ["accepted", "queued", "unknown", "queued", "delivered", "answered"]
    by_pid = {}
    for w in rig.writes():
        if w["line"].get("type") == "user":
            by_pid.setdefault(w["pid"], []).append(w["line"]["message"]["content"])
    assert sorted(len(v) for v in by_pid.values()) == [1, 1]    # once to each process, never twice to one
    assert HS.message_state("qa-demo", "r1", orch_dir=rig.orch)["attempts"] == 2
    # A caller's retry of the same id writes nothing more.
    assert HS.send("qa-demo", "Sample DIE-BEFORE-ECHO", "r1", orch_dir=rig.orch)["duplicate"] is True
    assert sum(len(v) for v in by_pid.values()) == 2


def test_supervisor_restart_keeps_ids_and_resends_unechoed(rig):
    p = rig.start()
    HS.send("qa-demo", "Sample kept", "k1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "k1") == "answered", what="k1")
    assert HS.request("qa-demo", {"cmd": "stop"}, orch_dir=rig.orch)["ok"]
    assert p.wait(timeout=20) == 0
    assert not HS.socket_path("qa-demo", rig.orch).exists()
    # A message the log shows queued with no echo (a supervisor killed mid-send) is resent on start.
    log = HS.seat_dir("qa-demo", rig.orch) / "events.jsonl"
    with open(log, "a") as fh:
        fh.write(json.dumps({"t": 0, "wall": "x", "gen": 9, "kind": "msg", "id": "k2",
                             "state": "accepted", "attempts": 0, "text": "Sample carried"}) + "\n")
        fh.write(json.dumps({"t": 0, "wall": "x", "gen": 9, "kind": "msg", "id": "k2",
                             "state": "queued", "attempts": 1}) + "\n")
    rig.start()
    assert HS.send("qa-demo", "Sample kept", "k1", orch_dir=rig.orch)["duplicate"] is True
    wait_for(lambda: msg_state(rig, "k2") == "answered", what="k2 resent")
    texts = [w["line"]["message"]["content"] for w in rig.writes() if w["line"].get("type") == "user"]
    assert texts.count("Sample kept") == 1 and texts.count("Sample carried") == 1


def test_interrupt_and_spawn_environment(rig):
    rig.start()
    out = HS.request("qa-demo", {"cmd": "interrupt"}, orch_dir=rig.orch)
    assert out["ok"] and out["request_id"].startswith("int-")
    wait_for(lambda: any(x["kind"] == "out" and x["ev"].get("type") == "control_response"
                         for x in log_records(rig)), what="interrupt reply")
    starts = list(rig.state.glob("start-*.json"))
    assert len(starts) == 1
    info = json.loads(starts[0].read_text())
    a = info["argv"]
    for flag in ("-p", "--replay-user-messages", "--include-partial-messages", "--verbose",
                 "--dangerously-skip-permissions"):
        assert flag in a
    assert a[a.index("--input-format") + 1] == "stream-json"
    assert a[a.index("--output-format") + 1] == "stream-json"
    assert a[a.index("--permission-prompt-tool") + 1] == "stdio"
    assert a[a.index("--resume") + 1] == SID and a[a.index("--model") + 1] == "sample-model"
    assert "--permission-mode" not in a
    env = info["env"]
    assert env["TMUX_PANE"] == "%99" and env["CLAUDECODE"] is None
    assert env["ORCHESTRA_DIR"] == env["ORCH_DIR"] == str(rig.orch)
    assert env["CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN"] == "1"
    assert info["cwd"] == str(rig.work)


def test_second_supervisor_for_one_seat_refuses(rig):
    rig.start()
    p = subprocess.run([sys.executable, str(HERE / "seat_supervisor.py"), "run", "qa-demo",
                        "--claude-bin", "/bin/false", "--no-settings"], env=rig.env,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    assert p.returncode == 3 and "already running" in p.stderr
    assert HS.status("qa-demo", orch_dir=rig.orch)["state"] == "idle"


def test_non_claude_seat_will_not_run(rig):
    reg = json.loads((rig.orch / "registry.json").read_text())
    reg["agents"]["qa-demo"]["runtime"] = "codex"
    (rig.orch / "registry.json").write_text(json.dumps(reg))
    p = subprocess.run([sys.executable, str(HERE / "seat_supervisor.py"), "run", "qa-demo",
                        "--claude-bin", "/bin/false", "--no-settings"], env=rig.env,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    assert p.returncode == 2 and "stays on tmux" in p.stderr


def test_argv_builders_never_use_manual_mode():
    a = SS.headless_argv("claude", session_id=SID, model="m", settings="/tmp/x.json")
    assert "--permission-mode" not in a and "manual" not in a
    assert a[a.index("--settings") + 1] == "/tmp/x.json"
    t = SS.tui_argv("claude", session_id=SID, model="m", settings=None)
    assert t == ["claude", "--dangerously-skip-permissions", "--model", "m", "--resume", SID]


def test_pane_line_carries_spawns_environment(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_SEAT_NICE", "10")
    line = SS.pane_line("qa-demo", orch_dir="/tmp/sample-data")
    assert line.startswith("env -u CLAUDECODE ")
    assert "ORCHESTRA_DIR=/tmp/sample-data ORCH_DIR=/tmp/sample-data" in line
    assert "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1 nice -n 10 python3 " in line
    assert line.endswith("seat_supervisor.py run qa-demo")


class _T:
    def __init__(self, cur):
        self.cur, self.calls = cur, []

    def __call__(self, *a, timeout=5):
        self.calls.append(a)
        out = self.cur if a[0] == "display-message" else ""
        return type("R", (), {"returncode": 0, "stdout": out + "\n"})()


def test_takeover_types_only_into_a_shell_of_a_switched_on_seat(tmp_path):
    orch = tmp_path
    (orch / "state").mkdir()
    t = _T("bash")
    ok, why = SS.takeover("qa-demo", orch_dir=orch, tmux=t)
    assert not ok and "switch it on" in why and not t.calls
    HS.set_switch("qa-demo", True, runtime="claude", orch_dir=orch)
    t = _T("claude")
    ok, why = SS.takeover("qa-demo", orch_dir=orch, tmux=t)
    assert not ok and "not a shell" in why
    assert not [c for c in t.calls if c[0] == "send-keys"]
    t = _T("bash")
    ok, line = SS.takeover("qa-demo", orch_dir=orch, tmux=t)
    keys = [c for c in t.calls if c[0] == "send-keys"]
    assert ok and keys[0][-1] == line and keys[1][-1] == "Enter"


def test_restart_command_starts_a_fresh_process_and_keeps_messages(rig):
    rig.start()
    HS.send("qa-demo", "Sample before", "b1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "b1") == "answered", what="b1")
    pid1 = HS.status("qa-demo", orch_dir=rig.orch)["pid"]
    assert HS.request("qa-demo", {"cmd": "restart"}, orch_dir=rig.orch)["ok"]
    wait_for(lambda: HS.status("qa-demo", orch_dir=rig.orch)["generation"] == 2
             and HS.status("qa-demo", orch_dir=rig.orch)["state"] == "idle", what="restarted")
    st = HS.status("qa-demo", orch_dir=rig.orch)
    assert st["pid"] != pid1 and st["messages"] == {"answered": 1}
    HS.send("qa-demo", "Sample after", "b2", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "b2") == "answered", what="b2")
    texts = [w["line"]["message"]["content"] for w in rig.writes() if w["line"].get("type") == "user"]
    assert texts == ["Sample before", "Sample after"]


def test_killed_after_echo_ends_interrupted_and_is_not_resent(rig):
    rig.start()
    HS.send("qa-demo", "Sample DIE-AFTER-ECHO", "x1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "x1") == "interrupted", timeout=20, what="interrupted")
    wait_for(lambda: HS.status("qa-demo", orch_dir=rig.orch)["state"] == "idle", what="restarted idle")
    states = [x["state"] for x in log_records(rig) if x["kind"] == "msg" and x["id"] == "x1"]
    assert states == ["accepted", "queued", "delivered", "interrupted"]
    texts = [w["line"]["message"]["content"] for w in rig.writes() if w["line"].get("type") == "user"]
    assert texts.count("Sample DIE-AFTER-ECHO") == 1                # delivered once: never resent


def test_interrupt_mid_turn_ends_the_message_interrupted(rig):
    rig.start()
    HS.send("qa-demo", "Sample HANG", "h1", orch_dir=rig.orch)
    wait_for(lambda: HS.status("qa-demo", orch_dir=rig.orch)["tools_open"] == ["Bash"], what="tool open")
    st = HS.status("qa-demo", orch_dir=rig.orch)
    assert st["state"] == "working" and st["last_tool"] == "Bash" and st["model"] == "sample-model"
    assert st["turn_age_s"] is not None and st["last_event_age_s"] is not None
    assert HS.request("qa-demo", {"cmd": "interrupt"}, orch_dir=rig.orch)["ok"]
    wait_for(lambda: msg_state(rig, "h1") == "interrupted", what="interrupted")
    st = HS.status("qa-demo", orch_dir=rig.orch)
    assert st["state"] == "idle" and st["tools_open"] == [] and st["turn_age_s"] is None


def test_status_carries_context_and_held_summaries(rig):
    rig.start()
    assert HS.status("qa-demo", orch_dir=rig.orch)["context"]["tokens"] is None
    HS.send("qa-demo", "Sample ctx", "c1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "c1") == "answered", what="answered")
    ctx = HS.status("qa-demo", orch_dir=rig.orch)["context"]
    # The main model carries the most input; the side model's larger window is not used.
    assert ctx["tokens"] == 50000 and ctx["window"] == 100000 and ctx["at"]
    HS.send("qa-demo", "ASK sample", "c2", orch_dir=rig.orch)
    wait_for(lambda: HS.status("qa-demo", orch_dir=rig.orch)["state"] == "waiting", what="waiting")
    h = HS.status("qa-demo", orch_dir=rig.orch)["held_requests"]
    assert len(h) == 1 and h[0]["kind"] == "question" and h[0]["question"] == "Which size?"
    assert h[0]["options"] == ["Small", "Large"] and h[0]["tool"] == "AskUserQuestion"
    # A restarted supervisor reads the context back from its log.
    assert HS.request("qa-demo", {"cmd": "stop"}, orch_dir=rig.orch)["ok"]
    wait_for(lambda: _status(rig.orch) is None, what="stopped")
    rig.start()
    ctx = HS.status("qa-demo", orch_dir=rig.orch)["context"]
    assert ctx["tokens"] == 50000 and ctx["window"] == 100000
    assert msg_state(rig, "c2") == "interrupted"                    # its turn was cut by the stop


def test_held_summary_for_a_permission():
    s = SS.held_summary({"request_id": "r1", "request": {"subtype": "can_use_tool", "tool_name": "Bash",
                                                         "input": {"command": "ls /tmp"}}})
    assert s["kind"] == "permission" and s["question"] == "Allow Bash? ls /tmp" and s["tool"] == "Bash"


def test_our_uuid_rides_each_message_and_matches_the_echo_when_echoed(rig):
    rig.env["FAKE_ECHO_UUID"] = "1"
    rig.start()
    HS.send("qa-demo", "Sample uuid", "u1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "u1") == "answered", what="answered")
    want = str(SS.uuid.uuid5(SS.MESSAGE_UUID_NS, "u1"))
    sent = [w["line"] for w in rig.writes() if w["line"].get("type") == "user"]
    assert sent[0]["uuid"] == want
    rec = [x for x in log_records(rig) if x["kind"] == "msg" and x["id"] == "u1" and x["state"] == "delivered"][0]
    assert rec["echo_match"] == "uuid" and rec["cli_uuid"] == want
    st = HS.status("qa-demo", orch_dir=rig.orch)
    assert st["message_uuid"] is True and st["echo_matches"] == {"uuid": 1, "text": 0, "order": 0}


def test_a_cli_that_echoes_its_own_uuid_falls_back_to_text(rig):
    rig.start()
    HS.send("qa-demo", "Sample text match", "t1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "t1") == "answered", what="answered")
    rec = [x for x in log_records(rig) if x["kind"] == "msg" and x["id"] == "t1" and x["state"] == "delivered"][0]
    assert rec["echo_match"] == "text"
    assert HS.status("qa-demo", orch_dir=rig.orch)["echo_matches"]["text"] == 1


def test_a_resend_carries_the_same_uuid(rig):
    rig.start()
    HS.send("qa-demo", "Sample DIE-BEFORE-ECHO", "r9", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "r9") == "answered", timeout=20, what="resent")
    ids = [w["line"]["uuid"] for w in rig.writes() if w["line"].get("type") == "user"]
    assert len(ids) == 2 and ids[0] == ids[1] == str(SS.uuid.uuid5(SS.MESSAGE_UUID_NS, "r9"))


def test_message_uuid_off_for_a_seat_applies_at_the_next_cli_start(rig):
    rig.start()
    HS.set_option("qa-demo", "message_uuid", False, orch_dir=rig.orch)
    assert HS.request("qa-demo", {"cmd": "restart"}, orch_dir=rig.orch)["ok"]
    wait_for(lambda: HS.status("qa-demo", orch_dir=rig.orch)["generation"] == 2
             and HS.status("qa-demo", orch_dir=rig.orch)["state"] == "idle", what="restarted")
    assert HS.status("qa-demo", orch_dir=rig.orch)["message_uuid"] is False
    HS.send("qa-demo", "Sample plain", "p1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "p1") == "answered", what="answered")
    sent = [w["line"] for w in rig.writes() if w["line"].get("type") == "user"]
    assert "uuid" not in sent[-1]
    # The option never switches the seat on, and switching on keeps it.
    assert HS.is_headless("qa-demo", orch_dir=rig.orch) is False
    HS.set_switch("qa-demo", True, runtime="claude", orch_dir=rig.orch)
    assert HS.load_switch(rig.orch)["qa-demo"]["message_uuid"] is False


def test_a_cut_turn_is_listed_for_the_chat(rig):
    rig.start()
    HS.send("qa-demo", "Sample DIE-AFTER-ECHO for the chat", "x9", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "x9") == "interrupted", timeout=20, what="interrupted")
    doc = json.loads((HS.seat_dir("qa-demo", rig.orch) / "interrupted.json").read_text())
    assert doc["seat"] == "qa-demo"
    [it] = doc["items"]
    assert it["id"] == "x9" and it["text"] == "Sample DIE-AFTER-ECHO for the chat" and it["at"]
    assert "exited" in it["reason"]


def _ledger(rig):
    from approval_schema import ApprovalStore
    st = ApprovalStore(db_path=str(rig.orch / "state" / "tasks.db"))
    st.migrate()
    return st


def test_a_held_question_becomes_one_card_and_its_answer_one_control_response(rig, monkeypatch):
    rig.env["HEADLESS_CARDS"] = "1"
    rig.env["APPROVAL_DB_PATH"] = str(rig.orch / "state" / "tasks.db")
    rig.start()
    HS.send("qa-demo", "ASK me a size", "c1", orch_dir=rig.orch)
    wait_for(lambda: (HS.status("qa-demo", orch_dir=rig.orch)["held_requests"] or [{}])[0].get("card_id"),
             what="card filed")
    card = HS.status("qa-demo", orch_dir=rig.orch)["held_requests"][0]["card_id"]
    st = _ledger(rig)
    row = st.get(card)
    assert row["status"] == "pending" and row["from_agent"] == "qa-demo" and row["origin"] == "headless"
    # Answer it the way every surface does: the ONE answer core, then the resume.
    monkeypatch.setenv("ORCHESTRA_DIR", str(rig.orch))
    spec = importlib.util.spec_from_file_location("wg_sup_cards", HERE / "watch_gateway.py")
    G = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(G)
    import approval_resume as R
    sent = []
    monkeypatch.setattr(R, "_msg_store", lambda: types.SimpleNamespace(send=lambda **kw: sent.append(kw)))
    monkeypatch.setattr(R, "_default_inject", lambda *a: pytest.fail("injected"))
    import headless_switch
    real_request = headless_switch.request
    monkeypatch.setattr(headless_switch, "request", lambda seat, payload, **kw: real_request(
        seat, payload, orch_dir=rig.orch))
    out = G.apply_answer(st, card, "option", option_n="2", fire=True, surface="web")
    assert out["applied"]
    wait_for(lambda: msg_state(rig, "c1") == "answered", what="answered")
    responses = [w["line"] for w in rig.writes() if w["line"].get("type") == "control_response"]
    assert len(responses) == 1
    assert responses[0]["response"]["response"]["updatedInput"]["answers"] == {"Which size?": "Large"}
    assert st.get(card)["status"] == "resumed" and sent == []
    # Again (a watchdog beat, a second device): no second control response.
    R.fire_resume(st.get(card), st)
    assert headless_switch.request("qa-demo", {"cmd": "resolve", "request_id": "x", "card_id": card})["ok"] is False
    time.sleep(0.3)
    assert len([w for w in rig.writes() if w["line"].get("type") == "control_response"]) == 1


def test_a_request_answered_elsewhere_withdraws_its_card(rig):
    rig.env["HEADLESS_CARDS"] = "1"
    rig.env["APPROVAL_DB_PATH"] = str(rig.orch / "state" / "tasks.db")
    rig.start()
    HS.send("qa-demo", "ASK me a size", "w1", orch_dir=rig.orch)
    wait_for(lambda: (HS.status("qa-demo", orch_dir=rig.orch)["held_requests"] or [{}])[0].get("card_id"),
             what="card filed")
    h = HS.status("qa-demo", orch_dir=rig.orch)["held_requests"][0]
    resp = {"behavior": "allow", "updatedInput": {"questions": [], "answers": {"Which size?": "Small"}}}
    assert HS.request("qa-demo", {"cmd": "answer", "request_id": h["request_id"], "response": resp},
                      orch_dir=rig.orch)["ok"]
    wait_for(lambda: _ledger(rig).get(h["card_id"])["status"] == "resolved_elsewhere", what="withdrawn")


def test_a_restarted_supervisor_withdraws_the_seats_old_cards(rig):
    rig.env["HEADLESS_CARDS"] = "1"
    rig.env["APPROVAL_DB_PATH"] = str(rig.orch / "state" / "tasks.db")
    import headless_cards
    st = _ledger(rig)
    old = headless_cards.file_card("qa-demo", "qa-demo", "gone-1", {"subtype": "can_use_tool",
                                   "tool_name": "Bash", "input": {"command": "ls"}}, store=st,
                                   notify=lambda *a, **k: None)
    rig.start()
    wait_for(lambda: st.get(old)["status"] == "resolved_elsewhere", what="old card withdrawn")


def _starts(rig, at_least=1):
    """The fake CLI's start records, oldest first (waits for `at_least`: a start is recorded a moment after
    the supervisor already reports its process)."""
    def read():
        out = []
        for p in sorted(rig.state.glob("start-*.json"), key=lambda p: p.stat().st_mtime):
            try:
                out.append(json.loads(p.read_text()))
            except ValueError:
                return None                              # being written
        return out if len(out) >= at_least else None
    return wait_for(read, what=f"{at_least} start record(s)")


def test_set_model_through_the_cli_control_request_keeps_the_process(rig):
    rig.env["FAKE_SET_MODEL"] = "ok"
    rig.start()
    pid = HS.status("qa-demo", orch_dir=rig.orch)["pid"]
    out = HS.request("qa-demo", {"cmd": "set_model", "model": "sample-model-2"}, orch_dir=rig.orch)
    assert out == {"ok": True, "model": "sample-model-2", "how": "set_model"}
    st = HS.status("qa-demo", orch_dir=rig.orch)
    assert st["pid"] == pid and st["model_arg"] == "sample-model-2" and st["restarts"] == 0
    # A later restart starts on the new model.
    HS.request("qa-demo", {"cmd": "restart"}, orch_dir=rig.orch)
    a = _starts(rig, 2)[-1]["argv"]
    assert a[a.index("--model") + 1] == "sample-model-2"


def test_set_model_falls_back_to_a_restart_when_the_cli_refuses(rig):
    rig.start()
    out = HS.request("qa-demo", {"cmd": "set_model", "model": "sample-model-3"}, orch_dir=rig.orch)
    assert out["ok"] and out["how"] == "restart" and "Unsupported" in out["why"]
    wait_for(lambda: HS.status("qa-demo", orch_dir=rig.orch)["generation"] == 2
             and HS.status("qa-demo", orch_dir=rig.orch)["state"] == "idle", what="restarted on the new model")
    a = _starts(rig, 2)[-1]["argv"]
    assert a[a.index("--model") + 1] == "sample-model-3"
    assert a[a.index("--resume") + 1] == SID                         # the same session, resumed


def test_set_model_waits_for_the_turn_to_end(rig):
    rig.env["FAKE_SET_MODEL"] = "ok"
    rig.start()
    HS.send("qa-demo", "Sample HANG", "hm1", orch_dir=rig.orch)
    wait_for(lambda: HS.status("qa-demo", orch_dir=rig.orch)["state"] == "working", what="working")
    out = HS.request("qa-demo", {"cmd": "set_model", "model": "sample-model-2"}, orch_dir=rig.orch)
    assert out == {"ok": False, "reason": "busy", "state": "working"}


def test_status_names_the_pane_id_and_the_child_has_the_digest_session(rig):
    rig.start()
    assert HS.status("qa-demo", orch_dir=rig.orch)["pane_id"] == "%99"
    assert _starts(rig)[0]["env"]["AQD_TMUX_SESSION"] == "qa-demo"


def test_no_pane_gets_a_synthetic_pane_id(rig, tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "tmux").write_text("#!/bin/sh\nexit 1\n")              # no tmux server to ask
    (fake_bin / "tmux").chmod(0o755)
    rig.env.pop("TMUX_PANE", None)
    rig.env["PATH"] = f"{fake_bin}:{rig.env.get('PATH', '')}"
    rig.start()
    assert HS.status("qa-demo", orch_dir=rig.orch)["pane_id"] == "%hl-qa-demo"
    assert _starts(rig)[0]["env"]["TMUX_PANE"] == "%hl-qa-demo"
    assert SS.synthetic_pane_id("a b/c") == "%hl-a_b_c"


@pytest.fixture
def tty_rig(rig, tmp_path):
    """The supervisor on a pseudo-terminal of its own (as in a pane), and a tmux that records keys."""
    import pty
    fake_bin = tmp_path / "ttybin"
    fake_bin.mkdir()
    (fake_bin / "tmux").write_text(f"#!/bin/sh\necho \"$*\" >> {rig.state}/tmux-keys\n")
    (fake_bin / "tmux").chmod(0o755)
    rig.env["PATH"] = f"{fake_bin}:{rig.env.get('PATH', '')}"
    master, slave = pty.openpty()

    def as_pane():
        import fcntl
        import termios
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    procs = []

    def start():
        p = subprocess.Popen([sys.executable, str(HERE / "seat_supervisor.py"), "run", "qa-demo",
                              "--claude-bin", str(rig.fake), "--no-settings"],
                             env=rig.env, stdin=slave, stdout=slave, stderr=slave, preexec_fn=as_pane)
        procs.append(p)
        wait_for(lambda: _status(rig.orch) is not None, what="supervisor socket")
        return p
    rig.start_tty = start
    yield rig
    for p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
    os.close(master)
    os.close(slave)


def test_dev_round_trip_keeps_the_environment_and_resumes_headless(tty_rig):
    r = tty_rig
    r.start_tty()
    HS.set_switch("qa-demo", True, runtime="claude", orch_dir=r.orch)
    wait_for(lambda: HS.status("qa-demo", orch_dir=r.orch)["state"] == "idle", what="idle")
    head = _starts(r)[0]
    out = HS.request("qa-demo", {"cmd": "dev"}, orch_dir=r.orch, timeout=20)
    assert out["ok"] and out["tui_pid"]
    st = HS.status("qa-demo", orch_dir=r.orch)
    assert st["state"] == "dev" and st["dev"] and st["pid"] is None
    # Every reader sees a TUI seat now; the route still finds it.
    assert HS.headless_seat("qa-demo", orch_dir=r.orch) is None
    assert HS.headless_seat("qa-demo", orch_dir=r.orch, include_dev=True) == "qa-demo"
    assert HS.send("qa-demo", "Sample in dev", "d1", orch_dir=r.orch) == {"ok": False, "reason": "dev"}
    tui = _starts(r, 2)[1]
    assert "-p" not in tui["argv"] and tui["argv"][tui["argv"].index("--resume") + 1] == SID
    assert tui["env"] == head["env"]                                  # the same environment, TMUX_PANE included
    assert tui["env"]["TMUX_PANE"] == "%99"
    back = HS.request("qa-demo", {"cmd": "chat"}, orch_dir=r.orch, timeout=40)
    assert back["ok"] and back["state"] == "idle" and back["exit_ms"] >= 0
    keys = (r.state / "tmux-keys").read_text()
    assert "send-keys -t %99 -l /exit" in keys and "-H 0d" in keys
    st = HS.status("qa-demo", orch_dir=r.orch)
    assert st["dev"] is False and st["generation"] == 2 and st["restarts"] == 0
    assert HS.headless_seat("qa-demo", orch_dir=r.orch) == "qa-demo"
    assert _starts(r, 3)[-1]["env"] == head["env"] and "-p" in _starts(r, 3)[-1]["argv"]
    HS.send("qa-demo", "Sample after dev", "d2", orch_dir=r.orch)
    wait_for(lambda: msg_state(r, "d2") == "answered", what="headless again")


def test_leaving_the_tui_in_the_terminal_also_returns_to_headless(tty_rig):
    r = tty_rig
    r.start_tty()
    wait_for(lambda: HS.status("qa-demo", orch_dir=r.orch)["state"] == "idle", what="idle")
    assert HS.request("qa-demo", {"cmd": "dev"}, orch_dir=r.orch, timeout=20)["ok"]
    (r.state / "tui-exit").write_text("1")
    wait_for(lambda: HS.status("qa-demo", orch_dir=r.orch)["state"] == "idle"
             and HS.status("qa-demo", orch_dir=r.orch)["generation"] == 2, timeout=20, what="headless again")
    assert not (HS.seat_dir("qa-demo", r.orch) / "dev").exists()


def test_dev_only_from_idle_and_only_in_a_pane(tty_rig, rig):
    r = tty_rig
    r.start_tty()
    HS.send("qa-demo", "Sample HANG", "b1", orch_dir=r.orch)
    wait_for(lambda: HS.status("qa-demo", orch_dir=r.orch)["state"] == "working", what="working")
    assert HS.request("qa-demo", {"cmd": "dev"}, orch_dir=r.orch) == {"ok": False, "reason": "busy",
                                                                       "state": "working"}


def test_dev_needs_a_terminal(rig):
    rig.start()                                  # stdin is not a terminal
    out = HS.request("qa-demo", {"cmd": "dev"}, orch_dir=rig.orch)
    assert out["ok"] is False and "pane" in out["reason"]
    assert HS.request("qa-demo", {"cmd": "chat"}, orch_dir=rig.orch) == {"ok": True, "already": True}
