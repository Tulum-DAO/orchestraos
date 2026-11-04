/**
 * Settings › General › Text size as a slider ("I don't think text size should display as percentages
 * like that"): a small A, a five-stop track, a large A, and the stop's name under it (Small, Default, Large, Larger,
 * Largest). The five values, the storage key and the live effect are unchanged; no percentage anywhere in the row.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { TEXT_SCALES, TEXT_SCALE_NAMES, DISPLAY_KEYS, DISPLAY_DEFAULTS, readDisplay, textScaleAt, textScaleIndex, textScaleName } from './displaySettings.ts';
import { TextSizeSlider } from '../components/settings/TextSizeSlider.tsx';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf-8');
// tsx compiles the component's JSX in classic mode here (the root tsconfig has no "jsx"), so it needs a global React.
(globalThis as { React?: typeof React }).React = React;
const NAMES = ['Small', 'Default', 'Large', 'Larger', 'Largest'];

test('the five values are unchanged and each has its name, in order', () => {
  assert.deepEqual([...TEXT_SCALES], [0.87, 1, 1.12, 1.25, 1.5]);
  assert.deepEqual(TEXT_SCALES.map((t) => TEXT_SCALE_NAMES[t]), NAMES);
  assert.deepEqual(TEXT_SCALES.map(textScaleName), NAMES);
  assert.equal(textScaleName(DISPLAY_DEFAULTS.textScale), 'Default');
  assert.equal(DISPLAY_KEYS.textScale, 'orchestra.display.textScale');
});

test('value ↔ stop: every value round-trips; a stop out of range or between stops snaps to the nearest end or stop', () => {
  TEXT_SCALES.forEach((t, i) => { assert.equal(textScaleIndex(t), i); assert.equal(textScaleAt(i), t); });
  assert.equal(textScaleAt(-3), 0.87);
  assert.equal(textScaleAt(99), 1.5);
  assert.equal(textScaleAt(2.4), 1.12);
  assert.equal(textScaleAt(Number.NaN), 1);
  assert.equal(textScaleIndex(0.5), 1, 'an unknown value shows the Default stop');
  assert.equal(textScaleName(0.5), 'Default');
  // what a stop stores is read back as that stop
  for (const t of TEXT_SCALES) assert.equal(readDisplay({ getItem: (k) => (k === DISPLAY_KEYS.textScale ? String(t) : null) }).textScale, t);
});

const render = (value: (typeof TEXT_SCALES)[number]) =>
  renderToStaticMarkup(React.createElement(TextSizeSlider, { labelledBy: 'setting-display.textScale', value, onChange: () => {} }));

test('the slider: a native range of five stops, named for screen readers, a small and a large A, the name under it', () => {
  TEXT_SCALES.forEach((t, i) => {
    const html = render(t);
    assert.match(html, /<input[^>]*type="range"/);
    assert.match(html, /min="0"/); assert.match(html, /max="4"/); assert.match(html, /step="1"/);
    assert.match(html, new RegExp(`value="${i}"`));
    assert.match(html, new RegExp(`aria-valuetext="${NAMES[i]}"`));
    assert.match(html, /aria-labelledby="setting-display\.textScale"/);
    assert.match(html, new RegExp(`data-text-size-name="[^"]*"[^>]*>${NAMES[i]}<`));
    assert.equal(html.match(/data-text-size-tick/g)?.length, 5, 'five tick dots');
    assert.equal(html.match(/>A</g)?.length, 2, 'a small and a large A');
  });
});

test('no percentage anywhere in the Text size row: the slider (text and attributes), the row, its keywords', () => {
  for (const t of TEXT_SCALES) assert.doesNotMatch(render(t), /%|\d{2,3} ?percent/i, String(t));
  const g = read('components/settings/sections/GeneralSection.tsx');
  const row = g.slice(g.indexOf('function TextSizeRow'), g.indexOf('function SkinToneRow'));
  assert.ok(row.includes('label="Text size"'), 'TextSizeRow found');
  assert.doesNotMatch(row, /%|\* ?100|percent/i);
  assert.doesNotMatch(read('components/settings/TextSizeSlider.tsx').replace(/\/\*[\s\S]*?\*\//g, ''), /%|\* ?100|percent/i);
});

test('General uses the slider row (with the live preview under it); the old percentage buttons are gone', () => {
  const g = read('components/settings/sections/GeneralSection.tsx');
  assert.match(g, /function TextSizeRow\(\)[\s\S]*?useDeviceSetting\('textScale'\)[\s\S]*?<SettingRow id="display\.textScale" label="Text size"[\s\S]*?below=\{<DisplayPreview \/>\}[\s\S]*?<TextSizeSlider /);
  assert.doesNotMatch(g, /k="textScale"/);
  assert.doesNotMatch(g, /TEXT_SCALES\.map/);
});
