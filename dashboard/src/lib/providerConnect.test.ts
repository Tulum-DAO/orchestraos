import { test } from 'node:test';
import assert from 'node:assert/strict';
import { connectOverview, reasonText } from './providerConnect.ts';
import { statusLabel } from './modelSelectorFilter.ts';

test('a provider\'s state in words people read, never a code', () => {
  assert.equal(statusLabel({ installed: false, authed: false, auth_reason: 'not-installed' }), 'Not installed');
  assert.equal(statusLabel({ installed: true, authed: false, auth_reason: 'signed-out' }), 'Not signed in');
  assert.equal(statusLabel({ installed: true, authed: false, auth_reason: 'loggedIn=false' }), 'Not signed in');
  assert.equal(statusLabel({ installed: true, authed: 'unverified', auth_reason: 'auth-probe-cmd-failed' }), "Couldn't check the sign-in");
  assert.equal(statusLabel({ installed: true, authed: false, auth_reason: 'token-expired' }), 'Sign-in expired');
  assert.equal(reasonText({ id: 'codex', label: 'Codex', installed: false, authed: false, auth_reason: 'not-installed' }), 'Not installed');
  assert.equal(reasonText({ id: 'codex', label: 'Codex', installed: true, authed: true }), '');
});

test('the Connect sheet\'s plain top: Install, Sign in, and the status', () => {
  const fresh = connectOverview({ id: 'codex', label: 'Codex', installed: false, authed: false, auth_reason: 'not-installed' });
  assert.deepEqual(fresh, { steps: [{ label: 'Install Codex', state: 'todo' }, { label: 'Sign in', state: 'todo' }], status: 'Not installed', connected: false });
  const out = connectOverview({ id: 'codex', label: 'Codex', installed: true, authed: false, auth_reason: 'signed-out' });
  assert.deepEqual(out.steps.map((s) => s.state), ['done', 'todo']);
  assert.equal(out.status, 'Not signed in');
  const ok = connectOverview({ id: 'claude', label: 'Claude', installed: true, authed: true });
  assert.deepEqual([ok.connected, ok.status, ok.steps.map((s) => s.state)], [true, 'Connected', ['done', 'done']]);
});
