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
import { spawnSync } from 'node:child_process';
import { testTmpDir } from '../lib/testTmp.js';
import { canonicalAnswerArgv, answerFailure, type CanonicalRow } from './_canonical-approvals.js';
import { TASK_VERBS, taskVerbOf, normalizeCanonical } from './unified-approvals.js';
import { snoozedUntilColumn } from './agents.js';

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
  assert.ok(argv.includes('--text=no cable'), 'the note rides in the --text=value form');
  assert.ok(!argv.some((a) => a.startsWith('--option-n') || a.startsWith('--answer-text')));
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

// A one-word note or reason that starts with "-" ("--help") was read by argparse as a flag: the answer was refused,
// and the 400 carried the whole python command line (the server's path, the caller's peer, the card id).

test('words that look like a flag stay one --flag=value argument: a task note, a deny reason, a menu write-in', () => {
  assert.ok(canonicalAnswerArgv('apr_1', 'cant', '--help').includes('--text=--help'));
  assert.ok(canonicalAnswerArgv('apr_1', 'deny', '-nope').includes('--text=-nope'));
  assert.ok(canonicalAnswerArgv('apr_1', 'option', undefined, { answerText: '--version' }).includes('--answer-text=--version'));
  for (const argv of [canonicalAnswerArgv('apr_1', 'cant', '--help'), canonicalAnswerArgv('apr_1', 'deny', '-nope')]) {
    assert.ok(!argv.includes('--text'), 'never a bare --text followed by the words');
  }
});

test('approval.py parses a "--help" note as the note (argparse, run for real; it stops at an unknown flag)', () => {
  // A trailing unknown flag makes argparse stop right after parsing, before anything is read or written. Its
  // complaint names only that flag when the note parsed; a bare `--text --help` fails on the note itself.
  const scratch = testTmpDir('answer-argv-');
  {
    const env = { ...process.env, ORCHESTRA_DIR: scratch, APPROVAL_DB_PATH: join(scratch, 'tasks.db') };
    const run = (argv: string[]) => spawnSync('python3', [...argv, '--zz-stop-here'], { encoding: 'utf-8', env, timeout: 20000 });
    const ours = run(canonicalAnswerArgv('apr_argv_test', 'cant', '--help'));
    assert.equal(ours.status, 2);
    assert.match(ours.stderr, /unrecognized arguments: --zz-stop-here\s*$/);
    assert.doesNotMatch(ours.stderr, /expected one argument|show this help/);
    // The old form, for contrast: argparse takes the note for a flag.
    const old = canonicalAnswerArgv('apr_argv_test', 'cant', 'PLACEHOLDER').map((a) => (a === '--text=PLACEHOLDER' ? ['--text', '--help'] : [a])).flat();
    const theirs = run(old);
    assert.notEqual(theirs.status, null);
    assert.ok(/expected one argument/.test(theirs.stderr) || /show this help/.test(theirs.stdout), 'the bare form breaks');
  }
});

test('a failed answer returns a plain reason, never the python command line', () => {
  const crash = Object.assign(new Error('Command failed: python3 /srv/app/scripts/approval.py answer --id apr_1 --peer 10.0.0.1|5|8080'),
    { stdout: '', stderr: 'usage: approval.py ...' });
  const orig = console.error;
  console.error = () => {};
  try {
    const r = answerFailure(crash, 'apr_1');
    assert.deepEqual(r, { ok: false, error: 'the answer could not be recorded', id: 'apr_1' });
    assert.doesNotMatch(JSON.stringify(r), /python3|approval\.py|--peer|\/srv\//);
  } finally { console.error = orig; }
  // A refusal's own verdict (printed to stdout, exit non-zero) is still the result.
  const refused = Object.assign(new Error('Command failed: python3 …'), { stdout: '{"ok": false, "status": 400, "error": "human_task verbs are done|cant|snooze", "id": "apr_1"}\n' });
  assert.deepEqual(answerFailure(refused, 'apr_1'), { ok: false, status: 400, error: 'human_task verbs are done|cant|snooze', id: 'apr_1' });
});

test('a snoozed task carries snoozed_until to the dashboard (Approvals feed and chat)', () => {
  const row = { id: 'apr_t', from_agent: 'dev', question: 'Plug in the phone', op_key: 'k', options: ['done', 'cant', 'snooze'],
    status: 'pending', created_at: '2026-11-21T10:00:00Z', kind: 'human_task', summary: null, risk_level: null,
    reversibility: null, feature: null, provider: null, snoozed_until: '2026-11-21T12:00:00+00:00' } as CanonicalRow;
  assert.equal(normalizeCanonical(row).snoozed_until, '2026-11-21T12:00:00+00:00');
  assert.equal(normalizeCanonical({ ...row, snoozed_until: undefined }).snoozed_until, null);
  // The chat's SELECT names the column only where it exists (an unarmed install has none, and a SELECT naming it
  // would come back empty: queryDb swallows the error).
  assert.equal(snoozedUntilColumn(['id', 'kind', 'snoozed_until']), ', snoozed_until');
  assert.equal(snoozedUntilColumn(['id', 'kind']), '');
});
