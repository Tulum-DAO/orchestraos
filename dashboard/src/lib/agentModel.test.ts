import { test } from 'node:test';
import assert from 'node:assert/strict';
import { modelLabel, pickRefusal, switchNotice, switchableModels, type AgentModelInfo } from './agentModel.ts';

const live: AgentModelInfo = {
  runtime: 'claude', model: 'sample-a', how: 'live', allowed: ['sample-a', 'sample-b'],
  models: [{ id: 'sample-a', label: 'Sample A', available: true }, { id: 'sample-b', label: 'Sample B', available: true }],
};
const cannot: AgentModelInfo = {
  runtime: 'grok', model: null, how: null, allowed: [],
  models: [{ id: 'sample-g', label: 'Sample G', available: false, reason: 'this runtime cannot switch models yet' }],
};

test('labels come from the agent\'s own list, else the id', () => {
  assert.equal(modelLabel(live, 'sample-b'), 'Sample B');
  assert.equal(modelLabel(live, 'unknown-x'), 'unknown-x');
  assert.equal(modelLabel(null, null), null);
  // the gateway's own shape names models in `title`
  assert.equal(modelLabel({ ...live, models: [{ id: 'opus', title: 'Opus 5.5' }] }, 'opus'), 'Opus 5.5');
});

test('a pick from the same provider is allowed; another provider means a new agent', () => {
  assert.equal(pickRefusal(live, { providerId: 'claude', modelLabel: 'Sample B' }), null);
  assert.equal(pickRefusal(live, { providerId: 'codex', modelLabel: 'Sample C' }),
    'This agent runs on Claude. To use Sample C, start a new agent on Codex.');
});

test('an agent that cannot switch says why; an unreadable one says so', () => {
  assert.equal(pickRefusal(cannot, { providerId: 'grok', modelLabel: 'Sample G' }),
    "This agent can't switch models: this runtime cannot switch models yet.");
  assert.match(pickRefusal(null, { providerId: 'claude', modelLabel: 'x' }) ?? '', /can't be read/);
});

test('each gateway answer reads in plain words', () => {
  assert.deepEqual(switchNotice(200, { ok: true, switching: 'switched', changed: true }, 'Sample B'),
    { tone: 'ok', text: 'Switched to Sample B.' });
  assert.deepEqual(switchNotice(200, { ok: true, switching: 'next_rotation' }, 'Sample B'),
    { tone: 'wait', text: "Sample B will be used from this agent's next rotation." });
  assert.deepEqual(switchNotice(202, { ok: true, status: 'pending', applies: 'at turn end' }, 'Sample B'),
    { tone: 'wait', text: 'Sample B will apply when the agent finishes its current turn.' });
  assert.equal(switchNotice(200, { ok: true, changed: false }, 'Sample A').text, 'Already on Sample A.');
  assert.equal(switchNotice(200, { ok: true, changed: false, cancelled: true }, 'Sample A').text,
    'Kept Sample A; the waiting switch was cancelled.');
  assert.equal(switchNotice(200, { ok: true, switching: 'switched', warning: 'x' }, 'Sample B').tone, 'wait');
});

test('failures say what happened and never claim a switch', () => {
  assert.deepEqual(switchNotice(422, { ok: false, error: 'not offered for this agent' }, 'Sample Z'),
    { tone: 'error', text: "Couldn't switch to Sample Z: not offered for this agent." });
  assert.equal(switchNotice(0, {}, 'Sample B').text, "Couldn't reach OrchestraOS. Nothing was switched.");
  assert.match(switchNotice(502, { ok: false, error: 'gateway unreachable' }, 'Sample B').text, /may still go through/);
  assert.equal(switchNotice(503, {}, 'Sample B').text, "Couldn't switch to Sample B: error 503.");
});

test('the picker lists only the models this agent can switch to, by their titles', () => {
  const info: AgentModelInfo = { ...live, models: [
    { id: 'sample-a', title: 'Sample A', available: true },
    { id: 'sample-a[1m]', title: 'Sample A (1M)', available: true },
    { id: 'sample-x', title: 'Sample X', available: false, reason: 'off' }] };
  assert.deepEqual(switchableModels(info).map((m) => [m.id, m.label]), [['sample-a', 'Sample A'], ['sample-a[1m]', 'Sample A (1M)']]);
  assert.deepEqual(switchableModels(null), []);
});

test('effort: offered for an agent only where the gateway says a level reaches it, with the runtime\'s own levels', async () => {
  const { agentEffortLevels, effortNotice } = await import('./agentModel.ts');
  const claude: AgentModelInfo = { ...live, effort: 'high', effort_levels: ['low', 'medium', 'high', 'xhigh', 'max'], effort_how: 'live' };
  assert.deepEqual(agentEffortLevels(claude)?.map((l) => l.id), ['low', 'medium', 'high', 'xhigh', 'max']);
  assert.equal(agentEffortLevels({ ...claude, effort_how: null }), null, 'no way to apply it (e.g. a headless seat whose supervisor predates effort)');
  assert.equal(agentEffortLevels(live), null, 'an older gateway that does not answer effort');
  assert.equal(agentEffortLevels(cannot), null);
  assert.equal(agentEffortLevels(null), null);
  const codex: AgentModelInfo = { ...live, runtime: 'codex', effort: null, effort_levels: ['low', 'medium', 'high', 'xhigh'], effort_how: 'live' };
  assert.deepEqual(agentEffortLevels(codex)?.map((l) => l.label), ['Low', 'Medium', 'High', 'Extra high']);

  assert.deepEqual(effortNotice(200, { ok: true, effort: 'high', effort_switching: 'switched' }, 'High'), { tone: 'ok', text: 'Effort set to High.' });
  assert.deepEqual(effortNotice(200, { ok: true, changed: false }, 'High'), { tone: 'ok', text: 'Already on High effort.' });
  assert.deepEqual(effortNotice(200, { ok: true, effort: 'high', effort_switching: 'next_start' }, 'High'),
    { tone: 'ok', text: 'High effort applies when the agent next restarts.' }, 'kept for the next start, not set now');
  assert.deepEqual(effortNotice(202, { ok: true, status: 'pending' }, 'High'),
    { tone: 'wait', text: 'High effort will apply when the agent finishes its current turn.' });
  assert.deepEqual(effortNotice(502, { ok: false, error: 'unconfirmed' }, 'High'), { tone: 'error', text: "Couldn't set High effort: unconfirmed." });
  assert.equal(effortNotice(0, {}, 'High').tone, 'error');
});

test('an effort pick sends the model still waiting to be switched to, so it never cancels that switch', async () => {
  const { readFileSync } = await import('node:fs');
  const c = readFileSync(new URL('../components/agent/Composer.tsx', import.meta.url), 'utf8');
  assert.match(c, /baseModel: serverModel\?\.pending\?\.model \?\? serverModel\?\.model \?\? null/);
});

test('a model switch whose kept effort could not be set again says so', () => {
  assert.deepEqual(switchNotice(200, { ok: true, switching: 'switched', effort_warning: 'x', effort_not_set: 'xhigh' }, 'Sonnet 5.5'),
    { tone: 'wait', text: 'Switched to Sonnet 5.5, but its Extra high effort could not be set again.' });
});
