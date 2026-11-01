import { test } from 'node:test';
import assert from 'node:assert/strict';
import { liveCallUrl } from './voiceSession.ts';

test('an agent\'s call names the agent; Arturo\'s own call names none', () => {
  assert.equal(liveCallUrl('wss', 'example.test', 'marin', 'dev-demo'), 'wss://example.test/api/voice/live?voice=marin&agent=dev-demo');
  assert.equal(liveCallUrl('wss', 'example.test', 'Fenrir'), 'wss://example.test/api/voice/live?voice=Fenrir');
  assert.equal(liveCallUrl('ws', 'example.test', 'a b', 'qa demo'), 'ws://example.test/api/voice/live?voice=a+b&agent=qa+demo');
});
