#!/usr/bin/env bash
# tg-notify.sh — Reliable Telegram send for agents. Verifies delivery, never lies.
#
# Why this exists: agents sent via `curl ... -o /dev/null -w "%{http_code}"`, which
# DISCARDS the response body — so they could not tell ok:true from ok:false
# (Telegram returns HTTP 200 with ok:false on soft failures) and reported "sent"
# without proof. This helper captures the real JSON, checks ok:true, splits messages
# over Telegram's 4096-char hard limit, retries transient failures, logs durably,
# and EXITS NON-ZERO on failure so a caller can trust its result.
#
# Usage:
#   scripts/tg-notify.sh "message text"
#   echo "message" | scripts/tg-notify.sh                  # read from stdin
#   scripts/tg-notify.sh --from design-reviewer "message"  # optional label prefix
#
# Exit: 0 = delivered (all chunks ok:true), non-zero = at least one chunk failed.

set -uo pipefail

ORCHESTRA_DIR="${ORCHESTRA_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="$ORCHESTRA_DIR/.env.telegram"
LOG="$ORCHESTRA_DIR/state/tg-notify.log"
MAXLEN=3900   # under Telegram's 4096 hard limit, leaves room for a chunk header

# --- optional --from label ---
LABEL=""
if [ "${1:-}" = "--from" ]; then LABEL="[$2] "; shift 2; fi

# --- optional --text alias (agents infer it from msg_store's --body; accept it
# rather than sending the literal string "--text" as the message, which is
# exactly what happened 2026-08-11: the operator received "[gm] --text" x3 with the
# real content silently dropped) ---
if [ "${1:-}" = "--text" ] || [ "${1:-}" = "--body" ] || [ "${1:-}" = "--message" ]; then shift; fi
# allow --from AFTER the alias too (arg-order tolerance)
if [ "${1:-}" = "--from" ]; then LABEL="[$2] "; shift 2; fi

# --- message from arg or stdin ---
if [ -n "${1:-}" ]; then MSG="$1"; else MSG="$(cat)"; fi

# --- refuse flag-looking messages: a MSG starting with "--" is ALWAYS a
# mis-invocation; failing loudly beats texting the operator garbage ---
case "$MSG" in
  --*) echo "tg-notify: message looks like a flag ('$MSG') — usage: tg-notify.sh [--from AGENT] \"message\" (message is positional or stdin)" >&2; exit 2;;
esac

MSG="${LABEL}${MSG}"
[ -z "${MSG// }" ] && { echo "tg-notify: empty message" >&2; exit 2; }

# --- creds ---
[ -f "$ENV_FILE" ] || { echo "tg-notify: creds file missing: $ENV_FILE" >&2; exit 3; }
TG_TOKEN="$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
TG_ID="$(grep -E '^SHAW_TELEGRAM_ID=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
[ -n "$TG_TOKEN" ] && [ -n "$TG_ID" ] || { echo "tg-notify: token/id empty in $ENV_FILE" >&2; exit 3; }

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# send_one <text>: POST one chunk, verify ok:true (1 retry). returns 0 on verified success.
send_one() {
  local text="$1" attempt resp verdict
  for attempt in 1 2; do
    resp="$(curl -s --max-time 20 -X POST \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      -d chat_id="$TG_ID" --data-urlencode "text=${text}")"
    verdict="$(printf '%s' "$resp" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
    if d.get("ok"): print("OK "+str(d["result"]["message_id"]))
    else: print("FAIL "+str(d.get("error_code",""))+" "+str(d.get("description","")))
except Exception:
    print("FAIL parse "+sys.stdin.read()[:120])' 2>/dev/null)"
    if [[ "$verdict" == OK\ * ]]; then
      echo "{\"ts\":\"$(ts)\",\"status\":\"ok\",\"message_id\":${verdict#OK },\"len\":${#text}}" >> "$LOG"
      return 0
    fi
    echo "{\"ts\":\"$(ts)\",\"status\":\"fail\",\"attempt\":$attempt,\"error\":\"${verdict#FAIL }\",\"len\":${#text}}" >> "$LOG"
    sleep 1
  done
  echo "tg-notify: send failed after 2 attempts: ${verdict#FAIL }" >&2
  return 1
}

mkdir -p "$(dirname "$LOG")"
rc=0
if [ "${#MSG}" -le "$MAXLEN" ]; then
  send_one "$MSG" || rc=1
else
  # split into <=MAXLEN chunks on line boundaries, NUL-delimited
  mapfile -d '' -t PARTS < <(printf '%s' "$MSG" | python3 -c '
import sys
maxlen=int(sys.argv[1]); msg=sys.stdin.read()
chunks=[]; cur=""
for line in msg.splitlines(keepends=True):
    if len(cur)+len(line) > maxlen and cur: chunks.append(cur); cur=""
    while len(line) > maxlen: chunks.append(line[:maxlen]); line=line[maxlen:]
    cur+=line
if cur: chunks.append(cur)
n=len(chunks)
for i,c in enumerate(chunks,1):
    sys.stdout.write(f"({i}/{n}) "+c+"\x00")' "$MAXLEN")
  for part in "${PARTS[@]}"; do
    send_one "$part" || rc=1
    sleep 0.4
  done
fi

if [ "$rc" -eq 0 ]; then echo "tg-notify: delivered (verified ok:true)"; else echo "tg-notify: DELIVERY FAILED — see $LOG" >&2; fi
exit "$rc"
