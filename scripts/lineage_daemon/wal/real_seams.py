"""real_seams — stage-4 wrappers filling bg_arm's dependency-injected seams
against the LIVE identity store. Thin adapters ONLY; no orchestration change.

The swap seam is the load-bearing one:
  * wraps ``identity_writer.swap_generation`` with ``sync_effects_owner=False``
    (the async r-a-b contract: identity commits, effects labeled
    'effects-incomplete', bg_arm's own effect layer completes/reaps);
  * builds ``documents=`` via build_swap_documents so the full successor +
    predecessor-archive records persist IN the swap txn — a fail-closed swap
    (SwapPreconditionError) therefore writes NOTHING;
  * a ``False`` return means cutover is inactive. On OUR path that is an
    INVARIANT VIOLATION (bg_arm's ArmRefused guarantees bg_enabled ⊆
    cutover-active, so we only ever swap under active cutover) → raise
    SwapInvariantViolation rather than silently no-op.

``project_now`` is imported LAZILY: the canonical seam is
``identity_writer.project_now`` on the live line; a branch that predates it still
lets the other seams build/test (the arm injects a fake in unit tests).
"""
import os
from dataclasses import dataclass

from identity_store import identity_writer
from identity_store import orchestra_db
from .swap_documents import build_swap_documents


class SwapInvariantViolation(Exception):
    """swap_generation returned False (cutover inactive) on the async arm's path,
    which bg_enabled ⊆ cutover-active should make impossible. Hard error, never a
    silent legacy fallback (there is no legacy path for a blue-green async swap)."""


@dataclass
class _Outcome:
    status: str
    swap_id: int = 0
    green_generation_id: int = 0
    needs_degraded: bool = False


def make_swap_fn(orchestra_dir, blue_record, cwd=None, now=None):
    """Return a swap_fn(root, green, blue_generation_id) for bg_arm's swap seam.

    green must carry {generation, session_id, model, resume_command?}. The wrapper
    builds documents from the successor gen + the predecessor archive and calls
    swap_generation. Returns an outcome with status 'effects-incomplete' (the
    async default — bg_arm's effect layer then reaps + marks complete)."""
    def swap_fn(root, green, blue_generation_id=None):
        # P0.4 (leg-(ii)): bind canonical to the green's ACTUAL gen-suffixed pane
        # {root}-g{N} (Astra §C immutable execution binding) — NOT the literal root
        # (blue's dead pane post-swap). Carried in green so execute_swap +
        # build_swap_documents both write it; a copy so we never mutate the caller's obs.
        green = {**green, "tmux_session": f"{root}-g{green['generation']}"}
        documents = build_swap_documents(
            root=root, blue_generation=blue_record.get("generation"),
            green=green, blue_record=blue_record, cwd=cwd, now=now)
        handled = identity_writer.swap_generation(
            orchestra_dir, root, green, blue_generation_id=blue_generation_id,
            now=now, documents=documents, sync_effects_owner=False)
        if handled is False:
            raise SwapInvariantViolation(
                f"swap_generation returned False for {root!r}: cutover inactive on "
                f"the async arm path (bg_enabled ⊆ cutover-active violated)")
        # M3 (leg-(ii)): VERIFY the green's sid landed on the canonical row BY EFFECT.
        # A swap that commits but leaves the promoted gen's session_id null/wrong is
        # the rotation landmine (canonical resolves the WRONG transcript). Only when
        # green carried a sid (a not-yet-booted green has none => nothing to verify).
        expected_sid = green.get("session_id")
        if expected_sid is not None:
            _conn = orchestra_db.get_connection(
                os.path.join(orchestra_dir, "state", "orchestra-registry.db"))
            try:
                orchestra_db.verify_session_id_attributed(
                    _conn, root, green["generation"], expected_sid)
            finally:
                _conn.close()
        # identity committed; effects are the async arm's job (effects-incomplete).
        return _Outcome(status="effects-incomplete", needs_degraded=False)
    return swap_fn


def make_register_provisional_fn(orchestra_dir):
    def register_provisional(root, green_alias, generation=None, model=None):
        return identity_writer.register_provisional(
            orchestra_dir, root, generation, model=model)
    return register_provisional


def make_project_now_fn(orchestra_dir):
    """F3 read-your-writes: synchronous reproject, RAISES on failure so bg_arm
    aborts pre-spawn. Lazily resolves the live identity_writer.project_now."""
    def project_now(root):
        fn = getattr(identity_writer, "project_now", None)
        if fn is None:
            raise RuntimeError(
                "identity_writer.project_now unavailable on this line — the F3 "
                "seam is required before spawn; refuse rather than spawn unprojected")
        return fn(orchestra_dir)
    return project_now
