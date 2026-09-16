"""self-retire autonomy layer (self-retire-builder) — RED-first tests.

Covers SPEC §12 tests 1,2,6,8 (this layer's own) + the D2 notify-only ping.
Re-asserts substrate invariants 3/4/5/7/9 with the card SKIPPED live in the
substrate suite; here we prove the REAL predicate + its integration into Stage C's
`plan_graduation_retire` seam.

Positive-signal-only, REFUSE-on-zero-signal: `is_graduated_autoretire` returns True
ONLY when ALL of {tier∈armed, lineage armed, kill-switch off, grade PASS} hold; any
missing/uncertain signal -> False (-> REQUIRE_CARD True -> card).
"""
import json
import os

import pytest

from scripts.lineage_daemon import self_retire_gate as SG
from scripts.lineage_daemon import graduation_retire as G


# --- fixtures: a fully cold-verified graduated-T2 world (all four gates PASS) ---

def _write_registry(tmp_path, lineage="x", tier="T2"):
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"agents": {lineage: {"tier": tier, "name": lineage}}}))
    return str(reg)


def _write_armed(tmp_path, *lineages):
    f = tmp_path / "self_retire_armed"
    f.write_text("\n".join(["# armed T2 lineages", *lineages]) + "\n")
    return str(f)


def _write_comprehension(tmp_path, successor="x-3", passed=True, mode="strict"):
    d = tmp_path / "handoffs"
    d.mkdir(exist_ok=True)
    art = {"pass": passed, "successor_id": successor}
    if mode is not None:
        art["mode"] = mode
    (d / f"{successor}.comprehension.json").write_text(json.dumps(art))
    return str(d)


def _seat(**kw):
    base = {"agent_id": "x-gen2", "successor": "x-3", "lineage_root": "x",
            "generation": 2, "grade_commit": "c", "beats_since_promotion": 2}
    base.update(kw)
    return base


def _paths(tmp_path, *, lineage="x", tier="T2", armed=("x",), successor="x-3",
           passed=True, mode="strict", disabled=False):
    """All four injectable sources set to the given world."""
    disabled_path = str(tmp_path / "SELF_RETIRE_DISABLED")
    if disabled:
        (tmp_path / "SELF_RETIRE_DISABLED").write_text("brake")
    return dict(
        disabled_path=disabled_path,
        armed_path=_write_armed(tmp_path, *armed),
        registry_path=_write_registry(tmp_path, lineage=lineage, tier=tier),
        handoffs_dir=_write_comprehension(tmp_path, successor=successor,
                                          passed=passed, mode=mode),
    )


# --- §12 test 1: the ONE True case + default-false on any missing signal --------

def test_true_only_when_all_four_gates_pass(tmp_path):
    assert SG.is_graduated_autoretire(_seat(), **_paths(tmp_path)) is True


def test_false_when_tier_not_armed(tmp_path):
    # §12 test 6: a non-T2 (e.g. T1) lineage never auto-skips.
    assert SG.is_graduated_autoretire(
        _seat(), **_paths(tmp_path, tier="T1")) is False


def test_false_when_lineage_not_in_arming_allowlist(tmp_path):
    # tier ok but the lineage was never opted-in via the arming knob.
    assert SG.is_graduated_autoretire(
        _seat(), **_paths(tmp_path, armed=("someone-else",))) is False


def test_false_when_kill_switch_present(tmp_path):
    # §12 test 8 / invariant #4: SELF_RETIRE_DISABLED forces False for every seat.
    assert SG.is_graduated_autoretire(
        _seat(), **_paths(tmp_path, disabled=True)) is False


def test_false_when_grade_not_pass(tmp_path):
    # comprehension pass:false is NOT a graduation.
    assert SG.is_graduated_autoretire(
        _seat(), **_paths(tmp_path, passed=False)) is False


def test_false_when_comprehension_missing(tmp_path):
    # no comprehension.json for the successor -> no-data-is-not-permission.
    p = _paths(tmp_path)
    os.remove(os.path.join(p["handoffs_dir"], "x-3.comprehension.json"))
    assert SG.is_graduated_autoretire(_seat(), **p) is False


# --- STRICT-MODE arming precondition (DEC-1787728346) ---------------------------
# The rebuilt KEY-1 grader (rotation_gate_manual.record_readback) writes a top-level
# `mode: strict|supervised` into <successor>.comprehension.json. An UNATTENDED
# auto-retire consumer (this predicate) may consume ONLY strict grades; supervised
# is human-review-only. pass:true alone is no longer sufficient to auto-skip.

def test_false_when_grade_mode_supervised(tmp_path):
    # a PASS graded in supervised mode must NOT auto-skip the card.
    assert SG.is_graduated_autoretire(
        _seat(), **_paths(tmp_path, mode="supervised")) is False


def test_false_when_grade_mode_absent(tmp_path):
    # pre-rebuild artifacts have no `mode` key -> not strict -> card (covers every
    # comprehension.json graded before the strict grader landed).
    assert SG.is_graduated_autoretire(
        _seat(), **_paths(tmp_path, mode=None)) is False


def test_true_only_when_grade_mode_strict(tmp_path):
    # the affirmative case: pass:true AND mode == the exact string "strict".
    assert SG.is_graduated_autoretire(
        _seat(), **_paths(tmp_path, mode="strict")) is True


def test_false_when_seat_unknown_to_registry(tmp_path):
    # lineage absent from the registry -> no affirmative tier signal.
    assert SG.is_graduated_autoretire(
        _seat(lineage_root="ghost"), **_paths(tmp_path)) is False


def test_false_on_non_dict_seat(tmp_path):
    # a bare string carries no tier/arm/grade signal.
    assert SG.is_graduated_autoretire("any-seat", **_paths(tmp_path)) is False


def test_false_when_registry_unreadable(tmp_path):
    p = _paths(tmp_path)
    p["registry_path"] = str(tmp_path / "nonexistent-registry.json")
    assert SG.is_graduated_autoretire(_seat(), **p) is False


def test_false_when_armed_file_absent(tmp_path):
    p = _paths(tmp_path)
    p["armed_path"] = str(tmp_path / "no-such-armed-file")
    assert SG.is_graduated_autoretire(_seat(), **p) is False


# --- §12 test 2 + 6 + 8: integration through Stage C's substrate seam ----------
# The real predicate feeds `plan_graduation_retire`'s REQUIRE_CARD via graduated_fn.

def _ctx(**kw):
    base = {"successor_live": True, "handoff_confirmed": True, "grade_agree": True,
            "beats_since_promotion": 2, "pending_duty": None}
    base.update(kw)
    return base


def _bind(**paths):
    # bind the real predicate's injectable paths for the frozen graduated_fn(seat).
    return lambda seat: SG.is_graduated_autoretire(seat, **paths)


def test_substrate_skips_card_when_graduated(tmp_path):
    # §12 test 2: may_retire PASS + graduated-T2 -> card SKIPPED (proceed_no_card).
    p = _paths(tmp_path)
    d = G.plan_graduation_retire(_ctx(), _seat(), disabled_path=p["disabled_path"],
                                 graduated_fn=_bind(**p))
    assert d["action"] == "proceed_no_card"
    assert d["card"] is None


def test_substrate_cards_when_tier_not_armed(tmp_path):
    # §12 test 6: a T1 seat -> REQUIRE_CARD True -> Stage C's carded retire.
    p = _paths(tmp_path, tier="T1")
    d = G.plan_graduation_retire(_ctx(), _seat(), disabled_path=p["disabled_path"],
                                 graduated_fn=_bind(**p))
    assert d["action"] == "await_card"
    assert d["card"]["kind"] == "promote_graded"


def test_substrate_cards_when_kill_switch_on(tmp_path):
    # §12 test 8: SELF_RETIRE_DISABLED forces the card even for a graduated seat.
    p = _paths(tmp_path, disabled=True)
    d = G.plan_graduation_retire(_ctx(), _seat(), disabled_path=p["disabled_path"],
                                 graduated_fn=_bind(**p))
    assert d["action"] == "await_card"


def test_substrate_keeps_when_may_retire_false_even_if_graduated(tmp_path):
    # invariant #1: may_retire is upstream of the skip; grade pending -> KEEP.
    p = _paths(tmp_path)
    d = G.plan_graduation_retire(_ctx(grade_agree=None), _seat(),
                                 disabled_path=p["disabled_path"],
                                 graduated_fn=_bind(**p))
    assert d["action"] == "keep"


# --- D2: notify-only ping on each card-skipped retire --------------------------

def test_build_skip_notice_names_seat_ctx_and_reason():
    msg = SG.build_skip_notice(_seat(), reason="RETIRE: grade PASSED, 2 beats",
                               used_pct=87)
    assert "x-gen2" in msg          # the seat being retired
    assert "87" in msg              # ctx%
    assert "grade PASSED" in msg    # the may_retire reason


def test_notify_card_skipped_sends_via_injected_send_fn():
    sent = []
    out = SG.notify_card_skipped(_seat(), reason="RETIRE: ok", used_pct=50,
                                 send_fn=lambda m: sent.append(m))
    assert len(sent) == 1
    assert "x-gen2" in sent[0]
    assert out == sent[0]           # returns the message it sent


def test_notify_is_notify_only_never_raises_on_send_failure():
    # D2 is notify-only; a failed ping must not abort the retire path.
    def _boom(m):
        raise RuntimeError("telegram down")
    # should swallow (return the message), never propagate.
    out = SG.notify_card_skipped(_seat(), reason="r", used_pct=1, send_fn=_boom)
    assert "x-gen2" in out
