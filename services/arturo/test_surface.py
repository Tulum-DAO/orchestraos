import json
import time
from services.arturo import surface


def _write(tmp_path, current):
    p = tmp_path / "active-surface.json"
    p.write_text(json.dumps({"devices": {}, "current": current}))
    return p


def test_readout_fresh(tmp_path):
    now = time.time()
    p = _write(tmp_path, {"route": "/agents/jarvis-poc-builder-v3", "hint": "AgentDetail working",
                          "device": "ios", "updated_at": now - 8})
    line = surface.readout_line(p, now=now)
    assert line == 'OPERATOR\'S SCREEN: /agents/jarvis-poc-builder-v3 — "AgentDetail working" (8s ago, ios).'


def test_readout_stale_backgrounded(tmp_path):
    now = time.time()
    p = _write(tmp_path, {"route": "/approvals/apr_1", "hint": "", "device": "ios",
                          "updated_at": now - 400})
    line = surface.readout_line(p, now=now)
    assert "last seen /approvals/apr_1" in line and "app backgrounded" in line
    assert "6m ago" in line


def test_readout_null_route(tmp_path):
    p = _write(tmp_path, {"route": None})
    assert surface.readout_line(p) == "OPERATOR'S SCREEN: the operator is not looking at the app."


def test_readout_missing_or_malformed_omits(tmp_path):
    assert surface.readout_line(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"; bad.write_text("{not json")
    assert surface.readout_line(bad) is None


def test_resolve_agent_route(tmp_path):
    p = _write(tmp_path, {"route": "/agents/dev7", "updated_at": time.time()})
    def gw(path):
        if path.startswith("/agent-screen"): return {"pane": "line1\nbuilding widget\nline3"}
        if path == "/agents": return {"agents": [{"session": "dev7", "state": "working", "activity": "compiling"}]}
        if path.startswith("/transcript"): return {"turns": [{"role": "user", "content": "go"}]}
        return None
    out = surface.resolve_screen(p, gw_get=gw)
    assert "agent 'dev7'" in out
    assert "working" in out and "compiling" in out
    assert "building widget" in out


def test_resolve_agent_surfaces_full_pending_menu(tmp_path):
    # DELIB-BUG-2 / VQ-7: a parked agent's FULL option list (incl. "Type something"/"Chat about
    # this") must reach Arturo via read_screen_context, not just the truncated pane.
    p = _write(tmp_path, {"route": "/agents/jarvis-poc-builder", "updated_at": time.time()})
    menu = {"kind": "options", "question": "Which deployment strategy?",
            "options": [{"n": "1", "label": "Full rollout"}, {"n": "2", "label": "Canary cohort"},
                        {"n": "3", "label": "Defer"}, {"n": "4", "label": "Type something"},
                        {"n": "5", "label": "Chat about this"}], "selected_n": "1"}
    def gw(path):
        if path.startswith("/agent-screen"):
            return {"pane": "menu chrome...", "pending_menu": menu}
        if path == "/agents":
            return {"agents": [{"session": "jarvis-poc-builder", "state": "waiting"}]}
        return None
    out = surface.resolve_screen(p, gw_get=gw)
    # ALL five options present, including the free-text affordances
    for opt in ("1) Full rollout", "4) Type something", "5) Chat about this"):
        assert opt in out, opt
    assert "Type something" in out and "inject_message" in out   # the two-step hint


def test_resolve_approval_route(tmp_path):
    p = _write(tmp_path, {"route": "/approvals/apr_5", "updated_at": time.time()})
    out = surface.resolve_screen(p, gw_get=lambda path: {"id": "apr_5", "summary": "ship it", "evidence": "tests pass"})
    assert "approval apr_5" in out and "ship it" in out


def test_resolve_dead_route_does_not_guess(tmp_path):
    p = _write(tmp_path, {"route": "/bogus/x", "updated_at": time.time()})
    out = surface.resolve_screen(p, gw_get=lambda path: None)
    assert "can't resolve" in out
    p2 = _write(tmp_path, {"route": None})
    assert "isn't looking at anything" in surface.resolve_screen(p2, gw_get=lambda path: None)


def test_resolve_bare_list_route_falls_back_to_voice_focus(tmp_path):
    # gm-mine-menu-card seam 3: the operator is scrolling the plain /approvals LIST (no
    # tap, no id in the route) but focus_entity() already voice-located a
    # specific card and wrote it to focus.json. Before the fix, resolve_screen
    # ignored focus.json entirely (only build_turn_context read it) and
    # returned "I can't resolve that screen" even though the focus was live.
    p = _write(tmp_path, {"route": "/approvals", "updated_at": time.time()})
    focus_path = tmp_path / "focus.json"
    focus_path.write_text(json.dumps({
        "entity": {"kind": "approval", "id": "apr_993fc651_2533713"},
        "asserted_at": time.time(), "source": "voice", "sticky": True,
    }))
    out = surface.resolve_screen(
        p, gw_get=lambda path: {"id": "apr_993fc651_2533713", "summary": "which audience?"},
        focus_path=focus_path,
    )
    assert "apr_993fc651_2533713" in out and "which audience?" in out


def test_resolve_bare_list_route_without_focus_still_cant_resolve(tmp_path):
    # No focus.json at all (or an empty one) -> unchanged pre-fix behavior.
    p = _write(tmp_path, {"route": "/approvals", "updated_at": time.time()})
    out = surface.resolve_screen(p, gw_get=lambda path: None,
                                 focus_path=tmp_path / "no-focus.json")
    assert "can't resolve" in out


def test_resolve_id_bearing_route_ignores_focus_json(tmp_path):
    # Route-first precedence: an id-bearing route ALWAYS wins over focus.json,
    # even if focus.json names a different entity.
    p = _write(tmp_path, {"route": "/approvals/apr_route_wins", "updated_at": time.time()})
    focus_path = tmp_path / "focus.json"
    focus_path.write_text(json.dumps({
        "entity": {"kind": "approval", "id": "apr_focus_loses"},
        "asserted_at": time.time(), "source": "voice", "sticky": True,
    }))
    out = surface.resolve_screen(
        p, gw_get=lambda path: {"id": "apr_route_wins", "summary": "route wins"},
        focus_path=focus_path,
    )
    assert "apr_route_wins" in out and "apr_focus_loses" not in out


def test_route_kind():
    assert surface.route_kind("/agents/foo") == ("agent", "foo")
    assert surface.route_kind("/approvals/apr_9") == ("approval", "apr_9")
    assert surface.route_kind("/history/apr_9") == ("approval", "apr_9")
    assert surface.route_kind("/projects/acme") == ("project", "acme")
    assert surface.route_kind("/pipeline") == ("pipeline", None)
    assert surface.route_kind("/arturo") == ("arturo", None)
    assert surface.route_kind("/voice/vc_abc") == ("voice", "vc_abc")
    assert surface.route_kind(None) == (None, None)
    assert surface.route_kind("/unknown/x") == (None, None)


def test_parse_stack_reads_layers():
    doc = {"current": {"device": "ios", "conv_id": "cv1", "stack": [
        {"z": 0, "role": "base", "route": "/agents/acme-dev",
         "entity": {"kind": "agent", "id": "acme-dev"}, "hint": "AgentDetail: acme-dev"},
        {"z": 1, "role": "overlay", "route": "/voice/vc_1",
         "entity": {"kind": "voice", "id": "vc_1"}, "hint": "call live"},
        {"z": 2, "role": "badge", "route": "/approvals",
         "entity": {"kind": "approval_count", "id": None}, "hint": "2 pending"},
    ]}}
    layers = surface.parse_stack(doc)
    assert [l["z"] for l in layers] == [0, 1, 2]
    assert layers[0]["entity"]["kind"] == "agent"


def test_parse_stack_wraps_legacy_flat():
    doc = {"current": {"device": "ios", "route": "/agents/x", "hint": "h"}}
    layers = surface.parse_stack(doc)
    assert len(layers) == 1
    assert layers[0]["route"] == "/agents/x"
    assert layers[0]["role"] == "base"


def test_pick_focus_skips_voice_and_badge():
    layers = [
        {"z": 0, "role": "base", "entity": {"kind": "agent", "id": "acme-dev"}},
        {"z": 1, "role": "overlay", "entity": {"kind": "voice", "id": "vc_1"}},
        {"z": 2, "role": "badge", "entity": {"kind": "approval_count", "id": None}},
    ]
    focus = surface.pick_focus(layers)
    assert focus["entity"] == {"kind": "agent", "id": "acme-dev"}


def test_pick_focus_prefers_topmost_resolvable_sheet():
    layers = [
        {"z": 0, "role": "base", "entity": {"kind": "agent", "id": "a"}},
        {"z": 1, "role": "sheet", "entity": {"kind": "approval", "id": "apr_9"}},
    ]
    assert surface.pick_focus(layers)["entity"]["id"] == "apr_9"


def test_pick_focus_none_when_only_voice():
    layers = [{"z": 0, "role": "overlay", "entity": {"kind": "arturo", "id": None}}]
    assert surface.pick_focus(layers) is None


def test_ambient_line_busiest_projects_from_cache():
    agents = {"agents": [
        {"session": "acme-dev", "state": "working", "project": "acme"},
        {"session": "acme-legal", "state": "working", "project": "acme"},
        {"session": "arturo-x", "state": "working", "project": "arturo"},
        {"session": "idle-1", "state": "idle", "project": "acme"},
    ]}
    line = surface.ambient_line(lambda p: agents if p == "/agents" else None)
    assert "3 agents working" in line or "running" in line
    assert "acme (2)" in line
    assert "arturo (1)" in line
    assert "on track" not in line.lower()


def test_ambient_line_omits_on_cache_miss():
    assert surface.ambient_line(lambda p: None) is None


def test_resolve_stack_tiers_focus_recent_ambient(tmp_path):
    doc = {"current": {"device": "ios", "conv_id": "cv1", "stack": [
        {"z": 0, "role": "base", "route": "/agents/acme-dev",
         "entity": {"kind": "agent", "id": "acme-dev"}, "hint": "AgentDetail"},
        {"z": 1, "role": "overlay", "route": "/voice/vc_1",
         "entity": {"kind": "voice", "id": "vc_1"}, "hint": "call"},
    ]}}
    p = tmp_path / "active-surface.json"
    p.write_text(json.dumps(doc))

    def fake_gw(path):
        if path == "/agents":
            return {"agents": [{"session": "acme-dev", "state": "working",
                                "project": "acme", "activity": "OAuth screen"}]}
        if path.startswith("/agent-screen"):
            return {"pane": "waiting at oauth"}
        if path.startswith("/transcript"):
            return {"turns": [{"role": "user", "content": "fix it"}]}
        return None

    recent = [{"ts": 1.0, "focused": {"kind": "project", "id": "globex"}}]
    blob = surface.resolve_stack(p, gw_get=fake_gw, recent_events=recent)
    assert "IN FRONT OF YOU" in blob
    assert "acme-dev" in blob
    assert "AROUND THE CORNER" in blob
    assert "globex" in blob
    assert "AMBIENT" in blob
    assert "acme (1)" in blob


def test_resolve_stack_focus_is_agent_behind_call(tmp_path):
    # regression for the 2026-08-10 failure: call overlay must NOT mask the agent
    doc = {"current": {"stack": [
        {"z": 0, "role": "base", "route": "/agents/acme-dev",
         "entity": {"kind": "agent", "id": "acme-dev"}},
        {"z": 1, "role": "overlay", "route": "/voice/vc_1",
         "entity": {"kind": "voice", "id": "vc_1"}},
    ]}}
    p = tmp_path / "s.json"; p.write_text(json.dumps(doc))
    blob = surface.resolve_stack(p, gw_get=lambda x: None, recent_events=[])
    assert "acme-dev" in blob


def test_build_turn_context_uses_stack_and_returns_focus(tmp_path):
    doc = {"current": {"stack": [
        {"z": 0, "role": "base", "route": "/agents/acme-dev",
         "entity": {"kind": "agent", "id": "acme-dev"}},
        {"z": 1, "role": "overlay", "entity": {"kind": "voice", "id": "vc_1"}},
    ]}}
    p = tmp_path / "active-surface.json"; p.write_text(json.dumps(doc))
    calls = []

    def spy_gw(path):
        calls.append(path)
        return {"agents": [{"session": "acme-dev", "state": "working", "project": "acme"}]}

    blob, focused = surface.build_turn_context(p, gw_get=spy_gw, recent_events=[])
    assert "acme-dev" in blob                                    # composite resolve used
    assert focused["entity"] == {"kind": "agent", "id": "acme-dev"}  # default target
    assert "/agents" in calls                                       # warm cache path
    assert not any("scan" in c or "cold" in c for c in calls)       # never cold scan
