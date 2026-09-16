/**
 * AgentStatusDot — the canonical agent status dot for web surfaces.
 * Binds to the v2 detector `status` (raw or mapped vocab) via the shared
 * normalizeAgentState + STATE_STYLE, so the title/card dot matches the chat
 * pill and the iOS app. Replaces the pane-scrape ActivityDot for status.
 */
import clsx from 'clsx';
import { normalizeAgentState, STATE_STYLE } from '../lib/agentStatus';

export function AgentStatusDot({ status, className }: { status?: string; className?: string }) {
  const st = normalizeAgentState(status);
  const s = STATE_STYLE[st];
  return (
    <span
      className={clsx('inline-block w-2 h-2 rounded-full shrink-0', s.dot, className)}
      title={s.label}
    />
  );
}
