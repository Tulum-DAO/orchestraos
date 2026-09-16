"""Armed rotation orchestrator -- the ONE place armed=True executors run.

The single most dangerous path in WS1. Every safety property is enforced HERE,
in code, not by convention:

  * CANARY-SCOPED: execute_rotation acts on exactly ONE agent (the canary). There
    is no fleet path, no loop over agents. main() only reaches here with an
    explicit --canary <id>.
  * REVERSIBLE-FIRST: register -> spawn -> verify_successor -> inject_init ->
    wire_edge -> verify_edge run first. These are additive/reversible (they bring
    a successor up + wire routing); none kills anything.
  * KILL IS DOUBLE-GATED: the retire/kill step runs ONLY when BOTH hold, checked
    immediately before the kill:
       (1) an execute-time safety re-check re-classifies the canary as
           SUPERSEDED_SAFE (a fresh park-idle classify: dirty-cwd/8a,
           the operator-viewing/8b, recent-activity/8c, pending-work, attached ALL clear),
       AND
       (2) the operator taps APPROVE on a one-tap approval.py decision.
    Deny / timeout / not-safe -> retire is NOT run; the predecessor stays alive.
    The successor is already up, so this is the TRANSIENT two-heads state, which
    is SAFE: the R2 succeeded_by chain routes mail to the successor. repin runs
    only AFTER a successful retire.
  * NO cron / @reboot / systemd / fleet-wide. This function is invoked manually,
    per rotation, per the operator tap.

ALL IO seams are injectable (executors_impl, approval_fn, safety_fn) so tests
exercise the full orchestration WITHOUT shelling out or paging the operator. The default
seams are the real executors + a real approval gate + a real park-idle re-check.
"""

import re


# --- outcome/status constants -------------------------------------------------
DONE = "rotated"                       # full rotation incl. retire + repin
ABORT_NO_SUCCESSOR = "abort:successor-not-live"
ABORT_EDGE = "abort:edge-not-wired"
HOLD_UNSAFE = "hold:safety-recheck-failed"   # both live; retire skipped
HOLD_DENIED = "hold:approval-denied"         # both live; retire skipped
HOLD_TIMEOUT = "hold:approval-timeout"       # both live; retire skipped
HOLD_UNCONFIRMED = "hold:successor-unconfirmed"  # S3 exhausted; both live, NO retire
HOLD_GRADE = "hold:grade-not-pass"           # auto-grade FAIL/REFUSED; both live, NO retire
ABORT_LIVE_SUCCESSOR = "abort:successor-already-live"


def _lineage_of(canary, pred_entry):
    """(lineage_root, predecessor_generation) -- mirrors lineage-daemon._lineage_of
    so the executor and the planner agree on the successor id + generation."""
    pred_entry = pred_entry or {}
    root = pred_entry.get("lineage_root") or re.sub(r"-g\d+$", "", canary) or canary
    gen = pred_entry.get("generation")
    if gen is None:
        m = re.search(r"-g(\d+)$", canary)
        gen = int(m.group(1)) if m else 1
    return root, int(gen)


def _alive(step_result) -> bool:
    """A verify_successor result reports alive (armed) or is a planned no-op."""
    return bool(step_result.get("alive")) if step_result.get("executed") else False


def _edge_ok(step_result) -> bool:
    return bool(step_result.get("ok")) if step_result.get("executed") else False


def execute_rotation(canary, registry, *, executors_impl=None,
                     approval_fn=None, safety_fn=None,
                     successor_live_fn=None, confirm_fn=None,
                     grade_fn=None, orchestra_dir=None):
    """Execute the armed hard_rotate for ONE canary. Returns a trace dict:
        {"canary","successor","generation","status","steps":[...]}

    Seams (all injectable; defaults are the real IO):
      executors_impl     -- the executors module (armed=True calls run for real).
      approval_fn(canary, successor) -> "approve"|"deny"|"timeout"
                            (the the operator one-tap gate; default posts an approval.py
                            decision + waits).
      safety_fn(canary)  -> (category, reason)  (execute-time re-classify; default
                            re-runs park-idle.classify with fresh Gap-8 signals).
      successor_live_fn(successor) -> bool  (the pre-spawn no-live-successor gate).
      confirm_fn(canary, successor) -> {"outcome": "confirmed"|"held", ...}
                            (S3 confirm-and-correct, WS3 v2 DEC-1786724046) — the
                            predecessor verifies AT SOURCE that the successor
                            absorbed the deep context + began correct work, via
                            the content gate (read-back + canary vs daemon-held
                            ground truth). Runs AFTER verify_edge and BEFORE the
                            kill gates; a 'held' outcome means the successor could
                            not be confirmed after N corrections -> both stay
                            LIVE, NOTHING is retired. When None (legacy/canary
                            path), S3 is skipped (v1 behavior).
      grade_fn(canary, successor) -> {"disposition": "PASS"|"FAIL"|"REFUSED", ...}
                            (T2 auto-rotation machine grade) — after S3 confirm and
                            BEFORE KILL GATE 2, run the deterministic comprehension
                            grader on the successor's readback. Recorded as the
                            "auto_grade" trace step. A non-PASS disposition
                            (FAIL/REFUSED) HOLDS the rotation (status HOLD_GRADE):
                            both stay LIVE, NOTHING is retired, and the the operator/approval
                            gate is never even reached. When None (default/v1 path),
                            auto-grade is skipped entirely and behavior is
                            byte-identical to the pre-grade executor. This is the
                            unattended machine gate that lets a graduated seat
                            auto-approve at KILL GATE 2 with a real PASS behind it.
    """
    from scripts.lineage_daemon import executors as _ex
    ex = executors_impl or _ex

    pred_entry = (registry or {}).get("agents", {}).get(canary, {})
    lineage_root, pred_gen = _lineage_of(canary, pred_entry)
    generation = pred_gen + 1
    successor = f"{lineage_root}-g{generation}"

    steps = []
    trace = {"canary": canary, "successor": successor,
             "generation": generation, "lineage_root": lineage_root,
             "status": None, "steps": steps}

    def _record(name, result):
        steps.append({"step": name, "result": result})
        return result

    # 0. no-live-successor gate (double-spawn guard) -- BEFORE any spawn.
    if successor_live_fn is not None and successor_live_fn(successor):
        trace["status"] = ABORT_LIVE_SUCCESSOR
        return trace

    # 1-6. REVERSIBLE build: register -> spawn -> verify -> init -> wire -> verify_edge.
    _record("register_successor", ex.plan_register_successor(
        successor, pred_entry, generation, lineage_root,
        armed=True, orchestra_dir=orchestra_dir))
    _record("spawn", ex.plan_spawn(successor, armed=True,
                                   orchestra_dir=orchestra_dir))
    verify = _record("verify_successor", ex.verify_successor(
        successor, armed=True, orchestra_dir=orchestra_dir))
    if not _alive(verify):
        trace["status"] = ABORT_NO_SUCCESSOR   # successor never came up; kill NOTHING
        return trace
    _record("inject_init", ex.plan_inject_init(
        successor, canary, armed=True, orchestra_dir=orchestra_dir))
    _record("wire_edge", ex.plan_wire_edge(
        canary, successor, generation, armed=True, orchestra_dir=orchestra_dir))
    edge = _record("verify_edge", ex.plan_verify_edge(
        canary, successor, armed=True, orchestra_dir=orchestra_dir))
    if not _edge_ok(edge):
        # succeeded_by did not land -> DO NOT kill (mail wouldn't route). Both live;
        # successor is up + reachable directly; predecessor still owns the name.
        trace["status"] = ABORT_EDGE
        return trace

    # --- S3 CONFIRM-AND-CORRECT (WS3 v2): the predecessor verifies the successor
    # absorbed the deep context + began correct work BEFORE any kill gate. A
    # 'held' outcome (couldn't confirm after N corrections) leaves BOTH live and
    # retires NOTHING — a rotation that can't confirm correct work is a HELD
    # failure, not a completed one. Skipped when confirm_fn is None (v1 path).
    if confirm_fn is not None:
        confirm = _record("confirm_correct", confirm_fn(canary, successor))
        if (confirm or {}).get("outcome") != "confirmed":
            trace["status"] = HOLD_UNCONFIRMED
            return trace

    # --- AUTO-GRADE (T2 machine gate): grade the successor's readback AFTER S3
    # confirm and BEFORE the kill gates. A non-PASS disposition (FAIL/REFUSED) is a
    # HELD failure — both stay LIVE, NOTHING is retired, and KILL GATE 2 (the
    # auto-approve / the operator card) is never reached. Only a PASS proceeds. Skipped
    # entirely when grade_fn is None (v1 path byte-identical). This is what lets a
    # graduated seat auto-approve at KILL GATE 2 with a real machine PASS behind it.
    if grade_fn is not None:
        grade = _record("auto_grade", grade_fn(canary, successor))
        if (grade or {}).get("disposition") != "PASS":
            trace["status"] = HOLD_GRADE
            return trace

    # --- KILL GATE 1: execute-time safety re-check (fresh classify) ---
    if safety_fn is not None:
        cat, reason = safety_fn(canary)
        _record("safety_recheck", {"category": cat, "reason": reason})
        if cat != "SUPERSEDED_SAFE":
            # No longer safe to retire (dirty cwd / viewing / recent / pending /
            # attached). Leave BOTH live; the plan-time approval does NOT bypass
            # this. R2 chain routes to the successor meanwhile.
            trace["status"] = HOLD_UNSAFE
            return trace

    # --- KILL GATE 2: the operator one-tap approval ---
    decision = approval_fn(canary, successor) if approval_fn else "timeout"
    _record("approval", {"decision": decision})
    if decision != "approve":
        trace["status"] = HOLD_DENIED if decision == "deny" else HOLD_TIMEOUT
        return trace

    # 7. RETIRE (the kill) -- both gates passed.
    _record("retire", ex.plan_retire(canary, armed=True,
                                     orchestra_dir=orchestra_dir))
    # 8. REPIN successor to the canonical name (Gap 7) -- only after retire.
    _record("repin_canonical", ex.plan_repin_canonical(
        successor, lineage_root, generation, armed=True,
        orchestra_dir=orchestra_dir))
    trace["status"] = DONE
    return trace


# --- default seams (real IO; used only from main()'s --execute path) ----------

def default_safety_recheck(canary, orchestra_dir=None):
    """Re-classify the canary via park-idle at execute-time with fresh Gap-8
    signals. Returns (category, reason). Fail-closed: any error -> a non-
    SUPERSEDED_SAFE category so the caller holds the kill."""
    import importlib.util
    import os
    od = orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
    pk_path = os.path.join(od, "scripts", "park-idle.py")
    try:
        spec = importlib.util.spec_from_file_location("park_idle_rt", pk_path)
        pk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pk)
        reg = pk._load(pk.REGISTRY)
        meta = pk._load(pk.AGENT_SESSIONS)
        live = pk.live_sessions()
        attached = pk.session_attached()
        presence = pk.load_presence()
        reg_agents = reg.get("agents", {})
        entry = meta.get(canary, {})
        pane_plain = pk.tmux("capture-pane", "-t", canary, "-p").stdout
        pane_ansi = pk.tmux("capture-pane", "-t", canary, "-e", "-p").stdout
        cwd = entry.get("cwd") or reg_agents.get(canary, {}).get("cwd")
        # A-with-guard (H7): the predecessor's OWN at-risk work (jsonl-edited ∩
        # dirty, or unique unpushed commits). Resolve its live .jsonl path.
        own_risk = False
        try:
            from scripts.lineage_daemon import enrich as _enrich
            sid = _enrich.live_sid(entry)
            jsonl_path = None
            if sid and cwd:
                root = os.path.expanduser("~/.claude/projects")
                jsonl_path = os.path.join(
                    root, _enrich._project_dir(cwd), f"{sid}.jsonl")
            own_risk = pk.own_work_at_risk(cwd, jsonl_path)
        except Exception:  # noqa: BLE001 -- fail-safe: unresolved -> block
            own_risk = True
        return pk.classify(
            canary, entry, canary in reg_agents,
            bool(reg_agents.get(canary, {}).get("always_on")),
            attached.get(canary, False), live, meta, pane_plain, pane_ansi,
            has_uncommitted_work=pk.cwd_has_uncommitted_work(cwd),
            shaw_viewing=pk.shaw_viewing_agent(canary, presence),
            state_age_s=pk.session_activity_age(canary),
            own_work_at_risk=own_risk,
        )
    except Exception as e:  # noqa: BLE001 -- fail CLOSED (hold the kill)
        return ("UNKNOWN", f"safety re-check error (fail-closed): {e}")


def default_approval_gate(canary, successor, *, timeout_s=1800, poll_s=10,
                          store=None, orchestra_dir=None):
    """Post a one-tap retire decision to the operator via approval.py and WAIT for it.

    Returns "approve" | "deny" | "timeout". kind='menu', worker_kind='pane',
    from=lineage-daemon. This is the ONLY thing standing between a spawned
    successor and the predecessor's kill.
    """
    import os
    import sys
    import time
    od = orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
    sys.path.insert(0, os.path.join(od, "scripts"))
    from approval_schema import ApprovalStore
    st = store or ApprovalStore()
    st.migrate()
    rid = st.create(
        from_agent="lineage-daemon",
        question=f"Retire {canary} (superseded by live {successor})?",
        worker_kind="pane",
        options=[{"label": "Approve retire", "value": "approve"},
                 {"label": "Keep both live", "value": "deny"}],
        kind="menu",
        summary=(f"WS1 canary rotation: {successor} is spawned + verified + "
                 f"wired. Approve to retire the superseded predecessor {canary}; "
                 f"Keep both leaves the predecessor alive (successor already "
                 f"routes via the lineage chain)."),
        risk_level="high", reversibility="hard", feature="WS1 lineage rotation")
    try:
        from approval_notify import notify
        notify(rid, store=st)
    except Exception:  # noqa: BLE001 -- cron backstop re-notifies
        pass
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        row = st.get(rid)
        if row and row.get("status") in ("answered", "resumed"):
            ans = (row.get("answer") or "").lower()
            return "approve" if ans in ("approve", "accept") else "deny"
        time.sleep(poll_s)
    return "timeout"
