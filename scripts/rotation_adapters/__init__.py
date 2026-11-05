"""Rotation adapters, one per runtime (base.py has the interface). Only Claude is built; the others refuse."""
from .base import Adapter, RotationUnsupported, SessionHandle, capability_diff  # noqa: F401

_REFUSED = {"codex", "gemini", "grok"}


def adapter_for(runtime: str, **kw) -> Adapter:
    """The adapter for a runtime, built with the supervisor's process details (argv_for, env, cwd, ...). Raises
    RotationUnsupported with the reason to show for any runtime without a built and tested adapter."""
    rt = (runtime or "claude").strip().lower()
    if rt == "claude":
        from .claude import ClaudeAdapter
        return ClaudeAdapter(**kw)
    if rt in _REFUSED:
        raise RotationUnsupported(f"{rt} rotation is not supported yet: its adapter isn't built and tested")
    raise RotationUnsupported(f"{rt or 'this runtime'} rotation is not supported yet: its adapter isn't built and "
                              f"tested")


def refusal(runtime: str) -> str | None:
    """Why this runtime cannot rotate, or None. Builds nothing."""
    rt = (runtime or "claude").strip().lower()
    return None if rt == "claude" else (f"{rt} rotation is not supported yet: its adapter isn't built and tested")
