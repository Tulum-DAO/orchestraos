import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  indexPage, navItemsFor, showArturoPill, showNewAgent, providerTileAction, showAddProvider,
} from './features.ts';
import { resolveRuntimeConfig, setRuntimeConfig, featureEnabled, DEFAULT_RUNTIME_CONFIG } from './runtimeConfig.ts';

const ON = { arturo: true, newAgent: true, providerSignIn: true };
const off = (name: keyof typeof ON) => ({ ...ON, [name]: false });

// ---- the config file ------------------------------------------------------------------------

test('every feature defaults ON, so a public install is unchanged', () => {
  assert.deepEqual(resolveRuntimeConfig(null).features, ON);
  assert.deepEqual(DEFAULT_RUNTIME_CONFIG.features, ON);
});

test('a false switches only that feature off', () => {
  for (const name of Object.keys(ON) as (keyof typeof ON)[]) {
    assert.deepEqual(resolveRuntimeConfig({ features: { [name]: false } }).features, off(name), name);
  }
});

test('only a real boolean counts: a typo never hides a working surface', () => {
  for (const v of ['false', 0, null, 'no', {}]) {
    assert.equal(resolveRuntimeConfig({ features: { arturo: v } }).features.arturo, true, String(v));
  }
  assert.deepEqual(resolveRuntimeConfig({ features: 'none' }).features, ON);
  assert.deepEqual(resolveRuntimeConfig({ features: { unknownThing: false } }).features, ON);
});

test('resolving never shares the defaults object (a false cannot leak into the next resolve)', () => {
  resolveRuntimeConfig({ features: { arturo: false } });
  assert.equal(resolveRuntimeConfig(null).features.arturo, true);
});

test('featureEnabled reads the loaded config', () => {
  setRuntimeConfig({ features: { newAgent: false } });
  assert.equal(featureEnabled('newAgent'), false);
  assert.equal(featureEnabled('arturo'), true);
  assert.equal(showNewAgent(), false);           // the helpers default to the loaded config
  setRuntimeConfig(null);
  assert.equal(showNewAgent(), true);
});

// ---- each flag, both ways -------------------------------------------------------------------

test('arturo ON: "/" is the Arturo home, the pill shows, the sidebar keeps its Arturo entry', () => {
  const nav = [{ to: '/' }, { to: '/overview' }, { to: '/agents' }];
  assert.equal(indexPage(ON), 'arturo');
  assert.equal(showArturoPill(ON), true);
  assert.deepEqual(navItemsFor(nav, ON), nav);
});

test('arturo OFF: "/" opens the Overview, no pill, no Arturo entry; nothing else moves', () => {
  const nav = [{ to: '/' }, { to: '/overview' }, { to: '/agents' }];
  assert.equal(indexPage(off('arturo')), 'overview');
  assert.equal(showArturoPill(off('arturo')), false);
  assert.deepEqual(navItemsFor(nav, off('arturo')), [{ to: '/overview' }, { to: '/agents' }]);
  // the other flags are untouched
  assert.equal(showNewAgent(off('arturo')), true);
  assert.equal(showAddProvider(off('arturo')), true);
});

test('newAgent ON / OFF', () => {
  assert.equal(showNewAgent(ON), true);
  assert.equal(showNewAgent(off('newAgent')), false);
  assert.equal(showArturoPill(off('newAgent')), true);
});

test('providerSignIn ON: a disconnected tile opens the connect modal; Add a provider shows', () => {
  assert.equal(providerTileAction(false, ON), 'connect');
  assert.equal(showAddProvider(ON), true);
});

test('providerSignIn OFF: a disconnected tile does nothing, no Add a provider; usable tiles still expand', () => {
  assert.equal(providerTileAction(false, off('providerSignIn')), 'none');
  assert.equal(showAddProvider(off('providerSignIn')), false);
  assert.equal(providerTileAction(true, off('providerSignIn')), 'expand');
  assert.equal(providerTileAction(true, ON), 'expand');
});

// ---- wiring: each surface asks its flag ------------------------------------------------------

test('every gated surface is wired to its helper', () => {
  const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');
  const wiring: [string, RegExp][] = [
    ['App.tsx', /indexPage\(\)/],
    ['layouts/DashboardLayout.tsx', /showArturoPill\(\)\s*&&\s*<ArturoPill/],
    ['components/Sidebar.tsx', /navItemsFor\(coreNav\)/],
    ['pages/Overview.tsx', /showNewAgent\(\)/],
    ['pages/Agents.tsx', /showNewAgent\(\)/],
    ['components/agent/ModelSelectorSheet.tsx', /providerTileAction\(/],
    ['components/agent/ModelSelectorSheet.tsx', /showAddProvider\(\)/],
  ];
  for (const [file, re] of wiring) assert.match(src(file), re, file);
  // No surface renders its component unconditionally any more.
  assert.doesNotMatch(src('layouts/DashboardLayout.tsx'), /^\s*<ArturoPill \/>/m);
});
