"""Regression test for the router starvation bug (2026-08-15).

Root cause: the HUMAN-PRESENCE guard did `if attached > 0: continue` — a mere
attached tmux client froze ALL mail to that session FOREVER (no TTL). A
persistent terminal on gm/ob's pane starved inter-agent mail for hours.

Fix: hold ONLY while the attached pane has been active within ATTACHED_IDLE_TTL;
a persistently-attached-but-idle pane delivers again. Typed-composer/busy
protection is separate (agent_is_idle), so idle-past-TTL delivery is safe.
"""
import importlib.util
import os

_here = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "message_router", os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


def test_attached_and_recently_active_holds():
    # the operator plausibly mid-conversation in an attached pane -> hold (don't interject)
    assert mr.hold_for_attached(1, 30) is True


def test_attached_but_long_idle_delivers():
    # THE BUG: persistent terminal attached, pane idle for hours -> must NOT freeze
    assert mr.hold_for_attached(1, 30000) is False
    assert mr.hold_for_attached(1, mr.ATTACHED_IDLE_TTL + 1) is False


def test_boundary_at_ttl():
    assert mr.hold_for_attached(1, mr.ATTACHED_IDLE_TTL - 1) is True
    assert mr.hold_for_attached(1, mr.ATTACHED_IDLE_TTL) is False


def test_unattached_never_held_by_this_guard():
    # Unattached sessions are the breathe-window guard's concern, not this one.
    assert mr.hold_for_attached(0, 10) is False
    assert mr.hold_for_attached(0, 30000) is False
