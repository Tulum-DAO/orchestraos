/**
 * The API's call-context builder and the Python loader (services/arturo/thread_context.py) produce the same bytes:
 * contract/call-context-cases.json is generated from the Python reference and both suites run it. Plus the route's
 * budget clamp edges.
 *
 * Run: npx tsx --test src/lib/callContext.test.ts   (from api/)
 */
import { testTmpDir } from './testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCallContext, readJournals, clampBudget } from './callContext.js';

const here = dirname(fileURLToPath(import.meta.url));
const cases = JSON.parse(readFileSync(join(here, '..', '..', '..', 'contract', 'call-context-cases.json'), 'utf-8')).cases;

for (const c of cases) {
  test(`call-context bytes match the Python reference: ${c.name}`, () => {
    const dir = testTmpDir('call-context-');
    mkdirSync(dir, { recursive: true });
    for (const j of c.journals) writeFileSync(join(dir, j.name), j.raw ?? JSON.stringify(j.doc));
    const out = buildCallContext({
      chatAgent: c.chat_agent, callOwner: c.call_owner, items: c.items, chatError: c.chat_error ?? null,
      journals: readJournals(dir, Date.now() / 1000), exclude: c.exclude, budgetTokens: c.budget,
      tz: c.tz, tzLabel: c.tz_label, operatorLabel: c.operator_label,
    });
    assert.equal(out, c.expected);
  });
}

test('the budget clamps to 500..20000 and defaults to 6000', () => {
  assert.equal(clampBudget(499), 500);
  assert.equal(clampBudget(500), 500);
  assert.equal(clampBudget(20000), 20000);
  assert.equal(clampBudget(20001), 20000);
  assert.equal(clampBudget(undefined), 6000);
  assert.equal(clampBudget('abc'), 6000);
});

test('secrets are blanked before the clip, by the default shapes and by a passed redactor', () => {
  const token = 'gwtok_' + 'Q7'.repeat(20);
  const items = [
    { kind: 'text', role: 'user', text: 'x'.repeat(1190) + ' sk-proj_abcdefghijklmnop' + 'y'.repeat(40), ts: '2026-11-25T17:00:00Z', audience: 'user' },
    { kind: 'text', role: 'user', text: 'x'.repeat(1170) + ' ' + token + ' tail', ts: '2026-11-25T17:00:01Z', audience: 'user' },
  ];
  const base = { chatAgent: 'gm', callOwner: null, items, journals: [], tz: 'UTC', tzLabel: 'UTC', operatorLabel: 'S' };
  const plain = buildCallContext(base);
  assert.ok(!plain.includes('sk-proj'), 'a key cut at the clip boundary shipped as a prefix');
  const full = buildCallContext({ ...base, redact: (t: string) => t.split(token).join('[redacted]') });
  assert.ok(!full.includes(token.slice(0, 20)), 'the install token cut at the clip boundary shipped as a prefix');
});

test('the opening is fenced as past context that is over, the boundary last (live probe 2026-11-25)', async () => {
  const { PAST_OPEN, PAST_CLOSE } = await import('./callContext.js');
  const base = { chatAgent: 'gm', callOwner: null, journals: [], tz: 'UTC', tzLabel: 'UTC' };
  for (const o of [{ ...base, items: [] }, { ...base, items: null, chatError: 'down' }]) {
    const lines = buildCallContext(o).split('\n');
    assert.equal(lines[0], PAST_OPEN);
    assert.equal(lines[lines.length - 1], PAST_CLOSE);
  }
});
