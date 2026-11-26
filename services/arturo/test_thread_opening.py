"""A call placed from the standalone Arturo page opens with THAT page's own thread (the operator, 2026-11-25, gm
msg_e7402cbb): the web_ thread the call is slotted into, its typed turns and its earlier calls, the last 25 turns,
every one stamped, never gm's chat. The thread is the proxy's threads.db (thread_store), read-only; if it cannot be
read, the voice-call journals that name the thread stand in."""
import json

from services.arturo import thread_context as tc
from services.arturo import thread_store

T0 = 1_764_090_000.0      # 2025-11-25T17:00:00Z


def _thread(tmp_path, rows, tid="web_abc123"):
    db = tmp_path / "threads.db"
    st = thread_store.ThreadStore(db)
    for i, (role, text, mode, meta) in enumerate(rows):
        st.append_message(tid, role, text, mode=mode, meta=meta, ts=T0 + i * 10)
    return db


ROWS = [
    ("user", "[Context: route=/arturo entity=none] what did the card say?", "", None),
    ("assistant", "It said the deploy is waiting on you.", "", None),
    ("user", "hey are you there", "voice", None),
    ("assistant", "I'm here.", "voice", None),
    ("assistant", "Call ended (vc_one).", "voice", {"kind": "call_end", "call_id": "vc_one"}),
    ("user", "typed after the call", "", None),
    ("user", "a line on a call still open", "voice", None),
]


def test_the_threads_turns_become_stamped_lines_grouped_by_call(tmp_path):
    db = _thread(tmp_path, ROWS)
    lines = tc.thread_lines("web_abc123", db, operator_label="Ada")
    assert [(l.channel, l.call_id, l.who, l.text) for l in lines] == [
        ("chat", "", "Ada", "what did the card say?"),                  # the hidden page tag is not a word he said
        ("chat", "", "Arturo", "It said the deploy is waiting on you."),
        ("call", "vc_one", "Ada", "hey are you there"),
        ("call", "vc_one", "Arturo", "I'm here."),                      # the end marker itself is not a line
        ("chat", "", "Ada", "typed after the call"),
        ("call", "live", "Ada", "a line on a call still open"),
    ]
    assert lines[0].at == T0 and lines[-1].at == T0 + 60


def test_the_thread_opening_is_fenced_stamped_and_has_no_other_chat(tmp_path):
    db = _thread(tmp_path, ROWS)
    out = tc.load_thread("web_abc123", db=db, calls_dir=tmp_path / "none", tz="UTC", tz_label="UTC",
                         operator_label="Ada")
    assert out.split("\n")[0] == tc.PAST_OPEN and out.split("\n")[-1] == tc.PAST_CLOSE
    assert "[2025-11-25T17:00:00Z 17:00 UTC] chat Ada: what did the card say?" in out
    assert "[2025-11-25T17:00:30Z 17:00 UTC] call vc_one Arturo: I'm here." in out
    assert "THREAD web_abc123" in out


def test_an_unreadable_thread_falls_back_to_the_journals_that_name_it(tmp_path):
    calls = tmp_path / "calls"
    calls.mkdir()
    (calls / "vc_j.json").write_text(json.dumps({"call_id": "vc_j", "thread_id": "web_abc123", "started_at": T0,
                                                 "turns": [{"role": "user", "text": "from the journal", "ts": T0}]}))
    (calls / "vc_k.json").write_text(json.dumps({"call_id": "vc_k", "thread_id": "web_other", "started_at": T0,
                                                 "turns": [{"role": "user", "text": "another thread", "ts": T0}]}))
    out = tc.load_thread("web_abc123", db=tmp_path / "missing.db", calls_dir=calls, tz="UTC", tz_label="UTC",
                         operator_label="Ada")
    assert "call vc_j Ada: from the journal" in out and "another thread" not in out
    assert "(thread web_abc123 could not be read" in out and out.endswith(tc.PAST_CLOSE)


def test_a_bad_thread_id_reads_nothing(tmp_path):
    db = _thread(tmp_path, ROWS)
    assert tc.thread_lines("../etc", db) == []


def _owned(tmp_path, owner):
    db = tmp_path / "threads.db"
    st = thread_store.ThreadStore(db)
    st.record_turn("web_abc123", "Shaw's private question", "Arturo's answer", principal=owner)
    return db


def test_only_fleet_or_the_threads_own_starter_may_read_it(tmp_path):
    # Claude review of #644 (blocking): the proxy's _thread_refusal rule, mirrored. A non-fleet caller reads only a
    # thread it started; anything else reads as if the thread did not exist (no opening, no feed, no journal fallback).
    db = _owned(tmp_path, "owner:shaw")
    assert tc.thread_readable("web_abc123", "fleet", db) is True
    assert tc.thread_readable("web_abc123", "owner:shaw", db) is True
    assert tc.thread_readable("web_abc123", "owner:bob", db) is False
    assert tc.thread_readable("web_nobody", "owner:bob", db) is False          # unknown: fail closed for non-fleet
    assert tc.thread_readable("web_abc123", "owner:bob", tmp_path / "missing.db") is False


def test_a_crashed_call_without_its_end_marker_does_not_take_the_next_calls_id(tmp_path):
    rows = [("user", "call A line", "voice", None), ("user", "typed between", "", None),
            ("user", "call B line", "voice", None),
            ("assistant", "Call ended (vc_B).", "voice", {"kind": "call_end", "call_id": "vc_B"})]
    lines = tc.thread_lines("web_abc123", _thread(tmp_path, rows), operator_label="Ada")
    assert [(l.call_id, l.text) for l in lines] == [("earlier", "call A line"), ("", "typed between"),
                                                    ("vc_B", "call B line")]


def test_a_bad_thread_id_never_opens_the_store(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    assert tc.thread_lines("../etc", tmp_path / "x.db") == [] and tc.thread_lines("web_x\n", tmp_path / "x.db") == []


def test_a_page_threads_call_phrases_fold_by_role_even_under_one_name(tmp_path):
    # #645 on the page thread: an operator named like the assistant never folds into the assistant's turn
    db = _thread(tmp_path, [("user", "are you there", "voice", None), ("assistant", "I am", "voice", None),
                            ("assistant", "still here", "voice", None),
                            ("assistant", "Call ended (vc_one).", "voice", {"kind": "call_end", "call_id": "vc_one"})])
    lines = tc.thread_lines("web_abc123", db, operator_label="Arturo")
    assert [l.role for l in lines] == ["user", "agent", "agent"]
    out = tc.render(lines, budget_tokens=2000, tz="UTC", tz_label="UTC")
    body = out.split("RECENT (verbatim):\n")[1].splitlines()
    assert [ln.split("] ", 1)[1] for ln in body] == ["call vc_one Arturo: are you there",
                                                       "call vc_one Arturo: I am still here"]
