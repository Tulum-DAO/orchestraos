#!/usr/bin/env bash
# db-checkpoint.sh — Force WAL checkpoint on SQLite databases under the Orchestra data dir.
# Prevents WAL bloat that can corrupt DBs on crash.
#
# Discovers *.db under $ORCHESTRA_DIR (or orchestra_cli.settings.data_dir). Never hardcodes
# a home path. Skips node_modules, .venv, worktrees, and parallel scratch trees.
# Cron example: */5 * * * * cd <checkout> && ORCHESTRA_DIR=<data> bash scripts/db-checkpoint.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${ORCHESTRA_DIR:-}" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then
    ORCHESTRA_DIR="$("$ROOT/.venv/bin/python" -c 'from orchestra_cli.settings import data_dir; print(data_dir())' 2>/dev/null || true)"
  fi
  ORCHESTRA_DIR="${ORCHESTRA_DIR:-$ROOT}"
fi
DATA="$(cd "$ORCHESTRA_DIR" && pwd)"

# Find candidate DBs: prefer state/, also top-level *.db under data. Cap depth to stay cheap.
mapfile -t DBS < <(
  find "$DATA" \
    \( -path '*/node_modules/*' -o -path '*/.venv/*' -o -path '*/worktrees/*' \
       -o -path '*/parallel/*' -o -path '*/.git/*' -o -path '*/dream/*' \) -prune -o \
    -type f -name '*.db' -print 2>/dev/null \
  | head -n 200
)

ok=0; fail=0; skip=0
for db in "${DBS[@]:-}"; do
  [ -f "$db" ] || continue
  # Only checkpoint if a sibling -wal exists or the file is non-trivial
  if [ ! -f "${db}-wal" ] && [ ! -f "${db}-shm" ]; then
    # still fine to checkpoint empty/new DBs occasionally; keep it cheap
    skip=$((skip+1))
  fi
  if python3 - "$db" <<'PY'
import sqlite3, sys
path = sys.argv[1]
try:
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
except Exception as e:
    print(f"checkpoint failed: {path}: {e}", file=sys.stderr)
    sys.exit(1)
PY
  then ok=$((ok+1))
  else fail=$((fail+1))
  fi
done

echo "db-checkpoint: data=$DATA ok=$ok fail=$fail scanned=${#DBS[@]}"
[ "$fail" -eq 0 ]
