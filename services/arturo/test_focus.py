# test_focus.py — voice-assertable focus (spec §3.2). focus.json + assert/current + merge.
import json
import time
from services.arturo import surface


def _approval_ent():
    return {"kind": "approval", "id": "approval:apr_1", "label": "Deploy adaptiv to prod?",
            "aliases": [], "from_agent": "pm-adaptiv-payments", "feature": "payments-deploy",
            "pending": True}


def test_assert_then_current_roundtrips(tmp_path):
    fp = tmp_path / "focus.json"
    ent = _approval_ent()
    surface.assert_focus(ent, "voice", now=123.0, focus_path=fp)
    cur = surface.current_focus(focus_path=fp)
    assert cur["entity"]["id"] == "approval:apr_1"
    assert cur["source"] == "voice"
    assert cur["asserted_at"] == 123.0
    assert cur["sticky"] is True


def test_current_focus_missing_is_none(tmp_path):
    assert surface.current_focus(focus_path=tmp_path / "nope.json") is None


def test_no_decay_sticky(tmp_path):
    fp = tmp_path / "focus.json"
    surface.assert_focus(_approval_ent(), "voice", now=0.0, focus_path=fp)
    # a long time later, still focused (no decay — a mid-deliberation timeout is maddening)
    cur = surface.current_focus(focus_path=fp, now=10_000_000.0)
    assert cur is not None and cur["entity"]["id"] == "approval:apr_1"


def _surface(tmp_path, updated_at):
    doc = {"current": {"route": "/agents/acme-dev", "updated_at": updated_at,
                       "entity": {"kind": "agent", "id": "acme-dev"},
                       "stack": [{"z": 0, "role": "base", "route": "/agents/acme-dev",
                                  "entity": {"kind": "agent", "id": "acme-dev"}}]}}
    p = tmp_path / "active-surface.json"; p.write_text(json.dumps(doc))
    return p


def _gw(path):
    if path == "/agents":
        return {"agents": [{"session": "acme-dev", "state": "working", "project": "acme"}]}
    if path.startswith("/approvals/"):
        return {"id": "apr_1", "question": "Deploy adaptiv to prod?",
                "from_agent": "pm-adaptiv-payments", "feature": "payments-deploy"}
    return None


def test_voice_focus_wins_when_more_recent(tmp_path):
    p = _surface(tmp_path, updated_at=100.0)
    fp = tmp_path / "focus.json"
    surface.assert_focus(_approval_ent(), "voice", now=500.0, focus_path=fp)  # after screen
    blob, focused = surface.build_turn_context(p, gw_get=_gw, recent_events=[], focus_path=fp)
    assert focused["entity"]["id"] == "approval:apr_1"       # voice focus is the effective target
    assert focused["source"] == "voice"
    # hydrated top-of-mind: question + from_agent pulled in
    assert "pm-adaptiv-payments" in blob
    assert "Deploy adaptiv to prod?" in blob


def test_screen_focus_wins_when_more_recent(tmp_path):
    p = _surface(tmp_path, updated_at=900.0)
    fp = tmp_path / "focus.json"
    surface.assert_focus(_approval_ent(), "voice", now=500.0, focus_path=fp)  # before screen
    blob, focused = surface.build_turn_context(p, gw_get=_gw, recent_events=[], focus_path=fp)
    assert focused["entity"] == {"kind": "agent", "id": "acme-dev"}       # screen wins


def test_no_focus_file_keeps_screen_behavior(tmp_path):
    # backward compat: no focus.json → identical to the pre-focus behavior
    p = _surface(tmp_path, updated_at=100.0)
    blob, focused = surface.build_turn_context(p, gw_get=_gw, recent_events=[])
    assert focused["entity"] == {"kind": "agent", "id": "acme-dev"}
