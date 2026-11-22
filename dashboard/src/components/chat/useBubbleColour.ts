/**
 * The style that colours the person's Bubbles bubbles for one agent (lib/bubbleColour.ts): the agent's look colour,
 * followed live (a new look, a theme switch), as the CSS variables .bubble-user-colour reads. Put it on the element
 * that holds the thread: every bubble under it (sent, just sent, held, a call's transcript) takes the colour.
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

/** For a look colour ("ink", a hex, or missing), in the page's theme. */
export function useBubbleColourOf(color: string | null | undefined): CSSProperties {
  const dark = useSyncExternalStore(subscribeDark, isDark, () => false);
  return useMemo(() => bubbleVars(bubbleColours(color, dark ? 'dark' : 'light')) as CSSProperties, [color, dark]);
}

/** For an agent: its live look colour. */
export function useBubbleColour(agentId: string): CSSProperties {
  return useBubbleColourOf(useAgentRecord(agentId).color);
}
