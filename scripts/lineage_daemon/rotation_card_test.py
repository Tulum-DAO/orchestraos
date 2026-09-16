"""Tier-3 hard-rotation approval-emit tests (DEC-1786835700, dry-run-first).

The WEDGED class: an agent idle past HARD with NO self-trigger mark can't
self-rotate. Instead of an unattended kill (the operator ruling: NEVER auto-kill), the
beat EMITS a rotation_kill approval card; the kill runs ONLY on the operator's Approve.
Pure over injected seams (create_fn / execute_fn) so it's hermetic.
"""
from scripts.lineage_daemon import rotation_card as rc


def _wedged(aid="jarvis-v4", **kw):
    a = {"agent_id": aid, "ctx_pct": 90, "state_age_s": 1800,
         "lineage_root": "jarvis", "generation": 4, "tier_class": "T1",
         "self_trigger": "wedged", "open_tasks": ["ship voice fix"],
         "jsonl_turns": 412}
    a.update(kw)
    return a


# --- build_rotation_card (pure) -----------------------------------------------

class TestBuildCard:
    def test_card_shape_and_op_key(self):
        c = rc.build_rotation_card(_wedged())
        assert c["kind"] == "rotation_kill"
        assert c["from_agent"] == "jarvis-v4"
        assert c["worker_kind"] == "fleet-rotation"
        assert c["op_key"] == "rotation_kill:jarvis:4"        # lineage:generation
        assert c["risk_level"] == "high"
        assert c["reversibility"] == "reversible"             # respawns from handoff

    def test_question_carries_ctx_and_why(self):
        c = rc.build_rotation_card(_wedged())
        q = c["question"].lower()
        assert "jarvis-v4" in q and "90" in q and "wedged" in q

    def test_evidence_has_decision_fields(self):
        e = rc.build_rotation_card(_wedged())["evidence"]
        assert e["ctx_pct"] == 90 and e["why_wedged"] == "wedged"
        assert e["open_tasks"] == ["ship voice fix"]
        assert e["lineage_root"] == "jarvis" and e["generation"] == 4
        assert e["last_activity_age_s"] == 1800 and e["jsonl_turns"] == 412

    def test_verbs_are_approve_and_respond_only(self):
        # ratified contract (gm msg_cd11cfed): Approve(=kill+respawn) + Respond;
        # NO Deny/Defer/Reject verb produced.
        vals = [o["value"] for o in rc.build_rotation_card(_wedged())["options"]]
        assert vals == ["approve", "respond"]

    def test_only_wedged_class_is_cardable(self):
        # a self_triggered agent (handled its own rotation) is NEVER carded
        assert rc.is_cardable(_wedged(self_trigger="self_triggered")) is False
        assert rc.is_cardable(_wedged(self_trigger="wedged")) is True
        assert rc.is_cardable(_wedged(self_trigger=None)) is False


# --- emit_rotation_card (dry-run gate) ----------------------------------------

class TestEmit:
    def test_dry_run_never_creates_a_row(self):
        calls = []
        r = rc.emit_rotation_card(rc.build_rotation_card(_wedged()),
                                  create_fn=lambda **k: calls.append(k) or "apr_x",
                                  dry_run=True)
        assert r["would_emit"] is True and r["dry_run"] is True
        assert r["row_id"] is None and calls == []           # touched nothing

    def test_armed_creates_one_row_with_op_key_dedup(self):
        calls = []
        r = rc.emit_rotation_card(rc.build_rotation_card(_wedged()),
                                  create_fn=lambda **k: calls.append(k) or "apr_1",
                                  dry_run=False)
        assert r["row_id"] == "apr_1" and len(calls) == 1
        assert calls[0]["op_key"] == "rotation_kill:jarvis:4"
        assert calls[0]["kind"] == "rotation_kill"

    def test_emit_error_is_failclosed_no_raise(self):
        def boom(**k):
            raise RuntimeError("db locked")
        r = rc.emit_rotation_card(rc.build_rotation_card(_wedged()),
                                  create_fn=boom, dry_run=False)
        assert r["row_id"] is None and r["error"]


# --- resolve_rotation_cards (kill ONLY on approve, idempotent) ----------------

class TestResolve:
    def _row(self, answer=None, **kw):
        r = {"id": "apr_1", "kind": "rotation_kill", "from_agent": "jarvis-v4",
             "op_key": "rotation_kill:jarvis:4", "status": "answered",
             "answer": answer, "answer_text": kw.get("answer_text"),
             "resumed_at": kw.get("resumed_at")}
        return r

    def test_approve_runs_kill_once(self):
        killed = []
        out = rc.resolve_rotation_cards(
            [self := self._row(answer="approve")],
            execute_fn=lambda aid: killed.append(aid) or {"ok": True},
            route_fn=lambda *a, **k: None, dry_run=False)
        assert killed == ["jarvis-v4"]
        assert out[0]["action"] == "killed"

    def test_approve_dry_run_does_not_kill(self):
        killed = []
        out = rc.resolve_rotation_cards(
            [self._row(answer="approve")],
            execute_fn=lambda aid: killed.append(aid),
            route_fn=lambda *a, **k: None, dry_run=True)
        assert killed == []
        assert out[0]["action"] == "would_kill"

    def test_respond_routes_to_gm_never_kills(self):
        killed, routed = [], []
        out = rc.resolve_rotation_cards(
            [self._row(answer="respond", answer_text="wait, investigate first")],
            execute_fn=lambda aid: killed.append(aid),
            route_fn=lambda **k: routed.append(k), dry_run=False)
        assert killed == []
        assert routed and routed[0]["from_agent"] == "jarvis-v4"
        assert "investigate" in routed[0]["text"]
        assert out[0]["action"] == "routed_no_kill"

    def test_already_resumed_row_is_idempotent_noop(self):
        killed = []
        out = rc.resolve_rotation_cards(
            [self._row(answer="approve", resumed_at="2026-08-16T00:00:00Z")],
            execute_fn=lambda aid: killed.append(aid),
            route_fn=lambda *a, **k: None, dry_run=False)
        assert killed == []                                  # already acted
        assert out[0]["action"] == "already_acted"

    def test_unanswered_row_skipped(self):
        killed = []
        out = rc.resolve_rotation_cards(
            [dict(self._row(answer=None), status="pending")],
            execute_fn=lambda aid: killed.append(aid),
            route_fn=lambda *a, **k: None, dry_run=False)
        assert killed == [] and out[0]["action"] == "skip_unanswered"
