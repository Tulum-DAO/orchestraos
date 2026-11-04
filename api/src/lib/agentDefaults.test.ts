import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readDefaultModels, writeDefaultModel, BUILTIN_MODELS, MODEL_ID_RE } from './agentDefaults.js';

test('a default model per runtime round-trips; unknown runtimes and bad ids are not kept', () => {
  const d = mkdtempSync(join(tmpdir(), 'agent-defaults-'));
  assert.deepEqual(readDefaultModels(d), {});
  writeDefaultModel(d, 'codex', 'gpt-5.6-mini');
  writeDefaultModel(d, 'claude', 'claude-opus-5-5[1m]');
  assert.deepEqual(readDefaultModels(d), { codex: 'gpt-5.6-mini', claude: 'claude-opus-5-5[1m]' });
  assert.throws(() => writeDefaultModel(d, 'cobol', 'x'));
  assert.throws(() => writeDefaultModel(d, 'claude', 'bad id; rm'));
  assert.deepEqual(Object.keys(BUILTIN_MODELS).sort(), ['claude', 'codex', 'gemini', 'grok']);
  assert.equal(MODEL_ID_RE.test('claude-opus-5-5[1m]'), true);
});
