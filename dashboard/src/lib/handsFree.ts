/**
 * handsFree.ts — Live voice mode (the live call where Arturo talks back), as ONE
 * controller that the home composer and the Arturo pane both drive. It was the pill's own
 * code; the home's button had no call behind it at all.
 *
 * The session is lib/voiceSession.ts (→ /api/voice/live → gateway /live → Gemini Live). Arturo's
 * words arrive as transcript frames; the caller's words are captioned on-device by the browser
 * (startDictation), because the gateway deliberately does not transcribe the caller.
 */
import type { VoiceSessionCallbacks, VoiceSessionStartOptions, VoiceSessionState } from './voiceSession.ts';

/** What the controller needs from a session (VoiceSession has exactly these). */
export interface CallSession {
  start(opts: VoiceSessionStartOptions): Promise<void>;
  stop(): void;
  startDictation(): void;
  stopDictation(): void;
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
}

export function isInCall(state: VoiceSessionState): boolean {
  return state === 'live' || state === 'connecting';
}

export class HandsFreeCall {
  state: VoiceSessionState = 'idle';
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
          if (st === 'idle' || st === 'error') this.session?.stopDictation();
          this.ev.onState?.(st);
        },
        onCallEnded: (marker, id) => this.ev.onEnded?.(`Call ended (${id}). ${marker}`),
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

  /** Unmount: never leave a mic or a socket open. */
  dispose(): void {
    this.session?.stop();
    this.session?.stopDictation();
  }
}
