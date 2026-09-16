"""lineage_gate CLI — grade / explain / regrade. SHADOW-FIRST.

Kill switch (no deploy): ~/runtime/LINEAGE_GATE_DISABLED. Arming = gm + the operator.
Failure path per spec §7 + 7.4-RESOLUTION (the operator Q5, 2026-08-18): a failing
grade BLOCKS rotation, returns the exact failing gates, and after 3 attempts
or on request escalates to gm + the operator and WAITS. There is NO override branch —
deliberately no code path for one, ever; a failing lineage is quarantined by
Amendment 7.2 and the wait is the feature.

Usage:
  python3 -m lineage_gate.cli grade --handoff H.md --canary C.json \
      --jsonl J.jsonl --agent orchestra-builder --successor x-g11 \
      [--now EPOCH --session-start EPOCH]
  python3 -m lineage_gate.cli regrade --record R.grade.json ...same inputs...
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from .artifact import canonical_json
    from . import grade as G
    from . import shadow as SH
except ImportError:
    # file-form invocation (`python3 scripts/lineage_gate/cli.py`) has no
    # parent package — an operator WILL type this form (gm did, msg_db2a4014).
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lineage_gate.artifact import canonical_json          # noqa: F401
    from lineage_gate import grade as G                       # noqa: F401
    from lineage_gate import shadow as SH                     # noqa: F401

ORCH = os.path.expanduser("~/scripts/agent-orchestra")
KILL_FILE = os.path.expanduser("~/runtime/LINEAGE_GATE_DISABLED")


def _epoch_or_iso(v: str) -> int:
    if v.isdigit():
        return int(v)
    from datetime import datetime, timezone
    s = v[:-1] if v.endswith("Z") else v
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"{v!r}: not epoch seconds or ISO-8601") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _resolver():
    spec = importlib.util.spec_from_file_location(
        "RG", os.path.join(ORCH, "scripts", "rotation_gate_manual.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.resolve_pointer


def _leak_lint(handoff, canary):
    """Run the calibrated leak lint; its CALIBRATION rides in the result so the
    grade artifact can tell 'clean' from 'blind' months later (spec A4)."""
    r = subprocess.run([sys.executable,
                        os.path.join(ORCH, "scripts", "canary_leak_lint.py"),
                        handoff, canary], capture_output=True, text=True)
    clean = "0 leaking" in (r.stdout + r.stderr)
    return {"leaking": 0 if clean else 1,
            "calibration_passed": r.returncode in (0, 1),
            "raw": (r.stdout + r.stderr).strip()[:200]}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lineage-gate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # key1 — the Key-1 counter as a VERB (gm msg_e2bcc05f, the operator-found: the
    # count had no instrument and sat at 0 rows through three rotations)
    k = sub.add_parser("key1", help="Key-1 consecutive-agreement status")
    k.add_argument("--check", action="store_true",
                   help="exit 0 when the arming ask is due (5/5), 1 otherwise "
                        "— for a cron/gate; it never arms anything")
    k.add_argument("--record", default=None,
                   help="append a raw row: JSON object with kind/rotation/...")
    # the row-SHAPE verb (gm gen-14 Order 2): a supervisor must not be able to
    # mis-shape a Key-1 row, and must not be able to write a COUNTING row
    # without both verdicts.
    k.add_argument("--rotation", default=None,
                   help="record a Key-1 datum, e.g. 'gm gen-14 <- gen-15'")
    k.add_argument("--grader-verdict", default=None, help="PASS|FAIL")
    k.add_argument("--supervisor-verdict", default=None, help="PROMOTE|BLOCK")
    k.add_argument("--author", default=None,
                   help="who AUTHORED the handoff/canaries being graded")
    k.add_argument("--supervisor", default=None,
                   help="who RAN the grade; if it equals --author the row is "
                        "marked asterisked and a --disclosure is required")
    k.add_argument("--disclosure", default=None,
                   help="what was not independent (required when author == "
                        "supervisor)")
    k.add_argument("--grade-record", default=None)
    k.add_argument("--grader-commit", default=None)
    k.add_argument("--failed-gate", action="append", default=None)
    for name in ("grade", "regrade"):
        p = sub.add_parser(name)
        p.add_argument("--handoff", required=True)
        p.add_argument("--canary", required=True)
        p.add_argument("--jsonl", required=True)
        p.add_argument("--agent", required=True)
        p.add_argument("--successor", required=True)
        p.add_argument("--session-start", type=_epoch_or_iso, required=True,
                       help="epoch seconds OR ISO-8601 (e.g. "
                            "2026-08-18T14:03:56Z) — operator-friendly both "
                            "ways (gm msg_db2a4014)")
        p.add_argument("--now", type=_epoch_or_iso, default=None,
                       help="injected time (epoch or ISO); defaults to current "
                            "epoch AT INVOCATION and is frozen into the record")
        if name == "grade":
            p.add_argument("--shadow", action="store_true",
                           help="blinding for Key-1 shadow rotations (gm ruling "
                                "msg_0ab0a80e): print ONLY the verdict + record "
                                "path, suppressing gate detail at STDOUT so the "
                                "supervisor's manual grade cannot be biased by "
                                "the grader's reasoning. NON-MUTATING by bind: "
                                "the frozen record is byte-identical with or "
                                "without this flag (tested); detail stays in "
                                "the record for post-verdict reading")
        if name == "grade":
            p.add_argument("--record-out", default=None,
                           help="override the record OUTPUT PATH (calibration "
                                "runs must never overwrite a historical "
                                "record — the g11 grade.json is frozen "
                                "evidence, DEC-1787085269 calibration bind)")
        if name == "regrade":
            p.add_argument("--record", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "key1":
        if args.rotation:
            try:
                row = SH.record_rotation(
                    rotation=args.rotation,
                    grader_verdict=args.grader_verdict,
                    supervisor_verdict=args.supervisor_verdict,
                    author=args.author, supervisor=args.supervisor,
                    disclosure=args.disclosure,
                    grade_record=args.grade_record,
                    grader_commit=args.grader_commit,
                    failed_gates=args.failed_gate)
            except SH.MalformedRotationRow as e:
                print(canonical_json({"recorded": False, "refused": str(e)}))
                return 2
            print(canonical_json({"recorded": True, "row": row}))
        if args.record:
            SH.append_row(json.loads(args.record))
        st = SH.key1_status()
        print(canonical_json(st))
        if st["ready_for_arming_ask"]:
            print("ARMING ASK DUE — 5/5 consecutive agreements. This is an ASK "
                  "to gm + the operator (plus Key 2 + congruence), never a self-serve "
                  "arm.")
        return 0 if (args.check and st["ready_for_arming_ask"]) else (
            1 if args.check else 0)

    if os.path.exists(KILL_FILE):
        print(json.dumps({"status": "DISABLED", "kill_file": KILL_FILE}))
        return 3

    reg = json.loads(Path(ORCH, "registry.json").read_text())
    sess = json.loads(Path(ORCH, "state", "agent-sessions.json").read_text())
    kw = dict(
        handoff_path=args.handoff, canary_path=args.canary,
        jsonl_path=args.jsonl, agent_id=args.agent,
        successor_key=args.successor, repo_dir=ORCH,
        tasks_db=os.path.join(ORCH, "state", "tasks.db"),
        registry=reg, sessions=sess, resolver=_resolver(),
        leak_lint_result=_leak_lint(args.handoff, args.canary),
        session_start_epoch=args.session_start,
        now_epoch=args.now if args.now is not None else int(time.time()))

    if args.cmd == "regrade":
        out = G.regrade(args.record, **kw)
        print(canonical_json(out))
        # scriptable statuses (gm bind #3, DEC-1787085269): 0 REPRODUCED,
        # 2 ARTIFACT-DRIFT / MISMATCH, 4 RECORD-PREDATES-FROZEN-WORLD
        # (3 is taken by the kill file above)
        if out["status"] == "REPRODUCED":
            return 0
        if out["status"] == "RECORD-PREDATES-FROZEN-WORLD":
            return 4
        return 2

    rec = G.grade(**kw)
    if getattr(args, "record_out", None):
        Path(args.record_out).write_text(canonical_json(rec))
        path = args.record_out
    else:
        path = G.write_record(rec, args.successor,
                              os.path.join(ORCH, "state", "agent-handoffs"))
    if getattr(args, "shadow", False):
        # Blinding (shadow-consumption Decision 4): verdict only. The grader
        # agreeing with itself through a human proxy is the laundering shape
        # Amendment 3 gate 4 names — detail is read from the record AFTER the
        # supervisor's own verdict is committed to the ledger.
        print(canonical_json({"passed": rec["passed"], "record": path}))
        return 0 if rec["passed"] else 1
    failing = [g for g in rec["gates"] if not g["passed"]]
    print(canonical_json({"passed": rec["passed"], "record": path,
                          "failing": [{"id": g["id"], "why": g["why"],
                                       "evidence": g["evidence"][:4]}
                                      for g in failing]}))
    # §7: below the bar = BLOCKED, with the exact failing line items. The
    # re-author loop and 3-strike escalation live with the CALLER (daemon /
    # supervisor); this tool only ever grades. No override exists here.
    return 0 if rec["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
