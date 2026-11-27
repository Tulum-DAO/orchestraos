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


def test_a_native_call_counts_the_tool_runs_in_its_ledger(monkeypatch, tmp_path):
    # Shaw's 18:3x call: three gm_command runs on a native call, summary "0 tool runs" (no server journal to fold)
    from services.arturo.native.tool_ledger import ToolLedger
    scratch = tmp_path / "calls"
    scratch.mkdir()
    m = _load_proxy(scratch)
    m._GM_INJECT_ENABLED = False
    led = ToolLedger(tmp_path / "native")
    for i in range(3):
        led.run_once_external(f"c{i}", "native-q1", "fleet", "gm_command", {"prompt": f"q{i}"},
                              lambda: ({"ok": True, "result": "sent"}, None))
    monkeypatch.setattr(m, "NATIVE_TOOL_LEDGER", tmp_path / "native" / "tool_ledger.sqlite3")
    now = __import__("time").time()
    (scratch / "vc_client_nq1.json").write_text(json.dumps({
        "call_id": "vc_client_nq1", "source": "client", "status": "ended", "conv_id": "native-q1",
        "started_at": now - 60, "ended_at": now,
        "turns": [{"role": "user", "text": "ask the gm", "ts": now - 50},
                  {"role": "arturo", "text": "Sent.", "ts": now - 40}]}))
    # twice: with the gm inject off (or failing) gm_injected stays False, so a re-notify merges again (codex review)
    for _ in range(2):
        assert m.finalize_from_client(client_call_id="vc_client_nq1", conv_id="native-q1") is True
        client = json.loads((scratch / "vc_client_nq1.json").read_text())
        assert [t["tool"] for t in client["turns"] if t.get("role") == "tool"] == ["gm_command"] * 3
        assert client["summary"].startswith("Voice call: 2 turns, 3 tool runs.")


# --- item 10: a native (dashboard GPT-Live) call's card lands in the Arturo thread it named ---------------------------
# Synthetic journals (no live fixtures), a scratch thread store, gm inject OFF (so a re-driven finalize really re-runs
# the merge path), thread summaries OFF (no background brain call).

NATIVE = "vc_client_00000000a10a"


def _native_proxy(tmp_path, monkeypatch):
    calls = tmp_path / "calls"
    calls.mkdir()
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(calls))
    monkeypatch.setenv("ARTURO_THREAD_SUMMARY", "0")
    spec = importlib.util.spec_from_file_location("arturo_proxy_native_card",
                                                  str(REPO / "services" / "arturo" / "arturo-proxy.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    from services.arturo.thread_store import ThreadStore
    m.VOICE_CALLS_DIR = calls
    m._THREADS = ThreadStore(tmp_path / "threads.db")
    m._GM_INJECT_ENABLED = False
    m._settle_for_finalize = lambda **k: None
    return m


def _native_journal(m, thread_id="web_abc123", turns=None, principal="fleet"):
    now = 1_800_000_000.0
    j = {"call_id": NATIVE, "started_at": now, "ended_at": now + 30, "page": "client", "status": "ended",
         "source": "client", "conv_id": "native-abc", "summary": "Client transcript.",
         "turns": turns if turns is not None else [
             {"role": "user", "text": "what is up", "ts": now + 1},
             {"role": "arturo", "text": "Two approvals.", "ts": now + 3}]}
    if thread_id is not None:
        j["thread_id"] = thread_id
        j["thread_principal"] = principal        # the gateway stamps who posted it (the dashboard = fleet)
    (m.VOICE_CALLS_DIR / f"{NATIVE}.json").write_text(json.dumps(j))


def _cards(m, thread_id):
    th = m._THREADS.get_thread(thread_id) or {"turns": []}
    return [t for t in th["turns"] if (t.get("meta") or {}).get("kind") == "call_end"]


def test_native_call_card_written_once_even_when_finalize_is_re_driven(tmp_path, monkeypatch):
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    _native_journal(m)
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert m.finalize_from_client(client_call_id=NATIVE) is True          # the re-notify
    turns = m._THREADS.get_thread("web_abc123")["turns"]
    cards = _cards(m, "web_abc123")
    assert len(cards) == 1
    text, meta = m._call_end_card(NATIVE)                                 # the relay's own card grammar
    assert cards[0]["content"] == text and cards[0]["meta"] == meta == {"kind": "call_end", "call_id": NATIVE}
    assert text.startswith(f"Call ended ({NATIVE}). [voice-call: {NATIVE} ")
    said = [(t["role"], t["content"], t.get("mode")) for t in turns[2:]]
    assert said == [("user", "what is up", "voice"), ("assistant", "Two approvals.", "voice"),
                    ("assistant", text, "voice")]
    assert json.loads((m.VOICE_CALLS_DIR / f"{NATIVE}.json").read_text())["thread_card_written"] is True


def test_a_repeated_call_ended_post_never_writes_the_call_twice(tmp_path, monkeypatch):
    # The gateway rewrites the journal whole on every POST /voice-call-ended for the same conv_id (same call id),
    # so the journal's thread_card_written flag is gone the second time. The thread itself must remember.
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    _native_journal(m)
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    _native_journal(m)                                                    # the second POST: a fresh journal
    assert "thread_card_written" not in json.loads((m.VOICE_CALLS_DIR / f"{NATIVE}.json").read_text())
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert len(_cards(m, "web_abc123")) == 1
    said = [(t["role"], t["content"]) for t in m._THREADS.get_thread("web_abc123")["turns"][2:]]
    assert said.count(("user", "what is up")) == 1 and said.count(("assistant", "Two approvals.")) == 1


def test_the_same_call_id_in_another_thread_is_not_a_repeat(tmp_path, monkeypatch):
    # The once check is per (thread, call id): a card for this call in one thread does not stop its own thread.
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_other", "typed", "answer", principal="fleet")
    m._THREADS.append_message("web_other", "assistant", "Call ended.", mode="voice",
                              meta={"kind": "call_end", "call_id": NATIVE}, principal="fleet")
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    _native_journal(m)
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert len(_cards(m, "web_abc123")) == 1


def test_only_the_callers_own_lines_reach_the_thread_never_a_merged_server_journal(tmp_path, monkeypatch):
    # The merge's time-window fallback can link ANY overlapping server journal, and it recovers a role the client
    # journal lacks entirely. Those lines are someone else's call: they must never be archived into this thread.
    m = _native_proxy(tmp_path, monkeypatch)
    now = 1_800_000_000.0
    other = {"call_id": "vc_server_0000other", "started_at": now - 5, "page": "server", "status": "live",
             "turns": [{"role": "user", "text": "someone else's secret", "ts": now + 2},
                       {"role": "arturo", "text": "their answer", "ts": now + 4},
                       {"role": "tool", "name": "lookup", "text": "their tool", "ts": now + 3}]}
    (m.VOICE_CALLS_DIR / "vc_server_0000other.json").write_text(json.dumps(other))
    _native_journal(m, thread_id="dev1_t", turns=[], principal="device:dev1")    # an empty call: both roles absent
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    th = m._THREADS.get_thread("dev1_t")
    lines = [t["content"] for t in th["turns"]]
    assert not any("someone else" in c or "their answer" in c or "their tool" in c for c in lines), lines
    assert len(_cards(m, "dev1_t")) == 1                                             # the empty call's own card


def test_no_thread_id_no_card_and_journal_unchanged_shape(tmp_path, monkeypatch):
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    _native_journal(m, thread_id=None)
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert m._THREADS.get_thread("web_abc123")["turns"].__len__() == 2
    d = json.loads((m.VOICE_CALLS_DIR / f"{NATIVE}.json").read_text())
    assert "thread_card_written" not in d and d["status"] == "ended"


def test_a_thread_store_that_throws_never_fails_the_finalize(tmp_path, monkeypatch):
    m = _native_proxy(tmp_path, monkeypatch)
    reached = []

    class Boom:
        # Answers the once check (not written yet), so the finalize really reaches the writes, which throw.
        usable = True

        def has_meta(self, tid, **match):
            reached.append("has_meta")
            return False

        def started_by(self, tid):
            reached.append("started_by")
            return "fleet"

        def append_message(self, *a, **k):
            reached.append("append_message")
            raise RuntimeError("disk on fire")

        def __getattr__(self, name):
            raise RuntimeError("disk on fire")
    m._THREADS = Boom()
    _native_journal(m)
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert "append_message" in reached                                   # the throwing write was really reached
    d = json.loads((m.VOICE_CALLS_DIR / f"{NATIVE}.json").read_text())
    # Nothing landed (every write threw), so nothing would repeat: the card is still owed, for the next finalize.
    assert d["status"] == "ended" and not d.get("thread_card_written") and "own_turns" in d

    # and a store that throws on the ownership check: a device caller, so _thread_refusal asks started_by
    reached.clear()

    class Boom2(Boom):
        def started_by(self, tid):
            reached.append("started_by")
            raise RuntimeError("locked")
    m._THREADS = Boom2()
    _native_journal(m, principal="device:dev1")
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert reached[:1] == ["started_by"]                                 # the throwing check was really reached

    # and a store that cannot answer the once check: nothing is written (never post blind), finalize goes on
    reached.clear()

    class Boom3(Boom):
        def has_meta(self, tid, **match):
            reached.append("has_meta")
            raise RuntimeError("unreadable")
    m._THREADS = Boom3()
    _native_journal(m)
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert reached == ["has_meta"]                                       # no append after an unanswerable check


class _Unreadable:
    """A thread store that cannot answer the once check (a locked db): the card is not written, and nothing else."""
    usable = True

    def has_meta(self, tid, **match):
        raise RuntimeError("database is locked")

    def __getattr__(self, name):
        raise RuntimeError("database is locked")


def test_a_failed_first_card_is_written_on_the_next_finalize(tmp_path, monkeypatch):
    # The flag is set only once the card is in the thread: a first archive that failed is not marked written,
    # so the call's next finalize writes it (once).
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    store = m._THREADS
    _native_journal(m)
    m._THREADS = _Unreadable()
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    d = json.loads((m.VOICE_CALLS_DIR / f"{NATIVE}.json").read_text())
    assert not d.get("thread_card_written")
    m._THREADS = store
    assert m.finalize_from_client(client_call_id=NATIVE) is True          # the re-notify
    assert m.finalize_from_client(client_call_id=NATIVE) is True          # and another: still once
    assert len(_cards(m, "web_abc123")) == 1
    said = [(t["role"], t["content"]) for t in m._THREADS.get_thread("web_abc123")["turns"][2:]]
    assert said[:2] == [("user", "what is up"), ("assistant", "Two approvals.")]
    d = json.loads((m.VOICE_CALLS_DIR / f"{NATIVE}.json").read_text())
    assert d["thread_card_written"] is True and "own_turns" not in d     # the kept lines go once the card is in


def test_a_store_that_takes_no_write_without_raising_leaves_the_card_owed(tmp_path, monkeypatch):
    # append_message answers None when it cannot write (a locked db): the archive raises nothing, yet no card is in.
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    store = m._THREADS
    _native_journal(m)
    monkeypatch.setattr(store, "append_message", lambda *a, **k: None)
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert not json.loads((m.VOICE_CALLS_DIR / f"{NATIVE}.json").read_text()).get("thread_card_written")
    monkeypatch.undo()
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert len(_cards(m, "web_abc123")) == 1
    said = [(t["role"], t["content"]) for t in m._THREADS.get_thread("web_abc123")["turns"][2:]]
    assert said[:2] == [("user", "what is up"), ("assistant", "Two approvals.")] and len(said) == 3


def test_lines_in_but_no_card_is_not_retried_into_a_double(tmp_path, monkeypatch):
    # Some lines landed and the card did not: a retry would write those lines again, so the call is marked done.
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    store = m._THREADS
    real = store.append_message
    monkeypatch.setattr(store, "append_message",
                        lambda tid, role, text, **k: None if (k.get("meta") or {}).get("kind") == "call_end"
                        else real(tid, role, text, **k))
    _native_journal(m)
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    monkeypatch.undo()
    assert json.loads((m.VOICE_CALLS_DIR / f"{NATIVE}.json").read_text())["thread_card_written"] is True
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    said = [(t["role"], t["content"]) for t in m._THREADS.get_thread("web_abc123")["turns"][2:]]
    assert said == [("user", "what is up"), ("assistant", "Two approvals.")]


def test_a_failed_card_is_retried_even_after_gm_has_the_call(tmp_path, monkeypatch):
    # gm's inject succeeding first makes a re-notify skip the merge; the card still owed is written all the same.
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    store = m._THREADS
    _native_journal(m)
    m._THREADS = _Unreadable()
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    p = m.VOICE_CALLS_DIR / f"{NATIVE}.json"
    d = json.loads(p.read_text())
    d["gm_injected"] = True
    p.write_text(json.dumps(d))
    m._THREADS = store
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert len(_cards(m, "web_abc123")) == 1
    assert json.loads(p.read_text())["thread_card_written"] is True
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert len(_cards(m, "web_abc123")) == 1


def test_a_retried_card_carries_the_callers_own_lines_not_the_merge(tmp_path, monkeypatch):
    # The first finalize replaces the journal's turns with the merge (which can recover another call's lines); the
    # retry must still write only what this caller's journal held.
    m = _native_proxy(tmp_path, monkeypatch)
    now = 1_800_000_000.0
    other = {"call_id": "vc_server_0000other", "started_at": now - 5, "page": "server", "status": "live",
             "turns": [{"role": "user", "text": "someone else's secret", "ts": now + 2},
                       {"role": "arturo", "text": "their answer", "ts": now + 4}]}
    (m.VOICE_CALLS_DIR / "vc_server_0000other.json").write_text(json.dumps(other))
    store = m._THREADS
    _native_journal(m, thread_id="dev1_t", turns=[], principal="device:dev1")
    m._THREADS = _Unreadable()
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    m._THREADS = store
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    lines = [t["content"] for t in m._THREADS.get_thread("dev1_t")["turns"]]
    assert not any("someone else" in c or "their answer" in c for c in lines), lines
    assert len(_cards(m, "dev1_t")) == 1


def test_a_call_from_a_fresh_page_starts_its_thread_under_the_caller_like_the_relay(tmp_path, monkeypatch):
    # a native call from an Arturo page nobody has typed in yet: the relay's bind starts the thread under the
    # caller, so the native card does too (not lost because the thread did not exist yet)
    m = _native_proxy(tmp_path, monkeypatch)
    _native_journal(m, thread_id="web_fresh")
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert m._THREADS.started_by("web_fresh") == "fleet"
    assert len(_cards(m, "web_fresh")) == 1


def test_a_device_cannot_write_its_call_card_into_another_callers_thread(tmp_path, monkeypatch):
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    _native_journal(m, principal="device:dev1")
    assert m.finalize_from_client(client_call_id=NATIVE) is True          # finalize itself still succeeds
    assert len(m._THREADS.get_thread("web_abc123")["turns"]) == 2 and _cards(m, "web_abc123") == []


def test_a_journal_without_a_stamped_caller_writes_nothing_into_an_owned_thread(tmp_path, monkeypatch):
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    _native_journal(m, principal=None)
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    assert _cards(m, "web_abc123") == []


def test_an_empty_native_call_still_gets_its_card_like_the_relay(tmp_path, monkeypatch):
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    _native_journal(m, turns=[])
    assert m.finalize_from_client(client_call_id=NATIVE) is True
    turns = m._THREADS.get_thread("web_abc123")["turns"]
    assert len(turns) == 3 and len(_cards(m, "web_abc123")) == 1


def test_the_relay_end_marker_is_the_same_card(tmp_path, monkeypatch):
    m = _native_proxy(tmp_path, monkeypatch)
    from services.arturo.relay_live_bridge import journal_call_id
    assert m._call_end_marker("conv-xyz") == m._call_end_card(journal_call_id("conv-xyz"))


def test_two_overlapping_finalizes_never_double_a_call_whose_card_failed(tmp_path, monkeypatch):
    # Both finalizes chose the owed card before either wrote it. The first lands its lines but not the card; the
    # second must not write those lines again (the thread holds no card for it to find).
    m = _native_proxy(tmp_path, monkeypatch)
    m._THREADS.record_turn("web_abc123", "typed first", "typed answer", principal="fleet")
    store = m._THREADS
    real = store.append_message
    turns = [{"role": "user", "text": "hi", "ts": 1_800_000_001.0}]
    monkeypatch.setattr(store, "append_message",
                        lambda tid, role, text, **k: None if (k.get("meta") or {}).get("kind") == "call_end"
                        else real(tid, role, text, **k))
    assert m._archive_client_call(NATIVE, "web_abc123", turns, "fleet") is True     # lines in, card not: done
    monkeypatch.undo()
    assert m._archive_client_call(NATIVE, "web_abc123", turns, "fleet") is True     # the overlapping second
    said = [t["content"] for t in m._THREADS.get_thread("web_abc123")["turns"][2:]]
    assert said == ["hi"]
