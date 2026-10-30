/**
 * The status stream leaves headless seats out: telemetryd reads them from a pane that holds their
 * supervisor, not the agent. Temp files only (nothing watched, nothing written outside the temp dir).
 *
 * Run: npx tsx --test src/services/status-stream.headless.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { headlessSeats, withoutHeadless, readSeats } from './status-stream.js';

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'headless-'));

test('no switch file, or a broken one, switches nothing', () => {
  const dir = tmp();
  assert.equal(headlessSeats(path.join(dir, 'none.json')).size, 0);
  fs.writeFileSync(path.join(dir, 'bad.json'), '{"seats": ');
  assert.equal(headlessSeats(path.join(dir, 'bad.json')).size, 0);
  assert.equal(headlessSeats(null).size, 0);
});

test('only switched-on Claude seats count', () => {
  const dir = tmp();
  const f = path.join(dir, 'headless-seats.json');
  fs.writeFileSync(f, JSON.stringify({ seats: {
    'dev-demo': { headless: true, runtime: 'claude' },
    'qa-demo': { headless: true, runtime: 'codex' },
    'pm-demo': { headless: false, runtime: 'claude' },
  } }));
  assert.deepEqual([...headlessSeats(f)], ['dev-demo']);
});

test('a headless seat is dropped by key or by session; the rest pass unchanged', () => {
  const seats = { 'dev-demo': { status: 'idle' }, x: { status: 'streaming', session: 'pm-demo' }, 'qa-demo': { status: 'idle' } };
  assert.deepEqual(withoutHeadless(seats, new Set()), seats);
  assert.deepEqual(Object.keys(withoutHeadless(seats, new Set(['dev-demo', 'pm-demo']))), ['qa-demo']);
});

test('readSeats applies the filter', () => {
  const dir = tmp();
  const f = path.join(dir, 'status.json');
  fs.writeFileSync(f, JSON.stringify({ seats: { 'dev-demo': { status: 'idle' }, 'qa-demo': { status: 'streaming' } } }));
  assert.deepEqual(Object.keys(readSeats(f, new Set(['dev-demo']))), ['qa-demo']);
  assert.deepEqual(Object.keys(readSeats(f, new Set())), ['dev-demo', 'qa-demo']);
});
