/**
 * GET/PUT /api/settings against the real registry on a temp data dir and config: roles (403 for a member on an
 * Everyone row), validation, the change log, and the push of Everyone changes.
 * Run: npx tsx --test src/routes/settings.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { createSettingsRouter } from './settings.js';
import { SETTINGS, type GatewayClient } from '../lib/settingsRegistry.js';

const d = testTmpDir('settings-route-');
mkdirSync(join(d, 'state'));
const cfg = join(d, 'orchestra.toml');
writeFileSync(cfg, '[operator]\nid = "operator"\ntimezone = "America/New_York"\n');
let role: 'admin' | 'member' = 'admin';
const published: any[] = [];
const gateway: GatewayClient = {
  get: async () => ({ status: 502, body: { ok: false, error: 'down' } }),
  put: async () => ({ status: 502, body: { ok: false, error: 'the voice service is down' } }),
};
const app = express();
app.use(express.json());
app.use('/api/settings', createSettingsRouter({
  ctx: () => ({ dataDir: d, configPath: cfg, user: 'operator', gateway }),
  role: () => role,
  who: () => 'sam@example.com',
  publish: (c) => { published.push(c); },
  now: () => new Date('2026-11-03T10:00:00.000Z'),
}));
const server = app.listen(0);
after(() => server.close());
const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/settings`;
const get = async () => (await fetch(base)).json() as Promise<any>;
const put = async (key: string, value: unknown) => {
  const r = await fetch(`${base}/${key}`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ value }) });
  return { status: r.status, body: await r.json() as any };
};
const logLines = () => existsSync(join(d, 'state', 'settings-log.jsonl'))
  ? readFileSync(join(d, 'state', 'settings-log.jsonl'), 'utf-8').trim().split('\n').map((l) => JSON.parse(l)) : [];

test('GET lists every registry row with its scope, value and whether this caller may edit it', async () => {
  const g = await get();
  assert.equal(g.ok, true);
  assert.equal(g.role, 'admin');
  assert.deepEqual(g.rows.map((r: any) => r.key), SETTINGS.map((e) => e.key));
  const tz = g.rows.find((r: any) => r.key === 'general.timeZone');
  assert.deepEqual([tz.scope, tz.value, tz.editable, tz.readOnly, tz.changedBy], ['everyone', 'America/New_York', true, false, null]);
  const vp = g.rows.find((r: any) => r.key === 'voice.provider');
  assert.match(vp.detail, /Couldn't reach the voice service/);
});

test('a member sees Everyone rows read-only and is refused with 403; their own rows still save', async () => {
  role = 'member';
  try {
    const tz = (await get()).rows.find((r: any) => r.key === 'general.timeZone');
    assert.equal(tz.editable, false);
    const r = await put('general.timeZone', 'Asia/Tokyo');
    assert.deepEqual([r.status, r.body.error], [403, 'Only an admin can change this']);
    assert.match(readFileSync(cfg, 'utf-8'), /America\/New_York/);
    assert.equal(published.length, 0);
    assert.equal((await put('general.defaultChatStyle', 'modern')).status, 200);
  } finally { role = 'admin'; }
});

test('an admin change is saved, logged with who and when, and pushed to other dashboards', async () => {
  const before = published.length;
  const r = await put('general.timeZone', 'Asia/Tokyo');
  assert.equal(r.status, 200);
  assert.deepEqual([r.body.row.value, r.body.row.changedBy, r.body.row.changedAt], ['Asia/Tokyo', 'sam@example.com', '2026-11-03T10:00:00.000Z']);
  assert.match(readFileSync(cfg, 'utf-8'), /timezone = "Asia\/Tokyo"/);
  assert.deepEqual(published.slice(before), [{ key: 'general.timeZone', value: 'Asia/Tokyo', changedBy: 'sam@example.com', changedAt: '2026-11-03T10:00:00.000Z' }]);
  assert.deepEqual(logLines().at(-1), { key: 'general.timeZone', scope: 'everyone', by: 'sam@example.com', at: '2026-11-03T10:00:00.000Z', value: 'Asia/Tokyo' });
  const n = published.length;
  await put('general.defaultChatStyle', 'bubbles');
  assert.equal(published.length, n, 'a You change is not pushed to everyone');
});

test('bad input is a 400 with the reason; unknown 404; read-only 405; a store failure 502 with its reason and no log', async () => {
  assert.deepEqual(await put('general.timeZone', 'Mars/Base'), { status: 400, body: { ok: false, error: 'Pick a time zone from the list' } });
  assert.equal((await put('nope.nothing', 1)).status, 404);
  assert.equal((await put('agents.safetyLimits', 1)).status, 405);
  const lines = logLines().length;
  const r = await put('voice.provider', 'openai');
  assert.deepEqual([r.status, r.body.error], [502, 'the voice service is down']);
  assert.equal(logLines().length, lines);
});

test('the pushed and logged value is what the store holds after the write, not the request body', async () => {
  const before = published.length;
  const r = await put('agents.defaultModel', { runtime: 'codex', model: 'gpt-test-1' });
  assert.equal(r.status, 200);
  const full = { claude: 'claude-opus-5-5[1m]', gemini: 'gemini-3.1-pro', codex: 'gpt-test-1', grok: 'grok-4.7' };
  assert.deepEqual(r.body.row.value, full);
  assert.deepEqual(published.slice(before).map((p) => [p.key, p.value]), [['agents.defaultModel', full]]);
  assert.deepEqual(logLines().at(-1)?.value, full);
});

test('a secret row is logged and pushed as {set, last4}, never the secret itself', async () => {
  let held = '';
  const secret = {
    key: 'voice.fakeApiKey', section: 'voice' as const, scope: 'everyone' as const, admin: true, secret: true,
    store: 'a test double', validate: (v: unknown) => (typeof v === 'string' ? null : 'a key'),
    // the worst case: a reader that hands back the raw secret. The route must still never log or push it.
    read: async () => ({ value: held, source: 'store' as const }),
    write: async (_c: unknown, v: unknown) => { held = v as string; },
  };
  const pushed: any[] = [];
  const a = express();
  a.use(express.json());
  a.use('/api/settings', createSettingsRouter({
    ctx: () => ({ dataDir: d, configPath: cfg, user: 'operator', gateway }),
    role: () => 'admin', who: () => 'sam@example.com', publish: (c) => { pushed.push(c); },
    now: () => new Date('2026-11-03T10:00:00.000Z'), entries: [secret],
  }));
  const s = a.listen(0);
  try {
    const url = `http://127.0.0.1:${(s.address() as AddressInfo).port}/api/settings/voice.fakeApiKey`;
    const r = await fetch(url, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ value: 'sk-very-secret-9876' }) });
    assert.equal(r.status, 200);
    assert.deepEqual(((await r.json()) as any).row.value, { set: true, last4: '9876' }, 'nor the browser');
    const raw = readFileSync(join(d, 'state', 'settings-log.jsonl'), 'utf-8');
    assert.ok(!raw.includes('sk-very-secret'), 'the secret never reaches the log');
    assert.deepEqual(logLines().at(-1)?.value, { set: true, last4: '9876' });
    assert.deepEqual(pushed.map((p) => p.value), [{ set: true, last4: '9876' }]);
    assert.ok(!JSON.stringify(pushed).includes('sk-very-secret'));
  } finally { s.close(); }
});

test('one row that fails or hangs does not fail the page: it comes back with no value and the reason', async () => {
  const base = { section: 'general' as const, scope: 'everyone' as const, admin: true, store: 'a test double', validate: () => null };
  const entries = [
    { ...base, key: 'general.ok', read: async () => ({ value: 'fine', source: 'store' as const }) },
    { ...base, key: 'general.broken', read: async () => { throw new Error('the file is unreadable'); } },
    { ...base, key: 'general.slow', read: () => new Promise<never>(() => {}) },
  ];
  const a = express();
  a.use('/api/settings', createSettingsRouter({
    ctx: () => ({ dataDir: d, configPath: cfg, user: 'operator', gateway }),
    role: () => 'admin', who: () => 'x', publish: () => {}, entries, readTimeoutMs: 50,
  }));
  const s = a.listen(0);
  try {
    const t0 = Date.now();
    const r = await fetch(`http://127.0.0.1:${(s.address() as AddressInfo).port}/api/settings`);
    assert.equal(r.status, 200);
    assert.ok(Date.now() - t0 < 2000, 'a hung read is cut off');
    const rows = ((await r.json()) as any).rows;
    assert.deepEqual(rows.map((x: any) => [x.key, x.value]), [['general.ok', 'fine'], ['general.broken', null], ['general.slow', null]]);
    assert.match(rows[1].detail, /Couldn't read this setting: the file is unreadable/);
    assert.match(rows[2].detail, /Couldn't read this setting in time/);
  } finally { s.close(); }
});

test('a slow or failing read-back after a good write answers with the value just saved, never a blank row', async () => {
  const base = { section: 'voice' as const, scope: 'everyone' as const, admin: true, store: 'a test double', validate: () => null };
  let writes = 0;
  const entries = [
    // the voice service: the write lands, the read right after it hangs past the timeout
    { ...base, key: 'voice.slow', read: () => (writes ? new Promise<never>(() => {}) : Promise.resolve({ value: 'marin', source: 'store' as const })),
      write: async () => { writes += 1; } },
    { ...base, key: 'voice.broken', read: async () => { throw new Error('the voice service is restarting'); }, write: async () => {} },
  ];
  const a = express();
  a.use(express.json());
  a.use('/api/settings', createSettingsRouter({
    ctx: () => ({ dataDir: d, configPath: cfg, user: 'operator', gateway }),
    role: () => 'admin', who: () => 'x', publish: () => {}, entries, readTimeoutMs: 50,
  }));
  const s = a.listen(0);
  try {
    for (const key of ['voice.slow', 'voice.broken']) {
      const r = await fetch(`http://127.0.0.1:${(s.address() as AddressInfo).port}/api/settings/${key}`, {
        method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ value: 'cedar' }) });
      assert.equal(r.status, 200);
      const row = ((await r.json()) as any).row;
      assert.equal(row.value, 'cedar', `${key}: the row holds what was saved`);
      assert.equal(row.detail, null, `${key}: no "couldn't read" on a row that just saved`);
    }
  } finally { s.close(); }
});

test('a change that saved still answers 200 when the change log cannot be written', async () => {
  const logPath = join(d, 'state', 'settings-log.jsonl');
  const kept = existsSync(logPath) ? readFileSync(logPath, 'utf-8') : '';
  rmSync(logPath, { force: true });
  mkdirSync(logPath);                       // a directory where the log should be: every append throws
  const errs: unknown[] = [];
  const orig = console.error;
  console.error = (...a: unknown[]) => { errs.push(a); };
  try {
    const before = published.length;
    const r = await put('general.timeZone', 'Europe/Lisbon');
    assert.equal(r.status, 200);
    assert.equal(r.body.row.value, 'Europe/Lisbon');
    assert.match(readFileSync(cfg, 'utf-8'), /timezone = "Europe\/Lisbon"/);
    assert.equal(published.length, before + 1, 'still pushed');
    assert.ok(errs.length >= 1, 'the log failure is reported');
  } finally {
    console.error = orig;
    rmSync(logPath, { recursive: true, force: true });
    writeFileSync(logPath, kept);
  }
});

test('Notifications rows are You: a member saves them, each goes to their own prefs.json, nothing is pushed to everyone', async () => {
  role = 'member';
  try {
    const before = published.length;
    const rows = (await get()).rows.filter((r: any) => r.section === 'notifications');
    assert.deepEqual(rows.map((r: any) => [r.key, r.scope, r.editable]),
      [['notifications.level', 'user', true], ['notifications.agentOverrides', 'user', true], ['notifications.quietHours', 'user', true]]);
    assert.deepEqual(rows.map((r: any) => r.value), ['all', {}, null], 'untouched: today\'s behaviour');
    assert.equal((await put('notifications.level', 'needs_me')).status, 200);
    assert.equal((await put('notifications.agentOverrides', { 'dev-a': 'nothing' })).status, 200);
    const q = await put('notifications.quietHours', { from: '22:00', to: '07:00' });
    assert.deepEqual([q.status, q.body.row.value], [200, { from: '22:00', to: '07:00' }]);
    const prefs = JSON.parse(readFileSync(join(d, 'state', 'users', 'operator', 'prefs.json'), 'utf-8'));
    assert.equal(prefs.notify_level, 'needs_me');
    assert.deepEqual(prefs.notify_agents, { 'dev-a': 'nothing' });
    assert.deepEqual(prefs.quiet_hours, { from: '22:00', to: '07:00' });
    assert.equal(published.length, before);
    assert.deepEqual(logLines().at(-1), { key: 'notifications.quietHours', scope: 'user', by: 'sam@example.com', at: '2026-11-03T10:00:00.000Z', value: { from: '22:00', to: '07:00' } });
    // bad input is refused with the reason and leaves the file alone
    assert.deepEqual((await put('notifications.level', 'loud')).body, { ok: false, error: 'Pick All, Needs me or Nothing' });
    assert.equal((await put('notifications.quietHours', { from: '22:00', to: '22:00' })).status, 400);
    assert.equal((await put('notifications.agentOverrides', { 'dev-a': 'loud' })).status, 400);
    // off again: the keys go
    assert.equal((await put('notifications.quietHours', null)).status, 200);
    assert.equal((await put('notifications.agentOverrides', {})).status, 200);
    const after = JSON.parse(readFileSync(join(d, 'state', 'users', 'operator', 'prefs.json'), 'utf-8'));
    assert.equal('quiet_hours' in after, false);
    assert.equal('notify_agents' in after, false);
    assert.equal(after.notify_level, 'needs_me');
  } finally { role = 'admin'; }
});
