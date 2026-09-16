"""green_liveness — the Layer-2 green-AGENT-liveness gate (congruence DEC-1788655588,
both peers APPROVE signal B).

verify_green (bar#4) proves CHANNEL + WAL-recall fidelity, but its probe is bg_arm-
invoked ORCHESTRATOR-side, so a green frozen/hung PRE-READY (the Claude Code trust
dialog, or any post-trust init hang) still verifies GREEN — and a false-verify reaps
the WORKING blue and promotes a DEAD pane (total loss). This gate closes it: the GREEN
PROCESS must have produced >=1 assistant turn in its OWN transcript (a frozen-at-trust
green wrote NONE, verified by effect on the first real fire).

Deterministic (turn EXISTENCE only — never an LLM answer-grade). Fail-CLOSED. Every
binding ties the transcript to THIS green so a reused pane number / stale pane event /
tmux-server restart cannot false-PASS:
  * the green session's CURRENT pane pid == the recorded spawn pid (process unchanged);
  * the pane event is fresh (ts >= green_spawned_at) and in the green's cwd;
  * the transcript (bare-sid glob) DECLARES green_alias (sid_invariants.declared_identity);
  * >=1 assistant turn (the agent actually ran).
The gate is ANDed with the channel probe in verify_green — strictly stricter, never a
weakening of bar#4.
"""
import json
import os


def _read_pane_event(panes_dir, pane_id):
    # state-event-hook.py writes `pane.lstrip("%") + ".json"`; tmux returns "%N".
    num = str(pane_id).lstrip("%")
    try:
        with open(os.path.join(panes_dir, f"{num}.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _has_assistant_turn(transcript):
    """>=1 jsonl line with type=='assistant' AND message.role=='assistant' (the agent
    produced a turn). Early-exit; content is NOT inspected (deterministic, no grade)."""
    try:
        with open(transcript, errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "assistant":
                    m = d.get("message") or {}
                    if isinstance(m, dict) and m.get("role") == "assistant":
                        return True
        return False
    except OSError:
        return False


def _default_session_pane(green_alias):
    """(pane_id, pane_pid) for the green session's pane, or None (session gone)."""
    import subprocess
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", f"{green_alias}:0.0",
             "#{pane_id} #{pane_pid}"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        parts = r.stdout.split()
        return (parts[0], int(parts[1])) if len(parts) == 2 else None
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def claude_green_is_live(root, green_alias, *, wal_dir, orchestra_dir, expected_cwd=None,
                         session_pane_fn=None, panes_dir=None, projects_root=None, **_kw):
    """CLAUDE liveness adapter (today's behavior, unchanged): the green's claude
    pane-event (session_id) + its ~/.claude transcript declaring green_alias + >=1
    assistant turn. A gemini green writes NEITHER, so this fail-closes for it — which is
    why the liveness seam MUST be runtime-keyed (see gemini_green_is_live)."""
    try:
        from .bg_state import BgStateStore
        st = BgStateStore(wal_dir, root)
        rec_pid = st.read_meta("green_pane_pid", None)
        spawned_at = st.read_meta("green_spawned_at", None)
        if rec_pid is None or spawned_at is None:
            return False

        cur = (session_pane_fn or _default_session_pane)(green_alias)
        if not cur:
            return False           # green session gone
        pane_id, cur_pid = cur
        if cur_pid != rec_pid:
            return False           # pane reused / green process replaced

        panes_dir = panes_dir or os.path.join(
            orchestra_dir, "state", "agent-events", "panes")
        pev = _read_pane_event(panes_dir, pane_id)
        if not pev:
            return False
        if (pev.get("ts") or 0) < spawned_at:
            return False           # stale pane event (tmux-server restart %N reuse)
        if expected_cwd is not None and pev.get("cwd") != expected_cwd:
            return False           # reused pane carrying a different agent's cwd
                                   # (belt+suspenders; declared_identity is the anchor)
        sid = pev.get("session_id")
        if not sid:
            return False

        # transcript + identity via the shared helpers (bare-sid glob, not slug(cwd))
        import sys
        _scripts = os.path.join(orchestra_dir, "scripts")
        if os.path.isdir(_scripts) and _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        from sid_invariants import find_transcript, declared_identity
        from pathlib import Path
        transcript = find_transcript(
            sid, projects_root=Path(projects_root) if projects_root else None)
        if not transcript:
            return False           # frozen-at-trust: no transcript at all
        if declared_identity(transcript, {green_alias}) != green_alias:
            return False           # transcript is not THIS green's (stale/reused sid)
        return _has_assistant_turn(transcript)
    except Exception:              # noqa: BLE001 — fail-closed; never promote on error
        return False


def gemini_green_is_live(root, green_alias, *, wal_dir, orchestra_dir=None,
                         expected_cwd=None, session_pane_fn=None,
                         cid_fn=None, progress_fn=None, **_kw):
    """GEMINI liveness adapter (fix #5). A gemini green runs NO claude SessionStart hook,
    so it writes no claude pane-event and no assistant-jsonl transcript — the claude gate
    can never pass it. Prove it live by effect from GEMINI-shaped evidence instead:
      * the recorded green pane pid (spawn_green, provider-neutral) == the CURRENT live
        pane pid for green_alias (process unchanged, not a reused pane);
      * its brain declares 'You are <green_alias>' — resolve_gemini_cid resolves a cid
        (identity anchor, mirrors the ctx/#1 path);
      * it produced >=1 step by effect (a turn actually ran) — green_progress_any(cid)>=1.
    Fail-CLOSED on any miss. cid_fn/progress_fn/session_pane_fn injected for tests."""
    try:
        from .bg_state import BgStateStore
        rec_pid = BgStateStore(wal_dir, root).read_meta("green_pane_pid", None)
        if rec_pid is None:
            return False
        cur = (session_pane_fn or _default_session_pane)(green_alias)
        if not cur:
            return False           # green session gone
        _pane_id, cur_pid = cur
        if cur_pid != rec_pid:
            return False           # pane reused / green process replaced
        if cid_fn is None:
            from .ctx_adapters import resolve_gemini_cid as _rc
            cid_fn = _rc
        cid = cid_fn(green_alias)
        if not cid:
            return False           # brain never declared 'You are <alias>' (not booted)
        if progress_fn is None:
            from .ctx_adapters import green_progress_any as _gp
            progress_fn = _gp
        prog = progress_fn(cid)
        return bool(prog is not None and prog >= 1)   # >=1 turn by effect
    except Exception:              # noqa: BLE001 — fail-closed; never promote on error
        return False


def codex_green_is_live(root, green_alias, *, wal_dir, orchestra_dir=None,
                        expected_cwd=None, session_pane_fn=None,
                        cid_fn=None, progress_fn=None, **_kw):
    """CODEX liveness adapter — SAME 3 by-effect conditions as gemini_green_is_live (a codex
    green runs no claude SessionStart hook, so the claude gate can never pass it), anchored by
    CODEX-shaped evidence:
      * recorded green pane pid == the CURRENT live pane pid for green_alias;
      * its rollout declares 'You are <green_alias>' — resolve_codex_cid resolves a sid;
      * it produced >=1 turn by effect — green_progress_any(sid) >= 1 (codex task_started).
    Fail-CLOSED on any miss. Differs from the gemini adapter ONLY by the default cid resolver;
    progress is provider-agnostic via green_progress_any (GREEN_PROGRESS_REGISTRY has codex)."""
    try:
        from .bg_state import BgStateStore
        rec_pid = BgStateStore(wal_dir, root).read_meta("green_pane_pid", None)
        if rec_pid is None:
            return False
        cur = (session_pane_fn or _default_session_pane)(green_alias)
        if not cur:
            return False           # green session gone
        _pane_id, cur_pid = cur
        if cur_pid != rec_pid:
            return False           # pane reused / green process replaced
        if cid_fn is None:
            from .ctx_adapters import resolve_codex_cid as _rc
            cid_fn = _rc
        cid = cid_fn(green_alias)
        if not cid:
            return False           # rollout never declared 'You are <alias>' (not booted)
        if progress_fn is None:
            from .ctx_adapters import green_progress_any as _gp
            progress_fn = _gp
        prog = progress_fn(cid)
        return bool(prog is not None and prog >= 1)   # >=1 turn by effect
    except Exception:              # noqa: BLE001 — fail-closed; never promote on error
        return False


# ---- the liveness registry: runtime -> liveness adapter (data lookup; NOT claude-only) ----
GREEN_LIVENESS_REGISTRY = {
    "claude": claude_green_is_live,
    "gemini": gemini_green_is_live,
    "codex": codex_green_is_live,
}


def green_is_live(root, green_alias, *, runtime=None, **kw):
    """Runtime-keyed liveness dispatcher (the seam the sweep de-Claude-shaped). A named
    runtime dispatches to its adapter (unknown named runtime => False, fail-closed). When
    runtime is None (the beat's _lf does not thread it), try EVERY adapter — only the
    green's OWN runtime adapter can pass (each is fail-closed on the wrong runtime's
    evidence), so this is a safe provider-agnostic OR, mirroring green_progress_any /
    resolve_cid_any. Backward-compatible: existing callers/tests pass no runtime and get
    the claude adapter's result on claude evidence."""
    if runtime is not None:
        fn = GREEN_LIVENESS_REGISTRY.get(str(runtime).strip().lower())
        if fn is None:
            return False           # unknown named runtime => fail-closed
        try:
            return bool(fn(root, green_alias, **kw))
        except Exception:          # noqa: BLE001 — fail-closed
            return False
    for fn in GREEN_LIVENESS_REGISTRY.values():
        try:
            if fn(root, green_alias, **kw):
                return True
        except Exception:          # noqa: BLE001 — fail-closed per adapter
            continue
    return False
