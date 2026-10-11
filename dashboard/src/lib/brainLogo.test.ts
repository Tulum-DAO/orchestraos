import { test } from 'node:test';
import assert from 'node:assert/strict';
import { brainProviderId } from './brainLogo.ts';

test('a picked brain wins', () => {
  assert.equal(brainProviderId({ provider: 'codex' }, { kind: 'runtime', runtime: 'claude' }), 'codex');
});
test('default runtime brain: its runtime', () => {
  assert.equal(brainProviderId(null, { kind: 'runtime', runtime: 'claude' }), 'claude');
});
test('default api brain: its provider, gemini when unnamed', () => {
  assert.equal(brainProviderId(null, { kind: 'api', provider: 'gemini' }), 'gemini');
  assert.equal(brainProviderId(null, { kind: 'api' }), 'gemini');
});
test('nothing known: null', () => {
  assert.equal(brainProviderId(null, null), null);
  assert.equal(brainProviderId(null, { kind: 'none' }), null);
});
