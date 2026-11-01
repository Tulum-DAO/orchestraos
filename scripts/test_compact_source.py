"""Who sent it: only a PERSON's /compact starts a switch. The source rides from the gateway's /agent-message (a
person's surface unless the caller says "mail") through headless_switch.send to the supervisor; everything else is
mail, so nothing triggers by accident. On a seat that does not run headless, a person's /compact is refused and never
typed into the terminal."""
import asyncio
import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import headless_switch as HS  # noqa: E402

G = importlib.import_module("watch_gateway")


def test_headless_switch_send_says_mail_unless_told_person(monkeypatch):
    sent = []
    monkeypatch.setattr(HS, "request", lambda seat, payload, orch_dir=None, timeout=5.0: sent.append(payload) or {"ok": True})
    HS.send("qa-demo", "hi", "m1")
    HS.send("qa-demo", "/compact", "m2", source="person")
    HS.send("qa-demo", "/compact", "m3", source="anything")
    assert [p["source"] for p in sent] == ["mail", "person", "mail"]


def test_a_persons_compact_on_a_terminal_seat_is_refused_and_never_typed(monkeypatch):
    monkeypatch.setattr(G, "_headless_seat_for", lambda session: None)
    typed = []
    monkeypatch.setattr(G, "_verified_inject_locked", lambda *a, **k: typed.append(a) or (True, {}))
    ok, info = G.verified_inject("pm-demo", "/compact", source="person")
    assert ok is False and info["reason"] == "compact_refused" and typed == []
    ok, _ = G.verified_inject("pm-demo", "/compact", source="mail")
    assert typed, "mail's /compact is ordinary text, typed as before"


def test_a_headless_seat_gets_the_source_with_the_words(monkeypatch):
    monkeypatch.setattr(G, "_headless_seat_for", lambda session: "qa-demo")
    monkeypatch.setattr(G, "_compact_allowed", lambda session, hl=None: True)   # a seat compacting is on for
    seen = []
    monkeypatch.setattr(HS, "send", lambda seat, text, mid, **kw: seen.append(kw) or {"ok": True, "state": "intercepted"})
    G.verified_inject("qa-demo", "/compact", source="person")
    G.verified_inject("qa-demo", "hello")
    assert [k.get("source") for k in seen] == ["person", "mail"]


def test_agent_message_is_a_person_unless_the_caller_says_mail(monkeypatch):
    got = []
    monkeypatch.setattr(G, "_authorized", lambda request: True)
    monkeypatch.setattr(G, "_protected_refusal", lambda request, session: None)
    monkeypatch.setattr(G, "_tmux_session_names", lambda: {"qa-demo"})
    monkeypatch.setattr(G, "verified_inject", lambda s, t, f=False, source="mail": got.append(source) or
                        (False, {"reason": "send_failed"}))

    class Req:
        def __init__(self, body):
            self.body = body
            self.headers = {}

        async def json(self):
            return self.body
    for body in ({"session": "qa-demo", "text": "/compact"}, {"session": "qa-demo", "text": "/compact", "source": "mail"}):
        try:
            asyncio.run(G.handle_agent_message(Req(body)))
        except Exception:  # noqa: BLE001 — the reply path after a refusal is not what this test reads
            pass
    assert got == ["person", "mail"]
