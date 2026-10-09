import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  resolveRuntimeConfig, loadRuntimeConfig, operatorUserId, setRuntimeConfig, DEFAULT_RUNTIME_CONFIG,
} from './runtimeConfig.ts';

const respond = (body: string, status = 200, type = 'application/json') =>
  (async () => new Response(body, { status, headers: { 'Content-Type': type } })) as typeof fetch;

test('no config means the public placeholder', () => {
  setRuntimeConfig(null);
  assert.equal(operatorUserId(), 'operator');
  assert.deepEqual(resolveRuntimeConfig(undefined), DEFAULT_RUNTIME_CONFIG);
});

test('a deployment value replaces the placeholder', async () => {
  await loadRuntimeConfig(respond('{"operatorUserId":"alice"}'));
  assert.equal(operatorUserId(), 'alice');
  setRuntimeConfig(null);
});

test('the id is trimmed, and anything that is not a plain identifier falls back', () => {
  assert.equal(resolveRuntimeConfig({ operatorUserId: ' alice ' }).operatorUserId, 'alice');
  for (const bad of ['', '   ', '../etc', 'a/b', 'a b', 'x'.repeat(65), 42, null, ['alice']]) {
    assert.equal(resolveRuntimeConfig({ operatorUserId: bad }).operatorUserId, 'operator', String(bad));
  }
});

test('a missing file served as the SPA fallback (index.html, 200) keeps the defaults', async () => {
  setRuntimeConfig({ operatorUserId: 'stale' });
  await loadRuntimeConfig(respond('<!doctype html><html></html>', 200, 'text/html'));
  assert.equal(operatorUserId(), 'operator');
});

test('a 404, a network error and a hang all keep the defaults and never reject', async () => {
  await loadRuntimeConfig(respond('nope', 404));
  assert.equal(operatorUserId(), 'operator');
  await loadRuntimeConfig((async () => { throw new TypeError('network'); }) as typeof fetch);
  assert.equal(operatorUserId(), 'operator');
  const hang = ((_u: unknown, init?: RequestInit) => new Promise((_, rej) => {
    init?.signal?.addEventListener('abort', () => rej(init.signal!.reason));
  })) as typeof fetch;
  // AbortSignal.timeout's timer is unref'd in node: hold the loop open or the runner cancels us.
  const keepAlive = setTimeout(() => {}, 5_000);
  const t0 = Date.now();
  await loadRuntimeConfig(hang, '/runtime-config.json', 50);
  clearTimeout(keepAlive);
  assert.equal(operatorUserId(), 'operator');
  assert.ok(Date.now() - t0 < 2_000);
});

test('the fetch bypasses the HTTP cache', async () => {
  let seen: RequestInit | undefined;
  await loadRuntimeConfig((async (_u: unknown, init?: RequestInit) => {
    seen = init;
    return new Response('{}');
  }) as typeof fetch);
  assert.equal(seen?.cache, 'no-store');
});

test('no source file hardcodes the operator user id; it is read from runtimeConfig', async () => {
  // The scrub turned the operator's real id into the literal 'operator' in ~20 places. A
  // deployment whose data is keyed by another id saw empty Insights, mis-attributed messages and
  // a wrong "Needs me" count. ONE reader now; this pins it. Allowed: the default itself, and
  // ChatHistory's 'operator' VIEW id (a tab name, never compared with data).
  const { readdirSync, readFileSync, statSync } = await import('node:fs');
  const { join, relative } = await import('node:path');
  const root = new URL('..', import.meta.url).pathname;           // dashboard/src
  const hits: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) { walk(p); continue; }
      if (!/\.(ts|tsx)$/.test(name) || /\.test\./.test(name)) continue;
      const rel = relative(root, p);
      if (rel === 'lib/runtimeConfig.ts') continue;
      readFileSync(p, 'utf8').split('\n').forEach((line, i) => {
        if (!/'operator'/.test(line)) return;
        if (rel === 'pages/ChatHistory.tsx' && /ViewMode|view [!=]==|id: 'operator'/.test(line)) return;
        hits.push(`${rel}:${i + 1}: ${line.trim()}`);
      });
    }
  };
  walk(root);
  assert.deepEqual(hits, []);
});
