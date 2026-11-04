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

test('a default effort per runtime round-trips next to the models; only a level that runtime has is kept', async () => {
  const { readDefaultEfforts, writeDefaultEffort, EFFORT_LEVELS } = await import('./agentDefaults.js');
  const d = mkdtempSync(join(tmpdir(), 'agent-defaults-'));
  writeDefaultModel(d, 'codex', 'gpt-5.6-mini');
  assert.deepEqual(readDefaultEfforts(d), {});
  writeDefaultEffort(d, 'codex', 'xhigh');
  writeDefaultEffort(d, 'claude', 'max');
  assert.deepEqual(readDefaultEfforts(d), { codex: 'xhigh', claude: 'max' });
  assert.deepEqual(readDefaultModels(d), { codex: 'gpt-5.6-mini' }, 'the models are kept');
  writeDefaultModel(d, 'claude', 'claude-opus-5-5[1m]');
  assert.deepEqual(readDefaultEfforts(d), { codex: 'xhigh', claude: 'max' }, 'and a model write keeps the efforts');
  assert.throws(() => writeDefaultEffort(d, 'codex', 'max'), /level/);
  assert.throws(() => writeDefaultEffort(d, 'gemini', 'high'));
  writeDefaultEffort(d, 'codex', null);
  assert.deepEqual(readDefaultEfforts(d), { claude: 'max' }, 'null: back to the CLI default');
  assert.deepEqual(EFFORT_LEVELS.claude, ['low', 'medium', 'high', 'xhigh', 'max']);
  assert.deepEqual(EFFORT_LEVELS.codex, ['low', 'medium', 'high', 'xhigh']);
});
