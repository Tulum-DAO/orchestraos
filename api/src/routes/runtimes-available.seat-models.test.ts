/**
 * The seat-only models (contract/seat-models.json) on GET /api/runtimes/available: listed in the Claude catalog,
 * marked `seat_only` with a reason so Arturo's picker can dim them, and kept out of `model_catalog_valid_ids` (what
 * the proxy and Arturo accept). The list is the one scripts/agent_model.py offers a seat; a guard here fails if the two
 * ever disagree.
 *
 * Run: npx tsx --test src/routes/runtimes-available.seat-models.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  probeAll, readSeatModels, withSeatModels, SEAT_MODELS_PATH,
  type ProbeDeps, type ProviderConfig, type StaticModel, type SeatModels,
} from './runtimes-available.js';

const REASON = "Only for agents; Arturo can't use the 1M versions";
const caps = (context_window: number) => ({ text: true, image: true, audio: false, video: false, context_window });
const model = (id: string, label: string, cw = 200000): StaticModel => ({ id, label, capabilities: caps(cw) });

function provider(id: string, models: StaticModel[]): ProviderConfig {
  return {
    id, label: id, logo_svg: '<svg/>', cli: id, aliases: [], detect: { cmd: `which ${id}` },
    auth_probe: { kind: 'cli-json', cmd: `${id} auth status`, success_key: 'loggedIn' },
    model_catalog: { source: 'static', static: models },
  } as ProviderConfig;
}

function deps(seatModels?: () => SeatModels | null): ProbeDeps {
  const providers = [
    provider('claude', [model('claude-opus-5-5', 'Opus 5.5'), model('claude-opus-5', 'Opus 5'), model('claude-sonnet-5', 'Sonnet 5')]),
    provider('codex', [model('gpt-sample', 'Sample')]),
  ];
  return {
    loadProviders: () => providers,
    isInstalled: () => true,
    probeAuth: () => ({ authed: true }),
    loadModelCatalog: (p) => ({
      models: (p.model_catalog.static || []).map((m) => ({ ...m })),
      valid_ids: (p.model_catalog.static || []).map((m) => m.id),
      source: 'static',
    }),
    publishLiveCatalog: () => {},
    now: () => 1,
    ...(seatModels ? { seatModels } : {}),
  };
}

test('the contract file is found from the module and holds the two 1M Claude models', () => {
  assert.ok(SEAT_MODELS_PATH.endsWith(join('contract', 'seat-models.json')));
  const s = readSeatModels();
  assert.ok(s);
  assert.equal(s!.reason, REASON);
  assert.deepEqual(s!.models.claude, [
    { id: 'claude-opus-5-5[1m]', label: 'Opus 5.5 (1M)' },
    { id: 'claude-opus-5[1m]', label: 'Opus 5 (1M)' },
  ]);
});

test('the contract list is the one scripts/agent_model.py offers a seat', () => {
  const py = readFileSync(resolve(fileURLToPath(import.meta.url), '../../../../scripts/agent_model.py'), 'utf-8');
  if (py.includes('seat-models.json')) return;                 // it reads the contract itself: one list
  const line = /^SEAT_MODELS\s*=\s*(\{.*\})\s*$/m.exec(py);
  assert.ok(line, 'agent_model.py has neither a SEAT_MODELS literal nor a read of the contract');
  const pairs = [...line![1].matchAll(/\("([^"]+)",\s*"([^"]+)"\)/g)].map((m) => ({ id: m[1], label: m[2] }));
  assert.deepEqual(pairs, readSeatModels()!.models.claude, 'contract/seat-models.json and SEAT_MODELS disagree');
});

test('the Claude list carries them right after their base model, marked with the reason', () => {
  const r = probeAll(deps(() => readSeatModels()));
  const claude = r.providers.find((p) => p.id === 'claude')!;
  assert.deepEqual(claude.models.map((m) => m.id),
    ['claude-opus-5-5', 'claude-opus-5-5[1m]', 'claude-opus-5', 'claude-opus-5[1m]', 'claude-sonnet-5']);
  const opus = claude.models.find((m) => m.id === 'claude-opus-5[1m]')!;
  assert.deepEqual(opus, {
    id: 'claude-opus-5[1m]', label: 'Opus 5 (1M)', capabilities: caps(1_000_000), seat_only: true, seat_only_reason: REASON,
  });
  for (const m of claude.models.filter((x) => !x.id.endsWith('[1m]'))) {
    assert.ok(!('seat_only' in m) && !('seat_only_reason' in m), `${m.id} is not marked`);
  }
});

test('every other field is unchanged: valid ids, other providers, the shape', () => {
  const plain = probeAll(deps());
  const withSeat = probeAll(deps(() => readSeatModels()));
  const claude = withSeat.providers.find((p) => p.id === 'claude')!;
  assert.deepEqual(claude.model_catalog_valid_ids, ['claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5'],
    'a seat-only id is never one the proxy or Arturo accepts');
  assert.deepEqual(withSeat.providers.find((p) => p.id === 'codex'), plain.providers.find((p) => p.id === 'codex'));
  const strip = (r: typeof plain) => r.providers.map((p) => ({ ...p, models: p.models.filter((m) => !m.seat_only) }));
  assert.deepEqual(strip(withSeat), strip(plain));
});

test('no seat-only rows without a model probe, without the file, or without a catalog', () => {
  assert.ok(probeAll(deps(() => readSeatModels()), { withModels: false }).providers.every((p) => p.models.length === 0));
  const dir = testTmpDir('seat-models-');
  const bad = join(dir, 'seat-models.json');
  assert.equal(readSeatModels(bad), null, 'missing');
  writeFileSync(bad, 'not json');
  assert.equal(readSeatModels(bad), null, 'not json');
  writeFileSync(bad, JSON.stringify({ models: { claude: [] } }));
  assert.equal(readSeatModels(bad), null, 'no reason');
  writeFileSync(bad, JSON.stringify({ seat_only_reason: REASON, models: { claude: [{ id: '/model x; rm', label: 'x' }] } }));
  assert.deepEqual(readSeatModels(bad)!.models.claude, [], 'an id a pane could not take is dropped');
  assert.deepEqual(withSeatModels([], 'claude', readSeatModels()), []);
});

test('a catalog that already lists one keeps its own row, unmarked', () => {
  const own = [{ ...model('claude-opus-5-5', 'Opus 5.5') }, { ...model('claude-opus-5-5[1m]', 'Opus 5.5 · 1M', 1_000_000) }];
  const out = withSeatModels(own, 'claude', readSeatModels());
  assert.equal(out.filter((m) => m.id === 'claude-opus-5-5[1m]').length, 1);
  assert.equal(out.find((m) => m.id === 'claude-opus-5-5[1m]')!.seat_only, undefined);
});
