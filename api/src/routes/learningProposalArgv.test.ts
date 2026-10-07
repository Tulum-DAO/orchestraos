/**
 * The proposal approve/reject routes pass the proposal id to the python bridge as an ARGV value,
 * never as text inside the python script. Structural test over the route source: it reads
 * learning.ts and needs no database, no server and no python.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'learning.ts'), 'utf-8');

function handler(route: string): string {
  const start = SRC.indexOf(`router.post('${route}'`);
  assert.ok(start >= 0, `route ${route} not found`);
  const next = SRC.indexOf('\nrouter.', start + 1);
  return SRC.slice(start, next < 0 ? undefined : next);
}

for (const route of ['/proposals/:id/approve', '/proposals/:id/reject']) {
  test(`${route}: the id reaches python only via argv`, () => {
    const body = handler(route);
    assert.ok(!body.includes('${req.params.id}'), 'the id must never be placed inside the script text');
    assert.ok(body.includes('sys.argv[1]'), 'the script must read the id from sys.argv[1]');
    assert.match(body, /execFileSync\('python3', \['-c', script, String\(req\.params\.id\)\]/,
      'the id must be passed as its own argv element after the script');
  });

  test(`${route}: the id is checked before anything runs`, () => {
    const body = handler(route);
    const check = body.indexOf('SAFE_PROPOSAL_ID.test(');
    const run = body.indexOf('execFileSync(');
    assert.ok(check >= 0 && run > check, 'the id check must come before the python call');
  });
}

test('SAFE_PROPOSAL_ID accepts every real proposal id and refuses anything else', () => {
  const m = SRC.match(/const SAFE_PROPOSAL_ID = \/(.+)\/;/);
  assert.ok(m, 'SAFE_PROPOSAL_ID not found');
  const re = new RegExp(m![1]);
  for (const id of ['prop_207d7c', 'prop_deny01', 'prop_e292ba']) assert.ok(re.test(id), id);
  for (const id of ['', 'a"b', 'a/b', 'a.b', 'a b', 'x'.repeat(129)]) assert.ok(!re.test(id), JSON.stringify(id));
});
