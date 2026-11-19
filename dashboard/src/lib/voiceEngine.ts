/**
 * voiceEngine.ts — which engine runs a hands-free call: native GPT-Live (lib/nativeVoiceSession.ts, the iPhone
 * app's engine) when GET /api/voice/native/ready says so, else the relay (lib/voiceSession.ts). Both speak the same
 * CallSession + VoiceSessionCallbacks interface, so lib/handsFree.ts and the UI above it never know which one ran.
 *
 * A native call refused before it went live because the native service is down or switched off (4502 / 4503) is
 * started again on the relay, once, with the same options. A `refused` frame from Arturo is NOT retried on the relay:
 * it is Arturo saying no, and the caller is told why.
 */
import type { CallSession } from './handsFree.ts';
import type { VoiceSessionCallbacks, VoiceSessionStartOptions } from './voiceSession.ts';
import { chooseEngine, type VoiceEngine } from './nativeVoice.ts';

export interface NativeCallbacks extends VoiceSessionCallbacks {
  /** The native socket was refused before the call went live in a way the relay can cover. */
  onFallback?: (reason: string) => void;
}

export interface EngineDeps {
  makeNative: (cb: NativeCallbacks) => CallSession;
  makeRelay: (cb: VoiceSessionCallbacks) => CallSession;
  ready: () => Promise<{ ok: boolean; reason?: string } | null>;
  log?: (msg: string) => void;
  /** Which engine this call runs on, as soon as it is chosen (and again on a fall-back): the live card names it. */
  onEngine?: (engine: VoiceEngine | null) => void;
}

export class EngineSwitch implements CallSession {
  engine: VoiceEngine | null = null;
  private inner: CallSession | null = null;
  private seq = 0;
  /** handsFree asked for the call's on-device captions (a no-op on native, which captions the caller itself). */
  private wantDictation = false;
  /** The caller's mute, kept here too: a call handed to the relay mid-start stays muted if it was. */
  private muted = false;
  private readonly cb: VoiceSessionCallbacks;
  private readonly deps: EngineDeps;

  constructor(cb: VoiceSessionCallbacks, deps: EngineDeps) {
    this.cb = cb;
    this.deps = deps;
  }

  async start(opts: VoiceSessionStartOptions): Promise<void> {
    const seq = ++this.seq;
    this.inner = null;                             // a new call: the last one's session is done with
    this.muted = false;
    this.engine = null;
    this.cb.onStateChange?.('connecting');        // the ready check takes a moment: the call is already starting
    const ready = opts.agentId ? null : await this.deps.ready().catch(() => null);
    if (seq !== this.seq) return;                  // hung up while asking
    const engine = chooseEngine(ready, opts.agentId);
    this.deps.log?.(`[voice] engine: ${engine}${engine === 'relay' && ready && !ready.ok ? ` (native: ${ready.reason || 'not ready'})` : ''}`);
    await this.run(engine, opts, seq);
  }

  private async run(engine: VoiceEngine, opts: VoiceSessionStartOptions, seq: number): Promise<void> {
    this.engine = engine;
    this.deps.onEngine?.(engine);
    if (engine === 'relay') {
      this.inner = this.deps.makeRelay(this.cb);
      await this.inner.start(opts);
      return;
    }
    let fellBack = false;
    const native = this.deps.makeNative({
      ...this.cb,
      onFallback: (reason) => {
        if (fellBack || seq !== this.seq || this.inner !== native) return;
        fellBack = true;
        this.deps.log?.(`[voice] engine: relay (native refused before the call went live: ${reason})`);
        void this.run('relay', opts, seq).then(() => {
          // The relay captions the caller on-device unless its bridge says it does: hand it the Dictate the call had.
          if (seq !== this.seq) return;
          if (this.wantDictation) this.inner?.startDictation();
          if (this.muted) this.inner?.setMuted?.(true);
        });
      },
    });
    this.inner = native;
    await native.start(opts);
  }

  stop(): void {
    this.seq++;
    this.wantDictation = false;
    this.muted = false;
    if (this.inner) this.inner.stop();
    else this.cb.onStateChange?.('idle');          // stopped during the ready check: nothing else will say so
  }

  startDictation(): void { this.wantDictation = true; this.inner?.startDictation(); }
  stopDictation(): void { this.wantDictation = false; this.inner?.stopDictation(); }
  sendContext(opts: VoiceSessionStartOptions): void { this.inner?.sendContext?.(opts); }
  setMuted(muted: boolean): void { this.muted = muted; this.inner?.setMuted?.(muted); }
}
