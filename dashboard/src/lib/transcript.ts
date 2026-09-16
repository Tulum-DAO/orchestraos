/**
 * Transcript data layer — the message-isolation key.
 *
 * Instead of scraping the tmux pane (terminal soup, ANSI, wrapping, ghost text),
 * the chat renders from Claude Code's per-session transcript JSONL, which is
 * appended live, one clean structured entry per message block. The API endpoint
 * (`GET /agents/:id/transcript`) resolves the session and returns normalized
 * ChatItems; this module types them and turns them into a render list.
 *
 * LANE: conversation rendering only. Live agent STATE (working/idle/waiting/
 * stranded) comes from the /agents v2 detector (agent-state-truth), not here.
 */

// v2: the item/envelope wire shapes are GENERATED from the shared versioned
// schema (contract/transcript/transcript.v2.schema.json -> codegen.mjs ->
// synced transcript.gen.ts). Do not hand-declare wire shapes here — that is
// the exact web-vs-iOS drift the contract exists to kill.
import type { ChatItem as GenChatItem, TranscriptEnvelope } from './transcript.gen';

export type ChatItem = GenChatItem;
export type TranscriptResponse = TranscriptEnvelope;
export { GRAMMAR_VERSION } from './transcript.gen';

/** A tool_use with its (optional) paired result — rendered as one collapsible card. */
export interface ToolBlock {
  kind: 'tool';
  tool: string;
  input: Record<string, unknown>;
  result?: string;
  isError?: boolean;
  ts?: string;
  key: string;
}

export interface BatchEntryNode { agent: string; sent_ts: string; body: string }

export type RenderNode =
  | { kind: 'user'; text: string; ts?: string; key: string; isSystem?: boolean; queued?: boolean }
  | { kind: 'assistant'; text: string; ts?: string; key: string }
  | { kind: 'thinking'; text: string; ts?: string; key: string }
  | { kind: 'queued_batch'; count: number; entries: BatchEntryNode[]; ts?: string; key: string }
  | ToolBlock;

/**
 * Pair tool_result items under their originating tool_use, drop empty items,
 * and flag the (huge) spawn system-prompt so the UI can collapse it. Order is
 * preserved from the transcript.
 */
export function buildRenderList(items: ChatItem[]): RenderNode[] {
  // index results by tool_use_id
  const resultById = new Map<string, { text: string; isError: boolean }>();
  for (const it of items) {
    if (it.kind === 'tool_result' && it.tool_use_id) {
      resultById.set(it.tool_use_id, { text: it.text, isError: !!it.is_error });
    }
  }

  const nodes: RenderNode[] = [];
  let firstUserSeen = false;
  let i = 0;
  for (const it of items) {
    const key = 'uuid' in it && it.uuid ? `${it.uuid}:${i}` : `n${i}`;
    i++;
    if (it.kind === 'tool_result') continue; // consumed via pairing
    if (it.kind === 'text') {
      const text = it.text?.trim();
      if (!text) continue;
      if (it.role === 'user') {
        // The very first user text on a spawned session is the system/spawn prompt.
        const isSystem = !firstUserSeen && (text.length > 800 || /^You are\b/.test(text));
        firstUserSeen = true;
        nodes.push({ kind: 'user', text, ts: it.ts, key, isSystem, queued: (it as any).queued === true });
      } else {
        nodes.push({ kind: 'assistant', text, ts: it.ts, key });
      }
      continue;
    }
    if (it.kind === 'thinking') {
      const text = it.text?.trim();
      if (!text) continue;
      nodes.push({ kind: 'thinking', text, ts: it.ts, key });
      continue;
    }
    if (it.kind === 'queued_batch') {
      // B2 processed-batch div (queued-native-render): entries already newest->oldest.
      const qb = it as any;
      nodes.push({
        kind: 'queued_batch',
        count: typeof qb.count === 'number' ? qb.count : (Array.isArray(qb.entries) ? qb.entries.length : 0),
        entries: Array.isArray(qb.entries) ? qb.entries : [],
        ts: it.ts,
        key: qb.key || key,
      });
      continue;
    }
    if (it.kind === 'tool_use') {
      const paired = it.id ? resultById.get(it.id) : undefined;
      nodes.push({
        kind: 'tool',
        tool: it.tool,
        input: it.input || {},
        result: paired?.text,
        isError: paired?.isError,
        ts: it.ts,
        key,
      });
      continue;
    }
  }
  return nodes;
}

/** One-line human summary of a tool call for the collapsed card header. */
export function toolSummary(tool: string, input: Record<string, unknown>): string {
  const s = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v));
  switch (tool) {
    case 'Bash':
    case 'run_command':
      return s(input.CommandLine || input.command || '').split('\n')[0].slice(0, 120);
    case 'Read':
    case 'view_file':
      return shortPath(s(input.AbsolutePath || input.file_path || input.path || ''));
    case 'Edit':
    case 'replace_file_content':
      return shortPath(s(input.TargetFile || input.file_path || ''));
    case 'Write':
    case 'write_to_file':
      return shortPath(s(input.TargetFile || input.file_path || ''));
    case 'NotebookEdit':
      return shortPath(s(input.notebook_path || ''));
    case 'Glob':
    case 'find_by_name':
      return `${s(input.Pattern || input.pattern || '')}${input.SearchDirectory ? ' in ' + shortPath(s(input.SearchDirectory)) : ''}`;
    case 'Grep':
    case 'grep_search':
      return `${s(input.Query || input.pattern || '')}${input.SearchPath || input.path ? ' in ' + shortPath(s(input.SearchPath || input.path)) : ''}`;
    case 'Task':
    case 'Agent':
    case 'invoke_subagent':
    case 'manage_subagents':
      return s(input.Description || input.description || input.toolSummary || input.subagent_type || '');
    case 'WebFetch':
    case 'read_url_content':
    case 'WebSearch':
    case 'search_web':
      return s(input.Url || input.url || input.Query || input.query || '');
    case 'Skill':
      return s(input.skill || input.command || '');
    case 'send_message':
      return `To ${s(input.Recipient || '')}: ${s(input.Message || '').slice(0, 60)}`;
    default: {
      const summary = input.toolSummary || input.toolAction;
      if (summary) return s(summary);
      const first = Object.values(input)[0];
      return first != null ? s(first).slice(0, 120) : '';
    }
  }
}

function shortPath(p: string): string {
  if (!p) return '';
  return p.replace(/^\/home\/[^/]+\//, '~/').replace(/^\/Users\/[^/]+\//, '~/');
}

const API_BASE = '/api';
export async function fetchTranscript(agentId: string, limit = 120): Promise<TranscriptResponse> {
  const res = await fetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}/transcript?limit=${limit}`);
  if (!res.ok) throw new Error(`transcript ${agentId}: ${res.status}`);
  return res.json();
}
