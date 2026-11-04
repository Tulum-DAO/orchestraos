/**
 * The new model picker (components/kit/ModelPicker.tsx + ModelPickerPanel.tsx): purpose words, the context rule
 * (an existing agent picks only its runtime), the all-provider search, effort per runtime, the keyboard, the usage
 * ring, and an unavailable provider with Sign in. The panel is rendered from props (no browser).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { buildProviderRows, type ProviderAvailability } from './modelSelectorFilter.ts';
import { buildPickerModels, nameAndTag, type PickerModel } from './modelPicker.ts';
import { modelPurpose, PURPOSE_TABLE } from './modelPurpose.ts';
import { EFFORT, effortLabel, effortLevels, effortStop, type RuntimeEffort } from './modelEffort.ts';
import {
  browseRows, canPick, contextControl, contextChoices, familiesFor, familyOf, initialTab, isRuntimeDefault, navKey, olderKey,
  otherRuntimeNote, pickableKeys, pillName, pillText, railEntries, railKey, resolveFamily, ringDash, rowReason, scopeFor, statusWords,
  usageFor, visibleRows, railLabel, showsDefaultMark,
} from './modelPickerPanel.ts';
import { agentEffortLevels, applyNote } from './agentModel.ts';
import { newAgentHref, newAgentRuntimeParam } from './newAgent.ts';
import ModelPickerPanel, { type ModelPickerPanelProps } from '../components/kit/ModelPickerPanel.tsx';
import { sliderCommit, sliderDraft, sliderMove, sliderSync } from './stopSlider.ts';
import { StopSlider, type StopSliderProps } from '../components/kit/StopSlider.tsx';

(globalThis as { React?: typeof React }).React = React;

const cap = (cw: number | null = null) => ({ text: true, image: false, audio: false, video: false, context_window: cw });
const pm = (id: string, label: string, cw: number | null = null) => ({ id, label, capabilities: cap(cw) });

// As the live probe answers on a test install: Claude signed in, the rest not installed / not signed in.
const PROVIDERS: ProviderAvailability[] = [
  { id: 'claude', label: 'Claude', logo_svg: '', installed: true, authed: true, version: '2.1.295', model_catalog_default: 'claude-opus-5-5',
    models: [pm('opus', 'Opus 5.5'), pm('sonnet', 'Sonnet 5.5'), pm('haiku', 'Haiku 5.5'), pm('fable', 'Fable 5.1'), pm('claude-opus-5', 'Opus 5', 200_000)] },
  { id: 'codex', label: 'Codex', logo_svg: '', installed: true, authed: false, auth_reason: 'signed-out', models: [pm('gpt-6-astra', 'GPT-6 Astra')] },
  { id: 'gemini', label: 'Gemini', logo_svg: '', installed: false, authed: false, auth_reason: 'not-installed',
    models: [pm('gemini-3.8-flash-high', 'Gemini 3.8 Flash (High)'), pm('gemini-3.1-pro-high', 'Gemini 3.1 Pro (High)')] },
  { id: 'grok', label: 'Grok', logo_svg: '', installed: true, authed: true, models: [pm('grok-4.7', 'Grok 4.7', 256_000)] },
];
const ROWS = buildProviderRows(PROVIDERS);
const USAGE = [
  { provider: 'claude', account: 'default', window: '5h', used_pct: 12, stale: false },
  { provider: 'claude', account: 'default', window: 'weekly', used_pct: 74.4, stale: false },
  { provider: 'codex', used_pct: null, reason: 'no Codex session has reported its limits yet' },
];
const byName = (ms: PickerModel[], name: string) => ms.find((m) => m.name === name)!;

// ── purpose table ──

test('purpose words: from the table by runtime and family; an unknown model gets none', () => {
  assert.equal(modelPurpose('claude', 'Opus 5.5'), 'Deep work · long tasks');
  assert.equal(modelPurpose('claude', 'Opus 6'), 'Deep work · long tasks', 'a new version keeps its family words');
  assert.equal(modelPurpose('claude', 'Sonnet 5'), 'Everyday · fast');
  assert.equal(modelPurpose('claude', 'Haiku 5.5'), 'Quick replies');
  assert.equal(modelPurpose('gemini', 'Gemini 3.8 Flash (High)'), 'Everyday · fast');
  assert.equal(modelPurpose('gemini', 'Gemini 3.1 Pro (High)'), 'Complex reasoning');
  assert.equal(modelPurpose('claude', 'Fable 5.1'), null, 'no entry: no words, never a guess');
  assert.equal(modelPurpose('codex', 'GPT-6 Astra'), null);
  assert.equal(modelPurpose('grok', 'Grok 4.7'), null);
  assert.equal(modelPurpose('codex', 'Opus 5'), null, 'a family word only counts for its own runtime');
  for (const r of PURPOSE_TABLE) assert.ok(r.words.length <= 24, `short: ${r.words}`);
});

// ── the context rule ──

test('rule 2: an existing agent picks only its runtime; defaults / Arturo pick any signed-in provider', () => {
  const agent = scopeFor({ providerId: 'claude' });
  const any = scopeFor(null);
  assert.deepEqual(agent, { kind: 'agent', runtime: 'claude' });
  assert.deepEqual(any, { kind: 'any' });
  const models = buildPickerModels(ROWS);
  const grok = byName(models, 'Grok 4.7');
  const sonnet = byName(models, 'Sonnet 5.5');
  assert.equal(canPick(sonnet, agent), true);
  assert.equal(canPick(grok, agent), false, "another runtime is not this agent's");
  assert.equal(canPick(grok, any), true, 'in Settings every usable provider can be picked');
  assert.equal(canPick(byName(models, 'GPT-6 Astra'), any), false, 'never a provider that is not signed in');
  assert.match(otherRuntimeNote('grok', 'Grok', agent, (id) => (id === 'claude' ? 'Claude' : id))!, /runs on Claude.*Grok needs a new agent/);
  assert.equal(otherRuntimeNote('claude', 'Claude', agent, (id) => id), null);
  assert.equal(otherRuntimeNote('grok', 'Grok', any, (id) => id), null);
  const entries = railEntries(ROWS, USAGE, agent);
  assert.deepEqual(entries.filter((e) => e.otherRuntime).map((e) => e.id), ['codex', 'gemini', 'grok']);
  assert.equal(initialTab(entries, agent, 'grok'), 'claude', "an agent's picker opens on its own runtime");
  assert.equal(initialTab(railEntries(ROWS, USAGE, any), any, 'grok'), 'grok', 'elsewhere on the model in use');
  assert.equal(initialTab(railEntries(ROWS, USAGE, any), any, null), 'claude', 'else the first usable provider');
});

test('rule 2 in the panel: another provider offers "Start a new agent with …", not its models', () => {
  const html = render({ scope: { kind: 'agent', runtime: 'claude' }, tab: 'grok', onNewAgent: () => {} });
  assert.match(html, /Start a new agent with Grok/);
  assert.match(html, /data-model-option="grok:grok-4\.7"[^>]*data-model-unavailable="true"/);
  // Without a new-agent flow (feature off): the explanation only, no fake action.
  const plain = render({ scope: { kind: 'agent', runtime: 'claude' }, tab: 'grok', onNewAgent: null });
  assert.match(plain, /This agent runs on Claude/);
  assert.doesNotMatch(plain, /Start a new agent/);
});

test('"Start a new agent with X" opens the Agents page form on that runtime', () => {
  assert.equal(newAgentHref('codex'), '/agents?new=codex');
  assert.equal(newAgentRuntimeParam('?new=codex'), 'codex');
  assert.equal(newAgentRuntimeParam('?new=../x'), null, 'only a plain runtime id');
  assert.equal(newAgentRuntimeParam(''), null);
  const agents = readFileSync(new URL('../pages/Agents.tsx', import.meta.url), 'utf8');
  assert.match(agents, /newAgentRuntimeParam\(/);
  assert.match(agents, /initialRuntime=\{newAgentRuntime\}/);
  const modal = readFileSync(new URL('../components/NewAgentModal.tsx', import.meta.url), 'utf8');
  assert.match(modal, /authed\.find\(\(r\) => r\.id === initialRuntime\)/);
});

// ── search ──

test('search covers every provider at once, each hit with its provider badge; Enter skips what cannot be picked', () => {
  const models = buildPickerModels(ROWS);
  const hits = visibleRows(models, 'fast', 'claude');
  assert.deepEqual([...new Set(hits.map((m) => m.providerId))], ['claude', 'gemini'], 'Haiku and Flash, across providers');
  assert.deepEqual(visibleRows(models, '', 'grok').map((m) => m.providerId), ['grok'], 'no query: the selected provider only');
  const html = render({ query: 'fast', shown: hits });
  assert.match(html, /data-provider-badge="claude"/);
  assert.match(html, /data-provider-badge="gemini"/);
  assert.match(html, /aria-label="Models from every provider"/);
  // In an agent's picker, a hit from another runtime is shown, never a key Enter can reach.
  const agent = scopeFor({ providerId: 'claude' });
  const keys = hits.filter((m) => canPick(m, agent)).map((m) => m.key);
  assert.ok(keys.every((k) => k.startsWith('claude:')));
  assert.equal(navKey('Enter', { tabs: [], tab: null, keys, active: 'gemini:gemini-3.8-flash-high', query: 'fast' }), null);
});

// ── effort ──

test('effort: each runtime its own levels, and a control only where a level is really applied (Claude, Codex)', () => {
  assert.deepEqual(EFFORT.claude.levels.map((l) => l.id), ['low', 'medium', 'high', 'xhigh', 'max'], '`claude --help` 2.1.295');
  assert.deepEqual(EFFORT.codex.levels.map((l) => l.id), ['low', 'medium', 'high', 'xhigh'], "codex's reasoning picker");
  assert.equal(EFFORT.gemini, undefined, 'Gemini: the level is part of the model id');
  for (const rt of ['claude', 'codex']) {
    assert.ok(effortLevels(rt, 'agent'), `${rt}: applied to a running agent`);
    assert.ok(effortLevels(rt, 'defaults'), `${rt}: applied at a new agent's spawn`);
  }
  for (const rt of ['gemini', 'grok']) {
    assert.equal(effortLevels(rt, 'agent'), null, `${rt}: no effort setting`);
    assert.equal(effortLevels(rt, 'defaults'), null, `${rt}: no effort setting`);
  }
  // Each note says when a level takes effect, truthfully.
  assert.match(EFFORT.claude.note, /\/effort/);
  assert.match(EFFORT.claude.note, /--effort/);
  assert.match(EFFORT.codex.note, /model_reasoning_effort/);
  // Once an apply path exists, flipping `applies` is all it takes; levels stay the runtime's own.
  const on: Record<string, RuntimeEffort> = { codex: { ...EFFORT.codex, applies: { agent: true, defaults: false } } };
  assert.deepEqual(effortLevels('codex', 'agent', on)?.map((l) => l.label), ['Low', 'Medium', 'High', 'Extra high']);
  assert.equal(effortLevels('codex', 'defaults', on), null);
  assert.equal(effortLabel('claude', 'medium'), 'Medium');
  assert.equal(effortLabel('claude', 'turbo'), null);
  assert.equal(pillText('Opus 5', 'Medium'), 'Opus 5 · Medium');
  assert.equal(pillText('Opus 5', null), 'Opus 5');
  assert.equal(pillText(null, 'Medium'), 'Choose a model');
  // The panel draws the slider only when handed levels, and none otherwise.
  assert.doesNotMatch(render({}), /data-model-effort/);
  const html = render({ effort: { levels: EFFORT.codex.levels, value: 'medium', onChange: () => {} } });
  assert.match(html, /type="range"[^>]*aria-valuetext="Medium"/);
});

// ── keyboard ──

test('keyboard: ↑/↓ rows (wrapping), ←/→ providers with an empty search, Enter picks, Esc closes; the rail is a tablist', () => {
  const s = { tabs: ['claude', 'codex', 'grok'], tab: 'claude', keys: ['a', 'b', 'c'], active: 'a', query: '' };
  assert.deepEqual(navKey('ArrowDown', s), { kind: 'active', active: 'b' });
  assert.deepEqual(navKey('ArrowUp', s), { kind: 'active', active: 'c' });
  assert.deepEqual(navKey('ArrowRight', s), { kind: 'tab', tab: 'codex' });
  assert.deepEqual(navKey('ArrowLeft', s), { kind: 'tab', tab: 'grok' });
  assert.equal(navKey('ArrowLeft', { ...s, query: 'op' }), null, 'with text, ←/→ move the caret');
  assert.deepEqual(navKey('Enter', s), { kind: 'pick', key: 'a' });
  assert.equal(navKey('Enter', { ...s, active: null }), null);
  assert.deepEqual(navKey('Escape', s), { kind: 'close' });
  assert.equal(navKey('x', s), null);
  assert.equal(railKey('ArrowDown', s.tabs, 'grok'), 'claude');
  assert.equal(railKey('ArrowUp', s.tabs, 'claude'), 'grok');
  assert.equal(railKey('End', s.tabs, 'claude'), 'grok');
  assert.equal(railKey('Home', s.tabs, 'grok'), 'claude');
  assert.equal(railKey('Enter', s.tabs, 'grok'), null);
  const html = render({});
  assert.match(html, /role="tablist" aria-label="Providers" aria-orientation="vertical"/);
  assert.match(html, /role="tab" id="t-tab-claude" aria-selected="true"[^>]*tabindex="0"/);
  assert.match(html, /role="tab" id="t-tab-codex" aria-selected="false"[^>]*tabindex="-1"/);
  assert.match(html, /role="combobox"[^>]*aria-activedescendant="t-opt-claude:sonnet"/);
  assert.match(html, /role="listbox"/);
  assert.match(render({ layout: 'sheet' }), /aria-orientation="horizontal"/, 'on a phone the rail is a row of tabs');
  assert.match(render({ layout: 'sheet' }), /min-h-\[44px\]/, '44px targets on a phone');
  const src = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(src, /focusTrap\(e\)/, 'Tab stays inside the popover');
  assert.match(src, /\(pointer: coarse\)/, 'a coarse pointer gets the sheet');
});

// ── usage ring ──

test('usage ring: the limit nearest to running out; no ring without a number; the arc is that share of the circle', () => {
  const u = usageFor(USAGE, 'claude')!;
  assert.equal(u.pct, 74);
  assert.equal(u.text, '74% of weekly used');
  assert.equal(u.high, false);
  assert.equal(usageFor(USAGE, 'codex'), null, 'a reason is not a number');
  assert.equal(usageFor(USAGE, 'grok'), null);
  assert.equal(usageFor(null, 'claude'), null);
  assert.equal(usageFor([{ provider: 'claude', window: '5h', used_pct: 91 }], 'claude')!.high, true);
  const stale = usageFor([{ provider: 'claude', window: 'weekly', used_pct: 50, stale: true, as_of: 1_793_612_401 }], 'claude', new Date(1_793_700_000_000))!;
  assert.match(stale.text, /^50% of weekly used \(as of .+\)$/, 'an old reading says so');
  const r = ringDash(25, 10);
  assert.ok(Math.abs(r.circumference - 2 * Math.PI * 10) < 1e-9);
  assert.ok(Math.abs(r.dash - r.circumference / 4) < 1e-9);
  assert.ok(Math.abs(r.dash + r.gap - r.circumference) < 1e-9);
  assert.equal(ringDash(150, 10).gap, 0, 'clamped at a full circle');
  assert.equal(ringDash(-5, 10).dash, 0);
  assert.equal(ringDash(Number.NaN, 10).dash, 0);
  const html = render({});
  assert.match(html, /data-usage-ring="74"/);
  assert.equal((html.match(/data-usage-ring=/g) ?? []).length, 1, 'only Claude reports a number');
  assert.match(html, /aria-label="Claude: signed in, 74% of weekly used, CLI v2\.1\.295"/, 'status, usage and version on the rail mark');
});

// ── unavailable provider ──

test('an unavailable provider stays in the rail, dimmed with its status, and offers Sign in', () => {
  const entries = railEntries(ROWS, USAGE, { kind: 'any' });
  assert.deepEqual(entries.map((e) => [e.id, e.status, e.usable]),
    [['claude', 'signed-in', true], ['codex', 'signed-out', false], ['gemini', 'not-installed', false], ['grok', 'signed-in', true]]);
  assert.equal(statusWords(ROWS[1]), 'not signed in');
  assert.equal(statusWords(ROWS[2]), 'not installed');
  const html = render({ tab: 'codex', onSignIn: () => {} });
  assert.match(html, /data-rail="codex"[^>]*opacity-60/);
  assert.match(html, /data-status="signed-out"/);
  assert.match(html, /data-status="not-installed"/);
  assert.match(html, /Codex is not signed in on this machine/);
  assert.match(html, /data-model-connect="codex"[^>]*>Sign in</);
  assert.match(html, /data-model-option="codex:gpt-6-astra"[^>]*data-model-unavailable="true"/);
  assert.doesNotMatch(render({ tab: 'codex', onSignIn: null }), /Sign in</, 'no sign-in flow: no button');
  assert.match(html, /aria-label="Codex: not signed in"/);
});

// ── rows ──

test('rows: purpose words, the runtime\'s "default", a check and a filled radio on the model in use', () => {
  const models = buildPickerModels(ROWS);
  assert.equal(isRuntimeDefault(byName(models, 'Opus 5.5'), 'claude-opus-5-5'), true, 'the CLI alias stands for it');
  assert.equal(isRuntimeDefault(byName(models, 'Opus 5'), 'claude-opus-5-5'), false);
  assert.equal(isRuntimeDefault(byName(models, 'Opus 5'), undefined), false);
  const html = render({ value: { providerId: 'claude', modelId: 'sonnet' } });
  assert.match(html, /data-model-option="claude:opus"(?:(?!data-model-option).)*data-default-pill/, 'Opus 5.5 is the default');
  assert.equal((html.match(/data-default-pill/g) ?? []).length, 1);
  assert.match(html, /id="t-opt-claude:sonnet" role="option" aria-selected="true" data-model-option="claude:sonnet"(?:(?!data-model-option).)*In use/);
  assert.match(html, /Deep work · long tasks/);
  assert.match(html, /data-model-option="claude:fable"(?:(?!data-model-option).)*Fable 5\.1<\/span><\/span>/, 'Fable: no purpose words');
});

test('"Use the default brain" stays first for Arturo; the note says when a pick applies', () => {
  const html = render({ defaultRow: { key: '__default__', selected: true, onPick: () => {} } });
  assert.ok(html.indexOf('Use the default brain') < html.indexOf('data-model-option="claude:'));
  assert.match(render({ note: "Applies from this agent's next rotation" }), /data-model-picker-note[^>]*>Applies from this agent&#x27;s next rotation</);
  const info = { runtime: 'claude', model: 'opus', allowed: [], models: [] };
  assert.equal(applyNote({ ...info, how: 'live' }), null, 'the usual timing is not said every time');
  assert.equal(applyNote({ ...info, how: 'next_rotation' }), "Applies from this agent's next rotation");
  assert.equal(applyNote({ ...info, how: null }), null);
  assert.equal(applyNote(null), null);
});

test('every former user of the picker still uses it', () => {
  const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');
  for (const p of ['components/agent/Composer.tsx', 'components/settings/sections/AgentsSection.tsx', 'components/arturo/ArturoPill.tsx',
    'components/arturo/ArturoPanel.tsx', 'pages/ArturoHome.tsx', 'components/kit/ModelPickerDemo.tsx']) {
    assert.match(src(p), /import ModelPicker(, \{[^}]*\})? from '(\.\.?\/)+(components\/)?(kit\/)?ModelPicker'/, p);
  }
  assert.match(src('components/agent/Composer.tsx'), /note=\{applyNote\(agentModel\)\}/);
  assert.match(src('components/arturo/ArturoPill.tsx'), /onPickDefault=/);
  assert.match(src('pages/ArturoHome.tsx'), /onPickDefault=/);
  assert.match(src('components/settings/sections/AgentsSection.tsx'), /<ModelPicker variant="inline"/);
});

// ── review minors (coordinator, 2026-11-03) ──

test('pill: the size only when the runtime offers that model in more than one size', () => {
  const two = buildPickerModels(buildProviderRows([{ ...PROVIDERS[0], models: [
    pm('claude-opus-5-5', 'Opus 5.5', 200_000), pm('claude-opus-5-5[1m]', 'Opus 5.5 (1M)'), pm('claude-opus-5', 'Opus 5', 200_000)] }]));
  assert.equal(pillName(two.find((m) => m.tag === '1M')!, two), 'Opus 5.5 · 1M');
  assert.equal(pillName(two.find((m) => m.name === 'Opus 5.5' && m.tag === '200K')!, two), 'Opus 5.5 · 200K');
  assert.equal(pillText(pillName(two.find((m) => m.tag === '1M')!, two), 'High'), 'Opus 5.5 · 1M · High');
  assert.equal(pillName(byName(two, 'Opus 5'), two), 'Opus 5', 'one size: no tag');
  assert.equal(pillName(null, two), null);
  const src = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(src, /pillText\(pillName\(current, models\)/);
});

test('a dimmed row says why on a visible line under its name, not only on hover', () => {
  const models = buildPickerModels(ROWS);
  const agent = scopeFor({ providerId: 'claude' });
  const label = (id: string) => ({ claude: 'Claude', grok: 'Grok' } as Record<string, string>)[id] ?? id;
  assert.equal(rowReason(byName(models, 'GPT-6 Astra'), { kind: 'any' }, label), 'Not signed in');
  assert.equal(rowReason(byName(models, 'Grok 4.7'), agent, label), 'Needs a new agent on Grok');
  assert.equal(rowReason(byName(models, 'Sonnet 5.5'), agent, label), null);
  const html = render({ tab: 'codex' });
  assert.match(html, /data-model-option="codex:gpt-6-astra"(?:(?!data-model-option).)*data-model-reason="true">Not signed in</);
  assert.doesNotMatch(render({}), /data-model-reason/, 'a pickable row has no reason line');
});

test('"Use the default brain" shows once, above the provider content, on every tab, never under a banner', () => {
  const def = { key: '__default__', selected: false, onPick: () => {} };
  for (const tab of ['claude', 'gemini', 'grok']) {
    const html = render({ tab, defaultRow: def });
    assert.equal((html.match(/Use the default brain/g) ?? []).length, 1, tab);
    const at = html.indexOf('Use the default brain');
    assert.ok(at < html.indexOf('data-model-picker-header'), `${tab}: above the provider header`);
    assert.ok(at < html.indexOf('role="tabpanel"'), `${tab}: outside the tab's content`);
  }
  assert.ok(render({ tab: 'gemini', defaultRow: def }).indexOf('Use the default brain') < render({ tab: 'gemini', defaultRow: def }).indexOf('data-model-picker-banner'));
  assert.doesNotMatch(render({ query: 'opus', shown: visibleRows(buildPickerModels(ROWS), 'opus', 'claude'), defaultRow: def }), /Use the default brain/);
});

test('wiring: Settings marks each runtime\'s own default on its tab; Arturo closes before a new agent; focus returns below 480px', () => {
  const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');
  assert.match(src('components/settings/sections/AgentsSection.tsx'), /value=\{\{ providerId: runtime, modelId: models\[runtime\] \?\? '' \}\} onTabChange=\{setRuntime\}/);
  assert.match(src('components/kit/ModelPicker.tsx'), /setQuery\(''\); onTabChange\?\.\(id\)/);
  assert.match(src('components/arturo/ArturoPanel.tsx'), /onBeforeNewAgent=\{onClose\}/);
  assert.match(src('components/kit/ModelPicker.tsx'), /onBeforeNewAgent\?\.\(\); navigate\(newAgentHref\(id\)\)/, 'the pane closes before navigating');
  const picker = src('components/kit/ModelPicker.tsx');
  assert.match(picker, /t\.getClientRects\(\)\.length > 0\) t\.focus\(\);\s*else returnFocus\?\.\(\)\?\.focus\(\)/);
  const composer = src('components/agent/Composer.tsx');
  assert.match(composer, /returnFocus=\{\(\) => menuButtonRef\.current\}/);
  assert.match(composer, /button\[aria-haspopup="menu"\]/);
});

// ── helpers ──

function render(over: Partial<ModelPickerPanelProps>): string {
  const scope = over.scope ?? { kind: 'any' as const };
  const models = buildPickerModels(ROWS);
  const tab = over.tab ?? 'claude';
  const value = over.value === undefined ? { providerId: 'claude', modelId: 'opus' } : over.value;
  const browse = browseRows(models, tab, { value, scope, defaultId: 'claude-opus-5-5' });
  const props: ModelPickerPanelProps = {
    layout: 'popover', idBase: 't', rows: ROWS, entries: railEntries(ROWS, USAGE, scope), shown: visibleRows(models, over.query ?? '', tab),
    scope, value, browse, tab, onTab: () => {}, query: '', onQuery: () => {},
    active: 'claude:sonnet', onActive: () => {}, onPick: () => {},
    renderMark: (id, label, size) => React.createElement('i', { 'data-mark': id, 'data-size': size }, label[0]),
    onSignIn: null, onNewAgent: null, onAddProvider: null,
    ...over,
  };
  return renderToStaticMarkup(React.createElement(ModelPickerPanel, props));
}

test('effort as a default can be put back to the CLI\'s own: a "CLI default" toggle beside the slider, null when on', async () => {
  const { readFileSync } = await import('node:fs');
  const a = readFileSync(new URL('../components/settings/sections/AgentsSection.tsx', import.meta.url), 'utf8');
  assert.match(a, /effortClearable/);
  const p = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(p, /onEffortChange\?: \(level: string \| null\) => void/);
});

// ── one row per family ("go ahead with the picker regrouping") ──

// The static catalogs in config/providers.json, plus the explicit 1M Opus an agent's own list carries.
const CATALOG: ProviderAvailability[] = [
  { id: 'claude', label: 'Claude', logo_svg: '', installed: true, authed: true, model_catalog_default: 'claude-opus-5-5', models: [
    pm('claude-opus-5-5', 'Opus 5.5', 200_000), { ...pm('claude-opus-5-5[1m]', 'Opus 5.5 (1M)'), seat_only: true },
    pm('claude-fable-5-1', 'Fable 5.1'), pm('claude-fable-5', 'Fable 5'), pm('claude-opus-5', 'Opus 5', 200_000),
    pm('claude-opus-4-8', 'Opus 4.8', 200_000), pm('claude-opus-4-6', 'Opus 4.6', 200_000), pm('claude-sonnet-5', 'Sonnet 5', 200_000),
    pm('claude-haiku-4-5-20251001', 'Haiku 4.5', 200_000)] },
  { id: 'codex', label: 'Codex', logo_svg: '', installed: true, authed: true, models: [
    pm('gpt-5.6-terra', 'GPT-5.6 Terra'), pm('gpt-5.6-luna', 'GPT-5.6 Luna'), pm('gpt-6-astra', 'GPT-6 Astra')] },
  { id: 'gemini', label: 'Gemini', logo_svg: '', installed: true, authed: true, models: [
    pm('gemini-3.8-flash-high', 'Gemini 3.8 Flash (High)'), pm('gemini-3.8-flash-low', 'Gemini 3.8 Flash (Low)'),
    pm('gemini-3.7-flash-high', 'Gemini 3.7 Flash (High)', 1_000_000), pm('gemini-3.7-flash-low', 'Gemini 3.7 Flash (Low)', 1_000_000),
    pm('gemini-3.1-pro-high', 'Gemini 3.1 Pro (High)')] },
  { id: 'grok', label: 'Grok', logo_svg: '', installed: true, authed: true, models: [
    pm('grok-4.7', 'Grok 4.7', 256_000), pm('grok-4.7-build-fast', 'Grok 4.7 Build Fast'), pm('grok-4.6', 'Grok 4.6'), pm('grok-4.5', 'Grok 4.5')] },
];
const CAT = buildPickerModels(buildProviderRows(CATALOG));
const ANY = { kind: 'any' as const };
const names = (ms: PickerModel[]) => ms.map((m) => (m.tag ? `${m.name} · ${m.tag}` : m.name));

test('family: the name (or the id) without its version, size, date or provider word; none detectable: its own row', () => {
  const f = (p: string, s: string) => { const r = familyOf(p, s); return [r.family, r.version.join('.')]; };
  assert.deepEqual(f('claude', 'Opus 5.5'), ['opus', '5.5']);
  assert.deepEqual(f('claude', 'claude-opus-5-5[1m]'), ['opus', '5.5'], 'an id: hyphenated version, [1m] tag');
  assert.deepEqual(f('claude', 'claude-opus-5-5'), ['opus', '5.5']);
  assert.deepEqual(f('claude', 'claude-haiku-4-5-20251001'), ['haiku', '4.5'], 'a date stamp is not the version');
  assert.deepEqual(f('claude', 'Fable 5.1'), ['fable', '5.1']);
  assert.deepEqual(f('codex', 'GPT-5.6 Terra'), ['terra', '5.6']);
  assert.deepEqual(f('codex', 'gpt-5.6-terra'), ['terra', '5.6'], 'the name and the id agree');
  assert.deepEqual(f('codex', 'GPT-6 Astra'), ['astra', '6']);
  assert.deepEqual(f('gemini', 'Gemini 3.8 Flash (High)'), ['flash high', '3.8'], "Gemini's level is part of the model");
  assert.deepEqual(f('gemini', 'gemini-3.8-flash-high'), ['flash high', '3.8']);
  assert.deepEqual(f('grok', 'Grok 4.7'), ['grok', '4.7'], 'only the provider word: it is the family');
  assert.deepEqual(f('grok', 'grok-4.7-build-fast'), ['build fast', '4.7']);
  assert.deepEqual(f('claude', 'Opus 5.5 1M'), ['opus', '5.5'], 'a size word is not the family');
  assert.equal(familyOf('grok', '4.7').family, null, 'nothing but a number: no family');
});

test('grouping: one row per family, named by its newest version; the rest under "Older versions"', () => {
  const claude = familiesFor(CAT, 'claude');
  assert.deepEqual(claude.map((f) => f.newest[0].name), ['Opus 5.5', 'Fable 5.1', 'Sonnet 5', 'Haiku 4.5']);
  assert.deepEqual(names(claude[0].newest), ['Opus 5.5 · 200K', 'Opus 5.5 · 1M'], 'the newest version in every size, standard first');
  assert.deepEqual(names(claude[0].older), ['Opus 5 · 200K', 'Opus 4.8 · 200K', 'Opus 4.6 · 200K']);
  assert.deepEqual(names(claude[1].older), ['Fable 5']);
  assert.deepEqual(familiesFor(CAT, 'codex').map((f) => f.newest[0].name), ['GPT-6 Astra', 'GPT-5.6 Terra', 'GPT-5.6 Luna']);
  assert.deepEqual(familiesFor(CAT, 'gemini').map((f) => f.newest[0].name), ['Gemini 3.8 Flash (High)', 'Gemini 3.8 Flash (Low)', 'Gemini 3.1 Pro (High)']);
  assert.deepEqual(familiesFor(CAT, 'grok').map((f) => names(f.newest)), [['Grok 4.7 · 256K'], ['Grok 4.7 Build Fast']]);
  assert.deepEqual(names(familiesFor(CAT, 'grok')[0].older), ['Grok 4.6', 'Grok 4.5']);
  // The list a provider shows: its families, then the disclosure (closed by default), then nothing else.
  const b = browseRows(CAT, 'claude', { value: { providerId: 'claude', modelId: 'claude-sonnet-5' }, scope: ANY, defaultId: 'claude-opus-5-5' });
  assert.deepEqual(b.rows.map((r) => r.kind), ['family', 'family', 'family', 'family', 'older']);
  assert.equal(b.olderOpen, false);
  assert.deepEqual(b.rows.find((r) => r.kind === 'older'), { kind: 'older', key: olderKey('claude'), providerId: 'claude', open: false });
  // Opened: one row per concrete older model, sizes included.
  const open = browseRows(CAT, 'claude', { value: null, scope: ANY, olderOpen: true });
  assert.deepEqual(names(open.rows.flatMap((r) => (r.kind === 'model' ? [r.model] : []))), ['Opus 5 · 200K', 'Opus 4.8 · 200K', 'Opus 4.6 · 200K', 'Fable 5']);
  assert.equal(browseRows(CAT, 'codex', { value: null, scope: ANY }).rows.some((r) => r.kind === 'older'), false, 'nothing older: no disclosure');
});

test('context: 200K | 1M for a family offered in two sizes; a family pick keeps the size in use when it has it', () => {
  const [opus, , sonnet] = familiesFor(CAT, 'claude');
  assert.deepEqual(contextChoices(opus).map((c) => [c.label, c.tag, c.model.id]), [['200K', '200K', 'claude-opus-5-5'], ['1M', '1M', 'claude-opus-5-5[1m]']]);
  assert.deepEqual(contextChoices(sonnet).length, 1, 'one size: no control');
  assert.equal(resolveFamily(opus, '1M', ANY).id, 'claude-opus-5-5[1m]', 'row + size → the concrete id');
  assert.equal(resolveFamily(opus, '200K', ANY).id, 'claude-opus-5-5');
  assert.equal(resolveFamily(opus, null, ANY, 'claude-opus-5-5').id, 'claude-opus-5-5', 'no size in use: the default');
  assert.equal(resolveFamily(sonnet, '1M', ANY).id, 'claude-sonnet-5', 'a size it lacks: its standard');
  // On a 1M Opus, picking Sonnet then Opus again comes back on 1M; the row stands for that concrete model.
  const on1m = browseRows(CAT, 'claude', { value: { providerId: 'claude', modelId: 'claude-opus-5-5[1m]' }, scope: ANY, defaultId: 'claude-opus-5-5' });
  const fam = on1m.rows.filter((r) => r.kind === 'family');
  assert.equal(fam[0].kind === 'family' && fam[0].model.id, 'claude-opus-5-5[1m]');
  assert.equal(fam[0].kind === 'family' && fam[0].current, true, 'the Opus row is ticked on a non-standard size');
  assert.equal(fam[2].kind === 'family' && fam[2].model.id, 'claude-sonnet-5');
  const ctl = contextControl(on1m, { providerId: 'claude', modelId: 'claude-opus-5-5[1m]' }, ANY)!;
  assert.deepEqual(ctl.choices.map((c) => [c.id, c.label]), [['200K', '200K'], ['1M', '1M']]);
  assert.equal(ctl.value, '1M');
  assert.equal(contextControl(browseRows(CAT, 'claude', { value: { providerId: 'claude', modelId: 'claude-sonnet-5' }, scope: ANY }),
    { providerId: 'claude', modelId: 'claude-sonnet-5' }, ANY), null, 'a family in one size: no control');
  // Arturo's brain can't use the 1M Opus: the size is offered dimmed with the reason, a family pick falls back.
  const arturo = buildPickerModels(buildProviderRows(CATALOG), null, { forArturo: true });
  const aOpus = familiesFor(arturo, 'claude')[0];
  assert.equal(resolveFamily(aOpus, '1M', ANY).id, 'claude-opus-5-5', 'never resolves to a model it cannot use');
  // Arturo's brain can't use the 1M Opus: one usable size left, so no Context control at all (never a dimmed size).
  assert.equal(contextControl(browseRows(arturo, 'claude', { value: { providerId: 'claude', modelId: 'claude-opus-5-5' }, scope: ANY }),
    { providerId: 'claude', modelId: 'claude-opus-5-5' }, ANY), null);
  // The panel draws it beside Effort, in the same style.
  const html = render({ context: { choices: ctl.choices, value: '1M', onChange: () => {} }, effort: { levels: EFFORT.claude.levels, value: 'high', onChange: () => {} } });
  assert.match(html, /data-model-context(?:(?!data-model-effort).)*role="radiogroup"/);
  assert.match(html, /data-model-effort(?:(?!role="radiogroup").)*type="range"/, 'Effort is a slider, not a segmented control');
  assert.match(html, />Context</);
  assert.match(html, /role="radio" aria-checked="true"[^>]*data-context="1M"/);
  assert.match(html, /role="radio" aria-checked="false"[^>]*data-context="200K"[^>]*>200K</);
  assert.ok(html.indexOf('data-model-context') < html.indexOf('data-model-effort'), 'Context, then Effort');
  assert.doesNotMatch(render({}), /data-model-context/);
});

test('an older model in use stays visible and ticked: "Older versions" opens on it; no family row claims it', () => {
  const value = { providerId: 'claude', modelId: 'claude-opus-4-8' };
  const b = browseRows(CAT, 'claude', { value, scope: ANY, defaultId: 'claude-opus-5-5' });
  assert.equal(b.olderOpen, true, 'opened for the model in use');
  assert.ok(b.rows.some((r) => r.kind === 'model' && r.model.id === 'claude-opus-4-8'));
  assert.ok(b.rows.every((r) => r.kind !== 'family' || !r.current), 'the Opus row is the newest, not the one in use');
  assert.equal(contextControl(b, value, ANY), null, 'no size control for an older model');
  assert.equal(browseRows(CAT, 'claude', { value, scope: ANY, olderOpen: false }).olderOpen, false, 'closing it is honoured');
  const keys = pickableKeys(b.rows, ANY);
  assert.ok(keys.indexOf(olderKey('claude')) < keys.indexOf('claude:claude-opus-4-8'), 'the arrows reach the disclosure and its rows');
  assert.deepEqual(navKey('Enter', { tabs: ['claude'], tab: 'claude', keys, active: olderKey('claude'), query: '' }), { kind: 'pick', key: olderKey('claude') });
  // Drawn: the disclosure is open, and the older row is the one in use (tinted, checked).
  const html = renderCat({ value, browse: b });
  assert.match(html, /aria-label="Older versions, shown"[^>]*data-model-older="claude"/);
  assert.doesNotMatch(html, /data-model-older="claude"[^>]*aria-expanded/, 'aria-expanded is not an option state: the name says it');
  assert.match(html, /data-model-option="claude:claude-opus-4-8"[^>]*class="[^"]*bg-tone-800 [^"]*font-medium(?:(?!data-model-option).)*In use/);
  assert.equal((html.match(/aria-label="In use"/g) ?? []).length, 1);
  assert.match(html, /Older versions/);
  // Closed (a newer model in use): the older rows are not drawn, the disclosure is.
  const closed = renderCat({ value: { providerId: 'claude', modelId: 'claude-sonnet-5' } });
  assert.match(closed, /aria-label="Older versions, hidden"[^>]*data-model-older="claude"/);
  assert.doesNotMatch(closed, /data-model-option="claude:claude-opus-4-8"/);
  assert.equal((closed.match(/role="option"[^>]*data-model-option="claude:/g) ?? []).length, 4, 'one row per family');
});

test('a family row: the newest name, purpose, "default", the check; no radio; its size only when it has one size', () => {
  const html = renderCat({ value: { providerId: 'claude', modelId: 'claude-opus-5-5' } });
  assert.match(html, /data-model-option="claude:claude-opus-5-5"[^>]*bg-tone-800 [^"]*font-medium(?:(?!data-model-option).)*>Opus 5\.5<(?:(?!data-model-option).)*Deep work · long tasks(?:(?!data-model-option).)*data-default-pill(?:(?!data-model-option).)*In use/);
  assert.doesNotMatch(html, />Opus 5\.5<span/, 'the sizes are the Context control');
  assert.doesNotMatch(html, /type="radio"|rounded-full border-2/, 'no per-row radio circle');
  assert.match(renderCat({ tab: 'grok', value: null }), />Grok 4\.7<span[^>]*> · (?:<!-- -->)?256K</, 'one size: said on the row, as before');
});

test('search is still flat over every concrete model, every version and size', () => {
  assert.deepEqual(names(visibleRows(CAT, 'opus', 'grok')), ['Opus 5.5 · 200K', 'Opus 5.5 · 1M', 'Opus 5 · 200K', 'Opus 4.8 · 200K', 'Opus 4.6 · 200K']);
  const html = renderCat({ query: 'opus', shown: visibleRows(CAT, 'opus', 'claude') });
  assert.doesNotMatch(html, /data-model-older/, 'no disclosure while searching');
  assert.match(html, /data-model-option="claude:claude-opus-5-5\[1m\]"/);
});

test('wiring: the container groups by family, opens older versions, and picks a size without closing', () => {
  const src = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(src, /browseRows\(models, shownTab/);
  assert.match(src, /contextControl\(/);
  assert.match(src, /k\.startsWith\('older:'\)/);
  assert.match(src, /pickableKeys\(browse\.rows, scope\)/);
});

function renderCat(over: Partial<ModelPickerPanelProps>): string {
  const rows = buildProviderRows(CATALOG);
  const tab = over.tab ?? 'claude';
  const value = over.value === undefined ? null : over.value;
  return renderToStaticMarkup(React.createElement(ModelPickerPanel, {
    layout: 'popover', idBase: 'c', rows, entries: railEntries(rows, [], ANY), shown: visibleRows(CAT, over.query ?? '', tab), scope: ANY, value,
    browse: browseRows(CAT, tab, { value, scope: ANY, defaultId: tab === 'claude' ? 'claude-opus-5-5' : null }), tab, onTab: () => {},
    query: '', onQuery: () => {}, active: null, onActive: () => {}, onPick: () => {}, renderMark: (id) => React.createElement('i', null, id),
    ...over,
  }));
}

// ── review (coordinator, 2026-11-03): labels with "context", ids in the label, dated ids, provider words ──

test('family: "context"/"with" and a repeated id in the label are not the family; such labels group and give the Context control', () => {
  const f = (s: string, p = 'claude') => { const r = familyOf(p, s); return [r.family, r.version.join('.')]; };
  assert.deepEqual(f('Opus 5 with 1M context'), ['opus', '5']);
  assert.deepEqual(f('Opus 5.5 (1M context)'), ['opus', '5.5']);
  assert.deepEqual(f('Sonnet (claude-sonnet-5)'), ['sonnet', '5'], 'the id in brackets repeats the model: its version, not its words');
  assert.deepEqual(f('Opus (1M context) (claude-opus-5[1m])'), ['opus', '5']);
  assert.deepEqual(nameAndTag('claude-opus-5', 'Opus 5 with 1M context'), { name: 'Opus 5', tag: '1M' });
  assert.deepEqual(nameAndTag('x', 'Opus 5.5 (1M context)'), { name: 'Opus 5.5', tag: '1M' });
  assert.equal(nameAndTag('claude-opus-5', 'Opus (1M context) (claude-opus-5[1m])').tag, '1M');
  const group = (models: ReturnType<typeof pm>[]) => {
    const ms = buildPickerModels(buildProviderRows([{ ...CATALOG[0], models }]));
    const fams = familiesFor(ms, 'claude');
    const cur = fams[0].newest[0];
    const ctl = contextControl(browseRows(ms, 'claude', { value: { providerId: 'claude', modelId: cur.id }, scope: ANY }), { providerId: 'claude', modelId: cur.id }, ANY);
    return { fams: fams.length, sizes: ctl?.choices.map((c) => c.label) ?? null };
  };
  const two = { fams: 1, sizes: ['200K', '1M'] };
  assert.deepEqual(group([pm('claude-opus-5', 'Opus 5', 200_000), pm('claude-opus-5-1m', 'Opus 5 with 1M context')]), two);
  assert.deepEqual(group([pm('claude-opus-5-5', 'Opus 5.5', 200_000), pm('claude-opus-5-5[1m]', 'Opus 5.5 (1M context)')]), two);
  assert.deepEqual(group([pm('claude-opus-5', 'Opus 5', 200_000), pm('claude-opus-5[1m]', 'Opus (1M context) (claude-opus-5[1m])')]), two);
  assert.deepEqual(group([pm('claude-sonnet-5', 'Sonnet 5', 200_000), pm('claude-sonnet-5[1m]', 'Sonnet (claude-sonnet-5[1m])')]), two);
});

test('version: a date stamp or a zero-led number after the version never counts ("grok-4-0709" is 4, not newer than 4.7)', () => {
  assert.deepEqual(familyOf('grok', 'grok-4-0709').version, [4]);
  assert.deepEqual(familyOf('codex', 'gpt-4o-2024-08-06').version, [4]);
  assert.deepEqual(familyOf('claude', 'claude-haiku-4-5-20251001').version, [4, 5]);
  const ms = buildPickerModels(buildProviderRows([{ ...CATALOG[3], models: [pm('grok-4-0709', 'grok-4-0709'), pm('grok-4.7', 'Grok 4.7')] }]));
  const [grok] = familiesFor(ms, 'grok');
  assert.equal(grok.newest[0].id, 'grok-4.7');
  assert.deepEqual(grok.older.map((m) => m.id), ['grok-4-0709']);
});

test('provider words: dropped only in front, so "gpt-5.1-codex-mini" and "gpt-5-mini" are different families', () => {
  assert.equal(familyOf('codex', 'gpt-5.1-codex-mini').family, 'codex mini');
  assert.equal(familyOf('codex', 'gpt-5-mini').family, 'mini');
  assert.equal(familyOf('codex', 'GPT-5.6 Terra').family, 'terra');
  assert.equal(familyOf('grok', 'Grok 4.7').family, 'grok');
});

test('context: one choice per size, never "Standard | Standard"; an untagged standard size is "Standard"', () => {
  const opus = { key: 'k', providerId: 'claude', older: [], newest: [
    { ...byName(CAT, 'Opus 5.5'), key: 'a', id: 'a' }, { ...byName(CAT, 'Opus 5.5'), key: 'b', id: 'b' },
    { ...CAT.find((m) => m.tag === '1M')!, key: 'c' }] };
  assert.deepEqual(contextChoices(opus).map((c) => [c.tag, c.label, c.model.id]), [['200K', '200K', 'a'], ['1M', '1M', 'claude-opus-5-5[1m]']]);
  const untagged = { ...opus, newest: [{ ...opus.newest[0], tag: undefined }, opus.newest[2]] };
  assert.deepEqual(contextChoices(untagged).map((c) => c.label), ['Standard', '1M']);
});

test("an agent whose list excludes the newest version: its row is the newest version it may use; the rest dimmed under older", () => {
  const rows = buildProviderRows(CATALOG);
  const own = { providerId: 'claude', models: [pm('claude-opus-5', 'Opus 5', 200_000), pm('claude-sonnet-5', 'Sonnet 5', 200_000)] };
  const ms = buildPickerModels(rows, own);
  const agent = scopeFor(own);
  const [opus] = familiesFor(ms, 'claude');
  assert.deepEqual(names(opus.newest), ['Opus 5 · 200K'], 'the newest it may use');
  assert.deepEqual(names(opus.older), ['Opus 5.5 · 200K', 'Opus 5.5 · 1M', 'Opus 4.8 · 200K', 'Opus 4.6 · 200K']);
  assert.ok(opus.older.slice(0, 2).every((m) => m.unavailable === 'Not allowed for this agent'), 'dimmed with why, never hidden');
  const b = browseRows(ms, 'claude', { value: { providerId: 'claude', modelId: 'claude-sonnet-5' }, scope: agent });
  const fam = b.rows.filter((r) => r.kind === 'family');
  assert.equal(fam[0].kind === 'family' && fam[0].model.id, 'claude-opus-5');
  assert.equal(canPick(fam[0].model, agent), true);
  // A family it may use none of stays as its newest version, dimmed (Fable).
  const fable = fam.find((r) => r.kind === 'family' && r.model.name.startsWith('Fable'));
  assert.equal(fable?.kind === 'family' && fable.model.name, 'Fable 5.1');
  assert.equal(fable?.kind === 'family' && canPick(fable.model, agent), false);
});

test('review wiring: the disclosure keeps focus in the search box; a size pick in an agent closes the picker', () => {
  const panel = readFileSync(new URL('../components/kit/ModelPickerPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /data-model-older=\{r\.providerId\}[^>]*onMouseDown=\{\(e\) => e\.preventDefault\(\)\}/);
  const src = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(src, /if \(scope\.kind === 'agent'\) close\(\);/, "the agent's model updates after the switch: close rather than show a stale size");
});

// ── declutter ("go ahead, use the slider for effort") ──

test('declutter 1: the header is the provider name only; status, usage (with "as of") and version are on the rail mark', () => {
  const html = render({});
  assert.match(html, /data-model-picker-header="claude"[^>]*><span[^>]*>Claude<\/span><\/div>/, 'the name, nothing else');
  const header = html.slice(html.indexOf('data-model-picker-header'), html.indexOf('role="combobox"'));
  assert.doesNotMatch(header, /signed in|weekly|v2\.1\.295/);
  // The full words, stale reading included, in the mark's title and aria-label.
  const stale = [{ provider: 'claude', window: 'weekly', used_pct: 50, stale: true, as_of: 1_793_612_401 }];
  const [claude] = railEntries(ROWS, stale, ANY, new Date(1_793_700_000_000));
  assert.match(railLabel(claude), /^Claude: signed in, 50% of weekly used \(as of .+\), CLI v2\.1\.295$/);
  const at = render({ entries: [claude, ...railEntries(ROWS, [], ANY).slice(1)] });
  assert.match(at, /data-rail="claude"/);
  assert.match(at, /title="Claude: signed in, 50% of weekly used \(as of [^"]+\), CLI v2\.1\.295"/);
  assert.equal(railLabel(railEntries(ROWS, [], ANY)[1]), 'Codex: not signed in');
  // A provider that needs action keeps its banner.
  assert.match(render({ tab: 'codex', onSignIn: () => {} }), /data-model-picker-banner="codex"/);
});

test('declutter 2: "default" marks only where the picker sets a default (Settings, Arturo), not in an agent', () => {
  assert.equal(showsDefaultMark(ANY), true);
  assert.equal(showsDefaultMark(scopeFor({ providerId: 'claude' })), false);
  assert.match(render({}), /data-default-pill/);
  assert.doesNotMatch(render({ scope: scopeFor({ providerId: 'claude' }) }), /data-default-pill/);
});

test('declutter 3: Context offers only sizes usable here (fewer than 2: no control, no reason line); effort only the agent\'s levels', () => {
  const rows = buildProviderRows(CATALOG);
  const own = { providerId: 'claude', models: [pm('claude-opus-5-5', 'Opus 5.5', 200_000), pm('claude-sonnet-5', 'Sonnet 5', 200_000)] };
  const ms = buildPickerModels(rows, own);
  const agent = scopeFor(own);
  const value = { providerId: 'claude', modelId: 'claude-opus-5-5' };
  assert.equal(contextControl(browseRows(ms, 'claude', { value, scope: agent }), value, agent), null, '1M not allowed for this agent: no control');
  const both = contextControl(browseRows(CAT, 'claude', { value, scope: ANY }), value, ANY)!;
  assert.deepEqual(both.choices.map((c) => c.id), ['200K', '1M']);
  assert.ok(both.choices.every((c) => !('disabled' in c)), 'never a dimmed size');
  const panel = readFileSync(new URL('../components/kit/ModelPickerPanel.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(panel, /data-model-context-reason|context-why/);
  // Effort: the agent's own levels when the gateway names them.
  const info = { runtime: 'claude', model: 'opus', how: 'live', effort_how: 'live', allowed: [], models: [] };
  assert.deepEqual(agentEffortLevels({ ...info, effort_levels: ['low', 'high', 'turbo'] })?.map((l) => l.id), ['low', 'high']);
  assert.deepEqual(agentEffortLevels(info)?.map((l) => l.id), ['low', 'medium', 'high', 'xhigh', 'max'], 'no list: the runtime\'s');
  const picker = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(picker, /effortLevels\?: EffortLevel\[\] \| null/);
  const composer = readFileSync(new URL('../components/agent/Composer.tsx', import.meta.url), 'utf8');
  assert.match(composer, /effortLevels=\{agentEffortLevels\(agentModel\)\}/);
});

test('declutter 4: no permanent "Applies from your next message"; an unusual timing still says so', () => {
  const info = { runtime: 'claude', model: 'opus', allowed: [], models: [] };
  assert.equal(applyNote({ ...info, how: 'live' }), null);
  assert.equal(applyNote({ ...info, how: 'next_rotation' }), "Applies from this agent's next rotation");
  assert.doesNotMatch(render({ note: null }), /data-model-picker-note/);
  assert.doesNotMatch(readFileSync(new URL('./agentModel.ts', import.meta.url), 'utf8'), /'Applies from your next message'/);
});

test('rail-all-providers: every configured provider has a rail tab, signed in or not, installed or not; the unusable ones dimmed with their reason', () => {
  const all = railEntries(ROWS, USAGE, ANY);
  assert.deepEqual(all.map((e) => e.id), ['claude', 'codex', 'gemini', 'grok']);
  const html = render({});
  for (const id of ['claude', 'codex', 'gemini', 'grok']) assert.match(html, new RegExp(`data-rail="${id}"`), `${id} has a rail tab`);
  const tab = (id: string) => html.match(new RegExp(`<button[^>]*data-rail="${id}"[^>]*>`))![0];
  assert.match(tab('codex'), /aria-label="Codex: not signed in"/);
  assert.match(tab('codex'), /opacity-60/, 'not signed in: dimmed');
  assert.match(tab('gemini'), /aria-label="Gemini: not installed"/);
  assert.match(tab('gemini'), /opacity-60/, 'not installed: dimmed');
  assert.doesNotMatch(tab('claude'), /opacity-60/);
  assert.doesNotMatch(tab('grok'), /opacity-60/);
  // The agent's picker too: every provider, the others with the scope reason.
  const agent = scopeFor({ providerId: 'claude' });
  const ah = render({ scope: agent, entries: railEntries(ROWS, USAGE, agent) });
  for (const id of ['claude', 'codex', 'gemini', 'grok']) assert.match(ah, new RegExp(`data-rail="${id}"`));
  // The container passes the whole rail; no hiding helpers left.
  const src = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(src, /const entries = useMemo\(\(\) => railEntries\(rows, usage, scope\), \[rows, usage, scope\]\);/);
  assert.doesNotMatch(src, /shownEntries|hiddenEntries/);
  const lib = readFileSync(new URL('./modelPickerPanel.ts', import.meta.url), 'utf8');
  assert.doesNotMatch(lib, /export function (shownEntries|hiddenEntries)/);
});

test('rail-all-providers: a signed-out provider\'s banner says so with Sign in; its models are listed, dimmed, unpickable', () => {
  const models = buildPickerModels(ROWS);
  const browse = browseRows(models, 'codex', { value: { providerId: 'claude', modelId: 'opus' }, scope: ANY, defaultId: null });
  const html = render({ tab: 'codex', browse, onSignIn: () => {} });
  assert.match(html, /data-model-picker-header="codex"[^>]*><span[^>]*>Codex<\/span><\/div>/, 'header: the name only');
  assert.match(html, /data-model-picker-banner="codex"[^>]*><span>Codex is not signed in on this machine\.<\/span>/);
  assert.match(html, /data-model-connect="codex"[^>]*>Sign in<\/button>/);
  assert.match(html, /GPT-6 Astra/, 'its models are shown');
  assert.match(html, /role="option"[^>]*aria-disabled="true"(?:(?!role="option").)*GPT-6 Astra/, 'dimmed: aria-disabled');
  assert.equal(canPick(byName(models, 'GPT-6 Astra'), ANY), false, 'not signed in: not pickable');
  assert.deepEqual(pickableKeys(browse.rows, ANY), [], 'Enter skips them');
  // Not installed: says so, Sign in opens the Connect sheet (which installs).
  const gb = browseRows(models, 'gemini', { value: null, scope: ANY, defaultId: null });
  const g = render({ tab: 'gemini', browse: gb, onSignIn: () => {} });
  assert.match(g, /Gemini is not installed on this machine\./);
  assert.match(g, /data-model-connect="gemini"/);
  assert.match(g, /Gemini 3\.1 Pro/);
});

test('rail-all-providers: with no Connect flow (deployment has it off) the banner gives the CLI\'s own sign-in / install', () => {
  const models = buildPickerModels(ROWS);
  const c = render({ tab: 'codex', browse: browseRows(models, 'codex', { value: null, scope: ANY }), onSignIn: null });
  assert.doesNotMatch(c, /data-model-connect=/);
  assert.match(c, /data-model-banner-signin-note="codex"[^>]*>Sign in from the CLI on this machine: <code[^>]*>codex login --device-auth<\/code>/);
  const g = render({ tab: 'gemini', browse: browseRows(models, 'gemini', { value: null, scope: ANY }), onSignIn: null });
  assert.match(g, /data-model-banner-install="gemini"/);
});

test('rail-all-providers: search covers every provider\'s models again; "+" is add a provider (as before the declutter)', () => {
  const models = buildPickerModels(ROWS);
  assert.deepEqual([...new Set(visibleRows(models, 'fast', 'claude').map((m) => m.providerId))], ['claude', 'gemini'], 'a signed-out / not-installed provider\'s models are searched too');
  assert.equal(visibleRows.length, 3, 'no provider filter parameter');
  const src = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(src, /visibleRows\(models, query, shownTab\)/);
  assert.match(src, /onAddProvider=\{showAddProvider\(\) \? \(\) => setAdding\(true\) : null\}/, '"+" only where the deployment can add a provider');
  assert.match(src, /<AddProviderPanel rows=\{rows\} /, 'the add panel covers every provider not usable yet');
  assert.match(render({ onAddProvider: () => {} }), /aria-label="Add a provider" title="Add a provider"/);
});

test('declutter 6: Effort is an N-stop slider: the level named beside "Effort", aria-valuetext, a CLI-default toggle in Settings', () => {
  const levels = EFFORT.claude.levels;
  assert.deepEqual(effortStop(levels, 'high'), { index: 2, name: 'High', unset: false });
  assert.deepEqual(effortStop(levels, null), { index: 2, name: 'CLI default', unset: true }, 'unset: the middle stop, said as CLI default');
  assert.deepEqual(effortStop(EFFORT.codex.levels, null).index, 1);
  assert.deepEqual(effortStop(levels, 'turbo'), { index: 2, name: 'CLI default', unset: true });
  const html = render({ effort: { levels, value: 'high', onChange: () => {} } });
  assert.match(html, /data-model-effort[^>]*>.*>Effort<.*data-effort-name="High"/);
  assert.match(html, /type="range"[^>]*min="0"[^>]*max="4"[^>]*step="1"[^>]*value="2"/);
  assert.match(html, /type="range"[^>]*aria-labelledby="t-effort"[^>]*aria-valuetext="High"/);
  assert.equal((html.match(/data-effort-tick=/g) ?? []).length, 5, 'one tick per level');
  assert.doesNotMatch(html, /data-effort-cli-default/, 'no CLI default outside Settings');
  const codex = render({ effort: { levels: EFFORT.codex.levels, value: 'low', onChange: () => {} } });
  assert.match(codex, /max="3"/, 'N stops: the runtime\'s own levels');
  const unset = render({ effort: { levels, value: null, clearable: true, onChange: () => {} } });
  assert.match(unset, /data-effort-cli-default[^>]*aria-pressed="true"|aria-pressed="true"[^>]*data-effort-cli-default/);
  assert.match(unset, /aria-valuetext="CLI default"/);
  assert.doesNotMatch(unset, /data-effort-name=/, 'the pressed toggle says it; no second "CLI default" beside Effort');
  const set = render({ effort: { levels, value: 'max', clearable: true, onChange: () => {} } });
  assert.match(set, /data-effort-cli-default[^>]*aria-pressed="false"|aria-pressed="false"[^>]*data-effort-cli-default/);
  const src = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(src, /clearable: effortClearable/);
});

test('declutter 7: Context and Effort share one footer line (Context left, Effort right), wrapping when narrow', () => {
  const two = contextControl(browseRows(CAT, 'claude', { value: { providerId: 'claude', modelId: 'claude-opus-5-5' }, scope: ANY }), { providerId: 'claude', modelId: 'claude-opus-5-5' }, ANY)!;
  const html = render({ context: { ...two, onChange: () => {} }, effort: { levels: EFFORT.claude.levels, value: 'high', onChange: () => {} } });
  assert.equal((html.match(/data-model-picker-footer/g) ?? []).length, 1);
  assert.match(html, /data-model-picker-footer[^>]*class="[^"]*flex-wrap|class="[^"]*flex-wrap[^"]*"[^>]*data-model-picker-footer/);
  assert.ok(html.indexOf('data-model-context') < html.indexOf('data-model-effort'));
  assert.match(html, /class="[^"]*ml-auto[^"]*"[^>]*data-model-effort|data-model-effort[^>]*class="[^"]*ml-auto/);
  assert.doesNotMatch(render({ effort: null, context: null }), /data-model-picker-footer/, 'nothing applies: no footer');
});

test('declutter 8: purpose words come from the table, else the CLI\'s own description; never invented', () => {
  assert.equal(modelPurpose('claude', 'Fable 5.1'), null, 'no source: no words');
  assert.equal(modelPurpose('claude', 'Fable 5.1', 'For your toughest challenges'), 'For your toughest challenges');
  assert.equal(modelPurpose('claude', 'Fable 5.1', 'Fable 5.1 · Most capable for your hardest tasks'), 'Most capable for your hardest tasks', 'the name head is dropped');
  assert.equal(modelPurpose('claude', 'Opus 5.5', 'For complex work and everyday tasks'), 'Deep work · long tasks', 'the approved table first');
  assert.equal(modelPurpose('claude', 'Fable 5.1', '  '), null);
  const ms = buildPickerModels(buildProviderRows([{ ...PROVIDERS[0], models: [{ ...pm('fable', 'Fable 5.1'), description: 'For your toughest challenges' }] }]));
  assert.equal(ms[0].description, 'For your toughest challenges', 'the probe\'s description reaches the row');
  const html = renderToStaticMarkup(React.createElement(ModelPickerPanel, {
    layout: 'popover', idBase: 'f', rows: ROWS, entries: railEntries(ROWS, [], ANY), shown: [], scope: ANY, value: null,
    browse: browseRows(ms, 'claude', { value: null, scope: ANY }), tab: 'claude', onTab: () => {}, query: '', onQuery: () => {},
    active: null, onActive: () => {}, onPick: () => {}, renderMark: (id) => React.createElement('i', null, id),
  }));
  assert.match(html, /data-purpose="true">For your toughest challenges</);
});

test('declutter 9: "Older versions" has no count', () => {
  const html = renderCat({ value: { providerId: 'claude', modelId: 'claude-sonnet-5' } });
  assert.match(html, /data-model-older="claude"(?:(?!role="option").)*>Older versions<\/span><\/div>/);
  assert.doesNotMatch(html, /Older versions \(\d+\)/);
});

test('declutter keeps: no per-row radio, tinted row + check, scope reasons in search', () => {
  const agent = scopeFor({ providerId: 'claude' });
  const models = buildPickerModels(ROWS);
  const html = render({ scope: agent, query: 'grok', shown: visibleRows(models, 'grok', 'claude') });
  assert.match(html, /data-model-reason="true">Needs a new agent on Grok</);
  assert.doesNotMatch(render({}), /type="radio"/);
  assert.match(render({}), /aria-selected="true"[^>]*class="[^"]*bg-tone-800[^"]*"(?:(?!role="option").)*In use/);
});

test('declutter 6: the slider keeps its own ←/→, Home/End: the panel\'s keys come only from the search box', () => {
  const src = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(src, /if \(e\.key !== 'Escape' && el !== inputRef\.current\) return;/);
  assert.doesNotMatch(src, /el\.tagName !== 'INPUT'/, 'the range input is an INPUT too');
});

// ── review of 53d6154 (coordinator, 2026-11-03) ──

/** Drive the slider's rules as the component does: a change per stop crossed, a commit on release. */
function drive(prop: number, unset: boolean, steps: (number | 'pointer' | 'key' | 'blur' | 'activate')[]) {
  const calls: number[] = [];
  let d = sliderDraft(prop);
  for (const s of steps) {
    if (typeof s === 'number') { d = sliderMove(d, s); continue; }
    const r = sliderCommit(d, prop, unset, s);
    d = r.draft;
    if (r.call !== null) calls.push(r.call);
  }
  return { calls, shown: d.shown };
}

test('review 1: a drag across 3 stops sends ONE pick, on release; the thumb stays where it was let go', () => {
  const drag = drive(1, false, [2, 3, 4, 'pointer']);
  assert.deepEqual(drag.calls, [4], 'exactly one onIndex, the stop let go on');
  assert.equal(drag.shown, 4, 'no snap back while the switch is on its way');
  // Holding an arrow key: a change per repeat, one call after the key comes up.
  assert.deepEqual(drive(0, false, [1, 2, 3, 'key']).calls, [3]);
  // Back where it started, or a release with no move: nothing sent.
  assert.deepEqual(drive(2, false, [3, 2, 'pointer']).calls, []);
  assert.deepEqual(drive(2, false, ['pointer']).calls, []);
  // A blur after the release sends nothing more; a blur mid-move (focus leaves) commits.
  assert.deepEqual(drive(1, false, [3, 'pointer', 'blur']).calls, [3]);
  assert.deepEqual(drive(1, false, [3, 'blur']).calls, [3]);
  // The prop catching up (or moving elsewhere) re-syncs the thumb.
  assert.equal(sliderSync(sliderMove(sliderDraft(1), 3), 1, 3).shown, 3);
  assert.equal(sliderSync(sliderDraft(1), 1, 0).shown, 0);
  assert.equal(sliderSync(sliderMove(sliderDraft(1), 3), 1, 1).shown, 3, 'same prop: a drag in progress is kept');
  const src = readFileSync(new URL('../components/kit/StopSlider.tsx', import.meta.url), 'utf8');
  for (const ev of ['onPointerUp', 'onKeyUp', 'onBlur']) assert.match(src, new RegExp(`${ev}=\\{`), `commits on ${ev}`);
  assert.match(src, /useState/);
});

test('review 2: the hollow (unset) thumb can be picked where it rests: release, Enter or Space', () => {
  assert.deepEqual(drive(2, true, ['pointer']).calls, [2], 'pointer up on the hollow thumb');
  assert.deepEqual(drive(2, true, ['activate']).calls, [2], 'Enter / Space');
  assert.deepEqual(drive(2, true, ['pointer', 'pointer']).calls, [2], 'once, not again before the prop catches up');
  assert.deepEqual(drive(2, true, ['key']).calls, [], 'a Tab key coming up on it picks nothing');
  assert.deepEqual(drive(2, true, ['blur']).calls, [], 'tabbing through picks nothing');
  assert.deepEqual(drive(2, false, ['activate']).calls, [], 'a set level: Enter / Space change nothing');
  assert.deepEqual(drive(2, true, [3, 'pointer']).calls, [3]);
  const src = readFileSync(new URL('../components/kit/StopSlider.tsx', import.meta.url), 'utf8');
  assert.match(src, /e\.key === 'Enter' \|\| e\.key === ' '/);
});

test('rail-all-providers: the search box says "Search all models" again (it searches every provider)', () => {
  const html = render({});
  assert.match(html, /placeholder="Search all models" aria-label="Search all models"/);
});

test('review 4: "+" panel: a signed-out provider with no connect action says how to sign in from its CLI', () => {
  const src = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(src, /signinCommand\(r\.provider\.id\)/);
  assert.match(src, /Sign in from the CLI on this machine/);
  assert.match(src, /data-model-add-signin-note/);
});

test('review 5: the hover title is React state, never a direct DOM write', () => {
  const src = readFileSync(new URL('../components/kit/StopSlider.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(src, /\.title = /);
  assert.match(src, /title=\{hoverName \?\? valueText\}/);
});

// ── re-review of 71a28ba (coordinator, 2026-11-03) ──

/**
 * A component harness with no DOM (the dashboard has none): StopSlider runs as itself, its own useState / useRef
 * through React 19's hook dispatcher, re-rendered until its render-phase updates settle, as React does; events are
 * its own handlers called with a minimal event. Props change through rerender(), as a parent would.
 */
type Handler = (e: unknown) => void;
function mountSlider(first: StopSliderProps) {
  const internals = (React as unknown as Record<string, { H: unknown }>).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
  const slots: unknown[] = [];
  let at = 0;
  let dirty = false;
  const dispatcher = {
    useState(init: unknown) {
      const k = at++;
      if (!(k in slots)) slots[k] = typeof init === 'function' ? (init as () => unknown)() : init;
      const set = (v: unknown) => {
        const next = typeof v === 'function' ? (v as (p: unknown) => unknown)(slots[k]) : v;
        if (!Object.is(next, slots[k])) { slots[k] = next; dirty = true; }
      };
      return [slots[k], set];
    },
    useRef(v: unknown) { const k = at++; if (!(k in slots)) slots[k] = { current: v }; return slots[k]; },
  };
  let props = first;
  let tree: React.ReactNode = null;
  const render = () => {
    const prev = internals.H;
    internals.H = dispatcher;
    try {
      let n = 0;
      do { dirty = false; at = 0; tree = (StopSlider as (p: StopSliderProps) => React.ReactNode)(props); } while (dirty && ++n < 25);
      assert.ok(n < 25, 'render settles');
    } finally { internals.H = prev; }
  };
  const input = (): { props: Record<string, unknown> } => {
    const walk = (node: unknown): { props: Record<string, unknown> } | null => {
      if (!node || typeof node !== 'object') return null;
      if (Array.isArray(node)) { for (const c of node) { const f = walk(c); if (f) return f; } return null; }
      const el = node as { type?: unknown; props?: Record<string, unknown> };
      if (el.type === 'input') return el as { props: Record<string, unknown> };
      return walk(el.props?.children);
    };
    const f = walk(tree);
    assert.ok(f, 'the range input');
    return f!;
  };
  const fire = (name: string, e: Record<string, unknown> = {}) => {
    const h = input().props[name] as Handler | undefined;
    assert.ok(h, `${name} handler`);
    h({ preventDefault() {}, currentTarget: { value: String(e.value ?? ''), getBoundingClientRect: () => ({ left: 0, width: 0 }) }, ...e });
    render();
  };
  render();
  return {
    rerender(next: Partial<StopSliderProps>) { props = { ...props, ...next }; render(); },
    fire,
    value: () => Number(input().props.value),
    move: (i: number) => fire('onChange', { value: i }),
  };
}

const LEVELS = ['Low', 'Medium', 'High', 'Extra high', 'Max'];

test('re-review: after a prop change, the hollow thumb picks the middle stop (no stale draft from the last drag)', () => {
  const calls: number[] = [];
  const s = mountSlider({ stops: LEVELS, index: 2, valueText: 'High', onIndex: (i) => calls.push(i), labelledBy: 'x' });
  s.move(3); s.move(4); s.fire('onPointerUp');
  assert.deepEqual(calls, [4], 'the drag to Max: one pick');
  s.rerender({ index: 4, valueText: 'Max' });            // the switch landed
  s.rerender({ index: 2, valueText: 'CLI default', unset: true });   // "CLI default" pressed
  assert.equal(s.value(), 2, 'the hollow thumb on the middle stop');
  s.fire('onPointerUp');
  assert.deepEqual(calls, [4, 2], 'a tap on the hollow thumb picks the middle stop');
});

test('re-review: a refused pick reverts, and the same stop can be tried again', () => {
  const calls: number[] = [];
  const s = mountSlider({ stops: LEVELS, index: 2, valueText: 'High', onIndex: (i) => calls.push(i), labelledBy: 'x' });
  s.move(4); s.fire('onPointerUp');
  s.rerender({ index: 4, valueText: 'Max' });            // optimistic
  s.rerender({ index: 2, valueText: 'High' });           // refused: reverted
  assert.equal(s.value(), 2, 'the thumb follows the revert');
  s.move(4); s.fire('onPointerUp');
  assert.deepEqual(calls, [4, 4], 'the retry goes out');
  // Even with no prop change at all (a refusal that never moved the value), moving there again sends again.
  const c2: number[] = [];
  const t = mountSlider({ stops: LEVELS, index: 2, valueText: 'High', onIndex: (i) => c2.push(i), labelledBy: 'x' });
  t.move(4); t.fire('onPointerUp'); t.move(3); t.move(4); t.fire('onPointerUp');
  assert.deepEqual(c2, [4, 4]);
  assert.deepEqual(drive(2, false, [4, 'pointer', 3, 4, 'pointer']).calls, [4, 4]);
});

test('re-review: a drag across stops is one pick in the component too; a cancelled pointer commits', () => {
  const calls: number[] = [];
  const s = mountSlider({ stops: LEVELS, index: 0, valueText: 'Low', onIndex: (i) => calls.push(i), labelledBy: 'x' });
  s.move(1); s.move(2); s.move(3);
  assert.equal(s.value(), 3, 'the thumb follows at once');
  s.fire('onPointerUp'); s.fire('onBlur');
  assert.deepEqual(calls, [3]);
  s.move(4); s.fire('onPointerCancel');
  assert.deepEqual(calls, [3, 4], 'pointer cancel commits like pointer up');
  // Unset, Enter on the hollow thumb.
  const u: number[] = [];
  const h = mountSlider({ stops: LEVELS, index: 2, valueText: 'CLI default', unset: true, onIndex: (i) => u.push(i), labelledBy: 'x' });
  h.fire('onKeyDown', { key: 'Enter' });
  assert.deepEqual(u, [2]);
});

test('re-review: the composer sets effort at once; a refused pick re-reads the real level (lib/liveSwitch.ts), nothing guessed', () => {
  const src = readFileSync(new URL('../components/agent/Composer.tsx', import.meta.url), 'utf8');
  const on = src.slice(src.indexOf('const onEffort'), src.indexOf('const voiceRef'));
  assert.match(on, /live\.pick\(\{ effort: \{ id: level, label \} \}\)/, 'through the sequencer: shown at once, one request out');
  assert.match(src, /agentModel: AgentModelInfo \| null = serverModel && \{ \.\.\.serverModel, \.\.\.live\.shown \}/);
  assert.match(src, /resync: refreshModel/, 'a failure re-reads');
  assert.doesNotMatch(src, /effortAfter/);
});
