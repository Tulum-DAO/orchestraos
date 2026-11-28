"""The relay call's frames from inside the finance tracker carry {app, page}: the bridge hands them to the gateway's
surface writer as `host=`, and only when sent (an older writer takes no `host`)."""
import json

from services.arturo import relay_live_bridge as B


def test_the_host_page_goes_to_the_surface_writer():
    calls = []
    sess = B.RelayLiveSession(None, "http://x", {}, on_surface=lambda *a, **k: calls.append((a, k)))
    page = {"title": "Spending · Home", "summary": "Balance now $8,000.00."}
    sess._note_page(json.dumps({"route": "/spending-tracker/home", "focusedEntity": None, "app": "spending-tracker",
                                "page": page}))
    (route, focused, _cid), kw = calls[-1]
    assert route == "/spending-tracker/home" and focused is None
    assert kw == {"host": {"app": "spending-tracker", "route": "/spending-tracker/home", "page": page}}
    # a later frame without `app` (the card deleted, or the dashboard) clears it
    sess._note_page(json.dumps({"event": "context", "route": "/spending-tracker/home", "focusedEntity": None}))
    assert calls[-1][1] == {}


def test_an_old_three_argument_writer_still_works_without_a_host():
    pages = []
    sess = B.RelayLiveSession(None, "http://x", {}, on_surface=lambda r, f, c: pages.append((r, f)))
    sess._note_page(json.dumps({"route": "/agents", "focusedEntity": "agent:a1"}))
    assert pages == [("/agents", "agent:a1")]
