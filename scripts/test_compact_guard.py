"""Until compacting is turned on for real seats, a person's /compact runs only on a TEST seat that runs headless.
Everywhere else it is refused with one plain line and nothing is sent: not to the pane, not to the supervisor. Mail's
"/compact" is ordinary text, as before."""
import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import headless_switch as HS  # noqa: E402

G = importlib.import_module("watch_gateway")


@pytest.fixture
def seats(tmp_path, monkeypatch):
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {
        "pm-demo": {"tmux_session": "pm-demo"},
        "qa-demo": {"tmux_session": "qa-demo", "test": True},
        "dev-demo": {"tmux_session": "dev-demo"}}}))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    sent, typed = [], []
    monkeypatch.setattr(HS, "send", lambda seat, text, mid, **kw: sent.append((seat, text, kw)) or
                        {"ok": True, "state": "intercepted"})
    monkeypatch.setattr(G, "_verified_inject_locked", lambda *a, **k: typed.append(a) or (True, {"verified": True}))
    return sent, typed


def headless(monkeypatch, *names):
    monkeypatch.setattr(G, "_headless_seat_for", lambda session: session if session in names else None)


def test_a_real_headless_seat_refuses_and_nothing_is_sent(seats, monkeypatch):
    sent, typed = seats
    headless(monkeypatch, "pm-demo", "qa-demo")
    ok, info = G.verified_inject("pm-demo", " /compact ", source="person")
    assert ok is False and info == {"reason": "compact_refused", "detail": "Compacting isn't turned on for this agent yet."}
    assert sent == [] and typed == []


def test_a_real_pane_seat_refuses_and_nothing_is_typed(seats, monkeypatch):
    sent, typed = seats
    headless(monkeypatch)
    ok, info = G.verified_inject("dev-demo", "/compact", source="person")
    assert ok is False and info["detail"] == G.COMPACT_OFF and sent == [] and typed == []


def test_a_test_seat_that_runs_headless_passes_through(seats, monkeypatch):
    sent, typed = seats
    headless(monkeypatch, "qa-demo")
    ok, _ = G.verified_inject("qa-demo", "/compact", source="person")
    assert ok and sent == [("qa-demo", "/compact", {"source": "person"})]


def test_a_test_seat_on_a_pane_is_refused_too(seats, monkeypatch):
    sent, typed = seats
    headless(monkeypatch)
    ok, info = G.verified_inject("qa-demo", "/compact", source="person")
    assert ok is False and info["reason"] == "compact_refused" and typed == []


def test_mail_compact_is_plain_text_as_before(seats, monkeypatch):
    sent, typed = seats
    headless(monkeypatch, "pm-demo")
    G.verified_inject("pm-demo", "/compact", source="mail")
    G.verified_inject("dev-demo", "/compact")
    assert sent == [("pm-demo", "/compact", {"source": "mail"})] and len(typed) == 1


def test_the_refusal_is_a_409_with_the_plain_line(seats, monkeypatch):
    headless(monkeypatch, "pm-demo")
    monkeypatch.setattr(G, "_authorized", lambda request: True)
    monkeypatch.setattr(G, "_protected_refusal", lambda request, session: None)
    monkeypatch.setattr(G, "_tmux_session_names", lambda: {"pm-demo"})

    class Req:
        headers = {}

        async def json(self):
            return {"session": "pm-demo", "text": "/compact"}
    resp = asyncio.run(G.handle_agent_message(Req()))
    assert resp.status == 409
    body = json.loads(resp.text)
    assert body["reason"] == "compact_refused" and body["detail"] == "Compacting isn't turned on for this agent yet."


def test_the_allow_rule_is_one_function():
    assert G._compact_allowed("x", None) is False
