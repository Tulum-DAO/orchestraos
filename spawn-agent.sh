#!/usr/bin/env bash
# spawn-agent.sh — Create a tmux session and launch a Claude Code agent
#
# Usage:
#   spawn-agent.sh <agent-id>                    # Spawn from registry
#   spawn-agent.sh <agent-id> --task "Fix bug"   # Spawn with initial task
#   spawn-agent.sh <agent-id> --resume <sid>     # RESUME a known session through the adopt gate
#   spawn-agent.sh --list                        # List all registered agents
#   spawn-agent.sh --running                     # List running agents
#   spawn-agent.sh --kill <agent-id>             # Kill an agent
#   spawn-agent.sh --kill-all                    # Kill all agents

set -euo pipefail

# Timezone: fleet runs on the operator's Eastern time (VPS system clock is UTC).
# Every spawned agent inherits ET so `date`/naive datetime read local, not UTC.
# Stored timestamps stay explicit-UTC (+00:00) — this only changes display/reasoning.
export TZ="${ORCHESTRA_TZ:-${TZ:-UTC}}"

# Ensure homebrew tools are available (needed when called via SSH)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/orchestra-env.sh
source "$SCRIPT_DIR/scripts/orchestra-env.sh"
# REGISTRY defaults to the checkout's registry.json; env-overridable so the
# §4.2 dispatch path can be exercised against a scratch/fake registry without
# a real spawn (the R3 real-artifact gate). Production sets no REGISTRY env.
# ORCHESTRA_DIR (orchestra.toml [data] dir, exported by orchestra-env.sh / `orchestra up`)
# is where registry.json + state/ + logs/ live; the checkout is the default.
ORCHESTRA_DIR="${ORCHESTRA_DIR:-$SCRIPT_DIR}"
REGISTRY="${REGISTRY:-$ORCHESTRA_DIR/registry.json}"
OMNI_DIR="${OMNI_CONTEXT_DIR:-$HOME/scripts/omni-context}"
STATE_DIR="$ORCHESTRA_DIR/state"
mkdir -p "$STATE_DIR" "$ORCHESTRA_DIR/logs"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${CYAN}[spawn]${NC} $*"; }
warn() { echo -e "${YELLOW}[spawn]${NC} $*"; }
err() { echo -e "${RED}[spawn]${NC} $*" >&2; }

# Pinned Claude binary — prevents PATH version skew between tmux shells.
# Set ORCHESTRA_CLAUDE_BIN to pin an exact path; otherwise resolve from PATH.
CLAUDE_BIN="${ORCHESTRA_CLAUDE_BIN:-}"
[[ -x "$CLAUDE_BIN" ]] || CLAUDE_BIN="$(command -v claude || echo claude)"

# --- Runtime dispatch (spec §4.2 all-model-parity): binary + ready-signature
# chosen BY the registry `runtime` field, consuming the ONE signature source
# (scripts/runtime_signatures.py — the SAME table the router idle-gate uses).
# Historically spawn hardcoded the claude binary (line ~34) and grepped a
# hardcoded '❯' (wait_for_tui) — spawning a Gemini agent ran claude, the silent
# seat-corruption "success that isn't" defect. This REFUSES a service/codex/
# unknown runtime (a service is a process, not a seat; codex has no Phase-1
# adapter) rather than defaulting it to claude. An UNDECLARED runtime is
# inferred from the model exactly as the router does — never a Gemini seat,
# which always carries an explicit runtime='gemini' — so the un-backfilled
# legacy rows keep spawning as claude.
# Prints "<runtime>\t<prompt_char>" on stdout; on refusal prints a REFUSE line
# and exits 3.
runtime_dispatch() {
    local agent_id="$1"
    python3 - "$agent_id" "$REGISTRY" "$SCRIPT_DIR/scripts" <<'PYEOF'
import json, sys
agent_id, reg_path, scripts_dir = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, scripts_dir)
try:
    entry = (json.load(open(reg_path)).get("agents", {}) or {}).get(agent_id) or {}
except Exception as e:
    sys.stderr.write(f"REFUSE: registry unreadable ({e})\n"); sys.exit(3)
import runtime_signatures as rs
# R4 unified field-read (spec §4.1 R4(b)): spawn dispatch consumes the SAME
# strict resolver promote does — positive-signal only, `runtime` field (never
# `provider`), model-derive as the R1b-legal fallback, and REFUSE a zero-signal
# / unknown runtime (never default a live Gemini seat to claude — the §4.1
# corruption). The resolver raises RuntimeResolutionError with the --declare
# guidance; we surface it verbatim as the REFUSE line.
try:
    rt = rs.resolve_runtime(entry, agent_id=agent_id, strict=True)
except rs.RuntimeResolutionError as e:
    sys.stderr.write(f"REFUSE: {e}\n"); sys.exit(3)
# Spawnability policy lives at the edge (the resolver stays provider-neutral): a
# 'service' row is a process, not a seat; 'codex' is Phase-2 (no Phase-1 spawn
# adapter). Both are VALID declared runtimes — just not spawnable here.
if rt == "service":
    sys.stderr.write(f"REFUSE: agent {agent_id!r} runtime='service' is a process, not a spawnable seat (spec §4.1)\n"); sys.exit(3)
# codex is spawnable as of C-R2 (SPEC_codex-parity-phase2): binary 'codex',
# bypass flag '--yolo' (NOT --dangerously-skip-permissions), ready-signature
# from PROMPT_SIGNATURES['codex'] — same one-signature-source as claude/gemini.
sig = rs.PROMPT_SIGNATURES.get(rt) or {}
sys.stdout.write(f"{rt}\t{sig.get('prompt_char','')}\n")
PYEOF
}

# --- [1m] model guard (the operator ruling 2026-08-15, feedback_all_models_1m_context) ---
# EVERY fable-5 and EVERY opus instance MUST be the [1m] 1M-context variant; a bare
# ~200k window burns ctx% ~5x faster and churns rotations (the leak). Normalizing
# bare -> [1m] only ever ENLARGES the window (always safe, never the small-window
# wedge), so no congruence needed. Scoped to the families the operator named (fable, opus):
# a model with no known 1M SKU (sonnet/haiku) passes through UNTOUCHED — exact scope.
normalize_model_1m() {
    local m="$1"
    [[ -z "$m" ]] && { printf ''; return 0; }          # empty => inherit settings.json default
    case "$m" in
        *'[1m]') printf '%s' "$m" ;;                     # already [1m] — idempotent
        claude-fable-*|claude-opus-*) printf '%s[1m]' "$m" ;;   # named families -> [1m]
        *) printf '%s' "$m" ;;                           # untouched (unknown 1M SKU)
    esac
}

# The "by effect" predicate: does a captured status-line report a [1m] model token?
# The live meter line renders e.g. "│ claude-opus-4-8[1m] │"; bare = a leak.
model_is_1m() {
    local status_line="$1"
    [[ "$status_line" == *'[1m]'* ]]
}

# shellcheck source=scripts/spawn_model_verify.sh
source "$SCRIPT_DIR/scripts/spawn_model_verify.sh"
# shellcheck source=scripts/spawn_guards.sh
source "$SCRIPT_DIR/scripts/spawn_guards.sh"   # issue #92: FATAL guards (model/runtime, injection)

# Post-spawn model verify (by effect): capture the TUI status line and confirm the
# running model is a [1m] variant. If bare and we know the intended model, switch
# it in-session via /model. Best-effort + non-fatal — a spawned agent is never
# killed over this; the worst case degrades to today's inherited-default behavior.
verify_spawn_model() {
    # NEVER fatal (B1 run-2 finding A): every read is `|| true`-guarded so `set -euo pipefail`
    # cannot abort the spawn between launch and --task injection; the verdict is always printed.
    local session="$1" intended="$2" line="" pane="" verdict="none"
    for _ in $(seq 1 8); do
        pane=$(tmux capture-pane -t "$session" -p 2>/dev/null || true)
        line=$(read_model_line "$pane")
        [[ -n "$line" ]] && break
        sleep 1
    done
    verdict=$(classify_model_line "$line")
    case "$verdict" in
        1m)     log "  [1m]-verify OK (by effect): '$session' running a [1m] model"; return 0 ;;
        family) warn "  [1m]-verify: '$session' banner shows '$(printf '%s' "$line" | grep -oE "$SPAWN_MODEL_LABEL_RE" | head -1)' — [1m] cannot be confirmed from this banner; continuing (non-fatal)"; return 0 ;;
        none)   warn "  [1m]-verify: could not read a model from '$session' (non-fatal); continuing"; return 0 ;;
    esac
    # bare: an explicit non-[1m] model id is visible. Correct ONLY if the operator asked for a
    # [1m] model (#95): config/providers.json ships no [1m] SKU for any family, so on a default
    # install this branch fired every spawn and could never succeed — permanent noise that trains
    # users to ignore warnings.
    if ! correction_warranted "$intended"; then
        log "  [1m]-verify OK: '$session' running '$(printf '%s' "$line" | tr -d '\n')' (no [1m] requested)"
        return 0
    fi
    warn "  [1m]-verify: '$session' came up on a BARE (non-[1m]) model — correcting"
    local target="$intended"
    [[ -z "$target" ]] && target="claude-opus-4-8[1m]"   # settings default variant
    target=$(normalize_model_1m "$target")
    tmux send-keys -t "$session" "/model $target" Enter || true
    sleep 2
    pane=$(tmux capture-pane -t "$session" -p 2>/dev/null || true)
    line=$(read_model_line "$pane")
    if model_is_1m "$line"; then
        log "  [1m]-verify: corrected '$session' to $target"
    else
        warn "  [1m]-verify: '$session' still not [1m] after /model $target — flag for the operator (non-fatal)"
    fi
    return 0
}

# Wait until the Claude TUI input prompt is visible (max 30s).
# Canary test 2026-07-09: a fixed sleep races Claude startup — early paste is
# silently dropped and the agent sits idle with no task.
wait_for_tui() {
    local session="$1" want="${2:-❯}"     # per-runtime ready-signature (§4.2)
    for _ in $(seq 1 30); do
        local pane_txt
        pane_txt=$(tmux capture-pane -t "$session" -p 2>/dev/null || true)
        if echo "$pane_txt" | grep -qF "Update available!"; then
            # Auto-skip update prompt on startup (Codex CLI update menus)
            tmux send-keys -t "$session" "2" Enter
            sleep 1
            continue
        fi
        if echo "$pane_txt" | grep -qF "$want"; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# Race-safe prompt injection into the Claude TUI, with verify + one retry.
# send-keys text + Enter with no delay loses the race against Ink's async
# input buffer (~5-10% silent failures). paste-buffer is atomic; the delayed
# Enter submits after ingestion settles; then we verify the text landed.
inject_prompt() {
    local session="$1" text="$2"
    local probe attempt
    probe=$(echo "$text" | head -c 60)
    for attempt in 1 2; do
        tmux set-buffer -b spawn-inject "$text"
        tmux paste-buffer -b spawn-inject -t "$session" -d
        sleep 0.7
        tmux send-keys -t "$session" Enter
        sleep 2
        if tmux capture-pane -t "$session" -p -S -15 2>/dev/null | grep -qF "$probe"; then
            return 0
        fi
        warn "Injection attempt $attempt not visible in '$session' — retrying"
        sleep 2
    done
    err "Injection FAILED twice for '$session' — agent may be idle without a task"
    return 1
}

# BG leg-(ii) P2.7 (Seam 2a): emit the launch_cmd env prefix that carries a BG green's
# marker vars INTO the pane. spawn_green (bg_green_env) sets BG_GREEN_ROOT/ALIAS +
# BG_QUARANTINE_ALIAS/WALDIR in OUR env, but `tmux new-session` inherits the tmux
# SERVER env, not ours — so the pane would not see them. We therefore prepend the vars
# to launch_cmd (same mechanism as the alt-screen guard) so the green's claude process,
# and its capture_green_sid SessionStart + quarantine PreToolUse hooks, are BG-aware.
# INERT: prints NOTHING for a normal spawn (BG_GREEN_ROOT unset). %q-quoted (safe values).
# NOTE (congruence reviewer, DEC-1788855536846541, non-blocking): the markers reach a pane
# ONLY via this explicit prefix — a green spawn cannot leak them to sibling panes. The one
# theoretical footgun is a green spawn that STARTS a fresh tmux server (server env would then
# carry BG_GREEN_ROOT to later panes); impossible in practice (the fleet server is always
# already running) AND the sid-hook install independently gates on this process's env, not the
# pane's, so a leaked pane env alone cannot trigger capture without the installed hook.
_bg_green_env_prefix() {
    [[ -n "${BG_GREEN_ROOT:-}" ]] || return 0
    printf 'BG_GREEN_ROOT=%q BG_GREEN_ALIAS=%q BG_QUARANTINE_ALIAS=%q BG_QUARANTINE_WALDIR=%q' \
        "$BG_GREEN_ROOT" "${BG_GREEN_ALIAS:-}" "${BG_QUARANTINE_ALIAS:-}" "${BG_QUARANTINE_WALDIR:-}"
}

# Spawn-path DB-first cache (DEC-1788603298): when the flat registry.json has flapped an
# agent out under the identity-store cutover (foreign-branch checkouts revert the tracked
# file on the shared tree — the motion-graphics / bg-drill-victim-g2 class), get_agent_field
# resolves the agent's registry record from the identity DB ONCE per agent (resolve-once,
# C4) and serves every field from this cache — one DB build per spawn + a consistent
# snapshot (no torn read if a rotation promotes/renames the provisional mid-spawn).
declare -gA _DB_FIELD_CACHE   # "<agent_id>|<field>" -> value
declare -gA _DB_AGENT_STATE   # "<agent_id>" -> 1 (in DB) | 0 (not in DB)

# Parse registry
get_agent_field() {
    local agent_id="$1" field="$2" out rc
    # Flat registry FIRST (heredoc UNCHANGED — byte-identical read). rc=1 => agent absent.
    out=$(python3 -c "
import json, sys
with open('$REGISTRY') as f:
    r = json.load(f)
a = r['agents'].get('$agent_id')
if not a:
    sys.exit(1)
v = a.get('$field', '')
if isinstance(v, list):
    print(' '.join(v))
else:
    print(v)
")
    rc=$?
    # INERT: cheap PURE-BASH cutover probe (mirrors both legs of cutover.is_active — env
    # OR flag file; NO python). FLAG OFF => EXACT legacy behavior: flat exit 0 returns the
    # value verbatim (incl 'None'/''), exit 1 returns 1. Byte-identical, no DB touch.
    local _state_dir; _state_dir="$(dirname "$REGISTRY")/state"
    if [[ ! -e "$_state_dir/identity-store-cutover.flag" && "${IDENTITY_STORE_CUTOVER:-}" != "1" ]]; then
        if [[ $rc -eq 0 ]]; then printf '%s\n' "$out"; return 0; else return 1; fi
    fi
    # ARMED. Fast path: a NON-EMPTY, non-'None' flat value wins (the fully-populated agent).
    if [[ $rc -eq 0 && -n "$out" && "$out" != "None" ]]; then
        printf '%s\n' "$out"
        return 0
    fi
    # Flat MISS *or* a present-but-NULL/EMPTY field. The DP-B2 provisional-alias payload
    # carries cwd=null, and bg_arm's project_now momentarily writes that null into
    # registry.json — a flat-first short-circuit would return 'None'/'' and bypass the
    # DB-first [C1] resolve-chain (resolver.registry_agent_db: null alias cwd ->
    # lineages[root].cwd -> root agent-doc cwd -> orch, never None). So fall through to the
    # DB for ANY null/empty flat field, not just a whole-agent miss (gm option a — the
    # general class fix: DB-first resolve-chain, never a flat-projected gap wins).
    # DB fallback, RESOLVE-ONCE per agent: dump the whole spawnable record in one call.
    if [[ -z "${_DB_AGENT_STATE[$agent_id]:-}" ]]; then
        local dump k v
        if dump=$(ORCHESTRA_DIR="$(dirname "$REGISTRY")" python3 \
                "$SCRIPT_DIR/scripts/identity_store/spawn_registry_resolve.py" \
                --dump "$agent_id" 2>/dev/null); then
            while IFS=$'\t' read -r k v; do
                [[ -n "$k" ]] && _DB_FIELD_CACHE["$agent_id|$k"]="$v"
            done <<< "$dump"
            _DB_AGENT_STATE[$agent_id]=1
        else
            _DB_AGENT_STATE[$agent_id]=0
        fi
    fi
    # A NON-EMPTY DB value fills the flat gap (the [C1] cwd inherit lives here).
    if [[ "${_DB_AGENT_STATE[$agent_id]}" == "1" ]]; then
        local key="$agent_id|$field"
        if [[ -v "_DB_FIELD_CACHE[$key]" && -n "${_DB_FIELD_CACHE[$key]}" ]]; then
            printf '%s\n' "${_DB_FIELD_CACHE[$key]}"
            return 0
        fi
    fi
    # DB had nothing better. Return the flat result, NORMALIZED ('None' json-null -> ''),
    # or exit 1 if the agent was absent from BOTH flat and DB.
    if [[ $rc -eq 0 ]]; then
        [[ "$out" == "None" ]] && out=""
        printf '%s\n' "$out"
        return 0
    fi
    return 1
}

# FIX #2 (rotate_agent fable-DOA class): resolve the successor's LAUNCH model, letting an
# explicit AGENT_MODEL override win over the resolved registry/DB field. A rotation whose
# flat -gN alias record is stale (e.g. a prior fable-DOA spawn left model=claude-fable-5[1m])
# would otherwise re-launch on the credit-walled model that get_agent_field reads flat-first;
# rotate_agent now computes the intended model (from --model / predecessor) and passes it as
# AGENT_MODEL, and this resolver honors it. With NO override set (the common bare-spawn case)
# behavior is EXACT prior: the declared field. An EMPTY override (${x:-}) falls through — it
# never overrides with nothing. normalize_model_1m applies to whichever wins (the [1m] guard
# is preserved on both paths).
resolve_spawn_model() {  # $1 agent_id -> echoes normalized launch model
    local agent_id="$1"
    normalize_model_1m "${AGENT_MODEL:-$(get_agent_field "$agent_id" "model" || true)}"
}

list_agents() {
    python3 -c "
import json
with open('$REGISTRY') as f:
    r = json.load(f)
print(f'{'ID':<20} {'Tier':<5} {'Name':<25} {'Machine':<6} {'Always On':<10}')
print('-' * 70)
for aid, a in sorted(r['agents'].items(), key=lambda x: (x[1] or {}).get('tier', 'T9')):
    print(f'{aid:<20} {a.get(\"tier\", \"-\"):<5} {a.get(\"name\", aid):<25} {a.get(\"machine\", \"-\"):<6} {str(a.get(\"always_on\", False)):<10}')
"
}

list_running() {
    echo -e "${CYAN}Running tmux sessions:${NC}"
    tmux list-sessions 2>/dev/null || echo "  (none)"
}

kill_agent() {
    local agent_id="$1"
    local tmux_name
    tmux_name=$(get_agent_field "$agent_id" "tmux_session") || { err "Unknown agent: $agent_id"; exit 1; }

    if tmux has-session -t "$tmux_name" 2>/dev/null; then
        # Save handoff context before killing
        log "Saving handoff for $agent_id..."
        python3 "$SCRIPT_DIR/agent_handoff.py" save "$agent_id" >/dev/null 2>&1 || warn "Handoff save failed for $agent_id"

        # Try graceful shutdown first
        tmux send-keys -t "$tmux_name" "/exit" Enter 2>/dev/null || true
        sleep 2
        if tmux has-session -t "$tmux_name" 2>/dev/null; then
            tmux kill-session -t "$tmux_name"
        fi
        log "Killed agent: $agent_id (session: $tmux_name)"

        # Update state
        python3 -c "
import json, datetime
state_file = '$STATE_DIR/$agent_id.json'
try:
    with open(state_file) as f:
        s = json.load(f)
except:
    s = {}
s['status'] = 'killed'
s['killed_at'] = datetime.datetime.now().isoformat()
with open(state_file, 'w') as f:
    json.dump(s, f, indent=2)
"
    else
        warn "Agent $agent_id not running"
    fi
}

kill_all() {
    python3 -c "
import json
with open('$REGISTRY') as f:
    r = json.load(f)
for aid in r['agents']:
    print(aid)
" | while read -r aid; do
        kill_agent "$aid" 2>/dev/null || true
    done
}

spawn_agent() {
    local agent_id="$1"
    local task="${2:-}"
    # --resume <sid> (gm msg_e9a921fe ruling (2), 2026-09-16): the ONE sanctioned resume
    # path. roster-resume-all.sh used to raw tmux+`claude --resume` (registered nothing).
    # The adopt gate receives the known sid, the launch carries --resume, and the init
    # paste is a short wake line (the session already holds its context).
    local resume_sid="${3:-}"
    local parent_id="${PARENT_AGENT_ID:-}"
    local reincarnation="${REINCARNATION:-false}"

    # Read agent config — auto-register unknown agents instead of failing.
    # Unregistered agents were invisible to recovery/reincarnation (12 of 18 live
    # sessions weren't in the registry). Defaults: T2, vps, cwd from $AGENT_CWD
    # or orchestra dir, not always_on.
    local tmux_name tier cwd prompt_file name machine memory_scope
    if ! tmux_name=$(get_agent_field "$agent_id" "tmux_session"); then
        local reg_cwd="${AGENT_CWD:-$SCRIPT_DIR}"
        warn "Agent $agent_id not in registry — auto-registering (cwd: $reg_cwd)"
        # R4 registration invariant (spec §4.1 R4(a)): a NEW row MUST carry a
        # resolvable runtime — the zero-signal class cannot regrow. The write is
        # routed through the shared flock-safe writer (registry-update.py), which
        # (a) ENFORCES the invariant, (b) HONORS $REGISTRY via REGISTRY_PATH, and
        # (c) drops the old inline os.replace (an inode swap that orphans registry
        # flocks — memory reference_registry_write_protocol). runtime comes from
        # AGENT_RUNTIME, or AGENT_MODEL is derived by the writer; with NEITHER the
        # writer refuses and we abort rather than seat a guessed-claude row.
        local -a ar_fields=( --field "name=$agent_id" --field "tier=T2"
            --field "machine=vps" --field "cwd=$reg_cwd"
            --field "tmux_session=$agent_id" --field "system_prompt="
            --field "always_on=false"
            --field "auto_registered=$(date -u +%Y-%m-%dT%H:%M:%SZ)" )
        [[ -n "${AGENT_RUNTIME:-}" ]] && ar_fields+=( --field "runtime=$AGENT_RUNTIME" )
        [[ -n "${AGENT_MODEL:-}" ]] && ar_fields+=( --field "model=$AGENT_MODEL" )
        # Under the identity-store cutover the generic registry CLI REFUSES to
        # establish a NEW identity (CutoverRefused, by design) — which left this
        # path with no legitimate new-agent route and grew the raw-tmux-spawn
        # class (gm msg_00bc6d2a). Route establishment through the sanctioned
        # U16 adopt seam instead: registers lineage+gen+canonical+source_record,
        # verifies by re-read, and PROJECTS so the get_agent_field reads below
        # see the row immediately. Fail-closed: no complete identity, no seat.
        if [[ -e "$STATE_DIR/identity-store-cutover.flag" \
              || "${IDENTITY_STORE_CUTOVER:-}" == "1" ]]; then
            if ! ORCHESTRA_DIR="$ORCHESTRA_DIR" python3 \
                    "$SCRIPT_DIR/scripts/identity_store/spawn_adopt.py" \
                    "$agent_id" --runtime "${AGENT_RUNTIME:-}" \
                    --model "${AGENT_MODEL:-}" --tier "T2" \
                    --cwd "$reg_cwd" --machine "vps" >/dev/null; then
                err "$agent_id: auto-register REFUSED under cutover — a new agent"
                err "  needs a COMPLETE identity (set AGENT_RUNTIME=claude|gemini"
                err "  AND AGENT_MODEL); refusing to seat an unregistered pane"
                exit 3
            fi
        elif ! REGISTRY_PATH="$REGISTRY" python3 "$SCRIPT_DIR/scripts/registry-update.py" \
                "$agent_id" "${ar_fields[@]}" >/dev/null; then
            err "$agent_id: auto-register REFUSED — a new agent needs a runtime "
            err "  (set AGENT_RUNTIME=claude|gemini or AGENT_MODEL, or declare via "
            err "  backfill_registry_runtime.py --declare); refusing to seat a "
            err "  zero-signal row and guess claude (spec §4.1 R4(a))"
            exit 3
        fi
        tmux_name="$agent_id"
    fi
    # A row registered by hand (INSTALL.md §3) may carry no tmux_session; the auto-register
    # fallback above only runs for UNKNOWN agents, so spawn used to fail with "invalid session:"
    # (B1 finding 2). Default to the agent id and record it.
    if [[ -z "$tmux_name" || "$tmux_name" == "None" || "$tmux_name" == "null" ]]; then
        tmux_name="$agent_id"
        warn "Agent $agent_id has no tmux_session in the registry — using '$agent_id' and recording it"
        REGISTRY_PATH="$REGISTRY" python3 "$SCRIPT_DIR/scripts/registry-update.py" \
            "$agent_id" --field "tmux_session=$agent_id" >/dev/null 2>&1 \
            || warn "  could not record tmux_session for $agent_id (continuing)"
    fi
    tier=$(get_agent_field "$agent_id" "tier")
    cwd=$(get_agent_field "$agent_id" "cwd")
    prompt_file=$(get_agent_field "$agent_id" "system_prompt")
    # Role prompt default (the operator 'resume apprvd-pm' 2026-09-16): a blank/missing system_prompt
    # field booted the seat on FOUNDATION_STATIC only although prompts/<id>.md existed.
    if [[ -z "$prompt_file" || "$prompt_file" == "None" || "$prompt_file" == "null" ]] \
          && [[ -f "$SCRIPT_DIR/prompts/${agent_id}.md" ]]; then
        prompt_file="prompts/${agent_id}.md"
        log "  Role prompt defaulted to $prompt_file"
    fi
    name=$(get_agent_field "$agent_id" "name")
    machine=$(get_agent_field "$agent_id" "machine")
    memory_scope=$(get_agent_field "$agent_id" "memory_scope")
    # [1m] guard: a declared model is normalized to its [1m] variant and passed
    # EXPLICITLY, so a fable lineage can't drop [1m] to a bare ~200k window. Empty
    # (no declared model) inherits settings.json's default (opus-4-8[1m]) — fine.
    local model
    # FIX #2: AGENT_MODEL override wins over the (possibly stale-fable) resolved field.
    model=$(resolve_spawn_model "$agent_id")

    # --- Runtime dispatch (§4.2): resolve binary + ready-signature by the
    # declared runtime; REFUSE service/codex/unknown rather than defaulting to
    # claude. Resolved BEFORE any tmux/session work so a refusal writes nothing.
    local runtime prompt_char dispatch agent_bin
    if ! dispatch=$(runtime_dispatch "$agent_id" 2>&1); then
        err "$agent_id: spawn REFUSED — ${dispatch}"
        exit 3
    fi
    runtime="${dispatch%%$'\t'*}"
    prompt_char="${dispatch#*$'\t'}"
    case "$runtime" in
        claude) agent_bin="$CLAUDE_BIN" ;;
        gemini) agent_bin="$(command -v agy 2>/dev/null || echo agy)" ;;
        codex)  agent_bin="$(command -v codex 2>/dev/null || echo codex)" ;;
        *) err "$agent_id: unsupported resolved runtime '$runtime' — refusing"; exit 3 ;;
    esac
    # issue #92: a model id that names ANOTHER runtime (claude-sonnet-5 on a codex seat)
    # makes the CLI exit and the init prompt land in bare bash — refuse before any pane.
    refuse_model_mismatch "$runtime" "$model" "$agent_id" || exit 3

    # Check if we're on the right machine
    local this_machine="mac"
    if [[ "$(hostname)" == *"${ORCHESTRA_VPS_HOSTNAME:-__unset__}"* ]] || [[ "$(whoami)" == "root" ]]; then
        this_machine="vps"
    fi
    # Single-machine install: [machines] in orchestra.toml is blank (no vps_hostname, no
    # tailscale ips), so there is nowhere else to dispatch to — this host IS the agent's
    # machine, whatever label the row carries (B1 finding 1: the doc's own machine=vps example
    # refused to spawn for a non-root user).
    if [[ -z "${ORCHESTRA_VPS_HOSTNAME:-}" && -z "${ORCHESTRA_MAC_TAILSCALE_IP:-}" \
          && -z "${ORCHESTRA_VPS_TAILSCALE_IP:-}" ]]; then
        this_machine="$machine"
    fi

    if [[ "$machine" != "$this_machine" ]]; then
        err "Agent $agent_id belongs on $machine but we're on $this_machine"
        err "To spawn remotely, use: ssh to $machine and run this command there"
        exit 1
    fi

    # Check if already running
    if tmux has-session -t "$tmux_name" 2>/dev/null; then
        warn "Agent $agent_id already running in tmux session '$tmux_name'"
        if [[ -n "$task" ]]; then
            log "Sending task to existing session..."
            inject_or_fail "$tmux_name" "$task" || return 1     # issue #92: never a silent miss
        fi
        return 0
    fi

    # Validate cwd exists
    if [[ ! -d "$cwd" ]]; then
        err "Working directory does not exist: $cwd"
        exit 1
    fi

    # Build composite system prompt: FOUNDATION_STATIC (cached) + role prompt
    local static_prompt="$SCRIPT_DIR/prompts/FOUNDATION_STATIC.md"
    local role_prompt="$SCRIPT_DIR/$prompt_file"
    local combined_prompt="/tmp/agent-prompt-${agent_id}.md"

    if [[ -f "$static_prompt" ]]; then
        cat "$static_prompt" > "$combined_prompt"
        echo "" >> "$combined_prompt"
        echo "# --- ROLE-SPECIFIC PROMPT ---" >> "$combined_prompt"
        if [[ -f "$role_prompt" ]]; then
            cat "$role_prompt" >> "$combined_prompt"
        else
            warn "Role prompt not found: $role_prompt — using FOUNDATION_STATIC only"
        fi
    elif [[ -f "$role_prompt" ]]; then
        # Fallback: no static foundation, use role prompt alone
        warn "FOUNDATION_STATIC.md not found — using role prompt only"
        cp "$role_prompt" "$combined_prompt"
    else
        warn "No system prompts found — proceeding without system prompt"
        combined_prompt=""
    fi

    local full_prompt="$combined_prompt"

    # Build memory context loading command
    local memory_cmd=""
    if [[ -f "$OMNI_DIR/global/context_layer.json" ]]; then
        memory_cmd="Read ~/scripts/omni-context/global/context_layer.json first."
    fi

    # PM Startup Ritual — inject full briefing for T1 PMs
    local pm_briefing=""
    if [[ "$tier" == "T1" && -x "$SCRIPT_DIR/pm-startup.sh" ]]; then
        log "Running PM startup ritual for $agent_id..."
        pm_briefing=$("$SCRIPT_DIR/pm-startup.sh" "$agent_id" 2>/dev/null || true)
    fi

    # Check for reincarnation handoff
    local reincarnation_handoff="$STATE_DIR/handoffs/${agent_id}.json"
    local is_reincarnation="false"
    if [[ "$reincarnation" == "true" ]] && [[ -f "$reincarnation_handoff" ]]; then
        is_reincarnation="true"
        log "  REINCARNATION: reading handoff from previous instance"
    fi

    # Build the initial prompt
    local init_prompt="You are ${name} (${agent_id}), a ${tier} agent in this OrchestraOS install."
    init_prompt+=" Your working directory is ${cwd}."

    # Inject reincarnation protocol
    local reincarnation_protocol="$SCRIPT_DIR/prompts/_reincarnation-protocol.md"
    if [[ -f "$reincarnation_protocol" ]]; then
        init_prompt+=" Read $reincarnation_protocol for the reincarnation protocol — follow it when your context gets full."
    fi

    # If this IS a reincarnation, inject the handoff as primary context
    if [[ "$is_reincarnation" == "true" ]]; then
        init_prompt+=" --- REINCARNATION HANDOFF --- You are a reincarnation of a previous instance."
        init_prompt+=" Read $reincarnation_handoff for your previous state. Your #1 priority is continuing exactly where the previous instance left off."
        init_prompt+=" The user should not notice the transition. Start by reading the handoff, then execute the next_action. --- END HANDOFF ---"
    fi

    # Inject shared infrastructure context (Telegram, Tailscale, topology, etc.)
    local infra_ctx="$SCRIPT_DIR/prompts/infrastructure.md"
    if [[ -f "$infra_ctx" ]]; then
        init_prompt+=" Read $infra_ctx for infrastructure context (how to text the operator, serve URLs, communicate with the GM)."
    fi

    # Per-seat memory (docs/MEMORY.md): the directory is keyed by the LINEAGE id (the
    # seat name with any -gN / -genN generation suffix stripped) so every generation of
    # a seat reads and extends the same files — that is what makes gate step 7
    # ("one fact written, restart, agent recalls it") pass.
    local memory_root
    memory_root="$(printf '%s' "$agent_id" | sed -E 's/-(g|gen)[0-9]+$//')"
    local memory_dir="$ORCHESTRA_DIR/memory/${memory_root}"
    mkdir -p "$memory_dir"
    [[ -f "$memory_dir/MEMORY.md" ]] || printf '# %s memory index\n' "$memory_root" > "$memory_dir/MEMORY.md"
    init_prompt+=" Your memory directory is $memory_dir — read $memory_dir/MEMORY.md now, before anything else; it indexes one-fact files that survive restarts and rotations. The convention (index + one-fact files + baton) is the Memory section of $SCRIPT_DIR/prompts/_agent-protocol.md — follow it."

    # Inject tier-appropriate memory payload via token enforcer
    local memory_payload=""
    if [[ -f "$OMNI_DIR/token_enforcer.py" ]]; then
        memory_payload=$(python3 "$OMNI_DIR/token_enforcer.py" --agent "$agent_id" 2>/dev/null || true)
    fi

    if [[ -n "$memory_payload" ]]; then
        init_prompt+=" --- MEMORY CONTEXT --- ${memory_payload} --- END MEMORY ---"
    elif [[ -n "$memory_cmd" ]]; then
        # Fallback to basic context_layer.json if token enforcer fails
        init_prompt+=" ${memory_cmd}"
    fi

    # Check for project handoff file
    local project_id
    project_id=$(echo "$memory_scope" | awk '{print $1}')
    local handoff="$OMNI_DIR/projects/$project_id/handoff.md"
    if [[ -f "$handoff" ]]; then
        init_prompt+=" Read the handoff file at $handoff to see where the last session left off."
    fi

    # Load agent conversation handoff (previous session context)
    local agent_handoff=""
    if [[ -f "$SCRIPT_DIR/agent_handoff.py" ]]; then
        agent_handoff=$(python3 "$SCRIPT_DIR/agent_handoff.py" load "$agent_id" 2>/dev/null || true)
    fi
    if [[ -n "$agent_handoff" && "$agent_handoff" != "No handoff found"* ]]; then
        init_prompt+=" ${agent_handoff}"
    fi

    # --- Comprehension-gate read-back demand (Phase B2 §6.5, RED-TEAM Finding 0) ---
    # Spawning WITH a handoff/reincarnation context => the machinery itself demands
    # the own-words read-back (+ canary questions if the predecessor authored them,
    # via rotation_gate_manual — QUESTIONS only, answers stay daemon-held).
    # FAIL-OPEN on every extra: a missing/corrupt canary file NEVER blocks the spawn.
    if [[ "$is_reincarnation" == "true" ]] || [[ -n "$agent_handoff" && "$agent_handoff" != "No handoff found"* ]]; then
        local canary_qs=""
        canary_qs=$(python3 "$SCRIPT_DIR/scripts/rotation_gate_manual.py" questions "$agent_id" 2>/dev/null || true)
        init_prompt+=" --- REQUIRED FIRST ACT (comprehension gate) --- Before any work:"
        init_prompt+=" (1) restate your mission, standing guards, open loops, and top hazards IN YOUR OWN WORDS (never quoting the handoff);"
        if [[ -n "$canary_qs" ]]; then
            init_prompt+=" (2) answer these canary questions: ${canary_qs//$'\n'/ };"
        fi
        init_prompt+=" write both to $STATE_DIR/agent-handoffs/${agent_id}.readback.md."
        init_prompt+=" Your spawner grades this via the comprehension gate before your rotation counts as complete. --- END FIRST ACT ---"
    fi

    # Inject PM briefing if available
    if [[ -n "$pm_briefing" ]]; then
        init_prompt+=" --- PM STARTUP BRIEFING --- ${pm_briefing} --- END BRIEFING ---"
    fi

    # Special GM context — gets all project summaries
    if [[ "$agent_id" == "gm" ]]; then
        local gm_projects=""
        for project_dir in "$OMNI_DIR"/projects/*/; do
            local proj_name=$(basename "$project_dir")
            local roadmap="$project_dir/ROADMAP.md"
            if [[ -f "$roadmap" ]]; then
                local status=$(grep -m1 'Status:' "$roadmap" 2>/dev/null | sed 's/.*\*\* //' || true)
                local phase=$(grep -m1 'Phase:' "$roadmap" 2>/dev/null | sed 's/.*\*\* //' || true)
                gm_projects+="  $proj_name: [$status] $phase\n"
            elif [[ -f "$project_dir/handoff.md" ]]; then
                local last_line=$(head -3 "$project_dir/handoff.md" 2>/dev/null | tail -1 || true)
                gm_projects+="  $proj_name: $last_line\n"
            fi
        done
        if [[ -n "$gm_projects" ]]; then
            init_prompt+=" ALL PROJECT STATUS:\n${gm_projects}"
        fi
    fi

    if [[ -n "$task" ]]; then
        init_prompt+=" YOUR TASK: ${task}"
    else
        init_prompt+=" Check your inbox at ~/scripts/agent-orchestra/queue/inbox/${agent_id}/ for pending tasks."
    fi

    # --- Fail-closed identity gate under the identity-store cutover ---------
    # (gm msg_00bc6d2a, spawn-under-cutover class): a spawn that does not
    # register (lineage+generation+canonical+source_record), VERIFY the rows by
    # re-read, and PROJECT them must NOT come up as a live seat. spawn_adopt.py
    # wires the sanctioned U16 adopt seam into this general path — the missing
    # legitimate NEW-agent route whose absence grew the raw-tmux-spawn class.
    # Inactive cutover => {"handled": false} and the legacy behavior is
    # byte-identical. Exit 3 from the gate refuses the spawn entirely.
    if ! ORCHESTRA_DIR="$ORCHESTRA_DIR" python3 \
            "$SCRIPT_DIR/scripts/identity_store/spawn_adopt.py" "$agent_id" \
            --runtime "$runtime" --model "${model:-}" --tier "${tier:-T2}" \
            --cwd "$cwd" --machine "${machine:-vps}" \
            ${resume_sid:+--session-id "$resume_sid"} >/dev/null; then
        err "$agent_id: spawn REFUSED — identity-store adopt/verify failed under"
        err "  the cutover (see stderr above). No registered+projected identity ="
        err "  no seat: an unregistered pane is invisible to routing/recovery and"
        err "  dual-chips the operator's surface. Set AGENT_RUNTIME/AGENT_MODEL if missing."
        exit 3
    fi

    # Create tmux session
    log "Spawning $agent_id ($name) in tmux session '$tmux_name'..."
    log "  Tier: $tier | Machine: $machine | CWD: $cwd"

    # Pre-trust the cwd so an INTERACTIVE claude spawn does NOT stall at the
    # workspace-trust dialog. --dangerously-skip-permissions does NOT skip that
    # dialog for a TTY session (only -p/non-TTY skips it) — a spawn in a
    # non-pre-trusted cwd (e.g. a BG green in ~/repos/<x>) freezes with no one to
    # answer it, and a deterministic boot-probe can't detect the dead LLM. Only
    # meaningful for the claude runtime; fail-soft, never blocks the spawn.
    # (gm-g44 2026-09-05: first-real-BG-green froze on an untrusted repo cwd.)
    if [[ "$runtime" == "claude" ]]; then
        python3 "$SCRIPT_DIR/scripts/ensure_cwd_trusted.py" "$cwd" 2>&1 \
            | sed 's/^/[spawn] /' || warn "  trust pre-seed skipped for '$cwd'"
    fi

    tmux new-session -d -s "$tmux_name" -c "$cwd"

    # Telemetry-v2 (INERT until the systemd unit is installed+started): attach
    # pipe-pane at session-create so the real-time lane captures from t0 with no
    # daemon dependency for the ATTACH. The helper self-gates on telemetryd being
    # live (a fresh status.json) — so this is a NO-OP until the operator activates the
    # unit, and never orphans an unconsumed growing sink. All command-injection
    # defense + shell-quoting live in Python (reused from pipe_pane); the session
    # name is validated there. Fail-soft: a pipe-pane failure NEVER blocks spawn.
    # The telemetryd attach-SWEEP re-attaches across rotations (invariant-keeper).
    python3 "$SCRIPT_DIR/scripts/lineage_daemon/spawn_pipe_attach.py" "$tmux_name" \
        >/dev/null 2>&1 || warn "  Telemetry: pipe-pane attach skipped for '$tmux_name'"

    # Write init prompt to file (avoids shell escaping issues with long prompts)
    local init_file="/tmp/agent-init-${agent_id}.md"
    echo "$init_prompt" > "$init_file"

    # Launch the runtime's CLI in interactive mode (§4.2 dispatch): the binary
    # is chosen by the declared runtime, not hardcoded to claude. The [1m]
    # model flag + post-spawn model verify are claude-specific and apply only
    # to the claude runtime; a gemini seat launches agy with no --model.
    # Per-runtime bypass flag (C-R2, ledger row 10): claude/gemini use
    # --dangerously-skip-permissions; codex uses --yolo + --dangerously-bypass-hook-trust
    # to run lifecycle hooks without interactive prompt stalls.
    local bypass_flag="--dangerously-skip-permissions"
    [[ "$runtime" == "codex" ]] && bypass_flag="--yolo --dangerously-bypass-hook-trust"
    local launch_cmd="$agent_bin $bypass_flag"
    # Scrollback guard (2026-09-03): claude >=2.1.x renders in the terminal
    # ALTERNATE SCREEN by default, which zeroes tmux scrollback (history_size=0,
    # alternate_on=1) — every pane-capture/copy-mode consumer in the fleet
    # (agent-status, watch consoles, the operator's manual scrolling) depends on normal-
    # screen output. Official kill-switch per CC docs/issue #67289:
    [[ "$runtime" == "claude" ]] && launch_cmd="CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1 $launch_cmd"
    # BG leg-(ii) P2.7 (Seam 2a): carry a BG green's marker env into the pane so its
    # capture_green_sid / green_boot_probe / quarantine hooks fire (INERT if not a green).
    local _bg_prefix; _bg_prefix="$(_bg_green_env_prefix)"
    [[ -n "$_bg_prefix" ]] && launch_cmd="$_bg_prefix $launch_cmd"
    # Install env into the pane (Tier 0 item 2): `tmux new-session` inherits the tmux SERVER
    # env, not ours, so a seat spawned by `orchestra spawn` would otherwise run msg_store.py /
    # approval.py against the default data dir and load hooks from the default config dir.
    # Carry the data dir, checkout, config and (when set) the Claude config dir explicitly.
    local _orch_prefix
    _orch_prefix="$(printf 'ORCHESTRA_DIR=%q ORCH_DIR=%q ORCHESTRA_ROOT=%q' "$ORCHESTRA_DIR" "$ORCHESTRA_DIR" "$SCRIPT_DIR")"
    [[ -n "${ORCHESTRA_CONFIG:-}" ]] && _orch_prefix="$_orch_prefix $(printf 'ORCHESTRA_CONFIG=%q' "$ORCHESTRA_CONFIG")"
    [[ -n "${CLAUDE_CONFIG_DIR:-}" ]] && _orch_prefix="$_orch_prefix $(printf 'CLAUDE_CONFIG_DIR=%q' "$CLAUDE_CONFIG_DIR")"
    launch_cmd="$_orch_prefix $launch_cmd"
    if [[ -n "$resume_sid" && "$runtime" != "claude" ]]; then
        err "$agent_id: --resume is claude-only (runtime=$runtime has no resume adapter here)"
        tmux kill-session -t "$tmux_name" 2>/dev/null || true
        exit 3
    fi
    if [[ "$runtime" == "claude" ]]; then
        [[ -n "$model" ]] && launch_cmd+=" --model $model"   # [1m] guard: explicit model
        [[ -n "$resume_sid" ]] && launch_cmd+=" --resume $resume_sid"
        # Layer A (Bug 2, live-but-unreachable): pre-configure permission
        # allow-rules so benign PROJECT-LOCAL self-edits never raise the
        # class-2 permission prompt a headless seat can't answer.
        # BOUNDARY (gm ruling 1, BINDING): the allow-set is STRICTLY
        # cwd/project-local + own-state/.workspace WRITES — never widen to
        # blanket shell/network/cross-tenant; anything outside still prompts
        # and escalates via Layer B (scripts/pane_reachability.py).
        local perm_settings=""
        # STDERR IS KEPT (#95): this was `2>/dev/null || true`, so when the generator was
        # missing entirely the operator saw "generation failed" with no cause — and a security
        # feature degraded to "no rules" without ever saying why.
        local perm_err=""
        perm_err="$(mktemp)"
        perm_settings="$(python3 "$SCRIPT_DIR/scripts/spawn_permission_rules.py" "$agent_id" "$cwd" 2>"$perm_err" || true)"
        if [[ -n "$perm_settings" && -f "$perm_settings" ]]; then
            launch_cmd+=" --settings $perm_settings"
            log "  Perms: project-local allow-rules -> $perm_settings"
        else
            warn "  Perms: allow-rule generation failed — spawning without (prompts escalate via Layer B)"
            # if-form, not `[[ ]] && warn`. I expected the && form to abort the spawn under
            # `set -euo pipefail` when the file is empty and DROVE IT: it does not — set -e
            # ignores a failure that is not the command following the final &&. Kept as an if
            # because it reads as a branch and cannot acquire that hazard later.
            if [[ -s "$perm_err" ]]; then
                warn "  Perms: cause: $(head -3 "$perm_err" | tr '\n' ' ')"
            fi
        fi
        rm -f "$perm_err"
        log "  Runtime: claude | Model: ${model:-<settings default opus-4-8[1m]>}"
    elif [[ "$runtime" == "codex" ]]; then
        [[ -n "$model" ]] && launch_cmd+=" --model $model"
        log "  Runtime: codex | Model: ${model:-<config default>}"
    else
        log "  Runtime: $runtime | Binary: $agent_bin"
    fi
    tmux send-keys -t "$tmux_name" "$launch_cmd" Enter

    # Wait for the runtime's TUI to be ready (polling, not a blind sleep),
    # using the per-runtime ready-signature from the ONE signature source.
    if ! wait_for_tui "$tmux_name" "$prompt_char"; then
        warn "$runtime TUI not ready after 30s in '$tmux_name' — injecting anyway"
    fi

    # POST-SPAWN VERIFY (by effect, claude-only): the running model MUST be a
    # [1m] variant. If the status line shows a bare model, switch it in-session
    # + log. Reuses the verify-model-by-effect discipline the rotation hook
    # mandates. Skipped for non-claude runtimes (no [1m] SKU concept).
    if [[ "$runtime" == "claude" ]]; then
        verify_spawn_model "$tmux_name" "$model" || warn "  [1m]-verify returned non-zero (ignored)"
    fi

    # Inject the prompt by telling Claude to read the init file (race-safe, verified).
    # The DECLARATION must ride the FIRST USER MESSAGE, not only the init file.
    # 2026-08-19, found by orchestra-builder-g12 (which refused to self-declare to pass
    # its own gate): a pointer-only injection leaves the transcript UNDECLARED, so
    # sid_invariants.declared_identity() returns None. That breaks the identity plane in
    # three places at once — H2 refuses the comprehension gate ("unverifiable is never a
    # pass"), _resolve_by_declaration falls through to a LIVE PREDECESSOR's session, and
    # promote_successor correctly refuses to promote. Hand-spawned agents were unaffected
    # because their send-keys already carried "You are <id>"; only this path was cut.
    if [[ -n "$resume_sid" ]]; then
        log "  Resume: session $resume_sid (roster/adopt-gated); short wake line, no init file"
        inject_or_fail "$tmux_name" "You are $agent_id, resumed (session $resume_sid) after a crash by roster-resume-all. Check your msg_store inbox (python3 $SCRIPT_DIR/msg_store.py inbox --agent $agent_id) and continue your last task." || exit 1
    else
        inject_or_fail "$tmux_name" "You are $agent_id. Read $init_file and follow all instructions in it." || exit 1
    fi
    # issue #92: past here the seat is instructed — only now may the spawn report success.

    # Record state (includes parent tracking for completion callbacks)
    python3 -c "
import json, datetime
state = {
    'agent_id': '$agent_id',
    'name': '$name',
    'tier': '$tier',
    'tmux_session': '$tmux_name',
    'status': 'spawning',
    'spawned_at': datetime.datetime.now().isoformat(),
    'cwd': '$cwd',
    'task': '''$task''',
    'machine': '$machine',
    'parent_id': '$parent_id' if '$parent_id' else None,
    'reincarnation': '$reincarnation' == 'true',
    'generation': 1
}
# If reincarnating, increment generation from previous state
if '$reincarnation' == 'true':
    try:
        with open('$STATE_DIR/$agent_id.json') as f:
            prev = json.load(f)
        state['generation'] = prev.get('generation', 0) + 1
    except: pass
with open('$STATE_DIR/$agent_id.json', 'w') as f:
    json.dump(state, f, indent=2)
"

    # Post-boot sid attribution (systemic, the operator 'resume apprvd-pm' 2026-09-16): a plain
    # spawn used to leave generations.session_id NULL / no resume_command until a human or
    # the */15 reconciler attributed it. Bounded (8 x 3s), detached so the spawn returns now,
    # DB-first + re-project; inert when the cutover is off. Fail-soft: never blocks the seat.
    ( ORCHESTRA_DIR="$ORCHESTRA_DIR" nohup python3 "$SCRIPT_DIR/scripts/identity_store/spawn_attribute_sid.py" \
          "$agent_id" --runtime "$runtime" >> "$ORCHESTRA_DIR/logs/spawn-attribute-sid.log" 2>&1 </dev/null & ) \
        || warn "  sid attribution not started for $agent_id (reconciler will attribute)"
    log "${GREEN}Agent $agent_id spawned successfully${NC}"
    log "  Attach: tmux attach -t $tmux_name"
    log "  Send:   tmux send-keys -t $tmux_name 'your message' Enter"
}

# --- Main (guarded so the file is sourceable for unit tests) ---
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
case "${1:-}" in
    --list|-l)
        list_agents
        ;;
    --running|-r)
        list_running
        ;;
    --kill|-k)
        [[ -z "${2:-}" ]] && { err "Usage: spawn-agent.sh --kill <agent-id>"; exit 1; }
        kill_agent "$2"
        ;;
    --kill-all)
        kill_all
        ;;
    --dispatch)
        # §4.2 dispatch preview — resolve runtime + ready-signature WITHOUT
        # creating a tmux session (a tmux-free proof of the dispatch table +
        # service/codex/unknown refusal). Prints "<runtime>\t<prompt_char>" or
        # a REFUSE line + exit 3.
        [[ -z "${2:-}" ]] && { err "Usage: spawn-agent.sh --dispatch <agent-id>"; exit 1; }
        if out=$(runtime_dispatch "$2" 2>&1); then
            echo "$out"
        else
            echo "$out" >&2
            exit 3
        fi
        ;;
    --help|-h|"")
        echo "Usage: spawn-agent.sh <agent-id> [--task \"task description\"] [--resume <sid>]"
        echo "       spawn-agent.sh --list        List all agents"
        echo "       spawn-agent.sh --running     Show running tmux sessions"
        echo "       spawn-agent.sh --kill <id>   Kill an agent"
        echo "       spawn-agent.sh --kill-all    Kill all agents"
        echo "       spawn-agent.sh --dispatch <id>  Preview runtime dispatch (no spawn)"
        ;;
    *)
        agent_id="$1"
        shift
        task=""
        resume_sid=""
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --task)   shift; task="${1:-}";;
                --resume) shift; resume_sid="${1:-}"
                          [[ -n "$resume_sid" ]] || { echo "--resume needs a session id" >&2; exit 2; };;
                *) echo "unknown option: $1" >&2; exit 2;;
            esac
            shift
        done
        spawn_agent "$agent_id" "$task" "$resume_sid"
        ;;
esac
fi
