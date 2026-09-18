/**
 * Deliverable 6 (report button = ticket gateway). Drives handleReport with a fake runner —
 * never spawns python. Run: npx tsx --test src/routes/red-alert.test.ts (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Request, Response } from 'express';
import { handleReport, validate, type Runner } from './red-alert.js';

function req(body: any): Request { return { body, query: {} } as unknown as Request; }
function fakeRes() {
  const calls: { status?: number; json?: any } = {};
  const res = { status(c: number) { calls.status = c; return res; }, json(b: any) { calls.json = b; return res; } } as unknown as Response;
  return { res, calls };
}

test('validate: kind + seat + words', () => {
  assert.equal(validate({ seat: 'gm', kind: 'crash', words: 'it froze' }).ok, true);
  assert.equal(validate({ seat: 'gm; rm', kind: 'crash', words: 'x y z' }).ok, false);
  assert.equal(validate({ seat: 'gm', kind: 'rant', words: 'x y z' }).ok, false);
  assert.equal(validate({ seat: 'gm', kind: 'bug', words: 'x' }).ok, false);
});

test('report runs red_alert.py report --surface with the user words verbatim', async () => {
  const seen: string[][] = [];
  const run: Runner = async (args) => { seen.push(args); return { code: 0, stdout: '{"id":"ra_1","path":"p","class":null,"severity":"bug","card_id":"apr_1","surfaced":true}\n', stderr: '' }; };
  const { res, calls } = fakeRes();
  await handleReport(req({ seat: 'gm', kind: 'bug', words: 'the card never showed up' }), res, run);
  assert.equal(calls.json.ok, true);
  assert.equal(calls.json.card_id, 'apr_1');
  const a = seen[0];
  assert.deepEqual(a.slice(0, 5), ['report', '--reported-by', 'user', '--channel', 'dashboard']);
  assert.ok(a.includes('--surface') && a.includes('--kind') && a[a.indexOf('--symptom') + 1] === 'the card never showed up');
});

test('a failed script is a 500 with detail, never a fake ok', async () => {
  const run: Runner = async () => ({ code: 1, stdout: '', stderr: 'boom' });
  const { res, calls } = fakeRes();
  await handleReport(req({ seat: 'gm', kind: 'crash', words: 'gm is dead' }), res, run);
  assert.equal(calls.status, 500);
  assert.match(calls.json.detail, /boom/);
});
