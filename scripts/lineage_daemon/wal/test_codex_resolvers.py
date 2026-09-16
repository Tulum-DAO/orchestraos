"""Codex per-runtime resolvers (provider-agnostic rotation): resolve_codex_cid /
green_progress_codex / codex_green_is_live mirror the gemini adapters off CODEX-shaped
evidence (the ~/.codex/sessions rollout JSONL). Hermetic via a HOME override; the
identity declaration is the SECOND user message (codex injects <environment_context>
first — unlike gemini where identity is the first user input).
"""
import json
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import ctx_adapters as ca  # noqa: E402
from lineage_daemon.wal import green_liveness as gl  # noqa: E402

_SID = "01a0a300-261d-7ff2-a2e1-f697da67ec8d"


def _write_rollout(home, seat, sid=_SID, task_starts=1, declare=True):
    d = os.path.join(home, ".codex", "sessions", "2026", "09", "14")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"rollout-2026-09-14T22-58-11-{sid}.jsonl")
    lines = [
        {"type": "session_meta", "payload": {"session_id": sid, "cwd": "/x"}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer",
         "content": [{"type": "input_text", "text": "You are `/root`, the primary agent"}]}},
        # codex injects an environment_context USER message BEFORE identity:
        {"type": "response_item", "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "<environment_context><cwd>/x</cwd>"}]}},
    ]
    if declare:
        lines.append({"type": "response_item", "payload": {"type": "message", "role": "user",
                      "content": [{"type": "input_text",
                                   "text": f"You are {seat}. Read /tmp/agent-init-{seat}.md and follow it."}]}})
    for _ in range(task_starts):
        lines.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t"}})
    with open(p, "w") as fh:
        for ln in lines:
            fh.write(json.dumps(ln) + "\n")
    return p


def test_resolve_codex_cid_finds_sid_from_second_user_message(tmp_path, monkeypatch):
    # hermetic: the resolver's DB-retired-sid exclusion (gm msg_faf46471) must read the
    # sandbox, never the live orchestra-registry.db (the fixture sid is a real retired one).
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_rollout(str(tmp_path), "demo-codex-pred2")
    assert ca.resolve_codex_cid("demo-codex-pred2") == _SID


def test_resolve_codex_cid_none_when_not_declared(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_rollout(str(tmp_path), "demo-codex-pred2", declare=False)
    assert ca.resolve_codex_cid("demo-codex-pred2") is None


def test_resolve_codex_cid_word_boundary_no_substring_match(tmp_path, monkeypatch):
    # 'demo-codex-pred' must NOT resolve a rollout that only declares 'demo-codex-pred2'
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_rollout(str(tmp_path), "demo-codex-pred2")
    assert ca.resolve_codex_cid("demo-codex-pred") is None


def test_green_progress_codex_counts_task_started(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_rollout(str(tmp_path), "demo-codex-pred2", task_starts=3)
    assert ca.green_progress_codex(_SID) == 3
    assert ca.green_progress_codex("no-such-sid") is None
    assert ca.green_progress_codex(None) is None


def test_registries_carry_codex():
    assert ca.CID_RESOLVER_REGISTRY.get("codex") is ca.resolve_codex_cid
    assert ca.GREEN_PROGRESS_REGISTRY.get("codex") is ca.green_progress_codex
    assert gl.GREEN_LIVENESS_REGISTRY.get("codex") is gl.codex_green_is_live


def test_codex_green_is_live_three_conditions(tmp_path):
    # injected seams: recorded pane pid == live pid, cid resolves, progress >= 1 => LIVE
    from lineage_daemon.wal.bg_state import BgStateStore
    wal = str(tmp_path)
    BgStateStore(wal, "demo-codex-pred2").write_meta("green_pane_pid", 4242)
    live = gl.codex_green_is_live(
        "demo-codex-pred2", "demo-codex-pred2-g2", wal_dir=wal,
        session_pane_fn=lambda a: ("%1", 4242),
        cid_fn=lambda a: _SID,
        progress_fn=lambda sid: 2)
    assert live is True
    # pane pid mismatch (process replaced / OOM-killed green) => fail-closed
    dead = gl.codex_green_is_live(
        "demo-codex-pred2", "demo-codex-pred2-g2", wal_dir=wal,
        session_pane_fn=lambda a: ("%1", 9999),
        cid_fn=lambda a: _SID, progress_fn=lambda sid: 2)
    assert dead is False
    # no turn yet (progress 0) => fail-closed
    noturn = gl.codex_green_is_live(
        "demo-codex-pred2", "demo-codex-pred2-g2", wal_dir=wal,
        session_pane_fn=lambda a: ("%1", 4242),
        cid_fn=lambda a: _SID, progress_fn=lambda sid: 0)
    assert noturn is False
