"""A headless seat's rotation (scripts/headless_rotation.py, run by scripts/seat_supervisor.py).

The steps on their own (settings, the boundary rule, export + verify on a temp index, the handoff from records, the
gate's grading, the warm process's fence, the warm-up mark, the identity store's swap), then the supervisor end to
end: a real supervisor subprocess, a fake `claude` that speaks stream-json, and fake steps for the index, the gate
and the identity store (written by this file into a temp folder). Nothing touches tmux, a real seat, a real CLI, the
live index, the approvals ledger or the live identity store."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)
import headless_rotation as HR  # noqa: E402
import headless_switch as HS  # noqa: E402

SEAT = "qa-demo"
SID = "00000000-0000-4000-8000-0000000000a1"


# --- settings and the boundary -------------------------------------------------------------------------

def test_rotation_is_off_by_default_and_the_numbers_come_from_the_entry_or_the_env(tmp_path):
    (tmp_path / "state").mkdir()
    conf = HR.settings(SEAT, tmp_path, env={})
    assert conf["enabled"] is False and conf["at"] == 75.0 and conf["ceiling"] == 90.0
    assert HR.due(99.0, conf) is None, "off: never automatic, even over the ceiling"
    assert HR.due(None, conf, manual=True) == "manual"
    HS.set_option(SEAT, "rotation", True, orch_dir=tmp_path)
    HS.set_option(SEAT, "rotation_at", 60, orch_dir=tmp_path)
    conf = HR.settings(SEAT, tmp_path, env={})
    assert conf["enabled"] and conf["at"] == 60.0
    assert [HR.due(p, conf) for p in (59.9, 60, 89, 90, None)] == [None, "threshold", "threshold", "ceiling", None]
    conf = HR.settings(SEAT, tmp_path, env={"HEADLESS_ROTATE_AT": "5", "HEADLESS_ROTATE_CEILING": "7"})
    assert conf["at"] == 5.0 and conf["ceiling"] == 7.0
    HS.set_option(SEAT, "rotation", "yes", orch_dir=tmp_path)
    assert HR.settings(SEAT, tmp_path, env={})["enabled"] is False, "only a literal true turns it on"


def test_a_boundary_is_idle_with_nothing_held_and_nothing_on_its_way():
    assert HR.at_boundary("idle", False, 0, ["answered", "interrupted", "failed"])
    assert not HR.at_boundary("working", True, 0, [])
    assert not HR.at_boundary("idle", True, 0, [])
    assert not HR.at_boundary("waiting", False, 1, []), "a question card waiting"
    for s in ("accepted", "queued", "delivered", "unknown"):
        assert not HR.at_boundary("idle", False, 0, [s]), s
    assert HR.context_pct(150, 1000) == 15.0 and HR.context_pct(None, 1000) is None


def test_the_fault_switch_is_test_only(tmp_path):
    (tmp_path / "rotation-fault").write_text("grade\n")
    assert HR.fault(tmp_path, env={}) is None, "a stray file on a real seat does nothing"
    assert HR.fault(tmp_path, env={"HEADLESS_ROTATION_TEST": "1"}) == "grade"
    assert HR.fault(None, env={"HEADLESS_ROTATION_FAULT": "verify"}) == "verify"
    assert HR.fault(None, env={"HEADLESS_ROTATION_FAULT": "anything"}) is None


# --- a transcript to read ------------------------------------------------------------------------------

def _line(kind, uuid, content, ts="2026-10-31T04:00:00Z"):
    return {"type": kind, "uuid": uuid, "timestamp": ts, "sessionId": SID,
            "message": {"role": kind, "content": content}}


def transcript(tmp_path) -> Path:
    rows = [
        _line("user", "u-1", "Please build the Example report and keep the Sample tests green."),
        _line("assistant", "a-1", [{"type": "tool_use", "id": "toolu_todo1", "name": "TodoWrite", "input": {"todos": [
            {"content": "Write the Example report", "status": "in_progress"},
            {"content": "Run the Sample tests", "status": "pending"},
            {"content": "Read the brief", "status": "completed"}]}}]),
        _line("assistant", "a-2", [{"type": "tool_use", "id": "toolu_task1", "name": "TaskCreate",
                                    "input": {"taskId": "7", "subject": "Ask about the deadline"}}]),
        _line("assistant", "a-3", [{"type": "tool_use", "id": "toolu_write1", "name": "Write",
                                    "input": {"file_path": "/work/example/report.md", "content": "x"}}]),
        _line("assistant", "a-4", [{"type": "tool_use", "id": "toolu_ask1", "name": "AskUserQuestion", "input": {
            "questions": [{"question": "Which format?", "options": [{"label": "Markdown"}, {"label": "PDF"}]}]}}]),
        _line("user", "u-2", [{"type": "tool_result", "tool_use_id": "toolu_ask1",
                               "content": "Your questions have been answered: \"Which format?\"=\"Markdown\"."}]),
        _line("user", "u-3", "[DECISION ANSWERED] keep the quarterly totals in the appendix"),
        _line("user", "u-4", "Also put the zebra-crossing chart on page two, scaled to the column width, with the "
                             "quarterly totals from the appendix labelled underneath each bar."),
        _line("assistant", "a-5", [{"type": "text", "text": "Done: the zebra-crossing chart is on page two of the "
                                    "report, scaled to the column width, and every bar carries its quarterly total "
                                    "from the appendix underneath."}]),
        # The last exchange is trivial: a canary pointing here could never be graded.
        _line("user", "u-5", "Test only: reply with exactly the word ALIVE."),
        _line("assistant", "a-6", [{"type": "text", "text": "ALIVE"}]),
    ]
    p = tmp_path / f"{SID}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_the_handoff_is_built_from_records_and_every_item_cites_its_id(tmp_path):
    lines = HR.read_lines(transcript(tmp_path))
    h, r = HR.build_handoff(SEAT, lines, cards=[{"card_id": "apr_sample1", "text": "Approve the Sample spend?"}])
    from lineage_daemon.handoff_schema import Handoff
    assert isinstance(h, Handoff)
    assert [t["text"] for t in r["open_tasks"]] == ["Write the Example report", "Run the Sample tests",
                                                    "Ask about the deadline"], "completed todos are not open"
    assert all("[tool toolu_" in loop for loop in h.open_loops)
    assert h.file_roots_touched == ["/work/example/report.md (Write) [uuid a-3] [tool toolu_write1]"]
    assert [d["tool_id"] or d["uuid"] for d in h.decisions] == ["toolu_ask1", "u-3"]
    assert "Markdown" in h.decisions[0]["text"]
    assert h.hazards == ["pending card apr_sample1: Approve the Sample spend?"]
    assert h.current_goal.endswith("[uuid u-5]")
    assert "[uuid a-6]" in h.working_state
    assert "orchestra memory search" in h.next_3_actions[0] and f"--agent {SEAT}" in h.next_3_actions[0]
    msg = HR.handoff_message(SEAT, h, 3, 4)
    assert "orchestra memory search" in msg and "generation 4" in msg and "canary" not in msg


def test_export_indexes_the_session_and_finds_its_last_turn(tmp_path):
    import episodic_memory as em
    path = transcript(tmp_path)
    db = tmp_path / "idx.db"
    out = HR.export_session(SEAT, SID, 3, path, db_path=db)
    assert out["ok"] and out["added"] > 0 and out["token"] == "ALIVE", "the export check reads the last turn"
    assert out["row"]["uuid"] == "a-6" and out["row"]["generation"] == 3
    rows = em.query("zebra-crossing", agent=SEAT, db_path=db)
    assert rows and rows[0]["session_id"] == SID
    assert HR.export_session(SEAT, SID, 3, path, db_path=db, force_fail=True)["ok"] is False

    class Missing:
        ingest_session = staticmethod(lambda *a, **k: {"added": 0})
        verify = staticmethod(lambda *a, **k: (False, None))
    bad = HR.export_session(SEAT, SID, 3, path, em=Missing)
    assert bad["ok"] is False and "not in the index" in bad["reason"]


def test_the_canary_points_only_at_rows_with_substance_never_the_trivial_last_turn(tmp_path):
    import rotation_gate_manual as gate
    path = transcript(tmp_path)
    lines = HR.read_lines(path)
    _, r = HR.build_handoff(SEAT, lines)
    qs = HR.canary_questions(lines, r, SEAT)
    assert len(qs) == HR.CANARY_N
    trivial = {n for n, o in enumerate(HR.text_turns(lines), 1) if o.get("uuid") in ("u-5", "a-6")}
    for q in qs:
        n = int(q["source_pointer"].split("-")[-1])
        assert n not in trivial, q
        region = gate.resolve_pointer(path, q["source_pointer"])
        assert len(region) >= HR.MIN_REGION_CHARS and len(HR._terms(region)) >= HR.MIN_REGION_TERMS, (q, region)
        assert f"orchestra memory search" in q["q"] and f"--agent {SEAT}" in q["q"], "answered by looking up"
        assert not gate.pointer_discloses(q["q"], q["source_pointer"])
    gate_ok = [gate._POINTER_FORM.match(q["source_pointer"]) for q in qs]
    assert all(gate_ok)
    # A session with no substance has no canary at all (and so no pass): never the one-word turn.
    tiny = [_line("user", "t-1", "Reply ALIVE."), _line("assistant", "t-2", [{"type": "text", "text": "ALIVE"}])]
    assert HR.canary_questions(tiny, HR.records(tiny), SEAT) == []
    assert not HR.gradeable(_line("assistant", "x", [{"type": "text", "text": "Okay."}]))


def test_every_pointer_resolves_through_the_gate_to_its_own_row_with_other_rows_and_late_messages(tmp_path):
    import rotation_gate_manual as gate
    path = transcript(tmp_path)
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    # The rows a real session interleaves: the gate counts every row with text (a top-level `content` too), so a
    # builder that counts only message rows points at the wrong row from the first of these on.
    noise = [{"type": "system", "subtype": "local_command", "content": "Sample system note about the session"},
             {"type": "attachment", "content": "queued: Sample note typed while busy"},
             {"type": "file-history-snapshot", "snapshot": {"files": []}}]
    mixed = [rows[0], *noise, *rows[1:5], noise[0], *rows[5:]]
    path.write_text("\n".join(json.dumps(r) for r in mixed) + "\n")
    lines = HR.read_lines(path)
    _, r = HR.build_handoff(SEAT, lines)
    qs = HR.canary_questions(lines, r, SEAT, resolve=lambda ptr: gate.resolve_pointer(path, ptr))
    assert len(qs) == HR.CANARY_N
    # Messages that reach the old session during the warm-up are appended: earlier turn numbers never move.
    with open(path, "a") as fh:
        fh.write(json.dumps(_line("user", "late-1", "Sample message sent during the warm-up, about nothing")) + "\n")
        fh.write(json.dumps({"type": "system", "content": "Sample late system row"}) + "\n")
    for q in qs:
        word = q["q"].split('memory search "')[1].split('"')[0]
        region = gate.resolve_pointer(path, q["source_pointer"])
        assert word in region.lower(), (q["source_pointer"], word, region[:120])
        assert len(region) >= HR.MIN_REGION_CHARS
    # A pointer the gate would resolve to another row is never kept.
    assert HR.canary_questions(lines, r, SEAT, resolve=lambda ptr: "something else") == []


def test_the_check_asks_for_the_entries_quoted_after_a_search():
    msg = HR.grading_message([{"q": "Look up, with `orchestra memory search \"harbour\" --agent qa-demo`, the entry.",
                               "source_pointer": "jsonl:turn-3"}])
    assert "never answer from recall" in msg and "word for word" in msg and "## Q1" in msg and "## Readback" in msg
    assert "transcript" in msg, "a cut-short snippet: read the whole entry by its uuid"


def test_grading_uses_the_existing_gate_and_fails_closed(tmp_path, monkeypatch):
    import rotation_gate_manual as gate
    monkeypatch.setattr(gate, "HANDOFFS_DIR", tmp_path / "handoffs")
    path = transcript(tmp_path)
    lines = HR.read_lines(path)
    _, r = HR.build_handoff(SEAT, lines)
    qs = HR.canary_questions(lines, r, SEAT)
    truth = HR.readback_truth(r, [])
    # An answer from the entry each search finds (here: the region itself, as the session would restate it).
    good = "".join(f"## Q{i}\nThe entry said: {gate.resolve_pointer(path, q['source_pointer'])}\n"
                   for i, q in enumerate(qs, 1))
    good += "## Readback\nOpen: Write the Example report; Run the Sample tests; Ask about the deadline."
    succ = HR.successor_id(SEAT, 4)
    assert HR.grade(succ, qs, good, truth, path)["pass"] is True
    assert json.loads((tmp_path / "handoffs" / f"{succ}.comprehension.json").read_text())["pass"] is True
    assert HR.grade(succ, qs, good, truth, path, force_fail=True)["pass"] is False
    bad = "".join(f"## Q{i}\nno idea\n" for i in range(1, len(qs) + 1)) + "## Readback\nnothing"
    assert HR.grade(succ, qs, bad, truth, path)["pass"] is False
    assert HR.grade(succ, qs, "just words, no headings", truth, path)["pass"] is False
    assert HR.grade(succ, [], good, truth, path)["pass"] is False, "no canary: never a pass"


def test_the_warm_process_is_fenced_and_runs_a_new_session_with_the_same_flags():
    env = {"TMUX": "/tmp/tmux-1/default,1,0", "TMUX_PANE": "%7", "AQD_TMUX_SESSION": SEAT, "ORCHESTRA_DIR": "/d"}
    w = HR.warm_env(env, SEAT)
    assert "TMUX" not in w and "TMUX_PANE" not in w and w["ORCHESTRA_DIR"] == "/d"
    assert w["AQD_TMUX_SESSION"] == f"headless-warmup.{SEAT}" and w["ORCH_SELF_ROTATE"] == "off"
    argv = ["claude", "-p", "--model", "m", "--settings", "s.json", "--resume", SID]
    assert HR.fresh_argv(argv) == ["claude", "-p", "--model", "m", "--settings", "s.json"]


def test_the_warmup_mark_is_merged_per_session(tmp_path):
    HR.write_warmup_mark(tmp_path, "s-new", uuids=["h1"])
    HR.write_warmup_mark(tmp_path, "s-new", uuids=["h1", "g1"], through="a9")
    HR.write_warmup_mark(tmp_path, "s-other", uuids=["x"])
    doc = json.loads((tmp_path / "warmup.json").read_text())
    assert doc["sessions"]["s-new"] == {"uuids": ["h1", "g1"], "through": "a9"}
    assert doc["sessions"]["s-other"] == {"uuids": ["x"]}


def _store(tmp_path):
    db = tmp_path / "state" / "orchestra-registry.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT);
                       CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT);
                       INSERT INTO generations VALUES (1, 'qa-demo', 6, 'old-a'), (2, 'qa-demo', 2, 'cur');
                       INSERT INTO canonical VALUES ('qa-demo', 2, 'qa-demo');""")
    c.commit()
    c.close()


def test_promote_mints_the_next_generation_in_one_swap(tmp_path):
    _store(tmp_path)
    calls = []

    class Writer:
        @staticmethod
        def swap_generation(od, root, green, sync_effects_owner=False):
            calls.append(("swap", root, green, sync_effects_owner))
            return True

        @staticmethod
        def project_now(od):
            calls.append(("project",))
            return True
    gen = HR.promote_identity(tmp_path, SEAT, "new-sid", model="sample-model", conversation_path="/x.jsonl",
                              writer=Writer)
    assert gen == 7, "max(generation)+1: numbers reset, so never the canonical's number + 1"
    assert calls[0][1] == "qa-demo" and calls[0][2]["session_id"] == "new-sid" and calls[0][3] is True
    assert calls[-1] == ("project",)

    class Off(Writer):
        swap_generation = staticmethod(lambda *a, **k: False)
    with pytest.raises(HR.PromoteRefused, match="cutover"):
        HR.promote_identity(tmp_path, SEAT, "new-sid", writer=Off)
    with pytest.raises(HR.PromoteRefused, match="no lineage"):
        HR.promote_identity(tmp_path, "zz-none", "new-sid", writer=Writer)


# --- the supervisor end to end (Release 1: briefing mode) ----------------------------------------------

FAKE = r'''#!__PY__
"""A stand-in for `claude -p` (stream-json): --session-id names a new conversation, --resume an old one,
--no-session-persistence is the config probe. One init per process at its first input, then one reply per message."""
import json, os, sys, time, uuid
state = os.environ["FAKE_STATE"]
args = sys.argv[1:]
probe = "--no-session-persistence" in args
sid = (args[args.index("--resume") + 1] if "--resume" in args else
       args[args.index("--session-id") + 1] if "--session-id" in args else str(uuid.uuid4()))
pid = os.getpid()
if "--resume" in args and not os.path.exists(os.path.join(state, "turned-" + sid)):
    sys.stderr.write("No conversation found with session ID: %s\n" % sid)
    sys.exit(1)                                       # the CLI writes a session's file only once a turn ran
pfile = args[args.index("--append-system-prompt-file") + 1] if "--append-system-prompt-file" in args else None
prompt = open(pfile).read() if pfile else None
with open(os.path.join(state, "start-%d.json" % pid), "w") as fh:
    json.dump({"pid": pid, "argv": args, "sid": sid, "probe": probe, "prompt": prompt, "env": {k: os.environ.get(k) for k in
               ("TMUX_PANE", "TMUX", "AQD_TMUX_SESSION", "ORCH_SELF_ROTATE", "DISABLE_AUTO_COMPACT")}}, fh)
tokens = int(open(os.path.join(state, "ctx")).read()) if os.path.exists(os.path.join(state, "ctx")) else 100
model = "sample-model"
if probe and os.path.exists(os.path.join(state, "probe-model")):
    model = open(os.path.join(state, "probe-model")).read().strip()
once = os.path.join(state, "successor-model")
if "--session-id" in args and not probe and os.path.exists(once):
    model = open(once).read().strip()
    os.unlink(once)                                   # only the first new conversation differs
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
t0 = time.monotonic()
try:
    mcp = json.load(open(os.path.join(state, "mcp.json")))
except (OSError, ValueError):
    mcp = {"servers": []}
fresh = probe or "--session-id" in args
def mcp_now():
    up = time.monotonic() - t0
    return [{"name": x["name"], "status": x.get("status") or (
        "pending" if (mcp.get("never") and fresh) or up < float(x.get("delay", 0)) else "connected")}
            for x in mcp["servers"]]
inited = False
for line in sys.stdin:
    if not line.strip():
        continue
    o = json.loads(line)
    with open(os.path.join(state, "stdin.jsonl"), "a") as fh:
        fh.write(json.dumps({"pid": pid, "sid": sid, "probe": probe, "line": o}) + "\n")
    if o.get("type") == "control_request":
        if o["request"].get("subtype") == "mcp_status" and not mcp.get("no_status"):
            out({"type": "control_response", "response": {"subtype": "success", "request_id": o["request_id"],
                 "response": {"mcpServers": mcp_now()}}})
        elif o["request"].get("subtype") == "mcp_status":
            out({"type": "control_response", "response": {"subtype": "error", "request_id": o["request_id"],
                 "error": "Unsupported control request subtype: mcp_status"}})
        else:
            out({"type": "control_response", "response": {"subtype": "success", "request_id": o["request_id"]}})
        continue
    if o.get("type") != "user":
        continue
    text = o["message"]["content"]
    if not probe:
        open(os.path.join(state, "turned-" + sid), "w").close()
    if not inited:
        inited = True
        servers = mcp_now()
        tools = ["Bash", "Read"] + [f"mcp__{x['name'].replace(' ', '_')}__read" for x in servers
                                    if x["status"] == "connected"]
        out({"type": "system", "subtype": "init", "session_id": sid, "model": model, "cwd": os.getcwd(),
             "tools": tools, "permissionMode": "bypassPermissions", "mcp_servers": servers,
             "slash_commands": [], "skills": [], "agents": [], "output_style": "default"})
    out({"type": "user", "message": {"role": "user", "content": text}, "session_id": sid,
         "uuid": o.get("uuid") or str(uuid.uuid4()), "isReplay": True})
    crash = os.path.join(state, "crashed")
    if "CRASH-ONCE" in text and "--session-id" in args and not probe and not os.path.exists(crash):
        open(crash, "w").close()
        os._exit(1)
    if "SLOW" in text:
        time.sleep(2)
    out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "re"}}, "session_id": sid, "parent_tool_use_id": None})
    answer = "reply to: " + text[-40:]
    if probe and prompt is not None:                  # the fork answers its check from its startup prompt
        answer = "I don't know." if os.path.exists(os.path.join(state, "fork-forget")) else prompt
        af = os.path.join(state, "fork-answers.json")
        if answer == prompt and os.path.exists(af):
            answer = "\n".join(f"## Q{i}\n{a}" for i, a in enumerate(json.load(open(af)), 1))
    out({"type": "assistant", "parent_tool_use_id": None, "uuid": str(uuid.uuid4()), "session_id": sid,
         "message": {"content": [{"type": "text", "text": answer}],
                     "usage": {"input_tokens": tokens, "output_tokens": 0}}})
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": sid,
         "usage": {"input_tokens": tokens, "output_tokens": 5},
         "modelUsage": {"sample-model": {"inputTokens": tokens, "contextWindow": 1000}}})
'''

RUNNER = r'''
import asyncio, json, os, sys, time, types
sys.path.insert(0, os.environ["SCRIPTS"])
state = os.environ["FAKE_STATE"]

# the seat's settings, from a file the test writes (seat_settings.rotation_settings stand-in)
ss = types.ModuleType("seat_settings")
def rotation_settings(seat):
    try:
        return json.load(open(os.path.join(state, "settings.json")))
    except (OSError, ValueError):
        return {}
ss.rotation_settings = rotation_settings
sys.modules["seat_settings"] = ss
import seat_supervisor as SS

def note(**k):
    with open(os.path.join(state, "calls.jsonl"), "a") as fh:
        fh.write(json.dumps({"t": time.time(), **k}) + "\n")

class Store:
    """The contract's store functions, recorded."""
    n = 0
    def notice_start(self, agent, from_session, before_tokens, trigger="auto"):
        Store.n += 1
        note(call="notice_start", agent=agent, from_session=from_session, before=before_tokens, trigger=trigger)
        return Store.n
    a = 0
    def record_attempt(self, agent, trigger, outcome, reason, notice_id=None, from_session=None, to_session=None):
        Store.a += 1
        note(call="record_attempt", id=Store.a, trigger=trigger, outcome=outcome, reason=reason)
        return Store.a
    def update_attempt(self, aid, outcome, reason=None, to_session=None):
        note(call="update_attempt", id=aid, outcome=outcome, reason=reason)
    def notice_finish(self, nid, st, to_session=None, after_tokens=None):
        note(call="notice_finish", id=nid, state=st, to_session=to_session)
    def notice_set_tokens(self, nid, after_tokens):
        note(call="notice_set_tokens", id=nid, after=after_tokens)
    active = None
    def register_session(self, agent, runtime, model, sid, generation, seed_mode, *, state="active", reason=None):
        note(call="register_session", sid=sid, generation=generation, seed_mode=seed_mode, runtime=runtime,
             state=state, reason=reason)
        if state == "active":
            Store.active = sid
    def active_session(self, agent):
        time.sleep(float(os.environ.get("FAKE_REGISTER_S", "0")))      # a slow store
        if os.path.exists(os.path.join(state, "foreign-active")):
            return {"provider_session_id": "someone-else"}
        return {"provider_session_id": Store.active} if Store.active else None
    def ingest_tick(self, agent):
        note(call="ingest_tick", active=Store.active)
        time.sleep(float(os.environ.get("FAKE_INGEST_S", "0")))         # a slow save never holds a turn up
        return {"added": 1, "lag_bytes": 0}
    def generation_of(self, sid):
        return _gens().get(sid, 1)
    def set_session_cost(self, sid, prompt_chars=None, cost_tokens=None):
        note(call="set_session_cost", sid=sid, prompt_chars=prompt_chars, cost_tokens=cost_tokens)
    def set_session_state(self, sid, st, reason=None):
        note(call="set_session_state", sid=sid, state=st, reason=reason)
    def mark_seen_only(self, sid, uuid):
        note(call="mark_seen_only", sid=sid, uuid=uuid)

def _gens():
    try:
        return json.load(open(os.path.join(state, "generations.json")))
    except (OSError, ValueError):
        return {}

class Steps:
    def transcript(self, sid):
        if os.environ.get("FAKE_TRANSCRIPT_ALWAYS") == "1" or os.path.exists(os.path.join(state, "turned-" + sid)):
            return "/fake/%s.jsonl" % sid
        return None
    def export(self, seat, sid, path, force_fail=False, assume_saved=False):
        note(call="export", sid=sid, force_fail=force_fail, assume_saved=assume_saved)
        turned = os.path.exists(os.path.join(state, "turned-" + sid))
        if assume_saved and not turned:
            return {"ok": True, "added": 0, "token": None, "saved": "promoted here and no turn since"}
        if not turned:                                 # the real export: nothing in the last turn to find
            return {"ok": False, "added": 0, "token": None, "reason": "no text in the last turn"}
        if os.path.exists(os.path.join(state, "foreign")):
            return {"ok": False, "foreign": True, "active": "someone-else", "added": 0, "token": None}
        return {"ok": not force_fail, "added": 3, "token": "Sample", **({"reason": "forced"} if force_fail else {})}
    def packet(self, seat, window):
        text = ("<earlier-conversation>\nSAMPLE BLOCK for " + seat + "\n[o1] user: build it\n[a1] assistant: on it"
                "\n- [toolu_w9] Write ok file /w/title.md\n- task [toolu_t1] the Sample task\n</earlier-conversation>")
        qs = [{"kind": "last_message", "q": "Quote my last message to you, word for word.",
               "expected": "Sample last words", "id": "o1"},
              {"kind": "card", "q": "What is the id of the pending approval card?", "expected": "apr_sample1",
               "id": "apr_sample1"}]
        n = os.path.join(state, "check-qs")
        if os.path.exists(n):
            qs = qs[:int(open(n).read())]
        answers = ["\u201cSample last words.\u201d", "It is apr_sample1."]
        if os.path.exists(os.path.join(state, "fork-wrong-card")):
            answers[1] = "It is apr_other9."
        json.dump(answers, open(os.path.join(state, "fork-answers.json"), "w"))
        return {"text": text, "chars": len(text), "tokens_est": len(text) // 4, "counts": {}, "trimmed": {},
                "check": {"questions": qs}}
    def promote(self, seat, sid, model=None):
        g = _gens(); new = max([1, *g.values()]) + 1; g[sid] = new
        json.dump(g, open(os.path.join(state, "generations.json"), "w"))
        note(call="promote", sid=sid, generation=new)
        return new
    def verify_bound(self, seat, sid):
        time.sleep(float(os.environ.get("FAKE_BIND_S", "0")))
        return {"db": _gens().get(sid) is not None, "json": {}, "ok": True}

if os.environ.get("NOSLOT") == "1":                  # a runtime with no startup slot: the fallback delivery
    import rotation_adapters.claude as RC
    RC.ClaudeAdapter.startup_slot = None
orch, seat = os.environ["ORCHESTRA_DIR"], os.environ["SEAT"]
try:                                                  # after a restart: the seat's own saved conversation
    sid0 = json.load(open(os.path.join(str(SS.HS.seat_dir(seat, orch)), "seat.json")))["session_id"]
except (OSError, ValueError, KeyError):
    sid0 = os.environ["SID"]
env = SS.seat_env(os.environ, orch, "%99", session=seat)
sup = SS.Supervisor(seat, env=env, cwd=os.environ["WORK"], home=SS.HS.seat_dir(seat, orch),
                    session_id=sid0, runtime=os.environ.get("RUNTIME", "claude"),
                    argv_for=lambda s, m=None: [os.environ["FAKE_BIN"], "-p", "--model", "sample-model", "--resume", s],
                    orch_dir=orch, rotation_steps=Steps(), store=Store(), backoff_base=0.2)
sys.exit(asyncio.run(sup.main()))
'''


@pytest.fixture
def rig():
    root = Path(tempfile.mkdtemp(prefix="hr-", dir="/tmp"))     # a unix socket path is limited to ~107 bytes
    orch, work, state = root / "d", root / "w", root / "s"
    for d in (orch / "state", work, state):
        d.mkdir(parents=True)
    (state / f"turned-{SID}").write_text("")              # the seat's own conversation exists on disk
    fake = root / "claude"
    fake.write_text(FAKE.replace("__PY__", sys.executable))
    fake.chmod(0o755)
    runner = root / "run.py"
    runner.write_text(RUNNER)
    env = {**os.environ, "ORCHESTRA_DIR": str(orch), "ORCH_DIR": str(orch), "SCRIPTS": str(HERE), "SEAT": SEAT,
           "FAKE_BIN": str(fake), "FAKE_STATE": str(state), "WORK": str(work), "SID": SID,
           "HEADLESS_CARDS": "0", "TMUX_PANE": "%99", "TMUX": "/tmp/tmux-sample,1,0", "HEADLESS_ROTATE_IDLE_S": "0"}
    for k in ("HEADLESS_ROTATION_FAULT", "HEADLESS_ROTATION_TEST", "HEADLESS_ROTATE_AT"):
        env.pop(k, None)
    procs = []

    def start(**extra):
        p = subprocess.Popen([sys.executable, str(runner)], env={**env, **extra}, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        procs.append(p)
        wait_for(lambda: status() is not None, what="supervisor socket")
        wait_for(lambda: (status() or {}).get("state") == "idle", what="idle")
        return p

    def status():
        try:
            return HS.status(SEAT, orch_dir=orch)
        except HS.SupervisorDown:
            return None

    r = type("Rig", (), {})()
    r.orch, r.state, r.start, r.status = orch, state, start, status
    r.home = HS.seat_dir(SEAT, orch)
    r.settings = lambda **k: (state / "settings.json").write_text(json.dumps(k))
    r.ctx = lambda n: (state / "ctx").write_text(str(n))
    jl = lambda p: [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    r.events = lambda: jl(r.home / "events.jsonl")
    r.calls = lambda: jl(state / "calls.jsonl")
    r.stdin = lambda: jl(state / "stdin.jsonl")
    def starts():
        out = {}
        for p in state.glob("start-*.json"):
            try:
                rec = json.loads(p.read_text())
            except ValueError:
                continue                                  # the fake is still writing it
            out[rec["pid"]] = rec
        return out
    r.starts = starts
    r.send = lambda text, mid, source="mail": HS.send(SEAT, text, mid, orch_dir=orch, source=source)
    r.state_of = lambda mid: HS.message_state(SEAT, mid, orch_dir=orch).get("state")
    yield r
    for p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
    shutil.rmtree(root, ignore_errors=True)


def wait_for(cond, timeout=20.0, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def whats(r):
    return [e["what"] for e in r.events() if e.get("kind") == "sup" and str(e.get("what", "")).startswith("rotate-")]


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def turn(r, text, mid, source="mail"):
    r.send(text, mid, source)
    wait_for(lambda: r.state_of(mid) == "answered", what=mid)


def user_lines(r):
    return [w for w in r.stdin() if w["line"].get("type") == "user" and not w["probe"]]


SWITCH = ["rotate-compact_asked", "rotate-pending", "rotate-attempt", "rotate-export_done",
          "rotate-export_verified", "rotate-packet", "rotate-session_started", "rotate-fork", "rotate-settled",
          "rotate-notice_start", "rotate-bound", "rotate-promote", "rotate-old_closed"]


def test_the_persons_compact_switches_with_the_context_in_the_startup_prompt_checked_by_a_fork(rig):
    rig.settings(auto_compact=False)
    rig.start()
    turn(rig, "Sample before", "m1")
    old = rig.status()
    assert rig.send("/compact", "c1", source="person")["state"] == "intercepted"
    wait_for(lambda: "rotate-old_closed" in whats(rig), what="the switch")
    assert whats(rig)[:len(SWITCH)] == SWITCH
    new = rig.status()
    assert new["session_id"] != old["session_id"] and new["pid"] != old["pid"] and new["state"] == "idle"
    wait_for(lambda: not alive(old["pid"]), what="the old process gone")
    assert not [w for w in rig.stdin() if "/compact" in json.dumps(w["line"])], "the CLI never gets /compact"
    calls = [c["call"] for c in rig.calls() if "attempt" not in c["call"]]
    calls = calls[calls.index("export"):]                     # the start's own registration comes first
    # nothing shows until the checked copy passed and the switch is committed; then compacted at the switch
    assert calls[:calls.index("promote") + 1] == ["export", "register_session", "notice_start", "promote"]
    assert calls[calls.index("promote") + 1:] == ["set_session_state", "register_session", "notice_finish"]
    att = [c for c in rig.calls() if "attempt" in c["call"]]
    assert [(c["call"], c["outcome"]) for c in att] == [("record_attempt", "waiting"), ("update_attempt", "committed")]
    assert att[0]["trigger"] == "manual"
    copy = [c for c in rig.calls() if c["call"] == "register_session" and c.get("reason") == "check copy"][0]
    assert copy["state"] == "failed" and copy["reason"] == "check copy" and copy["sid"] != new["session_id"]
    fin = [c for c in rig.calls() if c["call"] == "notice_finish"][0]
    assert fin["state"] == "compacted" and fin["to_session"] == new["session_id"]
    prompt = rig.home / f"packet-{new['session_id']}.txt"
    assert prompt.exists() and oct(prompt.stat().st_mode & 0o777) == "0o600", "on disk before the switch"
    # the fork: the identical command line and prompt, unsaved, fenced, its check passed, then gone
    wait_for(lambda: new["pid"] in rig.starts(), what="the start record")
    starts = rig.starts()
    fork = [x for x in starts.values() if x["probe"]][0]
    real = starts[new["pid"]]
    assert "--append-system-prompt-file" in real["argv"] and real["argv"][real["argv"].index("--system-prompt-snapshot") + 1] == "on"
    assert fork["argv"] == [a for a in real["argv"] if a not in ("--session-id", new["session_id"])] + \
        ["--no-session-persistence"], "the identical command line"
    assert fork["prompt"] == real["prompt"] and "SAMPLE BLOCK" in real["prompt"], "byte-identical prompts"
    assert fork["env"]["TMUX_PANE"] is None and fork["env"]["AQD_TMUX_SESSION"] == f"headless-probe.{SEAT}"
    assert not alive(fork["pid"])
    check = [e for e in rig.events() if e.get("what") == "rotate-fork"][0]["check"]
    assert check["pass"] and [(r["kind"], r["expected"], r["pass"]) for r in check["results"]] == \
        [("last_message", "Sample last words", True), ("card", "apr_sample1", True)]
    assert not [w for w in rig.stdin() if w["pid"] == new["pid"] and w["line"].get("type") == "user"], \
        "the real conversation never saw the check"
    # its first input is its first turn, with nothing attached (Claude has the startup slot)
    turn(rig, "Sample after", "m2")
    first = [w for w in user_lines(rig) if w["pid"] == new["pid"]]
    assert [w["line"]["message"]["content"] for w in first] == ["Sample after"]
    wait_for(lambda: not prompt.exists(), what="the prompt file deleted after the first turn")
    assert not [c for c in rig.calls() if c["call"] == "mark_seen_only"], "nothing went into the conversation"
    assert [c["call"] for c in rig.calls()][-2:] == ["notice_set_tokens", "set_session_cost"]
    cost = [c for c in rig.calls() if c["call"] == "set_session_cost"][0]
    assert cost["sid"] == new["session_id"] and cost["prompt_chars"] > 0 and cost["cost_tokens"] is not None
    swap = json.loads((rig.home / "swaps.jsonl").read_text().splitlines()[-1])
    assert swap["delivery"] == "startup prompt" and swap["block_chars"] > 0 and swap["first_token_ms"] is not None
    assert "probe_cost" in swap and swap["check"]["pass"]


def test_a_failed_continuity_check_is_no_switch_and_the_old_conversation_serves(rig):
    # (a failed /compact is never retried by itself: the person was told)
    rig.start()
    turn(rig, "Sample before", "m0")
    old = rig.status()
    (rig.state / "fork-forget").write_text("1")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-failed" in whats(rig), what="the refusal")
    fork = [e for e in rig.events() if e.get("what") == "rotate-fork"][0]
    assert fork["check"]["pass"] is False and fork["check"]["missing"] == ["last_message", "card"]
    assert fork["check"]["results"][0]["question"] == "Quote my last message to you, word for word."
    assert "rotate-promote" not in whats(rig)
    early = [e for e in rig.events() if e.get("what") == "rotate-session_started"][0]["pid"]
    assert "rotate-warm_killed" in whats(rig) and not alive(early), "the clean copy started early is stopped"
    assert rig.status()["pid"] == old["pid"] and rig.status()["session_id"] == old["session_id"]
    starts = [c for c in rig.calls() if c["call"] == "notice_start"]
    assert len(starts) == 1 and starts[0]["trigger"] == "manual", "a failed /compact says so, once"
    assert [c for c in rig.calls() if c["call"] == "notice_finish"][0]["state"] == "failed"
    failed = [c for c in rig.calls() if c["call"] == "update_attempt"][-1]
    assert failed["outcome"] == "failed" and failed["reason"].startswith(
        "the check on the copy failed: it couldn't quote your last message (Quote my last message to you, word for "
        "word. expected \u201cSample last words\u201d, got \u201c\u201d")
    # the serving conversation hears of it once, on its next input, seen only (marked before the write)
    turn(rig, "Sample still here", "k1")
    turn(rig, "Sample again", "k2")
    lines = [w["line"] for w in user_lines(rig) if w["pid"] == old["pid"]]
    noted = [l for l in lines if "<compact-note>" in l["message"]["content"]]
    assert len(noted) == 1 and noted[0]["message"]["content"].endswith("\n\nSample still here")
    assert "it couldn't quote your last message" in noted[0]["message"]["content"]
    assert "orchestra memory compaction-status" in noted[0]["message"]["content"]
    seen = [c for c in rig.calls() if c["call"] == "mark_seen_only"]
    assert len(seen) == 1 and seen[0]["uuid"] == noted[0]["uuid"] and seen[0]["sid"] == old["session_id"]


def test_a_failed_automatic_attempt_shows_nothing_and_leaves_no_note(rig):
    rig.settings(auto_compact=True, compact_at=40)
    rig.ctx(500)
    (rig.state / "fork-forget").write_text("1")
    rig.start(HEADLESS_ROTATE_BACKOFF_S="300")
    turn(rig, "Sample turn", "a1")
    wait_for(lambda: "rotate-retry_at" in whats(rig), timeout=30, what="the automatic failure")
    assert not [c for c in rig.calls() if c["call"].startswith("notice_")], "nothing in the chat"
    failed = [c for c in rig.calls() if "attempt" in c["call"]][-1]
    assert failed["outcome"] == "failed" and "couldn't quote your last message" in failed["reason"]
    assert [c for c in rig.calls() if c["call"] == "record_attempt"][0]["trigger"] == "auto"
    turn(rig, "Sample next", "a2")
    assert not [w for w in user_lines(rig) if "<compact-note>" in w["line"]["message"]["content"]]


def test_a_runtime_with_no_startup_slot_gets_the_block_on_its_first_input(rig):
    rig.start(NOSLOT="1")
    turn(rig, "Sample before", "m1")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-old_closed" in whats(rig), what="the switch")
    assert "rotate-probe" in whats(rig) and "rotate-fork" not in whats(rig)
    new = rig.status()
    wait_for(lambda: new["pid"] in rig.starts(), what="the start record")
    assert "--append-system-prompt-file" not in rig.starts()[new["pid"]]["argv"]
    packet = rig.home / f"packet-{new['session_id']}.txt"
    turn(rig, "Sample after", "m2")
    first = [w for w in user_lines(rig) if w["pid"] == new["pid"]]
    assert len(first) == 1 and first[0]["line"]["message"]["content"].startswith("<earlier-conversation>")
    assert first[0]["line"]["message"]["content"].endswith("\n\nSample after")
    wait_for(lambda: not packet.exists(), what="the block file deleted after its turn")
    seen = [c for c in rig.calls() if c["call"] == "mark_seen_only"]
    assert len(seen) == 1 and seen[0]["sid"] == new["session_id"] and seen[0]["uuid"] == first[0]["line"]["uuid"]
    written = next(e for e in rig.events() if e.get("kind") == "in" and "<earlier-conversation>" in json.dumps(e))
    assert seen[0]["t"] <= time.mktime(time.strptime(written["wall"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone + 1, \
        "marked before the write"
    swap = json.loads((rig.home / "swaps.jsonl").read_text().splitlines()[-1])
    assert swap["delivery"] == "attached to the first input"
    turn(rig, "Sample later", "m3")
    later = [w for w in user_lines(rig) if w["line"]["message"]["content"] == "Sample later"]
    assert len(later) == 1, "only the first input carries it"


def test_two_switches_in_a_row(rig):
    rig.start()
    turn(rig, "Sample one", "a1")
    sids = [rig.status()["session_id"]]
    for i in (1, 2):
        rig.send("/compact", f"c{i}", source="person")
        wait_for(lambda: rig.status()["session_id"] not in sids and rig.status()["state"] == "idle", what=f"switch {i}")
        sids.append(rig.status()["session_id"])
        turn(rig, f"Sample after {i}", f"b{i}")
    gens = json.loads((rig.state / "generations.json").read_text())
    assert [gens[s] for s in sids[1:]] == [2, 3]
    assert whats(rig).count("rotate-promote") == 2 and whats(rig).count("rotate-first_turn") == 2


def test_mail_compact_is_ordinary_text_and_auto_compact_off_never_switches(rig):
    rig.settings(auto_compact=False, compact_at=40)
    rig.ctx(960)                                                         # 96%: over the guard
    rig.start()
    turn(rig, "/compact", "m1", source="mail")
    turn(rig, "Sample filler", "m2")
    time.sleep(3)
    assert whats(rig) == [], "Auto-compact off: no switch at any %, the guard included"
    assert [w["line"]["message"]["content"] for w in user_lines(rig)][0] == "/compact", "mail is delivered as text"
    assert "DISABLE_AUTO_COMPACT" not in [k for s in rig.starts().values() for k, v in s["env"].items() if v], \
        "with ours off, the CLI keeps its own compaction"


def test_auto_compact_on_switches_at_compact_at_and_the_cli_compaction_is_off(rig):
    rig.settings(auto_compact=True, compact_at=40)
    rig.ctx(500)
    rig.start()
    first = rig.status()["pid"]
    wait_for(lambda: first in rig.starts(), what="the start record")
    assert rig.starts()[first]["env"]["DISABLE_AUTO_COMPACT"] == "1"
    turn(rig, "Sample turn", "a1")
    wait_for(lambda: "rotate-old_closed" in whats(rig), timeout=30, what="the automatic switch")
    pend = [e for e in rig.events() if e.get("what") == "rotate-pending"][0]
    assert pend["reason"] == "threshold" and pend["context_pct"] == 50.0
    now = rig.status()["pid"]
    wait_for(lambda: now in rig.starts(), what="the new start record")
    assert rig.starts()[now]["env"]["DISABLE_AUTO_COMPACT"] == "1"


def test_a_compact_sent_mid_turn_waits_for_the_turn_end(rig):
    rig.start()
    turn(rig, "Sample warm-up turn", "w0")
    rig.send("Sample SLOW one", "s1")
    wait_for(lambda: rig.state_of("s1") == "delivered", what="the long turn running")
    out = rig.send("/compact", "c1", source="person")
    assert out["ok"] and out["pending"] and not out["started"]
    wait_for(lambda: "rotate-old_closed" in whats(rig), timeout=30, what="the switch")
    ev = rig.events()
    answered = next(i for i, e in enumerate(ev) if e.get("kind") == "msg" and e.get("id") == "s1"
                    and e.get("state") == "answered")
    started = next(i for i, e in enumerate(ev) if e.get("what") == "rotate-notice_start")
    assert answered < started
    waiting = [c for c in rig.calls() if c["call"] == "record_attempt"][0]
    assert waiting["outcome"] == "waiting" and waiting["reason"] == "a reply was still being written"


def test_a_message_held_across_the_switch_goes_once_and_carries_the_block(rig):
    rig.start(FAKE_BIND_S="1.5")
    turn(rig, "Sample before", "m0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: rig.status()["rotation"]["phase"] == "ready" or "rotate-bound" in whats(rig) or
             rig.status()["state"] == "idle" and "rotate-session_started" in whats(rig), what="about to bind")
    wait_for(lambda: any(c["call"] == "promote" for c in rig.calls()), what="binding")
    rig.send("Sample held", "h1")                                       # arrives while the switch holds words
    wait_for(lambda: rig.state_of("h1") == "answered", timeout=30, what="h1")
    writes = [w for w in user_lines(rig) if w["line"]["message"]["content"].endswith("Sample held")]
    assert len(writes) == 1 and writes[0]["line"]["message"]["content"] == "Sample held"
    assert writes[0]["pid"] == rig.status()["pid"], "delivered once, to the new conversation, as its first turn"
    assert json.loads((rig.home / "swaps.jsonl").read_text().splitlines()[-1])["message"] == "h1"


def test_a_failed_first_turn_rebuilds_forward_from_the_same_block(rig):
    (rig.home).mkdir(parents=True, exist_ok=True)
    (rig.home / "rotation-fault").write_text("first_turn\n")
    rig.start(HEADLESS_ROTATION_TEST="1")
    turn(rig, "Sample before", "m0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-old_closed" in whats(rig), what="the switch")
    bound = rig.status()["session_id"]
    turn(rig, "Sample first", "f1")
    wait_for(lambda: "rotate-first_turn" in whats(rig), timeout=30, what="the rebuilt first turn")
    assert "rotate-first_turn_failed" in whats(rig)
    now = rig.status()["session_id"]
    assert now != bound
    writes = [w for w in user_lines(rig) if w["line"]["message"]["content"].endswith("Sample first")]
    assert [w["sid"] for w in writes] == [bound, now] and all(
        w["line"]["message"]["content"] == "Sample first" for w in writes), "the same message, nothing attached"
    starts = rig.starts()
    prompts = {x["sid"]: x["prompt"] for x in starts.values() if not x["probe"] and x["prompt"]}
    assert prompts[bound] == prompts[now], "rebuilt from the same context"
    assert {"call": "set_session_state", "sid": bound, "state": "failed"}.items() <= \
        [c for c in rig.calls() if c["call"] == "set_session_state" and c["sid"] == bound][0].items()
    assert json.loads((rig.home / "swaps.jsonl").read_text().splitlines()[-1])["rebuilds"] == 1
    assert "rotate-fault_first_turn" in whats(rig), "the fault is a crash before the result, not an overruled answer"
    assert answers(rig, "f1") == 1, "the message carried exactly once, answered once"


def answers(r, mid):
    return len([e for e in r.events() if e.get("kind") == "msg" and e.get("id") == mid and e.get("state") == "answered"])


def test_a_crash_in_the_first_turn_rebuilds_forward(rig):
    rig.start()
    turn(rig, "Sample before", "m0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-old_closed" in whats(rig), what="the switch")
    rig.send("Sample CRASH-ONCE", "x1")
    wait_for(lambda: rig.state_of("x1") == "answered" and "rotate-first_turn" in whats(rig), timeout=30,
             what="answered by a fresh start")
    assert "rotate-first_turn_failed" in whats(rig)
    writes = [w for w in user_lines(rig) if w["line"]["message"]["content"] == "Sample CRASH-ONCE"]
    assert len(writes) == 2 and writes[0]["sid"] != writes[1]["sid"], "carried once, to one fresh start"
    assert answers(rig, "x1") == 1
    assert "replay-unmatched" not in [e.get("what") for e in rig.events()]


def test_the_new_conversations_own_init_is_compared_again(rig):
    rig.start()
    turn(rig, "Sample before", "m0")
    (rig.state / "successor-model").write_text("other-model\n")         # the real new one differs, once
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-old_closed" in whats(rig), what="the switch")
    bound = rig.status()["session_id"]
    turn(rig, "Sample first", "f1")
    diffs = [e for e in rig.events() if e.get("what") == "rotate-capability_diff"]
    assert diffs and diffs[0]["stage"] == "first_input" and "model" in diffs[0]["diff"]
    wait_for(lambda: "rotate-suspect" in whats(rig), what="marked suspect")
    # its answer stands: never asked for twice
    assert answers(rig, "f1") == 1
    assert [w["sid"] for w in user_lines(rig) if w["line"]["message"]["content"] == "Sample first"] == [bound]
    # the NEXT input goes to a fresh conversation from the same context, and that one matches
    wait_for(lambda: rig.status()["session_id"] != bound and rig.status()["state"] == "idle", what="rebuilt")
    turn(rig, "Sample second", "f2")
    wait_for(lambda: "rotate-first_turn" in whats(rig), timeout=30, what="the rebuilt first turn")
    assert "rotate-init_matches" in whats(rig) and answers(rig, "f2") == 1
    assert "replay-unmatched" not in [e.get("what") for e in rig.events()]
    st = [c for c in rig.calls() if c["call"] == "set_session_state" and c["sid"] == bound]
    assert st and st[0]["state"] == "inactive", "a session that answered is replaced, never marked failed"


def test_a_fork_whose_init_differs_stops_the_switch_and_the_old_conversation_serves(rig):
    rig.start()
    turn(rig, "Sample before", "m0")
    old = rig.status()
    (rig.state / "probe-model").write_text("other-model\n")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-failed" in whats(rig), what="the refusal")
    assert "rotate-capability_diff" in whats(rig) and "rotate-promote" not in whats(rig)
    assert [c for c in rig.calls() if c["call"] == "update_attempt"][-1]["reason"] == \
        "the new copy wouldn't have had the same model"
    assert rig.status()["pid"] == old["pid"] and rig.status()["session_id"] == old["session_id"]
    assert [c for c in rig.calls() if c["call"] == "notice_finish"][0]["state"] == "failed"
    turn(rig, "Sample still here", "k1")


def test_a_runtime_without_an_adapter_refuses_compact_and_never_passes_it_on(rig):
    rig.start(RUNTIME="codex")
    out = rig.send("/compact", "c1", source="person")
    assert out["ok"] is False and "codex rotation is not supported yet" in out["reason"]
    time.sleep(1)
    assert not [w for w in rig.stdin() if "/compact" in json.dumps(w["line"])]


def test_a_conversation_started_outside_a_switch_is_registered_as_the_seats_active_one_once(rig):
    rig.start()                                                # as after a takeover: never registered before
    wait_for(lambda: any(c["call"] == "register_session" for c in rig.calls()), what="the registration")
    regs = [c for c in rig.calls() if c["call"] == "register_session"]
    assert len(regs) == 1 and regs[0]["sid"] == SID and regs[0]["state"] == "active" and regs[0]["seed_mode"] is None
    assert HS.request(SEAT, {"cmd": "restart"}, orch_dir=rig.orch)["ok"]   # a restart resumes the same one
    wait_for(lambda: rig.status()["generation"] == 2 and rig.status()["state"] == "idle", what="restarted")
    turn(rig, "Sample after restart", "r1")
    assert len([c for c in rig.calls() if c["call"] == "register_session"]) == 1, "never twice"
    # after a switch, the new conversation was registered by the switch itself: no second row for it either
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-old_closed" in whats(rig), what="the switch")
    turn(rig, "Sample new", "n1")
    new = rig.status()["session_id"]
    assert len([c for c in rig.calls() if c["call"] == "register_session" and c["sid"] == new]) == 1


def _mcp(rig, **k):
    (rig.state / "mcp.json").write_text(json.dumps(k))


def test_the_new_copies_wait_for_the_mcp_servers_the_chat_had_before_their_first_input(rig):
    _mcp(rig, servers=[{"name": "Sample Docs", "delay": 1.5}, {"name": "Sample Mail", "status": "needs-auth"}])
    rig.start(HEADLESS_ROTATE_MCP_SETTLE_S="8")
    time.sleep(1.6)
    turn(rig, "Sample before", "m0")                  # the chat's own init: Sample Docs connected
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-old_closed" in whats(rig) or "rotate-failed" in whats(rig), timeout=30, what="the end")
    assert "rotate-failed" not in whats(rig), [e.get("reason") for e in rig.events() if e.get("what") == "rotate-failed"]
    fork = [e for e in rig.events() if e.get("what") == "rotate-fork"][0]
    assert fork["check"]["pass"]
    settled = [e for e in rig.events() if e.get("what") == "rotate-settled"][0]
    assert settled["by"] == "status" and settled["statuses"]["Sample Docs"] == "connected"
    started = [e for e in rig.events() if e.get("what") == "rotate-session_started"][0]
    assert started["mcp_needed"] == ["Sample Docs"], "a server that needed auth before is not required now"
    turn(rig, "Sample after", "m1")
    swap = json.loads((rig.home / "swaps.jsonl").read_text().splitlines()[-1])
    assert swap["settle"]["copy"]["by"] == "status" and swap["settle"]["clean"]["by"] == "status"
    assert "rotate-init_matches" in whats(rig), "the clean conversation's first turn had the connectors too"


def test_a_cli_that_does_not_report_mcp_status_is_given_the_settle_time(rig):
    _mcp(rig, servers=[{"name": "Sample Docs", "delay": 1.0}], no_status=True)
    rig.start(HEADLESS_ROTATE_MCP_SETTLE_S="2")
    time.sleep(1.1)
    turn(rig, "Sample before", "m0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-old_closed" in whats(rig) or "rotate-failed" in whats(rig), timeout=30, what="the end")
    assert "rotate-old_closed" in whats(rig)
    assert [e for e in rig.events() if e.get("what") == "rotate-fork"][0]["check"]["pass"]


def test_a_server_that_never_connects_on_the_new_copy_is_a_real_loss_and_no_switch(rig):
    _mcp(rig, servers=[{"name": "Sample Docs", "delay": 0.5}], never=True)
    rig.start(HEADLESS_ROTATE_MCP_SETTLE_S="2")
    time.sleep(0.6)
    turn(rig, "Sample before", "m0")
    old = rig.status()
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-failed" in whats(rig), timeout=30, what="the refusal")
    assert "rotate-promote" not in whats(rig) and rig.status()["pid"] == old["pid"]
    assert [c for c in rig.calls() if c["call"] == "update_attempt"][-1]["reason"] == \
        "the MCP server Sample Docs didn't connect on the new copy"


def test_no_init_since_a_restart_is_not_a_safe_moment_and_not_a_failure(rig):
    rig.start()                                       # nothing has been asked since the start: no init on record
    rig.send("/compact", "c1", source="person")
    time.sleep(3)
    assert "rotate-failed" not in whats(rig) and rig.status()["rotation"]["failures"] in (0, None)
    waiting = [c for c in rig.calls() if c["call"] == "record_attempt"][0]
    assert waiting["outcome"] == "waiting" and waiting["reason"] == "waiting for the first reply since the agent restarted"
    turn(rig, "Sample first", "m0")                   # the first reply brings the init: the /compact goes ahead
    wait_for(lambda: "rotate-old_closed" in whats(rig), timeout=30, what="the switch after the first reply")


def test_a_slow_store_never_holds_up_the_start(rig):
    t0 = time.monotonic()
    rig.start(FAKE_REGISTER_S="3")
    turn(rig, "Sample first", "m0")
    assert time.monotonic() - t0 < 2.5, "the seat answered while the registration was still waiting on the store"
    assert rig.status()["restarts"] == 0
    wait_for(lambda: any(c["call"] == "register_session" for c in rig.calls()), timeout=10, what="registered later")


def test_a_rebuild_waits_for_the_mcp_settle_before_its_first_input(rig):
    _mcp(rig, servers=[{"name": "Sample Docs", "delay": 1.5}])
    rig.start(HEADLESS_ROTATE_MCP_SETTLE_S="8")
    time.sleep(1.6)
    turn(rig, "Sample before", "m0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-old_closed" in whats(rig), timeout=30, what="the switch")
    rig.send("Sample CRASH-ONCE", "x1")                    # the new conversation dies in its first turn
    wait_for(lambda: rig.state_of("x1") == "answered" and "rotate-first_turn" in whats(rig), timeout=40,
             what="answered by a settled fresh start")
    reb = [e for e in rig.events() if e.get("what") == "rotate-settled" and e.get("stage") == "rebuild"]
    assert reb and reb[0]["by"] == "status" and reb[0]["statuses"]["Sample Docs"] == "connected"
    assert "rotate-init_matches" in whats(rig) and "rotate-capability_diff" not in whats(rig)
    assert answers(rig, "x1") == 1


def test_a_compact_right_after_a_switch_runs_without_waiting_for_an_input(rig):
    rig.start()
    turn(rig, "Sample before", "m0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-old_closed" in whats(rig), what="the first switch")
    first = rig.status()["session_id"]
    wait_for(lambda: rig.status()["state"] == "idle", what="idle")
    rig.send("/compact", "c2", source="person")             # nothing sent in between: no init of its own yet
    wait_for(lambda: whats(rig).count("rotate-old_closed") == 2, timeout=30, what="the second switch")
    assert rig.status()["session_id"] != first
    assert not [c for c in rig.calls() if c["call"] in ("record_attempt", "update_attempt")
                and "first reply since" in (c.get("reason") or "")]


def test_two_compacts_a_minute_apart_after_a_question_and_answer_both_pass(rig):
    rig.start()
    turn(rig, "Sample work", "w0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: whats(rig).count("rotate-old_closed") == 1, timeout=30, what="the first switch")
    turn(rig, "Sample: is step 2 done?", "q1")       # a status question and its answer as the last turn
    rig.send("/compact", "c2", source="person")
    wait_for(lambda: whats(rig).count("rotate-old_closed") == 2, timeout=30, what="the second switch")
    forks = [e for e in rig.events() if e.get("what") == "rotate-fork"]
    assert [f["check"]["pass"] for f in forks] == [True, True]
    assert all(r["pass"] and r["answer"] for f in forks for r in f["check"]["results"]), "each answer graded, logged"


def test_a_saved_history_of_another_session_is_refused_in_plain_words(rig):
    (rig.state / "foreign").write_text("1")
    rig.start()
    turn(rig, "Sample before", "m0")
    old = rig.status()
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-failed" in whats(rig), what="the refusal")
    assert "rotate-promote" not in whats(rig) and rig.status()["pid"] == old["pid"]
    assert [c for c in rig.calls() if c["call"] == "update_attempt"][-1]["reason"] == \
        "the chat's saved history belongs to a different session than the one running, so nothing was changed"


def test_a_failed_check_logs_what_the_copy_answered(rig):
    (rig.state / "fork-forget").write_text("1")
    rig.start()
    turn(rig, "Sample before", "m0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-failed" in whats(rig), what="the refusal")
    fork = [e for e in rig.events() if e.get("what") == "rotate-fork"][0]
    assert fork["answer"] == "I don't know." and [r["pass"] for r in fork["check"]["results"]] == [False, False]
    reason = [c for c in rig.calls() if c["call"] == "update_attempt"][-1]["reason"]
    assert "What is the id of the pending approval card? expected \u201capr_sample1\u201d" in reason


def _restart(rig, **extra):
    assert HS.request(SEAT, {"cmd": "stop"}, orch_dir=rig.orch)["ok"]
    wait_for(lambda: rig.status() is None, what="stopped")
    return rig.start(**extra)


@pytest.mark.parametrize("lookup", ["knows there is no file", "says there is one"])
def test_a_promoted_conversation_with_no_turn_yet_survives_a_restart(rig, lookup):
    extra = {"FAKE_TRANSCRIPT_ALWAYS": "1"} if lookup == "says there is one" else {}
    rig.start(**extra)
    turn(rig, "Sample before", "m0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-old_closed" in whats(rig), what="the switch")
    new = rig.status()["session_id"]
    prompt = rig.home / f"packet-{new}.txt"
    assert prompt.exists()
    _restart(rig, **extra)                             # before any input reached it
    wait_for(lambda: (rig.status() or {}).get("state") == "idle" and "fresh-start" in
             [e.get("what") for e in rig.events()], timeout=30, what="up again")
    st = rig.status()
    assert st["session_id"] == new
    assert st["state"] == "idle"
    assert st.get("failed") is not True
    fresh = [x for x in rig.starts().values() if x["sid"] == new and not x["probe"]]
    last = max(fresh, key=lambda x: x["pid"])
    assert "--session-id" in last["argv"] and last["argv"][last["argv"].index("--session-id") + 1] == new
    assert last["argv"][last["argv"].index("--append-system-prompt-file") + 1] == str(prompt)
    assert last["prompt"] and "SAMPLE BLOCK" in last["prompt"], "the same startup context"
    turn(rig, "Sample first", "f1")
    wait_for(lambda: not prompt.exists(), what="the file goes after its first turn")
    assert answers(rig, "f1") == 1
    _restart(rig, **extra)                             # now it has a turn: an ordinary resume
    turn(rig, "Sample after", "f2")
    assert rig.status()["session_id"] == new


def test_a_compact_that_cannot_start_in_time_is_dropped_and_never_runs_late(rig):
    rig.start(HEADLESS_COMPACT_WAIT_S="1")
    turn(rig, "Sample before", "m0")
    rig.send("Sample SLOW one", "s1")
    wait_for(lambda: rig.state_of("s1") == "delivered", what="a long turn running")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-compact_dropped" in whats(rig), timeout=10, what="dropped")
    wait_for(lambda: rig.state_of("s1") == "answered", what="the turn ends")
    time.sleep(3)
    assert "rotate-promote" not in whats(rig) and "rotate-notice_start" in whats(rig)
    starts = [c for c in rig.calls() if c["call"] == "notice_start"]
    assert len(starts) == 1 and starts[0]["trigger"] == "manual"
    assert [c for c in rig.calls() if c["call"] == "notice_finish"][-1]["state"] == "failed"
    assert [c for c in rig.calls() if c["call"] in ("record_attempt", "update_attempt")][-1]["reason"] == \
        "the agent was busy, so the chat wasn't compacted; type /compact again"
    turn(rig, "Sample next", "n1")
    noted = [w for w in user_lines(rig) if "<compact-note>" in w["line"]["message"]["content"]]
    assert len(noted) == 1 and "type /compact again" in noted[0]["line"]["message"]["content"]


def test_a_waiting_compact_is_not_carried_across_a_restart(rig):
    rig.start()
    turn(rig, "Sample before", "m0")
    rig.send("Sample SLOW one", "s1")
    wait_for(lambda: rig.state_of("s1") == "delivered", what="a long turn running")
    rig.send("/compact", "c1", source="person")
    _restart(rig)
    turn(rig, "Sample after", "a1")
    time.sleep(3)
    assert "rotate-promote" not in whats(rig)


def test_the_chat_is_saved_as_it_goes_off_the_loop_and_coalesced(rig):
    rig.start(FAKE_INGEST_S="1.5")                   # each save takes 1.5 s
    t0 = time.monotonic()
    for i in range(5):
        rig.send(f"Sample burst {i}", f"b{i}")
    wait_for(lambda: all(rig.state_of(f"b{i}") == "answered" for i in range(5)), what="all answered")
    assert time.monotonic() - t0 < 3.0, "slow saves never held a turn up"
    wait_for(lambda: rig.status()["saving"]["ticks"] >= 1, timeout=10, what="saved")
    time.sleep(4)
    ticks = len([c for c in rig.calls() if c["call"] == "ingest_tick"])
    assert 1 <= ticks <= 4, f"a burst of 5 inputs and 5 results folded into a few passes, not 10 ({ticks})"
    st = rig.status()["saving"]
    assert st["errors"] == 0 and st["last_ms"] is not None and st["last_added"] == 1


def test_saving_waits_while_the_index_names_another_conversation(rig):
    (rig.state / "foreign-active").write_text("1")
    rig.start()
    turn(rig, "Sample one", "m0")
    time.sleep(2)
    assert not [c for c in rig.calls() if c["call"] == "ingest_tick"]
    assert rig.status()["saving"]["skipped"] >= 1 and "save-skipped" in [e.get("what") for e in rig.events()]


def test_a_compact_right_after_our_own_switch_counts_as_saved(rig):
    rig.start()
    turn(rig, "Sample before", "m0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: whats(rig).count("rotate-old_closed") == 1, what="the first switch")
    rig.send("/compact", "c2", source="person")        # no turn on the promoted conversation yet
    wait_for(lambda: whats(rig).count("rotate-old_closed") == 2, timeout=30, what="the second switch")
    exports = [c for c in rig.calls() if c["call"] == "export"]
    assert exports[1]["assume_saved"] is True and "rotate-export_saved" in whats(rig)
    turn(rig, "Sample turn", "t1")                      # now it has a turn: the full export runs
    rig.send("/compact", "c3", source="person")
    wait_for(lambda: whats(rig).count("rotate-old_closed") == 3, timeout=30, what="the third switch")
    assert whats(rig).count("rotate-export_saved") == 1, "a conversation with a turn is exported and checked"


@pytest.mark.parametrize("n", [0, 1])
def test_a_check_with_fewer_than_two_questions_is_refused_before_any_copy(rig, n):
    (rig.state / "check-qs").write_text(str(n))
    rig.start()
    turn(rig, "Sample before", "m0")
    old = rig.status()
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-failed" in whats(rig), what="the refusal")
    thin = [e for e in rig.events() if e.get("what") == "rotate-check_too_thin"]
    assert thin and thin[0]["asked"] == ["last_message", "card"][:n] and thin[0]["need"] == 2
    assert "rotate-session_started" not in whats(rig) and "rotate-fork" not in whats(rig), "no copy at all"
    assert not [x for x in rig.starts().values() if x["pid"] != old["pid"]], "nothing started"
    assert rig.status()["pid"] == old["pid"]
    assert [c for c in rig.calls() if c["call"] == "update_attempt"][-1]["reason"] == \
        "there wasn't enough on record to check the compacted chat, so nothing was changed"
    assert [c for c in rig.calls() if c["call"] == "notice_finish"][-1]["state"] == "failed"
    turn(rig, "Sample next", "n1")
    assert [w for w in user_lines(rig) if "<compact-note>" in w["line"]["message"]["content"]]


def test_an_automatic_attempt_with_too_thin_a_check_backs_off(rig):
    (rig.state / "check-qs").write_text("1")
    rig.settings(auto_compact=True, compact_at=40)
    rig.ctx(500)
    rig.start(HEADLESS_ROTATE_BACKOFF_S="300")
    turn(rig, "Sample turn", "a1")
    wait_for(lambda: "rotate-retry_at" in whats(rig), timeout=30, what="the automatic refusal")
    assert "rotate-check_too_thin" in whats(rig) and not [c for c in rig.calls() if c["call"].startswith("notice_")]


def test_two_questions_with_one_wrong_answer_are_refused(rig):
    (rig.state / "fork-wrong-card").write_text("1")
    rig.start()
    turn(rig, "Sample before", "m0")
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-failed" in whats(rig), what="the refusal")
    fork = [e for e in rig.events() if e.get("what") == "rotate-fork"][0]
    assert [r["pass"] for r in fork["check"]["results"]] == [True, False]
    assert [c for c in rig.calls() if c["call"] == "update_attempt"][-1]["reason"].startswith(
        "the check on the copy failed: it couldn't name the pending card")


def test_no_switch_during_a_live_call_and_the_compact_waits_for_its_end(rig):
    """A live call with the seat (its marker, services/arturo/agent_call.py) holds every rotation: a person's
    /compact waits past its own wait time without being dropped, and switches once the call is over."""
    rig.settings(auto_compact=False)
    rig.start(HEADLESS_COMPACT_WAIT_S="1")
    turn(rig, "Sample before", "m1")
    old = rig.status()
    mark = rig.home / "call"
    mark.write_text(json.dumps({"cid": "c-call"}))
    assert rig.status()["on_call"] is True
    rig.send("/compact", "c1", source="person")
    wait_for(lambda: "rotate-deferred" in whats(rig), what="deferred")
    for _ in range(25):                                  # 2.5 s: past the 1 s wait, the call still live
        os.utime(mark)
        time.sleep(0.1)
    assert "rotate-compact_dropped" not in whats(rig) and "rotate-notice_start" not in whats(rig)
    assert rig.status()["pid"] == old["pid"]
    mark.unlink()                                        # the call ended
    wait_for(lambda: "rotate-old_closed" in whats(rig), what="the switch after the call")
    assert rig.status()["session_id"] != old["session_id"]
