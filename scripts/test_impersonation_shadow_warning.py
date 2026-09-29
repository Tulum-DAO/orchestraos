"""The shadow-mode impersonation warning must be LOUD (stderr), not just logged.

Two seats wrote rows under each other's identity on 2026-09-29 and neither got any
signal at the time, because shadow mode only wrote to a log file nobody tails.
"""
import os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _db(tmp):
    state = Path(tmp) / "state"; state.mkdir(parents=True, exist_ok=True)
    db = state / "tasks.db"
    c = sqlite3.connect(str(db))
    c.executescript("""
    CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT,
      task_id TEXT, parent_id TEXT, type TEXT NOT NULL, from_agent TEXT NOT NULL,
      to_agent TEXT NOT NULL, subject TEXT, body TEXT,
      priority TEXT NOT NULL DEFAULT 'medium', source TEXT DEFAULT 'system',
      status TEXT NOT NULL DEFAULT 'pending', retry_count INTEGER NOT NULL DEFAULT 0,
      max_retries INTEGER NOT NULL DEFAULT 5, metadata TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')), attempted_at TEXT,
      delivered_at TEXT, acknowledged_at TEXT, archived_at TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, subject TEXT,
      participants TEXT, task_id TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now')));""")
    c.close()
    sys.path.insert(0, str(ROOT))
    import msg_store
    msg_store.MessageStore(db_path=str(db)).migrate()
    return db


SNIP = """
import sys; sys.path.insert(0, {root!r})
import msg_store
msg_store.MessageStore().send(from_agent={frm!r}, to_agent={to!r},
    type="task", subject="s", body="a body long enough to not be trivial")
print("SENT")
"""


def run(tmp, db, frm, to, caller):
    """Run a send in a subprocess with a forced caller identity."""
    env = dict(os.environ, MSG_DB_PATH=str(db), ORCHESTRA_DIR=str(tmp))
    env.pop("IMPERSONATION_REFUSE", None)          # shadow mode
    env.pop("TMUX_PANE", None)
    code = SNIP.format(root=str(ROOT), frm=frm, to=to)
    if caller:
        # sender_identity() reads TMUX_PANE then shells to tmux; stub it instead.
        code = (f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
                f"import msg_store\n"
                f"msg_store.sender_identity = lambda: {caller!r}\n"
                f"msg_store.MessageStore().send(from_agent={frm!r}, to_agent={to!r},"
                f" type='task', subject='s', body='a body long enough')\n"
                f"print('SENT')\n")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)


def main():
    tmp = tempfile.mkdtemp()
    db = _db(tmp)
    fails = []

    r = run(tmp, db, "plan", "gm", caller="build")      # impersonation
    if "SENT" not in r.stdout:
        fails.append(f"shadow mode must still WRITE the row: {r.stderr[-300:]}")
    if "WARNING" not in r.stderr or "SHADOW" not in r.stderr:
        fails.append(f"no loud stderr warning on impersonated send. stderr={r.stderr[-300:]!r}")

    r = run(tmp, db, "plan", "plan", caller=None)       # self-addressed, no identity
    if "WARNING" not in r.stderr:
        fails.append(f"no warning on self-addressed send. stderr={r.stderr[-300:]!r}")

    r = run(tmp, db, "build", "gm", caller="build")     # legitimate
    if "WARNING" in r.stderr:
        fails.append(f"warned on a perfectly legitimate send: {r.stderr[-300:]!r}")

    if fails:
        print("FAIL"); [print("  -", f) for f in fails]; return 1
    print("PASS: warns loudly on impersonated + self-addressed, silent on legitimate, "
          "and shadow mode still writes the row")
    return 0


# Guarded: this file is named test_*.py, so pytest IMPORTS it during collection. A bare
# sys.exit() at module level raises SystemExit there, which pytest reports as INTERNALERROR
# and aborts the whole scripts/ shard — 620 tests stopped running. Still runs standalone.
if __name__ == "__main__":
    sys.exit(main())
