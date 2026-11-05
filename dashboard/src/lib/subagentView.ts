/**
 * The agent-tree drill-in on the agent page: a subagent's transcript, read-only, in the agent's own chat skin.
 * Dev is the seat's terminal, which a subagent does not have, so it falls back to Bubbles.
 */
import type { ChatStyle } from './agentPrefs';

export function subagentSkin(style: ChatStyle): 'modern' | 'bubbles' {
  return style === 'modern' ? 'modern' : 'bubbles';
}

/** What the agent page's body shows: the seat's terminal (Dev), its chat, or a subagent's transcript. */
export function pageBody({ mode, readOnly }: { mode: 'chat' | 'dev'; readOnly: boolean }): 'terminal' | 'chat' | 'subagent' {
  if (readOnly) return 'subagent';
  return mode === 'dev' ? 'terminal' : 'chat';
}
