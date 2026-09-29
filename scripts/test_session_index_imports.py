"""session-index.py must import on the repo's DEFAULT python3, not just the 3.12 the
supervisor happens to use.

It didn't: five signatures use PEP 604 unions (`dict | None`), which are evaluated at
import time before Python 3.10, so `session-index.py scan` died with
"TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'" under python3.9.
Combined with nothing calling the updater, state/agent-sessions.json sat at {} from
2026-09-19 and every transcript reader — the /field dashboard, gm spawn/resume,
agent-recovery, promote_successor — saw an empty map.

This test runs under whatever interpreter pytest is using, so it fails on the oldest
supported one rather than passing only where someone happened to develop.
"""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "session-index.py"


def test_session_index_imports_on_this_interpreter():
    spec = importlib.util.spec_from_file_location("session_index_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # raises TypeError on <3.10 without the fix
    assert hasattr(mod, "lookup_agent"), "module imported but looks wrong"


def test_pep604_annotations_stay_lazy():
    """The one-line guard that makes the five `X | None` signatures safe."""
    src = SCRIPT.read_text()
    assert "from __future__ import annotations" in src, (
        "session-index.py uses `X | None` annotations; without the future import they are "
        "evaluated at import time and the module dies on python < 3.10")


def test_it_would_have_caught_the_original_break():
    """Guard the guard: prove `dict | None` really is fatal on this interpreter."""
    if sys.version_info >= (3, 10):
        return                              # natively supported; nothing to prove here
    try:
        eval("dict | None")
        raise AssertionError("expected PEP 604 to be unsupported on this interpreter")
    except TypeError:
        pass
