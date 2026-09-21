#!/usr/bin/env bash
# spawn_guards.sh — FATAL pre-/post-launch guards, sourced by spawn-agent.sh (issue #92,
# fresh-install DX report 2026-09-20). Unlike spawn_model_verify.sh (best-effort, never
# fatal), these are meant to stop a spawn: a seat that would die on launch, or a seat that
# launched but never received its instructions, must not be reported as a success.
#
# Contract: each function prints its reason to stderr via the caller's `err` and RETURNS a
# non-zero code; the CALLER decides to exit (so the functions stay testable under set -e).
# Requires: $SCRIPT_DIR (repo root), `err` (defined by spawn-agent.sh), and for
# inject_or_fail an `inject_prompt <session> <text>` function in scope.

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
