"""RED-first tests for blocker-#3 STEP 3 — cursor consistency (DEC-1788481319
CONDITION B).

Step 2 added per-seat containment: multiplexer.tick / telemetryd catch a bad
seat's raise and continue. WITHOUT this step that containment SILENTLY CORRUPTS
the append-only WAL: a mid-loop adapter raise leaves rows committed but the
byte/idx cursor un-advanced, so the next tick re-reads and re-appends them. The
WAL has no UNIQUE dedup, so that is duplication into rotation-survival state.

CONDITION B (from docs/HANDOFF_telemetry-wiring-dev-next.md):
  * gemini = simple finally-lift: max_idx already tracks the last-APPENDED idx,
    so advancing the cursor in a finally can never point past a failed row.
  * claude/codex = NOT a naive lift: they advance the byte offset BEFORE the
    append, so a finally on the running offset would persist PAST a failed line
    = SILENT DATA LOSS (a gap). Fix: a `committed_offset` set to line-end ONLY
    after `_emit_line` returns, AND each line's appends wrapped in ONE sqlite txn
    (store commits per-append otherwise -> half-committed-line residual).
  * enricher = update `_last_status` per-item right after each append, not bulk
    at end, so a mid-loop raise cannot re-emit already-captured file_mod rows.

Every RED asserts BOTH invariants at once:
  - NO double-capture: a row committed before the raise is NOT re-emitted.
  - NO gap: the row that FAILED is re-read (captured) on the next pass.

Fixtures are SYNTHETIC (no model voice) per the A0 opaque-fixture protocol; the
fault is injected via a WalStore subclass that raises on the Nth append — the
real store code path (schema, triggers, txn) is exercised, not a mock.
"""
import json
import os
import sqlite3
import tempfile

import pytest

from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal.adapter_claude import ClaudeWalAdapter
from lineage_daemon.wal.adapter_codex import CodexWalAdapter
from lineage_daemon.wal.adapter_gemini import GeminiWalAdapter
from lineage_daemon.wal.enrichers import GitEnricher


class _FlakyStore(WalStore):
    """A real WalStore that raises on a chosen append call (1-based), to
    simulate a seat whose data trips the adapter mid-loop. Disarm by setting
    `fail_on = None` to run the recovery pass."""

    def __init__(self, path, fail_on=None):
        super().__init__(path)
        self.fail_on = fail_on
        self.append_calls = 0

    def append(self, **kw):
        self.append_calls += 1
        if self.fail_on is not None and self.append_calls == self.fail_on:
            raise RuntimeError("injected append failure #%d" % self.append_calls)
        return super().append(**kw)


def _offsets(*lines):
    """Byte start offset of each newline-terminated line."""
    starts = []
    off = 0
    for ln in lines:
        starts.append(off)
        off += len(ln.encode())
    return starts


# --------------------------------------------------------------------------
# gemini: simple finally-lift
# --------------------------------------------------------------------------

def _make_gemini_db(path, steps):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE steps (idx integer PRIMARY KEY, step_type integer NOT NULL"
        " DEFAULT 0, status integer NOT NULL DEFAULT 0, permissions blob,"
        " step_payload blob, step_format integer NOT NULL DEFAULT 0)")
    conn.executemany(
        "INSERT INTO steps (idx, step_type, status, permissions, step_payload)"
        " VALUES (?,?,?,?,?)", steps)
    conn.commit()
    conn.close()


def test_gemini_contained_midloop_no_double_capture_no_gap():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "conv.db")
        _make_gemini_db(db, [
            (1, 15, 3, None, b"\x08\x01"),
            (2, 15, 3, None, b"\x08\x02"),
            (3, 15, 3, None, b"\x08\x03"),
        ])
        st = _FlakyStore(os.path.join(tmp, "wal.db"), fail_on=3)
        ad = GeminiWalAdapter(st, "gem-seat", 7)

        # tick 1: idx 1,2 commit, idx 3's append raises -> lane contains it.
        with pytest.raises(RuntimeError):
            ad.tail(db)

        # cursor MUST have advanced to the last committed idx (2), NOT stayed at 0.
        cur = st.get_cursor(db)
        assert cur is not None and int(cur["last_off"]) == 2

        # tick 2 (fault cleared): must NOT re-emit idx 1,2 and MUST capture idx 3.
        st.fail_on = None
        ad.tail(db)

        idxs = sorted(int(e["body_ref"].split("#idx=")[1]) for e in st.events())
        assert idxs == [1, 2, 3]  # each exactly once: no double-capture, no gap


# --------------------------------------------------------------------------
# claude: committed_offset + per-line txn
# --------------------------------------------------------------------------

def _claude_user_text(sid, text):
    return json.dumps({
        "type": "user", "sessionId": sid,
        "timestamp": "2026-09-04T00:00:00.000Z",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }) + "\n"


def _claude_assistant_text_with_usage(sid, text):
    return json.dumps({
        "type": "assistant", "sessionId": sid,
        "timestamp": "2026-09-04T00:00:00.000Z",
        "message": {"role": "assistant", "content": text,
                    "usage": {"input_tokens": 10, "output_tokens": 5}},
    }) + "\n"


def test_claude_contained_midloop_committed_lines_not_recaptured():
    with tempfile.TemporaryDirectory() as tmp:
        l1 = _claude_user_text("s", "one")
        l2 = _claude_user_text("s", "two")
        l3 = _claude_user_text("s", "three")
        starts = _offsets(l1, l2, l3)
        src = os.path.join(tmp, "s.jsonl")
        with open(src, "w") as fh:
            fh.write(l1 + l2 + l3)

        # each user-text line = exactly 1 append; fail on line 3's append.
        st = _FlakyStore(os.path.join(tmp, "wal.db"), fail_on=3)
        ad = ClaudeWalAdapter(st, "cl-seat", 6)

        with pytest.raises(RuntimeError):
            ad.tail(src)
        # cursor advanced to end of line 2 (start of line 3), NOT left at 0.
        cur = st.get_cursor(src)
        assert cur is not None and int(cur["last_off"]) == starts[2]

        st.fail_on = None
        ad.tail(src)

        offs = sorted(e["source_off"] for e in st.events())
        # all three line starts present exactly once: no double, no gap.
        assert offs == starts


def test_claude_multi_append_line_is_atomic():
    with tempfile.TemporaryDirectory() as tmp:
        # one assistant line emits TWO appends (response + ctx).
        line = _claude_assistant_text_with_usage("s", "hello world")
        src = os.path.join(tmp, "s.jsonl")
        with open(src, "w") as fh:
            fh.write(line)

        st = _FlakyStore(os.path.join(tmp, "wal.db"), fail_on=2)  # 2nd append of the line
        ad = ClaudeWalAdapter(st, "cl-seat", 6)

        with pytest.raises(RuntimeError):
            ad.tail(src)
        # the line half-committed nothing: append #1 rolled back with the txn.
        assert st.events() == []
        # cursor did NOT advance past the failed line.
        cur = st.get_cursor(src)
        assert cur is None or int(cur["last_off"]) == 0

        st.fail_on = None
        ad.tail(src)
        kinds = sorted(e["kind"] for e in st.events())
        # exactly the two events, once each — no duplicated response residual.
        assert kinds == ["ctx", "response"]


# --------------------------------------------------------------------------
# codex: committed_offset + per-line txn
# --------------------------------------------------------------------------

def _codex_meta(sid):
    return json.dumps({"type": "session_meta",
                       "payload": {"id": sid, "timestamp": "2026-09-04T00:00:00Z",
                                   "cwd": "/tmp/x"}}) + "\n"


def _codex_user(text):
    return json.dumps({"type": "response_item",
                       "payload": {"type": "message", "role": "user",
                                   "content": [{"type": "input_text", "text": text}]}}) + "\n"


def test_codex_contained_midloop_committed_lines_not_recaptured():
    with tempfile.TemporaryDirectory() as tmp:
        sid = "01a01801-b232-7760-b6f0-c6ea1d6e9152"
        m = _codex_meta(sid)
        u1 = _codex_user("one")
        u2 = _codex_user("two")
        u3 = _codex_user("three")
        starts = _offsets(m, u1, u2, u3)
        src = os.path.join(tmp, "rollout.jsonl")
        with open(src, "w") as fh:
            fh.write(m + u1 + u2 + u3)

        # appends: 1 meta, 2 u1, 3 u2, 4 u3 -> fail on u3.
        st = _FlakyStore(os.path.join(tmp, "wal.db"), fail_on=4)
        ad = CodexWalAdapter(st, "cx-seat", 3)

        with pytest.raises(RuntimeError):
            ad.tail(src)
        cur = st.get_cursor(src)
        assert cur is not None and int(cur["last_off"]) == starts[3]

        st.fail_on = None
        ad.tail(src)

        offs = sorted(e["source_off"] for e in st.events())
        assert offs == starts  # 4 line starts, each once: no double, no gap


# --------------------------------------------------------------------------
# store: the per-line transaction primitive
# --------------------------------------------------------------------------

def _ev(**over):
    base = dict(ts=1.0, lineage_root="l", generation=1, sid="s", runtime="claude",
                kind="marker", summary="x", body_ref="p:0", source_path="p",
                source_off=0, integrity="h")
    base.update(over)
    return base


def test_store_transaction_rolls_back_on_error():
    with tempfile.TemporaryDirectory() as tmp:
        st = WalStore(os.path.join(tmp, "wal.db"))
        with pytest.raises(RuntimeError):
            with st.transaction():
                st.append(**_ev(summary="a"))
                st.append(**_ev(summary="b"))
                raise RuntimeError("boom")
        # both appends in the aborted txn are gone (atomic line).
        assert st.events() == []
        # a subsequent committed txn persists normally.
        with st.transaction():
            st.append(**_ev(summary="c"))
        assert [e["summary"] for e in st.events()] == ["c"]


# --------------------------------------------------------------------------
# enricher: per-item _last_status update
# --------------------------------------------------------------------------

def test_git_enricher_per_item_no_double_capture_on_midloop_raise():
    import subprocess

    def _git(cwd, *args):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       cwd=cwd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.mkdir(repo)
        _git(repo, "init")  # no commit -> no HEAD -> no git event to offset counting
        for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
            with open(os.path.join(repo, name), "w") as fh:
                fh.write("x")

        st = _FlakyStore(os.path.join(tmp, "wal.db"), fail_on=3)  # 3rd file_mod raises
        en = GitEnricher(st, "l", 1, "sid", repo)

        with pytest.raises(RuntimeError):
            en.sample()

        st.fail_on = None
        en.sample()

        paths = [r["summary"].split()[-1] for r in st.events()
                 if r["kind"] == "file_mod"]
        # every changed path captured exactly once: the two emitted before the
        # raise are NOT re-emitted (per-item _last_status), the failed one is.
        assert sorted(paths) == ["a.txt", "b.txt", "c.txt", "d.txt"]
        assert len(paths) == len(set(paths))
