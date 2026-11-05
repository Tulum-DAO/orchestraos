/**
 * /api/attention: the shared list (waiting, updates, answered), seen marks, the live-update stream
 * and the timeout rules, all forwarded to the gateway's /attention routes with the server bearer.
 * The API's own job is scope: a member names only the agents they may see (so the gateway filters
 * before limits and counts), the API filters again, seen marks carry a surface the server picks,
 * and the stream says only "refetch" (never the global pending count).
 * Pure over injected deps and an in-process express app.
 * Run: npx tsx --test src/routes/attention.test.ts
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { createAttentionRouter, type AttentionDeps } from './attention.js';

type Call = { url: string; init: RequestInit };

const ADMIN = { username: 'operator', role: 'admin', allowedAgents: '*', clientScope: null, trusted: false };
const MEMBER_GM = { username: 'member', role: 'member', allowedAgents: ['gm'], clientScope: null, trusted: true };

function item(id: string, agent: string | null, tab: string, extra: Record<string, unknown> = {}) {
  return { id, agent, tab, kind: tab === 'update' ? 'stuck' : 'decision', blocking: tab === 'waiting',
    seen_at: null, state: tab === 'answered' ? 'answered' : 'waiting', ...extra };
}

const LIST = {
  ok: true, person: 'op', rev: 7, generated_at: '2026-11-04T10:00:00+00:00',
  counts: { waiting: 2, waiting_blocking: 2, updates_unread: 2 },
  items: [
    item('apr_gm', 'gm', 'waiting'),
    item('apr_pm', 'pm', 'waiting'),
    item('evt_gm', 'gm', 'update'),
    item('evt_pm', 'pm', 'update'),
  ],
};

const RULES = [
  { id: 'rul_gm', trigger: 'no_progress', after_s: 1500, scope_agent: 'gm', actions: [{ type: 'mark_stuck' }] },
  { id: 'rul_pm', trigger: 'no_progress', after_s: 1500, scope_agent: 'pm', actions: [{ type: 'mark_stuck' }] },
  { id: 'rul_all', trigger: 'unanswered', after_s: 3600, scope_agent: null, actions: [{ type: 'remind' }] },
];

function makeDeps(over: Partial<AttentionDeps> = {}, respond?: (url: string, init: RequestInit) => { status: number; body: any }) {
  const calls: Call[] = [];
  const deps: any = {
    token: () => 'gw-token',
    gatewayUrl: () => 'http://gw.test',
    principal: () => ADMIN,
    role: () => 'admin',
    resolveTags: () => [],
    agentIds: () => ['gm', 'pm'],
    fetchJson: async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (respond) return respond(url, init);
      const u = new URL(url);
      if (u.pathname === '/attention' && (init.method || 'GET') === 'GET') {
        // the gateway honours agents= the way the plan says: every tab narrowed before counts
        const agents = u.searchParams.get('agents');
        if (!agents) return { status: 200, body: LIST };
        const keep = new Set(agents.split(','));
        return { status: 200, body: { ...LIST, items: LIST.items.filter((i) => keep.has(String(i.agent))) } };
      }
      if (u.pathname === '/attention/seen') return { status: 200, body: { ok: true, seen: 1, accepted_cards: [], skipped: [] } };
      if (u.pathname === '/attention/rules' && (init.method || 'GET') === 'GET') {
        return { status: 200, body: { ok: true, rules: RULES, min_after_s: { no_progress: 600 } } };
      }
      if (u.pathname.startsWith('/attention/rules')) return { status: 200, body: { ok: true, rule: { id: 'rul_new' } } };
      return { status: 404, body: { ok: false, error: 'not found' } };
    },
    pipeStream: async () => { throw new Error('no stream in this test'); },
    ...over,
  };
  deps.calls = calls;
  return deps as AttentionDeps & { calls: Call[] };
}

function app(deps: AttentionDeps) {
  const a = express();
  a.use(express.json());
  a.use('/api/attention', createAttentionRouter(deps));
  return a;
}

async function req(deps: AttentionDeps, method: string, path: string, body?: unknown) {
  const server = app(deps).listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const data = body === undefined ? undefined : JSON.stringify(body);
      const r = http.request({ host: '127.0.0.1', port, path, method,
        headers: data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {} }, (res) => {
        let out = '';
        res.on('data', (c) => (out += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, json: JSON.parse(out || '{}') }));
      });
      r.on('error', reject);
      r.setTimeout(5000, () => r.destroy(new Error(`no response in 5s: ${method} ${path}`)));  // a broken route fails, never hangs
      if (data) r.write(data);
      r.end();
    });
  } finally {
    server.close();
  }
}

const bodyOf = (c: Call) => JSON.parse(String(c.init.body));

// ---------------------------------------------------------------- GET /

test('admin gets the gateway list unchanged', async () => {
  const deps = makeDeps();
  const r = await req(deps, 'GET', '/api/attention?tab=waiting,update&limit_updates=20&since=2026-11-04T00:00:00Z&agents=pm&evil=1');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json, LIST);
  const u = new URL(deps.calls[0].url);
  assert.equal(u.pathname, '/attention');
  assert.equal(u.searchParams.get('tab'), 'waiting,update');
  assert.equal(u.searchParams.get('limit_updates'), '20');
  assert.equal(u.searchParams.get('since'), '2026-11-04T00:00:00Z');
  assert.equal(u.searchParams.get('agents'), null, 'an unscoped admin passes no agents, and a client one is dropped');
  assert.equal(u.searchParams.get('evil'), null, 'only allow-listed params are forwarded');
  assert.equal((deps.calls[0].init.headers as any).Authorization, 'Bearer gw-token');
});

test('a member sees only in-scope agents, the gateway call carried agents=, and counts are recomputed', async () => {
  // The gateway here ignores agents= and returns everything: the API's own filter must still hold.
  const deps = makeDeps({ principal: () => MEMBER_GM as any }, (url) => ({ status: 200, body: LIST }));
  const r = await req(deps, 'GET', '/api/attention?agents=pm');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.items.map((i: any) => i.id), ['apr_gm', 'evt_gm']);
  assert.deepEqual(r.json.counts, { waiting: 1, waiting_blocking: 1, updates_unread: 1 });
  assert.equal(new URL(deps.calls[0].url).searchParams.get('agents'), 'gm', 'the client agents= never widens the scope');
});

test('a member who may see no agent gets an empty list and the gateway is never asked', async () => {
  const deps = makeDeps({ principal: () => ({ ...MEMBER_GM, allowedAgents: [] }) as any });
  const r = await req(deps, 'GET', '/api/attention');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.items, []);
  assert.deepEqual(r.json.counts, { waiting: 0, waiting_blocking: 0, updates_unread: 0 });
  assert.equal(deps.calls.length, 0);
});

test('no principal is 401', async () => {
  const deps = makeDeps({ principal: () => null });
  for (const [m, p, b] of [['GET', '/api/attention'], ['POST', '/api/attention/seen', { ids: ['evt_gm'] }],
    ['GET', '/api/attention/stream'], ['GET', '/api/attention/rules'], ['POST', '/api/attention/rules', {}],
    ['PUT', '/api/attention/rules/rul_gm', {}], ['DELETE', '/api/attention/rules/rul_gm']] as const) {
    const r = await req(deps, m, p, b);
    assert.equal(r.status, 401, `${m} ${p}`);
  }
  assert.equal(deps.calls.length, 0);
});

test('gateway down is 502 with a plain error', async () => {
  const deps = makeDeps({ fetchJson: async () => { throw new Error('ECONNREFUSED'); } });
  const r = await req(deps, 'GET', '/api/attention');
  assert.equal(r.status, 502);
  assert.deepEqual(r.json, { ok: false, error: 'gateway unreachable' });
});

test('a gateway refusal passes through with its status and body', async () => {
  const deps = makeDeps({}, () => ({ status: 400, body: { ok: false, error: 'bad since' } }));
  const r = await req(deps, 'GET', '/api/attention?since=nope');
  assert.equal(r.status, 400);
  assert.deepEqual(r.json, { ok: false, error: 'bad since' });
});

// ---------------------------------------------------------------- POST /seen

test('seen forces surface web and drops ids out of scope', async () => {
  const deps = makeDeps({ principal: () => MEMBER_GM as any });
  const r = await req(deps, 'POST', '/api/attention/seen', { ids: ['evt_gm', 'evt_pm'], surface: 'watch' });
  assert.equal(r.status, 200);
  const post = deps.calls.find((c) => c.url.endsWith('/attention/seen'))!;
  assert.equal(post.init.method, 'POST');
  assert.deepEqual(bodyOf(post), { ids: ['evt_gm'], surface: 'web' });
});

test('seen as an admin forwards every id unchecked, still with surface web', async () => {
  const deps = makeDeps();
  await req(deps, 'POST', '/api/attention/seen', { ids: ['evt_gm', 'evt_pm', 'evt_gone'], surface: 'phone' });
  assert.equal(deps.calls.length, 1, 'no lookup for an unscoped admin');
  assert.deepEqual(bodyOf(deps.calls[0]), { ids: ['evt_gm', 'evt_pm', 'evt_gone'], surface: 'web' });
});

test('seen via chat sends surface chat', async () => {
  const deps = makeDeps();
  await req(deps, 'POST', '/api/attention/seen', { ids: ['evt_gm'], via: 'chat', surface: 'watch' });
  assert.deepEqual(bodyOf(deps.calls[0]), { ids: ['evt_gm'], surface: 'chat' });
});

test('seen with nothing left in scope answers without a gateway write', async () => {
  const deps = makeDeps({ principal: () => MEMBER_GM as any });
  const r = await req(deps, 'POST', '/api/attention/seen', { ids: ['evt_pm'] });
  assert.equal(r.status, 200);
  assert.equal(r.json.seen, 0);
  assert.ok(!deps.calls.some((c) => c.url.endsWith('/attention/seen')));
});

test('seen needs a list of string ids', async () => {
  const deps = makeDeps();
  const r = await req(deps, 'POST', '/api/attention/seen', { ids: 'evt_gm' });
  assert.equal(r.status, 400);
  assert.equal(deps.calls.length, 0);
});

// ---------------------------------------------------------------- GET /stream

test('stream pipes text/event-stream from GET /events, re-emits only changed/attention_rev, and cancels upstream on close', async () => {
  const enc = new TextEncoder();
  let cancelled = false;
  let seen: { url: string; headers: Record<string, string> } | null = null;
  const upstream = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(enc.encode('data: {"changed": true, "pending": 9, "attention_rev": 41, "ts": 1700000000}\n\n'));
      c.enqueue(enc.encode(': keepalive\n\n'));
      // split across chunks: the relay must buffer to the blank line
      c.enqueue(enc.encode('data: {"changed": true, "pend'));
      c.enqueue(enc.encode('ing": 3, "attention_rev": 42, "ts": 1700000002}\n\n'));
      // left open, like the real stream
    },
    cancel() { cancelled = true; },
  });
  const deps = makeDeps({
    pipeStream: async (url, headers) => {
      seen = { url, headers };
      return { status: 200, contentType: 'text/event-stream', body: upstream, json: async () => ({}) };
    },
  });
  const server = app(deps).listen(0);
  const port = (server.address() as any).port;
  try {
    const { contentType, text } = await new Promise<{ contentType: string; text: string }>((resolve, reject) => {
      const r = http.request({ host: '127.0.0.1', port, path: '/api/attention/stream', method: 'GET' }, (res) => {
        let out = '';
        res.on('data', (c) => {
          out += c;
          if ((out.match(/^data: /gm) || []).length >= 2) { resolve({ contentType: String(res.headers['content-type']), text: out }); r.destroy(); }
        });
      });
      r.on('error', (e: any) => { if (e.code !== 'ECONNRESET') reject(e); });
      r.setTimeout(5000, () => r.destroy(new Error('stream sent nothing in 5s')));
      r.end();
    });
    assert.match(contentType, /^text\/event-stream/);
    assert.equal(seen!.url, 'http://gw.test/events');
    assert.equal(seen!.headers.Authorization, 'Bearer gw-token');
    assert.ok(!/pending/.test(text), `the global pending count leaked: ${text}`);
    const datas = text.split('\n').filter((l) => l.startsWith('data: ')).map((l) => JSON.parse(l.slice(6)));
    assert.deepEqual(datas, [
      { changed: true, attention_rev: 41, ts: 1700000000 },
      { changed: true, attention_rev: 42, ts: 1700000002 },
    ]);
    assert.match(text, /^: keepalive$/m);
    for (let i = 0; i < 50 && !cancelled; i++) await new Promise((r) => setTimeout(r, 20));
    assert.ok(cancelled, 'the upstream read was not cancelled when the client went away');
  } finally {
    server.close();
  }
});

test('stream: a member hears "changed" only when their own list changed, never the global rev', async () => {
  const enc = new TextEncoder();
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const upstream = new ReadableStream<Uint8Array>({ start(c) { ctl = c; } });
  let gmItems: any[] = [{ id: 'apr_gm', agent: 'gm', tab: 'waiting' }];
  const lists: string[] = [];
  const deps = makeDeps({
    principal: () => MEMBER_GM as any,
    pipeStream: async () => ({ status: 200, contentType: 'text/event-stream', body: upstream, json: async () => ({}) }),
  }, (url) => {
    lists.push(new URL(url).searchParams.get('agents') || '');
    return { status: 200, body: { ok: true, items: gmItems, counts: {} } };
  });
  const server = app(deps).listen(0);
  const port = (server.address() as any).port;
  try {
    const text = await new Promise<string>((resolve, reject) => {
      const r = http.request({ host: '127.0.0.1', port, path: '/api/attention/stream', method: 'GET' }, (res) => {
        let out = '';
        res.on('data', (c) => {
          out += c;
          if ((out.match(/^data: /gm) || []).length >= 1 && /: done/.test(out)) { resolve(out); r.destroy(); }
        });
      });
      r.on('error', (e: any) => { if (e.code !== 'ECONNRESET') reject(e); });
      r.setTimeout(5000, () => r.destroy(new Error('stream sent nothing in 5s')));
      r.end();
      setTimeout(async () => {
        // a change elsewhere in the install: the member's list is the same, so nothing is said
        ctl.enqueue(enc.encode('data: {"changed": true, "attention_rev": 41, "ts": 1700000000}\n\n'));
        await new Promise((x) => setTimeout(x, 50));
        gmItems = [...gmItems, { id: 'evt_gm', agent: 'gm', tab: 'update' }];
        ctl.enqueue(enc.encode('data: {"changed": true, "attention_rev": 42, "ts": 1700000002}\n\n'));
        await new Promise((x) => setTimeout(x, 50));
        ctl.enqueue(enc.encode(': done\n\n'));
      }, 100);
    });
    const datas = text.split('\n').filter((l) => l.startsWith('data: ')).map((l) => JSON.parse(l.slice(6)));
    assert.equal(datas.length, 1, text);
    assert.equal(datas[0].changed, true);
    assert.equal(datas[0].attention_rev, null, 'the install-wide rev is never sent to a member');
    assert.ok(lists.every((a) => a === 'gm'), 'the member list is always fetched with their own agents');
  } finally {
    server.close();
  }
});

test('stream: a gateway refusal is plain JSON, and a gateway down is 502', async () => {
  const refused = makeDeps({ pipeStream: async () => ({ status: 403, contentType: 'application/json', body: null,
    json: async () => ({ ok: false, error: 'forbidden' }) }) });
  const r1 = await req(refused, 'GET', '/api/attention/stream');
  assert.equal(r1.status, 403);
  assert.deepEqual(r1.json, { ok: false, error: 'forbidden' });
  const down = makeDeps({ pipeStream: async () => { throw new Error('ECONNREFUSED'); } });
  const r2 = await req(down, 'GET', '/api/attention/stream');
  assert.equal(r2.status, 502);
  assert.deepEqual(r2.json, { ok: false, error: 'gateway unreachable' });
});

// ---------------------------------------------------------------- rules

test('rules: list/create/update/delete forward to /attention/rules; 422 passes through with its error', async () => {
  const deps = makeDeps();
  const list = await req(deps, 'GET', '/api/attention/rules');
  assert.equal(list.status, 200);
  assert.deepEqual(list.json.rules.map((r: any) => r.id), ['rul_gm', 'rul_pm', 'rul_all']);
  assert.deepEqual(list.json.min_after_s, { no_progress: 600 }, 'extra fields pass through');

  const rule = { trigger: 'no_progress', after_s: 900, scope_agent: 'gm', actions: [{ type: 'mark_stuck' }] };
  await req(deps, 'POST', '/api/attention/rules', rule);
  await req(deps, 'PUT', '/api/attention/rules/rul_gm', { after_s: 1200 });
  await req(deps, 'DELETE', '/api/attention/rules/rul_gm');
  const writes = deps.calls.filter((c) => (c.init.method || 'GET') !== 'GET');
  assert.deepEqual(writes.map((c) => [c.init.method, new URL(c.url).pathname]), [
    ['POST', '/attention/rules'], ['PUT', '/attention/rules/rul_gm'], ['DELETE', '/attention/rules/rul_gm']]);
  assert.deepEqual(bodyOf(writes[0]), rule);
  assert.deepEqual(bodyOf(writes[1]), { after_s: 1200 });
  for (const c of deps.calls) assert.equal((c.init.headers as any).Authorization, 'Bearer gw-token');

  const refusing = makeDeps({}, (url, init) => (init.method === 'POST'
    ? { status: 422, body: { error: 'action_not_available', detail: 'hand_off needs a manager' } }
    : { status: 404, body: { error: 'not_found' } }));
  const r422 = await req(refusing, 'POST', '/api/attention/rules', { ...rule, actions: [{ type: 'hand_off' }] });
  assert.equal(r422.status, 422);
  assert.deepEqual(r422.json, { error: 'action_not_available', detail: 'hand_off needs a manager' });
  const r404 = await req(refusing, 'DELETE', '/api/attention/rules/rul_nope');
  assert.equal(r404.status, 404);
  assert.deepEqual(r404.json, { error: 'not_found' });
});

test('rules: a member may edit only rules scoped to agents they can see; person-wide rules are admin-only', async () => {
  const deps = makeDeps({ principal: () => MEMBER_GM as any });
  const forbidden = { ok: false, error: 'forbidden' };

  const list = await req(deps, 'GET', '/api/attention/rules');
  assert.deepEqual(list.json.rules.map((r: any) => r.id), ['rul_gm', 'rul_all'], 'own agents plus person-wide, read-only');

  const base = { trigger: 'no_progress', after_s: 900, actions: [{ type: 'mark_stuck' }] };
  assert.equal((await req(deps, 'POST', '/api/attention/rules', { ...base, scope_agent: 'gm' })).status, 200);
  for (const scope of [{ scope_agent: 'pm' }, { scope_agent: null }, {}]) {
    const r = await req(deps, 'POST', '/api/attention/rules', { ...base, ...scope });
    assert.equal(r.status, 403, JSON.stringify(scope));
    assert.deepEqual(r.json, forbidden);
  }

  assert.equal((await req(deps, 'PUT', '/api/attention/rules/rul_gm', { after_s: 1200 })).status, 200);
  // moving an own rule onto another agent, or making it person-wide, is refused
  assert.equal((await req(deps, 'PUT', '/api/attention/rules/rul_gm', { scope_agent: 'pm' })).status, 403);
  assert.equal((await req(deps, 'PUT', '/api/attention/rules/rul_gm', { scope_agent: null })).status, 403);
  // the stored rule's scope counts too, whatever the body says
  assert.equal((await req(deps, 'PUT', '/api/attention/rules/rul_pm', { scope_agent: 'gm' })).status, 403);
  assert.equal((await req(deps, 'PUT', '/api/attention/rules/rul_all', { after_s: 60 })).status, 403);
  assert.equal((await req(deps, 'DELETE', '/api/attention/rules/rul_pm')).status, 403);
  assert.equal((await req(deps, 'DELETE', '/api/attention/rules/rul_all')).status, 403);
  assert.equal((await req(deps, 'DELETE', '/api/attention/rules/rul_gm')).status, 200);

  const writes = deps.calls.filter((c) => (c.init.method || 'GET') !== 'GET');
  assert.deepEqual(writes.map((c) => [c.init.method, new URL(c.url).pathname]), [
    ['POST', '/attention/rules'], ['PUT', '/attention/rules/rul_gm'], ['DELETE', '/attention/rules/rul_gm']],
  'nothing refused ever reached the gateway');

  // an unscoped admin may write a person-wide rule
  const admin = makeDeps();
  assert.equal((await req(admin, 'POST', '/api/attention/rules', { ...base, scope_agent: null })).status, 200);
  assert.equal((await req(admin, 'DELETE', '/api/attention/rules/rul_all')).status, 200);
});

test('rules: an unscoped principal that is not an install admin is a member for person-wide writes', async () => {
  // No authenticating proxy: every caller's principal is the unscoped operator, so only the install role decides.
  const deps = makeDeps({ role: () => 'member' });
  const base = { trigger: 'no_progress', after_s: 900, actions: [{ type: 'mark_stuck' }] };
  assert.equal((await req(deps, 'POST', '/api/attention/rules', { ...base, scope_agent: null })).status, 403);
  assert.equal((await req(deps, 'PUT', '/api/attention/rules/rul_all', { after_s: 60 })).status, 403);
  assert.equal((await req(deps, 'DELETE', '/api/attention/rules/rul_all')).status, 403);
  // a rule on an agent the caller can see is still theirs to write
  assert.equal((await req(deps, 'POST', '/api/attention/rules', { ...base, scope_agent: 'gm' })).status, 200);
  assert.equal((await req(deps, 'PUT', '/api/attention/rules/rul_gm', { after_s: 1200 })).status, 200);
  const writes = deps.calls.filter((c) => (c.init.method || 'GET') !== 'GET');
  assert.deepEqual(writes.map((c) => [c.init.method, new URL(c.url).pathname]), [
    ['POST', '/attention/rules'], ['PUT', '/attention/rules/rul_gm']]);
});
