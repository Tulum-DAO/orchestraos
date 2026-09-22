# local_stt.py — key-free speech-to-text on the box (item C + P1-a default-on).
#
# The web composer's Mic button falls back to "record, then transcribe on the server" in browsers
# with no on-device SpeechRecognition (Firefox, iPhone Chrome, Brave, keyless Chromium). This module
# is that server side. Two engines, one contract:
#
#   * sherpa-whisper (DEFAULT, P1-a, DEC-1790055166991588): sherpa-onnx + whisper tiny.en int8. The
#     wheel is in the default install (`orchestra init`, second soft-fail pip step); the model (99 MB,
#     three files) is fetched from HuggingFace, pinned by size + sha256, atomically (flock, per-pid
#     partial names, hash while streaming, os.replace, `.ok` manifest written LAST). The browser sends
#     16 kHz WAV, so no ffmpeg / PyAV anywhere. Whisper decodes 30 s at a time, so clips are split
#     into <= 28 s windows cut at the quietest point (measured: 42 s clip 76 -> 103 of 106 words).
#     `zipformer-small-en` (27 MB, ALL CAPS, no punctuation) is an env option for constrained boxes.
#   * faster-whisper (OPT-IN, `orchestra init --stt`): the better engine (base.en / small.en); decodes
#     any container itself via PyAV. Selected automatically when installed.
#
# Rules (peer-reviewed, gm msg_0bc8f601): never download inside a request, never in CI (stub engine),
# never blocking boot; `state()` is the one truth /health, doctor and the composer read; every failure
# is first-person and names the fix. Lazy imports: importing this module never imports an engine.
# Web-only: the watch /ptt path and its vendor STT order are untouched.
import fcntl
import hashlib
import os
import struct
import threading
import time
import urllib.request
from pathlib import Path

ENABLED = os.environ.get("ARTURO_LOCAL_STT", "1") not in ("0", "false", "no", "off")
ENGINE_PREF = os.environ.get("ARTURO_LOCAL_STT_ENGINE", "auto").strip().lower()   # auto | faster-whisper | sherpa
MODEL_PREF = os.environ.get("ARTURO_LOCAL_STT_MODEL", "").strip()                 # engine-scoped, see MODEL_DEFAULTS
TRANSCRIBE_TIMEOUT_S = float(os.environ.get("ARTURO_LOCAL_STT_TIMEOUT", "25"))   # < gateway 35 s < api 40 s
CPU_THREADS = int(os.environ.get("ARTURO_LOCAL_STT_THREADS", "4"))
WINDOW_S = 28.0            # whisper hard-caps at 30 s; cut before it
WINDOW_SEARCH_S = 4.0      # look for the quietest 50 ms in the last 4 s of a window
INSTALL_CMD_DEFAULT = "orchestra init"
INSTALL_CMD_BETTER = "orchestra init --stt"

MODEL_DEFAULTS = {"sherpa": "whisper-tiny.en", "faster-whisper": "base.en"}

# Pinned model files (HuggingFace mirrors of the sherpa-onnx release; byte-identical, verified 2026-09-22).
_HF = "https://huggingface.co/csukuangfj/{repo}/resolve/main/{name}"
SHERPA_MODELS = {
    "whisper-tiny.en": {
        "kind": "whisper", "repo": "sherpa-onnx-whisper-tiny.en", "backend": "local-whisper",
        "files": {
            "encoder": ("tiny.en-encoder.int8.onnx", 12937772, "0ce578b827c94a961aacb8fa14b02f096504b337e5c94be37c36238cbe3e8bc6"),
            "decoder": ("tiny.en-decoder.int8.onnx", 89853865, "06c0e6ff6348d427e51839219d1c886c18cfdf411e629e33f5e1679bff9c1527"),
            "tokens":  ("tiny.en-tokens.txt",        835554,   "306cd27f03c1a714eca7108e03d66b7dc042abe8c258b44c199a7ed9838dd930"),
        },
    },
    "zipformer-small-en": {
        "kind": "transducer", "repo": "sherpa-onnx-zipformer-small-en-2023-06-26", "backend": "local-zipformer",
        "files": {
            "encoder": ("encoder-epoch-99-avg-1.int8.onnx", 26015366, "3a6ac78a31cc2c60ca8c1e2e2f43c878fbbcaf051ada4900e0a42ef8ba53d375"),
            "decoder": ("decoder-epoch-99-avg-1.int8.onnx", 1307236,  "f462ab9189ba6f9b2658774e6bf3d651913de54a3833655dc5e093e2f5e4c2b6"),
            "joiner":  ("joiner-epoch-99-avg-1.int8.onnx",  259335,   "6b183b6ec656e4d3ca6b86d0aeca992dac14df80aae6b416d7c068d0ff2bd4d7"),
            "tokens":  ("tokens.txt",                       5048,     "49e3c2646595fd907228b3c6787069658f67b17377c60aeb8619c4551b2316fb"),
        },
    },
}


def _data_dir() -> Path:
    return Path(os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCHESTRA_DATA") or
                os.path.expanduser("~/.orchestra"))


def models_root() -> Path:
    return Path(os.environ.get("ARTURO_LOCAL_STT_DIR") or (_data_dir() / "models"))


def model_dir(engine: str = None, model: str = None) -> Path:
    """Where the files live, under the DATA dir (outside the repo)."""
    engine, model = _resolve(engine, model)
    if engine == "sherpa":
        return models_root() / "sherpa" / model
    return models_root() / "whisper"          # faster-whisper HF cache layout (item C, unchanged)


# ── engine + model resolution ─────────────────────────────────────────────────────────────────

def _importable(mod: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(mod) is not None
    except Exception:  # noqa: BLE001
        return False


def installed(engine: str = None) -> bool:
    """Is the engine's wheel importable? Lazy on purpose (see module doc)."""
    engine = engine or resolve_engine()
    if engine == "faster-whisper":
        return _importable("faster_whisper")
    if engine == "sherpa":
        return _importable("sherpa_onnx")
    return False


def resolve_engine() -> str:
    """auto: the better engine when installed, else the default one, else none. An explicit
    preference is honoured even when its wheel is missing so state() can say so."""
    if ENGINE_PREF in ("faster-whisper", "sherpa"):
        return ENGINE_PREF
    if _importable("faster_whisper"):
        return "faster-whisper"
    if _importable("sherpa_onnx"):
        return "sherpa"
    return "sherpa"          # the default install's engine; state() reports not-installed


def resolve_model(engine: str) -> str:
    default = MODEL_DEFAULTS.get(engine, "")
    if not MODEL_PREF:
        return default
    if engine == "sherpa":
        return MODEL_PREF if MODEL_PREF in SHERPA_MODELS else default
    return MODEL_PREF


def _resolve(engine=None, model=None):
    engine = engine or resolve_engine()
    return engine, (model or resolve_model(engine))


# ── on-disk state ─────────────────────────────────────────────────────────────────────────────

def _manifest_path(d: Path) -> Path:
    return d / ".ok"


def model_on_disk(engine: str = None, model: str = None) -> bool:
    """True only when every file is present with the pinned size AND the manifest was written
    (written last, so a crashed fetch never counts). No network."""
    engine, model = _resolve(engine, model)
    if engine == "sherpa":
        spec = SHERPA_MODELS.get(model)
        if not spec:
            return False
        d = models_root() / "sherpa" / model
        if not _manifest_path(d).exists():
            return False
        for name, size, _sha in spec["files"].values():
            p = d / name
            if not p.exists() or p.stat().st_size != size:
                return False
        return True
    root = models_root() / "whisper"
    if not root.exists():
        return False
    for snap in root.glob(f"models--*faster-whisper-{model}/snapshots/*/model.bin"):
        if snap.exists():
            return True
    return (root / model / "model.bin").exists()


class _State:
    lock = threading.Lock()          # in-memory load AND decode: one clip at a time per process
    model = None
    loaded_key = None
    prefetching = False
    last_error = None


_S = _State()


def state() -> dict:
    """The one truth the /health field, `orchestra doctor` and the composer's tier-3 note all read.
    state ∈ ready | warming | not-installed | off | error. Additive fields: engine, model, backend."""
    engine, model = _resolve()
    backend = SHERPA_MODELS.get(model, {}).get("backend", "local-whisper") if engine == "sherpa" else "local-whisper"
    base = {"server": False, "engine": engine, "model": model, "backend": backend}
    if not ENABLED:
        return {**base, "backend": "none", "state": "off", "reason": "ARTURO_LOCAL_STT=0"}
    if not installed(engine):
        cmd = INSTALL_CMD_BETTER if engine == "faster-whisper" else INSTALL_CMD_DEFAULT
        what = "faster-whisper" if engine == "faster-whisper" else "the speech engine (sherpa-onnx)"
        return {**base, "backend": "none", "state": "not-installed",
                "reason": f"{what} is not installed on this box: run `{cmd}`", "install": cmd}
    if _S.last_error and not model_on_disk(engine, model):
        return {**base, "state": "error", "reason": _S.last_error, "install": INSTALL_CMD_DEFAULT}
    if not model_on_disk(engine, model):
        size = _model_size_mb(engine, model)
        return {**base, "state": "warming",
                "reason": f"downloading the {model} speech model (~{size} MB) in the background"}
    return {**base, "server": True, "state": "ready"}


def _model_size_mb(engine, model) -> int:
    if engine == "sherpa" and model in SHERPA_MODELS:
        return round(sum(f[1] for f in SHERPA_MODELS[model]["files"].values()) / 1_000_000)
    return 140


def available():
    s = state()
    return s["state"] == "ready", s.get("reason")


# ── fetch (never inside a request) ────────────────────────────────────────────────────────────

def _http_stream(url, chunk=1 << 20):
    """Transport seam (tests replace it): yields bytes chunks for url."""
    with urllib.request.urlopen(url, timeout=60) as r:   # noqa: S310 — pinned https URL
        while True:
            b = r.read(chunk)
            if not b:
                return
            yield b


def _fetch_sherpa(model: str, log=None, stream=None) -> bool:
    """Atomic, resumable fetch of one sherpa model. Returns True when on disk. Never raises."""
    stream = stream or _http_stream
    spec = SHERPA_MODELS[model]
    d = models_root() / "sherpa" / model
    partial = d / ".partial"
    d.mkdir(parents=True, exist_ok=True)
    partial.mkdir(exist_ok=True)
    with open(d / ".lock", "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)              # init and the proxy boot thread never race
        try:
            if model_on_disk("sherpa", model):
                return True
            for stale in partial.glob("*"):             # a crashed fetch leaves only .partial/*
                try:
                    stale.unlink()
                except OSError:
                    pass
            for name, size, sha in spec["files"].values():
                dest = d / name
                if dest.exists() and dest.stat().st_size == size and _sha256(dest) == sha:
                    continue                             # resumable: keep files that already verify
                tmp = partial / f"{name}.{os.getpid()}"
                h = hashlib.sha256()
                n = 0
                with open(tmp, "wb") as f:
                    for chunk in stream(_HF.format(repo=spec["repo"], name=name)):
                        f.write(chunk)
                        h.update(chunk)
                        n += len(chunk)
                if n != size or h.hexdigest() != sha:
                    tmp.unlink(missing_ok=True)
                    raise ValueError(f"{name}: got {n} bytes sha {h.hexdigest()[:12]}…, expected {size} bytes sha {sha[:12]}…")
                os.replace(tmp, dest)
            _manifest_path(d).write_text(
                "\n".join(f"{name} {size} {sha}" for name, size, sha in spec["files"].values()) +
                f"\nfetched_at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
            _S.last_error = None
            if log:
                log.info(f"local_stt: {model} ready in {d}")
            return True
        except Exception as e:  # noqa: BLE001
            _S.last_error = f"model download failed: {e}"
            if log:
                log.warning(f"local_stt: {_S.last_error}")
            return False
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prefetch(blocking: bool = False, log=None, engine: str = None, model: str = None) -> bool:
    """Get the model files onto disk if they are not there. Background thread by default (proxy boot);
    `blocking=True` is what `orchestra init` uses. Never raises; failures land in state()."""
    engine, model = _resolve(engine, model)
    if not ENABLED or not installed(engine) or model_on_disk(engine, model):
        return model_on_disk(engine, model)

    def _run():
        _S.prefetching = True
        try:
            if engine == "sherpa":
                _fetch_sherpa(model, log=log)
            else:
                from faster_whisper.utils import download_model
                download_model(model, cache_dir=str(models_root() / "whisper"))
                _S.last_error = None
                if log:
                    log.info(f"local_stt: {model} model ready in {models_root() / 'whisper'}")
        except Exception as e:  # noqa: BLE001
            _S.last_error = f"model download failed: {e}"
            if log:
                log.warning(f"local_stt: {_S.last_error}")
        finally:
            _S.prefetching = False

    if blocking:
        _run()
        return model_on_disk(engine, model)
    if not _S.prefetching:
        threading.Thread(target=_run, daemon=True, name="local-stt-prefetch").start()
    return False


# ── audio helpers (pure; tested) ──────────────────────────────────────────────────────────────

class WavRequired(ValueError):
    """The sherpa engine takes 16-bit PCM WAV only (the browser encodes it); anything else -> 415."""


def parse_wav(data: bytes):
    """RIFF/WAVE 16-bit PCM -> (float32 mono samples, sample_rate). Raises WavRequired otherwise."""
    import numpy as np
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavRequired("not a WAV file")
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    if not fmt or pcm is None:
        raise WavRequired("WAV without fmt/data chunks")
    tag, channels, rate, _br, _ba, bits = fmt
    if tag not in (1, 0xFFFE) or bits != 16 or channels < 1:
        raise WavRequired(f"WAV must be 16-bit PCM (got tag {tag}, {bits} bits)")
    a = np.frombuffer(pcm[: len(pcm) - (len(pcm) % (2 * channels))], dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        a = a.reshape(-1, channels).mean(axis=1)
    return a, rate


def resample_to_16k(samples, rate: int):
    import numpy as np
    if rate == 16000 or len(samples) == 0:
        return samples
    n = int(round(len(samples) * 16000 / rate))
    return np.interp(np.linspace(0, len(samples), n, endpoint=False), np.arange(len(samples)), samples).astype(np.float32)


def split_windows(samples, sr: int = 16000, max_s: float = WINDOW_S, search_s: float = WINDOW_SEARCH_S):
    """Split PCM into <= max_s windows, each cut at the quietest 50 ms in the last search_s seconds
    (so words are not sliced). Whisper discards everything past 30 s otherwise."""
    import numpy as np
    out, i, n = [], 0, len(samples)
    max_n, search_n, k = int(max_s * sr), int(search_s * sr), max(1, sr // 20)
    while n - i > max_n:
        lo, hi = i + max_n - search_n, i + max_n
        env = np.convolve(np.abs(samples[lo:hi]), np.ones(k) / k, mode="same")
        cut = lo + int(np.argmin(env))
        out.append(samples[i:cut])
        i = cut
    out.append(samples[i:])
    return [w for w in out if len(w) > 0]


# ── engines ───────────────────────────────────────────────────────────────────────────────────

class _SherpaEngine:
    backend = "local-whisper"

    def __init__(self, model: str):
        import sherpa_onnx as so
        spec = SHERPA_MODELS[model]
        d = models_root() / "sherpa" / model
        f = {k: str(d / v[0]) for k, v in spec["files"].items()}
        self.kind = spec["kind"]
        self.backend = spec["backend"]
        if self.kind == "whisper":
            self.rec = so.OfflineRecognizer.from_whisper(encoder=f["encoder"], decoder=f["decoder"], tokens=f["tokens"],
                                                         num_threads=CPU_THREADS)
        else:
            self.rec = so.OfflineRecognizer.from_transducer(encoder=f["encoder"], decoder=f["decoder"], joiner=f["joiner"],
                                                            tokens=f["tokens"], num_threads=CPU_THREADS)

    def transcribe_pcm(self, samples) -> str:
        texts = []
        for w in split_windows(samples):
            s = self.rec.create_stream()
            s.accept_waveform(16000, w)
            self.rec.decode_stream(s)
            t = (s.result.text or "").strip()
            if t:
                texts.append(t)
        return " ".join(texts).strip()

    def transcribe_bytes(self, audio_bytes: bytes, content_type: str = "") -> str:
        samples, rate = parse_wav(audio_bytes)        # WavRequired -> 415 at the route
        return self.transcribe_pcm(resample_to_16k(samples, rate))


class _FasterWhisperEngine:
    backend = "local-whisper"
    kind = "whisper"

    def __init__(self, model: str):
        from faster_whisper import WhisperModel
        self.m = WhisperModel(model, device="cpu", compute_type="int8", download_root=str(models_root() / "whisper"),
                              local_files_only=True, cpu_threads=CPU_THREADS)

    def transcribe_bytes(self, audio_bytes: bytes, content_type: str = "") -> str:
        import io
        segments, _info = self.m.transcribe(io.BytesIO(audio_bytes), beam_size=1, vad_filter=False)
        return " ".join(s.text.strip() for s in segments if s.text and s.text.strip()).strip()


def _load(engine: str, model: str):
    """In-memory load, single-flight (the transcribe worker holds _S.lock). Files are on disk (no fetch here)."""
    key = (engine, model)
    if _S.model is None or _S.loaded_key != key:
        _S.model = _SherpaEngine(model) if engine == "sherpa" else _FasterWhisperEngine(model)
        _S.loaded_key = key
    return _S.model


class SttUnavailable(RuntimeError):
    """transcribe() cannot run now; .reason says why (warming / not-installed / off / error)."""
    def __init__(self, reason, st):
        super().__init__(reason)
        self.reason = reason
        self.state = st


def transcribe(audio_bytes: bytes, filename: str = "audio.wav", content_type: str = "",
               timeout_s: float = None, _engine=None) -> dict:
    """Full-clip transcription -> {text, ms, backend, engine, model}. '' text = no speech.
    Raises SttUnavailable (route -> 503), WavRequired (-> 415), TimeoutError (-> 504).
    Clips are serialized per process; the timeout clock starts AFTER the engine lock is taken, so a
    queued clip gets its full budget (peer note, DEC-1790055166991588).
    `_engine` lets tests inject a stub with transcribe_bytes()."""
    st = state()
    if st["state"] != "ready" and _engine is None:
        raise SttUnavailable(st.get("reason") or st["state"], st)
    engine, model = st["engine"], st["model"]
    timeout_s = TRANSCRIBE_TIMEOUT_S if timeout_s is None else timeout_s
    result = {}
    # Queue wait is NOT charged to the clip: take the engine lock first (up to timeout+60 s), then
    # start the clock. The lock is released only when the worker is really done, so a runaway
    # decode can never overlap the next one.
    if not _S.lock.acquire(timeout=timeout_s + 60):
        raise TimeoutError("the speech engine is busy")

    def _run():
        t0 = time.time()
        try:
            eng = _engine if _engine is not None else _load(engine, model)
            result["text"] = eng.transcribe_bytes(audio_bytes, content_type)
            result["backend"] = getattr(eng, "backend", st["backend"])
        except WavRequired as e:
            result["wav_required"] = str(e)
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            result["ms"] = int((time.time() - t0) * 1000)
            _S.lock.release()

    worker = threading.Thread(target=_run, daemon=True, name="local-stt-transcribe")
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise TimeoutError(f"transcription exceeded {timeout_s:.0f}s")
    if "wav_required" in result:
        raise WavRequired(result["wav_required"])
    if "error" in result:
        raise RuntimeError(result["error"])
    return {"text": result["text"], "ms": result["ms"], "backend": result.get("backend", st["backend"]),
            "engine": engine, "model": model}
