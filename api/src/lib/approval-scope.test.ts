/**
 * RED-first: the approve/deny/answer routes of /api/approvals/unified had no check of their own.
 *
 * Under a trusted identity proxy a principal scoped to one client could answer ANY card by id,
 * the operator's included, and the list and stats showed every card to everyone. These pin the
 * one rule (lib/approval-scope.ts) and that the real router applies it.
 *
 * In the default untrusted mode principal() is the configured operator (admin, '*'), so nothing
 * changes there; the untrusted test asserts that.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Request } from 'express';
import type { Principal } from './principal.js';
import { canPrincipalSeeCard, isUnscopedAdmin, makeApprovalScopeParam } from './approval-scope.js';
import { scopeItems, statsOf } from '../routes/unified-approvals.js';

const P = (over: Partial<Principal>): Principal => ({
  username: 'u', role: 'viewer', allowedAgents: [], clientScope: null, trusted: true, ...over,
});
const ADMIN = P({ role: 'admin', allowedAgents: '*' });
const ACME = P({ clientScope: 'acme', allowedAgents: 'acme' });
const TAGS: Record<string, string[]> = { 'pm-acme': ['client:acme'], 'pm-other': ['client:other'], gm: [] };
const tags = (id: string) => TAGS[id];

// ---- the rule --------------------------------------------------------------------------------

test('the operator (admin, no client scope) sees every card; no principal sees none', () => {
  assert.equal(canPrincipalSeeCard(ADMIN, 'gm', tags), true);
  assert.equal(canPrincipalSeeCard(ADMIN, null, tags), true);
  assert.equal(canPrincipalSeeCard(null, 'pm-acme', tags), false);
});

test("a client scope sees only its own client's cards, even when its role says admin", () => {
  assert.equal(canPrincipalSeeCard(ACME, 'pm-acme', tags), true);
  assert.equal(canPrincipalSeeCard(ACME, 'gm', tags), false);
  assert.equal(canPrincipalSeeCard(ACME, 'pm-other', tags), false);
  assert.equal(canPrincipalSeeCard(ACME, null, tags), false);
  assert.equal(isUnscopedAdmin(P({ role: 'admin', clientScope: 'acme' })), false);
  assert.equal(canPrincipalSeeCard(P({ role: 'admin', clientScope: 'acme' }), 'gm', tags), false);
});

// ---- the /:id guard ----------------------------------------------------------------------------

function run(p: Principal | null, id: string, owners: Record<string, string> = { apr_acme: 'pm-acme', apr_gm: 'gm' }) {
  const guard = makeApprovalScopeParam({
    principal: () => p, pendingCardAgent: (i) => owners[i] ?? null, resolveTags: tags,
  });
  let status = 0; let passed = false;
  const res: any = { status(s: number) { status = s; return res; }, json() { return res; } };
  guard({ headers: {} } as Request, res, () => { passed = true; }, id);
  return { status, passed };
}

test('no identity is a 401, and nothing reaches the handler', () => {
  assert.deepEqual(run(null, 'apr_gm'), { status: 401, passed: false });
});

test("a client-scoped viewer answers its own client's card, and gets the route's 404 for any other", () => {
  assert.deepEqual(run(ACME, 'apr_acme'), { status: 0, passed: true });
  assert.deepEqual(run(ACME, 'apr_gm'), { status: 404, passed: false });           // the operator's card
  assert.deepEqual(run(ACME, 'prop_123'), { status: 404, passed: false });         // a learning proposal
  assert.deepEqual(run(ACME, 'apr_nope'), { status: 404, passed: false });         // unknown: same answer
});

test('the operator acts on anything, known or not (the handler answers unknown ids itself)', () => {
  assert.deepEqual(run(ADMIN, 'apr_gm'), { status: 0, passed: true });
  assert.deepEqual(run(ADMIN, 'prop_123'), { status: 0, passed: true });
  assert.deepEqual(run(ADMIN, 'apr_nope'), { status: 0, passed: true });
});

// ---- the list and stats -------------------------------------------------------------------------

const item = (over: any) => ({
  id: 'x', type: 'agent', urgency: 'normal', status: 'pending', title: '', description: '', risk_level: 'low',
  evidence_summary: '', qa_verdict: null, sandbox_result: null, onepager_url: null, created_at: '', updated_at: '',
  source: 'agent_request', agent_id: 'gm', ...over,
});
const LIST = { items: [
  item({ id: 'apr_gm', agent_id: 'gm' }),
  item({ id: 'apr_acme', agent_id: 'pm-acme', urgency: 'critical' }),
  item({ id: 'apr_acme_old', agent_id: 'pm-acme', status: 'approved' }),
  item({ id: 'prop_1', type: 'learning', agent_id: null }),
], total: 4 };

test('the list: the operator gets everything; a client scope only its pending cards', () => {
  assert.equal(scopeItems(LIST as any, ADMIN, tags).total, 4);
  assert.deepEqual(scopeItems(LIST as any, ACME, tags).items.map((i) => i.id), ['apr_acme']);
});

test('stats of a scoped list count only what that caller can see', () => {
  const s = statsOf(scopeItems(LIST as any, ACME, tags).items);
  assert.equal(s.total, 1);
  assert.equal(s.by_status.pending, 1);
  assert.equal(s.by_urgency.critical, 1);
  assert.equal(s.by_type.learning, 0);
});

// ---- THE WIRING: the real router -----------------------------------------------------------------
// Without this, unhooking router.param('id') would leave every test above green.
// The router reads its data dir at import, so each test imports it fresh over an empty temp one.
function freshDataDir() {
  const d = mkdtempSync(join(tmpdir(), 'apr-scope-'));
  // one pending card asked by gm (the operator's), in the frozen filesystem store the list still reads
  mkdirSync(join(d, 'state', 'approvals', 'pending'), { recursive: true });
  writeFileSync(join(d, 'state', 'approvals', 'pending', 'apr_gm_fs.json'),
    JSON.stringify({ id: 'apr_gm_fs', agent_id: 'gm', title: 'Operator card', status: 'pending' }));
  process.env.ORCHESTRA_DIR = d;
}

async function call(app: express.Express, method: string, path: string, headers: Record<string, string> = {}) {
  const server = app.listen(0);
  try {
    const port = (server.address() as any).port;
    return await new Promise<{ status: number; body: any }>((resolve, reject) => {
      const r = http.request({ host: '127.0.0.1', port, method, path, headers: { 'content-type': 'application/json', ...headers } }, (res) => {
        let out = ''; res.on('data', (c) => (out += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, body: JSON.parse(out || '{}') }));
      });
      r.on('error', reject);
      r.setTimeout(10000, () => r.destroy(new Error(`no response in 10s: ${method} ${path}`)));
      r.end(method === 'POST' ? JSON.stringify({ reason: 'r', option_n: '1' }) : undefined);
    });
  } finally { server.close(); }
}

test('the real router: behind a proxy, no identity is a 401 everywhere; a client scope never sees or answers an operator card', async () => {
  const prev = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  try {
    freshDataDir();
    const router = (await import(`../routes/unified-approvals.js?t=${Date.now()}`)).default;
    const app = express(); app.use(express.json()); app.use('/u', router);
    for (const [m, p] of [['POST', '/u/apr_x/approve'], ['POST', '/u/apr_x/deny'], ['POST', '/u/apr_x/answer'],
                          ['GET', '/u'], ['GET', '/u/stats']] as const) {
      assert.equal((await call(app, m, p)).status, 401, `${m} ${p}`);
    }
    // a client-scoped caller asking for a card that is not its own gets the 404, not the handler
    const acme = { 'x-orchestra-user': 'c', 'x-orchestra-role': 'viewer', 'x-orchestra-client': 'acme' };
    const r = await call(app, 'POST', '/u/apr_x/approve', acme);
    assert.equal(r.status, 404);
    // the list and stats: the operator sees the gm card, the client-scoped caller does not
    const op = { 'x-orchestra-user': 'op', 'x-orchestra-role': 'admin', 'x-orchestra-allowed-agents': '*' };
    assert.ok((await call(app, 'GET', '/u?type=agent', op)).body.items.some((i: any) => i.id === 'apr_gm_fs'));
    assert.deepEqual((await call(app, 'GET', '/u?type=agent', acme)).body.items, []);
    assert.equal((await call(app, 'GET', '/u/stats', acme)).body.total, 0);
    assert.ok((await call(app, 'GET', '/u/stats', op)).body.by_type.agent >= 1);
  } finally {
    if (prev === undefined) delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
    else process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prev;
  }
});

test('the real router, default mode (no proxy): the operator is unchanged — the list answers 200', async () => {
  const prev = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  try {
    freshDataDir();
    const router = (await import(`../routes/unified-approvals.js?t=${Date.now()}`)).default;
    const app = express(); app.use(express.json()); app.use('/u', router);
    const r = await call(app, 'GET', '/u?type=learning', { 'x-orchestra-user': 'eve', 'x-orchestra-client': 'acme' });
    assert.equal(r.status, 200);
    assert.ok(Array.isArray(r.body.items));
  } finally {
    if (prev !== undefined) process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prev;
  }
});
