/**
 * handsFree.ts — Live voice mode (the live call where Arturo talks back), as ONE
 * controller that the home composer and the Arturo pane both drive. It was the pill's own
 * code; the home's button had no call behind it at all.
 *
 * The session is lib/voiceSession.ts (→ /api/voice/live → gateway /live → Gemini Live). Arturo's
 * words arrive as transcript frames; the caller's words are captioned on-device by the browser
 * (startDictation), because the gateway deliberately does not transcribe the caller.
 */
import type { CallToolEvent, VoiceSessionCallbacks, VoiceSessionStartOptions, VoiceSessionState } from './voiceSession.ts';

/** What the controller needs from a session (VoiceSession has exactly these). */
export interface CallSession {
  start(opts: VoiceSessionStartOptions): Promise<void>;
  stop(): void;
  startDictation(): void;
  stopDictation(): void;
  /** The page changed mid-call: tell the bridge, so Arturo's screen context follows you. */
  sendContext?(opts: VoiceSessionStartOptions): void;
  /** Mute the caller's microphone (uplink only: Arturo keeps talking and captions keep coming). */
  setMuted?(muted: boolean): void;
}

export interface HandsFreeEvents {
  /** The call's state changed (idle / connecting / live / error). */
  onState?: (state: VoiceSessionState) => void;
  /** A live caption grew: the caller's words (`user`) or Arturo's. */
  onPartial?: (text: string, role: 'user' | 'arturo') => void;
  /** A caption became final: commit it as a turn. */
  onFinal?: (text: string, role: 'user' | 'arturo') => void;
  /** The call could not start or broke: an honest first-person reason, never silence. */
  onUnavailable?: (reason: string) => void;
  /** The call ended; `text` is the line to leave in the thread. */
  onEnded?: (text: string) => void;
  /** A tool Arturo runs during the call (start, then done): a tool card in the thread. */
  onTool?: (e: CallToolEvent) => void;
  /** The caller muted or unmuted (also false when a call ends: the next call starts unmuted). */
  onMuted?: (muted: boolean) => void;
}

export function isInCall(state: VoiceSessionState): boolean {
  return state === 'live' || state === 'connecting';
}

export class HandsFreeCall {
  state: VoiceSessionState = 'idle';
  muted = false;
  private session: CallSession | null = null;
  private readonly make: (cb: VoiceSessionCallbacks) => CallSession;
  private readonly ev: HandsFreeEvents;

  constructor(make: (cb: VoiceSessionCallbacks) => CallSession, ev: HandsFreeEvents = {}) {
    this.make = make;
    this.ev = ev;
  }

  private get s(): CallSession {
    if (!this.session) {
      this.session = this.make({
        onPartial: (text, role) => this.ev.onPartial?.(text, role),
        onFinal: (text, role) => this.ev.onFinal?.(text, role),
        onUnavailable: (reason) => this.ev.onUnavailable?.(reason),
        onStateChange: (st) => {
          this.state = st;
          // The call is over (ended, refused or failed): its on-device captions end with it, so a later
          // Dictate tap never fights a stale recognizer ("dictation error: aborted").
          if (st === 'idle' || st === 'error') {
            this.session?.stopDictation();
            if (this.muted) { this.muted = false; this.ev.onMuted?.(false); }
          }
          this.ev.onState?.(st);
        },
        onCallEnded: (marker, id) => this.ev.onEnded?.(`Call ended (${id}). ${marker}`),
        onTool: (e) => this.ev.onTool?.(e),
      });
    }
    return this.session;
  }

  get inCall(): boolean {
    return isInCall(this.state);
  }

  /** Start the call, or end it if one is up. Captions start only for a call that actually opened;
   *  a refused call has already said why through onUnavailable. */
  async toggle(opts: VoiceSessionStartOptions): Promise<void> {
    if (this.inCall) { this.s.stop(); this.s.stopDictation(); return; }
    await this.s.start(opts);
    if (this.inCall) this.s.startDictation();
  }

  /** The page (route + focused entity) changed during the call. No call, nothing to send. */
  setContext(opts: VoiceSessionStartOptions): void {
    if (this.inCall) this.session?.sendContext?.(opts);
  }

  /** Mute or unmute the caller during a call. Outside a call there is nothing to mute. */
  setMuted(muted: boolean): void {
    if (!this.inCall || muted === this.muted) return;
    this.muted = muted;
    this.session?.setMuted?.(muted);
    this.ev.onMuted?.(muted);
  }

  toggleMute(): void {
    this.setMuted(!this.muted);
  }

  /** Unmount: never leave a mic or a socket open. */
  dispose(): void {
    this.session?.stop();
    this.session?.stopDictation();
  }
}
