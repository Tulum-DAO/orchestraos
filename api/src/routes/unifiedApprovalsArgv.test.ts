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
import { canonicalAnswerArgv, answerSurface, answerPeer, answerRefusalStatus } from './_canonical-approvals.js';

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
  assert.match(SRC, /canonicalAnswer\(id, 'approve', undefined, undefined, \{ surface: req\.body\?\.surface, peer: answerPeer\(req\) \}\)/);
  assert.match(SRC, /canonicalAnswer\(id, 'deny', reason, undefined, \{ surface: req\.body\?\.surface, peer: answerPeer\(req\) \}\)/);
  assert.match(SRC, /canonicalAnswer\(id, 'option', undefined, \{ optionN, answerText \}, \{ surface: req\.body\?\.surface, peer: answerPeer\(req\) \}\)/);
});

// Plan Task 7a: only a person answers a safety-gate card. approval.py works out who is calling from the
// request's peer, so every answer route passes it, and a missing socket reads as unknown (never as a person).
const LEGACY = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'approvals.ts'), 'utf-8');

test('every answer route passes its peer, and a gate refusal is a 403', () => {
  assert.equal((SRC.match(/= canonicalAnswer\(/g) || []).length, (SRC.match(/peer: answerPeer\(req\)/g) || []).length);
  assert.equal((LEGACY.match(/= canonicalAnswer\(/g) || []).length, (LEGACY.match(/peer: answerPeer\(req\)/g) || []).length);
  assert.ok(!/res\.status\(400\)\.json\(\{ error: result\.error/.test(SRC + LEGACY), 'a refused answer must map its status');
  assert.equal(answerRefusalStatus({ ok: false, id: 'x', error: 'agent_cannot_answer_gate_card' }), 403);
  assert.equal(answerRefusalStatus({ ok: false, id: 'x', error: 'bad option' }), 400);
});

test('the peer reaches approval.py; a missing or malformed one is unknown, never a person', () => {
  const flag = (argv: string[], f: string) => (argv.includes(f) ? argv[argv.indexOf(f) + 1] : undefined);
  const req = (socket: unknown, headers: Record<string, unknown> = {}) => ({ socket, headers }) as any;
  const p = answerPeer(req({ remoteAddress: '127.0.0.1', remotePort: 50123, localPort: 8888 },
    { 'x-orchestra-peer': '127.0.0.1|40001|8891', 'x-forwarded-for': '10.0.0.9, 100.64.1.2' }));
  assert.deepEqual(p, { peer: '127.0.0.1|50123|8888', forwardedPeer: '127.0.0.1|40001|8891', forwardedFor: '100.64.1.2' });
  const argv = canonicalAnswerArgv('apr_1', 'option', undefined, { optionN: '1' }, { peer: p });
  assert.equal(flag(argv, '--peer'), '127.0.0.1|50123|8888');
  assert.equal(flag(argv, '--forwarded-peer'), '127.0.0.1|40001|8891');
  assert.equal(flag(argv, '--forwarded-for'), '100.64.1.2');
  assert.equal(flag(canonicalAnswerArgv('apr_1', 'approve'), '--peer'), 'unknown');
  const bad = answerPeer(req(null, { 'x-orchestra-peer': '1.2.3.4|1|2; rm -rf /', 'x-forwarded-for': '$(x)' }));
  assert.deepEqual(bad, {});
  assert.equal(flag(canonicalAnswerArgv('apr_1', 'approve', undefined, undefined, { peer: bad }), '--peer'), 'unknown');
  assert.deepEqual(answerPeer(req({ remoteAddress: '::ffff:127.0.0.1', remotePort: 5, localPort: 6 })), { peer: '::ffff:127.0.0.1|5|6' });
});
