import { useState } from 'react';
import { clsx } from 'clsx';
import DecisionCard, { AgentSide, ApprovalAnswer } from './DecisionCard';
import DecisionQuestion, { AnsweredDecision } from './DecisionQuestion';
import { dismissMenuCard, post } from '../../lib/api';
import { answeredByLine, chosenText, decisionTone, menuAnswerLine, outcomeLine, previewText, type Decision } from '../../lib/decisions';
import { refState } from '../../lib/cardRefs';
import { PopHeaderControls } from '../kit/CardPopControls';
import { useCardPop } from '../kit/cardPopContext';

/**
 * DecisionBubble — one of the agent's approval cards, in its conversation, as a message: under the message
 * it belongs to, answerable right here, and kept, answered, as history once it is answered from anywhere. A question
 * (kind 'menu') is the one question card (DecisionQuestion); an approval keeps Approve and Deny.
 * The server state is the only truth; `onAnswered` asks the list to refresh. An answered in-agent menu
 * (`menu_answer`) is a single line instead: what was asked, and what was chosen or that it was dismissed; in a card's
 * popover the line ends with the popover's Go to card and Close, as every popover card's top right does.
 */
function MenuAnswerLine({ d }: { d: Decision }) {
  const dismissed = d.answer === 'dismiss';
  const pop = useCardPop();
  const words = (
    <>
      <span className="text-card-foreground/85">{menuAnswerLine(d)}</span>
      {!dismissed && d.question && <span> · {previewText(d.question, 90)}</span>}
    </>
  );
  return (
    <div id={`decision-${d.id}`} data-decision-state="answered" data-menu-answer
         className="flex justify-center">
      {/* In a popover the pill is tall and wide enough to hold the controls' 44px tap areas (the popover clips). */}
      <div className={clsx('max-w-[92%] text-center text-px11 text-muted-foreground rounded-full border bg-card',
        dismissed ? 'border-border' : 'border-info/50', pop ? 'flex items-center gap-2 pl-3 pr-2 py-2' : 'px-3 py-1')}>
        {pop ? <span className="min-w-0">{words}</span> : words}
        <PopHeaderControls />
      </div>
    </div>
  );
}

export default function DecisionBubble({ decision: d, onAnswered }: { decision: Decision; onAnswered: () => void }) {
  if (d.kind === 'menu_answer') return <MenuAnswerLine d={d} />;
  return <DecisionCardBubble decision={d} onAnswered={onAnswered} />;
}

function DecisionCardBubble({ decision: d, onAnswered }: { decision: Decision; onAnswered: () => void }) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const open = d.state === 'pending';
  const isMenu = d.kind === 'menu' && (d.menu?.options?.length ?? 0) > 0;

  // A question (a menu card) is the one question card, open or answered; an approval keeps Approve and Deny.
  if (isMenu && (open || d.state === 'answered')) {
    const row = { id: d.id, question: d.question, agent: d.from_agent, risk: d.risk_level, body: d.summary,
      options: d.menu?.options ?? [] };
    return (
      <AgentSide>
        <div id={`decision-${d.id}`} data-decision-state={d.state}>
          {open ? (
            <DecisionQuestion row={row} onAnswered={onAnswered} onDismiss={async () => {
              const err = await dismissMenuCard(d.id, d.from_agent, d.menu?.question || d.question);
              if (!err) onAnswered();
              return err;
            }} />
          ) : (
            <AnsweredDecision row={row} optionN={d.option_n} answerText={d.answer_text} note={answeredByLine(d)} state={refState(d.state)} />
          )}
        </div>
      </AgentSide>
    );
  }

  const send = async (fn: () => Promise<unknown>) => {
    setSending(true); setError(undefined);
    try { await fn(); onAnswered(); }
    catch (e) { setError(e instanceof Error ? e.message.replace(/^API [^:]+: /, 'Could not send: ') : 'Could not send'); }
    finally { setSending(false); }
  };
  const path = (verb: string) => `/approvals/unified/${encodeURIComponent(d.id)}/${verb}`;

  return (
    <AgentSide>
      <DecisionCard id={`decision-${d.id}`} dataState={d.state} tone={decisionTone(d)}
        kind={isMenu ? 'decision' : 'approval'} question={d.question} agent={d.from_agent} risk={d.risk_level}
        body={d.summary} open={open && !isMenu} outcome={outcomeLine(d)}
        report={{ id: d.id, from: d.from_agent, state: refState(d.state), answer: d.state === 'answered' ? chosenText(d) : null }}>
        <ApprovalAnswer sending={sending} error={error}
          onApprove={() => void send(() => post(path('approve'), {}))}
          onDeny={(reason) => void send(() => post(path('deny'), { reason }))} />
      </DecisionCard>
    </AgentSide>
  );
}
