import { testTmpDir } from './testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, readFileSync, statSync, chmodSync } from 'node:fs';
import { join } from 'node:path';
import { setTomlString, setTomlStringChecked, readTomlString, isTimeZone, writeFileKeepingMode } from './tomlEdit.js';

const SRC = '# top\n[operator]\n# Your timezone, as an IANA name\nid = "operator"\ntimezone = "America/New_York"  # keep me\n\n[machines]\nx = 1\n';

test('replaces the value in place, keeping comments and the rest of the file', () => {
  const out = setTomlString(SRC, 'operator', 'timezone', 'Europe/Berlin');
  assert.equal(out, SRC.replace('"America/New_York"', '"Europe/Berlin"'));
  assert.equal(readTomlString(out, 'operator', 'timezone'), 'Europe/Berlin');
});

test('adds the key at the end of its section when it is missing', () => {
  const src = '[operator]\nid = "operator"\n\n[machines]\nx = 1\n';
  assert.equal(setTomlString(src, 'operator', 'timezone', 'UTC'), '[operator]\nid = "operator"\ntimezone = "UTC"\n\n[machines]\nx = 1\n');
});

test('adds the section when it is missing', () => {
  assert.equal(setTomlString('[a]\nb = 1\n', 'operator', 'timezone', ''), '[a]\nb = 1\n\n[operator]\ntimezone = ""\n');
});

test('only the named section is read or touched', () => {
  const src = '[other]\ntimezone = "x"\n[operator]\nid = "o"\n';
  assert.equal(readTomlString(src, 'operator', 'timezone'), null);
  assert.equal(setTomlString(src, 'operator', 'timezone', 'UTC'), '[other]\ntimezone = "x"\n[operator]\nid = "o"\ntimezone = "UTC"\n');
});

test('a single-quoted or bare value is read and replaced, never duplicated', () => {
  const src = "[operator]\ntimezone = 'Asia/Tokyo'\n";
  assert.equal(readTomlString(src, 'operator', 'timezone'), 'Asia/Tokyo');
  assert.equal(setTomlString(src, 'operator', 'timezone', 'UTC'), '[operator]\ntimezone = "UTC"\n');
});

test('isTimeZone: IANA names and "" (UTC) only', () => {
  for (const ok of ['Europe/Berlin', 'America/Argentina/Buenos_Aires', 'UTC', '']) assert.equal(isTimeZone(ok), true, ok);
  for (const bad of ['Mars/Base', 'x"; rm -rf', 5, null]) assert.equal(isTimeZone(bad), false, String(bad));
});

test('writeFileKeepingMode keeps the file mode', () => {
  const f = join(testTmpDir('toml-'), 'orchestra.toml');
  writeFileSync(f, 'a'); chmodSync(f, 0o640);
  writeFileKeepingMode(f, 'b');
  assert.equal(readFileSync(f, 'utf-8'), 'b');
  assert.equal(statSync(f).mode & 0o777, 0o640);
});

// The line editor only understands `key = "value"` directly under [section]. Anything else (the shapes a reviewer found)
// would come out as a broken or duplicate key, so the result is parsed with the same TOML parser the API reads the
// file with, and refused unless it holds exactly the new value.
const REFUSED = "Couldn't edit orchestra.toml safely; set [operator] timezone by hand";
test('refuses shapes it cannot edit safely: root dotted key, inline table, quoted key, triple-quoted value', () => {
  const shapes: Record<string, string> = {
    'root dotted operator.id': 'operator.id = "x"\n\n[rotation]\nctx_ceiling_pct = 0.8\n',
    'inline table': 'operator = { id = "x", timezone = "UTC" }\n',
    'quoted key': '[operator]\n"timezone" = "UTC"\n',
    'triple-quoted value': '[operator]\ntimezone = """UTC"""\n',
  };
  for (const [name, src] of Object.entries(shapes)) {
    assert.throws(() => setTomlStringChecked(src, 'operator', 'timezone', 'Europe/Paris'), { message: REFUSED }, name);
  }
});

test('the checked edit returns the same text as the plain one when the result parses to the new value', () => {
  assert.equal(setTomlStringChecked(SRC, 'operator', 'timezone', 'Europe/Berlin'), setTomlString(SRC, 'operator', 'timezone', 'Europe/Berlin'));
  assert.equal(setTomlStringChecked('', 'operator', 'timezone', 'UTC'), setTomlString('', 'operator', 'timezone', 'UTC'));
  assert.equal(setTomlStringChecked(SRC, 'operator', 'timezone', ''), setTomlString(SRC, 'operator', 'timezone', ''));
});
