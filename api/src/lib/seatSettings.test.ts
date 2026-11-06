/** A seat's rotation settings, the API's reader and writer (lib/seatSettings.ts): fallback, validation, history. */
import { testTmpDir } from './testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync, statSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { readSeatSettings, writeSeatSettings, cleanSettingsChange, seatHistory, seatRotates, seatCanCompactNow, readCompactGate, COMPACT_GATE_FILE, readInstallDefault, writeInstallDefault } from './seatSettings.js';

const box = () => { const d = testTmpDir('seatset-'); mkdirSync(join(d, 'state')); return d; };

test('built in: auto-compact off, notice shown, 65 %, guard 90', () => {
  assert.deepEqual(readSeatSettings(box(), 'qa-demo'), {
    guard_pct: 90, auto_compact: false, show_compaction: true, compact_at: 65,
    source: { auto_compact: 'builtin', show_compaction: 'builtin', compact_at: 'builtin' },
  });
});

test('fallback per field: the seat, then the fleet default, then built in; bad stored values ignored', () => {
  const d = box();
  writeFileSync(join(d, 'state', 'seat-settings-default.json'), JSON.stringify({ default: { compact_at: 70, show_compaction: false } }));
  let s = readSeatSettings(d, 'qa-demo');
  assert.deepEqual([s.compact_at, s.show_compaction, s.auto_compact], [70, false, false]);
  assert.deepEqual(s.source, { auto_compact: 'builtin', show_compaction: 'default', compact_at: 'default' });
  writeSeatSettings(d, 'qa-demo', { compact_at: 50 }, 'sample-user');
  s = readSeatSettings(d, 'qa-demo');
  assert.deepEqual([s.compact_at, s.source.compact_at, s.show_compaction, s.source.show_compaction], [50, 'seat', false, 'default']);
  assert.equal(readSeatSettings(d, 'dev-demo').compact_at, 70, 'per agent');
  writeFileSync(join(d, 'state', 'seat-settings.json'), JSON.stringify({ seats: { 'qa-demo': { compact_at: 99, auto_compact: 'yes' } } }));
  s = readSeatSettings(d, 'qa-demo');
  assert.deepEqual([s.compact_at, s.source.compact_at, s.auto_compact], [70, 'default', false]);
});

test('a change is checked: 40–85 whole numbers, booleans, known fields only', () => {
  for (const bad of [{ compact_at: 39 }, { compact_at: 86 }, { compact_at: 65.5 }, { compact_at: '65' }, { compact_at: true },
                     { show_compaction: 1 }, { auto_compact: 'on' }, { guard_pct: 95 }, {}, null, [1]]) {
    assert.ok('error' in cleanSettingsChange(bad), JSON.stringify(bad));
  }
  assert.deepEqual(cleanSettingsChange({ compact_at: 40, auto_compact: true }), { change: { compact_at: 40, auto_compact: true } });
  assert.deepEqual(cleanSettingsChange({ compact_at: 85 }), { change: { compact_at: 85 } });
});

test('history: one row per changed field with who and when; the file is 0600, written whole', () => {
  const d = box();
  const at = () => new Date('2026-10-31T07:00:00.123+00:00');
  writeSeatSettings(d, 'qa-demo', { compact_at: 60, auto_compact: true }, 'sample-user', at);
  writeSeatSettings(d, 'qa-demo', { compact_at: 60 }, 'sample-user', at);       // no change: no row
  const s = writeSeatSettings(d, 'qa-demo', { compact_at: 75 }, 'example-user', at);
  assert.deepEqual([s.compact_at, s.auto_compact], [75, true]);
  assert.deepEqual(seatHistory(d, 'qa-demo').map((r) => [r.field, r.old, r.new, r.by, r.at]), [
    ['compact_at', 65, 60, 'sample-user', '2026-10-31T07:00:00Z'], ['auto_compact', false, true, 'sample-user', '2026-10-31T07:00:00Z'],
    ['compact_at', 60, 75, 'example-user', '2026-10-31T07:00:00Z']]);
  const f = join(d, 'state', 'seat-settings.json');
  assert.equal(statSync(f).mode & 0o777, 0o600);
  assert.deepEqual(JSON.parse(readFileSync(f, 'utf-8')).seats, { 'qa-demo': { compact_at: 75, auto_compact: true } });
  assert.deepEqual(readdirSync(join(d, 'state')).filter((n) => n.endsWith('.tmp')), []);
});

test('which seats can use our rotation: registered, on a runtime with a built adapter, never Arturo', () => {
  const reg = { agents: { 'qa-demo': { runtime: 'claude' }, 'dev-demo': {}, 'pm-demo': { runtime: 'codex' }, 'sample-g': { runtime: 'gemini' }, arturo: { runtime: 'claude' } } };
  assert.equal(seatRotates('qa-demo', reg), true);
  assert.equal(seatRotates('dev-demo', reg), true, 'a row with no runtime predates the field: claude');
  assert.equal(seatRotates('pm-demo', reg), false);
  assert.equal(seatRotates('sample-g', reg), false);
  assert.equal(seatRotates('arturo', reg), false);
  assert.equal(seatRotates('nobody', reg), false);
  assert.equal(seatRotates('qa-demo', null), false);
});

test('"Compact now" is offered only where a person\'s /compact can run: a headless test seat (the gateway\'s gate)', () => {
  const reg = { agents: { 'qa-demo': { test: true, tmux_session: 'qa-demo' }, 'dev-demo': {}, 'pm-demo': { test: true } } };
  const headless = new Set(['qa-demo', 'dev-demo']);
  assert.equal(seatCanCompactNow('qa-demo', reg, headless), true);
  assert.equal(seatCanCompactNow('dev-demo', reg, headless), false, 'not a test seat: compacting is not turned on for it yet');
  assert.equal(seatCanCompactNow('pm-demo', reg, headless), false, 'a test seat that does not run headless');
  assert.equal(seatCanCompactNow('qa-demo', null, headless), false);
  assert.equal(seatCanCompactNow('qa-demo', reg, headless, (id) => id === 'qa-demo'), false, 'in Dev the gateway refuses it');
});

// The case table the gateway runs too (contract/compact-gate.json), read through the same path the route uses.
const GATE = JSON.parse(readFileSync(COMPACT_GATE_FILE, 'utf-8'));

test('the compact gate file is the contract one, and its flags are today\'s rule', () => {
  assert.ok(COMPACT_GATE_FILE.endsWith(join('contract', 'compact-gate.json')));
  assert.deepEqual(readCompactGate(), { needs_headless: true, needs_test_seat: true });
  assert.ok(GATE.cases.length >= 10);
});

for (const c of GATE.cases as { name: string; seat: string; headless: boolean; dev: boolean; registry: Record<string, unknown> | null; expected: boolean }[]) {
  test(`compact gate case: ${c.name}`, () => {
    const reg = { agents: c.registry ? { [c.seat]: c.registry } : {} };
    const headless = new Set(c.headless ? [c.seat] : []);
    assert.equal(seatCanCompactNow(c.seat, reg, headless, (id) => c.dev && id === c.seat), c.expected);
  });
}

test('an unreadable or malformed gate file refuses every seat', () => {
  const d = testTmpDir('compact-gate-');
  const f = join(d, 'compact-gate.json');
  assert.equal(readCompactGate(f), null, 'missing');
  writeFileSync(f, 'not json');
  assert.equal(readCompactGate(f), null, 'not json');
  writeFileSync(f, JSON.stringify({ needs_headless: 'yes', needs_test_seat: true }));
  assert.equal(readCompactGate(f), null, 'a flag that is not a boolean');
  const reg = { agents: { 'qa-demo': { test: true } } };
  assert.equal(seatCanCompactNow('qa-demo', reg, new Set(['qa-demo']), () => false, readCompactGate(f)), false);
});

test('the flags are read, not assumed: turning one off widens the gate', () => {
  const reg = { agents: { 'dev-demo': { test: false } } };
  const headless = new Set(['dev-demo']);
  assert.equal(seatCanCompactNow('dev-demo', reg, headless, () => false, { needs_headless: true, needs_test_seat: false }), true);
  assert.equal(seatCanCompactNow('dev-demo', reg, new Set(), () => false, { needs_headless: false, needs_test_seat: false }), true);
  assert.equal(seatCanCompactNow('dev-demo', reg, headless, () => true, { needs_headless: true, needs_test_seat: false }), false, 'Dev still is not headless');
});

test('the install default is written to seat-settings-default.json and every seat without its own value follows it', () => {
  const d = testTmpDir('install-default-');
  assert.deepEqual(readInstallDefault(d), {});
  writeInstallDefault(d, { compact_at: 50 });
  writeInstallDefault(d, { auto_compact: true });
  assert.deepEqual(readInstallDefault(d), { compact_at: 50, auto_compact: true });
  const s = readSeatSettings(d, 'any-seat');
  assert.equal(s.compact_at, 50); assert.equal(s.source.compact_at, 'default');
  assert.equal(s.auto_compact, true);
  assert.throws(() => writeInstallDefault(d, { compact_at: 99 }));
});

test('changing the install default keeps keys under "default" it does not know (a newer field, a hand-made note)', () => {
  const d = testTmpDir('install-default-keep-');
  mkdirSync(join(d, 'state'), { recursive: true });
  const f = join(d, 'state', 'seat-settings-default.json');
  writeFileSync(f, JSON.stringify({ other: 1, default: { future_field: 'x', compact_at: 60 } }));
  writeInstallDefault(d, { auto_compact: false });
  assert.deepEqual(JSON.parse(readFileSync(f, 'utf-8')), { other: 1, default: { future_field: 'x', compact_at: 60, auto_compact: false } });
});
