/**
 * nativeVoiceSession.ts — the browser glue for a native GPT-Live call (wss://<host>/api/voice/native). It speaks the
 * relay session's interface (CallSession + VoiceSessionCallbacks), so lib/handsFree.ts and the UI cannot tell them
 * apart: captions via onPartial/onFinal, connecting/live/idle via onStateChange, tools via onTool, the call's
 * `[voice-call: …]` marker via onCallEnded, and every refusal or notice via onUnavailable.
 * The pure parts (framing, resampling, the transcript rows, wording) are lib/nativeVoice.ts, with tests.
 *
 * The native server keeps no transcript: on any end of a call that went live, this posts the call's turns to
 * POST /api/voice/call-ended (-> gateway /voice-call-ended -> vc_client_<sha1(conv_id)[:12]>), and the id it answers
 * names the call's card. On hang-up that post goes first, then {"type":"disconnect"}, then the socket is closed on
 * `stopped`, on the server's close, or after 6 s.
 */
import { buildVoiceCallMarker, type VoiceSessionStartOptions, type VoiceSessionState } from './voiceSession.ts';
import type { CallSession } from './handsFree.ts';
import type { NativeCallbacks } from './voiceEngine.ts';
import {
  START_FRAME, DISCONNECT_FRAME, contextFrame, MicUplink, leBytesToFloat, NATIVE_RATE, parseNativeFrame,
  emptyTranscript, reduceTranscript, closeAll, journalTurns, newToolCalls, argsSummary, closeMessage, shouldFallBack,
  type TranscriptEffect, type TranscriptState,
} from './nativeVoice.ts';

const HANGUP_WAIT_MS = 6000;
/** Lead before the first chunk of an utterance (playback otherwise starts the instant a chunk lands). */
const PLAYBACK_LEAD_S = 0.08;

const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** The worklet: hands each 128-sample block of the mic to the page. Loaded from a Blob URL, so it needs no file
 *  served next to the app (an embedded widget build serves the app from a different base). */
const WORKLET_SRC = `class OosMicTap extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor('oos-mic-tap', OosMicTap);`;

export class NativeVoiceSession implements CallSession {
  state: VoiceSessionState = 'idle';
  private readonly cb: NativeCallbacks;
  private ws: WebSocket | null = null;
  private seq = 0;
  private muted = false;
  private mediaStream: MediaStream | null = null;
  private audioCtx: AudioContext | null = null;
  private captureNode: AudioWorkletNode | ScriptProcessorNode | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private playbackCtx: AudioContext | null = null;
  private playbackTime = 0;
  private playing = new Set<AudioBufferSourceNode>();
  micAnalyser: AnalyserNode | null = null;
  playbackAnalyser: AnalyserNode | null = null;

  constructor(cb: NativeCallbacks = {}) {
    this.cb = cb;
  }

  private setState(s: VoiceSessionState) {
    this.state = s;
    this.cb.onStateChange?.(s);
  }

  async start(opts: VoiceSessionStartOptions): Promise<void> {
    if (this.state === 'live' || (this.state === 'connecting' && this.ws)) return;
    if (typeof window === 'undefined' || !window.WebSocket) {
      this.cb.onUnavailable?.("voice isn't configured yet: this environment has no WebSocket support");
      this.setState('error');
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      this.cb.onUnavailable?.("voice isn't configured yet: this browser can't access the microphone (no getUserMedia)");
      this.setState('error');
      return;
    }
    this.setState('connecting');
    const seq = ++this.seq;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e: unknown) {
      if (seq !== this.seq) return;
      this.cb.onUnavailable?.(`voice isn't configured yet: microphone permission was denied or unavailable (${errText(e)})`);
      this.setState('error');
      return;
    }
    if (seq !== this.seq) { for (const t of stream.getTracks()) t.stop(); return; }
    this.mediaStream = stream;

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    let ws: WebSocket;
    try {
      ws = new WebSocket(`${proto}://${window.location.host}/api/voice/native`);
      ws.binaryType = 'arraybuffer';
    } catch (e: unknown) {
      this.cb.onUnavailable?.(`voice isn't configured yet: couldn't open the voice socket (${errText(e)})`);
      this.stopMic();
      this.setState('error');
      return;
    }
    this.ws = ws;

    // Per-socket call state: a stale socket's late events never touch a newer call.
    let convId: string | null = null;
    let startedAt = 0;
    let wentLive = false;
    let hungUp = false;
    let refused = false;
    let journaled = false;
    let rows: TranscriptState = emptyTranscript();
    const seenTools = new Set<string>();
    let lastContext = '';
    const mine = () => this.ws === ws;

    const emit = (fx: TranscriptEffect[]) => {
      if (!mine()) return;
      for (const e of fx) {
        if (e.kind === 'final') this.cb.onFinal?.(e.text, e.who);
        else this.cb.onPartial?.(e.text, e.who);
      }
    };
    const sendContext = (route: string) => {
      const f = contextFrame(route);
      if (!f || f === lastContext || ws.readyState !== WebSocket.OPEN) return;
      lastContext = f;
      try { ws.send(f); } catch { /* closing */ }
    };
    this.contextSender = sendContext;

    /** Journal the call once (only a call that went live has a conversation id), then name its card. */
    const journal = async (): Promise<void> => {
      if (journaled || !convId) return;
      journaled = true;
      const end = closeAll(rows);
      rows = end.state;
      emit(end.effects);
      const turns = journalTurns(rows);
      try {
        const r = await fetch('/api/voice/call-ended', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ started_at: startedAt / 1000, ended_at: Date.now() / 1000, turns, conv_id: convId }),
          signal: AbortSignal.timeout(8000),
        });
        const d = await r.json().catch(() => null) as { ok?: boolean; call_id?: string; error?: string } | null;
        if (r.ok && d?.ok && typeof d.call_id === 'string') {
          this.cb.onCallEnded?.(buildVoiceCallMarker(d.call_id), d.call_id);
        } else {
          this.cb.onUnavailable?.(`the call's transcript was not saved (${d?.error || `HTTP ${r.status}`})`);
        }
      } catch (e: unknown) {
        this.cb.onUnavailable?.(`the call's transcript was not saved (${errText(e)})`);
      }
    };

    const teardown = () => {
      if (!mine()) return;
      this.ws = null;
      this.contextSender = null;
      this.hangupHook = null;
      this.muted = false;
      this.stopMic();
      this.closePlayback();
      this.setState('idle');
    };

    ws.addEventListener('open', () => {
      try { ws.send(START_FRAME); } catch { /* closing */ }
      if (!mine()) return;
      // startMic is not awaited by anyone: a failure in it (a context rate the resampler cannot use, a node the
      // engine refuses) must still end the call loudly, never leave it open and silent.
      this.startMic(ws).catch((e: unknown) => {
        if (!mine()) return;
        this.cb.onUnavailable?.(`the microphone can't be used for this call (${errText(e)})`);
        this.stop();
      });
    });

    ws.addEventListener('message', (ev) => {
      if (typeof ev.data !== 'string') { if (mine()) this.play(ev.data as ArrayBuffer); return; }
      const f = parseNativeFrame(ev.data);
      if (!f) return;
      switch (f.type) {
        case 'state':
          if (!wentLive) {
            wentLive = true;
            convId = f.conversation_id || null;
            startedAt = Date.now();
            if (mine()) {
              this.setState('live');
              if (convId) this.cb.onConnected?.(convId);
            }
            sendContext(opts.route);
          }
          return;
        case 'transcript': case 'audio_end': {
          const r = reduceTranscript(rows, f, Date.now());
          rows = r.state;
          emit(r.effects);
          return;
        }
        case 'tools':
          if (!mine()) return;
          for (const c of newToolCalls(seenTools, f)) {
            const name = String(c.name || 'tool');
            this.cb.onTool?.({ phase: 'start', callId: c.call_id, name, argsSummary: argsSummary(c.args) });
            this.cb.onTool?.({ phase: 'done', callId: c.call_id, name, ok: c.ok !== false,
              summary: c.dup_hits ? `ran ${c.dup_hits + 1} times` : undefined });
          }
          return;
        case 'barge':
          if (f.cut) this.flushPlayback();          // cut:false is advisory only: keep playing
          return;
        case 'notice':
          if (mine()) this.cb.onUnavailable?.(f.text);
          return;
        case 'refused':
          refused = true;
          if (mine()) this.cb.onUnavailable?.(f.reason);
          try { ws.close(1000, 'refused'); } catch { /* closing */ }
          return;
        case 'stopped':
          // The server ended it: say why, unless it is only echoing our own hang-up.
          if (!hungUp && mine() && f.reason && f.reason.trim() && f.reason.trim() !== 'you hung up') {
            this.cb.onUnavailable?.(f.reason.trim());
          }
          try { ws.close(1000, 'stopped'); } catch { /* closing */ }
          return;
        default:
          return;                                   // tick and anything newer: nothing to show
      }
    });

    let closedOnce = false;
    const onClose = (code: number, reason: string) => {
      if (closedOnce) return;
      closedOnce = true;
      if (!mine()) { void journal(); return; }      // hung up already: the hang-up path journals
      if (!hungUp && !refused && shouldFallBack(code, wentLive) && this.cb.onFallback) {
        // Native is down or switched off: the engine switch starts the relay. Say nothing; no state change here.
        this.ws = null;
        this.contextSender = null;
        this.stopMic();
        this.cb.onFallback(reason || `code ${code}`);
        return;
      }
      const msg = refused ? null : closeMessage(code, reason, hungUp);
      if (msg) this.cb.onUnavailable?.(msg);
      void journal();
      teardown();
    };
    ws.addEventListener('close', (ev) => onClose(ev.code, ev.reason));
    ws.addEventListener('error', () => { /* a 'close' always follows; it carries the code */ });

    // The caller hangs up: journal first, then disconnect, then close on `stopped` / the server's close / 6 s.
    this.hangupHook = () => {
      hungUp = true;
      const sock = ws;
      const end = closeAll(rows);                   // the lines still open become turns, as spoken so far
      rows = end.state;
      emit(end.effects);
      teardown();                                   // the UI ends the call now; the rest happens in the background
      if (sock.readyState !== WebSocket.OPEN) { try { sock.close(); } catch { /* noop */ } return; }
      void journal().finally(() => {
        if (sock.readyState !== WebSocket.OPEN) return;
        try { sock.send(DISCONNECT_FRAME); } catch { /* closing */ }
        const t = setTimeout(() => { try { sock.close(1000, 'hung up'); } catch { /* noop */ } }, HANGUP_WAIT_MS);
        sock.addEventListener('close', () => clearTimeout(t));
      });
    };
  }

  private contextSender: ((route: string) => void) | null = null;
  private hangupHook: (() => void) | null = null;

  sendContext(opts: VoiceSessionStartOptions): void {
    this.contextSender?.(opts.route);
  }

  setMuted(muted: boolean): void {
    this.muted = muted;                             // the uplink keeps flowing: zero frames while muted
  }

  get isMuted(): boolean { return this.muted; }

  stop(): void {
    this.seq++;
    if (this.hangupHook) { const h = this.hangupHook; this.hangupHook = null; h(); return; }
    // No socket yet (still asking for the mic): release whatever there is.
    try { this.ws?.close(); } catch { /* noop */ }
    this.ws = null;
    this.muted = false;
    this.stopMic();
    this.closePlayback();
    this.setState('idle');
  }

  /** The native server captions the caller itself (echo-cancelled call audio): no on-device recognizer. */
  startDictation(): void { /* by design */ }
  stopDictation(): void { /* by design */ }

  // ── audio ──────────────────────────────────────────────────────────────────────────────────────────────────────

  private async startMic(ws: WebSocket): Promise<void> {
    if (!this.mediaStream) return;
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext!;
    const ctx: AudioContext = new Ctor();
    this.audioCtx = ctx;
    const source = ctx.createMediaStreamSource(this.mediaStream);
    this.sourceNode = source;
    const uplink = new MicUplink(ctx.sampleRate);
    const onBlock = (block: Float32Array) => {
      if (ws.readyState !== WebSocket.OPEN || this.ws !== ws) return;
      for (const frame of uplink.push(block, this.muted)) {
        try { ws.send(frame); } catch { return; }
      }
    };
    const silence = ctx.createGain();
    silence.gain.value = 0;
    silence.connect(ctx.destination);
    try {
      const m = ctx.createAnalyser();
      m.fftSize = 512;
      source.connect(m);
      this.micAnalyser = m;
    } catch { this.micAnalyser = null; }
    let node: AudioWorkletNode | ScriptProcessorNode | null = null;
    if (ctx.audioWorklet && typeof AudioWorkletNode !== 'undefined') {
      const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: 'application/javascript' }));
      try {
        await ctx.audioWorklet.addModule(url);
        if (this.audioCtx !== ctx) return;          // the call ended while the module loaded
        const w = new AudioWorkletNode(ctx, 'oos-mic-tap', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
        w.port.onmessage = (e) => onBlock(e.data as Float32Array);
        node = w;
      } catch {
        node = null;                                // a blocked Blob module or an old engine: ScriptProcessor below
      } finally {
        URL.revokeObjectURL(url);
      }
    }
    if (this.audioCtx !== ctx) return;
    if (!node) {
      const sp = ctx.createScriptProcessor(4096, 1, 1);
      sp.onaudioprocess = (e) => onBlock(e.inputBuffer.getChannelData(0));
      node = sp;
    }
    source.connect(node);
    node.connect(silence);
    this.captureNode = node;
    if (ctx.state === 'suspended') void ctx.resume().catch(() => {});
  }

  private stopMic() {
    try { this.captureNode?.disconnect(); } catch { /* noop */ }
    if (this.captureNode && 'port' in this.captureNode) this.captureNode.port.onmessage = null;
    try { this.sourceNode?.disconnect(); } catch { /* noop */ }
    try { void this.audioCtx?.close(); } catch { /* noop */ }
    this.captureNode = null;
    this.sourceNode = null;
    this.micAnalyser = null;
    this.audioCtx = null;
    for (const t of this.mediaStream?.getTracks() ?? []) t.stop();
    this.mediaStream = null;
  }

  /** Arturo's voice: 16 kHz s16le, already paced by the server; queued back to back so it plays without gaps. */
  private play(buf: ArrayBuffer) {
    const samples = leBytesToFloat(buf);
    if (!samples.length) return;
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext!;
    if (!this.playbackCtx) {
      this.playbackCtx = new Ctor();
      this.playbackTime = this.playbackCtx.currentTime;
      try {
        const a = this.playbackCtx.createAnalyser();
        a.fftSize = 512;
        a.connect(this.playbackCtx.destination);
        this.playbackAnalyser = a;
      } catch { this.playbackAnalyser = null; }
    }
    const ctx = this.playbackCtx;
    if (ctx.state === 'suspended') void ctx.resume().catch(() => {});
    // createBuffer at 16 kHz: the context resamples to its own rate on playback (no 16 kHz context needed).
    const ab = ctx.createBuffer(1, samples.length, NATIVE_RATE);
    ab.copyToChannel(samples, 0);
    const src = ctx.createBufferSource();
    src.buffer = ab;
    src.connect(this.playbackAnalyser ?? ctx.destination);
    const first = this.playing.size === 0;
    if (first) this.cb.onPlayback?.(true);
    this.playing.add(src);
    src.onended = () => { this.playing.delete(src); if (this.playing.size === 0) this.cb.onPlayback?.(false); };
    // The first chunk after silence starts a little ahead, so network jitter on the next one does not open a gap.
    const lead = first ? PLAYBACK_LEAD_S : 0;
    const at = Math.max(ctx.currentTime + lead, this.playbackTime);
    src.start(at);
    this.playbackTime = at + ab.duration;
  }

  /** The call is over: its playback context is closed, never left open for the next call to pile up beside. */
  private closePlayback() {
    this.flushPlayback();
    const ctx = this.playbackCtx;
    this.playbackCtx = null;
    this.playbackAnalyser = null;
    this.playbackTime = 0;
    if (ctx) { try { void ctx.close().catch(() => {}); } catch { /* already closed */ } }
  }

  private flushPlayback() {
    for (const src of this.playing) { try { src.stop(); } catch { /* ended */ } }
    const had = this.playing.size > 0;
    this.playing.clear();
    if (had) this.cb.onPlayback?.(false);
    if (this.playbackCtx) this.playbackTime = this.playbackCtx.currentTime;
  }
}
