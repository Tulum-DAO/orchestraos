#!/bin/bash
# refresh-architecture.sh: append a LIVE STATUS block (services, ports, crons, learning DB, disk)
# to the install's architecture doc, every dumped line passed through the credential redactor.
#
# The doc is regenerated several times a day and backed up for 14 days, so a secret that lands in a
# dumped crontab or tmux line would be written and snapshotted again and again: every dumped
# section goes through scripts/redact_secrets.py (next to this script) before it is written.
#
#   ORCHESTRA_DIR  the install's data dir (default ~/.orchestra): state/learning.db is read there
#   ARCH_DOC       the doc to update (default $ORCHESTRA_DIR/docs/ARCHITECTURE.md)
#   ARCH_PORTS     regex of listening ports to show (default 5052|5053|8888|8891|8895)
#
# Cron: 0 */6 * * * bash <checkout>/scripts/refresh-architecture.sh

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${ORCHESTRA_DIR:-$HOME/.orchestra}"
ARCH_DOC="${ARCH_DOC:-$DATA/docs/ARCHITECTURE.md}"
BACKUP_DIR="$(dirname "$ARCH_DOC")/architecture-backups"
PORTS="${ARCH_PORTS:-5052|5053|8888|8891|8895}"
LEARNING_DB="$DATA/state/learning.db"
REDACT=(python3 "$HERE/redact_secrets.py")

if [ ! -f "$ARCH_DOC" ]; then
  echo "no architecture doc at $ARCH_DOC; nothing to refresh" >&2
  exit 1
fi

# Backup current version; keep 14 days.
mkdir -p "$BACKUP_DIR"
cp "$ARCH_DOC" "$BACKUP_DIR/ARCHITECTURE-$(date +%Y%m%d-%H%M).md" 2>/dev/null
find "$BACKUP_DIR" -name "ARCHITECTURE-*.md" -mtime +14 -delete 2>/dev/null

# Strip every earlier LIVE STATUS block, so the doc does not grow forever.
python3 - "$ARCH_DOC" <<'PYEOF'
import re, sys
p = sys.argv[1]
text = open(p).read()
cleaned = re.split(r"\n---\n+## LIVE STATUS \(auto-updated", text)[0].rstrip() + "\n"
if cleaned != text:
    open(p, "w").write(cleaned)
PYEOF

bullets() { while IFS= read -r line; do echo "- $line"; done; }

learning_db() {
  [ -f "$LEARNING_DB" ] || { echo "- (no learning DB at this install)"; return; }
  python3 - "$LEARNING_DB" <<'PYEOF' 2>/dev/null
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
q = lambda sql: conn.execute(sql).fetchone()[0]
print("- Snippets:", q("SELECT COUNT(*) FROM snippets"))
print("- Patterns:", q("SELECT COUNT(*) FROM patterns WHERE status='active'"))
print("- Proposals:", q("SELECT COUNT(*) FROM proposals"))
print("- Active Rules:", q("SELECT COUNT(*) FROM rules WHERE status IN ('probationary','permanent')"))
PYEOF
}

CRONS="$(crontab -l 2>/dev/null | grep -v '^#')"
{
  echo
  echo "---"
  echo
  echo "## LIVE STATUS (auto-updated $(date -u '+%Y-%m-%d %H:%M UTC'))"
  echo
  echo "### Services"
  tmux list-sessions 2>/dev/null | bullets | "${REDACT[@]}"
  echo
  echo "### Ports"
  ss -tlnp 2>/dev/null | grep -E "$PORTS" | bullets | "${REDACT[@]}"
  echo
  echo "### Crons ($(printf '%s' "$CRONS" | grep -c .) active)"
  printf '%s\n' "$CRONS" | grep . | bullets | "${REDACT[@]}"
  echo
  echo "### Learning DB"
  learning_db
  echo
  echo "### Disk"
  echo "- Free: $(df -h / | tail -1 | awk '{print $4}')"
  [ -f "$LEARNING_DB" ] && echo "- Learning DB: $(ls -lh "$LEARNING_DB" | awk '{print $5}')"
} >> "$ARCH_DOC"

echo "Architecture doc updated: $ARCH_DOC"
echo "Backup: $BACKUP_DIR/"
