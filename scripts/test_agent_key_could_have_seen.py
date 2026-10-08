"""A single-digit permission answer can only answer what the device could have seen.

/agent-key's digit path sends the key to whatever prompt is on screen, and the apps send no card
identity with it. So a tap meant for "proceed? [Bash: cmd A]" used to approve cmd B if B replaced
A before the tap arrived. The gateway now refuses (409) when the prompt on screen was first seen
AFTER the requesting device last fetched a surface showing permission cards, or when it has no
fetch on record for that device.
"""
import asyncio
import json

import pytest

import scripts.watch_gateway as G

SESSION = "seat"
PHONE_UA = "OrchestraOS/269 CFNetwork Darwin iOS"
WATCH_UA = "OrchestraOS/267 CFNetwork Darwin watchOS"


def _menu(cmd):
    return {"kind": "permission", "question": "Do you want to proceed? [Bash]",
            "context": f"Bash command\n{cmd}\nThis command requires approval",
            "options": [{"n": "1", "label": "Yes"}, {"n": "2", "label": "No"}]}


CMD_A, CMD_B = _menu("echo safe"), _menu("rm -rf /tmp/important")


class _Req:
    def __init__(self, payload=None, ua=PHONE_UA):
        self.headers = {"Authorization": "Bearer fleet-tok", "User-Agent": ua}
        self._payload = payload or {}
        self.query = {}
        self.match_info = {}

    async def json(self):
        return self._payload


@pytest.fixture
def screen(monkeypatch, tmp_path):
    state = {"menu": CMD_A, "sent": []}

    class _AS:
        def get_agent_status(self, session):
            return {"pending_menu": state["menu"], "state": "waiting"}

    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-tok")
    monkeypatch.setattr(G, "_PERM_INSTANCE_LEDGER", str(tmp_path / "ledger.json"), raising=False)
    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    monkeypatch.setattr(G, "_tmux_session_names", lambda: [SESSION])
    monkeypatch.setattr(G, "_is_gemini_session", lambda s: False)
    monkeypatch.setattr(G, "_protected_refusal", lambda request, session: None)

    class _R:
        returncode = 0

    monkeypatch.setattr(G, "_tmux", lambda *a: state["sent"].append(a) or _R())
    monkeypatch.setattr(G, "_MENU_FETCHES", {})
    return state


def _show(menu, now):
    """A surface rendered `menu`: the gateway stamps its instance (as /pending-approvals and
    /agent-screen do) at `now`."""
    G._stamp_instance(SESSION, menu, now=now)


def _fetch(now, ua=PHONE_UA):
    G._note_menu_fetch(_Req(ua=ua), now=now)


def _tap(ua=PHONE_UA, key="1"):
    resp = asyncio.run(G.handle_agent_key(_Req({"session": SESSION, "key": key, "confirm": True}, ua=ua)))
    return resp.status, json.loads(resp.text)


def test_a_device_that_saw_the_prompt_can_answer_it(screen):
    _show(CMD_A, now=100.0)
    _fetch(now=101.0)
    status, body = _tap()
    assert status == 200 and body["sent"] == "1"
    assert screen["sent"], "the key reached the pane"


def test_a_tap_meant_for_A_does_not_approve_B_that_replaced_it(screen):
    _show(CMD_A, now=100.0)
    _fetch(now=101.0)                         # the device saw A
    screen["menu"] = CMD_B
    _show(CMD_B, now=105.0)                   # B appeared after that fetch (another surface stamped it)
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_mismatch"
    assert screen["sent"] == [], "nothing reached the pane"


def test_a_prompt_no_surface_has_shown_yet_is_refused(screen):
    _show(CMD_A, now=100.0)
    _fetch(now=101.0)
    screen["menu"] = CMD_B                    # B is on screen but nothing has stamped it yet
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_unknown" and screen["sent"] == []


def test_after_a_fresh_fetch_the_new_prompt_can_be_answered(screen):
    _show(CMD_A, now=100.0)
    _fetch(now=101.0)
    screen["menu"] = CMD_B
    _show(CMD_B, now=105.0)
    _fetch(now=106.0)                         # the device refreshed and saw B
    assert _tap()[0] == 200


def test_no_fetch_on_record_fails_closed_for_permission_prompts(screen):
    _show(CMD_A, now=100.0)
    status, body = _tap()
    assert status == 409 and body["reason"] == "instance_unknown"
    assert "Open it in Approvals" in body["error"]


def test_phone_and_watch_on_one_bearer_do_not_vouch_for_each_other(screen):
    _show(CMD_A, now=100.0)
    _fetch(now=101.0, ua=WATCH_UA)            # the watch last looked when A was up
    screen["menu"] = CMD_B
    _show(CMD_B, now=105.0)
    _fetch(now=106.0, ua=PHONE_UA)            # the phone has seen B
    assert _tap(ua=WATCH_UA)[0] == 409, "the watch's stale card can't answer B"
    assert _tap(ua=PHONE_UA)[0] == 200, "the phone, which saw B, can"


def test_non_permission_menus_are_not_gated(screen):
    screen["menu"] = {"kind": "options", "question": "Which?", "options": [{"n": "1", "label": "A"}]}
    status, _ = _tap()
    assert status == 200


def test_the_respond_path_is_gated_too(screen, monkeypatch):
    _show(CMD_A, now=100.0)
    called = []
    monkeypatch.setattr(G, "permission_respond", lambda *a, **k: called.append(1) or (True, {}))
    resp = asyncio.run(G.handle_agent_key(_Req({"session": SESSION, "answer": "respond",
                                                "text": "do it differently", "confirm": True})))
    assert resp.status == 409 and called == []


def test_the_two_surfaces_record_a_fetch_and_a_refusal_does_not(screen, monkeypatch):
    async def ok(request):
        return G._json({"ok": True})

    async def denied(request):
        return G._json({"ok": False}, status=401)

    for name in ("_handle_pending", "_handle_agent_screen"):
        G._MENU_FETCHES.clear()
        monkeypatch.setattr(G, name, denied)
        asyncio.run(getattr(G, name.replace("_handle", "handle"))(_Req()))
        assert G._MENU_FETCHES == {}, name
        monkeypatch.setattr(G, name, ok)
        asyncio.run(getattr(G, name.replace("_handle", "handle"))(_Req()))
        assert len(G._MENU_FETCHES) == 1, name


def test_the_instance_ledger_records_when_each_instance_was_first_seen(screen):
    _show(CMD_A, now=100.0)
    led = G._load_instance_ledger()
    (entry,) = led.values()
    assert entry["first_seen_ts"] == 100.0
    _show(CMD_A, now=150.0)                   # still the same instance: unchanged
    (entry,) = G._load_instance_ledger().values()
    assert entry["first_seen_ts"] == 100.0


def test_the_same_prompt_coming_back_as_a_NEW_instance_needs_a_fresh_look(screen):
    _show(CMD_A, now=100.0)
    _fetch(now=101.0)
    G._mark_instance_answered(SESSION, CMD_A["question"], CMD_A["context"])   # answered elsewhere
    _show(CMD_A, now=105.0)                   # the same ask again: a new instance
    assert _tap()[0] == 409
    _fetch(now=106.0)
    assert _tap()[0] == 200
