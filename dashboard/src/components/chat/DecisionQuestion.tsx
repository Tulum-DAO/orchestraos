/**
 * DecisionQuestion — a decision card an agent posted (an approval_requests row of kind 'menu'), drawn as the one
 * question card (MultiPartCard's QuestionCard) with a single part: on the Approvals and Inbox pages and as a bubble
 * in the agent's chat. Picking an option sends it at once; words go alone where the menu has a write-in. Answers go
 * through POST /api/approvals/unified/:id/answer ({option_n} or {answer_text}); one question, so there is no Skip
 * (the ✕ closes it unanswered).
 *
 * An answered card stays as the dimmed answered card, with who answered, where and when under it.
 */
import { useState, type ReactNode } from 'react';
import Markdown from './Markdown';
import { AnsweredQuestionCard, QuestionCard } from './MultiPartCard';
import { post } from '../../lib/api';
import { previewText } from '../../lib/decisions';
import { riskText } from '../../lib/decisionCard';
import { answerFromStored, decisionParts, singleBody, type PlainOption } from '../../lib/multiPart';

const SUMMARY_PREVIEW = 280;

export interface DecisionRow {
  id: string;
  question: string;
  agent?: string | null;
  risk?: string | null;
  body?: string | null;
  options: PlainOption[];
  /** The option the agent leans to. */
  lean?: string | null;
  /** Bridged from an agent's terminal menu (lib/decisionCard isTerminalMenu): a pick or words, not both. */
  terminal?: boolean;
}

/** The card's text, cut short until opened, and anything the page adds under it. */
function Context({ body, extra }: { body?: string | null; extra?: ReactNode }) {
  const [more, setMore] = useState(false);
  const text = (body || '').trim();
  if (!text && !extra) return null;
  const long = text.length > SUMMARY_PREVIEW;
  return (
    <div className="space-y-2">
      {text && (
        <div className="text-[13px] leading-relaxed text-card-foreground/85">
          <Markdown text={more || !long ? text : previewText(text, SUMMARY_PREVIEW)} />
          {long && (
            <button type="button" onClick={() => setMore((v) => !v)} className="text-xs text-info hover:underline mt-0.5">
              {more ? 'less' : 'more'}
            </button>
          )}
        </div>
      )}
      {extra}
    </div>
  );
}

function meta(risk?: string | null) {
  const r = riskText(risk);
  return <>· Decision{r ? ` · ${r}` : ''}</>;
}

export default function DecisionQuestion({ row, extra, onAnswered, onDismiss, highlighted, id }: {
  row: DecisionRow;
  extra?: ReactNode;
  /** After a send: refresh the list the card came from. */
  onAnswered?: () => void;
  onDismiss?: () => Promise<string | null>;
  highlighted?: boolean;
  id?: string;
}) {
  const parts = decisionParts(row);
  const submit = async (answers: Parameters<typeof singleBody>[0][]): Promise<string | null> => {
    const body = singleBody(answers[0]);
    if (!body) return 'Pick an option, or type your answer.';
    try {
      await post(`/approvals/unified/${encodeURIComponent(row.id)}/answer`, body);
      onAnswered?.();
      return null;
    } catch (e) {
      return e instanceof Error ? e.message.replace(/^API [^:]+: /, 'Could not send: ') : 'Could not send — try again.';
    }
  };
  return (
    <QuestionCard key={row.id} id={id} highlighted={highlighted} title={row.agent || 'An agent'} meta={meta(row.risk)}
      context={<Context body={row.body} extra={extra} />} parts={parts} onSubmit={submit} onDismiss={onDismiss} />
  );
}

/** An answered decision card: what was chosen (or the words), and a line saying who answered, where and when. */
export function AnsweredDecision({ row, optionN, answerText, note, id }: {
  row: DecisionRow; optionN?: string | null; answerText?: string | null; note?: ReactNode; id?: string;
}) {
  const parts = decisionParts(row);
  return (
    <AnsweredQuestionCard id={id} title={row.agent || 'An agent'} meta={meta(row.risk)} context={<Context body={row.body} />}
      parts={parts} answers={[answerFromStored(parts[0], optionN, answerText)]} note={note} />
  );
}
