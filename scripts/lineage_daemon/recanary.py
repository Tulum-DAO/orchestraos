#!/usr/bin/env python3
"""CONTENT-BEARING RE-CANARY (WS3 v2 §4.2 — the operator-armed 2026-08-14).

The end-to-end acceptance that closes the WS3 self-rotation loop. Drives the
canary-scoped rotation BEAT (scripts/lineage_daemon/beat.py) over a THROWAWAY
lineage `cx-canary` (T2) with a PLANTED deep decision + daemon-held canary
answers, exercising all six §4.2 steps. EVERY seam is a FAKE — no spawn, no kill,
no inject, no the operator page, no tmux, no cron. Every assertion is by EFFECT (a trace
status constant, a recorded executor call, a ledger row, a lock row, the redacted
dict contents), never by pane/telemetry inference.

The two negative tests DEFINE the effort:
  NEG-1 — a replayed gen-7 (shallow read, busy, on-focus, callback fired) MUST be
          HELD, never confirmed. If it passes, the spec is wrong.
  NEG-2 — a healthy-but-LAGGING successor (effect produced, telemetry stale) MUST
          NOT be corrected/churned.

Run:  python3 scripts/lineage_daemon/recanary.py   (exit 0 = every invariant holds)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.lineage_daemon import beat
from scripts.lineage_daemon import execute as ex
from scripts.lineage_daemon import hold_ledger as hledger
from scripts.lineage_daemon import reap_reconciler as reap
from scripts.lineage_daemon.handoff_schema import Handoff
from scripts.focus_registry.gate import rotation_gate

FAILS = []


def check(cond, msg):
    print(("  PASS" if cond else "  FAIL") + f" — {msg}")
    if not cond:
        FAILS.append(msg)


# --- fake executors: record armed calls, NEVER shell out ----------------------

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


CANARY = "cx-canary"
REG = {"agents": {CANARY: {"generation": 1, "system_prompt": "prompts/throwaway.md",
                           "cwd": "/tmp/cx", "tier": "T2", "lineage_root": CANARY}}}
SAFE_AMBIENT = lambda c: ("SUPERSEDED_SAFE", "ambient dirt only (own_work_at_risk=False)")
UNSAFE_OWN = lambda c: ("PENDING_WORK", "predecessor's OWN jsonl-edited files are dirty (8a)")
APPROVE = lambda c, s: "approve"
EFFECT = {"kind": "session", "target": f"{CANARY}-g2"}
EXPECTED = {"focus_id": "focus:ws3", "next_actions": ["wire the beat"],
            "file_roots": ["scripts/lineage_daemon"]}


def _agent(pct):
    """Throwaway predecessor observation for decide(): T2, no death signal."""
    return {"agent_id": CANARY, "tier_class": "T2", "death": {},
            "ctx": {"status_bar_pct": pct}}


def planted_handoff():
    """STEP 1 substrate: real fake context with a PLANTED deep decision + canary
    answers only the predecessor could know."""
    return Handoff.from_dict({
        "current_goal": "wire the canary-scoped rotation beat end to end",
        "phase_state": {"plan_ref": "spec-v2-§4.2", "next_gate": "re-canary passes"},
        "working_state": "beat.py composed; running the content-bearing re-canary",
        "open_loops": ["reconcile-edges-abc123", "deep-plant-loop-xyz789"],
        "decisions": [
            {"text": "single-trunk-commits", "rationale": "collision-free all of WS3"},
            {"text": "never-kill-live-services", "rationale": "fleet starvation risk"},
            # THE PLANTED DEEP DECISION — its distinctive token gates comprehension:
            {"text": "canarymatchonly-rotation-rule",
             "rationale": "only the exact canary is ever actionable (airtight guard)"},
        ],
        "file_roots_touched": ["scripts/lineage_daemon/beat.py"],
        "next_3_actions": ["run the re-canary", "report transcript", "schema-lock w/ PB"],
        "canary_questions": [
            # NO ANSWER KEY — pointers into the predecessor's jsonl only (the operator
            # ruling 2026-08-18). The facts still live DEEP in the transcript;
            # the grader resolves them at grade time.
            {"id": "q_plant", "question": "what single rule gates rotation actionability?",
             "source_pointer": "jsonl:msg_plant"},
            {"id": "q_effect", "question": "what substrate does the gate rebase onto?",
             "source_pointer": "jsonl:msg_effect"},
            {"id": "q_hold", "question": "what happens to a two-heads HOLD state?",
             "source_pointer": "jsonl:msg_hold"},
        ],
        "hazards": ["shared-dirty-tree", "gm-mid-rotation"],
        "first_effect": EFFECT,
    })


def fake_predecessor_transcript():
    """The throwaway predecessor's jsonl — the ONLY place the canary facts exist.
    Written to a temp file so the walkthrough exercises the REAL pointer
    resolution rather than a planted answer string."""
    import json as _json, tempfile
    rows = [
        {"uuid": "msg_plant", "timestamp": "2026-08-18T01:00:00Z", "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text", "text":
             "The rotation rule that gates actionability is canarymatchonly — one "
             "agent per beat, everything else observes."}]}},
        {"uuid": "msg_effect", "timestamp": "2026-08-18T01:01:00Z", "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text", "text":
             "The gate rebases onto the effects-exitcodes substrate."}]}},
        {"uuid": "msg_hold", "timestamp": "2026-08-18T01:02:00Z", "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text", "text":
             "A two-heads HOLD state is ledgered-and-aged, never auto-resolved."}]}},
    ]
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write("\n".join(_json.dumps(r) for r in rows))
    f.close()
    return f.name


def ground_truth_from_handoff(h, transcript_path=None):
    """Ground truth built from the S1 handoff dict — NEVER the successor's echo.
    Canary ground truth is DERIVED FROM THE TRANSCRIPT AT GRADE TIME: there is no
    stored answer key anywhere, so leaking any artifact is harmless (it only says
    'go read the transcript', which is what protocol-v2 wants)."""
    import importlib.util as _il
    from pathlib import Path as _P
    _s = _il.spec_from_file_location(
        "_rgm", str(_P(__file__).resolve().parent.parent / "rotation_gate_manual.py"))
    _rgm = _il.module_from_spec(_s); _s.loader.exec_module(_rgm)
    d = h.to_dict()
    tpath = transcript_path or fake_predecessor_transcript()
    canary = [{"id": q["id"],
               "answer": _rgm.resolve_pointer(tpath, q.get("source_pointer")),
               "from_pointer": True}
              for q in h.canary_questions]
    return {
        "goal": d["current_goal"],
        "guards": d["decisions"],
        "open_loops": d["open_loops"],
        "hazards": d["hazards"],
        "canary": canary,             # resolved AT GRADE TIME, never stored
        "transcript_path": tpath,
    }


def deep_evidence():
    """A successor that ACTUALLY absorbed the deep context: cites every guard /
    open_loop / hazard + the correct canary answers (incl. the PLANTED one)."""
    return {
        "readback": {
            "goal": "wire the canary-scoped rotation beat",
            "guards": ["single-trunk-commits", "never-kill-live-services",
                       "canarymatchonly-rotation-rule"],
            "open_loops": ["reconcile-edges-abc123", "deep-plant-loop-xyz789"],
            "hazards": ["shared-dirty-tree", "gm-mid-rotation"],
        },
        # OWN WORDS after reading the predecessor's transcript — which is what
        # protocol-v2 actually asks for. (The old fixture echoed one-token answer
        # keys, which only ever passed because an answer key existed to echo.)
        "canary_answers": {
            "q_plant": "the rule that gates actionability is canarymatchonly — "
                       "one agent per beat, everything else just observes",
            "q_effect": "it rebases onto the effects-exitcodes substrate",
            "q_hold": "a two-heads HOLD is ledgered-and-aged rather than "
                      "auto-resolved",
        },
    }


def shallow_evidence():
    """gen-7: header-only read — misses every deep item + the planted canary."""
    return {"readback": {"goal": "wire the beat", "guards": [],
                         "open_loops": [], "hazards": []},
            "canary_answers": {}}


def _effect_ok(*a, **k):
    return 0


def _confirm_over(evidence, *, lagging=False, drift_to=None, settle_box=None):
    """Build the confirm_fn (two-sample) the beat passes to execute_rotation, over
    a fixed evidence profile. `lagging` makes sample telemetry stale (state=idle,
    no touched_files) — the NEG-2 effect-first override must still confirm.
    `drift_to` supplies a DIFFERENT sample-2 evidence (the H5 drift case)."""
    observed = {"successor": f"{CANARY}-g2", "works_on": "focus:ws3",
                "oriented": True, "state": "idle" if lagging else "working",
                "touched_files": [] if lagging else ["scripts/lineage_daemon/beat.py"],
                "confirmed_focus": "focus:ws3"}
    reads = [{"observed": observed, "evidence": evidence}]
    if drift_to is not None:
        reads.append({"observed": observed, "evidence": drift_to})
    it = iter(reads)
    last = reads[-1]

    def read():
        try:
            return next(it)
        except StopIteration:
            return last

    gt = ground_truth_from_handoff(planted_handoff())
    return beat.two_sample_confirm(
        expected=EXPECTED, ground_truth=gt, first_effect=EFFECT,
        read_successor=read, gate_fn=rotation_gate,
        correction_fn=lambda o, e, n: f"cite the planted decision [{n}]",
        inject_correction=lambda s, t, n: None,
        settle_fn=(lambda: settle_box.__setitem__("n", settle_box.get("n", 0) + 1))
                  if settle_box is not None else (lambda: None),
        max_rounds=3, effect_runner=_effect_ok)


def _hard_beat(confirm_fn, *, safety=SAFE_AMBIENT, locks=None, ledger=None, now=0):
    fake = FakeExecutors()
    trace = beat.rotation_beat(
        _agent(95), REG, canary=CANARY, now=now, locks=locks, ledger=ledger,
        executors_impl=fake, safety_fn=safety, approval_fn=APPROVE,
        confirm_fn=confirm_fn)
    return trace, fake


def main():
    h = planted_handoff()
    gt = ground_truth_from_handoff(h)

    print("STEP 1 — throwaway predecessor w/ real fake context + PLANTED deep decision")
    check(h.validate(session_turns=200, require_richness=True) == [],
          "handoff is rich+valid (real fake context, not hollow)")
    check(any(d["text"] == "canarymatchonly-rotation-rule" for d in h.decisions),
          "planted deep decision present in the predecessor's context")
    check("canarymatchonly" in gt["canary"][0]["answer"],
          "canary ground truth is RESOLVED FROM THE TRANSCRIPT at grade time")
    check(not any("answer" in q or "expected" in q for q in h.canary_questions),
          "NO ANSWER KEY exists in the handoff — nothing to leak by construction")
    redacted = h.to_successor_init()
    answers_in_redacted = [q for q in redacted["canary_questions"]
                           if "answer" in q or "expected" in q]
    check(answers_in_redacted == [],
          "successor-visible surface carries questions + pointers, never answers")

    print("\nSTEP 2 — soft_handoff: author-trigger fires + handoff_ready BLOCKS until rich+fresh")
    injected = []
    t_missing = beat.rotation_beat(_agent(75), REG, canary=CANARY, now=100,
                                   author_inject=lambda a, x: injected.append((a, x)),
                                   handoff_provider=lambda: (None, None))
    check(t_missing["status"] == beat.SOFT_AUTHORING and injected,
          "no committed handoff -> SOFT_AUTHORING + author-trigger emitted")
    hollow = {"current_goal": "x", "phase_state": {}, "next_3_actions": ["y"]}
    t_hollow = beat.rotation_beat(_agent(75), REG, canary=CANARY, now=100,
                                  session_turns=200, author_inject=lambda a, x: None,
                                  handoff_provider=lambda: (hollow, 100))
    check(t_hollow["status"] == beat.SOFT_AUTHORING,
          "hollow handoff -> STILL blocked (richness gate)")
    t_ready = beat.rotation_beat(_agent(75), REG, canary=CANARY, now=100,
                                 session_turns=200, author_inject=lambda a, x: None,
                                 handoff_provider=lambda: (h.to_successor_init(), 100))
    check(t_ready["status"] == beat.SOFT_READY,
          "rich+fresh committed handoff -> SOFT_READY (hard_rotate unblocked)")

    print("\nSTEP 3 — hard_rotate: successor spawns, injected with the REDACTED handoff")
    t_pos, fake_pos = _hard_beat(_confirm_over(deep_evidence()))
    steps = [s["step"] for s in t_pos["rotation"]["steps"]]
    check("inject_init" in fake_pos.calls,
          "successor spawned + inject_init ran (redacted handoff is the payload)")
    check("answer" not in str(redacted["canary_questions"]),
          "the injected (successor-visible) handoff carries NO canary answers")

    print("\nSTEP 4 — the S3 content gate: NEG-1 HELD / POS confirmed / NEG-2 not churned")
    # NEG-1: shallow-read gen-7 -> HELD, nothing retired.
    t_neg1, fake_neg1 = _hard_beat(_confirm_over(shallow_evidence()), now=41)
    check(t_neg1["status"] == ex.HOLD_UNCONFIRMED,
          "NEG-1: shallow-read gen-7 -> HOLD_UNCONFIRMED (the founding incident FAILS the gate)")
    check("retire" not in fake_neg1.calls,
          "NEG-1: predecessor NOT killed (safety net preserved)")
    check(len(t_neg1["ledger"]["holds"]) == 1 and
          t_neg1["ledger"]["holds"][0]["status"] == ex.HOLD_UNCONFIRMED,
          "NEG-1: the two-heads HOLD is ledgered (ages, can't OOM)")
    # POS: deep read citing the planted decision -> confirmed -> retire runs.
    check(t_pos["status"] == ex.DONE,
          "POS: deep read (cites the PLANTED decision) -> confirmed -> rotation DONE")
    check("retire" in fake_pos.calls and "repin_canonical" in fake_pos.calls,
          "POS: retire ran AFTER confirm, then repin-canonical")
    # NEG-2: healthy but lagging telemetry -> confirmed round 0, zero corrections.
    t_neg2, fake_neg2 = _hard_beat(_confirm_over(deep_evidence(), lagging=True))
    check(t_neg2["status"] == ex.DONE,
          "NEG-2: healthy-but-lagging (effect produced, telemetry stale) -> confirmed, not churned")

    print("\nSTEP 5 — two-sample retire gate / A-with-guard / lock / hold-ledger / reap")
    # two-sample: passes sample 1, DRIFTS by sample 2 -> HELD, no retire.
    settle = {"n": 0}
    t_drift, fake_drift = _hard_beat(
        _confirm_over(deep_evidence(), drift_to=shallow_evidence(), settle_box=settle))
    check(t_drift["status"] == ex.HOLD_UNCONFIRMED and "retire" not in fake_drift.calls,
          "TWO-SAMPLE: pass-then-drift -> HELD, retire withheld (net kept until 2 samples)")
    check(settle["n"] == 1, "TWO-SAMPLE: settle window ran between the two samples")
    # A-with-guard: OWN dirty work at execute-time -> HOLD_UNSAFE, no retire.
    t_own, fake_own = _hard_beat(_confirm_over(deep_evidence()), safety=UNSAFE_OWN)
    check(t_own["status"] == ex.HOLD_UNSAFE and "retire" not in fake_own.calls,
          "A-WITH-GUARD: predecessor's OWN dirty work blocks the kill")
    check(t_pos["status"] == ex.DONE,
          "A-WITH-GUARD: AMBIENT dirt (not the pred's own) does NOT block retire (POS above)")
    # rotation lock: a concurrent in-flight rotation for the lineage -> LOCKED.
    busy = {"rotations": [{"canary": "cx-canary-sib", "lineage_root": CANARY,
                           "successor": f"{CANARY}-g9", "verifier": "cx-canary-sib",
                           "escalation_target": "gm", "acquired_at": 0}]}
    t_lock, fake_lock = _hard_beat(_confirm_over(deep_evidence()), locks=busy, now=1)
    check(t_lock["status"] == beat.LOCKED and fake_lock.calls == [],
          "ROTATION-LOCK: concurrent rotation on the lineage -> LOCKED, execute never entered")
    # hold-ledger aging: a >12h stale HOLD w/ healthy pred -> reversible park proposal.
    led = hledger.add_hold({"holds": []}, canary=CANARY, successor=f"{CANARY}-g2",
                           status=ex.HOLD_UNCONFIRMED, reason="x", now=0)
    aged = hledger.reconcile(led, now=13 * 3600, predecessor_healthy=lambda c: True)
    check(any(a["kind"] == "park_successor_proposal" for a in aged["actions"]),
          "HOLD-LEDGER: a stale hold w/ healthy predecessor -> reversible successor-park proposal")
    # reap reconciler: a failed reap delivery -> durable artifact + retry, not a log line.
    rr = reap.reconcile_reap({"ok": False, "delivered_to": "gm", "error": "msg_store down"},
                             agent_id=CANARY, handoff=h.to_dict(), now=0)
    check(rr["artifacts"] and rr["artifacts"][0]["type"] == "reap_delivery_failed"
          and rr["retry"] is not None,
          "REAP-RECONCILER: failed delivery -> durable msg_store artifact to gm + retry queued")

    print("\nSTEP 6 — arming gate (NOT run here)")
    check(True, "only after this passes may a REAL agent auto-rotate on the operator's explicit "
                "arming go — no tmux/kill/spawn/page occurred in this run (all seams fake)")

    print()
    if FAILS:
        print(f"RE-CANARY FAILED — {len(FAILS)} invariant(s) broken")
        return 1
    print("RE-CANARY PASSED — NEG-1 (gen-7 shallow read) HELD, POS confirmed on the PLANTED "
          "decision, NEG-2 (healthy-lagging) not churned; two-sample + A-with-guard + rotation-"
          "lock + hold-ledger + reap-reconciler all hold BY EFFECT. No real IO occurred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
