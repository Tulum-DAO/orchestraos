#!/usr/bin/env python3
"""#1b harness: pane_split + stuck_own_message runtime-signature-aware.
Proves: (1) claude unchanged; (2) gemini '>' input line picked correctly even
when ❯ appears in gemini PROSE above (the mis-delivery the cross-model verify
found); (3) stuck-own-message detection keys on the right prompt_char.
Monkeypatches tmux() with synthetic pane captures. Live router untouched."""
import importlib.util, os, sys
from types import SimpleNamespace

ORCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("mrouter1b", os.path.join(ORCH, "scripts", "message-router.py"))
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)

fails = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond: fails.append(name)

# --- synthetic panes ---
# Claude pane: scrollback + a real ❯ input line at the bottom
claude_pane = "\n".join([
    "● Did some work",
    "  ⎿ result line",
    "✻ Worked for 3s",
    "❯ ",                      # the real input line (empty)
])
# Gemini pane WHERE ❯ APPEARS IN PROSE (the mis-delivery trap) + real '>' input
gemini_pane_prose_trap = "\n".join([
    "● The router should split on ❯ for claude — but here that ❯ is PROSE",
    "  so a ❯-split would wrongly pick THIS line as the input box.",
    "  Some more gemini output mentioning ❯ again in a sentence.",
    "> ",                      # the REAL gemini input line (empty)
    "? for shortcuts                              Gemini 3.7 Flash · high",
])
# Gemini pane with OUR stuck [MSG] line typed at the '>' prompt
gemini_stuck = "\n".join([
    "● prior gemini work",
    "> [MSG from gm | high] see /tmp/agent-msg-msg_abc123.md",
])
# Claude pane with our stuck [MSG] at ❯
claude_stuck = "\n".join([
    "● prior claude work",
    "❯ [MSG from gm | high] see /tmp/agent-msg-msg_def456.md",
])

def set_pane(plain, ansi=None):
    ansi = ansi if ansi is not None else plain
    def _tmux(*args):
        is_ansi = "-e" in args
        return SimpleNamespace(returncode=0, stdout=(ansi if is_ansi else plain))
    mr.tmux = _tmux

# --- 1. pane_split picks the right input line per runtime ---
set_pane(claude_pane)
sb, il, ok = mr.pane_split("x", "claude")
check("claude pane_split -> ok, input line is the ❯ line", ok and il.startswith("❯"))

set_pane(gemini_pane_prose_trap)
sb_c, il_c, ok_c = mr.pane_split("x", "claude")   # WRONG runtime on gemini pane
sb_g, il_g, ok_g = mr.pane_split("x", "gemini")   # correct runtime
# claude-split on the gemini-prose pane picks a PROSE ❯ line (the bug)
check("gemini pane split as CLAUDE picks a prose ❯ line (demonstrates the trap)",
      ok_c and "PROSE" in il_c or "sentence" in il_c)
# gemini-split picks the real '>' input line, NOT a prose ❯ line
check("gemini pane split as GEMINI picks the real '>' input line",
      ok_g and il_g.strip().startswith(">") and "❯" not in il_g)

# --- 2. stuck_own_message keys on the right prompt_char ---
# gemini stuck line: ansi must mark it 'typed' (non-dim). Build a typed ansi.
gemini_stuck_ansi = gemini_stuck.replace("> [MSG", "> \x1b[0m[MSG")  # default color = typed
set_pane(gemini_stuck, gemini_stuck_ansi)
stuck_g = mr.stuck_own_message("x", "gemini")
check("gemini stuck [MSG] at '>' detected (runtime=gemini)", bool(stuck_g) and "[MSG from" in (stuck_g or ""))
# same pane read as CLAUDE (wrong runtime) -> no ❯ present -> not detected
stuck_g_as_claude = mr.stuck_own_message("x", "claude")
check("gemini stuck pane read as CLAUDE -> not detected (no ❯) = keying matters", stuck_g_as_claude is None)

claude_stuck_ansi = claude_stuck.replace("❯ [MSG", "❯ \x1b[0m[MSG")
set_pane(claude_stuck, claude_stuck_ansi)
stuck_c = mr.stuck_own_message("x")   # default runtime=claude
check("claude stuck [MSG] at ❯ detected (default runtime, unchanged)", bool(stuck_c) and "[MSG from" in (stuck_c or ""))

# --- 3. default-runtime pane_split == explicit claude (byte-identical) ---
set_pane(claude_pane)
a = mr.pane_split("x")
b = mr.pane_split("x", "claude")
check("pane_split default == explicit claude (byte-identical)", a == b)

if __name__ == "__main__":
    print("\n#1b HARNESS VERDICT:", "ALL PASS" if not fails else f"FAILS: {fails}")
    sys.exit(1 if fails else 0)
