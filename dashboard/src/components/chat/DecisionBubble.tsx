import { useState } from 'react';
import { clsx } from 'clsx';
import MenuAnswerBlock from './MenuAnswerCard';
import DecisionCard, { AgentSide, ApprovalAnswer } from './DecisionCard';
import { dismissMenuCard, post } from '../../lib/api';
import { decisionTone, menuAnswerLine, outcomeLine, previewText, type Decision } from '../../lib/decisions';
import {
  buildMenuAnswerPayload, initialMenuAnswerState, selectOption, setText, type MenuAnswerState,
} from '../../lib/menuAnswer';

/**
 * DecisionBubble — one of the agent's approval cards, in its conversation, as a message: under the message
 * it belongs to, answerable right here, and kept, answered, as history once it is answered from anywhere.
 * The server state is the only truth; `onAnswered` asks the list to refresh. An answered in-agent menu
 * (`menu_answer`) is a single line instead: what was asked, and what was chosen or that it was dismissed.
 */
function MenuAnswerLine({ d }: { d: Decision }) {
  const dismissed = d.answer === 'dismiss';
  return (
    <div id={`decision-${d.id}`} data-decision-state="answered" data-menu-answer
         className="flex justify-center">
      <div className={clsx('max-w-[92%] text-center text-[11px] text-muted-foreground px-3 py-1 rounded-full border bg-card',
        dismissed ? 'border-border' : 'border-info/50')}>
        <span className="text-card-foreground/85">{menuAnswerLine(d)}</span>
        {!dismissed && d.question && <span> · {previewText(d.question, 90)}</span>}
      </div>
    </div>
  );
}

export default function DecisionBubble({ decision: d, onAnswered }: { decision: Decision; onAnswered: () => void }) {
  if (d.kind === 'menu_answer') return <MenuAnswerLine d={d} />;
  return <DecisionCardBubble decision={d} onAnswered={onAnswered} />;
}

function DecisionCardBubble({ decision: d, onAnswered }: { decision: Decision; onAnswered: () => void }) {
  const [state, setState] = useState<MenuAnswerState>(initialMenuAnswerState);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const open = d.state === 'pending';
  const isMenu = d.kind === 'menu' && (d.menu?.options?.length ?? 0) > 0;

  const send = async (fn: () => Promise<unknown>) => {
    setSending(true); setError(undefined);
    try { await fn(); onAnswered(); }
    catch (e) { setError(e instanceof Error ? e.message.replace(/^API [^:]+: /, 'Could not send: ') : 'Could not send'); }
    finally { setSending(false); }
  };
  const path = (verb: string) => `/approvals/unified/${encodeURIComponent(d.id)}/${verb}`;
  const respond = () => {
    const payload = buildMenuAnswerPayload(state);
    if (payload) void send(() => post(path('answer'), payload));
  };

  return (
    <AgentSide>
      <DecisionCard id={`decision-${d.id}`} dataState={d.state} tone={decisionTone(d)}
        kind={isMenu ? 'decision' : 'approval'} question={d.question} agent={d.from_agent} risk={d.risk_level}
        body={d.summary} open={open} outcome={outcomeLine(d)}
        onDismiss={isMenu ? async () => {
          const err = await dismissMenuCard(d.id, d.from_agent, d.menu?.question || d.question);
          if (!err) onAnswered();
          return err;
        } : undefined}>
        {isMenu ? (
          <MenuAnswerBlock item={{ id: d.id, menu: d.menu as never }} state={state} sending={sending} error={error}
            onSelect={(n) => setState((s) => selectOption(s, n))}
            onText={(t) => setState((s) => setText(s, t))}
            onRespond={respond} />
        ) : (
          <ApprovalAnswer sending={sending} error={error}
            onApprove={() => void send(() => post(path('approve'), {}))}
            onDeny={(reason) => void send(() => post(path('deny'), { reason }))} />
        )}
      </DecisionCard>
    </AgentSide>
  );
}
