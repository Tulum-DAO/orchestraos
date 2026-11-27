"""No tool holds a voice call (arturo-latency-dev msg_2dc6dcd4: GPT-Live takes no new turn while a function call is
pending; a call on 11-26 had three 25-27 s silences that way). #674 handed gm_command over after
DETACH_AFTER_S; now EVERY tool still running then answers "accepted, still running", the call talks on, and the real
result lands later: as a note while the call is up (the deliveries path), as a line in the call's thread once it ended.
"Accepted" backs "I've started it", never "it's done"."""
import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from services.arturo.native import server as srv
from services.arturo.native.session import RUNNING
from services.arturo.native.tests.test_native import (FakeExecutor, FakeProvider, HDRS, _audio_frame, _client,
                                                      _fence, _run, _server)  # noqa: F401 — _fence: autouse, temp tree


class _Tools:
    ledger = None

    def __init__(self, reply, gate):
        self.reply, self.gate = reply, gate

    def new_turn(self, conversation_id):
        pass

    def execute(self, binding, tool_call_id, name, arguments_json):
        self.gate.wait(5)
        return self.reply


class _B:
    conversation_id = "native-test"


def _runner(reply):
    gate, clock, late = threading.Event(), [100.0], []
    r = srv.ToolRunner(_Tools(reply, gate), ThreadPoolExecutor(2), late=lambda *a: late.append(a),
                       clock=lambda: clock[0])
    return r, gate, clock, late


def _wait(pred, s=3.0):
    end = time.monotonic() + s
    while not pred() and time.monotonic() < end:
        time.sleep(0.01)
    return pred()


# ---- the runner ----------------------------------------------------------------------------------------------------
def test_a_slow_tool_answers_accepted_at_the_detach_time_and_its_result_comes_later():
    r, gate, clock, late = _runner({"ok": True, "result": "seats: gm (working), research-bot (idle)"})
    assert r.execute(_B(), "c1", "list_agents", "{}") is RUNNING
    clock[0] += srv.DETACH_AFTER_S - 0.1
    assert r.execute(_B(), "c1", "list_agents", "{}") is RUNNING        # a fast result still comes back in the turn
    clock[0] += 0.2
    out = r.execute(_B(), "c1", "list_agents", "{}")
    assert out["receipt"] == "accepted" and out["status"] == "running" and "still running" in out["result"]
    assert late == []
    gate.set()
    assert _wait(lambda: late)
    assert late == [("c1", "list_agents", True, "seats: gm (working), research-bot (idle)")]


def test_a_handed_over_tool_stays_handed_over_after_its_result_is_in():
    # the session re-polls every call of a held delegation: the real result must never go back as the output too
    r, gate, clock, late = _runner({"ok": True, "result": "Noted and saved: buy milk"})
    r.execute(_B(), "c1", "remember_note", "{}")
    clock[0] += srv.DETACH_AFTER_S
    first = r.execute(_B(), "c1", "remember_note", "{}")
    gate.set()
    assert _wait(lambda: late)
    assert r.execute(_B(), "c1", "remember_note", "{}") == first and first["receipt"] == "accepted"
    assert len(late) == 1


def test_a_fast_tool_is_never_handed_over():
    r, gate, clock, late = _runner({"ok": True, "result": "seats: gm"})
    gate.set()
    r.execute(_B(), "c1", "list_agents", "{}")
    assert _wait(lambda: r.execute(_B(), "c1", "list_agents", "{}") is not RUNNING)
    clock[0] += 60
    assert r.execute(_B(), "c1", "list_agents", "{}") == {"ok": True, "result": "seats: gm"}
    assert late == []


def test_a_failed_slow_tool_comes_back_as_not_done():
    r, gate, clock, late = _runner({"ok": False, "error": "TOOL_ERROR", "detail": "TimeoutError"})
    r.execute(_B(), "c1", "spawn_agent", "{}")
    clock[0] += srv.DETACH_AFTER_S
    r.execute(_B(), "c1", "spawn_agent", "{}")
    gate.set()
    assert _wait(lambda: late)
    assert late[0][:3] == ("c1", "spawn_agent", False) and "TimeoutError" in late[0][3]


def test_the_escape_hatch_holds_every_tool_but_gm_command(monkeypatch):
    monkeypatch.setenv(srv.RECEIPT_FLAG, "0")
    r, gate, clock, late = _runner({"ok": True, "result": "seats: gm"})
    r.execute(_B(), "c1", "list_agents", "{}")
    clock[0] += 60
    assert r.execute(_B(), "c1", "list_agents", "{}") is RUNNING
    r.execute(_B(), "g1", "gm_command", "{}")
    clock[0] += srv.DETACH_AFTER_S
    assert r.execute(_B(), "g1", "gm_command", "{}") == srv.DETACHED
    gate.set()


# ---- the claim gate: accepted is not done ------------------------------------------------------------------------
def test_accepted_backs_i_started_it_never_done_or_saved_until_the_result_is_in(tmp_path, monkeypatch):
    gate = threading.Event()
    ex = FakeExecutor()
    real = ex.__call__

    def slow(name, args, principal, **kw):
        gate.wait(5)
        return real(name, args, principal, **kw)
    s = _server(tmp_path, monkeypatch, executor=slow_executor(ex, slow))
    call = srv.NativeCall(s, None, "fleet", "iphone")
    clock = [100.0]
    call.session.orchestra.clock = lambda: clock[0]
    b = call.binding
    assert call.session.orchestra.execute(b, "n1", "remember_note", "{}") is RUNNING
    clock[0] += srv.DETACH_AFTER_S
    assert call.session.orchestra.execute(b, "n1", "remember_note", "{}")["receipt"] == "accepted"
    assert call.gate.is_backed(("action", "start"))                  # "I've started it": true, it is running
    assert not call.gate.is_backed(("state", None))                  # "it's done": not until the result is in
    assert not call.gate.is_backed(("action", "save"))               # "I've saved it": not yet
    gate.set()
    assert _wait(lambda: call.gate.is_backed(("action", "save")))   # the real result backs it
    assert not call.session.orchestra.accepted_running()


def slow_executor(base, fn):
    class Slow:
        non_fleet = base.non_fleet

        def specs(self, fleet):
            return base.specs(fleet)

        def __call__(self, *a, **k):
            return fn(*a, **k)
    return Slow()


# ---- end to end: the call talks on, the result lands later ---------------------------------------------------------
def _tool_request(p, call_id, name, args="{}"):
    p.push({"type": "session.input_transcript.delta", "delta": "save a note to buy milk"})
    p.push({"type": "session.delegation.created", "delegation": {"id": f"d_{call_id}"}})
    p.push({"type": "response.event", "delegation_id": f"d_{call_id}", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "call_id": call_id, "name": name, "arguments": args}}})
    p.push({"type": "response.event", "delegation_id": f"d_{call_id}", "event": {
        "type": "response.completed", "response": {"model": "gpt-6-luna",
                                                   "usage": {"input_tokens": 10, "output_tokens": 2}}}})


def _speaks_on_continue(p, marks):
    """Like GPT-Live: it speaks as soon as it may take a turn (our response.create after the function output)."""
    def run():
        while not p.down:
            if "continue" not in marks and "response.create" in p.types():
                marks["continue"] = time.monotonic()
                p.push({"type": "session.output_transcript.delta", "delta": "I'm on it, starting that now."})
                for _ in range(5):
                    p.push(_audio_frame())
                for _ in range(10):
                    p.push(_audio_frame(rms=0))
            time.sleep(0.02)
    threading.Thread(target=run, daemon=True).start()


TOOL_S = 4.0     # the stub tool's run time (the probe uses 10 s; the shape is the same)


def test_a_slow_tool_lets_arturo_speak_within_3_5_s_and_its_result_lands_later_as_a_note(tmp_path, monkeypatch):
    ex = FakeExecutor(reply=lambda name, args: "Noted and saved: buy milk", delay=TOOL_S)
    marks, audio_t = {}, []

    async def script(ws, p):
        p.push({"type": "session.started"})
        await asyncio.sleep(0.2)
        _speaks_on_continue(p, marks)
        marks["request"] = time.monotonic()
        _tool_request(p, "call_N", "remember_note")
        await asyncio.sleep(TOOL_S + 1.5)

    async def go():
        s = _server(tmp_path, monkeypatch, executor=ex)
        c = await _client(s)
        orig = srv.NativeCall.emit

        def emit(self, msg):
            if isinstance(msg, (bytes, bytearray)) and any(msg):
                audio_t.append(time.monotonic())
            return orig(self, msg)
        monkeypatch.setattr(srv.NativeCall, "emit", emit)
        try:
            got, p = await _call(c, script)
        finally:
            await c.close()
        return got, p
    got, p = _run(go())
    outs = [f for f in p.sent if f["type"] == "response.item.create"]
    assert len(outs) == 1 and outs[0]["item"]["call_id"] == "call_N"
    out = json.loads(outs[0]["item"]["output"])
    assert out["receipt"] == "accepted" and "buy milk" not in outs[0]["item"]["output"]
    spoke = [t for t in audio_t if t >= marks["request"]]
    assert spoke and spoke[0] - marks["request"] <= 3.5, (
        f"Arturo first spoke {spoke[0] - marks['request'] if spoke else None} s after the request")
    notes = [f for f in p.sent if f["type"].startswith("session.") and f["type"].endswith(".append")
             and f["type"] != "session.input_audio.append" and "buy milk" in f.get("content", "")]
    assert len(notes) == 1, "the real result must land in the call once, as a note"
    assert notes[0]["type"] == "session.thinking.append"                 # the kind measured to be voiced
    assert "remember_note" in notes[0]["content"] and "finished" in notes[0]["content"]
    rep = json.loads(next((tmp_path / "native" / "calls").glob("*.json")).read_text())
    assert list(rep["deliveries"].values()) == ["attempted"]
    assert [h["name"] for h in rep["handed_over"]] == ["remember_note"]


def test_a_result_that_lands_after_the_call_ended_is_a_line_in_the_calls_thread(tmp_path, monkeypatch):
    ex = FakeExecutor(reply=lambda name, args: "Noted and saved: buy milk", delay=2.5)

    async def script(ws, p):
        p.push({"type": "session.started"})
        await asyncio.sleep(0.2)
        _tool_request(p, "call_E", "remember_note")
        await asyncio.sleep(srv.DETACH_AFTER_S + 0.4)                  # accepted went back; then Shaw hangs up

    async def go():
        s = _server(tmp_path, monkeypatch, executor=ex)
        s.page_thread_loader = lambda tid, principal: ""
        s.page_feed_factory = lambda tid, principal=None: None
        c = await _client(s)
        try:
            got, p = await _call(c, script, start={"type": "start", "thread": "web_receipt1"})
            await asyncio.sleep(2.0)                                     # the tool finishes after the call ended
        finally:
            await c.close()
        return p
    p = _run(go())
    assert not [f for f in p.sent if "buy milk" in json.dumps(f)], "the ended call's provider got the result"
    db = tmp_path / "orch" / "state" / "arturo" / "threads.db"
    rows = sqlite3.connect(db).execute("SELECT role, content, meta FROM turns WHERE thread_id = ?",
                                       ("web_receipt1",)).fetchall()
    assert len(rows) == 1, rows
    role, content, meta = rows[0]
    assert role == "assistant" and "buy milk" in content and "remember_note" in content
    assert json.loads(meta)["kind"] == "after_call"


async def _call(c, script, start=None):
    from services.arturo.native.tests.test_native import _call as base
    return await base(c, script, start=start)
