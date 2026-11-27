"""Shaw, approving #680 (2026-11-27): "that's running now" or other phrases besides "I've started it"; gpt-live-1 is
full duplex. The receipt gives the live model the INTENT (running, not done; vary it; keep talking) and never a line
to read out; a progress claim ("that's running now") is backed while the tool runs, "it's done" never is."""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from services.arturo.native import claim_gate, native_config, server as srv
from services.arturo.native.tests.test_native import FakeExecutor, _fence, _server  # noqa: F401 — _fence: autouse

_QUOTED = re.compile(r"[\"“”]|(?<![A-Za-z])'[^']{3,}'(?![A-Za-z])")     # a quoted line to read out


def test_no_receipt_carries_a_fixed_sentence_to_read_out():
    for out in (srv.ACCEPTED, srv.DETACHED):
        text = out["result"]
        assert not _QUOTED.search(text), f"a quoted line in the receipt: {text!r}"
        assert "own words" in text and "say" not in out
        assert "meanwhile" in text.lower()                                      # full duplex: keep talking, take other asks
    assert "differently" in srv.ACCEPTED["result"] and "never say it is done" in srv.ACCEPTED["result"]


class _Tools:
    ledger = None

    def __init__(self, gate):
        self.gate = gate

    def new_turn(self, c):
        pass

    def execute(self, binding, cid, name, args):
        self.gate.wait(5)
        return {"ok": True, "result": "x"}


class _B:
    conversation_id = "native-test"


def test_each_receipt_in_a_call_is_numbered_so_the_model_can_vary_it():
    gate, clock = threading.Event(), [100.0]
    r = srv.ToolRunner(_Tools(gate), ThreadPoolExecutor(3), late=lambda *a: None, clock=lambda: clock[0])
    names = {"a": "list_agents", "g": "gm_command", "b": "remember_note"}
    for cid in ("a", "g", "b"):
        r.execute(_B(), cid, names[cid], "{}")
    clock[0] += srv.DETACH_AFTER_S
    outs = [r.execute(_B(), cid, names[cid], "{}") for cid in ("a", "g", "b")]
    assert [o.get("receipt_n") for o in outs] == [1, None, 2]                # gm's question has its own wording
    assert r.execute(_B(), "a", "list_agents", "{}")["receipt_n"] == 1     # a re-poll is the same receipt
    assert srv.ACCEPTED.get("receipt_n") is None                             # the template is never mutated
    gate.set()


def test_the_live_prompt_asks_for_varied_receipts_not_one_line():
    live = native_config.prompts()["live"]
    assert 'If a result says "accepted", the work is running, not done' in live
    assert "differently each\ntime" in live or "differently each time" in live.replace("\n", " ")
    assert "take\nanything else" in live or "take anything else" in live.replace("\n", " ")
    assert "accepted and is starting" not in live


def test_progress_claims_are_their_own_kind():
    for said in ("That's running now.", "It's now running in the background.", "It's under way.", "That's queued."):
        assert claim_gate.claim_kind(said) == ("state", "progress"), said
    for said in ("That's done.", "It's saved.", "All done!", "It's running. Done."):
        assert claim_gate.claim_kind(said) == ("state", None), said
    for said in ("I'm pulling that up.", "On it, it'll take a moment."):
        assert claim_gate.claim_kind(said) is None, said


def test_while_a_tool_runs_after_its_receipt_thats_running_now_is_true_and_its_done_is_not(tmp_path, monkeypatch):
    gate = threading.Event()
    ex = FakeExecutor()

    class Slow:
        non_fleet = ex.non_fleet

        def specs(self, fleet):
            return ex.specs(fleet)

        def __call__(self, *a, **k):
            gate.wait(5)
            return ex(*a, **k)
    call = srv.NativeCall(_server(tmp_path, monkeypatch, executor=Slow()), None, "fleet", "iphone")
    clock = [100.0]
    call.session.orchestra.clock = lambda: clock[0]
    progress = claim_gate.claim_kind("That's running now.")
    assert not call.gate.is_backed(progress)                              # nothing running: not true
    call.session.orchestra.execute(call.binding, "n1", "remember_note", "{}")
    clock[0] += srv.DETACH_AFTER_S
    assert call.session.orchestra.execute(call.binding, "n1", "remember_note", "{}")["receipt"] == "accepted"
    assert call.gate.is_backed(progress)
    assert not call.gate.is_backed(claim_gate.claim_kind("That's done."))
    assert not call.gate.is_backed(claim_gate.claim_kind("It's saved."))
    gate.set()
    end = time.monotonic() + 3
    while call.session.orchestra.accepted_running() and time.monotonic() < end:
        time.sleep(0.02)
    assert call.gate.is_backed(claim_gate.claim_kind("It's saved."))      # the real result is in


# ---- review NITs from #680 -------------------------------------------------------------------------------------
def test_a_tool_still_running_at_the_hang_up_is_running_in_the_journal_not_failed(tmp_path):
    from services.arturo import finalize_merge
    from services.arturo.native.tool_ledger import ToolLedger
    led = ToolLedger(tmp_path)
    led.run_once_external("c1", "native-x", "fleet", "spawn_agent", {}, lambda: (dict(led.PENDING), None))
    led.run_once_external("c2", "native-x", "fleet", "list_agents", {}, lambda: ({"ok": False, "error": "TOOL_ERROR"},
                                                                                 None))
    rows = {r["call_id"]: r["status"] for r in finalize_merge.native_tool_turns("native-x", led.path)}
    assert rows == {"c1": "running", "c2": "not_ok"}


def test_an_owner_device_starts_a_new_thread_with_its_after_call_line(tmp_path, monkeypatch):
    # the same rule as #687's gm answers (_may_write): the fleet, the thread's starter, or a thread with no row yet
    import sqlite3
    call = srv.NativeCall(_server(tmp_path, monkeypatch), None, "owner:dev1", "iphone")
    call.page_thread = "web_new1"
    call._end_late(None)
    call._late("c1", "remember_note", True, "Noted and saved: hi")
    db = tmp_path / "orch" / "state" / "arturo" / "threads.db"
    rows = sqlite3.connect(db).execute("SELECT t.started_by, u.content FROM threads t JOIN turns u ON u.thread_id = t.id"
                                       " WHERE t.id = 'web_new1'").fetchall()
    assert len(rows) == 1 and rows[0][0] == "owner:dev1" and "hi" in rows[0][1]


def test_the_tool_timeline_marks_handed_over_rows_and_a_late_note_as_what_came_next(tmp_path):
    from services.arturo.native import fixtures as fx
    from services.arturo.native.session import NativeSession
    from services.arturo.native.tests.test_native import _fleet, _tools
    t, _ = _tools(tmp_path)
    gate, clock = threading.Event(), [100.0]
    runner = srv.ToolRunner(_SlowVia(t, gate), ThreadPoolExecutor(2), late=lambda *a: None, clock=lambda: clock[0])
    sess = NativeSession(_fleet(t), runner, clock=lambda: clock[0], settle=native_config.SETTLE_FAST)
    sess.on_server_frame(fx.started()[0])
    for f in fx.tool_turn("d1", "r1", [("s1", "list_agents", "{}")]):
        sess.on_server_frame(f[0])
    clock[0] += srv.DETACH_AFTER_S
    assert sess.tick()                                                   # ACCEPTED went back
    sess.delivery.queue("s1#0", sess.binding.conversation_id, "the late note")
    for tid, f in sess.delivery_frames():
        sess.delivery_sent(tid, f)
    (row,) = sess.tool_timeline()
    assert row["handed_over"] is True and row["next"] == "note"
    gate.set()


class _SlowVia:
    def __init__(self, t, gate):
        self.t, self.ledger, self.gate = t, t.ledger, gate

    def new_turn(self, c):
        self.t.new_turn(c)

    def execute(self, binding, cid, name, args):
        self.gate.wait(5)
        return {"ok": True, "result": "seats: gm"}
