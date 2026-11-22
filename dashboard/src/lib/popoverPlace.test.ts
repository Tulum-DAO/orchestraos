/**
 * Where the model picker floats (lib/popoverPlace.ts), and that Arturo's composers anchor it to the brain chip
 * instead of dropping it mid-screen.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { placePopover, type AnchorRect } from './popoverPlace.ts';

const DESK = { width: 1440, height: 900 };
const OPTS = { width: 440, height: 460 };
const chip = (left: number, top: number, w = 120, h = 32): AnchorRect => ({ left, top, right: left + w, bottom: top + h });

test('above: a chip in a composer at the bottom gets the panel directly above it, left edges aligned', () => {
  const a = chip(300, 820);
  const p = placePopover(a, DESK, { ...OPTS, placement: 'above' });
  assert.equal(p.top, undefined);
  assert.equal(p.bottom, DESK.height - a.top + 6); // its bottom edge 6px over the chip
  assert.equal(p.left, 300);
  assert.equal(p.maxHeight, 460);
});

test('above: a chip in a composer in the middle of the page still opens above it (not below, not centred)', () => {
  const a = chip(500, 480); // 466px of room above: the whole panel fits there
  assert.ok(placePopover(a, DESK, { ...OPTS, placement: 'above' }).bottom !== undefined);
  const high = chip(500, 300); // 292px above, 568 below: auto goes below, above stays above (capped to its room)
  const auto = placePopover(high, DESK, OPTS);
  assert.equal(auto.top, high.bottom + 6);
  const up = placePopover(high, DESK, { ...OPTS, placement: 'above' });
  assert.equal(up.top, undefined);
  assert.equal(up.bottom, DESK.height - high.top + 6);
  assert.equal(up.maxHeight, high.top - 6 - 8);
});

test('above: flips under the chip only when the room above is too small for the panel and smaller than below', () => {
  const a = chip(500, 60);
  const p = placePopover(a, DESK, { ...OPTS, placement: 'above' });
  assert.equal(p.top, a.bottom + 6);
  assert.equal(p.bottom, undefined);
});

test('a chip near the right edge (Arturo pane brain logo): right edge on the chip, never off-screen', () => {
  const a = chip(1380, 820, 36, 36);
  const p = placePopover(a, DESK, { ...OPTS, placement: 'above' });
  assert.equal(p.left, Math.min(DESK.width - 440 - 8, a.right - 440));
  assert.ok(p.left + 440 <= DESK.width - 8);
  assert.ok(p.left <= a.left && p.left + 440 >= a.right, 'the panel spans the chip it belongs to');
});

test('a narrow window: the panel narrows and keeps the 8px edges', () => {
  const vp = { width: 400, height: 800 };
  const p = placePopover(chip(20, 700), vp, { ...OPTS, placement: 'above' });
  assert.equal(p.left, 8);
});

test('auto (the agent composer pill) keeps its rule: under when it fits there, else above', () => {
  assert.equal(placePopover(chip(300, 100), DESK, OPTS).top, 138);
  assert.equal(placePopover(chip(300, 820), DESK, OPTS).bottom, 900 - 820 + 6);
});

test('no anchor at all: the old fallback, near the top and centred', () => {
  const p = placePopover(null, DESK, OPTS);
  assert.equal(p.left, (1440 - 440) / 2);
  assert.equal(p.top, Math.round(900 * 0.12));
});

test("Arturo's composers anchor the picker to the brain chip, directly above it", () => {
  for (const f of ['../pages/ArturoHome.tsx', '../components/arturo/ArturoPill.tsx']) {
    const src = readFileSync(new URL(f, import.meta.url), 'utf8');
    const picker = src.slice(src.indexOf('<ModelPicker chooser="arturo"'));
    assert.match(picker.slice(0, 200), /anchorRef=\{brainChipRef\}/, `${f}: the picker is anchored`);
    assert.match(picker.slice(0, 200), /placement="above"/, `${f}: above the chip`);
    assert.match(src, /<button ref=\{brainChipRef\}[^>]*onClick=\{\(\) => setModelOpen\(true\)\}/, `${f}: the ref is on the brain chip`);
  }
  const kit = readFileSync(new URL('../components/kit/ModelPicker.tsx', import.meta.url), 'utf8');
  assert.match(kit, /triggerRef\.current \?\? anchorRef\?\.current/, 'the kit places next to the anchor when there is no pill');
  assert.match(kit, /placePopover\(/, 'the kit uses the placement helper');
});
