"""INTEGRATION test (gm gen38 required) — bind real_seams to the REAL
identity_writer, NO FAKE. This closes the fake-masks-missing-binding gap that
made a prior base DOA: real_seams.make_project_now_fn resolved
`identity_writer.project_now` which a refactored base had REMOVED, so every
prewarm/swap would have aborted pre-spawn — but the unit tests faked the seam and
never caught it.

These tests bind the wrappers to the actual live `identity_writer` module and
assert each seam resolves to a REAL CALLABLE (not the raise/None path), so an arm
built on this base cannot be silently DOA on a missing/renamed seam.
"""
import sys

sys.path.insert(0, "scripts")
from identity_store import identity_writer  # noqa: E402
from lineage_daemon.wal.real_seams import (  # noqa: E402
    make_swap_fn, make_register_provisional_fn, make_project_now_fn)


def _blue_record():
    return {"name": "identity-store-builder", "generation": 2,
            "session_id": "0ed9c5d7", "status": "online", "tier": "T2"}


def test_project_now_seam_resolves_a_real_callable_not_the_raise_path():
    """The F3 seam MUST bind to a live identity_writer.project_now. If the base
    lacks it, make_project_now_fn's inner call raises 'unavailable' — this test
    fails LOUD instead of the arm being silently DOA in production."""
    assert callable(getattr(identity_writer, "project_now", None)), (
        "identity_writer.project_now is REQUIRED on the arm's base (F3 seam); "
        "a base without it makes every prewarm/swap abort pre-spawn (DOA arm)")
    fn = make_project_now_fn("/tmp/nonexistent-orchestra-dir")
    assert callable(fn)


def test_swap_generation_seam_present_with_expected_signature():
    """make_swap_fn binds identity_writer.swap_generation — must exist and accept
    documents= + sync_effects_owner (the DP-A2 + async-owner contract)."""
    import inspect
    assert callable(getattr(identity_writer, "swap_generation", None))
    params = inspect.signature(identity_writer.swap_generation).parameters
    assert "documents" in params
    assert "sync_effects_owner" in params
    # the wrapper itself binds without error
    assert callable(make_swap_fn("/tmp/x", blue_record=_blue_record()))


def test_register_provisional_seam_resolves_real_callable():
    assert callable(getattr(identity_writer, "register_provisional", None))
    assert callable(make_register_provisional_fn("/tmp/x"))


def test_swap_precondition_error_type_is_importable():
    # the fail-closed contract depends on this exception existing on the base
    from identity_store.identity_writer import SwapPreconditionError
    assert issubclass(SwapPreconditionError, Exception)
