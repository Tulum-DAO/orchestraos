/**
 * callTranscript.ts — a live call's transcript, turn by turn, from the separate captions of the two speakers (your
 * words, the agent's words; one person per call, no speaker detection). A partial caption is the turn's words so
 * far; a final one closes the turn. Pure.
 */
export interface CallTurn { role: 'user' | 'agent'; text: string; final: boolean }

export function addCaption(turns: CallTurn[], role: 'user' | 'agent', text: string, final: boolean): CallTurn[] {
  const t = text.trim();
  if (!t) return turns;
  const last = turns[turns.length - 1];
  if (last && last.role === role && !last.final) return [...turns.slice(0, -1), { role, text: t, final }];
  return [...turns, { role, text: t, final }];
}
