import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkCompactAt, stepCompactAt, settingsFromJson, showsRotationSettings, saveSeatSettings, fetchSeatSettings } from './seatSettings.ts';

test('"Compact at" takes a whole per cent from 40 to 85 and says why anything else is refused', () => {
  assert.deepEqual(checkCompactAt('65'), { ok: true, value: 65 });
  assert.deepEqual(checkCompactAt(' 40 % '), { ok: true, value: 40 });
  assert.deepEqual(checkCompactAt(85), { ok: true, value: 85 });
  assert.equal((checkCompactAt('39') as { error: string }).error, 'Compact at must be from 40% to 85%.');
  assert.equal((checkCompactAt('86') as { error: string }).error, 'Compact at must be from 40% to 85%.');
  assert.equal((checkCompactAt('65.5') as { error: string }).error, 'Use a whole number from 40 to 85.');
  assert.equal((checkCompactAt('abc') as { error: string }).error, 'Use a whole number from 40 to 85.');
  assert.equal((checkCompactAt('') as { error: string }).error, 'Enter a number from 40 to 85.');
});

test('the stepper stays inside the range', () => {
  assert.equal(stepCompactAt(65, 1), 66);
  assert.equal(stepCompactAt(85, 1), 85);
  assert.equal(stepCompactAt(40, -1), 40);
});

test('the server\'s settings, with the built-in defaults for anything it leaves out (Auto-compact off)', () => {
  assert.deepEqual(settingsFromJson({ auto_compact: true, show_compaction: false, compact_at: 70, guard_pct: 90 }),
    { auto_compact: true, show_compaction: false, compact_at: 70, guard_pct: 90 });
  assert.deepEqual(settingsFromJson({ ok: true, settings: { compact_at: 50 } }), { auto_compact: false, show_compaction: true, compact_at: 50 });
  assert.deepEqual(settingsFromJson({ auto_compact: false }), { auto_compact: false, show_compaction: true, compact_at: 65 });
  assert.equal(settingsFromJson({ ok: true }), null);
  assert.equal(settingsFromJson({ ok: true, auto_compact: false, compact_now: false })?.compact_now, false);   // "Compact now" can't run here
});

test('Arturo shows the controls only when the server says he is compacted; every other agent unless it says not', () => {
  assert.equal(showsRotationSettings(true, null), false);
  assert.equal(showsRotationSettings(true, { auto_compact: false, show_compaction: true, compact_at: 65 }), false);
  assert.equal(showsRotationSettings(true, { auto_compact: false, show_compaction: true, compact_at: 65, rotates: true }), true);
  assert.equal(showsRotationSettings(false, { auto_compact: false, show_compaction: true, compact_at: 65 }), true);
  assert.equal(showsRotationSettings(false, { auto_compact: false, show_compaction: true, compact_at: 65, rotates: false }), false);
});

const answer = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

test('a refused save says the server\'s reason in words; a saved one gives the saved settings', async () => {
  await assert.rejects(saveSeatSettings('dev-demo', { compact_at: 90 }, answer(400, { ok: false, error: 'compact_at must be 40-85' })), /compact_at must be 40-85/);
  await assert.rejects(saveSeatSettings('dev-demo', { compact_at: 60 }, answer(500, {})), /Could not save \(500\)/);
  await assert.rejects(saveSeatSettings('dev-demo', { compact_at: 60 }, (async () => { throw new TypeError('Failed to fetch'); }) as unknown as typeof fetch),
    /Could not reach the server\. Nothing was changed\./);
  assert.deepEqual(await saveSeatSettings('dev-demo', { auto_compact: true }, answer(200, { ok: true, auto_compact: true, show_compaction: true, compact_at: 60 })),
    { auto_compact: true, show_compaction: true, compact_at: 60 });
  assert.equal(await saveSeatSettings('dev-demo', { compact_at: 60 }, answer(200, { ok: true })), null);
  let sent: { url: string; body: string } | null = null;
  await saveSeatSettings('dev-demo', { show_compaction: false }, (async (url: string, init: RequestInit) => {
    sent = { url, body: String(init.body) }; return new Response('{}', { status: 200 });
  }) as unknown as typeof fetch);
  assert.deepEqual(sent, { url: '/api/agents/dev-demo/settings', body: '{"show_compaction":false}' });
});

test('a failed read says so in words', async () => {
  await assert.rejects(fetchSeatSettings('dev-demo', answer(404, {})), /no settings here/);
  await assert.rejects(fetchSeatSettings('dev-demo', answer(200, { ok: true })), /shape/);
});
