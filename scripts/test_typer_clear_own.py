"""CLEAR-OWN, NEVER STRAND for the router and the nudger (gm msg_2291dee3).

A swallowed Enter used to leave the router's message (or a nudge) sitting in the agent's composer
until a person pressed Enter or cleared it. Now, under the pane lock, the typer clears its OWN text
with C-u when the composer holds exactly that text and no client is attached, verifies the line is
empty, and the row goes back to pending (router) / the nudge is re-sent next cycle. Anything else
(a person's words, our text with their words appended, a client attached) is never touched.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nudge_pane as NP  # noqa: E402

TEXT = "[MSG from orchestra-builder | low] a router delivery that gets stuck"


def _router():
    spec = importlib.util.spec_from_file_location("message_router_co", HERE / "message-router.py")
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)
    return R


def _rig(monkeypatch, R, lines, viewer=False):
    keys, seq = [], list(lines)
    monkeypatch.setattr(R, "_inject_unlocked", lambda *a, **k: "stuck")
    monkeypatch.setattr(R, "pane_split", lambda s, rt=None: ([], seq.pop(0) if len(seq) > 1 else seq[0], True))

    def tmux(*a, **k):
        keys.append(a)
        return types.SimpleNamespace(returncode=0, stdout="client0\n" if (a[0] == "list-clients" and viewer) else "")
    monkeypatch.setattr(R, "tmux", tmux)
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    return keys


def test_router_clears_its_own_stuck_text_and_hands_the_row_back(monkeypatch):
    R = _router()
    keys = _rig(monkeypatch, R, [f"❯ {TEXT}", "❯ "])
    assert R.inject("pm-x", TEXT) == "cleared"
    assert ("send-keys", "-t", "pm-x", "C-u") in keys


@pytest.mark.parametrize("line,viewer", [
    (f"❯ {TEXT} and the operator's own words", False),     # ours with their words appended
    ("❯ the operator is typing something", False),          # not ours
    (f"❯ {TEXT}", True),                                    # ours, but a client is attached
])
def test_router_never_clears_anything_else(monkeypatch, line, viewer):
    R = _router()
    keys = _rig(monkeypatch, R, [line], viewer=viewer)
    assert R.inject("pm-x", TEXT) == "stuck"
    assert not [k for k in keys if k[-1] == "C-u"]


def test_router_clear_that_does_not_verify_stays_stuck(monkeypatch):
    R = _router()
    _rig(monkeypatch, R, [f"❯ {TEXT}", f"❯ {TEXT}"])            # C-u did not take
    assert R.inject("pm-x", TEXT) == "stuck"


class _Store:
    def __init__(self):
        self.calls = []

    def claim(self, i):
        self.calls.append(("claim", i)); return True

    def defer(self, i, error=None):
        self.calls.append(("defer", i)); return True

    def fail(self, i, error=None):
        self.calls.append(("fail", i))

    def deliver(self, i):
        self.calls.append(("deliver", i))


def test_router_digest_cleared_defers_every_row(monkeypatch):
    R = _router()
    rows = [{"id": "m1"}, {"id": "m2"}]
    monkeypatch.setattr(R, "partition_for_digest", lambda rows, age: ([], rows))
    monkeypatch.setattr(R, "compose_digest", lambda claimed, store: "digest")
    st = _Store()
    assert R.deliver_stale_digest("pm-x", st, rows, {"enabled": True}, inject_fn=lambda *a: "cleared") == 0
    assert [c for c in st.calls if c[0] != "claim"] == [("defer", "m1"), ("defer", "m2")]


# ------------------------------------------------------------------ nudge_pane
def _nudge_rig(monkeypatch, composer_seq, attached=False):
    keys, seq = [], list(composer_seq)
    monkeypatch.setattr(NP, "tmux", lambda *a: keys.append(a) or types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(NP, "composer_line", lambda s: seq.pop(0) if len(seq) > 1 else seq[0])
    monkeypatch.setattr(NP, "attached", lambda s: attached)
    monkeypatch.setattr(NP.time, "sleep", lambda s: None)
    return keys


def test_nudge_clears_its_own_unsubmitted_nudge(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    nudge = "continue please"
    # before: empty; then our text stays through 3 checks; then the clear leaves it empty
    keys = _nudge_rig(monkeypatch, ["", nudge, nudge, nudge, nudge, ""])
    assert NP._nudge_unlocked("seat", nudge) == 1
    assert ("send-keys", "-t", "seat", "C-u") in keys
    import inject_journal as J
    assert J.rows() == []


def test_nudge_never_clears_with_a_client_attached(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    nudge = "continue please"
    keys = _nudge_rig(monkeypatch, ["", nudge], attached=False)
    monkeypatch.setattr(NP, "attached", lambda s, _n=[0]: (_n.__setitem__(0, _n[0] + 1) or _n[0] > 1))
    assert NP._nudge_unlocked("seat", nudge) == 1
    assert not [k for k in keys if k[-1] == "C-u"]


def test_a_busy_codex_pane_is_HELD_not_stuck_so_the_row_is_never_marked_delivered(monkeypatch):
    """Nothing is typed into a working codex pane; 'stuck' (the old answer) made the caller mark
    the row DELIVERED and alert "Enter swallowed" for a message nobody typed."""
    R = _router()
    keys = []
    monkeypatch.setattr(R, "fence_check", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(R, "exit_copy_mode", lambda s: None)

    def tmux(*a, **k):
        keys.append(a)
        out = "◦ Working (12s • esc to interrupt)\n› \n" if a[0] == "capture-pane" else ""
        return types.SimpleNamespace(returncode=0, stdout=out)
    monkeypatch.setattr(R, "tmux", tmux)
    assert R.inject("cx-seat", "hello", "codex") == "held"
    assert not [k for k in keys if k[0] == "send-keys"]
