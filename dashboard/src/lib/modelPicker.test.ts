import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  canonicalId, isHidden, nameAndTag, modelTitle, pickerModelsFor, buildPickerModels, greyProviders, isCurrent, findCurrent,
  matches, groupModels, flatten, moveActive, greyFor, readRecent, pushRecent, writeRecent, letterMark, RECENT_KEY, newestFirst, NOT_ALLOWED,
} from './modelPicker.ts';
import { buildProviderRows, type ProviderAvailability } from './modelSelectorFilter.ts';
import { switchableModels } from './agentModel.ts';

const cap = (cw: number | null = null) => ({ text: true, image: false, audio: false, video: false, context_window: cw });
const pm = (id: string, label: string, cw: number | null = null) => ({ id, label, capabilities: cap(cw) });

// The probe's shape (GET /api/runtimes/available), as it answers on a box with one provider signed in.
const PROVIDERS: ProviderAvailability[] = [
  { id: 'claude', label: 'Claude', logo_svg: '', installed: true, authed: true, models: [
    pm('opus', 'Opus 5.5'), pm('fable', 'Fable 5.1'), pm('sonnet', 'Sonnet 5.5'), pm('haiku', 'Haiku 5.5'),
    pm('claude-opus-5-5', 'Opus 5.5', 200_000), pm('claude-opus-5', 'Opus 5', 200_000), pm('claude-fable-5-1', 'Fable 5.1'),
  ] },
  { id: 'gemini', label: 'Gemini', logo_svg: '', installed: false, authed: false, auth_reason: 'not-installed', models: [pm('gemini-3.8-flash-high', 'Gemini 3.8 Flash (High)')] },
  { id: 'codex', label: 'Codex', logo_svg: '', installed: true, authed: false, models: [pm('gpt-6-astra', 'GPT-6 Astra')] },
];
// An agent's own list from the gateway (GET /api/agents/:id/model): short and full ids, a subtitle for context.
const AGENT = [
  { ...pm('fable', 'Fable 5.1') }, { ...pm('haiku', 'Haiku 5.5') },
  { ...pm('claude-opus-5', 'Opus 5'), subtitle: '200K context' },
  { ...pm('claude-fable-5-1', 'Fable 5.1') },
  { ...pm('claude-opus-5-5[1m]', 'Opus 5.5 (1M)'), subtitle: '1M context' },
  { ...pm('claude-opus-5[1m]', 'Opus 5 (1M)'), subtitle: '1M context' },
];

test('model rules: Opus 5.5 is the 1M model, the 200k one never shows, Fable 5.1 is listed once', () => {
  assert.equal(canonicalId('claude', 'opus'), 'claude-opus-5-5[1m]');
  assert.equal(canonicalId('codex', 'opus'), 'opus', 'only for its own provider');
  assert.equal(isHidden('claude', 'claude-opus-5-5'), true);
  assert.equal(isHidden('claude', 'opus'), false);
  const probe = buildPickerModels(buildProviderRows(PROVIDERS));
  const opus = probe.filter((m) => m.name === 'Opus 5.5');
  assert.equal(opus.length, 1, 'one Opus 5.5');
  assert.equal(opus[0].id, 'claude-opus-5-5[1m]');
  assert.equal(modelTitle(opus[0]), 'Opus 5.5 · 1M');
  assert.ok(!probe.some((m) => m.id === 'claude-opus-5-5' || m.aliases.includes('claude-opus-5-5')), 'the 200k model under no id');
  assert.equal(probe.filter((m) => m.name === 'Fable 5.1').length, 1);
  const fable = probe.find((m) => m.name === 'Fable 5.1')!;
  assert.equal(fable.id, 'claude-fable-5-1', 'the full id is kept');
  assert.deepEqual(fable.aliases, ['fable']);
  // the agent's own list: the same, and Opus 5 at 200K and at 1M are two models
  const own = pickerModelsFor('claude', 'Claude', AGENT);
  // A size only where a source states it: none is assumed (Fable 5.1 and Haiku 5.5 have none stated).
  assert.deepEqual(own.map(modelTitle), ['Fable 5.1', 'Haiku 5.5', 'Opus 5 · 200K', 'Opus 5.5 · 1M', 'Opus 5 · 1M']);
});

test('names and context tags come from the label, the id, the subtitle or the context window', () => {
  assert.deepEqual(nameAndTag('x', 'Opus 5.5 (1M)'), { name: 'Opus 5.5', tag: '1M' });
  assert.deepEqual(nameAndTag('claude-opus-5-5[1m]', 'Opus 5.5'), { name: 'Opus 5.5', tag: '1M' });
  assert.deepEqual(nameAndTag('x', 'Haiku 4.5', '200K context'), { name: 'Haiku 4.5', tag: '200K' });
  assert.deepEqual(nameAndTag('x', 'Grok 4.7', undefined, 256_000), { name: 'Grok 4.7', tag: '256K' });
  assert.deepEqual(nameAndTag('x', 'Gemini 3.7 Flash (Low)', undefined, 1_000_000), { name: 'Gemini 3.7 Flash (Low)', tag: '1M' });
  assert.deepEqual(nameAndTag('x', 'Sonnet 5.5'), { name: 'Sonnet 5.5' });
});

test('every picker lists every entry; what can\'t be used here is dimmed with why, never hidden', () => {
  const rows = buildProviderRows(PROVIDERS);
  const all = buildPickerModels(rows, { providerId: 'claude', models: AGENT });
  const by = (t: string) => all.find((m) => modelTitle(m) === t);
  // The agent's list adds what only it has (the 1M models); the probe's entries it does not allow stay, dimmed.
  assert.equal(by('Opus 5 · 1M')?.unavailable, undefined);
  assert.equal(by('Sonnet 5.5')?.unavailable, NOT_ALLOWED);
  assert.equal(by('Fable 5.1')?.unavailable, undefined, 'fable and claude-fable-5-1 are one entry, allowed');
  // Providers that can't be used list their models too, dimmed with their state in words.
  assert.equal(all.find((m) => m.providerId === 'gemini')?.unavailable, 'Not installed');
  assert.equal(all.find((m) => m.providerId === 'codex')?.unavailable, 'Not signed in');
  // Without an agent (Arturo's brain picker): the same probe entries, the usable ones all available.
  const plain = buildPickerModels(rows);
  assert.ok(plain.filter((m) => m.providerId === 'claude').every((m) => !m.unavailable));
  assert.deepEqual(greyProviders(rows), [
    { providerId: 'gemini', label: 'Gemini', reason: 'Not installed' },
    { providerId: 'codex', label: 'Codex', reason: 'Not signed in' },
  ]);
});

test('the current model: its id, an alias, or a short id standing for it', () => {
  const models = buildPickerModels(buildProviderRows(PROVIDERS));
  assert.equal(findCurrent(models, { providerId: 'claude', modelId: 'claude-opus-5-5[1m]' })?.name, 'Opus 5.5');
  assert.equal(findCurrent(models, { providerId: 'claude', modelId: 'opus' })?.name, 'Opus 5.5');
  assert.equal(findCurrent(models, { providerId: 'claude', modelId: 'fable' })?.id, 'claude-fable-5-1');
  assert.equal(findCurrent(models, { providerId: 'gemini', modelId: 'claude-fable-5-1' }), null, 'the provider must match too');
  assert.equal(isCurrent(models[0], null), false);
});

test('search: name, provider and nicknames; every word must match', () => {
  const models = pickerModelsFor('claude', 'Claude', AGENT).concat(
    pickerModelsFor('codex', 'Codex', [pm('gpt-6-astra', 'GPT-6 Astra'), pm('gpt-5.6-luna', 'GPT-5.6 Luna')]),
    pickerModelsFor('gemini', 'Gemini', [pm('gemini-3.8-flash-low', 'Gemini 3.8 Flash (Low)')]),
  );
  const names = (q: string) => models.filter((m) => matches(m, q)).map(modelTitle);
  assert.deepEqual(names('opus'), ['Opus 5 · 200K', 'Opus 5.5 · 1M', 'Opus 5 · 1M']);
  assert.deepEqual(names('opus 1m'), ['Opus 5.5 · 1M', 'Opus 5 · 1M']);
  assert.deepEqual(names('fast'), ['Haiku 5.5', 'GPT-5.6 Luna', 'Gemini 3.8 Flash (Low)']);
  assert.deepEqual(names('gpt'), ['GPT-6 Astra', 'GPT-5.6 Luna']);
  assert.deepEqual(names('openai'), ['GPT-6 Astra', 'GPT-5.6 Luna'], 'a provider\'s other name');
  assert.deepEqual(names('anthropic fable'), ['Fable 5.1']);
  assert.deepEqual(names('google'), ['Gemini 3.8 Flash (Low)']);
  assert.deepEqual(names('nothing like this'), []);
  assert.equal(names('').length, models.length);
  assert.deepEqual(names('claude-opus-5[1m]'), ['Opus 5 · 1M'], 'an id finds its model');
});

test('grouping: recent first (not listed twice, not while searching), then each provider; a name that starts with the query first', () => {
  const models = pickerModelsFor('claude', 'Claude', AGENT).concat(pickerModelsFor('grok', 'Grok', [pm('grok-4.7', 'Grok 4.7', 256_000)]));
  const recent = ['grok:grok-4.7', 'claude:claude-opus-5-5[1m]', 'claude:gone-model'];
  const g = groupModels(models, { recent });
  assert.deepEqual(g.map((x) => x.label), ['Recent', 'Claude'], 'grok\'s only model is in Recent');
  assert.deepEqual(g[0].models.map(modelTitle), ['Grok 4.7 · 256K', 'Opus 5.5 · 1M'], 'newest first, a gone one skipped');
  assert.ok(!g[1].models.some((m) => m.name === 'Opus 5.5'), 'not twice');
  assert.equal(groupModels(models, { recent, maxRecent: 1 })[0].models.length, 1);
  const s = groupModels(models, { query: '5', recent });
  assert.ok(!s.some((x) => x.id === 'recent'), 'no Recent while searching');
  const h = groupModels(models, { query: 'h' });
  assert.equal(h[0].models[0].name, 'Haiku 5.5');
  assert.deepEqual(groupModels(models, { query: 'zzz' }), []);
  assert.deepEqual(flatten(g).map((m) => m.key).slice(0, 2), ['grok:grok-4.7', 'claude:claude-opus-5-5[1m]']);
});

test('keyboard: arrows move through the rows and wrap', () => {
  const keys = ['a', 'b', 'c'];
  assert.equal(moveActive(keys, null, 1), 'a');
  assert.equal(moveActive(keys, null, -1), 'c');
  assert.equal(moveActive(keys, 'c', 1), 'a');
  assert.equal(moveActive(keys, 'a', -1), 'c');
  assert.equal(moveActive(keys, 'gone', 1), 'a');
  assert.equal(moveActive([], 'a', 1), null);
});

test('grey providers follow the search by their names', () => {
  const grey = greyProviders(buildProviderRows(PROVIDERS));
  assert.equal(greyFor(grey, '').length, 2);
  assert.deepEqual(greyFor(grey, 'gpt').map((x) => x.providerId), ['codex']);
  assert.deepEqual(greyFor(grey, 'google').map((x) => x.providerId), ['gemini']);
  assert.deepEqual(greyFor(grey, 'opus'), []);
});

test('recently used: newest first, no repeats, a few kept, bad storage ignored', () => {
  const mem = new Map<string, string>();
  const store = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) };
  assert.deepEqual(readRecent(store), []);
  let list: string[] = [];
  for (const k of ['a', 'b', 'a', 'c', 'd', 'e', 'f', 'g']) list = pushRecent(list, k);
  assert.deepEqual(list, ['g', 'f', 'e', 'd', 'c', 'a']);
  writeRecent(store, list);
  assert.deepEqual(readRecent(store), list);
  mem.set(RECENT_KEY, '{not json');
  assert.deepEqual(readRecent(store), []);
  assert.deepEqual(readRecent(null), []);
  assert.doesNotThrow(() => writeRecent({ getItem: () => null, setItem: () => { throw new Error('full'); } }, ['a']));
});

test('a provider without a proven brand mark gets a neutral letter', () => {
  assert.equal(letterMark('Sample'), 'S');
  assert.equal(letterMark('  example'), 'E');
  assert.equal(letterMark(''), '?');
});

test('the picker keeps the deployment gates the sheet had (connect, add a provider) and draws only proven marks', () => {
  const src = fs.readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(src, /providerTileAction\(/);
  assert.match(src, /showAddProvider\(\)/);
  assert.match(src, /\/api\/runtimes\/available/);
  assert.match(src, /providerMark\(/, 'logos only through the brand marks');
  assert.doesNotMatch(src, /logo_svg/, 'never the probe\'s own svg');
  assert.doesNotMatch(src, /(bg|text|border)-neutral-\d/, 'tone tokens, not a fixed palette');
});

test('an agent\'s own list keeps the gateway\'s context size, so 200K and 1M models are told apart', () => {
  const info = { runtime: 'claude', model: 'claude-opus-5-5[1m]', how: 'live', allowed: [], models: [
    { id: 'claude-opus-5', title: 'Opus 5', subtitle: '200K context', available: true },
    { id: 'claude-opus-5[1m]', title: 'Opus 5 (1M)', subtitle: '1M context', available: true },
    { id: 'claude-opus-5-5', title: 'Opus 5.5', subtitle: '200K context', available: true },
    { id: 'sample-off', title: 'Sample', available: false },
  ] };
  const rows = pickerModelsFor('claude', 'Claude', switchableModels(info));
  assert.deepEqual(rows.map(modelTitle), ['Opus 5 · 200K', 'Opus 5 · 1M'], 'the 200k Opus 5.5 never shows; an unavailable one is left out');
});

test('one order in every picker: by provider, newest first (larger family first at a version, 200K before 1M)', () => {
  // The agent's list as the gateway answers it (Opus 5.5 last) and the probe's (Opus 5.5 first) come out the same.
  const gateway = [
    pm('fable', 'Fable 5.1'), pm('sonnet', 'Sonnet 5.5'), pm('haiku', 'Haiku 5.5'), { ...pm('claude-opus-5', 'Opus 5'), subtitle: '200K context' },
    pm('claude-opus-4-7', 'Opus 4.7'), pm('claude-sonnet-4-6', 'Sonnet 4.6'), pm('claude-fable-5-1', 'Fable 5.1'),
    { ...pm('claude-opus-5-5[1m]', 'Opus 5.5 (1M)'), subtitle: '1M context' }, { ...pm('claude-opus-5[1m]', 'Opus 5 (1M)'), subtitle: '1M context' },
  ];
  const rows = buildProviderRows(PROVIDERS);
  const fromAgent = buildPickerModels(rows, { providerId: 'claude', models: gateway }).map(modelTitle);
  assert.deepEqual(fromAgent.slice(0, 8), ['Opus 5.5 · 1M', 'Sonnet 5.5', 'Haiku 5.5', 'Fable 5.1', 'Opus 5 · 200K', 'Opus 5 · 1M',
    'Opus 4.7', 'Sonnet 4.6']);
  const fromProbe = buildPickerModels(rows).map(modelTitle);
  assert.equal(fromProbe[0], 'Opus 5.5 · 1M');
  assert.ok(newestFirst({ name: 'GPT-6 Astra' }, { name: 'GPT-5.6 Luna' }) < 0, 'other providers by version too');
});

test('rows only agents can use (seat_only, the 1M versions): dimmed in Arturo\'s brain picker, usable for an agent', () => {
  // The probe's Claude list as the API sends it: each 1M row right after its base model, with the reason.
  const seat = (id: string, label: string) => ({ ...pm(id, label, 1_000_000), seat_only: true, seat_only_reason: "Only for agents; Arturo can't use the 1M versions" });
  const providers: ProviderAvailability[] = [{ id: 'claude', label: 'Claude', logo_svg: '', installed: true, authed: true, models: [
    pm('opus', 'Opus 5.5'), seat('claude-opus-5-5[1m]', 'Opus 5.5 (1M)'), pm('claude-opus-5', 'Opus 5', 200_000), seat('claude-opus-5[1m]', 'Opus 5 (1M)'),
  ] }];
  const rows = buildProviderRows(providers);
  const arturo = buildPickerModels(rows, null, { forArturo: true });
  const a = (t: string) => arturo.find((m) => modelTitle(m) === t);
  assert.equal(a('Opus 5 · 1M')?.unavailable, "Only for agents; Arturo can't use the 1M versions");
  assert.equal(a('Opus 5.5 · 1M')?.unavailable, "Only for agents; Arturo can't use the 1M versions");
  assert.equal(a('Opus 5.5')?.unavailable, undefined, 'his own Opus 5.5 (opus) stays his to pick');
  assert.equal(a('Opus 5 · 200K')?.unavailable, undefined);
  const agent = buildPickerModels(rows);
  assert.equal(agent.find((m) => modelTitle(m) === 'Opus 5 · 1M')?.unavailable, undefined, 'agents can use them');
  assert.equal(agent.find((m) => modelTitle(m) === 'Opus 5.5 · 1M')?.unavailable, undefined);
});
