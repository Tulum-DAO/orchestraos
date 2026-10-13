"""Codex SUBAGENT rollouts are never a seat's session.

Real shape (codex 0.153.4): a seat's main thread (session_meta thread_source='user') and every
subagent it spawns (thread_source='subagent', source.subagent.thread_spawn.parent_thread_id, and a
top-level parent_thread_id) are ALL open in the one codex process, and every subagent's first user
message repeats the parent's boot line. Before this fix codex_context._open_rollout returned the
FIRST rollout descriptor it saw (a subagent's, as often as not), so a seat's context % could be a
subagent's; and resolve_codex_cid took the newest matching file, usually a subagent's.
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import codex_context as CC  # noqa: E402
from lineage_daemon.wal import ctx_adapters as CA  # noqa: E402

MAIN = "00000000-0000-7000-8000-0000000000a0"
OTHER = "00000000-0000-7000-8000-0000000000b0"
SUBS = ["00000000-0000-7000-8000-0000000000a1", "00000000-0000-7000-8000-0000000000a2",
        "00000000-0000-7000-8000-0000000000a3"]
SEAT = "dev-codex-g2"


def _meta(sid, sub):
    if not sub:
        return {"id": sid, "session_id": sid, "source": "cli", "thread_source": "user",
                "originator": "codex-tui"}
    return {"id": sid, "session_id": sid, "parent_thread_id": MAIN, "forked_from_id": MAIN,
            "thread_source": "subagent", "originator": "codex-tui",
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": MAIN, "depth": 1,
                                                      "agent_path": "/root/worker"}}}}


def _rollout(home, sid, sub, mtime):
    d = os.path.join(home, ".codex", "sessions", "2026", "10", "13")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"rollout-2026-10-13T00-00-00-{sid}.jsonl")
    with open(p, "w") as fh:
        fh.write(json.dumps({"type": "session_meta", "payload": _meta(sid, sub)}) + "\n")
        fh.write(json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text",
                         "text": f"You are {SEAT}. Read /tmp/agent-init-{SEAT}.md and follow all instructions in it."}]}}) + "\n")
    os.utime(p, (mtime, mtime))
    return p


def test_is_subagent_rollout_reads_the_real_markers(tmp_path):
    assert CC.is_subagent_rollout(_rollout(str(tmp_path), MAIN, False, time.time())) is False
    assert CC.is_subagent_rollout(_rollout(str(tmp_path), SUBS[0], True, time.time())) is True


def test_an_unreadable_or_metaless_rollout_is_not_called_a_subagent(tmp_path):
    p = tmp_path / "rollout-x.jsonl"
    p.write_text("not json\n")
    assert CC.is_subagent_rollout(str(p)) is False
    assert CC.is_subagent_rollout(str(tmp_path / "missing.jsonl")) is False


def test_reconciler_resolver_picks_the_MAIN_thread_not_the_newer_subagents(tmp_path, monkeypatch):
    home = str(tmp_path)
    now = time.time()
    _rollout(home, MAIN, False, now - 3600)
    for i, s in enumerate(SUBS):
        _rollout(home, s, True, now - i)          # every subagent is NEWER than the main thread
    monkeypatch.setenv("HOME", home)
    assert CA.resolve_codex_cid("dev-codex", retired_sids_fn=lambda seat: set()) == MAIN


def test_a_lone_subagent_resolves_to_None_never_to_the_subagent(tmp_path, monkeypatch):
    home = str(tmp_path)
    _rollout(home, SUBS[0], True, time.time())
    monkeypatch.setenv("HOME", home)
    assert CA.resolve_codex_cid("dev-codex", retired_sids_fn=lambda seat: set()) is None


def _fake_fds(monkeypatch, paths):
    """The codex process's /proc/<pid>/fd, in the given ORDER (listdir order decides 'first')."""
    links = {str(i): p for i, p in enumerate(paths)}
    real_listdir, real_readlink = os.listdir, os.readlink
    monkeypatch.setattr(CC.os, "listdir",
                        lambda d: list(links) if str(d).endswith("/fd") else real_listdir(d))
    monkeypatch.setattr(CC.os, "readlink",
                        lambda p: links[os.path.basename(p)] if "/fd/" in str(p) else real_readlink(p))


def test_open_rollout_with_the_subagents_open_FIRST_is_the_main_thread(tmp_path, monkeypatch):
    home = str(tmp_path)
    subs = [_rollout(home, s, True, time.time()) for s in SUBS]
    main = _rollout(home, MAIN, False, time.time())
    _fake_fds(monkeypatch, subs + [main])
    assert CC._open_rollout(12345) == main


def test_open_rollout_with_only_a_subagent_open_is_None(tmp_path, monkeypatch):
    _fake_fds(monkeypatch, [_rollout(str(tmp_path), SUBS[1], True, time.time())])
    assert CC._open_rollout(12345) is None


def test_two_main_threads_open_is_None_never_the_first_descriptor(tmp_path, monkeypatch):
    """Two non-subagent rollouts in one process: which one is the seat's is not known, so fail closed
    (the widget shows --/--) rather than report whichever descriptor the kernel listed first."""
    home = str(tmp_path)
    _fake_fds(monkeypatch, [_rollout(home, OTHER, False, time.time()),
                            _rollout(home, MAIN, False, time.time())])
    assert CC._open_rollout(12345) is None


def test_the_widget_reads_the_main_threads_tokens_not_a_subagents(tmp_path, monkeypatch):
    """By effect, through the widget entry point: the subagent's rollout says 90%, the seat's 10%."""
    home = str(tmp_path)
    sub = _rollout(home, SUBS[0], True, time.time())
    main = _rollout(home, MAIN, False, time.time())
    def tokens(path, used):
        with open(path, "a") as fh:
            fh.write(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"total_tokens": used}, "model_context_window": 1000}}}) + "\n")
    tokens(sub, 900)
    tokens(main, 100)
    _fake_fds(monkeypatch, [sub, main])
    monkeypatch.setattr(CC, "_pane_pid", lambda s: 111)
    monkeypatch.setattr(CC, "_codex_pid_under", lambda p: 12345)
    assert CC.get_codex_session_context(SEAT) == (10.0, 100, 1000)
