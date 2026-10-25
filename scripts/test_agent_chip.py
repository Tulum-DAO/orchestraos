"""agent_chip: the one status chip (docs/AGENT_CHIP.md). Fixtures are rows shaped like the gateway's
/agents output for each detector state."""
import json
import os

import pytest

import scripts.agent_chip as C
import scripts.watch_gateway as G

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "contract", "fixtures", "agent-chips.json")


@pytest.mark.parametrize("state,menu,cards,want", [
    ("working", False, 0, "working"),
    ("idle", False, 0, "idle"),
    ("waiting", False, 0, "needs_you"),          # a permission prompt
    ("stranded", False, 0, "needs_you"),
    ("working", True, 0, "needs_you"),           # a pending menu outranks working
    ("idle", False, 1, "needs_you"),             # a card this seat filed, waiting on a person
    ("offline", False, 2, "needs_you"),          # a stopped seat can still be waiting on a person
    ("crashed", False, 0, "down"),
    ("stalled", False, 0, "down"),
    ("offline", False, 0, "down"),
    ("stopped", False, 0, "down"),
    (None, False, 0, "down"),                    # unknown is never "alive at the prompt"
    ("some-new-state", False, 0, "down"),
])

def test_status(state, menu, cards, want):
    assert C.chip_status(state, menu, cards) == want


def test_every_state_the_gateway_emits_is_mapped_on_purpose():
    for st in set(G._STATE_MAP.values()) | {"crashed", "offline"}:
        assert C.chip_status(st) in {"working", "needs_you", "idle", "down"}, st
    assert C.chip_status("idle") == "idle" and C.chip_status("working") == "working"


@pytest.mark.parametrize("model,label", [
    ("claude-opus-5-5", "Opus 5.5"), ("claude-opus-5-5[1m]", "Opus 5.5"), ("claude-fable-5-1", "Fable 5.1"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5"), ("claude-sonnet-5", "Sonnet 5"),
    ("grok-4.7", "Grok 4.7"), ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
    ("gpt-5.6-terra", "GPT-5.6 Terra"),          # the catalog's own label
    ("gpt-9-unknown", "gpt-9-unknown"),          # no label known: shown as it is, never invented
    ("Opus 5.5", "Opus 5.5"), ("", None), (None, None),
])
def test_model_label(model, label):
    assert C.model_label(model) == label


def test_provider_falls_back_to_the_registry_and_knows_grok():
    assert C.provider_id(None, "grok") == "grok"
    assert C.provider_id("codex", "claude") == "codex"            # the live process wins
    assert C.provider_id("antigravity") == "gemini"
    assert C.provider_id("bash", None) is None                    # not a provider: no badge


@pytest.mark.parametrize("used,left", [(8, 92), (0, 100), (100, 0), (None, None), ("8", None),
                                       (True, None), (-1, None), (101, None)])
def test_context_left_never_invents_a_reading(used, left):
    assert C.context_left(used) == left


def test_the_published_fixture_is_what_chip_for_returns():
    # docs/AGENT_CHIP.md points native clients at this file; it must never drift from the code.
    cases = json.load(open(FIXTURE))
    for case in cases:
        assert C.chip_for(case["row"], case.get("registry_runtime"), case.get("pending_cards", 0)) \
            == case["chip"], case["row"].get("id")


def test_every_chip_provider_has_one_mark_in_the_shared_set():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "providers.json")
    marks = {p["id"]: p.get("logo_svg") for p in json.load(open(path))["providers"]}
    for p in C.PROVIDERS:
        assert isinstance(marks.get(p), str) and marks[p].lstrip().startswith("<svg"), p


def test_gateway_stamps_a_chip_on_every_row(monkeypatch):
    monkeypatch.setattr(G, "_pending_cards_by_author", lambda: {"ops": 1})
    rows = [{"id": "gm", "state": "working", "provider": "claude", "model": "claude-opus-5-5",
             "context_pct_of_window": 8},
            {"id": "ops", "state": "idle", "provider": None, "model": None}]
    G._stamp_chips(rows, {"ops": {"runtime": "grok"}})
    assert rows[0]["chip"] == {"name": "gm", "kind": "agent", "provider": "claude",
                               "model_label": "Opus 5.5", "status": "working", "context_left_pct": 92}
    assert rows[1]["chip"]["provider"] == "grok" and rows[1]["chip"]["status"] == "needs_you"


def test_a_broken_card_store_never_breaks_the_chips(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no db")
    monkeypatch.setattr(G, "ApprovalStore", Boom)
    assert G._read_pending_cards_by_author() == {}


def test_pending_card_counts_are_cached_between_refreshes(monkeypatch):
    calls = []
    monkeypatch.setattr(G, "_read_pending_cards_by_author", lambda: calls.append(1) or {"ops": 1})
    monkeypatch.setattr(G, "_pending_cards_cache", {"at": -1e18, "counts": {}})
    for _ in range(5):
        assert G._pending_cards_by_author() == {"ops": 1}
    assert len(calls) == 1


def test_a_service_seat_is_a_service_not_a_free_agent():
    # A proxy registered as a seat reads idle, i.e. a green "talk to it" dot, but it takes
    # no messages. kind says so; clients draw "Service" and no message box.
    c = C.chip_for({"id": "watch-gateway", "state": "idle"}, registry_runtime="service")
    assert c["kind"] == "service" and c["provider"] is None
    assert C.chip_for({"id": "x", "state": "idle", "raw_state": "service"})["kind"] == "service"


def test_an_external_seat_is_not_checked_here_never_down():
    # Operated elsewhere, liveness not knowable here; red down would assert it is dead.
    c = C.chip_for({"id": "remote", "state": "external"}, registry_runtime="claude")
    assert c["kind"] == "external" and c["status"] is None and c["provider"] == "claude"
    assert C.chip_for({"id": "gm", "state": "working"})["kind"] == "agent"
