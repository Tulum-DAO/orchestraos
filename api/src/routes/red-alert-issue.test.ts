/**
 * Create-issue endpoints — fake runner, never spawns python.
 * Run: npx tsx --test src/routes/red-alert-issue.test.ts (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { handleIssuePreview, handleIssueCreate } from './red-alert.js';

function mockRes() {
  const res: any = { statusCode: 200, body: null as any };
  res.status = (c: number) => { res.statusCode = c; return res; };
  res.json = (b: any) => { res.body = b; return res; };
  return res;
}

test('preview returns parsed JSON from runner', async () => {
  const run = async () => ({
    code: 0,
    stdout: JSON.stringify({ ok: true, can_file: false, repo_configured: false, title: 't', body: 'b' }),
    stderr: '',
  });
  const res = mockRes();
  await handleIssuePreview({ params: { id: 'ra_1' } } as any, res, run);
  assert.equal(res.statusCode, 200);
  assert.equal(res.body.ok, true);
});

test('create refuses without confirm:true', async () => {
  let called = false;
  const run = async () => { called = true; return { code: 0, stdout: '{}', stderr: '' }; };
  const res = mockRes();
  await handleIssueCreate({ params: { id: 'ra_1' }, body: {} } as any, res, run);
  assert.equal(res.statusCode, 400);
  assert.match(String(res.body.error), /confirm/);
  assert.equal(called, false);
});

test('create with confirm passes --yes', async () => {
  let args: string[] = [];
  const run = async (a: string[]) => {
    args = a;
    return { code: 0, stdout: JSON.stringify({ ok: true, action: 'create', dry_run: true }), stderr: '' };
  };
  const res = mockRes();
  await handleIssueCreate({ params: { id: 'ra_1' }, body: { confirm: true, dry_run: true } } as any, res, run);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(args, ['create', 'ra_1', '--yes', '--dry-run']);
});
