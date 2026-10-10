"""Runtime voice preference per engine (hume, openai; contract v2 msg_a7cf1484). Originally the Hume voice preference (the operator ask via ios-g16 msg_6b349b0a; contract msg_f5b57c9d).

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

import sys
# The ONE data-dir default is orchestra_cli.settings.data_dir (data-dir sweep S5); orchestra_cli
# lives in this file's checkout, appended (never prepended) so nothing already on the path is shadowed.
if os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402


ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", _data_dir()))
DEFAULT_PATH = ORCHESTRA_DIR / "state/voice-choice.json"
DEFAULT_LOG = ORCHESTRA_DIR / "state/voice-choice.log"
MAX_VOICE_ID = 200

# Hume's voices API sits behind Cloudflare: a bare urllib UA gets 403 (ios msg_6b349b0a),
# so the fetcher sends a browser-ish UA alongside the API key.
_UA = "Mozilla/5.0 (X11; Linux x86_64) arturo-proxy/1.0"


VENDORS = ("hume", "openai")          # vendors whose voice is chosen SERVER-side
_EMPTY = {"voice_id": None, "changed_at": None, "changed_by": None, "source": None}


class VoiceChoiceStore:
    """ONE global record every surface reads and writes (the operator 2026-10-10: "one change on any surface ... applies
    to all surfaces"). It remembers one pick PER ENGINE ({hume: {...}, openai: {...}}), never per device, so
    switching vendor and back keeps each pick. A legacy single-hume file is read as the hume pick."""
    def __init__(self, path=DEFAULT_PATH, log_path=DEFAULT_LOG):
        self.path = Path(path)
        self.log_path = Path(log_path)
        self._lock = threading.Lock()

    def _read(self):
        try:
            d = json.loads(self.path.read_text())
        except Exception:
            return {}
        if not isinstance(d, dict):
            return {}
        if "voice_id" in d:                                  # legacy: one record, hume only
            return {str(d.get("vendor") or "hume"): {k: d.get(k) for k in _EMPTY}}
        return {v: r for v, r in d.items() if v in VENDORS and isinstance(r, dict)}

    def get(self, vendor):
        return (self._read().get(vendor) or {}).get("voice_id") or None

    def state(self, vendor="hume"):
        rec = self._read().get(vendor) or {}
        return {"vendor": vendor, **{k: rec.get(k) for k in _EMPTY}}

    def set(self, vendor, voice_id, by="settings", source="settings", device=None):
        if vendor not in VENDORS:
            raise ValueError(f"voice choice is server-side for {', '.join(VENDORS)} only "
                             "(elevenlabs voices are picked client-side)")
        voice_id = str(voice_id or "").strip()
        if not voice_id or len(voice_id) > MAX_VOICE_ID:
            raise ValueError(f"voice_id must be 1-{MAX_VOICE_ID} chars")
        rec = {"voice_id": voice_id,
               "changed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "changed_by": str(by)[:40], "source": str(source)[:40]}
        with self._lock:
            d = self._read()
            d[vendor] = rec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(self.path)
            with open(self.log_path, "a") as f:
                # audit: who (by, the client's label), how (source), and WHICH DEVICE (the gateway's
                # resolved caller, never the body) - gm ruling msg_f3c2cec6 condition 2
                f.write(json.dumps({"vendor": vendor, **rec, "device": device}) + "\n")
        return {"vendor": vendor, **rec}


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


# --- server-rendered voice rows (contract v2, ios-watch-dev msg_db125483) ---
TITLE_MAX = 20                         # the Quest's hand-ray targets (quest-orchestra msg_7f9bc6d8)
# GPT-Live voices, MEASURED by session.start probes 2026-10-10 (the docs publish no list): these 10 start a
# session; fable/onyx/nova are refused. With no voice the session reports marin, so marin is the default.
OPENAI_VOICES = ("marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse")
OPENAI_DEFAULT = "marin"
# The operator's curated Hume voices (the client's humeCurated Set, moved server-side), matched by NAME so a
# Hume re-id keeps them. Order = display order; the title is the short label every surface draws.
HUME_CURATED = (("Serene Assistant", "Serene Assistant"), ("Warm Female Assistant Voice", "Warm Female"),
                ("Warm American Female", "Warm American Female"),
                ("Comforting Male Conversationalist", "Comforting Male"),
                ("Soft Male Conversationalist", "Soft Male"), ("Deep Male Conversational Voice", "Deep Male"),
                ("Conversational English Guy", "English Guy"), ("Demure Conversationalist", "Demure"),
                ("Casual Podcast Host", "Casual Podcast Host"), ("Ito", "Ito"))


def short_title(name):
    """<= TITLE_MAX chars, cut at a word boundary (a single overlong word is cut hard)."""
    name = " ".join(str(name).split())
    if len(name) <= TITLE_MAX:
        return name
    cut = name[:TITLE_MAX + 1].rsplit(" ", 1)[0]
    return (cut if cut and len(cut) <= TITLE_MAX else name[:TITLE_MAX]).rstrip()


GROUP_CUSTOM, GROUP_HUME = "Your voices", "Hume voices"   # server-named headings (ios msg_0aa40402 option c)


def _row(vid, name, title, recommended, order, group=None):
    return {"id": vid, "name": name, "title": title, "subtitle": name if name != title else None,
            "recommended": bool(recommended), "order": int(order), "sample_url": None, "group": group}


def voice_rows(vendor, raw=None):
    """The rows a picker draws, every field strictly typed. A row the server cannot fill completely is
    DROPPED here, never sent half-filled (a lenient client must never be the only guard). `group` is a
    heading the server names; clients render whatever groups arrive, in `order`. A voice the operator made themselves
    (Hume custom) is its own group, first and recommended; Hume's library follows."""
    if vendor == "openai":
        return [_row(v, v.capitalize(), v.capitalize(), True, i) for i, v in enumerate(OPENAI_VOICES)]
    if vendor != "hume":
        raise ValueError(f"no server-side voice list for {vendor!r}")
    raw = cached_hume_voices() if raw is None else raw
    curated = {n: (i, t) for i, (n, t) in enumerate(HUME_CURATED)}
    rows = []
    for i, v in enumerate(raw or []):
        vid, name = (v or {}).get("id"), (v or {}).get("name")
        if not (isinstance(vid, str) and vid and isinstance(name, str) and name.strip()):
            continue
        name = " ".join(name.split())
        if v.get("provider") == "custom":
            rows.append(_row(vid, name, short_title(name), True, i, GROUP_CUSTOM))
        elif name in curated:
            pos, title = curated[name]
            rows.append(_row(vid, name, title, True, 1000 + pos, GROUP_HUME))
        else:
            rows.append(_row(vid, name, short_title(name), False, 2000 + i, GROUP_HUME))
    return sorted(rows, key=lambda r: r["order"])


def in_effect(vendor):
    """The voice the NEXT call on this vendor will use (None for hume = the pinned config's voice)."""
    v = _default.get(vendor)
    return v or (OPENAI_DEFAULT if vendor == "openai" else None)


def get_voice(vendor):
    return _default.get(vendor)


def state(vendor="hume"):
    return _default.state(vendor)


def set_voice(vendor, voice_id, by="settings", source="settings", device=None):
    """The PUT cannot be fooled: voice_id must be one of THAT vendor's voices. Hume is checked against the cached
    list and FAILS CLOSED when Hume has never answered (a stale list is fine)."""
    if vendor not in VENDORS:
        return _default.set(vendor, voice_id, by=by, source=source, device=device)   # refuses: plain reason
    vid = str(voice_id or "").strip()
    if vendor == "openai":
        if vid not in OPENAI_VOICES:
            raise ValueError(f"{vid!r} is not an openai voice")
    else:
        try:
            ids = {v.get("id") for v in cached_hume_voices()}
        except Exception as e:  # noqa: BLE001 — no list ever fetched: refuse rather than store an unchecked id
            raise ValueError(f"hume voice list unavailable ({e}); try again") from None
        if vid not in ids:
            raise ValueError(f"{vid!r} is not a hume voice")
    return _default.set(vendor, vid, by=by, source=source, device=device)
