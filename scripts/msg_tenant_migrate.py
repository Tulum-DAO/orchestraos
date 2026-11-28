#!/usr/bin/env python3
"""Close originals that were re-delivered by hand during the message-tenant split (2026-11-27).

Mail written under one tenant and read under another was stranded; the operator re-sent it under the
right tenant. Once readers also read the legacy name ([messages] also_read), the stranded originals
would show up beside their re-deliveries. This closes each named original, only while it is still pending:

    status -> 'retracted', error -> 're-delivered as <NEW> (message tenant fix)'

Dry run by default (prints what it would do, writes nothing); --apply writes. No bulk tenant rewrite:
history keeps the tenant it was written with.

  python3 scripts/msg_tenant_migrate.py --close msg_OLD=msg_NEW [--close ...] [--db PATH] [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.append(ROOT)
from orchestra_cli.settings import data_dir  # noqa: E402


def parse_pairs(items: list) -> list:
    out = []
    for it in items:
        old, sep, new = it.partition("=")
        if not sep or not old.strip() or not new.strip():
            raise SystemExit(f"--close wants OLD=NEW, got {it!r}")
        out.append((old.strip(), new.strip()))
    return out


def run(db: str, pairs: list, apply: bool) -> list:
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    report = []
    try:
        for old, new in pairs:
            row = conn.execute("SELECT id, status, to_agent, tenant_id FROM messages WHERE id = ?",
                               (old,)).fetchone()
            if row is None:
                report.append({"id": old, "action": "skip", "why": "not found"})
                continue
            if row["status"] != "pending":
                report.append({"id": old, "action": "skip", "why": f"status {row['status']}"})
                continue
            entry = {"id": old, "to": row["to_agent"], "tenant": row["tenant_id"],
                     "action": "close" if apply else "would close", "redelivered_as": new}
            if apply:
                cur = conn.execute(
                    "UPDATE messages SET status = 'retracted', error = ? WHERE id = ? AND status = 'pending'",
                    (f"re-delivered as {new} (message tenant fix)", old))
                if cur.rowcount != 1:
                    entry = {"id": old, "action": "skip", "why": "changed under us"}
            report.append(entry)
        if apply:
            conn.commit()
    finally:
        conn.close()
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--close", action="append", default=[], metavar="OLD=NEW")
    ap.add_argument("--db", default=os.environ.get("MSG_DB_PATH") or os.path.join(str(data_dir()), "state", "tasks.db"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    pairs = parse_pairs(a.close)
    if not pairs:
        ap.error("nothing to do: pass at least one --close OLD=NEW")
    for r in run(a.db, pairs, a.apply):
        print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
