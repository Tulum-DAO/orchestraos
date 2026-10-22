"""codex r3: boot_inject and nudge_pane journal their typed-not-entered text too (inject_journal):
a row exists from before typing until a verified submit; a crash in between leaves evidence for
the gateway's next-start reconcile. No row, no typing."""
import importlib, os, sys
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
J = importlib.import_module("inject_journal")
B = importlib.import_module("boot_inject")
N = importlib.import_module("nudge_pane")


@pytest.fixture(autouse=True)
def fence(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    monkeypatch.setenv("PANE_LOCK_DIR", str(tmp_path / "locks"))


def test_boot_inject_row_cleared_on_submit_kept_on_stuck(monkeypatch):
    seen = {}
    def fake(session, text, runtime, **kw):
        seen["row_while_typing"] = [r["session"] for r in J.rows()]
        return seen["out"]
    monkeypatch.setattr(B, "_boot_inject_unlocked", fake)
    seen["out"] = ("submitted", "enter-1")
    assert B.boot_inject("pm-x", "hello") == ("submitted", "enter-1")
    assert seen["row_while_typing"] == ["pm-x"] and J.rows() == []
    seen["out"] = ("stuck", "stuck")
    B.boot_inject("pm-x", "hello")
    assert [r["session"] for r in J.rows()] == ["pm-x"]


def test_boot_inject_refuses_when_the_row_cannot_be_written(monkeypatch):
    monkeypatch.setattr(J, "record", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    monkeypatch.setattr(B, "_boot_inject_unlocked", lambda *a, **k: pytest.fail("typed without a row"))
    assert B.boot_inject("pm-x", "hello") == ("failed", "journal-unwritable")


def test_nudge_row_exists_while_typed_and_clears_on_submit(monkeypatch):
    calls, state = [], {"composer": ""}
    def tmux(*a, **k):
        calls.append(a)
        if a[0] == "send-keys" and a[-1] == "Enter":
            state["row_at_enter"] = [r["session"] for r in J.rows()]
        class R: returncode, stdout, stderr = 0, "", ""
        return R()
    monkeypatch.setattr(N, "tmux", tmux)
    monkeypatch.setattr(N, "attached", lambda s: False)
    # a real pane: our nudge shows in the composer once typed, and leaves it at the Enter
    monkeypatch.setattr(N, "composer_line",
                        lambda s: "" if (not calls or "row_at_enter" in state) else "continue please")
    monkeypatch.setattr(N, "pane_readable", lambda s: True, raising=False)
    monkeypatch.setattr(N, "fence_check", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(N.time, "sleep", lambda s: None)
    import inspect
    if "read_composer_state" in dir(N):
        monkeypatch.setattr(N, "read_composer_state", lambda s: {"input": "empty", "text": "", "busy": False, "source": "t"}, raising=False)
    rc = N.nudge("seat", "continue please")
    assert rc == 0, calls
    assert state.get("row_at_enter") == ["seat"] and J.rows() == []
