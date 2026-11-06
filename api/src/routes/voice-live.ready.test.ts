/**
 * GET /api/voice/live/ready — will a call start? Read-only: the page asks before offering Call. These run the real
 * route with its real deps against a temp data dir and a stand-in gateway on loopback, so nothing reaches a real
 * gateway, Arturo or a voice vendor, and no call or realtime session is ever opened.
 *
 * Run: npx tsx --test src/routes/voice-live.ready.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const SAMPLE_KEY = 'sample-voice-key-0123456789';
const SAMPLE_BEARER = 'sample-gateway-bearer-9876';

const dir = testTmpDir('voice-ready-');
mkdirSync(join(dir, 'state'));
const tokenFile = join(dir, 'state', 'watch-gateway-token');
const secrets = join(dir, '.env.secrets');

// The stand-in gateway: answers /arturo/health with whatever engine a test sets, or fails.
let engine: unknown = 'gemini';
let healthUp = true;
let seenAuth: string | undefined;
const gateway = http.createServer((req, res) => {
  seenAuth = req.headers.authorization;
  if (!healthUp || req.url !== '/arturo/health') { res.writeHead(503); res.end(); return; }
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ live: engine !== null, live_engine: engine }));
});
await new Promise<void>((r) => gateway.listen(0, '127.0.0.1', () => r()));

process.env.ORCHESTRA_DIR = dir;
process.env.WATCH_GATEWAY_TOKEN_FILE = tokenFile;
process.env.WATCH_GATEWAY_URL = `http://127.0.0.1:${(gateway.address() as AddressInfo).port}`;
delete process.env.GEMINI_API_KEY;

const { createVoiceLiveReadyRouter, NO_KEY, NOT_SET_UP } = await import('./voice-live.js');

const app = express();
app.use('/api/voice', createVoiceLiveReadyRouter());
const server = app.listen(0);
const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
after(() => { server.close(); gateway.close(); });

/** The body, as text too, and everything the route wrote to the console while answering. */
async function ready() {
  const logged: string[] = [];
  const orig = { log: console.log, warn: console.warn, error: console.error };
  for (const k of ['log', 'warn', 'error'] as const) console[k] = (...a: unknown[]) => { logged.push(a.map(String).join(' ')); };
  try {
    const r = await fetch(`${base}/api/voice/live/ready?agent=dev-demo`);
    const text = await r.text();
    return { status: r.status, body: JSON.parse(text), text, logged: logged.join('\n') };
  } finally { Object.assign(console, orig); }
}

function setup({ bearer = true, key = false }: { bearer?: boolean; key?: boolean }) {
  rmSync(tokenFile, { force: true });
  rmSync(secrets, { force: true });
  if (bearer) writeFileSync(tokenFile, SAMPLE_BEARER);
  if (key) writeFileSync(secrets, `OTHER=1\nGEMINI_API_KEY=${SAMPLE_KEY}\n`);
}

function neverLeaks(r: { text: string; logged: string }) {
  for (const secret of [SAMPLE_KEY, SAMPLE_KEY.slice(0, 8), SAMPLE_KEY.slice(-8), SAMPLE_BEARER]) {
    assert.ok(!r.text.includes(secret), 'no key or part of one in the body');
    assert.ok(!r.logged.includes(secret), 'nor in the log');
  }
}

test('a key present: ok, and the key is not in the body', async () => {
  setup({ key: true });
  engine = 'gemini'; healthUp = true;
  const r = await ready();
  assert.equal(r.status, 200);
  assert.deepEqual(r.body, { ok: true });
  assert.equal(seenAuth, `Bearer ${SAMPLE_BEARER}`, 'the gateway is asked as the API, not the browser');
  neverLeaks(r);
});

test('no key: not ok, with a plain reason', async () => {
  setup({ key: false });
  engine = null; healthUp = true;
  const r = await ready();
  assert.deepEqual(r.body, { ok: false, reason: NO_KEY });
  assert.equal(NO_KEY, 'No voice key is set up');
  neverLeaks(r);
});

test('the GPT-Live relay starts a call with no Gemini key', async () => {
  setup({ key: false });
  engine = 'relay'; healthUp = true;
  assert.deepEqual((await ready()).body, { ok: true });
});

test('with Arturo unreachable the gateway runs Gemini, so only a Gemini key makes it ready', async () => {
  healthUp = false;
  setup({ key: true });
  const r = await ready();
  assert.deepEqual(r.body, { ok: true });
  neverLeaks(r);
  setup({ key: false });
  assert.deepEqual((await ready()).body, { ok: false, reason: NO_KEY });
});

test('no gateway bearer: the bridge would refuse, so not ok', async () => {
  setup({ bearer: false, key: true });
  engine = 'gemini'; healthUp = true;
  const r = await ready();
  assert.deepEqual(r.body, { ok: false, reason: NOT_SET_UP });
  neverLeaks(r);
});
