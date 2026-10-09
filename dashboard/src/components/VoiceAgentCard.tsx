import { Mic } from 'lucide-react';
import { clsx } from 'clsx';
import { VOICE_AGENT_PENDING_ID } from '../lib/constants';

interface Props {
  pmId: string;
  name?: string;
  agentId: string;
  voice: string;
  updatedAt: string;
  isInCall: boolean;
  onCall: () => void;
}

export function VoiceAgentCard({ pmId, name, agentId, voice, updatedAt, isInCall, onCall }: Props) {
  // The install's own voice agent, as state/voice-agents.json names it: never a built-in roster.
  const label = name || pmId;
  // A soak/POC card whose ElevenLabs agent isn't wired yet: render it, but the call
  // button is disabled + labeled honestly (no broken call). Real agents are unaffected.
  const isPending = !agentId || agentId === VOICE_AGENT_PENDING_ID;

  const synced = updatedAt
    ? new Date(updatedAt).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'Never';

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-5 flex flex-col gap-3">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-lg font-semibold">{label}</h3>
          {label !== pmId && <p className="text-sm text-neutral-500 mt-0.5">{pmId}</p>}
        </div>
        {voice && (
          <span className="text-[10px] uppercase tracking-wider bg-neutral-800 text-neutral-400 px-2 py-0.5 rounded-full whitespace-nowrap">
            {voice}
          </span>
        )}
      </div>

      <div className="flex items-center justify-between mt-auto pt-2">
        <span className="text-[11px] text-neutral-600">Synced {synced}</span>
        <button
          onClick={onCall}
          disabled={isInCall || isPending}
          className={clsx(
            'inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
            isPending
              ? 'bg-neutral-800 text-neutral-500 cursor-not-allowed'
              : isInCall
                ? 'bg-green-900/40 text-green-400 cursor-not-allowed animate-pulse'
                : 'bg-green-600 hover:bg-green-500 text-white'
          )}
        >
          <Mic size={15} />
          {isPending ? 'Awaiting setup' : isInCall ? 'In Call...' : 'Start Call'}
        </button>
      </div>
    </div>
  );
}
