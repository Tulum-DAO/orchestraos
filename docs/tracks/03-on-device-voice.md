# Track 3 — On-device voice tier (iOS)

Size: M · Labels: `track`, `ios`, `arturo`

## Problem

Voice on the iOS app needs a vendor key today: `services/arturo/voice_vendor.py`
defines exactly two allowed vendors, `elevenlabs` (`ELEVENLABS_API_KEY`) and `hume`
(`HUME_API_KEY` + two more config values) — see `REQUIRED` at the top of that file.
`VendorStore.get()` refuses to select a vendor whose credentials aren't present
(`set()` "refuses anything else (no silent fallback)"). A fresh install with no
vendor keys has no voice path at all, even though the iOS app already ships
`SFSpeechRecognizer`-based on-device STT for live captions (a different feature) and
the phone itself can do TTS for free.

## Design

Add `local` as a third vendor in `voice_vendor.py`'s `REQUIRED` map with an empty
credential list (always "allowed"), and a corresponding `LocalVoiceEngine` on the
iOS side implementing the same `VoiceEngine` protocol the existing Cartesia/EL/Hume
engines conform to (grep the iOS repo for `protocol VoiceEngine` — mirror its
method surface, don't invent a new one):

- **STT** — `SFSpeechRecognizer` with `requiresOnDeviceRecognition = true` where the
  locale supports it (fall back to server-side Apple recognition otherwise, which
  still needs no vendor key); this is the same recognizer class the app already uses
  for caption overlays, so the permission prompt and availability check are reused,
  not new.
- **Turn text** — goes through the existing gateway text path, i.e. whatever `Brain`
  is selected by Track 2 (`[arturo] brain`); `LocalVoiceEngine` does not talk to any
  brain directly, it only wraps STT/TTS around the same HTTP call the text composer
  already makes.
- **TTS** — `AVSpeechSynthesizer` speaking the reply text; no audio file round-trip,
  no vendor upload.

Engine selection is server-driven, not a client toggle: the gateway's existing
`/voice/vendor` shape (`GET` returns `_voice_vendor.state()`,
`arturo-proxy.py` ~line 3920) gains a `local` value that the client should offer as
one of its choices, and `voice_vendor.get_vendor()` should resolve to `local` when
none of `elevenlabs`/`hume`'s credentials are present (the `auto`-style fallback,
mirroring Track 2's `arturo.brain = auto`). The VoiceSurface pill/panel and partial-
transcript rendering the app already has for other engines are unchanged; only the
STT/TTS backend swaps.

## Files you will touch

- `services/arturo/voice_vendor.py` — add `"local": []` to `REQUIRED`; update
  `get_vendor()` (or wherever the "refuse to select without creds" check lives) so
  `local` is always eligible and is the fallback when neither other vendor has
  creds.
- `services/arturo/arturo-proxy.py` (~line 3914-3928, the `/voice/vendor` GET/POST
  handlers) — confirm `local` round-trips through `state()`/`set_vendor()`
  unchanged; no new route needed.
- iOS repo — new `LocalVoiceEngine: VoiceEngine` (STT via `SFSpeechRecognizer`,
  reusing the existing caption-overlay permission/availability code; TTS via
  `AVSpeechSynthesizer`); wire it into whatever engine-selection switch the app uses
  today (search for the existing Cartesia/EL engine instantiation site).
- iOS repo — VoiceSurface pill/panel: confirm no changes needed beyond engine
  selection (partials and the transcript card contract stay the same).

## Steps

1. `python3 -c "from services.arturo import voice_vendor as vv; print(vv.REQUIRED)"`
   — confirm the current two-vendor map before touching it.
2. Add `local` to `REQUIRED`, add/adjust the fallback logic, run
   `services/arturo/test_voice_vendor.py` and `test_ptt_vendor_routes.py` — both
   green, plus a new case: no `ELEVENLABS_API_KEY`/`HUME_API_KEY` in env →
   `get_vendor()` returns `local`.
3. `curl <arturo>/voice/vendor` with no vendor keys set — expect `{"vendor":
   "local", ...}`.
4. On the iOS side, implement `LocalVoiceEngine`, gate it behind the same
   `/voice/vendor` response, and run the app on a device (simulator has no real
   microphone/speech recognition — do not "ship" a simulator-only verification,
   see the fleet's own on-device-only rule for this exact class of feature).
5. Hold the orb with no vendor keys configured anywhere → speak a short phrase →
   confirm the on-device transcript appears, a reply comes back through the T2
   brain path, and it's spoken back via `AVSpeechSynthesizer`.
6. Confirm the transcript card posts to the dashboard/Inbox exactly like a
   vendor-backed call (same card shape, same `call_journal.py` write).

## Acceptance test

Fresh install, no `ELEVENLABS_API_KEY` / `HUME_API_KEY` / `CARTESIA_API_KEY`
anywhere in env or `.env.secrets`. Hold the orb on a real device → speak → hear a
spoken reply with no vendor round-trip (confirm via network inspector or a log line
that no ElevenLabs/Hume/Cartesia host was contacted). The call's transcript card
posts to the dashboard the same way a vendor-backed call's does.

## Start prompt

```
I'm working Track 3 (on-device voice tier) for the OrchestraOS hackathon.
Read docs/tracks/03-on-device-voice.md in this repo for the full design.
Files to touch: services/arturo/voice_vendor.py (add "local" to REQUIRED
and the fallback logic), services/arturo/arturo-proxy.py (confirm the
existing /voice/vendor routes carry the new value unchanged), and the iOS
repo's VoiceEngine implementations (new LocalVoiceEngine using
SFSpeechRecognizer + AVSpeechSynthesizer).
Start on the server side: add "local" to voice_vendor.py's REQUIRED map,
prove get_vendor() falls back to it with no vendor keys set (Steps 1-3),
then move to iOS. This must be verified on a real device — the simulator
has no usable speech recognition or microphone.
```

## Out of scope

- Wake-word / always-listening (this track is push-to-talk / held-orb only, same as
  the existing engines).
- Non-English on-device recognition quality tuning (use whatever locale support
  `SFSpeechRecognizer` ships with; don't build a custom model).
- Changing the transcript card contract or `call_journal.py` shape — this track
  only changes which engine produces the audio/text.
