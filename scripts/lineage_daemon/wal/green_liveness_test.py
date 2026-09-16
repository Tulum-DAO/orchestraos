"""RED-first tests for green_liveness — the Layer-2 green-AGENT-liveness gate
(congruence DEC-1788655588, both peers APPROVE signal B).

verify_green (bar#4) proves CHANNEL + WAL-recall fidelity but the probe is
bg_arm-invoked ORCHESTRATOR-side, so a green frozen/hung PRE-READY (the trust
dialog, or any post-trust init hang) still verifies GREEN -> reap-blue-promote-DEAD.
The liveness gate closes it: the green process must have produced >=1 assistant turn
in its OWN transcript (a frozen-at-trust green wrote NONE, by effect). Deterministic,
fail-closed, bound to THIS green (pane_pid + ts-fence + cwd + declared_identity) so a
reused pane number / stale pane event / tmux-restart cannot false-PASS.
"""
import json
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import green_liveness  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402

ROOT = "bg-drill-victim"
ALIAS = "bg-drill-victim-g2"
CWD = "/home/testuser/repos/second-brain"
SID = "aaaa1111-bbbb-2222-cccc-333344445555"
SPAWNED_AT = 1000.0


def _panes(tmp_path):
    d = tmp_path / "state" / "agent-events" / "panes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_pane_event(tmp_path, pane_num, *, sid=SID, cwd=CWD, ts=1500.0,
                      event="Stop", state="idle"):
    (_panes(tmp_path) / f"{pane_num}.json").write_text(json.dumps(
        {"pane": f"%{pane_num}", "session_id": sid, "cwd": cwd,
         "event": event, "tool": "", "ts": ts, "state": state}))


def _projects(tmp_path):
    d = tmp_path / "projects" / "-home-testuser-repos-second-brain"
    d.mkdir(parents=True, exist_ok=True)
    return tmp_path / "projects"


def _write_transcript(tmp_path, *, sid=SID, alias=ALIAS, assistant_turns=1):
    """A green transcript: the spawn-injected init declaration ('You are <alias>')
    + `assistant_turns` assistant messages (the agent ran)."""
    lines = [{"type": "user", "message": {"role": "user",
              "content": f"You are {alias} ({alias}), a T2 agent. Read /tmp/agent-init-{alias}.md."}}]
    for i in range(assistant_turns):
        lines.append({"type": "assistant", "message": {"role": "assistant",
                      "content": [{"type": "text", "text": f"turn {i}"}]}})
    p = _projects(tmp_path) / "-home-testuser-repos-second-brain" / f"{sid}.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


def _seed_bg(tmp_path, *, pane_pid=4242, spawned_at=SPAWNED_AT):
    wal = str(tmp_path / "wal")
    os.makedirs(wal, exist_ok=True)
    st = BgStateStore(wal, ROOT)
    st.write_meta("green_pane_pid", pane_pid)
    st.write_meta("green_spawned_at", spawned_at)
    return wal


def _call(tmp_path, *, wal, pane_num="215", cur_pane_pid=4242,
          expected_cwd=CWD, projects_root=None):
    return green_liveness.green_is_live(
        ROOT, ALIAS, wal_dir=wal, orchestra_dir=str(tmp_path),
        expected_cwd=expected_cwd,
        session_pane_fn=lambda a: (f"%{pane_num}", cur_pane_pid),
        panes_dir=str(_panes(tmp_path)),
        projects_root=str(projects_root or _projects(tmp_path)))


# ---- the load-bearing frozen-green cases (must be False) ----

def test_frozen_at_trust_no_transcript_is_not_live(tmp_path):
    """The live incident: green stuck at the trust dialog wrote NO transcript ->
    find_transcript None -> NOT live (even though the channel probe passes)."""
    wal = _seed_bg(tmp_path)
    _write_pane_event(tmp_path, "215")           # pane event exists (SessionEnd/kill)
    # NO transcript written
    assert green_liveness.green_is_live(
        ROOT, ALIAS, wal_dir=wal, orchestra_dir=str(tmp_path), expected_cwd=CWD,
        session_pane_fn=lambda a: ("%215", 4242), panes_dir=str(_panes(tmp_path)),
        projects_root=str(_projects(tmp_path))) is False


def test_post_trust_hang_transcript_zero_assistant_turns_is_not_live(tmp_path):
    """The post-trust init hang: transcript created + init prompt received but the
    agent never produced an assistant turn -> NOT live (this is why signal A fails)."""
    wal = _seed_bg(tmp_path)
    _write_pane_event(tmp_path, "215")
    _write_transcript(tmp_path, assistant_turns=0)
    assert _call(tmp_path, wal=wal) is False


def test_live_green_with_assistant_turn_is_live(tmp_path):
    """A green that booted past trust, ingested, and ran (>=1 assistant turn) ->
    live True (all bindings satisfied)."""
    wal = _seed_bg(tmp_path)
    _write_pane_event(tmp_path, "215")
    _write_transcript(tmp_path, assistant_turns=3)
    assert _call(tmp_path, wal=wal) is True


# ---- the catastrophic false-PASS bindings (peer-mandated) ----

def test_pane_reused_different_pid_is_not_live(tmp_path):
    """A reused pane: the green session's CURRENT pane pid != the recorded spawn pid
    -> the green process is gone/replaced -> NOT live (never promote a dead green)."""
    wal = _seed_bg(tmp_path, pane_pid=4242)
    _write_pane_event(tmp_path, "215")
    _write_transcript(tmp_path, assistant_turns=5)   # a live sid's transcript
    assert _call(tmp_path, wal=wal, cur_pane_pid=9999) is False   # pid mismatch


def test_stale_pane_event_before_spawn_is_not_live(tmp_path):
    """tmux-server-restart hazard: a stale panes/<N>.json from an OLD session (ts <
    green_spawned_at) whose transcript HAS turns -> ts-fence rejects -> NOT live."""
    wal = _seed_bg(tmp_path, spawned_at=2000.0)
    _write_pane_event(tmp_path, "215", ts=500.0)     # stale (before spawn)
    _write_transcript(tmp_path, assistant_turns=5)
    assert _call(tmp_path, wal=wal) is False


def test_pane_event_wrong_cwd_is_not_live(tmp_path):
    """A reused pane carrying a different agent's cwd -> cwd mismatch -> NOT live."""
    wal = _seed_bg(tmp_path)
    _write_pane_event(tmp_path, "215", cwd="/home/testuser/some-other-repo")
    _write_transcript(tmp_path, assistant_turns=5)
    assert _call(tmp_path, wal=wal) is False


def test_transcript_declares_different_identity_is_not_live(tmp_path):
    """The pane-reuse-stale-sid catastrophe: the pane event's sid resolves to a
    DIFFERENT live agent's transcript (has assistant turns) but it declares another
    identity -> declared_identity != green_alias -> NOT live (no promote-dead)."""
    wal = _seed_bg(tmp_path)
    _write_pane_event(tmp_path, "215")
    # transcript exists for SID with turns, but declares a DIFFERENT agent
    _write_transcript(tmp_path, alias="some-other-agent", assistant_turns=5)
    assert _call(tmp_path, wal=wal) is False


# ---- fail-closed resolution failures ----

def test_no_recorded_pane_pid_is_not_live(tmp_path):
    wal = str(tmp_path / "wal"); os.makedirs(wal, exist_ok=True)
    BgStateStore(wal, ROOT).write_meta("green_spawned_at", SPAWNED_AT)  # no pid
    assert _call(tmp_path, wal=wal) is False


def test_green_session_gone_is_not_live(tmp_path):
    wal = _seed_bg(tmp_path)
    _write_pane_event(tmp_path, "215")
    _write_transcript(tmp_path, assistant_turns=5)
    assert green_liveness.green_is_live(
        ROOT, ALIAS, wal_dir=wal, orchestra_dir=str(tmp_path), expected_cwd=CWD,
        session_pane_fn=lambda a: None,                 # session gone
        panes_dir=str(_panes(tmp_path)),
        projects_root=str(_projects(tmp_path))) is False


def test_no_pane_event_is_not_live(tmp_path):
    wal = _seed_bg(tmp_path)
    _write_transcript(tmp_path, assistant_turns=5)      # no pane event file
    assert _call(tmp_path, wal=wal) is False


def test_pane_event_null_sid_is_not_live(tmp_path):
    wal = _seed_bg(tmp_path)
    _write_pane_event(tmp_path, "215", sid="")
    assert _call(tmp_path, wal=wal) is False


def test_exception_in_resolution_fails_closed(tmp_path):
    wal = _seed_bg(tmp_path)
    def boom(a):
        raise RuntimeError("tmux exploded")
    assert green_liveness.green_is_live(
        ROOT, ALIAS, wal_dir=wal, orchestra_dir=str(tmp_path), expected_cwd=CWD,
        session_pane_fn=boom, panes_dir=str(_panes(tmp_path)),
        projects_root=str(_projects(tmp_path))) is False


def test_pane_id_percent_prefix_normalized(tmp_path):
    """state-event-hook writes lstrip('%')+.json (215.json); the session_pane_fn
    returns '%215' -> resolver must strip '%'."""
    wal = _seed_bg(tmp_path)
    _write_pane_event(tmp_path, "215")
    _write_transcript(tmp_path, assistant_turns=1)
    # pane_num returned WITH the % prefix; the file is 215.json
    assert _call(tmp_path, wal=wal, pane_num="215") is True


# ---- fix #5: runtime-keyed liveness registry (gemini green has NO claude pane-event /
#      assistant-jsonl transcript) — the sweep's closing item. -----------------------

def test_liveness_registry_has_claude_gemini_and_codex():
    """GATE (widened): the liveness seam must NOT be claude-only (the re-fire #4 blocker).
    GREEN_LIVENESS_REGISTRY must carry EVERY spawnable runtime key, mirroring
    CTX_ADAPTER_REGISTRY — claude, gemini AND codex (provider-agnostic rotation)."""
    reg = green_liveness.GREEN_LIVENESS_REGISTRY
    assert "claude" in reg and "gemini" in reg and "codex" in reg


def test_gemini_green_is_live_by_pid_cid_and_step(tmp_path):
    """A gemini green proves LIVE WITHOUT a claude pane-event/assistant transcript:
    recorded pane pid matches the live pane, its brain declares 'You are <alias>'
    (resolve_gemini_cid resolves a cid), AND it produced >=1 step (a turn by effect)."""
    wal = str(tmp_path)
    BgStateStore(wal, ROOT).write_meta("green_pane_pid", 4242)
    live = green_liveness.gemini_green_is_live(
        ROOT, ALIAS, wal_dir=wal, orchestra_dir=str(tmp_path), expected_cwd=None,
        session_pane_fn=lambda a: ("%7", 4242),           # live pane, pid matches
        cid_fn=lambda a: "d691f18f-cid" if a == ALIAS else None,
        progress_fn=lambda cid: 3)                         # >=1 step -> a turn ran
    assert live is True


def test_gemini_green_not_live_when_no_turn_or_pid_mismatch(tmp_path):
    wal = str(tmp_path)
    st = BgStateStore(wal, ROOT)
    st.write_meta("green_pane_pid", 4242)
    # (i) zero steps -> not a live turn yet
    assert green_liveness.gemini_green_is_live(
        ROOT, ALIAS, wal_dir=wal, orchestra_dir=str(tmp_path),
        session_pane_fn=lambda a: ("%7", 4242),
        cid_fn=lambda a: "cid", progress_fn=lambda cid: 0) is False
    # (ii) pane pid mismatch -> pane reused / process replaced
    assert green_liveness.gemini_green_is_live(
        ROOT, ALIAS, wal_dir=wal, orchestra_dir=str(tmp_path),
        session_pane_fn=lambda a: ("%7", 9999),
        cid_fn=lambda a: "cid", progress_fn=lambda cid: 5) is False
    # (iii) no cid (brain never declared identity) -> not resolvable
    assert green_liveness.gemini_green_is_live(
        ROOT, ALIAS, wal_dir=wal, orchestra_dir=str(tmp_path),
        session_pane_fn=lambda a: ("%7", 4242),
        cid_fn=lambda a: None, progress_fn=lambda cid: 5) is False


def test_dispatch_unknown_runtime_fails_closed(tmp_path):
    wal = str(tmp_path)
    assert green_liveness.green_is_live(
        ROOT, ALIAS, runtime="no-such-runtime", wal_dir=wal,
        orchestra_dir=str(tmp_path), expected_cwd=None) is False
