"""Stage 4 (DEC-1791324686371842 v2): the seam signal (A) and the green brief addendum (B)."""
import itertools
import json
import os
import subprocess
import time

import pytest

from _jevtest import answers_decide, choice_a, noul_a, score_a
from jev import compact, handoff
from jev.client import JevClient, JevTimeout, JevTransportError

SID = "sess-seam"


def write_verdict(tmp_path, signals, transcript_age=0.0, verdict_age=0.0, skipped=False, transcript=True):
    t = tmp_path / "t.jsonl"
    if transcript:
        t.write_text('{"type":"user"}\n')
    rec = {"tier": "recommend", "signals": signals, "skipped": skipped,
           "transcript_path": str(t) if transcript else None}
    path = compact.write_verdict(SID, rec)
    now = time.time()
    os.utime(path, (now - verdict_age, now - verdict_age))
    if transcript:
        os.utime(t, (now - transcript_age, now - transcript_age))
    return path


MOVED = {"switched_gears": 0.95, "at_boundary": 0.2, "needs_history": 1.5, "mid_operation": 0.1}


# ---------------------------------------------------------------- A: seam_signal

def test_moved_on_when_fresh(tmp_path):
    write_verdict(tmp_path, MOVED, transcript_age=0, verdict_age=3)  # transcript trails by 3 s: still fresh
    assert compact.seam_signal(SID) == {"moved_on": True, "why": "moved-on"}


def test_stale_when_the_transcript_moved_after_the_verdict(tmp_path):
    write_verdict(tmp_path, MOVED, transcript_age=0, verdict_age=compact.SEAM_FRESH_TOLERANCE_S + 5)
    assert compact.seam_signal(SID)["why"] == "stale-verdict"


def test_tolerance_value_is_pinned_not_just_the_rule(tmp_path):
    """orchestraos-builder: the fixture above scales WITH the constant, so 999999 s stayed green.
    Tolerance is a safety parameter; a stale verdict must never accelerate a swap."""
    assert 0 < compact.SEAM_FRESH_TOLERANCE_S <= 60
    write_verdict(tmp_path, MOVED, transcript_age=0, verdict_age=120)  # a fixed 2 min, independent of the constant
    assert compact.seam_signal(SID)["why"] == "stale-verdict"


@pytest.mark.parametrize("signals, why", [
    ({**MOVED, "mid_operation": 0.61}, "mid-operation"),
    ({**MOVED, "switched_gears": 0.7}, "same-work"),
    ({"switched_gears": 0.1, "at_boundary": 0.9, "needs_history": 0.5, "mid_operation": 0.1}, "moved-on"),
    ({"switched_gears": 0.1, "at_boundary": 0.9, "needs_history": 1.0, "mid_operation": 0.1}, "same-work"),
])
def test_predicate(tmp_path, signals, why):
    write_verdict(tmp_path, signals)
    assert compact.seam_signal(SID)["why"] == why


def test_every_doubt_is_false(tmp_path):
    assert compact.seam_signal("never-seen")["why"] == "no-verdict"
    write_verdict(tmp_path, MOVED, skipped=True)
    assert compact.seam_signal(SID)["why"] == "verdict-skipped"
    write_verdict(tmp_path, {"switched_gears": "high"})
    assert compact.seam_signal(SID)["why"] == "malformed-verdict"
    write_verdict(tmp_path, MOVED, transcript=False)
    assert compact.seam_signal(SID)["why"] == "no-transcript"
    with open(compact.verdict_path(SID), "w") as fh:
        fh.write("{torn")
    assert compact.seam_signal(SID)["why"] == "unreadable-verdict"
    assert "seam_signal" in open(os.environ["JEV_LOG"]).read()


def test_seam_predicate_matches_decide_tier():
    """signals_moved_on must say 'moved on' exactly when decide_tier would not be silent for a
    work reason (given compactable and usage above every line)."""
    grid = [0.0, 0.6, 0.61, 0.7, 0.71, 1.0]
    lines = {"notice": 1, "recommend": 2, "request": 3}
    for sw, ab, mid, nh in itertools.product(grid, grid, grid, [0.0, 0.99, 1.0, 2.0]):
        a = {"switched_gears": noul_a(sw), "at_boundary": noul_a(ab), "needs_history": score_a(nh),
             "mid_operation": noul_a(mid)}
        tier = compact.decide_tier(a, {"tokens": 10, "pct": 1.0}, True, lines)["tier"]
        sig = {"switched_gears": sw, "at_boundary": ab, "needs_history": nh, "mid_operation": mid}
        assert compact.signals_moved_on(sig) == (tier != "silent"), sig


def test_stop_hook_records_the_transcript_path(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "message": {"role": "user", "content": "fix bug A"}},
        {"type": "user", "message": {"role": "user", "content": "now feature B"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}],
                                          "usage": {"input_tokens": 50_000}}}]) + "\n")
    a = {"switched_gears": noul_a(0.9), "at_boundary": noul_a(0.9), "needs_history": score_a(0.1),
         "mid_operation": noul_a(0.1)}
    compact.stop_hook({"session_id": SID, "transcript_path": str(t)}, answers_decide(a))
    assert compact.latest_verdict(SID)["transcript_path"] == str(t)
    assert compact.seam_signal(SID) == {"moved_on": True, "why": "moved-on"}


# ---------------------------------------------------------------- client: one attempt

def test_max_attempts_validated():
    with pytest.raises(ValueError):
        JevClient(provider="mock", max_attempts=0)
    assert JevClient(provider="mock", max_attempts=1).max_attempts == 1


# ---------------------------------------------------------------- B: green_brief_addendum

def transcript(tmp_path, prompts=("fix the auth bug", "now build billing export")):
    t = tmp_path / "blue.jsonl"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 60))
    t.write_text("\n".join(json.dumps({"type": "user", "timestamp": ts, "message": {"role": "user", "content": p}})
                           for p in prompts) + "\n")
    return str(t)


def git_tree(tmp_path, old=0, new=0):
    tree = tmp_path / "tree"
    tree.mkdir()
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    for i in range(old):
        f = tree / f"old{i}.txt"
        f.write_text("x")
        past = time.time() - 3600
        os.utime(f, (past, past))
    for i in range(new):
        (tree / f"new{i}.txt").write_text("y")
    return str(tree)


def raising(exc):
    def decide(state, questions):
        raise exc
    return decide


def test_full_addendum(tmp_path):
    out = handoff.green_brief_addendum(transcript(tmp_path), git_tree(tmp_path, old=3, new=2),
                                       answers_decide({"live_from": choice_a("1", 0.9)}))
    assert out.startswith("From the blue you replace:")
    assert 'The live work starts at "now build billing export"' in out
    assert "2 of the 5 uncommitted paths" in out and "new0.txt" in out and "old0.txt" not in out


def test_inherited_drift_reads_differently_from_real_work(tmp_path):
    out = handoff.green_brief_addendum(transcript(tmp_path), git_tree(tmp_path, old=4),
                                       answers_decide({"live_from": choice_a("none", 0.9)}))
    assert "4 uncommitted paths, none changed during the blue's session" in out
    assert "left uncommitted work" not in out


def test_unknown_session_start_says_so(tmp_path):
    line = handoff.uncommitted_line(git_tree(tmp_path, new=2), None)
    assert "has 2 uncommitted paths" in line and "inherited drift" in line


def test_clean_tree_and_single_turn_add_nothing(tmp_path):
    assert handoff.green_brief_addendum(transcript(tmp_path, ("only one",)), git_tree(tmp_path),
                                        answers_decide({})) == ""


@pytest.mark.parametrize("exc", [JevTransportError("down"), JevTimeout("slow")])
def test_jev_failure_or_timeout_still_yields_a_brief(tmp_path, exc):
    out = handoff.green_brief_addendum(transcript(tmp_path), git_tree(tmp_path, new=1), raising(exc))
    assert "left uncommitted work" in out and "live work starts" not in out
    assert type(exc).__name__ in open(os.environ["JEV_LOG"]).read()


def test_git_failure_still_yields_a_brief(tmp_path):
    out = handoff.green_brief_addendum(transcript(tmp_path), str(tmp_path / "not-a-repo"),
                                       answers_decide({"live_from": choice_a("1", 0.9)}))
    assert "live work starts" in out
    assert "handoff.uncommitted" in open(os.environ["JEV_LOG"]).read()


def test_never_raises_even_on_a_bug(tmp_path):
    out = handoff.green_brief_addendum(transcript(tmp_path), None, raising(KeyError("bug in caller code")))
    assert out == ""
    rec = json.loads(open(os.environ["JEV_LOG"]).read().splitlines()[-1])
    assert rec["bug"] is True and "KeyError" in rec["error"]


def test_bounded_decide_is_one_attempt_three_seconds(monkeypatch):
    seen = {}

    class Spy(JevClient):
        def __init__(self, **kw):
            seen.update(kw)
            super().__init__(provider="mock")
    monkeypatch.setattr(handoff, "JevClient", Spy)
    handoff._bounded_decide("s", {"q": {"type": "noul", "instructions": "x"}})
    assert seen == {"timeout_ms": 3000, "retry_delay_ms": 0, "max_attempts": 1}
