#!/usr/bin/env bash
# spawn_model_verify.sh — post-spawn model verification helpers, sourced by spawn-agent.sh.
#
# B1 outsider run 2, finding A: spawn-agent.sh runs under `set -euo pipefail`, and
# verify_spawn_model read the model with `line=$(tmux capture-pane ... | grep ... | tail -1)`.
# When the banner carries no `claude-<family>` token (Claude Code >= 2.1.26x banners as
# "Fable 5.1 with medium effort · Claude Max"), grep exits 1, pipefail makes the pipeline
# fail, and the unguarded assignment aborts the WHOLE spawn silently — after the seat was
# launched and before --task was injected (exit 1, seat alive, uninstructed). The private
# fleet never hit it because every spawn there sets AGENT_MODEL.
#
# Contract: every function here exits 0 whatever the pane says. Verification is
# best-effort and NEVER fatal; the verdict is always printed.

# Vocabulary: model ids (claude-<family>-...) and the banner's display labels from
# config/providers.json's claude catalog (Fable 5.1, Opus 4.8, Sonnet 5, Haiku 4.5, ...).
SPAWN_MODEL_ID_RE='claude-(fable|opus|sonnet|haiku)[A-Za-z0-9._-]*(\[1m\])?'
SPAWN_MODEL_LABEL_RE='(Fable|Opus|Sonnet|Haiku) [0-9]+(\.[0-9]+)?'

# read_model_line TEXT -> the first line naming a model (id or label), or '' — exit 0 always.
read_model_line() {
    printf '%s\n' "${1:-}" | grep -m1 -E "$SPAWN_MODEL_ID_RE|$SPAWN_MODEL_LABEL_RE" || true
}

# classify_model_line LINE -> 1m | bare | family | none
#   1m     : an explicit [1m] token is visible (confirmed).
#   bare   : an explicit model id without [1m] (the leak the old check corrected).
#   family : only a display label is visible (Fable 5.1 ...); [1m] cannot be confirmed
#            from this banner — never "correct" it, it may already be right.
#   none   : nothing model-like visible.
classify_model_line() {
    local line="${1:-}"
    [[ -z "$line" ]] && { printf 'none'; return 0; }
    [[ "$line" == *'[1m]'* ]] && { printf '1m'; return 0; }
    if printf '%s' "$line" | grep -qE "$SPAWN_MODEL_ID_RE"; then printf 'bare'; return 0; fi
    if printf '%s' "$line" | grep -qE "$SPAWN_MODEL_LABEL_RE"; then printf 'family'; return 0; fi
    printf 'none'
    return 0
}
