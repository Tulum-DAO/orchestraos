/**
 * URL builders for the seat transcript, a subagent's own transcript (agent-tree drill-in) and the
 * agent tree. Seat URLs are exactly what the chat always fetched; the subagent id follows the API's
 * rule (SAFE_SUBAGENT_ID, api/src/routes/chat-transcript.ts), so a bad id never reaches the server.
 */
const API_BASE = '/api';
export const SAFE_SUBAGENT_ID = /^[a-f0-9]{6,40}$/;

export interface TranscriptUrlOpts { limit: number; stream?: boolean; subagentId?: string }

export function transcriptUrl(agentId: string, { limit, stream, subagentId }: TranscriptUrlOpts): string {
  let base = `${API_BASE}/agents/${encodeURIComponent(agentId)}`;
  if (subagentId !== undefined) {
    if (!SAFE_SUBAGENT_ID.test(subagentId)) throw new Error(`bad subagent id: ${subagentId.slice(0, 50)}`);
    base += `/subagents/${subagentId}`;
  }
  return `${base}/transcript${stream ? '/stream' : ''}?limit=${limit}`;
}

export function treeUrl(agentId: string, { stream, all }: { stream?: boolean; all?: boolean } = {}): string {
  return `${API_BASE}/agents/${encodeURIComponent(agentId)}/tree${stream ? '/stream' : ''}${all ? '?all=1' : ''}`;
}
