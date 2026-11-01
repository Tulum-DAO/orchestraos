// The agent whose chat this is, for what it draws that belongs to that agent (its calls' cards: their transcript and
// thumbs come from that agent's calls). Absent outside an agent's chat (Arturo's page, the docked feed).
import { createContext } from 'react';

// `skin`: the chat style the agent's call transcripts are drawn in (its thread's own: Bubbles or Modern).
export const CallAgentContext = createContext<{ agentId: string; name: string; skin?: 'bubbles' | 'modern' } | null>(null);
