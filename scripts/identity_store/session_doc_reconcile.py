"""Session-document status reconcile — Identity Layer v1 (gm ruling msg_61b4581b).

Item-(a) status_reconcile brought a parked seat's typed ``canonical.status`` AND its
``registry.json`` agent document to 'parked', but never touched its ``agent-sessions.json``
SESSION document — whose own ``status`` field keeps the migration-era legacy zoo
('quiescent'/'killed'/'online'/'active'/...). The faithful projector serves that session doc
verbatim, so registry says 'parked' while sessions says the legacy value: a registry-vs-
sessions split M1 (per-file) cannot see, and a flat UPDATE is futile (the regenerator reverts
it in ~30s).

FIX: for each canonical seat whose ``canonical.status`` is a valid enum value, if its session
document's ``status`` field differs, set the session-doc ``status`` to the canonical status
DB-FIRST via the sanctioned ``identity_writer.update_session(full_record=<complete doc>)``
(read-modify-write of the COMPLETE doc; the projector serves it verbatim), then one
``project_now``. Dry-run by default; ``--apply``. Scoped to canonical MEMBERS only (archives
are governed by the swap-archive rule; provisional aliases are registry-only).
"""
import argparse
import json
import os

from scripts.identity_store import identity_writer, orchestra_db, status_vocab


def _db_path(od):
    return os.path.join(od, "state", "orchestra-registry.db")


def reconcile(orchestra_dir, *, apply=False):
    conn = orchestra_db.get_connection(_db_path(orchestra_dir))
    try:
        canon = {r["root"]: r["status"] for r in conn.execute(
            "SELECT root, status FROM canonical")}
        docs = {}
        for r in conn.execute("SELECT key, payload_json FROM source_records "
                              "WHERE file='agent-sessions.json' AND kind='session'"):
            docs[r["key"]] = json.loads(r["payload_json"])
    finally:
        conn.close()

    changes = []      # (root, old_doc_status, canonical_status)
    for root, cstatus in canon.items():
        doc = docs.get(root)
        if doc is None:
            continue                      # no session doc to reconcile
        if cstatus not in status_vocab.STATUS_ENUM:
            continue                      # never write a non-enum onto the doc
        if doc.get("status") != cstatus:
            changes.append((root, doc.get("status"), cstatus))

    if apply and changes:
        for root, _old, cstatus in changes:
            doc = dict(docs[root])
            doc["status"] = cstatus
            identity_writer.update_session(orchestra_dir, root, {}, full_record=doc)
        identity_writer.project_now(orchestra_dir)

    by_old = {}
    for _root, old, _new in changes:
        by_old[old] = by_old.get(old, 0) + 1
    return {"applied": bool(apply), "scanned": len(canon),
            "changed": len(changes), "by_old_status": by_old,
            "examples": [c[0] for c in changes[:20]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="session-document status reconcile (dry-run default)")
    ap.add_argument("--dir", default=os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(reconcile(a.dir, apply=a.apply), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
