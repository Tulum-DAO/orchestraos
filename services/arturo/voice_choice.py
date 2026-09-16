"""Runtime Hume voice preference (the operator ask via ios-g16 msg_6b349b0a; contract msg_f5b57c9d).

Same pattern as voice_vendor.py: ONE JSON file read per connect — never env-at-boot — so a
Settings pick takes effect on the next conversation with no :5071 restart. The pick reaches
EVI via session_settings.voice_id (docs-verified: "change the voice during an active chat"),
so no config re-pin either. EL voice choice is client-side (the phone passes voice_id) and is
refused here. Append-only audit log; atomic tmp-replace writes."""
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", Path.home() / "scripts/agent-orchestra"))
DEFAULT_PATH = ORCHESTRA_DIR / "state/voice-choice.json"
DEFAULT_LOG = ORCHESTRA_DIR / "state/voice-choice.log"
MAX_VOICE_ID = 200

# Hume's voices API sits behind Cloudflare: a bare urllib UA gets 403 (ios msg_6b349b0a),
# so the fetcher sends a browser-ish UA alongside the API key.
_UA = "Mozilla/5.0 (X11; Linux x86_64) arturo-proxy/1.0"


class VoiceChoiceStore:
    def __init__(self, path=DEFAULT_PATH, log_path=DEFAULT_LOG):
        self.path = Path(path)
        self.log_path = Path(log_path)
        self._lock = threading.Lock()

    def _read(self):
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {"vendor": "hume", "voice_id": None, "changed_at": None,
                    "changed_by": None, "source": None}

    def get(self, vendor):
        if vendor != "hume":
            return None
        d = self._read()
        return d.get("voice_id") or None

    def state(self, vendor="hume"):
        d = self._read()
        d["vendor"] = "hume"
        return d

    def set(self, vendor, voice_id, by="settings", source="settings"):
        if vendor != "hume":
            raise ValueError("voice choice is server-side for hume only "
                             "(elevenlabs voices are picked client-side)")
        voice_id = str(voice_id or "").strip()
        if not voice_id or len(voice_id) > MAX_VOICE_ID:
            raise ValueError(f"voice_id must be 1-{MAX_VOICE_ID} chars")
        st = {"vendor": "hume", "voice_id": voice_id,
              "changed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "changed_by": str(by)[:40], "source": str(source)[:40]}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st))
            tmp.replace(self.path)
            with open(self.log_path, "a") as f:
                f.write(json.dumps(st) + "\n")
        return st


def _secret(k):
    try:
        for line in open(ORCHESTRA_DIR / ".env.secrets"):
            if line.startswith(k + "="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get(k, "")


def _default_fetch(provider, page_number):
    req = urllib.request.Request(
        f"https://api.hume.ai/v0/tts/voices?provider={provider}"
        f"&page_number={page_number}&page_size=100",
        headers={"X-Hume-Api-Key": _secret("HUME_API_KEY"), "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


_PROVIDER_LABEL = {"CUSTOM_VOICE": "custom", "HUME_AI": "hume_library"}


def list_hume_voices(fetch=None):
    """Aggregate BOTH Hume providers (custom account voices + the Hume voice library),
    paged, mapped to the client contract [{id, name, provider: custom|hume_library}]."""
    fetch = fetch or _default_fetch
    out = []
    for provider in ("CUSTOM_VOICE", "HUME_AI"):
        page = 0
        while True:
            d = fetch(provider, page)
            for v in d.get("voices_page") or []:
                out.append({"id": v.get("id"), "name": v.get("name"),
                            "provider": _PROVIDER_LABEL.get(v.get("provider"), "hume_library")})
            page += 1
            if page >= int(d.get("total_pages") or 1):
                break
    return out


VOICES_CACHE_TTL_S = 6 * 3600     # ios msg_64481ea2: cold double-provider fetch + Funnel
                                  # overhead timed out the picker's first open; cache it.
_voices_cache = {"voices": None, "ts": 0.0}
_voices_cache_lock = threading.Lock()


def clear_voices_cache():
    with _voices_cache_lock:
        _voices_cache["voices"], _voices_cache["ts"] = None, 0.0


def cached_hume_voices(ttl_s=VOICES_CACHE_TTL_S):
    """TTL-cached aggregated voices list (refresh on miss; STALE beats an error when the
    refresh fails — the picker degrades to a slightly old list, never a 502, unless there
    has never been a successful fetch)."""
    with _voices_cache_lock:
        cached, ts = _voices_cache["voices"], _voices_cache["ts"]
    if cached is not None and time.time() - ts < ttl_s:
        return cached
    try:
        fresh = list_hume_voices()
    except Exception:
        if cached is not None:
            return cached
        raise
    with _voices_cache_lock:
        _voices_cache["voices"], _voices_cache["ts"] = fresh, time.time()
    return fresh


_default = VoiceChoiceStore()


def get_voice(vendor):
    return _default.get(vendor)


def state(vendor="hume"):
    return _default.state(vendor)


def set_voice(vendor, voice_id, by="settings", source="settings"):
    return _default.set(vendor, voice_id, by=by, source=source)
