"""Discover SQLite DBs under an Orchestra data dir for WAL checkpointing."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SKIP_PARTS = {"node_modules", ".venv", "worktrees", "parallel", ".git", "dream"}


def iter_dbs(data_dir: Path, *, limit: int = 200):
    """Yield *.db paths under data_dir, skipping known junk trees."""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        return
    n = 0
    for p in data_dir.rglob("*.db"):
        if any(part in SKIP_PARTS for part in p.parts):
            continue
        if not p.is_file():
            continue
        yield p
        n += 1
        if n >= limit:
            return


def checkpoint(db: Path) -> None:
    conn = sqlite3.connect(str(db), timeout=10)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def run(data_dir: Path | None = None) -> dict:
    root = Path(data_dir or os.environ.get("ORCHESTRA_DIR") or ".")
    ok = fail = 0
    errors = []
    for db in iter_dbs(root):
        try:
            checkpoint(db)
            ok += 1
        except Exception as e:  # noqa: BLE001 — report and continue
            fail += 1
            errors.append(f"{db}: {e}")
    return {"data_dir": str(root), "ok": ok, "fail": fail, "errors": errors}


if __name__ == "__main__":
    rep = run()
    print(f"db-checkpoint: data={rep['data_dir']} ok={rep['ok']} fail={rep['fail']}")
    for e in rep["errors"]:
        print(e, flush=True)
    raise SystemExit(0 if rep["fail"] == 0 else 1)
