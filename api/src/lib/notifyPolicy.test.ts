/**
 * Settings › Notifications, the decision: every case in contract/notify-policy-cases.json (the push path runs the same
 * cases, scripts/test_notify_policy.py), then what a save may hold.
 * Run: npx tsx --test src/lib/notifyPolicy.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { decide, shouldAlert, levelFor, inQuietHours, validQuietHours, validOverrides, readOverrides, readQuietHours, MAX_AGENT_OVERRIDES } from './notifyPolicy.js';

const CASES = JSON.parse(readFileSync(new URL('../../../contract/notify-policy-cases.json', import.meta.url), 'utf-8')).cases as {
  name: string; prefs: any; event: string; agent: string | null; now: string; tz: string; expected: boolean; reason: string;
}[];

test('the shared cases: at least 100, every one decided the same as the push path', () => {
  assert.ok(CASES.length >= 100);
  for (const c of CASES) {
    assert.equal(decide(c.prefs, c.event, c.agent, new Date(c.now), c.tz), c.reason, c.name);
    assert.equal(shouldAlert(c.prefs, c.event, c.agent, new Date(c.now), c.tz), c.expected, c.name);
  }
  assert.deepEqual(new Set(CASES.map((c) => c.reason)), new Set(['ok', 'quiet', 'suppressed']));
  for (const n of ['rotated id: dev-3 Nothing applies to dev-3-gen4', 'rotated id: global Nothing + dev-3 All alerts for dev-3-gen4'])
    assert.ok(CASES.some((c) => c.name === n), n);
});

test('junk prefs read as the defaults (All, no quiet hours)', () => {
  const now = new Date('2026-11-03T12:00:00Z');
  for (const junk of [null, undefined, [], 'all', 3] as any[]) assert.equal(shouldAlert(junk, 'finished', 'a', now, 'UTC'), true);
  assert.equal(levelFor(undefined, 'a'), 'all');
  assert.equal(inQuietHours(['22:00', '07:00'], now, 'UTC'), false);
});

test('quiet hours: what a save may hold', () => {
  assert.equal(validQuietHours(null), null);
  assert.equal(validQuietHours({ from: '22:00', to: '07:00' }), null);
  assert.equal(validQuietHours({ from: '00:00', to: '23:59' }), null);
  for (const bad of [{ from: '22:00' }, { from: '24:00', to: '07:00' }, { from: '9:00', to: '17:00' }, { from: 22, to: 7 }, '22:00-07:00',
    [], { from: '22:00', to: '07:00', except: ['gm'] }, undefined]) assert.ok(validQuietHours(bad), JSON.stringify(bad));
  assert.match(String(validQuietHours({ from: '08:00', to: '08:00' })), /must differ/);
  assert.deepEqual(readQuietHours({ from: '22:00', to: '07:00', x: 1 }), null);
  assert.deepEqual(readQuietHours({ from: '22:00', to: '07:00' }), { from: '22:00', to: '07:00' });
  assert.equal(readQuietHours(null), null);
  assert.equal(readQuietHours(undefined), null);
});

test('per-agent overrides: what a save may hold, and reading back drops anything malformed', () => {
  assert.equal(validOverrides({}), null);
  assert.equal(validOverrides({ 'dev-client': 'nothing', gm: 'all', 'qa.sample_m1': 'needs_me' }), null);
  for (const bad of [null, [], 'gm', { 'dev a': 'all' }, { '../x': 'all' }, { gm: 'loud' }, { gm: null }, { '': 'all' }])
    assert.ok(validOverrides(bad), JSON.stringify(bad));
  const many = Object.fromEntries(Array.from({ length: MAX_AGENT_OVERRIDES + 1 }, (_, i) => [`a${i}`, 'all']));
  assert.match(String(validOverrides(many)), /At most/);
  assert.deepEqual(readOverrides({ gm: 'all', 'bad id': 'all', x: 'loud' }), { gm: 'all' });
  assert.deepEqual(readOverrides(['gm']), {});
});
