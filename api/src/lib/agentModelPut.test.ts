import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseModelPut } from './agentModelPut.js';

test('PUT /api/agents/:id/model: a model, and an effort level when one is picked, go to the gateway as sent', () => {
  assert.deepEqual(parseModelPut({ model: ' claude-opus-5-5 ' }), { model: 'claude-opus-5-5' });
  assert.deepEqual(parseModelPut({ model: 'gpt-5.6-terra', effort: 'xhigh' }), { model: 'gpt-5.6-terra', effort: 'xhigh' });
  assert.deepEqual(parseModelPut({ model: 'm', effort: null }), { model: 'm' }, 'no effort: the effort is left alone');
  assert.deepEqual(parseModelPut({}), { error: 'model required' });
  assert.deepEqual(parseModelPut({ model: 'm', effort: 'High' }), { error: 'effort must be a level id (low, medium, high, xhigh, max)' });
  assert.deepEqual(parseModelPut({ model: 'm', effort: 3 }), { error: 'effort must be a level id (low, medium, high, xhigh, max)' });
  assert.deepEqual(parseModelPut(null), { error: 'model required' });
});

test('a switch with an effort waits long enough for both, and the dashboard proxy waits longer still', async () => {
  const { MODEL_PUT_TIMEOUT_MS } = await import('./agentModelPut.js');
  // /model then /effort: each up to 10 s for the CLI's line + 3 s settle + 5 s recheck.
  assert.ok(MODEL_PUT_TIMEOUT_MS >= 40000, `${MODEL_PUT_TIMEOUT_MS}`);
  const { readFileSync } = await import('node:fs');
  const agents = readFileSync(new URL('../routes/agents.ts', import.meta.url), 'utf8');
  assert.match(agents, /AbortSignal\.timeout\(put \? MODEL_PUT_TIMEOUT_MS : 10000\)/);
  const proxy = readFileSync(new URL('../../../dashboard-proxy.js', import.meta.url), 'utf8');
  const m = proxy.match(/MODEL_PUT_PROXY_TIMEOUT_MS=(\d+)/);
  assert.ok(m && Number(m[1]) > MODEL_PUT_TIMEOUT_MS, 'the proxy must not drop it first');
  assert.match(proxy, /proxyTo\(VPS_API,req,res,isModelPut\(req\)\?MODEL_PUT_PROXY_TIMEOUT_MS:undefined\)/);
});

test('forgetAgentEffort hands the name to scripts/agent_effort.py forget, which writes under its lock', async () => {
  const { forgetAgentEffort } = await import('./agentModelPut.js');
  const { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  const calls: string[][] = [];
  forgetAgentEffort('/data', 'x', (cmd, args) => { calls.push([cmd, ...args]); });
  assert.equal(calls[0][0], 'python3');
  assert.match(calls[0][1], /scripts\/agent_effort\.py$/);
  assert.deepEqual(calls[0].slice(2), ['forget', 'x', '--orchestra-dir', '/data']);
  // And for real, through the Python helper: the lock file is the writers' own.
  const d = mkdtempSync(join(tmpdir(), 'efforts-'));
  mkdirSync(join(d, 'state'), { recursive: true });
  const f = join(d, 'state', 'agent-efforts.json');
  writeFileSync(f, JSON.stringify({ efforts: { x: 'high', y: 'low' } }));
  const saved = process.env.AGENT_EFFORT_FILE;
  delete process.env.AGENT_EFFORT_FILE;
  try { forgetAgentEffort(d, 'x'); } finally { if (saved !== undefined) process.env.AGENT_EFFORT_FILE = saved; }
  assert.deepEqual(JSON.parse(readFileSync(f, 'utf8')), { efforts: { y: 'low' } });
  assert.ok(existsSync(`${f}.lock`));
  void fileURLToPath;
});
