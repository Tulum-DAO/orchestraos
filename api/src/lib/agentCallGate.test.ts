/**
 * `agent_calls` on GET /api/agents/:id/settings: true only when a call to the agent is the agent's own call, by the
 * voice side's rule (services/arturo/agent_call.py enabled(), on the relay engine; a seat in Dev counts, and `in_dev`
 * is reported beside it). Every case of contract/agent-call-gate.json runs here through the same files the route
 * reads; scripts/test_agent_call_gate.py runs the same cases on the voice side.
 *
 * Run: npx tsx --test src/lib/agentCallGate.test.ts   (from api/)
 */
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const CASES = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'contract', 'agent-call-gate.json'), 'utf-8')).cases as {
  name: string; seat: string; switch_on: unknown; headless: boolean; registry_runtime: string | null; dev: boolean; engine: string | null; expected: boolean;
}[];

const dir = mkdtempSync(join(tmpdir(), 'agent-call-gate-'));
mkdirSync(join(dir, 'state'), { recursive: true });
writeFileSync(join(dir, 'state', 'watch-gateway-token'), 'sample-bearer\n');
let engine: unknown = 'relay';
let healthCalls = 0;
const gw = http.createServer((req, res) => {
  healthCalls += 1;
  res.writeHead(req.url === '/arturo/health' ? 200 : 404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ live_engine: engine }));
});
await new Promise<void>((r) => gw.listen(0, '127.0.0.1', () => r()));
process.env.ORCHESTRA_DIR = dir;
process.env.HEADLESS_SEATS_FILE = join(dir, 'state', 'headless-seats.json');
process.env.WATCH_GATEWAY_TOKEN_FILE = join(dir, 'state', 'watch-gateway-token');
process.env.WATCH_GATEWAY_URL = `http://127.0.0.1:${(gw.address() as AddressInfo).port}`;

const { agentCallGoesToSeat, liveEngine, forgetLiveEngine } = await import('./agentCallGate.js');
const { default: agentsRouter } = await import('../routes/agents.js');
const app = express();
app.use('/api/agents', agentsRouter);
const server = app.listen(0);
const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/agents`;
after(() => { server.close(); gw.close(); });

/** Lay out the files a case describes, as the voice side reads them. */
function layOut(c: typeof CASES[number]) {
  writeFileSync(join(dir, 'state', 'agent-calls.json'), JSON.stringify({ seats: { [c.seat]: { on: c.switch_on } } }));
  writeFileSync(join(dir, 'state', 'headless-seats.json'), JSON.stringify({ seats: c.headless ? { [c.seat]: { headless: true, runtime: 'claude' } } : {} }));
  writeFileSync(join(dir, 'registry.json'), JSON.stringify({ agents: { [c.seat]: c.registry_runtime ? { runtime: c.registry_runtime } : {} } }));
  rmSync(join(dir, 'state', 'headless', c.seat), { recursive: true, force: true });
  if (c.dev) { mkdirSync(join(dir, 'state', 'headless', c.seat), { recursive: true }); writeFileSync(join(dir, 'state', 'headless', c.seat, 'dev'), ''); }
  engine = c.engine === 'unreachable' ? undefined : c.engine;
  forgetLiveEngine();
}

for (const c of CASES) {
  test(`agent-call gate: ${c.name}`, async () => {
    assert.equal(agentCallGoesToSeat({ switch_on: c.switch_on, headless: c.headless, registry_runtime: c.registry_runtime,
      dev: c.dev, engine: c.engine as any }), c.expected, 'the rule');
    layOut(c);
    const r = await (await fetch(`${base}/${c.seat}/settings`)).json() as any;
    assert.equal(r.ok, true);
    assert.equal(r.agent_calls, c.expected, 'the route, through the files');
    assert.equal(r.in_dev, c.dev, 'in_dev beside it');
  });
}

test('the settings read keeps every field it had', async () => {
  layOut(CASES[0]);
  const r = await (await fetch(`${base}/${CASES[0].seat}/settings`)).json() as any;
  for (const k of ['ok', 'agent', 'rotates', 'compact_now', 'agent_calls', 'in_dev', 'auto_compact', 'compact_at', 'show_compaction', 'history']) assert.ok(k in r, k);
});

test('a seat that fails the other checks never asks for the engine; the engine is cached', async () => {
  layOut({ ...CASES[0], switch_on: false });
  healthCalls = 0;
  await fetch(`${base}/${CASES[0].seat}/settings`);
  assert.equal(healthCalls, 0);
  layOut(CASES[0]);
  healthCalls = 0;
  await fetch(`${base}/${CASES[0].seat}/settings`);
  await fetch(`${base}/${CASES[0].seat}/settings`);
  assert.equal(healthCalls, 1, 'read once, then from the cache');
});

test('a slow engine read does not hold the settings up', async () => {
  forgetLiveEngine();
  const t0 = Date.now();
  const v = await liveEngine(50, Date.now(), () => new Promise((r) => setTimeout(() => r('relay'), 500)));
  assert.equal(v, 'unreachable');
  assert.ok(Date.now() - t0 < 400);
  forgetLiveEngine();
});
