"""Stage C — auto-grade the successor readback in-loop (STRICT), task #4.

The machine grader for the T2 auto-rotation. Wires the live comprehension gate
(scripts/rotation_gate_manual.py `grade`) into the rotation loop so an unattended
graduation has a MACHINE grade to gate on — no human grader.

Why this exists: dispatch_graduation consumes a `grade_of(successor)` reader that
must return "PASS"/"FAIL"/None. Until now that grade came from a human running the
CLI by hand. `auto_grade_successor` invokes the SAME live grader with `--strict`
(so the written <id>.comprehension.json carries mode=strict, the arming
precondition) and maps the process exit code to a disposition the loop can gate on.

Disposition mapping (the grader CLI exit codes ARE the contract — see
rotation_gate_manual.__main__):
    exit 0  -> {"disposition":"PASS"}      graded PASS; strict artifact written
    exit 1  -> {"disposition":"FAIL"}      graded FAIL; artifact written
    exit 2  -> {"disposition":"REFUSED"}   harness/input defect; NO artifact
    other   -> {"disposition":"REFUSED"}   fail-safe: an ambiguous exit is NOT a pass

CALLER CONTRACT: only PASS — with a written strict artifact — may feed a
graduation. FAIL and REFUSED must route to hold/card. They are RETURNED cleanly;
this function NEVER raises (a raise would wedge the rotation loop). `may_graduate`
encodes the gate so callers don't re-derive it.

The subprocess call is an INJECTED seam (`runner`) so tests are hermetic: a fake
runner returns each exit code without shelling out, spawning an agent, or needing
a transcript on disk.
"""
import os
import sys

ORCHESTRA_DIR = os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/orchestra")   # DATA dir
# CODE lives in the checkout (this file: <root>/scripts/lineage_daemon/auto_grade.py).
CODE_ROOT = os.environ.get("ORCHESTRA_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_GRADER = os.path.join(CODE_ROOT, "scripts", "rotation_gate_manual.py")

# exit code -> disposition (fail-safe default REFUSED for anything unexpected).
_DISPOSITION = {0: "PASS", 1: "FAIL", 2: "REFUSED"}


def _default_runner(argv):
    """Real subprocess seam: run the grader CLI, capture streams, return the
    completed process. Isolated behind the seam so tests never touch it."""
    import subprocess
    return subprocess.run(argv, capture_output=True, text=True)


def _artifact_path(successor):
    return os.path.join(ORCHESTRA_DIR, "state", "agent-handoffs",
                        f"{successor}.comprehension.json")


def build_grade_argv(successor, predecessor_sid, *, transcript=None,
                     strict=True, successor_sid=None):
    """The argv that invokes the live grader for `successor` against the
    predecessor transcript/sid. `--strict` is included for unattended runs so the
    written artifact carries mode=strict (the arming precondition). Prefers an
    explicit --transcript over --predecessor-sid when both are given. When
    `successor_sid` is given (D9, DEC-1787789209), threads `--successor-sid` so
    record_readback stamps the immutable provenance sid into the artifact."""
    argv = [sys.executable, _GRADER, "grade", successor]
    if transcript:
        argv += ["--transcript", transcript]
    elif predecessor_sid:
        argv += ["--predecessor-sid", predecessor_sid]
    if successor_sid:
        argv += ["--successor-sid", successor_sid]
    if strict:
        argv.append("--strict")
    return argv


def auto_grade_successor(successor, predecessor_sid, *, transcript=None,
                         strict=True, successor_sid=None, runner=_default_runner):
    """Grade the successor's readback via the live grader and map the result to a
    disposition. NEVER raises — every failure path returns a clean REFUSED so the
    caller routes to hold/card instead of the loop crashing.

    Returns a dict:
        {"disposition": "PASS"|"FAIL"|"REFUSED",
         "exit_code": int|None,
         "strict": bool,
         "artifact": <path>,
         "artifact_written": bool,   # False for REFUSED (grader writes none)
         "stdout": str, "stderr": str}

    Only disposition=="PASS" with strict==True AND artifact_written may feed a
    graduation (see may_graduate). FAIL/REFUSED must hold.
    """
    artifact = _artifact_path(successor)
    base = {"strict": bool(strict), "artifact": artifact}

    # A locator is mandatory: with no sid AND no transcript the grader has nothing
    # to point at. Refuse cleanly rather than shell out to a guaranteed REFUSAL.
    if not predecessor_sid and not transcript:
        return {**base, "disposition": "REFUSED", "exit_code": None,
                "artifact_written": False, "stdout": "",
                "stderr": "no predecessor_sid or transcript to locate the "
                          "predecessor jsonl"}

    argv = build_grade_argv(successor, predecessor_sid, transcript=transcript,
                            strict=strict, successor_sid=successor_sid)
    try:
        proc = runner(argv)
        code = int(getattr(proc, "returncode", 2))
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
    except Exception as e:  # noqa: BLE001 -- never wedge the loop; fail -> REFUSED
        return {**base, "disposition": "REFUSED", "exit_code": None,
                "artifact_written": False, "stdout": "",
                "stderr": f"grader runner error (fail-safe REFUSED): "
                          f"{type(e).__name__}: {e}"}

    disposition = _DISPOSITION.get(code, "REFUSED")
    # exit 2 (and any unexpected code we treat as refusal) means the grader wrote
    # NO artifact — a harness/input defect held for a human. 0/1 DID write one.
    artifact_written = disposition in ("PASS", "FAIL")
    return {**base, "disposition": disposition, "exit_code": code,
            "artifact_written": artifact_written, "stdout": stdout,
            "stderr": stderr}


def may_graduate(grade_result) -> bool:
    """The gate: True ONLY for a strict PASS with a written artifact. FAIL,
    REFUSED, and a non-strict (supervised) PASS all return False — an unattended
    auto-retire may consume nothing but a strict machine PASS."""
    if not isinstance(grade_result, dict):
        return False
    return (grade_result.get("disposition") == "PASS"
            and grade_result.get("strict") is True
            and grade_result.get("artifact_written") is True)


def grade_signal(grade_result):
    """Adapt a grade_result to the "PASS"/"FAIL"/None signal that
    dispatch_graduation's `grade_of` reader expects. REFUSED maps to None (an
    unknown/held grade -> may_retire's grade_agree tri-state None, which does NOT
    graduate) so a refusal can never masquerade as a FAIL-verdict or a PASS."""
    if not isinstance(grade_result, dict):
        return None
    disp = grade_result.get("disposition")
    if disp == "PASS" and grade_result.get("strict") is True \
            and grade_result.get("artifact_written") is True:
        return "PASS"
    if disp == "FAIL":
        return "FAIL"
    return None
