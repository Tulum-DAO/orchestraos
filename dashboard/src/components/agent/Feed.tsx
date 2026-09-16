import TranscriptChatView from '../chat/TranscriptChatView';

interface FeedProps {
  agentId?: string;
}

export function Feed({ agentId = 'gm' }: FeedProps) {
  return <TranscriptChatView agentId={agentId} />;
}
