/**
 * B2 RED-first: proves GET /api/runtimes/available and POST .../refresh
 * against a FAKE providers.json + mocked probes (never touches real
 * filesystem/CLIs) — installed, authed, unverified, and self-heal-on-refresh.
 *
 * Run: npx tsx --test src/routes/runtimes-available.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  createRuntimesAvailableRouter,
  defaultPublishLiveCatalog,
  probeAll,
  salvageCliJsonAnswer,
  defaultProbeAuth,
  type ProbeDeps,
  type ProviderConfig,
  type AuthResult,
} from './runtimes-available.js';

function fakeProvider(overrides: Partial<ProviderConfig> = {}): ProviderConfig {
  return {
    id: 'claude',
    label: 'Claude',
    logo_svg: '<svg/>',
    cli: 'claude',
    aliases: [],
    detect: { cmd: 'which claude' },
    auth_probe: { kind: 'cli-json', cmd: 'claude auth status', success_key: 'loggedIn' },
    model_catalog: {
      source: 'static',
      static: [
        {
          id: 'claude-sonnet-5',
          label: 'Sonnet 5',
          capabilities: { text: true, image: true, audio: false, video: false, context_window: 200000 },
        },
      ],
    },
    ...overrides,
  };
}

/**
 * Inert cache IO. These tests mock the probes and must never touch the real filesystem
 * (see the header), so they must not read or write the persisted response cache either —
 * a seeded router answers from disk and never probes, which is precisely what the
 * caching tests below are trying to observe.
 */
function noDisk() {
  return { readCache: () => null, writeCache: () => {} };
}

function makeFakeDeps(opts: {
  providers: ProviderConfig[];
  installed: Record<string, boolean>;
  auth: Record<string, AuthResult>;
  nowSeq?: number[];
}): ProbeDeps {
  let call = 0;
  const nowSeq = opts.nowSeq || [];
  return {
    loadProviders: () => opts.providers,
    isInstalled: (p) => opts.installed[p.id] ?? false,
    probeAuth: (p) => opts.auth[p.id] ?? { authed: 'unverified', auth_reason: 'no-fixture' },
    loadModelCatalog: (p) => ({
      models: (p.model_catalog.static || []).map((m) => ({ id: m.id, label: m.label, capabilities: m.capabilities })),
      valid_ids: (p.model_catalog.static || []).map((m) => m.id),
      source: 'static' as const,
    }),
    publishLiveCatalog: () => {},
    now: () => (nowSeq.length ? nowSeq[Math.min(call++, nowSeq.length - 1)] : Date.now()),
  };
}

test('probeAll: installed + authed provider reports full model list', () => {
  const deps = makeFakeDeps({
    providers: [fakeProvider()],
    installed: { claude: true },
    auth: { claude: { authed: true } },
  });
  const result = probeAll(deps);
  assert.equal(result.providers.length, 1);
  assert.equal(result.providers[0].installed, true);
  assert.equal(result.providers[0].authed, true);
  assert.equal(result.providers[0].models.length, 1);
  assert.equal(result.providers[0].models[0].id, 'claude-sonnet-5');
  assert.equal(result.ttl_s, 300);
});

test('probeAll: not-installed provider is LOUD false, never probed for auth', () => {
  let authProbed = false;
  const deps: ProbeDeps = {
    loadProviders: () => [fakeProvider({ id: 'codex', cli: 'codex' })],
    isInstalled: () => false,
    probeAuth: () => {
      authProbed = true;
      return { authed: true };
    },
    loadModelCatalog: () => ({ models: [], valid_ids: [], source: 'static' as const }),
    publishLiveCatalog: () => {},
    now: () => 1000,
  };
  const result = probeAll(deps);
  assert.equal(result.providers[0].installed, false);
  assert.equal(result.providers[0].authed, false);
  assert.equal(result.providers[0].auth_reason, 'not-installed');
  assert.equal(authProbed, false, 'auth probe must be skipped when not installed');
});

test('probeAll: unverified auth state is reported LOUD, never coerced', () => {
  const deps = makeFakeDeps({
    providers: [fakeProvider({ id: 'gemini', cli: 'agy' })],
    installed: { gemini: true },
    auth: { gemini: { authed: 'unverified', auth_reason: 'auth-file-unparseable-expiry' } },
  });
  const result = probeAll(deps);
  assert.equal(result.providers[0].authed, 'unverified');
  assert.equal(result.providers[0].auth_reason, 'auth-file-unparseable-expiry');
});

test('probeAll: expired-token provider reports authed:false with reason', () => {
  const deps = makeFakeDeps({
    providers: [fakeProvider({ id: 'gemini', cli: 'agy' })],
    installed: { gemini: true },
    auth: { gemini: { authed: false, auth_reason: 'token-expired' } },
  });
  const result = probeAll(deps);
  assert.equal(result.providers[0].authed, false);
  assert.equal(result.providers[0].auth_reason, 'token-expired');
});

async function withServer(router: express.Router, fn: (base: string) => Promise<void>) {
  const app = express();
  app.use('/api/runtimes', router);
  const server = http.createServer(app);
  await new Promise<void>((r) => server.listen(0, r));
  const port = (server.address() as { port: number }).port;
  try {
    await fn(`http://127.0.0.1:${port}/api/runtimes`);
  } finally {
    await new Promise<void>((r) => server.close(() => r()));
  }
}

test('GET /available caches within TTL (probeAuth called once for two GETs)', async () => {
  let probeCount = 0;
  const deps: ProbeDeps = {
    loadProviders: () => [fakeProvider()],
    isInstalled: () => true,
    probeAuth: () => {
      probeCount += 1;
      return { authed: true };
    },
    loadModelCatalog: () => ({ models: [], valid_ids: [], source: 'static' as const }),
    publishLiveCatalog: () => {},
    now: () => 1000, // frozen clock => cache never expires between calls
  };
  const { router } = createRuntimesAvailableRouter(deps, noDisk());
  await withServer(router, async (base) => {
    const r1 = await fetch(`${base}/available`);
    const r2 = await fetch(`${base}/available`);
    assert.equal(r1.status, 200);
    assert.equal(r2.status, 200);
    assert.equal(probeCount, 1, 'second GET within TTL must be served from cache');
  });
});

test('POST /available/refresh self-heals: forces re-probe even within TTL', async () => {
  let probeCount = 0;
  let authed = false; // simulates: was down, refresh should pick up the flip
  const deps: ProbeDeps = {
    loadProviders: () => [fakeProvider()],
    isInstalled: () => true,
    probeAuth: () => {
      probeCount += 1;
      return { authed };
    },
    loadModelCatalog: () => ({ models: [], valid_ids: [], source: 'static' as const }),
    publishLiveCatalog: () => {},
    now: () => 1000,
  };
  const { router } = createRuntimesAvailableRouter(deps, noDisk());
  await withServer(router, async (base) => {
    const r1 = await fetch(`${base}/available`);
    const body1 = (await r1.json()) as { providers: { authed: boolean }[] };
    assert.equal(body1.providers[0].authed, false);
    assert.equal(probeCount, 1);

    // Simulate the underlying state healing (e.g. the operator re-logs-in) — a plain
    // GET must still serve stale cache...
    authed = true;
    const r2 = await fetch(`${base}/available`);
    const body2 = (await r2.json()) as { providers: { authed: boolean }[] };
    assert.equal(body2.providers[0].authed, false, 'still cached, stale');
    assert.equal(probeCount, 1);

    // ...but the self-heal refresh endpoint forces a fresh probe.
    const r3 = await fetch(`${base}/available/refresh`, { method: 'POST' });
    const body3 = (await r3.json()) as { providers: { authed: boolean }[] };
    assert.equal(body3.providers[0].authed, true);
    assert.equal(probeCount, 2);

    const r4 = await fetch(`${base}/available`);
    const body4 = (await r4.json()) as { providers: { authed: boolean }[] };
    assert.equal(body4.providers[0].authed, true, 'GET now serves the healed cache');
    assert.equal(probeCount, 2);
  });
});

test('the real config/providers.json loads and matches the ProviderConfig shape', async () => {
  const { defaultLoadProviders } = await import('./runtimes-available.js');
  const providers = defaultLoadProviders();
  assert.ok(providers.length >= 3, 'expected claude/gemini/codex at minimum');
  for (const p of providers) {
    assert.ok(p.id && p.label && p.cli, `provider missing core fields: ${JSON.stringify(p)}`);
    assert.ok(['cli-json', 'file-json-key', 'file-json-expiry'].includes(p.auth_probe.kind));
  }
});

// A logged-out CLI exits non-zero WITH its JSON (`claude auth status` -> {"loggedIn":false},
// status 1). That is an ANSWER, not a probe failure: falling through to the stale
// ~/.claude.json oauthAccount fallback reported authed:true for a logged-out CLI (found by
// effect in a container, 2026-09-18). Twin of orchestra_cli/tests/test_runtime_probe.py.
test('salvageCliJsonAnswer believes a non-zero probe that printed its JSON', () => {
  assert.deepEqual(salvageCliJsonAnswer('{"loggedIn": false}'), { authed: false, auth_reason: 'loggedIn=false' });
  assert.deepEqual(salvageCliJsonAnswer('{"loggedIn": true}'), { authed: true });
  assert.deepEqual(salvageCliJsonAnswer('{"ok": true}', 'ok'), { authed: true });
});

test('salvageCliJsonAnswer returns null when there is nothing usable (fallback still runs)', () => {
  assert.equal(salvageCliJsonAnswer(''), null);
  assert.equal(salvageCliJsonAnswer('   '), null);
  assert.equal(salvageCliJsonAnswer('command not found'), null);
  assert.equal(salvageCliJsonAnswer('{"loggedIn": "yes"}'), null);   // not a boolean: not an answer
});

// WIRING, not just the helper: a real CLI on PATH that prints its JSON and exits 1 (what a
// logged-out `claude auth status` does) must be believed, with a fallback file present that
// would otherwise say "authed". Red before the catch-path fix — the helper alone was not.
test('defaultProbeAuth believes a logged-out CLI that exits non-zero, over the fallback file', () => {
  const dir = mkdtempSync(join(tmpdir(), 'probe-cli-'));
  const bin = join(dir, 'fakecli');
  writeFileSync(bin, '#!/bin/sh\necho \'{"loggedIn": false}\'\nexit 1\n', { mode: 0o755 });
  const fallbackFile = join(dir, 'auth.json');
  writeFileSync(fallbackFile, JSON.stringify({ oauthAccount: { email: 'stale@example.com' } }));
  const prevPath = process.env.PATH;
  process.env.PATH = `${dir}:${prevPath}`;
  try {
    const out = defaultProbeAuth(fakeProvider({
      cli: 'fakecli',
      auth_probe: {
        kind: 'cli-json', cmd: 'fakecli auth status', success_key: 'loggedIn',
        fallback: { kind: 'file-json-key', path: fallbackFile, key: 'oauthAccount' },
      },
    }));
    assert.deepEqual(out, { authed: false, auth_reason: 'loggedIn=false' });
  } finally {
    process.env.PATH = prevPath;
    rmSync(dir, { recursive: true, force: true });
  }
});


// --- the probed catalog must reach the proxy that validates picks ----------
// The proxy only allowed providers.json static ids, so a probed-but-not-static model was
// shown in the picker and 502'd when picked. probeAll publishes what it probed.

test('probeAll publishes ONLY live-probed ids, and skips a static fallback', () => {
  const published: Record<string, string[]>[] = [];
  const deps: ProbeDeps = {
    loadProviders: () => [fakeProvider({ id: 'claude' }), fakeProvider({ id: 'codex', cli: 'codex' })],
    isInstalled: () => true,
    probeAuth: () => ({ authed: true }),
    loadModelCatalog: (p) =>
      p.id === 'claude'
        ? { models: [{ id: 'live-1', label: 'Live 1', capabilities: { text: true, image: false, audio: false, video: false, context_window: null } }], valid_ids: ['live-1', 'collapsed-alias'], source: 'probe' as const }
        : { models: [{ id: 'static-1', label: 'Static 1', capabilities: { text: true, image: false, audio: false, video: false, context_window: null } }], valid_ids: ['static-1'], source: 'static-fallback' as const, reason: 'probe-failed:ENOENT' },
    publishLiveCatalog: (rows) => {
      const live: Record<string, string[]> = {};
      for (const r of rows) if (r.model_catalog_source === 'probe') live[r.id] = r.model_catalog_valid_ids;
      published.push(live);
    },
    now: () => 1,
  };
  const res = probeAll(deps);
  // the collapsed alias is published for VALIDATION even though it is not offered
  assert.deepEqual(published[0], { claude: ['live-1', 'collapsed-alias'] });
  assert.equal(res.providers[1].model_catalog_source, 'static-fallback');
  assert.match(res.providers[1].model_catalog_reason || '', /probe-failed/);
});

test('publishing the live catalog writes atomically and survives an unwritable path', () => {
  const dir = mkdtempSync(join(tmpdir(), 'orch-live-'));
  const target = join(dir, 'nested', 'model-catalog-live.json');
  const row = {
    id: 'claude', label: 'Claude', logo_svg: '', installed: true, authed: true as const,
    models: [{ id: 'm1', label: 'M1', capabilities: { text: true, image: false, audio: false, video: false, context_window: null } }],
    model_catalog_source: 'probe' as const,
    model_catalog_valid_ids: ['m1'],
  };
  defaultPublishLiveCatalog([row], target);
  assert.deepEqual(JSON.parse(readFileSync(target, 'utf-8')).providers, { claude: ['m1'] });
  // a path that cannot be written is a cache miss, never a thrown probe
  // (a plain FILE standing where a directory would have to be: ENOTDIR)
  const blocker = join(dir, 'blocker');
  writeFileSync(blocker, 'not a directory');
  assert.doesNotThrow(() => defaultPublishLiveCatalog([row], join(blocker, 'live.json')));
  rmSync(dir, { recursive: true, force: true });
});

// Other routes need "what is installed and signed in" too. They must share THIS cache:
// probing is now a live CLI spawn, and paying for it per request turned opening a terminal
// into a 3.5s call that 502'd behind a proxy.

test('getCached shares the route cache — a GET then a getCached is ONE probe', async () => {
  let probes = 0;
  const deps: ProbeDeps = {
    loadProviders: () => [fakeProvider()],
    isInstalled: () => true,
    probeAuth: () => { probes += 1; return { authed: true }; },
    loadModelCatalog: () => ({ models: [], valid_ids: [], source: 'static' as const }),
    publishLiveCatalog: () => {},
    now: () => 1000,
  };
  const { router, getCached } = createRuntimesAvailableRouter(deps, noDisk());
  const app = express();
  app.use('/api/runtimes', router);
  const server = app.listen(0);
  const port = (server.address() as { port: number }).port;
  await new Promise<void>((r) => { http.get(`http://127.0.0.1:${port}/api/runtimes/available`, (res) => { res.resume(); res.on('end', () => r()); }); });
  getCached();
  getCached();
  server.close();
  assert.equal(probes, 1, 'the cache must be shared, not per-caller');
});

test('withModels:false never asks a CLI for its catalog, and never publishes one', () => {
  let catalogCalls = 0;
  let published = 0;
  const deps: ProbeDeps = {
    loadProviders: () => [fakeProvider()],
    isInstalled: () => true,
    probeAuth: () => ({ authed: true }),
    loadModelCatalog: () => { catalogCalls += 1; return { models: [], valid_ids: [], source: 'probe' as const }; },
    publishLiveCatalog: () => { published += 1; },
    now: () => 1,
  };
  const res = probeAll(deps, { withModels: false });
  assert.equal(catalogCalls, 0);
  assert.equal(published, 0, 'an empty catalog must never overwrite a real one');
  assert.equal(res.providers[0].authed, true, 'auth is still answered');
  assert.equal(res.providers[0].model_catalog_source, 'not-probed');
});

test('the live catalog publishes each login\'s default model, and only a probed one', () => {
  const dir = mkdtempSync(join(tmpdir(), 'orch-live-'));
  const target = join(dir, 'model-catalog-live.json');
  const cap = { text: true, image: false, audio: false, video: false, context_window: null };
  const base = { label: '', logo_svg: '', installed: true, authed: true as const };
  defaultPublishLiveCatalog([
    { ...base, id: 'codex', models: [{ id: 'gpt-6.1-sol', label: 'x', capabilities: cap }],
      model_catalog_source: 'probe' as const, model_catalog_valid_ids: ['gpt-6.1-sol'],
      model_catalog_default: 'gpt-6.1-sol' },
    { ...base, id: 'gemini', models: [{ id: 'g', label: 'g', capabilities: cap }],
      model_catalog_source: 'probe' as const, model_catalog_valid_ids: ['g'] },
  ], target);
  assert.deepEqual(JSON.parse(readFileSync(target, 'utf-8')).defaults, { codex: 'gpt-6.1-sol' });
});

test('probeAll carries the probed default model onto the provider row', () => {
  const deps: ProbeDeps = {
    loadProviders: () => [fakeProvider({ id: 'codex', cli: 'codex' })],
    isInstalled: () => true,
    probeAuth: () => ({ authed: true }),
    loadModelCatalog: () => ({ models: [{ id: 'gpt-6.1-sol', label: 'x', capabilities: { text: true, image: false, audio: false, video: false, context_window: null } }], valid_ids: ['gpt-6.1-sol'], source: 'probe' as const, default_model: 'gpt-6.1-sol' }),
    publishLiveCatalog: () => {},
    now: () => 1,
  };
  assert.equal(probeAll(deps).providers[0].model_catalog_default, 'gpt-6.1-sol');
});
