"""Cutover routing for the embedded-python SHELL identity writers.

The shell writers (state-snapshot-agents.sh, spinup-orchestra-builder-v2.sh) write
the identity projections from a `python3 -c` / heredoc block that bypasses any
importable shim. ob rider 2: the guard must key on the FILE/PATH, not on a writer
voluntarily calling a shim. These helpers are the sanctioned routing the shell
invokes: under cutover they route the write to the transactional store (the
projector regenerates the JSON); flag-off they decline (return False) so the shell
does its byte-identical legacy json.dump.
"""
from scripts.identity_store import cutover, identity_writer, shims


def managed_state_write(orchestra_dir, agent_id, state_file, state) -> bool:
    """state-snapshot-agents.sh :70-71 per-agent snapshot write. Under cutover, a
    ``state/agents/*.json`` target is a projector-owned managed projection: persist
    the FULL free-form blob to the live document store (DP-A2, via write_agent_state)
    and take NO direct file write. Returns True iff handled against the DB.

    Declines (False) when: cutover inactive (INERT — caller does its json.dump), or
    the target is NOT a managed projection (a ``state/`` root file is a different,
    unmanaged location the projector does not own)."""
    if not cutover.is_active(orchestra_dir):
        return False
    if not shims.is_managed_projection(state_file):
        return False
    identity_writer.write_agent_state(orchestra_dir, agent_id, state,
                                      full_record=state)
    return True
