# RED-first tests for the PTT orchestration in arturo-proxy.ptt_turn — the lifecycle-free stateless
# turn: STT -> Arturo brain -> TTS, with turn_id dedup and honest error codes.
#
# ★ The gm design-gate condition (msg_dc8d4197 / msg_05158a29): a PTT turn must NOT trigger the EL
# voice-CALL lifecycle — NO CallJournal, NO _log_voice_turn / capture_mid_call_turns, NO end-of-call
# finalize/gm-inject. test_ptt_turn_emits_no_call_lifecycle proves it by spying those seams and
# asserting zero calls while running the REAL brain path (build_context + brain stubbed, not the
# journaling).

import base64
import importlib.util
import pathlib

import pytest


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Msg:
    def __init__(self, content): self.message = type("M", (), {"content": content})


class _Resp:
    def __init__(self, content): self.choices = [_Msg(content)]


def _stub_vendors(mod, monkeypatch, *, stt="hello arturo", reply="hi shaw", tts=b"MP3DATA"):
    """Stub the three vendor seams so no network is touched; brain uses the REAL _ptt_brain but with
    build_context + brain stubbed."""
    monkeypatch.setattr(mod, "_ptt_stt", lambda audio, fname: stt)
    monkeypatch.setattr(mod, "_ptt_tts", lambda text: tts)
    monkeypatch.setattr(mod, "build_context", lambda calling_channel="ptt": "SYS-CONTEXT")
    monkeypatch.setattr(mod.brain, "complete", lambda **kw: _Resp(reply))
    # fresh per-test history/cache so tests don't bleed
    monkeypatch.setattr(mod, "_PTT_HISTORY", mod._ptt.PttHistory())
    monkeypatch.setattr(mod, "_PTT_TURN_CACHE", mod._ptt.TurnCache())


GOOD_AUDIO = b"\x00\x01" * 500          # 1000 bytes, within cap


def test_ptt_turn_happy_path_returns_reply_stt_audio(monkeypatch):
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch)
    code, res = mod.ptt_turn(GOOD_AUDIO, "u.m4a", "conv-1", "turn-1", content_type="audio/m4a")
    assert code == 200
    assert res["reply_text"] == "hi shaw"
    assert res["stt_text"] == "hello arturo"
    assert base64.b64decode(res["audio"]) == b"MP3DATA"


def test_ptt_turn_oversize_413(monkeypatch):
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch)
    big = b"x" * (mod._ptt.MAX_AUDIO_BYTES + 1)
    code, res = mod.ptt_turn(big, "u.m4a", "c", "t", content_type="audio/m4a")
    assert code == 413 and res["error"] == "too_large"


def test_ptt_turn_empty_400(monkeypatch):
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch)
    code, res = mod.ptt_turn(b"", "u.m4a", "c", "t", content_type="audio/m4a")
    assert code == 400 and res["error"] == "empty"


def test_ptt_turn_bad_type_400(monkeypatch):
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch)
    code, res = mod.ptt_turn(GOOD_AUDIO, "u.mov", "c", "t", content_type="video/quicktime")
    assert code == 400 and res["error"] == "bad_type"


def test_ptt_turn_no_speech_422(monkeypatch):
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch, stt="")           # STT heard nothing
    code, res = mod.ptt_turn(GOOD_AUDIO, "u.m4a", "c", "t", content_type="audio/m4a")
    assert code == 422 and res["error"] == "no_speech"


def test_ptt_turn_stt_failure_502(monkeypatch):
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch)
    def boom(audio, fname): raise RuntimeError("stt down")
    monkeypatch.setattr(mod, "_ptt_stt", boom)
    code, res = mod.ptt_turn(GOOD_AUDIO, "u.m4a", "c", "t", content_type="audio/m4a")
    assert code == 502 and res["error"] == "stt_failed"


def test_ptt_turn_tts_failure_502(monkeypatch):
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch)
    def boom(text): raise RuntimeError("tts down")
    monkeypatch.setattr(mod, "_ptt_tts", boom)
    code, res = mod.ptt_turn(GOOD_AUDIO, "u.m4a", "c", "t", content_type="audio/m4a")
    assert code == 502 and res["error"] == "tts_failed"


def test_ptt_turn_dedup_same_turn_id_runs_brain_once(monkeypatch):
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch)
    calls = {"n": 0}
    real_brain = mod._ptt_brain
    def counting_brain(stt_text, conversation_id):
        calls["n"] += 1
        return real_brain(stt_text, conversation_id)
    monkeypatch.setattr(mod, "_ptt_brain", counting_brain)
    a = mod.ptt_turn(GOOD_AUDIO, "u.m4a", "c", "same-turn", content_type="audio/m4a")
    b = mod.ptt_turn(GOOD_AUDIO, "u.m4a", "c", "same-turn", content_type="audio/m4a")
    assert a[0] == 200 and b[0] == 200
    assert a[1] == b[1]                       # identical cached turn
    assert calls["n"] == 1, "brain must run once for a repeated turn_id (dedup)"


def test_ptt_turn_concurrent_same_turn_id_runs_brain_once(monkeypatch):
    # I1: two overlapping tunnel retries with the SAME turn_id must not both run the pipeline. With
    # Flask threaded=True the post-completion cache write races; single-flight must make the 2nd wait
    # for the 1st and return the cached turn (no double STT/brain charge).
    import threading
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch)
    calls = {"n": 0}
    entered = threading.Event()
    release = threading.Event()
    real_brain = mod._ptt_brain
    lock = threading.Lock()

    def slow_brain(stt_text, conversation_id):
        with lock:
            calls["n"] += 1
        entered.set()            # the leader is now inside the pipeline (in-flight registered)
        release.wait(3)          # hold until the 2nd request has arrived
        return real_brain(stt_text, conversation_id)

    monkeypatch.setattr(mod, "_ptt_brain", slow_brain)
    results = {}

    def run(i):
        results[i] = mod.ptt_turn(GOOD_AUDIO, "u.m4a", "c", "same-turn", content_type="audio/m4a")

    t1 = threading.Thread(target=run, args=(1,))
    t2 = threading.Thread(target=run, args=(2,))
    t1.start()
    assert entered.wait(3), "leader never entered the pipeline"
    t2.start()               # arrives while the leader is in-flight
    import time as _t
    _t.sleep(0.25)           # give t2 time to reach the dedup wait
    release.set()            # let the leader finish
    t1.join(5)
    t2.join(5)
    assert calls["n"] == 1, "brain must run ONCE for concurrent same-turn_id requests"
    assert results[1] == results[2]     # both callers get the identical turn


def test_ptt_turn_emits_no_call_lifecycle(monkeypatch):
    # ★ gm condition: a PTT turn is a stateless HTTP turn, NOT an EL call — it must touch NONE of the
    # call-lifecycle seams. Run the REAL _ptt_brain (build_context + brain stubbed) and spy them.
    import services.arturo.ended_once as _eo_mod
    import services.arturo.endcall as _ec_mod
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch)
    hits = {"journal": 0, "log_turn": 0, "capture": 0, "finalize": 0, "ended_once": 0, "gm_inject": 0}
    monkeypatch.setattr(mod, "_CallJournal", lambda *a, **k: hits.__setitem__("journal", hits["journal"] + 1))
    monkeypatch.setattr(mod, "_log_voice_turn", lambda *a, **k: hits.__setitem__("log_turn", hits["log_turn"] + 1))
    monkeypatch.setattr(mod, "capture_mid_call_turns", lambda *a, **k: hits.__setitem__("capture", hits["capture"] + 1))
    monkeypatch.setattr(mod, "_finalize_journal_file", lambda *a, **k: hits.__setitem__("finalize", hits["finalize"] + 1))
    # SPEC §10.5 also enumerates the ended_once ledger + the end-of-call gm-injection seam:
    monkeypatch.setattr(_eo_mod, "claim", lambda *a, **k: hits.__setitem__("ended_once", hits["ended_once"] + 1) or True)
    monkeypatch.setattr(_ec_mod, "_default_post", lambda *a, **k: hits.__setitem__("gm_inject", hits["gm_inject"] + 1) or 200)
    code, res = mod.ptt_turn(GOOD_AUDIO, "u.m4a", "conv-x", "turn-x", content_type="audio/m4a")
    assert code == 200 and res["reply_text"] == "hi shaw"     # the brain DID reply
    assert hits == {"journal": 0, "log_turn": 0, "capture": 0, "finalize": 0, "ended_once": 0, "gm_inject": 0}, \
        f"PTT must not touch the call lifecycle, got {hits}"


def test_ptt_turn_threads_history_across_turns(monkeypatch):
    mod = _load_proxy()
    _stub_vendors(mod, monkeypatch)
    seen = {}
    def capture_msgs(**kw):
        seen["messages"] = kw.get("messages")
        return _Resp("ok")
    monkeypatch.setattr(mod.brain, "complete", capture_msgs)
    mod.ptt_turn(GOOD_AUDIO, "u.m4a", "conv-h", "t1", content_type="audio/m4a")
    mod.ptt_turn(GOOD_AUDIO, "u.m4a", "conv-h", "t2", content_type="audio/m4a")
    # 2nd turn's messages include the 1st turn's user+assistant as prior history
    contents = [m["content"] for m in seen["messages"]]
    assert "hello arturo" in contents and "ok" in contents
