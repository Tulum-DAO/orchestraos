"""RED-first tests for run_capture.resolve_seat — the READ-ONLY seat resolver
that points the capture engine at ios-watch-dev's live streams (spec DP-5).

It resolves the live sid the way enrich.py does (prefer the resume_command
--resume sid over the periodically-clobbered session_id) and derives the
projects/<slug>/<sid>.jsonl transcript path from the cwd.
"""
from lineage_daemon.wal.run_capture import resolve_seat


def _reg(**over):
    a = {
        "cwd": "/home/testuser/repos/watch-approval-app",
        "session_id": "757ef800-5ca4-4c04-91d9-48bbfce9f4a5",
        "resume_command": ("claude --resume 757ef800-5ca4-4c04-91d9-48bbfce9f4a5"
                           " --dangerously-skip-permissions"),
        "generation": 6,
    }
    a.update(over)
    return {"agents": {"ios-watch-dev": a}}


def test_resolves_lineage_cwd_generation():
    spec = resolve_seat(_reg(), "ios-watch-dev", home="/home/testuser")
    assert spec.lineage_root == "ios-watch-dev"
    assert spec.cwd == "/home/testuser/repos/watch-approval-app"
    assert spec.generation == 6


def test_derives_transcript_path_from_cwd_and_sid():
    spec = resolve_seat(_reg(), "ios-watch-dev", home="/home/testuser")
    assert spec.source_path == (
        "/home/testuser/.claude/projects/-home-testuser-repos-watch-approval-app/"
        "757ef800-5ca4-4c04-91d9-48bbfce9f4a5.jsonl")


def test_prefers_resume_sid_over_clobbered_session_id():
    reg = _reg(session_id="stale-clobbered-value",
               resume_command="claude --resume 757ef800-5ca4-4c04-91d9-48bbfce9f4a5 --x")
    spec = resolve_seat(reg, "ios-watch-dev", home="/home/testuser")
    assert spec.sid == "757ef800-5ca4-4c04-91d9-48bbfce9f4a5"


def test_panes_dir_is_orchestra_relative():
    spec = resolve_seat(_reg(), "ios-watch-dev", home="/home/testuser",
                        orchestra_dir="/home/testuser/agent-orchestra")
    assert spec.panes_dir == (
        "/home/testuser/agent-orchestra/state/agent-events/panes")
