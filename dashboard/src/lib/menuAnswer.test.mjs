/**
 * Pure-logic tests for the kind='menu' answer contracts (gm-mine-menu-card
 * seam 2, A1/A2/A3 per DOCS/SURFACE_CONTRACTS.md).
 *   node --experimental-strip-types dashboard/src/lib/menuAnswer.test.mjs
 */
import assert from 'node:assert';
import {
  initialMenuAnswerState,
  selectOption,
  setText,
  isFreeText,
  buildMenuAnswerPayload,
  canSubmit,
} from './menuAnswer.ts';

// apr_993fc651-shaped options: 2 direct + 1 free_text write-in.
const OPTIONS = [
  { n: '1', label: 'Oil change (OKC)', input_kind: 'direct' },
  { n: '2', label: "Alzheimer's topic", input_kind: 'direct' },
  { n: '3', label: 'Other / Write-in...', input_kind: 'free_text' },
];

// --- A1: text-alone is valid ------------------------------------------------
{
  let s = initialMenuAnswerState;
  s = setText(s, 'Actually, do the OKC one but push it a day');
  assert.deepStrictEqual(buildMenuAnswerPayload(s), { answer_text: 'Actually, do the OKC one but push it a day' });
  assert.strictEqual(canSubmit(s), true);
  console.log('PASS: A1 text-alone is a valid answer');
}

// --- A1: option-alone is valid ----------------------------------------------
{
  let s = initialMenuAnswerState;
  s = selectOption(s, '1');
  assert.deepStrictEqual(buildMenuAnswerPayload(s), { option_n: '1' });
  assert.strictEqual(canSubmit(s), true);
  console.log('PASS: A1 option-alone is a valid answer');
}

// --- A1: neither present -> invalid (Respond disabled) ----------------------
{
  const s = initialMenuAnswerState;
  assert.strictEqual(buildMenuAnswerPayload(s), null);
  assert.strictEqual(canSubmit(s), false);
  console.log('PASS: A1 neither option nor text -> not submittable');
}

// --- A2: option + free text both submitted together -------------------------
{
  let s = initialMenuAnswerState;
  s = selectOption(s, '3'); // the free_text write-in option
  s = setText(s, 'A third audience: retirees');
  assert.deepStrictEqual(buildMenuAnswerPayload(s), { option_n: '3', answer_text: 'A third audience: retirees' });
  console.log('PASS: A2 option + free text both ride in the same submit');
}

// --- A3: selecting an option never wipes previously-typed answer_text -------
{
  let s = initialMenuAnswerState;
  s = setText(s, 'a note I typed first');
  s = selectOption(s, '2');
  assert.strictEqual(s.text, 'a note I typed first', 'text must survive an option select');
  s = selectOption(s, '1'); // swapping the selection again
  assert.strictEqual(s.text, 'a note I typed first', 'text must survive re-selecting a different option');
  console.log('PASS: A3 selecting an option never wipes typed text');
}

// --- V3 input_kind classification --------------------------------------------
{
  assert.strictEqual(isFreeText(OPTIONS[0]), false);
  assert.strictEqual(isFreeText(OPTIONS[1]), false);
  assert.strictEqual(isFreeText(OPTIONS[2]), true);
  assert.strictEqual(isFreeText({}), false, 'absent input_kind defaults to direct');
  console.log('PASS: V3 input_kind free_text classification');
}

console.log('ALL PASS: menuAnswer.ts (A1, A2, A3, V3 classification)');
