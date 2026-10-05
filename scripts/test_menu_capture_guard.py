"""`/agent-menu-capture` may not press a key unless a menu is actually on screen.

gm's condition for classifying this route `approve` (2026-10-05): an approve-scoped device —
a headset that may answer cards but NOT inject — must never be able to send a keystroke into a
pane that is idle or mid-composer. The walk already behaved correctly; it was simply UNTESTED,
which is how a guard quietly stops being one. These pin it.

The pairing to remember: ROUTE_SCOPES["POST /agent-menu-capture"] == "approve" is safe ONLY
while these hold. Remove the guard and that classification becomes a keystroke primitive handed
to every device allowed to approve.
"""
import scripts.watch_gateway as G


def _spy():
    """A key_fn that records instead of pressing, so 'no key sent' is an assertion about
    behaviour rather than a hope about tmux."""
    sent = []
    return sent, (lambda k: sent.append(k) or True)


def test_no_menu_on_screen_means_no_key_is_ever_sent():
    sent, key_fn = _spy()
    out = G.menu_capture_walk("seat", read_fn=lambda: None, key_fn=key_fn)
    assert out["reason"] == "menu_gone"
    assert out["walk_complete"] is False
    assert sent == [], f"a key was pressed into a pane with no menu: {sent}"


def test_a_non_dict_from_the_pane_reader_is_also_refused_with_no_key():
    """Whatever the reader hands back, only a parsed menu dict earns a keypress."""
    for junk in ("", "menu", 0, [], ["parts"], False):
        sent, key_fn = _spy()
        out = G.menu_capture_walk("seat", read_fn=lambda: junk, key_fn=key_fn)
        assert out["reason"] == "menu_gone", junk
        assert sent == [], f"{junk!r} produced a keypress"


def test_the_operator_being_mid_navigation_also_presses_zero_keys():
    """part_index != 0 means he is partway through the menu himself. Paging it under him
    would move him somewhere he did not choose."""
    sent, key_fn = _spy()
    menu = {"multipart": True, "walk_complete": False, "part_index": 2, "part_count": 3,
            "parts": []}
    out = G.menu_capture_walk("seat", read_fn=lambda: menu, key_fn=key_fn)
    assert out["reason"] == "not_on_part_zero"
    assert out["walk_complete"] is False
    assert sent == [], f"keys were pressed while the operator was mid-navigation: {sent}"


def test_a_single_part_menu_is_served_without_pressing_anything():
    """Nothing to page, so nothing to press — the common case must not touch the pane."""
    sent, key_fn = _spy()
    menu = {"multipart": False, "walk_complete": True, "part_count": 1,
            "parts": [{"n": "1", "label": "yes"}]}
    out = G.menu_capture_walk("seat", read_fn=lambda: menu, key_fn=key_fn)
    assert out["walk_complete"] is True and out["part_count"] == 1
    assert sent == [], f"a single-part menu should need no keys: {sent}"


def test_a_real_multipart_walk_DOES_press_keys_so_these_tests_can_fail():
    """The control. Without this, every assertion above would still pass if the walk had
    simply stopped working — a suite that cannot tell 'guarded' from 'broken' proves nothing."""
    menu = {"multipart": True, "walk_complete": False, "part_index": 0, "part_count": 2,
            "parts": [{"n": "1", "label": "a"}]}
    sent, key_fn = _spy()
    G.menu_capture_walk("seat", read_fn=lambda: menu, key_fn=key_fn, settle_s=0)
    assert sent, "the walk pressed nothing even with a pageable menu on part 0"
    assert all(k in ("Right", "Left") for k in sent), f"navigation only, got {sent}"
    assert "Enter" not in sent and not any(k.isdigit() for k in sent), \
        f"the walk must never commit an answer, got {sent}"
