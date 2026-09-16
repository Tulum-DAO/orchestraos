/**
 * menuAnswer.ts — pure logic for rendering + answering a kind='menu' approval
 * row on the web dashboard (gm-mine-menu-card seam 2). Extracted out of the
 * page components so the answer contracts (DOCS/SURFACE_CONTRACTS.md) are
 * unit-testable without a DOM/React-render harness, which this package does
 * not have (see src/lib/assistant/*.test.mjs for the established pure-logic
 * test pattern this file follows).
 *
 * Contracts enforced HERE (client-side shape only — the server, via
 * watch_gateway.apply_answer, is the actual gate; see api/src/routes/
 * unified-approvals.ts POST /:id/answer):
 *   A1. Text-alone is a valid answer: option_n OR answer_text.
 *   A2. option_n AND answer_text may both be submitted.
 *   A3. Selecting an option never wipes previously-typed answer_text — this
 *       module never couples the two: option selection and free-text are
 *       independent fields on MenuAnswerState, merged only at submit time.
 */

export interface MenuOption {
  n: string;
  label: string;
  input_kind?: 'direct' | 'free_text' | 'chat' | string;
  detail?: string;
}

export interface MenuAnswerState {
  selectedN: string | null;
  text: string;
}

export const initialMenuAnswerState: MenuAnswerState = { selectedN: null, text: '' };

/** A3: selecting (or re-selecting/clearing) an option never touches `text`. */
export function selectOption(state: MenuAnswerState, n: string | null): MenuAnswerState {
  return { ...state, selectedN: n };
}

/** Typing never touches `selectedN`. */
export function setText(state: MenuAnswerState, text: string): MenuAnswerState {
  return { ...state, text };
}

/** V3: does this option's kind require (or accept) a free-text field alongside it? */
export function isFreeText(opt: Pick<MenuOption, 'input_kind'>): boolean {
  return (opt.input_kind || 'direct') === 'free_text';
}

export interface MenuAnswerPayload {
  option_n?: string;
  answer_text?: string;
}

/**
 * A1/A2: build the wire payload from current state. Returns null when NEITHER
 * an option nor typed text is present (nothing valid to submit — Respond
 * stays disabled).
 */
export function buildMenuAnswerPayload(state: MenuAnswerState): MenuAnswerPayload | null {
  const optionN = state.selectedN || undefined;
  const answerText = state.text.trim() ? state.text.trim() : undefined;
  if (!optionN && !answerText) return null; // A1: at least one required
  const payload: MenuAnswerPayload = {};
  if (optionN) payload.option_n = optionN;
  if (answerText) payload.answer_text = answerText; // A2: both may ride together
  return payload;
}

/** Is there anything valid to submit right now? Drives the Respond button's disabled state. */
export function canSubmit(state: MenuAnswerState): boolean {
  return buildMenuAnswerPayload(state) !== null;
}
