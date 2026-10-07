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
