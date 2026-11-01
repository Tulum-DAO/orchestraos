import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Mic } from 'lucide-react';
import { useAgentSettings } from '../../stores/agentSettings';
import { VoiceSession } from '../../lib/voiceSession';
import { useCallStore } from '../../stores/callStore';
import { clearForAgentCall } from '../../lib/switchCall';

interface VoiceControlsProps {
  /** Current route, for the page-aware VoiceLogo call (DEC §1: "carrying page context"). */
  route: string;
  focusedEntity?: string | null;
  /** Bound by the integrator to the composer's textarea value (live, sub-second). */
  onPartial: (text: string) => void;
  /** Committed dictation / call speech — appended to the textarea by the integrator. */
  onFinal?: (text: string) => void;
  /** `[voice-call: <id> <path>]` marker text to submit into the composer on call end (P1). */
  onCallEnded?: (marker: string) => void;
  /** Told whenever a call starts connecting or ends, so a caller can render for it. */
  onInCallChange?: (inCall: boolean) => void;
  /** The agent whose page this is: its call is for that agent (`agent=<id>` on the live call). */
  agentId?: string;
  /** Who the call is with, for the question asked before Arturo's live call is ended for this one. */
  agentName?: string;
  /** The agent's calls reach it (lib/agentCallsOn.ts); otherwise the call is Arturo's, and is marked so. */
  agentCalls?: boolean;
  /** Told when a call could not start, with why (a missing voice key, no microphone): the composer's round button
   *  then offers no Call, and says why. */
  onCallUnavailable?: (reason: string) => void;
}

/** The call, started and ended by the composer's round button (lib/roundAction.ts): this keeps the mic only. */
export interface VoiceControlsHandle {
  startCall: () => Promise<void>;
  /** Start a call, or end the one that is on. */
  toggleCall: () => Promise<void>;
}


/**
 * The composer's Mic button (hold/toggle per settings.micMode -> dictation
 * into the textarea) and the VoiceLogo call button (a full page-aware Gemini
 * Live session over the new `/api/voice/live` proxy). Every unavailable path
 * renders an honest first-person reason (W1) — never a silent no-op.
 */
export const VoiceControls = forwardRef<VoiceControlsHandle, VoiceControlsProps>(function VoiceControls({
  route,
  focusedEntity,
  onPartial,
  onFinal,
  onCallEnded,
  onInCallChange,
  onCallUnavailable,
  agentId,
  agentName,
  agentCalls = false,
}, ref) {
  const settings = useAgentSettings();
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [dictating, setDictating] = useState(false);
  const sessionRef = useRef<VoiceSession | null>(null);

  // Dictation stays this composer's own (a session that never opens a call). The CALL is the app's one call
  // (stores/callStore.ts): its bar, timer and waveform read it there, and it outlives this composer.
  function session(): VoiceSession {
    if (!sessionRef.current) {
      sessionRef.current = new VoiceSession({
        onPartial: (text, role) => { if (role === 'user') onPartial(text); },
        onFinal: (text, role) => { if (role === 'user') onFinal?.(text); },
        onUnavailable: (reason) => setUnavailable(reason),
      });
    }
    return sessionRef.current;
  }

  useEffect(() => () => { sessionRef.current?.stopDictation(); }, []);
  const inCall = useCallStore((s) => !!s.call && s.call.agentId === agentId);
  useEffect(() => { onInCallChange?.(inCall); }, [inCall]); // eslint-disable-line react-hooks/exhaustive-deps

  const startDictation = () => { setUnavailable(null); setDictating(true); session().startDictation(); };
  const stopDictation = () => { setDictating(false); session().stopDictation(); };

  const micHandlers = settings.micMode === 'hold'
    ? {
      onMouseDown: startDictation,
      onMouseUp: stopDictation,
      onMouseLeave: () => { if (dictating) stopDictation(); },
      onTouchStart: startDictation,
      onTouchEnd: stopDictation,
    }
    : { onClick: () => (dictating ? stopDictation() : startDictation()) };

  const callListeners = {
    onPartial, onFinal, onCallEnded,
    onUnavailable: (reason: string) => { setUnavailable(reason); if (/voice isn't configured|voice bridge/i.test(reason)) onCallUnavailable?.(reason); },
  };
  // The store's own state, not the render's: a second tap in the same frame still sees the call and ends it.
  const toggleCall = async () => {
    setUnavailable(null);
    const store = useCallStore.getState();
    if (store.call && store.call.agentId === agentId) { store.hangUp(); return; }
    if (!agentId) return;
    if (!(await clearForAgentCall(agentName || agentId))) return;
    await useCallStore.getState().start({ route, focusedEntity, agentId, withArturo: !agentCalls }, callListeners);
  };

  const startCall = async () => {
    const store = useCallStore.getState();
    if (store.call || !agentId) return;
    setUnavailable(null);
    // Never switch silently: with Arturo's call up, ask first; only a yes ends it.
    if (!(await clearForAgentCall(agentName || agentId))) return;
    await useCallStore.getState().start({ route, focusedEntity, agentId, withArturo: !agentCalls }, callListeners);
  };
  useImperativeHandle(ref, () => ({ startCall, toggleCall }));

  return (
    <div className="flex items-center gap-1">
      {unavailable && (
        <div className="absolute bottom-full left-0 right-0 px-3 py-1.5 text-xs text-amber-500 bg-background/95 border-t border-border">
          {`I can't do that yet — ${unavailable}.`}
        </div>
      )}
      <button
        {...micHandlers}
        aria-pressed={dictating}
        aria-label="Microphone"
        title={settings.micMode === 'hold' ? 'Hold to talk' : 'Tap to talk'}
        className={`touch-circle flex items-center justify-center rounded-full transition-colors ${dictating ? 'bg-[var(--accent-voice)] text-background' : 'text-foreground/70 hover:text-foreground hover:bg-muted'}`}
      >
        <Mic size={18} />
      </button>
    </div>
  );
});
