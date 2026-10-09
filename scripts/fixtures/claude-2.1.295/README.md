# REAL Claude Code 2.1.295 pane captures

Captured 2026-10-09 with `tmux capture-pane -p` from a throwaway, isolated container
(`docker build --build-arg AGENT_CLIS="@anthropic-ai/claude-code@2.1.295"`) signed in for this
capture only and signed out afterwards. Width 160 columns. The banner's plan name is removed;
nothing else is edited.

| File | Screen |
|---|---|
| `perm_bash_dashed.pane.txt` | Bash permission prompt in manual mode. Since 2.1.286 the command sits **between `╌` dashed lines** inside the `─` frame, under a `Tip:` line and the description. |
| `perm_bash_dashed_parallel.pane.txt` | The first of two Bash calls made in parallel in one turn (the second waits; no "N of M" is drawn for this case). |
| `askuserquestion.pane.txt` | An AskUserQuestion menu (one question, three options with descriptions). |
| `idle_manual_mode.pane.txt` | The idle screen in manual mode: footer `⏸ manual mode on · ? for shortcuts · ← for agents`. |
