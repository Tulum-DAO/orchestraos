/**
 * Pure-logic tests for the New Agent client helpers.
 *   node --experimental-strip-types dashboard/src/lib/newAgent.test.mjs
 */
import assert from 'node:assert';
import { authedRuntimes, runtimeLabel, previewName, nameError, freshRuntimes, createAgent, openLoginShell } from './newAgent.ts';

const row = (id, cli, authed, installed = true) => ({ id, cli, authed, installed });

// --- who counts as "signed in" ---------------------------------------------------------
assert.deepEqual(authedRuntimes([row('claude', 'claude', true), row('codex', 'codex', false), row('gemini', 'agy', 'unverified')]).map(r => r.id), ['claude']);
assert.deepEqual(authedRuntimes([row('claude', 'claude', true, false)]), []);      // installed=false never counts
assert.deepEqual(authedRuntimes([]), []);
assert.equal(runtimeLabel(row('codex', 'codex', true)), 'Codex');
assert.equal(runtimeLabel({ id: 'claude', cli: 'claude', label: 'Claude', installed: true, authed: true }), 'Claude');

// --- the name the seat will get --------------------------------------------------------
assert.equal(previewName('  Docs Writer '), 'docs-writer');
assert.equal(previewName('Release_Notes 2'), 'release-notes-2');
assert.equal(previewName('!!!'), '');
assert.equal(nameError('', new Set()), null);                       // nothing typed yet
assert.equal(nameError('!!!', new Set()), 'Use letters or numbers — that name has none.');
// DISCLOSED test change (Shaw, 2026-09-22): `gm` is no longer reserved — docs/INSTALL.md tells a
// new operator to create it and the CLI allows it. The real sentinels still are.
assert.equal(nameError('gm', new Set()), null);
assert.equal(nameError('arturo', new Set()), '"arturo" is reserved.');
assert.equal(nameError('system', new Set()), '"system" is reserved.');
assert.equal(nameError('gm', new Set(['gm'])), '"gm" already exists.');   // taken still wins
assert.equal(nameError('Docs Writer', new Set(['docs-writer'])), '"docs-writer" already exists.');
assert.equal(nameError('Docs Writer', new Set()), null);

// --- freshRuntimes prefers the refresh probe, falls back to the cached read -------------
{
  const seen = [];
  globalThis.fetch = async (u, o) => { seen.push(`${o?.method || 'GET'} ${u}`); return { ok: true, json: async () => ({ providers: [row('claude', 'claude', true)] }) }; };
  const rows = await freshRuntimes();
  assert.deepEqual(seen, ['POST /api/runtimes/available/refresh']);
  assert.equal(rows.length, 1);
}
{
  const seen = [];
  globalThis.fetch = async (u, o) => {
    seen.push(`${o?.method || 'GET'} ${u}`);
    if (u.endsWith('/refresh')) return { ok: false, json: async () => ({}) };
    return { ok: true, json: async () => ({ providers: [row('codex', 'codex', true)] }) };
  };
  const rows = await freshRuntimes();
  assert.deepEqual(seen, ['POST /api/runtimes/available/refresh', 'GET /api/runtimes/available']);
  assert.equal(rows[0].id, 'codex');
}
{
  globalThis.fetch = async () => { throw new Error('down'); };
  assert.deepEqual(await freshRuntimes(), []);          // a dead API is [] = "not signed in", never a crash
}

// --- createAgent / openLoginShell carry the status through ------------------------------
{
  globalThis.fetch = async (u, o) => {
    assert.equal(u, '/api/agents/new');
    assert.deepEqual(JSON.parse(o.body), { name: 'docs writer', task: 'write it', runtime: 'claude' });
    return { ok: false, status: 409, json: async () => ({ ok: false, reason: 'no_authed_runtime', runtimes: [] }) };
  };
  const r = await createAgent('docs writer', 'write it', 'claude');
  assert.equal(r.status, 409);
  assert.equal(r.reason, 'no_authed_runtime');
}
{
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ ok: true, session: 'login-claude', cli: 'claude', hint: 'run claude' }) });
  const r = await openLoginShell();
  assert.equal(r.ok, true); assert.equal(r.session, 'login-claude');
}
{
  globalThis.fetch = async () => { throw new Error('boom'); };
  const r = await createAgent('x', '');
  assert.equal(r.ok, false); assert.equal(r.reason, 'network');
}

console.log('newAgent.test.mjs: all assertions passed');
