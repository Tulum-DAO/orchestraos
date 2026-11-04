import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseSettings, saveServerSetting, applyChange, formatChangedBy, whoName, timeZoneOptions, revertRow, mergeDefaultModel, type SettingRow } from './settingsApi.ts';

const answer = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;
const ROW: SettingRow = { key: 'general.timeZone', section: 'general', scope: 'everyone', value: 'UTC', editable: true, readOnly: false,
  source: 'store', changedBy: null, changedAt: null, note: null, detail: null };

test('GET is read into rows and a role; anything else is an error', () => {
  assert.deepEqual(parseSettings({ ok: true, role: 'member', rows: [ROW] }), { ok: true, role: 'member', rows: [ROW] });
  assert.deepEqual(parseSettings({ ok: true, rows: [ROW] }), { ok: true, role: 'member', rows: [ROW] }, 'no role from the server: member (Always member)');
  assert.deepEqual(parseSettings({ ok: true, role: 'admin', rows: [ROW] }), { ok: true, role: 'admin', rows: [ROW] });
  assert.equal(parseSettings({ ok: false, error: 'x' }).ok, false);
  assert.equal(parseSettings(null).ok, false);
});

test('a save returns the server\'s row, or its reason', async () => {
  assert.deepEqual(await saveServerSetting('general.timeZone', 'Asia/Tokyo', answer(200, { ok: true, row: { ...ROW, value: 'Asia/Tokyo' } })),
    { ok: true, row: { ...ROW, value: 'Asia/Tokyo' } });
  assert.deepEqual(await saveServerSetting('general.timeZone', 'x', answer(403, { ok: false, error: 'Only an admin can change this' })),
    { ok: false, error: 'Only an admin can change this' });
  assert.deepEqual(await saveServerSetting('k', 1, (async () => { throw new Error('offline'); }) as typeof fetch), { ok: false, error: 'offline' });
});

test('a change pushed from another dashboard updates the row with who and when', () => {
  assert.deepEqual(applyChange(ROW, { key: ROW.key, value: 'Asia/Tokyo', changedBy: 'mo@y.net', changedAt: '2026-11-03T10:00:00Z' }),
    { ...ROW, value: 'Asia/Tokyo', changedBy: 'mo@y.net', changedAt: '2026-11-03T10:00:00Z' });
});

test('"Changed by Sam · 2 min ago"', () => {
  const now = Date.parse('2026-11-03T10:02:30Z');
  assert.equal(whoName('sam@example.com'), 'Sam');
  assert.equal(formatChangedBy('sam@example.com', '2026-11-03T10:00:00Z', now), 'Changed by Sam · 2 min ago');
  assert.equal(formatChangedBy('operator', '2026-11-03T10:02:10Z', now), 'Changed by Operator · just now');
  assert.equal(formatChangedBy('mo@y.net', '2026-11-02T09:00:00Z', now), 'Changed by Mo · 1 d ago');
  assert.equal(formatChangedBy(null, null, now), null);
});

test('time zone choices: "" first, named for what it does (each viewer\'s own clock), the current value always present', () => {
  const o = timeZoneOptions('Etc/Unknown_Test');
  assert.deepEqual(o[0], { value: '', label: "This device's time zone (not set)" });
  assert.ok(o.some((x) => x.value === 'America/New_York'));
  assert.ok(o.some((x) => x.value === 'Etc/Unknown_Test'));
});

test('a refused save puts the old row back only while the row still holds what this save put there', () => {
  const before = { key: 'general.timeZone', section: 'general', scope: 'everyone' as const, value: 'America/New_York', editable: true, readOnly: false,
    source: 'store', changedBy: null, changedAt: null, note: null, detail: null };
  const mine = { ...before, value: 'Asia/Tokyo' };
  assert.deepEqual(revertRow(mine, 'Asia/Tokyo', before), before, 'nothing newer: the old value comes back');
  const pushed = applyChange(mine, { key: 'general.timeZone', value: 'Europe/Lisbon', changedBy: 'mo@y.net', changedAt: '2026-11-03T10:00:00Z' });
  assert.equal(revertRow(pushed, 'Asia/Tokyo', before), null, 'a change pushed meanwhile is kept, not overwritten');
  const map = { claude: 'x' };
  assert.deepEqual(revertRow({ ...before, value: map }, map, before), before, 'an object value: the same object is still there');
  assert.equal(revertRow({ ...before, value: { claude: 'x' } }, map, before), null, 'a new object came in meanwhile');
  assert.equal(revertRow(undefined, 'Asia/Tokyo', before), null);
});

test('picking a default model shows at once as the whole map with that runtime changed, never just the one sent', () => {
  const held = { claude: 'claude-opus-5-5[1m]', gemini: 'gemini-3.1-pro', codex: 'gpt-5.6-terra' };
  assert.deepEqual(mergeDefaultModel(held, 'codex', 'gpt-test-1'), { claude: 'claude-opus-5-5[1m]', gemini: 'gemini-3.1-pro', codex: 'gpt-test-1' });
  assert.equal(held.codex, 'gpt-5.6-terra', 'the held map is not changed in place');
  assert.deepEqual(mergeDefaultModel(null, 'claude', 'x'), { claude: 'x' });
  const a = readFileSync(new URL('../components/settings/sections/AgentsSection.tsx', import.meta.url), 'utf8');
  assert.match(a, /save\(\{ runtime: p\.providerId, model: p\.modelId \}, mergeDefaultModel\(models, p\.providerId, p\.modelId\)\)/);
});
