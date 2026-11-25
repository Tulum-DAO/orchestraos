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
# Orphaned plugin Chrome (any age): a Chrome whose profile is under the browser plugin's profile
# root and whose MCP parent is gone (reparented to pid 1 or `systemd --user`) is ended first, one log
# line each. A plugin Chrome with a live parent is that seat's own process and is never touched here.
# The decision is scripts/chrome_orphans.py (tested); this script only acts on its list.
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

# True when $1 is a live chrome-family process (comm is truncated to 15 chars; a prefix match).
is_browser() {
  local c
  c="$(cat "/proc/$1/comm" 2>/dev/null)" || return 1
  case "$c" in chrome*|chromium*|*headless*) return 0 ;; *) return 1 ;; esac
}

# Every descendant pid of $1 (not $1 itself), depth-first.
tree_pids() {
  local k
  for k in $(pgrep -P "$1" 2>/dev/null); do
    echo "$k"
    tree_pids "$k"
  done
}

# Orphaned plugin Chrome first, at any age.
HERE="$(cd "$(dirname "$0")" && pwd)"
if $LIST_ONLY; then
  echo "Plugin Chrome (verdict, pid, profile, why, parent chain):"
  python3 "$HERE/chrome_orphans.py" --all || echo "chrome_orphans.py failed"
else
  if ! ORPHAN_LIST="$(python3 "$HERE/chrome_orphans.py" 2>"$LOG.err")"; then
    echo "$(ts) [reaper] WARN orphan list unavailable (chrome_orphans.py failed: $(tail -1 "$LOG.err" 2>/dev/null)); no orphan reaped this run" >> "$LOG"
    ORPHAN_LIST=""
  fi
  mapfile -t ORPHANS <<< "$ORPHAN_LIST"
  OTREE=()
  for row in "${ORPHANS[@]}"; do
    IFS=$'\t' read -r opid oprof owhy <<< "$row"
    # a row must be a number naming a process that is still a browser, or it is not acted on
    [[ "$opid" =~ ^[0-9]+$ ]] && is_browser "$opid" || continue
    # the subtree is listed BEFORE the TERM: once the browser exits, its renderers are reparented
    OTREE+=("$opid" $(tree_pids "$opid"))
    echo "$(ts) [reaper] ORPHAN pid=$opid profile=$oprof why=$owhy" >> "$LOG"
    kill -TERM "$opid" 2>/dev/null
  done
  if [ "${#OTREE[@]}" -gt 0 ]; then
    sleep 3
    # re-checked at kill time: a pid freed in those 3 s and reused by something else is left alone.
    # A descendant that is not chrome-family (a helper Chrome exec'd) is left too; renderers, GPU,
    # utility and crashpad are all chrome-family and exit with the browser anyway.
    for p in "${OTREE[@]}"; do is_browser "$p" && kill -KILL "$p" 2>/dev/null; done
  fi
fi

# The age rule below never touches a plugin Chrome whose plugin MCP is alive (parent, or ancestor
# through a wrapper), or anything under it: that is a live seat's own browser. A plugin Chrome under
# some OTHER live parent keeps the age rule. If the list cannot be built, say so and protect nothing
# (the age rule then behaves as it always did).
if PROTECT_LIST="$(python3 "$HERE/chrome_orphans.py" --protected 2>/dev/null)"; then
  PROTECT=" $(echo $PROTECT_LIST) "
else
  PROTECT=" "
  $LIST_ONLY || echo "$(ts) [reaper] WARN live-parent plugin Chrome list unavailable; age rule unfiltered" >> "$LOG"
fi
# CHROME_REAPER_PIDS_ALLOW (tests only): when set, nothing outside this pid list is eligible. It only
# NARROWS the age rule (the protected list still applies) and does not touch the orphan phase.
ALLOW=" ${CHROME_REAPER_PIDS_ALLOW:-} "
eligible() { awk -v prot="$PROTECT" -v allow="$ALLOW" \
  'index(prot, " " $1 " ") == 0 && (allow == "  " || index(allow, " " $1 " ") > 0)'; }

# Main headless browser processes: chrome-family executable, has --headless, and is NOT a
# child worker (renderers/gpu/utility carry --type=). These are the top-level browsers.
mapfile -t MAINS < <(
  ps -eo pid=,etimes=,comm=,args= | eligible | awk -v max="$MAX_AGE" '
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
for pid in $(ps -eo pid=,etimes=,comm= | eligible | awk -v max="$MAX_AGE" '($2+0) > max && $3 ~ /^chrome|headless/ {print $1}'); do
  kill -KILL "$pid" 2>/dev/null && straggler=$((straggler+1))
done

echo "$(ts) [reaper] reaped ${#MAINS[@]} browser tree(s); force-killed $straggler straggler proc(s)" >> "$LOG"
