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
