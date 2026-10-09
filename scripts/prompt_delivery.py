"""Did a prompt we pasted into a pane actually get SUBMITTED, or is it still sitting in the composer?

spawn-agent.sh's inject_prompt used to count a prompt as delivered when its text was anywhere on
screen. Text that was pasted but never submitted is on screen too (in the composer), so a lost Enter
passed the check: a new user's first gm sat with its init prompt typed in the CLI and not sent, and
the spawn reported success (operator report, 2026-10-09).

    verdict(raw, probe, runtime) -> "sent" | "pending" | "absent" | "foreign" | "unknown"

  sent     the probe is on a line ABOVE the composer (the transcript), or the runtime is mid-turn
           with the probe on screen. Delivered.
  pending  the composer holds our text: pasted, not submitted. The caller presses Enter again.
           It must NOT paste again (that would stack a second copy in the box).
  absent   our text is nowhere: the paste was dropped. The caller may paste again.
  foreign  the composer holds text that is not ours. Never Enter it.
  unknown  the screen cannot be read for this runtime. Not proof of anything.

The composer read is scripts/composer_state.py (the one reader; SGR-aware, so a dim ghost
suggestion is never taken for our text). Needs a capture WITH SGR (`capture-pane -e`).

CLI: tmux capture-pane -e -p -t <target> | python3 scripts/prompt_delivery.py <probe> [--runtime R]
prints the verdict; exit 0 always (the verdict is the answer). The caller captures, so the pane is
read through the same tmux (server, socket) that the prompt was pasted with.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.composer_state import _strip_sgr, classify_screen  # noqa: E402

# Long prompts wrap, so only the head of the probe is compared: it is what lands on the composer's
# first line, and on the first transcript line once it is sent.
HEAD_CHARS = 24


def _norm(s: str) -> str:
    return " ".join(_strip_sgr(s).replace("\xa0", " ").split())


def verdict(raw: str, probe: str, runtime: str = "claude") -> str:
    head = _norm(probe)[:HEAD_CHARS]
    if not raw or not head:
        return "unknown"
    r = classify_screen(raw, runtime=runtime)
    lines = raw.split("\n")
    composer = (r.get("evidence") or {}).get("line")
    above = lines
    if composer is not None:
        for i in range(len(lines) - 1, -1, -1):
            if lines[i] == composer:
                above = lines[:i]
                break
    seen_above = any(head in _norm(line) for line in above)
    state = r["state"]
    if state == "typed":
        typed = _norm(r["text"])
        # the composer can show only the start of a long paste: a prefix of ours (8+ chars) is ours
        if head in typed or (len(typed) >= 8 and head.startswith(typed)):
            return "pending"
        return "foreign"
    if state == "submitted":
        return "sent" if seen_above or any(head in _norm(line) for line in lines) else "unknown"
    if state in ("empty", "ghost"):
        return "sent" if seen_above else "absent"
    return "unknown"


def main(argv: list[str]) -> int:
    args = list(argv)
    runtime = "claude"
    if "--runtime" in args:
        i = args.index("--runtime")
        runtime = (args[i + 1] if i + 1 < len(args) else "") or "claude"
        del args[i:i + 2]
    if len(args) != 1:
        print("usage: tmux capture-pane -e -p -t T | prompt_delivery.py <probe> [--runtime R]", file=sys.stderr)
        print("unknown")
        return 0
    print(verdict(sys.stdin.read(), args[0], runtime))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
