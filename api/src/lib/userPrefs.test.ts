import { testTmpDir } from './testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { readUserPrefs, writeUserPref, userPrefsFile } from './userPrefs.js';

const dir = () => testTmpDir('user-prefs-');

test('a person\'s preference round-trips in state/users/<u>/prefs.json; null removes it', () => {
  const d = dir();
  assert.deepEqual(readUserPrefs(d, 'operator'), {});
  writeUserPref(d, 'operator', 'default_chat_style', 'modern');
  assert.ok(existsSync(join(d, 'state', 'users', 'operator', 'prefs.json')));
  assert.deepEqual(readUserPrefs(d, 'operator'), { default_chat_style: 'modern' });
  assert.deepEqual(readUserPrefs(d, 'someone-else'), {});
  writeUserPref(d, 'operator', 'default_chat_style', null);
  assert.deepEqual(readUserPrefs(d, 'operator'), {});
});

test('a user name that could leave the users folder is refused', () => {
  const d = dir();
  assert.throws(() => userPrefsFile(d, '../x'));
  assert.throws(() => writeUserPref(d, '..', 'k', 1));
  assert.deepEqual(readUserPrefs(d, '../x'), {});
});
