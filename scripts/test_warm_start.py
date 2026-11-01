"""Warm start: a fresh headless process (after Dev, a restart, takeover) is driven to ready at once, with no user
message and no model turn: the CLI's control request mcp_status (and its start hooks finishing) mark it booted, and
the MCP servers its conversation had connected are asked for until connected. Back to chat answers once it is booted,
so the next words meet a CLI that has finished starting. The real supervisor against the fake CLI, temp dirs."""
import json
import time

import pytest

from test_seat_supervisor import (HS, SID, SS, _status, log_records, msg_state, rig, tty_rig,  # noqa: F401
                                  wait_for)


def _boot(r):
    return HS.status("qa-demo", orch_dir=r.orch)["boot"]


def _sup(r, what):
    return [x for x in log_records(r) if x["kind"] == "sup" and x.get("what") == what]


def test_a_fresh_process_is_booted_without_a_user_message_or_a_turn(rig):
    rig.env["FAKE_BOOT_S"] = "0.4"
    rig.start()
    wait_for(lambda: (_status(rig.orch) or {}).get("boot", {}).get("settled"), what="settled")
    b = _boot(rig)
    assert b["booted"] and b["booted_by"] in ("initialize", "status", "hook_response") and b["booted_ms"] >= 350
    assert b["settled_by"] == "booted" and b["need"] == []
    lines = [w["line"] for w in rig.writes()]
    assert lines and all(l.get("type") == "control_request" for l in lines)   # asked only: no user message, no turn
    assert {l["request"]["subtype"] for l in lines} == {"mcp_status"}         # initialize is off by default
    assert _sup(rig, "boot-booted") and _sup(rig, "boot-settled")
    HS.send("qa-demo", "Sample after boot", "w1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "w1") == "answered", what="answered")


def test_back_to_chat_answers_once_the_new_process_is_booted(tty_rig):
    r = tty_rig
    r.env["FAKE_BOOT_S"] = "0.8"
    r.start_tty()
    wait_for(lambda: (_status(r.orch) or {}).get("state") == "idle", what="idle")
    assert HS.request("qa-demo", {"cmd": "dev"}, orch_dir=r.orch, timeout=20)["ok"]
    back = HS.request("qa-demo", {"cmd": "chat"}, orch_dir=r.orch, timeout=40)
    assert back["ok"] and back["booted"] is True
    b = _boot(r)
    assert b["booted"] and b["booted_ms"] >= 750                    # it waited out the CLI's own start
    assert b["pid"] == back["pid"] == HS.status("qa-demo", orch_dir=r.orch)["pid"]
    # The next words go to a CLI that has finished starting: written after it was booted.
    HS.send("qa-demo", "Sample right after dev", "d2", orch_dir=r.orch)
    wait_for(lambda: msg_state(r, "d2") == "answered", what="answered")
    recs = log_records(r)
    gen2_booted = [i for i, x in enumerate(recs) if x["kind"] == "sup" and x.get("what") == "boot-booted"][-1]
    first_in = next(i for i, x in enumerate(recs) if x["kind"] == "in" and x["obj"].get("type") == "user"
                    and x["obj"]["message"]["content"] == "Sample right after dev")
    assert gen2_booted < first_in


def test_the_connectors_the_conversation_had_are_settled_before_anyone_asks(tty_rig):
    r = tty_rig
    r.env["FAKE_MCP"] = "docs:0.6"
    r.start_tty()
    wait_for(lambda: (_status(r.orch) or {}).get("boot", {}).get("settled"), what="first settle")
    time.sleep(0.7)
    HS.send("qa-demo", "Sample with docs", "c1", orch_dir=r.orch)      # its init records docs connected
    wait_for(lambda: msg_state(r, "c1") == "answered", what="answered")
    assert HS.request("qa-demo", {"cmd": "dev"}, orch_dir=r.orch, timeout=20)["ok"]
    assert HS.request("qa-demo", {"cmd": "chat"}, orch_dir=r.orch, timeout=40)["ok"]
    wait_for(lambda: _boot(r).get("settled"), what="settled after dev")
    b = _boot(r)
    assert b["need"] == ["docs"] and b["settled_by"] == "status" and b["settled_ms"] >= 550
    assert b["statuses"] == {"docs": "connected"}


def test_a_cli_that_says_nothing_never_holds_back_to_chat_long(tty_rig):
    r = tty_rig
    r.env.update({"FAKE_MCP_STATUS": "silent", "FAKE_NO_HOOK": "1", "FAKE_INITIALIZE": "silent"})
    r.start_tty()
    wait_for(lambda: (_status(r.orch) or {}).get("state") == "idle", what="idle")
    assert HS.request("qa-demo", {"cmd": "dev"}, orch_dir=r.orch, timeout=20)["ok"]
    t0 = time.monotonic()
    back = HS.request("qa-demo", {"cmd": "chat"}, orch_dir=r.orch, timeout=40)
    took = time.monotonic() - t0
    assert back["ok"] and back["booted"] is False
    assert took < SS.BOOT_CHAT_WAIT_S + 3.0
    HS.send("qa-demo", "Sample anyway", "s1", orch_dir=r.orch)          # it still takes words
    wait_for(lambda: msg_state(r, "s1") == "answered", what="answered")


# --- a live call: no restart, no Dev; the message's own times (handed over at the write, answering at the echo) -
def test_on_a_call_dev_is_refused_and_a_model_switch_never_restarts(tty_rig):
    r = tty_rig
    r.start_tty()
    wait_for(lambda: (_status(r.orch) or {}).get("state") == "idle", what="idle")
    pid = HS.status("qa-demo", orch_dir=r.orch)["pid"]
    (HS.seat_dir("qa-demo", r.orch) / SS.CALL_MARK).write_text("{}")
    assert HS.status("qa-demo", orch_dir=r.orch)["on_call"] is True
    out = HS.request("qa-demo", {"cmd": "dev"}, orch_dir=r.orch, timeout=20)
    assert out["ok"] is False and "call" in out["reason"]
    out = HS.request("qa-demo", {"cmd": "set_model", "model": "sample-other"}, orch_dir=r.orch, timeout=30)
    assert out == {"ok": True, "model": "sample-other", "how": "next-start", "why": "on a call"}
    time.sleep(0.5)
    st = HS.status("qa-demo", orch_dir=r.orch)
    assert st["pid"] == pid and st["restarts"] == 0 and st["model_arg"] == "sample-other"


def test_a_stale_call_marker_is_no_call(rig):
    import os
    rig.start()
    m = HS.seat_dir("qa-demo", rig.orch) / SS.CALL_MARK
    m.write_text("{}")
    os.utime(m, (time.time() - SS.CALL_STALE_S - 5,) * 2)
    assert HS.status("qa-demo", orch_dir=rig.orch)["on_call"] is False


def test_a_message_says_when_it_was_handed_over_and_when_its_answer_began(rig):
    rig.start()
    HS.send("qa-demo", "Sample timing", "t1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "t1") == "answered", what="answered")
    m = HS.message_state("qa-demo", "t1", orch_dir=rig.orch)
    assert m["handed_over_at"] and m["answering_at"] and m["handed_over_at"] <= m["answering_at"]
    recs = [x for x in log_records(rig) if x["kind"] == "msg" and x["id"] == "t1"]
    assert [x["state"] for x in recs] == ["accepted", "queued", "delivered", "answered"]   # names unchanged
    assert recs[1]["handed_over_at"] == m["handed_over_at"] and recs[2]["answering_at"] == m["answering_at"]


# --- initialize: the CLI's own set-up done at start, not at the first message ------------------------------------
def test_initialize_is_sent_once_first_and_marks_the_cli_booted(rig):
    rig.env.update({"FAKE_BOOT_S": "0.3", "HEADLESS_PREWARM_INITIALIZE": "1"})
    rig.start()
    wait_for(lambda: (_status(rig.orch) or {}).get("boot", {}).get("settled"), what="settled")
    b = _boot(rig)
    assert b["initialize"]["ok"] is True and b["booted"]           # the first of hook or answer marks it booted
    inits = [w for w in rig.writes() if w["line"].get("request", {}).get("subtype") == "initialize"]
    assert len(inits) == 1 and rig.writes()[0] == inits[0]
    assert (rig.state / f"initialized-{inits[0]['pid']}").exists()
    HS.send("qa-demo", "Sample after initialize", "i1", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "i1") == "answered", what="answered")
    assert len([w for w in rig.writes() if w["line"].get("request", {}).get("subtype") == "initialize"]) == 1


def test_a_cli_that_refuses_initialize_still_boots_and_answers(rig):
    rig.env.update({"FAKE_INITIALIZE": "error", "HEADLESS_PREWARM_INITIALIZE": "1"})
    rig.start()
    wait_for(lambda: (_status(rig.orch) or {}).get("boot", {}).get("settled"), what="settled")
    b = _boot(rig)
    assert b["initialize"]["ok"] is False and "initialize" in b["initialize"]["error"]
    assert b["booted"] and b["booted_by"] in ("status", "hook_response")
    HS.send("qa-demo", "Sample anyway", "i2", orch_dir=rig.orch)
    wait_for(lambda: msg_state(rig, "i2") == "answered", what="answered")


def test_initialize_is_off_by_default(rig):
    rig.start()
    wait_for(lambda: (_status(rig.orch) or {}).get("boot", {}).get("settled"), what="settled")
    assert "initialize" not in _boot(rig)
    assert not [w for w in rig.writes() if w["line"].get("request", {}).get("subtype") == "initialize"]


# --- Back to chat answers once the connectors have settled (within its cap) ---------------------------------------
def test_back_to_chat_answers_after_the_connectors_settle(tty_rig):
    r = tty_rig
    r.env["FAKE_MCP"] = "docs:0.8"
    r.start_tty()
    wait_for(lambda: (_status(r.orch) or {}).get("boot", {}).get("settled"), what="first settle")
    time.sleep(0.9)
    HS.send("qa-demo", "Sample with docs", "s1", orch_dir=r.orch)       # its init records docs connected
    wait_for(lambda: msg_state(r, "s1") == "answered", what="answered")
    assert HS.request("qa-demo", {"cmd": "dev"}, orch_dir=r.orch, timeout=20)["ok"]
    back = HS.request("qa-demo", {"cmd": "chat"}, orch_dir=r.orch, timeout=40)
    b = _boot(r)
    assert back["ok"] and back["booted"] is True and back["settled"] is True
    assert b["need"] == ["docs"] and b["settled_by"] == "status" and b["settled_ms"] >= 750
    chat = [x for x in log_records(r) if x["kind"] == "sup" and x.get("what") == "chat"][-1]
    settled = [x for x in log_records(r) if x["kind"] == "sup" and x.get("what") == "boot-settled"][-1]
    assert settled["t"] <= chat["t"]                                     # answered only once settled


def test_back_to_chat_never_waits_past_its_cap_for_connectors_that_never_connect(tty_rig):
    r = tty_rig
    r.env.update({"FAKE_MCP": "docs:0", "HEADLESS_ROTATE_MCP_SETTLE_S": "30"})
    r.start_tty()
    wait_for(lambda: (_status(r.orch) or {}).get("state") == "idle", what="idle")
    HS.send("qa-demo", "Sample with docs", "s2", orch_dir=r.orch)
    wait_for(lambda: msg_state(r, "s2") == "answered", what="answered")
    assert HS.request("qa-demo", {"cmd": "dev"}, orch_dir=r.orch, timeout=20)["ok"]
    (r.state / "mcp-spec").write_text("docs:999")   # the next process's docs never connect
    t0 = time.monotonic()
    back = HS.request("qa-demo", {"cmd": "chat"}, orch_dir=r.orch, timeout=40)
    assert back["ok"] and back["booted"] is True and back["settled"] is False
    assert time.monotonic() - t0 < SS.BOOT_CHAT_WAIT_S + 3.0
    assert _boot(r)["statuses"] == {"docs": "pending"}
