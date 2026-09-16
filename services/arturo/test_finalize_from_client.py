# test_finalize_from_client.py — Piece A end-to-end merge/finalize (DEC-1786426323).
# Loads the hyphenated arturo-proxy.py by path, points VOICE_CALLS_DIR at a scratch dir seeded
# with the REAL 3-stream fixture (client spine + server-with-tools + surface sidecar), and asserts
# the finalize merges + supersedes + injects exactly once. Inject is stubbed (no live gm).
import json
import os
import shutil
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "state" / "voice-calls"
CLIENT = "vc_client_2f140442c137"
SERVER = "vc_e91877ed787b1316"


def _load_proxy(scratch):
    os.environ["ARTURO_VOICE_CALLS_DIR"] = str(scratch)
    spec = importlib.util.spec_from_file_location("arturo_proxy_undertest",
                                                  str(REPO / "services" / "arturo" / "arturo-proxy.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def scratch(tmp_path):
    d = tmp_path / "voice-calls"
    d.mkdir()
    # seed the real fixture trio (skip if fixtures absent in this checkout)
    for name in (f"{CLIENT}.json", f"{SERVER}.json", f"{CLIENT}.surface.jsonl"):
        src = FIX / name
        if not src.exists():
            pytest.skip(f"fixture missing: {name}")
        shutil.copy(src, d / name)
    # HERMETIC: the LIVE inject-retry sweeper mutates the source fixtures (gm_injected/attempts) in
    # state/voice-calls — strip all inject-state so each test starts from a clean un-injected call
    # regardless of what the live service did to the originals.
    for name in (f"{CLIENT}.json", f"{SERVER}.json"):
        p = d / name
        try:
            j = json.loads(p.read_text())
            for k in ("gm_injected", "gm_inject_attempts", "gm_inject_next_ts",
                      "gm_inject_gaveup", "gm_inject_suppressed"):
                j.pop(k, None)
            p.write_text(json.dumps(j))
        except Exception:
            pass
    return d


def test_finalize_merges_supersedes_injects_once(scratch, monkeypatch):
    m = _load_proxy(scratch)
    m._GM_INJECT_ENABLED = True
    m._INJECT_ASYNC = False                          # deterministic: inline inject, no thread race                     # force the inject gate open for the test
    injections = []
    # stub the gm injection so nothing hits the live gateway; capture calls
    monkeypatch.setattr(__import__("services.arturo.endcall", fromlist=["inject_to_gm"]),
                        "inject_to_gm", lambda *a, **k: injections.append((a, k)) or (True, 1))

    ok = m.finalize_from_client(client_call_id=CLIENT, conv_id="")
    assert ok is True

    client = json.loads((scratch / f"{CLIENT}.json").read_text())
    server = json.loads((scratch / f"{SERVER}.json").read_text())

    # server shard superseded + ended (so the watchdog guard suppresses it)
    assert server["superseded_by"] == CLIENT
    assert server["status"] == "ended"
    # server was linked + recorded on the client
    assert client.get("merged_server") == SERVER
    # merged timeline folded the server's tool turns into the client spine
    n_tools = sum(1 for t in client["turns"] if t.get("role") == "tool")
    assert n_tools >= 40                            # the real server journal has 40 tool turns
    # surface events folded (role=surface) and the sidecar deleted post-persist
    assert any(t.get("role") == "surface" for t in client["turns"])
    assert not (scratch / f"{CLIENT}.surface.jsonl").exists()
    # timeline is ts-ordered
    ts = [t.get("ts") or 0 for t in client["turns"]]
    assert ts == sorted(ts)
    # exactly one injection, marked on disk
    assert len(injections) == 1
    assert client.get("gm_injected") is True


def test_finalize_idempotent_no_double_inject(scratch, monkeypatch):
    m = _load_proxy(scratch)
    m._GM_INJECT_ENABLED = True
    m._INJECT_ASYNC = False                          # deterministic: inline inject, no thread race
    injections = []
    monkeypatch.setattr(__import__("services.arturo.endcall", fromlist=["inject_to_gm"]),
                        "inject_to_gm", lambda *a, **k: injections.append(1) or (True, 1))
    assert m.finalize_from_client(client_call_id=CLIENT) is True
    # a re-notify (gateway idempotent re-POST) must NOT inject again
    assert m.finalize_from_client(client_call_id=CLIENT) is True
    assert len(injections) == 1


def test_watchdog_guard_suppresses_superseded_server(scratch, monkeypatch):
    m = _load_proxy(scratch)
    m._GM_INJECT_ENABLED = True
    m._INJECT_ASYNC = False                          # deterministic: inline inject, no thread race
    injections = []
    monkeypatch.setattr(__import__("services.arturo.endcall", fromlist=["inject_to_gm"]),
                        "inject_to_gm", lambda *a, **k: injections.append(1) or (True, 1))
    m.finalize_from_client(client_call_id=CLIENT)
    inj_after_client = len(injections)
    # now the watchdog trips over the (now superseded+ended) server journal → must NOT inject
    m._finalize_journal_file(scratch / f"{SERVER}.json")
    assert len(injections) == inj_after_client      # no extra injection from the server shard


def test_no_leak_to_live_gm_without_stub(scratch):
    # ISOLATION regression (gm 2026-08-11 leak): even with the REAL inject_to_gm (NO monkeypatch,
    # async ON), the endcall._default_post PYTEST_CURRENT_TEST guard blocks the network POST — so a
    # finalize under pytest can never reach the live gm inbox. This is the belt-and-suspenders that
    # doesn't depend on monkeypatch discipline. We just assert it completes without raising/posting.
    import time as _t
    m = _load_proxy(scratch)
    m._GM_INJECT_ENABLED = True                      # inject gate OPEN, no stub — the guard must hold
    assert m.finalize_from_client(client_call_id=CLIENT) is True
    _t.sleep(0.5)                                    # let any async inject thread run + hit the guard
    # client journal still finalized correctly (the inject being blocked doesn't corrupt state)
    client = json.loads((scratch / f"{CLIENT}.json").read_text())
    assert client["status"] == "ended" and client.get("merged_server") == SERVER


def test_busy_gm_inject_retries_then_succeeds(scratch, monkeypatch):
    # DELIB-BUG-1: gm busy on first attempt -> gm_injected stays False + attempts bumped; a later
    # re-drive (sweeper/re-notify) with gm idle -> injects, gm_injected True. The old optimistic
    # flag lost the transcript here.
    m = _load_proxy(scratch)
    m._GM_INJECT_ENABLED = True
    m._INJECT_ASYNC = False
    state = {"busy": True, "calls": 0}
    endcall = __import__("services.arturo.endcall", fromlist=["inject_to_gm"])

    def fake_inject(session, summary, marker, **kw):
        state["calls"] += 1
        return (not state["busy"]), 1        # (ok, attempts)
    monkeypatch.setattr(endcall, "inject_to_gm", fake_inject)

    # first finalize: gm busy -> not injected, but merged + durable
    assert m.finalize_from_client(client_call_id=CLIENT) is True
    j = json.loads((scratch / f"{CLIENT}.json").read_text())
    assert j.get("gm_injected") is False
    assert j.get("gm_inject_attempts") == 1
    assert j.get("status") == "ended" and j.get("merged_server") == SERVER   # transcript safe

    # gm frees up -> a re-drive (simulating the sweeper) now lands it
    state["busy"] = False
    m._attempt_gm_inject(scratch / f"{CLIENT}.json")
    j2 = json.loads((scratch / f"{CLIENT}.json").read_text())
    assert j2.get("gm_injected") is True
    assert "gm_inject_next_ts" not in j2


def test_renotify_reinjects_when_not_yet_injected(scratch, monkeypatch):
    # DELIB-BUG-1: a re-notify of a merged-but-not-injected journal MUST re-attempt (old code
    # short-circuited on the optimistic flag and no-op'd).
    m = _load_proxy(scratch)
    m._GM_INJECT_ENABLED = True
    m._INJECT_ASYNC = False
    state = {"busy": True}
    endcall = __import__("services.arturo.endcall", fromlist=["inject_to_gm"])
    monkeypatch.setattr(endcall, "inject_to_gm",
                        lambda *a, **k: ((not state["busy"]), 1))
    m.finalize_from_client(client_call_id=CLIENT)          # busy -> not injected
    assert json.loads((scratch / f"{CLIENT}.json").read_text()).get("gm_injected") is False
    state["busy"] = False
    m.finalize_from_client(client_call_id=CLIENT)          # re-notify, gm idle -> injects
    assert json.loads((scratch / f"{CLIENT}.json").read_text()).get("gm_injected") is True


def test_trivial_stub_sibling_suppressed_no_inject(scratch, monkeypatch):
    # DELIB-BUG-3: a 2-turn stub abutting the rich 44-turn call is suppressed (never injects).
    m = _load_proxy(scratch)
    m._GM_INJECT_ENABLED = True
    m._INJECT_ASYNC = False
    injected = []
    endcall = __import__("services.arturo.endcall", fromlist=["inject_to_gm"])
    monkeypatch.setattr(endcall, "inject_to_gm", lambda *a, **k: injected.append(1) or (True, 1))
    # seed a trivial stub client journal abutting the rich CLIENT fixture
    rich = json.loads((scratch / f"{CLIENT}.json").read_text())
    start = rich["started_at"]
    stub = {"call_id": "vc_client_stub00000000", "source": "client", "status": "ended",
            "started_at": start - 10, "ended_at": start - 2,
            "turns": [{"role": "user", "text": "hey", "ts": start - 9},
                      {"role": "arturo", "text": "hi", "ts": start - 8}],
            "conv_id": "conv_stub"}
    (scratch / "vc_client_stub00000000.json").write_text(json.dumps(stub))
    m.finalize_from_client(client_call_id="vc_client_stub00000000")
    sj = json.loads((scratch / "vc_client_stub00000000.json").read_text())
    assert sj.get("gm_inject_suppressed") is True
    assert injected == []                                  # the stub never injected
