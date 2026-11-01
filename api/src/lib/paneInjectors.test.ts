/**
 * Every API path that types into a seat's tmux pane, checked for seats that run headless (whose pane holds the
 * supervisor, not the CLI):
 *  - text for the seat goes through the gateway's /agent-message (lib/gatewayMessage.ts): tasks, cross-machine;
 *  - pane control (keys, typing into a prompt, the gm sign-in flow) stays a keystroke and is refused on a headless
 *    seat outside Dev, where it would land in the supervisor's pane;
 *  - an inventory of every send-keys left in api/src, so a new one is a decision, not an accident.
 * A stand-in gateway answers on loopback; no real pane, gateway or tmux session is touched.
 *
 * Run: npx tsx --test src/lib/paneInjectors.test.ts   (from api/)
 */
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';

const dir = mkdtempSync(join(tmpdir(), 'pane-injectors-'));
mkdirSync(join(dir, 'state', 'headless', 'dev-demo'), { recursive: true });
writeFileSync(join(dir, 'registry.json'), JSON.stringify({ agents: { 'pm-demo': { tmux_session: 'pm-demo' }, 'dev-demo': {}, gm: {} } }));
const headlessFile = join(dir, 'state', 'headless-seats.json');
const writeHeadless = (seats: string[]) => writeFileSync(headlessFile, JSON.stringify({
  seats: Object.fromEntries(seats.map((s) => [s, { headless: true, runtime: 'claude' }])),
}));
writeHeadless(['pm-demo', 'gm']);
const tokenFile = join(dir, 'state', 'watch-gateway-token');
writeFileSync(tokenFile, 'sample-bearer\n');

// The stand-in gateway: records each /agent-message and answers delivered.
const seen: { headers: http.IncomingHttpHeaders; body: any; at: number }[] = [];
const gw = http.createServer((req, res) => {
  let raw = '';
  req.on('data', (c) => { raw += c; });
  req.on('end', () => {
    seen.push({ headers: req.headers, body: JSON.parse(raw || '{}'), at: performance.now() });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, delivered: true, state: 'headless', msg_state: 'handed_over' }));
  });
});
await new Promise<void>((r) => gw.listen(0, '127.0.0.1', () => r()));
process.env.ORCHESTRA_DIR = dir;
process.env.HEADLESS_SEATS_FILE = headlessFile;
process.env.WATCH_GATEWAY_TOKEN_FILE = tokenFile;
process.env.WATCH_GATEWAY_URL = `http://127.0.0.1:${(gw.address() as AddressInfo).port}`;
after(() => gw.close());

const { gatewayAgentMessage } = await import('./gatewayMessage.js');
const { paneHoldsSupervisor } = await import('./paneGuard.js');
const { injectToRemoteAgent } = await import('../services/cross-machine.js');

test('the gateway helper posts the text with the bearer, the actor and the source', async () => {
  seen.length = 0;
  const r = await gatewayAgentMessage('pm-demo', 'Sample task', { actor: 'automation:tasks', source: 'mail' });
  assert.deepEqual([r.ok, r.delivered, r.status], [true, true, 200]);
  assert.equal(seen[0].headers.authorization, 'Bearer sample-bearer');
  assert.equal(seen[0].headers['x-actor'], 'automation:tasks');
  assert.deepEqual(seen[0].body, { session: 'pm-demo', text: 'Sample task', source: 'mail' });
});

test('no token or no gateway: a plain failure, never a throw', async () => {
  const none = await gatewayAgentMessage('pm-demo', 'x', { actor: 'automation:tasks' },
    { fetch, token: () => '', url: () => 'http://127.0.0.1:9' });
  assert.equal(none.ok, false);
  assert.match(String(none.error), /token/);
  const down = await gatewayAgentMessage('pm-demo', 'x', { actor: 'automation:tasks', timeoutMs: 2000 },
    { fetch, token: () => 't', url: () => 'http://127.0.0.1:9' });
  assert.equal(down.ok, false);
  assert.ok(down.error);
});

test('a machine sender on this box reaches a headless seat through the gateway, not its pane', async () => {
  seen.length = 0;
  const r = await injectToRemoteAgent('pm-demo', 'Example note', { agents: { 'pm-demo': { tmux_session: 'pm-demo', machine: 'vps' } } });
  assert.deepEqual(r, { success: true, output: 'injected' });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].body.session, 'pm-demo');
  assert.equal(seen[0].headers['x-actor'], 'automation:cross-machine');
});

test('the reroute adds no delay: the gateway round trip, against spawning tmux for the old keystrokes', async () => {
  const runs = 20;
  const gwMs: number[] = [];
  for (let i = 0; i < runs; i++) {
    const t0 = performance.now();
    await gatewayAgentMessage('pm-demo', 'Sample', { actor: 'automation:tasks', source: 'mail' });
    gwMs.push(performance.now() - t0);
  }
  // The old path spawned `tmux send-keys`; `tmux -V` is the same spawn without touching any pane.
  const spawnMs: number[] = [];
  for (let i = 0; i < runs; i++) {
    const t0 = performance.now();
    await new Promise<void>((r) => execFile('tmux', ['-V'], () => r()));
    spawnMs.push(performance.now() - t0);
  }
  const median = (a: number[]) => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)];
  console.log(`[measure] gateway /agent-message median ${median(gwMs).toFixed(2)} ms; tmux spawn median ${median(spawnMs).toFixed(2)} ms (n=${runs})`);
  // Both callers fire and forget, so neither ever waits on it; the bound only catches a wrong turn (a retry, a sleep).
  assert.ok(median(gwMs) < 50, `gateway round trip ${median(gwMs)} ms`);
});

test('a headless seat outside Dev: its pane is the supervisor\'s; in Dev, or not headless, it is the CLI\'s', () => {
  assert.equal(paneHoldsSupervisor('pm-demo'), true);
  assert.equal(paneHoldsSupervisor('dev-demo', new Set(['dev-demo']), () => true), false, 'in Dev the pane is the TUI');
  assert.equal(paneHoldsSupervisor('dev-demo'), false, 'not headless');
});

async function withApp(mount: (app: express.Express) => Promise<void> | void, fn: (base: string) => Promise<void>) {
  const app = express();
  app.use(express.json());
  await mount(app);
  const server = app.listen(0);
  try { await fn(`http://127.0.0.1:${(server.address() as AddressInfo).port}`); } finally { server.close(); }
}
const post = async (url: string, body: unknown) => {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return { status: r.status, body: await r.json() as any };
};

test('keys and prompt typing for a headless seat outside Dev are refused before any keystroke', async () => {
  const { default: agentsRouter } = await import('../routes/agents.js');
  await withApp((app) => { app.use('/api/agents', agentsRouter); }, async (base) => {
    for (const key of ['Enter', 'C-c', '1']) {
      const r = await post(`${base}/api/agents/pm-demo/key`, { key });
      assert.equal(r.status, 409, key);
      assert.equal(r.body.error, 'headless');
    }
    const raw = await post(`${base}/api/agents/pm-demo/inject-raw`, { text: 'Sample' });
    assert.equal(raw.status, 409);
    assert.match(raw.body.detail, /Open Dev/);
  });
});

test('the gm sign-in flow is refused while gm runs headless outside Dev', async () => {
  const { default: authRouter } = await import('../routes/auth.js');
  await withApp((app) => { app.use('/api/system/vps-auth', authRouter); }, async (base) => {
    for (const [path, body] of [['send-login', {}], ['select-option', {}], ['submit-code', { code: 'sample-code' }]] as const) {
      const r = await post(`${base}/api/system/vps-auth/${path}`, body);
      assert.equal(r.status, 409, path);
      assert.equal(r.body.error, 'headless');
    }
  });
});

test('every send-keys left in api/src is pane control or another machine\'s seat', () => {
  const src = join(dirname(fileURLToPath(import.meta.url)), '..');
  const files: string[] = [];
  const walk = (d: string) => {
    for (const n of readdirSync(d)) {
      const p = join(d, n);
      if (statSync(p).isDirectory()) walk(p);
      else if (n.endsWith('.ts') && !n.endsWith('.test.ts')) files.push(p);
    }
  };
  walk(src);
  const hits: string[] = [];
  for (const f of files) {
    readFileSync(f, 'utf-8').split('\n').forEach((line) => {
      if (/send-keys/.test(line) && !/^\s*(\/\/|\*)/.test(line)) hits.push(relative(src, f));
    });
  }
  const counts = hits.reduce<Record<string, number>>((m, f) => ({ ...m, [f]: (m[f] || 0) + 1 }), {});
  assert.deepEqual(counts, {
    // /:id/key (Mac + this box) and /:id/inject-raw: pane control, refused on a supervisor's pane; the Mac /inject
    // fallback (sendToTmux, twice, and its extra Enter): a machine the local gateway cannot reach.
    'routes/agents.ts': 6,
    // gm's sign-in: the CLI's own TUI, refused on a supervisor's pane.
    'routes/auth.ts': 3,
    // Another machine's seat over SSH: out of the local gateway's reach.
    'services/cross-machine.ts': 1,
  });
});
