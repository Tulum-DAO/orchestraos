"""The fact redactor next to bus.py: scrub_fact_text hides credentials, leaves ordinary text alone,
is idempotent, and is what bus.ingest stores. Synthetic values only.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bus  # noqa: E402
import redact_secrets as R  # noqa: E402
from facts_schema import make_fact  # noqa: E402


def _tok(*parts):
    return "".join(parts)


TG = _tok("1234567890", ":", "AAHk3mPq9xZr7Lw2Vb8Nc4Fd6Gs1Jt5Yu0E")  # pragma: allowlist secret (fake test fixture)
GH = _tok("gh", "p_", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8")      # pragma: allowlist secret (fake test fixture)
SK = _tok("sk", "-", "abcdEFGHijklMNOPqrstUVWX1234")                # pragma: allowlist secret (fake test fixture)
BEARER = _tok("Zq8", "Xw7Vu6Ts5Rq4Po3Nm2Lk1Jh0")                    # pragma: allowlist secret (fake test fixture)

SECRETS = [
    ("telegram-shaped", f"the bot token is {TG} keep it", TG),
    ("github", f"use {GH} for the push", GH),
    ("api key", f"export KEY={SK}", SK),
    ("bearer", f"curl -H 'Authorization: Bearer {BEARER}' https://example.invalid", BEARER),
]
PLAIN = [
    "keep the dark theme on every page",
    "the live_test_suite passed",
    "primary_key: id",
    "ship the beta on friday",
]


@pytest.mark.parametrize("name,text,secret", SECRETS, ids=[s[0] for s in SECRETS])
def test_a_credential_is_hidden(name, text, secret):
    out = R.scrub_fact_text(text)
    assert secret not in out
    assert R.OUT_PLACEHOLDER in out or R.PLACEHOLDER in out


@pytest.mark.parametrize("text", PLAIN)
def test_ordinary_text_is_untouched(text):
    assert R.scrub_fact_text(text) == text


@pytest.mark.parametrize("text", [s[1] for s in SECRETS] + PLAIN)
def test_scrubbing_twice_changes_nothing(text):
    once = R.scrub_fact_text(text)
    assert R.scrub_fact_text(once) == once


def test_bus_ingest_stores_the_scrubbed_text(tmp_path, monkeypatch):
    monkeypatch.delenv("REDACT_SECRETS_PATH", raising=False)
    db = str(tmp_path / "facts.db")
    fact = make_fact(ts="2026-11-25T10:00:00.000Z", kind="fact", text=f"the bot token is {TG} keep it",
                     actor="agent", provenance={"source_kind": "test", "source_id": "t:1"})
    bus.ingest(db, [fact], source_kind="test", last_source_event_ts=None)
    (stored,) = sqlite3.connect(db).execute("SELECT text FROM facts").fetchone()
    assert TG not in stored
    assert stored == R.scrub_fact_text(fact["text"])
