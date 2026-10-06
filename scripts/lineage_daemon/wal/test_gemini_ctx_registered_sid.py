"""The gemini adapter must use the seat's REGISTERED session id, which it was discarding.

Why this blocks the whole programme. A seat can only be blue-green ARMED if
`is_arm_conformant` gets a fresh, in-range ctx from its provider adapter — the gate is
fail-closed by design. An unarmed seat does not auto-rotate, so every unarmed live seat is
a seat a human has to ask to rotate. That is the cost of this bug.

THE BUG ITSELF: `read_ctx_gemini(seat, **_)` SWALLOWED the `sid` kwarg that
`arm_hooks.arm_seat` deliberately resolves and passes, and keyed only on
`resolve_gemini_cid(seat)`, which scans brain transcripts for a `You are <seat>` declaration
and so answers with an EARLIER GENERATION's conversation whenever the current one has not
re-declared. Registry-first is the documented order and was simply never implemented.

CORRECTION (2026-10-06). An earlier version of this docstring said `gemini-pm-adaptiv`'s
REGISTERED sid was the surfaced conversation and the transcript scan returned the stale one.
THAT WAS BACKWARDS. Measured by orchestra-builder-g72 and re-verified here against the live
registry, the real shape is worse for that seat: canonical holds a sid that `gemini_context`
does not surface at all, the one conversation that IS surfaced (190027 tokens) has no registry
row and reports agent='unknown' so the name-match branch cannot rescue it, and
`resolve_gemini_cid` returns the SAME stale value as the registry. Both paths agree and both
are stale — the worst shape for a sid-first fix, because it looks resolved and is wrong.

So the ordering these tests pin is still correct and still required, but it does NOT on its own
arm a seat whose REGISTRY ENTRY is the stale side. That residue is the identity-freshness /
ctx:unknown gap and is tracked as Identity Layer v1, not papered over here. The tests below use
synthetic ids and assert the ORDERING property, which is what they were always really pinning.

The adapter's own docstring already described the correct behaviour — "first by the seat's
registered session_id (cid) if agent-sessions maps it (finding #1), else by the seat's
`You are <seat>` brain declaration" — so the documented primary path was simply never
implemented. The transcript scan is the FALLBACK, not the authority: it picks the
newest-by-mtime transcript declaring the seat, which is an older generation's conversation
whenever the current one has not re-declared.

`gemini-gm` masked it: its status row happens to carry `agent == "gemini-gm"`, so the
name-match branch rescued it even though its `resolve_gemini_cid` was ALSO wrong
(a stale `<stale sid>` vs the live `<live sid>`). A seat whose row reports `agent: "unknown"` — which
is most of them — had nothing to fall back on.
"""
import sys
import types

import pytest

from lineage_daemon.wal import ctx_adapters as ca


@pytest.fixture
def fake_gemini_context(monkeypatch):
    """Stand in for the real `gemini_context` import inside read_ctx_gemini."""
    def _install(rows):
        mod = types.ModuleType("gemini_context")
        mod.get_gemini_agents_status = lambda: rows
        monkeypatch.setitem(sys.modules, "gemini_context", mod)
        return mod
    return _install


# The live shape that was failing: the seat's row is surfaced but UNNAMED, so only the
# conversation id can identify it.
_UNNAMED_ROW = [{"agent": "unknown", "conversation_id": "aaaaaaaa-live",
                 "estimated_tokens": 190027}]


def test_the_registered_sid_identifies_an_UNNAMED_row(fake_gemini_context, monkeypatch):
    fake_gemini_context(_UNNAMED_ROW)
    # the transcript scan returns a STALE conversation, as it did live
    monkeypatch.setattr(ca, "resolve_gemini_cid", lambda seat, **kw: "bbbbbbbb-stale")
    pct, fresh = ca.read_ctx_gemini("gemini-pm-adaptiv", sid="aaaaaaaa-live")
    assert fresh is True, "the registered sid was discarded — the seat stays unarmable"
    assert pct == pytest.approx(190027 / 1_000_000)


def test_the_registered_sid_WINS_over_a_stale_transcript_scan(fake_gemini_context,
                                                              monkeypatch):
    """Both resolve, and they disagree. The registry is the authority; the transcript scan
    is a fallback for seats the registry does not map."""
    rows = [{"agent": "unknown", "conversation_id": "aaaaaaaa-live",
             "estimated_tokens": 190027},
            {"agent": "unknown", "conversation_id": "bbbbbbbb-stale",
             "estimated_tokens": 900000}]
    fake_gemini_context(rows)
    monkeypatch.setattr(ca, "resolve_gemini_cid", lambda seat, **kw: "bbbbbbbb-stale")
    pct, fresh = ca.read_ctx_gemini("seat-x", sid="aaaaaaaa-live")
    assert fresh is True
    assert pct == pytest.approx(0.190027), (
        "the stale transcript conversation won — that is a ctx read for the WRONG "
        "generation, which would arm or fire a seat on another conversation's context")


# --- CONTROLS: the fallbacks that already worked must keep working ----------------
def test_control_the_transcript_scan_still_resolves_when_no_sid_is_passed(
        fake_gemini_context, monkeypatch):
    """`/agent-key`-style callers and the beat may call without a sid. Unchanged path."""
    fake_gemini_context([{"agent": "unknown", "conversation_id": "bbbbbbbb-stale",
                          "estimated_tokens": 41015}])
    monkeypatch.setattr(ca, "resolve_gemini_cid", lambda seat, **kw: "bbbbbbbb-stale")
    pct, fresh = ca.read_ctx_gemini("seat-x")
    assert fresh is True and pct == pytest.approx(0.041015)


def test_control_the_NAME_match_still_rescues_a_named_row(fake_gemini_context,
                                                          monkeypatch):
    """This is the branch that masked the bug for gemini-gm. It must not regress."""
    fake_gemini_context([{"agent": "gemini-gm", "conversation_id": "cccccccc-live",
                          "estimated_tokens": 110046}])
    monkeypatch.setattr(ca, "resolve_gemini_cid", lambda seat, **kw: None)
    pct, fresh = ca.read_ctx_gemini("gemini-gm")
    assert fresh is True and pct == pytest.approx(0.110046)


def test_control_an_unsurfaced_seat_still_fails_CLOSED(fake_gemini_context, monkeypatch):
    """Fail-closed is the safety property the arm gate depends on: no reading means no
    arm. A sid that matches nothing must NOT invent a value."""
    fake_gemini_context([{"agent": "unknown", "conversation_id": "someone-else",
                          "estimated_tokens": 1234}])
    monkeypatch.setattr(ca, "resolve_gemini_cid", lambda seat, **kw: None)
    pct, fresh = ca.read_ctx_gemini("ghost-seat", sid="not-present")
    assert (pct, fresh) == (None, False)
