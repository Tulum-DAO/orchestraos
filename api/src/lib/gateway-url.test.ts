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

test("no config file at all: the gateway's own default", () => {
  const broken = () => { throw new Error('no toml'); };
  assert.equal(gatewayUrl({}, broken, () => false), 'http://127.0.0.1:9091');
});

test('a config that exists but is invalid throws instead of guessing a port', () => {
  const invalid = () => { throw new Error('missing [gateway] port'); };
  assert.throws(() => gatewayUrl({}, invalid, () => true), /missing \[gateway\] port/);
});

// The lint: the gateway's address is derived in ONE place. Any other source file that reads the gateway env
// vars or the config's gateway fields, or names the gateway's known ports in code, is deriving its own.
function codeLines(file: string): Array<[number, string]> {
  return readFileSync(file, 'utf8').split('\n').map((l, i) => [i + 1, l] as [number, string])
    .filter(([, l]) => !/^\s*(\*|\/\/|\/\*)/.test(l));
}

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((d) =>
    d.isDirectory() ? walk(join(dir, d.name)) : d.name.endsWith('.ts') && !d.name.endsWith('.test.ts') ? [join(dir, d.name)] : []);
}

test('only lib/gateway-url.ts derives the gateway address', () => {
  const src = join(dirname(fileURLToPath(import.meta.url)), '..');
  const hits: string[] = [];
  for (const f of walk(src)) {
    if (f.endsWith(join('lib', 'gateway-url.ts')) || f.endsWith(join('lib', 'config.ts'))) continue;
    for (const [n, l] of codeLines(f)) {
      if (/WATCH_GATEWAY_(WS_)?URL|\.gatewayHost\b|\.gatewayPort\b|:(9091|8890)\b/.test(l)) hits.push(`${f.slice(src.length + 1)}:${n}: ${l.trim()}`);
    }
  }
  assert.deepEqual(hits, []);
});
