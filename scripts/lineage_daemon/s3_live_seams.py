"""Gap-1 — the LIVE S3 confirm seams provider for the cron fleet-beat.

Congruence: DEC-1787767251 (CONSENSUS_REACHED, agy-ops + orchestra-builder, both
APPROVE on proposal sha 985c3239). The S3 loop machinery (beat.two_sample_confirm ->
confirm_correct.confirm_and_correct) already exists + is tested; what was missing is a
LIVE `seams_provider(canary, successor)` to feed it. cron_beat.main() historically
built the confirm with seams_provider=None, so every hard_rotate HELD
(s3-live-seams-unavailable) — the last barrier between an armed T2 auto-rotation and a
destructive retire. This module supplies that provider.

DESIGN (proposal D1-D6):
  D1  expected / ground_truth / first_effect are sourced from the predecessor's
      COMMITTED handoff (docs/HANDOFF_<lineage>-next.md machine-readable block) —
      never invented, never the successor's echo. Canary ground truth is resolved
      from the predecessor TRANSCRIPT at build time (no stored answer key).
  D2  read_successor reads the successor AT SOURCE — its committed <succ>.readback.md
      (evidence: readback + canary_answers) — not a self-report. Fail-closed to empty.
  D3  S3 stays AND runs before auto_grade; they draw ground truth from DIFFERENT
      authorities (handoff doc vs predecessor transcript). This module only builds S3.
  D4  inject_correction is durable-first (msg_store) + best-effort tmux nudge honoring
      the live-AUQ inject-guard; a nudge failure never aborts the loop.
  D5/D6  FAIL-CLOSED everywhere: a missing/degenerate handoff, an unresolvable
      transcript, or ANY error => the provider returns None => build_s3_confirm_fn
      HOLDs => NOTHING retires. The provider is only CALLED on an armed hard_rotate,
      which stays gated behind SOFT_ONLY=True + floor + empty self_retire_armed, so
      this lands INERT.

Every positive-confirm path is exercised for real only at the step-4 WATCHED trial;
here the contract is: correct seam shapes + HOLD on every ambiguity.
"""
import json
import os
import re
import sys

ORCHESTRA_DIR = os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))

# per-question readback section header the grader convention uses (**Q<N>** / cq<N>).
_SECTION_HEAD = re.compile(r"^\*{0,2}(?:cq|q)(\d+)\b", re.IGNORECASE | re.MULTILINE)
_MD_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)


def _import_rgm(orchestra_dir=None):
    """Import rotation_gate_manual robustly. It lives in <orchestra>/scripts/ which is
    NOT on sys.path in the live daemon context (cron_beat runs with sys.path[0] =
    scripts/lineage_daemon/), so a bare `import rotation_gate_manual` raises
    ModuleNotFoundError — which the provider's try/except swallowed into a None return,
    making the S3 confirm HOLD for EVERY seat (the watched trials masked this because
    they were driven by hand, never through the daemon's import context). Insert the
    scripts/ dir first (the same pattern execute.py:267 + graduation_executors.py use)."""
    od = orchestra_dir or ORCHESTRA_DIR
    scripts_dir = os.path.join(od, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import rotation_gate_manual as RG
    return RG


def _handoffs_dir(od):
    return os.path.join(od, "state", "agent-handoffs")


def _handoff_path(lineage_root, od):
    return os.path.join(od, "docs", f"HANDOFF_{lineage_root}-next.md")


def _extract_machine_block(md_text):
    """The last ```json fenced block in a committed handoff .md (the machine-readable
    section). Returns the parsed dict, or None on absence/parse error (fail-closed)."""
    blocks = re.findall(r"```json\s*(.*?)```", md_text, re.DOTALL)
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1])
    except (ValueError, TypeError):
        return None


def _load_committed_handoff(lineage_root, od):
    """The predecessor's committed handoff dict, or None (missing / unreadable /
    no machine-readable block / malformed). Never raises."""
    try:
        from scripts.lineage_daemon.handoff_provider import read_committed_handoff
        data, _mtime = read_committed_handoff(lineage_root, lineage_root=lineage_root, orchestra_dir=od)
        return data
    except Exception:
        path = _handoff_path(lineage_root, od)
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            return None
        return _extract_machine_block(text)


def _is_degenerate_handoff(h):
    """True when the handoff cannot anchor a meaningful S3 confirm — no goal, no
    first_effect, or no canary_questions. The reviewer-surfaced hardening
    (DEC-985c3239): S3 must not lean on a trivially-authored handoff even though
    auto_grade independently guards via the transcript. Degenerate => provider
    returns None => HOLD (never a weak pass)."""
    if not isinstance(h, dict):
        return True
    goal = str(h.get("current_goal") or "").strip()
    first_effect = h.get("first_effect") or {}
    canary = h.get("canary_questions") or []
    return not goal or not first_effect or not canary


def _build_expected(h):
    """The expected-work profile check_on_track compares observed behavior against,
    from the handoff phase_state + file roots. Best-effort; missing keys are fine
    (check_on_track only flags on the keys that ARE present)."""
    ps = h.get("phase_state") or {}
    return {
        "focus_canonical": h.get("current_goal") or "the inherited focus",
        "focus_id": ps.get("plan_ref"),
        "file_roots": h.get("file_roots_touched") or [],
        "first_action": (h.get("next_3_actions") or [None])[0],
        "next_gate": ps.get("next_gate"),
    }


def _build_ground_truth(h, transcript_path, orchestra_dir=None):
    """Daemon-held ground truth — NEVER the successor's echo. Canary answers are
    resolved from the predecessor transcript at build time via the real resolver
    (no stored answer key). mode='strict' so the S3 comprehension arm applies the
    same strict grounding the arming precondition requires."""
    RG = _import_rgm(orchestra_dir)
    canary = []
    for q in h.get("canary_questions") or []:
        canary.append({
            "id": q.get("id"),
            "answer": RG.resolve_pointer(transcript_path, q.get("source_pointer")),
            "from_pointer": True,
        })
    return {
        "goal": h.get("current_goal"),
        "guards": h.get("decisions") or [],
        "open_loops": h.get("open_loops") or [],
        "hazards": h.get("hazards") or [],
        "canary": canary,
        "transcript_path": transcript_path,
        "mode": "strict",
    }


def _split_readback_sections(md, n_questions):
    """Per-question answer text keyed q1..qn from a committed readback, using the
    grader's line-start **Q<N>** convention. Best-effort (a missing/misshapen
    readback just yields fewer sections => the gate scores them absent => not
    confirmed). Never raises."""
    out = {}
    try:
        hits = list(_SECTION_HEAD.finditer(md))
        for i, m in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(md)
            seg = md[m.start():end]
            stop = _MD_HEADING.search(seg, 1)
            if stop:
                seg = seg[:stop.start()]
            out[f"q{int(m.group(1))}"] = seg.strip()
    except Exception:  # noqa: BLE001 -- fail-closed: an unparseable readback => {}
        return {}
    return out


def _read_successor_at_source(successor, od):
    """Read the successor AT SOURCE: its COMMITTED <succ>.readback.md (the same
    artifact gap-2's inject_init commands + auto_grade reads). Returns
    {observed, evidence}. Fail-closed: a missing/unreadable readback => empty
    evidence + empty observed (=> gate not-confirmed), never an exception.

    `observed.oriented` is True iff the successor committed a readback (it had to read
    the predecessor handoff to write one); state defaults to 'working'. The heavy
    lifting is the comprehension arm (canary/readback grounding), not this telemetry."""
    rb_path = os.path.join(_handoffs_dir(od), f"{successor}.readback.md")
    try:
        with open(rb_path) as f:
            rb = f.read()
    except OSError:
        return {"observed": {}, "evidence": {"readback": {}, "canary_answers": {}}}
    canary_answers = _split_readback_sections(rb, 0)
    observed = {
        "successor": successor,
        "oriented": True,
        "state": "working",
    }
    evidence = {"readback": {"goal": rb[:2000]}, "canary_answers": canary_answers}
    return {"observed": observed, "evidence": evidence}


def _correction_fn(observed, expected, nonce):
    """A nonce-keyed correction message for a lagging successor (PB-style)."""
    focus = (expected or {}).get("focus_canonical") or "your inherited focus"
    first = (expected or {}).get("first_action")
    tail = f" Resume: {first}." if first else ""
    return (f"[S3 CORRECTION {nonce}] You have not yet demonstrably absorbed the "
            f"handoff for {focus}. Re-read your predecessor's committed handoff + "
            f"answer your canary in your readback.{tail} (ack {nonce})")


def _inject_correction(successor, text, nonce, *, orchestra_dir=None,
                       send_fn=None, nudge_fn=None):
    """Durable-first correction delivery (D4): msg_store send is primary + auditable;
    the tmux nudge is best-effort and honors the live-AUQ inject-guard (never clobber
    a live prompt/composer). A nudge failure is swallowed — it must NEVER abort the
    rotation loop; the durable message still lands."""
    od = orchestra_dir or ORCHESTRA_DIR
    # 1. durable send (primary).
    if send_fn is None:
        def send_fn(succ, body, nc):  # noqa: E306
            import subprocess
            import sys
            subprocess.run(
                [sys.executable, os.path.join(od, "msg_store.py"), "send",
                 "--from", "lineage-daemon", "--to", succ, "--type", "s3_correction",
                 "--priority", "high", "--subject", f"S3 correction {nc}",
                 "--body", body],
                capture_output=True, text=True)
    try:
        send_fn(successor, text, nonce)
    except Exception:  # noqa: BLE001 -- even a durable-send failure must not raise
        pass
    # 2. best-effort tmux nudge (inject-guard: never clobber a live prompt).
    if nudge_fn is None:
        def nudge_fn(succ, body, nc):  # noqa: E306
            import subprocess
            # inject-guard (feedback_inject_guard_live_auq): capture the pane FIRST and
            # skip the nudge if a live prompt / AUQ is showing — never clobber it. The
            # durable msg_store send already delivered; the nudge is only a wake.
            cap = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", f"{succ}:0.0"],
                capture_output=True, text=True)
            pane = cap.stdout or ""
            if ("Do you want" in pane or "❯ 1." in pane
                    or "1." in pane and "2." in pane):
                return  # a live AUQ/menu is showing — do not send-keys into it
            subprocess.run(
                ["tmux", "send-keys", "-t", f"{succ}:0.0", "", "Enter"],
                capture_output=True, text=True)
    try:
        nudge_fn(successor, text, nonce)
    except Exception:  # noqa: BLE001 -- nudge is best-effort; never aborts the loop
        pass


def build_live_seams_provider(*, orchestra_dir=None, transcript_of=None):
    """Return a `seams_provider(canary, successor) -> dict | None` for
    build_s3_confirm_fn. Sources ground truth from the predecessor's COMMITTED
    handoff + transcript (D1), reads the successor at source (D2), and is
    FAIL-CLOSED: a missing/degenerate handoff or an unresolvable transcript => None
    => the confirm HOLDs => NOTHING retires.

    `transcript_of(successor) -> path|None` is an injectable seam for locating the
    predecessor transcript (default resolves via graduation_resolvers live path).
    """
    od = orchestra_dir or ORCHESTRA_DIR

    def _default_transcript_of(canary):
        # the predecessor (canary) transcript, located by its sid via the live
        # resolver's own sessions read; None on any failure (=> HOLD).
        try:
            from scripts.lineage_daemon import graduation_resolvers as GR  # noqa: F401
        except Exception:  # noqa: BLE001
            return None
        try:
            import glob
            # sid of the canonical seat (canary) from agent-sessions.
            sess_path = os.path.join(od, "state", "agent-sessions.json")
            with open(sess_path) as f:
                sess = json.load(f)
            sid = _find_sid(sess, canary)
            if not sid:
                return None
            hits = sorted(
                p for p in glob.glob(
                    os.path.expanduser(f"~/.claude/projects/*/{sid}*.jsonl"))
                if "/subagents/" not in p)
            return hits[0] if len(hits) == 1 else None
        except Exception:  # noqa: BLE001
            return None

    tof = transcript_of or _default_transcript_of

    def seams_provider(canary, successor):
        # lineage_root: the canary IS the canonical seat name (== lineage root here).
        h = _load_committed_handoff(canary, od)
        if h is None or _is_degenerate_handoff(h):
            return None
        tpath = tof(canary)
        if not tpath or not os.path.exists(tpath):
            return None
        try:
            expected = _build_expected(h)
            ground_truth = _build_ground_truth(h, tpath, orchestra_dir=od)
            first_effect = h.get("first_effect") or {}

            def read_successor():
                return _read_successor_at_source(successor, od)

            def inject_correction(succ, text, nonce):
                _inject_correction(succ, text, nonce, orchestra_dir=od)

            return {
                "expected": expected,
                "ground_truth": ground_truth,
                "first_effect": first_effect,
                "read_successor": read_successor,
                "correction_fn": _correction_fn,
                "inject_correction": inject_correction,
            }
        except Exception:  # noqa: BLE001 -- any assembly error => HOLD, never confirm
            return None

    return seams_provider


def _find_sid(obj, agent_id):
    """The session_id for agent_id anywhere in the agent-sessions structure."""
    if isinstance(obj, dict):
        if obj.get("agent_id") == agent_id and obj.get("session_id"):
            return obj["session_id"]
        row = obj.get(agent_id)
        if isinstance(row, dict) and row.get("session_id"):
            return row["session_id"]
        for v in obj.values():
            r = _find_sid(v, agent_id)
            if r:
                return r
    return None
