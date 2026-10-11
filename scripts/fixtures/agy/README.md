# agy (Antigravity CLI) pane captures

| File | Screen |
|---|---|
| `idle_1.3.3.pane.*` | Idle agy 1.3.3 seat, captured with `tmux capture-pane -p` / `-e -p`, 2026-10-10. The composer box, the `>` prompt line and the `? for shortcuts … Gemini 3.7 Flash · low` footer are byte-for-byte as captured. The four scrollback lines above the box were replaced with neutral text because the original described private work. |

The files below are REAL captures from agy 1.3.3 in an isolated gate container built from this repo (no Claude, no
host credentials), signed in for these captures only with a throwaway login, then logged out and deleted. Pane
80x24, captured with `tmux capture-pane -p` (`.txt`) and `-e -p` (`.ansi`), 2026-10-10. The only edit: the signed-in
account's address in the header is replaced with `user@example.com`.

| File | Screen |
|---|---|
| `idle_after_turn_1.3.3.pane.*` | Idle after one answer: empty `>` composer, footer `? for shortcuts … Gemini 3.8 Flash · high`. The scrollback holds a `> Run the shell command …` user line. |
| `busy_generating_1.3.3.pane.*` | Mid-turn ("Generating..."). The scrollback holds the `> Run the shell command …` user line and the empty `>` composer is still drawn; the footer is `esc to cancel … Gemini 3.8 Flash · high` (no `? for shortcuts`). So `>` on screen does not mean idle. |
| `busy_running_task_1.3.3.pane.*` | Mid-turn while a shell command runs; an extra task row sits between the composer and the footer (`… · 1 task(s) · /tasks`). |
| `permission_prompt_1.3.3.pane.*` | agy's own command-permission prompt (`> 1. Yes, run command` … `4. No, cancel`). |
| `typed_1.3.3.pane.*` | A person typed `half-typed note from the operator, do not send` and did not press Enter. Typing hides `? for shortcuts`. |
| `stuck_marker_1.3.3.pane.*` | A router `[MSG from gm ...]` line left in the composer without Enter. At 80 columns agy wraps at a space, so `/tmp/agent-msg-msg_fixture_0001.md` starts line 2 whole. Ctrl-U cleared it (observed, as it cleared the typed line). |
