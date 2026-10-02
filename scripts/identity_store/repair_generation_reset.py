#!/usr/bin/env python3
"""Repair a lineage whose generation counter was reset backwards.

WHY THIS IS NOT COSMETIC. gm's counter ran 1..87 and then restarted at 1 (swap 194,
2026-09-26; cause fixed in #152/#153). The live head is now generation 2, so the NEXT
rotation mints 3 — and `generations` carries `UNIQUE(root, generation)`, with 3, 36, 37,
38, 39 ... already taken by the pre-reset history. The restarted sequence walks straight
into them: the first collision silently takes the REUSE branch of _resolve_or_insert_green
and a live seat adopts a retired 2026-09 archive row's identity.

Renumbering the post-reset tail to continue past the lineage's high-water mark removes
that collision course and makes the history readable in one order again.

WHAT IT CHANGES, and nothing else:
  * generations.generation for the post-reset rows only (gm: 1,2,3 -> 88,89,90).
  * the matching flat `generation` in registry.json and agent-sessions.json for the
    lineage's CURRENT head, so DB-first and flat do not disagree afterwards.

WHAT IT DELIBERATELY DOES NOT CHANGE:
  * row ids, session ids, timestamps, the canonical pointer, or swap records — identity
    and history stay exactly as they were; only the label moves.
  * registry ALIAS KEYS. gm's post-reset seats are named `gm-gen1` and `gm-g3`, and those
    names encode the old number. Renaming registry keys would touch tmux sessions, handoff
    files and prompts, so the alias names stay and are REPORTED as residue instead.
  * the 32 generation numbers gm never recorded at all. Those rotations were never written
    and cannot be recovered here.

Dry-run by default: it prints every intended change and touches nothing.

Usage:
  repair_generation_reset.py                 # dry run, every affected root
  repair_generation_reset.py --root gm       # dry run, one root
  repair_generation_reset.py --root gm --apply
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

ORCHESTRA_DIR = os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))


def db_path(orch: str) -> str:
    return os.path.join(orch, "state", "orchestra-registry.db")


def find_reset_tail(conn, root: str):
    """Rows whose generation went BACKWARDS relative to the lineage's running high-water
    mark, in insertion order. Returns [(id, old_generation), ...] oldest first.

    Insertion order (id), not generation order: the tail is defined by when the rows were
    written, which is the only thing that says 'this came after the lineage had already
    reached 87'.
    """
    rows = conn.execute(
        "SELECT id, generation FROM generations WHERE root=? ORDER BY id", (root,)).fetchall()
    high, tail = 0, []
    for rid, gen in rows:
        if gen < high:
            tail.append((rid, gen))
        else:
            high = gen
    return tail, high


def plan(conn, root: str):
    """What the repair would do to one root, as data. Empty list => nothing to repair."""
    tail, high = find_reset_tail(conn, root)
    if not tail:
        return []
    taken = {g for (g,) in conn.execute(
        "SELECT generation FROM generations WHERE root=?", (root,))}
    moves, nxt = [], high
    for rid, old in tail:
        nxt += 1
        while nxt in taken:          # never collide with a number the lineage already used
            nxt += 1
        taken.add(nxt)
        moves.append({"id": rid, "root": root, "old": old, "new": nxt})
    return moves


def affected_roots(conn):
    roots = [r[0] for r in conn.execute(
        "SELECT DISTINCT root FROM generations WHERE root IS NOT NULL")]
    return [r for r in roots if find_reset_tail(conn, r)[0]]


def _head_generation(conn, root: str):
    row = conn.execute(
        "SELECT g.id, g.generation FROM canonical c JOIN generations g ON g.id = c.generation_id "
        "WHERE c.root=?", (root,)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _patch_flat(path: str, root: str, new_gen: int, apply: bool):
    """Keep the flat files agreeing with the DB for the lineage's head. Returns a note."""
    if not os.path.exists(path):
        return f"  (absent, skipped) {path}"
    with open(path) as fh:
        doc = json.load(fh)
    bag = doc.get("agents", doc)
    entry = bag.get(root)
    if not isinstance(entry, dict):
        return f"  (no {root!r} entry) {os.path.basename(path)}"
    old = entry.get("generation")
    if old == new_gen:
        return f"  (already {new_gen}) {os.path.basename(path)}"
    if apply:
        entry["generation"] = new_gen
        tmp = path + ".repair-tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, indent=2)
        os.replace(tmp, path)
    return f"  {os.path.basename(path)}: {root}.generation {old!r} -> {new_gen}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", help="repair one root (default: every affected root)")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--orchestra-dir", default=ORCHESTRA_DIR)
    args = ap.parse_args()

    dbp = db_path(args.orchestra_dir)
    if not os.path.exists(dbp):
        print(f"no identity store at {dbp}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(dbp)
    conn.row_factory = sqlite3.Row
    try:
        roots = [args.root] if args.root else affected_roots(conn)
        if not roots:
            print("no lineage carries a backwards generation reset — nothing to repair")
            return 0

        total = []
        for root in roots:
            moves = plan(conn, root)
            head_id, head_gen = _head_generation(conn, root)
            print(f"\n{root}: {len(moves)} row(s) to renumber"
                  + ("" if moves else " (nothing to repair)"))
            for m in moves:
                mark = "   <- canonical head" if m["id"] == head_id else ""
                print(f"  id={m['id']:<6} generation {m['old']} -> {m['new']}{mark}")
            total.extend(moves)
            new_head = next((m["new"] for m in moves if m["id"] == head_id), None)
            if new_head is not None:
                print(f"  flat files follow the head to {new_head}:")
                for p in (os.path.join(args.orchestra_dir, "registry.json"),
                          os.path.join(args.orchestra_dir, "state", "agent-sessions.json")):
                    print(_patch_flat(p, root, new_head, apply=False))

        if not args.apply:
            print(f"\nDRY RUN — {len(total)} row(s) would change. Nothing was written. "
                  f"Re-run with --apply.")
            return 0

        backup = f"{dbp}.pre-renumber-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        shutil.copy2(dbp, backup)
        print(f"\nbackup: {backup}")

        # One transaction: a half-renumbered lineage is worse than a reset one.
        conn.execute("BEGIN IMMEDIATE")
        try:
            for m in total:
                # Park it out of the way first — the new number may be one another row in
                # this same batch is still sitting on, and UNIQUE(root, generation) is
                # checked per statement.
                conn.execute("UPDATE generations SET generation = -generation WHERE id=?", (m["id"],))
            for m in total:
                conn.execute("UPDATE generations SET generation=? WHERE id=?", (m["new"], m["id"]))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        for root in roots:
            head_id, _ = _head_generation(conn, root)
            new_head = next((m["new"] for m in total
                             if m["id"] == head_id and m["root"] == root), None)
            if new_head is not None:
                for p in (os.path.join(args.orchestra_dir, "registry.json"),
                          os.path.join(args.orchestra_dir, "state", "agent-sessions.json")):
                    print(_patch_flat(p, root, new_head, apply=True))

        print(f"\napplied: {len(total)} row(s) renumbered. Roll back with:\n"
              f"  cp {backup} {dbp}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
