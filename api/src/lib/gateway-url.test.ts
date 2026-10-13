/**
 * lib/gateway-url: the API dials the gateway the config names (#84 class), and no route file carries
 * its own literal fallback any more.
 * Run: npx tsx --test src/lib/gateway-url.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gatewayUrl, gatewayWsUrl } from './gateway-url.js';

const cfg = (port: number) => () => ({ gatewayHost: '127.0.0.1', gatewayPort: port });

test('the [gateway] port from orchestra.toml, not a literal', () => {
  assert.equal(gatewayUrl({}, cfg(8890)), 'http://127.0.0.1:8890');
  assert.equal(gatewayUrl({}, cfg(18890)), 'http://127.0.0.1:18890');
  assert.equal(gatewayWsUrl({}, cfg(18890)), 'ws://127.0.0.1:18890');
});

test('the env wins', () => {
  assert.equal(gatewayUrl({ WATCH_GATEWAY_URL: 'http://gw:1' }, cfg(8890)), 'http://gw:1');
  assert.equal(gatewayWsUrl({ WATCH_GATEWAY_WS_URL: 'ws://gw:2' }, cfg(8890)), 'ws://gw:2');
});

test("an unreadable config falls back to the gateway's own default", () => {
  const broken = () => { throw new Error('no toml'); };
  assert.equal(gatewayUrl({}, broken), 'http://127.0.0.1:9091');
});

test('no route file dials a literal gateway port', () => {
  const routes = join(dirname(fileURLToPath(import.meta.url)), '..', 'routes');
  const hits: string[] = [];
  for (const f of readdirSync(routes)) {
    if (!f.endsWith('.ts') || f.endsWith('.test.ts')) continue;
    readFileSync(join(routes, f), 'utf8').split('\n').forEach((line, i) => {
      if (/(https?|wss?):\/\/127\.0\.0\.1:(9091|8890)/.test(line)) hits.push(`${f}:${i + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(hits, []);
});
