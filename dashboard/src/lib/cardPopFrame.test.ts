/**
 * The card popover's frame (components/kit/CardPopFrame.tsx, used by CardChip): no box of its own, the card drawn
 * exactly as given, no controls of its own (the card draws Go to card and Close: lib/cardPopInCard.test.ts), keyboard
 * focus and keys (lib/cardPop), placement beside the chip. Rendered from props (no browser); the look is read from
 * kit.css.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import CardPopFrame from '../components/kit/CardPopFrame.tsx';
import { placePopover, POP, popoverKeyAction, type PopFocus } from './cardPop.ts';

(globalThis as { React?: typeof React }).React = React;

const SRC = fileURLToPath(new URL('..', import.meta.url));
const read = (f: string) => readFileSync(SRC + f, 'utf8');
const CSS = read('components/kit/kit.css');
/** The declarations of one exact selector's rule in kit.css. */
const rule = (sel: string) => {
  const i = CSS.indexOf(`\n${sel} {`);
  assert.ok(i >= 0, `kit.css has no rule ${sel}`);
  return CSS.slice(CSS.indexOf('{', i) + 1, CSS.indexOf('}', i));
};

// An answered card as the popover draws one (the chat card imports stylesheets node cannot load, so its markup is
// stood in: AgentSide's row, the card surface, its header, options and the answered line).
const card = () => React.createElement('div', { className: 'flex justify-start' },
  React.createElement('div', { className: 'w-full max-w-[min(92%,480px)]', 'data-agent-side': true },
    React.createElement('div', { id: 'decision-dec_1', 'data-decision-card': '', tabIndex: 0, className: 'rounded-2xl border bg-card text-card-foreground overflow-hidden' },
      React.createElement('div', { className: 'px-4 pt-3.5 pb-1' }, 'Which size?'),
      React.createElement('button', { type: 'button', 'data-option': '1' }, 'Circle'),
      React.createElement('span', { 'data-answered-note': '' }, 'Answered by you · the dashboard · 01:19 AM · delivered in <1s'))));

const frame = (onClose = () => {}, onGoTo = () => {}) => renderToStaticMarkup(
  React.createElement(CardPopFrame, { id: 'dec_1', label: 'Card: Which size?', onGoTo, onClose }, card()));

test('the wrapper draws no box: no border, background, padding, radius or shadow of its own', () => {
  const html = frame();
  assert.match(html, /^<div class="kit-card-pop" data-cap-root="true" role="dialog" tabindex="-1" aria-label="Card: Which size\?" data-card-pop="dec_1">/);
  const pop = rule('.kit-card-pop');
  for (const p of ['background', 'border', 'padding', 'box-shadow', 'backdrop-filter', 'overflow']) {
    assert.doesNotMatch(pop, new RegExp(`(^|[\\s;])${p}\\s*:`), `.kit-card-pop sets ${p}`);
  }
  assert.doesNotMatch(html, /kit-card-pop-bar|kit-card-pop-btn/, 'the old pill row is gone');
  assert.doesNotMatch(CSS, /\.kit-card-pop-(bar|btn)\b/);
  assert.doesNotMatch(html, /arturo-/);
});

test('the card is the only surface: its container lifts it with the popover shadow (no ring), nothing else', () => {
  const c = rule('.kit-card-pop-card');
  assert.match(c, /box-shadow:\s*var\(--kit-card-pop-shadow\)/);
  // --shadow-overlay's layers, light and dark, without its 0 0 0 1px ring (the card draws its own border).
  const light = CSS.match(/:root \{ --kit-card-pop-shadow: ([^;]+); \}/)?.[1];
  const dark = CSS.match(/\.dark \{ --kit-card-pop-shadow: ([^;]+); \}/)?.[1];
  assert.ok(light && dark, 'themed for light and dark');
  for (const v of [light, dark]) assert.doesNotMatch(v!, /0 0 0 1px/);
  assert.match(c, /overflow-y:\s*auto/, 'the card container scrolls, not the frame');
  for (const p of ['background', 'border', 'padding']) assert.doesNotMatch(c, new RegExp(`(^|[\\s;])${p}\\s*:`), `card container sets ${p}`);
  const idx = read('index.css');
  assert.ok(idx.includes(`--bui-shadow-overlay: 0 0 0 1px var(--bui-line), ${light};`), 'light = the overlay token less its ring');
  assert.ok(idx.includes(`--bui-shadow-overlay: 0 0 0 1px oklch(100% 0 0 / .15), ${dark};`), 'dark = the overlay token less its ring');
});

test('the card fills the popover (AgentSide by its data-agent-side hook); a menu-answer pill is hugged', () => {
  assert.match(read('components/chat/DecisionCard.tsx'), /<div className="w-full max-w-\[min\(92%,480px\)\]" data-agent-side>\{children\}<\/div>/);
  assert.match(rule('.kit-card-pop-card [data-agent-side]'), /max-width: none;/);
  assert.doesNotMatch(CSS, /max-w-\[min/, 'not keyed on a Tailwind class string');
  assert.match(read('components/chat/DecisionBubble.tsx'), /data-menu-answer/);
  assert.match(rule('.kit-card-pop:has([data-menu-answer])'), /width: fit-content !important;.*right: auto !important;/);
  assert.match(rule('.kit-card-pop-card [data-menu-answer] > div'), /max-width: none;/);
});

test('the card subtree is drawn exactly as given', () => {
  const html = frame();
  const body = renderToStaticMarkup(card());
  assert.ok(html.includes(`<div class="kit-card-pop-card" data-card-pop-card="true">${body}</div>`), 'card markup unchanged inside the frame');
  assert.match(body, /Answered by you · the dashboard/);
});

test('no floating cluster: the frame draws no controls, and kit.css keeps none of the cluster\'s rules', () => {
  const html = frame();
  const body = renderToStaticMarkup(card());
  assert.equal(html, `<div class="kit-card-pop" data-cap-root="true" role="dialog" tabindex="-1" aria-label="Card: Which size?" data-card-pop="dec_1"><div class="kit-card-pop-card" data-card-pop-card="true">${body}</div></div>`,
    'the frame is the dialog and the card container, nothing else');
  assert.doesNotMatch(html, /kit-card-pop-ctl|kit-card-pop-round|data-card-pop-ctl/);
  assert.doesNotMatch(CSS, /kit-card-pop-ctl|kit-card-pop-round|--pop-round/, 'the cluster\'s CSS is gone');
  assert.doesNotMatch(CSS, /\.kit-card-pop:hover|\.kit-card-pop:has\(:focus-visible\)|\.kit-card-pop:focus-visible/, 'no hover / focus reveal');
  // The popover's own shadow still lifts the card off the page.
  assert.match(rule('.kit-card-pop-card'), /box-shadow:\s*var\(--kit-card-pop-shadow\)/);
  assert.doesNotMatch(read('components/kit/CardChip.tsx'), /canHover|HOVER_QUERY/, 'no hover tracking for a reveal');
  assert.doesNotMatch(read('lib/cardPop.ts'), /CTL_OUT|CTL_RIGHT|HOVER_QUERY/);
});

test('focus: the frame takes focus when placed; Escape / Close give it back to the chip', () => {
  const chip = read('components/kit/CardChip.tsx');
  assert.match(chip, /useEffect\(\(\) => \{ if \(placed\) box\.current\?\.focus\(\{ preventScroll: true \}\); \}, \[placed\]\);/);
  assert.match(chip, /const close = useCallback\(\(refocus: boolean\) => \{\s*onClose\(\); if \(refocus\) anchor\.current\?\.focus\(\{ preventScroll: true \}\);/);
  assert.match(chip, /if \(act === 'close'\) close\(true\);/);
  // Answered or dismissed from the popover: it closes and focus goes back to the chip.
  assert.match(chip, /const answered = \(\) => \{\s*close\(true\);/);
  assert.match(chip, /onGoTo=\{goTo\} onClose=\{\(\) => close\(true\)\}>\s*<CardSurface\.Provider value="popover">\{body\}<\/CardSurface\.Provider>/);
  assert.match(rule('.kit-card-pop:focus'), /outline: none;/);
});

const at = (o: Partial<PopFocus> = {}): PopFocus => ({ topmost: true, inside: true, atStart: false, atEnd: false, ...o });

test('keys: Escape closes only the topmost popover, never a key already handled', () => {
  assert.equal(popoverKeyAction({ key: 'Escape' }, at()), 'close');
  assert.equal(popoverKeyAction({ key: 'Escape' }, at({ inside: false })), 'close', 'focus elsewhere still closes it');
  assert.equal(popoverKeyAction({ key: 'Escape' }, at({ topmost: false })), null, 'an outer popover leaves it to the inner');
  assert.equal(popoverKeyAction({ key: 'Escape', defaultPrevented: true }, at()), null);
  assert.equal(popoverKeyAction({ key: 'Enter' }, at()), null);
  assert.equal(popoverKeyAction({ key: 'x' }, at()), null);
  const chip = read('components/kit/CardChip.tsx');
  // Handled keys are stopped (on the document, before the agent panel's window listener) and closing is prevented.
  assert.match(chip, /if \(!act\) return;\s*e\.stopPropagation\(\);\s*if \(act === 'keep'\) return;\s*e\.preventDefault\(\);/);
  assert.match(chip, /topmost: pops\[pops\.length - 1\] === el/);
  assert.match(chip, /document\.addEventListener\('keydown', onKey\)/);
});

test('keys: Tab stays in the popover (an outer focus trap cannot take it out)', () => {
  assert.equal(popoverKeyAction({ key: 'Tab' }, at()), 'keep');
  assert.equal(popoverKeyAction({ key: 'Tab' }, at({ atEnd: true })), 'first');
  assert.equal(popoverKeyAction({ key: 'Tab', shiftKey: true }, at({ atStart: true })), 'last');
  assert.equal(popoverKeyAction({ key: 'Tab', shiftKey: true }, at({ atEnd: true })), 'keep');
  assert.equal(popoverKeyAction({ key: 'Tab' }, at({ inside: false })), null, 'focus outside: not this popover\'s Tab');
  assert.equal(popoverKeyAction({ key: 'Tab' }, at({ topmost: false })), null);
});

test('placement: beside the chip, inside the screen, no room kept for controls outside the card', () => {
  const chip = { left: 600, top: 100, bottom: 120 };
  const below = placePopover(chip, 1000, 900);
  assert.deepEqual(below, { left: 1000 - 480 - 16, width: 480, top: 120 + 8, maxHeight: 900 - 120 - 8 - 16 });
  assert.ok((below.left as number) + 480 <= 1000 - POP.EDGE, 'in the gutter');
  const above = placePopover({ left: 20, top: 800, bottom: 820 }, 1000, 900);
  assert.deepEqual(above, { left: 20, width: 480, bottom: 900 - 800 + 8, maxHeight: 800 - 8 - 16 });
  const full = placePopover({ left: 20, top: 300, bottom: 320 }, 1000, 600);
  assert.deepEqual(full, { left: 20, width: 480, top: 16, maxHeight: 600 - 32 });
  const narrow = placePopover({ left: 10, top: 100, bottom: 120 }, 400, 900);
  assert.equal(narrow.width, 400 - 32);
  assert.equal(narrow.left, 16);
  assert.match(read('components/kit/CardChip.tsx'), /placePopover\(a, document\.documentElement\.clientWidth/);
  assert.match(rule('.kit-card-pop'), /width: min\(480px, calc\(100vw - 32px\)\);/, 'the CSS width matches: two 16px gutters');
  // A phone's bottom sheet overrides the inline width.
  assert.match(CSS, /\.kit-card-pop \{ left: 16px !important; right: 16px; top: auto !important; bottom: 16px !important; width: auto !important;/);
});
