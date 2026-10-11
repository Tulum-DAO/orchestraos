# REAL Codex CLI 0.153.4 pane captures

Captured 2026-10-10 with `tmux capture-pane -p` (`.txt`) and `-e -p` (`.ansi`) from an isolated gate container
built from this repo with only `@openai/codex@0.153.4` installed (no Claude), signed in for these captures only with
a throwaway device login (the container is isolated, holds no host credentials, and is deleted after the captures). Pane width 80 columns. One line naming the plan's usage
allowance is removed; nothing else is edited.

| File | Screen |
|---|---|
| `trust_prompt_0.153.4.pane.txt` | First start in an untrusted directory, even with `--yolo`: "Do you trust the contents of this directory?" (`› 1. Yes, continue` / `2. No, quit`). |
| `idle_0.153.4.pane.*` | Idle seat after one answer. Composer `› Ask Codex to do anything` (dim placeholder), footer `gpt-5.6-terra default · ~/orchestraos`. The scrollback holds an earlier `› You are ...` user line. |
| `busy_0.153.4.pane.*` | Mid-turn: `◦ Working (11s • esc to interrupt)`. The composer placeholder is still drawn and the scrollback holds a `› Run the shell command ...` user line, so `›` on screen does not mean idle. |
| `typed_0.153.4.pane.*` | A person typed `half-typed note from the operator, do not send` into the composer and did not press Enter. |
| `stuck_marker_0.153.4.pane.*` | A router `[MSG from gm ...]` line left in the composer without Enter. At 80 columns it wraps: `/tmp/agent-msg-` ends line 1 and `msg_fixture_0001.md` starts line 2. Ctrl-U cleared it (observed). |
| `update_prompt_0.153.4.pane.txt` | Startup "Update available! 0.153.4 -> 0.162.1" menu; `› 1. Update now` is the Enter default. Seats launch with `-c check_for_update_on_startup=false` (docs/INSTALL.md "Codex seats and updates"). |
| `starting_mcp_0.153.4.pane.txt` | About 1 s after start with an MCP server configured: `• Starting MCP servers (1/2): codex_apps (0s • esc to interrupt)`. Boot waits this out. |
