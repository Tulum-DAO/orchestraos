"""A long Arturo thread read a page at a time (scrolling up through it on his page and in the pane).

GET /threads/<id>?before=<seq>&limit=<n> answers the turns just older than the turn `before` names (each with its
seq), plus has_older and oldest_seq, the next page's cursor; ?limit=<n> alone, the newest n. Without either, the
response is the whole thread exactly as it always was: the clients that read it whole (the drawer's cards, older
builds) must see no change.
"""
import importlib.util
import pathlib

import pytest

from services.arturo.thread_store import ThreadStore


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod_and_store(tmp_path):
    mod = _load_proxy()
    s = ThreadStore(tmp_path / "threads.db")
    mod._THREADS = s
    for n in range(12):                                  # 24 turns: q0, a0, q1, a1, ...
        s.record_turn("c1", f"q{n}", f"a{n}", ts=1000 + n)
    return mod, s


def _get(mod, path):
    r = mod.app.test_client().get(path, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    return r.status_code, r.get_json()


def test_without_a_cursor_the_thread_reads_exactly_as_before(mod_and_store):
    mod, s = mod_and_store
    code, body = _get(mod, "/threads/c1")
    assert code == 200
    assert set(body) == {"ok", "thread"}
    assert body["thread"] == s.get_thread("c1")          # the whole thread, today's shape (no seq, no paging keys)
    assert len(body["thread"]["turns"]) == 24 and "seq" not in body["thread"]["turns"][0]


def test_the_newest_page_then_older_pages_to_the_start_every_turn_once_in_order(mod_and_store):
    mod, s = mod_and_store
    code, body = _get(mod, "/threads/c1?limit=10")
    assert code == 200 and body["has_older"] is True
    seen = body["thread"]["turns"]
    assert [t["content"] for t in seen][-2:] == ["q11", "a11"] and len(seen) == 10
    assert body["oldest_seq"] == seen[0]["seq"]
    cursor, pages = body["oldest_seq"], 0
    while True:
        code, page = _get(mod, f"/threads/c1?before={cursor}&limit=10")
        assert code == 200 and "error" not in page
        seen = page["thread"]["turns"] + seen
        pages += 1
        if not page["has_older"]:
            break
        cursor = page["oldest_seq"]
    assert pages == 2                                    # 10 + 10 + 4
    whole = s.get_thread("c1")["turns"]
    assert [(t["role"], t["content"]) for t in seen] == [(t["role"], t["content"]) for t in whole]
    assert [t["seq"] for t in seen] == sorted({t["seq"] for t in seen})


def test_a_page_at_the_very_start_is_short_and_says_nothing_is_older(mod_and_store):
    mod, s = mod_and_store
    first = s.get_thread_page("c1", limit=24)["turns"]
    _, page = _get(mod, f"/threads/c1?before={first[1]['seq']}&limit=10")
    assert [t["content"] for t in page["thread"]["turns"]] == ["q0"]
    assert page["has_older"] is False and page["oldest_seq"] == first[0]["seq"]
    _, empty = _get(mod, f"/threads/c1?before={first[0]['seq']}")
    assert empty["thread"]["turns"] == [] and empty["has_older"] is False and empty["oldest_seq"] is None


def test_an_unknown_cursor_is_an_empty_page_that_says_so(mod_and_store):
    mod, _ = mod_and_store
    code, body = _get(mod, "/threads/c1?before=99999&limit=10")
    assert code == 200
    assert body["thread"]["turns"] == [] and body["has_older"] is False and body["oldest_seq"] is None
    assert body["error"] == "unknown cursor"


def test_bad_paging_values_are_refused_and_a_thread_that_does_not_exist_is_null(mod_and_store):
    mod, _ = mod_and_store
    assert _get(mod, "/threads/c1?before=abc")[0] == 400
    assert _get(mod, "/threads/c1?limit=0")[0] == 400
    code, body = _get(mod, "/threads/nope?limit=10")
    assert code == 200 and body["thread"] is None and body["has_older"] is False


def test_a_page_is_capped(mod_and_store):
    _, s = mod_and_store
    for n in range(300):
        s.record_turn("c2", f"q{n}", f"a{n}")
    page = s.get_thread_page("c2", limit=10_000)
    assert len(page["turns"]) == 500 and page["has_older"] is True
