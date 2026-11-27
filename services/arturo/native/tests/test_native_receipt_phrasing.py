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
    for said in ("That's running now.", "It's now running in the background.", "It's under way.", "That's queued.",
                 "It's running, it'll be done in a minute.", "That's running now and being saved."):
        assert claim_gate.claim_kind(said) == ("state", "progress"), said
    for said in ("That's done.", "It's saved.", "All done!", "It's running. Done.",
                 "It's running and the note is saved.", "It's running; it's done now.",   # codex on #698: mixed
                 "The agent has been started.", "It's started.",                          # a finished start
                 "It's running and saved.", "It's running and done now.",                 # codex round 2: shared
                 "It's under way; that's already saved.",                                 # subject, adverbs
                 "It's running and the message was delivered.",                           # codex round 3
                 "It's running; your note is confirmed to be saved already.",
                 "It's running; your note appears to be saved already.",                   # "to be" is no future
                 "It's running; your note has been written down."):                        # codex round 4
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


def test_a_finished_success_is_no_evidence_that_something_is_running_now(tmp_path, monkeypatch):
    # codex on #698: a remember_note that finished must not back "that's running now"; a started seat does
    call = srv.NativeCall(_server(tmp_path, monkeypatch), None, "fleet", "iphone")
    b, progress = call.binding, ("state", "progress")
    assert call.toolset.execute(b, "n1", "remember_note", "{}")["ok"]
    assert call.gate.is_backed(("state", None)) and not call.gate.is_backed(progress)
    spawn = '{"session_name": "research-bot", "machine": "vps", "task": "compare the vendors"}'
    call.toolset.new_turn(b.conversation_id)                                # the caller asks again (I5 fence)
    call.session.spoken_turn = "I'll start a seat called research-bot."
    call.toolset.spoken_fn = lambda conv: call.session.spoken_turn
    assert call.toolset.execute(b, "s1", "spawn_agent", spawn)["ok"]
    assert call.gate.is_backed(progress)                                    # the seat runs: true


def test_every_claim_in_an_utterance_needs_its_own_backing():
    # codex round 3 on #698: "I've started it and it's done" was checked only as a start, so a running spawn backed
    # the "done" beside it
    assert claim_gate.claim_kinds("I've started it and it's done.") == [("action", "start"), ("state", None)]
    backs = {("action", "start"), ("state", "progress")}                 # a spawn still running after its receipt
    g = claim_gate.ClaimGate(is_backed=lambda k: k in backs, hold_s=0.0)
    g.feed_transcript("I've started it and it's done.")
    assert g.blocked
    g2 = claim_gate.ClaimGate(is_backed=lambda k: k in backs, hold_s=0.0)
    g2.feed_transcript("I've started it, and that's running now.")
    assert not g2.blocked


def test_a_thread_created_by_someone_else_after_the_owner_check_is_not_written(tmp_path, monkeypatch):
    # codex round 4 on #698: _may_write saw no thread; the fleet created it before the append. The append itself
    # refuses (require_owner, inside its transaction), and the row says the post failed
    import sqlite3
    from services.arturo import gm_answers
    from services.arturo.thread_store import ThreadStore
    from services.arturo.native import call_context
    monkeypatch.setattr(gm_answers, "_may_write", lambda threads, tid, principal: None)   # the check already passed
    ThreadStore(call_context.thread_context.threads_db_path()).append_message("web_race1", "user", "fleet's",
                                                                              principal="fleet")
    call = srv.NativeCall(_server(tmp_path, monkeypatch), None, "owner:dev1", "iphone")
    call.page_thread = "web_race1"
    call._end_late(None)
    call._late("c1", "remember_note", True, "Noted and saved: planted")
    db = tmp_path / "orch" / "state" / "arturo" / "threads.db"
    assert [r[0] for r in sqlite3.connect(db).execute("SELECT content FROM turns WHERE thread_id = 'web_race1'")] == \
        ["fleet's"]
    assert call.session.delivery.states(call.binding.conversation_id) == {"c1#0": "post_failed"}


def test_require_owner_is_opt_in_and_lets_the_owner_and_the_fleet_write(tmp_path):
    from services.arturo.thread_store import ThreadStore
    t = ThreadStore(tmp_path / "threads.db")
    t.append_message("w1", "user", "a", principal="owner:dev1")
    assert t.append_message("w1", "assistant", "b", principal="owner:dev2", require_owner=True) is None
    assert t.append_message("w1", "assistant", "c", principal="owner:dev1", require_owner=True) is not None
    assert t.append_message("w1", "assistant", "d", principal="fleet", require_owner=True) is not None
    assert t.append_message("w1", "assistant", "e", principal="owner:dev2") is not None      # default: unchanged
    assert t.append_message("w2", "assistant", "f", principal="owner:dev2", require_owner=True) is not None  # new
