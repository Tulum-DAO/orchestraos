import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  indexPage, navItemsFor, showArturoPill, showNewAgent, providerTileAction, showAddProvider, isViewHidden,
} from './features.ts';
import {
  resolveRuntimeConfig, setRuntimeConfig, featureEnabled, DEFAULT_RUNTIME_CONFIG, resolveHiddenViews, hiddenViews,
} from './runtimeConfig.ts';

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
    ['layouts/DashboardLayout.tsx', /isViewHidden\(location\.pathname\) \? <NotFound \/> : <Outlet \/>/],
    ['components/CommandPalette.tsx', /navItemsFor\(PAGES\)/],
  ];
  for (const [file, re] of wiring) assert.match(src(file), re, file);
  // No surface renders its component unconditionally any more.
  assert.doesNotMatch(src('layouts/DashboardLayout.tsx'), /^\s*<ArturoPill \/>/m);
  // The layout renders the route ONLY through the gate.
  assert.doesNotMatch(src('layouts/DashboardLayout.tsx'), /<RouteErrorBoundary label="page"><Outlet \/>/);
  // Every sidebar list goes through navItemsFor: no raw list reaches NavItems / NavSection.
  const sidebar = src('components/Sidebar.tsx');
  for (const list of ['assistantNav', 'coreNav', 'operationsNav', 'intelligenceNav', 'projectsNav', 'commsNav']) {
    assert.match(sidebar, new RegExp(`items=\\{navItemsFor\\(${list}\\)\\}`), list);
    assert.doesNotMatch(sidebar, new RegExp(`items=\\{${list}\\}`), list);
  }
});

// ---- hiddenViews ------------------------------------------------------------------------------

test('hiddenViews defaults to none, so a public install shows every view', () => {
  assert.deepEqual(resolveRuntimeConfig(null).hiddenViews, []);
  assert.deepEqual(resolveRuntimeConfig({}).hiddenViews, []);
  assert.deepEqual(DEFAULT_RUNTIME_CONFIG.hiddenViews, []);
});

test('valid entries are kept, normalised (trim, lowercase, no trailing slash, no duplicates)', () => {
  const warned: string[] = [];
  assert.deepEqual(resolveHiddenViews(['/chat-history', ' /Analytics/ ', '/tasks', '/tasks', '/roadmaps/x'], (m) => warned.push(m)),
    ['/chat-history', '/analytics', '/tasks', '/roadmaps/x']);
  assert.deepEqual(warned, []);
});

test('malformed: not an array -> none + a warning; bad entries dropped with a warning each', () => {
  for (const bad of ['/tasks', { '/tasks': true }, 42, null, true]) {
    const warned: string[] = [];
    assert.deepEqual(resolveHiddenViews(bad, (m) => warned.push(m)), [], JSON.stringify(bad));
    assert.equal(warned.length, 1, JSON.stringify(bad));
  }
  const warned: string[] = [];
  assert.deepEqual(resolveHiddenViews(['/', 'tasks', '/a b', '../x', 7, '', '/ok'], (m) => warned.push(m)), ['/ok']);
  assert.equal(warned.length, 6);           // "/" is not hideable: the index belongs to features.arturo
});

test('isViewHidden: the view and its sub-paths, never a lookalike', () => {
  const h = ['/tasks', '/roadmaps'];
  assert.equal(isViewHidden('/tasks', h), true);
  assert.equal(isViewHidden('/tasks/', h), true);
  assert.equal(isViewHidden('/Tasks', h), true);             // the router matches case-insensitively
  assert.equal(isViewHidden('/roadmaps/acme', h), true);
  assert.equal(isViewHidden('/tasks-archive', h), false);
  assert.equal(isViewHidden('/agents', h), false);
  assert.equal(isViewHidden('/', h), false);
  assert.equal(isViewHidden('/tasks', []), false);           // control: nothing hidden
});

test('navItemsFor drops hidden views (sidebar and palette), and nothing else', () => {
  const items = [{ to: '/' }, { to: '/overview' }, { to: '/tasks' }, { to: '/chat-history' }];
  assert.deepEqual(navItemsFor(items, ON, ['/tasks', '/chat-history']), [{ to: '/' }, { to: '/overview' }]);
  assert.deepEqual(navItemsFor(items, ON, []), items);
  // both rules together
  assert.deepEqual(navItemsFor(items, off('arturo'), ['/tasks']), [{ to: '/overview' }, { to: '/chat-history' }]);
});

test('the helpers read the loaded config', () => {
  setRuntimeConfig({ hiddenViews: ['/analytics'] });
  assert.deepEqual(hiddenViews(), ['/analytics']);
  assert.equal(isViewHidden('/analytics'), true);
  assert.deepEqual(navItemsFor([{ to: '/analytics' }, { to: '/tasks' }]), [{ to: '/tasks' }]);
  setRuntimeConfig(null);
  assert.equal(isViewHidden('/analytics'), false);
});
