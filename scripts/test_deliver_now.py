"""Deliver now: the waiver (scripts/deliver_now.py) and the router's Claude mid-turn check."""
import importlib.util
import json
import os
import subprocess

import pytest

import scripts.deliver_now as D

_spec = importlib.util.spec_from_file_location(
    "message_router_dn", os.path.join(os.path.dirname(__file__), "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

NOW = 1_800_000_000


def test_a_waiver_is_in_force_for_five_minutes_then_gone(tmp_path):
    rec = D.write_waiver(tmp_path, "gm", "shaw-iphone", now=NOW)
    assert rec == {"session": "gm", "by": "shaw-iphone", "at": NOW, "expires_at": NOW + D.WAIVER_S}
    assert D.active_waiver(tmp_path, "gm", now=NOW + D.WAIVER_S - 1)["by"] == "shaw-iphone"
    assert D.active_waiver(tmp_path, "gm", now=NOW + D.WAIVER_S) is None
    assert D.active_waiver(tmp_path, "other", now=NOW) is None          # one session only


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 200, None])
def test_a_session_name_cannot_escape_the_waiver_folder(tmp_path, bad):
    with pytest.raises(ValueError):
        D.write_waiver(tmp_path, bad, "x", now=NOW)


def test_a_malformed_or_foreign_waiver_is_no_waiver(tmp_path):
    d = D.waiver_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "gm.json").write_text("{not json")
    assert D.active_waiver(tmp_path, "gm", now=NOW) is None
    (d / "gm.json").write_text(json.dumps({"session": "ops", "expires_at": NOW + 99}))
    assert D.active_waiver(tmp_path, "gm", now=NOW) is None
    (d / "gm.json").write_text(json.dumps({"session": "gm", "expires_at": True}))
    assert D.active_waiver(tmp_path, "gm", now=NOW) is None


def test_waiting_summary_names_the_latest_hold_reason():
    holds = {
        "gm|breathe-window": {"logged_at": NOW - 300, "msgs": {"m1": {"seen_at": NOW - 200}}},
        "gm|not-idle": {"logged_at": NOW - 100, "msgs": {"m1": {"seen_at": NOW - 10}}},
        "ops|attached-active": {"msgs": {"m9": {"seen_at": NOW}}},
    }
    rows = [{"id": "m1", "from_agent": "pm", "subject": "s", "created_at": "2027-01-15T08:00:00+00:00"}]
    out = D.waiting_summary(rows, holds, "gm", now=NOW)
    assert out["count"] == 1 and out["messages"][0]["from"] == "pm"
    assert out["reason"] == D.REASONS["not-idle"]
    assert D.waiting_summary([], holds, "gm", now=NOW) == {"count": 0, "messages": [], "reason": None}
    assert "next delivery pass" in D.waiting_summary(rows, {}, "gm", now=NOW)["reason"]


class _R:
    def __init__(self, out, rc=0):
        self.stdout, self.returncode = out, rc


def _pane(monkeypatch, ansi, hook=None):
    monkeypatch.setattr(mr, "hook_state", lambda s: hook)
    monkeypatch.setattr(mr, "tmux", lambda *a, **k: _R(ansi))
    monkeypatch.setattr(mr, "pane_split", lambda s, r="claude": ([], ansi.split("\n")[-1], True))


def test_claude_mid_turn_takes_a_message_only_into_an_empty_composer(monkeypatch):
    _pane(monkeypatch, "✻ Thinking… (12s · esc to interrupt)\n❯ ")
    assert mr.claude_mid_turn_safe("gm") is True


def test_typed_text_in_the_box_is_never_waived(monkeypatch):
    _pane(monkeypatch, "✻ Thinking… (12s · esc to interrupt)\n❯ half a sentence the human is typing")
    assert mr.claude_mid_turn_safe("gm") is False


def test_a_permission_dialog_is_never_waived(monkeypatch):
    _pane(monkeypatch, "Do you want to proceed?\n 1. Yes\n 2. No")           # no composer on screen
    assert mr.claude_mid_turn_safe("gm") is False
    _pane(monkeypatch, "❯ ", hook=("waiting_permission", NOW))
    assert mr.claude_mid_turn_safe("gm") is False


def test_a_stranded_paste_chip_is_never_waived(monkeypatch):
    _pane(monkeypatch, "❯ [Pasted text #1 +12 lines]")
    assert mr.claude_mid_turn_safe("gm") is False


def test_the_router_reads_waivers_from_its_own_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "ORCHESTRA_DIR", tmp_path)
    assert mr.deliver_now_waiver("gm") is None
    D.write_waiver(tmp_path, "gm", "web")
    assert mr.deliver_now_waiver("gm")["by"] == "web"


# ---- gateway ---------------------------------------------------------------------------------
import scripts.watch_gateway as G
from scripts.test_gateway_scopes import _call, _run, _with_store


def test_held_is_read_and_deliver_now_is_message():
    assert G.ROUTE_SCOPES[("GET", "/held")] == "read"
    assert G.ROUTE_SCOPES[("POST", "/deliver-now")] == "message"


def test_a_read_only_device_cannot_deliver_now(monkeypatch, tmp_path):
    _, _, read_only = _with_store(monkeypatch, tmp_path, ["read"])
    resp, reached, _ = _call("POST", "/deliver-now", {"Authorization": f"Bearer {read_only}"})
    assert resp.status == 403 and not reached


class _Req(dict):
    def __init__(self, body=None, query=None, principal=None):
        super().__init__()
        self._body, self.query, self.method, self.headers = body, query or {}, "POST", {}
        if principal is not None:
            self["principal"] = principal

    async def json(self):
        return self._body


def _gw(monkeypatch, tmp_path, sessions=("gm",)):
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_tmux_session_names", lambda: set(sessions))
    monkeypatch.setattr(G, "_protected_refusal", lambda req, s: None)
    kicks = []
    monkeypatch.setattr(G, "_kick_router", lambda: kicks.append(1) or True)
    monkeypatch.setattr(G, "_waiting_for", lambda s: {"count": 1, "messages": [], "reason": "x"})
    return kicks


def test_deliver_now_writes_a_waiver_naming_who_pressed_it_and_kicks_the_router(monkeypatch, tmp_path):
    kicks = _gw(monkeypatch, tmp_path)
    resp = _run(G.handle_deliver_now(_Req({"session": "gm"}, principal={"id": "d1", "label": "Shaw iPhone", "scopes": ["message"]})))
    body = json.loads(resp.body)
    assert resp.status == 200 and body["waiver"]["by"] == "Shaw iPhone" and kicks == [1]
    assert D.active_waiver(tmp_path, "gm")["by"] == "Shaw iPhone"


def test_deliver_now_refuses_an_unknown_or_protected_session(monkeypatch, tmp_path):
    _gw(monkeypatch, tmp_path)
    resp = _run(G.handle_deliver_now(_Req({"session": "nope"}, principal={"id": "d", "scopes": ["*"]})))
    assert resp.status == 404 and D.active_waiver(tmp_path, "nope") is None
    monkeypatch.setattr(G, "_protected_refusal", lambda req, s: {"ok": False, "error": "protected"})
    resp = _run(G.handle_deliver_now(_Req({"session": "gm"}, principal={"id": "d", "scopes": ["*"]})))
    assert resp.status == 403 and D.active_waiver(tmp_path, "gm") is None


def test_held_reports_the_waiting_mail_and_any_waiver(monkeypatch, tmp_path):
    _gw(monkeypatch, tmp_path)
    req = _Req(query={"session": "gm"}, principal={"id": "d", "scopes": ["read"]})
    body = json.loads(_run(G.handle_held(req)).body)
    assert body["count"] == 1 and body["waiver"] is None
