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
import { createArturoRouter, GATEWAY_URL, AVATAR_SHAPES, AVATAR_COLORS, type ArturoDeps } from './arturo.js';

function makeDeps(over: Partial<ArturoDeps> = {}): ArturoDeps & { urls: string[] } {
  const urls: string[] = [];
  const deps: any = {
    enabled: () => true,
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

test('a disabled Arturo service is named, not forwarded as an unreachable gateway', async () => {
  let called = false;
  const deps = makeDeps({ enabled: () => false, fetchJson: async () => {
    called = true;
    return { status: 200, body: { ok: true } };
  } });
  const r = await get(deps, '/api/arturo/health');
  assert.equal(r.status, 503);
  assert.deepEqual(r.json, { ok: false, error: 'arturo_disabled', disabled: true });
  assert.equal(called, false, 'a disabled service must not make a gateway request');
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

// --- POST /client-log: a turn that died in the browser reports where -----------------------
test('POST /client-log keeps only the flat diagnostic fields, capped, and never message text', async () => {
  const lines: string[] = [];
  const realWarn = console.warn;
  console.warn = (...a: unknown[]) => { lines.push(a.join(' ')); };
  try {
    const r = await postSse(makeDeps(), '/api/arturo/client-log', {
      stage: 'stream_fetch_rejected', error: 'x'.repeat(500), ua: 'Safari',
      text: 'the operator said something private', reply_text: 'secret',
    });
    assert.equal(r.status, 204);
  } finally {
    console.warn = realWarn;
  }
  assert.equal(lines.length, 1);
  const rec = JSON.parse(lines[0].replace('[arturo-client] ', ''));
  assert.equal(rec.stage, 'stream_fetch_rejected');
  assert.equal(rec.error.length, 200);
  assert.equal(rec.ua, 'Safari');
  assert.equal(rec.text, undefined, 'message text must never reach the log');
  assert.equal(rec.reply_text, undefined);
});

test('a turn read-back (?turn=) survives the hop, escaped', async () => {
  const deps = makeDeps();
  await get(deps, '/api/arturo/threads/c1?turn=t_abc-123');
  assert.equal(deps.urls[0], `${GATEWAY_URL}/arturo/threads/c1?turn=t_abc-123`);
  await get(deps, '/api/arturo/threads/c1?turn=' + encodeURIComponent('a&b=c'));
  assert.equal(deps.urls[1], `${GATEWAY_URL}/arturo/threads/c1?turn=a%26b%3Dc`);
});

// --- the voice picker's pass-throughs: GET vendor / voices / voice, PUT voice ---

async function put(deps: ArturoDeps, path: string, body: unknown, headers: Record<string, string> = {}) {
  const app = express();
  app.use(express.json());
  app.use('/api/arturo', createArturoRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const payload = JSON.stringify(body);
      const req = http.request({ host: '127.0.0.1', port, path, method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload), ...headers } }, (res) => {
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

test('the voice reads forward to the gateway with the bearer, vendor kept', async () => {
  const seen: { url: string; auth: string; method: string }[] = [];
  const deps = makeDeps({
    fetchJson: async (url: string, init: RequestInit) => {
      seen.push({ url, auth: (init.headers as Record<string, string>).Authorization, method: String(init.method) });
      return { status: 200, body: { ok: true, voice_id: 'marin', title: 'Marin' } };
    },
  });
  assert.equal((await get(deps, '/api/arturo/ptt/vendor')).status, 200);
  assert.equal((await get(deps, '/api/arturo/ptt/voices?vendor=openai')).status, 200);
  const r = await get(deps, '/api/arturo/ptt/voice?vendor=openai');
  assert.deepEqual(r.json, { ok: true, voice_id: 'marin', title: 'Marin' });
  assert.deepEqual(seen.map((s) => s.url), [
    `${GATEWAY_URL}/arturo/ptt/vendor`,
    `${GATEWAY_URL}/arturo/ptt/voices?vendor=openai`,
    `${GATEWAY_URL}/arturo/ptt/voice?vendor=openai`,
  ]);
  assert.ok(seen.every((s) => s.auth === 'Bearer test-token' && s.method === 'GET'));
});

test('a vendor that is not a plain name never reaches the gateway URL', async () => {
  const deps = makeDeps();
  await get(deps, '/api/arturo/ptt/voices?vendor=openai%26x%3D1');
  assert.deepEqual(deps.urls, [`${GATEWAY_URL}/arturo/ptt/voices`]);
});

test('PUT /ptt/voice forwards only the vendor and voice id', async () => {
  let sent: { url: string; method?: string; body?: string } | null = null;
  const deps = makeDeps({
    fetchJson: async (url: string, init: RequestInit) => {
      sent = { url, method: init.method, body: String(init.body) };
      return { status: 200, body: { ok: true, vendor: 'openai', voice_id: 'cedar' } };
    },
  });
  const r = await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai', voice_id: 'cedar', by: 'someone-else' });
  assert.equal(r.status, 200);
  assert.equal(r.json.voice_id, 'cedar');
  assert.deepEqual(sent, { url: `${GATEWAY_URL}/arturo/ptt/voice`, method: 'PUT', body: '{"vendor":"openai","voice_id":"cedar"}' });
});

test('PUT /ptt/voice passes the server\'s refusal through, status and reason', async () => {
  const deps = makeDeps({
    fetchJson: async () => ({ status: 400, body: { ok: false, error: "'nova' is not an openai voice" } }),
  });
  const r = await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai', voice_id: 'nova' });
  assert.equal(r.status, 400);
  assert.deepEqual(r.json, { ok: false, error: "'nova' is not an openai voice" });
});

test('PUT /ptt/voice without a vendor and voice id is refused before the gateway', async () => {
  const deps = makeDeps();
  const r = await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai' });
  assert.equal(r.status, 400);
  assert.deepEqual(deps.urls, []);
});

test('PUT /ptt/voice: an admin may change the voice', async () => {
  const deps = makeDeps({ isAdmin: () => true });
  const r = await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai', voice_id: 'cedar' });
  assert.equal(r.status, 200);
  assert.deepEqual(deps.urls, [`${GATEWAY_URL}/arturo/ptt/voice`]);
});

test('PUT /ptt/voice: anyone else is refused with a plain reason, before the gateway', async () => {
  const deps = makeDeps({ isAdmin: () => false });
  const r = await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai', voice_id: 'cedar' });
  assert.equal(r.status, 403);
  assert.deepEqual(r.json, { ok: false, error: "Only an admin can change the assistant's voice" });
  assert.deepEqual(deps.urls, []);
});

test('the voice reads stay open to a non-admin', async () => {
  const deps = makeDeps({ isAdmin: () => false });
  for (const path of ['/api/arturo/ptt/vendor', '/api/arturo/ptt/voices?vendor=openai', '/api/arturo/ptt/voice?vendor=openai']) {
    assert.equal((await get(deps, path)).status, 200);
  }
  assert.equal(deps.urls.length, 3);
});

test("an agent's own voice: the id is passed on, checked; DELETE puts it back on the install's voice", async () => {
  const seen: { url: string; method: string; body?: string }[] = [];
  const deps = makeDeps({ isAdmin: () => true, fetchJson: async (url: string, init: RequestInit) => {
    seen.push({ url, method: String(init.method), body: init.body ? String(init.body) : undefined });
    return { status: 200, body: { ok: true } };
  } });
  assert.equal((await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai', voice_id: 'cedar', agent: 'dev-demo' })).status, 200);
  assert.equal((await get(deps, '/api/arturo/ptt/voice?vendor=openai&agent=dev-demo')).status, 200);
  assert.equal((await send(deps, 'DELETE', '/api/arturo/ptt/voice?vendor=openai&agent=dev-demo')).status, 200);
  assert.deepEqual(seen.map((x) => [x.method, x.url, x.body]), [
    ['PUT', `${GATEWAY_URL}/arturo/ptt/voice`, '{"vendor":"openai","voice_id":"cedar","agent":"dev-demo"}'],
    ['GET', `${GATEWAY_URL}/arturo/ptt/voice?vendor=openai&agent=dev-demo`, undefined],
    ['DELETE', `${GATEWAY_URL}/arturo/ptt/voice?vendor=openai&agent=dev-demo`, undefined],
  ]);
  assert.equal((await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai', voice_id: 'cedar', agent: '../x' })).status, 400);
  assert.equal((await send(deps, 'DELETE', '/api/arturo/ptt/voice?vendor=openai')).status, 400);   // no agent: nothing to clear
  assert.equal(seen.length, 3);
});
test('an agent\'s own voice: anyone else is refused a PUT or a DELETE before the gateway', async () => {
  const deps = makeDeps({ isAdmin: () => false });
  assert.equal((await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai', voice_id: 'cedar', agent: 'dev-demo' })).status, 403);
  assert.equal((await send(deps, 'DELETE', '/api/arturo/ptt/voice?vendor=openai&agent=dev-demo')).status, 403);
  assert.deepEqual(deps.urls, []);
});

test('the real role check decides when an authenticating proxy names the caller', async () => {
  const before = process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
  process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = '1';
  try {
    const deps = makeDeps();                                    // no injected check: the real one
    const viewer = await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai', voice_id: 'cedar' },
      { 'x-orchestra-user': 'demo-user', 'x-orchestra-role': 'viewer' });
    assert.equal(viewer.status, 403);
    const noRole = await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai', voice_id: 'cedar' },
      { 'x-orchestra-user': 'demo-user' });
    assert.equal(noRole.status, 403);                           // no role is never an admin
    assert.deepEqual(deps.urls, []);
    const admin = await put(deps, '/api/arturo/ptt/voice', { vendor: 'openai', voice_id: 'cedar' },
      { 'x-orchestra-user': 'demo-admin', 'x-orchestra-role': 'admin' });
    assert.equal(admin.status, 200);
  } finally {
    if (before === undefined) delete process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS;
    else process.env.ORCHESTRA_API_TRUST_IDENTITY_HEADERS = before;
  }
});

// --- avatar looks: GET /avatars, GET/PUT/DELETE /avatar ---

async function send(deps: ArturoDeps, method: string, path: string, body?: unknown) {
  const app = express();
  app.use(express.json());
  app.use('/api/arturo', createArturoRouter(deps));
  const server = app.listen(0);
  const port = (server.address() as any).port;
  try {
    return await new Promise<{ status: number; json: any }>((resolve, reject) => {
      const payload = body === undefined ? '' : JSON.stringify(body);
      const req = http.request({ host: '127.0.0.1', port, path, method,
        headers: payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {} }, (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () => resolve({ status: res.statusCode || 0, json: JSON.parse(data || '{}') }));
      });
      req.on('error', reject);
      req.end(payload || undefined);
    });
  } finally {
    server.close();
  }
}

test('avatar reads forward to the gateway, the agent escaped, open to anyone', async () => {
  const seen: { url: string; method: string }[] = [];
  const deps = makeDeps({ isAdmin: () => false, fetchJson: async (url: string, init: RequestInit) => { seen.push({ url, method: String(init.method) }); return { status: 200, body: { ok: true } }; } });
  assert.equal((await send(deps, 'GET', '/api/arturo/avatars')).status, 200);
  assert.equal((await send(deps, 'GET', '/api/arturo/avatar?agent=dev-client-a')).status, 200);
  assert.deepEqual(seen, [
    { url: `${GATEWAY_URL}/arturo/avatars`, method: 'GET' },
    { url: `${GATEWAY_URL}/arturo/avatar?agent=dev-client-a`, method: 'GET' },
  ]);
  assert.equal((await send(deps, 'GET', '/api/arturo/avatar?agent=a%2Fb')).status, 400);   // never pasted into the URL
  assert.equal(seen.length, 2);
});

test('an admin may set a look: only the checked fields are forwarded, colours normalised', async () => {
  let sent: { url: string; method?: string; body?: string } | null = null;
  const deps = makeDeps({ isAdmin: () => true, fetchJson: async (url: string, init: RequestInit) => { sent = { url, method: init.method, body: String(init.body) }; return { status: 200, body: { ok: true, chosen: true } }; } });
  const r = await send(deps, 'PUT', '/api/arturo/avatar', { agent: 'arturo', shape: 'heart', color: '#1e6be6', by: 'x', device: 'spoof' });
  assert.equal(r.status, 200);
  assert.deepEqual(sent, { url: `${GATEWAY_URL}/arturo/avatar`, method: 'PUT', body: '{"agent":"arturo","shape":"heart","color":"#1E6BE6"}' });
});

test('an admin may reset a look', async () => {
  const deps = makeDeps({ isAdmin: () => true });
  const r = await send(deps, 'DELETE', '/api/arturo/avatar?agent=arturo');
  assert.equal(r.status, 200);
  assert.deepEqual(deps.urls, [`${GATEWAY_URL}/arturo/avatar?agent=arturo`]);
});

test('anyone else is refused a PUT or a DELETE before the gateway', async () => {
  const deps = makeDeps({ isAdmin: () => false });
  const put = await send(deps, 'PUT', '/api/arturo/avatar', { agent: 'arturo', shape: 'heart' });
  const del = await send(deps, 'DELETE', '/api/arturo/avatar?agent=arturo');
  assert.equal(put.status, 403);
  assert.equal(del.status, 403);
  assert.deepEqual(put.json, { ok: false, error: "Only an admin can change an agent's look" });
  assert.deepEqual(deps.urls, []);
});

test('bad avatar values are refused before the gateway', async () => {
  const deps = makeDeps({ isAdmin: () => true });
  for (const body of [{ agent: 'arturo', shape: 'triangle' }, { agent: 'arturo', color: '#123456' }, { agent: 'arturo', color: 'red' },
    { agent: 'arturo', eyeColor: 'green' }, { agent: 'arturo' }, { agent: '../x', shape: 'star' }, { shape: 'star' }]) {
    const r = await send(deps, 'PUT', '/api/arturo/avatar', body);
    assert.equal(r.status, 400, JSON.stringify(body));
  }
  assert.equal((await send(deps, 'DELETE', '/api/arturo/avatar')).status, 400);
  assert.deepEqual(deps.urls, []);
});

test('the allowed avatar values are the dashboard core\'s', async () => {
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../../../dashboard/src/components/avatar/avatarCore.ts', import.meta.url), 'utf-8');
  const shapes = src.slice(src.indexOf('export const SHAPES'), src.indexOf('export const SHAPE_KEYS'));
  assert.deepEqual([...shapes.matchAll(/^\s+(\w+):\s+\{/gm)].map((m) => m[1]), [...AVATAR_SHAPES]);
  const bold = src.slice(src.indexOf('const BOLD'), src.indexOf('export const PALETTES'));
  assert.deepEqual(['ink', ...[...bold.matchAll(/hex: '(#[0-9A-F]{6})'/g)].map((m) => m[1])], [...AVATAR_COLORS]);
});

// --- the assistant's name: GET/PUT/DELETE /name ---

test('anyone reads the name; an admin renames it with only the name forwarded, or resets it', async () => {
  const seen: { url: string; method?: string; body?: string }[] = [];
  const fetchJson = async (url: string, init: RequestInit) => { seen.push({ url, method: init.method, body: init.body === undefined ? undefined : String(init.body) }); return { status: 200, body: { ok: true, name: 'Nova' } }; };
  assert.equal((await send(makeDeps({ isAdmin: () => false, fetchJson }), 'GET', '/api/arturo/name')).status, 200);
  const admin = makeDeps({ isAdmin: () => true, fetchJson });
  assert.equal((await send(admin, 'PUT', '/api/arturo/name', { name: 'Nova', by: 'x', device: 'spoof' })).status, 200);
  assert.equal((await send(admin, 'DELETE', '/api/arturo/name')).status, 200);
  assert.deepEqual(seen, [
    { url: `${GATEWAY_URL}/arturo/assistant/name`, method: 'GET', body: undefined },
    { url: `${GATEWAY_URL}/arturo/assistant/name`, method: 'PUT', body: '{"name":"Nova"}' },
    { url: `${GATEWAY_URL}/arturo/assistant/name`, method: 'DELETE', body: undefined },
  ]);
});

test('only an admin renames; an empty or oversized name is refused before the gateway', async () => {
  const user = makeDeps({ isAdmin: () => false });
  const put = await send(user, 'PUT', '/api/arturo/name', { name: 'Nova' });
  assert.equal(put.status, 403);
  assert.deepEqual(put.json, { ok: false, error: 'Only an admin can rename the assistant' });
  assert.equal((await send(user, 'DELETE', '/api/arturo/name')).status, 403);
  const admin = makeDeps({ isAdmin: () => true });
  for (const body of [{}, { name: '' }, { name: '   ' }, { name: 42 }, { name: 'x'.repeat(65) }]) {
    assert.equal((await send(admin, 'PUT', '/api/arturo/name', body)).status, 400, JSON.stringify(body));
  }
  assert.deepEqual([...user.urls, ...admin.urls], []);
});
