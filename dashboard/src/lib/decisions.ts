/**
 * decisions.ts — an agent's approval cards as bubbles in its own conversation: each one sits under the
 * message it belongs to, like any other message, and stays there once answered, from ANY surface (this
 * chat, the Approvals page, the notifications menu, the phone, Arturo), as part of the history. The server
 * is the truth (GET /api/agents/:id/decisions); the bubble only draws its state.
 *
 * The same feed carries `menu_answer` entries: an in-agent menu that was answered or dismissed. The live
 * menu leaves no line in the transcript, so this one-line record is what keeps it in the conversation.
 */
import type { GroupedNode } from './toolGroups';
import type { MenuOption } from './menuAnswer';
import { operatorUserId } from './runtimeConfig';

export type DecisionState = 'pending' | 'answered' | 'expired' | 'withdrawn' | 'dismissed' | 'superseded' | string;

export interface Decision {
  id: string;
  from_agent: string;
  kind: string | null;
  state: DecisionState;
  question: string;
  summary: string | null;
  options: string[];
  menu: { question?: string; options?: MenuOption[] } | null;
  risk_level: string | null;
  reversibility: string | null;
  answer: string | null;
  answer_text: string | null;
  option_n: string | null;
  answered_by: string | null;
  answer_surface: string | null;
  answer_device: string | null;
  created_at: string;
  answered_at: string | null;
  expires_at: string | null;
  /** When the answer reached the agent's pane (the approval loop's delivery). */
  resumed_at?: string | null;
  /** answered cards: 'held' while the answer waits for its agent, 'delivered' once it is in the pane. */
  delivery?: 'held' | 'delivered' | null;
}

export async function fetchDecisions(agentId: string): Promise<Decision[]> {
  const res = await fetch(`/api/agents/${encodeURIComponent(agentId)}/decisions`, { signal: AbortSignal.timeout(12_000) });
  if (!res.ok) throw new Error(`decisions: ${res.status}`);
  const json = await res.json();
  const all: Decision[] = Array.isArray(json.decisions) ? json.decisions : [];
  return currentGenerationOnly(all, typeof json.generation_started_at === 'string' ? json.generation_started_at : null);
}

/** Only the cards filed in the agent's current generation. The server already leaves the others
 *  out; this holds the line against an older server too. An earlier generation's card is never drawn
 *  in the successor's chat (not even pinned at the top): it lives on the Approvals page. */
export function currentGenerationOnly(ds: Decision[], startedAt: string | null): Decision[] {
  const start = startedAt ? Date.parse(startedAt) : NaN;
  if (Number.isNaN(start)) return ds;
  return ds.filter((d) => { const c = Date.parse(d.created_at); return Number.isNaN(c) || c >= start; });
}

const SURFACE: Record<string, string> = {
  web: 'the dashboard', phone: 'the phone app', watch: 'the watch', gateway: 'an app',
  agent_cli: "the agent's terminal", arturo: 'the assistant',
};

function clock(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** What was chosen: the option's label (and any written answer), or the bare verb. */
export function chosenText(d: Decision): string {
  const opt = d.option_n ? (d.menu?.options || []).find((o) => o.n === d.option_n) : undefined;
  const label = opt && !/write-in|other/i.test(opt.label) ? opt.label : '';
  const text = (d.answer_text || '').trim();
  // Typed words are shown as plain text, never in quotes.
  if (label && text) return `${label} — ${text}`;
  if (label) return label;
  if (text) return text;
  if (d.answer === 'approve') return 'Approved';
  if (d.answer === 'deny') return 'Denied';
  return d.answer || 'Answered';
}

/** The lasting line for an answered or dismissed in-agent menu ("You dismissed: …" / "Chose: 2 · …"). */
export function menuAnswerLine(d: Decision): string {
  // answered_by is set only when an agent answered; a person's answer has none, or this install's user id.
  const by = !d.answered_by || d.answered_by === operatorUserId() ? null : d.answered_by;
  const who = by ? `${by} · ` : '';
  if (d.answer === 'dismiss') return `${by ? `${by} dismissed` : 'You dismissed'}: ${d.question}`;
  const opt = d.option_n ? (d.menu?.options || []).find((o) => o.n === d.option_n) : undefined;
  const text = (d.answer_text || '').trim();
  const label = opt?.label && !(text && /write-in|other|type something/i.test(opt.label)) ? ` · ${opt.label}` : '';
  return `${who}Chose: ${d.option_n ?? '?'}${label}${text ? ` — ${text}` : ''}`;
}

/** How fast an answer reached its agent: "delivered in 2s", "delivery held" while it waits, '' when
 *  unknown (a card answered before delivery times were recorded, or not delivered by the loop). */
export function deliveryText(d: Decision): string {
  if (d.delivery === 'held') return 'delivery held';
  if (d.delivery !== 'delivered') return '';
  const a = d.answered_at ? Date.parse(d.answered_at) : NaN;
  const r = d.resumed_at ? Date.parse(d.resumed_at) : NaN;
  if (Number.isNaN(a) || Number.isNaN(r) || r < a) return 'delivered';
  const s = (r - a) / 1000;
  return s < 1 ? 'delivered in <1s' : s < 90 ? `delivered in ${Math.round(s)}s` : `delivered in ${Math.round(s / 60)}m`;
}

/** The answered card's outline, by outcome: blue answered, green approved, red denied, a dimmer blue
 *  while delivery is held, grey only when it expired. A card withdrawn, dismissed or replaced has no outcome and
 *  keeps the plain card edge. */
export type DecisionTone = 'pending' | 'answered' | 'approved' | 'denied' | 'held' | 'expired' | 'closed';
export function decisionTone(d: Decision): DecisionTone {
  if (d.state === 'pending') return 'pending';
  if (d.state === 'expired') return 'expired';
  if (d.state !== 'answered') return 'closed';
  if (d.delivery === 'held') return 'held';
  const a = (d.answer || '').toLowerCase();
  if (a === 'approve' || a === 'accept') return 'approved';
  if (a === 'deny' || a === 'reject') return 'denied';
  return 'answered';
}

/** The line an answered (or closed) bubble shows instead of its buttons: one compact row, the
 *  choice · who answered · where · when · how fast it reached the agent. */
/** Who answered, where, when and how fast it reached the agent: "Answered by you · the dashboard · 10:42". */
export function answeredByLine(d: Decision): string {
  // answered_by is the answering principal: this install's operator (you), or an agent answering its own gate
  const who = !d.answered_by || d.answered_by === operatorUserId() ? 'you' : d.answered_by;
  const where = d.answer_surface && SURFACE[d.answer_surface] ? ` · ${SURFACE[d.answer_surface]}` : '';
  const when = clock(d.answered_at);
  const speed = deliveryText(d);
  return `Answered by ${who}${where}${when ? ` · ${when}` : ''}${speed ? ` · ${speed}` : ''}`;
}

export function outcomeLine(d: Decision): string {
  if (d.state === 'answered') {
    const by = answeredByLine(d);
    return `${chosenText(d)} · a${by.slice(1)}`;
  }
  if (d.state === 'expired') return `Expired unanswered${d.expires_at ? ` · ${clock(d.expires_at)}` : ''}`;
  if (d.state === 'withdrawn') return 'Withdrawn by the agent';
  if (d.state === 'dismissed') { const when = clock(d.answered_at); return `Dismissed by you${when ? ` · ${when}` : ''}`; }
  if (d.state === 'superseded') return 'Replaced by a newer question';
  return '';
}

function nodeTs(n: GroupedNode): number {
  const ts = n.kind === 'tool_group' ? n.children[n.children.length - 1]?.ts : (n as { ts?: string }).ts;
  const t = ts ? Date.parse(ts) : NaN;
  return Number.isNaN(t) ? -Infinity : t;
}

const isOperatorLine = (n: GroupedNode) => n.kind === 'user' && !(n as { isSystem?: boolean }).isSystem;

export type FeedEntry = { kind: 'node'; node: GroupedNode } | { kind: 'decision'; decision: Decision };

/** Where a decision goes: under the agent's message that names it (its id), which is the message announcing
 *  it; else at the end of the agent turn it was filed in (before the operator's next message), since a card
 *  is usually filed mid-turn, before the reply that explains it; else at the end. Returns the index of the
 *  node it follows (-1 = before everything). */
export function anchorIndex(nodes: GroupedNode[], d: Decision): number {
  const named = nodes.findIndex((n) => n.kind === 'assistant' && (n as { text?: string }).text?.includes(d.id));
  if (named >= 0) return named;
  const filed = Date.parse(d.created_at);
  let i = nodes.findIndex((n) => nodeTs(n) >= filed);
  if (i < 0) return nodes.length - 1;
  if (isOperatorLine(nodes[i])) return i - 1;            // filed between turns: right before the next message
  while (i + 1 < nodes.length && !isOperatorLine(nodes[i + 1])) i += 1;
  return i;
}

/** The conversation with each decision placed at its anchor (anchorIndex), in filing order. */
export function interleave(nodes: GroupedNode[], decisions: Decision[]): FeedEntry[] {
  const after = new Map<number, Decision[]>();
  const ds = [...decisions].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
  for (const d of ds) {
    const k = anchorIndex(nodes, d);
    after.set(k, [...(after.get(k) ?? []), d]);
  }
  const out: FeedEntry[] = [];
  for (const d of after.get(-1) ?? []) out.push({ kind: 'decision', decision: d });
  nodes.forEach((node, i) => {
    out.push({ kind: 'node', node });
    for (const d of after.get(i) ?? []) out.push({ kind: 'decision', decision: d });
  });
  return out;
}

/** A long summary's preview: cut at a line break (else a word), never inside Markdown emphasis or code, so
 *  the preview never shows a stray "**" or backtick. */
export function previewText(text: string, max = 280): string {
  const t = text.trim();
  if (t.length <= max) return t;
  let cut = t.lastIndexOf('\n', max);
  if (cut < max / 3) cut = t.lastIndexOf(' ', max);
  if (cut < max / 3) cut = max;
  let out = t.slice(0, cut).trimEnd();
  for (const mark of ['**', '`']) {
    const parts = out.split(mark);
    if (parts.length % 2 === 0) out = parts.slice(0, -1).join(mark).trimEnd();   // an unclosed one: drop it
  }
  return `${out.replace(/[\s,;:—-]+$/, '')}…`;
}

/** Ask the open agent view to bring a decision into sight: a named one (the bell), or with no id
 *  its first open one ("Needs you"). */
export const FOCUS_DECISION_EVENT = 'orchestra:focus-decision';
export interface FocusRequest { agentId: string; decisionId?: string }
let pendingFocus: (FocusRequest & { at: number }) | null = null;
export function focusDecision(agentId: string, decisionId?: string): void {
  pendingFocus = { agentId, decisionId, at: Date.now() };     // the view may not be open yet: it asks when it is
  try { window.dispatchEvent(new CustomEvent<FocusRequest>(FOCUS_DECISION_EVENT, { detail: { agentId, decisionId } })); } catch { /* no window */ }
}
/** A focus request for this agent waiting to be shown, taken once, good for 10 s; else null. */
export function takeFocusRequest(agentId: string, now = Date.now()): FocusRequest | null {
  if (!pendingFocus || pendingFocus.agentId !== agentId || now - pendingFocus.at > 10_000) return null;
  const req: FocusRequest = { agentId: pendingFocus.agentId, decisionId: pendingFocus.decisionId };
  pendingFocus = null;
  return req;
}

/** Which decision a focus request shows: the named one, else the first open one. */
export function focusTarget(ds: Decision[], decisionId?: string): Decision | undefined {
  return decisionId ? ds.find((d) => d.id === decisionId) : ds.find((d) => d.state === 'pending');
}

export interface PendingCard { id: string; agent_id?: string | null; created_at?: string; status?: string }

/**
 * Where a bell row for `agent` goes: its OLDEST waiting card, opened where that card lives.
 *   chat       the card is drawn in the agent's chat (its current generation; `chatIds`, from
 *              GET /api/agents/:id/decisions) -> the chat, scrolled to the card.
 *   approvals  anything else: a card from before the current generation began, or an on-screen
 *              menu mirror (the chat draws those as the live menu, not as a card) -> the Approvals
 *              page, focused on it.
 *   null       nothing of this agent's is waiting any more (answered elsewhere).
 */
export function bellTarget(items: PendingCard[], agent: string, chatIds: ReadonlySet<string>):
    { where: 'chat' | 'approvals'; id: string } | null {
  const mine = items
    .filter((it) => it.agent_id === agent && (!it.status || it.status === 'pending'))
    .sort((a, b) => (Date.parse(a.created_at ?? '') || 0) - (Date.parse(b.created_at ?? '') || 0));
  const oldest = mine[0];
  if (!oldest) return null;
  return { where: chatIds.has(oldest.id) ? 'chat' : 'approvals', id: oldest.id };
}

/** The Approvals page's one-shot focus: /approvals?focus=<id>. */
export const approvalsFocusPath = (id: string) => `/approvals?focus=${encodeURIComponent(id)}`;

/** Pending approvals per agent, most first: one row each in the panel, which opens that agent's
 *  conversation at its open card, as "Needs you" does. */
export function waitingByAgent(items: { agent_id?: string | null; status?: string }[]): { agent: string; count: number }[] {
  const by = new Map<string, number>();
  for (const it of items) {
    if (it.status && it.status !== 'pending') continue;
    if (it.agent_id) by.set(it.agent_id, (by.get(it.agent_id) ?? 0) + 1);
  }
  return [...by].map(([agent, count]) => ({ agent, count })).sort((a, b) => b.count - a.count || a.agent.localeCompare(b.agent));
}

/**
 * The bell's click, decided on FRESH reads (the panel's list can be seconds old): the agent's pending
 * cards, and the ids its chat draws. A failed read of the chat's cards sends the click to the Approvals
 * page, where every card is drawn, rather than to a chat that may not show it.
 */
export async function resolveBellJump(
  agent: string,
  readPending: () => Promise<PendingCard[]>,
  readChatIds: (agent: string) => Promise<string[]>,
): Promise<{ where: 'chat' | 'approvals'; id: string } | null> {
  const items = await readPending();
  let chat: string[] = [];
  try { chat = await readChatIds(agent); } catch { chat = []; }
  return bellTarget(items, agent, new Set(chat));
}
