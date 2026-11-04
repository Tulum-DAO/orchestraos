/**
 * The CLI version in GET /api/runtimes/available (the model picker's header shows it, small and grey): every installed
 * CLI's `--version` run at once (not one after another), kept for an hour apart from the 300 s response cache, taken
 * only from a run that exited 0 and only from its first line, and never asked of a CLI that is not installed.
 *
 * Run: npx tsx --test src/routes/runtimes-available.version.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import http from 'node:http';
import {
  createRuntimesAvailableRouter, createVersionCache, parseCliVersion, withVersions, VERSION_TTL_MS,
  type CacheIO, type ProbeDeps, type ProviderConfig, type RuntimesAvailableResponse, type VersionRunner,
} from './runtimes-available.js';

const provider = (id: string, cli = id): ProviderConfig => ({
  id, label: id, logo_svg: '', cli, aliases: [], detect: { cmd: `which ${cli}` },
  auth_probe: { kind: 'cli-json', cmd: `${cli} auth status` },
  model_catalog: { source: 'static', static: [] },
});

test('parseCliVersion: exit 0 and the first line only, at its start or after the CLI\'s name', () => {
  assert.equal(parseCliVersion('2.1.295 (Claude Code)\n', 0), '2.1.295');
  assert.equal(parseCliVersion('codex-cli 0.98.1\n', 0), '0.98.1');
  assert.equal(parseCliVersion('grok 4.7.0-beta.2', 0), '4.7.0-beta.2');
  assert.equal(parseCliVersion('agy version 1.4.0', 0), '1.4.0');
  assert.equal(parseCliVersion('v3.2.1', 0), '3.2.1');
  assert.equal(parseCliVersion('requires Node.js 18.0.0', 0), null, 'an error that names a version is not one');
  assert.equal(parseCliVersion('Error: this CLI requires Node.js 18.0.0 or newer', 0), null);
  assert.equal(parseCliVersion('Welcome!\n2.0.1', 0), null, 'only the first line');
  assert.equal(parseCliVersion('2.1.295 (Claude Code)', 1), null, 'a failed run has no version');
  assert.equal(parseCliVersion('2.1.295', null), null, 'nor a killed one');
  assert.equal(parseCliVersion('', 0), null);
});

test('every --version runs at once, and the answer is kept for an hour', async () => {
  let t = 0;
  const started: string[] = [];
  const release: (() => void)[] = [];
  const run: VersionRunner = (cli) => { started.push(cli); return new Promise((r) => release.push(() => r({ code: 0, stdout: `${cli} ${cli === 'claude' ? '1.0.0' : '2.0.0'}` }))); };
  const cache = createVersionCache(run, () => t);
  const pending = cache.get([{ id: 'claude', cli: 'claude' }, { id: 'codex', cli: 'codex' }]);
  await Promise.resolve();
  assert.deepEqual(started, ['claude', 'codex'], 'both started before either answered (parallel)');
  const again = cache.get([{ id: 'codex', cli: 'codex' }, { id: 'claude', cli: 'claude' }]);
  release.forEach((f) => f());
  assert.deepEqual(await pending, { claude: '1.0.0', codex: '2.0.0' });
  assert.deepEqual(await again, { claude: '1.0.0', codex: '2.0.0' }, 'a second caller joins the batch in flight');
  assert.equal(started.length, 2);
  t = VERSION_TTL_MS - 1;
  await cache.get([{ id: 'claude', cli: 'claude' }, { id: 'codex', cli: 'codex' }]);
  assert.equal(started.length, 2, 'fresh for an hour');
  t = VERSION_TTL_MS + 1;
  const late = cache.get([{ id: 'claude', cli: 'claude' }, { id: 'codex', cli: 'codex' }]);
  await Promise.resolve();
  release.slice(2).forEach((f) => f());
  await late;
  assert.equal(started.length, 4, 'asked again after the hour');
});

test('a CLI that fails, hangs past its bound or throws reports no version; the others still do', async () => {
  const run: VersionRunner = async (cli) => {
    if (cli === 'bad') return { code: 1, stdout: 'requires Node.js 18.0.0' };
    if (cli === 'boom') throw new Error('spawn');
    return { code: 0, stdout: '2.1.295 (Claude Code)' };
  };
  const v = await createVersionCache(run).get([{ id: 'claude', cli: 'claude' }, { id: 'x', cli: 'bad' }, { id: 'y', cli: 'boom' }]);
  assert.deepEqual(v, { claude: '2.1.295' });
});

test('withVersions: installed providers only, by their CLI name; no cache, no change', async () => {
  const resp: RuntimesAvailableResponse = { probed_at: 1, ttl_s: 300, providers: [
    { id: 'claude', label: 'Claude', logo_svg: '', installed: true, authed: true, models: [], model_catalog_source: 'static', model_catalog_valid_ids: [] },
    { id: 'gemini', label: 'Gemini', logo_svg: '', installed: false, authed: false, models: [], model_catalog_source: 'static', model_catalog_valid_ids: [] },
  ] };
  const asked: string[] = [];
  const cache = createVersionCache(async (cli) => { asked.push(cli); return { code: 0, stdout: `${cli} 9.9.9` }; });
  const out = await withVersions(resp, [provider('claude'), provider('gemini', 'agy')], cache);
  assert.deepEqual(asked, ['claude']);
  assert.equal(out.providers[0].version, '9.9.9');
  assert.equal('version' in out.providers[1], false);
  assert.equal(await withVersions(resp, [provider('claude')], null), resp);
});

test('the route serves the version, and the response cache never stores it', async () => {
  const written: RuntimesAvailableResponse[] = [];
  const io: CacheIO = { readCache: () => null, writeCache: (v) => { written.push(v); } };
  const deps: ProbeDeps = {
    loadProviders: () => [provider('claude')], isInstalled: () => true, probeAuth: () => ({ authed: true }),
    loadModelCatalog: () => ({ models: [], valid_ids: [], source: 'static' as const }), publishLiveCatalog: () => {}, now: () => 1,
  };
  const { router } = createRuntimesAvailableRouter(deps, io, createVersionCache(async () => ({ code: 0, stdout: '2.1.295 (Claude Code)' })));
  const app = express(); app.use('/api/runtimes', router);
  const server = http.createServer(app);
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  try {
    const port = (server.address() as { port: number }).port;
    const body = await (await fetch(`http://127.0.0.1:${port}/api/runtimes/available`)).json() as RuntimesAvailableResponse;
    assert.equal(body.providers[0].version, '2.1.295');
    assert.equal('version' in written[0].providers[0], false);
  } finally {
    await new Promise<void>((r) => server.close(() => r()));
  }
});
