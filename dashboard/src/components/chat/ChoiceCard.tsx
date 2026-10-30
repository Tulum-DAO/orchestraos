/**
 * ChoiceCard — a decision whose answer goes back to the agent as words in the chat: Arturo's ask_choices (one or
 * several picks) and his onboarding steps. Drawn as the one question card (MultiPartCard's QuestionCard) with a single
 * part: a pick sends at once; several picks take ticks and Send ("None of these" stands alone); the operator's own
 * words can be sent instead (on a pick-any card, with the ticks). One question, so there is no Skip: the ✕ closes it.
 * A pick is shown once, on the answered card: the page does not echo it as a chat bubble (lib/choiceCard isCardEcho).
 */
import { AnsweredQuestionCard, QuestionCard } from './MultiPartCard';
import { AgentSide } from './DecisionCard';
import { choiceAnswerText, choiceParts, isCardEcho } from '../../lib/choiceCard';
import { choiceAnswerFromText, type PartAnswer } from '../../lib/multiPart';

export default function ChoiceCard({ question, note, options, multi = false, exclusive, onAnswer, answered, onDismiss, dismissed, asker = 'Arturo', cardId }: {
  question?: string;
  /** The agent's own line about the choice, shown under the question. */
  note?: string;
  options: string[];
  multi?: boolean;
  /** Multi-select: an option that stands alone ("None of these"). */
  exclusive?: string;
  /** The words to send back, and what was picked and typed (so the server can keep the answer on the card). */
  onAnswer: (text: string, answer: { picked: string[]; words: string }) => void;
  /** The card's id on the server: its element is `choice-<id>`, which the drawer's History scrolls to. */
  cardId?: string;
  /** Once answered: what was sent, shown as the card's outcome. */
  answered?: string | null;
  /** Close it unanswered (the ✕, two clicks): returns an error to show, or null. Absent: no ✕. */
  onDismiss?: () => Promise<string | null>;
  dismissed?: boolean;
  asker?: string;
}) {
  const q = question || note || (multi ? 'Pick all that apply' : 'Pick one');
  const context = question && note ? <div className="text-[13px] leading-relaxed text-card-foreground/85">{note}</div> : null;
  const parts = choiceParts(options, multi, exclusive, q);

  const submit = async (answers: PartAnswer[]): Promise<string | null> => {
    const a = answers[0];
    const picked = a.ns.map((n) => options[Number(n) - 1]);
    const text = choiceAnswerText(options, picked, a.text);
    if (!text) return 'Pick an option, or type your answer.';
    onAnswer(text, { picked, words: a.text });
    return null;
  };

  return (
    <AgentSide>
      {/* A steady width: inside a reply that shrinks to its text, the card would otherwise squeeze its own field. */}
      <div className="w-[min(30rem,calc(100vw-2.5rem))] max-w-full" id={cardId ? `choice-${cardId}` : undefined}
           data-choice-card={cardId} data-choice-state={answered ? 'answered' : dismissed ? 'dismissed' : 'waiting'}>
        {answered ? (
          // A pick shows here and is not echoed in the chat; words alone are the operator's own message, so the
          // card points to it instead of repeating them.
          <AnsweredQuestionCard title={asker} context={context} parts={parts} answers={[choiceAnswerFromText(parts[0], answered)]}
            elsewhere={isCardEcho({ options, multi, exclusive }, answered) ? null : 'Answered in the chat'} />
        ) : (
          <QuestionCard title={asker} context={context} parts={parts} onSubmit={submit} onDismiss={onDismiss} dismissed={dismissed} />
        )}
      </div>
    </AgentSide>
  );
}
