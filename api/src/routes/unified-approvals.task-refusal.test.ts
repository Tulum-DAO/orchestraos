/**
 * Approve / Deny on a task for you (kind 'human_task') is refused with a readable reason that names the verbs
 * and the dismiss, never a bare 400; and the ✕ (dismiss) is one endpoint for every card kind, carrying where,
 * who and why (operator order 2026-11-25, gm msg_b083c465: Deny on a commitment card showed "400" and nothing else).
 *
 * Run: npx tsx --test src/routes/unified-approvals.task-refusal.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { refusalBody, dismissFieldsOf } from './unified-approvals.js';

const SRC = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'unified-approvals.ts'), 'utf-8');
const TASK_VERDICT = { ok: false, status: 400, error: 'human_task verbs are done|cant|snooze', id: 'apr_task1' };

test('the gateway\'s human_task verdict becomes a readable refusal naming the verbs and the dismiss', () => {
  const b = refusalBody(TASK_VERDICT as any, 'apr_task1', 'deny');
  assert.equal(b.kind, 'human_task');
  assert.deepEqual(b.verbs, ['done', 'cant', 'snooze']);
  assert.equal(b.dismiss, '/api/approvals/unified/apr_task1/dismiss');
  assert.match(String(b.error), /task for you/i);
  assert.match(String(b.error), /done, cant or snooze/);
  assert.match(String(b.error), /dismiss/i);
  assert.equal(b.id, 'apr_task1');
});

test('any other refusal keeps its own words (and its why)', () => {
  const b = refusalBody({ ok: false, status: 400, error: 'not an option', why: 'option 9 of 3' } as any, 'apr_x', 'approve');
  assert.equal(b.error, 'not an option');
  assert.equal(b.why, 'option 9 of 3');
  assert.ok(!('kind' in b) && !('verbs' in b));
});

test('approve and deny both answer with refusalBody, not the raw error', () => {
  for (const verb of ['approve', 'deny']) {
    const start = SRC.indexOf(`router.post('/:id/${verb}'`);
    const route = SRC.slice(start, SRC.indexOf('\n});\n', start));
    assert.match(route, /res\.status\(answerRefusalStatus\(result\)\)\.json\(refusalBody\(result, id, '(approve|deny)'\)\)/, verb);
    assert.ok(!/json\(\{ error: result\.error \|\| 'answer refused'/.test(route), `${verb} no longer sends the bare error`);
  }
});

test('the dismiss route forwards where, who and why from the body, for every kind', () => {
  assert.deepEqual(dismissFieldsOf({ surface: ' web ', reason: ' old, remove ', by: 'operator' }),
    { surface: 'web', by: 'operator', reason: 'old, remove' });
  assert.deepEqual(dismissFieldsOf({ surface: 7, reason: ['x'] }), {});
  assert.deepEqual(dismissFieldsOf(undefined), {});
  assert.equal(dismissFieldsOf({ reason: 'r'.repeat(900) }).reason?.length, 500);
  const start = SRC.indexOf("router.post('/:id/dismiss'");
  const route = SRC.slice(start, SRC.indexOf('\n});\n', start));
  assert.match(route, /dismissCard\(String\(req\.params\.id\), defaultDismissDeps\(\), dismissFieldsOf\(req\.body\)\)/);
});
