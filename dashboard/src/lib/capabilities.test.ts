import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { isCapable } from './capabilities.ts';

test('only an explicit false hides a capability', () => {
  assert.equal(isCapable({ learningLog: false }, 'learningLog'), false);
  assert.equal(isCapable({ learningLog: true }, 'learningLog'), true);
});

test('unknown keeps the entry: loading, failed probe, older API, missing key', () => {
  for (const caps of [undefined, null, 'x', {}, { inspectScript: false }]) {
    assert.equal(isCapable(caps, 'learningLog'), true, JSON.stringify(caps));
  }
});

test('REMOVED (gm): no Sync Prompts button, no /api/project-status call', () => {
  const voice = readFileSync(new URL('../pages/Voice.tsx', import.meta.url), 'utf-8');
  const cc = readFileSync(new URL('../pages/CommandCenter.tsx', import.meta.url), 'utf-8');
  const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf-8');
  assert.ok(!voice.includes('Sync Prompts') && !api.includes('/voice/sync-prompts'));
  assert.ok(!cc.includes('/api/project-status'));
});
