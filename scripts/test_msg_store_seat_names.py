"""Sending by name: an agent's name or old name reaches its id (exact only for agents); an address no agent has
ever had is refused at once with the closest names; a known agent that is not running is still written, and
the sender is told. The assistant and the operator are always valid addresses."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import msg_store as ms  # noqa: E402
from scripts.identity_store import orchestra_db as odb  # noqa: E402
from scripts.identity_store import seat_names as sn  # noqa: E402
from scripts.identity_store import seat_resolve as sr  # noqa: E402
from scripts.test_msg_store_kick_on_send import SCHEMA  # noqa: E402

# ids no real tmux session on the test box can share
PM, DEV = "zz-pm-demo", "zz-dev-demo"


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    db = data / "state" / "tasks.db"
    c = sqlite3.connect(str(db)); c.executescript(SCHEMA); c.close()
    (data / "registry.json").write_text(json.dumps({"agents": {PM: {"tmux_session": PM}, DEV: {"tmux_session": DEV}}}))
    (data / "state" / "agent-sessions.json").write_text(json.dumps({PM: {"tmux_session": PM}, DEV: {"tmux_session": DEV}}))
    idb = str(data / "state" / "orchestra-registry.db")
    odb.init_db(idb)
    ic = odb.get_connection(idb)
    for r in (PM, DEV):
        ic.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?, 'T1', 'claude')", (r,))
    sn.set_name(ic, PM, "Former")
    sn.set_name(ic, PM, "Sample")                    # "Former" is now its old name
    ic.close()
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setattr(ms, "ORCHESTRA_DIR", data)
    monkeypatch.setenv("MSG_STORE_NO_KICK", "1")
    monkeypatch.setenv("UNKNOWN_RECIPIENT_REFUSE", "1")
    monkeypatch.setenv("ADDRESSABILITY_LOG_FILE", str(tmp_path / "addr.log"))
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setattr(sr, "assistant_display_name", lambda: "Example")
    s = ms.MessageStore(db_path=str(db))
    s.migrate()
    return s, db


def _to(db, msg_id):
    c = sqlite3.connect(str(db))
    try:
        r = c.execute("SELECT to_agent, metadata, conversation_id FROM messages WHERE id=?", (msg_id,)).fetchone()
        return r[0], json.loads(r[1] or "{}"), r[2]
    finally:
        c.close()


def test_a_name_or_old_name_reaches_the_id_and_keeps_one_thread(store):
    s, db = store
    a = s.send(from_agent=DEV, to_agent="Sample", type="message", subject="status", body="a real body")
    b = s.send(from_agent=DEV, to_agent="former", type="message", subject="status", body="a real body")
    c = s.send(from_agent=DEV, to_agent=PM, type="message", subject="status", body="a real body")
    (ta, ma, ca), (tb, mb, cb), (tc, mc, cc) = _to(db, a), _to(db, b), _to(db, c)
    assert ta == tb == tc == PM
    assert ma["addressed_as"] == "Sample" and mb["addressed_as"] == "former" and "addressed_as" not in mc
    assert ca == cb == cc                            # one conversation whatever it was called


def test_agents_are_exact_a_typo_is_refused_with_the_closest_name_and_nothing_is_written(store):
    s, db = store
    with pytest.raises(ms.UnknownRecipient, match=r"Did you mean: .*Sample \(zz-pm-demo\)"):
        s.send(from_agent=DEV, to_agent="Sampl", type="message", subject="x", body="a real body")
    with pytest.raises(ms.UnknownRecipient, match="no agent has ever had"):
        s.send(from_agent=DEV, to_agent="nobody-at-all", type="message", subject="x", body="a real body")
    c = sqlite3.connect(str(db))
    assert c.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    c.close()


def test_the_refusal_is_switched_on_per_install_by_its_flag(store, monkeypatch, tmp_path):
    s, db = store
    monkeypatch.delenv("UNKNOWN_RECIPIENT_REFUSE")
    assert s.send(from_agent=DEV, to_agent="nobody-yet", type="message", subject="x", body="a real body")   # off by default
    (tmp_path / "data" / "state" / "unknown-recipient-refuse.flag").write_text("")
    with pytest.raises(ms.UnknownRecipient):
        s.send(from_agent=DEV, to_agent="nobody-now", type="message", subject="x", body="a real body")


def test_the_refusal_can_be_switched_off_and_overridden(store, monkeypatch):
    s, db = store
    assert s.send(from_agent=DEV, to_agent="nobody-at-all", type="message", subject="x", body="a real body",
                  allow_unaddressable=True)
    monkeypatch.setenv("UNKNOWN_RECIPIENT_REFUSE", "0")
    assert s.send(from_agent=DEV, to_agent="nobody-else", type="message", subject="x", body="a real body")


def test_the_assistant_by_id_or_name_and_the_operator_are_always_reachable(store):
    s, db = store
    for addr, want in (("arturo", "arturo"), ("Example", "arturo"), ("operator", "operator")):
        mid = s.send(from_agent=DEV, to_agent=addr, type="message", subject="x", body="a real body")
        assert _to(db, mid)[0] == want, addr


def test_a_known_agent_that_is_not_running_is_written_and_the_sender_is_told(store):
    s, db = store
    mid = s.send(from_agent=PM, to_agent=DEV, type="message", subject="x", body="a real body")
    assert _to(db, mid)[0] == DEV
    assert s.last_send_note and "not running" in s.last_send_note and "48 hours" in s.last_send_note
    mid = s.send(from_agent=DEV, to_agent="Sample", type="message", subject="x", body="a real body")
    assert s.last_send_note and DEV not in s.last_send_note and PM in s.last_send_note


def test_a_resolver_failure_never_blocks_a_send(store, monkeypatch):
    s, db = store
    def boom(*a, **k):
        raise RuntimeError("resolver down")
    monkeypatch.setattr(sr, "load_directory", boom)
    mid = s.send(from_agent=DEV, to_agent="Sample", type="message", subject="x", body="a real body")
    assert _to(db, mid)[0] == "Sample"                # fails open: written as addressed, exactly as before



def test_a_name_two_projects_share_is_refused_with_both_full_names(store, monkeypatch):
    s, db = store
    from scripts.identity_store import seat_resolve as _sr
    two = [{"id": "zz-dev-a", "name": "dev", "aliases": [], "alive": True, "project": {"slug": "a", "label": "A"}, "full_name": "A · dev"},
           {"id": "zz-dev-b", "name": "dev", "aliases": [], "alive": True, "project": {"slug": "b", "label": "B"}, "full_name": "B · dev"}]
    monkeypatch.setattr(_sr, "load_directory", lambda *a, **k: two)
    with pytest.raises(ms.AmbiguousRecipient, match=r"A · dev \(zz-dev-a\); B · dev \(zz-dev-b\)"):
        s.send(from_agent=DEV, to_agent="dev", type="message", subject="x", body="a real body")
    mid = s.send(from_agent=DEV, to_agent="B · dev", type="message", subject="x", body="a real body")
    assert _to(db, mid)[0] == "zz-dev-b"                               # the full name reaches it
