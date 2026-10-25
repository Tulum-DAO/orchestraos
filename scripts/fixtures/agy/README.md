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
| `stuck_marker_1.3.3.pane.*` | A router `[MSG from gm ...]` line left in the composer without Enter (typed SHORT for the capture: no `--body` trailer, so the end anchor treats it as not-exactly-one-injection). At 80 columns agy wraps at a space, so `/tmp/agent-msg-msg_fixture_0001.md` starts line 2 whole. Ctrl-U cleared it (observed, as it cleared the typed line). |

| `stuck_full_injection_1.3.3.pane.*` | SYNTHETIC (built from the real idle_after_turn chrome): one complete router injection, wrapped at 80 columns, and nothing else in the composer. Only the composer lines differ from the real capture. |
| `stuck_plus_appended_1.3.3.pane.*` | SYNTHETIC: the same injection with a person's words typed after it on the same line. |
| `stuck_plus_second_line_1.3.3.pane.*` | SYNTHETIC: the same injection with a person's second line under it (agy's composer is multi-line). |
| `stuck_plus_blank_and_draft_1.3.3.pane.*` | SYNTHETIC: one injection, a blank line, then a person's draft paragraph under it. |
| `stuck_two_injections_1.3.3.pane.*` | SYNTHETIC: two router injections run together in one composer. |
| `stuck_interior_edit_1.3.3.pane.*` | SYNTHETIC: one injection with a person's words inserted after its first sentence. |
| `stuck_plus_separator_and_draft_1.3.3.pane.*` | SYNTHETIC: one injection, then a person's `---` separator line and a draft paragraph under it (the separator is content, not the box border). |
| `stuck_subject_edited_1.3.3.pane.*` | SYNTHETIC: the injection with one letter added inside the subject span. |
| `stuck_words_before_read_1.3.3.pane.*` | SYNTHETIC: the injection with a person's words inserted before " — Read". |
| `stuck_plus_indented_border_and_draft_1.3.3.pane.*` | SYNTHETIC: the injection, then an indented line of 40 box-drawing characters typed by a person, then a draft (an indented run is content; agy's real border starts at column 0). |
| `stuck_bracket_in_subject_1.3.3.pane.*` | SYNTHETIC: a person typed `[hi gm]` inside the subject span. |
| `stuck_sent_token_in_subject_1.3.3.pane.*` | SYNTHETIC: a person typed `(sent 1m ago)` inside the subject span. |
| `stuck_edited_store_path_1.3.3.pane.*` | SYNTHETIC: the msg_store path in the reply hint was edited. |
