import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Mic } from 'lucide-react';
import { useAgentSettings } from '../../stores/agentSettings';
import { VoiceSession } from '../../lib/voiceSession';

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
}, ref) {
  const settings = useAgentSettings();
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [dictating, setDictating] = useState(false);
  const sessionRef = useRef<VoiceSession | null>(null);

  function session(): VoiceSession {
    if (!sessionRef.current) {
      sessionRef.current = new VoiceSession({
        onPartial: (text, role) => { if (role === 'user') onPartial(text); },
        onFinal: (text, role) => { if (role === 'user') onFinal?.(text); },
        onUnavailable: (reason) => { setUnavailable(reason); if (/voice isn't configured|voice bridge/i.test(reason)) onCallUnavailable?.(reason); },
        onStateChange: (s) => { onInCallChange?.(s === 'live' || s === 'connecting'); },
        onCallEnded: (marker) => onCallEnded?.(marker),
      });
    }
    return sessionRef.current;
  }

  useEffect(() => () => { sessionRef.current?.stop(); sessionRef.current?.stopDictation(); }, []);

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

  const toggleCall = async () => {
    setUnavailable(null);
    // The session's own state, not the render's `inCall`: a second tap in the same frame as the
    // first still sees the call as connecting and cancels it rather than starting it twice.
    const st = sessionRef.current?.state;
    if (st === 'connecting' || st === 'live') {
      session().stop();
      return;
    }
    await session().start({ route, focusedEntity });
  };

  const startCall = async () => {
    const st = sessionRef.current?.state;
    if (st === 'connecting' || st === 'live') return;
    setUnavailable(null);
    await session().start({ route, focusedEntity });
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
