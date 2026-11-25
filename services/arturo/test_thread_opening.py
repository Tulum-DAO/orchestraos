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
