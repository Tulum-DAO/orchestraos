/**
 * gm msg_c146a84e: three live routes built a filesystem path from a request-supplied agent id.
 * Drives the REAL routers through Express (which decodes %2F in route params) against a temp
 * ORCHESTRA_DIR with a temp registry. Every refused request must leave the tree byte-identical.
 * ORCHESTRA_DIR is read at import time by state-reader, so it is set BEFORE the dynamic imports.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, existsSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { SAFE_AGENT_ID, containedPath, promptPathFor, inboxDirFor, registeredAgent, UnsafeAgentPath } from '../lib/agentPaths.js';

// ---- the guard itself -------------------------------------------------------------------------
test('SAFE_AGENT_ID: real ids pass; traversal, separators, dots and empties do not', () => {
  for (const ok of ['gm', 'orchestraos-builder', 'pm-intentmagic', 'a', 'x_1']) assert.ok(SAFE_AGENT_ID.test(ok), ok);
  for (const bad of ['', '..', '../x', 'a/b', 'a\\b', '.hidden', 'a.b', '-lead', 'x\u0000y', 'unregistered:foo', 'a'.repeat(129)]) {
    assert.ok(!SAFE_AGENT_ID.test(bad), JSON.stringify(bad));
  }
});

test('containedPath: inside is fine; an escape and a look-alike sibling both throw', () => {
  assert.equal(containedPath('/o/queue/inbox', 'gm'), '/o/queue/inbox/gm');
  assert.throws(() => containedPath('/o/queue/inbox', '../../x'), UnsafeAgentPath);
  assert.throws(() => containedPath('/o/prompts', '../prompts-evil/x.md'), UnsafeAgentPath);
  assert.throws(() => containedPath('/o/x', ''), UnsafeAgentPath, 'the base itself is not inside it');
});

test('promptPathFor: registry paths must stay under prompts/ and be .md', () => {
  assert.equal(promptPathFor('/o', 'gm', {}), '/o/prompts/gm.md');
  assert.equal(promptPathFor('/o', 'gm', { system_prompt: 'prompts/custom.md' }), '/o/prompts/custom.md');
  assert.throws(() => promptPathFor('/o', 'gm', { system_prompt: '../.claude/CLAUDE.md' }), UnsafeAgentPath);
  assert.throws(() => promptPathFor('/o', 'gm', { system_prompt: 'CLAUDE.md' }), UnsafeAgentPath);
  assert.throws(() => promptPathFor('/o', 'gm', { system_prompt: 'prompts/x.sh' }), UnsafeAgentPath);
});

test('registeredAgent: only a SAFE id that the registry holds', () => {
  const reg = { agents: { gm: { tier: 'T0' }, 'pm-x': {} } };
  assert.ok(registeredAgent(reg, 'pm-x'));
  assert.equal(registeredAgent(reg, 'nope'), null);
  assert.equal(registeredAgent(reg, '../gm'), null);
  assert.equal(registeredAgent(reg, '__proto__'), null);
  assert.equal(inboxDirFor('/o', 'pm-x'), '/o/queue/inbox/pm-x');
});

// ---- the routes, end to end -------------------------------------------------------------------
function tree(dir: string): string[] {
  const out: string[] = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    out.push(p);
    if (e.isDirectory()) out.push(...tree(p));
  }
  return out.sort();
}

async function app() {
  const root = mkdtempSync(join(tmpdir(), 'guards-'));
  const orch = join(root, 'orch');
  mkdirSync(join(orch, 'prompts'), { recursive: true });
  mkdirSync(join(orch, 'state'), { recursive: true });
  writeFileSync(join(orch, 'registry.json'), JSON.stringify({ agents: {
    'pm-x': { tier: 'T2', system_prompt: 'prompts/pm-x.md', tmux_session: 'pm-x' },
    gm: { tier: 'T2', tmux_session: 'gm' },
  } }));
  writeFileSync(join(orch, 'prompts', 'pm-x.md'), 'original prompt');
  process.env.ORCHESTRA_DIR = orch;
  if (!process.env.ORCHESTRA_CONFIG) {
    process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
  }
  const express = (await import('express')).default;
  const agents = (await import(`./agents.js?t=${Date.now()}`)).default;
  const inspect = (await import(`./inspect-feedback.js?t=${Date.now()}`)).default;
  const a = express();
  a.use(express.json());
  a.use('/api/agents', agents);
  a.use('/api/inspect-feedback', inspect);
  const server = a.listen(0);
  const base = `http://127.0.0.1:${(server.address() as any).port}`;
  const call = (method: string, path: string, body?: unknown) => fetch(base + path, {
    method, headers: { 'content-type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
  });
  return { root, orch, server, call };
}

test('ROUTES: traversal and unknown ids are refused and write NOTHING; registered ids still work', async () => {
  const { root, orch, server, call } = await app();
  try {
    const before = tree(root);
    const refused: [string, string, unknown?][] = [
      ['PUT', '/api/agents/..%2F..%2Fpwn/prompt', { content: 'INJECTED' }],
      ['PUT', '/api/agents/not-registered/prompt', { content: 'INJECTED' }],
      ['GET', '/api/agents/..%2F..%2F..%2Fetc%2Fpasswd%00/prompt'],
      ['GET', '/api/agents/not-registered/prompt'],
      ['POST', '/api/agents/..%2F..%2Fpwn/task', { task: 'x' }],
      ['POST', '/api/agents/not-registered/task', { task: 'x' }],
      ['POST', '/api/inspect-feedback', { content: 'c', element: { tagName: 'div' }, agentId: '../../pwn' }],
      ['POST', '/api/inspect-feedback', { content: 'c', element: { tagName: 'div' }, agentId: 'not-registered' }],
    ];
    for (const [m, p, b] of refused) {
      const r = await call(m, p, b);
      assert.ok(r.status === 404 || r.status === 400, `${m} ${p} -> ${r.status}`);
    }
    assert.deepEqual(tree(root), before, 'a refused request must not create or change anything');

    // POSITIVE CONTROLS: the guard is a distinction, not an off switch.
    assert.equal((await call('GET', '/api/agents/pm-x/prompt')).status, 200);
    assert.equal((await call('PUT', '/api/agents/pm-x/prompt', { content: 'new prompt' })).status, 200);
    assert.equal(readFileSync(join(orch, 'prompts', 'pm-x.md'), 'utf-8'), 'new prompt');
    assert.equal((await call('POST', '/api/agents/pm-x/task', { task: 'do it' })).status, 200);
    assert.equal(readdirSync(join(orch, 'queue', 'inbox', 'pm-x')).length, 1);
    assert.equal((await call('POST', '/api/inspect-feedback', { content: 'c', element: { tagName: 'div' }, agentId: 'gm' })).status, 200);
    assert.ok(existsSync(join(orch, 'queue', 'inbox', 'gm')));
  } finally { server.close(); }
});
