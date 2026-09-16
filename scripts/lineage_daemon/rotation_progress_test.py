"""Tests — progressing_fn (DEC-1787808620 A.3 rework, gm-LOCKED msg_772da5ec).

The SOLE gate before predecessor destruction: keys on a POSITIVE task-directed ARTIFACT
(focus-file edit / commit-delta), NEVER on tool-call/activity volume. A flailing
successor (reads + calls, no artifact) must NOT trip it.
"""
import inspect

from scripts.lineage_daemon.rotation_progress import progressing


def test_progressing_true_on_focus_file_edit():
    assert progressing("succ", focus_edited_since_fn=lambda s: True,
                       commit_delta_fn=lambda s: 0) is True


def test_progressing_true_on_commit_delta():
    assert progressing("succ", focus_edited_since_fn=lambda s: False,
                       commit_delta_fn=lambda s: 1) is True


def test_progressing_false_on_comprehension_reads_or_idle():
    assert progressing("succ", focus_edited_since_fn=lambda s: False,
                       commit_delta_fn=lambda s: 0) is False


def test_flailing_reads_and_toolcalls_without_artifact_does_NOT_progress():
    """gm-LOCKED REQUIRED negative case: a busy-but-empty successor (high tool-call/read
    activity, NO work-file artifact) is NOT progressing. Enforced by construction —
    progressing() exposes NO tool-call/activity-count parameter."""
    assert progressing("succ", focus_edited_since_fn=lambda s: False,
                       commit_delta_fn=lambda s: 0) is False
    params = set(inspect.signature(progressing).parameters)
    assert not (params & {"tool_call_count_fn", "tool_calls", "activity_count",
                          "read_count_fn", "tool_call_count"}), (
        "progressing_fn must key on a POSITIVE ARTIFACT, never tool-call/activity count")


def test_commit_delta_error_fails_closed_not_progressing():
    def boom(s):
        raise RuntimeError("git blew up")
    assert progressing("succ", focus_edited_since_fn=lambda s: False,
                       commit_delta_fn=boom) is False
