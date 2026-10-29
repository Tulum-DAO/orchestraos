/**
 * decisionCard.ts — the rules behind the one card every decision, menu and approval is drawn as
 * (components/chat/DecisionCard.tsx), kept here so they can be tested without a browser.
 *
 * The card: a header (kind chip, question, agent, risk), the body, then Approve and Deny side by side
 * (approvals). Questions (menus, decisions) are the one question card instead (lib/multiPart.ts).
 */
import type { MenuOption } from './menuAnswer';

export type CardKind = 'decision' | 'approval';

export const CHIP_LABEL: Record<CardKind, string> = { decision: 'Decision', approval: 'Approval' };

/** Claude Code adds these to every menu; the card's own-words field does their job, so they are not shown. */
const BUILT_IN_EXTRA = /^\s*(type something\.?|chat about this\.?)\s*$/i;

export function isBuiltInExtra(opt: Pick<MenuOption, 'label'>): boolean {
  return BUILT_IN_EXTRA.test(opt.label || '');
}

/** The rows the card draws: every option except Claude Code's built-in extras. */
export function visibleOptions<T extends Pick<MenuOption, 'label'>>(options: T[]): T[] {
  return options.filter((o) => !isBuiltInExtra(o));
}

// The write-in and chat label classes, for live menus whose options arrive without a kind. Must match
// watch_gateway._FREE_TEXT_CLASSES / _CHAT_CLASS (and agent-status.py's _FREE_TEXT_LABELS).
const FREE_TEXT_LABELS = ['type something', 'write-in', 'write in', 'custom answer', 'other'];
const CHAT_LABEL = 'chat about this';

/** Each option with its kind: kept when it has one, else read from its label the way the server does. */
export function withInputKinds<T extends Pick<MenuOption, 'label' | 'input_kind'>>(options: T[]): T[] {
  return options.map((o) => {
    if (o.input_kind) return o;
    const l = (o.label || '').trim().replace(/\.+$/, '').toLowerCase();
    return { ...o, input_kind: FREE_TEXT_LABELS.includes(l) ? 'free_text' : l === CHAT_LABEL ? 'chat' : 'direct' };
  });
}

/** Words alone can only be sent when the menu has somewhere to put them: a write-in option, shown or not.
 *  Words with a picked option always go (the server keeps them as a note on that option). */
export function canSendWords(options: Pick<MenuOption, 'input_kind'>[]): boolean {
  return options.some((o) => o.input_kind === 'free_text');
}

/** "medium risk" from the card's risk level; nothing when it has none. */
export function riskText(risk: string | null | undefined): string {
  const r = (risk || '').trim().toLowerCase();
  return r ? `${r} risk` : '';
}

export type ApprovalAction = { kind: 'approve' } | { kind: 'deny'; reason: string } | { kind: 'need-reason' };

/** Approve goes at once. Deny needs a reason (the agent sees it), taken from the card's text field. */
export function approvalAction(which: 'approve' | 'deny', text: string): ApprovalAction {
  if (which === 'approve') return { kind: 'approve' };
  const reason = text.trim();
  return reason ? { kind: 'deny', reason } : { kind: 'need-reason' };
}

const baseId = (id: string | null | undefined) => String(id || '').replace(/-(?:gen|g)\d+$/, '');
const norm = (s: string | null | undefined) => String(s || '').replace(/\s+/g, ' ').trim();

export interface MenuCardRow {
  id: string;
  kind?: string | null;
  agent_id?: string | null;
  title?: string | null;
  menu?: { question?: string; read_only?: boolean } | null;
}

/** The card the server keeps for an agent's live menu (it appears a few seconds after the menu does), which is
 *  the only way words reach a live menu. Matched by agent and question; none while it doesn't exist yet. */
export function linkedMenuCard<T extends MenuCardRow>(items: T[], agentId: string, question: string): T | undefined {
  const q = norm(question);
  if (!q) return undefined;
  return items.find((i) => i.kind === 'menu' && !i.menu?.read_only && baseId(i.agent_id) === baseId(agentId)
    && norm(i.menu?.question || i.title) === q);
}

/** A decision row bridged from an agent's terminal menu (menu_bridge mints `menu:<session>:<digest>` op_keys): the
 *  agent is blocked on it and the pane takes a pick OR words, never both. Rows an agent posts itself have other keys. */
export function isTerminalMenu(row: { op_key?: string | null }): boolean {
  return /^menu:/.test(row.op_key || '');
}
