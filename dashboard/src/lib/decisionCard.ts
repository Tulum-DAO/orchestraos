/**
 * decisionCard.ts — the rules behind the one card every decision, menu and approval is drawn as
 * (components/chat/DecisionCard.tsx), kept here so they can be tested without a browser.
 *
 * The card: a header (kind chip, question, agent, risk), the body, then either numbered option rows with an
 * always-shown "own words" field (decisions and menus) or Approve and Deny side by side (approvals).
 * Picking a row shows "Send N ✓" on it, so a send always takes two steps. Keys while the card has focus:
 * 1–9 pick, Return sends, e goes to the text field.
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

/** A write-in row (shows a text-cursor icon; picking it goes to the text field). */
export function isWriteIn(opt: Pick<MenuOption, 'input_kind'>): boolean {
  return opt.input_kind === 'free_text';
}

/** Words alone can only be sent when the menu has somewhere to put them: a write-in option, shown or not.
 *  Words with a picked option always go (the server keeps them as a note on that option). */
export function canSendWords(options: Pick<MenuOption, 'input_kind'>[]): boolean {
  return options.some(isWriteIn);
}

/** Cut short until opened: over two lines' worth, or more than one line. */
export const LONG_OPTION = 90;
export function isLongOption(opt: Pick<MenuOption, 'label' | 'detail'>): boolean {
  return (opt.label || '').length > LONG_OPTION || /\n/.test(opt.label || '') || (opt.detail || '').length > LONG_OPTION;
}

export type CardKeyAction =
  | { kind: 'pick'; n: string }
  | { kind: 'send' }
  | { kind: 'focus-text' }
  | null;

/** What a key does while the card has focus. Keys typed into a text field are the field's, except Return. */
export function keyAction(key: string, opts: { inField: boolean; options: Pick<MenuOption, 'n'>[] }): CardKeyAction {
  if (key === 'Enter') return { kind: 'send' };
  if (opts.inField) return null;
  if (key === 'e' || key === 'E') return { kind: 'focus-text' };
  if (/^[1-9]$/.test(key) && opts.options.some((o) => o.n === key)) return { kind: 'pick', n: key };
  return null;
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
