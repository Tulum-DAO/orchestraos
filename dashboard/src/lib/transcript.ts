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
import { transcriptUrl } from './transcriptUrl';
import type { ResultNode } from './threadKit/results';
import { compactionText, isCompaction, type CompactionItem } from './compaction';
import { isTranscriptLine } from './threadKit/paging';

// The compaction notice (lib/compaction.ts) is a transcript item the generated contract types do not name yet; it
// joins the union here until they do.
// `ref`: the server's message id for reactions, `<session>~<uuid>#<part>` (api/src/lib/messageRef.ts), stamped by the
// API on agent-chat log items (poll route and stream). Synthetic rows have none, and the client never builds one.
// Not in the generated contract yet (reactions build step 5).
export type ChatItem = (GenChatItem | CompactionItem) & { ref?: string };
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
  /** When the paired tool_result arrived; the call's own `ts` is when it STARTED. */
  resultTs?: string;
  /** The tool call's id (tool_use id): a background job's notice names the call that started it. */
  id?: string;
  key: string;
}

export interface BatchEntryNode { agent: string; sent_ts: string; body: string }

export type RenderNode =
  | { kind: 'user'; text: string; ts?: string; key: string; isSystem?: boolean; queued?: boolean;
      /** The server's tag for a line a machine wrote (a decision answer, another agent's message, ...). */
      machine?: string;
      /** A job notice that arrived while the agent was mid-turn: it does not start a turn. */
      midTurn?: boolean;
      /** The server's message ref (ChatItem.ref): what a reaction to it is stored under. */
      ref?: string }
  /** afterReaction: the node right after a hidden reaction wake (api chat-transcript reaction_wake), i.e. the agent's
   *  answer to the operator's reaction; never folded into the message before it (view.ts withReactions). */
  | { kind: 'assistant'; text: string; ts?: string; key: string; afterReaction?: boolean; ref?: string }
  | { kind: 'thinking'; text: string; ts?: string; key: string }
  | { kind: 'queued_batch'; count: number; entries: BatchEntryNode[]; ts?: string; key: string }
  /** The chat's compaction notice (lib/compaction.ts): one line that changes in place, keyed by the notice's id. */
  | { kind: 'compaction'; state: CompactionItem['state']; trigger?: CompactionItem['trigger']; ts?: string; key: string }
  | ToolBlock
  /** Finished work (lib/threadKit/results.ts markResults): one result or a stack. Built on the client. */
  | ResultNode;

/**
 * Pair tool_result items under their originating tool_use, drop empty items,
 * and flag the (huge) spawn system-prompt so the UI can collapse it. Order is
 * preserved from the transcript.
 */
export function buildRenderList(items: ChatItem[]): RenderNode[] {
  // index results by tool_use_id
  const resultById = new Map<string, { text: string; isError: boolean; ts?: string }>();
  for (const it of items) {
    if (it.kind === 'tool_result' && it.tool_use_id) {
      resultById.set(it.tool_use_id, { text: it.text, isError: !!it.is_error, ts: it.ts });
    }
  }

  const nodes: RenderNode[] = [];
  let firstUserSeen = false;
  let i = 0;
  // A row's key is its item's own (its uuid, and which of that log line's items it is), never its place in the list:
  // older pages added above, or the live window's top moving down, must not rename (and so redraw) every row.
  const seen = new Map<string, number>();
  // Where the node after each hidden reaction wake lands (marked at the end when it is the agent's reply).
  const afterWake: number[] = [];
  for (const it of items) {
    if (it.kind === 'compaction') {
      if (isCompaction(it) && compactionText(it.state, it.trigger)) {
        nodes.push({ kind: 'compaction', state: it.state, ...(it.trigger ? { trigger: it.trigger } : {}), ts: it.ts, key: it.id });
      }
      i++;
      continue;
    }
    let key: string;
    if ('uuid' in it && it.uuid) {
      const n = seen.get(it.uuid) ?? 0;
      seen.set(it.uuid, n + 1);
      key = `${it.uuid}:${n}`;
    } else key = `n${i}`;
    i++;
    if (it.kind === 'tool_result') continue; // consumed via pairing
    if (it.kind === 'text') {
      if (it.role === 'user' && (it as { reaction_wake?: boolean }).reaction_wake === true) { afterWake.push(nodes.length); continue; }
      const text = it.text?.trim();
      if (!text) continue;
      if (it.role === 'user') {
        // Said on a voice call and handed to the agent (api chat-transcript voice_said): the call's card holds it.
        if ((it as { voice_said?: boolean }).voice_said === true) continue;
        // Not the operator's words, so never a blue bubble: what the server already flagged (codex's
        // developer brief and its <environment_context>/<multi_agent_mode> envelopes), the spawn
        // boot line ("You are gm. Read /tmp/agent-init-gm.md ..."), and the first user text of a
        // spawned session. Codex sends its envelopes FIRST, so "first" alone missed the boot line.
        const flagged = (it as { is_system?: boolean }).is_system === true;
        const bootLine = /^You are [\w.-]+\. Read \/tmp\/agent-init-[\w.-]+\.md\b/.test(text);
        const isSystem = flagged || bootLine || (!firstUserSeen && (text.length > 800 || /^You are\b/.test(text)));
        if (!flagged) firstUserSeen = true;
        const machine = (it as { machine?: string }).machine;
        const midTurn = (it as { mid_turn?: boolean }).mid_turn === true;
        nodes.push({ kind: 'user', text, ts: it.ts, key, isSystem, queued: (it as any).queued === true, ...(machine ? { machine } : {}),
          ...(midTurn ? { midTurn } : {}), ...(it.ref ? { ref: it.ref } : {}) });
      } else {
        nodes.push({ kind: 'assistant', text, ts: it.ts, key, ...(it.ref ? { ref: it.ref } : {}) });
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
        ...(paired?.ts ? { resultTs: paired.ts } : {}),
        ...(it.id ? { id: it.id } : {}),
        key,
      });
      continue;
    }
  }
  for (const at of afterWake) {
    const n = nodes[at];
    if (n?.kind === 'assistant') nodes[at] = { ...n, afterReaction: true };
  }
  return nodes;
}

/** One-line human summary of a tool call for the collapsed card header. */
export function toolSummary(tool: string, input: Record<string, unknown>): string {
  const s = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v));
  switch (tool) {
    case 'Bash':
    case 'run_command':
    case 'run_terminal_command':
      return s(input.CommandLine || input.command || '').split('\n')[0].slice(0, 120);
    case 'Read':
    case 'view_file':
    case 'read_file':
      return shortPath(s(input.AbsolutePath || input.file_path || input.path || input.target_file || ''));
    case 'Edit':
    case 'replace_file_content':
    case 'search_replace':
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
    case 'grep':
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

/**
 * The absolute file path a tool call acts on, or null when the call is not about one file.
 *
 * `toolSummary` folds the path into a display string, which loses it: the result is
 * shortened for display and cannot be copied back. This returns the path UNSHORTENED so a
 * caller can show `shortPath()` and still hand over the real thing.
 */
export function toolFilePath(tool: string, input: Record<string, unknown>): string | null {
  const s = (v: unknown) => (typeof v === 'string' ? v : '');
  let raw = '';
  switch (tool) {
    case 'Read':
    case 'view_file':
    case 'read_file':
      raw = s(input.AbsolutePath || input.file_path || input.path || input.target_file); break;
    case 'Edit':
    case 'replace_file_content':
    case 'search_replace':
    case 'Write':
    case 'write_to_file':
      raw = s(input.TargetFile || input.file_path); break;
    case 'NotebookEdit':
      raw = s(input.notebook_path); break;
    default:
      return null;
  }
  // A directory or a glob is not a file; neither is an empty slot.
  if (!raw || raw.endsWith('/') || /[*?]/.test(raw)) return null;
  return raw;
}

export function shortPath(p: string): string {
  if (!p) return '';
  return p.replace(/^\/home\/[^/]+\//, '~/').replace(/^\/Users\/[^/]+\//, '~/');
}

const API_BASE = '/api';
/** The seat's transcript, or with `subagentId` one subagent's own (agent-tree drill-in, read-only). */
export async function fetchTranscript(agentId: string, limit = 120, opts: { subagentId?: string } = {}): Promise<TranscriptResponse> {
  const res = await fetch(transcriptUrl(agentId, { limit, subagentId: opts.subagentId }));
  if (!res.ok) throw new Error(`transcript ${agentId}: ${res.status}`);
  return res.json();
}

/** The page of the chat just older than `before` (a page's own `oldest_id`, or an item's uuid): its items, whether
 *  there is more before it, and the cursor for the next. An unknown cursor answers an empty page, no more. */
export async function fetchOlderTranscript(agentId: string, before: string, limit = 100):
    Promise<{ items: ChatItem[]; hasOlder: boolean; next: string | null }> {
  const res = await fetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}/transcript?before=${encodeURIComponent(before)}&limit=${limit}`);
  if (!res.ok) throw new Error(`transcript ${agentId}: ${res.status}`);
  const body = await res.json() as TranscriptResponse & { has_older?: boolean; oldest_id?: string | null };
  const items = body.items || [];
  // The next cursor is the route's own oldest_id, a transcript line's uuid; should it ever name a notice or a marker
  // between conversations instead, the page's first real line stands in (the route pages only from those).
  const oldest = body.oldest_id ?? null;
  const next = oldest && isTranscriptLine({ uuid: oldest }) ? oldest : firstLineUuid(items);
  return { items, hasOlder: !!body.has_older && !body.error, next };
}

/** The uuid of a page's first real transcript line (not a notice, not a marker), or null. */
function firstLineUuid(items: readonly object[]): string | null {
  const it = items.find(isTranscriptLine) as { uuid?: string } | undefined;
  return it?.uuid ?? null;
}
