import math, struct
import numpy as np
from services.arturo import hume_audio as ha


def _wav(rate, channels, seconds=0.06, freq=440.0, extra_chunk=False):
    n = int(rate * seconds)
    t = np.arange(n) / rate
    mono = (np.sin(2 * math.pi * freq * t) * 12000).astype("<i2")
    frames = np.repeat(mono[:, None], channels, axis=1).reshape(-1).tobytes()
    fmt = struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * 2, channels * 2, 16)
    body = b"fmt " + fmt
    if extra_chunk:                       # a LIST chunk before data: header is NOT 44 bytes
        body += b"LIST" + struct.pack("<I", 4) + b"INFO"
    body += b"data" + struct.pack("<I", len(frames)) + frames
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def test_48k_mono_decimates_by_3():
    out = ha.wav_to_pcm16k(_wav(48000, 1))
    assert len(out) == int(48000 * 0.06) // 3 * 2
    assert np.abs(np.frombuffer(out, "<i2")).max() > 8000       # signal survived


def test_stereo_downmixed():
    out = ha.wav_to_pcm16k(_wav(48000, 2))
    assert len(out) == int(48000 * 0.06) // 3 * 2


def test_header_not_assumed_44_bytes():
    out = ha.wav_to_pcm16k(_wav(48000, 1, extra_chunk=True))
    assert len(out) == int(48000 * 0.06) // 3 * 2


def test_16k_passthrough():
    out = ha.wav_to_pcm16k(_wav(16000, 1))
    assert len(out) == int(16000 * 0.06) * 2


def test_garbage_returns_empty():
    assert ha.wav_to_pcm16k(b"not a wav") == b""


# ---- DOWNLINK FIR (gm msg_77458978): the 3-tap box let 10 kHz fold to 6 kHz at only -5.9 dB
# (sibilants -> harsh/metallic on every Hume call). A stateful polyphase FIR across Hume chunks.
import time as _time


def _tone48(freq, seconds=1.0, amp=12000.0):
    t = np.arange(int(48000 * seconds)) / 48000
    return (np.sin(2 * math.pi * freq * t) * amp).astype("<i2")


def _wav_from(samples, rate=48000):
    frames = samples.astype("<i2").tobytes()
    fmt = struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    body = b"fmt " + fmt + b"data" + struct.pack("<I", len(frames)) + frames
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def _level_db(pcm16k, freq, amp=12000.0):
    y = np.frombuffer(pcm16k, "<i2").astype(np.float64)[400:]          # skip the filter warm-up
    w = np.hanning(len(y))
    spec = np.abs(np.fft.rfft(y * w)) / (w.sum() / 2)
    fr = np.fft.rfftfreq(len(y), 1 / 16000)
    return 20 * np.log10(spec[np.argmin(np.abs(fr - freq))] / amp + 1e-12)


def test_fir_rejects_aliases_at_least_50db():
    for f in (10000, 12000, 14000):
        out = ha.Downsampler().feed(_wav_from(_tone48(f)))
        assert _level_db(out, 16000 - f) <= -50.0, f"{f} Hz folds to {16000 - f} Hz too loud"


def test_fir_passband_flat_within_half_db():
    for f in (1000, 4000, 7000):
        out = ha.Downsampler().feed(_wav_from(_tone48(f)))
        assert abs(_level_db(out, f)) <= 0.5, f"{f} Hz passband off by more than 0.5 dB"


def test_chunked_equals_whole_no_edge_clicks():
    rng = np.random.default_rng(7)
    x = (rng.standard_normal(48000) * 4000).astype("<i2")
    whole = np.frombuffer(ha.Downsampler().feed(_wav_from(x)), "<i2").astype(int)
    d, parts, i = ha.Downsampler(), [], 0
    for n in (4801, 7, 12000, 999, 3, 20000):                 # odd sizes: phase must carry
        parts.append(d.feed(_wav_from(x[i:i + n])))
        i += n
    parts.append(d.feed(_wav_from(x[i:])))
    chunked = np.frombuffer(b"".join(parts), "<i2").astype(int)
    assert len(chunked) == len(whole) == 16000
    assert np.abs(chunked - whole).max() <= 1


def test_fir_cpu_per_second_of_audio():
    wav = _wav_from(_tone48(1000))
    d = ha.Downsampler()
    d.feed(wav)                                               # warm
    t0 = _time.perf_counter()
    for _ in range(5):
        d.feed(wav)
    per_s = (_time.perf_counter() - t0) / 5
    assert per_s < 0.05, f"{per_s * 1000:.1f} ms CPU per second of audio (> 5% of a core)"


def test_stateless_helper_uses_the_fir_too():
    assert _level_db(ha.wav_to_pcm16k(_wav_from(_tone48(10000))), 6000) <= -50.0


def test_flag_off_restores_the_box(monkeypatch):
    monkeypatch.setenv("ARTURO_DOWNLINK_FIR", "0")
    out = ha.Downsampler().feed(_wav_from(_tone48(10000)))
    assert _level_db(out, 6000) > -10.0                       # the old box: about -5.9 dB


def test_relay_call_site_filters_a_reply_split_across_chunks_as_one_stream():
    """Call site: the relay must keep ONE Downsampler per call. A per-chunk stateless filter
    restarts at every audio_output frame and clicks at each edge."""
    import base64
    from services.arturo.test_stream_relay_hume import _manager, _wait
    rng = np.random.default_rng(11)
    x = (rng.standard_normal(14401) * 4000).astype("<i2")
    cuts = [0, 4801, 9599, 14401]
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[0]
        for k in range(3):
            s.push({"type": "audio_output", "id": "a", "index": k,
                    "data": base64.b64encode(_wav_from(x[cuts[k]:cuts[k + 1]])).decode()})
        want = np.frombuffer(ha.Downsampler().feed(_wav_from(x)), "<i2").astype(int)

        def got():
            ev = [e for e in m.events("h1", 0)[0] if e["type"] == "audio"]
            return np.frombuffer(b"".join(base64.b64decode(e["audio"]) for e in ev), "<i2").astype(int)
        assert _wait(lambda: len(got()) >= len(want))
        assert np.abs(got()[:len(want)] - want).max() <= 1
    finally:
        m.shutdown()
