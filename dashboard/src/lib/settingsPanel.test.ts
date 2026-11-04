/**
 * Settings' own surface (components/settings): its inner scroll boxes let the page scroll on, and it is drawn with the
 * shared panel classes (components/kit/panel.css, `panel-*`), never Arturo's (`arturo-*`).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ModelPickerPanel, { type ModelPickerPanelProps, type PanelLayout } from '../components/kit/ModelPickerPanel.tsx';

(globalThis as { React?: typeof React }).React = React;

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8');
const walk = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? walk(p) : [p];
});

function tabpanelClass(layout: PanelLayout): string {
  const props: ModelPickerPanelProps = {
    layout, idBase: 's', rows: [], entries: [], shown: [], scope: { kind: 'any' }, value: null, tab: null, onTab: () => {},
    query: '', onQuery: () => {}, active: null, onActive: () => {}, onPick: () => {}, renderMark: () => null,
  };
  const html = renderToStaticMarkup(React.createElement(ModelPickerPanel, props));
  const m = html.match(/<div role="tabpanel"[^>]*class="([^"]*)"/);
  assert.ok(m, `the ${layout} picker has its tabpanel`);
  return m[1];
}

test('the inline picker (a settings page) hands the wheel on to the page at the end of its list; popover and sheet keep it', () => {
  const inline = tabpanelClass('inline');
  assert.match(inline, /\boverflow-y-auto\b/, 'the inline list still scrolls itself');
  assert.doesNotMatch(inline, /overscroll/, 'no overscroll containment inline: the settings page scrolls on');
  for (const layout of ['popover', 'sheet'] as const) {
    assert.match(tabpanelClass(layout), /\boverscroll-contain\b/, `the ${layout} keeps its scroll to itself`);
  }
});

test('nothing drawn inside Settings contains overscroll on the vertical axis', () => {
  for (const f of walk(join(SRC, 'components/settings'))) {
    const s = readFileSync(f, 'utf8');
    assert.doesNotMatch(s, /overscroll-(contain|none|y-contain|y-none)\b|overscroll-behavior(-y)?\s*:\s*(contain|none)/, `${f}: an inline scroll box in Settings must let the page scroll on`);
  }
});

test('Settings uses the shared panel classes, never Arturo\'s', () => {
  const files = walk(join(SRC, 'components/settings')).filter((f) => /\.(tsx?|css)$/.test(f));
  assert.ok(files.length > 10);
  for (const f of files) {
    const s = readFileSync(f, 'utf8');
    assert.doesNotMatch(s, /arturo-[a-z]/, `${f}: an arturo- class (or Arturo's stylesheet) in Settings`);
    assert.doesNotMatch(s, /arturo\.css/, `${f}: Settings imports Arturo's stylesheet`);
  }
  // What Settings borrows from components/arturo (the Switch) draws no arturo- class either.
  assert.doesNotMatch(read('components/arturo/PanelRows.tsx'), /className="arturo-/);
  assert.match(read('components/settings/SettingsShell.tsx'), /import '\.\.\/kit\/panel\.css';\s*\nimport '\.\/settings\.css';/,
    'panel.css, then settings.css (its overrides win at equal specificity)');
  assert.match(read('components/settings/SettingsKit.tsx'), /import '\.\.\/kit\/panel\.css';\s*\nimport '\.\/settings\.css';/);
});

test('every panel- class Settings and Arturo draw is defined once, in kit/panel.css', () => {
  const panel = read('components/kit/panel.css');
  const arturo = read('components/arturo/arturo.css').replace(/\/\*[\s\S]*?\*\//g, '');
  const defined = new Set([...panel.matchAll(/\.(panel-[a-z-]+)/g)].map((m) => m[1]));
  const used = new Set<string>();
  for (const f of ['arturo', 'settings', 'avatar', 'kit'].flatMap((d) => walk(join(SRC, 'components', d))).filter((x) => x.endsWith('.tsx'))) {
    for (const m of readFileSync(f, 'utf8').matchAll(/(?<![\w-])(panel-[a-z][a-z-]*)/g)) used.add(m[1]);
  }
  for (const c of used) assert.ok(defined.has(c), `${c} is drawn but not defined in kit/panel.css`);
  for (const c of defined) assert.doesNotMatch(arturo, new RegExp(`(^|,)\\s*\\.${c}\\s*[{,:]`, 'm'), `${c} has a base rule in arturo.css too`);
  assert.doesNotMatch(panel, /\.arturo-panel-[a-z]/, 'no Arturo-named panel class in the shared sheet');
});
