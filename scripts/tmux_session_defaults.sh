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
# The cost of mouse on, and its guard: a scroll-up puts the pane in tmux's scroll mode
# (copy-mode), which it leaves only when scrolled back to the bottom. While a pane is in that
# mode, `send-keys` text is swallowed with no error, and most of the injectors (approval
# resume, nudges, Arturo, the dashboard send) do not check for it. In this codebase a pane only
# ENTERS the mode from an attached client (a wheel scroll, prefix-[, or the web terminal, which is
# an attached client too). A script that runs `tmux copy-mode -t <seat>` would break that, so
# don't add one. So a client-detached hook leaves the mode: an unattended pane,
# which is exactly when injectors type into it, is never stuck in it. That covers ssh detach,
# a closed terminal window and a closed web-terminal tab. While someone is attached and
# scrolled up, they are looking at it; that case predates this file (prefix-[ and the web
# terminal's own wheel handling) and wants a shared injector guard.
#
# Fail-soft: an option that cannot be set must never block a spawn or a recovery.
orch_tmux_session_defaults() {
    local session="${1:-}"
    [[ -n "$session" ]] || return 0
    # "=NAME:" is an EXACT session match. tmux 3.4 refuses a bare "=NAME" for set-option
    # ("no such session"), which the fail-soft below would have hidden: the option would just
    # never be set. A plain "NAME" would prefix-match a neighbour (seat-x -> seat-xy).
    tmux set-option -t "=$session:" mouse on >/dev/null 2>&1 || true
    # `send-keys -X cancel` leaves scroll mode, and is a harmless "not in a mode" otherwise
    # (no keystroke reaches the app). The target is quoted for tmux's own command parser.
    # Only for names tmux's command parser cannot misread (seat names are not validated
    # upstream); mouse is still set above for any name.
    if [[ "$session" =~ ^[A-Za-z0-9_-]+$ ]]; then
        tmux set-hook -t "=$session:" client-detached "send-keys -t '=$session:' -X cancel" >/dev/null 2>&1 || true
        # A seat must never attach a tmux client inside its own screen (operator finding #10,
        # 2026-10-08): gm's pane ran a nested `tmux attach` (tmux refuses one while TMUX is set;
        # it gets through with TMUX unset, which is tmux's own error hint), its CLI went off
        # screen, and the dashboard showed a dead gm offering Spawn. A client whose tty is one
        # of this server's own panes can only be such a nested client, so it is detached at
        # once, and the attaching pane gets back whatever ran the attach. Clients from outside
        # (an ssh terminal, the web terminal's pty) have their own ttys and are left alone.
        # ##{pane_tty} survives the hook's format expansion as #{pane_tty} for list-panes.
        tmux set-hook -t "=$session:" client-attached \
            "run-shell \"tmux list-panes -a -F '##{pane_tty}' | grep -qxF '#{client_tty}' && tmux detach-client -t '#{client_tty}' || true\"" \
            >/dev/null 2>&1 || true
    fi
    return 0
}
