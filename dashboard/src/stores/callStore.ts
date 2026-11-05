/**
 * callStore — THE app's one live call (one at a time), for every surface that shows it: the agent page's call bar,
 * later the page-top chip and the sidebar pill. It owns the voice session for an agent's call: who the call is with,
 * when it started, what it is doing (lib/callPhase.ts), whether you are muted, and the Web Audio analysers on your
 * microphone and the agent's playback (the waveform).
 *
 * Arturo's own call keeps its own experience (hooks/useHandsFreeCall.ts) and is not held here.
 *
 * A caller that starts a call hands its listeners (the words you say go into that composer, the end marker after the
 * call); they live with the call and go when it ends.
 */
import { create } from 'zustand';
import { VoiceSession, type VoiceSessionStartOptions } from '../lib/voiceSession';
import { nextPhase, type CallEvent, type CallPhase } from '../lib/callPhase';
import { addCaption, type CallTurn } from '../lib/callTranscript';

export interface CallListeners {
  onPartial?: (text: string) => void;
  onFinal?: (text: string) => void;
  /** The call ended: its marker, its id and how long it lasted (seconds). */
  onCallEnded?: (marker: string, callId: string, durationS: number) => void;
  onUnavailable?: (reason: string) => void;
}

export interface LiveCall {
  agentId: string;
  startedAt: number;
  phase: CallPhase;
  muted: boolean;
  /** The call's words so far, turn by turn (lib/callTranscript.ts). */
  turns: CallTurn[];
  /** Who answers: the agent itself, or Arturo while its agent calls are off (lib/agentCallsOn.ts). Every surface
   *  names this one. */
  party: 'agent' | 'arturo';
}

/** Where the call bar reads loudness: the two analysers, or a stand-in with the same shape (tests). */
export interface CallLevels { mic: () => AnalyserNode | null; playback: () => AnalyserNode | null }

interface CallState {
  call: LiveCall | null;
  /** Start a call with this agent. Refused ('busy') while another call is live: one call at a time. */
  start: (opts: VoiceSessionStartOptions & { agentId: string; withArturo?: boolean }, listeners?: CallListeners) => Promise<'started' | 'busy'>;
  hangUp: () => void;
  setMuted: (muted: boolean) => void;
  /** Feed an event into the phase (the session does; tests do). */
  event: (e: CallEvent) => void;
}

let session: VoiceSession | null = null;
let gapTimer: ReturnType<typeof setTimeout> | null = null;
/** How long the agent's voice must be silent before the call is your turn again. */
export const PLAYBACK_GAP_MS = 350;

/** The live call's analysers (null when there is none or a stream has not started). */
export const callLevels: CallLevels = {
  mic: () => session?.micAnalyser ?? null,
  playback: () => session?.playbackAnalyser ?? null,
};

export const useCallStore = create<CallState>((set, get) => ({
  call: null,
  event: (e) => set((s) => {
    if (!s.call) return s;
    const phase = nextPhase(s.call.phase, e);
    if (phase === 'ended') { session = null; return { call: null }; }
    return phase === s.call.phase ? s : { call: { ...s.call, phase } };
  }),
  start: async (opts, l = {}) => {
    if (get().call) return 'busy';
    // The listeners live with THIS call (its end marker arrives after the call is over).
    const mine = new VoiceSession({
      onStateChange: (st) => { if (session === mine) get().event({ type: 'session', state: st }); },
      onPartial: (text, role) => {
        if (session !== mine) return;
        caption(role === 'user' ? 'user' : 'agent', text, false);
        if (role !== 'user') return;
        l.onPartial?.(text); get().event({ type: 'userSpeaking' });
      },
      onFinal: (text, role) => {
        if (session !== mine) return;
        caption(role === 'user' ? 'user' : 'agent', text, true);
        if (role !== 'user') return;
        l.onFinal?.(text); get().event({ type: 'userDone' });
      },
      // The agent's voice arrives in short chunks with hairline gaps between them: it has stopped talking only once
      // nothing has played for PLAYBACK_GAP_MS (no flicker between talking and listening mid-sentence).
      onPlayback: (playing) => {
        if (session !== mine) return;
        if (gapTimer) { clearTimeout(gapTimer); gapTimer = null; }
        if (playing) get().event({ type: 'playback', playing: true });
        else gapTimer = setTimeout(() => { gapTimer = null; if (session === mine) get().event({ type: 'playback', playing: false }); }, PLAYBACK_GAP_MS);
      },
      onUnavailable: (reason) => l.onUnavailable?.(reason),
      onCallEnded: (marker, id) => l.onCallEnded?.(marker, id, (Date.now() - startedAt) / 1000),
    });
    session = mine;
    const caption = (role: 'user' | 'agent', text: string, final: boolean) =>
      set((s) => (s.call ? { call: { ...s.call, turns: addCaption(s.call.turns, role, text, final) } } : s));
    const { withArturo, ...sessionOpts } = opts;
    const startedAt = Date.now();
    set({ call: { agentId: opts.agentId, startedAt, phase: 'connecting', muted: false, turns: [], party: withArturo ? 'arturo' : 'agent' } });
    await mine.start(sessionOpts);
    return 'started';
  },
  hangUp: () => { session?.stop(); },
  setMuted: (muted) => {
    session?.setMuted(muted);
    set((s) => (s.call ? { call: { ...s.call, muted } } : s));
  },
}));
