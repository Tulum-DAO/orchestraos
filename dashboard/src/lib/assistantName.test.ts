import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_ASSISTANT_NAME, applyAssistantName, assistantName, subscribeAssistantName, readCached, named, canRename } from './assistantName.ts';
import { panelStatus } from './arturoPanel.ts';
import { handsFreeTitle } from './arturo.ts';
import { transcriptText } from './arturoCallGroups.ts';

function mem() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); }, removeItem: (k: string) => { m.delete(k); }, m };
}

test('the default is Arturo; the server\'s name replaces it, is remembered for a reload, and resets', () => {
  const s = mem();
  assert.equal(readCached(s), DEFAULT_ASSISTANT_NAME);
  let calls = 0; const off = subscribeAssistantName(() => calls++);
  applyAssistantName('Example', s);
  assert.equal(assistantName(), 'Example');
  assert.equal(readCached(s), 'Example');
  applyAssistantName('Example', s);                       // no change, no notification
  assert.equal(calls, 1);
  applyAssistantName(undefined, s);                    // an older server with no field: the default
  assert.equal(assistantName(), 'Arturo');
  assert.equal(s.m.size, 0);
  off();
});

test('constant lines take the name; only whole words, and the default leaves them as written', () => {
  assert.equal(named('Arturo is turned off on this server', 'Example'), 'Example is turned off on this server');
  assert.equal(named("Only an admin can change Arturo's voice", 'Example'), "Only an admin can change Example's voice");
  assert.equal(named('arturo.toml and Arturos', 'Example'), 'arturo.toml and Arturos');
  assert.equal(named('Arturo', 'Arturo'), 'Arturo');
});

test('status, titles and the copied transcript use the name given', () => {
  assert.deepEqual(panelStatus({ ok: false }, 'x', 'Example'), { text: "Example's brain is offline", fix: 'brain' });
  assert.equal(handsFreeTitle(true, 'Example'), 'Live voice mode: talk, and Example talks back');
  assert.equal(transcriptText([{ role: 'user', text: 'hi' }, { role: 'arturo', text: 'hello' }], 'Example'), 'You: hi\nExample: hello');
});

test('anyone may rename the assistant for now, whatever their role', () => {
  assert.equal(canRename(), true);
});
