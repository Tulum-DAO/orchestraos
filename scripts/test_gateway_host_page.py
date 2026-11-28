"""A call from inside the finance tracker: the gateway keeps the host app and its page (bounded) in the `web` surface
slot, where the voice brain's prompt reads it (services/arturo/host_page.surface_block). The dashboard's own calls
write the slot exactly as before."""
import json

import scripts.watch_gateway as G
from services.arturo import host_page


def _paths(tmp_path, monkeypatch):
    sp = tmp_path / "arturo" / "active-surface.json"
    monkeypatch.setattr(G, "SURFACE_PATH", sp)
    monkeypatch.setattr(G, "VOICE_CALLS_DIR", tmp_path / "voice-calls")
    return sp


def test_the_host_page_rides_in_the_web_slot(tmp_path, monkeypatch):
    sp = _paths(tmp_path, monkeypatch)
    frame = {"app": "spending-tracker", "route": "/spending-tracker/activity",
             "page": {"title": "Spending · Transactions", "summary": "row\n" * 10 + "x" * 4000}}
    assert G._record_web_call_surface("/spending-tracker/activity", None, None, host=frame)
    cur = json.loads(sp.read_text())["current"]
    assert cur["host"]["app"] == "spending-tracker" and cur["host"]["title"] == "Spending · Transactions"
    assert len(cur["host"]["summary"]) <= host_page.SUMMARY_MAX
    assert 'on its "Spending · Transactions" page' in host_page.surface_block(sp)


def test_a_dashboard_call_has_no_host(tmp_path, monkeypatch):
    sp = _paths(tmp_path, monkeypatch)
    G._record_web_call_surface("/approvals", None)
    assert "host" not in json.loads(sp.read_text())["current"]
    G._record_web_call_surface("/approvals", None, host={"app": "Not A Slug", "route": "/x"})
    assert "host" not in json.loads(sp.read_text())["current"]
    assert host_page.surface_block(sp) == ""
