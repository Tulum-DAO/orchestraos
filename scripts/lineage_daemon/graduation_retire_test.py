"""Stage C (Piece 3) — the graduation-completion substrate.

Converged design (ob msg_37cc7017; self-retire-spec agreed). ONE shared wiring
that finishes a GRADED-PASS rotation the self-trigger left incomplete
(second-brain-dev-3 stalled-two-head class):

  may_retire(ctx) -> if not may: KEEP
  REQUIRE_CARD(seat) = True if ~/runtime/SELF_RETIRE_DISABLED exists
                       else True on ANY predicate error (fail-toward-card)
                       else (not is_graduated_autoretire(seat))   # stub False -> True
  if REQUIRE_CARD: emit promote_graded card (fork B) -> await the operator approve
  else:            skip (graduated-T2 mode; self-retire-spec fills the stub later)
  KILL GATE 1 fresh-classify   # BOTH branches, POST-await / immediately-pre-retire
  park-idle single-writer atomic retire  (+ vouch consumption, separate seam)

Pure over injected seams; nothing spawns/kills/cards for real here.
"""
import os

from scripts.lineage_daemon import graduation_retire as G


# ---- is_graduated_autoretire: the STUB self-retire-spec fills later ----------

def test_stub_is_graduated_autoretire_defaults_false():
    # absence of an affirmative graduation determination -> False -> REQUIRE_CARD True.
    assert G.is_graduated_autoretire("any-seat") is False


# ---- REQUIRE_CARD: positive-signal-only, fail-toward-card --------------------

def test_require_card_true_when_stub_false(tmp_path):
    # stub graduated=False -> not False -> True -> card.
    assert G.require_card("seat", disabled_path=str(tmp_path / "none")) is True


def test_require_card_false_only_on_affirmative_graduation(tmp_path):
    # an affirmative cold-verified graduation (injected) -> skip the card.
    assert G.require_card("seat", disabled_path=str(tmp_path / "none"),
                          graduated_fn=lambda s: True) is False


def test_require_card_forced_true_by_kill_switch(tmp_path):
    # SELF_RETIRE_DISABLED sentinel forces the card EVEN for a graduated seat.
    sentinel = tmp_path / "SELF_RETIRE_DISABLED"
    sentinel.write_text("brake")
    assert G.require_card("seat", disabled_path=str(sentinel),
                          graduated_fn=lambda s: True) is True


def test_require_card_true_on_predicate_throw(tmp_path):
    # ANY error computing is_graduated_autoretire -> card, never skip.
    def _boom(seat):
        raise RuntimeError("predicate broke")
    assert G.require_card("seat", disabled_path=str(tmp_path / "none"),
                          graduated_fn=_boom) is True


def test_require_card_true_on_predicate_none(tmp_path):
    # None/unknown from the predicate is not an affirmative graduation -> card.
    assert G.require_card("seat", disabled_path=str(tmp_path / "none"),
                          graduated_fn=lambda s: None) is True


# ---- fork-B card builder: own kind + own op_key namespace -------------------

def test_promote_graded_card_kind_and_op_key():
    card = G.build_promote_graded_card({
        "agent_id": "second-brain-dev-gen2", "successor": "second-brain-dev-3",
        "lineage_root": "second-brain-dev", "generation": 2,
        "grade_commit": "abc123", "beats_since_promotion": 2})
    assert card["kind"] == "promote_graded"        # NOT rotation_kill
    assert card["op_key"] == "promote_graded:second-brain-dev:2"  # own namespace
    assert card["options"] == ["approve", "respond"]


def test_promote_graded_card_question_is_promote_not_kill():
    card = G.build_promote_graded_card({
        "agent_id": "x-gen2", "successor": "x-3", "lineage_root": "x",
        "generation": 2, "grade_commit": "c", "beats_since_promotion": 3})
    q = (card["question"] + card["summary"]).lower()
    assert "promote" in q or "retire" in q
    assert "kill" not in q                          # opposite meaning from rotation_kill


# ---- plan_graduation_retire: the substrate composition (pure decision) -------

def _ctx(**kw):
    base = {"successor_live": True, "handoff_confirmed": True, "grade_agree": True,
            "beats_since_promotion": 2, "pending_duty": None}
    base.update(kw)
    return base


def _seat(**kw):
    base = {"agent_id": "x-gen2", "successor": "x-3", "lineage_root": "x",
            "generation": 2, "grade_commit": "c", "beats_since_promotion": 2}
    base.update(kw)
    return base


def test_plan_keep_when_may_retire_false():
    # grade pending -> may_retire KEEP -> no card, no retire.
    d = G.plan_graduation_retire(_ctx(grade_agree=None), _seat(),
                                 disabled_path="/none")
    assert d["action"] == "keep"
    assert "grade" in d["reason"].lower()


def test_plan_emits_card_when_require_card_true():
    d = G.plan_graduation_retire(_ctx(), _seat(), disabled_path="/none")
    assert d["action"] == "await_card"
    assert d["card"]["kind"] == "promote_graded"


def test_plan_skip_card_when_graduated():
    d = G.plan_graduation_retire(_ctx(), _seat(), disabled_path="/none",
                                 graduated_fn=lambda s: True)
    assert d["action"] == "proceed_no_card"        # graduated-T2 mode
    assert d["card"] is None


def test_plan_skip_forced_to_card_by_kill_switch(tmp_path):
    sentinel = tmp_path / "SELF_RETIRE_DISABLED"
    sentinel.write_text("x")
    d = G.plan_graduation_retire(_ctx(), _seat(), disabled_path=str(sentinel),
                                 graduated_fn=lambda s: True)
    assert d["action"] == "await_card"             # brake forces the tap


# ---- execute_graduation_retire: KILL GATE 1 POST-await, LAST gate, both paths -

def test_execute_runs_kill_gate1_then_promote_then_retire():
    calls = []
    G.execute_graduation_retire(
        _seat(),
        kill_gate1_fn=lambda seat: calls.append("gate1") or (True, "fresh-ok"),
        promote_fn=lambda seat: calls.append("promote") or {"ok": True},
        retire_fn=lambda seat: calls.append("retire") or {"retired": True})
    # KILL GATE 1 is the LAST gate before promote+retire (fresh, post-await).
    assert calls == ["gate1", "promote", "retire"]


def test_execute_aborts_when_kill_gate1_stale():
    calls = []
    out = G.execute_graduation_retire(
        _seat(),
        kill_gate1_fn=lambda seat: (False, "cwd went dirty across await"),
        promote_fn=lambda seat: calls.append("promote"),
        retire_fn=lambda seat: calls.append("retire"))
    # a STALE fresh-classify aborts: NOTHING promoted/retired (fail toward not-acting).
    assert calls == []
    assert out["action"] == "aborted_kill_gate1"
    assert "stale" in out["reason"].lower() or "dirty" in out["reason"].lower()


def test_execute_kill_gate1_runs_on_skip_branch_too():
    # the graduated-skip path STILL runs KILL GATE 1 before retire (unconditional).
    calls = []
    G.execute_graduation_retire(
        _seat(),
        kill_gate1_fn=lambda seat: calls.append("gate1") or (True, "ok"),
        promote_fn=lambda seat: calls.append("promote") or {},
        retire_fn=lambda seat: calls.append("retire") or {})
    assert calls[0] == "gate1"     # gate1 first regardless of how we got here


# ---- resolve_promote_graded_cards: fork-B resolver, promote NOT kill ---------

def _row(answer="approve", kind="promote_graded", status="answered", **kw):
    r = {"id": "row1", "from_agent": "x-gen2", "kind": kind, "status": status,
         "answer": answer, "op_key": "promote_graded:x:2"}
    r.update(kw)
    return r


def test_resolver_ignores_rotation_kill_rows():
    # a rotation_kill row is NOT this resolver's business (own kind only).
    out = G.resolve_promote_graded_cards(
        [_row(kind="rotation_kill")],
        execute_fn=lambda aid: None, route_fn=lambda **k: None, dry_run=False)
    assert out[0]["action"] == "skip_not_promote_graded"


def test_resolver_approve_runs_execute_once():
    ran = []
    out = G.resolve_promote_graded_cards(
        [_row(answer="approve")],
        execute_fn=lambda aid: ran.append(aid) or {"promoted_and_retired": True},
        route_fn=lambda **k: None, dry_run=False)
    assert ran == ["x-gen2"]
    assert out[0]["action"] == "promoted_and_retired"


def test_resolver_approve_dry_run_does_not_execute():
    ran = []
    out = G.resolve_promote_graded_cards(
        [_row(answer="approve")],
        execute_fn=lambda aid: ran.append(aid), route_fn=lambda **k: None,
        dry_run=True)
    assert ran == []
    assert out[0]["action"] == "would_promote_retire"


def test_resolver_respond_routes_no_action():
    ran = []
    out = G.resolve_promote_graded_cards(
        [_row(answer="respond", answer_text="hold, still needed")],
        execute_fn=lambda aid: ran.append(aid),
        route_fn=lambda **k: None, dry_run=False)
    assert ran == []                               # Respond NEVER promotes/retires
    assert out[0]["action"] == "routed_no_action"


def test_resolver_idempotent_already_acted():
    out = G.resolve_promote_graded_cards(
        [_row(answer="approve", resumed_at="2026-01-01")],
        execute_fn=lambda aid: (_ for _ in ()).throw(AssertionError("re-ran")),
        route_fn=lambda **k: None, dry_run=False)
    assert out[0]["action"] == "already_acted"


def test_resolver_execute_error_recorded_not_raised():
    def _boom(aid):
        raise RuntimeError("promote refused")
    out = G.resolve_promote_graded_cards(
        [_row(answer="approve")],
        execute_fn=_boom, route_fn=lambda **k: None, dry_run=False)
    assert out[0]["action"] == "execute_error"
    assert "promote refused" in out[0]["error"]
