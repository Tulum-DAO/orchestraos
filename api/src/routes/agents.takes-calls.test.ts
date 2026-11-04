/**
 * PUT /api/agents/:id/takes-calls {on}: an admin's switch for the agent's own calls (state/agent-calls.json), its
 * other options kept; a member is refused. Run: npx tsx --test src/routes/agents.takes-calls.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dir = mkdtempSync(join(tmpdir(), 'agents-takes-calls-'));
mkdirSync(join(dir, 'state'));
writeFileSync(join(dir, 'registry.json'), JSON.stringify({ agents: { 'dev-demo': { tmux_session: 'dev-demo', runtime: 'claude' } } }));
process.env.ORCHESTRA_DIR = dir;
process.env.ORCHESTRA_TAILSCALE_USERS = 'sam@x.net, mo@y.net member';
delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
const calls = () => JSON.parse(readFileSync(join(dir, 'state', 'agent-calls.json'), 'utf-8'));

async function withApp(fn: (put: (body: unknown, login: string, id?: string) => Promise<{ status: number; body: any }>) => Promise<void>) {
  const { default: agentsRouter } = await import('./agents.js');
  const app = express();
  app.use(express.json());
  app.use('/api/agents', agentsRouter);
  const server = app.listen(0);
  const url = (id: string) => `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/agents/${id}/takes-calls`;
  try {
    await fn(async (body, login, id = 'dev-demo') => {
      const r = await fetch(url(id), { method: 'PUT', headers: { 'content-type': 'application/json', 'tailscale-user-login': login }, body: JSON.stringify(body) });
      return { status: r.status, body: await r.json() };
    });
  } finally { server.close(); }
}

test('an admin turns the agent\'s own calls on and off; its other options stay', async () => {
  writeFileSync(join(dir, 'state', 'agent-calls.json'), JSON.stringify({ seats: { 'dev-demo': { ack_after_s: 5 } } }));
  await withApp(async (put) => {
    assert.deepEqual(await put({ on: true }, 'sam@x.net'), { status: 200, body: { ok: true, agent: 'dev-demo', takes_calls: true } });
    assert.deepEqual(calls().seats['dev-demo'], { ack_after_s: 5, on: true });
    await put({ on: false }, 'sam@x.net');
    assert.equal(calls().seats['dev-demo'].on, false);
  });
});

test('a member is refused and nothing is written; a bad body is a 400', async () => {
  writeFileSync(join(dir, 'state', 'agent-calls.json'), JSON.stringify({ seats: {} }));
  await withApp(async (put) => {
    const r = await put({ on: true }, 'mo@y.net');
    assert.deepEqual([r.status, r.body.error], [403, 'Only an admin can change this']);
    assert.deepEqual(calls().seats, {});
    assert.equal((await put({ on: 'yes' }, 'sam@x.net')).status, 400);
  });
});

test('the switch that saved still answers 200 when the change log cannot be written', async () => {
  writeFileSync(join(dir, 'state', 'agent-calls.json'), JSON.stringify({ seats: {} }));
  const logPath = join(dir, 'state', 'settings-log.jsonl');
  rmSync(logPath, { force: true });
  mkdirSync(logPath);
  const orig = console.error;
  console.error = () => {};
  try {
    await withApp(async (put) => {
      assert.deepEqual(await put({ on: true }, 'sam@x.net'), { status: 200, body: { ok: true, agent: 'dev-demo', takes_calls: true } });
      assert.equal(calls().seats['dev-demo'].on, true);
    });
  } finally { console.error = orig; rmSync(logPath, { recursive: true, force: true }); }
});

test('an id that is not a known agent is a 404 and nothing is written', async () => {
  writeFileSync(join(dir, 'state', 'agent-calls.json'), JSON.stringify({ seats: {} }));
  await withApp(async (put) => {
    const r = await put({ on: true }, 'sam@x.net', 'ghost-seat');
    assert.equal(r.status, 404);
    assert.equal(r.body.ok, false);
    assert.deepEqual(calls().seats, {});
  });
});
