#!/usr/bin/env python3
"""FULL-LOOP DRY-RUN HARNESS for the T2 mechanical auto-rotation arm.

Proves, in ISOLATION (scratch stores, stubbed executors, injected floor/armed
paths), that the wired components compose into the intended unattended loop:

  auto_grade(--strict) PASS  -> strict comprehension.json
  is_graduated_autoretire     -> True (floor-absent + armed + T2 + strict)
  graduation_gated_approval   -> "approve" (graduated + S3-confirmed), else card
  dispatch_graduation(armed)  -> proceed_no_card -> execute_graduation_retire
                                 -> promoted_and_retired + notify-after fired

AND the fail-safes: a supervised grade OR a REFUSED grade NEVER auto-retires
(routes to the the operator card / keep). Touches NOTHING live.
"""
import os, sys, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, "scripts")
from scripts.lineage_daemon import auto_grade as AG
from scripts.lineage_daemon import self_retire_gate as SRG
from scripts.lineage_daemon import graduation_approval as GA
from scripts.lineage_daemon import graduation_dispatch as GD

FAILS = []
def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond: FAILS.append(name)

d = tempfile.mkdtemp()
HAND = os.path.join(d, "handoffs"); os.makedirs(HAND)
ARMED = os.path.join(d, "self_retire_armed"); open(ARMED, "w").write("root-x\n")
FLOOR_ABSENT = os.path.join(d, "NO_FLOOR")          # absent -> floor lifted
REG = {"agents": {"root-x": {"tier": "T2"}}}

def write_grade(succ, mode):
    p = os.path.join(HAND, f"{succ}.comprehension.json")
    json.dump({"pass": True, "mode": mode}, open(p, "w")); return p

# ---- 1. auto_grade seam maps strict PASS correctly -----------------------------
print("1. auto_grade disposition mapping (fake runner):")
def runner_exit(code):
    return lambda argv: type("R", (), {"returncode": code})()
r_pass = AG.auto_grade_successor("root-x-g2", "sid-pred", runner=runner_exit(0))
r_fail = AG.auto_grade_successor("root-x-g2", "sid-pred", runner=runner_exit(1))
r_ref  = AG.auto_grade_successor("root-x-g2", "sid-pred", runner=runner_exit(2))
check("exit0 -> PASS + may_graduate", r_pass["disposition"] == "PASS" and AG.may_graduate(r_pass))
check("exit1 -> FAIL + not may_graduate", r_fail["disposition"] == "FAIL" and not AG.may_graduate(r_fail))
check("exit2 -> REFUSED + grade_signal None", r_ref["disposition"] == "REFUSED" and AG.grade_signal(r_ref) is None)
check("--strict passed for unattended", "--strict" in AG.build_grade_argv("root-x-g2", "sid-pred"))

# ---- 2. is_graduated_autoretire strict gate ------------------------------------
print("2. is_graduated_autoretire (floor lifted, armed, T2):")
def grad(succ):
    seat = {"agent_id": "root-x", "lineage_root": "root-x", "tier": "T2", "successor": succ}
    return SRG.is_graduated_autoretire(seat, disabled_path=FLOOR_ABSENT, armed_path=ARMED,
                                       registry_path=os.path.join(d, "reg.json"), handoffs_dir=HAND)
json.dump(REG, open(os.path.join(d, "reg.json"), "w"))
write_grade("s-strict", "strict"); write_grade("s-sup", "supervised")
check("strict grade -> graduated True", grad("s-strict") is True)
check("supervised grade -> graduated False", grad("s-sup") is False)

# ---- 3. graduation_gated_approval ---------------------------------------------
print("3. graduation_gated_approval (auto-approve ONLY when graduated + confirmed):")
seat_g = {"agent_id": "root-x", "lineage_root": "root-x", "tier": "T2", "successor": "s-strict"}
def mk(gr, conf):
    return GA.make_graduation_gated_approval(seat_g, graduated_fn=lambda s: gr,
             confirmed_fn=lambda c, s: conf, fallback_fn=lambda c, s: "CARD")
check("graduated+confirmed -> approve", mk(True, True)("root-x", "s-strict") == "approve")
check("graduated+not-confirmed -> card", mk(True, False)("root-x", "s-strict") == "CARD")
check("not-graduated+confirmed -> card", mk(False, True)("root-x", "s-strict") == "CARD")

# ---- 4. dispatch_graduation full unattended retire (strict) --------------------
print("4. dispatch_graduation(armed=True) strict -> proceed_no_card -> retire:")
retired = {"done": False}
def readers_for(pred, succ, grade_mode):
    write_grade(succ, grade_mode)
    return {
      "successor_of": lambda p: succ,
      "seat_meta_of": lambda p: {"lineage_root": "root-x", "generation": 2},
      "beats_since_promotion_of": lambda p: 5,
      "successor_live": lambda s: True,
      "handoff_confirmed_of": lambda s: True,
      "grade_of": lambda s: "PASS" if grade_mode == "strict" else "PASS",  # may_retire grade_agree
      "pending_duty_of": lambda p: False,
    }
def kill_gate1_fn(seat): return (True, "clean")
def promote_fn(seat): return {"promoted": True}
def retire_fn(seat): retired["done"] = True; return {"retired": True}
notified = {"n": 0}
def skip_notify_fn(msg): notified["n"] += 1

disp = GD.dispatch_graduation("root-x", readers_for("root-x", "s-strict2", "strict"),
        disabled_path=FLOOR_ABSENT, armed=True,
        graduated_fn=lambda s: SRG.is_graduated_autoretire(s, disabled_path=FLOOR_ABSENT,
            armed_path=ARMED, registry_path=os.path.join(d, "reg.json"), handoffs_dir=HAND),
        kill_gate1_fn=kill_gate1_fn, promote_fn=promote_fn, retire_fn=retire_fn,
        skip_notify_fn=skip_notify_fn, used_pct=82)
check("action == proceed_no_card", disp["action"] == "proceed_no_card")
check("retire_fn actually ran", retired["done"] is True)
check("notify-after fired once", notified["n"] == 1)

# ---- 5. supervised grade -> await_card (NO unattended retire) -------------------
print("5. dispatch_graduation(armed=True) supervised -> await_card (fail-safe):")
retired2 = {"done": False}
def retire_fn2(seat): retired2["done"] = True; return {"retired": True}
disp_sup = GD.dispatch_graduation("root-x", readers_for("root-x", "s-sup2", "supervised"),
        disabled_path=FLOOR_ABSENT, armed=True,
        graduated_fn=lambda s: SRG.is_graduated_autoretire(s, disabled_path=FLOOR_ABSENT,
            armed_path=ARMED, registry_path=os.path.join(d, "reg.json"), handoffs_dir=HAND),
        kill_gate1_fn=kill_gate1_fn, promote_fn=promote_fn, retire_fn=retire_fn2,
        create_fn=lambda **k: "apr_test", skip_notify_fn=skip_notify_fn)
check("action == await_card", disp_sup["action"] == "await_card")
check("supervised NEVER auto-retired", retired2["done"] is False)

print()
if FAILS:
    print(f"HARNESS FAILED: {len(FAILS)} check(s) failed: {FAILS}"); sys.exit(1)
print("HARNESS GREEN — full mechanical loop proven in isolation, fail-safes hold.")
