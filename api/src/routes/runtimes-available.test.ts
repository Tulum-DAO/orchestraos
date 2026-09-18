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
import {
  createRuntimesAvailableRouter,
  probeAll,
  salvageCliJsonAnswer,
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
    loadModelCatalog: (p) => (p.model_catalog.static || []).map((m) => ({ id: m.id, label: m.label, capabilities: m.capabilities })),
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
    loadModelCatalog: () => [],
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
    loadModelCatalog: () => [],
    now: () => 1000, // frozen clock => cache never expires between calls
  };
  const { router } = createRuntimesAvailableRouter(deps);
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
    loadModelCatalog: () => [],
    now: () => 1000,
  };
  const { router } = createRuntimesAvailableRouter(deps);
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
