#!/usr/bin/env python3
"""approval_backfill.py — R7a §2.1 filesystem-store backfill migrator.

One-time, IDEMPOTENT import of every `state/approvals/pending/*.json` into the
canonical `approval_requests` ledger so the formerly filesystem-only agent
approvals become answerable on every surface (web/iOS/watch). Each imported row
carries provenance:

  origin  = 'backfill_fs'         (migration provenance, §1.1)
  op_key  = the original FS id     (the id preserved, dedup key)
  kind    = 'approval'

Idempotency: a re-run imports NOTHING already present (dedup by op_key+origin via
ApprovalStore.find_by_op_key), INDEPENDENT of the row's later status — a
backfilled row that has since been answered is never re-shadowed. Live count at
spec time: 1 stale April-era file — but this is written generically.

In-flight guarantee (§2.4): the migrator only READS the frozen filesystem store
and WRITES the canonical ledger. It never deletes or rewrites a pending file, so
a cutover rolls back by reverting the API to filesystem reads (store untouched).

Service boundary: this is a server-side maintenance tool on the SAME plane as
the store (like approval_schema itself), not a provider adapter — it constructs
ApprovalStore directly, honouring APPROVAL_DB_PATH for hermetic tests exactly as
approval.py's _store() does. It is NOT invoked from any web/adapter caller.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_schema import ApprovalStore


def _default_pending_dir():
    orchestra = os.environ.get("ORCHESTRA_DIR") or os.path.expanduser(
        "~/scripts/agent-orchestra")
    return os.path.join(orchestra, "state", "approvals", "pending")


def _question_of(data, fid):
    # Prefer a human ask; fall back through the FS shapes, then the id.
    return (data.get("title") or data.get("action") or data.get("question")
            or data.get("description") or fid)


def backfill_fs(store=None, pending_dir=None, db_path=None):
    """Import every pending/*.json into approval_requests idempotently.

    store       : an ApprovalStore (tests pass a scratch one). If None, build
                  ApprovalStore(db_path or $APPROVAL_DB_PATH) — production sets
                  neither and writes the live tasks.db.
    pending_dir : the frozen filesystem store; default $ORCHESTRA_DIR/state/
                  approvals/pending.
    Returns {imported, skipped, ids}.
    """
    if store is None:
        store = ApprovalStore(db_path=db_path or os.environ.get("APPROVAL_DB_PATH") or None)
    store.migrate()
    pending_dir = pending_dir or _default_pending_dir()

    imported, skipped, ids = 0, 0, []
    p = Path(pending_dir)
    if not p.is_dir():
        return {"imported": 0, "skipped": 0, "ids": []}

    for f in sorted(p.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            skipped += 1
            continue
        if not isinstance(data, dict):
            skipped += 1
            continue
        fid = str(data.get("id") or f.stem)
        # IDEMPOTENCY gate: already backfilled (any status) -> skip.
        if store.find_by_op_key(fid, origin="backfill_fs"):
            skipped += 1
            continue
        rid = store.create(
            from_agent=str(data.get("agent_id") or data.get("from") or "unknown"),
            question=str(_question_of(data, fid)),
            worker_kind="node",   # a frozen FS row has no live pane to resume
            op_key=fid,
            summary=data.get("description"),
            risk_level=data.get("risk_level"),
            kind="approval",
            origin="backfill_fs",
        )
        imported += 1
        ids.append(rid)

    return {"imported": imported, "skipped": skipped, "ids": ids}


def main(argv=None):
    ap = argparse.ArgumentParser(description="R7a FS approvals backfill migrator")
    ap.add_argument("--pending-dir", default=None,
                    help="override the frozen pending/ dir (default $ORCHESTRA_DIR/state/approvals/pending)")
    ap.add_argument("--db-path", default=None,
                    help="override the tasks.db path (default $APPROVAL_DB_PATH or live)")
    ap.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = ap.parse_args(argv)
    res = backfill_fs(pending_dir=args.pending_dir, db_path=args.db_path)
    if args.json:
        print(json.dumps(res))
    else:
        print(f"[approval_backfill] imported={res['imported']} skipped={res['skipped']}")
        for i in res["ids"]:
            print(f"  + {i}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
