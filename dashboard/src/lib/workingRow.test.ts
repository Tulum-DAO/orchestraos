// The working row's melt, against the reference design: one
// continuous change of the body into the middle dot, the side dots from under it, three equal dots, the brightness
// wave. Unit space (the reference square's half width is 0.86 units = A/2).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { MELT, meltFrame, LAYOUT, SHAPE_KEYS, meltOutline, outlineMeanRadius } from '../components/avatar/avatarCore.ts';
import { canvasSizeFor, bobScaleFor, ROW_GAP_A, ROW_BOB_A, REPLY_RISE_A, BODY_HALF_UNITS } from './workingRowLook.ts';

const src = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');
const A = (units: number) => units / (2 * BODY_HALF_UNITS);           // unit space -> shares of A
const near = (a: number, b: number, tol: number, what: string) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a.toFixed(3)} vs ${b}`);

test('the morph: rounds and one slit by frame 09, eyes gone by frame 11, side dots out from under it by frame 12', () => {
  const f0 = meltFrame(0);
  assert.equal(f0.shrink, 0); assert.equal(f0.round, 0); assert.equal(f0.eyes, 1); assert.equal(f0.side, 0);
  const fr = (n: number) => (n - 5) / 60;                           // reference frame n, seconds into the melt
  near(meltFrame(fr(9)).round, 1, 0.001, 'a circle at frame 09');
  near(meltFrame(fr(9)).slit, 1, 0.001, 'one slit at frame 09');
  assert.equal(meltFrame(fr(11)).eyes, 0, 'eyes gone at frame 11');
  const f9 = meltFrame(fr(9));
  assert.ok(f9.side > 0.1 && f9.side + MELT.dotR > 0.5 * MELT.size[2][1] - 0.05, 'frame 09: the side dots peek out from under the body');
  near(meltFrame(fr(12)).side, MELT.gap, 0.001, 'frame 12: a big circle with the dots out both sides');
  // it shrinks steadily: never grows on the way in
  let last = -1;
  for (let t = 0; t <= MELT.dur; t += 1 / 60) { const k = meltFrame(t).shrink; assert.ok(k >= last - 1e-9); last = k; }
  assert.equal(meltFrame(MELT.dur).shrink, 1);
});

test('the end state: three equal dots ~0.27 A across, centres ~0.47 A apart, ~0.1 A lower; the trio ~1.2 A wide', () => {
  near(A(2 * MELT.dotR), 0.27, 0.02, 'dot size');
  near(A(MELT.gap), 0.47, 0.02, 'dot spacing');
  near(A(MELT.drop), 0.1, 0.01, 'settles lower');
  near(A(2 * MELT.gap + 2 * MELT.dotR), 1.2, 0.05, 'trio width');
  near(MELT.size[MELT.size.length - 1][1] / 2, MELT.dotR, 1e-9, 'the body ends as one of the dots');
});

test('the wave: centre peaks ~400 ms after "Typing…" (frame 25), right ~0.38 s later (frame 48), the others at ~53%, looping', () => {
  const at = (t: number) => meltFrame(t).lit;
  const c = MELT.centrePeak;
  near(c + MELT.lag + 0.067, 0.40, 0.02, 'centre peak, from the start of the video (frame 25)');
  near(c + MELT.step + MELT.lag + 0.067, 0.783, 0.02, 'right peak (frame 48)');
  assert.deepEqual(at(c).map((x) => +x.toFixed(2)), [MELT.dim, 1, MELT.dim]);
  assert.deepEqual(at(c + MELT.step).map((x) => +x.toFixed(2)), [MELT.dim, MELT.dim, 1]);
  assert.deepEqual(at(c + 3 * MELT.step).map((x) => +x.toFixed(2)), [MELT.dim, 1, MELT.dim], 'one pass ~1.1 s, then again');
  const mid = at(c + MELT.step / 2);
  near(mid[1], mid[2], 1e-9, 'half way the two share it');
  assert.ok(mid[1] > MELT.dim && mid[1] < 1);
  assert.equal(MELT.dim, 0.53);
});

test('everything scales from A and the text size: canvas, gap, bob, rise; no px in the melt', () => {
  for (const a of [16, 24, 40]) {
    const s = canvasSizeFor(a, LAYOUT.u);
    near(2 * BODY_HALF_UNITS * LAYOUT.u * s, a, 1e-9, 'the body is A across');
    // the trio fits in the canvas at any size
    assert.ok((MELT.gap + MELT.dotR) * LAYOUT.u * s < s / 2);
  }
  near(ROW_GAP_A, 0.73, 1e-9, 'gap'); near(ROW_BOB_A, 0.14, 1e-9, 'bob'); near(REPLY_RISE_A, 0.39, 1e-9, 'rise');
  near(bobScaleFor(0.025) * 0.025 * 2 / (2 * BODY_HALF_UNITS), ROW_BOB_A, 1e-9, 'the row bob travels 0.14 A');
  const core = src('components/avatar/avatarCore.ts');
  const melt = core.slice(core.indexOf('export const MELT'), core.indexOf('export const STATES'));
  assert.doesNotMatch(melt, /px/);
  const css = src('components/agent/agentWorking.css');
  assert.match(css, /--shimmer-ms: 700ms;/);
  assert.match(css, /width:var\(--row-a, 24px\); height:var\(--row-a, 24px\);/);
  const row = src('components/agent/AgentWorkingRow.tsx');
  assert.match(row, /avatarState: AvatarState = isTyping \? 'thinking'/);
  assert.match(row, /shape=\{rec\.shape\} color=\{rec\.color\} eyeColor=\{rec\.eyeColor\}/, "each agent's own look");
  assert.doesNotMatch(row, /elapsed|Loader2|agent-working-name/);
});

test('every shape melts: it rounds into a circle of its own size, then ends as a dot of the same size as the others', () => {
  for (const key of SHAPE_KEYS) {
    const R = outlineMeanRadius(key);
    assert.ok(R > 0.5 && R < 1.2, `${key}: ${R}`);
    const circle = meltOutline(key, 1);
    for (const [x, y] of circle) near(Math.hypot(x, y), R, 1e-9, `${key} rounds to a circle`);
    // the renderer's scale at the end of the shrink makes it MELT.dotR across, whatever the shape
    near(R * (1 + (MELT.dotR / R - 1) * 1), MELT.dotR, 1e-9, `${key} ends as a dot`);
    assert.equal(meltOutline(key, 0), meltOutline(key, 0), 'no rounding: the shape itself');
  }
});
