/**
 * LiveStatusLine — what the agent is doing RIGHT NOW, pinned directly above the composer.
 *
 * Shaw's critique, top charge: "the page lies about state". The sidebar said WORKING while the
 * transcript's last event was "Stopped after 9s — 1 interrupted" followed by an unanswered
 * "Hello? Are you still there?". Nothing on the page said what the agent was doing, so the user
 * was talking into a void and the two surfaces contradicted each other.
 *
 * It reads the SAME row and the SAME staleness verdict as the rail dot (lib/feedLiveness), so
 * the two cannot disagree — that was the P1 and it is the same rule here.
 */
import { Loader2, CircleSlash, HandMetal, WifiOff } from 'lucide-react';
import { normalizeAgentState, type LiveState, offersResume, QUEUED_LINE } from '../../lib/agentStatus';
import { lastSeenLabel, isDegraded, type FeedVerdict } from '../../lib/feedLiveness';

export interface LiveStatusProps {
  state?: string;
  feed?: FeedVerdict;
  /** Seconds the current turn has been running, when the detector knows. */
  stateAgeS?: number;
  /** The tool the agent is in right now, when the detector knows. */
  tool?: string;
  onResume?: () => void;
}

function elapsed(s?: number): string {
  if (!s || s < 0) return '';
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${Math.round(s % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function LiveStatusLine({ state, feed, stateAgeS, tool, onResume }: LiveStatusProps) {
  // DISCONNECTED OUTRANKS EVERY AGENT STATE: with no live feed we cannot claim what it is doing.
  if (feed && isDegraded(feed)) {
    return (
      <div className="w-full max-w-[860px] mx-auto flex items-center gap-2 px-3 py-1.5 text-[11px] text-neutral-400" role="status">
        <WifiOff size={12} className="shrink-0" />
        <span>{feed.health === 'disconnected' ? 'Not connected' : 'Connection lost'} · {lastSeenLabel(feed.lastSeenAt)}</span>
      </div>
    );
  }

  const st: LiveState = normalizeAgentState(state);
  // Capped and centred on the SAME column as the transcript and the composer: three stacked
  // elements with three different widths is the ragged-edge problem one level up.
  const base = 'w-full max-w-[860px] mx-auto flex items-center gap-2 px-3 py-1.5 text-[11px]';

  if (st === 'working' || st === 'stalled') {
    const where = tool ? ` · ${tool}` : '';
    const how = elapsed(stateAgeS);
    return (
      <div className={`${base} text-orange-300`} role="status">
        <Loader2 size={12} className="animate-spin shrink-0" />
        <span>Working{how ? ` · ${how}` : ''}{where}</span>
      </div>
    );
  }

  if (st === 'waiting' || st === 'stranded') {
    return (
      <div className={`${base} text-amber-300`} role="status">
        <HandMetal size={12} className="shrink-0" />
        <span>{st === 'waiting' ? 'Waiting for you to answer' : 'An unsent draft is in its composer'}</span>
      </div>
    );
  }

  // queued: the CLI accepted the message while busy and ended the turn without running it. Not
  // "working" and not "idle at the prompt"; either would be the lie this line exists to stop.
  if (st === 'queued') {
    return (
      <div className={`${base} text-[#BF5AF2]`} role="status">
        <span className="w-1.5 h-1.5 rounded-full bg-[#BF5AF2] shrink-0" />
        <span>{QUEUED_LINE}</span>
      </div>
    );
  }

  // stopped / crashed / offline / retired: say WHY, and offer the one action that helps.
  if (st === 'stopped' || st === 'crashed' || st === 'offline' || st === 'retired') {
    const why = st === 'crashed' ? 'Crashed' : st === 'offline' ? 'Offline' : st === 'retired' ? 'Retired' : 'Stopped';
    return (
      <div className={`${base} text-neutral-400`} role="status">
        <CircleSlash size={12} className="shrink-0" />
        <span>{why} — it will not see a message until it is running.</span>
        {/* NOT offered for a RETIRED seat. Retired means intentionally decommissioned — the
            `seat-gN` row every lineage rotation leaves behind — so "Resume" there would
            resurrect a generation somebody deliberately ended, which is a different act from
            restarting something that fell over. The other three states are failures. */}
        {onResume && offersResume(st) && (
          <button type="button" onClick={onResume}
            className="ml-auto px-2 py-0.5 rounded border border-neutral-700 text-neutral-200 hover:bg-neutral-800">
            Resume
          </button>
        )}
      </div>
    );
  }

  if (st === 'idle') {
    return (
      <div className={`${base} text-green-400`} role="status">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500 shrink-0" />
        <span>Idle · at the prompt, ready for a message</span>
      </div>
    );
  }

  // unknown: claim NOTHING. An honest blank is better than a guess.
  return (
    <div className={`${base} text-neutral-500`} role="status">
      <span className="w-1.5 h-1.5 rounded-full bg-neutral-600 shrink-0" />
      <span>State unknown</span>
    </div>
  );
}
