#!/usr/bin/env bash
# agent-recovery.sh — Recover agent sessions after reboot/crash
#
# This script runs on boot (via systemd) and checks for agents that were
# running before the crash. It uses the agent-sessions.json index and
# handoff files to resume agents with their previous Claude Code sessions.
#
# It can also be run manually: bash scripts/agent-recovery.sh [--dry-run]
#
# Cron safety: */5 * * * * bash ~/scripts/agent-orchestra/scripts/agent-recovery.sh --cron
#   In --cron mode, only recovers agents that have been dead < 30 min
#   (prevents respawning agents that were intentionally killed)

set -uo pipefail

BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="${ORCHESTRA_DIR:-$(cd "$BIN_DIR/.." && pwd)}"
STATE_DIR="$SCRIPT_DIR/state"
SESSION_INDEX="$STATE_DIR/agent-sessions.json"
HANDOFF_DIR="$STATE_DIR/agent-handoffs"
REGISTRY="$SCRIPT_DIR/registry.json"
RECOVERY_RUNTIME_SCRIPT="${RECOVERY_RUNTIME_SCRIPT:-$BIN_DIR/recovery_runtime.py}"
LOG="$SCRIPT_DIR/logs/agent-recovery.log"
LOCKFILE="/tmp/agent-recovery.lock"

DRY_RUN=false
CRON_MODE=false
BOOT_MODE=false
PRINT_RUNTIME_PIDS=false
CHECK_SID_HELD=""
# --print-roster (R4 Stage-1 detect-only, DEC-1788691687768192): print the
# legacy recoverable roster (get_recoverable_agents, guards verbatim) and exit
# WITHOUT recovering anything — the recovery_shadow.py diff consumes this so
# the legacy side is the real thing, never a reimplementation.
PRINT_ROSTER=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --cron) CRON_MODE=true ;;
        --boot) BOOT_MODE=true ;;
        --print-runtime-pids) PRINT_RUNTIME_PIDS=true ;;
        --check-sid-held=*) CHECK_SID_HELD="${arg#*=}" ;;
        --print-roster) PRINT_ROSTER=true ;;
    esac
done

mkdir -p "$(dirname "$LOG")"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [recovery] $*" | tee -a "$LOG"; }

# Prevent concurrent runs
if [ -f "$LOCKFILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKFILE" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -lt 120 ]; then
        exit 0  # Another instance is running
    fi
    rm -f "$LOCKFILE"
fi
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

# Infrastructure sessions managed by service-watchdog — never touch these
INFRA_SESSIONS="dashboard custom-llm api-server jarvis-service jarvis-v2-mock lwe-feedback pocket-webhook pocket-service telegram-router cf-tunnel"

# Auto-recover agents — resume without the operator's approval
AUTO_RECOVER="gm telegram-router combo-proxy orchestra-builder"

# Notification cooldown file
NOTIFIED_FILE="/tmp/watchdog-notified.json"

# Pinned Claude binary — prevents version skew between PATH resolutions
CLAUDE_BIN="${ORCHESTRA_CLAUDE_BIN:-}"
[ -x "$CLAUDE_BIN" ] || CLAUDE_BIN="$(command -v claude || echo claude)"

# Quarantine: stop auto-recovering agents that crash repeatedly
QUARANTINE_DIR="$STATE_DIR/quarantine"
ATTEMPTS_DIR="$STATE_DIR/recovery-attempts"
MAX_ATTEMPTS=3
ATTEMPT_WINDOW=3600
mkdir -p "$QUARANTINE_DIR" "$ATTEMPTS_DIR"

is_quarantined() {
    [ -f "$QUARANTINE_DIR/$1.json" ]
}

# Record a recovery attempt; quarantine after MAX_ATTEMPTS within ATTEMPT_WINDOW.
# Returns 1 (and alerts once) when the agent gets quarantined.
record_attempt_or_quarantine() {
    local agent_id="$1"
    local attempts_file="$ATTEMPTS_DIR/$agent_id"
    local now count recent
    now=$(date +%s)
    echo "$now" >> "$attempts_file"
    recent=$(awk -v now="$now" -v win="$ATTEMPT_WINDOW" 'now - $1 <= win' "$attempts_file")
    echo "$recent" > "$attempts_file"
    count=$(echo "$recent" | grep -c . || true)
    if [ "$count" -ge "$MAX_ATTEMPTS" ]; then
        printf '{"agent_id": "%s", "quarantined_at": "%s", "attempts_in_window": %s, "reason": "repeated recovery failures"}\n' \
            "$agent_id" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$count" > "$QUARANTINE_DIR/$agent_id.json"
        log "QUARANTINED $agent_id after $count recovery attempts in ${ATTEMPT_WINDOW}s"
        TG_TOKEN=$(grep TELEGRAM_BOT_TOKEN "$SCRIPT_DIR/.env.telegram" 2>/dev/null | cut -d= -f2)
        TG_ID=$(grep SHAW_TELEGRAM_ID "$SCRIPT_DIR/.env.telegram" 2>/dev/null | cut -d= -f2)
        if [ -n "$TG_TOKEN" ] && [ -n "$TG_ID" ]; then
            curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
                -d chat_id="$TG_ID" \
                --data-urlencode "text=🚧 QUARANTINED: $agent_id crashed $count times in the last hour. Auto-recovery stopped. Release: rm ~/scripts/agent-orchestra/state/quarantine/$agent_id.json" \
                > /dev/null 2>&1
        fi
        return 1
    fi
    return 0
}

# Model to resume with: registry 'model' field (explicit intent, e.g.
# orchestra-builder is always opus-4-8[1m]) wins over index 'model' (observed
# in transcript). Empty = no --model flag (default). Without this, post-reboot
# resumes silently fell back to the default model (2026-07-13 incident).
resume_model() {
    local agent_id="$1"
    python3 -c "
import json
try:
    reg = json.load(open('$REGISTRY'))['agents'].get('$agent_id', {})
    if reg.get('model'):
        print(reg['model']); raise SystemExit
    idx = json.load(open('$SESSION_INDEX')).get('$agent_id', {})
    print(idx.get('model', ''))
except SystemExit: pass
except Exception: print('')
" 2>/dev/null
}

# ── SID-LIVENESS GUARD (P0, the operator-found 2026-08-19; gm msg_838a27f5) ──────────
# A recovery run spawned a SECOND claude on gm's LIVE session id. The daemon's
# liveness predicate was "is tmux session NAME X alive" — but the thing that
# must never be duplicated is the SESSION ID. Those are different questions,
# and ANY stale tmux pointer (rename, promotion, crashed writer, manual fix)
# turns that gap into a second writer on a live transcript. Same class as the
# jarvis :5060 incident where a dead-port window let a watchdog start a second
# writer and corrupt the ledger; here the shared resource is an agent's
# transcript and its identity. Name vs identity is the orthogonality axis
# again — in the daemon whose whole job is restoring identity.
#
# FAILS CLOSED: a missing agent is a visible problem, a duplicated one is an
# invisible corruption. If we cannot prove the sid is unheld, we refuse.
# TWO PREDICATES, because neither alone is sound — both calibrated by effect:
#
#  (1) ARGV: a claude process whose command line names the sid. Matched
#      against REAL claude processes (pgrep -x, exact comm) and NOT
#      `pgrep -af claude`, which matches any process whose cmdline merely
#      mentions a ~/.claude/... PATH — including this script's own shell,
#      which made the first version refuse EVERY sid and would have broken
#      recovery fleet-wide.
#
#  (2) RECENT WRITE: a respawned claude carries a BARE `claude` argv with no
#      sid in it (verified on the live gm and orchestra-builder panes), so
#      argv matching alone gives a FALSE NEGATIVE on exactly the case that
#      fired tonight. A transcript written within the grace window is being
#      held by something, whatever its argv looks like.
#
# The recent-write half can refuse an agent that died seconds ago. That is
# the SAFE direction (gm: a missing agent is a visible problem, a duplicated
# one is an invisible corruption) and it self-clears once the window passes,
# so it delays recovery rather than breaking it.
#
# DEVIATION FOR TRACK D.1 PARITY: Automatic recovery uses the strict python layer.
# There is NO fallback for Codex.
SID_HELD_GRACE_SECONDS="${SID_HELD_GRACE_SECONDS:-180}"
PGREP_BIN="${PGREP_BIN:-pgrep}"

# Exact executable-name enumeration.  Do not combine names into one `pgrep`
# invocation: procps accepts one pattern only, and a rejected multi-pattern
# command silently disabled the SID holder guard.  This is still only a
# best-effort holder signal, NOT C-R7 lease/process-instance fencing.
runtime_pids() {
    local comm
    for comm in claude agy codex; do
        "$PGREP_BIN" -x "$comm" 2>/dev/null || true
    done
}

sid_is_held() {
    local sid="$1"
    [ -n "$sid" ] || return 0      # unknown sid -> treat as held, i.e. refuse

    local pid
    for pid in $(runtime_pids); do
        if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q -- "$sid"; then
            return 0
        fi
    done

    local f now mtime
    for f in "$HOME"/.claude/projects/*/"$sid".jsonl; do
        [ -f "$f" ] || continue
        now=$(date +%s)
        mtime=$(stat -c %Y "$f" 2>/dev/null || echo 0)
        if [ $((now - mtime)) -lt "$SID_HELD_GRACE_SECONDS" ]; then
            return 0
        fi
    done
    return 1
}

# Automatic recovery is a stricter policy than runtime declaration.  It uses
# the shared strict resolver (normalization + positive-model derivation) and
# deliberately refuses Codex until C-R7 can bind a new process instance and
# lease to the rollout.  Never default an unresolved row to Claude.
automatic_recovery_runtime() {
    local agent_id="$1"
    python3 "$RECOVERY_RUNTIME_SCRIPT" \
        --registry "$REGISTRY" --agent "$agent_id"
}

# Read-only diagnostics for hermetic regression tests.  They execute no tmux,
# registry, or recovery action and make the exact pgrep enumeration observable.
if $PRINT_RUNTIME_PIDS; then
    runtime_pids
    exit 0
fi
if [ -n "$CHECK_SID_HELD" ]; then
    sid_is_held "$CHECK_SID_HELD"
    exit $?
fi

# A resume adapter is only callable after automatic_recovery_runtime accepted
# its normalized runtime.  Codex intentionally has no path here.
transcript_exists_in_cwd() {
    local session_id="$1"
    local cwd="$2"
    local runtime="$3"
    if [ "$runtime" = "gemini" ]; then
        local gbrain="${GEMINI_BRAIN_ROOT:-$HOME/.gemini/antigravity-cli/brain}"
        [ -f "$gbrain/$session_id/.system_generated/logs/transcript.jsonl" ] || \
            [ -f "$gbrain/$session_id/transcript.jsonl" ]
        return $?
    fi
    local slug
    slug=$(echo "$cwd" | sed 's/[\/.]/-/g')
    [ -f "$HOME/.claude/projects/$slug/$session_id.jsonl" ]
}

is_infra() {
    local session="$1"
    for s in $INFRA_SESSIONS; do
        [ "$session" = "$s" ] && return 0
    done
    return 1
}

is_auto_recover() {
    local session="$1"
    for s in $AUTO_RECOVER; do
        [ "$session" = "$s" ] && return 0
    done
    return 1
}

# Check if we already notified about this agent within the last hour
already_notified() {
    local agent_id="$1"
    python3 -c "
import json, os, time
nf = '$NOTIFIED_FILE'
if not os.path.exists(nf):
    exit(1)
with open(nf) as f:
    data = json.load(f)
ts = data.get('$agent_id', 0)
if time.time() - ts < 3600:
    exit(0)
else:
    exit(1)
" 2>/dev/null
}

record_notification() {
    local agent_id="$1"
    python3 -c "
import json, os, time
nf = '$NOTIFIED_FILE'
data = {}
if os.path.exists(nf):
    try:
        with open(nf) as f:
            data = json.load(f)
    except: pass
data['$agent_id'] = time.time()
with open(nf, 'w') as f:
    json.dump(data, f)
" 2>/dev/null
}

# Get agents that SHOULD be running
# Sources: 1) agent-sessions.json (has session IDs), 2) handoff files (has context)
get_recoverable_agents() {
    python3 -c "
import json, os, sys
from datetime import datetime, timezone, timedelta

session_index = '$SESSION_INDEX'
handoff_dir = '$HANDOFF_DIR'
registry = '$REGISTRY'
cron_mode = '$CRON_MODE' == 'true'

if not os.path.exists(session_index) or not os.path.exists(registry):
    sys.exit(0)

with open(registry) as f:
    reg = json.load(f)

with open(session_index) as f:
    sessions = json.load(f)

now = datetime.now(timezone.utc)
cutoff = now - timedelta(minutes=30)
# On boot, recover everything from the last 24 hours
boot_cutoff = now - timedelta(hours=24)

# SAFETY: Count session ID usage — skip agents with shared session IDs
# (the session-index.py has a bug where CWD matching assigns the same session to many agents)
from collections import Counter
sid_counts = Counter()
for aid, inf in sessions.items():
    if isinstance(inf, dict) and inf.get('session_id'):
        sid_counts[inf['session_id']] += 1

for agent_id, info in sessions.items():
    if not isinstance(info, dict):
        continue
    if not info.get('resumable'):
        continue

    # Skip agents not on this machine
    machine = info.get('machine', 'vps')
    if machine != 'vps':
        continue

    # Skip if session ID is shared with other agents (broken index)
    session_id = info.get('session_id', '')
    if session_id and sid_counts.get(session_id, 0) > 1:
        continue  # Don't resume a shared session — would be wrong context

    # Skip if tmux session is infrastructure
    tmux_name = info.get('tmux_session', agent_id)

    # Check the agent is in registry
    if agent_id not in reg.get('agents', {}):
        continue

    # F2 / gap C (DEC-1787043333 + gm vote bind): ACTIVE-STATUS ALLOWLIST — a
    # row is auto-recovery-eligible ONLY when its registry status says it
    # should be running. gm's bind evidence: a blocklist missing 'quiescent'
    # is exactly the status gm-gen11 auto-resurrected under at 06:58:03
    # (quiescent = deliberately parked-RESUMABLE, manual resume only;
    # never-prevent-resurrections covers manual paths, not auto-resume).
    # Missing/empty status = legacy row -> eligible (fail-open, preserves
    # pre-fix behavior for rows that predate the status field).
    _status = reg['agents'][agent_id].get('status')
    if _status and _status not in ('online', 'active'):
        continue

    # Get session ID
    session_id = info.get('session_id', '')
    if not session_id:
        continue

    # Check how recently it was active
    last_active = info.get('last_active', '')
    if last_active:
        try:
            la = datetime.fromisoformat(last_active)
            if cron_mode and la < cutoff:
                continue  # Too old for cron mode
            if not cron_mode and la < boot_cutoff:
                continue  # Too old even for boot
        except:
            pass

    # Get the conversation path to verify the session file exists
    conv_path = info.get('conversation_path', '')
    if conv_path and not os.path.exists(conv_path):
        continue

    # Get CWD from registry
    cwd = reg['agents'][agent_id].get('cwd', os.environ.get('ORCHESTRA_DIR', ''))

    print(f'{agent_id}|{tmux_name}|{session_id}|{cwd}|{conv_path}')
" 2>/dev/null
}

# --print-roster short-circuit: emit the legacy roster lines and stop before
# any recovery/side-effect logic (R4 detect-only feed).
if [ "$PRINT_ROSTER" = "true" ]; then
    get_recoverable_agents
    exit 0
fi

# Count recoveries
RECOVERED=0
SKIPPED=0
ALREADY_RUNNING=0

while IFS='|' read -r agent_id tmux_name session_id cwd conv_path; do
    [ -z "$agent_id" ] && continue

    # Skip infrastructure
    if is_infra "$tmux_name"; then
        continue
    fi

    # Skip quarantined agents (release: rm state/quarantine/<agent>.json)
    if is_quarantined "$agent_id"; then
        continue
    fi

    runtime=""
    if ! runtime=$(automatic_recovery_runtime "$agent_id" 2>&1); then
        log "SKIP $agent_id — automatic recovery REFUSED: $runtime"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # Auto-recover critical agents
    if is_auto_recover "$agent_id"; then
        # Skip if already running
        if tmux has-session -t "$tmux_name" 2>/dev/null; then
            # Session exists — check if Claude is actually alive in it
            PANE_PID=$(tmux list-panes -t "$tmux_name" -F '#{pane_pid}' 2>/dev/null | head -1)
            HAS_CLAUDE=$(pgrep -P "$PANE_PID" 2>/dev/null | wc -l)
            if [ "$HAS_CLAUDE" -gt 0 ]; then
                ALREADY_RUNNING=$((ALREADY_RUNNING + 1))
                continue
            fi
            # Session exists but Claude is dead — log crash and recover
            bash "$SCRIPT_DIR/scripts/agent-crash-logger.sh" "$agent_id" "$tmux_name" "auto-resumed"
            tmux kill-session -t "$tmux_name" 2>/dev/null
            sleep 1
        else
            # No session at all — log it
            bash "$SCRIPT_DIR/scripts/agent-crash-logger.sh" "$agent_id" "$tmux_name" "auto-resumed-no-session"
        fi

        if $DRY_RUN; then
            log "WOULD AUTO-RECOVER: $agent_id (tmux=$tmux_name, session=$session_id)"
            RECOVERED=$((RECOVERED + 1))
            continue
        fi

        if ! transcript_exists_in_cwd "$session_id" "$cwd" "$runtime"; then
            log "SKIP AUTO-RECOVER $agent_id — session $session_id has no transcript under cwd $cwd (would mis-resume)"
            SKIPPED=$((SKIPPED + 1))
            continue
        fi

        if sid_is_held "$session_id"; then
            log "REFUSE AUTO-RECOVER $agent_id — session $session_id is ALREADY HELD by a live claude process. Recovering would put a SECOND writer on a live transcript (P0 2026-08-19). The tmux pointer is stale, not the agent: fix the pointer, do not recover."
            SKIPPED=$((SKIPPED + 1))
            continue
        fi

        if ! record_attempt_or_quarantine "$agent_id"; then
            continue
        fi

        log "AUTO-RECOVER: $agent_id (tmux=$tmux_name, session=$session_id, runtime=$runtime)"
        tmux new-session -d -s "$tmux_name" -c "$cwd" 2>/dev/null
        sleep 1
        if [ "$runtime" = "gemini" ]; then
            tmux send-keys -t "$tmux_name" "agy --conversation $session_id --dangerously-skip-permissions" Enter
        else
            MODEL_FLAG=""
            RESUME_MODEL=$(resume_model "$agent_id")
            [ -n "$RESUME_MODEL" ] && MODEL_FLAG=" --model '$RESUME_MODEL'"
            tmux send-keys -t "$tmux_name" "$CLAUDE_BIN --resume $session_id$MODEL_FLAG --dangerously-skip-permissions" Enter
        fi
        RECOVERED=$((RECOVERED + 1))
        sleep 3
        continue
    fi

    # Skip if already running (and Claude is alive)
    if tmux has-session -t "$tmux_name" 2>/dev/null; then
        PANE_PID=$(tmux list-panes -t "$tmux_name" -F '#{pane_pid}' 2>/dev/null | head -1)
        HAS_CLAUDE=$(pgrep -P "$PANE_PID" 2>/dev/null | wc -l)
        if [ "$HAS_CLAUDE" -gt 0 ]; then
            ALREADY_RUNNING=$((ALREADY_RUNNING + 1))
            continue
        fi
        # Session exists but Claude is dead — log and notify
        if ! already_notified "$agent_id"; then
            bash "$SCRIPT_DIR/scripts/agent-crash-logger.sh" "$agent_id" "$tmux_name" "notified"
            TG_TOKEN=$(grep TELEGRAM_BOT_TOKEN "$SCRIPT_DIR/.env.telegram" 2>/dev/null | cut -d= -f2)
            TG_ID=$(grep SHAW_TELEGRAM_ID "$SCRIPT_DIR/.env.telegram" 2>/dev/null | cut -d= -f2)
            if [ -n "$TG_TOKEN" ] && [ -n "$TG_ID" ]; then
                LAST_LINE=$(tmux capture-pane -t "$tmux_name" -p 2>/dev/null | tail -3 | head -1 || echo "unknown")
                curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
                    -d chat_id="$TG_ID" \
                    --data-urlencode "text=⚠️ $agent_id is dead. Last: $LAST_LINE" \
                    > /dev/null 2>&1
            fi
            record_notification "$agent_id"
        fi
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # Verify the session file actually exists and has content
    if [ -n "$conv_path" ] && [ ! -s "$conv_path" ]; then
        log "SKIP $agent_id — session file empty or missing: $conv_path"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    if $DRY_RUN; then
        log "WOULD RECOVER: $agent_id (tmux=$tmux_name, session=$session_id, cwd=$cwd)"
        RECOVERED=$((RECOVERED + 1))
        continue
    fi

    if ! transcript_exists_in_cwd "$session_id" "$cwd" "$runtime"; then
        log "SKIP $agent_id — session $session_id has no transcript under cwd $cwd (would mis-resume)"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    if sid_is_held "$session_id"; then
        log "REFUSE RECOVER $agent_id — session $session_id is ALREADY HELD by a live claude process. Recovering would put a SECOND writer on a live transcript (P0 2026-08-19). Fix the stale tmux pointer, do not recover."
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    if ! record_attempt_or_quarantine "$agent_id"; then
        continue
    fi

    log "RECOVERING: $agent_id (tmux=$tmux_name, session=$session_id, runtime=$runtime)"

    # Create tmux session and resume
    tmux new-session -d -s "$tmux_name" -c "$cwd" 2>/dev/null
    if [ $? -ne 0 ]; then
        log "FAIL: Could not create tmux session '$tmux_name'"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    sleep 1
    if [ "$runtime" = "gemini" ]; then
        tmux send-keys -t "$tmux_name" "agy --conversation $session_id --dangerously-skip-permissions" Enter
    else
        MODEL_FLAG=""
        RESUME_MODEL=$(resume_model "$agent_id")
        [ -n "$RESUME_MODEL" ] && MODEL_FLAG=" --model '$RESUME_MODEL'"
        tmux send-keys -t "$tmux_name" "$CLAUDE_BIN --resume $session_id$MODEL_FLAG --dangerously-skip-permissions" Enter
    fi

    RECOVERED=$((RECOVERED + 1))

    # Don't flood — stagger spawns
    sleep 3

done < <(get_recoverable_agents)

# --- Dead Shell Cleanup ---
# Kill tmux sessions with no Claude process that have been idle for 30+ min
IDLE_STATE="/tmp/watchdog-idle-shells.json"

python3 -c "
import json, subprocess, os, time

idle_file = '$IDLE_STATE'
auto_recover = set('$AUTO_RECOVER'.split()) | set('$INFRA_SESSIONS'.split())

# Load previous idle state
prev = {}
if os.path.exists(idle_file):
    try:
        with open(idle_file) as f:
            prev = json.load(f)
    except:
        pass

current = {}
killed = []

# Get all tmux sessions
result = subprocess.run(['tmux', 'list-sessions', '-F', '#{session_name}'], capture_output=True, text=True)
sessions = result.stdout.strip().split('\n') if result.stdout.strip() else []

for sess in sessions:
    if not sess:
        continue
    # Skip auto-recover agents (they get resumed, not cleaned)
    if sess in auto_recover:
        continue
    # Check if Claude is running
    try:
        pane_pid = subprocess.run(['tmux', 'list-panes', '-t', sess, '-F', '#{pane_pid}'],
                                   capture_output=True, text=True).stdout.strip().split('\n')[0]
        children = subprocess.run(['pgrep', '-P', pane_pid], capture_output=True, text=True)
        if children.returncode == 0 and children.stdout.strip():
            continue  # Claude is alive, skip
    except:
        continue

    # No Claude process — kill orphaned children running >30 min
    try:
        orphans = subprocess.run(['ps', '--ppid', pane_pid, '--no-headers', '-o', 'pid,etimes'],
                                  capture_output=True, text=True)
        for oline in orphans.stdout.strip().split('\n'):
            oline = oline.strip()
            if not oline:
                continue
            parts = oline.split()
            if len(parts) >= 2 and int(parts[1]) > 1800:
                os.kill(int(parts[0]), 9)
    except:
        pass

    # Track idle time
    if sess in prev:
        idle_count = prev[sess] + 1
    else:
        idle_count = 1
    current[sess] = idle_count

    # 15 cycles * 2 min = 30 min
    if idle_count >= 15:
        subprocess.run(['tmux', 'kill-session', '-t', sess], capture_output=True)
        killed.append(sess)
        del current[sess]

with open(idle_file, 'w') as f:
    json.dump(current, f)

# Log kills
if killed:
    import datetime
    for sess in killed:
        entry = {
            'ts': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'action': 'killed_dead_shell',
            'session': sess,
            'idle_cycles': 15
        }
        with open('$SCRIPT_DIR/logs/watchdog.jsonl', 'a') as f:
            f.write(json.dumps(entry) + '\n')
" 2>/dev/null

if [ $RECOVERED -gt 0 ] || [ $SKIPPED -gt 0 ]; then
    log "Recovery complete: $RECOVERED recovered, $SKIPPED skipped, $ALREADY_RUNNING already running"

    # Notify the operator
    if [ $RECOVERED -gt 0 ] && ! $DRY_RUN; then
        TG_TOKEN=$(grep TELEGRAM_BOT_TOKEN "$SCRIPT_DIR/.env.telegram" 2>/dev/null | cut -d= -f2)
        TG_ID=$(grep SHAW_TELEGRAM_ID "$SCRIPT_DIR/.env.telegram" 2>/dev/null | cut -d= -f2)
        if [ -n "$TG_TOKEN" ] && [ -n "$TG_ID" ]; then
            curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
                -d chat_id="$TG_ID" \
                --data-urlencode "text=🔄 Agent recovery: $RECOVERED agents resumed after $(if $BOOT_MODE; then echo 'reboot'; elif $CRON_MODE; then echo 'crash'; else echo 'manual recovery'; fi). $SKIPPED skipped, $ALREADY_RUNNING already running." \
                > /dev/null 2>&1
        fi
    fi
fi

# >>> identity-store regenerator boot hook
# The identity-store cutover flag survives a reboot but the projector-regeneration
# daemon does NOT — so on boot, if (and only if) the cutover is armed, (re)start it,
# else the read-only JSON projections freeze and the U12 monitor pages. INERT: the
# start-if-armed entry is a no-op unless cutover.is_active, so flag-off (today, and
# after any cutover rollback) this changes NOTHING at boot. Defensive: last + isolated
# + log-and-continue so a regenerator hiccup can never break agent recovery.
if [ "${BOOT_MODE:-false}" = "true" ]; then
    ( cd "$SCRIPT_DIR" && ORCHESTRA_DIR="$SCRIPT_DIR" \
        python3 -m scripts.identity_store.regenerator start-if-armed ) >> "$LOG" 2>&1 \
        || log "identity-store regenerator start-if-armed hook failed (non-fatal)"
fi
# <<< identity-store regenerator boot hook

# >>> R4 Stage-1 detect-only recovery-roster shadow (DEC-1788691687768192).
# INERT unless state/RECOVERY_SHADOW_ARMED exists (creating it is gm-gated).
# Observes/logs only — recovery_shadow.py has zero spawn capability by
# construction; a failure here can never affect recovery (log-and-continue).
if [ -f "$STATE_DIR/RECOVERY_SHADOW_ARMED" ]; then
    (
        ROSTER_TMP=$(mktemp) && get_recoverable_agents > "$ROSTER_TMP" 2>/dev/null
        TMUX_TMP=$(mktemp) && tmux list-sessions -F '#{session_name}' > "$TMUX_TMP" 2>/dev/null || true
        cd "$SCRIPT_DIR" && python3 -c "
import sys
sys.path.insert(0, 'scripts')
from identity_store import recovery_shadow
legacy = open(sys.argv[1]).read().splitlines()
live = set(open(sys.argv[2]).read().splitlines())
res = recovery_shadow.run_cycle(orchestra_dir='$SCRIPT_DIR', legacy_roster=legacy, live_tmux_sessions=live)
print('[recovery-shadow]', {k: res.get(k) for k in ('ran','fail_open')}, 'divergences:', len(res.get('divergences') or []))
" "$ROSTER_TMP" "$TMUX_TMP"
        rm -f "$ROSTER_TMP" "$TMUX_TMP"
    ) >> "$LOG" 2>&1 || log "recovery-shadow cycle failed (non-fatal, detect-only)"
fi
# <<< R4 detect-only shadow
