"""The downlink FIR must never sit on the relay's CONNECT path (R2 follow-up).

R2 (#375) built a hume_audio.Downsampler in every relay holder's constructor, and the constructor
called _have_scipy(): importing scipy.signal costs ~1 s on a cold process. So the first call after the
proxy started waited ~1 s before its socket opened (test_socket_stormbreak's cooldown test flaked on
exactly this: 1 connect instead of >= 2 in its 1.2 s window). Now the FIR/box decision is made on the
first audio chunk, and RelayManager warms scipy + the filter in the background at startup.
"""
import os
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _fresh(code):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(ROOT)
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return r.stdout.strip()


def test_constructing_a_downsampler_does_not_import_scipy():
    out = _fresh("import sys; from services.arturo import hume_audio as ha; ha.Downsampler();"
                 "print('scipy.signal' in sys.modules)")
    assert out == "False", "a Downsampler built on the connect path must not pay the scipy import"


def test_relay_manager_warms_the_filter_in_the_background(monkeypatch):
    from services.arturo import hume_audio, stream_relay as sr
    calls = []
    monkeypatch.setattr(hume_audio, "warm_async", lambda: calls.append(1))
    m = sr.RelayManager(socket_factory=lambda cid: None)
    try:
        assert calls == [1]
    finally:
        m.shutdown()


def test_after_warming_the_first_fir_chunk_does_not_import_anything():
    # warm() is what the background thread runs: afterwards the first 48 kHz chunk is pure numpy work
    out = _fresh(
        "import importlib.util as u, sys, time\n"
        "from services.arturo import hume_audio as ha\n"
        "if u.find_spec('scipy') is None:\n"
        "    print('skip'); raise SystemExit\n"
        "ha.warm()\n"
        "import numpy as np, struct\n"
        "pcm = (np.sin(np.arange(4800) / 10) * 8000).astype('<i2').tobytes()\n"
        "fmt = struct.pack('<IHHIIHH', 16, 1, 1, 48000, 96000, 2, 16)\n"
        "body = b'fmt ' + fmt + b'data' + struct.pack('<I', len(pcm)) + pcm\n"
        "wav = b'RIFF' + struct.pack('<I', 4 + len(body)) + b'WAVE' + body\n"
        "t = time.perf_counter(); ha.Downsampler().feed(wav); print(time.perf_counter() - t < 0.2)\n")
    assert out in ("True", "skip"), out
