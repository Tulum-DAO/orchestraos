/**
 * /api/usage: the dashboard's path to plan usage. Pure over an injected fetch; nothing here opens a
 * socket to a real gateway.
 * Run: npx tsx --test src/routes/usage.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { mkdirSync, readFileSync, existsSync, writeFileSync } from 'node:fs';
import { testTmpDir } from '../lib/testTmp.js';
import { join } from 'node:path';
import { createUsageRouter, defaultOriginAllowed, type UsageDeps } from './usage.js';
import { configuredOrigins } from '../lib/cors-origin.js';
import { loadConfig } from '../lib/config.js';
import { CODEX_TERMS_LINE, TERMS_LINE, type AccountsView, type CliResult, type RunCli, type SwitchAnswer } from '../lib/accountSwitch.js';

type Call = { url: string; init: RequestInit };
const ROWS = [{ provider: 'codex', window: '5h', used_pct: 41, stale: false }];

function makeDeps(over: Partial<UsageDeps> = {}): UsageDeps & { calls: Call[] } {
  const calls: Call[] = [];
  const deps: any = {
    token: () => 'test-token',
    gatewayUrl: () => 'http://gw.test',
    fetchJson: async (url: string, init: RequestInit) => {
      calls.push({ url, init }); return { status: 200, body: { ok: true, usage: ROWS } };
    },
    isAdmin: () => true,
    ...over,
  };
  deps.calls = calls;
  return deps;
}

async function get(deps: UsageDeps, path: string) {
  const app = express();
  app.use('/api', createUsageRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const r = http.request({ host: '127.0.0.1', port, path, method: 'GET' }, (res) => {
        let out = '';
        res.on('data', (c) => (out += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, json: JSON.parse(out || '{}') }));
      });
      r.on('error', reject);
      r.end();
    });
  } finally {
    server.close();
  }
}

test('GET /api/usage forwards to the gateway with its bearer', async () => {
  const deps = makeDeps();
  const r = await get(deps, '/api/usage');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.usage, ROWS);
  assert.equal(deps.calls[0].url, 'http://gw.test/usage');
  assert.equal((deps.calls[0].init.headers as any).Authorization, 'Bearer test-token');
});

test('a non-admin (a client user behind the sign-in proxy) gets 404 and the gateway is never called', async () => {
  const deps = makeDeps({ isAdmin: () => false });
  const r = await get(deps, '/api/usage');
  assert.equal(r.status, 404);
  assert.equal(deps.calls.length, 0);
});

test('no gateway token is a 502, not an empty meter', async () => {
  const r = await get(makeDeps({ token: () => '' }), '/api/usage');
  assert.equal(r.status, 502);
});

test('an unreachable gateway is a 502', async () => {
  const r = await get(makeDeps({ fetchJson: async () => { throw new Error('ECONNREFUSED'); } }), '/api/usage');
  assert.equal(r.status, 502);
  assert.equal(r.json.error, 'gateway unreachable');
});

// ---- the Claude cards: GET /api/usage/accounts, POST /api/usage/account ("Use this account for all agents") ----

async function post(deps: UsageDeps, path: string, body: string, headers: Record<string, string>) {
  const app = express();
  app.use('/api', createUsageRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const r = http.request({ host: '127.0.0.1', port, path, method: 'POST', headers: { ...headers, 'Content-Length': Buffer.byteLength(body) } }, (res) => {
        let out = '';
        res.on('data', (c) => (out += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, json: JSON.parse(out || '{}') }));
      });
      r.on('error', reject);
      r.end(body);
    });
  } finally {
    server.close();
  }
}
const JSONH = { 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin' };

const VIEW: AccountsView = { provider: 'claude', shared: 'default', followers: 3, terms_ack: false, terms_text: TERMS_LINE,
  current: { account: 'default', name: 'bizypro', email: 'a@bizypro.co', since: null, since_text: null, by: null }, seats: {},
  accounts: [
    { id: 'default', name: 'bizypro', email: 'a@bizypro.co', where: 'shared', agents: true, canUse: false, whyNot: null },
    { id: 'beta', name: 'strategysimple', email: 'b@strategysimple.com', where: 'home', agents: false, canUse: true, whyNot: null },
  ] };

function cardDeps(over: Partial<UsageDeps> & { answer?: SwitchAnswer; acked?: boolean; view?: AccountsView | null } = {}) {
  const dir = testTmpDir('usage-acct-');
  mkdirSync(join(dir, 'state'), { recursive: true });
  writeFileSync(join(dir, 'state', 'claude_profiles.json'), JSON.stringify({ profiles: [
    { id: 'default', config_dir: '~/.claude', enrolled: true }, { id: 'beta', config_dir: '~/.claude-beta', enrolled: true },
    { id: 'codexwork', provider: 'codex', config_dir: '~/.codex', enrolled: true }] }));
  if (over.acked) writeFileSync(join(dir, 'state', 'account_terms_ack.json'), JSON.stringify({ acknowledged: true }));
  const switched: Array<{ account: string; dataDir: string; names: Record<string, string> }> = [];
  const deps = makeDeps({
    dataDir: () => dir,
    accounts: async () => (over.view === undefined ? VIEW : over.view),
    switchAll: async (account, dataDir, names) => {
      switched.push({ account, dataDir, names });
      return over.answer ?? { http: 200, body: { ok: true, status: 'switched', account, from: 'default', message: 'moved' } };
    },
    ...over,
  });
  return { deps, dir, switched };
}

test('GET /api/usage/accounts: the cards for an admin; 404 for anyone else', async () => {
  const { deps } = cardDeps();
  const r = await get(deps, '/api/usage/accounts');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.accounts, VIEW);
  assert.equal((await get(cardDeps({ isAdmin: () => false }).deps, '/api/usage/accounts')).status, 404);
  assert.equal((await get(cardDeps({ view: null }).deps, '/api/usage/accounts')).json.accounts, null, 'one account: no cards');
});

test('POST /api/usage/account: the first press without the terms is refused with the terms line; nothing runs', async () => {
  const { deps, switched, dir } = cardDeps();
  const r = await post(deps, '/api/usage/account', JSON.stringify({ account: 'beta' }), JSONH);
  assert.equal(r.status, 409);
  assert.equal(r.json.reason, 'terms-not-acknowledged');
  assert.equal(r.json.message, TERMS_LINE);
  assert.equal(switched.length, 0);
  assert.equal(existsSync(join(dir, 'state', 'account_terms_ack.json')), false);
});

test('POST /api/usage/account: the first press with ack_terms records it and runs the switch for that account', async () => {
  const { deps, switched, dir } = cardDeps();
  const r = await post(deps, '/api/usage/account', JSON.stringify({ account: 'beta', ack_terms: true, extra: 'x' }), JSONH);
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
  assert.equal(r.json.status, 'switched');
  assert.deepEqual(r.json.accounts, VIEW, 'the fresh cards come back with the answer');
  assert.equal(switched.length, 1);
  assert.equal(switched[0].account, 'beta');
  assert.equal(switched[0].dataDir, dir);
  assert.deepEqual(switched[0].names, { default: 'bizypro', beta: 'strategysimple' });
  assert.equal(JSON.parse(readFileSync(join(dir, 'state', 'account_terms_ack.json'), 'utf8')).acknowledged, true);
  const by = JSON.parse(readFileSync(join(dir, 'state', 'account_switch_by.json'), 'utf8'));
  assert.equal(by.to, 'beta');
  assert.match(by.by, /\(dashboard\)$/, 'the top line can say who pressed');
});

test('POST /api/usage/account: a refusal from the switch keeps its status and words', async () => {
  const answer: SwitchAnswer = { http: 409, body: { ok: false, status: 'refused', reason: 'account-live-at-home', account: 'beta', message: 'close it' } };
  const { deps, dir } = cardDeps({ acked: true, answer });
  const r = await post(deps, '/api/usage/account', JSON.stringify({ account: 'beta' }), JSONH);
  assert.equal(r.status, 409);
  assert.equal(r.json.reason, 'account-live-at-home');
  assert.equal(r.json.message, 'close it');
  assert.equal(existsSync(join(dir, 'state', 'account_switch_by.json')), false, 'a refusal records no presser');
  const busy = cardDeps({ acked: true, answer: { http: 503, body: { ok: false, status: 'refused', reason: 'cli-lock-busy', account: 'beta', message: 'wait' } } });
  assert.equal((await post(busy.deps, '/api/usage/account', JSON.stringify({ account: 'beta' }), JSONH)).status, 503);
});

test('POST /api/usage/account: Codex, an unknown account or a pool of one is refused in words; nothing runs', async () => {
  for (const [account, reason, view] of [['codexwork', 'not-claude', VIEW], ['nobody', 'unknown-account', VIEW], ['beta', 'single-account', null]] as const) {
    const { deps, switched } = cardDeps({ acked: true, view });
    const r = await post(deps, '/api/usage/account', JSON.stringify({ account }), JSONH);
    assert.equal(r.status, 409, account);
    assert.equal(r.json.reason, reason, account);
    assert.equal(switched.length, 0, account);
  }
});

test('another site cannot press it: cross-site / same-site Sec-Fetch-Site, or a foreign Origin, is 403', async () => {
  for (const site of ['cross-site', 'same-site']) {
    const { deps, switched } = cardDeps({ acked: true });
    const r = await post(deps, '/api/usage/account', JSON.stringify({ account: 'beta' }), { 'Content-Type': 'application/json', 'Sec-Fetch-Site': site });
    assert.equal(r.status, 403, site);
    assert.equal(switched.length, 0);
  }
  const { deps, switched } = cardDeps({ acked: true, originAllowed: () => false });
  const r = await post(deps, '/api/usage/account', JSON.stringify({ account: 'beta' }), { 'Content-Type': 'application/json', Origin: 'https://evil.example' });
  assert.equal(r.status, 403);
  assert.equal(switched.length, 0);
});

test('a plain form post (not JSON) is 415; a bad account name is 400; a non-admin gets 404; nothing runs', async () => {
  const { deps, switched } = cardDeps({ acked: true });
  assert.equal((await post(deps, '/api/usage/account', 'account=beta', { 'Content-Type': 'application/x-www-form-urlencoded' })).status, 415);
  assert.equal((await post(deps, '/api/usage/account', JSON.stringify({ account: '../x' }), JSONH)).status, 400);
  assert.equal((await post(cardDeps({ isAdmin: () => false }).deps, '/api/usage/account', JSON.stringify({ account: 'beta' }), JSONH)).status, 404);
  assert.equal(switched.length, 0);
});

test('the write check accepts exactly the origins the CORS layer accepts (dashboard, [public].host, env)', () => {
  process.env.ORCHESTRA_API_CORS_ORIGINS = 'https://phone.example:8443';
  try {
    const list = configuredOrigins(loadConfig());
    assert.ok(list.includes('https://phone.example:8443'));
    assert.ok(list.some((o) => /^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(o)), 'the dashboard origin');
    const req: any = { socket: { remoteAddress: '10.0.0.9' }, headers: { host: 'api.internal:9' } };
    for (const o of list) assert.equal(defaultOriginAllowed(o, req), true, o);
    assert.equal(defaultOriginAllowed('https://evil.example', req), false);
  } finally {
    delete process.env.ORCHESTRA_API_CORS_ORIGINS;
  }
});

// ---- the Codex cards: ?provider=codex / {provider: "codex"} through a stubbed account_switch.py ---------------

const CODEX_HOLDERS = {
  shawcole: { where: 'shared', email: 'shaw.cole@bizypro.co', name: 'shaw.cole', agents: 3 },
  shaw: { where: 'vault', email: 'shaw@bizypro.co', name: 'shaw', agents: 0 },
};

function codexDeps(opts: { shared?: CliResult; acked?: 'claude' | 'codex' } = {}) {
  const c = cardDeps(opts.acked === 'claude' ? { acked: true } : {});
  if (opts.acked === 'codex') writeFileSync(join(c.dir, 'state', 'account_terms_ack_codex.json'), JSON.stringify({ acknowledged: true }));
  const calls: string[][] = [];
  const run: RunCli = async (_py, args) => {
    calls.push(args.slice(1));
    if (args.includes('--holders')) return { code: 0, stdout: `${JSON.stringify(CODEX_HOLDERS)}\n`, timedOut: false };
    return opts.shared ?? { code: 0, stdout: `${JSON.stringify({ status: 'switched', from: 'shawcole', to: 'shaw', displaced_to: 'vault', followers: 3 })}\n`, timedOut: false };
  };
  c.deps.run = run;
  return { ...c, calls };
}

test('GET /api/usage/accounts?provider=codex: the Codex cards from --provider codex --holders; Claude\'s unchanged; a bad provider is 400', async () => {
  const { deps, calls } = codexDeps();
  const r = await get(deps, '/api/usage/accounts?provider=codex');
  assert.equal(r.status, 200);
  const v = r.json.accounts;
  assert.equal(v.provider, 'codex');
  assert.equal(v.shared, 'shawcole');
  assert.equal(v.followers, 3);
  assert.equal(v.terms_text, CODEX_TERMS_LINE);
  assert.deepEqual([v.current.account, v.current.name, v.current.email], ['shawcole', 'shaw.cole', 'shaw.cole@bizypro.co']);
  assert.deepEqual(v.accounts.map((a: any) => [a.id, a.name, a.email, a.where, a.agents, a.canUse, a.whyNot]), [
    ['shawcole', 'shaw.cole', 'shaw.cole@bizypro.co', 'shared', 3, false, 'This is the login all Codex agents use now.'],
    ['shaw', 'shaw', 'shaw@bizypro.co', 'vault', 0, true, null]]);
  assert.deepEqual(calls, [['--provider', 'codex', '--holders']]);
  for (const q of ['', '?provider=claude']) {
    const c = await get(deps, `/api/usage/accounts${q}`);
    assert.equal(c.status, 200);
    assert.deepEqual(c.json, { ok: true, accounts: VIEW }, q || 'no provider');
  }
  assert.equal(calls.length, 1, 'Claude never asks the Codex holders; the Codex view is cached separately');
  for (const q of ['?provider=gemini', '?provider=', '?provider=codex&provider=claude']) {
    const b = await get(deps, `/api/usage/accounts${q}`);
    assert.equal(b.status, 400, q);
    assert.deepEqual(b.json, { ok: false, error: 'unknown provider' }, q);
  }
});

test('POST /api/usage/account {provider: codex}: runs --provider codex --shared, words it for Codex, records the presser', async () => {
  const { deps, calls, dir } = codexDeps({ acked: 'codex' });
  const r = await post(deps, '/api/usage/account', JSON.stringify({ provider: 'codex', account: 'shaw' }), JSONH);
  assert.equal(r.status, 200);
  assert.equal(r.json.status, 'switched');
  assert.equal(r.json.message, 'New Codex sessions and reviews use shaw from now on. Running Codex agents keep shaw.cole until their next start. '
    + 'shaw.cole is parked; press its card to switch back.');
  assert.equal(r.json.accounts.provider, 'codex');
  assert.deepEqual(calls.find((a) => a.includes('--shared')), ['--provider', 'codex', '--shared', 'shaw']);
  assert.equal(JSON.parse(readFileSync(join(dir, 'state', 'account_switch_by_codex.json'), 'utf8')).to, 'shaw');
  assert.equal(existsSync(join(dir, 'state', 'account_switch_by.json')), false, 'the Claude presser is untouched');
});

test('POST codex: noop, a refusal in Codex words, an unknown account, a bad provider', async () => {
  const no = codexDeps({ acked: 'codex', shared: { code: 0, stdout: '{"status":"noop"}\n', timedOut: false } });
  const n = await post(no.deps, '/api/usage/account', JSON.stringify({ provider: 'codex', account: 'shawcole' }), JSONH);
  assert.deepEqual([n.status, n.json.status, n.json.message], [200, 'noop', 'shaw.cole is already the login all Codex agents use.']);
  const rf = codexDeps({ acked: 'codex', shared: { code: 2, stdout: '{"status":"refused","reason":"vault-slot-occupied","detail":"/home/x"}\n', timedOut: false } });
  const f = await post(rf.deps, '/api/usage/account', JSON.stringify({ provider: 'codex', account: 'shaw' }), JSONH);
  assert.equal(f.status, 409);
  assert.equal(f.json.reason, 'vault-slot-occupied');
  assert.match(f.json.message, /Codex/);
  assert.doesNotMatch(f.json.message, /\/home\//);
  const un = codexDeps({ acked: 'codex' });
  const u = await post(un.deps, '/api/usage/account', JSON.stringify({ provider: 'codex', account: 'nobody' }), JSONH);
  assert.deepEqual([u.status, u.json.reason, u.json.message], [409, 'unknown-account', 'No Codex account is called nobody.']);
  assert.ok(!un.calls.some((a) => a.includes('--shared')), 'nothing ran');
  const bad = await post(un.deps, '/api/usage/account', JSON.stringify({ provider: 'gemini', account: 'shaw' }), JSONH);
  assert.deepEqual([bad.status, bad.json], [400, { ok: false, error: 'unknown provider' }]);
});

test('POST codex: the terms gate is its own; a Claude ack does not open it; ack_terms records only the Codex one', async () => {
  const { deps, calls, dir, switched } = codexDeps({ acked: 'claude' });
  const r = await post(deps, '/api/usage/account', JSON.stringify({ provider: 'codex', account: 'shaw' }), JSONH);
  assert.deepEqual([r.status, r.json.reason, r.json.message, r.json.terms_text], [409, 'terms-not-acknowledged', CODEX_TERMS_LINE, CODEX_TERMS_LINE]);
  assert.ok(!calls.some((a) => a.includes('--shared')), 'nothing ran');
  const ok = await post(deps, '/api/usage/account', JSON.stringify({ provider: 'codex', account: 'shaw', ack_terms: true }), JSONH);
  assert.equal(ok.status, 200);
  assert.equal(JSON.parse(readFileSync(join(dir, 'state', 'account_terms_ack_codex.json'), 'utf8')).acknowledged, true);
  assert.equal(switched.length, 0, 'the Claude switch never ran');
  // and a Codex ack does not open Claude's gate
  const c = codexDeps({ acked: 'codex' });
  const cr = await post(c.deps, '/api/usage/account', JSON.stringify({ account: 'beta' }), JSONH);
  assert.deepEqual([cr.status, cr.json.reason, cr.json.message], [409, 'terms-not-acknowledged', TERMS_LINE]);
  const cp = await post(c.deps, '/api/usage/account', JSON.stringify({ provider: 'claude', account: 'beta', ack_terms: true }), JSONH);
  assert.equal(cp.status, 200);
  assert.deepEqual(c.switched.map((s) => s.account), ['beta'], 'provider "claude" is the Claude press');
  assert.deepEqual(cp.json.accounts, VIEW);
});
