/**
 * AgentWorkingRow — above the agent page's composer: the agent's animated avatar, its name, and what it is doing
 * (lib/agentWorkingRow.ts says which truth). It replaces the old status line and keeps every state that line had.
 *
 * Its avatar reads the same state as the sidebar dot (lib/avatarStatus over the rail's status): it is written to the
 * avatar hub here too, so the face is right on a phone whose sidebar is closed.
 */
import { useEffect, useState } from 'react';
import { CircleSlash, HandMetal, Loader2, WifiOff } from 'lucide-react';
import { AgentAvatar } from '../avatar/AgentAvatar';
import { avatarHub, ensureAgent } from '../avatar/agentState';
import { avatarStateFor } from '../../lib/avatarStatus';
import { dotState, rowShown, workingRowFor, type RowTone, type WorkingRowInput } from '../../lib/agentWorkingRow';
import { isDegraded } from '../../lib/feedLiveness';
import './agentWorking.css';

const TONE: Record<RowTone, string> = {
  working: 'text-tone-orange-300',
  needs: 'text-tone-amber-300',
  queued: 'text-[#BF5AF2]',
  down: 'text-tone-400',
  idle: 'text-tone-green-400',
  muted: 'text-tone-500',
};
const DOT: Record<RowTone, string> = {
  working: 'bg-orange-400', needs: 'bg-amber-300', queued: 'bg-[#BF5AF2]', down: 'bg-red-500', idle: 'bg-green-500', muted: 'bg-tone-600',
};

export function AgentWorkingRow({ agentId, name, onResume, ...input }: WorkingRowInput & {
  agentId: string;
  name: string;
  onResume?: () => void;
}) {
  // The elapsed time ticks while it works.
  const [now, setNow] = useState(() => Date.now());
  const row = workingRowFor({ ...input, now });
  const ticking = row.tone === 'working';
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ticking]);

  const { state, menu } = dotState(input);
  const face = avatarStateFor(state, menu, !!input.feed && isDegraded(input.feed));
  useEffect(() => {
    const prev = avatarHub.get(agentId) ?? ensureAgent(agentId);
    if (prev.status !== face) avatarHub.set(agentId, { status: face });
  }, [agentId, face]);

  // Idle or unknown: no row at all (only work, and states that need the person, show).
  if (!rowShown(row)) return null;
  const mark = row.mark === 'spin' ? <Loader2 size={12} className={`animate-spin ${row.kind === 'updating' ? 'opacity-60' : ''}`} />
    : row.mark === 'hand' ? <HandMetal size={12} />
    : row.mark === 'off' ? <CircleSlash size={12} />
    : row.mark === 'wifi' ? <WifiOff size={12} />
    : <span className={`w-1.5 h-1.5 rounded-full ${DOT[row.tone]}`} />;
  return (
    // data-arturo-avoid: a phone's floating Arturo button lifts above the row when the row reaches its corner.
    <div className="agent-working" role="status" aria-live="polite" data-row-kind={row.kind} data-row-state={row.state ?? ''} data-arturo-avoid>
      <AgentAvatar agentId={agentId} role="activity" size={24} label={name} />
      <span className="agent-working-name">{name}</span>
      <span className={`agent-working-mark ${TONE[row.tone]}`} aria-hidden>{mark}</span>
      <span className={`agent-working-step ${TONE[row.tone]}`}>{row.text}</span>
      {row.elapsed && <span className="agent-working-time">· {row.elapsed}</span>}
      {/* NOT offered for a RETIRED seat: retired is a generation somebody deliberately ended (offersResume). */}
      {row.resume && onResume && (
        <button type="button" className="agent-working-resume" onClick={onResume}>Resume</button>
      )}
    </div>
  );
}
