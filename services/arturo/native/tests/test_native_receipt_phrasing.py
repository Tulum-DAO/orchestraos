"""Shaw, approving #680 (2026-11-27): "that's running now" or other phrases besides "I've started it"; gpt-live-1 is
full duplex. The receipt gives the live model the INTENT (running, not done; vary it; keep talking) and never a line
to read out. Option 3 (arturo-context-dev msg_0e179be5): no progress claim kind; the receipt is said as a non-claim
("I'm on it", "pulling that up"), and "it's running" / "it's done" need the real result like any state claim."""
import re

import pytest
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
    assert "differently" in srv.ACCEPTED["result"] and "started, running or done" in srv.ACCEPTED["result"]


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
    live = " ".join(native_config.prompts()["live"].split())
    assert 'If a result says "accepted", it is not done yet: say you\'re on it in your own words and differently each time' in live
    assert "without calling it started, running or done" in live and "take anything else he asks meanwhile" in live
    assert "accepted and is starting" not in live and "running now" not in live


NATURAL_RECEIPTS = (
    "I'm on it.", "I'm pulling that up.", "On it, it'll take a moment.", "Give me a moment, I'm on it.",
    "I'm on it; meanwhile, what else do you need?", "Pulling that up now; I'll keep you posted.",
    "On it. Did you want anything else?", "I'm on it; you mentioned another question?",
    "Working on that; are you interested in the details?", "I'm getting that for you now.",
)


def test_receipts_are_said_as_non_claims_and_running_needs_a_result():
    for said in NATURAL_RECEIPTS:
        assert claim_gate.claim_kinds(said) == [], said
    for said in ("That's running now.", "It's under way.", "That's done.", "It's saved.", "It's started."):
        assert claim_gate.claim_kinds(said) == [("state", None)], said


# Every completion phrase main's own claim-gate tests know (test_native.py: m6 phrasings, backed/unbacked gate tests)
# plus the mixed ones from codex rounds 1-3.
MAIN_COMPLETIONS = (
    "I sent the message to gm.", "I saved that note.", "I started research-bot.", "Consider it done.",
    "I went ahead and did it.", "I got that going.", "Started.", "The seat is up.", "I've got it started.",
    "I've started it.", "Done, I sent the message to gm.", "It's done.", "Done, I've started the research seat.",
    "I've started research-bot on the vendor comparison.", "The build is finished.", "I've restarted the build.",
    "That's done.", "It's saved.", "All done!", "The note is saved.", "It's done now.",
    "The agent has been started.", "It's started.", "I've fixed it.", "I texted you.", "Sent.", "Saved.",
)


def test_a_receipt_phrase_never_hides_a_completion_main_blocks_alone():
    # arturo-context-dev msg_0e179be5: with only a running receipt (nothing finished), every completion main blocks
    # alone is blocked alone, and stays blocked beside a receipt phrase
    backs = set()

    def blocked(text):
        g = claim_gate.ClaimGate(is_backed=lambda k: k in backs, hold_s=0.0)
        g.feed_transcript(text)
        return g.blocked
    for x in MAIN_COMPLETIONS:
        assert blocked(x), f"main blocks {x!r} alone"
        for p in ("I'm on it.", "Pulling that up;", "On it,"):
            assert blocked(f"{p} {x}") and blocked(f"{x} {p}"), (p, x)


@pytest.mark.xfail(strict=True, reason="pre-existing gap in main's recognisers (they miss these ALONE too): a "
                                       "follow-up to the classifier itself, listed in #698's limits")
@pytest.mark.parametrize("x", ["Your note has been written down.", "I wrote the note.", "Your changes went through.",
                               "The message has been broadcast.", "The message was delivered.",
                               "Your note is confirmed to be saved already.", "That's already saved."])
def test_known_limit_completions_main_misses_alone(x):
    assert claim_gate.claim_kind(x) is not None


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


def test_every_claim_in_an_utterance_needs_its_own_backing():
    # codex round 3 on #698: "I've started it and it's done" was checked only as a start, so a running spawn backed
    # the "done" beside it
    assert claim_gate.claim_kinds("I've started it and it's done.") == [("action", "start"), ("state", None)]
    backs = {("action", "start")}                                         # a started seat backs the start only
    g = claim_gate.ClaimGate(is_backed=lambda k: k in backs, hold_s=0.0)
    g.feed_transcript("I've started it and it's done.")
    assert g.blocked
    g2 = claim_gate.ClaimGate(is_backed=lambda k: k in backs, hold_s=0.0)
    g2.feed_transcript("I've started it, I'm on the rest.")
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


def test_natural_receipts_stream_through_the_gate_word_by_word_unblocked():
    # the gate classifies the transcript as it grows: no prefix of a natural receipt may be cut off
    backs = set()
    for said in NATURAL_RECEIPTS:
        g = claim_gate.ClaimGate(is_backed=lambda k: k in backs, hold_s=0.0)
        for w in said.split(" "):
            g.feed_transcript(w + " ")
            assert not g.blocked, (said, g.text)
    g = claim_gate.ClaimGate(is_backed=lambda k: k in backs, hold_s=0.0)
    for w in "I'm on it; the note is saved.".split(" "):
        g.feed_transcript(w + " ")
    assert g.blocked                                                        # a completion still needs its result
