/**
 * The style that colours the person's Bubbles bubbles for one agent (lib/bubbleColour.ts): the agent's look colour,
 * followed live (a new look, a theme switch), as the two CSS variables .bubble-user-colour reads. Put it on the element
 * that holds the thread: every bubble under it (sent, just sent, a call's transcript) takes the colour.
 */
import { useMemo, useSyncExternalStore, type CSSProperties } from 'react';
import { useAgentRecord } from '../avatar/agentState';
import { bubbleColours, bubbleVars } from '../../lib/bubbleColour';

const subscribeDark = (cb: () => void) => {
  const mo = new MutationObserver(cb);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  return () => mo.disconnect();
};
const isDark = () => document.documentElement.classList.contains('dark');

export function useBubbleColour(agentId: string): CSSProperties {
  const color = useAgentRecord(agentId).color;
  const dark = useSyncExternalStore(subscribeDark, isDark, () => false);
  return useMemo(() => bubbleVars(bubbleColours(color, dark ? 'dark' : 'light')) as CSSProperties, [color, dark]);
}
