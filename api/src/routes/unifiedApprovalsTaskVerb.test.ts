/**
 * POST /api/approvals/unified/:id/answer takes a task for you's own verbs (kind 'human_task': done, cant, snooze).
 *
 * Before, the dashboard drew such a card with Approve / Deny, Approve posted /:id/approve, and the gateway answered
 * 400 "human_task verbs are done|cant|snooze". No web route sent those verbs at all. The answer route now sends
 * { answer: 'done' | 'cant' | 'snooze', answer_text? } through the same canonicalAnswer -> approval.py answer ->
 * watch_gateway.apply_answer path every other answer takes; the gateway still decides which card takes which verb.
 *
 * Run: npx tsx --test src/routes/unifiedApprovalsTaskVerb.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { canonicalAnswerArgv } from './_canonical-approvals.js';
import { TASK_VERBS, taskVerbOf } from './unified-approvals.js';

const SRC = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'unified-approvals.ts'), 'utf-8');
const GATEWAY = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'scripts', 'watch_gateway.py'), 'utf-8');

test('a task verb is read from { answer }, with its words as the note', () => {
  assert.deepEqual(taskVerbOf({ answer: 'done' }), { verb: 'done' });
  assert.deepEqual(taskVerbOf({ answer: ' CANT ', answer_text: '  no cable  ' }), { verb: 'cant', text: 'no cable' });
  assert.deepEqual(taskVerbOf({ answer: 'snooze', answer_text: '' }), { verb: 'snooze' });
});

test('any other body is not a task verb, so an option / free-text answer goes on as before', () => {
  for (const body of [{ option_n: '1' }, { answer_text: 'words' }, { answer: 'approve' }, { answer: 'option', option_n: '2' },
    { answer: 3 }, null, undefined, 'done', []]) {
    assert.equal(taskVerbOf(body), null, JSON.stringify(body));
  }
});

test('the verbs are the gateway\'s own human_task verbs', () => {
  assert.deepEqual([...TASK_VERBS], ['done', 'cant', 'snooze']);
  assert.match(GATEWAY, /if answer not in \("done", "cant", "snooze"\):\s*\n\s*return \{"ok": False, "status": 400,\s*\n\s*"error": "human_task verbs are done\|cant\|snooze"/);
});

test('a task verb reaches approval.py as --answer <verb>, its note as --text', () => {
  const argv = canonicalAnswerArgv('apr_task1', 'cant', 'no cable', undefined, { surface: 'chat' });
  assert.equal(argv[argv.indexOf('--answer') + 1], 'cant');
  assert.equal(argv[argv.indexOf('--text') + 1], 'no cable');
  assert.ok(!argv.includes('--option-n') && !argv.includes('--answer-text'));
});

test('the answer route takes a task verb without option_n / answer_text, and maps a snooze', () => {
  const route = SRC.slice(SRC.indexOf("router.post('/:id/answer'"), SRC.indexOf('\n});\n', SRC.indexOf("router.post('/:id/answer'")));
  const task = route.indexOf('const task = taskVerbOf(req.body);');
  const a1 = route.indexOf("'option_n or answer_text is required'");
  assert.ok(task > 0 && a1 > task, 'the task verb is read before the A1 check');
  assert.match(route, /if \(!task && !optionN && !answerText\) \{/, 'A1 does not refuse a task verb');
  // A task verb carries no option, so it never skips the admin check on a gate card's option 1 / 4.
  assert.match(route, /const optionN = !task && req\.body\?\.option_n != null/);
  assert.ok(route.indexOf('gateOptionRefusal(') < route.indexOf('canonicalAnswer('), 'the admin check still runs before any answer');
  assert.match(route, /canonicalAnswer\(id, task\.verb, task\.text, undefined, \{ surface: req\.body\?\.surface, peer: answerPeer\(req\) \}\)/);
  assert.match(route, /if \(task && result\.ok && result\.snoozed\) \{\s*res\.json\(\{ status: 'snoozed'/);
  // A snooze records nothing (applied false): it must be read as snoozed before "not found".
  assert.ok(route.indexOf('result.snoozed') < route.indexOf('result.applied === false'));
});
