/**
 * DecisionCard — the one card every decision, menu and approval is drawn as, in an agent's chat and on the
 * Approvals and Inbox pages. One bordered card: a header (list icon, the question in bold, the kind chip, the
 * agent, "· N risk", and a ✕ on menus that can be dismissed), the card's text with its bold section labels,
 * anything extra the page adds (a learning proposal's sandbox result), then the answer area as children:
 * MenuAnswerBlock for decisions and menus, ApprovalAnswer for approvals. Once answered, the answer area is
 * replaced by the outcome line, and the outline takes the outcome's colour.
 *
 * Theme colours only (card, border, muted, info, success, warning, destructive), so light and dark both work.
 * The card takes focus when clicked, which is what scopes its keys (1–9, Return, e) to it.
 */
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { clsx } from 'clsx';
import { ListChecks, Loader2, X } from 'lucide-react';
import Markdown from './Markdown';
import { withRefs } from '../kit/RefText';
import { previewText, type DecisionTone } from '../../lib/decisions';
import { CHIP_LABEL, approvalAction, riskText, type CardKind } from '../../lib/decisionCard';
import type { ScreenAnswer } from '../../lib/screenCards';
import type { RefCardState } from '../../lib/cardRefs';
import { useScreenCard } from '../../lib/threadKit/useScreenCard';

/** The answer area inside a card says how an answer Arturo hands back is applied (ApprovalAnswer's Approve/Deny). */
type Answerer = (a: ScreenAnswer) => string | null;
const AnswerSlot = createContext<((fn: Answerer | null) => void) | null>(null);

const SUMMARY_PREVIEW = 280;

// Waiting on you: a plain outline (the chip carries the colour). Answered: the outcome's colour, never grey
// except for a card that expired or closed unanswered.
const TONE_BORDER: Record<DecisionTone, string> = {
  pending: 'border-border',
  answered: 'border-info/50',
  approved: 'border-success/50',
  denied: 'border-destructive/50',
  held: 'border-info/25',
  expired: 'border-border',
  closed: 'border-border',
};

const CHIP_CLASS: Record<CardKind, string> = {
  decision: 'bg-info/15 text-info',
  approval: 'bg-warning/15 text-warning',
};

export interface DecisionCardProps {
  kind: CardKind;
  question: string;
  agent?: string | null;
  risk?: string | null;
  body?: string | null;
  /** Shown under the body: page-specific details. */
  extra?: ReactNode;
  tone?: DecisionTone;
  /** Still waiting on an answer: draws `children` (the answer area); otherwise the outcome line. */
  open: boolean;
  outcome?: ReactNode;
  /** A ✕ at the top right. Two clicks, so a stray one never dismisses; returns an error to show, or null. */
  onDismiss?: () => Promise<string | null>;
  dismissed?: boolean;
  children?: ReactNode;
  id?: string;
  dataState?: string;
  highlighted?: boolean;
  /** Reports the card into the cards-on-screen context (lib/screenCards.ts): its id and who asked; the card adds the
   *  rest. `state`/`answer` for a card that is no longer open. */
  report?: { id: string; from?: string | null; state?: RefCardState; answer?: string | null };
}

export default function DecisionCard(p: DecisionCardProps) {
  const [more, setMore] = useState(false);
  const summary = (p.body || '').trim();
  const long = summary.length > SUMMARY_PREVIEW;
  const risk = riskText(p.risk);
  const tone = p.tone ?? 'pending';
  const frame = useRef<HTMLDivElement>(null);
  const answerer = useRef<Answerer | null>(null);
  const live = p.open && !p.dismissed;
  useScreenCard(frame, p.report ? {
    id: p.report.id, kind: p.kind, from: p.report.from ?? p.agent ?? null,
    state: p.dismissed ? 'dismissed' : live ? 'waiting' : p.report.state ?? 'answered',
    question: p.question, ...(summary ? { summary } : {}),
    ...(live && p.kind === 'approval' ? { options: ['A. Approve', 'B. Deny (needs a reason)'] } : {}),
    ...(!live && p.report.answer ? { answer: p.report.answer } : {}),
  } : null, (a) => (live && answerer.current ? answerer.current(a) : 'That card is no longer waiting.'));

  return (
    <div ref={frame} id={p.id} data-decision-card data-focused={p.highlighted || undefined} data-decision-state={p.dataState} data-tone={tone} data-kind={p.kind} tabIndex={0}
         className={clsx('rounded-2xl border bg-card text-card-foreground overflow-hidden outline-none transition-colors',
           'focus-visible:ring-2 focus-visible:ring-info/50', p.highlighted && 'ring-2 ring-info/60', TONE_BORDER[tone])}>
      <div className="px-4 pt-3.5 pb-1">
        <div className="flex items-start gap-2.5">
          <ListChecks size={16} className={clsx('mt-0.5 shrink-0', p.open ? 'text-foreground' : 'text-muted-foreground')} aria-hidden />
          <div className="min-w-0 flex-1">
            <div className={clsx('text-[15px] font-semibold leading-snug', !p.open && 'text-muted-foreground')}>{withRefs(p.question)}</div>
            <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
              <span className={clsx('text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded', CHIP_CLASS[p.kind])}>
                {CHIP_LABEL[p.kind]}
              </span>
              {p.agent && <span className="font-mono">{p.agent}</span>}
              {risk && <span>· {risk}</span>}
            </div>
          </div>
          {p.onDismiss && p.open && !p.dismissed && <DismissButton onDismiss={p.onDismiss} />}
        </div>
        {summary && (
          <div className={clsx('mt-2 text-sm leading-relaxed', p.open ? 'text-card-foreground/85' : 'text-muted-foreground')}>
            <Markdown text={more || !long ? summary : previewText(summary, SUMMARY_PREVIEW)} />
            {long && (
              <button type="button" onClick={() => setMore((v) => !v)} className="text-xs text-info hover:underline mt-0.5">
                {more ? 'less' : 'more'}
              </button>
            )}
          </div>
        )}
        {p.extra && <div className="mt-2 space-y-2">{p.extra}</div>}
      </div>
      {p.open && !p.dismissed
        ? <div className="mt-2"><AnswerSlot.Provider value={(fn) => { answerer.current = fn; }}>{p.children}</AnswerSlot.Provider></div>
        : <div className="px-4 pt-1 pb-3 text-xs text-muted-foreground" data-testid="decision-outcome">{p.dismissed ? 'Dismissed' : p.outcome}</div>}
    </div>
  );
}

/** A card in an agent's chat: on the agent's side and message-sized, the way Arturo's cards sit under his
 *  replies, instead of spanning the column like a page element. Approvals and Inbox are lists and stay full width. */
export function AgentSide({ children }: { children: ReactNode }) {
  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[min(92%,480px)]">{children}</div>
    </div>
  );
}

export function DismissButton({ onDismiss }: { onDismiss: () => Promise<string | null> }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const click = async () => {
    if (busy) return;
    if (!armed) {
      setArmed(true); setError(null);
      timer.current = setTimeout(() => setArmed(false), 5000);
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    setBusy(true);
    const err = await onDismiss();
    setBusy(false); setArmed(false); setError(err);
  };
  return (
    <div className="touch-compact shrink-0 -mr-1.5 -mt-1 flex flex-col items-end">
      {armed ? (
        <button type="button" onClick={() => { void click(); }} aria-label="Dismiss this menu"
                className="hit-44 flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full bg-muted text-foreground hover:bg-accent">
          {busy ? <Loader2 size={12} className="animate-spin" /> : null}Dismiss ✓
        </button>
      ) : (
        <button type="button" onClick={() => { void click(); }} aria-label="Dismiss" title="Dismiss"
                className="hit-44 w-7 h-7 flex items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground">
          <X size={15} />
        </button>
      )}
      {error && <div className="text-[11px] text-warning mt-1 max-w-[220px] text-right">{error}</div>}
    </div>
  );
}

/** An approval's answer area: Approve and Deny side by side, and the text field that carries a deny reason. */
export function ApprovalAnswer({ onApprove, onDeny, sending, error }: {
  onApprove: () => void; onDeny: (reason: string) => void; sending?: boolean; error?: string | null;
}) {
  const [text, setText] = useState('');
  const [needReason, setNeedReason] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  const act = (which: 'approve' | 'deny') => {
    const a = approvalAction(which, text);
    if (a.kind === 'approve') onApprove();
    else if (a.kind === 'deny') onDeny(a.reason);
    else { setNeedReason(true); field.current?.focus(); }
  };
  // An answer Arturo hands back presses the same buttons: Approve, or Deny with the words as its reason.
  const slot = useContext(AnswerSlot);
  const latest = useRef({ sending, onApprove, onDeny });
  useEffect(() => { latest.current = { sending, onApprove, onDeny }; });
  useEffect(() => {
    if (!slot) return;
    slot((ans) => {
      const said = (ans.picks[0] || '').trim().toLowerCase();
      const pick = said.match(/^([ab])[.)]/)?.[1] ?? said;
      const which = /^(a|approve|approved|yes)$/.test(pick) ? 'approve' : /^(b|deny|denied|no)$/.test(pick) ? 'deny' : null;
      if (!which) return 'Approve or deny: those are the only answers this card takes.';
      if (latest.current.sending) return 'That card is already being answered.';
      const a = approvalAction(which, ans.words ?? '');
      if (a.kind === 'need-reason') return 'A deny needs a reason the agent will see.';
      if (a.kind === 'approve') latest.current.onApprove(); else latest.current.onDeny(a.reason);
      return null;
    });
    return () => slot(null);
  }, [slot]);
  return (
    <div className="border-t border-border px-4 py-3 space-y-2.5">
      <div className="grid grid-cols-2 gap-2">
        <button type="button" disabled={sending} onClick={() => act('approve')}
                className="flex items-center justify-center gap-1.5 rounded-xl py-2.5 text-sm font-semibold bg-success text-success-foreground hover:opacity-90 disabled:opacity-50">
          {sending && <Loader2 size={14} className="animate-spin" />}Approve
        </button>
        <button type="button" disabled={sending} onClick={() => act('deny')}
                className="rounded-xl py-2.5 text-sm font-semibold border border-destructive/60 text-destructive hover:bg-destructive/10 disabled:opacity-50">
          Deny
        </button>
      </div>
      <input ref={field} value={text} disabled={sending}
             onChange={(e) => { setText(e.target.value); if (e.target.value.trim()) setNeedReason(false); }}
             onKeyDown={(e) => { if (e.key === 'Enter' && text.trim()) act('deny'); }}
             placeholder="Reason, if you deny (the agent sees it)"
             className={clsx('w-full rounded-xl border bg-transparent px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:border-info',
               needReason ? 'border-destructive/60' : 'border-border')} />
      {needReason && <div className="text-[11px] text-destructive">Add a reason to deny.</div>}
      {error && <div className="text-[11px] text-warning">{error}</div>}
    </div>
  );
}
