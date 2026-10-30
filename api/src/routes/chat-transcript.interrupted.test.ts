/**
 * A headless seat's cut turn shows in its chat: the supervisor's interrupted.json becomes one machine line after
 * the turn its message opened, once, only for a message in the window. Temp files only.
 *
 * Run: npx tsx --test src/routes/chat-transcript.interrupted.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { mergeInterruptedTurns, readInterruptedTurns, interruptedLine, INTERRUPTED_PREFIX } from './chat-transcript.js';

const user = (text: string, uuid: string, extra: Record<string, unknown> = {}) => ({ kind: 'text', role: 'user', text, uuid, ...extra });
const said = (text: string, uuid: string) => ({ kind: 'text', role: 'assistant', text, uuid });

test('the line goes after the turn its message opened, before the next turn', () => {
  const items = [user('Sample one', 'u1'), said('Working on it', 'a1'),
    { kind: 'tool_use', role: 'assistant', tool: 'Bash', input: {}, id: 't1', uuid: 'a2' },
    user('Sample two', 'u2'), said('Done', 'a3')];
  const out = mergeInterruptedTurns(items, [{ id: 'm1', text: 'Sample one', at: '2026-01-01T00:00:00Z' }]);
  assert.equal(out.length, 6);
  const line = out[3];
  assert.equal(line.uuid, 'interrupted:m1');
  assert.equal(line.machine, 'system');
  assert.equal(line.mid_turn, true);                 // inside the cut turn: it opens no turn of its own
  assert.equal(line.audience, 'user');
  assert.ok(line.text.startsWith(INTERRUPTED_PREFIX) && line.text.includes('"Sample one"'));
  assert.equal(out[4].uuid, 'u2');
});

test('once per message, nothing for a message outside the window, the last matching turn wins', () => {
  const items = [user('Example', 'u1'), said('a', 'a1'), user('Example', 'u2')];
  const once = mergeInterruptedTurns(items, [{ id: 'm1', text: 'Example' }]);
  assert.equal(once[3].uuid, 'interrupted:m1');      // after the LAST "Example", at the end
  assert.equal(mergeInterruptedTurns(once, [{ id: 'm1', text: 'Example' }]).length, once.length);
  assert.equal(mergeInterruptedTurns(items, [{ id: 'm2', text: 'Not here' }]).length, items.length);
  assert.equal(mergeInterruptedTurns(items, []), items);
});

test('a session prompt is never taken for the message', () => {
  const items = [user('Example', 'u0', { is_system: true }), said('a', 'a1')];
  assert.equal(mergeInterruptedTurns(items, [{ id: 'm1', text: 'Example' }]).length, 2);
});

test('the supervisor file: read when present, nothing when missing, broken or the id is unsafe', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'interrupted-'));
  assert.deepEqual(readInterruptedTurns('dev-demo', root), []);
  const dir = path.join(root, 'state', 'headless', 'dev-demo');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'interrupted.json'), '{"items": [');
  assert.deepEqual(readInterruptedTurns('dev-demo', root), []);
  fs.writeFileSync(path.join(dir, 'interrupted.json'), JSON.stringify({ items: [
    { id: 'm1', text: 'Sample', at: 'x' }, { id: 'm2', text: '' }, { text: 'no id' }] }));
  assert.deepEqual(readInterruptedTurns('dev-demo', root).map((t) => t.id), ['m1']);
  assert.deepEqual(readInterruptedTurns('../etc', root), []);
});

test('a long message is cut in the line', () => {
  const line = interruptedLine({ id: 'm', text: 'x'.repeat(500) });
  assert.ok(line.length < 400 && line.includes('…'));
});
