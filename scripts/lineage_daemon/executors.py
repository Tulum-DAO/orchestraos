"""Executors -- ARMED action wiring for the lineage daemon.

Each executor builds an argv `cmd` and, ONLY if `armed` is True, runs it
via subprocess and captures output. If `armed` is False it returns the
planned command WITHOUT running anything. This lets the daemon (and its
tests) construct a full ordered action plan while executing nothing.

Reused, pre-existing helpers (targets configurable via ORCHESTRA_DIR):
  - spawn:    <od>/spawn-agent.sh <agent_id>
  - retire:   python3 <od>/scripts/park-idle.py --agent <id> --execute
              (reversible: registry-first-then-kill)
  - sessions: python3 <od>/scripts/sessions-update.py <id> --json '<obj>'
              (succession edges: consumers read succeeded_by/lineage_root here)
  - registry: python3 <od>/scripts/registry-update.py <id> --json '<obj>'
              (generation display/recovery field)
  - status:   python3 <od>/scripts/agent-status.py <session>  (read-only)

SAFETY: nothing here executes unless armed=True is passed explicitly.
Tests never pass armed=True.
"""

import json
import os
import subprocess

ORCHESTRA_DIR = os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")
)


def _run(cmd, armed):
    """Return the planned cmd unrun (armed=False) or run it (armed=True).

    Unarmed: {"cmd": cmd, "executed": False} -- NEVER shells out.
    Armed:   runs subprocess.run(capture_output) and returns rc/stdout/stderr.
    """
    if not armed:
        return {"cmd": cmd, "executed": False}
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "executed": True,
        "returncode": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
    }


# Registry fields a successor inherits from its predecessor so it boots as the
# SAME ROLE (Gap 1). spawn-agent.sh auto-registers a BLANK system_prompt when the
# id is unknown, so we must register these FIRST.
_INHERIT_FIELDS = ("tier", "machine", "cwd", "system_prompt", "model",
                   "always_on", "memory_scope", "runtime", "launch_command")


def plan_register_successor(successor_id, pred_entry, generation, lineage_root,
                            armed=False, orchestra_dir=None) -> dict:
    """Plan (or run) the successor's registry REGISTRATION before spawn (Gap 1).

    spawn-agent.sh auto-registers an unknown id with a BLANK system_prompt -> the
    successor boots without its role. Register it FIRST, inheriting the
    predecessor's system_prompt (the role prompt file), cwd, tier, machine, model,
    always_on (the GM_INIT_TEMPLATE), plus its transient identity (name /
    tmux_session = successor_id), generation, and lineage_root. The transient
    -gN identity is repinned to the canonical name later (Gap 7).
    """
    od = orchestra_dir or ORCHESTRA_DIR
    pred_entry = pred_entry or {}
    fields = {k: pred_entry[k] for k in _INHERIT_FIELDS if k in pred_entry}
    fields.update({
        "name": successor_id,
        "tmux_session": successor_id,
        "generation": generation,
        "lineage_root": lineage_root,
        "handoff_from": lineage_root,
    })
    cmd = ["python3", od + "/scripts/registry-update.py",
           successor_id, "--json", json.dumps(fields)]
    return _run(cmd, armed)


def plan_spawn(successor_id, armed=False, orchestra_dir=None) -> dict:
    """Plan (or run) the successor spawn via spawn-agent.sh.

    Precondition: plan_register_successor ran first (Gap 1), so spawn-agent.sh
    reads the registered role/system_prompt/cwd instead of auto-registering blank.
    """
    od = orchestra_dir or ORCHESTRA_DIR
    cmd = [od + "/spawn-agent.sh", successor_id]
    return _run(cmd, armed)


def plan_inject_init(successor_id, predecessor_id, armed=False,
                     orchestra_dir=None) -> dict:
    """Plan (or run) the successor's INIT TASK injection (Gap 2).

    After spawn+verify the successor needs a task telling it to read the
    predecessor's committed fixed-schema handoff + MEMORY.md + its role brief,
    do a MACHINE-GRADABLE READBACK, then continue and fire any inherited pending
    open_loops callbacks exactly once (the R1 delivery vehicle). Delivered durably
    via msg_store (from the daemon) so it survives and is auditable; the successor
    reads it on startup.

    THE READBACK STEP (Gap-2, T2 auto-loop): the unattended loop's `auto_grade`
    reads a COMMITTED `state/agent-handoffs/<successor>.readback.md` whose per-question
    answers use the grader's line-start section convention (`**Q<N>**`, parsed by
    rotation_gate_manual.split_readback_sections). Without an init that COMMANDS that
    artifact the successor just "continues work" and the in-loop grade holds
    (HOLD_GRADE) — the loop can never reach the strict PASS that arms an auto-retire.
    So the readback is ordered BEFORE continuing work: answer the daemon-held
    `<successor>.canary.json`, write + commit the readback in convention, THEN continue.
    """
    od = orchestra_dir or ORCHESTRA_DIR
    canary_rel = f"state/agent-handoffs/{successor_id}.canary.json"
    readback_rel = f"state/agent-handoffs/{successor_id}.readback.md"
    body = (
        f"[LINEAGE INIT] You are the successor to {predecessor_id}. "
        f"Read, in order: (1) {predecessor_id}'s committed handoff "
        f"(fixed-schema handoff written before rotation), (2) MEMORY.md, "
        f"(3) your role brief. "
        f"THEN do a READBACK before anything else: answer the canary questions in "
        f"{canary_rel} IN YOUR OWN WORDS by grepping {predecessor_id}'s transcript "
        f"(cite the exact shas/ids/numbers; there is NO answer key), and WRITE + git "
        f"commit ON-BRANCH {readback_rel} with each answer on its own line under a "
        f"bold per-question header (**Q1**, **Q2**, ... — the grader parses these; do "
        f"NOT put a markdown heading between the answers). This readback is what the "
        f"rotation grade reads — it is not optional. AFTER the readback is committed, "
        f"continue {predecessor_id}'s work. If the inherited handoff open_loops carry "
        f"pending outbound callbacks, fire each EXACTLY ONCE (mark sent; never "
        f"double-emit)."
    )
    cmd = ["python3", od + "/msg_store.py", "send",
           "--from", "lineage-daemon", "--to", successor_id,
           "--type", "lineage_init", "--priority", "high",
           "--subject", f"Readback + inherit {predecessor_id}'s work",
           "--body", body]
    return _run(cmd, armed)


def plan_verify_edge(predecessor_id, successor_id, armed=False,
                     orchestra_dir=None) -> dict:
    """Read-only verify that succeeded_by landed in LIVE agent-sessions (Gap 5).

    resolve_delivery_target's PRIMARY source is state/agent-sessions.json; Gap 4
    (ORCHESTRA_DIR) is what makes the wire-edge write land there. This reads the
    predecessor's entry back (sessions-update.py check mode = no --json) and, when
    armed, asserts succeeded_by == successor_id.
    """
    od = orchestra_dir or ORCHESTRA_DIR
    cmd = ["python3", od + "/scripts/sessions-update.py", predecessor_id]
    if not armed:
        return {"cmd": cmd, "executed": False}
    p = subprocess.run(cmd, capture_output=True, text=True)
    entry = {}
    try:
        entry = json.loads(p.stdout)
    except Exception:
        entry = {}
    succ = entry.get("succeeded_by") or entry.get("superseded_by")
    return {
        "cmd": cmd,
        "executed": True,
        "succeeded_by": succ,
        "ok": succ == successor_id,
    }


def plan_repin_canonical(successor_id, canonical, generation,
                         armed=False, orchestra_dir=None) -> dict:
    """Plan (or run) the ATOMIC repin of the successor to the CANONICAL name (Gap 7).

    The -gN + succeeded_by chain is a TRANSIENT (spawn+verify coexistence). The
    FINAL state is canonical name-reuse (like gm gen4->gen5): the successor takes
    over the canonical name, so resolve = direct-live (not chain).

    ORDERING: this runs AFTER retire(predecessor) so the canonical tmux name +
    registry key are FREE (retire is registry-removal-first -> kill). Steps:
      1. rename    tmux rename-session {successor_id} -> {canonical}
      2. registry  set {canonical} = name/tmux_session=canonical, generation,
                   succeeded_by=null (it IS the head now -> direct-live)
      3. sessions  set {canonical} = tmux_session=canonical, generation,
                   succeeded_by=null
    sid HAZARD (never guess): the real session_id is captured from the successor's
    live .jsonl by the caller (enrich.live_sid) or left for the periodic writer to
    recapture (session-index null guard). We do NOT write a guessed sid here.

    Returns the three armed-gated steps + a note. All armed=False by default.
    """
    od = orchestra_dir or ORCHESTRA_DIR
    canonical_fields = {"name": canonical, "tmux_session": canonical,
                        "generation": generation, "succeeded_by": None}
    rename_cmd = ["tmux", "rename-session", "-t", successor_id, canonical]
    registry_cmd = ["python3", od + "/scripts/registry-update.py",
                    canonical, "--json", json.dumps(canonical_fields)]
    sessions_cmd = ["python3", od + "/scripts/sessions-update.py",
                    canonical, "--json", json.dumps(
                        {"tmux_session": canonical, "generation": generation,
                         "succeeded_by": None})]
    return {
        "rename": _run(rename_cmd, armed),
        "registry_repin": _run(registry_cmd, armed),
        "sessions_repin": _run(sessions_cmd, armed),
        "sid": {"note": "capture from successor's live .jsonl (enrich.live_sid) "
                        "or leave for the periodic writer — NEVER guess",
                "from_id": successor_id},
    }



def plan_retire(agent_id, armed=False, orchestra_dir=None) -> dict:
    """Plan (or run) the reversible predecessor retire via park-idle.py."""
    od = orchestra_dir or ORCHESTRA_DIR
    cmd = ["python3", od + "/scripts/park-idle.py", "--agent", agent_id, "--execute"]
    
    if not armed:
        return {"cmd": cmd, "executed": False}
        
    # Get status before kill to know pid and sid
    status_cmd = ["python3", od + "/scripts/agent-status.py", agent_id]
    status_p = subprocess.run(status_cmd, capture_output=True, text=True)
    raw_status = {}
    try:
        raw_status = json.loads(status_p.stdout)
    except Exception:
        pass
        
    pid = None
    if isinstance(raw_status.get("process"), dict):
        pid = raw_status["process"].get("pid")
    sid = raw_status.get("session_id")
    
    p = subprocess.run(cmd, capture_output=True, text=True)
    
    # postcondition verification
    postcondition = False
    reason = ""
    if pid is None:
        # If we couldn't resolve the PID, it might already be dead, but to be fail-closed we must verify.
        # Wait, if PID is None, it means the process is not running. 
        # But we must verify it's really not running.
        # Let's check tmux.
        pass

    pid_alive = False
    if pid is not None:
        try:
            os.kill(int(pid), 0)
            pid_alive = True
        except OSError:
            pass

    tmux_cmd = ["tmux", "has-session", "-t", agent_id]
    tmux_p = subprocess.run(tmux_cmd, capture_output=True)
    tmux_alive = (tmux_p.returncode == 0)

    if pid_alive:
        reason = f"PID {pid} is still alive"
    elif tmux_alive:
        reason = f"Tmux session {agent_id} is still alive"
    else:
        postcondition = True
        reason = "Postconditions verified"

    from scripts.lineage_daemon.adapters.protocol import RetirementReceipt
    receipt = RetirementReceipt(
        requested_target=agent_id,
        archive_id=agent_id,
        resolved_sid=sid or "",
        resolved_pid=int(pid) if pid else 0,
        action_result=p.stdout,
        postcondition_verified=postcondition,
        reason=reason
    )
    
    return {
        "cmd": cmd,
        "executed": True,
        "returncode": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
        "receipt": receipt.__dict__
    }

def execute_tmux_repin(canonical, alias, successor_sid, armed=False):
    """Run the ATOMIC repin of the successor to the CANONICAL name and verify RepinReceipt."""
    if not armed:
        return {"executed": False}
        
    try:
        chk = subprocess.run(["tmux", "has-session", "-t", canonical], capture_output=True)
        if chk.returncode == 0:
            subprocess.run(["tmux", "kill-session", "-t", canonical], capture_output=True)
            
        chk_alias = subprocess.run(["tmux", "has-session", "-t", alias], capture_output=True)
        if chk_alias.returncode == 0:
            subprocess.run(["tmux", "rename-session", "-t", alias, canonical], capture_output=True)
            
        # Verify repin
        verify_chk = subprocess.run(["tmux", "has-session", "-t", canonical], capture_output=True)
        verified = (verify_chk.returncode == 0)
        
        # Get pane active session ID (we'll just use tmux display-message or assume it matches if we can't extract it easily, 
        # wait, the prompt says "Verify the pane's active session ID matches successor_provider_sid". 
        # Can we get the SID from the tmux pane? We can read it from agent-sessions.json or agent-status.py for the canonical name now.
        status_cmd = ["python3", ORCHESTRA_DIR + "/scripts/agent-status.py", canonical]
        status_p = subprocess.run(status_cmd, capture_output=True, text=True)
        raw_status = {}
        try:
            raw_status = json.loads(status_p.stdout)
        except Exception:
            pass
            
        actual_sid = raw_status.get("session_id")
        
        error = None
        if not verified:
            error = "Tmux session not found after rename"
        elif successor_sid and actual_sid != successor_sid:
            error = f"Active session ID {actual_sid} does not match successor {successor_sid}"
            verified = False
            
        from scripts.lineage_daemon.adapters.protocol import RepinReceipt
        receipt = RepinReceipt(
            previous_alias=alias,
            canonical_name=canonical,
            successor_sid=successor_sid or "",
            pane_id="unknown",
            verified_after_rename=verified,
            error=error
        )
        return {"executed": True, "receipt": receipt.__dict__}
    except Exception as e:
        return {"executed": True, "error": str(e)}



def plan_wire_edge(predecessor_id, successor_id, generation,
                   armed=False, orchestra_dir=None) -> dict:
    """Plan (or run) the calls that wire the lineage edge.

    Succession edges are written to agent-sessions.json (via
    sessions-update.py) because park-idle.py's retire gate and
    message-router.py's resolver BOTH read succession (succeeded_by /
    lineage_root) from that store -- NOT registry.json. Writing them to
    registry alone left a store-mismatch bug where a live successor edge was
    invisible to the consumers. Generation is a registry display/recovery
    field, so it stays in registry.json (via registry-update.py).

    The edge is wired BEFORE retire (per spec section 4) to avoid an
    ambiguous-lineage window while predecessor and successor coexist.
    Returns three armed-gated steps:
      * pred_succession  (agent-sessions): predecessor gets succeeded_by
      * succ_succession  (agent-sessions): successor gets lineage_root +
        handoff_from
      * succ_generation  (registry):       successor gets generation +
        handoff_from
    """
    od = orchestra_dir or ORCHESTRA_DIR
    pred_succession_cmd = [
        "python3", od + "/scripts/sessions-update.py",
        predecessor_id, "--json",
        json.dumps({"succeeded_by": successor_id})]
    succ_succession_cmd = [
        "python3", od + "/scripts/sessions-update.py",
        successor_id, "--json",
        json.dumps({"lineage_root": predecessor_id,
                    "handoff_from": predecessor_id})]
    succ_generation_cmd = [
        "python3", od + "/scripts/registry-update.py",
        successor_id, "--json",
        json.dumps({"generation": generation,
                    "handoff_from": predecessor_id})]
    return {
        "pred_succession": _run(pred_succession_cmd, armed),
        "succ_succession": _run(succ_succession_cmd, armed),
        "succ_generation": _run(succ_generation_cmd, armed),
    }


def verify_successor(session, armed=False, orchestra_dir=None) -> dict:
    """Read-only successor liveness check via agent-status.py.

    This is a READ, but it is still gated behind `armed` so the build and
    tests never shell out. When armed, it parses the status JSON and
    reports whether the successor is alive.
    """
    od = orchestra_dir or ORCHESTRA_DIR
    cmd = ["python3", od + "/scripts/agent-status.py", session]
    if not armed:
        return {"cmd": cmd, "executed": False}
    p = subprocess.run(cmd, capture_output=True, text=True)
    raw = {}
    try:
        raw = json.loads(p.stdout)
    except Exception:
        raw = {}
    state = raw.get("state", "unknown")
    running = bool(raw.get("process", {}).get("running")) \
        if isinstance(raw.get("process"), dict) else bool(raw.get("running"))
    alive = running or state not in ("stopped", "unknown")
    return {
        "cmd": cmd,
        "executed": True,
        "alive": alive,
        "state": state,
        "raw": raw,
    }
