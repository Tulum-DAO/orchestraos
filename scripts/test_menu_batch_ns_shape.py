"""An `ns` element is an option DIGIT, and `menu_batch_submit` must prove it before it presses.

Why this exists. `menu_batch_submit`'s default key sender is
`tmux send-keys -t <session> <k>` with NO `-l` flag, so tmux INTERPRETS KEY NAMES:
`Escape`, `C-c` and `q` are real keys, not text. The armed replay feeds every element of a
part's `ns` list into that sender. Validation that an `n` matches a captured option lives in
`_validate_batch_answer`, which is reached only INSIDE `durable_first_batch_submit` and only
AFTER the durable row lookup succeeds — so `handle_agent_key`'s legacy `no_durable_row` leg,
which calls `menu_batch_submit` directly, never validates at all.

That made the replay a general keystroke primitive for any caller holding `inject`. These
tests move the guard into the primitive itself, so it holds regardless of which caller
reaches it and regardless of whether a durable anchor exists.
"""
import scripts.watch_gateway as G


def _spy():
    """A key_fn that records instead of pressing, so 'zero keys' is an assertion about
    behaviour rather than a hope about tmux."""
    sent = []
    return sent, (lambda k: sent.append(k) or True)


def _menu(part_count=1, options=None):
    opts = options if options is not None else [{"n": "1", "label": "Yes"},
                                                {"n": "2", "label": "No"}]
    return {"question": "Ship it?", "part_count": part_count,
            "parts": [{"index": 0, "question": "Ship it?", "options": opts}],
            "options": opts}


def test_a_key_name_in_ns_presses_zero_keys():
    """`Escape` is a KEY to tmux, not an option. It must never reach the pane."""
    sent, key_fn = _spy()
    ok, info = G.menu_batch_submit("seat", answers=[{"part": 0, "ns": ["Escape"]}],
                                   armed=True, read_fn=lambda: _menu(), key_fn=key_fn,
                                   settle_s=0)
    assert ok is False, "a key-name ns was accepted"
    assert info.get("reason") == "bad_ns_shape", info
    assert sent == [], f"keys were pressed for a malformed ns: {sent}"


def test_an_interrupt_in_ns_presses_zero_keys():
    sent, key_fn = _spy()
    ok, info = G.menu_batch_submit("seat", answers=[{"part": 0, "ns": ["C-c"]}],
                                   armed=True, read_fn=lambda: _menu(), key_fn=key_fn,
                                   settle_s=0)
    assert ok is False and info.get("reason") == "bad_ns_shape", info
    assert sent == [], f"keys were pressed for a malformed ns: {sent}"


def test_a_two_digit_option_is_refused_as_UNPRESSABLE_not_as_malformed():
    """Option "10" IS a real captured option -- agent-status._MENU_OPT_RE is `\d{1,2}`.
    A digit press cannot select it (`send-keys 10` presses 1 then 0), so it is refused
    pre-actuation with its OWN reason. An earlier draft of this guard used a flat 1-9
    alphabet, which would have called a legitimate option 10 malformed and smuggled a
    nine-option ceiling in as a safety fix."""
    sent, key_fn = _spy()
    opts = [{"n": str(i), "label": f"opt{i}"} for i in range(1, 11)]
    ok, info = G.menu_batch_submit("seat", answers=[{"part": 0, "ns": ["10"]}],
                                   armed=True, read_fn=lambda: _menu(options=opts),
                                   key_fn=key_fn, settle_s=0)
    assert ok is False, "a 2-digit option was accepted for a digit press"
    assert info.get("reason") == "option_not_digit_pressable", info
    assert sent == [], f"keys were pressed for an unpressable option: {sent}"


def test_zero_alone_is_refused():
    """`0` is not an option number the parser ever emits (options start at 1)."""
    sent, key_fn = _spy()
    ok, info = G.menu_batch_submit("seat", answers=[{"part": 0, "ns": ["0"]}],
                                   armed=True, read_fn=lambda: _menu(), key_fn=key_fn,
                                   settle_s=0)
    assert ok is False, info
    assert sent == []


def test_a_digit_that_is_not_an_option_of_this_part_presses_nothing():
    """IDENTITY, not shape: `7` is well-formed but this part only has options 1 and 2.
    The shape gate cannot catch it and _validate_batch_answer runs only on the durable
    path, so the primitive refuses it itself."""
    sent, key_fn = _spy()
    ok, info = G.menu_batch_submit("seat", answers=[{"part": 0, "ns": ["7"]}],
                                   armed=True, read_fn=lambda: _menu(), key_fn=key_fn,
                                   settle_s=0)
    assert ok is False and info.get("reason") == "n_not_on_part", info
    assert sent == [], f"a non-option digit reached the pane: {sent}"


def test_the_refusal_happens_in_dry_run_too_so_a_client_learns_before_arming():
    """A dry-run that reports a plan for a malformed ns teaches the client the batch is
    fine, and it would then fail only once armed. Refuse in both modes."""
    sent, key_fn = _spy()
    ok, info = G.menu_batch_submit("seat", answers=[{"part": 0, "ns": ["Escape"]}],
                                   armed=False, read_fn=lambda: _menu(), key_fn=key_fn,
                                   settle_s=0)
    assert ok is False and info.get("reason") == "bad_ns_shape", info
    assert sent == []


# --- CONTROL ---------------------------------------------------------------
# Without these, every test above would pass if menu_batch_submit simply refused
# everything. They pin that a WELL-FORMED ns is still replayed.

def test_control_a_valid_digit_ns_is_still_replayed():
    sent, key_fn = _spy()
    G.menu_batch_submit("seat", answers=[{"part": 0, "ns": ["1"]}],
                        armed=True, read_fn=lambda: _menu(), key_fn=key_fn, settle_s=0)
    assert "1" in sent, f"a valid option digit was not pressed: {sent}"


def test_control_a_valid_dry_run_still_returns_a_plan():
    sent, key_fn = _spy()
    ok, info = G.menu_batch_submit("seat", answers=[{"part": 0, "ns": ["2"]}],
                                   armed=False, read_fn=lambda: _menu(), key_fn=key_fn,
                                   settle_s=0)
    assert ok is True and info.get("dry_run") is True, info
    assert info["plan"][0]["toggle"] == ["2"]
    assert sent == [], "a dry-run pressed a key"


def test_control_an_empty_ns_with_text_is_not_an_ns_shape_error():
    """Text-only answers are valid (contract A1); the shape guard must not swallow them."""
    sent, key_fn = _spy()
    ok, info = G.menu_batch_submit("seat", answers=[{"part": 0, "ns": [], "text": "later"}],
                                   armed=False, read_fn=lambda: _menu(), key_fn=key_fn,
                                   settle_s=0)
    assert info.get("reason") != "bad_ns_shape", info
