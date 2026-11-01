/**
 * The login-shell list: what the API writes when it starts a Connect shell, and what the dashboard proxy reads before it
 * lets the web terminal attach. Both halves run here against one temp data dir: the writer (lib/loginShells.ts) and the
 * proxy's own reader (dashboard-login-shells.js at the checkout root), so the two cannot drift.
 *
 * Run: npx tsx --test src/lib/loginShells.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { createRequire } from 'module';
import {
  recordLoginShell, readLoginShells, sweepLoginShells, loginShellsFile, LOGIN_SHELL_TTL_MS,
} from './loginShells.js';

const require = createRequire(import.meta.url);
const { listedLoginShell } = require('../../../dashboard-login-shells.js') as {
  listedLoginShell: (session: string, dir: string, now?: number) => boolean;
};

const fresh = () => mkdtempSync(join(tmpdir(), 'login-shells-'));
const T0 = Date.parse('2026-10-31T12:00:00Z');

test('an unlisted login-x is refused: the name alone is never enough', () => {
  const dir = fresh();
  assert.equal(listedLoginShell('login-x', dir, T0), false, 'no file');
  recordLoginShell(dir, { session: 'login-codex', provider: 'codex', createdBy: 'sample-user' }, T0);
  assert.equal(listedLoginShell('login-x', dir, T0), false, 'another shell is listed, not this one');
});

test('a listed shell is allowed, with the fields the proxy and an audit need', () => {
  const dir = fresh();
  const row = recordLoginShell(dir, { session: 'login-x', provider: 'codex', createdBy: 'sample-user' }, T0);
  assert.deepEqual(row, {
    session: 'login-x', provider: 'codex', created_at: '2026-10-31T12:00:00.000Z',
    expires_at: new Date(T0 + LOGIN_SHELL_TTL_MS).toISOString(), created_by: 'sample-user',
  });
  assert.equal(listedLoginShell('login-x', dir, T0 + 1000), true);
  assert.deepEqual(JSON.parse(readFileSync(loginShellsFile(dir), 'utf-8')).shells, [row]);
});

test('an expired entry is refused, and the sweep drops it', () => {
  const dir = fresh();
  recordLoginShell(dir, { session: 'login-x', provider: 'grok', createdBy: null }, T0);
  const after = T0 + LOGIN_SHELL_TTL_MS + 1;
  assert.equal(listedLoginShell('login-x', dir, after), false);
  assert.deepEqual(sweepLoginShells(dir, () => true, after), ['login-x']);
  assert.deepEqual(readLoginShells(dir), []);
});

test('when the shell exits its entry is removed, and the proxy refuses it at once', () => {
  const dir = fresh();
  recordLoginShell(dir, { session: 'login-x', provider: 'gemini', createdBy: null }, T0);
  recordLoginShell(dir, { session: 'login-y', provider: 'codex', createdBy: null }, T0);
  assert.equal(listedLoginShell('login-x', dir, T0), true);
  const live = new Set(['login-y']);                      // login-x's tmux session has ended
  assert.deepEqual(sweepLoginShells(dir, (s) => live.has(s), T0), ['login-x']);
  assert.equal(listedLoginShell('login-x', dir, T0), false, 'the proxy sees the change, not its cached copy');
  assert.equal(listedLoginShell('login-y', dir, T0), true, 'the other shell stays');
});

test('a sweep with nothing to drop leaves the file alone', () => {
  const dir = fresh();
  recordLoginShell(dir, { session: 'login-x', provider: 'codex', createdBy: null }, T0);
  const before = readFileSync(loginShellsFile(dir), 'utf-8');
  assert.deepEqual(sweepLoginShells(dir, () => true, T0), []);
  assert.equal(readFileSync(loginShellsFile(dir), 'utf-8'), before);
});

test('Connect again re-lists the same shell with a fresh TTL, never twice', () => {
  const dir = fresh();
  recordLoginShell(dir, { session: 'login-x', provider: 'codex', createdBy: null }, T0);
  const later = T0 + LOGIN_SHELL_TTL_MS - 1000;
  recordLoginShell(dir, { session: 'login-x', provider: 'codex', createdBy: null }, later);
  assert.equal(readLoginShells(dir).length, 1);
  assert.equal(listedLoginShell('login-x', dir, T0 + LOGIN_SHELL_TTL_MS + 1), true);
});

test('a bad file or a bad entry allows nothing', () => {
  const dir = fresh();
  mkdirSync(join(dir, 'state'), { recursive: true });
  writeFileSync(loginShellsFile(dir), 'not json');
  assert.equal(listedLoginShell('login-x', dir, T0), false);
  assert.deepEqual(readLoginShells(dir), []);
  writeFileSync(loginShellsFile(dir), JSON.stringify({ shells: [{ session: 'login-x' }, { session: 'bad name', expires_at: '2099-01-01T00:00:00Z' }] }));
  assert.equal(listedLoginShell('login-x', dir, T0), false, 'no expiry: not allowed');
  assert.equal(listedLoginShell('bad name', dir, T0), false);
});
