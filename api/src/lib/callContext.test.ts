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
    for (const j of c.journals) writeFileSync(join(dir, j.name), JSON.stringify(j.doc));
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

test('a planted secret never reaches the output', () => {
  const c = cases.find((x: any) => x.name.startsWith('budget summarizes'));
  assert.ok(!c.expected.includes('abcdefghijklmnop') && !c.expected.includes('hunter2xyz'));
  assert.ok(c.expected.includes('[redacted]'));
});
