import { test } from 'node:test';
import assert from 'node:assert/strict';
import { stripContextLine, hydrateTurns, onboardingConversation, carriesOnboardingMarker, mergeResumeReply, isBusy }
  from './arturoResume';

// The operator, 2026-10-09: the thread title read "[Context: route=/agent/pm-ops entity=agent:pm-ops] Wheres the
// general manager", and leaving onboarding partway then coming back showed a new-user greeting with the history
// hidden above it (DEC-1791511578959986).

const CTX = '[Context: route=/agent/pm-ops entity=agent:pm-ops]';
const OPENER = '(first run: the operator just opened OrchestraOS)';

test('the page-context line is never shown', () => {
  assert.equal(stripContextLine(`${CTX}\nWheres gm`), 'Wheres gm');
  assert.equal(stripContextLine('[Context] is a word'), '[Context] is a word');
  assert.equal(stripContextLine(`hi\n${CTX}`), `hi\n${CTX}`);
});

test("a stored thread hydrates without the page's opener or context lines", () => {
  const turns = hydrateTurns([
    { role: 'user', content: OPENER }, { role: 'assistant', content: 'Hi! What should I call you?' },
    { role: 'user', content: 'Shaw' }, { role: 'assistant', content: 'Good to meet you.' },
    { role: 'user', content: `${CTX}\nWheres gm` }, { role: 'assistant', content: 'It runs as gm.' },
  ]);
  assert.deepEqual(turns.map((t) => [t.role, t.text]), [
    ['arturo', 'Hi! What should I call you?'], ['user', 'Shaw'], ['arturo', 'Good to meet you.'],
    ['user', 'Wheres gm'], ['arturo', 'It runs as gm.'],
  ]);
});

test('the server names the onboarding thread; the browser only falls back to its own', () => {
  assert.equal(onboardingConversation('web_server', 'web_local'), 'web_server');
  assert.equal(onboardingConversation(null, 'web_local'), 'web_local');
  assert.equal(onboardingConversation(undefined, 'web_local'), 'web_local');
});

test('only the onboarding thread carries the onboarding marker', () => {
  assert.equal(carriesOnboardingMarker('onboarding', 'web_onb', 'web_onb'), true);
  assert.equal(carriesOnboardingMarker('onboarding', 'web_other', 'web_onb'), false);   // a resumed other thread
  assert.equal(carriesOnboardingMarker('done', 'web_onb', 'web_onb'), false);
  assert.equal(carriesOnboardingMarker('onboarding', 'web_onb', ''), true);            // nothing pinned yet
});

type T = { id: number; role: 'arturo' | 'user'; text: string; pending?: boolean; choices?: object; pairCard?: object };

test('a returning reply refreshes the unanswered question in place: the next step shows once', () => {
  const turns: T[] = [
    { id: 1, role: 'user', text: 'Shaw' }, { id: 2, role: 'arturo', text: 'Which devices do you have?' },
    { id: 3, role: 'arturo', text: '', pending: true },
  ];
  const out = mergeResumeReply(turns, 3, { text: 'Your name is set. Which devices do you have?', choices: { options: ['iPhone'] } });
  assert.deepEqual(out.map((t) => t.id), [1, 3]);
  assert.equal(out[1].pending, false);
  assert.deepEqual(out[1].choices, { options: ['iPhone'] });
});

test('a returning reply never replaces a report or an answered message', () => {
  const report: T[] = [
    { id: 1, role: 'user', text: 'pair my iphone' }, { id: 2, role: 'arturo', text: 'iPhone is paired.' },
    { id: 3, role: 'arturo', text: '', pending: true },
  ];
  assert.deepEqual(mergeResumeReply(report, 3, { text: 'Next: your Mac.' }).map((t) => t.id), [1, 2, 3]);
  const answered: T[] = [
    { id: 2, role: 'arturo', text: 'Which devices?' }, { id: 4, role: 'user', text: 'iPhone' },
    { id: 3, role: 'arturo', text: '', pending: true },
  ];
  assert.deepEqual(mergeResumeReply(answered, 3, { text: 'Which devices?', choices: {} }).map((t) => t.id), [2, 4, 3]);
});

test('a returning reply with nothing to say leaves no empty bubble', () => {
  const turns: T[] = [{ id: 2, role: 'arturo', text: 'Which devices?' }, { id: 3, role: 'arturo', text: '', pending: true }];
  assert.deepEqual(mergeResumeReply(turns, 3, { text: '' }).map((t) => t.id), [2]);
});

test('a busy conversation is retried, not shown as a failure', () => {
  assert.equal(isBusy({ ok: false, status: 409, error: 'busy' }), true);
  assert.equal(isBusy({ ok: false, status: 409, error: 'card_required' }), false);
  assert.equal(isBusy({ ok: true }), false);
});
