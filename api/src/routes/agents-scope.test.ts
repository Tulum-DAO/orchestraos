/**
 * The scope rule is only a fix if Express actually runs it on every router that serves
 * /api/agents/:id. agent-scope.test.ts proves the rule; this file proves the wiring.
 *
 * Read routes only. The mutating routes (kill, inject, spawn, ...) are covered by the same
 * router.param registration asserted below, but they shell out to tmux, and a test that fires
 * a real kill — even one expected to be refused — is one wiring mistake from killing a session.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { agentScopeParam } from '../lib/agent-scope.js';
import agentsRouter from './agents.js';
import chatTranscriptRouter from './chat-transcript.js';
import transcriptStreamRouter from './transcript-stream.js';
import agentSendRouter from './agent-send.js';

const ROUTERS = { agentsRouter, chatTranscriptRouter, transcriptStreamRouter, agentSendRouter };

test('every router mounted on /api/agents registers the scope param handler', () => {
  // Express keeps param handlers on router.params. A router missing from this list is a router
  // whose /:id routes are unscoped — which is exactly how 16 of them went unnoticed.
  for (const [name, r] of Object.entries(ROUTERS)) {
    const handlers = (r as unknown as { params?: Record<string, unknown[]> }).params?.id ?? [];
    assert.ok(handlers.includes(agentScopeParam), `${name} does not register agentScopeParam`);
  }
});

async function withApp(fn: (base: string) => Promise<void>) {
  const app = express();
  app.use(express.json());
  app.use('/api/agents', agentsRouter);
  app.use('/api/agents', chatTranscriptRouter);
  const server = app.listen(0);
  const { port } = server.address() as AddressInfo;
  try { await fn(`http://127.0.0.1:${port}`); } finally { server.close(); }
}

function trusted() {
  const prev = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  return () => {
    if (prev === undefined) delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
    else process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = prev;
  };
}

// A principal scoped to a client no agent is tagged for: every id is out of scope for it.
const SCOPED = {
  'x-orchestra-user': 'client1',
  'x-orchestra-role': 'viewer',
  'x-orchestra-client': 'scope-test-nobody',
};

test('end to end: a scoped principal is refused on GET /:id and GET /:id/transcript', async () => {
  const restore = trusted();
  try {
    await withApp(async (base) => {
      for (const path of ['/api/agents/gm', '/api/agents/gm/transcript']) {
        const r = await fetch(base + path, { headers: SCOPED });
        assert.equal(r.status, 404, path);
        assert.deepEqual(await r.json(), { error: "Agent 'gm' not found" }, path);
      }
    });
  } finally { restore(); }
});

test('end to end: an out-of-scope id and an unknown id return the same response', async () => {
  const restore = trusted();
  try {
    await withApp(async (base) => {
      const a = await fetch(`${base}/api/agents/gm`, { headers: SCOPED });
      const b = await fetch(`${base}/api/agents/no-such-agent-xyz`, { headers: SCOPED });
      assert.equal(a.status, b.status);
      const ja = JSON.stringify(await a.json()).replace('gm', 'X');
      const jb = JSON.stringify(await b.json()).replace('no-such-agent-xyz', 'X');
      assert.equal(ja, jb);
    });
  } finally { restore(); }
});
