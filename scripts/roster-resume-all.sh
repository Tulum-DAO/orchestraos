#!/usr/bin/env bash
# roster-resume-all.sh — Resume every agent in state/live-roster.json that isn't alive.
# Called after a crash (manually or by the watchdog) to restore the fleet.
#
# gm msg_e9a921fe ruling (2), 2026-09-16: every resume goes through spawn-agent.sh's
# adopt gate (auto-register / respawn_retired_canonical / re-establish legs) via
# `spawn-agent.sh <seat> --resume <sid>` — NEVER a raw `tmux new-session` +
# `claude --resume` paste, which registered nothing in the identity store (an orphan
# class invisible to routing and recovery). The gate receives the known sid, so a
# resumed seat has a DB canonical + that sid at adopt time.
# Only rows the roster still marks status=alive are crash victims; rows already
# marked dead (563 of 612 on 2026-09-16) are NOT resumed (the raw loop resumed them).
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROSTER="$SCRIPT_DIR/state/live-roster.json"
SPAWN="$SCRIPT_DIR/spawn-agent.sh"

[ -f "$ROSTER" ] || { echo "no roster at $ROSTER"; exit 1; }
[ -x "$SPAWN" ] || { echo "no spawn-agent.sh at $SPAWN"; exit 1; }

# get live tmux sessions
LIVE=$(tmux list-sessions -F '#{session_name}' 2>/dev/null | tr '\n' ' ')
INFRA="combo-proxy custom-llm dashboard lwe-feedback telegram-router gm jarvis-gm orchestra-builder"

RESUMED=0
FAILED=0
while IFS=$'\t' read -r name sid cwd model; do
    [ -n "$name" ] || continue
    # skip if already alive or infra
    case " $LIVE " in *" $name "*) continue;; esac
    case " $INFRA " in *" $name "*) continue;; esac
    [ -d "$cwd" ] || { echo "SKIP $name (cwd $cwd missing)"; continue; }
    echo "RESUME $name ← $sid (cwd $cwd, model $model) via spawn-agent.sh --resume"
    if AGENT_CWD="$cwd" AGENT_MODEL="$model" "$SPAWN" "$name" --resume "$sid"; then
        RESUMED=$((RESUMED + 1))
    else
        echo "FAILED $name (spawn-agent.sh --resume exit $?)"
        FAILED=$((FAILED + 1))
    fi
done < <(python3 -c "
import json
r=json.load(open('$ROSTER'))
for name,e in sorted(r.items()):
    sid=e.get('session_id','')
    cwd=e.get('cwd','')
    model=e.get('model','opus')
    if sid and cwd and e.get('status') == 'alive':
        print(f'{name}\t{sid}\t{cwd}\t{model}')
")
echo "Resumed $RESUMED agents from roster ($FAILED failed)."
# reconcile status
python3 "$SCRIPT_DIR/scripts/roster-update.py" --reconcile
