/**
 * CallBar — a live call, on the page of the agent you are calling: a floating bar pinned at the top of the chat, not
 * part of the thread (the thread scrolls under it).
 *
 *   left    the agent's own avatar in its look, showing the call (listening, thinking: the melt into three dots,
 *           talking), and the call's time
 *   middle  the rolling waveform (CallWaveform) in the agent's colour: you full colour, the agent a muted shade
 *   right   settings, mute, and a red hang-up
 *   width   a phone's width on every screen (the desktop bar was too wide), centred
 *   under   a small drag handle: a tap, or pulling it down, opens the call's transcript (CallTranscript) in a window
 *           that drops down under the bar, on a phone as on a desktop (an expandable chip, never full
 *           screen); pulling it up, or a tap, closes it.
 *
 * Everything comes from the app's one call (stores/callStore.ts).
 */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Mic, MicOff, PhoneOff, Settings } from 'lucide-react';
import CallTranscript from './CallTranscript';
import { AgentAvatar } from '../avatar/AgentAvatar';
import { resolveColor, shade } from '../avatar/avatarCore';
import { avatarStateForCall } from '../../lib/callPhase';
import { callLevels, useCallStore, type CallLevels } from '../../stores/callStore';
import { useCallParty } from './useCallParty';
import { styleBeforeDev, useChatStyle } from '../../lib/agentPrefs';
import { turnSkinOf } from '../../lib/chatSkin';
import CallWaveform from './CallWaveform';

const PHASE_WORDS = { connecting: 'Connecting…', listening: 'Listening', thinking: 'Thinking', talking: 'Talking', ended: 'Call ended' } as const;

function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const subscribeDark = (cb: () => void) => {
  const mo = new MutationObserver(cb);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  return () => mo.disconnect();
};
const isDark = () => document.documentElement.classList.contains('dark');

export default function CallBar({ agentId, name: seatName, onSettings, onExpand, onBackToChat, levels = callLevels }: {
  agentId: string;
  name: string;
  onSettings?: () => void;
  /** The agent is in Dev: a "Back to chat" button, the page's own Back to chat (the /dev {on:false} switch and the
   *  send hold that keeps the next reply warm). Offered, never done for you; what the agent says about it is its own. */
  onBackToChat?: () => void;
  /** Told when the transcript opens or closes. */
  onExpand?: (open: boolean) => void;
  levels?: CallLevels;
}) {
  const call = useCallStore((s) => (s.call && s.call.agentId === agentId ? s.call : null));
  const hangUp = useCallStore((s) => s.hangUp);
  const setMuted = useCallStore((s) => s.setMuted);
  // Named for who answers: this agent once its calls reach it, else Arturo (lib/agentCallsOn.ts).
  const party = useCallParty(call);
  // The agent's own call reads like its chat: its transcript in the chat style chosen for it (Arturo's: his list).
  const chatStyle = useChatStyle(agentId);
  const turnSkin = call?.party === 'agent' ? turnSkinOf(chatStyle, styleBeforeDev(agentId)) : undefined;
  const rec = party.rec;
  const name = call?.party === 'agent' ? seatName : party.name;
  const dark = useSyncExternalStore(subscribeDark, isDark, () => false);
  const [open, setOpenState] = useState(false);
  const setOpen = (o: boolean) => { setOpenState(o); onExpand?.(o); };
  const drag = useRef<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!call) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [call]);
  if (!call) return null;
  const color = resolveColor(rec.color || 'ink', dark ? 'dark' : 'light');
  // the agent's voice: the same colour, muted toward the bar's surface
  const muted = color.startsWith('#') ? shade(color, dark ? -0.45 : 0.55) : color;
  const btn = 'flex size-9 shrink-0 items-center justify-center rounded-full transition-colors';
  const phaseWords = call.muted ? 'Muted' : PHASE_WORDS[call.phase];
  // Only for the agent's own call: a call that went to Arturo does not wait on the seat's chat.
  const devLine = onBackToChat && call.party === 'agent' && (
    <div className="flex items-center justify-center gap-2 px-3 pb-1.5 text-[12px] text-muted-foreground" data-call-dev>
      <span>{seatName} is in Dev</span>
      <button type="button" onClick={onBackToChat} data-call-back-to-chat
              className="min-h-0 rounded-full border border-border px-2.5 py-0.5 text-[12px] font-medium text-foreground hover:bg-muted">Back to chat</button>
    </div>
  );

  return (
    <div className="pointer-events-none absolute inset-x-0 top-0 z-20 px-3 pt-2" data-call-bar={call.phase}>
      <div className="pointer-events-auto mx-auto w-full max-w-[420px] rounded-2xl border border-border bg-card/95 shadow-lg backdrop-blur-md">
        <div className="flex items-center gap-2.5 px-2.5 pt-2 pb-1">
          <div className="flex shrink-0 flex-col items-center">
            {/* its own look and colour, in the call's state (a state-driven avatar takes its look from these) */}
            <AgentAvatar agentId={party.id} role="drawer" size={38} state={avatarStateForCall(call.phase)} label={name}
                         shape={rec.shape} color={rec.color} eyeColor={rec.eyeColor} />
          </div>
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex items-baseline gap-2 text-[11px] leading-4 text-muted-foreground">
              <span className="truncate font-medium text-foreground">{name}</span>
              <span className="tabular-nums">{clock(now - call.startedAt)}</span>
              <span className="truncate" role="status" data-call-phase={call.phase}>{phaseWords}</span>
            </div>
            <CallWaveform levels={levels} color={color} muted={muted} micOff={call.muted} />
          </div>
          <button type="button" className={`${btn} text-foreground/70 hover:bg-muted hover:text-foreground`} onClick={onSettings}
                  aria-label="Call settings" title="Call settings"><Settings size={17} /></button>
          <button type="button" className={`${btn} ${call.muted ? 'bg-red-500 text-white' : 'text-foreground/70 hover:bg-muted hover:text-foreground'}`}
                  onClick={() => setMuted(!call.muted)} aria-pressed={call.muted} aria-label={call.muted ? 'Unmute' : 'Mute'}
                  title={call.muted ? 'Unmute' : 'Mute'}>{call.muted ? <MicOff size={17} /> : <Mic size={17} />}</button>
          <button type="button" className={`${btn} bg-red-500 text-white hover:bg-red-600`} onClick={hangUp}
                  aria-label="Hang up" title={`End the call with ${name}`}><PhoneOff size={17} /></button>
        </div>
        {devLine}
        {/* open: the transcript drops down under the bar (phone and desktop) */}
        {open && <CallTranscript turns={call.turns} name={name} skin={turnSkin} className="max-h-[45vh] border-t border-border px-4" />}
        {/* the drag handle: a tap, or pulling it down (up to close), opens the transcript */}
        <button type="button" aria-expanded={open} aria-label={open ? "Hide the call's transcript" : "Show the call's transcript"}
                title={open ? 'Hide the transcript' : 'Show the transcript'} data-call-handle
                onPointerDown={(e) => { drag.current = e.clientY; }}
                onPointerUp={(e) => {
                  const dy = drag.current === null ? 0 : e.clientY - drag.current;
                  drag.current = null;
                  if (dy > 24) setOpen(true); else if (dy < -24) setOpen(false); else setOpen(!open);
                }}
                className="mx-auto mb-1 flex h-4 w-16 touch-none items-center justify-center rounded-full">
          <span className="block h-1 w-9 rounded-full bg-foreground/20" />
        </button>
      </div>
    </div>
  );
}
