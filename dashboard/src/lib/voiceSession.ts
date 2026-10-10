/**
 * B3 voice bridge client — talks ONLY to the NEW server-side proxy
 * `wss://<host>/api/voice/live` (api/src/routes/voice-live.ts). The browser
 * never holds the watch-gateway bearer and never dials :9091 directly.
 *
 * Wire contract with that proxy (itself a byte-for-byte pass-through of
 * watch_gateway.py's `/live` — Gemini Live bidi, see gemini_live_bridge.py):
 *   up:   binary frames = 16kHz mono PCM16 (raw, little-endian)
 *   down: binary frames = 24kHz mono PCM16 (raw, little-endian) — Arturo's voice
 *         JSON frames  = {event:"connected", session_id, voice}
 *                        {event:"transcript", text, role:"arturo", partial?:true}
 *                        {event:"turn_complete"} | {event:"interrupted"}
 *                        {event:"tool_start"|"tool_done", ...}
 *                        {event:"error", reason}  (our proxy's own LOUD-close frame)
 *
 * IMPORTANT / HONEST GAP (documented, not silently papered over — W1):
 * gemini_live_bridge.py explicitly does NOT server-transcribe the caller's
 * own audio ("USER captions are intentionally NOT server-transcribed... the
 * client renders the user's words live ON-DEVICE"). So `{event:"transcript"}`
 * partials coming DOWN this socket are Arturo's spoken reply being
 * transcribed, never the user's own dictation. For the Mic button's
 * "sub-second partials into the textarea" requirement (DEC §1), this module
 * therefore drives the browser's native on-device SpeechRecognition
 * (webkitSpeechRecognition) directly — exactly the on-device-STT role iOS
 * plays for the same backend contract — and, on a final dictation result,
 * also posts `{event:"user_turn", text}` up the live socket (when a call is
 * active) so the server journals the turn exactly like the iOS client does.
 * Where SpeechRecognition doesn't exist in this browser, `onUnavailable` is
 * called with an honest first-person reason — never a silent no-op.
 */

import { startDictation, DICTATION_UNAVAILABLE, isTier1DeadError, type DictationHandle } from './dictation.ts';

// ── pure helpers (unit-testable without any real mic/WS) ───────────────────

/** Float32 [-1,1] samples -> Int16 PCM (standard 16-bit linear clamp). */
export function floatTo16BitPCM(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/**
 * Linear-average downsample from `inputSampleRate` to `outputSampleRate`
 * (default 16kHz — what the gateway's `/live` upstream expects). Mic
 * hardware is almost never natively 16kHz (44.1k/48k typical), so every
 * captured chunk must pass through this before going on the wire.
 */
export function downsampleBuffer(
  buffer: Float32Array,
  inputSampleRate: number,
  outputSampleRate = 16000,
): Float32Array {
  if (outputSampleRate === inputSampleRate) return buffer;
  if (outputSampleRate > inputSampleRate) {
    throw new Error(`voiceSession: cannot upsample ${inputSampleRate} -> ${outputSampleRate}`);
  }
  const ratio = inputSampleRate / outputSampleRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < newLength) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    result[offsetResult] = count ? accum / count : 0;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }
  return result;
}

/** buffer(any rate) -> 16kHz PCM16 bytes ready for `ws.send(...)`. */
export function pcmChunkForUplink(buffer: Float32Array, inputSampleRate: number): Int16Array {
  return floatTo16BitPCM(downsampleBuffer(buffer, inputSampleRate, 16000));
}

export interface GatewayEvent {
  event?: string;
  text?: string;
  role?: 'user' | 'arturo';
  partial?: boolean;
  session_id?: string;
  voice?: string;
  reason?: string;
  [k: string]: unknown;
}

export interface TranscriptRouterCallbacks {
  onPartial?: (text: string, role: 'user' | 'arturo') => void;
  onFinal?: (text: string, role: 'user' | 'arturo') => void;
}

/**
 * Pure event router: given one parsed JSON frame from the gateway, dispatch
 * to onPartial/onFinal. `partial: true` (or the event `user_turn_log`)
 * forwards IMMEDIATELY — no batching, per the sub-second requirement.
 */
export function routeTranscriptEvent(msg: GatewayEvent, cb: TranscriptRouterCallbacks): void {
  if (typeof msg.text !== 'string' || !msg.text) return;
  const role: 'user' | 'arturo' = msg.role === 'user' ? 'user' : 'arturo';
  if (msg.event === 'transcript') {
    if (msg.partial) cb.onPartial?.(msg.text, role);
    else cb.onFinal?.(msg.text, role);
    return;
  }
  if (msg.event === 'user_turn_log') {
    cb.onPartial?.(msg.text, 'user');
    return;
  }
  if (msg.event === 'user_turn') {
    cb.onFinal?.(msg.text, 'user');
  }
}

/** `[voice-call: <id> <path>]` grammar (dashboard/src/lib/voiceCall.ts MARKER_RE).
 * The path segment is parsed-but-ignored by the reader (card fetches by id via
 * the gateway), so any well-formed non-whitespace token satisfies the grammar.
 * The browser does not know the host's data dir, so the default is a neutral token. */
export function buildVoiceCallMarker(callId: string, orchestraDir = '<data-dir>'): string {
  return `[voice-call: ${callId} ${orchestraDir}/state/voice-calls/${callId}.json]`;
}

// ── runtime session (real mic/WS — not exercised by unit tests) ────────────

export type VoiceSessionState = 'idle' | 'connecting' | 'live' | 'error';

export interface VoiceSessionCallbacks extends TranscriptRouterCallbacks {
  onStateChange?: (state: VoiceSessionState) => void;
  /** W1: fires with an honest first-person reason instead of ever failing silently. */
  onUnavailable?: (reason: string) => void;
  /** Fired once the gateway has told us the call's session_id ({event:"connected"}). */
  onConnected?: (callId: string) => void;
  /** Fired when the call has fully ended (ws closed) and we have a callId to mark. */
  onCallEnded?: (marker: string, callId: string) => void;
}

export interface VoiceSessionStartOptions {
  route: string;
  focusedEntity?: string | null;
  voice?: string;
}

export class VoiceSession {
  state: VoiceSessionState = 'idle';
  private cb: VoiceSessionCallbacks;
  private ws: WebSocket | null = null;
  private mediaStream: MediaStream | null = null;
  private audioCtx: AudioContext | null = null;
  private captureNode: ScriptProcessorNode | AudioWorkletNode | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private playbackCtx: AudioContext | null = null;
  private playbackTime = 0;
  /** Bumped by every start() and stop(): a grant that resolves under a stale value is released. */
  private startSeq = 0;
  private dictation: DictationHandle | null = null;

  constructor(cb: VoiceSessionCallbacks = {}) {
    this.cb = cb;
  }

  private setState(s: VoiceSessionState) {
    this.state = s;
    this.cb.onStateChange?.(s);
  }

  /** Page-aware full call: opens the WS, sends {route, focusedEntity} as the
   * FIRST JSON message (per DEC §1), captures mic -> 16kHz PCM16 upstream,
   * plays 24kHz PCM downstream, and routes transcript JSON events. */
  async start(opts: VoiceSessionStartOptions): Promise<void> {
    if (this.state === 'connecting' || this.state === 'live') return;
    if (typeof window === 'undefined' || !window.WebSocket) {
      this.cb.onUnavailable?.('voice isn\'t configured yet: this environment has no WebSocket support');
      this.setState('error');
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      this.cb.onUnavailable?.('voice isn\'t configured yet: this browser can\'t access the microphone (no getUserMedia)');
      this.setState('error');
      return;
    }

    this.setState('connecting');
    // Which start() this is. stop() and a later start() both bump it, so a permission grant that
    // lands after either belongs to nobody (see the await below).
    const seq = ++this.startSeq;

    // THE MIC FIRST, THEN THE SOCKET (2026-10-09). The socket used to be opened before this
    // await and its 'open' listener attached after it: a socket that opened while the operator
    // was reading the permission prompt fired 'open' to nobody and the call sat in 'connecting'
    // forever. Opening it after the grant, with its listeners attached in the same tick, cannot
    // miss the event. It also means a denied mic never opens a socket at all.
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e: any) {
      if (seq !== this.startSeq) return;          // cancelled while the prompt was up: nothing to report
      this.cb.onUnavailable?.(`voice isn't configured yet: microphone permission was denied or unavailable (${e?.message || e})`);
      this.setState('error');
      return;
    }
    // CANCELLED (or superseded) WHILE THE PROMPT WAS UP. stop() ran before there was a stream to
    // stop, so this grant arrives for a call that no longer exists: release it here, or the mic
    // stays on until the tab closes. Test: voiceSessionStart.test.mjs.
    if (seq !== this.startSeq) {
      for (const track of stream.getTracks()) track.stop();
      return;
    }
    this.mediaStream = stream;

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const voice = opts.voice || 'Fenrir';
    const url = `${proto}://${window.location.host}/api/voice/live?voice=${encodeURIComponent(voice)}`;

    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
      ws.binaryType = 'arraybuffer';
    } catch (e: any) {
      this.cb.onUnavailable?.(`voice isn't configured yet: couldn't open the voice socket (${e?.message || e})`);
      this.stopMicCapture();                      // releases the stream we just took
      this.setState('error');
      return;
    }
    this.ws = ws;
    // THIS socket's call id, held per socket: a stale socket closing after a re-call must report
    // its own call's end, not the new call's.
    let socketCallId: string | null = null;

    ws.addEventListener('open', () => {
      ws.send(JSON.stringify({ route: opts.route, focusedEntity: opts.focusedEntity ?? null }));
      this.setState('live');
      this.startMicCapture();
    });

    ws.addEventListener('message', (ev) => {
      if (typeof ev.data === 'string') {
        let msg: GatewayEvent;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg.event === 'connected' && msg.session_id) {
          socketCallId = msg.session_id;
          this.cb.onConnected?.(msg.session_id);
          return;
        }
        if (msg.event === 'error') {
          this.cb.onUnavailable?.(msg.reason || 'voice bridge reported an error');
          return;
        }
        routeTranscriptEvent(msg, this.cb);
        return;
      }
      this.playPcm24k(ev.data as ArrayBuffer);
    });

    const finish = () => {
      // Only the CURRENT socket may end the call. After stop() + a new start(), the old socket's
      // 'close' still arrives, later; letting it through ended the new call and stopped its mic.
      const id = socketCallId;
      socketCallId = null;                        // 'error' then 'close' must not report twice
      if (this.ws === ws) {
        this.ws = null;
        this.stopMicCapture();
        this.setState('idle');
      }
      if (id) this.cb.onCallEnded?.(buildVoiceCallMarker(id), id);
    };
    ws.addEventListener('close', finish);
    ws.addEventListener('error', () => {
      this.cb.onUnavailable?.('voice bridge connection error');
      finish();
    });
  }

  stop(): void {
    this.startSeq++;                              // a permission grant still pending is now stale
    try { this.ws?.close(); } catch { /* noop */ }
    // Detach it: its 'close' arrives later and must only report its own call, never reset a call
    // started in the meantime (finish() acts on the session only for the CURRENT socket).
    this.ws = null;
    this.stopMicCapture();
    this.setState('idle');
  }

  private startMicCapture() {
    if (!this.mediaStream) return;
    const AudioCtxCtor = window.AudioContext || (window as any).webkitAudioContext;
    const ctx: AudioContext = new AudioCtxCtor();
    this.audioCtx = ctx;
    this.sourceNode = ctx.createMediaStreamSource(this.mediaStream);

    // ScriptProcessorNode is deprecated but universally supported; AudioWorklet
    // needs a separate module file served from the app, which the plain
    // downsample-and-send job here doesn't warrant. Buffer size 4096 @ ctx
    // sample rate keeps latency low while giving downsampleBuffer enough
    // samples per chunk to be cheap.
    const node = ctx.createScriptProcessor(4096, 1, 1);
    node.onaudioprocess = (e) => {
      const input = e.inputBuffer.getChannelData(0);
      const pcm16 = pcmChunkForUplink(input, ctx.sampleRate);
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(pcm16.buffer);
      }
    };
    this.sourceNode.connect(node);
    // A ScriptProcessorNode only fires onaudioprocess while connected into the
    // graph's destination path; route through a silent gain so nothing is
    // actually played back (we handle playback separately via playPcm24k).
    const silence = ctx.createGain();
    silence.gain.value = 0;
    node.connect(silence);
    silence.connect(ctx.destination);
    this.captureNode = node;
  }

  private stopMicCapture() {
    try { this.captureNode?.disconnect(); } catch { /* noop */ }
    try { this.sourceNode?.disconnect(); } catch { /* noop */ }
    try { this.audioCtx?.close(); } catch { /* noop */ }
    this.captureNode = null;
    this.sourceNode = null;
    this.audioCtx = null;
    for (const track of this.mediaStream?.getTracks() ?? []) track.stop();
    this.mediaStream = null;
  }

  private playPcm24k(buf: ArrayBuffer) {
    const AudioCtxCtor = window.AudioContext || (window as any).webkitAudioContext;
    if (!this.playbackCtx) {
      this.playbackCtx = new AudioCtxCtor({ sampleRate: 24000 });
      this.playbackTime = this.playbackCtx.currentTime;
    }
    const ctx = this.playbackCtx;
    const pcm16 = new Int16Array(buf);
    const float32 = new Float32Array(pcm16.length);
    for (let i = 0; i < pcm16.length; i++) float32[i] = pcm16[i] / 0x8000;
    const audioBuffer = ctx.createBuffer(1, float32.length, 24000);
    audioBuffer.copyToChannel(float32, 0);
    const src = ctx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime, this.playbackTime);
    src.start(startAt);
    this.playbackTime = startAt + audioBuffer.duration;
  }

  // ── dictation (Mic button push/hold-to-talk into the textarea) ──────────
  // See module doc: the gateway does not transcribe the caller's own voice,
  // so dictation uses on-device browser SpeechRecognition directly and does
  // NOT require an active VoiceSession/WS at all.

  startDictation(): void {
    const handle = startDictation({
      onPartial: (text) => { this.cb.onPartial?.(text, 'user'); },
      onFinal: (text) => {
        this.cb.onFinal?.(text, 'user');
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ event: 'user_turn', text }));
        }
      },
      // 'aborted' = we (or a second recognizer) stopped it; 'no-speech' = silence. Neither is a failure.
      onError: (code) => {
        if (code === 'aborted' || code === 'no-speech') return;
        // The recognizer exists but its service is dead (Brave, Chromium without Google keys,
        // offline). This mic is tier 1 only (it never uploads a clip: that is Arturo's tier 2, behind
        // the arturo flag), so say what works instead of echoing the browser's code.
        // Chrome itself reports 'network' when it is OFFLINE: say that, not "switch browser". Only an
        // explicit false counts; an unknown onLine is treated as online.
        if (code === 'network' && typeof navigator !== 'undefined' && navigator.onLine === false) {
          this.cb.onUnavailable?.('dictation needs a connection: you are offline');
          return;
        }
        if (isTier1DeadError(code)) { this.cb.onUnavailable?.(`voice isn't configured yet: ${DICTATION_UNAVAILABLE}`); return; }
        this.cb.onUnavailable?.(`dictation error: ${code}`);
      },
    });
    if (!handle) {
      this.cb.onUnavailable?.(`voice isn't configured yet: ${DICTATION_UNAVAILABLE}`);
      return;
    }
    this.dictation = handle;
  }

  stopDictation(): void {
    this.dictation?.stop();
    this.dictation = null;
  }
}
