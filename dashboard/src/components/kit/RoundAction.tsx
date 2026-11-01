/**
 * RoundAction — the composer's one round button (lib/roundAction.ts decides what it is): Send, Stop, Call or End.
 * One button that never moves: its icons sit on top of each other and crossfade, so a change of mode shifts nothing
 * around it. Its look is the caller's (`className`): Arturo's `.circle-btn.white`, or the agent composer's round
 * button in the same colours (near-black on light, white on dark).
 */
import { ArrowUp, Loader2, PhoneCall, PhoneOff } from 'lucide-react';
import type { ReactNode } from 'react';
import type { RoundView } from '../../lib/roundAction';

export default function RoundAction({ view, busy = false, className = '', onAction, callIcon }: {
  view: RoundView;
  /** A send or a stop is under way: a spinner in place of the icon. */
  busy?: boolean;
  className?: string;
  onAction: (mode: RoundView['mode']) => void;
  /** The call's own icon (Arturo's hands-free waves); a phone by default. */
  callIcon?: ReactNode;
}) {
  const layer = (on: boolean, node: ReactNode) => (
    <span aria-hidden className={`absolute inset-0 flex items-center justify-center transition-opacity duration-150 ${on ? 'opacity-100' : 'opacity-0'}`}>{node}</span>
  );
  const m = view.mode;
  return (
    <button type="button" className={`relative ${className}`.trim()} disabled={!view.enabled || busy}
            aria-label={view.label} title={view.title} data-round-action={m}
            onClick={() => { if (view.enabled && !busy) onAction(m); }}>
      {layer(busy, <Loader2 size={16} className="animate-spin" />)}
      {layer(!busy && (m === 'send' || m === 'idle'), <ArrowUp size={18} strokeWidth={2.5} />)}
      {layer(!busy && m === 'stop', <span className="block h-3.5 w-3.5 rounded-[3px] bg-current" />)}
      {layer(!busy && m === 'call', callIcon ?? <PhoneCall size={17} />)}
      {layer(!busy && m === 'end', <PhoneOff size={17} />)}
    </button>
  );
}
