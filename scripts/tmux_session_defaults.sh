# tmux_session_defaults.sh — options every seat's tmux session gets. Sourced by spawn-agent.sh
# and scripts/agent-recovery.sh right after `tmux new-session`.
#
# mouse on (operator finding, 2026-10-08, live on a fresh VPS): with tmux's mouse mode off, the
# terminal turns the scroll wheel into arrow keys. In Claude Code, Up walks the PROMPT HISTORY,
# and that history is shared by every seat started in the same directory. So one scroll and
# then Enter re-sent ANOTHER seat's init prompt into the pane the operator was reading. With
# mouse on, the wheel scrolls tmux's own scrollback instead.
#
# Set per SESSION, never globally, so an operator's own ~/.tmux.conf and their other tmux
# sessions are left alone. Selecting text inside a seat then needs Shift-drag (Linux, Windows
# Terminal) or Option-drag (macOS Terminal and iTerm2); docs/INSTALL.md §3 says so.
#
# Fail-soft: an option that cannot be set must never block a spawn or a recovery.
orch_tmux_session_defaults() {
    local session="$1"
    [[ -n "$session" ]] || return 0
    # "=NAME:" is an EXACT session match. tmux 3.4 refuses a bare "=NAME" for set-option
    # ("no such session"), which the fail-soft below would have hidden: the option would just
    # never be set. A plain "NAME" would prefix-match a neighbour (seat-x -> seat-xy).
    tmux set-option -t "=$session:" mouse on >/dev/null 2>&1 || true
    return 0
}
