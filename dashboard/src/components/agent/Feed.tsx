import TranscriptChatView from '../chat/TranscriptChatView';
import { agentRowsFrom } from '../../lib/api';
import { useAgents } from '../../hooks/useAgents';
import { useFeedHealth } from '../../hooks/useFeedHealth';

interface FeedProps {
  agentId?: string;
}

/**
 * The agent page's chat. It feeds the status pill from the SAME /api/agents row and the SAME
 * feed-health verdict the chips use (P1 2026-10-06): before this it passed no state at all, so
 * the page pill said 'unknown' while the chip said something else entirely — the two surfaces
 * were not merely disagreeing, they were reading different things.
 */
export function Feed({ agentId = 'gm' }: FeedProps) {
  const { data } = useAgents();
  const feed = useFeedHealth();
  const rows = agentRowsFrom(data) as
    | Array<{ id: string; status?: string; stranded?: unknown; pending_menu?: unknown }>
    | undefined;
  const row = Array.isArray(rows) ? rows.find((a) => a.id === agentId) : undefined;
  return (
    <TranscriptChatView
      agentId={agentId}
      state={row?.status || 'unknown'}
      strandedText={typeof row?.stranded === 'string' ? row.stranded : (row?.stranded as { text?: string } | undefined)?.text}
      pendingMenu={(row?.pending_menu as never) ?? null}
      feed={feed}
      hideStatusBar
    />
  );
}
