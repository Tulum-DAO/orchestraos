"""Where a seat's context meter is allowed to come from: its own footer, nowhere else.

A Claude Code status line (the `statusLine` command) draws a `█░` meter and a percent under the
composer box. Two readers take the seat's context % from the screen: agent-status.py
(`context_pct`, shown on the apps and read by the lineage daemon first) and the daemon's own
fallback (lineage_daemon/enrich.py). Both used to take the bottom-most `█`/`░` line ANYWHERE on
the screen. While a menu is open the status line is not drawn, so that scan fell through to the
conversation: a progress bar in tool output, or a meter an agent quoted. One seat read 99% that
way while it was at 39% (2026-10-09), and the operator stopped it.

The footer is what sits under the COMPOSER BOX: the screen's last frame rule, when the rule before
it opens a box whose first line is the `❯` (or `>`) composer. The status line is drawn directly
under that box. When a menu replaces the composer, the last rule belongs to the menu, and the lines
under it are menu content (a permission prompt's command, a question's option labels) that can hold
any text at all, so nothing is read and the caller reports '' (unknown) rather than a number that
belongs to something else.
"""
import re

# A frame rule: a run of box-drawing horizontals (Claude Code draws ─; some themes ━) from column 0,
# optionally with a label inset. Same shape agent-status.py uses for the composer frame.
_RULE_RE = re.compile(r'^[─━]{8,}( .+ [─━]+)?\s*$')
# A highlighted menu option: the cursor glyph, then the option number.
_MENU_OPTION_RE = re.compile(r'^[❯>]\s*\d{1,2}\.\s')


def footer_meter_line(stripped_lines) -> str:
    """The meter line (`█`/`░` plus a percent) in the footer under the composer box, or ''.

    `stripped_lines`: the screen, top to bottom, with ANSI removed."""
    lines = list(stripped_lines)
    # Frame rules start at column 0; output, quoted text and a typed message's own lines are
    # indented, so a rule-like line inside the composer or the conversation is not a frame edge.
    rules = [i for i, line in enumerate(lines) if _RULE_RE.match(line.rstrip())]
    if len(rules) < 2:
        return ''
    box_top, box_bottom = rules[-2], rules[-1]
    if not (box_top + 1 < box_bottom and _is_composer(lines[box_top + 1])):
        return ''
    for line in lines[box_bottom + 1:]:
        if ('█' in line or '░' in line) and '%' in line:
            return line
    return ''


def _is_composer(line: str) -> bool:
    t = line.strip()
    if _MENU_OPTION_RE.match(t):
        return False
    return t.startswith('❯') or (t.startswith('>') and not t.startswith('>>'))
