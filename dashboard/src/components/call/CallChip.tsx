/**
 * CallChip — a live call follows you (one call at a time): on any page but the called agent's own, a chip at the top
 * of the page that takes you back to it, where the call bar is. Desktop: the agent's small animated avatar and the
 * time. Phone: just the time, in a green pill. Arturo's own call is not here (it keeps its own experience).
 */
import { useNavigate } from 'react-router-dom';
import { AgentAvatar } from '../avatar/AgentAvatar';
import { useCallParty } from './useCallParty';
import { avatarStateForCall } from '../../lib/callPhase';
import { useCallStore } from '../../stores/callStore';
import CallTimer from './CallTimer';

export default function CallChip({ onAgentPage }: { onAgentPage: string | null }) {
  const call = useCallStore((s) => s.call);
  const navigate = useNavigate();
  // Named for who answers: the agent, or Arturo while its calls still go through him.
  const { id: partyId, rec, name } = useCallParty(call);
  if (!call || onAgentPage === call.agentId) return null;
  const go = () => navigate(`/agent/${encodeURIComponent(call.agentId)}`);
  return (
    <div className="pointer-events-none fixed inset-x-0 top-2 z-40 flex justify-center" data-call-chip>
      {/* desktop: the avatar and the time */}
      <button type="button" onClick={go} title={`Back to the call with ${name}`} aria-label={`Back to the call with ${name}`}
              className="pointer-events-auto hidden items-center gap-2 rounded-full border border-border bg-card/95 py-1 pl-1 pr-3 text-[12px] text-foreground shadow-md backdrop-blur sm:flex">
        <AgentAvatar agentId={partyId} role="drawer" size={24} state={avatarStateForCall(call.phase)} label={name}
                     shape={rec.shape} color={rec.color} eyeColor={rec.eyeColor} />
        <span className="font-medium">{name}</span>
        <CallTimer className="text-muted-foreground" />
      </button>
      {/* phone: the time, in a green pill */}
      <button type="button" onClick={go} aria-label={`Back to the call with ${name}`}
              className="pointer-events-auto flex items-center rounded-full bg-green-600 px-3 py-1 text-[12px] font-semibold text-white shadow-md sm:hidden">
        <CallTimer />
      </button>
    </div>
  );
}
