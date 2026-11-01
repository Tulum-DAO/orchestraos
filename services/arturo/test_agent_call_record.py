"""A3: a call answered by an agent's own session is recorded as that agent's call. Its call file carries a top-level
"agent" (the API's contract: it lists it in that agent's chat; no "agent" = Arturo's own call), its turns are
journaled, nothing about it is sent to gm at the end, and it is never archived into an Arturo thread. Temp dirs;
the gm post is a stand-in."""
import importlib.util
import json
import pathlib
import time

import services.arturo.endcall as ec


def _load_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "calls"))
    spec = importlib.util.spec_from_file_location("arturo_proxy_acr", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.VOICE_CALLS_DIR = tmp_path / "calls"
    mod.VOICE_CALLS_DIR.mkdir(parents=True, exist_ok=True)
    return mod


def _only_call(mod):
    files = sorted(mod.VOICE_CALLS_DIR.glob("vc_*.json"))
    assert len(files) == 1, files
    return files[0], json.loads(files[0].read_text())


def test_an_agent_calls_turns_land_in_a_call_file_marked_with_the_agent(tmp_path, monkeypatch):
    mod = _load_proxy(tmp_path, monkeypatch)
    mod._openai_journal("cid-a", "user", "what changed?", agent="dev-demo")
    mod._openai_journal("cid-a", "assistant", "Two files.", agent="dev-demo")
    path, d = _only_call(mod)
    assert d["agent"] == "dev-demo" and d["conv_id"] == "cid-a"
    said = [(t.get("role"), t.get("text")) for t in d["turns"]]
    assert ("user", "what changed?") in said and ("assistant", "Two files.") in said
    assert all("agent" not in t for t in d["turns"])                  # whose call: once, at the top level
    assert isinstance(d.get("started_at"), (int, float))


def test_arturos_own_call_has_no_agent(tmp_path, monkeypatch):
    mod = _load_proxy(tmp_path, monkeypatch)
    mod._openai_journal("cid-b", "user", "hello")
    _, d = _only_call(mod)
    assert "agent" not in d


def test_nothing_is_sent_to_gm_when_an_agent_call_ends(tmp_path, monkeypatch):
    mod = _load_proxy(tmp_path, monkeypatch)
    mod._GM_INJECT_ENABLED = True
    hits = []
    monkeypatch.setattr(ec, "_default_post", lambda s, t: hits.append(s) or 200)
    for agent in ("dev-demo", None):
        j = mod._CallJournal(dir=mod.VOICE_CALLS_DIR, page="voice")
        j.add_turn("user", "hi")
        j.add_turn("arturo", "hello")
        d = json.loads(j.path.read_text())
        d["origin"] = "relay"
        if agent:
            d["agent"] = agent
        from services.arturo.call_journal import _atomic_write
        _atomic_write(j.path, d)
        mod._finalize_journal_file(j.path)
        time.sleep(0.3)
        assert json.loads(j.path.read_text())["status"] == "ended"
        assert len(hits) == (0 if agent else 1)


def test_an_agent_call_is_never_bound_to_an_arturo_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy(tmp_path, monkeypatch)
    assert mod._STREAM_RELAY is not None and mod._CALL_THREADS is not None
    monkeypatch.setattr(mod._STREAM_RELAY, "shutdown", mod._STREAM_RELAY.shutdown)
    bound = []
    monkeypatch.setattr(mod._CALL_THREADS, "bind", lambda cid, t, principal=None: bound.append(cid))
    monkeypatch.setattr(mod._CALL_THREADS, "thread", lambda cid: None)
    monkeypatch.setattr(mod, "_thread_refusal", lambda t, c: None)
    monkeypatch.setattr(mod, "_stamped_principal", lambda req: "fleet")
    monkeypatch.setattr(mod, "_bind_call", lambda cid, caller, claim=True: (True, False))
    monkeypatch.setattr(mod._STREAM_RELAY, "feed_audio", lambda *a, **k: {"ok": True})
    modes = {"c-agent": True, "c-own": False}
    monkeypatch.setattr(mod._STREAM_RELAY, "agent_mode", lambda cid: modes[cid])
    c = mod.app.test_client()
    for cid in modes:
        r = c.post("/ptt/stream/audio", data=b"\x00\x01" * 50, environ_base={"REMOTE_ADDR": "127.0.0.1"},
                   headers={"X-Conversation-Id": cid, "X-Arturo-Thread": "th-1"},
                   content_type="application/octet-stream")
        assert r.status_code == 200, r.get_json()
    assert bound == ["c-own"]
    mod._STREAM_RELAY.shutdown()


def test_the_browsers_merged_record_keeps_the_agent_and_tells_gm_nothing(tmp_path, monkeypatch):
    """At hang-up the browser's own journal supersedes the server's: it takes over "agent", and gm gets nothing."""
    import hashlib
    mod = _load_proxy(tmp_path, monkeypatch)
    mod._GM_INJECT_ENABLED = True
    mod._INJECT_ASYNC = False
    monkeypatch.setattr(mod, "_settle_for_finalize", lambda **k: None)
    sent = []
    monkeypatch.setattr(ec, "inject_to_gm", lambda *a, **k: sent.append(a) or (True, 1))
    conv = "cid-merge"
    now = time.time()
    mod._openai_journal(conv, "user", "what changed?", agent="dev-demo")
    mod._openai_journal(conv, "assistant", "Two files.", agent="dev-demo")
    client_id = "vc_client_" + hashlib.sha1(conv.encode()).hexdigest()[:12]
    (mod.VOICE_CALLS_DIR / f"{client_id}.json").write_text(json.dumps({
        "call_id": client_id, "source": "client", "conv_id": conv, "status": "ended", "started_at": now - 30,
        "ended_at": now, "turns": [{"role": "user", "text": "what changed?", "ts": now - 20},
                                   {"role": "arturo", "text": "Two files.", "ts": now - 18}]}))
    assert mod.finalize_from_client(client_call_id=client_id, conv_id=conv) is True
    client = json.loads((mod.VOICE_CALLS_DIR / f"{client_id}.json").read_text())
    assert client.get("merged_server") and client["agent"] == "dev-demo"
    assert client["gm_inject_suppressed"] is True and sent == []
    assert mod._attempt_gm_inject(mod.VOICE_CALLS_DIR / f"{client_id}.json") is False
