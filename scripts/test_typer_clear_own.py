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
    keys = _nudge_rig(monkeypatch, ["", nudge, nudge, nudge, nudge, nudge, ""])
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


# ------------------------------------------------------------------ codex R1 on #472
def test_the_startup_reconcile_presses_nothing_while_a_client_is_attached(monkeypatch, tmp_path):
    import watch_gateway as G
    import inject_journal as J
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    J.record("gm", "our half-sent message", "our half-sent message")
    keys = []

    def tmux(*a, **k):
        keys.append(a)
        out = {"list-clients": "/dev/pts/3: gm\n", "capture-pane": "❯ our half-sent message\n"}.get(a[0], "")
        return types.SimpleNamespace(returncode=0, stdout=out)
    monkeypatch.setattr(G, "_tmux", tmux)
    out = G.reconcile_inject_journal()
    assert out == [("gm", "unreadable")]
    assert not [k for k in keys if k[0] == "send-keys"]
    assert [r["session"] for r in J.rows()] == ["gm"]          # kept as evidence for the next start


@pytest.mark.parametrize("stage", ["codex-busy", "stranded-chip", "refused-canonical"])
def test_boot_inject_leaves_no_row_when_it_typed_nothing(monkeypatch, tmp_path, stage):
    import boot_inject as BI
    import inject_journal as J
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    monkeypatch.setattr(BI, "_boot_inject_unlocked", lambda *a, **k: ("stuck" if "c" in stage[:1] or stage == "stranded-chip" else "failed", stage))
    BI.boot_inject("pm-x", "hello")
    assert J.rows() == []


def test_nudge_does_not_call_someone_elses_text_a_submit(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    nudge = "continue please"
    keys = _nudge_rig(monkeypatch, ["", nudge, "Do you want to proceed? 1. Yes"])
    assert NP._nudge_unlocked("seat", nudge) == 1               # never "submitted"
    assert [k for k in keys if k[-1] == "Enter"] == [("send-keys", "-t", "seat", "Enter")]   # no Enter on it
    import inject_journal as J
    assert [r["session"] for r in J.rows()] == ["seat"]        # evidence kept


# ------------------------------------------------------------------ codex R2 on #472
def test_nudge_presses_no_second_Enter_on_a_menu_row_that_is_a_substring_of_the_nudge(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    nudge = "please pick 1. Yes and continue"
    keys = _nudge_rig(monkeypatch, ["", nudge, "1. Yes"])      # ours after typing, then a menu row
    assert NP._nudge_unlocked("seat", nudge) == 1
    assert [k for k in keys if k[-1] == "Enter"] == [("send-keys", "-t", "seat", "Enter")]


def test_nudge_never_types_onto_existing_text_even_a_substring_of_the_nudge(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    keys = _nudge_rig(monkeypatch, ["continue"])                  # a person's draft: "continue"
    assert NP._nudge_unlocked("seat", "continue please") == 2
    assert not [k for k in keys if k[0] == "send-keys"]


# ------------------------------------------------------------------ codex R3 on #472
def test_nudge_presses_NO_first_Enter_when_the_composer_does_not_show_our_nudge(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    keys = _nudge_rig(monkeypatch, ["", "Do you want to proceed?"])     # a menu came up while typing
    assert NP._nudge_unlocked("seat", "continue please") == 1
    assert not [k for k in keys if k[-1] == "Enter"]
    import inject_journal as J
    assert [r["session"] for r in J.rows()] == ["seat"]


def test_nudge_first_Enter_needs_EXACTLY_our_text_a_prefix_is_not_enough(monkeypatch, tmp_path):
    """A composer showing only part of our nudge (wrapped, or changed) gets no Enter: the caller
    re-sends next cycle. Ownership is exact everywhere."""
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    keys = _nudge_rig(monkeypatch, ["", "continue please, and then"])
    assert NP._nudge_unlocked("seat", "continue please, and then report what changed") == 1
    assert not [k for k in keys if k[-1] == "Enter"]


def test_boot_inject_keeps_the_row_when_the_paste_may_have_landed_unseen(monkeypatch, tmp_path):
    import boot_inject as BI
    import inject_journal as J
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    def no_receipt(s, t, rt=None, journal=None, **k):
        J.record(s, t, t[:40]); journal.append(True)
        return "failed", "no-receipt"
    monkeypatch.setattr(BI, "_boot_inject_unlocked", no_receipt)
    BI.boot_inject("pm-x", "hello")
    assert [r["session"] for r in J.rows()] == ["pm-x"]


# ------------------------------------------------------------------ codex R6 on #472: the router journals too
def test_router_journals_before_it_pastes_and_clears_on_every_outcome_but_stuck(monkeypatch, tmp_path):
    import inject_journal as J
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    R = _router()
    monkeypatch.setattr(R, "tmux", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=""))
    monkeypatch.setattr(R, "pane_split", lambda s, rt=None: ([], "❯ someone else's line", True))
    seen = []
    for res in ("submitted", "failed", "held", "stuck"):
        J.clear("pm-x")
        def typed(s, t, rt=None, journal=None, res=res):
            J.record(s, t, t[:40]); journal.append(True)       # as the real one does, before pasting
            seen.append([r["session"] for r in J.rows()])
            return res
        monkeypatch.setattr(R, "_inject_unlocked", typed)
        assert R.inject("pm-x", TEXT) == res
        # kept on 'stuck' (our text is there) and 'failed' (a paste may have landed unseen)
        assert ([r["session"] for r in J.rows()] == ["pm-x"]) is (res in ("stuck", "failed")), res
    assert seen == [["pm-x"]] * 4                                   # a row existed while it typed


def test_router_pastes_nothing_when_the_journal_cannot_be_written(monkeypatch):
    import inject_journal as J
    R = _router()
    keys = []
    monkeypatch.setattr(J, "record", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    monkeypatch.setattr(R, "fence_check", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(R, "exit_copy_mode", lambda s: None)
    monkeypatch.setattr(R, "pane_split", lambda s, rt=None: ([], "❯ ", True))
    monkeypatch.setattr(R, "tmux", lambda *a, **k: keys.append(a) or types.SimpleNamespace(returncode=0, stdout=""))
    assert R.inject("pm-x", TEXT) == "held"
    assert not [k for k in keys if k[0] in ("paste-buffer", "set-buffer", "send-keys")]


def test_a_refusal_before_typing_never_touches_a_leftover_row(monkeypatch, tmp_path):
    """codex R11 on #472: a crash left a typed row whose text a guard now finds in the composer.
    The new attempt refuses before typing and must leave that row (the evidence) alone."""
    import inject_journal as J
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    J.record("pm-x", "the half-sent one", "the half-sent one"); J.mark_typed("pm-x")
    R = _router()
    monkeypatch.setattr(R, "fence_check", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(R, "exit_copy_mode", lambda s: None)
    monkeypatch.setattr(R, "tmux", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout="◦ Working (3s • esc to interrupt)\n› \n" if a[0] == "capture-pane" else ""))
    assert R.inject("pm-x", TEXT, "codex") == "held"           # refused before typing
    assert [(r["text"], r.get("typed")) for r in J.rows()] == [("the half-sent one", True)]
    import boot_inject as BI
    import test_boot_inject as TB
    p = TB.Pane(chip=True, preexisting="[Pasted text #1 +3 lines]")
    assert BI.boot_inject("pm-x", "hello", tmux_fn=p, sleep_fn=lambda s: None,
                          log_fn=lambda m: None)[1] == "stranded-chip"
    assert [(r["text"], r.get("typed")) for r in J.rows()] == [("the half-sent one", True)]


def test_boot_inject_marks_its_row_typed_once_the_paste_is_confirmed(monkeypatch, tmp_path):
    """codex R7 on #472: an unmarked row is report-only, so a boot prompt killed after its paste
    must be marked or the reconcile could never submit it."""
    import boot_inject as BI
    import inject_journal as J
    import test_boot_inject as TB
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    p = TB.Pane(swallow=99)                                  # the Enter never takes: stuck, row kept
    out = BI.boot_inject("pm-x", TB.TEXT, tmux_fn=p, sleep_fn=lambda s: None, log_fn=lambda m: None)
    assert out[0] == "stuck"
    assert [r.get("typed") for r in J.rows()] == [True]


# ------------------------------------------------------------------ codex R10 on #472: fail closed everywhere
def test_gateway_treats_an_unanswerable_attachment_check_as_attached(monkeypatch):
    import watch_gateway as G
    monkeypatch.setattr(G, "_tmux", lambda *a, **k: types.SimpleNamespace(
        returncode=1 if a[0] == "list-clients" else 0, stdout=""))
    assert G._client_attached("gm") is True
    monkeypatch.setattr(G, "_tmux", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=""))
    assert G._client_attached("gm") is False


def test_reconcile_presses_nothing_when_attachment_cannot_be_checked(monkeypatch, tmp_path):
    import watch_gateway as G
    import inject_journal as J
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    J.record("gm", "our message", "our message"); J.mark_typed("gm")
    keys = []

    def tmux(*a, **k):
        keys.append(a)
        rc = 1 if a[0] == "list-clients" else 0
        return types.SimpleNamespace(returncode=rc, stdout="❯ our message\n" if a[0] == "capture-pane" else "")
    monkeypatch.setattr(G, "_tmux", tmux)
    assert G.reconcile_inject_journal() == [("gm", "unreadable")]
    assert not [k for k in keys if k[0] == "send-keys"]


def test_router_never_clears_when_attachment_cannot_be_checked(monkeypatch):
    R = _router()
    keys = []
    monkeypatch.setattr(R, "_inject_unlocked", lambda *a, **k: "stuck")
    monkeypatch.setattr(R, "pane_split", lambda s, rt=None: ([], f"❯ {TEXT}", True))
    monkeypatch.setattr(R, "tmux", lambda *a, **k: keys.append(a) or types.SimpleNamespace(
        returncode=1 if a[0] == "list-clients" else 0, stdout=""))
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    assert R.inject("pm-x", TEXT) == "stuck"
    assert not [k for k in keys if k[-1] == "C-u"]


def test_nudge_refuses_when_attachment_cannot_be_checked(monkeypatch):
    keys = []
    monkeypatch.setattr(NP, "tmux", lambda *a: keys.append(a) or types.SimpleNamespace(
        returncode=1 if a[0] == "list-clients" else 0, stdout="", stderr=""))
    assert NP._nudge_unlocked("seat", "continue please") == 2
    assert not [k for k in keys if k[0] == "send-keys"]


# ------------------------------------------------------------------ codex R13 on #472
def test_router_never_pastes_onto_typed_text_and_keeps_a_leftover_row(monkeypatch, tmp_path):
    import inject_journal as J
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    J.record("pm-x", "the half-sent one", "the half-sent one"); J.mark_typed("pm-x")
    R = _router()
    keys = []
    monkeypatch.setattr(R, "fence_check", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(R, "exit_copy_mode", lambda s: None)
    monkeypatch.setattr(R, "pane_split", lambda s, rt=None: ([], "❯ the half-sent one", True))
    monkeypatch.setattr(R, "tmux", lambda *a, **k: keys.append(a) or types.SimpleNamespace(
        returncode=0, stdout="❯ the half-sent one\n" if a[0] == "capture-pane" else ""))
    assert R.inject("pm-x", TEXT) == "held"
    assert not [k for k in keys if k[0] in ("paste-buffer", "set-buffer", "send-keys")]
    assert [(r["text"], r.get("typed")) for r in J.rows()] == [("the half-sent one", True)]


def test_boot_inject_never_types_onto_typed_text(monkeypatch, tmp_path):
    import boot_inject as BI
    import test_boot_inject as TB
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    p = TB.Pane(preexisting="a draft someone is writing")
    assert BI.boot_inject("pm-x", TB.TEXT, tmux_fn=p, sleep_fn=lambda s: None,
                          log_fn=lambda m: None) == ("stuck", "composer-occupied")
    assert not [c for c in p.calls if c[0] in ("paste-buffer", "set-buffer")]


# ------------------------------------------------------------------ R13 follow-up: our prefix, another message's row
def _router_on(monkeypatch, line, keys):
    R = _router()
    monkeypatch.setattr(R, "fence_check", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(R, "exit_copy_mode", lambda s: None)
    monkeypatch.setattr(R, "pane_split", lambda s, rt=None: ([], line, True))
    monkeypatch.setattr(R, "tmux", lambda *a, **k: keys.append(a) or types.SimpleNamespace(
        returncode=0, stdout=f"{line}\n" if a[0] == "capture-pane" else ""))
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    return R


def _half_sent(monkeypatch, tmp_path, text):
    import inject_journal as J
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    J.record("pm-x", text, text[:40]); J.mark_typed("pm-x")
    return J


def test_router_presses_nothing_on_our_prefix_while_a_different_message_is_recorded(monkeypatch, tmp_path):
    """The line starts like our message, but a row marked typed records another one: the line may
    be that earlier half-sent message. No Enter (it would go out under our name); the row stays."""
    older = TEXT[:50] + " and an older tail"
    J = _half_sent(monkeypatch, tmp_path, older)
    keys = []
    R = _router_on(monkeypatch, f"❯ {older}", keys)
    assert R.inject("pm-x", TEXT) == "held"
    assert not [k for k in keys if k[0] in ("paste-buffer", "send-keys")]
    assert [r["text"] for r in J.rows()] == [older]


def test_router_still_resubmits_its_own_recorded_strand(monkeypatch, tmp_path):
    _half_sent(monkeypatch, tmp_path, TEXT)                     # control: the row is OUR message
    keys = []
    R = _router_on(monkeypatch, f"❯ {TEXT}", keys)
    R.inject("pm-x", TEXT)
    assert ("send-keys", "-t", "pm-x", "Enter") in keys


def _submits_on_enter(monkeypatch, R, keys):
    """The pane's line holds TEXT until an Enter, then it is in the scrollback and the line is empty."""
    state = {"sent": False}

    def split(s, rt=None):
        return ([f"> {TEXT}"], "❯ ", True) if state["sent"] else ([], f"❯ {TEXT}", True)

    def tmux(*a, **k):
        keys.append(a)
        if a[0] == "send-keys" and a[-1] == "Enter":
            state["sent"] = True
        return types.SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(R, "pane_split", split)
    monkeypatch.setattr(R, "tmux", tmux)


def test_router_resubmitting_its_own_strand_clears_that_row(monkeypatch, tmp_path):
    """codex R14 on #472: the resubmit path pressed Enter without taking the row over, so a submit
    left the old row behind; a later draft with the same words could get the restart's Enter."""
    J = _half_sent(monkeypatch, tmp_path, TEXT)
    keys = []
    R = _router_on(monkeypatch, f"❯ {TEXT}", keys)
    _submits_on_enter(monkeypatch, R, keys)
    assert R.inject("pm-x", TEXT) == "submitted"
    assert J.rows() == []


def test_router_resubmit_that_sticks_keeps_a_typed_row(monkeypatch, tmp_path):
    J = _half_sent(monkeypatch, tmp_path, TEXT)
    keys = []
    R = _router_on(monkeypatch, f"❯ {TEXT}", keys)
    monkeypatch.setattr(R, "tmux", lambda *a, **k: keys.append(a) or types.SimpleNamespace(
        returncode=1 if a[0] == "list-clients" else 0, stdout=""))   # attached check fails: no C-u
    assert R.inject("pm-x", TEXT) == "stuck"
    assert [(r["text"], r.get("typed")) for r in J.rows()] == [(TEXT, True)]


def test_router_resubmit_presses_nothing_when_the_journal_cannot_be_written(monkeypatch, tmp_path):
    import inject_journal as J
    keys = []
    R = _router_on(monkeypatch, f"❯ {TEXT}", keys)
    monkeypatch.setattr(J, "record", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert R.inject("pm-x", TEXT) == "held"
    assert not [k for k in keys if k[0] == "send-keys"]


def test_boot_inject_resubmitting_its_own_strand_clears_that_row(monkeypatch, tmp_path):
    import test_boot_inject as TB
    J = _half_sent(monkeypatch, tmp_path, TB.TEXT)
    assert TB.run(TB.Pane(preexisting=TB.TEXT)) == ("submitted", "enter-1")
    assert J.rows() == []


def test_boot_inject_presses_nothing_on_our_prefix_while_a_different_message_is_recorded(monkeypatch, tmp_path):
    import test_boot_inject as TB
    older = TB.TEXT + " plus an older tail"
    J = _half_sent(monkeypatch, tmp_path, older)
    p = TB.Pane(preexisting=older)
    assert TB.run(p) == ("stuck", "not-exactly-ours")
    assert p.pastes == 0 and p.enters == 0
    assert [r["text"] for r in J.rows()] == [older]


def test_boot_inject_still_resubmits_its_own_recorded_strand(monkeypatch, tmp_path):
    import test_boot_inject as TB
    _half_sent(monkeypatch, tmp_path, TB.TEXT)                  # control: the row is OUR prompt
    assert TB.run(TB.Pane(preexisting=TB.TEXT)) == ("submitted", "enter-1")


# ------------------------------------------------------------------ codex R15 on #472: exact recorded ownership
def test_router_never_enters_our_text_with_a_persons_words_appended(monkeypatch, tmp_path):
    J = _half_sent(monkeypatch, tmp_path, TEXT)                 # our attempt IS recorded...
    keys = []
    R = _router_on(monkeypatch, f"❯ {TEXT} and the operator's addition", keys)   # ...but they added to it
    assert R.inject("pm-x", TEXT) == "held"
    assert not [k for k in keys if k[0] in ("paste-buffer", "send-keys")]
    assert [(r["text"], r.get("typed")) for r in J.rows()] == [(TEXT, True)]


def test_router_never_enters_an_identical_line_no_typer_recorded(monkeypatch, tmp_path):
    import inject_journal as J
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    keys = []
    R = _router_on(monkeypatch, f"❯ {TEXT}", keys)
    assert R.inject("pm-x", TEXT) == "held"
    assert not [k for k in keys if k[0] in ("paste-buffer", "send-keys")]
    assert J.rows() == []


def test_boot_inject_never_enters_our_prompt_with_a_persons_words_appended(monkeypatch, tmp_path):
    import test_boot_inject as TB
    J = _half_sent(monkeypatch, tmp_path, TB.TEXT)
    p = TB.Pane(preexisting=TB.TEXT + " and the operator's addition")
    assert TB.run(p) == ("stuck", "not-exactly-ours")
    assert p.pastes == 0 and p.enters == 0
    assert [(r["text"], r.get("typed")) for r in J.rows()] == [(TB.TEXT, True)]
