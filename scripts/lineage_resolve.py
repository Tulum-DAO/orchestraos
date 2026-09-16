"""lineage_resolve -- the resolve-at-send-time hook for external inject-callers.

R2 requires every external injector (gateway `verified_inject`, Arturo gm-inject,
approval_resume) to resolve `agent_id -> live head session` at the MOMENT of
inject, not from a cached sid. This module EXPOSES that resolve call so callers
can adopt it without us editing their live services (per the WS1 addendum: "the
caller resolves first ... expose the resolve call").

It reuses the single source of truth -- `resolve_delivery_target` in
message-router.py (loaded via importlib because that filename has a hyphen) --
so name/sid resolution stays identical to inter-agent mail. Liveness is
tmux-authoritative (state files lie); `sessions`/`meta` are injectable so this is
hermetic in tests and never touches tmux there.
"""
import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MR_PATH = os.path.join(_ROOT, "scripts", "message-router.py")

_mr = None


def _router():
    """Lazy-load message-router.py once (hyphenated filename -> importlib)."""
    global _mr
    if _mr is None:
        spec = importlib.util.spec_from_file_location("message_router", _MR_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _mr = mod
    return _mr


def resolve_live_head(agent_id, sessions=None, meta=None):
    """Resolve an agent_id/name to its LIVE HEAD tmux session AT CALL TIME.

    Returns (session|None, reason). session=None means do NOT inject (keep the
    durable record / alert per reason -- e.g. 'no-live-head', 'ambiguous-
    lineage', 'unknown-address'). `forwarded_from` provenance from the underlying
    resolver is intentionally dropped here: an injector only needs the target.

    sessions/meta default to LIVE tmux + agent-sessions.json (production); pass
    them explicitly to resolve against a fixture (tests, dry-run).
    """
    mr = _router()
    if sessions is None:
        sessions = mr.live_sessions()
    if meta is None:
        meta = mr.load_agent_meta()
    session, _forwarded_from, reason = mr.resolve_delivery_target(
        agent_id, sessions, meta)
    return session, reason


def resolve_live_head_batch(agent_ids, sessions=None, meta=None):
    """READ-ONLY batch resolve: many agent_ids against ONE shared snapshot.

    Builds the live-tmux + agent-sessions.json snapshot ONCE (unless injected),
    then resolves every id against it -- so a caller polling ~50 agents pays a
    single tmux/file read per refresh instead of one per id. Pure read; writes
    nothing. Returns {agent_id: {"session": session_or_None, "reason": reason}}.
    session_or_None + reason carry the SAME semantics as resolve_live_head (None =
    no live head; reason in {'succeeded-by-chain','no-live-head','ambiguous-
    lineage','unknown-address',...}).
    """
    mr = _router()
    if sessions is None:
        sessions = mr.live_sessions()
    if meta is None:
        meta = mr.load_agent_meta()
    out = {}
    for aid in agent_ids:
        session, _forwarded_from, reason = mr.resolve_delivery_target(
            aid, sessions, meta)
        out[aid] = {"session": session, "reason": reason}
    return out


if __name__ == "__main__":
    # Thin read-only CLI: agent ids from argv OR stdin (one per line) -> a single
    # JSON map {id: {session, reason}} on stdout. Builds the snapshot once. For
    # external pollers (e.g. the :7373 firehose) to reuse the canonical lineage
    # resolver without forking succession semantics. Never writes / never injects.
    import json
    import sys
    _ids = [a for a in sys.argv[1:] if a.strip()]
    if not _ids:
        _ids = [ln.strip() for ln in sys.stdin if ln.strip()]
    json.dump(resolve_live_head_batch(_ids), sys.stdout)
    sys.stdout.write("\n")
