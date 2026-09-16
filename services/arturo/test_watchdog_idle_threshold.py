# test_watchdog_idle_threshold.py — regression for the premature-finalize double-inject (2026-09-06).
#
# BUG (live-caught on the operator's build-161 call): the finalize-watchdog is a FALLBACK for the app-crash
# case (client /finalize-call never arrives). It keyed on a 120s journal-mtime idle. A real
# ~2m36s conversational PAUSE (the operator thinking, call still open on WebRTC) produced no new turns ->
# stale mtime -> the watchdog wrongly finalized+injected the LIVE call (28 turns @00:57), then the
# real client /finalize-call re-injected the full 49-turn call @01:03 = a DOUBLE 'Voice call ended'
# to gm. Fix: the idle threshold must comfortably exceed any conversational pause (the client's real
# finalize fires within seconds of hangup, so the watchdog only needs to catch true abandonment).
import json
import os
import time
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_proxy(scratch):
    os.environ["ARTURO_VOICE_CALLS_DIR"] = str(scratch)
    spec = importlib.util.spec_from_file_location("arturo_proxy_idletest",
                                                  str(REPO / "services" / "arturo" / "arturo-proxy.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _seed_live_call(scratch, call_id, idle_secs_ago):
    p = scratch / f"{call_id}.json"
    p.write_text(json.dumps({
        "call_id": call_id, "status": "live", "origin": "funnel",
        "turns": [{"role": "user", "text": "hi"}, {"role": "arturo", "text": "hey"}],
    }))
    t = time.time() - idle_secs_ago
    os.utime(p, (t, t))
    return p


def test_watchdog_does_not_finalize_a_live_paused_call(tmp_path, monkeypatch):
    scratch = tmp_path / "voice-calls"
    scratch.mkdir()
    m = _load_proxy(scratch)

    # a call whose last turn was 200s ago — a conversational PAUSE, NOT a drop.
    _seed_live_call(scratch, "vc_pausetest0001", idle_secs_ago=200)

    finalized = []
    monkeypatch.setattr(m, "_finalize_journal_file", lambda pp: finalized.append(Path(pp).stem))

    # The shipped default threshold must be well above any plausible pause.
    assert m.FINALIZE_IDLE_SECS >= 600, f"idle threshold too aggressive: {m.FINALIZE_IDLE_SECS}"

    # Under the shipped default, a 200s-idle LIVE call must NOT be finalized (the bug).
    m._finalize_stale_calls(now=time.time(), idle_secs=m.FINALIZE_IDLE_SECS)
    assert finalized == [], f"watchdog prematurely finalized a live paused call: {finalized}"

    # Control: under the OLD 120s threshold the same call WOULD finalize — proves the threshold
    # is the governing knob and the fix is the raised default.
    m._finalize_stale_calls(now=time.time(), idle_secs=120)
    assert finalized == ["vc_pausetest0001"], "control: 200s idle should finalize under 120s"


def test_watchdog_still_finalizes_genuinely_abandoned_call(tmp_path, monkeypatch):
    scratch = tmp_path / "voice-calls"
    scratch.mkdir()
    m = _load_proxy(scratch)
    # idle well past the new threshold = genuine app-crash abandonment -> still swept.
    _seed_live_call(scratch, "vc_abandoned0001", idle_secs_ago=m.FINALIZE_IDLE_SECS + 60)
    finalized = []
    monkeypatch.setattr(m, "_finalize_journal_file", lambda pp: finalized.append(Path(pp).stem))
    m._finalize_stale_calls(now=time.time(), idle_secs=m.FINALIZE_IDLE_SECS)
    assert finalized == ["vc_abandoned0001"], "watchdog must still sweep genuinely abandoned calls"


def test_idle_threshold_env_overridable(tmp_path):
    scratch = tmp_path / "voice-calls"
    scratch.mkdir()
    os.environ["ARTURO_FINALIZE_IDLE_SECS"] = "1234"
    try:
        m = _load_proxy(scratch)
        assert m.FINALIZE_IDLE_SECS == 1234
    finally:
        del os.environ["ARTURO_FINALIZE_IDLE_SECS"]
