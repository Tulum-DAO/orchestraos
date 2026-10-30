/**
 * thread.ts — the thread kit's one model of a conversation: an agent's transcript and one of Arturo's threads read
 * into the same list of items, so one set of kit parts (Message, tool card, result card, decision card) can draw
 * either. Pure: no DOM, no fetching.
 *
 *   message   a turn's words, the operator's or the agent's; a reply that ran tools keeps its text and tool runs in
 *             the order they happened (`parts`)
 *   thinking  the agent's thinking between calls (agent transcripts only)
 *   tools     a run of tool calls, with the thinking and folded results that sat between them
 *   result    finished work (lib/threadKit/results.ts): one result or a stack
 *   batch     queued messages the agent took in at once (agent transcripts only)
 *
 * Agent transcripts arrive as ChatItems (lib/transcript.ts); Arturo's threads as stored turns (lib/arturoResume.ts
 * hydrateTurns) or the page's own live turns, which carry the same fields plus send state. Times are ISO-8601
 * whatever the source wrote (Arturo stores epoch seconds).
 */
import { buildRenderList, type ChatItem, type RenderNode } from '../transcript';
import { groupToolRuns, type GroupedNode } from '../toolGroups';
import { fromBlock, fromPart, type ToolCallView } from '../toolCalls';
import { groupParts, type TurnPart } from '../turnParts';
import { splitAttachments, type Attachment } from './markdown';
import { replyCopyText } from '../arturoCopy';
import type { ChoiceCard } from '../arturo';
import type { HydratedTurn } from '../arturoResume';
import { markResults, type FinishedResult, type ResultView, type ResultNode } from './results';

export type ThreadSource = 'agent' | 'arturo';
export type ThreadRole = 'user' | 'assistant';

/** A reply's pieces in the order they happened: words, or a run of tool calls. */
export type MessagePart = { kind: 'text'; text: string } | { kind: 'tools'; calls: ToolCallView[] };

export interface ThreadMessage {
  kind: 'message';
  key: string;
  role: ThreadRole;
  /** The words as written: Markdown for a reply, the operator's text for a user turn (attachment markers split off). */
  text: string;
  at?: string;
  /** A reply that ran tools: text and tool runs in order. Absent when the reply is words only. */
  parts?: MessagePart[];
  /** What the message's Copy puts on the clipboard: the words as shown, no tool or call cards. */
  copyText: string;
  attachments?: Attachment[];
  /** A line the operator did not type: the server's machine tag (a decision answer, another agent's message, a job
   *  notice), or the session's own brief. */
  machine?: string;
  system?: boolean;
  /** Waiting in the agent's input queue. */
  queued?: boolean;
  /** A job notice that arrived mid-turn: it does not start a turn. */
  midTurn?: boolean;
  /** Spoken on a live call, and the call this line ends: the call card groups these (Arturo's threads). */
  voice?: boolean;
  callEnd?: string;
  /** The page's own state for a send in flight (Arturo's live turns). */
  pending?: boolean;
  streaming?: boolean;
  sendState?: string;
  /** Tools a reply ran, by name, when the source has no ordered parts (an older Arturo reply). */
  toolNames?: string[];
  /** Agents the reply spawned. */
  spawned?: string[];
  /** A choice card the reply showed, what answered it, or that it was closed unanswered. */
  choices?: ChoiceCard;
  answered?: string;
  dismissed?: boolean;
  /** A card's own answer: the answered card shows it, so it draws no bubble of its own. */
  cardEcho?: boolean;
}

export interface ThreadThinking { kind: 'thinking'; key: string; text: string; at?: string }

/** One step of a tool run: a call, or what sat between calls. */
export type ToolRunStep = { kind: 'call'; call: ToolCallView } | ThreadThinking | ThreadResult;

export interface ThreadToolRun { kind: 'tools'; key: string; at?: string; calls: ToolCallView[]; steps: ToolRunStep[] }

export interface ThreadResult { kind: 'result'; key: string; at?: string; view?: ResultView; results: FinishedResult[] }

export interface ThreadBatch {
  kind: 'batch'; key: string; at?: string; count: number;
  entries: { agent: string; sent_ts: string; body: string; machine?: string }[];
}

export type ThreadItem = ThreadMessage | ThreadThinking | ThreadToolRun | ThreadResult | ThreadBatch;

export interface Thread { source: ThreadSource; items: ThreadItem[] }

/** Any timestamp a source writes, as ISO-8601; undefined when it is missing or unreadable. */
export function isoTime(ts: unknown): string | undefined {
  if (typeof ts === 'number' && Number.isFinite(ts)) return new Date(ts * 1000).toISOString();
  if (typeof ts === 'string' && ts && !Number.isNaN(Date.parse(ts))) return ts;
  return undefined;
}

const at = (ts: unknown) => { const t = isoTime(ts); return t ? { at: t } : {}; };

function resultItem(n: ResultNode): ThreadResult {
  return { kind: 'result', key: n.key, ...at(n.ts), ...(n.view ? { view: n.view } : {}), results: n.results };
}

/** One of the agent's render nodes (lib/transcript.ts, after results are marked and runs grouped) as a thread item. */
export function agentItem(n: GroupedNode): ThreadItem | null {
  switch (n.kind) {
    case 'user': {
      // A machine line keeps its words whole: the card that draws it reads its markers.
      const plain = !n.isSystem && !n.machine;
      const { attachments, text } = plain ? splitAttachments(n.text) : { attachments: [] as Attachment[], text: n.text };
      return {
        kind: 'message', key: n.key, role: 'user', text, ...at(n.ts), copyText: text,
        ...(attachments.length ? { attachments } : {}),
        ...(n.machine ? { machine: n.machine } : {}), ...(n.isSystem ? { system: true } : {}),
        ...(n.queued ? { queued: true } : {}), ...(n.midTurn ? { midTurn: true } : {}),
      };
    }
    case 'assistant':
      return { kind: 'message', key: n.key, role: 'assistant', text: n.text, ...at(n.ts), copyText: n.text };
    case 'thinking':
      return { kind: 'thinking', key: n.key, text: n.text, ...at(n.ts) };
    case 'tool': {
      const call = fromBlock(n);
      return { kind: 'tools', key: n.key, ...at(n.ts), calls: [call], steps: [{ kind: 'call', call }] };
    }
    case 'tool_group': {
      const steps = n.children.flatMap((c): ToolRunStep[] => {
        if (c.kind === 'tool') return [{ kind: 'call', call: fromBlock(c) }];
        if (c.kind === 'thinking') return [{ kind: 'thinking', key: c.key, text: c.text, ...at(c.ts) }];
        if (c.kind === 'result') return [resultItem(c)];
        return [];
      });
      const first = n.children[0];
      return { kind: 'tools', key: n.key, ...at(first && 'ts' in first ? first.ts : undefined),
        calls: steps.flatMap((s) => (s.kind === 'call' ? [s.call] : [])), steps };
    }
    case 'result':
      return resultItem(n);
    case 'queued_batch':
      return { kind: 'batch', key: n.key, ...at(n.ts), count: n.count, entries: n.entries };
    default:
      return null;
  }
}

/** Render nodes the agent chat already builds (finished work marked, tool runs grouped) as thread items. */
export function agentItems(nodes: GroupedNode[]): ThreadItem[] {
  return nodes.flatMap((n) => { const it = agentItem(n); return it ? [it] : []; });
}

/** An agent's transcript as a thread: the same steps the agent chat runs (pair results, mark finished work, group
 *  tool runs), then each node as an item. `agentId` names whose background jobs these are. */
export function threadFromTranscript(items: ChatItem[], agentId?: string): Thread {
  const nodes: RenderNode[] = markResults(buildRenderList(items), agentId);
  return { source: 'agent', items: agentItems(groupToolRuns(nodes)) };
}

/** A turn of Arturo's, stored (hydrateTurns) or live on the page (the same fields plus its send state). */
export type ArturoTurnLike = HydratedTurn & {
  id?: number | string; pending?: boolean; streaming?: boolean; state?: string;
  tools?: string[]; spawned?: string[];
};

function messageParts(parts?: TurnPart[]): MessagePart[] | undefined {
  if (!parts || !parts.some((p) => p.kind === 'tool')) return undefined;
  return groupParts(parts).map((g): MessagePart => (g.kind === 'tools'
    ? { kind: 'tools', calls: g.tools.map(fromPart) }
    : { kind: 'text', text: g.text }));
}

/** One of Arturo's turns as a message (a finished job's turn is a result instead: see threadFromArturo). */
export function arturoMessage(t: ArturoTurnLike, key: string): ThreadMessage {
  const role: ThreadRole = t.role === 'user' ? 'user' : 'assistant';
  const parts = role === 'assistant' ? messageParts(t.parts) : undefined;
  const attachments: Attachment[] = (t.images ?? []).map((url) => ({ kind: 'image', name: url.split('/').pop() || url, url }));
  return {
    kind: 'message', key, role, text: t.text, ...at(t.ts),
    ...(parts ? { parts } : {}),
    copyText: role === 'assistant' ? replyCopyText(t.text, t.parts) : t.text.trim(),
    ...(attachments.length ? { attachments } : {}),
    ...(t.voice ? { voice: true } : {}), ...(t.callEnd ? { callEnd: t.callEnd } : {}),
    ...(t.pending ? { pending: true } : {}), ...(t.streaming ? { streaming: true } : {}),
    ...(t.state ? { sendState: t.state } : {}),
    ...(t.tools?.length && !parts ? { toolNames: t.tools } : {}),
    ...(t.spawned?.length ? { spawned: t.spawned } : {}),
    ...(t.choices ? { choices: t.choices } : {}), ...(t.answered !== undefined ? { answered: t.answered } : {}),
    ...(t.dismissed ? { dismissed: true } : {}), ...(t.cardEcho ? { cardEcho: true } : {}),
  };
}

/** Arturo's thread as a thread: each turn a message, a finished job's turn a result; consecutive results stack, as
 *  on his page (stackRuns). `by` names him on his results. */
export function threadFromArturo(turns: ArturoTurnLike[], by?: string): Thread {
  const items: ThreadItem[] = [];
  turns.forEach((t, i) => {
    const key = `t${t.id ?? i}`;
    if (t.result) {
      const r = { ...t.result, by: t.result.by ?? by };
      const prev = items[items.length - 1];
      if (prev && prev.kind === 'result') prev.results.push(r);
      else items.push({ kind: 'result', key: `result:${t.result.key}`, ...at(t.ts), results: [r] });
      return;
    }
    items.push(arturoMessage(t, key));
  });
  return { source: 'arturo', items };
}

/** The messages of a thread, in order (what a reader would call the conversation). */
export function threadMessages(thread: Thread): ThreadMessage[] {
  return thread.items.filter((x): x is ThreadMessage => x.kind === 'message');
}
