"""B2 not-a-menu regression fixtures — real Claude Code 2.1.260 chrome that must
NEVER be mis-bridged to the operator's approvals watch as a decision menu.

Scope (cli-chrome-pin-dev, per gm boundary ruling + telemetry-wiring-dev <msg-id>):
the menu/chrome DETECTION surface — agent-status.parse_pending_menu. This asserts the
detector REJECTS 2.1.260 chrome that structurally resembles a menu. The companion
POSITIVE case (a real 2.1.260 decision menu must be RECOGNIZED) lands when gm hands
over the real menu capture — no synthetic menu fixture is manufactured.

Fixtures are REAL read-only `tmux capture-pane -p` captures committed under
scripts/fixtures/chrome-2.1.260/ (see that dir's README).

Highest-value case : the session-quality FEEDBACK SURVEY
    ● How is Claude doing this session? (optional)
      1: Bad    2: Fine   3: Good   0: Dismiss
has numbered options that mimic a decision menu — the exact mis-bridge-to-the operator class
the permission-filter guard @7cead0f9a defends. It must classify as chrome, not a menu.
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("agent_status", os.path.join(_HERE, "agent-status.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)

_FIX = os.path.join(_HERE, "fixtures", "chrome-2.1.260")


def _load(name):
    raw = open(os.path.join(_FIX, name)).read()
    lines = raw.split("\n")
    stripped = [l.rstrip() for l in lines]
    return lines, stripped


def _has_composer(stripped):
    """Detect the composer box the way the live caller (parse_status) does."""
    chrome = A._find_chrome(stripped)
    return bool(chrome and chrome.get("composer_idx") is not None)


def test_feedback_survey_is_not_a_menu():
    """The 'How is Claude doing this session?' survey must NOT bridge as a decision
    menu. Real capture; asserts rejection with the live composer-guard state AND,
    defensively, even if the composer box were absent (double protection: the
    survey's '1: Bad' colon/multi-per-line row does not match _MENU_OPT_RE's 'N.'
    form)."""
    lines, stripped = _load("feedback_survey_quality.pane.txt")
    hcb = _has_composer(stripped)
    assert A.parse_pending_menu(stripped, has_composer_box=hcb, raw_lines=lines) is None
    # defensive: even without the composer guard, the colon-format survey is not a menu
    assert A.parse_pending_menu(stripped, has_composer_box=False, raw_lines=lines) is None


def test_idle_footer_is_not_a_menu():
    """An idle 2.1.260 footer (empty composer + status line + auto-update banner)
    must not read as a pending menu."""
    lines, stripped = _load("idle_footer.pane.txt")
    hcb = _has_composer(stripped)
    assert A.parse_pending_menu(stripped, has_composer_box=hcb, raw_lines=lines) is None


def test_completion_spinner_footer_is_not_a_menu():
    """A completed-turn footer ('✻ Brewed for 40s · done 1:13 AM' + composer + status
    + /rc chip) must not read as a pending menu."""
    lines, stripped = _load("completion_spinner_footer.pane.txt")
    hcb = _has_composer(stripped)
    assert A.parse_pending_menu(stripped, has_composer_box=hcb, raw_lines=lines) is None
