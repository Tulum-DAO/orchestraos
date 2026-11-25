"""thread_context: what any agent's call knows at start (one contract, every agent; Shaw 2026-11-25).

The thread is the agent's chat (the lines the operator sees) and every phrase of every call, in time order, each
stamped. A call opens with the newest of it verbatim up to a token budget, the older part as one summary line per
call / chat stretch, so nothing above the call is silently missing.
"""
import json

import pytest

import thread_context as tc

T0 = 1_764_090_000.0      # 2025-11-25T17:00:00Z, any fixed epoch


def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def chat_item(role, text, at, audience="user", kind="text", **extra):
    return {"kind": kind, "role": role, "text": text, "ts": _iso(at), "audience": audience, **extra}


def journal(tmp_path, cid, turns, agent=None, started=None, status="ended", summary="", thread_id=None):
    doc = {"call_id": cid, "started_at": started if started is not None else turns[0][2],
           "status": status, "summary": summary,
           "turns": [{"role": r, "text": t, "ts": at} for r, t, at in turns]}
    if agent:
        doc["agent"] = agent
    if thread_id:
        doc["thread_id"] = thread_id
    if status == "ended":
        doc["ended_at"] = turns[-1][2]
    (tmp_path / f"{cid}.json").write_text(json.dumps(doc))
    return doc


# --- chat side ------------------------------------------------------------------------------------------------------

def test_chat_lines_keep_only_what_the_operator_sees():
    items = [
        chat_item("user", "ship it", T0),
        chat_item("assistant", "shipping now", T0 + 5),
        chat_item("user", "[mail from relay]", T0 + 6, audience="agent", machine="relay"),
        chat_item("assistant", "internal plan", T0 + 7, audience="agent"),
        chat_item("assistant", "", T0 + 8, kind="tool_use"),
        chat_item("assistant", "hmm", T0 + 9, kind="thinking"),
        chat_item("user", "approval card answered", T0 + 10, machine="approval"),
    ]
    lines = tc.chat_lines(items, agent_label="gm")
    assert [(l.who, l.text) for l in lines] == [("Shaw", "ship it"), ("gm", "shipping now")]
    assert [l.channel for l in lines] == ["chat", "chat"]
    assert lines[0].at == pytest.approx(T0)


# --- call side ------------------------------------------------------------------------------------------------------

def test_call_lines_pick_the_owners_calls_and_stamp_every_phrase(tmp_path):
    journal(tmp_path, "vc_a", [("user", "hi arturo", T0), ("assistant", "hey", T0 + 2)])
    journal(tmp_path, "vc_b", [("user", "hi gm", T0 + 3), ("gm", "yo", T0 + 4)], agent="gm")
    arturo = tc.call_lines(tmp_path, owner=None, assistant_label="Arturo")
    assert [(l.who, l.text, l.call_id) for l in arturo] == [("Shaw", "hi arturo", "vc_a"), ("Arturo", "hey", "vc_a")]
    gm = tc.call_lines(tmp_path, owner="gm", assistant_label="gm")
    assert [(l.who, l.text) for l in gm] == [("Shaw", "hi gm"), ("gm", "yo")]
    assert all(l.channel == "call" for l in arturo + gm)


def test_a_call_still_marked_live_counts_and_the_current_call_is_left_out(tmp_path):
    # 2026-11-25: the previous call finalized 16 s before the next began; its file can still say live.
    journal(tmp_path, "vc_prev", [("user", "remember the dream fix", T0)], status="live")
    journal(tmp_path, "vc_now", [("user", "what did we just say", T0 + 30)], status="live")
    lines = tc.call_lines(tmp_path, owner=None, exclude="vc_now")
    assert [l.call_id for l in lines] == ["vc_prev"]


def test_unreadable_journal_is_skipped_not_fatal(tmp_path):
    (tmp_path / "vc_bad.json").write_text("{half")
    journal(tmp_path, "vc_ok", [("user", "fine", T0)])
    assert [l.call_id for l in tc.call_lines(tmp_path, owner=None)] == ["vc_ok"]


# --- the opening context -------------------------------------------------------------------------------------------

def test_merged_thread_is_time_ordered_with_a_stamp_on_every_line(tmp_path):
    journal(tmp_path, "vc_a", [("user", "call line", T0 + 10), ("assistant", "call reply", T0 + 11)])
    chat = tc.chat_lines([chat_item("user", "typed before", T0), chat_item("assistant", "typed after", T0 + 20)],
                         agent_label="gm")
    text = tc.render(chat + tc.call_lines(tmp_path, owner=None), budget_tokens=2000, tz="America/Cancun",
                     tz_label="Tulum")
    order = [text.index(s) for s in ("typed before", "call line", "call reply", "typed after")]
    assert order == sorted(order)
    body = [ln for ln in text.splitlines() if ln.startswith("[")]
    assert len(body) == 4
    # ISO UTC and Shaw-local on every line: 17:00Z is 12:00 Tulum
    assert body[0].startswith("[2025-11-25T17:00:00Z 12:00 Tulum] chat Shaw:")
    assert "call vc_a Arturo:" in text


def test_budget_keeps_newest_verbatim_and_summarizes_the_rest_per_call(tmp_path):
    old = [("user" if i % 2 == 0 else "assistant", f"old line {i} " + "x" * 200, T0 + i) for i in range(40)]
    journal(tmp_path, "vc_old", old, summary="Voice call: 40 turns, 0 tool runs.")
    journal(tmp_path, "vc_new", [("user", "the newest thing", T0 + 1000)])
    lines = tc.call_lines(tmp_path, owner=None)
    text = tc.render(lines, budget_tokens=400, tz="UTC", tz_label="UTC")
    assert "the newest thing" in text
    assert "old line 39" in text                    # the tail of the old call still fits verbatim
    assert "old line 0 " not in text                # its head does not
    assert "EARLIER (summarized)" in text
    assert "vc_old" in text.split("EARLIER (summarized)")[1].split("RECENT")[0]
    assert tc.estimate_tokens(text) <= 400 + 120    # headers and the summary block ride on top, bounded


def test_empty_thread_says_so():
    assert "no earlier chat or calls" in tc.render([], budget_tokens=500, tz="UTC", tz_label="UTC")


def test_load_merges_the_page_chat_with_the_owners_calls(tmp_path):
    journal(tmp_path, "vc_prev", [("user", "last call words", T0 + 10)])
    fetched = {}

    def fetch(agent, limit):
        fetched["agent"] = agent
        return [chat_item("user", "chat words", T0)]

    text = tc.load(chat_agent="gm", call_owner=None, calls_dir=tmp_path, exclude="vc_now", fetch=fetch,
                   budget_tokens=2000, tz="UTC", tz_label="UTC")
    assert fetched["agent"] == "gm"
    assert "chat words" in text and "last call words" in text


def test_load_survives_an_unreachable_api(tmp_path):
    journal(tmp_path, "vc_prev", [("user", "still here", T0)])

    def fetch(agent, limit):
        raise OSError("api down")

    text = tc.load(chat_agent="gm", call_owner=None, calls_dir=tmp_path, fetch=fetch, budget_tokens=500,
                   tz="UTC", tz_label="UTC")
    assert "still here" in text
    assert "chat could not be read" in text


# --- live feed during a call ---------------------------------------------------------------------------------------

def test_new_chat_since_returns_only_lines_after_the_cursor():
    items = [chat_item("user", "before", T0), chat_item("user", "during", T0 + 60),
             chat_item("assistant", "reply during", T0 + 61)]
    lines = tc.chat_lines(items, agent_label="gm", since=T0 + 1)
    assert [l.text for l in lines] == ["during", "reply during"]


def test_live_note_is_stamped_and_fits_one_append():
    line = tc.Line(at=T0, who="Shaw", text="y" * 5000, channel="chat")
    notes = tc.live_notes(line, tz="UTC", tz_label="UTC", max_tokens=500)
    assert len(notes) > 1
    assert all(tc.estimate_tokens(n) <= 500 for n in notes)
    assert notes[0].startswith("[2025-11-25T17:00:00Z 17:00 UTC] chat Shaw")


def test_secret_shapes_are_blanked_before_they_reach_a_session():
    # the JSON transcript route is not redacted (the .md export is); the voice model is a third party
    line = tc.Line(at=T0, who="Shaw", text="use sk-proj_abcdefghijklmnop and API_TOKEN=hunter2xyz", channel="chat")
    text = tc.render([line], budget_tokens=500, tz="UTC", tz_label="UTC")
    notes = tc.live_notes(line, tz="UTC", tz_label="UTC")
    for out in (text, "\n".join(notes)):
        assert "abcdefghijklmnop" not in out and "hunter2xyz" not in out
        assert "[redacted]" in out
