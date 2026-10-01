// Pure logic for ConversationPanel.tsx, extracted so it can be unit-tested
// without a React/JSX test harness (see conversationPanel.test.mjs).

export type MessageTypeFilter = 'All' | 'Task' | 'Reply' | 'Task request';

export const TYPE_FILTERS: MessageTypeFilter[] = ['All', 'Task', 'Reply', 'Task request'];

const FILTER_TO_TYPE: Record<Exclude<MessageTypeFilter, 'All'>, string> = {
  Task: 'task',
  Reply: 'reply',
  'Task request': 'task_request',
};

export function matchesTypeFilter(messageType: string | null | undefined, filter: MessageTypeFilter): boolean {
  if (filter === 'All') return true;
  return messageType === FILTER_TO_TYPE[filter];
}

export interface BodyTruncation {
  preview: string;
  truncated: boolean;
}

// Truncates to ~8 lines (spec section 6); an "expand" affordance shows the rest.
export function truncateBody(body: string, maxLines = 8): BodyTruncation {
  const lines = (body || '').split('\n');
  if (lines.length <= maxLines) return { preview: body, truncated: false };
  return { preview: lines.slice(0, maxLines).join('\n'), truncated: true };
}
