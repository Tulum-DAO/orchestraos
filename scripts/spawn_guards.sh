#!/usr/bin/env bash
# spawn_guards.sh — FATAL pre-/post-launch guards, sourced by spawn-agent.sh (issue #92,
# fresh-install DX report 2026-09-20). Unlike spawn_model_verify.sh (best-effort, never
# fatal), these are meant to stop a spawn: a seat that would die on launch, or a seat that
# launched but never received its instructions, must not be reported as a success.
#
# Contract: each function prints its reason to stderr via the caller's `err` and RETURNS a
# non-zero code; the CALLER decides to exit (so the functions stay testable under set -e).
# Requires: $SCRIPT_DIR (repo root) and `err` / `warn` (defined by spawn-agent.sh).

# refuse_model_mismatch <runtime> <model> <agent_id>
# Returns 3 when the model id positively names ANOTHER runtime (claude-sonnet-5 on a codex
# seat: the CLI exits and the init prompt lands in bare bash). Empty / unknown-family model
# is not a mismatch (returns 0). Delegates to runtime_signatures.validate_model_for_runtime
# so the spawn path and the registration invariant share one rule.
refuse_model_mismatch() {
    local runtime="$1" model="$2" agent_id="${3:-}"
    [[ -z "$model" ]] && return 0
    local why
    if ! why=$(python3 - "$SCRIPT_DIR/scripts" "$runtime" "$model" "$agent_id" 2>&1 >/dev/null <<'PYEOF'
import sys
scripts_dir, runtime, model, agent_id = sys.argv[1:5]
sys.path.insert(0, scripts_dir)
import runtime_signatures as rs
try:
    rs.validate_model_for_runtime(runtime, model, agent_id=agent_id or None)
except rs.RuntimeResolutionError as e:
    sys.stderr.write(str(e) + "\n"); sys.exit(3)
PYEOF
    ); then
        err "$agent_id: spawn REFUSED — $why"
        return 3
    fi
    return 0
}

# inject_prompt <session> <text>
# Race-safe prompt injection, verified SUBMITTED. send-keys text + Enter with no delay loses the
# race against Ink's async input buffer, so the text goes in as one paste-buffer and Enter follows
# after a pause. Then scripts/prompt_delivery.py reads the composer: "sent" is success; "pending"
# (our text still in the box, the Enter was lost) gets Enter again, never a second paste; "absent"
# (the paste was dropped) pastes once more. Visible-on-screen is NOT delivered: text sitting
# unsent in the composer is visible too, which is how a new user's first gm sat with its init
# prompt typed and not sent while the spawn said success (operator report, 2026-10-09).
# Reads $runtime from the caller (default claude). INJECT_POLL_S / INJECT_POLLS: test knobs.
inject_prompt() {
    local session="$1" text="$2"
    local probe attempt poll v="unknown" rt="${runtime:-claude}"
    local pause="${INJECT_POLL_S:-1}" polls="${INJECT_POLLS:-6}"
    probe=$(printf '%s' "$text" | head -c 60)
    for attempt in 1 2; do
        tmux set-buffer -b spawn-inject "$text"
        tmux paste-buffer -b spawn-inject -t "$session" -d
        sleep 0.7
        tmux send-keys -t "$session" Enter
        for ((poll = 1; poll <= polls; poll++)); do
            sleep "$pause"
            v=$(tmux capture-pane -e -p -S -40 -t "$session" 2>/dev/null \
                | python3 "$SCRIPT_DIR/scripts/prompt_delivery.py" "$probe" --runtime "$rt" 2>/dev/null) || v="unknown"
            [[ -n "$v" ]] || v="unknown"
            case "$v" in
                sent) return 0 ;;
                pending)
                    warn "Prompt pasted but not submitted in '$session' — pressing Enter again"
                    tmux send-keys -t "$session" Enter ;;
                absent) break ;;
                *) ;;
            esac
        done
        # still in the box (or someone else's text is): a second paste would stack another copy
        [[ "$v" == "absent" ]] || break
        warn "Injection attempt $attempt not visible in '$session' — retrying"
        sleep 2
    done
    case "$v" in
        pending) err "Injection NOT SUBMITTED in '$session': the prompt is still in the composer after $polls Enter attempts" ;;
        foreign) err "Injection NOT SUBMITTED in '$session': the composer holds text that is not ours, so nothing was pressed" ;;
        absent)  err "Injection FAILED twice for '$session' — the prompt never appeared" ;;
        *)       err "Injection UNVERIFIED in '$session': the composer could not be read for runtime '$rt'" ;;
    esac
    return 1
}

# inject_or_fail <session> <text>
# Wraps inject_prompt: on failure says so LOUDLY and returns 1 so the caller exits non-zero
# instead of printing "spawned successfully" under an "Injection FAILED" line. The pane is
# left running for inspection (the operator can attach and type the task by hand).
inject_or_fail() {
    local session="$1" text="$2"
    if inject_prompt "$session" "$text"; then
        return 0
    fi
    err "$session: NOT spawned successfully — the seat launched but its instructions could not be injected; pane left running for inspection (tmux attach -t $session), exit 1"
    return 1
}

# clear_dead_session <tmux_name>
# Returns 0 after KILLING the session when it is safe to relaunch into its place: the session
# exists, no agent CLI is anywhere in its pane's process tree, and the screen shows only the
# shell the CLI exited to (or a nested tmux client over that shell). Returns 1 otherwise, and
# the caller keeps today's "already running" path. Operator finding #10 (2026-10-08): the
# has-session check alone said "already running" for a seat whose CLI was gone, so neither
# `orchestra starter` nor the dashboard's Resume could ever bring it back. A live CLI, a
# nested client the CLI itself launched, or anything else on screen (an editor, a build the
# operator is running) is never touched. The rule lives in scripts/pane_cli.py.
clear_dead_session() {
    local name="$1"
    python3 "$SCRIPT_DIR/scripts/pane_cli.py" --relaunchable "$name" >/dev/null 2>&1 || return 1
    echo "[spawn] session '$name' exists but its agent CLI has exited; relaunching it" >&2
    tmux kill-session -t "=$name" 2>/dev/null || return 1
    return 0
}
