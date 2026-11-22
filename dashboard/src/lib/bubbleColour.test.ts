/**
 * Bubbles: the person's bubble in the colour of the agent it was sent to (lib/bubbleColour.ts), readable text on it,
 * black and white agents following the theme, and the places that draw it reading the colour.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { AA_TEXT, MIN_SURFACE, PAGE_BG, TEXT_ON_DARK, TEXT_ON_LIGHT, bubbleColours, bubbleVars, normalHex, textOn } from './bubbleColour.ts';
import { BUBBLES_USER } from './chatSkin.ts';
import { INK_COLOR, PALETTES, SWATCHES, contrastRatio, type Theme } from '../components/avatar/avatarCore.ts';

const THEMES: Theme[] = ['light', 'dark'];
const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

test('a coloured agent: the bubble is its colour, with the text that reads best on it', () => {
  for (const theme of THEMES) {
    const blue = bubbleColours('#1E6BE6', theme);
    assert.equal(blue.bg, '#1e6be6');
    assert.equal(blue.fg, TEXT_ON_DARK);
    const yellow = bubbleColours('#FFC61A', theme);
    assert.equal(yellow.bg, '#ffc61a');
    assert.equal(yellow.fg, TEXT_ON_LIGHT, 'dark text on a light bubble');
  }
});

test('the default look ("ink"): near-black with white text in light, near-white with black text in dark', () => {
  const light = bubbleColours('ink', 'light');
  assert.equal(light.bg, INK_COLOR.light.toLowerCase());
  assert.equal(light.fg, TEXT_ON_DARK);
  const dark = bubbleColours('ink', 'dark');
  assert.equal(dark.bg, INK_COLOR.dark.toLowerCase());
  assert.equal(dark.fg, TEXT_ON_LIGHT);
  assert.deepEqual(bubbleColours(undefined, 'dark'), dark, 'no look yet: the default');
  assert.deepEqual(bubbleColours('not-a-colour', 'light'), light, 'a malformed colour: the default');
});

test('a black agent in dark mode is never black-on-black; in light mode it stays black', () => {
  for (const black of ['#000000', '#000', '#16161A', '#2C2E3B' /* Charcoal */]) {
    const d = bubbleColours(black, 'dark');
    assert.ok(contrastRatio(d.bg, PAGE_BG.dark) >= 3, `${black} in dark: the bubble stands off the page (${d.bg})`);
    assert.equal(d.bg, INK_COLOR.dark.toLowerCase(), `${black} in dark follows the theme's ink`);
    assert.equal(d.fg, TEXT_ON_LIGHT);
    const l = bubbleColours(black, 'light');
    assert.equal(l.bg, normalHex(black), `${black} in light keeps its own colour`);
    assert.equal(l.fg, TEXT_ON_DARK);
  }
});

test('a white agent in light mode is never white-on-white; in dark mode it stays white', () => {
  for (const white of ['#FFFFFF', '#fff', '#F4F4F5']) {
    const l = bubbleColours(white, 'light');
    assert.ok(contrastRatio(l.bg, PAGE_BG.light) >= 3, `${white} in light: the bubble stands off the page (${l.bg})`);
    assert.equal(l.bg, INK_COLOR.light.toLowerCase());
    assert.equal(l.fg, TEXT_ON_DARK);
    const d = bubbleColours(white, 'dark');
    assert.equal(d.bg, normalHex(white));
    assert.equal(d.fg, TEXT_ON_LIGHT);
  }
});

test('every swatch an agent can wear, in both themes: text at WCAG AA (4.5:1) and a bubble that shows on the page', () => {
  const all = [...new Set([...SWATCHES.map((s) => s.hex), ...PALETTES.all.map((s) => s.hex), '#000000', '#ffffff', '#808080'])];
  for (const theme of THEMES) {
    for (const c of all) {
      const b = bubbleColours(c, theme);
      assert.ok(b.contrast >= AA_TEXT, `${c} in ${theme}: text ${b.fg} on ${b.bg} is ${b.contrast.toFixed(2)}:1`);
      assert.equal(b.contrast, contrastRatio(b.bg, b.fg));
      assert.ok(contrastRatio(b.bg, PAGE_BG[theme]) >= MIN_SURFACE, `${c} in ${theme}: ${b.bg} shows on the page`);
    }
  }
});

test('any colour at all gets text at AA: the better of black and white never drops under 4.5:1 (Red, every grey, a hue sweep)', () => {
  const red = bubbleColours('#E5243B', 'light');
  assert.equal(red.bg, '#e5243b', 'the agent colour itself, never altered');
  assert.ok(red.contrast >= AA_TEXT);
  let worst = Infinity;
  for (let v = 0; v < 0x1000000; v += 1009) { // about 16,600 colours spread through the whole cube
    const hex = `#${v.toString(16).padStart(6, '0')}`;
    worst = Math.min(worst, contrastRatio(hex, textOn(hex)));
  }
  for (let g = 0; g < 256; g++) {
    const h = g.toString(16).padStart(2, '0');
    worst = Math.min(worst, contrastRatio(`#${h}${h}${h}`, textOn(`#${h}${h}${h}`)));
  }
  assert.ok(worst >= AA_TEXT, `worst ${worst.toFixed(3)}:1`);
});

test('textOn and the CSS variables', () => {
  assert.equal(textOn('#000000'), TEXT_ON_DARK);
  assert.equal(textOn('#ffffff'), TEXT_ON_LIGHT);
  assert.deepEqual(bubbleVars({ bg: '#123456', fg: '#ffffff', contrast: 9 }), { '--bubble-user-bg': '#123456', '--bubble-user-fg': '#ffffff' });
  assert.equal(normalHex('#AbC'), '#aabbcc');
  assert.equal(normalHex('ink'), null);
});

test('the Bubbles bubble reads the agent colour; Modern does not', () => {
  assert.match(BUBBLES_USER, /\bbubble-user-colour\b/);
  assert.doesNotMatch(BUBBLES_USER, /bg-blue-600|text-white/, 'no fixed blue/white left on the bubble');
  const css = src('index.css');
  assert.match(css, /\.bubble-user-colour\s*\{[^}]*background-color:\s*var\(--bubble-user-bg,[^}]*color:\s*var\(--bubble-user-fg,/);
  assert.doesNotMatch(css.match(/\.bubble-user\s*\{[^}]*\}/)?.[0] ?? '', /--bubble-user-bg/, 'Modern keeps its own bubble');
  assert.match(src('components/chat/TranscriptCards.tsx'), /clsx\(BUBBLES_WIDTH, 'rounded-2xl rounded-br-sm bubble-user-colour /, 'the just-sent bubble too');
  // Who sets it: the agent chat, Arturo's page, and a call's transcript, each from the agent the words go to.
  assert.match(src('components/chat/TranscriptChatView.tsx'), /useBubbleColour\(agentId\)/);
  assert.match(src('components/chat/TranscriptChatView.tsx'), /style=\{bubbleColour\}/);
  assert.match(src('pages/ArturoHome.tsx'), /useBubbleColour\(ARTURO_ID\)/);
  assert.match(src('pages/ArturoHome.tsx'), /className="arturo-feed"[^>]*style=\{bubbleColour\}/);
  assert.match(src('components/call/CallBar.tsx'), /useBubbleColour\(party\.id\)/);
  assert.match(src('components/call/CallBar.tsx'), /data-call-bar=\{call\.phase\} style=\{bubbleColour\}/);
  assert.match(src('components/chat/useBubbleColour.ts'), /useAgentRecord\(agentId\)\.color/);
});
