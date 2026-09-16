#!/usr/bin/env bash
# ONE TRUTH SOURCE guard — agent status has exactly one origin (the v2 detector
# feed via useAgents), and its colour has exactly one origin (STATE_STYLE).
#
# Why this exists as a build step and not a test: the dashboard has no test
# runner, `npm run lint` is not on the deploy path (scripts/deploy-dashboard.sh
# runs `npm run build` only), and `tsc` CANNOT catch this class — a resurrected
# pane-scrape classifier is perfectly valid TypeScript. It compiles, deploys,
# and then quietly disagrees with the detail modal on the operator's screen. That is
# the exact bug pair of 2026-08-18 (d941183af, d97fb06fd).
#
# Realistic resurrection path = someone restores the deleted module from git
# history under its old name, so matching the name covers the likely case.
# It is not a proof, it is a tripwire on the path that actually ships.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0

# (1) the retired module must not come back as a file
if find src -name 'agentActivity.ts*' -print -quit | grep -q .; then
  echo "GUARD FAIL: src/**/agentActivity.ts* is back — the retired pane-scrape"
  echo "  classifier is a SECOND truth source for agent status. Chip status must"
  echo "  come from useAgents()/chipStateFor(). See RecentAgentChips.tsx header."
  fail=1
fi

# (2) nothing may import it. Matches import/export statements only, so the
#     comments that document the retirement do not trip the guard.
if grep -rnE "(from|import\()[[:space:]]*['\"][^'\"]*agentActivity" src; then
  echo "GUARD FAIL: an import of the retired agentActivity module (above)."
  fail=1
fi

# (3) the chip dot's colour must stay delegated to STATE_STYLE
if ! grep -q 'STATE_STYLE' src/components/RecentAgentChips.tsx; then
  echo "GUARD FAIL: RecentAgentChips.tsx no longer references STATE_STYLE — the"
  echo "  chip has grown its own colour vocabulary again (round-2 regression)."
  fail=1
fi

# (4) the transcript wire shapes have exactly one origin: the versioned schema.
#     src/lib/transcript.gen.ts is a codegen-synced copy — if it (or gen/) was
#     hand-edited or the schema changed without regenerating, refuse the build.
#     (F0 shared chat contract, docs/SPEC_transcript-ecosystem.md §3.)
if ! node ../contract/transcript/codegen.mjs --check; then
  echo "GUARD FAIL: generated transcript contract types drifted from the schema."
  echo "  Run: node contract/transcript/codegen.mjs  — then commit the result."
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo "--- build refused. Fix the source, not the guard. ---"
  exit 1
fi
echo "one-truth guard: ok"
