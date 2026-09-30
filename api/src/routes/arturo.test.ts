/**
 * RED-first for G20's server half — the browser's path to the Arturo thread archive:
 *   GET /api/arturo/threads      -> gateway /arturo/threads      (the list)
 *   GET /api/arturo/threads/:id  -> gateway /arturo/threads/{id} (its turns)
 *
 * The route is a thin bearer-holding forwarder (the browser never sees the gateway token),
 * so what matters is the FORWARDING: the upstream path, paging surviving the hop, a thread
 * id being escaped rather than pasted into a URL, and upstream status codes passing through
 * instead of being flattened. Pure over an injected fetch — nothing here opens a socket to
 * a real gateway.
 *
 * Run: npx tsx --test src/routes/arturo.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { createArturoRouter, GATEWAY_URL, type ArturoDeps } from './arturo.js';

function makeDeps(over: Partial<ArturoDeps> = {}): ArturoDeps & { urls: string[] } {
  const urls: string[] = [];
  const deps: any = {
    token: () => 'test-token',
    fetchJson: async (url: string) => { urls.push(url); return { status: 200, body: { ok: true, threads: [] } }; },
    forwardStream: async (url: string) => { urls.push(url); return { status: 200, body: { ok: true, text: '' } }; },
    ...over,
  };
  deps.urls = urls;
  return deps;
}

async function get(deps: ArturoDeps, path: string) {
  const app = express();
  app.use(express.json());
  app.use('/api/arturo', createArturoRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port, path, method: 'GET' }, (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, json: JSON.parse(data || '{}') }));
      });
      req.on('error', reject);
      req.end();
    });
  } finally {
    server.close();
  }
}

test('GET /threads forwards to the gateway thread list and returns it', async () => {
  const deps = makeDeps({
    fetchJson: async (url: string) => {
      (deps as any).urls.push(url);
      return { status: 200, body: { ok: true, threads: [{ id: 'c1', title: 'q', updated: 2, turns: 2 }] } };
    },
  });
  const r = await get(deps, '/api/arturo/threads');
  assert.equal(r.status, 200);
  assert.equal(r.json.threads[0].id, 'c1');
  assert.equal(deps.urls[0], `${GATEWAY_URL}/arturo/threads`);
});

test('paging survives the hop to the gateway', async () => {
  const deps = makeDeps();
  await get(deps, '/api/arturo/threads?limit=10&offset=20');
  assert.match(deps.urls[0], /limit=10/);
  assert.match(deps.urls[0], /offset=20/);
});

test('a thread id is escaped, never pasted into the URL', async () => {
  const deps = makeDeps();
  await get(deps, '/api/arturo/threads/' + encodeURIComponent('a b/c'));
  assert.equal(deps.urls[0], `${GATEWAY_URL}/arturo/threads/a%20b%2Fc`);
});

test('an unknown thread keeps its 404 instead of becoming a 200', async () => {
  const deps = makeDeps({
    fetchJson: async () => ({ status: 404, body: { ok: false, error: 'not_found' } }),
  });
  const r = await get(deps, '/api/arturo/threads/nope');
  assert.equal(r.status, 404);
  assert.equal(r.json.ok, false);
});

test('an unreachable gateway is a 502, not a crash', async () => {
  const deps = makeDeps({ fetchJson: async () => { throw new Error('ECONNREFUSED'); } });
  const r = await get(deps, '/api/arturo/threads');
  assert.equal(r.status, 502);
  assert.equal(r.json.ok, false);
});

test('a missing gateway token never reaches the network', async () => {
  let called = false;
  const deps = makeDeps({ token: () => '', fetchJson: async () => { called = true; return { status: 200, body: {} }; } });
  const r = await get(deps, '/api/arturo/threads');
  assert.equal(r.status, 502);
  assert.equal(called, false);
});

// ---- item C: POST /transcribe streams the multipart clip through to the gateway -----------------
async function postRaw(deps: ArturoDeps, path: string, headers: Record<string, string>, payload: Buffer) {
  const app = express();
  app.use(express.json());
  app.use('/api/arturo', createArturoRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port, path, method: 'POST', headers }, (res) => {
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

test('POST /transcribe streams the multipart body to the gateway with the bearer and passes status through', async () => {
  let seen: any = null;
  const deps = makeDeps({
    forwardStream: async (url: string, req: any, headers: Record<string, string>) => {
      const chunks: Buffer[] = [];
      for await (const c of req) chunks.push(Buffer.from(c));
      seen = { url, headers, body: Buffer.concat(chunks).toString() };
      return { status: 422, body: { ok: false, error: 'no_speech' } };
    },
  });
  const boundary = 'xyz';
  const payload = Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="audio"; filename="c.webm"\r\nContent-Type: audio/webm\r\n\r\nOPUSBYTES\r\n--${boundary}--\r\n`);
  const r = await postRaw(deps, '/api/arturo/transcribe',
    { 'Content-Type': `multipart/form-data; boundary=${boundary}`, 'Content-Length': String(payload.length) }, payload);
  assert.equal(r.status, 422);
  assert.equal(r.json.error, 'no_speech');
  assert.equal(seen.url, `${GATEWAY_URL}/arturo/transcribe`);
  assert.equal(seen.headers.Authorization, 'Bearer test-token');
  assert.match(seen.headers['Content-Type'], /^multipart\/form-data; boundary=xyz/);
  assert.match(seen.body, /OPUSBYTES/);
});

test('POST /transcribe refuses a non-multipart body before touching the gateway', async () => {
  let called = false;
  const deps = makeDeps({ forwardStream: async () => { called = true; return { status: 200, body: {} }; } });
  const r = await postRaw(deps, '/api/arturo/transcribe', { 'Content-Type': 'application/json' }, Buffer.from('{}'));
  assert.equal(r.status, 400);
  assert.equal(called, false);
});

// --- POST /text/stream (spec §2.1): piped, never buffered ------------------------------
// The whole point is that frames leave as they arrive. A test that only checked the final
// body would pass against a buffered implementation, so these assert the SSE head and that
// the client's disconnect reaches the far end.

/** POST JSON and read the RAW body — an SSE response is not JSON. */
async function postSse(deps: ArturoDeps, path: string, body: unknown) {
  const app = express();
  app.use(express.json());
  app.use('/api/arturo', createArturoRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  const payload = JSON.stringify(body);
  try {
    return await new Promise<{ status: number; headers: Record<string, any>; text: string }>((resolve, reject) => {
      const req = http.request({
        host: '127.0.0.1', port, path, method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
      }, (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, headers: res.headers as any, text: data }));
      });
      req.on('error', reject);
      req.end(payload);
    });
  } finally {
    server.close();
  }
}

function sseUpstream(frames: string[], opts: { status?: number; contentType?: string } = {}) {
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) controller.enqueue(new TextEncoder().encode(f));
      controller.close();
    },
    cancel() { cancelled = true; },
  });
  return {
    up: {
      status: opts.status ?? 200,
      contentType: opts.contentType ?? 'text/event-stream',
      body,
      json: async () => ({ ok: false, error: 'not json here' }),
    },
    wasCancelled: () => cancelled,
  };
}

test('POST /text/stream answers with an SSE head and forwards the frames', async () => {
  const { up } = sseUpstream([
    'event: turn.start\ndata: {"turn_id":"t1"}\n\n',
    'event: text.delta\ndata: {"text":"On it"}\n\n',
    'event: turn.end\ndata: {"reply_text":"On it"}\n\n',
  ]);
  const deps = makeDeps({ pipeStream: async () => up });
  const r = await postSse(deps, '/api/arturo/text/stream', { text: 'hi' });
  assert.equal(r.status, 200);
  assert.match(r.headers['content-type'] || '', /text\/event-stream/);
  assert.equal(r.headers['x-accel-buffering'], 'no');
  assert.match(r.text, /event: turn\.start/);
  assert.match(r.text, /event: text\.delta/);
  assert.match(r.text, /"reply_text":"On it"/);
});

test('POST /text/stream passes a JSON refusal through as JSON, not as a stream', async () => {
  const { up } = sseUpstream([], { status: 400, contentType: 'application/json' });
  up.json = async () => ({ ok: false, error: 'unknown_model', field: 'brain.model' });
  const deps = makeDeps({ pipeStream: async () => up });
  const r = await postSse(deps, '/api/arturo/text/stream', { text: 'hi' });
  assert.equal(r.status, 400);
  assert.equal(JSON.parse(r.text).error, 'unknown_model');
});

test('POST /text/stream validates the brain before opening a stream', async () => {
  let opened = false;
  const deps = makeDeps({ pipeStream: async () => { opened = true; return sseUpstream([]).up; } });
  const r = await postSse(deps, '/api/arturo/text/stream',
    { text: 'hi', brain: { provider: 'claude', model: '--version' } });
  assert.equal(r.status, 400);
  assert.equal(opened, false, 'a bad request must never reach the gateway');
});

test('POST /text/stream requires text, like /text', async () => {
  const deps = makeDeps({ pipeStream: async () => sseUpstream([]).up });
  const r = await postSse(deps, '/api/arturo/text/stream', { text: '   ' });
  assert.equal(r.status, 400);
});

// --- POST /text/prewarm: start the warm CLI before the first message ------------------------
test('POST /text/prewarm forwards the conversation and the chosen brain', async () => {
  const bodies: any[] = [];
  const deps = makeDeps({
    fetchJson: async (url: string, init: any) => {
      bodies.push({ url, body: JSON.parse(init.body) });
      return { status: 200, body: { ok: true, warmed: true } };
    },
  });
  const r = await postSse(deps, '/api/arturo/text/prewarm',
    { conversation_id: 'c1', brain: { provider: 'claude', model: 'claude-opus-5' } });
  assert.equal(r.status, 200);
  assert.match(bodies[0].url, /\/arturo\/text\/prewarm$/);
  assert.deepEqual(bodies[0].body, { conversation_id: 'c1', brain: { provider: 'claude', model: 'claude-opus-5' } });
});

test('POST /text/prewarm needs a conversation — there is nothing to warm without one', async () => {
  const deps = makeDeps();
  const r = await postSse(deps, '/api/arturo/text/prewarm', {});
  assert.equal(r.status, 400);
});

test('POST /text/prewarm refuses a hostile model id before it reaches a spawn', async () => {
  let called = false;
  const deps = makeDeps({ fetchJson: async () => { called = true; return { status: 200, body: {} }; } });
  const r = await postSse(deps, '/api/arturo/text/prewarm',
    { conversation_id: 'c1', brain: { provider: 'claude', model: '--version' } });
  assert.equal(r.status, 400);
  assert.equal(called, false);
});
