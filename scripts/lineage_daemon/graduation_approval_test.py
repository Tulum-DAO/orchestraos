"""Tests for graduation_approval.py — the graduation-gated auto-approve factory.

The factory produces the `approval_fn(canary, successor)` seam that
execute_rotation's KILL GATE 2 calls. It returns "approve" WITHOUT a card + WITHOUT
a wait ONLY for a cold-verified graduated-T2 seat whose S3 confirm outcome is
"confirmed". EVERY other case falls back to the existing the operator-card gate.

Both signals (is_graduated_autoretire + the S3 confirmed check) are injected as
seams, so these tests are hermetic — no registry read, no S3 loop, no the operator page.
"""
import pytest

from scripts.lineage_daemon import graduation_approval as ga


# --- a recording fallback that stands in for the the operator-card gate ----------------

def _recording_fallback(canary, successor, *, ret="deny", sink=None):
    if sink is not None:
        sink.append((canary, successor))
    return ret


def _fallback(ret="deny"):
    sink = []

    def fb(canary, successor):
        sink.append((canary, successor))
        return ret

    fb.calls = sink
    return fb


SEAT = {"agent_id": "cand-g2", "lineage_root": "cand", "successor": "cand-g3"}


# --- THE auto-approve case: graduated AND confirmed -> approve, no card --------

def test_graduated_and_confirmed_auto_approves_no_card():
    fb = _fallback(ret="deny")
    approval = ga.make_graduation_gated_approval(
        SEAT,
        graduated_fn=lambda seat: True,
        confirmed_fn=lambda c, s: True,
        fallback_fn=fb,
    )
    assert approval("cand-g2", "cand-g3") == "approve"
    assert fb.calls == []                    # the the operator card was NEVER posted


# --- supervised / no-mode grade -> graduated_fn returns False -> card ----------

def test_supervised_grade_falls_back_to_card():
    """A supervised-mode grade makes is_graduated_autoretire False -> card."""
    fb = _fallback(ret="deny")
    approval = ga.make_graduation_gated_approval(
        SEAT,
        graduated_fn=lambda seat: False,     # supervised => not graduated
        confirmed_fn=lambda c, s: True,      # even a confirmed S3 can't override
        fallback_fn=fb,
    )
    assert approval("cand-g2", "cand-g3") == "deny"
    assert fb.calls == [("cand-g2", "cand-g3")]   # fell back to the card gate


def test_no_mode_legacy_grade_falls_back_to_card():
    """A pre-rebuild grade with no `mode` key -> not graduated -> card."""
    fb = _fallback(ret="deny")
    approval = ga.make_graduation_gated_approval(
        SEAT,
        graduated_fn=lambda seat: False,
        confirmed_fn=lambda c, s: True,
        fallback_fn=fb,
    )
    assert approval("cand-g2", "cand-g3") == "deny"
    assert fb.calls == [("cand-g2", "cand-g3")]


# --- floor present / not-armed / non-T2 all surface as graduated_fn False ------

@pytest.mark.parametrize("why", ["floor-present", "not-armed", "non-T2"])
def test_gate_false_reasons_fall_back_to_card(why):
    fb = _fallback(ret="approve")
    approval = ga.make_graduation_gated_approval(
        SEAT,
        graduated_fn=lambda seat: False,     # is_graduated_autoretire returned False
        confirmed_fn=lambda c, s: True,
        fallback_fn=fb,
    )
    # the decision is whatever the CARD gate returns — the point is the card ran.
    assert approval("cand-g2", "cand-g3") == "approve"
    assert fb.calls == [("cand-g2", "cand-g3")]


# --- S3 not confirmed -> card even when graduated -------------------------------

def test_not_confirmed_falls_back_to_card():
    fb = _fallback(ret="deny")
    approval = ga.make_graduation_gated_approval(
        SEAT,
        graduated_fn=lambda seat: True,
        confirmed_fn=lambda c, s: False,     # S3 held / drifted
        fallback_fn=fb,
    )
    assert approval("cand-g2", "cand-g3") == "deny"
    assert fb.calls == [("cand-g2", "cand-g3")]


# --- fail-toward-card: any non-True / error in either signal -> card -----------

def test_graduated_fn_non_bool_truthy_still_requires_exact_true():
    """Only exact True auto-approves — a truthy non-True value falls back."""
    fb = _fallback(ret="deny")
    approval = ga.make_graduation_gated_approval(
        SEAT,
        graduated_fn=lambda seat: 1,         # truthy but not `True`
        confirmed_fn=lambda c, s: True,
        fallback_fn=fb,
    )
    assert approval("cand-g2", "cand-g3") == "deny"
    assert fb.calls == [("cand-g2", "cand-g3")]


def test_confirmed_fn_non_bool_truthy_still_requires_exact_true():
    fb = _fallback(ret="deny")
    approval = ga.make_graduation_gated_approval(
        SEAT,
        graduated_fn=lambda seat: True,
        confirmed_fn=lambda c, s: "yes",     # truthy but not `True`
        fallback_fn=fb,
    )
    assert approval("cand-g2", "cand-g3") == "deny"
    assert fb.calls == [("cand-g2", "cand-g3")]


def test_graduated_fn_raises_falls_back_to_card():
    fb = _fallback(ret="deny")

    def boom(seat):
        raise RuntimeError("registry unreadable")

    approval = ga.make_graduation_gated_approval(
        SEAT, graduated_fn=boom, confirmed_fn=lambda c, s: True, fallback_fn=fb)
    assert approval("cand-g2", "cand-g3") == "deny"
    assert fb.calls == [("cand-g2", "cand-g3")]


def test_confirmed_fn_raises_falls_back_to_card():
    fb = _fallback(ret="deny")

    def boom(c, s):
        raise RuntimeError("S3 read error")

    approval = ga.make_graduation_gated_approval(
        SEAT, graduated_fn=lambda seat: True, confirmed_fn=boom, fallback_fn=fb)
    assert approval("cand-g2", "cand-g3") == "deny"
    assert fb.calls == [("cand-g2", "cand-g3")]


# --- graduated NOT re-checked until confirmed is known (short-circuit order) ---

def test_confirmed_checked_before_graduated_signal_is_trusted():
    """When S3 is not confirmed we NEVER even trust graduated -> straight to card.
    (Order is an impl detail; the invariant is: not-confirmed => card, always.)"""
    fb = _fallback(ret="deny")
    grad_calls = []
    approval = ga.make_graduation_gated_approval(
        SEAT,
        graduated_fn=lambda seat: grad_calls.append(1) or True,
        confirmed_fn=lambda c, s: False,
        fallback_fn=fb,
    )
    assert approval("cand-g2", "cand-g3") == "deny"
    assert fb.calls == [("cand-g2", "cand-g3")]


# --- default fallback preserves current behavior (no graduation caller) --------

def test_default_fallback_is_the_operator_card_gate():
    """With no fallback_fn passed, the factory binds default_approval_gate — i.e.
    the current the operator-card behavior is the default when graduation isn't enabled."""
    from scripts.lineage_daemon import execute as ex
    approval = ga.make_graduation_gated_approval(
        SEAT, graduated_fn=lambda seat: False, confirmed_fn=lambda c, s: True)
    # fallback_fn defaulted to execute.default_approval_gate (the real the operator card).
    assert approval.__wrapped_fallback__ is ex.default_approval_gate


# --- seat is threaded through to graduated_fn verbatim -------------------------

def test_seat_passed_to_graduated_fn():
    seen = {}
    fb = _fallback(ret="deny")

    def grad(seat):
        seen["seat"] = seat
        return False

    approval = ga.make_graduation_gated_approval(
        SEAT, graduated_fn=grad, confirmed_fn=lambda c, s: True, fallback_fn=fb)
    approval("cand-g2", "cand-g3")
    assert seen["seat"] is SEAT
