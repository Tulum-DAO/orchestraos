/**
 * Settings › Display in the OpenBot layout (2026-11-03), the Settings preview drawn by the real chat in the chosen Chat
 * style (the preview follows Bubbles / Modern, Show agent steps and Timestamps), and the ✓ Saved that never
 * moves a control ("the 'saved' text should not move any buttons"). Components have no DOM runner here, so their
 * wiring is read from the source; the pure parts (the preview's rows, the slot's markup) are run.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { previewItems, previewRows, previewSkin } from './settingsPreview.ts';
import { DISPLAY_RESET_KEYS } from './displaySettings.ts';
import { SavedSlot } from '../components/settings/SavedSlot.tsx';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (p: string) => readFileSync(join(SRC, p), 'utf-8');
(globalThis as { React?: typeof React }).React = React;
const display = () => read('components/settings/sections/DisplaySection.tsx');
const preview = () => read('components/settings/sections/DisplayPreview.tsx');

test('Display: intro, preview, a Layout card (spacing, message width, sidebar width) then a Motion card, reset last', () => {
  const d = display();
  assert.match(d, /<PageIntro>Spacing, widths and motion on this device\.<\/PageIntro>/);
  const at = ['<PageIntro>', '<DisplayPreview />', '<Card title="Layout">', 'k="spacing"', 'k="messageWidth"', '<SidebarWidthRow />',
    '<Card title="Motion">', 'k="motion"', '<ResetDisplay />'].map((m) => { const i = d.indexOf(m, d.indexOf('export function DisplaySection')); assert.ok(i >= 0, m); return i; });
  assert.deepEqual([...at].sort((a, b) => a - b), at);
  assert.doesNotMatch(d, /<Group /, 'no scope tags: each row says it in words');
  assert.equal(d.match(/description="[^"]*on this device\.[^"]*"/g)?.length, 4, 'every row says "on this device"');
});

test('Display rows keep their stores and values: the same keys and choices as before', () => {
  const d = display();
  assert.match(d, /k="spacing"[\s\S]*?\{ value: 'compact', label: 'Compact' \}, \{ value: 'comfortable', label: 'Comfortable' \}, \{ value: 'roomy', label: 'Roomy' \}/);
  assert.match(d, /\{desktop && <DeviceChoiceRow k="messageWidth"[\s\S]*?\{ value: 'narrow', label: 'Narrow' \}, \{ value: 'default', label: 'Default' \}, \{ value: 'wide', label: 'Wide' \}/);
  assert.match(d, /k="motion"[\s\S]*?\{ value: 'system', label: 'Follow system' \}, \{ value: 'reduced', label: 'Reduced' \}, \{ value: 'full', label: 'Full' \}/);
  assert.match(d, /\{desktop && <SidebarWidthRow \/>\}/);
  assert.match(d, /function SidebarWidthRow[\s\S]*?useDeviceSetting\('sidebarWidth'\)[\s\S]*?set\(null\)/);
  // DeviceChoiceRow stores under display.<k> through useDeviceSetting, as before
  assert.match(read('components/settings/DeviceRows.tsx'), /const \[value, set\] = useDeviceSetting\(k\);/);
});

test('Sidebar width says Automatic or Custom, never a px number', () => {
  const row = display().slice(display().indexOf('function SidebarWidthRow'), display().indexOf('const RESET_WHAT'));
  assert.match(row, /width === null \? 'Automatic' : 'Custom'/);
  assert.match(row, />Use automatic</);
  assert.doesNotMatch(row, /\bpx\b|defaultSidebarWidth/);
});

test('Reset: a quiet text button at the bottom, confirming with what it resets; it resets only Display\'s four rows', () => {
  const d = display();
  assert.match(d, /<ConfirmButton label="Reset display settings" confirmLabel="Reset"[\s\S]*?consequence=\{RESET_WHAT\}[\s\S]*?resetDisplay\(\)/);
  assert.match(d, /const RESET_WHAT = 'Spacing, message width, sidebar width and motion go back to the defaults on this device\.';/);
  assert.deepEqual([...DISPLAY_RESET_KEYS].sort(), ['messageWidth', 'motion', 'sidebarWidth', 'spacing']);
  assert.match(read('components/settings/SettingsKit.tsx'), /data-confirm-yes=\{data\}>\{confirmLabel\}<\/button>/);
});

test('the preview skin is the Chat style row: Modern draws Modern, anything else Bubbles', () => {
  assert.equal(previewSkin('modern'), 'modern');
  assert.equal(previewSkin('bubbles'), 'bubbles');
  assert.equal(previewSkin(undefined), 'bubbles');
  assert.equal(previewSkin('dev'), 'bubbles');
  const p = preview();
  assert.match(p, /previewSkin\(useServerSettings\(\(s\) => s\.rows\['general\.defaultChatStyle'\]\?\.value\)\)/);
});

test('the preview is the real thread: ThreadView in that skin, the chat\'s own node view, times as the chat has them', () => {
  const p = preview();
  assert.match(p, /<ThreadView rows=\{rows\} skin=\{skin\} times=\{modern\}/);
  assert.match(p, /<GroupedNodeView node=\{node\} interrupted=\{interrupted\} look=\{modern \? 'card' : 'lines'\} \/>/);
  // the same as the agent chat draws (TranscriptChatView)
  const chat = read('components/chat/TranscriptChatView.tsx');
  assert.match(chat, /<ThreadView rows=\{threadRows\} skin=\{modern \? 'modern' : 'bubbles'\} times=\{modern\}/);
  assert.match(chat, /<GroupedNodeView node=\{node\} interrupted=\{interrupted\} look=\{modern \? 'card' : 'lines'\} \/>/);
  assert.match(p, /className=\{`ui-feed-gap\$\{modern \? ' kit-modern' : ''\}`\}/);
  assert.doesNotMatch(p, /BUBBLES_USER|BUBBLES_AGENT/, 'no hand-built bubbles');
});

test('Show agent steps redraws the preview (a run reads it once, when it mounts); timestamps are the page\'s own rule', () => {
  const p = preview();
  assert.match(p, /const \[steps\] = useDeviceSetting\('toolDetail'\);/);
  assert.match(p, /key=\{`\$\{skin\}:\$\{steps\}`\}/);
  assert.match(read('components/kit/ToolCard.tsx'), /useState\(density === 'verbose'\)/);
  assert.match(read('index.css'), /html\[data-timestamps="off"\] \[data-msg-time\] \{ display: none; \}/);
});

test('sandboxed: fixed rows, inert, no fetch, stream, query or reactions', () => {
  const p = preview();
  assert.match(p, /data-display-preview data-preview-skin=\{skin\} data-preview-steps=\{steps\} inert>/);
  for (const bad of [/fetch\(/, /useQuery/, /ReactionsContext/, /subscribe/, /TranscriptChatView/, /send/i]) assert.doesNotMatch(p.replace(/\/\*\*[\s\S]*?\*\//, ''), bad, String(bad));
  assert.doesNotMatch(read('lib/settingsPreview.ts').replace(/\/\*\*[\s\S]*?\*\//g, ''), /fetch|stream|import\('/i);
});

test('Timestamps "On hover": the inert preview shows its times dimmed (nothing can hover it)', () => {
  assert.match(read('components/settings/settings.css'), /html\[data-timestamps="hover"\] \[data-display-preview\] \[data-msg-time\] \{ opacity: 0\.5; \}/);
  assert.match(read('index.css'), /html\[data-timestamps="hover"\] \[data-msg-time\] \{ opacity: 0;/);
});

test('the preview conversation: a question, a run of two commands, the reply, each with a time', () => {
  const items = previewItems();
  assert.ok(items.every((i) => 'ts' in i && i.ts), 'every item has a time');
  for (const skin of ['bubbles', 'modern'] as const) {
    const rows = previewRows(skin);
    const kinds = rows.map((r) => (r.kind === 'node' ? r.node.kind : r.kind));
    assert.deepEqual(kinds, ['user', 'tool_group', 'assistant'], skin);
    const run = rows[1].kind === 'node' ? rows[1].node : null;
    assert.equal((run as { children?: { kind: string }[] } | null)?.children?.filter((c) => c.kind === 'tool').length, 2, 'two calls, so agent steps can fold or open');
    const messages = rows.filter((r) => r.kind === 'node' && r.message).length;
    assert.equal(messages, skin === 'modern' ? 2 : 0, `${skin}: Modern draws the plain messages itself`);
  }
});

test('the ✓ Saved slot is always there and the same size: shown or not, only its visibility changes', () => {
  const on = renderToStaticMarkup(React.createElement(SavedSlot, { on: true }));
  const off = renderToStaticMarkup(React.createElement(SavedSlot, { on: false }));
  assert.match(on, /data-setting-saved=""/); assert.doesNotMatch(off, /data-setting-saved/);
  assert.match(off, /settings-saved is-idle/);
  const strip = (h: string) => h.replace(/ is-idle/, '').replace(/ data-setting-saved=""/, '').replace(/>Saved<\/span><\/span>$/, '').replace(/><\/span><\/span>$/, '');
  assert.equal(strip(on), strip(off), 'same parts, same words: the slot keeps its room');
  assert.match(on, /role="status">Saved</);
  const css = read('components/settings/settings.css');
  assert.match(css, /\.settings-saved\.is-idle \{ visibility: hidden; \}/);
  // on a phone it takes no inline width (out of the flow, beside the title), so it never wraps a control either
  assert.match(css, /@media \(max-width: 47\.99rem\) \{\s*\.settings-row-titleline \{ position: relative; \}\s*\.settings-saved-slot \{ position: absolute;/);
  // nothing that changes layout is toggled with it: only is-idle (visibility) and the data attribute differ
  for (const rule of css.match(/\.settings-saved[^{]*\{[^}]*\}/g) ?? []) if (/is-idle/.test(rule)) assert.match(rule, /\{ visibility: hidden; \}/);
  assert.doesNotMatch(read('components/settings/TextSizeSlider.tsx'), /saved|Saved/, 'the slider has no tick of its own');
  assert.doesNotMatch(css, /\.settings-textsize-foot \.settings-saved/);
  const kit = read('components/settings/SettingsKit.tsx');
  assert.equal(kit.match(/<SavedSlot on=\{status\.kind === 'saved'\} \/>/g)?.length, 2, 'beside the title, with or without a description');
  const control = kit.slice(kit.indexOf('<span className="settings-control">'), kit.indexOf('{below}'));
  assert.doesNotMatch(control, /Saved/, 'nothing is added beside the control');
  assert.match(read('components/arturo/TakesCalls.tsx'), /<SavedSlot on=\{status\.kind === 'saved'\} \/>/);
  assert.doesNotMatch(read('components/arturo/TakesCalls.tsx'), /className="settings-saved"/);
});
