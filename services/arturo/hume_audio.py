"""Hume audio_output (WAV, 48 kHz per Hume docs — but read the header, never assume) ->
raw s16le 16 kHz mono PCM for the watch downlink (spec §4.2)."""
import struct
import numpy as np

TARGET = 16000


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


def wav_to_pcm16k(wav):
    parsed = _parse(wav)
    if not parsed:
        return b""
    (afmt, channels, rate, _, _, bits), data = parsed
    if afmt != 1 or bits != 16 or channels < 1 or rate <= 0:
        return b""
    x = np.frombuffer(data[: len(data) - (len(data) % (2 * channels))], "<i2").astype(np.float32)
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    if rate != TARGET:
        if rate % TARGET == 0:                           # 48k -> 16k: box low-pass then decimate
            f = rate // TARGET
            n = (len(x) // f) * f
            x = x[:n].reshape(-1, f).mean(axis=1)
        else:                                            # generic: linear interpolation
            n_out = int(len(x) * TARGET / rate)
            x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    return np.clip(np.rint(x), -32768, 32767).astype("<i2").tobytes()
