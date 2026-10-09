"""Arturo chat thread integrity, the store side (DEC-1791511578959986; the operator's report,
2026-10-09): a thread title read "[Context: route=/agent/pm-ops entity=agent:pm-ops] Wheres the
general manager", the onboarding thread was titled with the page's hidden opener, and two turns on
one conversation at once could overwrite each other's rows.

Storage stays byte-identical with what the model saw (the context line is kept: warm sessions sync
by turn count, and a follow-up like "restart it" leans on earlier context lines). Every READ a
client gets (the thread list, one thread) has it stripped.
"""
import logging
import sqlite3
import threading

from services.arturo.thread_store import ThreadStore, derive_title, strip_context_line

CTX = "[Context: route=/agent/pm-ops entity=agent:pm-ops]"
OPENER = "(first run: the operator just opened OrchestraOS)"


def test_the_context_line_is_stripped_from_what_a_client_reads(tmp_path):
    s = ThreadStore(tmp_path / "t.db")
    s.record_turn("c1", f"{CTX}\nWheres the general manager agent?", "It runs as gm.")
    t = s.get_thread("c1")
    assert t["turns"][0]["content"] == "Wheres the general manager agent?"
    assert s.list_threads()[0]["title"] == "Wheres the general manager agent?"


def test_the_model_still_gets_the_context_line(tmp_path):
    s = ThreadStore(tmp_path / "t.db")
    s.record_turn("c1", f"{CTX}\nrestart it", "Done.")
    assert s.history("c1")[0]["content"] == f"{CTX}\nrestart it"


def test_the_page_opener_never_titles_a_thread(tmp_path):
    s = ThreadStore(tmp_path / "t.db")
    s.record_turn("c1", OPENER, "Hi, I'm Arturo. What should I call you?")
    assert s.list_threads()[0]["title"] == ""                 # nothing the operator said yet
    s.record_turn("c1", "Shaw", "Good to meet you, Shaw.")
    assert s.list_threads()[0]["title"] == "Shaw"             # their first real message


def test_titles_stored_before_this_fix_read_clean(tmp_path):
    # rows written by the old code: the title is the opener, or carries the context line
    s = ThreadStore(tmp_path / "t.db")
    s.record_turn("c1", "x", "a")
    s.record_turn("c1", "Shaw", "b")
    s.record_turn("c2", f"{CTX}\nWheres gm", "c")
    with sqlite3.connect(tmp_path / "t.db") as conn:
        conn.execute("UPDATE threads SET title = ? WHERE id = 'c1'", (OPENER,))
        conn.execute("UPDATE turns SET content = ? WHERE thread_id = 'c1' AND seq = 0", (OPENER,))
        conn.execute("UPDATE threads SET title = ? WHERE id = 'c2'", (f"{CTX} Wheres gm",))
    titles = {t["id"]: t["title"] for t in s.list_threads()}
    assert titles == {"c1": "Shaw", "c2": "Wheres gm"}
    assert s.get_thread("c1")["title"] == "Shaw"


def test_strip_matches_only_the_proxys_context_line():
    assert strip_context_line(f"{CTX}\nhello") == "hello"
    assert strip_context_line(f"{CTX} hello") == "hello"           # a title is one line
    assert strip_context_line("[Context] is a word I use") == "[Context] is a word I use"
    assert strip_context_line("hello\n[Context: route=/x]") == "hello\n[Context: route=/x]"
    assert derive_title(f"{CTX}\nhello") == "hello"
    assert derive_title(OPENER) == ""


def test_two_turns_at_once_on_one_thread_both_land_in_order(tmp_path):
    # The page's opener and the operator's first message start a NEW thread at the same moment:
    # before the fix one of them lost the INSERT race ("UNIQUE constraint failed: threads.id") and
    # was dropped, which is how a thread loses its first question. Repeated, so a race shows.
    s = ThreadStore(tmp_path / "t.db")
    for rnd in range(6):
        cid, n = f"c{rnd}", 12
        go = threading.Barrier(n)

        def turn(i, cid=cid, go=go):
            go.wait()
            assert s.record_turn(cid, f"q{i}", f"a{i}")
        workers = [threading.Thread(target=turn, args=(i,)) for i in range(n)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        rows = s.get_thread(cid)["turns"]
        assert len(rows) == 2 * n and s.turn_count(cid) == 2 * n
        assert sorted(r["content"] for r in rows[0::2]) == sorted(f"q{i}" for i in range(n))
        assert all(rows[k + 1]["content"] == "a" + rows[k]["content"][1:] for k in range(0, 2 * n, 2))


def test_a_failed_write_is_logged_not_swallowed(tmp_path, caplog, monkeypatch):
    s = ThreadStore(tmp_path / "t.db")

    def broken():
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(s, "_connect", broken)
    caplog.set_level(logging.WARNING)
    assert s.record_turn("c1", "q", "a") is False
    assert "thread store: could not record a turn for c1" in caplog.text and "disk I/O error" in caplog.text


def test_has_opener_finds_the_onboarding_thread(tmp_path):
    s = ThreadStore(tmp_path / "t.db")
    s.record_turn("onb", OPENER, "Hi")
    s.record_turn("other", "hello", "hi")
    s.record_turn("ctx", f"{CTX}\n{OPENER}", "Hi")
    assert s.has_opener("onb") and s.has_opener("ctx")
    assert not s.has_opener("other") and not s.has_opener("missing")
