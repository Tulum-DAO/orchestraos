/**
 * "Compact at" is one control in two places (Settings › Agents default, ServerRows.tsx; an agent's own Setup,
 * RotationSettings.tsx): one rounded box with a filled − on the left, the number and its % in the middle, a filled +
 * on the right. Its colours are the theme's own variables, so light and dark both look native.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf-8');

const css = () => read('components/arturo/rotationSettings.css');
const rule = (sel: string) => {
  const m = css().match(new RegExp(`(?:^|\\n)${sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\{([^}]*)\\}`));
  assert.ok(m, `no rule ${sel}`);
  return m[1];
};

test('both places draw the same stepper: box, −, number + %, +', () => {
  for (const f of ['components/settings/ServerRows.tsx', 'components/arturo/RotationSettings.tsx']) {
    const src = read(f);
    assert.match(src, /<span className=(?:"rotation-stepper"|\{stepper \? 'rotation-stepper' : 'settings-field'\})>\s*(\{stepper && )?<button type="button" className="rotation-step" aria-label="Lower"/, f);
    assert.match(src, /<span className=(?:"rotation-number"|\{stepper \? 'rotation-number' : 'settings-field'\})>\s*<input [\s\S]*?\/>\s*<span aria-hidden>\{?(suffix|%)\}?<\/span>\s*<\/span>\s*(\{stepper && )?<button type="button" className="rotation-step" aria-label="Raise"/, f);
  }
});

test('Settings without a stepper keeps the plain field', () => {
  const src = read('components/settings/ServerRows.tsx');
  assert.match(src, /className=\{stepper \? undefined : 'panel-input settings-number'\}/);
  assert.match(read('components/settings/settings.css'), /\.settings-field \{ display: contents; \}/);
});

test('the box: one rounded container with a hairline border on the row surface', () => {
  const box = rule('.rotation-stepper');
  assert.match(box, /border:1px solid var\(--hair\)/);
  assert.match(box, /border-radius:12px/);
  assert.match(box, /background:var\(--panel\)/);
});

test('the buttons are filled from the theme (no literal colours), white-on-dark in light, dark-on-white in dark', () => {
  const step = rule('.rotation-step');
  assert.match(step, /background:var\(--send-bg\)/);
  assert.match(step, /color:var\(--send-fg\)/);
  assert.match(step, /border:0/);
  assert.doesNotMatch(step, /#[0-9a-f]{3,6}/i);
  const off = rule('.rotation-step:disabled');
  assert.match(off, /background:var\(--card2\)/);
  assert.match(off, /color:var\(--grey2\)/);
});

test('the number sits in the middle, borderless; the box shows focus and a bad value', () => {
  const input = rule('.rotation-number input');
  assert.match(input, /border:0/);
  assert.match(input, /background:none/);
  assert.match(css(), /\.rotation-stepper:has\(input:focus-visible\)\{[^}]*outline:2px solid #3b82f6/);
  assert.match(css(), /\.rotation-stepper:has\(input\[aria-invalid="true"\]\)\{[^}]*border-color:var\(--err-soft\)/);
  assert.match(css(), /\.rotation-step:focus-visible\{[^}]*outline:2px solid #3b82f6/);
});

test('phone: each button still has a 44px tap area', () => {
  assert.match(css(), /\.rotation-step::after\{ content:''; position:absolute; inset:-6px; \}/);
  assert.match(rule('.rotation-step'), /width:32px; height:32px/);
});
