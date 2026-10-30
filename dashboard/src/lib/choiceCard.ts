/**
 * choiceCard.ts — a card whose answer goes back to the agent as words in the chat (Arturo's ask_choices and his
 * onboarding steps), drawn as the one question card (components/chat/ChoiceCard.tsx). Pure, so it is tested here.
 */
import { choiceAnswerFromText, onePart, type MenuPart } from './multiPart';

/** What is sent: the picked option(s), with the operator's own words after them, or the words alone. */
export function choiceAnswerText(options: string[], picked: string[], words: string): string {
  const chosen = options.filter((o) => picked.includes(o)).join(', ');
  const w = words.trim();
  return chosen && w ? `${chosen} — ${w}` : chosen || w;
}

/** The words a stored answer (the card's own record on the server: picks and words) reads as, the same words a tap
 *  sent, so the answered card draws it the same way live and after a reload. */
export function storedAnswerText(options: string[], answer: { picks?: string[]; words?: string } | null | undefined): string {
  if (!answer) return '';
  return choiceAnswerText(options, answer.picks ?? [], answer.words ?? '');
}

/** What a tap on a card sends with its words: the card it answers, the picked labels in the card's order, and the
 *  operator's own words (services/arturo/card_answers.py keeps it on the card with the turn). */
export function cardAnswer(cardId: string, options: string[], picked: string[], words: string): { card_id: string; picks: string[]; words: string } {
  return { card_id: cardId, picks: options.filter((o) => picked.includes(o)), words: words.trim() };
}

/** The card of one part a choice card is drawn as (components/chat/ChoiceCard.tsx). */
export function choiceParts(options: string[], multi = false, exclusive?: string, question = 'Pick one'): MenuPart[] {
  const ex = exclusive && options.includes(exclusive) ? String(options.indexOf(exclusive) + 1) : undefined;
  return onePart(question, options.map((label, i) => ({ n: String(i + 1), label })), { words: true, multi, exclusive: ex, pickAndWords: true });
}

/** Whether the message that answered a card is the card's own answer, shown on the answered card and so not echoed
 *  as a chat bubble: it names one of the card's options (a tap, with or without words). Words alone stay a message,
 *  since they read the same whether typed into the card or the chat box. Read from the text alone, so a reloaded
 *  thread draws it the same way. */
export function isCardEcho(card: { options: string[]; multi?: boolean; exclusive?: string }, text: string): boolean {
  const part = choiceParts(card.options, !!card.multi, card.exclusive)[0];
  return choiceAnswerFromText(part, text).ns.length > 0;
}
