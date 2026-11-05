/**
 * POST /api/approvals/unified/:id/approve and /:id/deny built their python-bridge scripts with the
 * proposal id and the free-text reason pasted into the source. Structural, over the route source: no
 * request value may appear inside a python template, and both values travel as argv.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { canonicalAnswerArgv, answerSurface } from './_canonical-approvals.js';

const SRC = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'unified-approvals.ts'), 'utf-8');

test('no request value is interpolated into a python script', () => {
  assert.ok(!/proposal_id = "\$\{/.test(SRC), 'proposal id pasted into python source');
  assert.ok(!/reason = """\$\{/.test(SRC), 'reason pasted into python source');
  // every ${...} inside an execDb template must be the constant DB_PATH
  for (const call of SRC.split('execDb(').slice(1)) {
    const tpl = call.slice(0, call.indexOf(');'));
    for (const m of tpl.matchAll(/\$\{([^}]*)\}/g)) assert.equal(m[1].trim(), 'DB_PATH', `interpolated: ${m[1]}`);
  }
});

test('the id and reason travel as argv and the scripts read sys.argv', () => {
  assert.match(SRC, /execFileSync\('python3', \['-c', script, \.\.\.args\]/);
  assert.equal((SRC.match(/proposal_id = sys\.argv\[1\]/g) || []).length, 2);
  assert.equal((SRC.match(/reason = sys\.argv\[2\]/g) || []).length, 2);
  assert.match(SRC, /\[String\(id\), String\(reason \|\| ''\)\]/);
  assert.match(SRC, /\[String\(id\), String\(reason\)\]/);
});

// The answer surface hint (attention part 1): a chat answer is recorded as surface chat so the
// list can say where it was answered. The client may only ask for chat; anything else is web, the
// web route's own surface, so no caller can claim to be the phone or the watch.
test('surface chat reaches approval.py as --surface chat; any other value is web', () => {
  const surfaceOf = (argv: string[]) => argv[argv.indexOf('--surface') + 1];
  assert.equal(surfaceOf(canonicalAnswerArgv('apr_1', 'approve', undefined, undefined, { surface: 'chat' })), 'chat');
  assert.equal(surfaceOf(canonicalAnswerArgv('apr_1', 'approve', undefined, undefined, { surface: 'watch' })), 'web');
  assert.equal(surfaceOf(canonicalAnswerArgv('apr_1', 'deny', 'no', undefined, { surface: 42 })), 'web');
  assert.equal(surfaceOf(canonicalAnswerArgv('apr_1', 'approve')), 'web');
  assert.equal(canonicalAnswerArgv('apr_1', 'approve', undefined, undefined, { surface: 'chat' }).filter((a) => a === '--surface').length, 1);
  assert.equal(answerSurface('chat'), 'chat');
  for (const v of ['watch', 'phone', 'web', 'rule', '', null, undefined, 'CHAT']) assert.equal(answerSurface(v), 'web', String(v));
});

test('approve, deny and answer pass the body surface to the canonical answer', () => {
  assert.match(SRC, /canonicalAnswer\(id, 'approve', undefined, undefined, \{ surface: req\.body\?\.surface \}\)/);
  assert.match(SRC, /canonicalAnswer\(id, 'deny', reason, undefined, \{ surface: req\.body\?\.surface \}\)/);
  assert.match(SRC, /canonicalAnswer\(id, 'option', undefined, \{ optionN, answerText \}, \{ surface: req\.body\?\.surface \}\)/);
});
