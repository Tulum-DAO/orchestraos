/**
 * RED-first for the "New Agent" button's server half:
 *   POST /api/agents/new          name -> a real registered seat (spawn-agent.sh)
 *   POST /api/agents/login-shell  no authed CLI -> a bash tmux session that says how to log in
 *
 * Pure over injected deps (probe / spawn / tmux), so nothing here touches a real
 * CLI, tmux, or the registry.
 *
 * Run: npx tsx --test src/routes/agents-new.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { createAgentsNewRouter, normalizeAgentName, pickRuntime, loginHint, composeTask, type NewAgentDeps } from './agents-new.js';

function authedRow(id: string, cli: string, authed: boolean | 'unverified' = true) {
  // logo_svg/models are part of ProviderResult; the login paths ignore them, but loginHint
  // takes RuntimeRow[] so the fixture has to be a real row rather than a near-miss.
  return { id, label: id, cli, installed: true, authed, auth_reason: undefined, logo_svg: '', models: [] };
}

function makeDeps(over: Partial<NewAgentDeps> & { rows?: any[] } = {}): NewAgentDeps & { calls: any } {
  const calls: any = { spawn: [], loginShell: [], probes: 0 };
  const deps: any = {
    probeRuntimes: () => { calls.probes += 1; return over.rows ?? [authedRow('claude', 'claude')]; },
    existingNames: () => new Set<string>(['gm']),
    spawn: async (args: any) => { calls.spawn.push(args); return { ok: true, output: 'spawned' }; },
    sessionExists: () => true,
    startLoginShell: async (args: any) => { calls.loginShell.push(args); return { ok: true, output: '' }; },
    ...over,
  };
  delete deps.rows;
  deps.calls = calls;
  return deps;
}

async function post(deps: NewAgentDeps, path: string, body: unknown) {
  const app = express();
  app.use(express.json());
  app.use('/api/agents', createAgentsNewRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  const res = await new Promise<{ status: number; json: any }>((resolve, reject) => {
    const payload = JSON.stringify(body ?? {});
    const req = http.request(
      { host: '127.0.0.1', port, path, method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } },
      (r) => { let d = ''; r.on('data', (c) => (d += c)); r.on('end', () => resolve({ status: r.statusCode!, json: d ? JSON.parse(d) : null })); },
    );
    req.on('error', reject);
    req.end(payload);
  });
  server.close();
  return res;
}

// ---- name normalization ---------------------------------------------------------------

test('normalizeAgentName slugifies what a person types', () => {
  assert.equal(normalizeAgentName('  Docs Writer  ').name, 'docs-writer');
  assert.equal(normalizeAgentName('Release_Notes 2').name, 'release-notes-2');
  assert.equal(normalizeAgentName('résumé bot').name, 'r-sum-bot');
});

test('normalizeAgentName refuses empty, too-long and reserved names', () => {
  assert.equal(normalizeAgentName('   ').error, 'name_required');
  assert.equal(normalizeAgentName('!!!').error, 'name_required');
  assert.equal(normalizeAgentName('a'.repeat(65)).error, 'name_too_long');
  assert.equal(normalizeAgentName('all').error, 'name_reserved');
});

// ---- runtime choice -------------------------------------------------------------------

test('pickRuntime takes the first authed row, ignoring unverified and not-installed', () => {
  const rows = [
    { id: 'claude', cli: 'claude', installed: true, authed: false },
    { id: 'gemini', cli: 'agy', installed: true, authed: 'unverified' },
    { id: 'codex', cli: 'codex', installed: true, authed: true },
  ];
  assert.deepEqual(pickRuntime(rows as any, undefined), { id: 'codex', cli: 'codex' });
});

test('pickRuntime honours an explicit request when that runtime is authed', () => {
  const rows = [authedRow('claude', 'claude'), authedRow('codex', 'codex')];
  assert.deepEqual(pickRuntime(rows as any, 'codex'), { id: 'codex', cli: 'codex' });
});

test('pickRuntime refuses an explicit runtime that is not authed', () => {
  const rows = [authedRow('claude', 'claude'), { id: 'codex', cli: 'codex', installed: true, authed: false }];
  assert.equal(pickRuntime(rows as any, 'codex'), null);
});

test('pickRuntime returns null when nothing is authed', () => {
  assert.equal(pickRuntime([{ id: 'claude', cli: 'claude', installed: false, authed: false }] as any, undefined), null);
});

// ---- POST /new ------------------------------------------------------------------------

test('POST /new spawns a registered seat on the authed runtime', async () => {
  const deps = makeDeps();
  const r = await post(deps, '/api/agents/new', { name: 'Docs Writer', task: 'write the README' });
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
  assert.equal(r.json.id, 'docs-writer');
  assert.equal(r.json.session, 'docs-writer');
  assert.equal(r.json.runtime, 'claude');
  // gm:false is new on this object (the General Manager path); every other field is unchanged.
  assert.deepEqual((deps as any).calls.spawn[0], { name: 'docs-writer', task: 'write the README', runtime: 'claude', gm: false });
});

test('POST /new works with no task', async () => {
  const deps = makeDeps();
  const r = await post(deps, '/api/agents/new', { name: 'quiet-seat' });
  assert.equal(r.json.ok, true);
  assert.equal((deps as any).calls.spawn[0].task, '');
});

test('composeTask folds a role into the first task (no role column in the registry)', () => {
  assert.equal(composeTask('docs writer', 'write the README'), 'Your role: docs writer.\n\nwrite the README');
  assert.equal(composeTask('docs writer', ''), 'Your role: docs writer.');
  assert.equal(composeTask('', 'write the README'), 'write the README');
  assert.equal(composeTask('  ', '  '), '');
});

test('POST /new passes the role as the first line of the spawn task', async () => {
  const deps = makeDeps();
  const r = await post(deps, '/api/agents/new', { name: 'docs-writer', role: 'docs writer', task: 'write the README' });
  assert.equal(r.json.ok, true);
  assert.equal((deps as any).calls.spawn[0].task, 'Your role: docs writer.\n\nwrite the README');
});

test('gm is NOT reserved — docs/INSTALL.md tells a new operator to create it', () => {
  assert.equal(normalizeAgentName('gm').error, undefined);
  assert.equal(normalizeAgentName('gm').name, 'gm');
  // the real sentinels stay reserved
  for (const n of ['all', 'none', 'new', 'arturo', 'self', 'system']) {
    assert.equal(normalizeAgentName(n).error, 'name_reserved', n);
  }
});

test('POST /new with gm:true spawns the General Manager, not an ordinary worker', async () => {
  // a FRESH install has no gm — the state Shaw hit: the name was refused and no gm existed
  const deps = makeDeps({ existingNames: () => new Set<string>() });
  const r = await post(deps, '/api/agents/new', { name: 'gm', gm: true, task: 'run the fleet' });
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
  assert.equal(r.json.gm, true);
  // T0 + always-on + prompts/gm.md are what `orchestra spawn --gm` sets; the route must take that
  // path, because spawn-agent.sh cannot set any of them.
  assert.deepEqual((deps as any).calls.spawn[0], { name: 'gm', task: 'run the fleet', runtime: 'claude', gm: true });
});

test('POST /new without gm keeps the ordinary worker path', async () => {
  const deps = makeDeps();
  await post(deps, '/api/agents/new', { name: 'worker-1' });
  assert.equal((deps as any).calls.spawn[0].gm, false);
});

test('POST /new refuses a duplicate name with 409', async () => {
  const deps = makeDeps({ existingNames: () => new Set(['docs-writer']) });
  const r = await post(deps, '/api/agents/new', { name: 'docs-writer' });
  assert.equal(r.status, 409);
  assert.equal(r.json.reason, 'name_taken');
  assert.equal((deps as any).calls.spawn.length, 0);
});

test('POST /new refuses a bad name with 400 and never spawns', async () => {
  const deps = makeDeps();
  const r = await post(deps, '/api/agents/new', { name: '   ' });
  assert.equal(r.status, 400);
  assert.equal(r.json.reason, 'name_required');
  assert.equal((deps as any).calls.spawn.length, 0);
});

test('POST /new with no authed CLI answers 409 no_authed_runtime (the login-shell path)', async () => {
  const deps = makeDeps({ rows: [{ id: 'claude', cli: 'claude', installed: true, authed: false, auth_reason: 'loggedIn=false' }] });
  const r = await post(deps, '/api/agents/new', { name: 'docs-writer' });
  assert.equal(r.status, 409);
  assert.equal(r.json.reason, 'no_authed_runtime');
  assert.ok(Array.isArray(r.json.runtimes));
  assert.equal((deps as any).calls.spawn.length, 0);
});

test('POST /new surfaces a spawn failure as 502 with the script output', async () => {
  const deps = makeDeps({ spawn: async () => ({ ok: false, output: 'REFUSE: no runtime' }) });
  const r = await post(deps, '/api/agents/new', { name: 'docs-writer' });
  assert.equal(r.status, 502);
  assert.equal(r.json.ok, false);
  assert.match(r.json.detail, /REFUSE/);
});

test('POST /new fails loud when the script exits 0 but no session exists', async () => {
  const deps = makeDeps({ sessionExists: () => false });
  const r = await post(deps, '/api/agents/new', { name: 'docs-writer' });
  assert.equal(r.status, 502);
  assert.equal(r.json.reason, 'session_missing');
});

// ---- POST /login-shell ----------------------------------------------------------------

test('POST /login-shell starts a bash session and returns its name + the command to run', async () => {
  const deps = makeDeps({ rows: [{ id: 'claude', cli: 'claude', installed: true, authed: false }] });
  const r = await post(deps, '/api/agents/login-shell', {});
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
  assert.match(r.json.session, /^login-/);
  assert.equal(r.json.cli, 'claude');
  assert.match(r.json.hint, /claude/);
  const call = (deps as any).calls.loginShell[0];
  assert.equal(call.session, r.json.session);
  assert.match(call.greeting, /log in/i);
});

test('POST /login-shell reuses the same session name on a second tap', async () => {
  const deps = makeDeps({ rows: [{ id: 'claude', cli: 'claude', installed: true, authed: false }] });
  const a = await post(deps, '/api/agents/login-shell', {});
  const b = await post(deps, '/api/agents/login-shell', {});
  assert.equal(a.json.session, b.json.session);
});

test('POST /login-shell names the first NOT-installed cli when nothing is installed', async () => {
  const deps = makeDeps({ rows: [] });
  const r = await post(deps, '/api/agents/login-shell', {});
  assert.equal(r.json.ok, true);
  assert.match(r.json.hint, /install/i);
});

// --- G21: /login-shell must target the provider the operator CLICKED ------------------------
// The disconnected-provider modal (operator, 2026-09-18) opens a login for the tile that was
// tapped. Before this, loginHint() picked `installed[0]` — so tapping Gemini on a box where
// Claude was installed opened a CLAUDE login and silently connected the wrong provider.

test('loginHint targets the requested provider, not the first installed one', () => {
  const rows = [authedRow('claude', 'claude'), { ...authedRow('gemini', 'agy'), authed: false }];
  const picked = loginHint(rows, 'gemini');
  assert.equal(picked.cli, 'agy');
  assert.match(picked.greeting, /agy/);
});

test('loginHint falls back to the old behaviour when no provider is named', () => {
  const rows = [authedRow('claude', 'claude')];
  assert.equal(loginHint(rows).cli, 'claude');
});

test('loginHint on a provider that is NOT installed says install, and names that CLI', () => {
  const rows = [authedRow('claude', 'claude'), { ...authedRow('gemini', 'agy'), installed: false, authed: false }];
  const picked = loginHint(rows, 'gemini');
  assert.equal(picked.cli, 'agy');
  assert.match(picked.hint, /not installed/i);
});

test('POST /login-shell opens a session named for the REQUESTED provider', async () => {
  const deps = makeDeps({ rows: [authedRow('claude', 'claude'), { ...authedRow('gemini', 'agy'), authed: false }] });
  const res = await post(deps, '/api/agents/login-shell', { provider: 'gemini' });
  assert.equal(res.status, 200);
  assert.equal(res.json.cli, 'agy');
  assert.equal(res.json.session, 'login-agy');
  assert.equal(deps.calls.loginShell[0].session, 'login-agy');
});

test('POST /login-shell with no provider still behaves as it did before', async () => {
  const deps = makeDeps();
  const res = await post(deps, '/api/agents/login-shell', {});
  assert.equal(res.status, 200);
  assert.equal(res.json.session, 'login-claude');
});
