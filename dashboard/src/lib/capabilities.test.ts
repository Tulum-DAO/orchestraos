import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { isCapable } from './capabilities.ts';

test('only an explicit false hides a capability', () => {
  assert.equal(isCapable({ voicePromptSync: false }, 'voicePromptSync'), false);
  assert.equal(isCapable({ voicePromptSync: true }, 'voicePromptSync'), true);
});

test('unknown keeps the entry: loading, failed probe, older API, missing key', () => {
  for (const caps of [undefined, null, 'x', {}, { projectStatus: false }]) {
    assert.equal(isCapable(caps, 'voicePromptSync'), true, JSON.stringify(caps));
  }
});

test('the Voice page gates Sync Prompts on voicePromptSync', () => {
  const src = readFileSync(new URL('../pages/Voice.tsx', import.meta.url), 'utf-8');
  const at = src.indexOf('Sync Prompts');
  assert.ok(at > 0);
  assert.match(src.slice(Math.max(0, at - 900), at), /isCapable\(caps, 'voicePromptSync'\)/);
});

test('Command Center skips /api/project-status when the install lacks it', () => {
  const src = readFileSync(new URL('../pages/CommandCenter.tsx', import.meta.url), 'utf-8');
  const at = src.indexOf("fetch('/api/project-status')");
  assert.ok(at > 0);
  assert.match(src.slice(Math.max(0, at - 400), at), /isCapable\(caps, 'projectStatus'\)/);
});
