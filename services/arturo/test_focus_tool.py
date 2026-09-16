# test_focus_tool.py — focus_entity voice tool (spec §3.4) + observe-only gate (spec §6).
import json
from pathlib import Path
from services.arturo import focus_tool as FT
from services.arturo import surface


def _gw(pending=None, agents=None):
    def gw(path):
        if path == "/pending-approvals":
            return {"ok": True, "pending": pending or []}
        if path == "/agents":
            return {"ok": True, "agents": agents or []}
        if path.startswith("/approvals/"):
            rid = path.split("/")[-1]
            for r in pending or []:
                if r.get("id") == rid:
                    return r
        return None
    return gw


_APPROVAL = {"id": "apr_1", "from_agent": "pm-adaptiv-payments",
             "question": "Deploy adaptiv payments to prod?", "feature": "payments-deploy",
             "created_at": "2026-08-12T10:00:00+00:00"}


def test_match_armed_asserts_focus(tmp_path):
    fp = tmp_path / "focus.json"
    log = []
    out = FT.focus_entity("what's that adaptiv approval about", _gw(pending=[_APPROVAL]),
                          now=1_760_000_000.0, focus_path=fp, observe=False, log=log.append)
    assert "pm-adaptiv-payments" in out                       # spoken confirmation names the source
    cur = surface.current_focus(focus_path=fp)
    assert cur is not None and cur["entity"]["id"] == "approval:apr_1"
    assert cur["source"] == "voice"
    # observe-only log still records the resolve even when armed
    assert log and log[0]["status"] == "match" and log[0]["scores"]


def test_observe_mode_does_not_switch_focus(tmp_path):
    fp = tmp_path / "focus.json"
    log = []
    out = FT.focus_entity("the adaptiv approval", _gw(pending=[_APPROVAL]),
                          now=1_760_000_000.0, focus_path=fp, observe=True, log=log.append)
    # names what it WOULD pick (audible) but honest that it didn't switch
    assert "observe" in out.lower()
    assert "adaptiv" in out.lower() or "pm-adaptiv-payments" in out
    assert surface.current_focus(focus_path=fp) is None       # NO focus mutation in observe mode
    assert log[0]["would_pick"] == "approval:apr_1"


def test_ambiguous_no_focus_change(tmp_path):
    fp = tmp_path / "focus.json"
    agents = [{"id": "acme-web", "tmux_session": "acme-web", "state": "working",
               "last_used_ts": 1_760_000_000.0},
              {"id": "acme-data", "tmux_session": "acme-data", "state": "working",
               "last_used_ts": 1_760_000_000.0}]
    out = FT.focus_entity("the acme agent", _gw(agents=agents),
                          now=1_760_000_000.0, focus_path=fp, observe=False)
    assert " or " in out                                      # one-line either/or
    assert surface.current_focus(focus_path=fp) is None       # no switch until the operator picks


def test_none_sharp_best_guess(tmp_path):
    fp = tmp_path / "focus.json"
    agents = [{"id": "gm", "tmux_session": "gm", "state": "working", "last_used_ts": 1.0}]
    out = FT.focus_entity("the quarterly budget spreadsheet", _gw(agents=agents),
                          now=1_760_000_000.0, focus_path=fp, observe=False)
    assert "?" in out
    assert surface.current_focus(focus_path=fp) is None


def test_acceptance_focus_gm_content(tmp_path):
    # §0a: "go through the options ... recommendations" → focus GM's content, armed, one call.
    fp = tmp_path / "focus.json"
    agents = [{"id": "gm", "tmux_session": "gm", "state": "working",
               "last_used_ts": 1_759_999_700.0}]
    def get_output(sess):
        return ("Here are my 5 recommendations / options for acme strategy"
                if sess == "gm" else "")
    out = FT.focus_entity("go through the options you suggested for the recommendations",
                          _gw(agents=agents), get_output=get_output, focused_agent="gm",
                          now=1_760_000_000.0, focus_path=fp, observe=False)
    cur = surface.current_focus(focus_path=fp)
    assert cur is not None and cur["entity"]["kind"] == "content"
    assert cur["entity"]["from_agent"] == "gm"
