"""Every tool a voice call ran shows in that call's chat thread, in place (call_tool_lines.py + CallThreads.archive).

Shaw's call native-35592a853127 (2026-11-26): Arturo asked gm three times. The native tool ledger holds all three, gm got
all three, and the thread showed none of them: "The issue wasn't that the tool calls didn't happen, it's that they
weren't logged in the chat." The native server keeps its tool runs in its own ledger, never in the call journal, so
the call's archive reads them from there and writes each as a line among the spoken ones.
"""
import json

import pytest

from services.arturo import call_tool_lines as ctl
from services.arturo.call_threads import CallThreads
from services.arturo.native.tool_ledger import ToolLedger
from services.arturo.relay_live_bridge import journal_call_id
from services.arturo.thread_store import ThreadStore

CONV = "native-35592a853127"
CALL = journal_call_id(CONV)
T0 = 1_795_734_790.0


def _ledger(tmp_path, rows):
    led = ToolLedger(tmp_path / "native")
    c = led._connect()
    try:
        for i, (conv, name, args, result, at) in enumerate(rows):
            c.execute("INSERT INTO tool_calls (tool_call_id, conversation_id, principal, name, args_sha, args_json,"
                      " result_json, created) VALUES (?,?,?,?,?,?,?,?)",
                      (f"call_{i}", conv, "fleet", name, f"sha{i}", json.dumps(args), json.dumps(result), at))
    finally:
        c.close()
    return led.path


GM_PROMISE = {"ok": False, "result": "gm has it; the answer comes to your chat. Tell the operator that in a few words "
                                     "and move on."}


# ---- the line ----------------------------------------------------------------------------------------------------
def test_a_question_to_gm_reads_as_the_ask_and_that_gm_has_it():
    line = ctl.tool_line("gm_command", {"prompt": "Shaw reports noticeable latency between turns in the live call."},
                         GM_PROMISE)
    assert line == "Asked gm: Shaw reports noticeable latency between turns in the live call. · gm has it"


def test_gms_answer_in_the_call_and_a_detached_hand_over_read_right():
    assert ctl.tool_line("gm_command", {"prompt": "is it in?"}, {"ok": True, "result": "gm says:\nYes."}).endswith(
        "· gm answered")
    assert ctl.tool_line("gm_command", {"prompt": "is it in?"}, {"ok": True, "result": "Sent to gm; gm has not "
                                                                 "answered yet. Tell the operator"}).endswith(
        "· gm has it")
    assert ctl.tool_line("gm_command", {"prompt": "is it in?"},
                         {"ok": False, "result": "Could not reach gm just now (the message store refused it)."}
                         ).endswith("· didn't reach gm")


def test_any_other_tool_is_its_name_the_gist_and_the_outcome():
    assert ctl.tool_line("research", {"query": "GPT-Live turn latency"}, {"ok": True, "result": "..."}) == \
        "research: GPT-Live turn latency · done"
    assert ctl.tool_line("spawn_agent", {"agent_id": "x"}, {"ok": False, "error": "TOOL_NOT_ALLOWED", "detail": "no"}) == \
        "spawn_agent: x · refused"
    assert ctl.tool_line("list_agents", {}, {"ok": True, "result": "a, b"}) == "list_agents · done"


def test_a_long_ask_is_cut_on_a_word():
    line = ctl.tool_line("gm_command", {"prompt": "word " * 80}, GM_PROMISE)
    assert len(line) < ctl.GIST_MAX + 40 and "…" in line and "word wor…" not in line


def test_a_spending_tool_shows_its_name_and_outcome_only():
    line = ctl.tool_line("spending_search_transactions", {"query": "payments to Dr. Reyes", "amount": "$412.00"},
                         {"ok": True, "result": "3 payments, $412.00"})
    assert line == "spending_search_transactions · done"


def test_secrets_never_reach_the_line():
    line = ctl.tool_line("send_telegram", {"message": "the key is sk-ant-api03-" + "z" * 40},
                         {"ok": True, "result": "sent"})
    assert "z" * 20 not in line and "[redacted]" in line


def test_a_relay_journals_own_tool_turn_reads_the_same_way():
    t = {"role": "tool", "tool": "knowledge", "input": "{'query': 'PM'}", "result": "dispatched", "status": "done"}
    assert ctl.line_for(t) == "knowledge: PM · done"


# ---- the ledger ---------------------------------------------------------------------------------------------------
def test_the_ledger_gives_this_calls_tool_runs_and_no_other_calls(tmp_path):
    db = _ledger(tmp_path, [
        (CONV, "gm_command", {"prompt": "latency?"}, GM_PROMISE, T0 + 13),
        ("native-someoneelse", "gm_command", {"prompt": "theirs"}, GM_PROMISE, T0 + 14),
        (CONV, "research", {"query": "q"}, {"ok": True, "result": "r"}, T0 + 40)])
    spoken = [{"role": "user", "text": "hi", "ts": T0}, {"role": "arturo", "text": "bye", "ts": T0 + 60}]
    got = ctl.ledger_turns(CALL, spoken, db)
    assert [(t["tool"], t["ts"]) for t in got] == [("gm_command", T0 + 13), ("research", T0 + 40)]
    assert all(t["role"] == "tool" and t["tool_call_id"] for t in got)


def test_no_ledger_or_no_timed_lines_is_no_tool_runs_never_an_error(tmp_path):
    assert ctl.ledger_turns(CALL, [{"role": "user", "text": "hi", "ts": T0}], tmp_path / "missing.sqlite3") == []
    db = _ledger(tmp_path, [(CONV, "gm_command", {"prompt": "x"}, GM_PROMISE, T0)])
    assert ctl.ledger_turns(CALL, [{"role": "user", "text": "hi"}], db) == []
    assert ctl.ledger_turns("vc_client_1", [{"role": "user", "text": "hi", "ts": T0}], db) == []


def test_tool_runs_go_between_the_lines_they_happened_between():
    spoken = [{"role": "user", "text": "a", "ts": 10.0}, {"role": "arturo", "text": "b", "ts": 20.0},
              {"role": "user", "text": "c", "ts": 30.0}]
    tools = [{"role": "tool", "tool": "y", "ts": 25.0}, {"role": "tool", "tool": "x", "ts": 15.0},
             {"role": "tool", "tool": "z", "ts": 99.0}]
    out = ctl.merge(spoken, tools)
    assert [t.get("text") or t.get("tool") for t in out] == ["a", "x", "b", "y", "c", "z"]


def test_a_tool_turn_already_in_the_journal_is_not_written_twice():
    spoken = [{"role": "tool", "tool": "x", "tool_call_id": "call_1", "ts": 5.0}, {"role": "user", "text": "a", "ts": 10.0}]
    out = ctl.merge(spoken, [{"role": "tool", "tool": "x", "tool_call_id": "call_1", "ts": 5.0}])
    assert len([t for t in out if t["role"] == "tool"]) == 1


# ---- the archive --------------------------------------------------------------------------------------------------
def _card(cid):
    return (f"Call ended ({cid}). [voice-call: {cid} /x.json]", {"kind": "call_end", "call_id": cid})


def test_the_archive_writes_each_tool_run_in_place_as_a_visible_call_line(tmp_path):
    db = _ledger(tmp_path, [(CONV, "gm_command", {"prompt": "why the latency?"}, GM_PROMISE, T0 + 13)])
    s = ThreadStore(tmp_path / "t.db")
    ct = CallThreads(s, end_marker=_card, tool_turns=lambda cid, turns: ctl.ledger_turns(cid, turns, db))
    spoken = [{"role": "user", "text": "there is latency", "ts": T0 + 10},
              {"role": "arturo", "text": "Let me check.", "ts": T0 + 12},
              {"role": "arturo", "text": "I've passed that to gm.", "ts": T0 + 20}]
    assert ct.archive(CALL, "web_ypc88hel", spoken, principal="fleet")
    got = s.get_thread("web_ypc88hel")["turns"]
    assert [(t["role"], t["content"], t.get("mode")) for t in got[:4]] == [
        ("user", "there is latency", "voice"), ("assistant", "Let me check.", "voice"),
        ("assistant", "Asked gm: why the latency? · gm has it", "voice"),
        ("assistant", "I've passed that to gm.", "voice")]
    assert got[2]["meta"] == {"kind": "call_tool", "tool": "gm_command", "ok": True, "tool_call_id": "call_0"}
    assert got[2]["ts"] == T0 + 13
    assert got[4]["meta"]["kind"] == "call_end"


def test_a_ledger_that_cannot_be_read_still_archives_the_call(tmp_path):
    s = ThreadStore(tmp_path / "t.db")
    ct = CallThreads(s, end_marker=_card, tool_turns=lambda cid, turns: 1 / 0)
    assert ct.archive(CALL, "web_x", [{"role": "user", "text": "hi", "ts": T0}], principal="fleet")
    assert [t["content"] for t in s.get_thread("web_x")["turns"]][0] == "hi"


def test_the_proxy_archives_a_native_calls_tool_runs_from_the_native_ledger(tmp_path, monkeypatch):
    """End to end: finalize_from_client -> _archive_client_call -> the native ledger at ARTURO_STATE/native."""
    from services.arturo.test_finalize_from_client import _native_proxy
    m = _native_proxy(tmp_path, monkeypatch)
    m.NATIVE_TOOL_LEDGER = _ledger(tmp_path, [(CONV, "gm_command", {"prompt": "why the latency?"}, GM_PROMISE,
                                               T0 + 13)])
    m._THREADS.record_turn("web_ypc88hel", "typed first", "typed answer", principal="fleet")
    j = {"call_id": CALL, "started_at": T0, "ended_at": T0 + 30, "page": "client", "status": "ended",
         "source": "client", "conv_id": CONV, "summary": "Client transcript.", "thread_id": "web_ypc88hel",
         "thread_principal": "fleet",
         "turns": [{"role": "user", "text": "there is latency", "ts": T0 + 10},
                   {"role": "arturo", "text": "I've passed that to gm.", "ts": T0 + 20}]}
    (m.VOICE_CALLS_DIR / f"{CALL}.json").write_text(json.dumps(j))
    assert m.finalize_from_client(client_call_id=CALL) is True
    said = [t["content"] for t in m._THREADS.get_thread("web_ypc88hel")["turns"][2:]]
    assert said[:3] == ["there is latency", "Asked gm: why the latency? · gm has it", "I've passed that to gm."]


def test_the_proxy_reads_the_native_services_own_ledger_file(tmp_path, monkeypatch):
    from services.arturo.test_finalize_from_client import _native_proxy
    m = _native_proxy(tmp_path, monkeypatch)
    # the native service's ledger: NativeServer(ORCHESTRA_DIR/state/arturo/native) -> ToolLedger(that dir).path
    assert m.NATIVE_TOOL_LEDGER == m.ARTURO_STATE / "native" / ToolLedger.__init__.__defaults__[0]


# ---- review round 1: what a run that had not finished, or crashed, reads as ---------------------------------------
PENDING = {"ok": False, "error": "IN_PROGRESS", "detail": "this tool call is still running"}   # ToolLedger.PENDING


def test_a_tool_still_running_at_the_hang_up_never_reads_as_failed():
    from services.arturo.native.tool_ledger import ToolLedger
    assert ToolLedger.PENDING == PENDING
    assert ctl.tool_line("gm_command", {"prompt": "why the latency?"}, PENDING) == "Asked gm: why the latency? · gm has it"
    assert ctl.tool_line("spawn_agent", {"agent_id": "x"}, PENDING) == "spawn_agent: x · still running when the call ended"
    assert ctl.outcome("spawn_agent", PENDING)[0] is True


def test_a_crash_is_didnt_finish_and_only_a_refusal_is_refused():
    crash = {"ok": False, "error": "TOOL_ERROR", "detail": "TimeoutError"}
    assert ctl.tool_line("research", {"query": "q"}, crash) == "research: q · didn't finish"
    assert ctl.tool_line("gm_command", {"prompt": "q"}, crash) == "Asked gm: q · didn't reach gm"
    for code in sorted(ctl.REFUSED):
        assert ctl.tool_line("research", {"query": "q"}, {"ok": False, "error": code}).endswith("· refused")


def test_the_proxys_gm_response_shape_is_an_answer():
    assert ctl.tool_line("gm_command", {"prompt": "q"}, {"ok": True, "result": "GM response: yes"}).endswith(
        "· gm answered")


def test_a_locked_ledger_is_an_error_the_archive_logs_never_a_silent_empty(tmp_path, monkeypatch):
    import sqlite3
    db = _ledger(tmp_path, [(CONV, "gm_command", {"prompt": "x"}, GM_PROMISE, T0)])
    real = sqlite3.connect

    class _Locked:
        def __init__(self, c):
            self.c = c
            self.row_factory = None

        def execute(self, *a):
            raise sqlite3.OperationalError("database is locked")

        def close(self):
            self.c.close()

    monkeypatch.setattr(ctl.sqlite3, "connect", lambda *a, **k: _Locked(real(*a, **k)))
    with pytest.raises(sqlite3.OperationalError):
        ctl.ledger_turns(CALL, [{"role": "user", "text": "hi", "ts": T0}], db)


def test_with_the_ledger_a_journals_own_tool_turns_are_not_written(tmp_path):
    """The merge may link another call's server journal: the ledger is this call's only source of tool runs."""
    s = ThreadStore(tmp_path / "t.db")
    ct = CallThreads(s, end_marker=_card, tool_turns=lambda cid, turns: [])
    turns = [{"role": "user", "text": "hi", "ts": T0},
             {"role": "tool", "tool": "inject_message", "input": {"message": "someone else's"}, "status": "done",
              "ts": T0 + 1}]
    assert ct.archive(CALL, "web_x", turns, principal="fleet")
    assert [t["content"] for t in s.get_thread("web_x")["turns"]][:1] == ["hi"]
    assert not any("someone else" in t["content"] for t in s.get_thread("web_x")["turns"])


def test_neither_brain_reads_a_tool_line_as_something_arturo_said(tmp_path):
    from services.arturo import thread_context as tc
    from services.arturo.thread_store import brain_content
    meta = {"kind": "call_tool", "tool": "gm_command", "ok": True, "tool_call_id": "call_0"}
    said = brain_content("assistant", "Asked gm: q · gm has it", "voice", meta)
    assert said.startswith("(A tool ran on the call; a record, not something said:") and "(on the call)" not in said
    db = _ledger(tmp_path, [(CONV, "gm_command", {"prompt": "why the latency?"}, GM_PROMISE, T0 + 13)])
    s = ThreadStore(tmp_path / "t.db")
    ct = CallThreads(s, end_marker=_card, tool_turns=lambda cid, turns: ctl.ledger_turns(cid, turns, db))
    ct.archive(CALL, "web_y", [{"role": "arturo", "text": "Let me check.", "ts": T0 + 12},
                               {"role": "arturo", "text": "Passed it on.", "ts": T0 + 20}], principal="fleet")
    lines = tc.thread_lines("web_y", tmp_path / "t.db")       # the next call's opening
    assert [l.text for l in lines] == ["Let me check.", "Passed it on."]
