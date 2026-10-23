#!/usr/bin/env bash
# Public OrchestraOS terminal-relay deployment/proof runner.
# Default is a read-only dry run.  --live is intentionally explicit and must be
# run as the public OrchestraOS service owner after Shaw approval + GM GO.
set -euo pipefail

LIVE=0
if [[ "${1:-}" == "--live" ]]; then LIVE=1; shift; fi
if [[ $# -ne 0 ]]; then echo "usage: $0 [--live]" >&2; exit 2; fi

ROOT="${ORCHESTRA_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
DATA="${ORCHESTRA_DIR:-$HOME/.orchestra}"
EXPECTED_SHA="${TERMINAL_RELAY_SHA:-}"
SCRATCH="${TERMINAL_RELAY_SCRATCH:-terminal-relay-proof}"
SERVE_URL="${TERMINAL_RELAY_SERVE_URL:-}"
SECRET="$DATA/state/terminal-relay-secret"

say() { printf '%s\n' "$*"; }
run() { if (( LIVE )); then say "+ $*"; "$@"; else say "DRY-RUN: $*"; fi; }
need() { command -v "$1" >/dev/null || { say "STOP: missing $1"; exit 1; }; }

need git; need tmux; need tailscale; need curl
say "mode=$([[ $LIVE -eq 1 ]] && echo live || echo dry-run)"
say "root=$ROOT"
say "data=$DATA"
say "scratch=$SCRATCH"

[[ -d "$ROOT/.git" ]] || { say "STOP: ORCHESTRA_ROOT is not a checkout"; exit 1; }
if [[ -n "$EXPECTED_SHA" ]]; then
  say "expected_sha=$EXPECTED_SHA"
else
  say "STOP: set TERMINAL_RELAY_SHA to the approved merged commit before live deployment"; [[ $LIVE -eq 1 ]] && exit 1
fi

say "--- Tailscale Serve (read-only; never modifies Funnel/Serve) ---"
tailscale serve status
if [[ -z "$SERVE_URL" ]]; then
  say "STOP: set TERMINAL_RELAY_SERVE_URL to the existing tailnet-only gateway URL before live proof"
  [[ $LIVE -eq 1 ]] && exit 1
else
  say "served_gateway=$SERVE_URL"
  [[ "$SERVE_URL" == https://* || "$SERVE_URL" == wss://* ]] || { say "STOP: served URL must be HTTPS/WSS"; exit 1; }
fi

say "--- preflight ---"
run git -C "$ROOT" fetch origin
if (( LIVE )); then
  [[ -z "$(git -C "$ROOT" status --porcelain)" ]] || { say "STOP: public checkout is dirty"; exit 1; }
  run git -C "$ROOT" merge --ff-only "$EXPECTED_SHA"
  run "$ROOT/.venv/bin/orchestra" init --yes
fi

if (( LIVE )); then
  [[ -f "$SECRET" ]] || { say "STOP: relay credential file missing: $SECRET"; exit 1; }
  [[ "$(stat -c '%a' "$SECRET")" == 600 ]] || { say "STOP: relay credential must be mode 0600"; exit 1; }
  say "credential_file=present mode=0600 (value not printed)"
  # The public supervisor currently owns gateway/api/dashboard together.  Do not
  # invoke this without GM approving that exact service scope.
  run "$ROOT/.venv/bin/orchestra" down
  run "$ROOT/.venv/bin/orchestra" up --detach
fi

say "--- scratch proof plan ---"
run tmux new-session -d -s "$SCRATCH" 'printf "TERMINAL-RELAY-SCRATCH\\n"; exec bash'
say "Positive (paired Mac only): WSS $SERVE_URL/terminal/ws?session=$SCRATCH&cols=80&rows=24 -> 101, ANSI/banner, type 'echo MACKEY-TYPED-OK' once; capture only $SCRATCH."
say "Negative: fleet bearer -> 403; missing/wrong paired token -> 401/403; bad/unregistered session -> 400/404; duplicate -> 409; cap -> 429; Funnel/off-tailnet -> 404."
say "Verify ledger: $DATA/state/operator-actions.jsonl has terminal open/close for $SCRATCH, no keystrokes/output."
say "Rollback (GM GO only): stop approved public service scope, git -C $ROOT checkout <previous-merged-sha>, rebuild/init, restart same scope. Do not alter Tailscale Serve/Funnel."
if (( LIVE )); then
  say "STOP after scratch setup: human paired-device proof is required; script never reads or logs device credentials."
fi
