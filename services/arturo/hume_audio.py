"""Hume audio_output (WAV, 48 kHz per Hume docs — but read the header, never assume) ->
raw s16le 16 kHz mono PCM for the watch downlink (spec §4.2)."""
import os
import struct
from functools import lru_cache

import numpy as np

TARGET = 16000
FIR_FLAG = "ARTURO_DOWNLINK_FIR"     # default on; "0" = the old 3-tap box (flip-back)


@lru_cache(maxsize=1)
def _have_scipy():
    """scipy is OPTIONAL in the public install (a heavy dependency for one filter). Without it the
    downlink uses the old 3-tap box, exactly as with ARTURO_DOWNLINK_FIR=0, and says so once."""
    try:
        import scipy.signal  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - ImportError or a broken install: fall back, never fail a call
        import logging
        logging.getLogger("arturo").warning(
            "downlink FIR off: scipy is not installed (pip install scipy for cleaner Hume audio)")
        return False


def _parse(wav):
    if len(wav) < 12 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        return None
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(wav):
        cid = wav[pos:pos + 4]; size = struct.unpack_from("<I", wav, pos + 4)[0]; body = pos + 8
        if cid == b"fmt " and size >= 16:
            fmt = struct.unpack_from("<HHIIHH", wav, body)   # audio_format, channels, rate, byte_rate, align, bits
        elif cid == b"data":
            data = wav[body:body + size]
        pos = body + size + (size & 1)
    if fmt is None or data is None:
        return None
    return fmt, data


@lru_cache(maxsize=8)
def _fir(factor):
    """Anti-alias low-pass for an integer decimation to 16 kHz (gm msg_77458978). The old 3-tap box
    let 10 kHz fold to 6 kHz at only -5.9 dB, so sibilants came back harsh on every Hume call.
    95-tap Kaiser (beta 7): passband <= 7 kHz within 0.5 dB, >= 50 dB at the 10-14 kHz folds."""
    from scipy.signal import firwin
    return firwin(95, 8500, fs=TARGET * factor, window=("kaiser", 7.0))


class Downsampler:
    """Hume audio_output WAV chunks -> s16le 16 kHz mono, ONE instance per stream. The FIR's state
    and the decimation phase carry across chunks, so a reply split over many audio_output frames
    is filtered exactly as if it were one piece: no clicks at the chunk edges."""

    def __init__(self):
        self._fir_on = os.environ.get(FIR_FLAG, "1") != "0" and _have_scipy()
        self._rate = None
        self._zi = None
        self._n = 0              # input samples consumed at this rate (decimation phase)

    def feed(self, wav):
        parsed = _parse(wav)
        if not parsed:
            return b""
        (afmt, channels, rate, _, _, bits), data = parsed
        if afmt != 1 or bits != 16 or channels < 1 or rate <= 0:
            return b""
        x = np.frombuffer(data[: len(data) - (len(data) % (2 * channels))], "<i2").astype(np.float64)
        if channels > 1:
            x = x.reshape(-1, channels).mean(axis=1)
        if rate != TARGET:
            if rate % TARGET == 0 and self._fir_on:      # 48k -> 16k: stateful FIR, then decimate
                from scipy.signal import lfilter
                f = rate // TARGET
                h = _fir(f)
                if self._rate != rate:
                    self._rate, self._zi, self._n = rate, np.zeros(len(h) - 1), 0
                y, self._zi = lfilter(h, 1.0, x, zi=self._zi)
                x = y[(-self._n) % f::f]
                self._n += len(y)
            elif rate % TARGET == 0:                     # flag off: the old per-chunk box
                f = rate // TARGET
                n = (len(x) // f) * f
                x = x[:n].reshape(-1, f).mean(axis=1)
            else:                                        # generic: linear interpolation
                n_out = int(len(x) * TARGET / rate)
                x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
        return np.clip(np.rint(x), -32768, 32767).astype("<i2").tobytes()


def wav_to_pcm16k(wav):
    """One self-contained chunk (stateless). A live stream must use one Downsampler instead."""
    return Downsampler().feed(wav)
