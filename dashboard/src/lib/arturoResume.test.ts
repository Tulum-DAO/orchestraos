import { test } from 'node:test';
import assert from 'node:assert/strict';
import { stripContextLine, hydrateTurns, onboardingConversation, carriesOnboardingMarker, mergeResumeReply, isBusy }
  from './arturoResume';

// The operator, 2026-10-09: the thread title read "[Context: route=/agent/pm-web entity=agent:pm-web] Wheres the
// general manager", and leaving onboarding partway then coming back showed a new-user greeting with the history
// hidden above it (DEC-1791511578959986).

const CTX = '[Context: route=/agent/pm-web entity=agent:pm-web]';
const OPENER = '(first run: the operator just opened OrchestraOS)';

test('the page-context line is never shown', () => {
  assert.equal(stripContextLine(`${CTX}\nWheres gm`), 'Wheres gm');
  assert.equal(stripContextLine('[Context] is a word'), '[Context] is a word');
  assert.equal(stripContextLine(`hi\n${CTX}`), `hi\n${CTX}`);
});

test("a stored thread hydrates without the page's opener or context lines", () => {
  const turns = hydrateTurns([
    { role: 'user', content: OPENER }, { role: 'assistant', content: 'Hi! What should I call you?' },
    { role: 'user', content: 'Ada' }, { role: 'assistant', content: 'Good to meet you.' },
    { role: 'user', content: `${CTX}\nWheres gm` }, { role: 'assistant', content: 'It runs as gm.' },
  ]);
  assert.deepEqual(turns.map((t) => [t.role, t.text]), [
    ['arturo', 'Hi! What should I call you?'], ['user', 'Ada'], ['arturo', 'Good to meet you.'],
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
    { id: 1, role: 'user', text: 'Ada' }, { id: 2, role: 'arturo', text: 'Which devices do you have?' },
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
  // a returning reply WITH a card replaced "Noted: iPhone." — a card is no reason
  const noted: T[] = [
    { id: 1, role: 'user', text: 'iPhone' }, { id: 2, role: 'arturo', text: 'Noted: iPhone.' },
    { id: 3, role: 'arturo', text: '', pending: true },
  ];
  assert.deepEqual(mergeResumeReply(noted, 3, { text: 'Next: your team?', choices: { options: ['Set it up'] } }).map((t) => t.id), [1, 2, 3]);
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

test('a reply that showed a card brings the card back, answered by the next message or dismissed', () => {
  const card = { options: ['Alpha', 'Beta'], multi: false, purpose: 'other' as const };
  const turns = hydrateTurns([
    { role: 'user', content: 'Start' },
    { role: 'assistant', content: 'Which one?', meta: { kind: 'card', card_id: 'c0ffee01', choices: card } },
    { role: 'user', content: 'Beta' },
    { role: 'assistant', content: 'And this?', meta: { kind: 'card', card_id: 'c0ffee02', choices: card, dismissed: true } },
    { role: 'assistant', content: 'Last one?', meta: { kind: 'card', card_id: 'c0ffee03', choices: card } },
  ]);
  assert.equal(turns[1].choices?.card_id, 'c0ffee01');
  assert.equal(turns[1].answered, 'Beta');
  assert.equal(turns[3].dismissed, true);
  assert.equal(turns[3].answered, undefined);
  assert.equal(turns[4].answered, undefined);         // nothing after it yet: still open
});

test("a card's tapped answer is not echoed as a bubble on reload; words typed instead stay a message", () => {
  const card = { options: ['Alpha', 'Beta'], multi: false, purpose: 'other' as const };
  const turns = hydrateTurns([
    { role: 'assistant', content: 'Which one?', meta: { kind: 'card', card_id: 'c0ffee01', choices: card } },
    { role: 'user', content: 'Beta' },
    { role: 'user', content: 'Beta' },
    { role: 'assistant', content: 'And this?', meta: { kind: 'card', card_id: 'c0ffee02', choices: card } },
    { role: 'user', content: 'neither, thanks' },
  ]);
  assert.equal(turns[1].cardEcho, true);          // the tap: drawn on the answered card only
  assert.equal(turns[2].cardEcho, undefined);     // a later message is a message
  assert.equal(turns[4].cardEcho, undefined);     // words alone stay the operator's message
  assert.equal(turns[3].answered, 'neither, thanks');
});

test('a stored turn read a page at a time keeps its seq (its identity when older pages join); a whole read has none', () => {
  const out = hydrateTurns([{ role: 'user', content: 'q', seq: 7 }, { role: 'assistant', content: 'a', seq: 8 }, { role: 'assistant', content: 'b' }]);
  assert.deepEqual(out.map((t) => t.seq), [7, 8, undefined]);
});

// A tool a voice call ran is a row of its own in the call's thread (services/arturo/call_tool_lines.py, meta kind
// "call_tool"): after a reload it is the kit's tool chip inside the call's card, the way the live call drew it, never
// an Arturo speech bubble (Shaw, 2026-11-26: the tool runs "weren't logged in the chat").
test("a call's tool run comes back as the call's tool chip, not a line Arturo said", () => {
  const turns = hydrateTurns([
    { role: 'user', content: 'there is latency', mode: 'voice', ts: 10 },
    { role: 'assistant', content: 'Asked gm: why the latency? · gm has it', mode: 'voice', ts: 13, seq: 41,
      meta: { kind: 'call_tool', tool: 'gm_command', ok: true, tool_call_id: 'call_0' } },
    { role: 'assistant', content: 'research: GPT-Live turn latency · didn\'t finish', mode: 'voice', ts: 15,
      meta: { kind: 'call_tool', tool: 'research', ok: false, tool_call_id: 'call_1' } },
    { role: 'assistant', content: 'list_agents · done', mode: 'voice', ts: 16,
      meta: { kind: 'call_tool', tool: 'list_agents', ok: true, tool_call_id: '' } },
    { role: 'assistant', content: "I've passed that to gm.", mode: 'voice', ts: 20 },
  ]);
  const [, gm, research, list] = turns;
  assert.equal(gm.text, '');
  assert.equal(gm.callTools, true);
  assert.equal(gm.voice, undefined);
  assert.equal(gm.ts, 13);
  assert.equal(gm.seq, 41);
  assert.deepEqual(gm.parts, [{ kind: 'tool', callId: 'call_0', name: 'gm_command', argsSummary: 'why the latency?',
                                status: 'ok', summary: 'gm has it' }]);
  assert.deepEqual(research.parts, [{ kind: 'tool', callId: 'call_1', name: 'research',
                                      argsSummary: 'GPT-Live turn latency', status: 'failed', summary: "didn't finish" }]);
  assert.deepEqual(list.parts, [{ kind: 'tool', callId: 'tool16', name: 'list_agents', argsSummary: '', status: 'ok',
                                  summary: 'done' }]);
  assert.equal(turns[4].text, "I've passed that to gm.");
});

test('a tool chip stays inside its call card', async () => {
  const { groupCalls } = await import('./threadKit/calls');
  const turns = hydrateTurns([
    { role: 'user', content: 'hi', mode: 'voice', ts: 10 },
    { role: 'assistant', content: 'Asked gm: q · gm has it', mode: 'voice', ts: 11,
      meta: { kind: 'call_tool', tool: 'gm_command', ok: true, tool_call_id: 'c' } },
    { role: 'assistant', content: 'ok', mode: 'voice', ts: 12 },
    { role: 'assistant', content: 'Call ended (vc_client_1). [voice-call: vc_client_1 /x.json]', mode: 'voice', ts: 13,
      meta: { kind: 'call_end', call_id: 'vc_client_1' } },
  ]);
  const items = groupCalls(turns, false);
  assert.equal(items.length, 1);
  const call = items[0] as { kind: string; turns: typeof turns };
  assert.equal(call.kind, 'call');
  assert.equal(call.turns.length, 3);                                    // hi, the tool chip, ok (the end line closes it)
  assert.ok(call.turns.some((t) => t.callTools && t.parts?.length === 1));
});

test("a tool row's ask keeps its own ': ' and ' · ', and a row without ok falls back on its outcome word", async () => {
  const { callToolPart } = await import('./arturoResume');
  const p = callToolPart({ role: 'assistant', content: 'Asked gm: a: b · c · gm has it',
                           meta: { kind: 'call_tool', tool: 'gm_command', ok: true, tool_call_id: 'x' } });
  assert.equal(p.argsSummary, 'a: b · c');
  assert.equal(p.summary, 'gm has it');
  const bare = (content: string) => callToolPart({ role: 'assistant', content, meta: { kind: 'call_tool', tool: 'research' } });
  assert.equal(bare("research: q · didn't finish").status, 'failed');
  assert.equal(bare('research: q · refused').status, 'failed');
  assert.equal(bare('research: q · done').status, 'ok');
  assert.equal(bare('spending_month_summary · done').argsSummary, '');
});
