import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fetchVoiceReady } from './voiceReady.ts';

const answer = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

test('ready, or not ready with the server\'s reason; never the key', async () => {
  assert.deepEqual(await fetchVoiceReady(answer(200, { ok: true })), { ok: true });
  assert.deepEqual(await fetchVoiceReady(answer(200, { ok: false, reason: 'No voice key is set up' })), { ok: false, reason: 'No voice key is set up' });
});

test('no answer (a server without the route, a dropped request) is unknown, not "not ready"', async () => {
  assert.equal(await fetchVoiceReady(answer(404, { error: 'not found' })), null);
  assert.equal(await fetchVoiceReady((async () => { throw new TypeError('Failed to fetch'); }) as unknown as typeof fetch), null);
  assert.equal(await fetchVoiceReady(answer(200, { something: 'else' })), null);
});
