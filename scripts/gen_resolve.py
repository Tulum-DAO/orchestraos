"""Resolve a lineage's predecessor generation DB-FIRST.

The flat registry.json is a PROJECTION of state/orchestra-registry.db (live
since the 09-02 cutover) and can lag; rotation primitives are identity actors
and must read the write-truth. This is the ONE shared resolver both
promote_successor and rotate_agent consume.

Contract (consensus spec, agy+r-a-b folds):
- PRIMARY: the DB (canonical.root -> generation_id -> generations.generation),
  opened READ-ONLY (mode=ro URI, 5s timeout, closed immediately). Never
  write-locks the identity store; call PRE-txn only (never inside a
  BEGIN IMMEDIATE — RED 11).
- FALLBACK: the flat value, only when the DB is absent/unreadable/has no row.
- DISAGREEMENT (both present, different): DB wins + the alarm seam fires —
  projector-coherence is INCIDENT-grade post-cutover (RED 10).
- NEVER fabricate.
"""
import os
import sqlite3
import sys


def _default_alarm(msg: str) -> None:
    # Incident-grade surfacing: stderr (caller logs capture it) + best-effort
    # gm message. Import inside so the resolver has no hard msg_store dep.
    print(f"[gen-resolve ALARM] {msg}", file=sys.stderr)
    try:
        import subprocess
        od = os.environ.get("ORCHESTRA_DIR", os.path.expanduser(
            "~/orchestra"))
        subprocess.run(
            ["python3", os.path.join(od, "msg_store.py"), "send",
             "--from", "gen-resolve", "--to", "gm", "--type", "alert",
             "--subject", "projection skew detected (gen-resolve)",
             "--body", msg],
            timeout=10, capture_output=True)
    except Exception:  # noqa: BLE001 — alarm is best-effort beyond stderr
        pass


def resolve_predecessor_generation(root, flat_value, *, orchestra_dir=None,
                                   alarm=_default_alarm):
    """Return the authoritative generation int for ``root``, or None.

    DB-first; flat fallback; DB-wins-plus-alarm on disagreement; never
    fabricates (null-in-both => None — the CALLER owns fail-closed/refusal).
    """
    od = orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/orchestra"))
    dbp = os.path.join(str(od), "state", "orchestra-registry.db")
    db_gen = None
    if os.path.exists(dbp):
        try:
            conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=5.0)
            try:
                row = conn.execute(
                    "SELECT g.generation FROM canonical c "
                    "JOIN generations g ON g.id = c.generation_id "
                    "WHERE c.root = ?", (str(root),)).fetchone()
                if row is not None and isinstance(row[0], int):
                    db_gen = row[0]
            finally:
                conn.close()
        except sqlite3.Error:
            db_gen = None  # unreadable => fallback path

    flat_gen = flat_value if isinstance(flat_value, int) else None

    if db_gen is not None:
        if flat_gen is not None and flat_gen != db_gen:
            alarm(f"projection skew for {root!r}: flat generation={flat_gen} "
                  f"but orchestra-registry.db says {db_gen} — DB wins; the "
                  f"projector/flat file is stale or foreign-edited (RED 10)")
        return db_gen
    return flat_gen
