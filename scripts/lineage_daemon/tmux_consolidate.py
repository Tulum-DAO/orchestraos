"""tmux_consolidate.py — the SHARED tmux session-consolidation helper (orchestra-builder
approved interface, msg_3b8fe585).

Closes the recurring D4 gap in BOTH directions: promote_successor (and any promote-class
flow) sets the registry `tmux_session` FIELD but does not RENAME the actual tmux session,
so the state-snapshot reconciler (which reads `tmux_session or agent_id`) re-crosses the
canonical sid back to the predecessor. This helper renames the ACTUAL session, restamps
the 3-store field, then RE-VERIFIES the canonical sid still resolves to the expected sid
(fail-closed if not — the reconciler-re-cross is caught here, not discovered later).

    consolidate_session(old_name, new_name, expected_sid) -> {ok, renamed, verified, reason}

Pure over injected seams; the real defaults wrap `tmux rename-session` + a 3-store restamp
+ a sid resolve. FORWARD promote_successor adopts this (orchestra-builder's standing fix);
the reworked prompt-retire/promote flow uses it too for clean consolidation.
"""


def _tmux_has_session(name) -> bool:
    import subprocess
    try:
        return subprocess.run(["tmux", "has-session", "-t", name],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10).returncode == 0
    except Exception:  # noqa: BLE001 -- absent tmux / error => treat as not-present
        return False


def _tmux_rename(old, new) -> bool:
    import subprocess
    try:
        return subprocess.run(["tmux", "rename-session", "-t", old, new],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def consolidate_session(old_name, new_name, expected_sid, *,
                        restamp_fn, resolve_sid_fn,
                        session_exists_fn=None, rename_fn=None):
    """Rename the actual tmux session `old_name` -> `new_name`, restamp the 3-store
    `tmux_session` field to `new_name`, and re-verify the canonical sid still resolves to
    `expected_sid`. Returns {ok, renamed, verified, reason}. Fail-closed on a missing
    session, a failed rename, or a post-restamp sid mismatch (NOTHING restamped on a
    failed rename). Idempotent: if `old_name` is already gone but `new_name` exists (a
    prior partial run), skip the rename and just restamp + verify.

    Seams (all injectable; real defaults wrap tmux + the 3-store):
      session_exists_fn(name) -> bool   : `tmux has-session -t name`.
      rename_fn(old, new)     -> bool   : `tmux rename-session -t old new` (True on rc0).
      restamp_fn(new_name)    -> None   : write tmux_session=new_name to the 3 stores.
      resolve_sid_fn(new_name)-> str|None: the sid the 3-store now maps `new_name` to.

    `restamp_fn` + `resolve_sid_fn` are CALLER-SUPPLIED (each promote-class flow owns its
    3-store shapes); `session_exists_fn` + `rename_fn` default to real `tmux` subprocess.
    """
    session_exists_fn = session_exists_fn or _tmux_has_session
    rename_fn = rename_fn or _tmux_rename
    renamed = False
    if session_exists_fn(old_name):
        if not rename_fn(old_name, new_name):
            return {"ok": False, "renamed": False, "verified": False,
                    "reason": "rename-failed"}
        renamed = True
    elif not session_exists_fn(new_name):
        # neither the old nor the target session exists -> nothing to consolidate.
        return {"ok": False, "renamed": False, "verified": False,
                "reason": "no-session"}
    # else: already named new_name (idempotent prior run) -> restamp + verify only.

    restamp_fn(new_name)

    got = resolve_sid_fn(new_name)
    verified = (got == expected_sid)
    return {"ok": verified, "renamed": renamed, "verified": verified,
            "reason": None if verified else "sid-verify-failed"}
