import { useState } from 'react';
import { clsx } from 'clsx';
import { CheckCircle2, Clock, HelpCircle, Loader2, XCircle } from 'lucide-react';
import Markdown from './Markdown';
import MenuAnswerBlock from './MenuAnswerCard';
import { post } from '../../lib/api';
import { decisionTone, menuAnswerLine, outcomeLine, previewText, type Decision, type DecisionTone } from '../../lib/decisions';
import {
  buildMenuAnswerPayload, initialMenuAnswerState, selectOption, setText, type MenuAnswerState,
} from '../../lib/menuAnswer';

/**
 * DecisionBubble — one of the agent's approval cards, in its conversation, as a message: under the message
 * it belongs to, answerable right here, and kept, answered, as history once it is answered from anywhere.
 * The server state is the only truth; `onAnswered` asks the list to refresh. An answered in-agent menu
 * (`menu_answer`) is a single line instead: what was asked, and what was chosen or that it was dismissed.
 */
const SUMMARY_PREVIEW = 280;

// The card's solid outline by outcome (lib/decisions decisionTone). Pending keeps the amber that means
// "waiting on you" everywhere else; once answered it never fades to grey: blue answered, green approved,
// red denied, a dimmer blue while the answer is held for its agent, grey only for an expired card.
const TONE_BORDER: Record<DecisionTone, string> = {
  pending: 'border-amber-500/40 bg-amber-500/[0.06]',
  answered: 'border-blue-500/50 bg-neutral-900/40',
  approved: 'border-green-500/50 bg-neutral-900/40',
  denied: 'border-red-500/50 bg-neutral-900/40',
  held: 'border-blue-500/25 bg-neutral-900/40',
  expired: 'border-neutral-600 bg-neutral-900/40',
  closed: 'border-neutral-800 bg-neutral-900/40',
};
const TONE_TEXT: Record<DecisionTone, string> = {
  pending: 'text-amber-300', answered: 'text-blue-300', approved: 'text-green-400', denied: 'text-red-400',
  held: 'text-blue-300/60', expired: 'text-neutral-500', closed: 'text-neutral-500',
};

function MenuAnswerLine({ d }: { d: Decision }) {
  const dismissed = d.answer === 'dismiss';
  return (
    <div id={`decision-${d.id}`} data-decision-state="answered" data-menu-answer
         className="flex justify-center">
      <div className={clsx('max-w-[92%] text-center text-[11px] text-neutral-400 px-3 py-1 rounded-full border bg-neutral-900/40',
        dismissed ? 'border-neutral-700' : 'border-blue-500/50')}>
        <span className="text-neutral-300">{menuAnswerLine(d)}</span>
        {!dismissed && d.question && <span className="text-neutral-500"> · {previewText(d.question, 90)}</span>}
      </div>
    </div>
  );
}

export default function DecisionBubble({ decision: d, onAnswered }: { decision: Decision; onAnswered: () => void }) {
  if (d.kind === 'menu_answer') return <MenuAnswerLine d={d} />;
  return <DecisionCard decision={d} onAnswered={onAnswered} />;
}

function DecisionCard({ decision: d, onAnswered }: { decision: Decision; onAnswered: () => void }) {
  const [state, setState] = useState<MenuAnswerState>(initialMenuAnswerState);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [more, setMore] = useState(false);
  const [denying, setDenying] = useState(false);
  const [reason, setReason] = useState('');
  const open = d.state === 'pending';
  const isMenu = d.kind === 'menu' && (d.menu?.options?.length ?? 0) > 0;
  const summary = (d.summary || '').trim();
  const long = summary.length > SUMMARY_PREVIEW;

  const send = async (fn: () => Promise<unknown>) => {
    setSending(true); setError(undefined);
    try { await fn(); onAnswered(); }
    catch (e) { setError(e instanceof Error ? e.message.replace(/^API [^:]+: /, 'Could not send: ') : 'Could not send'); }
    finally { setSending(false); }
  };
  const respond = () => {
    const payload = buildMenuAnswerPayload(state);
    if (payload) void send(() => post(`/approvals/unified/${encodeURIComponent(d.id)}/answer`, payload));
  };

  const tone = decisionTone(d);
  return (
    <div id={`decision-${d.id}`} data-decision-state={d.state} data-tone={tone}
         className={clsx('rounded-xl border px-3.5 py-3 transition-colors', TONE_BORDER[tone])}>
      <div className="flex items-start gap-2">
        {open ? <HelpCircle size={15} className="text-amber-300 mt-0.5 shrink-0" />
          : tone === 'denied' ? <XCircle size={15} className={clsx('mt-0.5 shrink-0', TONE_TEXT[tone])} />
          : d.state === 'answered' ? <CheckCircle2 size={15} className={clsx('mt-0.5 shrink-0', TONE_TEXT[tone])} />
          : <Clock size={15} className="text-neutral-500 mt-0.5 shrink-0" />}
        <div className="min-w-0 flex-1">
          {/* The risk tag rides at the END of the title's text, so in a narrow pane it wraps with the words
              instead of dropping to a line of its own. */}
          <div className={clsx('text-sm font-medium', open ? 'text-neutral-100' : 'text-neutral-300')}>
            {d.question}
            {d.risk_level && (
              <span className={clsx('ml-2 inline-block align-middle whitespace-nowrap text-[10px] font-normal px-1.5 py-0.5 rounded',
                d.risk_level === 'high' || d.risk_level === 'critical' ? 'bg-red-500/15 text-red-300'
                  : d.risk_level === 'medium' ? 'bg-amber-500/15 text-amber-300' : 'bg-neutral-800 text-neutral-400')}>
                {d.risk_level} risk
              </span>
            )}
          </div>
          {summary && (
            <div className={clsx('text-xs mt-1', open ? 'text-neutral-300' : 'text-neutral-500')}>
              <Markdown text={more || !long ? summary : previewText(summary, SUMMARY_PREVIEW)} />
              {long && (
                <button type="button" onClick={() => setMore((v) => !v)} className="text-[11px] text-blue-400 hover:text-blue-300 mt-0.5">
                  {more ? 'less' : 'more'}
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {open ? (
        <div className="mt-2 pl-6">
          {isMenu ? (
            <MenuAnswerBlock item={{ id: d.id, menu: d.menu as never }} state={state} sending={sending} error={error}
              onSelect={(n) => setState((s) => selectOption(s, n))}
              onText={(t) => setState((s) => setText(s, t))}
              onRespond={respond} />
          ) : denying ? (
            <div className="space-y-1.5">
              <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why not? (the agent sees this)"
                     className="w-full bg-neutral-950 border border-neutral-800 rounded-lg px-3 py-2 text-xs text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-blue-500" />
              <div className="flex gap-2">
                <button type="button" disabled={sending || !reason.trim()}
                        onClick={() => void send(() => post(`/approvals/unified/${encodeURIComponent(d.id)}/deny`, { reason: reason.trim() }))}
                        className="text-xs px-3 py-1.5 rounded-lg bg-red-600/80 hover:bg-red-600 text-white disabled:opacity-50">Deny</button>
                <button type="button" onClick={() => setDenying(false)} className="text-xs px-3 py-1.5 rounded-lg text-neutral-400 hover:text-neutral-200">Cancel</button>
              </div>
              {error && <div className="text-[11px] text-amber-400/90">{error}</div>}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <button type="button" disabled={sending}
                      onClick={() => void send(() => post(`/approvals/unified/${encodeURIComponent(d.id)}/approve`, {}))}
                      className="text-xs px-3 py-1.5 rounded-lg bg-green-600 hover:bg-green-500 text-white disabled:opacity-50 flex items-center gap-1.5">
                {sending && <Loader2 size={12} className="animate-spin" />}Approve
              </button>
              <button type="button" disabled={sending} onClick={() => setDenying(true)}
                      className="text-xs px-3 py-1.5 rounded-lg bg-neutral-800 hover:bg-neutral-700 text-neutral-200 disabled:opacity-50">Deny</button>
              {error && <span className="text-[11px] text-amber-400/90">{error}</span>}
            </div>
          )}
        </div>
      ) : (
        // One compact row tied to the card: the choice · who · where · when · how fast it reached the agent.
        <div className="mt-1.5 pl-6 text-[11px] text-neutral-400" data-testid="decision-outcome">{outcomeLine(d)}</div>
      )}
    </div>
  );
}
