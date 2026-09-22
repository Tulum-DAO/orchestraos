# local_stt.py — key-free speech-to-text on the box (item C, DEC-1790045383668733).
#
# The web composer's Mic button falls back to "record, then transcribe on the server" in browsers
# with no on-device SpeechRecognition (Firefox, iPhone Chrome, Brave, keyless Chromium). This module
# is that server side: faster-whisper (CTranslate2, CPU int8) on the install's own CPU, no vendor
# key, no network at request time.
#
# Contract (peer-reviewed):
#   * OPT-IN INSTALL: faster-whisper lives in requirements-stt.txt, installed by `orchestra init --stt`
#     (or [arturo] local_stt = true). `available()` says exactly which state the install is in, so the
#     composer and `orchestra doctor` can name the fix instead of failing silently.
#   * NEVER DOWNLOAD INSIDE A REQUEST: `prefetch()` runs in a background thread at proxy boot (and from
#     init); until the model files are on disk `transcribe()` refuses with "warming". The load lock is
#     held only across the in-memory load, never across a fetch.
#   * LAZY IMPORT: faster_whisper is imported inside functions only, so importing this module (and the
#     proxy) never pulls it in — the existing test suite and a slim install are untouched.
#   * Web-only. The watch /ptt path and its vendor STT order are not touched by this module.
import os
import threading
import time
from pathlib import Path

INSTALL_CMD = "orchestra init --stt"
DEFAULT_MODEL = "base.en"
MODEL = os.environ.get("ARTURO_LOCAL_STT_MODEL", DEFAULT_MODEL)
ENABLED = os.environ.get("ARTURO_LOCAL_STT", "1") not in ("0", "false", "no", "off")
TRANSCRIBE_TIMEOUT_S = float(os.environ.get("ARTURO_LOCAL_STT_TIMEOUT", "25"))   # < gateway 35 s < api 40 s
CPU_THREADS = int(os.environ.get("ARTURO_LOCAL_STT_THREADS", "4"))


def _data_dir() -> Path:
    return Path(os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCHESTRA_DATA") or
                os.path.expanduser("~/.orchestra"))


def model_dir() -> Path:
    """Where the model files live: under the DATA dir (outside the repo), one folder per install."""
    return Path(os.environ.get("ARTURO_LOCAL_STT_DIR") or (_data_dir() / "models" / "whisper"))


def installed() -> bool:
    """faster-whisper importable? Lazy on purpose (see module doc)."""
    try:
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:  # noqa: BLE001
        return False


def model_on_disk(name: str = None) -> bool:
    """True when the converted model files for `name` are already in model_dir() (no network needed)."""
    name = name or MODEL
    root = model_dir()
    if not root.exists():
        return False
    # HF hub layout: models--Systran--faster-whisper-<name>/snapshots/<rev>/model.bin
    for snap in root.glob(f"models--*faster-whisper-{name}/snapshots/*/model.bin"):
        if snap.exists():
            return True
    # or a plain directory named after the model (CTranslate2 export)
    return (root / name / "model.bin").exists()


class _State:
    lock = threading.Lock()          # held only across the in-memory load
    model = None
    loaded_name = None
    prefetching = False
    last_error = None


_S = _State()


def state() -> dict:
    """The one truth the /health field, `orchestra doctor` and the composer's tier-3 note all read.
    state ∈ ready | warming | not-installed | off | error."""
    if not ENABLED:
        return {"server": False, "backend": "none", "state": "off", "reason": "ARTURO_LOCAL_STT=0", "model": MODEL}
    if not installed():
        return {"server": False, "backend": "none", "state": "not-installed",
                "reason": f"faster-whisper is not installed: run `{INSTALL_CMD}`", "install": INSTALL_CMD, "model": MODEL}
    if _S.last_error and not model_on_disk():
        return {"server": False, "backend": "local-whisper", "state": "error", "reason": _S.last_error, "model": MODEL}
    if not model_on_disk():
        return {"server": False, "backend": "local-whisper", "state": "warming",
                "reason": f"downloading the {MODEL} speech model (~140 MB) in the background", "model": MODEL}
    return {"server": True, "backend": "local-whisper", "state": "ready", "model": MODEL}


def available():
    """(ok, reason) — ok only when a transcribe() call would run right now."""
    s = state()
    return s["state"] == "ready", s.get("reason")


def prefetch(blocking: bool = False, log=None) -> bool:
    """Download the model files to model_dir() if they are not there. Background thread by default;
    `blocking=True` is what `orchestra init --stt` uses. Never raises; failures land in state()."""
    if not ENABLED or not installed() or model_on_disk():
        return model_on_disk()

    def _run():
        _S.prefetching = True
        try:
            from faster_whisper.utils import download_model
            download_model(MODEL, cache_dir=str(model_dir()))
            _S.last_error = None
            if log:
                log.info(f"local_stt: {MODEL} model ready in {model_dir()}")
        except Exception as e:  # noqa: BLE001
            _S.last_error = f"model download failed: {e}"
            if log:
                log.warning(f"local_stt: {_S.last_error}")
        finally:
            _S.prefetching = False

    if blocking:
        _run()
        return model_on_disk()
    if not _S.prefetching:
        threading.Thread(target=_run, daemon=True, name="local-stt-prefetch").start()
    return False


def _load():
    """In-memory model load, single-flight. Only called once the files are on disk (no fetch here)."""
    with _S.lock:
        if _S.model is None or _S.loaded_name != MODEL:
            from faster_whisper import WhisperModel
            _S.model = WhisperModel(MODEL, device="cpu", compute_type="int8",
                                    download_root=str(model_dir()), local_files_only=True,
                                    cpu_threads=CPU_THREADS)
            _S.loaded_name = MODEL
        return _S.model


class SttUnavailable(RuntimeError):
    """transcribe() cannot run now; .reason says why (warming / not-installed / off / error)."""
    def __init__(self, reason, st):
        super().__init__(reason)
        self.reason = reason
        self.state = st


def transcribe(audio_bytes: bytes, filename: str = "audio.webm", content_type: str = "",
               timeout_s: float = None, _model=None) -> dict:
    """Full-clip transcription -> {text, ms, backend, model}. '' text means "no speech".
    Raises SttUnavailable when the backend is not ready (the route maps it to 503) and
    TimeoutError when a clip takes longer than timeout_s (25 s default, under the gateway's 35 s).
    `_model` lets tests inject a stub without faster-whisper."""
    st = state()
    if st["state"] != "ready" and _model is None:
        raise SttUnavailable(st.get("reason") or st["state"], st)
    model = _model if _model is not None else _load()
    timeout_s = TRANSCRIBE_TIMEOUT_S if timeout_s is None else timeout_s
    result = {}

    def _run():
        import io
        t0 = time.time()
        try:
            segments, _info = model.transcribe(io.BytesIO(audio_bytes), beam_size=1, vad_filter=False)
            text = " ".join(s.text.strip() for s in segments if s.text and s.text.strip())
            result["text"] = text.strip()
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"
        result["ms"] = int((time.time() - t0) * 1000)

    worker = threading.Thread(target=_run, daemon=True, name="local-stt-transcribe")
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise TimeoutError(f"transcription exceeded {timeout_s:.0f}s")
    if "error" in result:
        raise RuntimeError(result["error"])
    return {"text": result["text"], "ms": result["ms"], "backend": "local-whisper", "model": MODEL}
