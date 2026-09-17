"""B2 POSITIVE case — the menu-bridge must RECOGNIZE a real 2.1.260 agent-decision
menu (casualty #3: real menus go unrecognized, so decision cards never reach the operator's
watch). Companion to the not-a-menu fence in test_menu_bridge_2_1_260_chrome.py.

RED-first: this consumes a REAL rendered capture, not a synthetic menu. gm is
delivering scripts/fixtures/chrome-2.1.260/decision_menu.pane.txt (<msg-id>).
Until that fixture exists these tests SKIP — the scaffold is committed so the RED
fixture slots straight in and the assertions run the moment the capture lands.

Design + gap hypotheses: .workspace/proposals/cli-pin-B2-menu-detection-design.md
Scope: parse_pending_menu path only (mine per gm + telemetry-wiring-dev <msg-id>).
"""
import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("agent_status", os.path.join(_HERE, "agent-status.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)

_FIX = os.path.join(_HERE, "fixtures", "chrome-2.1.260")
_MENU = os.path.join(_FIX, "decision_menu.pane.txt")
_have_menu = os.path.exists(_MENU)
_skip = pytest.mark.skipif(not _have_menu,
                           reason="awaiting gm's real 2.1.260 decision-menu capture "
                                  "(decision_menu.pane.txt) — RED-first, no synthetic menu")


def _load(name):
    raw = open(os.path.join(_FIX, name)).read()
    lines = raw.split("\n")
    stripped = [l.rstrip() for l in lines]
    return lines, stripped


@_skip
def test_real_2_1_260_menu_is_recognized():
    """The real rendered menu must parse to a well-formed pending_menu dict."""
    lines, stripped = _load("decision_menu.pane.txt")
    # a real menu replaces the composer box
    res = A.parse_pending_menu(stripped, has_composer_box=False, raw_lines=lines)
    assert res is not None, "real 2.1.260 decision menu was NOT recognized (casualty #3)"


@_skip
def test_real_menu_has_question_and_options():
    """Card must be well-formed: non-empty question/summary + populated options
    (the permission-filter ingress rejects a summary-None capture)."""
    lines, stripped = _load("decision_menu.pane.txt")
    res = A.parse_pending_menu(stripped, has_composer_box=False, raw_lines=lines)
    assert res is not None
    q = res.get("question") or res.get("summary") or ""
    assert q.strip(), "question/summary is empty — labeled-rule title-loss hypothesis (see design doc)"
    opts = res.get("options") or res.get("opts") or []
    assert len(opts) >= 2, f"expected the menu's numbered options, got {opts!r}"


# --- LONG-TITLE variant (real capture, gm-delivered) --------------------------
# 2.1.260 renders a long question inside a bordered box: a short ☐-title SUMMARY
# (" ☐ API restart") plus the FULL question on a line PREFIXED with a "│"
# box-border ("│ Should we restart …?"). The box-border must NOT leak into the
# card summary the operator sees. Real capture -> RED before the _menu_question strip fix.
_LONG = os.path.join(_FIX, "decision_menu_longtitle.pane.txt")
_skip_long = pytest.mark.skipif(not os.path.exists(_LONG),
                                reason="awaiting gm's long-title variant capture")


@_skip_long
def test_long_title_menu_recognized_with_all_options():
    lines, stripped = _load("decision_menu_longtitle.pane.txt")
    res = A.parse_pending_menu(stripped, has_composer_box=False, raw_lines=lines)
    assert res is not None, "long-title menu not recognized"
    opts = res.get("options") or res.get("opts") or []
    assert len(opts) >= 2, f"expected the numbered options, got {opts!r}"


@_skip_long
def test_long_title_question_is_clean_no_box_border():
    """The full question must reach the card WITHOUT the '│' box-border prefix."""
    lines, stripped = _load("decision_menu_longtitle.pane.txt")
    res = A.parse_pending_menu(stripped, has_composer_box=False, raw_lines=lines)
    assert res is not None
    q = res.get("question") or res.get("summary") or ""
    assert "Should we restart the :8888 API server" in q, \
        f"real question text missing from summary: {q!r}"
    assert "│" not in q, f"box-border '│' leaked into the card summary: {q!r}"
    assert not q.lstrip().startswith(("│", "|")), f"summary starts with a border char: {q!r}"


# --- STRUCTURAL-INVARIANT guard (gm ruling (a), <msg-id>) -----------------
# NO real 2.1.260 reproducer: every real capture emits the question line (plain or
# │-bordered) bounded by a blank, so the walk-up never crosses the widget frame.
# This test SYNTHESIZES the future risk shape by removing the question line from
# the REAL long-title capture, to guard the structural invariant: _menu_question
# must NEVER walk PAST the widget's top rule into SCROLLBACK PROSE (which would leak
# a misleading question into a the operator decision card). Labeled synthetic-invariant, not
# a claimed real capture — per gm's no-synthetic exception for structural guards.
@_skip_long
def test_walkup_never_crosses_widget_frame_into_scrollback():
    lines, _ = _load("decision_menu_longtitle.pane.txt")
    # Drop the "│ Should we…?" question line (line 18, 1-indexed) -> no plain
    # question between the top rule and the options (the hypothesized future shape).
    synth = [l for l in lines if not l.lstrip().startswith("│ Should we restart")]
    stripped = [l.rstrip() for l in synth]
    res = A.parse_pending_menu(stripped, has_composer_box=False, raw_lines=synth)
    # It may or may not recognize a menu, but if it does, the question must NOT be
    # scrollback prose from ABOVE the widget frame.
    q = (res.get("question") or res.get("summary") or "") if res else ""
    assert "Use your AskUserQuestion tool" not in q, \
        f"walk-up crossed the widget frame and leaked scrollback prose: {q!r}"
