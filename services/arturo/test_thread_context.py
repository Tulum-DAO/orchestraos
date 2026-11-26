"""thread_context: what any agent's call knows at start (one contract, every agent; Shaw 2026-11-25).

The thread is the agent's chat (the lines the operator sees) and every phrase of every call, in time order, each
stamped. A call opens with the newest of it verbatim up to a token budget, the older part as one summary line per
call / chat stretch, so nothing above the call is silently missing.
"""
import json

import pytest

from services.arturo import thread_context as tc

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
    lines = tc.chat_lines(items, agent_label="gm", operator_label="Shaw")
    assert [(l.who, l.text) for l in lines] == [("Shaw", "ship it"), ("gm", "shipping now")]
    assert [l.channel for l in lines] == ["chat", "chat"]
    assert lines[0].at == pytest.approx(T0)


# --- call side ------------------------------------------------------------------------------------------------------

def test_call_lines_pick_the_owners_calls_and_stamp_every_phrase(tmp_path):
    journal(tmp_path, "vc_a", [("user", "hi arturo", T0), ("assistant", "hey", T0 + 2)])
    journal(tmp_path, "vc_b", [("user", "hi gm", T0 + 3), ("gm", "yo", T0 + 4)], agent="gm")
    arturo = tc.call_lines(tmp_path, owner=None, assistant_label="Arturo", operator_label="Shaw")
    assert [(l.who, l.text, l.call_id) for l in arturo] == [("Shaw", "hi arturo", "vc_a"), ("Arturo", "hey", "vc_a")]
    gm = tc.call_lines(tmp_path, owner="gm", assistant_label="gm", operator_label="Shaw")
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
                         agent_label="gm", operator_label="Shaw")
    text = tc.render(chat + tc.call_lines(tmp_path, owner=None, operator_label="Shaw"), budget_tokens=2000, tz="America/Cancun",
                     tz_label="Local")
    order = [text.index(s) for s in ("typed before", "call line", "call reply", "typed after")]
    assert order == sorted(order)
    body = [ln for ln in text.splitlines() if ln.startswith("[")]
    assert len(body) == 4
    # ISO UTC and Shaw-local on every line: 17:00Z is 12:00 Local
    assert body[0].startswith("[2025-11-25T17:00:00Z 12:00 Local] chat Shaw:")
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


# --- the shared contract with the API builder (api/src/lib/callContext.ts) ------------------------------------------

_CASES = json.loads((__import__("pathlib").Path(__file__).resolve().parents[2] / "contract" /
                     "call-context-cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_the_shared_call_context_cases(case, tmp_path):
    for j in case["journals"]:
        (tmp_path / j["name"]).write_text(j["raw"] if "raw" in j else json.dumps(j["doc"]))

    def fetch(agent, limit):
        if case["items"] is None:
            raise OSError("down")
        return case["items"]
    out = tc.load(chat_agent=case["chat_agent"], call_owner=case["call_owner"], calls_dir=tmp_path,
                  exclude=case["exclude"], fetch=fetch, budget_tokens=case["budget"], tz=case["tz"],
                  tz_label=case["tz_label"], operator_label=case["operator_label"])
    assert out == case["expected"]


def test_the_operator_is_named_by_their_own_name_else_operator(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(tc.os.path, "expanduser", lambda p: str(tmp_path / "nohome"))
    assert tc.operator_name() == "Operator"
    (tmp_path / "state" / "arturo").mkdir(parents=True)
    (tmp_path / "state" / "arturo" / "operator.json").write_text(json.dumps({"facts": {"name": {"value": "Ada"}}}))
    assert tc.operator_name() == "Ada"


def test_the_install_token_is_blanked_before_the_clip(tmp_path, monkeypatch):
    token = "gwtok_" + "Q7" * 20
    (tmp_path / "tok").write_text(token + "\n")
    monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(tmp_path / "tok"))
    monkeypatch.setattr(tc, "_EXACT", {"loaded": False, "values": []})
    line = tc.Line(at=T0, who="Shaw", text="x" * 1170 + " " + token + " tail", channel="chat")
    out = tc.render([line], budget_tokens=2000, tz="UTC", tz_label="UTC")
    assert token[:20] not in out


def test_the_opening_is_fenced_as_past_context_that_is_over(tmp_path):
    # live probes 2026-11-25: same build, 6K, silence uplink; thread on = no greet (native-90a7e00cd3d1), thread off =
    # greet at 1.5 s (native-8f4a14b348a8). The thread ended on verbatim call dialogue, read as a conversation going on.
    journal(tmp_path, "vc_prev", [("user", "you see that, right", T0 + 10), ("assistant", "yes, absolutely", T0 + 11)])
    for fetch in (lambda a, n: [], lambda a, n: (_ for _ in ()).throw(OSError("down"))):
        out = tc.load(chat_agent="gm", call_owner=None, calls_dir=tmp_path, fetch=fetch, tz="UTC", tz_label="UTC")
        assert out.split("\n")[0] == tc.PAST_OPEN
        assert out.split("\n")[-1] == tc.PAST_CLOSE                     # after the notes too: the last thing said
    empty = tc.load(chat_agent="gm", call_owner=None, calls_dir=tmp_path / "none", fetch=lambda a, n: [],
                    tz="UTC", tz_label="UTC")
    assert empty.startswith(tc.PAST_OPEN) and empty.endswith(tc.PAST_CLOSE)
    assert "NEW call" in tc.PAST_CLOSE and "nobody has spoken" in tc.PAST_CLOSE


def test_a_turn_is_an_exchange_consecutive_phrases_of_one_speaker_in_one_call_merge():
    # The operator asked for "the last 20 or 25 turns": 25 phrases spanned 12 minutes, 25 exchanges let them reference
    # "earlier". The merged turn keeps the first phrase's stamp; chat lines never merge.
    L = tc.Line
    lines = [L(T0, "Ada", "I didn't ask about", "call", "vc_a"), L(T0 + 1, "Ada", "that", "call", "vc_a"),
             L(T0 + 2, "Arturo", "You didn't.", "call", "vc_a"), L(T0 + 3, "Arturo", "Sorry.", "call", "vc_a"),
             L(T0 + 4, "Ada", "next call", "call", "vc_b"), L(T0 + 5, "Ada", "typed", "chat"),
             L(T0 + 6, "Ada", "typed again", "chat")]
    out = tc.render(lines, budget_tokens=2000, tz="UTC", tz_label="UTC")
    body = out.split("RECENT (verbatim):\n")[1].splitlines()
    assert body == ["[2025-11-25T17:00:00Z 17:00 UTC] call vc_a Ada: I didn't ask about that",
                    "[2025-11-25T17:00:02Z 17:00 UTC] call vc_a Arturo: You didn't. Sorry.",
                    "[2025-11-25T17:00:04Z 17:00 UTC] call vc_b Ada: next call",
                    "[2025-11-25T17:00:05Z 17:00 UTC] chat Ada: typed",
                    "[2025-11-25T17:00:06Z 17:00 UTC] chat Ada: typed again"]


def test_a_backchannel_between_two_phrases_of_the_other_speaker_folds_out_and_they_merge():
    # proving call 2026-11-26: the opening's 41 "turns" were mostly "Mm-h" / "of" / "m.": a phrase of two words or fewer
    # wedged between two phrases of the other speaker in one call is a backchannel, not a turn
    L = tc.Line
    a = lambda t, text, call="vc_a": L(T0 + t, "Arturo", text, "call", call, role="agent")     # noqa: E731
    u = lambda t, text, call="vc_a": L(T0 + t, "Ada", text, "call", call, role="user")         # noqa: E731
    out = tc.merge_phrases([u(0, "So if I reference something"), a(1, "Mm-h"), u(2, "from earlier in the chat"),
                            a(3, "You didn't."), a(4, "Sorry."),                  # a real short reply, not wedged
                            u(5, "Okay so tell me more"), a(6, "Right then, so"),  # wedged, but 3 words: a turn
                            u(7, "go on please now"),
                            a(8, "Yes"), u(9, "ok", "vc_b"),                      # neighbours in another call: kept
                            u(10, "first", "vc_c"), a(11, "Mm", "vc_c"),
                            u(11 + tc.MERGE_GAP_S + 1, "much later", "vc_c")])    # neighbours too far apart: kept
    assert [(l.who, l.text, l.phrases) for l in out] == [
        ("Ada", "So if I reference something from earlier in the chat", 2),
        ("Arturo", "You didn't. Sorry.", 2), ("Ada", "Okay so tell me more", 1), ("Arturo", "Right then, so", 1),
        ("Ada", "go on please now", 1), ("Arturo", "Yes", 1), ("Ada", "ok", 1),
        ("Ada", "first", 1), ("Arturo", "Mm", 1), ("Ada", "much later", 1)]
    assert out[0].at == T0                                                  # stamped at its first phrase


def _call(tmp_path, turns, call_id="vc_a", started=T0):
    (tmp_path / f"{call_id}.json").write_text(json.dumps({"call_id": call_id, "started_at": started, "turns": turns}))


def _recent(out):
    return [ln for ln in out.split("RECENT (verbatim):\n")[1].splitlines() if not ln.startswith("END OF PAST CONTEXT")]


def test_a_merged_turn_over_the_line_cap_keeps_its_tail_not_its_head():
    # Two 700-character replies were each shown whole before the merge; cut at the head, the turn lost the end of what
    # was said. The newest words are the ones a caller refers back to.
    L = tc.Line
    a, b = "a" * 699 + "A", "b" * 699 + "Z"
    out = tc.render([L(T0, "Arturo", a, "call", "vc_a", role="agent"), L(T0 + 1, "Arturo", b, "call", "vc_a", role="agent")],
                    budget_tokens=4000, tz="UTC", tz_label="UTC")
    turn = _recent(out)[0]
    words = turn.split("Arturo: ", 1)[1]
    assert len(words) == tc.LINE_MAX_CHARS and words.startswith("…") and words.endswith("b" * 690 + "Z")
    one = tc.render([L(T0, "Arturo", "x" * 1300 + "END", "call", "vc_a", role="agent")], budget_tokens=4000, tz="UTC",
                    tz_label="UTC")
    assert _recent(one)[0].endswith("x…")                               # a single phrase is cut as before: its head


def test_one_speaker_last_in_a_call_and_first_in_the_next_are_two_turns(tmp_path):
    _call(tmp_path, [{"role": "assistant", "text": "bye", "ts": T0}, {"role": "user", "text": "ok bye", "ts": T0 + 1}])
    _call(tmp_path, [{"role": "user", "text": "hi again", "ts": T0 + 2}], call_id="vc_b", started=T0 + 2)
    out = tc.load(chat_agent=None, call_owner=None, calls_dir=tmp_path, fetch=lambda a, n: [], budget_tokens=2000,
                  tz="UTC", tz_label="UTC", operator_label="Ada")
    body = _recent(out)
    assert body[-2].endswith("call vc_a Ada: ok bye") and body[-1].endswith("call vc_b Ada: hi again")


def test_the_operator_and_the_agent_never_fold_together_even_under_one_name(tmp_path):
    _call(tmp_path, [{"role": "user", "text": "are you there", "ts": T0}, {"role": "assistant", "text": "I am", "ts": T0 + 1}])
    out = tc.load(chat_agent=None, call_owner=None, calls_dir=tmp_path, fetch=lambda a, n: [], budget_tokens=2000,
                  tz="UTC", tz_label="UTC", operator_label="Arturo")
    assert [ln.split("] ", 1)[1] for ln in _recent(out)] == ["call vc_a Arturo: are you there", "call vc_a Arturo: I am"]


def test_phrases_more_than_the_gap_apart_are_separate_turns():
    L = tc.Line
    g = tc.MERGE_GAP_S
    out = tc.render([L(T0, "Ada", "one", "call", "vc_a", role="user"), L(T0 + g, "Ada", "two", "call", "vc_a", role="user"),
                     L(T0 + 2 * g + 1, "Ada", "three", "call", "vc_a", role="user")], budget_tokens=2000, tz="UTC",
                    tz_label="UTC")
    assert [ln.split(": ", 1)[1] for ln in _recent(out)] == ["one two", "three"]


def test_a_summarized_call_counts_its_phrases_not_its_merged_turns():
    L = tc.Line
    old = [L(T0 + i, "Ada", f"p{i} " + "w" * 300, "call", "vc_old", role="user") for i in range(3)]
    new = [L(T0 + 100 + i, "Ada" if i % 2 else "Arturo", "n" * 1100, "call", "vc_new", role="user" if i % 2 else "agent")
           for i in range(6)]
    out = tc.render(old + new, budget_tokens=900, tz="UTC", tz_label="UTC")
    assert "call vc_old, 3 phrase(s) not shown" in out
