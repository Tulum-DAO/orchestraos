/**
 * The operator's current zone on GET /api/me (`zone`): <data dir>/state/shaw-location.json, read on every request,
 * served to the install's admin only.
 *
 * Run: npx tsx --test src/lib/operatorZone.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from './testTmp.js';
import { operatorZone, operatorZoneFor } from './operatorZone.js';

const dir = testTmpDir('operator-zone-');
const file = join(dir, 'state', 'shaw-location.json');
mkdirSync(join(dir, 'state'), { recursive: true });
const put = (v: unknown) => writeFileSync(file, typeof v === 'string' ? v : JSON.stringify(v));

test('the file names the zone: tz and its label, other keys ignored', () => {
  put({ tz: 'America/Cancun', label: 'Local', since: '2026-01-01' });
  assert.deepEqual(operatorZone(dir), { tz: 'America/Cancun', label: 'Local' });
});

test('read on every call: the file changes when the operator travels', () => {
  put({ tz: 'America/Cancun', label: 'Local' });
  assert.equal(operatorZone(dir)?.label, 'Local');
  put({ tz: 'America/New_York', label: 'Away' });
  assert.deepEqual(operatorZone(dir), { tz: 'America/New_York', label: 'Away' });
});

test('no label, a blank or non-string label: the tz is the label; a padded tz is trimmed; a long label is cut', () => {
  put({ tz: 'Europe/Lisbon' });
  assert.deepEqual(operatorZone(dir), { tz: 'Europe/Lisbon', label: 'Europe/Lisbon' });
  put({ tz: 'Europe/Lisbon', label: '   ' });
  assert.deepEqual(operatorZone(dir), { tz: 'Europe/Lisbon', label: 'Europe/Lisbon' });
  put({ tz: 'Europe/Lisbon', label: 7 });
  assert.deepEqual(operatorZone(dir), { tz: 'Europe/Lisbon', label: 'Europe/Lisbon' });
  put({ tz: '  Europe/Lisbon ', label: 'x'.repeat(200) });
  assert.deepEqual(operatorZone(dir), { tz: 'Europe/Lisbon', label: 'x'.repeat(64) });
});

test('no file, no tz, an unknown tz, bad JSON, or JSON that is not an object: null (the client keeps its install zone)', () => {
  for (const v of [{ tz: 'Mars/Olympus', label: 'Mars' }, { label: 'nowhere' }, { tz: 7 }, '{not json', 'null', '"x"', '[]', '']) {
    put(v);
    assert.equal(operatorZone(dir), null, JSON.stringify(v));
  }
  rmSync(file);
  assert.equal(operatorZone(dir), null);
});

test('an alias this runtime accepts but a browser may not, or maps elsewhere, is null; a zone ICU renamed is kept under either name', () => {
  // The runtime's Intl constructor takes EST (and resolves it to America/Panama), EST5EDT, SystemV/EST5, +05:00: none
  // is a browser's zone, and the label would sit on the wrong hour. The file names an Area/Location zone or nothing.
  for (const tz of ['EST', 'EST5EDT', 'SystemV/EST5', 'Etc/GMT+5', '+05:00', 'utc', 'america/cancun', 'America/Nowhere']) {
    put({ tz, label: 'x' });
    assert.equal(operatorZone(dir), null, tz);
  }
  // ICU and IANA disagree on the canonical spelling of some zones; the file's spelling is served as given.
  for (const tz of ['UTC', 'America/Cancun', 'Asia/Tokyo', 'Asia/Kolkata', 'Asia/Calcutta', 'Europe/Kyiv', 'Europe/Kiev', 'Asia/Ho_Chi_Minh', 'America/Nuuk']) {
    put({ tz, label: 'x' });
    assert.deepEqual(operatorZone(dir), { tz, label: 'x' }, tz);
  }
});

test('served to the install admin only: a client-scoped caller or a member gets no field, nor does the admin without a file', () => {
  put({ tz: 'America/Cancun', label: 'Local' });
  assert.deepEqual(operatorZoneFor('admin', null, dir), { tz: 'America/Cancun', label: 'Local' });
  assert.equal(operatorZoneFor('admin', 'acme', dir), null);
  assert.equal(operatorZoneFor('member', null, dir), null);
  assert.equal(operatorZoneFor(null, null, dir), null);
  rmSync(file);
  assert.equal(operatorZoneFor('admin', null, dir), null);
});
