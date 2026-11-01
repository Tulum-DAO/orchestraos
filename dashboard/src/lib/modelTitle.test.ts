import { test } from 'node:test';
import assert from 'node:assert/strict';
import { agentModelTitle, modelIdTitle, choiceTitle } from './modelTitle.ts';
import type { AgentModelInfo } from './agentModel.ts';

const info = (model: string | null, models: AgentModelInfo['models']): AgentModelInfo =>
  ({ runtime: 'claude', model, how: 'live', allowed: [], models });

test('an agent\'s model is named as the picker names it: the name and its context size', () => {
  const models = [
    { id: 'claude-opus-5-5[1m]', title: 'Opus 5.5', subtitle: '1M context' },
    { id: 'claude-opus-5', title: 'Opus 5', subtitle: '200K context' },
    { id: 'fable', title: 'Fable 5.1' },
  ];
  assert.equal(agentModelTitle(info('claude-opus-5-5[1m]', models)), 'Opus 5.5 · 1M');
  assert.equal(agentModelTitle(info('claude-opus-5', models)), 'Opus 5 · 200K');
  assert.equal(agentModelTitle(info('fable', models)), 'Fable 5.1');   // no source states its size: none shown
});

test('a short id that stands for the 1M Opus 5.5 is named as it; an unknown model keeps its id; none is null', () => {
  assert.equal(agentModelTitle(info('opus', [{ id: 'opus', title: 'Opus 5.5' }])), 'Opus 5.5 · 1M');
  assert.equal(agentModelTitle(info('sample-model-x', [])), 'sample-model-x');
  assert.equal(agentModelTitle(info(null, [])), null);
  assert.equal(agentModelTitle(null), null);
});

test('Opus 5.5 at 200k is never called plain "Opus 5.5" on any surface: a seat still on it says 200K', () => {
  assert.equal(agentModelTitle(info('claude-opus-5-5', [{ id: 'claude-opus-5-5', title: 'Opus 5.5' }])), 'Opus 5.5 · 200K');
  assert.equal(agentModelTitle(info('claude-opus-5-5', [])), 'Opus 5.5 · 200K');
  assert.equal(modelIdTitle('claude', 'claude-opus-5-5', 'Opus 5.5'), 'Opus 5.5 · 200K');
  // other 200K models keep their name, tagged
  assert.equal(modelIdTitle('claude', 'claude-opus-5', 'Opus 5', '200K context'), 'Opus 5 · 200K');
  assert.equal(modelIdTitle('claude', 'claude-haiku-4-5-20251001', 'Haiku 4.5', '200K context'), 'Haiku 4.5 · 200K');
});

test('any model id is named by the picker\'s rules, with or without words to go on', () => {
  assert.equal(modelIdTitle('claude', 'claude-opus-5-5[1m]'), 'Opus 5.5 · 1M');
  assert.equal(modelIdTitle('claude', 'opus', 'Opus 5.5'), 'Opus 5.5 · 1M', 'the short id is the 1M model');
  assert.equal(modelIdTitle('claude', 'claude-opus-5-5[1m]', 'Opus 5.5 (1M)'), 'Opus 5.5 · 1M');
  assert.equal(modelIdTitle('claude', 'fable', 'Fable 5.1'), 'Fable 5.1');
  assert.equal(modelIdTitle('claude', 'claude-fable-5-1'), 'Fable 5.1');
  assert.equal(modelIdTitle('grok', 'grok-4.7', 'Grok 4.7'), 'Grok 4.7');
  assert.equal(modelIdTitle('codex', 'sample-model-x'), 'sample-model-x');
});

test('a saved brain choice is named as the picker names it now, whatever label was saved with it', () => {
  assert.equal(choiceTitle({ provider: 'claude', model: 'opus', label: 'Opus 5.5' }), 'Opus 5.5 · 1M');
  assert.equal(choiceTitle({ provider: 'claude', model: 'claude-opus-5-5', label: 'Opus 5.5' }), 'Opus 5.5 · 200K');
  assert.equal(choiceTitle({ provider: 'claude', model: 'claude-opus-5-5[1m]', label: 'Opus 5.5 · 1M' }), 'Opus 5.5 · 1M');
  assert.equal(choiceTitle({ provider: 'claude', model: 'fable', label: 'Fable 5.1' }), 'Fable 5.1');
  assert.equal(choiceTitle({ provider: 'gemini', model: '', label: 'gemini' }), 'gemini');
});
