import { useCallback, useEffect, useRef, useState } from 'react';
import { ConversationProvider, useConversation } from '@elevenlabs/react';
import { Mic, MicOff, PhoneOff, Volume2, Minimize2 } from 'lucide-react';
import { clsx } from 'clsx';
import { VOICE_AGENTS } from '../lib/constants';

interface Props {
  pmId: string;
  agentId: string;
  onClose: () => void;
}

function VoiceCallInner({ pmId, agentId, onClose }: Props) {
  const [callDuration, setCallDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [minimized, setMinimized] = useState(false);
  const [muted, setMuted] = useState(false);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const closedRef = useRef(false);
  const meta = VOICE_AGENTS[pmId];

  const conversation = useConversation({
    onConnect: () => console.log('[VoiceCall] Connected to', pmId),
    onDisconnect: () => {
      console.log('[VoiceCall] Disconnected');
      if (!closedRef.current) {
        closedRef.current = true;
        onClose();
      }
    },
    onError: (err) => {
      console.error('[VoiceCall] Error:', err);
      setError(typeof err === 'string' ? err : 'Connection error');
    },
  });

  const startCall = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioStreamRef.current = stream;
      await conversation.startSession({ agentId });
    } catch (err) {
      console.error('Failed to start call:', err);
      setError(err instanceof Error ? err.message : 'Failed to start call');
    }
  }, [agentId, conversation]);

  const toggleMute = useCallback(() => {
    const newMuted = !muted;
    // Mute mic via ElevenLabs SDK — stops Jarvis from hearing the operator
    try {
      // SDK v1.0.x uses setMuted (not setMicMuted)
      if (typeof conversation.setMuted === 'function') {
        conversation.setMuted(newMuted);
      } else if (typeof (conversation as any).setMicMuted === 'function') {
        (conversation as any).setMicMuted(newMuted);
      }
    } catch {}
    // Also disable raw audio tracks as belt-and-suspenders
    if (audioStreamRef.current) {
      audioStreamRef.current.getAudioTracks().forEach(track => {
        track.enabled = !newMuted;
      });
    }
    setMuted(newMuted);
  }, [muted, conversation]);

  useEffect(() => {
    startCall();
    return () => { conversation.endSession(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (conversation.status !== 'connected') return;
    const interval = setInterval(() => setCallDuration(d => d + 1), 1000);
    return () => clearInterval(interval);
  }, [conversation.status]);

  const formatDuration = (s: number) =>
    `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`;

  const handleEnd = useCallback(() => {
    if (closedRef.current) return;
    closedRef.current = true;
    conversation.endSession();
    onClose();
  }, [conversation, onClose]);

  const isConnected = conversation.status === 'connected';
  const isSpeaking = conversation.isSpeaking;

  // Minimized floating bubble
  if (minimized) {
    return (
      <div className="fixed bottom-4 right-4 z-50 flex items-center gap-2">
        {/* Pulsing orb */}
        <button
          onClick={() => setMinimized(false)}
          className={clsx(
            'w-14 h-14 rounded-full flex items-center justify-center shadow-xl transition-all',
            isConnected
              ? isSpeaking
                ? 'bg-green-600 scale-110 shadow-green-500/40 animate-pulse'
                : 'bg-green-700 shadow-green-500/20'
              : 'bg-neutral-700 animate-pulse'
          )}
        >
          {isSpeaking ? <Volume2 size={22} className="text-white" /> : <Mic size={22} className="text-white" />}
        </button>

        {/* Info pill */}
        <div className="bg-neutral-900 border border-neutral-700 rounded-full px-3 py-1.5 flex items-center gap-2 shadow-xl">
          <span className="text-xs text-neutral-300 font-medium">{meta?.label || pmId}</span>
          <span className="text-xs text-neutral-500">{formatDuration(callDuration)}</span>
          <button onClick={toggleMute} className={clsx('ml-1', muted ? 'text-amber-400 hover:text-amber-300' : 'text-neutral-500 hover:text-neutral-300')}>
            {muted ? <MicOff size={14} /> : <Mic size={14} />}
          </button>
          <button onClick={handleEnd} className="text-red-400 hover:text-red-300">
            <PhoneOff size={14} />
          </button>
        </div>
      </div>
    );
  }

  // Expanded floating panel (bottom-right, not full screen)
  return (
    <div className="fixed bottom-4 right-4 z-50 w-72 bg-neutral-900 border border-neutral-700 rounded-2xl shadow-2xl shadow-black/50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800">
        <div className="flex items-center gap-2">
          <span className={clsx('w-2 h-2 rounded-full', isConnected ? 'bg-green-500' : 'bg-amber-500 animate-pulse')} />
          <span className="text-sm font-medium">{meta?.label || pmId}</span>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => setMinimized(true)} className="p-1 text-neutral-500 hover:text-neutral-300">
            <Minimize2 size={14} />
          </button>
        </div>
      </div>

      {/* Call content */}
      <div className="p-4 text-center">
        {/* Orb */}
        <div className="relative mx-auto w-16 h-16 mb-3">
          <div className={clsx(
            'w-16 h-16 rounded-full flex items-center justify-center transition-all duration-300',
            isConnected
              ? isSpeaking
                ? 'bg-green-900/60 text-green-400 scale-110 shadow-lg shadow-green-500/20'
                : 'bg-green-900/30 text-green-500'
              : conversation.status === 'error'
                ? 'bg-red-900/30 text-red-500'
                : 'bg-neutral-800 text-neutral-500 animate-pulse'
          )}>
            {isSpeaking ? <Volume2 size={24} className="animate-pulse" /> : <Mic size={24} />}
          </div>
        </div>

        <p className="text-xs text-neutral-500">
          {isConnected ? `Connected  ${formatDuration(callDuration)}` :
           conversation.status === 'connecting' ? 'Connecting...' :
           conversation.status === 'error' ? 'Failed' : 'Initializing...'}
        </p>

        {isConnected && (
          <p className={clsx('mt-1 text-[10px]', isSpeaking ? 'text-green-400' : 'text-neutral-600')}>
            {isSpeaking ? 'Speaking...' : 'Listening...'}
          </p>
        )}

        {error && <p className="mt-2 text-[10px] text-red-400">{error}</p>}
      </div>

      {/* Controls */}
      <div className="px-4 pb-4 flex justify-center gap-3">
        <button
          onClick={toggleMute}
          className={clsx(
            'px-4 py-2 min-h-[44px] rounded-full text-sm font-medium inline-flex items-center gap-2 transition-colors',
            muted
              ? 'bg-amber-600 hover:bg-amber-500 text-white'
              : 'bg-neutral-700 hover:bg-neutral-600 text-neutral-200'
          )}
        >
          {muted ? <MicOff size={16} /> : <Mic size={16} />}
          {muted ? 'Unmute' : 'Mute'}
        </button>
        <button
          onClick={handleEnd}
          className="px-5 py-2 min-h-[44px] rounded-full bg-red-600 hover:bg-red-500 text-white text-sm font-medium inline-flex items-center gap-2"
        >
          <PhoneOff size={16} />
          End
        </button>
      </div>
    </div>
  );
}

export function VoiceCallBubble(props: Props) {
  return (
    <ConversationProvider>
      <VoiceCallInner {...props} />
    </ConversationProvider>
  );
}

// Keep old name for backward compat
export const VoiceCallModal = VoiceCallBubble;
