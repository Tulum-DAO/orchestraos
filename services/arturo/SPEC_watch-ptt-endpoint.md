# SPEC — Watch push-to-talk endpoint (`/arturo/ptt`)

**Status:** DESIGN GATE = APPROVED by gm (msg_dc8d4197). Design-first artifact for the build gate.
**Owner:** arturo-restore-dev (server) · **Client owner:** ios-watch-dev
**Contract:** LOCKED with ios-watch-dev (msg_ff49325b). **Do NOT build the live gateway change** until:
RED-first → multi-model consensus (Astra OUT) → gm BUILD gate (gm verifies by execution) → the operator install word.
Inject-fix (three-runtime invariant) stays **priority #1**; this endpoint is the Watch-Arturo critical path once that ships.

## 1. Goal
Turn-based push-to-talk for Watch-Arturo (the operator chose PTT). The watch records a full utterance, POSTs the
audio, and gets back reply text (for the on-watch transcript) + TTS audio to play. Plain HTTPS
request/response, **no persistent socket** — the turn-based alternative to the existing `/live`
(`handle_gemini_live`, 16k/24k PCM socket) which watchOS cannot hold. ios-watch-dev PROVED the client
round-trip on the operator's Ultra (record m4a → playback).

## 2. Placement (minimal new surface on the authenticated gateway)
- Host: `scripts/watch_gateway.py` (aiohttp `web.Application`).
- Route: one new line in `build_app()` — `app.router.add_post("/arturo/ptt", handle_arturo_ptt)`.
- Auth: **REUSE `_authorized(request)`** — Bearer against `~/.config/jarvis/watch-gateway-token`, the SAME
  token the watch approvals poll uses (`Config.gatewayToken`). No new secret, no new principal.
- Input: **MIRROR `handle_upload`'s** proven `request.multipart()` chunked-read path.
- The endpoint is a **thin HTTP adapter**: auth + parse + orchestrate; it delegates STT / brain / TTS to
  the arturo services and holds **no brain logic** in the gateway.

## 3. Request
`POST /arturo/ptt` — `multipart/form-data`, `Authorization: Bearer <watch-gateway-token>`

| field | value |
|---|---|
| `audio` | `.m4a` container, **AAC-LC, MONO, 16 kHz**. Max utterance **30 s** (~60 KB). |
| `conversation_id` | UUID string, minted per conversation by the watch, **reused across turns** (threads to one brain context). Watch resets it on new-conversation / relaunch. |
| `turn_id` | UUID string, **fresh per POST**. Idempotency key for tunnel retries. |

## 4. Limits
- Size cap: PTT-specific **1 MB** (30 s × 16k mono AAC ≪ 1 MB → 413 over cap). Not the 100 MB upload cap.
- STT bound: 30 s max audio.

## 5. Pipeline (`handle_arturo_ptt`)
1. Auth → `401` if not `_authorized`.
2. Parse multipart; enforce 1 MB cap + type/duration guard; `400` malformed/empty, `413` oversize.
3. **`turn_id` dedup:** if this `turn_id` already produced a result, return the CACHED turn (no re-run,
   no double STT charge).
4. **STT (full-utterance):** transcribe `audio` → `stt_text`. Recognizer = **OpenAI Whisper /
   `gpt-4o-transcribe`** (gm-approved default; authorized, OpenAI client already wired in arturo-proxy;
   native 16k; full-utterance is the easy case). Empty/no-speech → `422 no_speech`.
5. **Brain (★ condition 2 — the one real integration risk):** produce `reply_text` via a **lifecycle-free
   reply** — see §6. **MUST NOT** trigger the EL voice-CALL lifecycle.
6. **TTS:** Cartesia → **mp3** (AVAudioPlayer-native, no transcode).

## 6. ★ Brain reuse — lifecycle-free (gm condition 2)
The existing brain path is the journaled `chat_completions()` route (`arturo-proxy.py:2683`), which is
entangled with `_log_voice_turn` / `CallJournal` / `finalize_from_client` / `ended_once` / the
"Voice call ended" gm-inject. **PTT must NOT reuse that route.** Instead:
- A new **pure text-in/text-out** reply function shares the Arturo **system prompt + model + (optional
  tools)** and calls `client.chat.completions.create(...)` directly.
- It writes **NO `CallJournal`**, runs **NO** end-of-call gm-injection, claims **NO** `ended_once`
  ledger entry, and emits **NO** "Voice call ended" to gm. A PTT turn is a **stateless turn, not a call**.
- Multi-turn context: a **bounded server-side per-`conversation_id` history** (system + prior user/assistant
  turns) so turns thread without a socket. Bounded in turns/age; evicted on new-conversation.
- Tools: PTT MAY expose the brain's tools (same assistant capabilities). Tool *execution* side effects are
  out of scope for condition 2 (which is specifically the call lifecycle); if any tool itself has call-only
  side effects it is gated off the PTT path. (Flag at build if a tool assumes a live call.)

## 7. Response
`200 application/json`
- `reply_text`: str — Arturo's reply (on-watch transcript).
- `stt_text`: str — what STT heard (shown as the user's own turn).
- `audio`: **mp3 as inline base64** (Option A — one round-trip; replies are short; avoids a second
  tunnel-negotiating GET). Revisit a GETtable Bearer URL only for very long replies.

## 8. Errors (visible-state contract — mirror the approvals path; never a dead spinner, never a fabricated reply)
- `401` unauthorized · `400` malformed/empty multipart · `413` over 1 MB
- `422 no_speech` (STT empty) → watch: "didn't catch that, try again"
- `502` upstream STT/brain/TTS failure · `503`/`504` overload/timeout
- Every non-200 returns `{ok:false, error:<code>}` JSON.

## 9. Latency target (informs recognizer/timeouts, not a hard SLA)
p50 ~3–4 s, p95 < 8 s for upload+STT+brain+TTS on a ≤30 s clip.

## 10. Tests (RED-first, house importlib + spy pattern; no live vendor calls)
1. Unauthenticated POST → `401`; pipeline NOT called.
2. Valid multipart → STT spy gets the bytes; brain spy gets `stt_text`+`conversation_id`; TTS spy gets
   `reply_text`; response = `{reply_text, stt_text, audio(base64 mp3)}`.
3. Oversize → `413`; malformed/empty → `400`; STT empty → `422 no_speech`; upstream fail → `502`.
4. Same `turn_id` twice → brain invoked **once** (dedup); second returns the cached turn.
5. **★ No-call-lifecycle assertion (gm condition 2):** a PTT turn writes **no `CallJournal` file**,
   claims **no `ended_once`** entry, and issues **no** gm-inject / "Voice call ended". (Spy the
   journal/ended_once/gm-inject seams; assert zero calls.)

## 11. PRESERVED-CONTRACTS (DEC-1786888275 — user-facing surface change; checked vs DOCS/SURFACE_CONTRACTS.md)
`/arturo/ptt` is a new watch-facing surface, so it is checked item-by-item against the registry:
- **P1 (marker grammars render equivalently across iOS/web/watch):** `n-a` — PTT returns plain
  `reply_text`/`stt_text` + an audio blob, not marker-grammar rows; it renders none of `[voice-call:]`,
  pasted-text, or `[attached:]`.
- **W5 §1.1b (every endpoint returning an approval/menu ROW must apply the capability gate + add
  itself to the gated-egress list):** `n-a` — PTT is NOT a row-serving endpoint. It returns a turn
  result, never a `pending-approvals`/`approvals/{id}`-style menu row, so the
  `_client_hydrates_multipart`/`_failsafe_unhydrated_multipart` gate does not apply and it adds no new
  menu-row egress.
- **W6 (every watch-gateway respawn verifies all required `config/watch-gateway.env` vars):**
  `preserved` — PTT adds NO new *required* env var. `WATCH_GATEWAY_PTT_MAX` and `ARTURO_PTT_URL` are
  optional with safe defaults and are not added to the required `MENU_SUBMIT_ARMED` /
  `MENU_MULTIPART_SUBMIT_ARMED` respawn-verification set.
- **`_priority_score` (the one ordering brain) / `human_task` iron rule:** `preserved` — PTT does not
  touch `handle_pending`, ordering, or any `kind`; it introduces no card/menu row.

## 12. Build path / sequencing
Final spec (this doc) → RED-first (§10) → multi-model consensus (code-reviewer + gemini-dev, **Astra OUT**;
touches the authed gateway) → gm BUILD gate (verify by execution) → the operator install word. **No live gateway
change before that.** ios-watch-dev builds the client against a stub of exactly this shape in parallel;
ship = their stub→live swap.
