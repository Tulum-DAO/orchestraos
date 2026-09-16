#!/usr/bin/env python3
"""FULL SYNCHRONOUS LOOP harness — drives execute_rotation end-to-end
(spawn -> S3 confirm -> auto-grade -> graduation-gated auto-approve -> retire)
with fake executors + REAL grade/approval/gate logic. Proves detect->...->retire
runs with NO human for a strict-graded+confirmed+armed T2, and HOLDS otherwise."""
import os, sys, json, tempfile, types
sys.path.insert(0, "scripts"); sys.path.insert(0, ".")
from scripts.lineage_daemon import execute as EX
from scripts.lineage_daemon import auto_grade as AG
from scripts.lineage_daemon import graduation_approval as GA
from scripts.lineage_daemon import self_retire_gate as SRG

FAILS=[]
def ck(n,c): print(f"  [{'PASS' if c else 'FAIL'}] {n}"); (FAILS.append(n) if not c else None)

def fake_ex(retired):
    ns = types.SimpleNamespace()
    ns.plan_register_successor = lambda *a, **k: {"executed": True}
    ns.plan_spawn = lambda *a, **k: {"executed": True}
    ns.verify_successor = lambda *a, **k: {"executed": True, "alive": True}
    ns.plan_inject_init = lambda *a, **k: {"executed": True}
    ns.plan_wire_edge = lambda *a, **k: {"executed": True}
    ns.plan_verify_edge = lambda *a, **k: {"executed": True, "ok": True}
    ns.plan_retire = lambda c, **k: (retired.append(c), {"executed": True})[1]
    ns.plan_repin_canonical = lambda *a, **k: {"executed": True}
    return ns

def run(mode, confirmed=True):
    d = tempfile.mkdtemp(); hand = os.path.join(d, "hand"); os.makedirs(hand)
    armed = os.path.join(d, "armed"); open(armed, "w").write("root\n")
    floor = os.path.join(d, "NOFLOOR")
    reg = {"agents": {"root": {"tier": "T2", "lineage_root": "root", "generation": 1}}}
    regp = os.path.join(d, "reg.json"); json.dump(reg, open(regp, "w"))
    retired = []
    # grade_fn: runs REAL auto_grade (fake runner exit0=PASS unless mode says else), writes strict artifact
    def grade_fn(canary, successor):
        code = {"strict": 0, "fail": 1, "refused": 2}[mode if mode in ("strict","fail","refused") else "strict"]
        res = AG.auto_grade_successor(successor, "sid-pred", runner=lambda argv: types.SimpleNamespace(returncode=code))
        # emulate the grader writing the artifact for PASS (strict) / supervised
        if res["disposition"] == "PASS":
            json.dump({"pass": True, "mode": ("supervised" if mode=="supervised" else "strict")},
                      open(os.path.join(hand, f"{successor}.comprehension.json"), "w"))
        return res
    def graduated(seat):
        return SRG.is_graduated_autoretire(seat, disabled_path=floor, armed_path=armed,
                                           registry_path=regp, handoffs_dir=hand)
    approval = GA.make_graduation_gated_approval(
        {"agent_id": "root", "lineage_root": "root", "tier": "T2", "successor": "root-g2"},
        graduated_fn=graduated, confirmed_fn=lambda c, s: confirmed, fallback_fn=lambda c, s: "CARD")
    trace = EX.execute_rotation(
        "root", reg, executors_impl=fake_ex(retired),
        confirm_fn=lambda c, s: {"outcome": "confirmed" if confirmed else "held"},
        grade_fn=grade_fn, safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        approval_fn=approval, successor_live_fn=lambda s: False)
    steps = [s["step"] for s in trace["steps"]]
    return trace["status"], steps, retired

print("FULL detect->spawn->confirm->grade->approve->retire (fake executors, real logic):")
st, steps, retired = run("strict")
ck("strict+confirmed+armed -> status rotated (full auto retire)", st == EX.DONE)
ck("auto_grade ran BEFORE approval", steps.index("auto_grade") < steps.index("approval"))
ck("retire executed on predecessor", retired == ["root"])

st2, _, r2 = run("fail")
ck("grade FAIL -> HOLD_GRADE, no retire", st2 == EX.HOLD_GRADE and r2 == [])
st3, _, r3 = run("refused")
ck("grade REFUSED -> HOLD_GRADE, no retire", st3 == EX.HOLD_GRADE and r3 == [])
st4, s4, r4 = run("supervised")
ck("supervised grade -> approval falls to CARD, no auto-retire", st4 != EX.DONE and r4 == [])
st5, s5, r5 = run("strict", confirmed=False)
ck("S3 not confirmed -> HOLD before grade, no retire", st5 == EX.HOLD_UNCONFIRMED and r5 == [])

print()
print("  " + ("FULL-BEAT HARNESS GREEN — the entire loop runs with no human for a graduated T2; every non-strict/unconfirmed path HOLDS."
      if not FAILS else f"FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
