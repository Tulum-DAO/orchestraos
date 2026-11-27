"""RED-first: an adapter's EARLY RETURNS must still stamp wal_cursors.updated.

gm's ranked item 3 (msg_4098d648). Every adapter has bare `return 0`s BEFORE the
try/finally that owns `set_cursor` — the ONLY writer of wal_cursors.updated. So a
pass that LOOKED at a source and found nothing to consume stamped nothing, and
`updated` could not distinguish

    the recorder looked and there was nothing new        (healthy)
from
    the recorder never looked at this source at all      (the 4.3h stall)

bg_wal_census's FRESH-WAKE grace and its `looked` witness both rest on the
stronger reading, and its own docstring asserts it ("it moves on any tick that
touched this file, including one that consumed nothing") — which was FALSE for
exactly these paths.

THE DELIBERATE ASYMMETRY, asserted below: a tick that reached the READ stamps
insert-or-update; a tick that found NO SOURCE AT ALL only TOUCHES a cursor row
that already exists. A missing source must never mint a phantom cursor row —
the census treats a cursor as evidence the source is real, and "no cursor for
the live sid" is its fail-closed branch.
"""
import json
import sqlite3

from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal.adapter_claude import ClaudeWalAdapter
from lineage_daemon.wal.adapter_codex import CodexWalAdapter
from lineage_daemon.wal.adapter_gemini import GeminiWalAdapter


def _upd(store, path):
    cur = store.get_cursor(path)
    return cur["updated"] if cur else None


def _claude(tmp_path):
    store = WalStore(str(tmp_path / "ob.db"))
    return store, ClaudeWalAdapter(store, lineage_root="orchestra-builder", generation=75)


def _codex(tmp_path):
    store = WalStore(str(tmp_path / "ob-codex.db"))
    return store, CodexWalAdapter(store, lineage_root="orchestra-builder", generation=75)


def _gemini_db(path, steps):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE steps (idx INTEGER, step_type TEXT, status TEXT,"
                 " permissions INTEGER, step_payload BLOB)")
    for i in steps:
        conn.execute("INSERT INTO steps VALUES (?,?,?,?,?)",
                     (i, "message", "done", 0, b"{}"))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- claude / codex
def test_claude_nothing_new_still_stamps_updated(tmp_path):
    store, ad = _claude(tmp_path)
    src = str(tmp_path / "s.jsonl")
    with open(src, "w") as fh:
        fh.write(json.dumps({"type": "system", "sessionId": "sid-test-0001",
                             "timestamp": "2026-10-06T17:40:00.000Z"}) + "\n")
    assert ad.tail(src) >= 1
    first = _upd(store, src)
    assert first is not None
    store._conn.execute("UPDATE wal_cursors SET updated=1.0 WHERE source_path=?", (src,))
    store._conn.commit()
    # second pass: the empty-chunk early return
    assert ad.tail(src) == 0
    assert _upd(store, src) > 1.0, "a look that consumed nothing must still stamp `updated`"


def test_claude_partial_line_still_stamps_updated(tmp_path):
    store, ad = _claude(tmp_path)
    src = str(tmp_path / "p.jsonl")
    with open(src, "w") as fh:
        fh.write('{"type": "system"')      # no newline yet: nothing is consumable
    assert ad.tail(src) == 0
    assert _upd(store, src) is not None, "a newline-incomplete source was LOOKED at"


def test_claude_missing_source_touches_an_existing_cursor_only(tmp_path):
    store, ad = _claude(tmp_path)
    gone = str(tmp_path / "gone.jsonl")
    assert ad.tail(gone) == 0
    assert store.get_cursor(gone) is None, "a missing source must NOT mint a cursor row"
    # with a row already there (the source moved out from under a live cursor), touch it
    store.set_cursor(gone, "orchestra-builder", last_off=10, last_seq=0)
    store._conn.execute("UPDATE wal_cursors SET updated=1.0 WHERE source_path=?", (gone,))
    store._conn.commit()
    assert ad.tail(gone) == 0
    cur = store.get_cursor(gone)
    assert cur["updated"] > 1.0, "a look at a vanished source is still a look"
    assert cur["last_off"] == 10, "touching must not move the byte cursor"


def test_codex_nothing_new_and_missing_source(tmp_path):
    store, ad = _codex(tmp_path)
    src = str(tmp_path / "c.jsonl")
    with open(src, "w") as fh:
        fh.write(json.dumps({"type": "session_meta",
                             "payload": {"id": "sid1", "timestamp": 0}}) + "\n")
    assert ad.tail(src) >= 1
    store._conn.execute("UPDATE wal_cursors SET updated=1.0 WHERE source_path=?", (src,))
    store._conn.commit()
    assert ad.tail(src) == 0
    assert _upd(store, src) > 1.0
    gone = str(tmp_path / "c-gone.jsonl")
    assert ad.tail(gone) == 0
    assert store.get_cursor(gone) is None


# ---------------------------------------------------------------- gemini
def test_gemini_no_new_steps_still_stamps_updated(tmp_path):
    store = WalStore(str(tmp_path / "g.db"))
    ad = GeminiWalAdapter(store, lineage_root="gemini-seat", generation=3)
    db = str(tmp_path / "conv.db")
    _gemini_db(db, [0, 1])
    assert ad.tail(db) == 2
    store._conn.execute("UPDATE wal_cursors SET updated=1.0 WHERE source_path=?", (db,))
    store._conn.commit()
    assert ad.tail(db) == 0, "no steps past the cursor"
    assert _upd(store, db) > 1.0, "a poll that found no new steps is still a look"


def test_gemini_missing_db_does_not_mint_a_cursor(tmp_path):
    store = WalStore(str(tmp_path / "g2.db"))
    ad = GeminiWalAdapter(store, lineage_root="gemini-seat", generation=3)
    gone = str(tmp_path / "nope.db")
    assert ad.tail(gone) == 0
    assert store.get_cursor(gone) is None


def test_gemini_empty_db_mints_no_cursor_the_honest_limit(tmp_path):
    """The LIMIT of this fix, asserted so nobody "improves" it into a phantom row.

    A source with no cursor row has nothing to touch, so a look at it leaves no
    witness. That is deliberate: minting a row would hand bg_wal_census a cursor
    for a conversation that has produced nothing, and its "no cursor for the live
    sid is never graced" fail-closed branch depends on the absence meaning what it
    says. For gemini it would ALSO write the -1 sentinel into last_off.
    """
    store = WalStore(str(tmp_path / "g3.db"))
    ad = GeminiWalAdapter(store, lineage_root="gemini-seat", generation=3)
    db = str(tmp_path / "empty.db")
    _gemini_db(db, [])
    assert ad.tail(db) == 0
    assert store.get_cursor(db) is None, "no cursor to touch -> no row is minted"
