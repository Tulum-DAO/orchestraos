"""B2 — PRODUCTION arm hooks.

The CONVERGED provider-agnostic register_sid / start_capture (GOAL-FINDING #1 sid-register +
#5 WAL-capture-at-arm), lifted OUT of the one-off demo driver (fire_gemini_demo.py) into a
reusable production module so the operator arm-path and the demo driver share ONE
implementation and can never drift.

Every hook keys the seat's runtime/model off the CANONICAL LINEAGE (read_canonical_blue's #15
runtime join), NEVER a literal — the SAME hook arms a claude / gemini / codex seat unchanged.
All external effects are dependency-injected with live defaults (mirroring bg_supervise_fleet):
production binds the real identity store + provider adapters; tests inject fakes.

The production arm ENTRY is arm_seat(): it resolves the seat's runtime from the lineage and
calls the conformance-gated bg_state.arm_lineage with BOTH real hooks wired. This is the
first-class replacement for the demo driver's stage_arm — arming a lineage is now an operator
action on the production path, not something only the one-off fire driver can do.
"""
import glob
import os


def seat_lineage(orchestra_dir, root):
    """(runtime, model) from the canonical lineage — the SINGLE source of the seat's runtime,
    so no hook ever hardwires or guesses one. Fail-soft: () -> (None, None) (arm_lineage's
    conformance gate is the hard check; an unknown runtime is refused there)."""
    try:
        from lineage_daemon.wal.bg_beat import read_canonical_blue
        b = read_canonical_blue(orchestra_dir, root)
        return b.get("runtime"), b.get("model")
    except Exception:  # noqa: BLE001 -- fail-soft; conformance gate refuses unknown runtimes
        return None, None


def blue_generation(orchestra_dir, root, default=0):
    """Blue's current generation from the canonical DB (WAL-adapter needs it to tag events).
    Fail-soft to ``default`` so a missing row never crashes capture (WAL gate is the check)."""
    try:
        from lineage_daemon.wal.bg_beat import read_canonical_blue
        return int(read_canonical_blue(orchestra_dir, root).get("generation"))
    except Exception:  # noqa: BLE001
        return default


def _read_session_record(orchestra_dir, seat):
    """The seat's CURRENT session document (state/agent-sessions.json projection), or None.
    Read so register preserves a real seat's cwd/tier/prompt_file, overwriting only the sid +
    runtime/model. Fail-soft to None (the demo fixture has no record -> mint the default)."""
    import json
    try:
        path = os.path.join(orchestra_dir, "state", "agent-sessions.json")
        with open(path) as fh:
            return (json.load(fh) or {}).get(seat)
    except Exception:  # noqa: BLE001
        return None


def make_register_sid_fn(orchestra_dir, wal_dir, *,
                         resolve_cid_fn=None, lineage_fn=None,
                         read_record_fn=None, update_session_fn=None):
    """FINDING #1 (provider-agnostic): resolve the seat's LIVE cid via the CID_RESOLVER_REGISTRY
    (resolve_cid_any — each provider's own 'You are <seat>' declaration) and REGISTER it as
    session_id (agent-sessions.json). Runtime/model come from the lineage, never a literal, so
    the SAME hook arms claude/gemini/codex unchanged. Existing record fields (cwd/tier/
    prompt_file) are PRESERVED — only session_id + runtime/model are overwritten."""
    if resolve_cid_fn is None:
        from lineage_daemon.wal.ctx_adapters import resolve_cid_any
        resolve_cid_fn = resolve_cid_any
    if lineage_fn is None:
        lineage_fn = lambda seat: seat_lineage(orchestra_dir, seat)  # noqa: E731
    if read_record_fn is None:
        read_record_fn = lambda seat: _read_session_record(orchestra_dir, seat)  # noqa: E731
    if update_session_fn is None:
        from identity_store import identity_writer
        update_session_fn = lambda seat, fields, full_record: (  # noqa: E731
            identity_writer.update_session(orchestra_dir, seat, fields,
                                           full_record=full_record))

    def register(seat):
        runtime, model = lineage_fn(seat)
        cid = resolve_cid_fn(seat)
        if not cid:
            raise RuntimeError(
                f"register_sid: no cid for {seat} (no 'You are {seat}' declaration in any "
                f"provider store; runtime={runtime})")
        base = read_record_fn(seat) or {
            # demo-fixture / new-seat default shape (matches the converged driver's record)
            "name": seat, "tmux_session": seat, "cwd": orchestra_dir,
            "status": "online", "tier": "T2", "machine": "vps", "resumable": True,
            "prompt_file": "prompts/developer.md", "lineage_root": seat,
        }
        # overwrite ONLY the sid + the runtime identity; preserve everything else
        full_record = {**base, "session_id": cid, "runtime": runtime, "model": model}
        update_session_fn(seat, {"session_id": cid}, full_record)
        return cid

    return register


def live_seat_cwd(seat):
    """The seat's LIVE claude process cwd (tmux pane -> claude descendant -> /proc cwd), or
    None when there is no live claude process (non-claude runtimes ignore cwd anyway)."""
    try:
        from lineage_daemon.wal.ctx_adapters import _claude_proc_info
        info = _claude_proc_info(seat)
    except Exception:  # noqa: BLE001 — fail-soft: caller falls back to orchestra_dir
        return None
    return (info or {}).get("cwd") or None


def source_path_by_cid_glob(runtime, cid):
    """cwd-independent fallback for claude: ~/.claude/projects/*/<cid>.jsonl (the cid is
    globally unique, so the first hit is the transcript). None for other runtimes / no hit."""
    if (runtime or "").strip().lower() != "claude" or not cid:
        return None
    hits = glob.glob(os.path.join(os.path.expanduser("~/.claude/projects"), "*", f"{cid}.jsonl"))
    return hits[0] if hits else None


def make_start_capture_fn(orchestra_dir, wal_dir, *,
                          resolve_cid_fn=None, lineage_fn=None, source_path_fn=None,
                          exists_fn=None, store_fn=None, adapter_fn=None,
                          blue_generation_fn=None, cwd_fn=None, fallback_source_fn=None):
    """FINDING #5 (provider-agnostic): WAL capture WITH BACKFILL via the PRODUCTION dispatch —
    make_adapter(runtime) selects the provider's WAL adapter by runtime; source_path_for(
    runtime,cid) is that provider's live transcript (jsonl or db). No provider hardwiring —
    the SAME hook backfills any runtime the lineage declares. The store is ALWAYS closed."""
    if resolve_cid_fn is None:
        from lineage_daemon.wal.ctx_adapters import resolve_cid_any
        resolve_cid_fn = resolve_cid_any
    if source_path_fn is None:
        from lineage_daemon.wal.ctx_adapters import source_path_for
        source_path_fn = source_path_for
    if lineage_fn is None:
        lineage_fn = lambda seat: seat_lineage(orchestra_dir, seat)  # noqa: E731
    if exists_fn is None:
        exists_fn = os.path.exists
    if store_fn is None:
        from lineage_daemon.wal.store import WalStore
        store_fn = lambda seat: WalStore(os.path.join(wal_dir, f"{seat}.db"))  # noqa: E731
    if adapter_fn is None:
        from lineage_daemon.wal.multiplexer import make_adapter
        adapter_fn = make_adapter
    if blue_generation_fn is None:
        blue_generation_fn = lambda seat: blue_generation(orchestra_dir, seat)  # noqa: E731
    if cwd_fn is None:
        cwd_fn = live_seat_cwd
    if fallback_source_fn is None:
        fallback_source_fn = source_path_by_cid_glob

    def start_capture(seat):
        runtime, _model = lineage_fn(seat)
        cid = resolve_cid_fn(seat)
        # Foreign-cwd fix (rab root cause 2026-09-15): the claude transcript lives under the
        # seat's LIVE process cwd slug, NOT the orchestra dir — a client PM running in
        # repos/<client> resolved a nonexistent orchestra-dir path -> empty WAL -> fail-closed
        # fire (WalCaptureNotStarted). Resolve the live cwd; orchestra_dir only when no live proc.
        cwd = cwd_fn(seat) or orchestra_dir
        source = source_path_fn(runtime, cid, cwd=cwd)
        if not source or not exists_fn(source):
            # second line of defense: locate the transcript by cid regardless of cwd slug
            alt = fallback_source_fn(runtime, cid)
            if alt and exists_fn(alt):
                source = alt
            else:
                raise RuntimeError(
                    f"start_capture: no capture source for {seat} "
                    f"(runtime={runtime} cid={cid} cwd={cwd} source={source} glob={alt})")
        store = store_fn(seat)
        try:
            adapter = adapter_fn(runtime, store, seat, blue_generation_fn(seat))
            n = adapter.tail(source)
        finally:
            store.close()
        return n

    return start_capture


def arm_seat(orchestra_dir, wal_dir, root, seat=None, *,
             blue_reader=None, arm_fn=None,
             register_sid_fn=None, start_capture_fn=None, **read_ctx_kw):
    """PRODUCTION arm entry (B2). Resolves the seat's runtime from the CANONICAL LINEAGE
    (never a literal) and calls the conformance-gated bg_state.arm_lineage with BOTH real
    converged hooks wired. Returns the flag path on success; raises ArmNotConformant (from
    arm_lineage) if the provider adapter fails the arm-time gate (writes NOTHING). This is the
    first-class production replacement for the demo driver's stage_arm."""
    seat = seat or root
    if blue_reader is None:
        from lineage_daemon.wal.bg_beat import read_canonical_blue
        blue_reader = lambda r: read_canonical_blue(orchestra_dir, r)  # noqa: E731
    if arm_fn is None:
        from lineage_daemon.wal.bg_state import arm_lineage
        arm_fn = arm_lineage
    runtime = (blue_reader(root) or {}).get("runtime")
    register_sid_fn = register_sid_fn or make_register_sid_fn(orchestra_dir, wal_dir)
    start_capture_fn = start_capture_fn or make_start_capture_fn(orchestra_dir, wal_dir)
    return arm_fn(wal_dir, root, runtime, seat=seat,
                  register_sid_fn=register_sid_fn,
                  start_capture_fn=start_capture_fn,
                  **read_ctx_kw)
