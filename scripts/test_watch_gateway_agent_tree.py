"""GET /agent-tree?session=[&all=1]: the seat's subagent tree for the phone/watch/Quest (same contract as
/api/agents/:id/tree: transcript_path dropped, all_finished on the root)."""
import asyncio
import json
import os
import sys

from aiohttp.test_utils import make_mocked_request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import watch_gateway as G  # noqa: E402
from scripts import agent_tree  # noqa: E402

TREE = {"agent_id": "sid", "depth": 0, "type": "seat", "label": "pm-x", "state": "waiting",
        "transcript_path": "/home/u/.claude/projects/x/sid.jsonl",
        "children": [{"agent_id": "a1", "depth": 1, "state": "running", "transcript_path": "/home/u/a1.jsonl",
                      "children": []}]}
PUBLIC = {"agent_id": "sid", "depth": 0, "type": "seat", "label": "pm-x", "state": "waiting",
          "children": [{"agent_id": "a1", "depth": 1, "state": "running", "children": []}]}


def _get(qs, monkeypatch, sessions=("pm-x",), snap=lambda s, **k: [TREE], authorized=True, headless=None):
    monkeypatch.setattr(G, "_authorized", lambda r: authorized)
    monkeypatch.setattr(G, "_tmux_session_names", lambda: set(sessions))
    monkeypatch.setattr(G, "_headless_seat_for", lambda s: (headless or {}).get(s))
    seen = []
    monkeypatch.setattr(agent_tree, "snapshot", lambda s, **k: (seen.append((s, k.get("all_finished", False))), snap(s, **k))[1])
    resp = asyncio.new_event_loop().run_until_complete(
        G.handle_agent_tree(make_mocked_request("GET", f"/agent-tree{qs}")))
    return resp.status, json.loads(resp.body), seen


def test_route_is_declared_read_and_registered():
    assert G.ROUTE_SCOPES[("GET", "/agent-tree")] == "read"
    app = G.build_app()
    assert ("GET", "/agent-tree") in {(r.method, r.resource.canonical) for r in app.router.routes()
                                      if r.resource is not None}


def test_returns_the_tree_without_host_paths(monkeypatch):
    status, body, seen = _get("?session=pm-x", monkeypatch)
    assert status == 200 and body == {"ok": True, "tree": {**PUBLIC, "all_finished": False}}
    assert seen == [("pm-x", False)]
    assert TREE["transcript_path"], "the snapshot itself is not mutated"


def test_all_1_asks_for_every_finished_node(monkeypatch):
    status, body, seen = _get("?session=pm-x&all=1", monkeypatch)
    assert status == 200 and body["tree"]["all_finished"] is True and seen == [("pm-x", True)]
    assert _get("?session=pm-x&all=true", monkeypatch)[2] == [("pm-x", False)]


def test_refusals_never_run_the_tree(monkeypatch):
    assert _get("?session=pm-x", monkeypatch, authorized=False)[0] == 401
    assert _get("", monkeypatch)[0] == 400
    status, _, seen = _get("?session=../etc", monkeypatch)
    assert status == 404 and seen == []                  # only a live tmux seat name is looked up
    status, body, _ = _get("?session=pm-x", monkeypatch, snap=lambda s, **k: [])
    assert status == 404 and not body["ok"]


def test_a_headless_seat_has_a_tree_without_a_tmux_pane(monkeypatch):
    # A switched-on headless seat has no tmux session but a real Claude session and subagents.
    status, body, seen = _get("?session=pm-h", monkeypatch, sessions=(), headless={"pm-h": "pm-h"})
    assert status == 200 and body["ok"] and seen == [("pm-h", False)]
    status, _, seen = _get("?session=pm-h", monkeypatch, sessions=())
    assert status == 404 and seen == []
