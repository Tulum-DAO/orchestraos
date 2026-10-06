/**
 * AgentStatusDot — the canonical agent status dot for web surfaces.
 * Binds to the v2 detector `status` (raw or mapped vocab) via the shared
 * normalizeAgentState + STATE_STYLE, so the title/card dot matches the chat
 * pill and the iOS app. Replaces the pane-scrape ActivityDot for status.
 */
import clsx from 'clsx';
import { normalizeAgentState, STATE_STYLE } from '../lib/agentStatus';
import { styleForAgentState } from '../lib/feedLiveness';
import { useFeedHealth } from '../hooks/useFeedHealth';

export function AgentStatusDot({ status, className }: { status?: string; className?: string }) {
  // THE CANONICAL DOT READS THE FEED ITSELF. The live port proof caught this: chip and chat pill
  // had been taught the rule by their CALL SITES, and the page with a dot per agent kept
  // painting live colours over a dead feed because nobody passed it the verdict. A leaf that
  // asks for itself cannot be forgotten by a new call site.
  const feed = useFeedHealth();
  const st = normalizeAgentState(status);
  const s = styleForAgentState(feed, STATE_STYLE[st]);
  return (
    <span
      className={clsx('inline-block w-2 h-2 rounded-full shrink-0', s.dot, className)}
      title={s.label}
    />
  );
}
