/**
 * runInstall's process handling, with harmless shell lines (echo / exit / sleep): never a real installer.
 * Run: npx tsx --test src/lib/providerConnect/productionDeps.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tmpdir } from 'node:os';
import { runInstall } from './productionDeps.js';

const cwd = tmpdir();

test('the last lines of a failing command are kept (npm prints its EACCES last)', async () => {
  const r = await runInstall('for i in $(seq 1 200); do echo "line $i"; done; echo "npm error code EACCES" >&2; exit 243', { cwd });
  assert.equal(r.ok, false);
  assert.match(r.log, /line 200/);
  assert.match(r.log, /npm error code EACCES\s*$/);
});

test('success is exit 0', async () => {
  const r = await runInstall('echo installed', { cwd });
  assert.deepEqual([r.ok, r.log.trim()], [true, 'installed']);
});

test('a background helper holding the pipes does not hold the result past the cap', async () => {
  const t0 = Date.now();
  const r = await runInstall('(sleep 5; echo late) & echo done', { cwd, closeCapMs: 300 });
  assert.equal(r.ok, true);
  assert.match(r.log, /done/);
  assert.ok(Date.now() - t0 < 3000, `took ${Date.now() - t0} ms`);
});

test('a timeout kills the whole process group and is reported', async () => {
  const t0 = Date.now();
  const r = await runInstall('sleep 30 | cat; echo never', { cwd, timeoutMs: 300, closeCapMs: 300 });
  assert.equal(r.ok, false);
  assert.match(r.log, /timed out/);
  assert.doesNotMatch(r.log, /never/);
  assert.ok(Date.now() - t0 < 4000, `took ${Date.now() - t0} ms`);
});
