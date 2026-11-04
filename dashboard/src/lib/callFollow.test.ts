import { test } from 'node:test';
import assert from 'node:assert/strict';
import { RevealQueue, splitWords, staggerFor, STAGGER_MS, MIN_STAGGER_MS, CATCH_UP_MS } from './reveal.ts';
import { addCaption } from './callTranscript.ts';
import { callLength, fetchAgentCall, saveCallRating, withWork } from './agentCalls.ts';
import { askSwitchCall, clearForAgentCall, useArturoCall, useSwitchAsk } from './switchCall.ts';

test('the reveal splits words with their trailing space, and releases them in quick succession', () => {
  assert.deepEqual(splitWords('Hello, world. Next'), ['Hello, ', 'world. ', 'Next']);
  const q = new RevealQueue();
  q.set('one two three');
  assert.equal(q.tick(0), 1);
  assert.equal(q.tick(10), 0, 'not before the stagger');
  assert.equal(q.tick(STAGGER_MS), 1);
  assert.ok(STAGGER_MS >= 30 && STAGGER_MS <= 40);
});

test('a burst catches up within ~300 ms at a shorter stagger; the end drains fast; nothing shown ever changes', () => {
  assert.equal(staggerFor(1, false), STAGGER_MS);
  assert.ok(staggerFor(29, false) >= MIN_STAGGER_MS, 'a burst of a line or two: about 9 ms apart or slower');
  assert.ok(staggerFor(100, false) * 100 <= CATCH_UP_MS + 1, 'a bigger burst still catches up within ~300 ms');
  assert.ok(staggerFor(20, false) * 20 <= CATCH_UP_MS + 1);
  assert.equal(staggerFor(5, true), MIN_STAGGER_MS, 'the end drains at the fast pace');
  const q = new RevealQueue();
  q.set(Array.from({ length: 30 }, (_, i) => `w${i}`).join(' '));
  let t = 0;
  while (q.released < q.words.length && t < 2000) { q.tick(t); t += 16; }
  assert.ok(t <= CATCH_UP_MS + 120, `30 words shown by ${t} ms`);
  // append only: a grown text adds words; the shown ones stay as they are
  const shown = q.words.slice();
  q.set(`${shown.join('')} more words`);
  assert.deepEqual(q.words.slice(0, shown.length), shown);
  // a word cut mid-chunk grows in place while still unshown
  const r = new RevealQueue();
  r.set('Hel'); r.set('Hello there');
  assert.deepEqual(r.words, ['Hello ', 'there']);
  // a text that does not extend the old one starts over
  const gen = r.gen; r.set('Different'); assert.equal(r.gen, gen + 1); assert.equal(r.released, 0);
});

test('the call\'s transcript: turns by speaker, partial words replaced until the turn is final', () => {
  let t = addCaption([], 'user', 'Sample', false);
  t = addCaption(t, 'user', 'Sample question', false);
  t = addCaption(t, 'user', 'Sample question?', true);
  t = addCaption(t, 'agent', 'Example', false);
  t = addCaption(t, 'agent', 'Example answer.', true);
  t = addCaption(t, 'user', 'Thanks', true);
  assert.deepEqual(t.map((x) => [x.role, x.text, x.final]), [['user', 'Sample question?', true], ['agent', 'Example answer.', true], ['user', 'Thanks', true]]);
});

test('an ended call: its length, work chips placed where the work started, the thumbs saved', async () => {
  assert.equal(callLength(13), '0:13');
  assert.equal(callLength(75.4), '1:15');
  assert.equal(callLength(undefined), null);
  const placed = withWork(
    [{ role: 'user', text: 'Sample question', ts: '2026-11-01T01:00:00Z' }, { role: 'agent', text: 'On it.', ts: '2026-11-01T01:00:03Z' },
     { role: 'agent', text: 'Done.', ts: '2026-11-01T01:00:40Z' }],
    [{ uuid: 'u-1', ts: '2026-11-01T01:00:05Z', steps: 3 }]);
  assert.deepEqual(placed.map((e) => (e.kind === 'turn' ? e.turn.text : `work ${e.work.steps}`)), ['Sample question', 'On it.', 'work 3', 'Done.']);
  // routes not there yet (404): not available, the card falls back
  const answer = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
  assert.equal(await fetchAgentCall('dev-demo', 'vc_1', answer(404, {})), null);
  assert.equal((await fetchAgentCall('dev-demo', 'vc_1', answer(200, { ok: true, call: { call_id: 'vc_1', turns: [] } })))?.call_id, 'vc_1');
  let sent: { url: string; body: string } | null = null;
  assert.equal(await saveCallRating('dev-demo', 'vc_1', 'up', (async (url: string, init: RequestInit) => { sent = { url, body: String(init.body) }; return new Response('{}'); }) as unknown as typeof fetch), true);
  assert.deepEqual(sent, { url: '/api/agents/dev-demo/calls/vc_1/rating', body: '{"rating":"up"}' });
  assert.equal(await saveCallRating('dev-demo', 'vc_1', null, answer(404, {})), false);
});

test('one call at a time: switching asks first, and only a yes ends the live call', async () => {
  const p = askSwitchCall('Sample', 'Example');
  assert.equal(useSwitchAsk.getState().ask?.text, 'End the call with Sample and call Example?');
  useSwitchAsk.getState().ask!.resolve(false); useSwitchAsk.setState({ ask: null });
  assert.equal(await p, false);
});

test("the reverse: an agent's call while Arturo's call is up asks first; no means his call stays, yes ends it", async () => {
  assert.equal(await clearForAgentCall('dev-demo'), true, 'no call up: the way is clear, nothing asked');
  assert.equal(useSwitchAsk.getState().ask, null);
  let ended = 0;
  useArturoCall.setState({ live: { name: 'Sample', end: () => { ended += 1; } } });
  const no = clearForAgentCall('dev-demo');
  assert.equal(useSwitchAsk.getState().ask?.text, 'End the call with Sample and call dev-demo?');
  useSwitchAsk.getState().ask!.resolve(false); useSwitchAsk.setState({ ask: null });
  assert.equal(await no, false);
  assert.equal(ended, 0);
  assert.ok(useArturoCall.getState().live);
  const yes = clearForAgentCall('dev-demo');
  useSwitchAsk.getState().ask!.resolve(true); useSwitchAsk.setState({ ask: null });
  assert.equal(await yes, true);
  assert.equal(ended, 1);
  assert.equal(useArturoCall.getState().live, null);
});
