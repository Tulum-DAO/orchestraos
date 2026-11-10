#!/usr/bin/env bash
# write-handoff.sh — Auto-write handoff on session stop
# Called by Stop hook: bash write-handoff.sh "$SESSION_ID" "$CWD"

SESSION_ID="${1:-unknown}"
CWD="${2:-$(pwd)}"
ORCHESTRA_DIR="$HOME/scripts/agent-orchestra"

# Determine project from CWD
PROJECT=$(basename "$CWD")

# Write basic handoff from git state
HANDOFF_DIR="$HOME/scripts/omni-context/projects/$PROJECT"
mkdir -p "$HANDOFF_DIR"

HANDOFF_FILE="$HANDOFF_DIR/handoff.md"

# RATE LIMIT (CPU emergency 2026-10-08, the operator "prevent runaway git"). This runs on EVERY Stop of
# EVERY seat. ~30 seats share one project dir, so they all overwrite the same file (last writer
# wins), and each run did full-tree `git status` + `git diff --stat` on a 75k-file tree while
# taking .git/index.lock. Write at most once per HANDOFF_MIN_AGE_S per project.
HANDOFF_MIN_AGE_S="${HANDOFF_MIN_AGE_S:-600}"
if [ -f "$HANDOFF_FILE" ]; then
  _age=$(( $(date +%s) - $(stat -c %Y "$HANDOFF_FILE" 2>/dev/null || echo 0) ))
  if [ "$_age" -lt "$HANDOFF_MIN_AGE_S" ]; then
    exit 0
  fi
fi

# Read-only git: never take .git/index.lock (a status killed mid-refresh leaves it behind and
# blocks every seat's commits).
export GIT_OPTIONAL_LOCKS=0

# Get git info. No `git diff --stat`: a second full-tree scan for a 5-line summary.
GIT_BRANCH=$(cd "$CWD" && git branch --show-current 2>/dev/null || echo "unknown")
GIT_STATUS=$(cd "$CWD" && git status --short 2>/dev/null | head -10 || echo "clean")
GIT_LOG=$(cd "$CWD" && git log --oneline -5 2>/dev/null || echo "no commits")
GIT_DIFF="(omitted: full-tree diff --stat dropped for CPU; see Git status above)"

cat > "$HANDOFF_FILE" << HANDOFF
# Handoff — $PROJECT
**Session ended:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Working directory:** $CWD
**Branch:** $GIT_BRANCH
**Git status:**
\`\`\`
$GIT_STATUS
\`\`\`
**Recent commits:**
\`\`\`
$GIT_LOG
\`\`\`
**Recent changes:**
\`\`\`
$GIT_DIFF
\`\`\`
**Next step:** Review git log above and continue from last commit
**Note:** Auto-generated from git state. Agent did not write a manual handoff.
HANDOFF

echo "[write-handoff] Wrote $HANDOFF_FILE"
