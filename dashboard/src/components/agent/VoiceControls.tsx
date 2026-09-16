import { useEffect, useRef, useState } from 'react';
import { Mic, PhoneCall, PhoneOff } from 'lucide-react';
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
  /** true when the textarea is empty and there's no attachment — VoiceLogo shows; else hidden (Send owns that slot). */
  showCallButton: boolean;
}

/**
 * The composer's Mic button (hold/toggle per settings.micMode -> dictation
 * into the textarea) and the VoiceLogo call button (a full page-aware Gemini
 * Live session over the new `/api/voice/live` proxy). Every unavailable path
 * renders an honest first-person reason (W1) — never a silent no-op.
 */
export function VoiceControls({
  route,
  focusedEntity,
  onPartial,
  onFinal,
  onCallEnded,
  showCallButton,
}: VoiceControlsProps) {
  const settings = useAgentSettings();
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [dictating, setDictating] = useState(false);
  const [inCall, setInCall] = useState(false);
  const sessionRef = useRef<VoiceSession | null>(null);

  function session(): VoiceSession {
    if (!sessionRef.current) {
      sessionRef.current = new VoiceSession({
        onPartial: (text, role) => { if (role === 'user') onPartial(text); },
        onFinal: (text, role) => { if (role === 'user') onFinal?.(text); },
        onUnavailable: (reason) => setUnavailable(reason),
        onStateChange: (s) => setInCall(s === 'live' || s === 'connecting'),
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
    if (inCall) {
      session().stop();
      return;
    }
    await session().start({ route, focusedEntity });
  };

  return (
    <div className="flex items-center gap-2">
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
        className={`p-2 rounded-lg transition-colors ${dictating ? 'bg-[var(--accent-voice)] text-white' : 'text-foreground hover:bg-muted'}`}
      >
        <Mic size={20} />
      </button>
      {showCallButton && (
        <button
          onClick={toggleCall}
          aria-pressed={inCall}
          aria-label={inCall ? 'End call' : 'Start voice call'}
          title={inCall ? `End call with ${settings.assistantName}` : `Call ${settings.assistantName}`}
          className="p-2 rounded-lg transition-colors text-foreground hover:bg-muted"
          style={{ color: inCall ? 'var(--accent-voice)' : undefined }}
        >
          {inCall ? <PhoneOff size={20} /> : <PhoneCall size={20} />}
        </button>
      )}
    </div>
  );
}
