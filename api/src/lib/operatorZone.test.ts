/**
 * The operator's current zone on GET /api/me (`zone`): <data dir>/state/shaw-location.json, read on every request.
 *
 * Run: npx tsx --test src/lib/operatorZone.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from './testTmp.js';
import { operatorZone } from './operatorZone.js';

const dir = testTmpDir('operator-zone-');
const file = join(dir, 'state', 'shaw-location.json');
mkdirSync(join(dir, 'state'), { recursive: true });

test('the file names the zone: tz and its label', () => {
  writeFileSync(file, JSON.stringify({ tz: 'America/Cancun', label: 'Tulum', location: 'Tulum, Mexico' }));
  assert.deepEqual(operatorZone(dir), { tz: 'America/Cancun', label: 'Tulum' });
});

test('read on every call: the file changes when the operator travels', () => {
  writeFileSync(file, JSON.stringify({ tz: 'America/Cancun', label: 'Tulum' }));
  assert.equal(operatorZone(dir)?.label, 'Tulum');
  writeFileSync(file, JSON.stringify({ tz: 'America/New_York', label: 'ET' }));
  assert.deepEqual(operatorZone(dir), { tz: 'America/New_York', label: 'ET' });
});

test('no label: the tz is the label; no file, no tz, or a tz the runtime does not know: null (the client keeps its install zone)', () => {
  writeFileSync(file, JSON.stringify({ tz: 'Europe/Lisbon' }));
  assert.deepEqual(operatorZone(dir), { tz: 'Europe/Lisbon', label: 'Europe/Lisbon' });
  writeFileSync(file, JSON.stringify({ tz: 'Mars/Olympus', label: 'Mars' }));
  assert.equal(operatorZone(dir), null);
  writeFileSync(file, JSON.stringify({ label: 'nowhere' }));
  assert.equal(operatorZone(dir), null);
  writeFileSync(file, '{not json');
  assert.equal(operatorZone(dir), null);
  rmSync(file);
  assert.equal(operatorZone(dir), null);
});

test('only a canonical IANA zone (or UTC) is served: an alias this runtime accepts but a browser may not, or maps elsewhere, is null', () => {
  // The runtime's Intl constructor takes EST (and resolves it to America/Panama), EST5EDT, SystemV/EST5, +05:00: none
  // is a browser's zone, and the label would sit on the wrong hour. The file names a canonical zone or nothing.
  for (const tz of ['EST', 'EST5EDT', 'SystemV/EST5', '+05:00', 'utc', 'america/cancun']) {
    writeFileSync(file, JSON.stringify({ tz, label: 'x' }));
    assert.equal(operatorZone(dir), null, tz);
  }
  for (const tz of ['UTC', 'America/Cancun', 'Asia/Tokyo']) {
    writeFileSync(file, JSON.stringify({ tz, label: 'x' }));
    const z = operatorZone(dir);
    assert.ok(z && (z.tz === 'UTC' || Intl.supportedValuesOf('timeZone').includes(z.tz)), tz);
    assert.equal(new Intl.DateTimeFormat('en-US', { timeZone: z!.tz }).resolvedOptions().timeZone, z!.tz, tz);
  }
});
