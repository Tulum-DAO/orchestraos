/**
 * Call-site test for the tenant/sort/inbox fixes: the routes must actually USE the helpers
 * (lib/sqlScope.ts, lib/agentPaths.ts). Structural, over the route sources: no database, no server.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = (f: string) => readFileSync(join(here, f), 'utf-8');

for (const f of ['tasks-v2.ts', 'northstars.ts', 'people.ts']) {
  test(`${f}: the tenant value is never interpolated into SQL`, () => {
    const s = src(f);
    assert.ok(!/tenant_id = '\$\{/.test(s), 'raw tenant interpolation is back');
    assert.ok(s.includes('tenantFilter(scope'), 'the route must build its tenant clause with tenantFilter');
  });
}

for (const f of ['tasks-v2.ts', 'people.ts']) {
  test(`${f}: ORDER BY only takes an allow-listed column, unknown -> 400`, () => {
    const s = src(f);
    assert.ok(/const sort = sortColumn\(req\.query\.sort,/.test(s), 'sort must go through sortColumn');
    assert.ok(/if \(!sort\) \{ res\.status\(400\)/.test(s), 'an unknown sort column must be a 400');
    assert.ok(!/\(req\.query\.sort as string\) \|\|/.test(s), 'the raw query value must not be used directly');
  });
}

test('tasks-v2.ts: both inbox writes go through inboxDirFor, never a raw join', () => {
  const s = src('tasks-v2.ts');
  assert.ok(!/jn\(oDir, 'queue', 'inbox', task\.created_by\)/.test(s), 'raw created_by inbox join is back');
  assert.ok(!/jn\(oDir, 'queue', 'inbox', ct\.routed_to\)/.test(s), 'raw routed_to inbox join is back');
  assert.equal((s.match(/inboxDirFor\(oDir, /g) || []).length, 2, 'both write sites must use inboxDirFor');
});

test('tasks-v2.ts: created_by / assigned_to / routed_to are validated at the entry points', () => {
  const s = src('tasks-v2.ts');
  assert.ok(s.includes("for (const f of ['created_by', 'assigned_to'] as const)"), 'POST must validate both');
  assert.ok(/u\.routed_to !== undefined[^\n]*!isSafeAgentId\(u\.routed_to\)/.test(s), 'PATCH must validate routed_to');
});
