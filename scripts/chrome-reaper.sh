#!/usr/bin/env bash
# chrome-reaper.sh — Reap headless Chrome/Chromium that has run longer than MAX_AGE.
#
# Why this exists:
#   The superpowers-chrome MCP plugin launches a PERSISTENT headless browser per agent
#   (headlessMode = !hasDisplay(), so always headless on this GUI-less VPS). Abandoned /
#   leaked browsers accumulate and, on a swapless box, trigger the OOM-killer death spiral
#   that hard-resets the host. We keep short-lived / actively-used Chrome (<1h) so live
#   automation isn't interrupted, and kill anything that's been headless longer than that.
#
# What it does NOT touch:
#   - the `node .../superpowers-chrome/.../mcp/dist/index.js` MCP servers (comm=node)
#   - `claude` agent processes
#   Only processes whose executable name is in the chrome/chromium family are eligible.
#
# Usage: chrome-reaper.sh [max_age_seconds]   (default 3600 = 1h)
#        chrome-reaper.sh --list [max_age]     show what WOULD be reaped, kill nothing
set -u

LIST_ONLY=false
if [ "${1:-}" = "--list" ]; then LIST_ONLY=true; shift; fi
MAX_AGE="${1:-3600}"
LOG="${CHROME_REAPER_LOG:-${ORCHESTRA_DIR:-$HOME/.orchestra}/logs/chrome-reaper.log}"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Kill a process and all of its descendants (renderers/gpu/zygote/utility), depth-first.
# Walks the chrome process subtree only — never climbs up to node/claude.
kill_tree() {
  local p="$1" sig="$2" k
  for k in $(pgrep -P "$p" 2>/dev/null); do
    kill_tree "$k" "$sig"
  done
  kill "-$sig" "$p" 2>/dev/null
}

# Main headless browser processes: chrome-family executable, has --headless, and is NOT a
# child worker (renderers/gpu/utility carry --type=). These are the top-level browsers.
mapfile -t MAINS < <(
  ps -eo pid=,etimes=,comm=,args= | awk -v max="$MAX_AGE" '
    ($2+0) > max && $3 ~ /^chrome|^chromium|headless/ && /--headless/ && !/--type=/ { print $1 }'
)

if [ "${#MAINS[@]}" -eq 0 ]; then
  $LIST_ONLY && echo "no headless chrome older than ${MAX_AGE}s"
  exit 0
fi

if $LIST_ONLY; then
  echo "Would reap ${#MAINS[@]} headless browser(s) older than ${MAX_AGE}s:"
  for pid in "${MAINS[@]}"; do
    ps -o pid=,etime=,rss=,args= -p "$pid" 2>/dev/null | cut -c1-140
  done
  exit 0
fi

# Graceful first (lets Chrome release its profile lock), then force the whole tree.
for pid in "${MAINS[@]}"; do
  echo "$(ts) [reaper] SIGTERM pid=$pid :: $(ps -o etime=,rss=,args= -p "$pid" 2>/dev/null | tr -s ' ' | cut -c1-120)" >> "$LOG"
  kill -TERM "$pid" 2>/dev/null
done
sleep 3
for pid in "${MAINS[@]}"; do
  kill_tree "$pid" KILL
done

# Sweep any chrome-family stragglers still older than MAX_AGE (orphaned renderers, etc.).
straggler=0
for pid in $(ps -eo pid=,etimes=,comm= | awk -v max="$MAX_AGE" '($2+0) > max && $3 ~ /^chrome|headless/ {print $1}'); do
  kill -KILL "$pid" 2>/dev/null && straggler=$((straggler+1))
done

echo "$(ts) [reaper] reaped ${#MAINS[@]} browser tree(s); force-killed $straggler straggler proc(s)" >> "$LOG"
