/**
 * AgentMessage — a message another agent (or a service) sent the agent whose chat this is. Drawn the way a
 * collapsed Arturo call is: one quiet centred line, "Message from" and the sender's face and name side by side,
 * no card. Hovering the face and name brings up the agent identity pill (AgentIdentityPill, bare) behind just
 * them; clicking it opens the sender's chat. Priority is not shown.
 * The message itself shows in full below, as an incoming message, read from the message store by its id
 * (GET /api/agents/:id/messages/:msgId); until then, or if it can't be read, the subject the line carried.
 *
 * Arturo is an agent too: his face, his name, and a click opens his page. A sender that is no agent (the
 * approvals service, the briefing writer) gets one quiet centred line instead, what happened and to what
 * (lib/systemMessages serviceNoticeText), no face and no bubble; clicking it shows the message under it. The
 * approvals service's copy of an answer whose card this chat draws never gets here: it folds into the card.
 */
import { useContext, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import AgentIdentityPill from '../avatar/AgentIdentityPill';
import Markdown from './Markdown';
import { ChatAgentContext } from './chatAgent';
import { fetchAgentMessage } from '../../lib/api';
import { useAgents } from '../../hooks/useAgents';
import { useAssistantName } from '../../lib/assistantName';
import { ARTURO_ID } from '../avatar/agentState';
import { senderAgent, senderName, type AgentLike } from '../../lib/agentMessage';
import { serviceNoticeText, type SystemMessage } from '../../lib/systemMessages';
import { handbackResult } from '../../lib/threadKit/results';
import ResultCard from '../kit/ResultCard';
import { scrubIds } from '../../lib/cardRefs';

export default function AgentMessage({ msg }: { msg: SystemMessage }) {
  const viewerId = useContext(ChatAgentContext);
  const navigate = useNavigate();
  const { data } = useAgents();
  const agents = useMemo<AgentLike[]>(() => {
    if (Array.isArray(data)) return data as AgentLike[];
    const w = (data as { agents?: unknown } | undefined)?.agents;
    return Array.isArray(w) ? (w as AgentLike[]) : [];
  }, [data]);
  const assistant = useAssistantName();
  const [open, setOpen] = useState(false);
  const raw = msg.sender || '';
  const isArturo = raw === ARTURO_ID;
  const sender = raw && !isArturo ? senderAgent(agents, raw) : undefined;
  const viewer = viewerId ? agents.find((a) => a.id === viewerId) : undefined;
  const name = isArturo ? assistant : senderName(sender, raw || 'unknown', viewer);
  const face = isArturo ? { id: ARTURO_ID, href: '/' } : sender ? { id: sender.id, href: `/agent/${encodeURIComponent(sender.id)}` } : null;

  const full = useQuery({
    queryKey: ['agent-message', viewerId, msg.messageId],
    queryFn: () => fetchAgentMessage(viewerId!, msg.messageId!),
    enabled: !!viewerId && !!msg.messageId,
    staleTime: Infinity,
    retry: false,
  });
  const text = (full.data?.body || '').trim() || msg.subject || '';
  // A hand-back (phase_report, handoff) is finished work: its report is drawn as a result card under the line.
  const handback = full.data ? handbackResult(full.data, name) : null;

  if (!face) {
    return (
      <div data-testid="agent-message" data-sender={raw} data-service className="space-y-1.5">
        <div className="touch-compact flex justify-center px-4">
          <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
            className="hit-44 max-w-full truncate text-[13px] text-muted-foreground hover:text-foreground">
            {scrubIds(serviceNoticeText(msg, full.data?.body))}
          </button>
        </div>
        {handback ? <ResultCard results={[handback]} skin="bubbles" /> : open && text && <Bubble text={text} />}
      </div>
    );
  }

  return (
    <div data-testid="agent-message" data-sender={raw} className="space-y-1.5">
      <div className="touch-compact flex flex-wrap items-center justify-center gap-x-1.5 gap-y-1 px-4 text-[13px] text-muted-foreground">
        <span>Message from</span>
        <AgentIdentityPill variant="bare" text="inline" statusRoom={false} agentId={face.id} name={name} size={20}
          className="hit-44 text-foreground"
          aria-label={`Open ${name}'s chat`} title={`Open ${name}'s chat`}
          onClick={() => navigate(face.href)} />
        {msg.note?.includes('loop') && <span>· possible reply loop</span>}
      </div>
      {handback ? <ResultCard results={[handback]} skin="bubbles" /> : text && <Bubble text={text} />}
    </div>
  );
}

function Bubble({ text }: { text: string }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] min-w-0 rounded-2xl rounded-tl-sm border border-border bg-card text-card-foreground px-3.5 py-2 text-[13px] leading-relaxed break-words">
        <Markdown text={text} />
      </div>
    </div>
  );
}
