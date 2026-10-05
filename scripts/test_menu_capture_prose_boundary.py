"""A numbered line in the agent's PROSE is not a menu option, and duplicate `n`s prove it.

Live incident 2026-10-05 (quest-orchestra report, card apr_1336cef7_29624028): a pending
menu card carried ONE options list of nine with ns 1,2,3,4,1,2,3,4,5 -- and claimed
`part_count: 1`, `multipart: None`, `walk_complete: True`. So it asserted "fully captured,
single part" while holding two option groups, and nothing downstream could tell it was
broken. Every surface rendered duplicate numbers, so a tap sent a digit that meant a
different option than the one shown. Shaw hit it in the Quest.

Root cause, from the live pane: the real menu was ONE part (a single tab chip, options
1-5). The 1-4 were numbered lines from the agent's own prose ABOVE the menu block. The
block-walk in `parse_pending_menu` joins two consecutive option matches unless the gap
exceeds `_MENU_OPT_MAX_GAP` (8) or a FOOTER hint sits between them -- and here the gap was
~8 and the intervening lines were a blank, a prose sentence, a rule and a TAB CHIP, none of
which is a footer hint. A non-blank, non-rule, non-footer line between option groups was
silently tolerated.

Two guards, deliberately both:
  (A) a TAB-CHIP line between option matches ENDS the block -- the structural fix for this
      shape. Safe to detect because a multi-select option's checkbox is BRACKET form
      (`[ ]` / `[x]`) and sits AFTER the `N.` marker, so a bare glyph line with no marker
      cannot be an option.
  (B) non-unique `n`s mark the capture ambiguous regardless of cause -- the general net.
      Duplicate ns can never be a legitimate single menu, so this catches the next variant
      we have not seen rather than only the one we found.
"""
import importlib.util
import sys

_spec = importlib.util.spec_from_file_location("agent_status_mod", "scripts/agent-status.py")
A = importlib.util.module_from_spec(_spec)
sys.modules["agent_status_mod"] = A
try:
    _spec.loader.exec_module(A)
except SystemExit:
    pass


# Mirrors the live pane's STRUCTURE (synthetic text, no operator content):
# prose numbered list, a rule, a tab chip, then the real single-part menu.
PANE = """\
  Here is what I propose, in order:

  1. First proposed thing in the prose
  2. Second proposed thing in the prose
  3. Third proposed thing in the prose
  4. Fourth proposed thing in the prose

  One decision is yours, because it mutates live state:
────────────────────────────────────────────────────────────
 ☐ the decision

│ Which way do you want this done?

❯ 1. Do it now, then fix
     Detail subline for the first real option.
  2. Land the fix first
     Detail subline for the second real option.
  3. Do it now and stop there
  4. Type something.
────────────────────────────────────────────────────────────
  5. Chat about this

Enter to select · ↑/↓ to navigate · Esc to cancel
"""


def _parse():
    return A.parse_pending_menu(PANE.splitlines())


def test_the_prose_numbered_list_is_not_absorbed_into_the_menu():
    m = _parse()
    assert isinstance(m, dict), "the real menu was not parsed at all"
    ns = [o["n"] for o in (m.get("options") or [])]
    assert ns == ["1", "2", "3", "4", "5"], (
        f"prose lines were absorbed as options: ns={ns}")
    labels = " ".join(o["label"] for o in m["options"]).lower()
    assert "prose" not in labels, f"a prose line became an option: {labels[:200]}"


def test_option_numbers_are_unique():
    """The property that made the live card dangerous. A tap on a duplicate `n` means a
    different option than the one rendered."""
    m = _parse()
    ns = [o["n"] for o in (m.get("options") or [])]
    assert len(ns) == len(set(ns)), f"duplicate option numbers: {ns}"


def test_a_capture_with_duplicate_ns_is_marked_ambiguous():
    """Guard (B), tested on a pane the block-walk still mis-joins. Whatever the cause, a
    non-unique `n` set must be STAMPED so every surface refuses a tap, rather than each
    client needing its own fix."""
    bad = """\
  1. alpha
  2. beta
  3. gamma
  1. delta
  2. epsilon

Enter to select · ↑/↓ to navigate · Esc to cancel
"""
    m = A.parse_pending_menu(bad.splitlines())
    if not isinstance(m, dict):
        return                      # refusing to parse it at all is also acceptable
    ns = [o["n"] for o in (m.get("options") or [])]
    if len(ns) != len(set(ns)):
        assert m.get("ambiguous_options") is True, (
            f"duplicate ns {ns} were served without the ambiguous_options stamp")


# --- CONTROLS -------------------------------------------------------------
# Without these, (A) would pass by breaking the block at every option, and (B) would pass
# by stamping everything ambiguous.

def test_control_an_ordinary_single_part_menu_still_parses_whole():
    pane = """\
│ Pick one:

❯ 1. First
     Detail for first.
  2. Second
     Detail for second.
  3. Type something.

Enter to select · ↑/↓ to navigate · Esc to cancel
"""
    m = A.parse_pending_menu(pane.splitlines())
    assert isinstance(m, dict), "an ordinary menu stopped parsing"
    assert [o["n"] for o in m["options"]] == ["1", "2", "3"]
    assert not m.get("ambiguous_options"), "an ordinary menu was marked ambiguous"


def test_control_a_multi_select_menu_with_bracket_checkboxes_is_untouched():
    """Option checkboxes are BRACKET form after the `N.` marker. Guard (A) keys on a bare
    glyph line with NO marker, so it must not split these."""
    pane = """\
│ Choose any:

❯ 1. [ ] Alpha
  2. [✔] Beta
  3. [ ] Gamma

Enter to select · ↑/↓ to navigate · Esc to cancel
"""
    m = A.parse_pending_menu(pane.splitlines())
    assert isinstance(m, dict), "a multi-select menu stopped parsing"
    assert [o["n"] for o in m["options"]] == ["1", "2", "3"]
    assert [o.get("checked") for o in m["options"]] == [False, True, False]
    assert not m.get("ambiguous_options")


# --- server-side refusal: the stamp must actually STOP an answer ----------
# The stamp alone protects nobody. These pin that the gateway validators refuse an
# ambiguous row, which is what protects the phone and watch with no client change.

import scripts.watch_gateway as G


def _row(menu):
    import json
    return {"id": "apr_test", "kind": "menu", "menu": json.dumps(menu)}


def test_an_ambiguous_row_refuses_a_single_option_answer():
    err = G._validate_option_answer(
        _row({"options": [{"n": "1", "label": "a"}], "ambiguous_options": True}),
        "1", None)
    assert err and "ambiguous" in err.lower(), err


def test_an_ambiguous_row_refuses_a_batch_answer():
    err = G._validate_batch_answer(
        _row({"walk_complete": True, "part_count": 1, "ambiguous_options": True,
              "parts": [{"index": 0, "options": [{"n": "1", "label": "a"}]}]}),
        [{"part": 0, "ns": ["1"]}])
    assert err == "ambiguous_options", err


def test_control_a_sound_row_still_answers():
    """Without this, the two tests above would pass if the validators refused everything."""
    err = G._validate_option_answer(
        _row({"options": [{"n": "1", "label": "a"}]}), "1", None)
    assert err is None, err
