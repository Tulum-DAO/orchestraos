/**
 * RED-first: POST /api/arturo/text carries the turn's brain and page context
 * (DEC-1790669162399904, spec v4 §1.1-1.2).
 *
 * This hop used to rebuild the body as {text, conversation_id}, so a chosen model died here.
 * It now forwards brain + context, and ONLY those known fields. It also refuses malformed
 * shapes early, with the same error names the proxy uses. The proxy stays authoritative: it
 * alone knows the model catalog, and a well-shaped but unlisted model is refused there, not here.
 *
 * Run: npm test   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { createArturoRouter, type ArturoDeps } from './arturo.js';

type Sent = { url: string; body: any };

function makeDeps(reply: { status: number; body: any } = { status: 200, body: { ok: true, reply_text: 'hi' } }) {
  const sent: Sent[] = [];
  const deps: ArturoDeps = {
    token: () => 'test-token',
    fetchJson: async (url: string, init: RequestInit) => {
      sent.push({ url, body: JSON.parse(String(init.body || '{}')) });
      return reply;
    },
    forwardStream: async () => ({ status: 500, body: {} }),
  };
  return { deps, sent };
}

async function post(deps: ArturoDeps, body: unknown) {
  const app = express();
  app.use(express.json());
  app.use('/api/arturo', createArturoRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  const payload = JSON.stringify(body);
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port, path: '/api/arturo/text', method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } }, (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, json: JSON.parse(data || '{}') }));
      });
      req.on('error', reject);
      req.end(payload);
    });
  } finally {
    server.close();
  }
}

test('a plain turn still forwards exactly {text, conversation_id}', async () => {
  const { deps, sent } = makeDeps();
  const r = await post(deps, { text: 'hello', conversation_id: 'c1' });
  assert.equal(r.status, 200);
  assert.deepEqual(sent[0].body, { text: 'hello', conversation_id: 'c1' });
});

test('brain and context are forwarded to the gateway as given', async () => {
  const { deps, sent } = makeDeps();
  const brain = { provider: 'codex', model: 'gpt-5.6-terra' };
  const context = { route: '/agent', entityKind: 'agent', entityId: 'gm', hint: 'the red dot' };
  const r = await post(deps, { text: 'hello', conversation_id: 'c1', brain, context });
  assert.equal(r.status, 200);
  assert.deepEqual(sent[0].body, { text: 'hello', conversation_id: 'c1', brain, context });
});

test('an empty model (the CLI default) is forwarded', async () => {
  const { deps, sent } = makeDeps();
  await post(deps, { text: 'hi', brain: { provider: 'claude', model: '' } });
  assert.deepEqual(sent[0].body.brain, { provider: 'claude', model: '' });
});

test('unknown keys never ride along, top level or nested', async () => {
  const { deps, sent } = makeDeps();
  await post(deps, { text: 'hi', conversation_id: 'c', metadata: { channel: 'voice' },
    brain: { provider: 'claude', model: '', strict: false },
    context: { route: '/', extra: 'x' } });
  assert.deepEqual(sent[0].body, { text: 'hi', conversation_id: 'c',
    brain: { provider: 'claude', model: '' }, context: { route: '/' } });
});

const BAD_BRAINS: Array<[unknown, string, string]> = [
  ['claude', 'bad_brain', 'brain'],
  [{ model: 'x' }, 'bad_brain', 'brain.provider'],
  [{ provider: '' }, 'bad_brain', 'brain.provider'],
  [{ provider: 'x'.repeat(41) }, 'bad_brain', 'brain.provider'],
  [{ provider: 'claude', model: '--dangerously-skip-permissions' }, 'unknown_model', 'brain.model'],
  [{ provider: 'claude', model: 'a b' }, 'unknown_model', 'brain.model'],
  [{ provider: 'claude', model: 'x'.repeat(101) }, 'unknown_model', 'brain.model'],
  [{ provider: 'claude', model: 7 }, 'unknown_model', 'brain.model'],
];

for (const [brain, error, field] of BAD_BRAINS) {
  test(`a malformed brain ${JSON.stringify(brain).slice(0, 40)} is a 400 before the gateway`, async () => {
    const { deps, sent } = makeDeps();
    const r = await post(deps, { text: 'hi', brain });
    assert.equal(r.status, 400);
    assert.equal(r.json.error, error);
    assert.equal(r.json.field, field);
    assert.equal(sent.length, 0);
  });
}

for (const context of ['x', { hint: 'no route' }, { route: '' }, { route: 'x'.repeat(301) }, { route: '/', entityKind: ['a'] }]) {
  test(`a malformed context ${JSON.stringify(context).slice(0, 30)} is a 400 before the gateway`, async () => {
    const { deps, sent } = makeDeps();
    const r = await post(deps, { text: 'hi', context });
    assert.equal(r.status, 400);
    assert.equal(r.json.error, 'bad_context');
    assert.equal(sent.length, 0);
  });
}

test("the proxy's 409 provider_unavailable reaches the browser intact", async () => {
  const body = { ok: false, error: 'provider_unavailable', provider: 'codex', reason: 'its CLI is installed but not logged in' };
  const { deps } = makeDeps({ status: 409, body });
  const r = await post(deps, { text: 'hi', brain: { provider: 'codex', model: '' } });
  assert.equal(r.status, 409);
  assert.deepEqual(r.json, body);
});

test("the proxy's 502 brain_failed keeps tools_called, so the UI can say what already ran", async () => {
  const body = { ok: false, error: 'brain_failed', provider: 'claude', model: '', tools_called: ['send_telegram'], spawned: [] };
  const { deps } = makeDeps({ status: 502, body });
  const r = await post(deps, { text: 'hi', brain: { provider: 'claude', model: '' } });
  assert.equal(r.status, 502);
  assert.deepEqual(r.json.tools_called, ['send_telegram']);
});
