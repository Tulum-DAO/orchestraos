/**
 * RED-first: the model picker must never block on a provider probe.
 *
 * The picker's catalog probe costs ~3.4s measured on staging, while a warm cache
 * answers in 6ms. Before this change the cache was in-process only with a hard
 * 300s TTL and a SYNCHRONOUS miss, so three situations made a user wait:
 *   - every API restart (the cache died with the process),
 *   - every 300s expiry (the first caller after it paid the full probe),
 *   - a cold boot with no warm-up at all.
 *
 * These tests pin the fix: seed from disk at construction, serve stale while
 * revalidating in the background, persist every successful probe, and refuse to
 * trust a disk cache older than the 24h ceiling.
 *
 * Run: npx tsx --test src/routes/runtimes-available-warmcache.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  createRuntimesAvailableRouter,
  DISK_CACHE_MAX_AGE_MS,
  type ProbeDeps,
  type ProviderConfig,
  type RuntimesAvailableResponse,
} from './runtimes-available.js';

function provider(): ProviderConfig {
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
  };
}

/** Counts probes so a test can prove one did NOT happen. */
function countingDeps(now: () => number, onProbe: () => void): ProbeDeps {
  return {
    loadProviders: () => { onProbe(); return [provider()]; },
    isInstalled: () => true,
    probeAuth: () => ({ authed: true }),
    loadModelCatalog: () => ({ models: [], source: 'static', valid_ids: [], default: null }),
    publishLiveCatalog: () => {},
    now,
  };
}

function diskValue(probedAt: number): RuntimesAvailableResponse {
  return { providers: [], probed_at: probedAt, ttl_s: 300 } as RuntimesAvailableResponse;
}

test('a cold router seeds its cache from disk and answers WITHOUT probing', () => {
  let probes = 0;
  const NOW = 1_000_000_000;
  const seeded = diskValue(NOW - 60_000);
  const r = createRuntimesAvailableRouter(countingDeps(() => NOW, () => { probes++; }), {
    readCache: () => seeded,
    writeCache: () => {},
  });
  const got = r.getCached();
  assert.equal(probes, 0, 'a seeded router must answer from disk, never probe');
  assert.equal(got.probed_at, seeded.probed_at);
});

test('a disk cache older than the 24h ceiling is NOT trusted', () => {
  let probes = 0;
  const NOW = 1_000_000_000;
  const stale = diskValue(NOW - (DISK_CACHE_MAX_AGE_MS + 1));
  const r = createRuntimesAvailableRouter(countingDeps(() => NOW, () => { probes++; }), {
    readCache: () => stale,
    writeCache: () => {},
  });
  r.getCached();
  assert.equal(probes, 1, 'a catalog older than the ceiling must be re-probed, not served');
});

test('a disk cache exactly AT the ceiling is still too old', () => {
  let probes = 0;
  const NOW = 1_000_000_000;
  const r = createRuntimesAvailableRouter(
    countingDeps(() => NOW, () => { probes++; }),
    { readCache: () => diskValue(NOW - DISK_CACHE_MAX_AGE_MS), writeCache: () => {} },
  );
  r.getCached();
  assert.equal(probes, 1);
});

test('an unreadable or absent disk cache is not fatal — it just probes', () => {
  let probes = 0;
  const r = createRuntimesAvailableRouter(
    countingDeps(() => 1_000_000_000, () => { probes++; }),
    { readCache: () => { throw new Error('corrupt json'); }, writeCache: () => {} },
  );
  r.getCached();
  assert.equal(probes, 1, 'a broken cache file must never break the endpoint');
});

test('every successful probe is persisted to disk', () => {
  const written: RuntimesAvailableResponse[] = [];
  const r = createRuntimesAvailableRouter(
    countingDeps(() => 1_000_000_000, () => {}),
    { readCache: () => null, writeCache: (v) => { written.push(v); } },
  );
  r.getCached();
  assert.equal(written.length, 1, 'the probe result must be persisted for the next boot');
  assert.equal(written[0].probed_at, 1_000_000_000);
});

test('a failure to persist never fails the request', () => {
  const r = createRuntimesAvailableRouter(
    countingDeps(() => 1_000_000_000, () => {}),
    { readCache: () => null, writeCache: () => { throw new Error('disk full'); } },
  );
  assert.ok(r.getCached(), 'a cache write failure must not propagate');
});

test('an EXPIRED cache is served stale immediately and revalidated in the background', async () => {
  let probes = 0;
  let now = 1_000_000_000;
  const r = createRuntimesAvailableRouter(countingDeps(() => now, () => { probes++; }), {
    readCache: () => null,
    writeCache: () => {},
  });
  const first = r.getCached();          // probe 1, populates the cache
  assert.equal(probes, 1);

  now += 301_000;                       // past the 300s TTL
  const served = r.getCached();
  assert.equal(served.probed_at, first.probed_at,
    'an expired cache must still answer INSTANTLY with the stale value, not block on a probe');

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(probes, 2, 'the refresh must have run in the background');
});

test('concurrent callers on an expired cache trigger only ONE background probe', async () => {
  let probes = 0;
  let now = 1_000_000_000;
  const r = createRuntimesAvailableRouter(countingDeps(() => now, () => { probes++; }), {
    readCache: () => null,
    writeCache: () => {},
  });
  r.getCached();
  assert.equal(probes, 1);

  now += 301_000;
  for (let i = 0; i < 5; i++) r.getCached();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(probes, 2, 'five simultaneous callers must not start five probes');
});

test('a background probe that throws leaves the stale value servable', async () => {
  let probes = 0;
  let now = 1_000_000_000;
  const deps = countingDeps(() => now, () => {
    probes++;
    if (probes > 1) throw new Error('CLI gone');
  });
  const r = createRuntimesAvailableRouter(deps, { readCache: () => null, writeCache: () => {} });
  const first = r.getCached();

  now += 301_000;
  r.getCached();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(r.getCached().probed_at, first.probed_at,
    'a failed background refresh must leave the last good catalog in place');
});

test('POST /refresh still blocks and returns a genuinely fresh probe', () => {
  let probes = 0;
  let now = 1_000_000_000;
  const r = createRuntimesAvailableRouter(countingDeps(() => now, () => { probes++; }), {
    readCache: () => diskValue(now - 1000), writeCache: () => {},
  });
  r.getCached();
  assert.equal(probes, 0, 'seeded from disk');
  now += 5;
  const fresh = r.forceRefresh();
  assert.equal(probes, 1, 'an explicit refresh must probe, not serve the seed');
  assert.equal(fresh.probed_at, now);
});
