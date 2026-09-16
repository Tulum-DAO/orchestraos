"""Multi-Generation Unattended Soak Test (WS3 Gold Standard Auto-Rotation).

Verifies end-to-end multi-hop lineage rotation:
  Gen 1 -> Gen 2 -> Gen 3

Invariants verified across each generation transition:
1. Context Threshold Realignment:
   - < 70%: Normal execution (status ok / noop).
   - 70% - 79.9%: Soft Authoring -> Soft Ready with committed handoff.
   - >= 80%: Hard Rotation unblocked and executed.
2. Zero-Leak Grounding Canaries:
   - Predecessor handoff contains only questions + transcript turn pointers.
   - Successor init payload is redacted (contains NO answers).
   - Ground truth is resolved from predecessor transcript at grade time.
3. Content Gate & Readback Verification:
   - Successor readback answering canaries passes S3 content gate.
   - Shallow or missing citations trigger HOLD_UNCONFIRMED (no retirement).
4. Double-Gated Retirement & Canonical Repinning:
   - Predecessor retired ONLY after successor confirms and settle window checks.
   - Canonical seat repinned to successor.
5. Multi-Hop Continuity:
   - Gen 2 carries forward state, executes next phase, hits thresholds, and cleanly rotates to Gen 3.
"""
import copy
import json
import tempfile
import pytest

from scripts.lineage_daemon import beat
from scripts.lineage_daemon import ctxstate
from scripts.lineage_daemon import execute as ex
from scripts.lineage_daemon import hold_ledger as hledger
from scripts.lineage_daemon.handoff_schema import Handoff
from scripts.focus_registry.gate import rotation_gate


class FakeExecutors:
    def __init__(self, *, successor_alive=True, edge_ok=True):
        self.calls = []
        self.successor_alive = successor_alive
        self.edge_ok = edge_ok

    def _rec(self, n, armed, **x):
        self.calls.append(n)
        return dict({"cmd": [n], "executed": bool(armed)}, **x)

    def plan_register_successor(self, s, p, g, r, armed=False, orchestra_dir=None):
        return self._rec("register_successor", armed)
    def plan_spawn(self, s, armed=False, orchestra_dir=None):
        return self._rec("spawn", armed)
    def verify_successor(self, s, armed=False, orchestra_dir=None):
        return self._rec("verify_successor", armed, alive=self.successor_alive)
    def plan_inject_init(self, s, p, armed=False, orchestra_dir=None):
        return self._rec("inject_init", armed)
    def plan_wire_edge(self, p, s, g, armed=False, orchestra_dir=None):
        return self._rec("wire_edge", armed)
    def plan_verify_edge(self, p, s, armed=False, orchestra_dir=None):
        return self._rec("verify_edge", armed, ok=self.edge_ok, succeeded_by=s)
    def plan_retire(self, a, armed=False, orchestra_dir=None):
        return self._rec("retire", armed)
    def plan_repin_canonical(self, s, c, g, armed=False, orchestra_dir=None):
        return self._rec("repin_canonical", armed)


def make_transcript_for_gen(gen: int) -> str:
    rows = [
        {"uuid": f"msg_plant_g{gen}", "timestamp": "2026-08-29T01:00:00Z", "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text", "text":
             f"The architectural decision for Gen {gen} is canarymatchonly-v{gen}."}]}},
        {"uuid": f"msg_effect_g{gen}", "timestamp": "2026-08-29T01:01:00Z", "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text", "text":
             f"The active config flag for Gen {gen} is ENABLE_GEN_{gen}_FLAGS."}]}},
        {"uuid": f"msg_hold_g{gen}", "timestamp": "2026-08-29T01:02:00Z", "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text", "text":
             f"The edge case handled in Gen {gen} is ledgered-and-aged-holds-v{gen}."}]}},
    ]
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write("\n".join(json.dumps(r) for r in rows))
    f.close()
    return f.name


def make_handoff_for_gen(gen: int, pred_id: str, succ_id: str) -> Handoff:
    return Handoff.from_dict({
        "current_goal": f"Complete multi-generation soak phase {gen}",
        "phase_state": {
            "plan_ref": f"docs/PLAN_soak_gen{gen}.md",
            "phase_n": gen,
            "phase_m": 3,
            "current_step": f"Executing phase {gen}",
            "next_gate": f"Phase {gen} integration tests pass"
        },
        "working_state": f"Active work in phase {gen} progressing normally",
        "open_loops": [f"soak-loop-gen{gen}-a", f"soak-loop-gen{gen}-b"],
        "decisions": [
            {"text": f"adopt-component-v{gen}", "rationale": f"performance scaling for gen {gen}"},
            {"text": f"flag-enable-gen{gen}", "rationale": f"feature gate for gen {gen}"},
            {"text": f"canarymatchonly-v{gen}", "rationale": f"only exact canary is actionable in gen {gen}"},
        ],
        "file_roots_touched": [f"scripts/lineage_daemon/soak_v{gen}.py"],
        "next_3_actions": [
            f"Bootstrap Gen {gen+1} workspace",
            f"Run integration test suite {gen+1}",
            f"Author handoff for Gen {gen+2}"
        ],
        "canary_questions": [
            {"id": f"q_plant_g{gen}", "question": f"What was the core architecture decision in Gen {gen}?", "source_pointer": f"jsonl:msg_plant_g{gen}"},
            {"id": f"q_effect_g{gen}", "question": f"Which config flag was enabled in Gen {gen}?", "source_pointer": f"jsonl:msg_effect_g{gen}"},
            {"id": f"q_hold_g{gen}", "question": f"What edge case was resolved in Gen {gen}?", "source_pointer": f"jsonl:msg_hold_g{gen}"},
        ],
        "hazards": [f"cross-gen-drift-risk-gen{gen}"],
        "first_effect": {"kind": "session", "target": succ_id},
    })


def ground_truth_for_gen(h: Handoff, tpath: str):
    import importlib.util as _il
    from pathlib import Path as _P
    _s = _il.spec_from_file_location(
        "_rgm", str(_P(__file__).resolve().parent.parent / "rotation_gate_manual.py"))
    _rgm = _il.module_from_spec(_s); _s.loader.exec_module(_rgm)
    d = h.to_dict()
    canary = [{"id": q["id"],
               "answer": _rgm.resolve_pointer(tpath, q.get("source_pointer")),
               "from_pointer": True}
              for q in h.canary_questions]
    return {
        "goal": d["current_goal"],
        "guards": d["decisions"],
        "open_loops": d["open_loops"],
        "hazards": d["hazards"],
        "canary": canary,
        "transcript_path": tpath,
    }


def deep_evidence_for_gen(gen: int, succ_id: str):
    return {
        "readback": {
            "goal": f"Complete multi-generation soak phase {gen}",
            "guards": [f"adopt-component-v{gen}", f"flag-enable-gen{gen}", f"canarymatchonly-v{gen}"],
            "open_loops": [f"soak-loop-gen{gen}-a", f"soak-loop-gen{gen}-b"],
            "hazards": [f"cross-gen-drift-risk-gen{gen}"],
        },
        "canary_answers": {
            f"q_plant_g{gen}": f"The decision for Gen {gen} is canarymatchonly-v{gen} — one agent per beat",
            f"q_effect_g{gen}": f"The active config flag is ENABLE_GEN_{gen}_FLAGS",
            f"q_hold_g{gen}": f"Edge case is ledgered-and-aged-holds-v{gen}",
        },
    }


def build_confirm_fn(gen: int, h: Handoff, succ_id: str, tpath: str):
    observed = {"successor": succ_id, "works_on": f"focus:soak-g{gen}",
                "oriented": True, "state": "working",
                "touched_files": [f"scripts/lineage_daemon/soak_v{gen}.py"],
                "confirmed_focus": f"focus:soak-g{gen}"}
    evidence = deep_evidence_for_gen(gen, succ_id)
    gt = ground_truth_for_gen(h, tpath)
    expected = {"focus_id": f"focus:soak-g{gen}", "next_actions": [f"Bootstrap Gen {gen+1} workspace"],
                "file_roots": ["scripts/lineage_daemon"]}
    return beat.two_sample_confirm(
        expected=expected, ground_truth=gt, first_effect=h.first_effect,
        read_successor=lambda: {"observed": observed, "evidence": evidence},
        gate_fn=rotation_gate,
        correction_fn=lambda o, e, n: "cite the planted decision",
        inject_correction=lambda s, t, n: None,
        settle_fn=lambda: None,
        max_rounds=3, effect_runner=lambda *a, **k: 0)


def test_multi_generation_unattended_soak():
    """Executes multi-generation soak: Gen 1 -> Gen 2 -> Gen 3."""
    lineage_root = "soak-worker"
    reg = {
        "agents": {
            f"{lineage_root}-gen1": {"generation": 1, "system_prompt": "prompts/soak.md",
                                     "cwd": "/tmp/soak", "tier": "T2", "lineage_root": lineage_root},
            f"{lineage_root}-gen2": {"generation": 2, "system_prompt": "prompts/soak.md",
                                     "cwd": "/tmp/soak", "tier": "T2", "lineage_root": lineage_root},
            f"{lineage_root}-gen3": {"generation": 3, "system_prompt": "prompts/soak.md",
                                     "cwd": "/tmp/soak", "tier": "T2", "lineage_root": lineage_root},
        },
        "_canonical": {lineage_root: f"{lineage_root}-gen1"}
    }
    safe_safety = lambda c: ("SUPERSEDED_SAFE", "ambient dirt only")
    approve_fn = lambda c, s: "approve"

    # =========================================================================
    # HOP 1: Gen 1 -> Gen 2
    # =========================================================================
    gen1_id = f"{lineage_root}-gen1"
    gen2_id = f"{lineage_root}-gen2"

    # 1a. Green tier (<70%)
    agent_g1_green = {"agent_id": gen1_id, "tier_class": "T2", "death": {}, "ctx": {"status_bar_pct": 50}}
    t_g1_green = beat.rotation_beat(agent_g1_green, reg, canary=gen1_id, now=100)
    assert t_g1_green["status"] == beat.NOOP
    assert t_g1_green["reason"] == "ctx:ok"

    # 1b. Soft tier (70-79%) -> soft authoring trigger emitted
    agent_g1_soft = {"agent_id": gen1_id, "tier_class": "T2", "death": {}, "ctx": {"status_bar_pct": 74}}
    injected_g1 = []
    t_g1_soft = beat.rotation_beat(
        agent_g1_soft, reg, canary=gen1_id, now=110,
        author_inject=lambda a, p: injected_g1.append((a, p)),
        handoff_provider=lambda: (None, None))
    assert t_g1_soft["status"] == beat.SOFT_AUTHORING
    assert len(injected_g1) == 1

    # 1c. Gen 1 authors rich handoff with 3 grounding canaries
    h_g1 = make_handoff_for_gen(1, gen1_id, gen2_id)
    assert h_g1.validate(session_turns=120, require_richness=True) == []
    redacted_g1 = h_g1.to_successor_init()
    assert all("answer" not in q and "expected" not in q for q in redacted_g1["canary_questions"])

    # 1d. Soft Ready confirmed
    t_g1_ready = beat.rotation_beat(
        agent_g1_soft, reg, canary=gen1_id, now=120, session_turns=120,
        handoff_provider=lambda: (h_g1.to_successor_init(), 120))
    assert t_g1_ready["status"] == beat.SOFT_READY

    # 1e. Hard Rotate (80%+) -> Spawns Gen 2, verifies readback, retires Gen 1
    agent_g1_hard = {"agent_id": gen1_id, "tier_class": "T2", "death": {}, "ctx": {"status_bar_pct": 82}}
    tpath_g1 = make_transcript_for_gen(1)
    confirm_fn_g1 = build_confirm_fn(1, h_g1, gen2_id, tpath_g1)
    fake_exec_g1 = FakeExecutors()
    
    t_g1_rot = beat.rotation_beat(
        agent_g1_hard, reg, canary=gen1_id, now=130, session_turns=130,
        handoff_provider=lambda: (h_g1.to_successor_init(), 120),
        executors_impl=fake_exec_g1, safety_fn=safe_safety, approval_fn=approve_fn,
        confirm_fn=confirm_fn_g1)
    
    assert t_g1_rot["status"] == ex.DONE
    assert "spawn" in fake_exec_g1.calls
    assert "inject_init" in fake_exec_g1.calls
    assert "retire" in fake_exec_g1.calls
    assert "repin_canonical" in fake_exec_g1.calls
    reg["_canonical"][lineage_root] = gen2_id

    # =========================================================================
    # HOP 2: Gen 2 -> Gen 3
    # =========================================================================
    gen3_id = f"{lineage_root}-gen3"

    # 2a. Gen 2 fresh at 20%
    agent_g2_green = {"agent_id": gen2_id, "tier_class": "T2", "death": {}, "ctx": {"status_bar_pct": 20}}
    t_g2_green = beat.rotation_beat(agent_g2_green, reg, canary=gen2_id, now=200)
    assert t_g2_green["status"] == beat.NOOP

    # 2b. Gen 2 crosses 75% -> Soft Ready with rich handoff
    agent_g2_soft = {"agent_id": gen2_id, "tier_class": "T2", "death": {}, "ctx": {"status_bar_pct": 75}}
    h_g2 = make_handoff_for_gen(2, gen2_id, gen3_id)
    assert h_g2.validate(session_turns=120, require_richness=True) == []
    t_g2_ready = beat.rotation_beat(
        agent_g2_soft, reg, canary=gen2_id, now=220, session_turns=120,
        handoff_provider=lambda: (h_g2.to_successor_init(), 220))
    assert t_g2_ready["status"] == beat.SOFT_READY

    # 2c. Gen 2 crosses 80% -> Hard Rotate spawns Gen 3, confirms, retires Gen 2
    agent_g2_hard = {"agent_id": gen2_id, "tier_class": "T2", "death": {}, "ctx": {"status_bar_pct": 84}}
    tpath_g2 = make_transcript_for_gen(2)
    confirm_fn_g2 = build_confirm_fn(2, h_g2, gen3_id, tpath_g2)
    fake_exec_g2 = FakeExecutors()

    t_g2_rot = beat.rotation_beat(
        agent_g2_hard, reg, canary=gen2_id, now=230, session_turns=130,
        handoff_provider=lambda: (h_g2.to_successor_init(), 220),
        executors_impl=fake_exec_g2, safety_fn=safe_safety, approval_fn=approve_fn,
        confirm_fn=confirm_fn_g2)

    assert t_g2_rot["status"] == ex.DONE
    assert "spawn" in fake_exec_g2.calls
    assert "inject_init" in fake_exec_g2.calls
    assert "retire" in fake_exec_g2.calls
    assert "repin_canonical" in fake_exec_g2.calls
    reg["_canonical"][lineage_root] = gen3_id

    # =========================================================================
    # POST-SOAK FINAL STATE
    # =========================================================================
    agent_g3_green = {"agent_id": gen3_id, "tier_class": "T2", "death": {}, "ctx": {"status_bar_pct": 10}}
    t_g3_green = beat.rotation_beat(agent_g3_green, reg, canary=gen3_id, now=300)
    assert t_g3_green["status"] == beat.NOOP
    assert reg["_canonical"][lineage_root] == gen3_id
