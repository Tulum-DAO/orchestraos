"""
TDD test suite for format_injection prefix tokens:
  [FINAL], [LOOP-SUSPECT depth=N], (sent Xm ago)

Written FIRST per TDD: run this, see it FAIL, then implement.
"""

import importlib.util
import os
import sqlite3
import tempfile
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "message_router", os.path.join(ROOT, "scripts", "message-router.py")
)
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

import sys
sys.path.insert(0, ROOT)
from msg_store import MessageStore


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def make_store():
    """Create a fresh in-memory-ish store using a temp file DB."""
    tmp = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(tmp)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT,
            task_id TEXT,
            parent_id TEXT,
            type TEXT NOT NULL,
            from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            subject TEXT,
            body TEXT,
            priority TEXT NOT NULL DEFAULT 'medium',
            source TEXT DEFAULT 'system',
            status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 5,
            metadata TEXT,
            created_at TEXT,
            attempted_at TEXT,
            delivered_at TEXT,
            acknowledged_at TEXT,
            archived_at TEXT,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            subject TEXT,
            participants TEXT,
            task_id TEXT,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    conn.commit()
    conn.close()
    store = MessageStore(db_path=tmp)
    store.migrate()
    return store, tmp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStalenessAlwaysPresent:
    def test_staleness_always_present(self):
        """Fresh message → output contains 'sent 0m ago'."""
        store, _ = make_store()
        mid = store.send(
            from_agent="alpha", to_agent="beta",
            type="route", subject="hello world",
        )
        msg = store.get(mid)
        out = mr.format_injection(msg, store)
        assert "sent 0m ago" in out, f"Expected 'sent 0m ago' in: {out!r}"

    def test_staleness_15m(self):
        """Message created 15 min ago → output contains 'sent 15m ago'."""
        store, tmp = make_store()
        mid = store.send(
            from_agent="alpha", to_agent="beta",
            type="route", subject="old message",
        )
        # Manually backdate created_at by 15 minutes
        past = (datetime.now(tz=timezone.utc) - timedelta(minutes=15)).isoformat()
        conn = sqlite3.connect(tmp)
        conn.execute("UPDATE messages SET created_at=? WHERE id=?", [past, mid])
        conn.commit()
        conn.close()

        msg = store.get(mid)
        out = mr.format_injection(msg, store)
        assert "sent 15m ago" in out, f"Expected 'sent 15m ago' in: {out!r}"


class TestFinalMarker:
    def test_final_marker_when_closes_thread(self):
        """Message with closes_thread=True → [FINAL] in output."""
        store, _ = make_store()
        mid = store.send(
            from_agent="alpha", to_agent="beta",
            type="route", subject="wrapping up",
            closes_thread=True,
        )
        msg = store.get(mid)
        out = mr.format_injection(msg, store)
        assert "[FINAL]" in out, f"Expected '[FINAL]' in: {out!r}"

    def test_no_final_marker_normal(self):
        """Normal message (no closes_thread) → [FINAL] NOT in output."""
        store, _ = make_store()
        mid = store.send(
            from_agent="alpha", to_agent="beta",
            type="route", subject="regular message",
        )
        msg = store.get(mid)
        out = mr.format_injection(msg, store)
        assert "[FINAL]" not in out, f"Unexpected '[FINAL]' in: {out!r}"


class TestLoopSuspect:
    def test_loop_suspect_via_re_prefixes(self):
        """Subject with 3 re: prefixes → [LOOP-SUSPECT depth=3]."""
        store, _ = make_store()
        mid = store.send(
            from_agent="alpha", to_agent="beta",
            type="route", subject="re: re: re: build the thing",
        )
        msg = store.get(mid)
        out = mr.format_injection(msg, store)
        assert "[LOOP-SUSPECT depth=3]" in out, f"Expected '[LOOP-SUSPECT depth=3]' in: {out!r}"

    def test_loop_suspect_via_parent_chain(self):
        """3-deep parent chain → [LOOP-SUSPECT depth=3]."""
        store, _ = make_store()
        # A (root)
        a_id = store.send(
            from_agent="alpha", to_agent="beta",
            type="route", subject="start",
        )
        # B replies to A (chain len 1)
        b_id = store.reply(a_id, body="reply 1")
        # C replies to B (chain len 2)
        c_id = store.reply(b_id, body="reply 2")
        # D replies to C (chain len 3)
        d_id = store.reply(c_id, body="reply 3")

        msg = store.get(d_id)
        out = mr.format_injection(msg, store)
        assert "[LOOP-SUSPECT depth=3]" in out, f"Expected '[LOOP-SUSPECT depth=3]' in: {out!r}"

    def test_depth_2_no_loop_marker(self):
        """2-deep parent chain → [LOOP-SUSPECT NOT in output."""
        store, _ = make_store()
        a_id = store.send(
            from_agent="alpha", to_agent="beta",
            type="route", subject="start",
        )
        b_id = store.reply(a_id, body="reply 1")
        c_id = store.reply(b_id, body="reply 2")

        msg = store.get(c_id)
        out = mr.format_injection(msg, store)
        assert "[LOOP-SUSPECT" not in out, f"Unexpected '[LOOP-SUSPECT' in: {out!r}"


class TestBodyFileGuidance:
    def test_body_file_has_final_guidance(self):
        """Body file written to /tmp must contain the do-NOT-reply guidance line."""
        store, _ = make_store()
        mid = store.send(
            from_agent="alpha", to_agent="beta",
            type="route", subject="check guidance",
        )
        msg = store.get(mid)
        mr.format_injection(msg, store)
        msg_file = Path(f"/tmp/agent-msg-{mid}.md")
        assert msg_file.exists(), f"Body file not found: {msg_file}"
        content = msg_file.read_text()
        assert "do NOT reply unless it explicitly asks a question" in content, \
            f"Guidance line missing from body file. Content: {content!r}"


class TestMalformedMetadata:
    def test_malformed_metadata_does_not_crash(self):
        """Bad JSON in metadata → no crash, [FINAL] not present."""
        store, tmp = make_store()
        mid = store.send(
            from_agent="alpha", to_agent="beta",
            type="route", subject="bad metadata test",
        )
        # Corrupt the metadata
        conn = sqlite3.connect(tmp)
        conn.execute("UPDATE messages SET metadata=? WHERE id=?", ["{not json", mid])
        conn.commit()
        conn.close()

        msg = store.get(mid)
        # Must not raise
        out = mr.format_injection(msg, store)
        assert "[FINAL]" not in out, f"Unexpected '[FINAL]' in: {out!r}"


class TestStalenessNullCreatedAt:
    def test_staleness_fallback_when_created_at_null(self):
        """NULL created_at → fallback token '(sent ?m ago)' always present; no raise."""
        store, tmp = make_store()
        mid = store.send(
            from_agent="alpha", to_agent="beta",
            type="route", subject="null timestamp test",
        )
        # Force created_at to NULL
        conn = sqlite3.connect(tmp)
        conn.execute("UPDATE messages SET created_at=NULL WHERE id=?", [mid])
        conn.commit()
        conn.close()

        msg = store.get(mid)
        # Must not raise
        out = mr.format_injection(msg, store)
        assert "sent" in out, f"Expected 'sent' in output (fallback token): {out!r}"
        assert "ago" in out, f"Expected 'ago' in output (fallback token): {out!r}"


class TestParentWalkStoreGetError:
    def test_parent_walk_survives_store_get_error(self):
        """If store.get() raises during parent-chain walk, format_injection must not raise."""

        class BoomStore:
            def get(self, mid):
                raise RuntimeError("boom")

        msg = {
            "id": "test-msg-boom",
            "parent_id": "some-parent-id",
            "from_agent": "alpha",
            "to_agent": "beta",
            "type": "route",
            "subject": "resilience test",
            "body": "hello",
            "priority": "medium",
            "metadata": None,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        # Must not raise; must still return a string containing [MSG from
        out = mr.format_injection(msg, BoomStore())
        assert isinstance(out, str), "Expected a string return value"
        assert "[MSG from" in out, f"Expected '[MSG from' in: {out!r}"
