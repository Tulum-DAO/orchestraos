"""POST /menu-answer: a device that SEES an in-agent menu answers it by digit, no card wait.

Why this exists. The only answer path for a pane menu was the menu bridge's card, which lags
the on-screen menu by seconds, so the headset showed the menu and then made Shaw wait for the
card. `menu_answer_live` re-reads the pane and presses a single digit only when what is on
screen is exactly what the device showed.

gm standing test rule: a guard is coverage only when BOTH its refusal and the healthy path are
observed. Every refusal below asserts ZERO presses; `test_healthy_*` proves the same fixture
answers when nothing is wrong, so no guard here is one a correct system trips.
"""
import scripts.watch_gateway as G


def _menu(**over):
    m = {"kind": "decision", "question": "Which layout should the board use?",
         "options": [{"n": "1", "label": "Grid"},
                     {"n": "2", "label": "Wall"},
                     {"n": "3", "label": "Type something."},
                     {"n": "4", "label": "Chat about this"}]}
    m.update(over)
    return m


def _expect(menu=None):
    m = menu or _menu()
    return {"question": m["question"],
            "options": [{"n": o["n"], "label": o["label"]} for o in m["options"]]}


def _run(option_n="2", expect=None, menu=None, press_result=(True, {"verified": True})):
    pressed = []
    live = _menu() if menu is None else menu

    def press(n, q):
        pressed.append((n, q))
        return press_result

    ok, info = G.menu_answer_live("seat", option_n, _expect() if expect is None else expect,
                                  read_fn=lambda: live, press_fn=press)
    return ok, info, pressed


# ------------------------------------------------------------------ healthy path

def test_healthy_a_direct_option_is_pressed_once():
    ok, info, pressed = _run("2")
    assert ok is True, info
    assert info.get("sent") == "2"
    assert pressed == [("2", "Which layout should the board use?")]


def test_healthy_question_containment_tolerates_capture_wording():
    """The capture's question may be a truncated tail of what the device rendered (and vice
    versa); _menu_matches already treats that as the same menu."""
    exp = _expect()
    exp["question"] = "Board layout. Which layout should the board use?"
    ok, info, pressed = _run("1", expect=exp)
    assert ok is True, info
    assert pressed == [("1", "Which layout should the board use?")]


def test_a_failed_press_is_reported_not_claimed():
    ok, info, pressed = _run("2", press_result=(False, {"reason": "unverified_submit"}))
    assert ok is False and info.get("reason") == "unverified_submit"
    assert len(pressed) == 1


# ------------------------------------------------------------------ shape gate (#173)

def test_key_names_and_multi_digit_are_refused_before_reading_the_pane():
    for bad in ("Escape", "C-c", "q", "0", "10", "", " 2", "2\n", 2, True, None):
        ok, info, pressed = _run(bad)
        assert ok is False and info.get("reason") == "bad_option_n", (bad, info)
        assert pressed == [], (bad, pressed)


# ------------------------------------------------------------------ the device's claim is checked

def test_missing_expect_is_refused():
    for exp in (None, {}, {"question": "x"}, {"options": _expect()["options"]}, "menu"):
        pressed = []
        ok, info = G.menu_answer_live("seat", "2", exp, read_fn=_menu,
                                      press_fn=lambda n, q: pressed.append(n) or (True, {}))
        assert ok is False and info.get("reason") == "bad_expect", (exp, info)
        assert pressed == []


def test_no_menu_on_screen_presses_nothing():
    ok, info, pressed = _run("2", menu={})
    assert ok is False and info.get("reason") == "menu_gone"
    assert pressed == []


def test_a_different_question_presses_nothing():
    exp = _expect()
    exp["question"] = "Delete the production database?"
    ok, info, pressed = _run("2", expect=exp)
    assert ok is False and info.get("reason") == "menu_changed", info
    assert pressed == []


def test_relabelled_options_press_nothing():
    """Same numbers, different meanings: the digit the operator chose is not the one on screen."""
    exp = _expect()
    exp["options"][1]["label"] = "Delete everything"
    ok, info, pressed = _run("2", expect=exp)
    assert ok is False and info.get("reason") == "menu_changed", info
    assert pressed == []


def test_an_extra_or_missing_option_presses_nothing():
    exp = _expect()
    exp["options"] = exp["options"][:2]
    ok, info, pressed = _run("2", expect=exp)
    assert ok is False and info.get("reason") == "menu_changed"
    assert pressed == []


def test_an_option_not_on_the_menu_presses_nothing():
    ok, info, pressed = _run("7")
    assert ok is False and info.get("reason") == "option_not_on_menu"
    assert pressed == []


# ------------------------------------------------------------------ menus this route does not answer

def test_permission_prompts_are_refused():
    m = _menu(kind="permission")
    ok, info, pressed = _run("1", expect=_expect(m), menu=m)
    assert ok is False and info.get("reason") == "permission_menu"
    assert pressed == []


def test_ambiguous_capture_is_refused():
    """#174: duplicate option numbers mean the capture merged groups."""
    m = _menu(ambiguous_options=True)
    ok, info, pressed = _run("1", expect=_expect(m), menu=m)
    assert ok is False and info.get("reason") == "ambiguous_options"
    assert pressed == []


def test_multi_part_and_unhydrated_menus_are_refused():
    for over in ({"part_count": 3}, {"needs_hydration": True},
                 {"parts": [{"index": 0}, {"index": 1}]}):
        m = _menu(**over)
        ok, info, pressed = _run("1", expect=_expect(m), menu=m)
        assert ok is False and info.get("reason") == "multi_part", (over, info)
        assert pressed == []


def test_free_text_and_chat_options_are_refused():
    """No text rides this route; those options stay on the card."""
    ok, info, pressed = _run("3")
    assert ok is False and info.get("reason") == "free_text_option", info
    ok, info, pressed2 = _run("4")
    assert ok is False and info.get("reason") == "chat_option", info
    assert pressed == [] and pressed2 == []


# ------------------------------------------------------------------ authorization

def test_route_is_approve_scoped():
    assert G.ROUTE_SCOPES[("POST", "/menu-answer")] == "approve"
