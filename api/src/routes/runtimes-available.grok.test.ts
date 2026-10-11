import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { cliTextVerdict } from './runtimes-available.js';

// The grok entry's own patterns, read from the registry (no copy that could drift).
const providers = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../../config/providers.json'), 'utf8')).providers;
const grok = providers.find((p: { id: string }) => p.id === 'grok');

test('cli-text verdict: grok signed in / signed out / unknown', () => {
  assert.equal(grok.auth_probe.kind, 'cli-text');
  assert.equal(cliTextVerdict('You are logged in with grok.com\n\n  * grok-4.7 (default)\n', grok.auth_probe).authed, true);
  assert.equal(cliTextVerdict('You are not authenticated.\nRun `grok login`.\n', grok.auth_probe).authed, false);
  assert.equal(cliTextVerdict('unexpected banner\n', grok.auth_probe).authed, 'unverified');
});

test('line-regex catalog pattern picks grok ids and flags the default', () => {
  const p = grok.model_catalog.probe;
  const re = new RegExp(p.line_re);
  const out = 'You are logged in with grok.com\n\nModels:\n  * grok-4.7 (default)\n  - grok-4.6\n';
  const ids = out.split('\n').map((l) => l.match(re)?.[1]).filter(Boolean);
  assert.deepEqual(ids, ['grok-4.7', 'grok-4.6']);
  assert.ok(new RegExp(p.default_re).test('  * grok-4.7 (default)'));
});
