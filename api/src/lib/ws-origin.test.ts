import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { wsOriginDecision } from './ws-origin.js';

// The SAME cases the dashboard's check runs (test_dashboard_ws_origin.py), so the API and
// the dashboard cannot disagree about who may open a terminal WebSocket.
const here = dirname(fileURLToPath(import.meta.url));
const { cases } = JSON.parse(
  readFileSync(join(here, '..', '..', '..', 'contract', 'ws-origin-cases.json'), 'utf8'),
) as { cases: { name: string; in: Record<string, string>; allow: boolean }[] };

test('the shared case file is not empty', () => {
  assert.ok(cases.length >= 20, `only ${cases.length} cases`);
});

for (const c of cases) {
  test(`ws origin: ${c.name}`, () => {
    const d = wsOriginDecision({ port: 8891, dashboardHost: '127.0.0.1', extraHosts: '', ...c.in });
    assert.equal(d.allow, c.allow, `${c.name}: ${JSON.stringify(d)}`);
  });
}

test('server.ts guards its terminal WebSocketServer with the check', () => {
  const src = readFileSync(join(here, '..', 'server.ts'), 'utf8');
  assert.match(src, /new WebSocketServer\(\{ server, path: '\/ws\/terminal', verifyClient: /);
});
